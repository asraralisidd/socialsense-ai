from models.analysis import Analysis
from repositories.base import BaseRepository


class AnalysisRepository(BaseRepository):
    def __init__(self):
        super().__init__(Analysis)

    def get_by_user_id(self, user_id, limit=None, offset=None):
        """Analyses for a user, newest first, with optional SQL-level cap.

        The limit/offset are applied in the database (never load-all-then-slice).
        ``limit=None`` preserves the legacy unbounded read for callers that
        aggregate over the full history in SQL.
        """
        query = self.model.query.filter_by(user_id=user_id).order_by(
            Analysis.created_at.desc(), Analysis.id.desc())
        if limit is not None:
            try:
                limit = max(1, min(int(limit), 100))
            except (TypeError, ValueError):
                limit = None
        if limit is not None:
            query = query.limit(limit)
        if offset is not None:
            try:
                offset = max(0, int(offset))
            except (TypeError, ValueError):
                offset = None
        if offset is not None:
            query = query.offset(offset)
        return query.all()

    def get_user_analysis_with_youtube(self, analysis_id, user_id):
        return self.model.query.filter_by(id=analysis_id, user_id=user_id).first()

    def get_user_analysis_with_reddit(self, analysis_id, user_id):
        return self.model.query.filter_by(id=analysis_id, user_id=user_id).first()

    def count_by_user(self, user_id):
        return self.model.query.filter_by(user_id=user_id).count()

    def get_recent_by_user(self, user_id, limit=10):
        return self.model.query.filter_by(user_id=user_id).order_by(
            Analysis.created_at.desc()).limit(limit).all()
