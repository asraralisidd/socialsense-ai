import logging
import traceback
from datetime import datetime, timezone
from celery import current_task
from celery_app import celery_app
from database import db
from models.job import Job
from repositories.job_repository import JobRepository
from repositories.job_log_repository import JobLogRepository
from services.analysis_service import AnalysisService
from services.scheduler_service import SchedulerService
from services.report_generation_service import ReportGenerationService
from services.system_health_service import MaintenanceService

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def run_analysis(self, job_id):
    from flask import Flask
    from app import create_app

    app = create_app()
    with app.app_context():
        job_repo = JobRepository()
        log_repo = JobLogRepository()
        analysis_service = AnalysisService()

        try:
            job = job_repo.get_job(job_id)
            if not job:
                logger.error(f'Job {job_id} not found')
                return {'success': False, 'error': 'Job not found'}

            if job.cancellation_requested:
                job_repo.mark_cancelled(job_id)
                log_repo.create_log(job_id, 'INFO', 'Job was cancelled before starting.', 'Cancellation')
                return {'success': False, 'error': 'Cancelled'}

            job_repo.update_status(job_id, Job.RUNNING)
            log_repo.create_log(job_id, 'INFO', 'Celery worker assigned.', 'Starting')
            job_repo.update_progress(job_id, 5, 'Worker Assigned')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 10, 'Fetching Metadata')
            log_repo.create_log(job_id, 'INFO', f'Starting {job.platform} analysis: {job.source_input}', 'Fetching')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 20, 'Fetching Content')
            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 30, 'Fetching Comments')
            log_repo.create_log(job_id, 'INFO', f'Fetching up to {job.comment_limit} comments.', 'Fetching')

            _check_cancelled(job_id, job_repo)

            self.update_state(state='PROGRESS', meta={'step': 'AI Analysis', 'percent': 40})
            job_repo.update_progress(job_id, 40, 'Running AI Analysis')
            log_repo.create_log(job_id, 'INFO', 'AI analysis engines running.', 'Analysis')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 50, 'Generating Summary')
            log_repo.create_log(job_id, 'INFO', 'Generating analysis summary.', 'Summary')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 55, 'Processing Transcript')
            if job.platform == 'youtube':
                log_repo.create_log(job_id, 'INFO', 'Fetching and analyzing video transcript.', 'Transcript')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 60, 'Extracting Entities')
            log_repo.create_log(job_id, 'INFO', 'Extracting and analyzing entities.', 'Entity')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 65, 'Analyzing Entity Context')
            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 70, 'Loading Channel History')
            log_repo.create_log(job_id, 'INFO', 'Loading channel context and video history.', 'Channel')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 75, 'Computing Historical Trends')
            log_repo.create_log(job_id, 'INFO', 'Computing historical entity and topic trends.', 'Trends')

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 78, 'Generating Context Intelligence')

            def _progress(pct, step):
                try:
                    job_repo.update_progress(job_id, pct, step)
                    log_repo.create_log(job_id, 'INFO', step, 'Intelligence')
                except Exception:
                    db.session.rollback()
                    logger.warning(f'Progress update failed for job {job_id}')

            if job.platform == 'youtube':
                result = analysis_service.create_youtube_analysis(
                    job.user_id, job.source_input, comment_limit=job.comment_limit,
                    progress_callback=_progress,
                )
            elif job.platform == 'reddit':
                result = analysis_service.create_reddit_analysis(
                    job.user_id, job.source_input, comment_limit=job.comment_limit,
                    progress_callback=_progress,
                )
            else:
                job_repo.mark_failed(job_id, f'Unknown platform: {job.platform}')
                log_repo.create_log(job_id, 'ERROR', f'Unknown platform: {job.platform}', 'Error')
                return {'success': False, 'error': f'Unknown platform: {job.platform}'}

            if not result['success']:
                job_repo.mark_failed(job_id, result.get('error', 'Analysis failed'))
                log_repo.create_log(job_id, 'ERROR', result.get('error', 'Analysis failed'), 'Error')
                return {'success': False, 'error': result.get('error', 'Analysis failed')}

            _check_cancelled(job_id, job_repo)

            job_repo.update_progress(job_id, 99, 'Saving Results')
            log_repo.create_log(job_id, 'INFO', f'Analysis complete. {result["comment_count"]} comments processed.', 'Complete')

            job_repo.mark_completed(job_id, analysis_id=result['analysis_id'])
            log_repo.create_log(job_id, 'INFO', 'Job completed successfully.', 'Complete')

            return {
                'success': True,
                'analysis_id': result['analysis_id'],
                'comment_count': result['comment_count'],
            }

        except CancellationRequested:
            job_repo.mark_cancelled(job_id)
            log_repo.create_log(job_id, 'INFO', 'Job cancelled by user.', 'Cancellation')
            return {'success': False, 'error': 'Cancelled'}

        except Exception as e:
            error_msg = f'{type(e).__name__}: {str(e)}'
            logger.error(f'Celery task failed for job {job_id}: {error_msg}', exc_info=True)
            try:
                job_repo.mark_failed(job_id, error_msg)
                log_repo.create_log(job_id, 'ERROR', error_msg, 'Error', metadata_json=traceback.format_exc())
            except Exception:
                logger.error(f'Failed to mark job {job_id} as failed')
            raise self.retry(exc=e)


def _check_cancelled(job_id, job_repo):
    job = job_repo.get_job(job_id)
    if job and job.cancellation_requested:
        raise CancellationRequested()


class CancellationRequested(Exception):
    pass


@celery_app.task
def run_scheduler():
    from flask import Flask
    from app import create_app

    app = create_app()
    with app.app_context():
        try:
            scheduler = SchedulerService()
            count = scheduler.process_due_schedules(app)
            logger.info(f'Scheduler processed {count} due analyses')
        except Exception as e:
            logger.error(f'Scheduler failed: {e}', exc_info=True)


@celery_app.task
def run_scheduled_reports():
    from flask import Flask
    from app import create_app

    app = create_app()
    with app.app_context():
        try:
            from services.report_generation_service import ReportGenerationService
            rpt = ReportGenerationService()
            count = rpt.process_due_reports(app)
            logger.info(f'Scheduled reports generated: {count}')
        except Exception as e:
            logger.error(f'Scheduled reports failed: {e}', exc_info=True)


@celery_app.task
def cleanup_old_data():
    from flask import Flask
    from app import create_app

    app = create_app()
    with app.app_context():
        try:
            maintenance = MaintenanceService()
            results = maintenance.cleanup_all(app)
            logger.info(f'Cleanup complete: {results}')
        except Exception as e:
            logger.error(f'Cleanup failed: {e}', exc_info=True)
