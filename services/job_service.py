import hashlib
import json
from flask import current_app
from datetime import datetime, timezone
from database import db
from models.job import Job
from repositories.job_repository import JobRepository
from repositories.job_log_repository import JobLogRepository
from services.background_worker import BackgroundWorker
from services.job_queue_interface import ThreadPoolQueueProvider


class JobService:
    def __init__(self, worker=None):
        self.job_repo = JobRepository()
        self.log_repo = JobLogRepository()
        self.worker = worker or BackgroundWorker(ThreadPoolQueueProvider(
            max_workers=current_app.config.get('MAX_CONCURRENT_JOBS', 4) if current_app else 4
        ))

    def create_job(self, user_id, platform, source_input, comment_limit=100, request_hash=None):
        max_per_user = current_app.config.get('MAX_JOBS_PER_USER', 20) if current_app else 20
        running_count = self.job_repo.count_by_user_and_status(user_id, Job.RUNNING)
        if running_count >= max_per_user:
            return {'success': False, 'error': 'Maximum concurrent job limit reached. Wait for running jobs to complete.'}

        if request_hash:
            existing = self.job_repo.get_job_by_hash(request_hash)
            if existing:
                return {
                    'success': True,
                    'job_id': existing.id,
                    'duplicate': True,
                    'status': existing.status,
                }

        if platform in ('youtube', 'reddit'):
            source_type = 'url'
        else:
            source_type = platform

        job = self.job_repo.create_job(
            user_id=user_id,
            platform=platform,
            source_type=source_type,
            source_input=source_input,
            comment_limit=comment_limit,
            request_hash=request_hash,
        )

        self.log_repo.create_log(job.id, 'INFO', 'Job created and queued.', 'Created')

        if current_app and current_app.config.get('TESTING'):
            self._submit_with_context(job.id, current_app._get_current_object())
        else:
            import threading
            app = __import__('flask', fromlist=['current_app']).current_app._get_current_object()
            t = threading.Thread(target=self._submit_with_context, args=(job.id, app), daemon=True)
            t.start()

        return {'success': True, 'job_id': job.id, 'duplicate': False}

    def _submit_with_context(self, job_id, app):
        self.worker.submit_analysis(job_id, app)

    def get_job(self, job_id, user_id=None):
        job = self.job_repo.get_job(job_id)
        if not job:
            return None
        if user_id and job.user_id != user_id:
            return None
        return job

    def get_job_status(self, job_id, user_id=None):
        job = self.get_job(job_id, user_id)
        if not job:
            return None
        return {
            'job_id': job.id,
            'status': job.status,
            'progress_percent': job.progress_percent,
            'current_step': job.current_step,
            'error_message': job.error_message,
            'result_analysis_id': job.result_analysis_id,
            'redirect_url': f'/analysis/{job.result_analysis_id}' if job.result_analysis_id else None,
            'execution_time': job.execution_time_seconds or 0,
            'cancellation_requested': job.cancellation_requested,
        }

    def get_jobs_for_user(self, user_id, limit=50, offset=0, status=None):
        return self.job_repo.get_jobs_for_user(user_id, limit=limit, offset=offset, status=status)

    def cancel_job(self, job_id, user_id=None):
        job = self.get_job(job_id, user_id)
        if not job:
            return {'success': False, 'error': 'Job not found.'}
        return {'success': self.worker.cancel_job(job_id)}

    def retry_job(self, job_id, user_id=None):
        job = self.get_job(job_id, user_id)
        if not job:
            return {'success': False, 'error': 'Job not found.'}
        if job.status not in (Job.FAILED, Job.CANCELLED, Job.TIMEOUT):
            return {'success': False, 'error': 'Only failed, cancelled, or timed out jobs can be retried.'}

        max_retries = current_app.config.get('MAX_JOB_RETRIES', 3) if current_app else 3
        if (job.retry_count or 0) >= max_retries:
            return {'success': False, 'error': f'Maximum retry limit ({max_retries}) reached.'}

        self.job_repo.increment_retry_count(job.id)
        self.log_repo.create_log(job.id, 'INFO', f'Job queued for retry (attempt {job.retry_count + 1}).', 'Retry')

        if current_app and current_app.config.get('TESTING'):
            self._submit_with_context(job.id, current_app._get_current_object())
        else:
            import threading
            app = __import__('flask', fromlist=['current_app']).current_app._get_current_object()
            t = threading.Thread(target=self._submit_with_context, args=(job.id, app), daemon=True)
            t.start()

        return {'success': True, 'job_id': job.id}

    def get_job_logs(self, job_id, user_id=None, limit=100, offset=0):
        job = self.get_job(job_id, user_id)
        if not job:
            return None
        return self.log_repo.get_logs(job_id, limit=limit, offset=offset)

    def count_job_logs(self, job_id, user_id=None):
        job = self.get_job(job_id, user_id)
        if not job:
            return 0
        return self.log_repo.count_logs(job_id)

    def get_dashboard_metrics(self, user_id):
        return {
            'pending': self.job_repo.count_by_user_and_status(user_id, Job.PENDING),
            'running': self.job_repo.count_by_user_and_status(user_id, Job.RUNNING),
            'completed': self.job_repo.count_by_user_and_status(user_id, Job.COMPLETED),
            'failed': self.job_repo.count_by_user_and_status(user_id, Job.FAILED),
            'cancelled': self.job_repo.count_by_user_and_status(user_id, Job.CANCELLED),
        }

    def compute_request_hash(self, user_id, platform, source_input):
        raw = f'{user_id}:{platform}:{source_input}'
        return hashlib.sha256(raw.encode()).hexdigest()
