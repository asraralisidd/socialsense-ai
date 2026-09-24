"""V13 cross-analysis comparison & narrative evolution (Phase C backend only).

Answers, from real stored rows: "how has the current intelligence changed
compared with previous analyses, and how have observed narratives evolved
over time?"

Two structured, UI-agnostic entry points:

* ``compare_with_history(user_id, current_analysis_id, ...)`` - per-metric
  current-vs-history comparison with provenance-tagged historical values.
* ``narrative_evolution(user_id, current_analysis_id, ...)`` - descriptive
  evolution state per narrative occurring in the current analysis.

Narrative evolution rules (deterministic, time-windowed)
--------------------------------------------------------
Let W = ``V13_HISTORY_WINDOW_DAYS``. The window ``[now-W, now)`` splits
into a prior half ``[now-W, now-W/2)`` and a recent half
``[now-W/2, now)``. Only occurrences with a real stored ``occurred_at``
are placed; NULL timestamps are counted as ``unpositioned`` and disclosed,
never invented into a period. Only narratives with >= 1 occurrence in the
current analysis are evaluated:

* ``emerging`` - no positioned historical occurrences outside the current
  analysis (insufficient evidence of prior presence).
* ``reappearing`` - positioned history exists, but only in the prior half
  (observable gap: an empty recent half).
* ``declining`` - positioned history exists in both halves (or recent
  only) but the recent rate (recent + current occurrences per half-window)
  is below ``DECLINE_RATE_RATIO`` (0.5) of the prior rate.
* ``persistent`` - otherwise (observed across periods without decline).
* ``insufficient_history`` - history rows exist but none can be positioned
  (all NULL timestamps), so no state is determinable.

All labels are descriptive, never causal. Every result carries the
non-causal disclaimer and explicit limitations.

Scope: per-user only. Channel/video-scoped comparison is deferred.
"""
import logging
from datetime import datetime, timedelta, timezone

