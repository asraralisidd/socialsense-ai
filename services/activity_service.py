from repositories.activity_log_repository import ActivityLogRepository


class ActivityService:
    def __init__(self):
        self.repo = ActivityLogRepository()

    def log(self, user_id, action, description=None, resource_type=None, resource_id=None, ip_address=None):
        return self.repo.log_activity(user_id, action, description, resource_type, resource_id, ip_address)

    def get_user_activity(self, user_id, limit=100, offset=0):
        return self.repo.get_user_activity(user_id, limit, offset)

    def count_user_activity(self, user_id):
        return self.repo.count_user_activity(user_id)

    def cleanup_old(self, days=30):
        return self.repo.delete_old_logs(days)
