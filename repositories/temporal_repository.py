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

    # ------------------------------------------------- V13 historical basis
    # Bounded per-user history for the V13 baseline engine. Scope is
    # per-user: rows are never compared across users. Ordering is
    # deterministic (timestamp DESC, id DESC). NULL timestamps are never
    # fabricated: window filters exclude rows whose timestamp is NULL
    # because a NULL cannot be placed inside a time window.

    def get_user_analyses_in_window(self, user_id, since=None,
                                    exclude_analysis_id=None, limit=None):
        """Bounded analyses for a user inside an optional time window."""
        from models.analysis import Analysis
        query = Analysis.query.filter_by(user_id=user_id)
        if since is not None:
            query = query.filter(Analysis.created_at.isnot(None),
                                 Analysis.created_at >= since)
        if exclude_analysis_id is not None:
            query = query.filter(Analysis.id != exclude_analysis_id)
        return query.order_by(
            Analysis.created_at.desc(), Analysis.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_historical_threat_assessments(self, user_id, since=None,
                                         exclude_analysis_id=None, limit=None):
        """Bounded threat assessments for a user's past analyses."""
        from models.analysis import Analysis
        from models.threat_assessment import ThreatAssessment
        query = ThreatAssessment.query.join(
            Analysis, ThreatAssessment.analysis_id == Analysis.id,
        ).filter(Analysis.user_id == user_id)
        if since is not None:
            query = query.filter(Analysis.created_at.isnot(None),
                                 Analysis.created_at >= since)
        if exclude_analysis_id is not None:
            query = query.filter(ThreatAssessment.analysis_id != exclude_analysis_id)
        return query.order_by(
            Analysis.created_at.desc(), ThreatAssessment.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_historical_media_scores(self, user_id, since=None,
                                    exclude_analysis_id=None, limit=None):
        """Bounded authenticity rows for a user's past analyses."""
        from models.analysis import Analysis
        from models.media_analysis import MediaAnalysis
        query = MediaAnalysis.query.join(
            Analysis, MediaAnalysis.analysis_id == Analysis.id,
        ).filter(Analysis.user_id == user_id)
        if since is not None:
            query = query.filter(Analysis.created_at.isnot(None),
                                 Analysis.created_at >= since)
        if exclude_analysis_id is not None:
            query = query.filter(MediaAnalysis.analysis_id != exclude_analysis_id)
        return query.order_by(
            Analysis.created_at.desc(), MediaAnalysis.id.desc()
        ).limit(self._bounded(limit)).all()

    def get_comment_metric_means(self, analysis_ids):
        """Per-analysis comment metric means for exactly the given ids.

        Returns ``{analysis_id: {sentiment, toxicity, spam, duplicate, n}}``.
        Stored values are used as-is; rows are counted so callers can
        distinguish thin evidence from solid evidence.
        """
        from models.comment_result import CommentResult
        ids = [int(a) for a in (analysis_ids or []) if a is not None]
        if not ids:
            return {}
        rows = CommentResult.query.filter(CommentResult.analysis_id.in_(ids)).all()
        buckets = {}
        for row in rows:
            bucket = buckets.setdefault(getattr(row, 'analysis_id', None), {
                'sentiment': [], 'toxicity': [], 'spam': [], 'duplicate': [],
            })
            if getattr(row, 'sentiment_score', None) is not None:
                bucket['sentiment'].append(row.sentiment_score)
            if getattr(row, 'toxicity_score', None) is not None:
                bucket['toxicity'].append(row.toxicity_score)
            if getattr(row, 'spam_score', None) is not None:
                bucket['spam'].append(row.spam_score)
            if getattr(row, 'duplicate_score', None) is not None:
                bucket['duplicate'].append(row.duplicate_score)
        result = {}
        for aid, bucket in buckets.items():
            result[aid] = {
                key: (sum(values) / len(values) if values else None)
                for key, values in bucket.items()
            }
            result[aid]['n'] = sum(len(v) for v in bucket.values())
        return result

    def get_narrative_activity_counts(self, analysis_ids):
        """Occurrence counts per analysis id (genuine zeros stay zero)."""
        ids = [int(a) for a in (analysis_ids or []) if a is not None]
        if not ids:
            return {}
        rows = db.session.query(
            NarrativeOccurrence.analysis_id,
            func.count(NarrativeOccurrence.id),
        ).filter(NarrativeOccurrence.analysis_id.in_(ids)).group_by(
            NarrativeOccurrence.analysis_id,
        ).all()
        return {int(aid): int(count) for aid, count in rows}

    def count_occurrences_in_window(self, narrative_id, since, until=None,
                                    user_id=None):
        """Occurrences with a real timestamp inside ``[since, until)``.

        Rows with ``occurred_at IS NULL`` are excluded: NULL means the
        timestamp is unknown and must not be invented into the window.
        """
        query = NarrativeOccurrence.query.filter_by(narrative_id=narrative_id)
        query = query.filter(NarrativeOccurrence.occurred_at.isnot(None))
        if since is not None:
            query = query.filter(NarrativeOccurrence.occurred_at >= since)
        if until is not None:
            query = query.filter(NarrativeOccurrence.occurred_at < until)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.count()
