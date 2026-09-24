from models.activity_log import ActivityLog
from repositories.base import BaseRepository
from database import db
from datetime import datetime, timezone, timedelta


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ActivityLogRepository(BaseRepository):
    def __init__(self):
        super().__init__(ActivityLog)

    def log_activity(self, user_id, action, description=None, resource_type=None, resource_id=None, ip_address=None):
        entry = ActivityLog(
            user_id=user_id, action=action, description=description,
            resource_type=resource_type, resource_id=resource_id, ip_address=ip_address,
        )
        db.session.add(entry)
        db.session.commit()
        return entry

    def get_user_activity(self, user_id, limit=100, offset=0):
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 100
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        return self.model.query.filter_by(user_id=user_id).order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(limit).offset(offset).all()

    def count_user_activity(self, user_id):
        return self.model.query.filter_by(user_id=user_id).count()

    def delete_old_logs(self, days=30, limit=500):
        cutoff = _now() - timedelta(days=days)
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 500
        old = self.model.query.filter(ActivityLog.created_at < cutoff).order_by(ActivityLog.created_at.asc(), ActivityLog.id.asc()).limit(limit).all()
        for l in old:
            db.session.delete(l)
        db.session.commit()
        return len(old)
