from models.scheduled_report import ScheduledReport
from repositories.base import BaseRepository
from database import db
from datetime import datetime, timezone, timedelta


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ScheduledReportRepository(BaseRepository):
    def __init__(self):
        super().__init__(ScheduledReport)

    def get_user_reports(self, user_id, include_inactive=False, limit=20, offset=0):
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
        return q.order_by(ScheduledReport.next_run_at.asc(), ScheduledReport.id.desc()).limit(limit).offset(offset).all()

    def count_user_reports(self, user_id, include_inactive=False):
        q = self.model.query.filter_by(user_id=user_id)
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.count()

    def get_due_reports(self, limit=None):
        """Due reports oldest-first, with an optional SQL-level batch cap.

        ``limit=None`` preserves the legacy unbounded read; callers that
        process reports in a scheduler tick must pass an explicit bound so
        remaining reports stay due for the next cycle instead of piling
        into one tick.
        """
        query = self.model.query.filter(
            ScheduledReport.is_active == True,
            ScheduledReport.next_run_at <= _now(),
        ).order_by(ScheduledReport.next_run_at.asc(),
                   ScheduledReport.id.asc())
        if limit is not None:
            try:
                limit = max(1, int(limit))
            except (TypeError, ValueError):
                limit = None
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    def update_next_run(self, report_id):
        report = self.get_by_id(report_id)
        if not report:
            return None
        if report.frequency == ScheduledReport.FREQ_DAILY:
            report.next_run_at = _now() + timedelta(days=1)
        elif report.frequency == ScheduledReport.FREQ_WEEKLY:
            report.next_run_at = _now() + timedelta(weeks=1)
        elif report.frequency == ScheduledReport.FREQ_MONTHLY:
            report.next_run_at = _now() + timedelta(days=30)
        report.last_run_at = _now()
        db.session.commit()
        return report

    def delete_old_reports(self, days=30):
        cutoff = _now() - timedelta(days=days)
        old = self.model.query.filter(ScheduledReport.created_at < cutoff).all()
        for r in old:
            db.session.delete(r)
        db.session.commit()
        return len(old)
