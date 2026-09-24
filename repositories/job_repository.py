from datetime import datetime, timezone, timedelta
from database import db
from models.job import Job
from repositories.base import BaseRepository


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobRepository(BaseRepository):
    def __init__(self):
        super().__init__(Job)

    def create_job(self, user_id, platform, source_type, source_input, comment_limit=100, priority=0, request_hash=None):
        job = Job(
            user_id=user_id,
            platform=platform,
            source_type=source_type,
            source_input=source_input,
            comment_limit=comment_limit,
            priority=priority,
            request_hash=request_hash,
        )
        db.session.add(job)
        db.session.commit()
        return job

    def get_job(self, job_id):
        return self.model.query.get(job_id)

    def get_jobs_for_user(self, user_id, limit=50, offset=0, status=None):
        q = self.model.query.filter_by(user_id=user_id)
        if status:
            if isinstance(status, list):
                q = q.filter(Job.status.in_(status))
            else:
                q = q.filter_by(status=status)
        return q.order_by(Job.created_at.desc()).limit(limit).offset(offset).all()

    def get_running_jobs(self):
        return self.model.query.filter_by(status=Job.RUNNING).all()

    def get_pending_jobs(self):
        return self.model.query.filter_by(status=Job.PENDING).order_by(Job.priority.desc(), Job.created_at.asc()).all()

    def get_job_by_hash(self, request_hash):
        return self.model.query.filter_by(request_hash=request_hash).first()

    def update_progress(self, job_id, percent, step=None):
        job = self.get_job(job_id)
        if job:
            job.progress_percent = percent
            if step:
                job.current_step = step
            db.session.commit()
        return job

    def update_status(self, job_id, status, error_message=None):
        job = self.get_job(job_id)
        if job:
            job.status = status
            if status == Job.RUNNING and not job.started_at:
                job.started_at = _utcnow()
            if status in (Job.COMPLETED, Job.FAILED, Job.CANCELLED, Job.TIMEOUT):
                job.completed_at = _utcnow()
                if job.started_at:
                    job.execution_time_seconds = (job.completed_at - job.started_at).total_seconds()
            if error_message:
                job.error_message = error_message
            db.session.commit()
        return job

    def mark_completed(self, job_id, analysis_id=None):
        job = self.update_status(job_id, Job.COMPLETED)
        if job and analysis_id:
            job.result_analysis_id = analysis_id
            job.progress_percent = 100
            job.current_step = 'Completed'
            db.session.commit()
        return job

    def mark_failed(self, job_id, error_message=None):
        return self.update_status(job_id, Job.FAILED, error_message=error_message)

    def mark_cancelled(self, job_id):
        return self.update_status(job_id, Job.CANCELLED)

    def increment_retry_count(self, job_id):
        job = self.get_job(job_id)
        if job:
            job.retry_count = (job.retry_count or 0) + 1
            job.status = Job.PENDING
            job.cancellation_requested = False
            db.session.commit()
        return job

    def count_by_user_and_status(self, user_id, status):
        return self.model.query.filter_by(user_id=user_id, status=status).count()

    def count_jobs_for_user(self, user_id, status=None):
        q = self.model.query.filter_by(user_id=user_id)
        if status:
            if isinstance(status, list):
                q = q.filter(Job.status.in_(status))
            else:
                q = q.filter_by(status=status)
        return q.count()

    def count_all_by_status(self, status):
        return self.model.query.filter_by(status=status).count()

    def get_stuck_jobs(self, max_age_seconds=None, limit=100):
        """Jobs considered stuck: running/pending with age beyond threshold.

        Threshold defaults to twice MAX_JOB_RUNTIME (or 3600s) so normal
        running jobs are not flagged during a healthy run; only jobs
        whose ``started_at`` is older than the cutoff are returned.
        Ordered oldest-first so recovery processes the most overdue first.
        """
        try:
            if max_age_seconds is None:
                from flask import current_app
                max_age_seconds = int(current_app.config.get('MAX_JOB_RUNTIME', 600)) * 2 if current_app else 3600
            max_age_seconds = max(60, int(max_age_seconds))
        except (TypeError, ValueError, AttributeError):
            max_age_seconds = 3600
        cutoff = _utcnow() - timedelta(seconds=max_age_seconds)
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 100
        return self.model.query.filter(
            Job.status.in_([Job.RUNNING, Job.PENDING]),
            Job.started_at.isnot(None),
            Job.started_at < cutoff
        ).order_by(Job.started_at.asc(), Job.id.asc()).limit(limit).all()

    def count_stuck_jobs(self, max_age_seconds=None):
        try:
            if max_age_seconds is None:
                from flask import current_app
                max_age_seconds = int(current_app.config.get('MAX_JOB_RUNTIME', 600)) * 2 if current_app else 3600
            max_age_seconds = max(60, int(max_age_seconds))
        except (TypeError, ValueError, AttributeError):
            max_age_seconds = 3600
        cutoff = _utcnow() - timedelta(seconds=max_age_seconds)
        return self.model.query.filter(
            Job.status.in_([Job.RUNNING, Job.PENDING]),
            Job.started_at.isnot(None),
            Job.started_at < cutoff
        ).count()

    def cleanup_old_jobs(self, days=30, limit=500):
        cutoff = _utcnow() - timedelta(days=days)
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 500
        old = self.model.query.filter(
            Job.created_at < cutoff,
            Job.status.in_([Job.COMPLETED, Job.FAILED, Job.CANCELLED, Job.TIMEOUT])
        ).order_by(Job.created_at.asc(), Job.id.asc()).limit(limit).all()
        for j in old:
            db.session.delete(j)
        db.session.commit()
        return len(old)
