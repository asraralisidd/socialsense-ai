"""Threat Assessment persistence + bounded querying.

PostgreSQL notes
----------------
* Every aggregate lists its selected non-aggregate columns explicitly in
  ``GROUP BY``.
* All list-returning queries are bounded by an explicit ``limit``.
"""
from sqlalchemy import func

from database import db
from models.threat_assessment import ThreatAssessment
from repositories.base import BaseRepository


class ThreatAssessmentRepository(BaseRepository):
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 500

    def __init__(self):
        super().__init__(ThreatAssessment)

    def _bounded(self, limit):
        if not limit or limit <= 0:
            return self.DEFAULT_LIMIT
        return min(int(limit), self.MAX_LIMIT)

    # ------------------------------------------------------------ assessments

    def get_for_analysis(self, analysis_id):
        return ThreatAssessment.query.filter_by(analysis_id=analysis_id).first()

    def get_for_user(self, user_id, limit=None):
        return ThreatAssessment.query.filter_by(user_id=user_id).order_by(
            ThreatAssessment.created_at.desc(), ThreatAssessment.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_high_threat_for_user(self, user_id, min_score=50.0, limit=None):
        """Filtered list query - no GROUP BY, PostgreSQL-safe."""
        return ThreatAssessment.query.filter(
            ThreatAssessment.user_id == user_id,
            ThreatAssessment.overall_threat_score >= min_score,
        ).order_by(
            ThreatAssessment.overall_threat_score.desc(),
            ThreatAssessment.created_at.desc(),
            ThreatAssessment.id.desc(),
        ).limit(self._bounded(limit)).all()

    def count_for_user(self, user_id):
        return ThreatAssessment.query.filter_by(user_id=user_id).count()

    def get_level_distribution(self, user_id):
        """Narrative counts per threat level.

        GROUP BY lists the only selected non-aggregate column explicitly.
        """
        rows = db.session.query(
            ThreatAssessment.threat_level,
            func.count(ThreatAssessment.id).label('count'),
        ).filter(
            ThreatAssessment.user_id == user_id
        ).group_by(
            ThreatAssessment.threat_level
        ).order_by(
            ThreatAssessment.threat_level.asc()
        ).all()
        return {r.threat_level: int(r.count) for r in rows}

    def get_recent_high_threat_summary(self, user_id, min_score=50.0, limit=None):
        rows = self.get_high_threat_for_user(user_id, min_score=min_score, limit=limit)
        return [r.to_dict() for r in rows]
