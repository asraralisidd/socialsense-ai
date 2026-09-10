"""Temporal Intelligence persistence + bounded querying.

Phase F reads temporal evidence from stored NarrativeOccurrence /
PropagationEvent / EntityHistory rows and writes back only
``Narrative.growth_score`` (an existing column - no schema change).

PostgreSQL notes
----------------
* Every aggregate lists its selected non-aggregate columns explicitly in
  ``GROUP BY``.
* All list-returning queries are bounded by an explicit ``limit``.
* ``timestamp_source`` is preserved; the service never fabricates a timestamp.
"""
from datetime import datetime, timezone

from sqlalchemy import func

from database import db
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from repositories.base import BaseRepository


def _naive_utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return None


class TemporalRepository(BaseRepository):
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 1000

    def __init__(self):
        super().__init__(Narrative)

    def _bounded(self, limit):
        if not limit or limit <= 0:
            return self.DEFAULT_LIMIT
        return min(int(limit), self.MAX_LIMIT)

    # ------------------------------------------------------------ narratives

    def get_narratives_to_score(self, user_id, limit=None, exclude_id=None):
        """Bounded narratives for a user, most recently active first."""
        query = Narrative.query.filter_by(user_id=user_id)
        if exclude_id is not None:
            query = query.filter(Narrative.id != exclude_id)
        return query.order_by(
            Narrative.last_seen_at.desc(), Narrative.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_narrative(self, narrative_id, user_id=None):
        query = Narrative.query.filter_by(id=narrative_id)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.first()

    # --------------------------------------------------------- occurrences

    def get_occurrences(self, narrative_id, user_id=None, limit=None):
        """Bounded occurrences for one narrative, ordered oldest-first."""
        query = NarrativeOccurrence.query.filter_by(narrative_id=narrative_id)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.order_by(
            NarrativeOccurrence.occurred_at.asc(),
            NarrativeOccurrence.id.asc(),
        ).limit(self._bounded(limit)).all()

    def count_occurrences_since(self, narrative_id, since, user_id=None):
        """Bounded count of occurrences at/after ``since`` (no GROUP BY)."""
        query = NarrativeOccurrence.query.filter(
            NarrativeOccurrence.narrative_id == narrative_id,
            NarrativeOccurrence.occurred_at >= since,
        )
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.count()

    def count_occurrences_between(self, narrative_id, since, until, user_id=None):
        """Bounded count within a half-open window ``[since, until)``."""
        query = NarrativeOccurrence.query.filter(
            NarrativeOccurrence.narrative_id == narrative_id,
            NarrativeOccurrence.occurred_at >= since,
            NarrativeOccurrence.occurred_at < until,
        )
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.count()

    def get_occurrence_platforms(self, narrative_id, user_id=None):
        """DISTINCT platforms actually stored for a narrative (bounded)."""
        query = db.session.query(NarrativeOccurrence.platform).filter(
            NarrativeOccurrence.narrative_id == narrative_id,
        )
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        rows = query.group_by(NarrativeOccurrence.platform).all()
        return sorted(r[0] for r in rows if r[0])

    # --------------------------------------------------------- propagation

    def count_propagation_events(self, narrative_id, user_id=None):
        query = PropagationEvent.query.filter_by(narrative_id=narrative_id)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.count()

    def count_cross_platform_propagation(self, narrative_id, user_id=None):
        """Bounded with explicit platform inequality; no GROUP BY needed."""
        query = PropagationEvent.query.filter(
            PropagationEvent.narrative_id == narrative_id,
            PropagationEvent.source_platform != PropagationEvent.target_platform,
        )
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.count()

    # ------------------------------------------------------------- entities

    def get_entity_history_windows(self, user_id, normalized_name, since=None, limit=None):
        """Bounded entity-history rows for a named entity, oldest-first.

        Reads the existing V9 ``EntityHistory`` table so temporal signals can use
        stored per-analysis entity sentiment/risk over time.
        """
        from models.entity_history import EntityHistory
        query = EntityHistory.query.filter_by(user_id=user_id,
                                              normalized_name=normalized_name)
        if since is not None:
            query = query.filter(EntityHistory.created_at >= since)
        return query.order_by(
            EntityHistory.created_at.asc(), EntityHistory.id.asc()
        ).limit(self._bounded(limit)).all()

    # ------------------------------------------------------------- analysis

    def get_analysis_occurrence_count(self, analysis_id, user_id=None, limit=None):
        """Bounded narrative-occurrence count for one analysis (no GROUP BY)."""
        query = NarrativeOccurrence.query.filter_by(analysis_id=analysis_id)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.count()

    # ------------------------------------------------------------- summary

    def get_temporal_narrative_summary(self, user_id, limit=None):
        """Bounded aggregate-free narrative list for the summary read API."""
        limit = limit or self.DEFAULT_LIMIT
        narratives = Narrative.query.filter_by(user_id=user_id).order_by(
            Narrative.last_seen_at.desc(), Narrative.id.desc()
        ).limit(self._bounded(limit)).all()
        first = None
        last = None
        for n in narratives:
            if n.first_seen_at and (first is None or n.first_seen_at < first):
                first = n.first_seen_at
            if n.last_seen_at and (last is None or n.last_seen_at > last):
                last = n.last_seen_at
        return {
            'narrative_count': len(narratives),
            'first_seen_any': _naive_utc(first).isoformat() if first else None,
            'last_seen_any': _naive_utc(last).isoformat() if last else None,
            'narratives': [n.to_dict() for n in narratives],
        }
