"""V12 Phase F - Temporal Intelligence Service.

Detects **possible** temporal/trend signals from stored narrative occurrences and
propagation events, and updates each narrative's ``growth_score`` from real
temporal evidence (replacing the Phase C placeholder).

CAPABILITY HONESTY
------------------
Heuristic and rule-based. No trained model, no external service. Temporal
signals describe *observed* recurrence, recency, span and cross-platform spread;
they never claim causality, intent, authorship, orchestration or conspiracy.

WHAT IT DOES
------------
For each of the user's narratives (bounded), it gathers occurrence rows
(``NarrativeOccurrence``) and propagation counts (``PropagationEvent``), computes
an explainable growth score in 0-100, and persists it back to
``Narrative.growth_score`` - an existing column, so **no schema change is
required**.

UNAVAILABLE != ZERO
-------------------
A signal that cannot be computed (e.g. no occurrences, or a NULL timestamps
case) is reported as ``None`` and excluded from the weighted combination, whose
remaining weights are renormalized (the V11/V12 pattern). It is never silently
coerced to 0.0. ``timestamp_source`` is preserved; timestamps are never invented.

BOUNDS
------
Narratives scored, occurrences read per narrative, propagation counts and
repository limits are all capped by class constants. No unbounded history scan,
no O(n²) over all comments, and a single transaction per run.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from database import db
from repositories.temporal_repository import TemporalRepository
from services.text_similarity_service import TextSimilarityService

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return None


class TemporalIntelligenceService:
    ENABLE_KEY = 'ENABLE_TEMPORAL_INTELLIGENCE'

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_temporal_bucket'

    #: Max narratives scored in a run.
    MAX_NARRATIVES_SCORED = 50
    #: Max occurrences examined per narrative.
    MAX_OCCURRENCES_PER_NARRATIVE = 200
    #: Recency half-life (days): the window over which recency decays.
    RECENCY_HALF_LIFE_DAYS = 14
    #: Window (days) for judging "recent growth" vs an earlier baseline.
    GROWTH_WINDOW_DAYS = 7
    MAX_EVIDENCE_SAMPLES = 3
    MAX_EVIDENCE_ENTITIES = 5

    #: Growth component weights. Renormalized over available signals.
    GROWTH_WEIGHTS = {
        'recency': 0.35,
        'span': 0.15,
        'trend': 0.20,
        'recurrence': 0.15,
        'cross_platform': 0.10,
        'propagation': 0.05,
    }

    def __init__(self):
        self.temporal_repo = TemporalRepository()
        self.similarity = TextSimilarityService()

    # ----------------------------------------------------------------- config

    def _cfg(self, key, default):
        try:
            from flask import current_app
            if current_app:
                return current_app.config.get(key, default)
        except Exception:
            pass
        return default

    # ---------------------------------------------------------------- entry

    def analyze(self, analysis, narratives=None):
        """Compute + persist growth scores for a user's narratives.

        ``narratives`` is an optional precomputed list of ``Narrative`` for the
        current analysis; if omitted, the service loads a bounded set itself.
        Returns a component-contract dict.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('Temporal intelligence is disabled by configuration.')

        # Resolve the user scope from the analysis, or from the provided
        # narratives when only a bare list is supplied (e.g. in tests).
        user_id = analysis.user_id if analysis is not None else None
        if user_id is None and narratives:
            user_id = getattr(narratives[0], 'user_id', None)
        if user_id is None:
            return self._unavailable('No user context; temporal signals cannot be computed.')

        try:
            narratives = self._resolve_narratives(user_id, narratives)
            if not narratives:
                return self._unavailable(
                    'No narratives available; temporal signals cannot be computed.')

            results = []
            for narrative in narratives:
                signal = self._score_narrative(user_id, narrative)
                if signal is not None:
                    results.append(signal)

            if not results:
                return self._unavailable(
                    'No narrative had sufficient occurrence data for temporal scoring.')

            self._persist_growth(user_id, results)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Temporal intelligence failed: {exc}')
            return self._unavailable('Temporal computation failed; rolled back.')

        return self._build_result(user_id, results)

    # ------------------------------------------------------- narrative set

    def _resolve_narratives(self, user_id, narratives):
        if narratives is not None:
            out = []
            seen = set()
            for n in narratives:
                if getattr(n, 'id', None) in seen:
                    continue
                seen.add(n.id)
                out.append(n)
            return out
        limit = int(self._cfg('TEMPORAL_MAX_NARRATIVES', self.MAX_NARRATIVES_SCORED))
        return self.temporal_repo.get_narratives_to_score(user_id, limit=limit)

    # ------------------------------------------------------------- scoring

    def _score_narrative(self, user_id, narrative):
        """Compute an explainable growth score for one narrative.

        Reads bounded occurrences + propagation events. Returns a signal dict or
        ``None`` when there is no usable occurrence timestamp.
        """
        occurrence_limit = int(self._cfg('TEMPORAL_MAX_OCCURRENCES',
                                         self.MAX_OCCURRENCES_PER_NARRATIVE))
        occurrences = self.temporal_repo.get_occurrences(
            narrative.id, user_id=user_id, limit=occurrence_limit)
        if not occurrences:
            return None

        timestamps = [o.occurred_at for o in occurrences
                      if o.occurred_at is not None]
        if not timestamps:
            # occurred_at is NOT NULL in the model, so this is defensive only.
            return None

        timestamps = sorted(timestamps)
        first_seen = min(timestamps)
        last_seen = max(timestamps)
        now = _now()

        signals = {
            'recency': self._recency_score(last_seen, now),
            'span': self._span_score(first_seen, last_seen),
            'trend': self._trend_score(first_seen, last_seen, occurrences),
            'recurrence': self._recurrence_score(occurrences, last_seen, now),
            'cross_platform': self._cross_platform_score(user_id, narrative),
            'propagation': self._propagation_score(user_id, narrative),
        }
        available = {k: v for k, v in signals.items() if v is not None}
        unavailable = sorted(k for k, v in signals.items() if v is None)

        total_weight = sum(self.GROWTH_WEIGHTS[k] for k in available)
        if total_weight <= 0:
            return None
        growth = self._clamp(round(
            sum(self.GROWTH_WEIGHTS[k] * v for k, v in available.items()) / total_weight, 1))

        return {
            'narrative_id': narrative.id,
            'normalized_name': narrative.normalized_name,
            'growth_score': growth,
            'signals': signals,
            'unavailable_signals': unavailable,
            'first_seen_at': first_seen,
            'last_seen_at': last_seen,
            'occurrence_count': len(occurrences),
            'platform_count': narrative.platform_count,
            'is_cross_platform': narrative.is_cross_platform,
            'reasons': self._reasons(narrative, signals, unavailable),
            'evidence': self._evidence(narrative, signals, first_seen, last_seen),
            'timestamp_source_preserved': True,
        }

    # ---------------------------------------------------------- sub-signals

    def _recency_score(self, last_seen, now):
        """Higher recency = more recent last occurrence.

        Decays exponentially with a bounded half-life so it never exceeds the
        observation window.
        """
        hours = (now - last_seen).total_seconds() / 3600.0
        if hours < 0:
            hours = 0.0
        half_life_hours = self.RECENCY_HALF_LIFE_DAYS * 24
        return self._clamp(round(100.0 * (0.5 ** min(hours / max(half_life_hours, 1), 40.0)), 1))

    def _span_score(self, first_seen, last_seen):
        """Longer-lived narrative = higher span score, saturating bounded."""
        days = max(0.0, (last_seen - first_seen).total_seconds() / 86400.0)
        return self._clamp(round(min(100.0, days * 10.0), 1))

    def _trend_score(self, first_seen, last_seen, occurrences):
        """Recent-half vs earlier-half occurrence growth (bounded by window)."""
        span = last_seen - first_seen
        if span <= timedelta(0):
            return self._clamp(50.0)
        midpoint = first_seen + span / 2
        earlier = [o for o in occurrences
                   if self._occ_ts(o) is not None and self._occ_ts(o) <= midpoint]
        recent = [o for o in occurrences
                  if self._occ_ts(o) is not None and self._occ_ts(o) > midpoint]
        base = 50.0
        if not earlier and recent:
            base = 75.0          # new + growing
        elif earlier and not recent:
            base = 25.0          # fading
        else:
            base = 50.0 + 25.0 * ((len(recent) - len(earlier)) /
                                  max(len(earlier) + len(recent), 1))
        return self._clamp(round(base, 1))

    def _recurrence_score(self, occurrences, last_seen, now):
        """Intensity: recently repeated occurrences within a bounded window."""
        window = timedelta(days=self.GROWTH_WINDOW_DAYS)
        recent = [o for o in occurrences
                  if self._occ_ts(o) is not None
                  and self._occ_ts(o) >= (last_seen - window)]
        intensity = len(recent)
        # saturate at 6 recent occurrences -> ~100
        return self._clamp(round(min(100.0, intensity * (100.0 / 6.0)), 1))

    @staticmethod
    def _occ_ts(occurrence):
        """Occurrence timestamp, tolerating ORM objects and plain dicts."""
        if isinstance(occurrence, dict):
            return _naive_utc(occurrence.get('occurred_at'))
        return _naive_utc(getattr(occurrence, 'occurred_at', None))

    def _cross_platform_score(self, user_id, narrative):
        """Platform spread from stored occurrences (never inferred)."""
        platforms = self.temporal_repo.get_occurrence_platforms(
            narrative.id, user_id=user_id)
        # 1 platform -> low, 2+ -> high
        return self._clamp(round(min(100.0, max(len(platforms) - 1, 0) * 60.0), 1))

    def _propagation_score(self, user_id, narrative):
        """Propagation activity involving this narrative (bounded count)."""
        count = self.temporal_repo.count_propagation_events(
            narrative.id, user_id=user_id)
        return self._clamp(round(min(100.0, count * 20.0), 1))

    # ------------------------------------------------------------ persistence

    def _persist_growth(self, user_id, results):
        """Write growth_score back to each Narrative - single commit for batch."""
        for result in results:
            narrative = self.temporal_repo.get_narrative(
                result['narrative_id'], user_id=user_id)
            if narrative is None:
                continue
            narrative.growth_score = result['growth_score']
            # Preserve existing evidence; annotate the temporal inputs. Build a
            # NEW dict so SQLAlchemy reliably detects the JSON column change.
            existing = dict(narrative.evidence or {})
            existing['temporal'] = {
                'method': self.DETECTION_METHOD,
                'capability': self.CAPABILITY,
                'signals': result['signals'],
                'unavailable_signals': result['unavailable_signals'],
                'reasons': result['reasons'],
                'first_seen_at': result['first_seen_at'].isoformat()
                if result['first_seen_at'] else None,
                'last_seen_at': result['last_seen_at'].isoformat()
                if result['last_seen_at'] else None,
            }
            narrative.evidence = existing
        db.session.commit()

    # ---------------------------------------------------------------- result

    def _build_result(self, user_id, results):
        scores = [r['growth_score'] for r in results]
        results_sorted = sorted(results, key=lambda r: r['growth_score'], reverse=True)
        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'narratives_scored': len(results),
            'max_growth_score': self._clamp(max(scores)) if scores else None,
            'avg_growth_score': (round(sum(scores) / len(scores), 1) if scores else None),
            'top_narratives': results_sorted[:5],
            'reasons': [r['reasons'][0] for r in results_sorted
                        if r.get('reasons')][:5],
            'limitations': self._limitations(),
        }

    def _unavailable(self, reason):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'narratives_scored': 0,
            'max_growth_score': None,
            'avg_growth_score': None,
            'top_narratives': [],
            'reasons': [reason],
            'limitations': self._limitations(),
        }

    def _limitations(self):
        return [
            'Temporal intelligence is heuristic and rule-based; it is not a '
            'trained classifier of trends or diffusion.',
            'Growth and trend signals describe observed occurrence recency, span, '
            'recurrence and platform spread; they do not establish intent, '
            'authorship, orchestration or causality.',
            'Scoring is bounded by narrative and occurrence caps; longer histories '
            'are sampled, not exhaustively scored.',
            'Timestamps come from platform data when present, else an analysis '
            'creation-time fallback; unavailable values remain unavailable and are '
            'excluded from the weighted combination.',
        ]

    # ---------------------------------------------------------------- utils

    def _reasons(self, narrative, signals, unavailable):
        reasons = []
        if narrative.occurrence_count > 1:
            reasons.append(
                f'Narrative "{narrative.normalized_name}" has occurred '
                f'{narrative.occurrence_count} times across '
                f'{narrative.platform_count} platform(s), spanning '
                f'{self._span_days(signals)} day(s).')
        if signals.get('recency') is not None and signals['recency'] >= 50:
            reasons.append('A recent occurrence makes this narrative '
                           'temporally active right now.')
        if signals.get('trend') is not None and signals['trend'] >= 60:
            reasons.append('Occurrence frequency rose in the recent half of its '
                           'observed window (possible growth).')
        if signals.get('cross_platform') is None:
            reasons.append('Cross-platform signal unavailable; excluded from growth.')
        for name in unavailable:
            if name != 'cross_platform':
                reasons.append(f'Temporal signal unavailable and excluded: {name}.')
        reasons.append('Temporal conclusions are heuristic and non-causal.')
        return reasons

    @staticmethod
    def _span_days(signals):
        if signals.get('span') is None:
            return 0
        return round(signals['span'] / 10.0, 1)

    def _evidence(self, narrative, signals, first_seen, last_seen):
        return {
            'detection_method': self.DETECTION_METHOD,
            'capability': self.CAPABILITY,
            'signals': signals,
            'first_seen_at': first_seen.isoformat() if first_seen else None,
            'last_seen_at': last_seen.isoformat() if last_seen else None,
            'occurrence_count': narrative.occurrence_count,
            'platform_count': narrative.platform_count,
            'cross_platform': narrative.is_cross_platform,
            'reasons': self._reasons(narrative, signals,
                                     sorted(k for k, v in signals.items() if v is None)),
        }

    def _clamp(self, value):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    # ---------------------------------------------------------------- read API

    def get_temporal_summary(self, user_id, limit=None):
        """Bounded read API for later Threat Assessment / UI phases."""
        return self.temporal_repo.get_temporal_narrative_summary(user_id, limit=limit)

    def get_narrative_temporal(self, narrative_id, user_id=None, limit=None):
        """Per-narrative temporal signal read API (recomputed, no writes)."""
        narrative = self.temporal_repo.get_narrative(narrative_id, user_id=user_id)
        if narrative is None:
            return None
        signal = self._score_narrative(user_id, narrative)
        if signal is None:
            return {'narrative_id': narrative_id,
                    'normalized_name': narrative.normalized_name,
                    'available': False}
        return {'narrative_id': narrative.id,
                'normalized_name': narrative.normalized_name,
                'available': True,
                'growth_score': signal['growth_score'],
                'signals': signal['signals'],
                'first_seen_at': signal['first_seen_at'].isoformat()
                if signal['first_seen_at'] else None,
                'last_seen_at': signal['last_seen_at'].isoformat()
                if signal['last_seen_at'] else None,
                'reasons': signal['reasons'],
                'capability': self.CAPABILITY,
                'detection_method': self.DETECTION_METHOD}
