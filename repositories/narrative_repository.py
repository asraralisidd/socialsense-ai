"""Narrative persistence + aggregation.

PostgreSQL notes
----------------
Every aggregate below lists **all** selected non-aggregate columns in its
``GROUP BY``, rather than relying on PostgreSQL's primary-key functional
dependency shortcut or SQLite's permissiveness. This is the V9 GROUP BY lesson
applied up front (see ``tests/test_v9.py::test_aggregation_queries_group_by_entity_type``).

All list-returning queries are bounded by an explicit ``limit``.
"""
from sqlalchemy import func

from database import db
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from repositories.base import BaseRepository


class NarrativeRepository(BaseRepository):
    #: Safety ceiling for any caller that forgets to pass a limit.
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 500

    def __init__(self):
        super().__init__(Narrative)

    def _bounded(self, limit):
        if not limit or limit <= 0:
            return self.DEFAULT_LIMIT
        return min(int(limit), self.MAX_LIMIT)

    # ------------------------------------------------------------- narratives

    def get_by_normalized_name(self, user_id, normalized_name):
        """Exact identity lookup. Backed by uq_narratives_user_normalized."""
        return Narrative.query.filter_by(
            user_id=user_id, normalized_name=normalized_name
        ).first()

    def get_recent_for_user(self, user_id, limit=200):
        """Most recently active narratives, for bounded similarity reuse.

        Ordered by ``last_seen_at`` then ``id`` so the candidate set is
        deterministic even when timestamps tie.
        """
        return Narrative.query.filter_by(user_id=user_id).order_by(
            Narrative.last_seen_at.desc(), Narrative.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_top_for_user(self, user_id, limit=10):
        return Narrative.query.filter_by(user_id=user_id).order_by(
            Narrative.risk_score.desc(), Narrative.occurrence_count.desc(),
            Narrative.id.asc()
        ).limit(self._bounded(limit)).all()

    def get_for_analysis(self, analysis_id, limit=50):
        """Narratives that occurred in one analysis, strongest first."""
        return db.session.query(Narrative, NarrativeOccurrence).join(
            NarrativeOccurrence, NarrativeOccurrence.narrative_id == Narrative.id
        ).filter(
            NarrativeOccurrence.analysis_id == analysis_id
        ).order_by(
            NarrativeOccurrence.relevance_score.desc(), Narrative.id.asc()
        ).limit(self._bounded(limit)).all()

    def get_occurrences_for_analysis(self, analysis_id, limit=None):
        """Bounded narrative-occurrence rows for one analysis."""
        limit = limit or self.DEFAULT_LIMIT
        return NarrativeOccurrence.query.filter_by(
            analysis_id=analysis_id
        ).order_by(NarrativeOccurrence.id.desc()).limit(self._bounded(limit)).all()

    def get_id_to_name_map(self, user_id, limit=None):
        """Bounded ``{narrative_id -> normalized_name}`` map for a user.

        Avoids an N+1 of per-id lookups when resolving occurrence narrative ids.
        """
        rows = Narrative.query.filter_by(user_id=user_id).with_entities(
            Narrative.id, Narrative.normalized_name
        ).limit(self._bounded(limit)).all()
        return {nid: name for nid, name in rows}

    # ------------------------------------------------------------ occurrences

    def get_occurrence(self, narrative_id, analysis_id, source):
        """Explicit existence check backing uq_narrative_occurrence_*.

        All three columns are NOT NULL, so PostgreSQL's "NULLs are distinct"
        behaviour does not apply here and this lookup is exact.
        """
        return NarrativeOccurrence.query.filter_by(
            narrative_id=narrative_id, analysis_id=analysis_id, source=source
        ).first()

    def get_occurrence_for_analysis(self, narrative_id, analysis_id):
        """Existence check ignoring ``source``.

        Used before insert so that a re-analysis which resolves a different
        primary source updates the existing row rather than creating a second
        occurrence for the same (narrative, analysis) pair.
        """
        return NarrativeOccurrence.query.filter_by(
            narrative_id=narrative_id, analysis_id=analysis_id
        ).order_by(NarrativeOccurrence.id.asc()).first()

    def get_occurrence_stats(self, narrative_id):
        """Whole-table aggregate for one narrative - no GROUP BY needed.

        Returns occurrence count, DISTINCT platform count, the real first/last
        ``occurred_at``, and the strongest/mean observed occurrence risk.
        ``platform_count`` is derived from stored rows, never inferred from text
        similarity. Risk aggregates are ``None`` when there are no rows, so
        "unavailable" stays distinguishable from 0.0.
        """
        row = db.session.query(
            func.count(NarrativeOccurrence.id).label('occurrence_count'),
            func.count(func.distinct(NarrativeOccurrence.platform)).label('platform_count'),
            func.min(NarrativeOccurrence.occurred_at).label('first_seen_at'),
            func.max(NarrativeOccurrence.occurred_at).label('last_seen_at'),
            func.max(NarrativeOccurrence.risk_score).label('max_risk_score'),
            func.avg(NarrativeOccurrence.risk_score).label('avg_risk_score'),
            func.max(NarrativeOccurrence.relevance_score).label('max_relevance_score'),
        ).filter(NarrativeOccurrence.narrative_id == narrative_id).one()

        return {
            'occurrence_count': int(row.occurrence_count or 0),
            'platform_count': int(row.platform_count or 0),
            'first_seen_at': row.first_seen_at,
            'last_seen_at': row.last_seen_at,
            'max_risk_score': (float(row.max_risk_score)
                               if row.max_risk_score is not None else None),
            'avg_risk_score': (round(float(row.avg_risk_score), 1)
                               if row.avg_risk_score is not None else None),
            'max_relevance_score': (float(row.max_relevance_score)
                                    if row.max_relevance_score is not None else None),
        }

    def get_platforms_for_narrative(self, narrative_id):
        """DISTINCT platforms actually stored for a narrative (sorted)."""
        rows = db.session.query(NarrativeOccurrence.platform).filter(
            NarrativeOccurrence.narrative_id == narrative_id
        ).group_by(NarrativeOccurrence.platform).all()
        return sorted(r[0] for r in rows if r[0])

    # -------------------------------------------------- user-level aggregates

    def get_platform_distribution(self, user_id):
        """Occurrence counts per platform for a user.

        GROUP BY lists the only selected non-aggregate column explicitly.
        """
        rows = db.session.query(
            NarrativeOccurrence.platform,
            func.count(NarrativeOccurrence.id).label('occurrences'),
        ).filter(
            NarrativeOccurrence.user_id == user_id
        ).group_by(
            NarrativeOccurrence.platform
        ).order_by(
            func.count(NarrativeOccurrence.id).desc(),
            NarrativeOccurrence.platform.asc(),
        ).all()
        return {r.platform: int(r.occurrences) for r in rows}

    def get_category_distribution(self, user_id):
        """Narrative counts and mean risk per category.

        Both selected non-aggregate columns would be a GROUP BY error on
        PostgreSQL if omitted; ``category`` is grouped explicitly.
        """
        rows = db.session.query(
            Narrative.category,
            func.count(Narrative.id).label('narratives'),
            func.avg(Narrative.risk_score).label('avg_risk'),
        ).filter(
            Narrative.user_id == user_id
        ).group_by(
            Narrative.category
        ).order_by(
            func.count(Narrative.id).desc(), Narrative.category.asc()
        ).all()
        return [
            {
                'category': r.category,
                'narratives': int(r.narratives),
                'avg_risk': round(float(r.avg_risk or 0.0), 1),
            }
            for r in rows
        ]

    def get_cross_platform_narratives(self, user_id, limit=10):
        """Narratives with occurrences on more than one platform.

        Uses ``HAVING COUNT(DISTINCT platform) > 1`` over stored rows - actual
        platform diversity, not textual inference. Every selected non-aggregate
        column appears in the GROUP BY, which PostgreSQL requires.
        """
        rows = db.session.query(
            Narrative.id,
            Narrative.name,
            Narrative.normalized_name,
            Narrative.category,
            Narrative.risk_score,
            func.count(func.distinct(NarrativeOccurrence.platform)).label('platform_count'),
            func.count(NarrativeOccurrence.id).label('occurrence_count'),
        ).join(
            NarrativeOccurrence, NarrativeOccurrence.narrative_id == Narrative.id
        ).filter(
            Narrative.user_id == user_id
        ).group_by(
            Narrative.id,
            Narrative.name,
            Narrative.normalized_name,
            Narrative.category,
            Narrative.risk_score,
        ).having(
            func.count(func.distinct(NarrativeOccurrence.platform)) > 1
        ).order_by(
            func.count(func.distinct(NarrativeOccurrence.platform)).desc(),
            Narrative.risk_score.desc(),
            Narrative.id.asc(),
        ).limit(self._bounded(limit)).all()

        return [
            {
                'id': r.id,
                'name': r.name,
                'normalized_name': r.normalized_name,
                'category': r.category,
                'risk_score': r.risk_score,
                'platform_count': int(r.platform_count),
                'occurrence_count': int(r.occurrence_count),
            }
            for r in rows
        ]

    def count_for_user(self, user_id):
        return Narrative.query.filter_by(user_id=user_id).count()
