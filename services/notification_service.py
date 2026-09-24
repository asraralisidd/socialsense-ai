from repositories.notification_repository import NotificationRepository
from repositories.activity_log_repository import ActivityLogRepository
from models.notification import Notification


class NotificationService:
    def __init__(self):
        self.repo = NotificationRepository()
        self.activity_repo = ActivityLogRepository()

    def create(self, user_id, ntype, title, message=None, severity=Notification.SEVERITY_INFO, link_url=None):
        return self.repo.create_notification(user_id, ntype, title, message, severity, link_url)

    def get_user_notifications(self, user_id, limit=50, offset=0, unread_only=False):
        return self.repo.get_user_notifications(user_id, limit, offset, unread_only)

    def count_user_notifications(self, user_id, unread_only=False):
        return self.repo.count_user_notifications(user_id, unread_only)

    def get_unread_count(self, user_id):
        return self.repo.get_unread_count(user_id)

    def mark_as_read(self, notification_id, user_id):
        return self.repo.mark_as_read(notification_id, user_id)

    def mark_all_read(self, user_id):
        self.repo.mark_all_read(user_id)

    def notify_job_completed(self, user_id, job_id, analysis_id):
        self.create(
            user_id, Notification.TYPE_JOB_COMPLETED,
            'Analysis Complete',
            f'Your analysis (Job #{job_id}) has completed successfully.',
            Notification.SEVERITY_SUCCESS,
            f'/analysis/{analysis_id}' if analysis_id else None,
        )

    def notify_job_failed(self, user_id, job_id, error=None):
        self.create(
            user_id, Notification.TYPE_JOB_FAILED,
            'Analysis Failed',
            f'Job #{job_id} failed. {error or ""}',
            Notification.SEVERITY_DANGER,
            f'/analysis/jobs/{job_id}',
        )

    def cleanup_old(self, days=30):
        return self.repo.delete_old_notifications(days)