from database import db
from models.narrative_occurrence import NarrativeOccurrence
from repositories.narrative_repository import NarrativeRepository
from repositories.temporal_repository import TemporalRepository
from services.historical_context_service import HistoricalContextService

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CrossAnalysisService:
    """Leaf service: repository/database primitives + Phase B services only."""

    # Reuses the Phase B historical gate; Phase C is historical intelligence.
    ENABLE_KEY = 'ENABLE_V13_HISTORICAL_BASELINE'

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_cross_analysis'
    EVOLUTION_METHOD = 'heuristic_narrative_evolution'

    # Recent rate below this fraction of the prior rate counts as declining.
    DECLINE_RATE_RATIO = 0.5

    DISCLAIMER = (
        'Comparison and evolution labels are descriptive and associative, '
        'not causal: a change or deviation does not prove manipulation, '
        'coordination, or threat.'
    )

    # metric -> (source_table, kind) for provenance tagging.
    METRIC_SOURCES = {
        'threat': ('threat_assessments', 'assessment'),
        'authenticity': ('media_analyses', 'assessment'),
        'sentiment': ('comment_results', 'aggregate'),
        'toxicity': ('comment_results', 'aggregate'),
        'spam': ('comment_results', 'aggregate'),
        'duplicate': ('comment_results', 'aggregate'),
        'narrative_activity': ('narrative_occurrences', 'aggregate'),
    }

    STATE_EMERGING = 'emerging'
    STATE_PERSISTENT = 'persistent'
    STATE_REAPPEARING = 'reappearing'
    STATE_DECLINING = 'declining'
    STATE_INSUFFICIENT = 'insufficient_history'
    STATE_UNAVAILABLE = 'unavailable'

    def __init__(self):
        self.temporal_repo = TemporalRepository()
        self.narrative_repo = NarrativeRepository()
        self.baseline_service = HistoricalContextService()

    # ------------------------------------------------------------------ cfg

    def _cfg(self, key, default):
        try:
            from flask import current_app
            if current_app:
                return current_app.config.get(key, default)
        except Exception:
            pass
        return default

    def _window_days(self):
        try:
            return max(1, int(self._cfg('V13_HISTORY_WINDOW_DAYS', 90)))
        except (TypeError, ValueError):
            return 90

    def _max_analyses(self):
        try:
            return max(1, min(int(self._cfg('V13_MAX_HISTORY_ANALYSES', 20)), 500))
        except (TypeError, ValueError):
            return 20

    def _min_sample(self):
        try:
            return max(1, int(self._cfg('V13_MIN_HISTORY_SAMPLE', 3)))
        except (TypeError, ValueError):
            return 3

    # ------------------------------------------------------- cross-analysis

    def compare_with_history(self, user_id, current_analysis_id,
                             window_days=None, max_analyses=None, prefetch=None):
        """Compare the current analysis against bounded user history.

        ``prefetch`` reuse is request-local (no cross-request or cross-user
        cache); when provided the already-loaded window/series is reused
        with zero extra queries.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('V13 historical intelligence is disabled.')
        if user_id is None or current_analysis_id is None:
            return self._unavailable('User and current analysis are required.')

        if prefetch is not None:
            window = prefetch['window']
            minimum = prefetch['minimum']
            past_ids = prefetch['past_ids']
            series = prefetch['series']
        else:
            window = self._int_or(window_days, self._window_days(), 1, 3650)
            limit = self._int_or(max_analyses, self._max_analyses(), 1, 500)
            minimum = self._min_sample()
            if window is None or limit is None:
                return self._unavailable('Invalid comparison bounds provided.')

            try:
                since = _now() - timedelta(days=window)
                past = self.temporal_repo.get_user_analyses_in_window(
                    user_id, since=since,
                    exclude_analysis_id=current_analysis_id, limit=limit)
                past_ids = sorted({a.id for a in past
                                   if getattr(a, 'id', None) is not None})
                series = self.baseline_service.get_metric_series(
                    user_id, past_ids, current_analysis_id)
            except Exception as exc:
                db.session.rollback()
                logger.warning(f'V13 cross-analysis query failed: {exc}')
                return self._unavailable('Cross-analysis queries failed; rolled back.')

        metrics = {}
        for name in HistoricalContextService.METRICS:
            metrics[name] = self._compare_metric(
                name, series[name], minimum, window)
        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'scope': 'user',
            'user_id': user_id,
            'current_analysis_id': current_analysis_id,
            'window_days': window,
            'historical_analysis_ids': past_ids,
            'metrics': metrics,
            'limitations': self._comparison_limitations(past_ids, minimum),
            'disclaimer': self.DISCLAIMER,
        }

    def _compare_metric(self, name, entry, minimum, window):
        current = entry['current']  # (aid, value, row_id)
        history = entry['history']  # [(aid, value, row_id)]
        table, kind = self.METRIC_SOURCES[name]
        current_value = current[1]
        current_ref = self._ref(table, kind, current[0], current[2])
        hist_points = [
            {'analysis_id': aid, 'value': value,
             'source_table': table,
             'source_id': rid if kind == 'assessment' else None,
             'ref': self._ref(table, kind, aid, rid if kind == 'assessment' else None)}
            for aid, value, rid in sorted(history, key=lambda h: h[0])
            if value is not None
        ]
        if current_value is None:
            return self._metric_entry(name, None, None, None, current_ref,
                                      hist_points, window, self.STATE_UNAVAILABLE)
        if len(hist_points) < minimum:
            return self._metric_entry(name, current_value, None, None, current_ref,
                                      hist_points, window, self.STATE_INSUFFICIENT)
        values = [p['value'] for p in hist_points]
        baseline = sum(values) / len(values)
        return self._metric_entry(name, current_value, baseline,
                                  current_value - baseline, current_ref,
                                  hist_points, window,
                                  HistoricalContextService.classify_deviation(
                                      current_value, baseline))

    @staticmethod
    def _ref(table, kind, analysis_id, row_id):
        if kind == 'assessment' and isinstance(row_id, int):
            return f'{table}:{row_id}'
        return f'analysis:{analysis_id} {kind}'

    @staticmethod
    def _metric_entry(name, current, baseline, delta, current_ref,
                      history, window, availability):
        return {
            'metric': name,
            'current': current,
            'current_ref': current_ref,
            'history': history,
            'baseline': baseline,
            'delta': delta,
            'sample_size': len(history),
            'window_days': window,
            'availability': availability,
        }

    @staticmethod
    def _comparison_limitations(past_ids, minimum):
        notes = [
            'History is scoped to analyses owned by the same user.',
            'Only stored values are compared; unavailable values are ignored, not zeroed.',
        ]
        if len(past_ids) < minimum:
            notes.append(
                f'Only {len(past_ids)} historical analyses available; '
                f'at least {minimum} are needed for labeled comparisons.')
        return notes

    # ------------------------------------------------- narrative evolution

    def narrative_evolution(self, user_id, current_analysis_id,
                            window_days=None, max_narratives=None):
        """Evolution states for narratives in the current analysis."""
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('V13 historical intelligence is disabled.')
        if user_id is None or current_analysis_id is None:
            return self._unavailable('User and current analysis are required.')

        window = self._int_or(window_days, self._window_days(), 1, 3650)
        if window is None:
            return self._unavailable('Invalid evolution bounds provided.')
        try:
            limit = self._int_or(max_narratives, 50, 1, 500)
        except TypeError:
            return self._unavailable('Invalid evolution bounds provided.')

        try:
            pairs = self.narrative_repo.get_for_analysis(
                current_analysis_id, limit=limit) or []
            narratives = self._first_elements(pairs)
            narratives = [n for n in narratives
                          if getattr(n, 'user_id', user_id) == user_id]
            narratives.sort(key=lambda n: (
                str(getattr(n, 'normalized_name', '') or ''),
                getattr(n, 'id', 0) or 0))
            now = _now()
            split = now - timedelta(days=window / 2.0)
            start = now - timedelta(days=window)
            items = [self._evolve_one(n, user_id, current_analysis_id,
                                      start, split, now, window)
                     for n in narratives]
        except Exception as exc:
            db.session.rollback()
            logger.warning(f'V13 narrative evolution failed: {exc}')
            return self._unavailable('Narrative evolution queries failed; rolled back.')

        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.EVOLUTION_METHOD,
            'scope': 'user',
            'user_id': user_id,
            'current_analysis_id': current_analysis_id,
            'window_days': window,
            'narratives': items,
            'limitations': [
                'States describe stored occurrence patterns only; they do not '
                'imply intent, coordination, or causality.',
                'Occurrences without a stored timestamp cannot be placed in '
                'time and are reported as unpositioned, never invented.',
                'Only narratives occurring in the current analysis are evaluated.',
            ],
            'disclaimer': self.DISCLAIMER,
        }

    @staticmethod
    def _first_elements(pairs):
        out = []
        for pair in pairs:
            row = None
            try:
                candidate = pair[0]
            except (TypeError, IndexError, KeyError):
                candidate = pair
            if candidate is not None and hasattr(candidate, 'id'):
                row = candidate
            elif hasattr(pair, 'id'):
                row = pair
            if row is not None:
                out.append(row)
        # de-duplicate by id, deterministic
        seen = set()
        unique = []
        for row in out:
            if row.id not in seen:
                seen.add(row.id)
                unique.append(row)
        return unique

    def _evolve_one(self, narrative, user_id, current_analysis_id,
                    start, split, now, window):
        nid = getattr(narrative, 'id', None)
        try:
            rows = NarrativeOccurrence.query.filter_by(
                narrative_id=nid, user_id=user_id).all()
        except Exception:
            rows = []
        current_count = sum(1 for r in rows
                            if getattr(r, 'analysis_id', None) == current_analysis_id)
        prior_count = 0
        recent_count = 0
        unpositioned = 0
        positioned_ts = []
        refs = []
        for row in rows:
            if getattr(row, 'analysis_id', None) == current_analysis_id:
                continue
            ts = getattr(row, 'occurred_at', None)
            if ts is None:
                unpositioned += 1
                continue
            try:
                naive = ts.replace(tzinfo=None) if getattr(ts, 'tzinfo', None) else ts
            except Exception:
                unpositioned += 1
                continue
            if naive < start:
                continue  # outside the window: not evidence for/against
            positioned_ts.append(naive)
            if naive < split:
                prior_count += 1
            else:
                recent_count += 1
            refs.append({
                'source_table': 'narrative_occurrences',
                'source_id': getattr(row, 'id', None),
                'ref': f"narrative_occurrence:{getattr(row, 'id', None)}",
            })
        refs.sort(key=lambda r: (r['source_id'] is None, r['source_id'] or 0))
        state, limitation = self.classify_evolution(
            current_count, prior_count, recent_count, unpositioned)
        gap_days = None
        if positioned_ts:
            gap_days = round((max(positioned_ts) - min(positioned_ts)).total_seconds() / 86400.0, 1) \
                if len(positioned_ts) > 1 else 0.0
        return {
            'narrative_id': nid,
            'name': getattr(narrative, 'name', None),
            'normalized_name': getattr(narrative, 'normalized_name', None),
            'state': state,
            'current_occurrences': current_count,
            'prior_occurrences': prior_count,
            'recent_occurrences': recent_count,
            'unpositioned_occurrences': unpositioned,
            'gap_days': gap_days,
            'first_seen_at': self._iso(getattr(narrative, 'first_seen_at', None)),
            'last_seen_at': self._iso(getattr(narrative, 'last_seen_at', None)),
            'evidence_refs': refs[:25],
            'limitation': limitation,
        }

    @classmethod
    def classify_evolution(cls, current_count, prior_count, recent_count,
                           unpositioned=0):
        """Deterministic descriptive state from positioned occurrence counts.

        Returns ``(state, limitation)``. No timestamps are invented here;
        callers place occurrences into periods from stored ``occurred_at``.
        """
        for value in (current_count, prior_count, recent_count, unpositioned):
            if not isinstance(value, int) or value < 0:
                return cls.STATE_UNAVAILABLE, 'Invalid occurrence counts provided.'
        if current_count == 0:
            return cls.STATE_UNAVAILABLE, \
                'Narrative does not occur in the current analysis.'
        positioned = prior_count + recent_count
        if positioned == 0:
            if unpositioned > 0:
                return cls.STATE_INSUFFICIENT, \
                    (f'{unpositioned} historical occurrences have no stored '
                     'timestamp and cannot be placed in time.')
            return cls.STATE_EMERGING, \
                'No stored occurrences outside the current analysis.'
        if recent_count == 0:
            return cls.STATE_REAPPEARING, \
                'Historical occurrences exist only before the recent period (observable gap).'
        half = 2.0  # prior and recent halves have equal length
        prior_rate = prior_count / half if prior_count else 0.0
        recent_rate = (recent_count + current_count) / half
        if prior_rate > 0 and recent_rate < prior_rate * cls.DECLINE_RATE_RATIO:
            return cls.STATE_DECLINING, \
                'Recent occurrence rate is below half the prior rate.'
        return cls.STATE_PERSISTENT, \
            'Observed in the current analysis and across prior periods.'

    # ---------------------------------------------------------------- helpers

    def _int_or(self, override, default, minimum, maximum):
        try:
            value = int(default if override is None else override)
        except (TypeError, ValueError):
            return None
        return max(minimum, min(value, maximum))

    @staticmethod
    def _iso(value):
        try:
            return value.isoformat() if value is not None else None
        except Exception:
            return None

    def _unavailable(self, reason):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'scope': 'user',
            'metrics': {},
            'narratives': [],
            'reason': reason,
            'disclaimer': self.DISCLAIMER,
        }
