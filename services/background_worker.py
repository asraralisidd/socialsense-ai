import traceback
import logging
from datetime import datetime, timezone
from flask import current_app
from database import db
from models.job import Job
from repositories.job_repository import JobRepository
from repositories.job_log_repository import JobLogRepository
from services.analysis_service import AnalysisService
from services.job_queue_interface import ThreadPoolQueueProvider, CeleryQueueProvider

logger = logging.getLogger(__name__)


class BackgroundWorker:
    def __init__(self, queue_provider=None, use_celery=False):
        self.job_repo = JobRepository()
        self.log_repo = JobLogRepository()
        self.analysis_service = AnalysisService()
        self._use_celery = use_celery
        if queue_provider:
            self.queue = queue_provider
        elif use_celery:
            self.queue = CeleryQueueProvider()
        else:
            self.queue = ThreadPoolQueueProvider(max_workers=4)
        self._recovered = False

    def recover_stuck_jobs(self, app):
        with app.app_context():
            stuck = self.job_repo.get_stuck_jobs()
            for job in stuck:
                if job.status == Job.RUNNING:
                    self.job_repo.mark_failed(job.id, 'Job interrupted by application restart.')
                    self.log_repo.create_log(job.id, 'ERROR', 'Job interrupted by application restart.', 'Recovery')
            self._recovered = True

    def submit_analysis(self, job_id, app):
        if app and app.config.get('TESTING'):
            if self._use_celery:
                from celery_tasks import run_analysis
                return run_analysis.delay(job_id=job_id)
            self._run_analysis(job_id, app)
            return None
        future = self.queue.submit(self._run_analysis, job_id, app)
        return future

    def cancel_job(self, job_id):
        job = self.job_repo.get_job(job_id)
        if not job:
            return False
        if job.status == Job.PENDING:
            self.job_repo.mark_cancelled(job_id)
            self.log_repo.create_log(job_id, 'INFO', 'Job cancelled while pending.', 'Cancellation')
            return True
        if job.status == Job.RUNNING:
            job.cancellation_requested = True
            db.session.commit()
            self.log_repo.create_log(job_id, 'INFO', 'Cancellation requested.', 'Cancellation')
            return True
        return False

    def _run_analysis(self, job_id, app):
        with app.app_context():
            try:
                job = self.job_repo.get_job(job_id)
                if not job:
                    return

                if job.cancellation_requested:
                    self.job_repo.mark_cancelled(job_id)
                    self.log_repo.create_log(job_id, 'INFO', 'Job was cancelled before starting.', 'Cancellation')
                    return

                self.job_repo.update_status(job_id, Job.RUNNING)
                self.log_repo.create_log(job_id, 'INFO', 'Worker assigned.', 'Starting')
                self.job_repo.update_progress(job_id, 5, 'Worker Assigned')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 10, 'Fetching Metadata')
                self.log_repo.create_log(job_id, 'INFO', f'Starting {job.platform} analysis: {job.source_input}', 'Fetching')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 20, 'Fetching Content')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 30, 'Fetching Comments')
                self.log_repo.create_log(job_id, 'INFO', f'Fetching up to {job.comment_limit} comments.', 'Fetching')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 40, 'Running AI Analysis')
                self.log_repo.create_log(job_id, 'INFO', 'AI analysis engines running.', 'Analysis')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 50, 'Generating Summary')
                self.log_repo.create_log(job_id, 'INFO', 'Generating analysis summary.', 'Summary')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 55, 'Processing Transcript')
                if job.platform == 'youtube':
                    self.log_repo.create_log(job_id, 'INFO', 'Fetching and analyzing video transcript.', 'Transcript')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 60, 'Extracting Entities')
                self.log_repo.create_log(job_id, 'INFO', 'Extracting and analyzing entities.', 'Entity')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 65, 'Analyzing Entity Context')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 70, 'Loading Channel History')
                self.log_repo.create_log(job_id, 'INFO', 'Loading channel context and video history.', 'Channel')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 75, 'Computing Historical Trends')
                self.log_repo.create_log(job_id, 'INFO', 'Computing historical entity and topic trends.', 'Trends')

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 78, 'Generating Context Intelligence')

                # Build a progress callback that reports V12 stage progress
                # from within the analysis call (monotonic, never exceeds 100).
                def _progress(pct, step):
                    try:
                        self.job_repo.update_progress(job_id, pct, step)
                        self.log_repo.create_log(job_id, 'INFO', step, 'Intelligence')
                    except Exception:
                        db.session.rollback()
                        logger.warning(f'Progress update failed for job {job_id}')

                if job.platform == 'youtube':
                    result = self.analysis_service.create_youtube_analysis(
                        job.user_id, job.source_input, comment_limit=job.comment_limit,
                        progress_callback=_progress,
                    )
                elif job.platform == 'reddit':
                    result = self.analysis_service.create_reddit_analysis(
                        job.user_id, job.source_input, comment_limit=job.comment_limit,
                        progress_callback=_progress,
                    )
                else:
                    self.job_repo.mark_failed(job_id, f'Unknown platform: {job.platform}')
                    self.log_repo.create_log(job_id, 'ERROR', f'Unknown platform: {job.platform}', 'Error')
                    return

                if not result['success']:
                    self.job_repo.mark_failed(job_id, result.get('error', 'Analysis failed'))
                    self.log_repo.create_log(job_id, 'ERROR', result.get('error', 'Analysis failed'), 'Error')
                    return

                self._check_cancelled(job_id)

                self.job_repo.update_progress(job_id, 99, 'Saving Results')
                self.log_repo.create_log(job_id, 'INFO', f'Analysis complete. {result["comment_count"]} comments processed.', 'Complete')

                self.job_repo.mark_completed(job_id, analysis_id=result['analysis_id'])
                self.log_repo.create_log(job_id, 'INFO', 'Job completed successfully.', 'Complete')

            except CancellationRequested:
                self.job_repo.mark_cancelled(job_id)
                self.log_repo.create_log(job_id, 'INFO', 'Job cancelled by user.', 'Cancellation')

            except Exception as e:
                db.session.rollback()
                error_msg = f'{type(e).__name__}: {str(e)}'
                logger.error(f'Background worker failed for job {job_id}: {error_msg}', exc_info=True)
                self.job_repo.mark_failed(job_id, error_msg)
                self.log_repo.create_log(job_id, 'ERROR', error_msg, 'Error', metadata_json=traceback.format_exc())

    def _check_cancelled(self, job_id):
        job = self.job_repo.get_job(job_id)
        if job and job.cancellation_requested:
            raise CancellationRequested()


class CancellationRequested(Exception):
    pass
