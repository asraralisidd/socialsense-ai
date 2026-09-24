from models.scheduled_analysis import ScheduledAnalysis
from repositories.base import BaseRepository
from database import db
from datetime import datetime, timezone, timedelta


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ScheduledAnalysisRepository(BaseRepository):
    def __init__(self):
        super().__init__(ScheduledAnalysis)

    def get_user_schedules(self, user_id, include_inactive=False, limit=20, offset=0):
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 20
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        q = self.model.query.filter_by(user_id=user_id)
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(ScheduledAnalysis.next_run_at.asc(), ScheduledAnalysis.id.desc()).limit(limit).offset(offset).all()

    def count_user_schedules(self, user_id, include_inactive=False):
        q = self.model.query.filter_by(user_id=user_id)
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.count()

    def get_due_schedules(self):
        return self.model.query.filter(
            ScheduledAnalysis.is_active == True,
            ScheduledAnalysis.next_run_at <= _now(),
        ).all()

    def update_next_run(self, schedule_id):
        schedule = self.get_by_id(schedule_id)
        if not schedule:
            return None
        if schedule.frequency == ScheduledAnalysis.FREQ_ONCE:
            schedule.is_active = False
            schedule.next_run_at = None
        elif schedule.frequency == ScheduledAnalysis.FREQ_DAILY:
            schedule.next_run_at = _now() + timedelta(days=1)
        elif schedule.frequency == ScheduledAnalysis.FREQ_WEEKLY:
            schedule.next_run_at = _now() + timedelta(weeks=1)
        elif schedule.frequency == ScheduledAnalysis.FREQ_MONTHLY:
            schedule.next_run_at = _now() + timedelta(days=30)
        schedule.last_run_at = _now()
        db.session.commit()
        return schedule
