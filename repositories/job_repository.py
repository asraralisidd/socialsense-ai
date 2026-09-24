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

    def get_stuck_jobs(self):
        return self.model.query.filter(
            Job.status.in_([Job.RUNNING, Job.PENDING]),
            Job.started_at.isnot(None),
            Job.started_at < _utcnow()
        ).all()

    def cleanup_old_jobs(self, days=30):
        cutoff = _utcnow() - timedelta(days=days)
        old = self.model.query.filter(
            Job.created_at < cutoff,
            Job.status.in_([Job.COMPLETED, Job.FAILED, Job.CANCELLED, Job.TIMEOUT])
        ).all()
        for j in old:
            db.session.delete(j)
        db.session.commit()
        return len(old)
