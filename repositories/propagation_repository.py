"""Propagation persistence + bounded querying.

PostgreSQL notes
----------------
* ``uq_propagation_events_edge`` is ``(narrative_id, source_analysis_id,
  target_analysis_id, relationship_type)``. PostgreSQL treats NULLs as distinct,
  so the constraint does *not* deduplicate rows where ``narrative_id IS NULL``.
  The service layer therefore performs an explicit existence check before
  insert; the repository exposes ``edge_exists`` for that purpose.
* Every aggregate lists its non-aggregate columns explicitly in ``GROUP BY``.
* All list-returning queries are bounded by an explicit ``limit``.
"""
from sqlalchemy import or_

from database import db
from models.analysis import Analysis
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from repositories.base import BaseRepository


class PropagationRepository(BaseRepository):
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 500

    def __init__(self):
        super().__init__(PropagationEvent)

    def _bounded(self, limit):
        if not limit or limit <= 0:
            return self.DEFAULT_LIMIT
        return min(int(limit), self.MAX_LIMIT)

    # ---------------------------------------------------------------- events

    def edge_exists(self, narrative_id, source_analysis_id, target_analysis_id,
                    relationship_type):
        """Explicit existence check for a propagation edge.

        Needed because nullable ``narrative_id`` defeats the unique constraint
        on PostgreSQL (NULLs are distinct). Two edges are considered equal when
        their narrative_id BOTH equal ``narrative_id`` (including the NULL case),
        i.e. we compare on the same set of columns the constraint uses.
        """
        query = PropagationEvent.query.filter_by(
            source_analysis_id=source_analysis_id,
            target_analysis_id=target_analysis_id,
            relationship_type=relationship_type,
        )
        if narrative_id is None:
            query = query.filter(PropagationEvent.narrative_id.is_(None))
        else:
            query = query.filter(PropagationEvent.narrative_id == narrative_id)
        return query.first() is not None

    def get_edge(self, narrative_id, source_analysis_id, target_analysis_id,
                 relationship_type):
        query = PropagationEvent.query.filter_by(
            source_analysis_id=source_analysis_id,
            target_analysis_id=target_analysis_id,
            relationship_type=relationship_type,
        )
        if narrative_id is None:
            query = query.filter(PropagationEvent.narrative_id.is_(None))
        else:
            query = query.filter(PropagationEvent.narrative_id == narrative_id)
        return query.first()

    def get_for_analysis(self, analysis_id, limit=None):
        """All outgoing/incoming events for one analysis, strongest first."""
        rows = PropagationEvent.query.filter(
            or_(PropagationEvent.source_analysis_id == analysis_id,
                PropagationEvent.target_analysis_id == analysis_id)
        ).order_by(
            PropagationEvent.propagation_score.desc(), PropagationEvent.id.asc()
        ).limit(self._bounded(limit)).all()
        return rows

    def get_for_user(self, user_id, limit=100):
        return PropagationEvent.query.filter_by(user_id=user_id).order_by(
            PropagationEvent.occurred_at.desc(), PropagationEvent.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_cross_platform_events(self, user_id, limit=20):
        """Events where source and target are on different platforms.

        Uses explicit platform columns; every selected non-aggregate column is
        NOT grouped here (it is a filtered list query), so GROUP BY is avoided
        and the query stays PostgreSQL-safe on its own.
        """
        return PropagationEvent.query.filter(
            PropagationEvent.user_id == user_id,
            PropagationEvent.source_platform != PropagationEvent.target_platform,
        ).order_by(
            PropagationEvent.propagation_score.desc(), PropagationEvent.id.desc()
        ).limit(self._bounded(limit)).all()

    # --------------------------------------------- candidate analyses/narratives

    def get_recent_analyses(self, user_id, exclude_analysis_id=None, limit=None):
        """Bounded candidate set: the user's other analyses, most recent first.

        ``exclude_analysis_id`` removes the analysis currently being processed so
        it is not compared against itself.
        """
        query = Analysis.query.filter_by(user_id=user_id)
        if exclude_analysis_id is not None:
            query = query.filter(Analysis.id != exclude_analysis_id)
        return query.order_by(Analysis.created_at.desc()).limit(self._bounded(limit)).all()

    def get_narratives_for_user(self, user_id, limit=None):
        """Bounded narratives for a user, most recently updated first."""
        return Narrative.query.filter_by(user_id=user_id).order_by(
            Narrative.last_seen_at.desc(), Narrative.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_occurrences_for_analysis(self, analysis_id, limit=None):
        return NarrativeOccurrence.query.filter_by(
            analysis_id=analysis_id
        ).order_by(NarrativeOccurrence.id.desc()).limit(self._bounded(limit)).all()

    def get_analyses_for_narrative(self, narrative_id, user_id, limit=None):
        """Analyses carrying a given narrative (for cross-analysis pairing)."""
        rows = db.session.query(Analysis).join(
            NarrativeOccurrence, NarrativeOccurrence.analysis_id == Analysis.id
        ).filter(
            NarrativeOccurrence.narrative_id == narrative_id,
            Analysis.user_id == user_id,
        ).order_by(
            NarrativeOccurrence.occurred_at.desc(), Analysis.id.desc()
        ).limit(self._bounded(limit)).all()
        return rows

    def get_top_narratives_for_user(self, user_id, limit=20):
        """Bounded, aggregate-free, deterministic narrative list."""
        return Narrative.query.filter_by(user_id=user_id).order_by(
            Narrative.risk_score.desc(), Narrative.last_seen_at.desc(),
            Narrative.id.asc()
        ).limit(self._bounded(limit)).all()

    def get_cross_platform_narrative_count(self, user_id):
        """Number of the user's narratives that span more than one platform.

        Computed in Python over a bounded, full aggregate query (no GROUP BY on
        a mismatched projection) - PostgreSQL-safe and deterministic.
        """
        rows = db.session.query(
            NarrativeOccurrence.narrative_id,
            NarrativeOccurrence.platform,
        ).join(
            Narrative, Narrative.id == NarrativeOccurrence.narrative_id
        ).filter(
            Narrative.user_id == user_id
        ).all()
        seen = {}
        for narrative_id, platform in rows:
            seen.setdefault(narrative_id, set()).add(platform)
        return sum(1 for platforms in seen.values() if len(platforms) > 1)

    def count_for_user(self, user_id):
        return PropagationEvent.query.filter_by(user_id=user_id).count()
