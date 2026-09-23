"""V13 historical baseline service (Phase B backend only).

Computes bounded per-user historical baselines from REAL stored rows and
classifies the current analysis against them with hedged, non-causal
labels. No UI, no export, no pipeline side effects in this phase.

Scope
-----
Per-user: only analyses owned by the same user are compared. Channel or
video-scoped baselines are deferred (not implemented here).

Rules
-----
* At most ``V13_MAX_HISTORY_ANALYSES`` past analyses inside the last
  ``V13_HISTORY_WINDOW_DAYS`` days, deterministic order
  (``created_at DESC, id DESC``).
* ``None`` (unavailable) values are ignored, never treated as zero.
* Fewer than ``V13_MIN_HISTORY_SAMPLE`` historical values yields
  ``insufficient_history``; a missing current value yields
  ``unavailable``.
* Deviation labels are associative only; the result carries an explicit
  non-causal disclaimer.
"""
import logging
from datetime import datetime, timezone

from database import db
from repositories.temporal_repository import TemporalRepository
from repositories.threat_assessment_repository import ThreatAssessmentRepository

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HistoricalContextService:
    """Leaf service: repository/database primitives only."""

    ENABLE_KEY = 'ENABLE_V13_HISTORICAL_BASELINE'

    MAX_HISTORY_ANALYSES = 20
    HISTORY_WINDOW_DAYS = 90
    MIN_HISTORY_SAMPLE = 3

    # Relative-deviation thresholds: |rel| < moderate -> typical,
    # < substantial -> moderately above/below, else substantially above/below.
    MODERATE_THRESHOLD = 0.10
    SUBSTANTIAL_THRESHOLD = 0.30

    LABEL_TYPICAL = 'typical'
    LABEL_MODERATE_ABOVE = 'moderately_above_baseline'
    LABEL_SUBSTANTIAL_ABOVE = 'substantially_above_baseline'
    LABEL_MODERATE_BELOW = 'moderately_below_baseline'
    LABEL_SUBSTANTIAL_BELOW = 'substantially_below_baseline'
    LABEL_INSUFFICIENT = 'insufficient_history'
    LABEL_UNAVAILABLE = 'unavailable'

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_historical_baseline'

    DISCLAIMER = (
        'Historical deviation is associative, not causal: a deviation '
        'does not prove manipulation, coordination, or threat.'
    )

    # metric -> (kind, higher_is) for documentation; labels stay neutral.
    METRICS = (
        'threat',
        'authenticity',
        'sentiment',
        'toxicity',
        'spam',
        'duplicate',
        'narrative_activity',
    )

    def __init__(self):
        self.temporal_repo = TemporalRepository()
        self.threat_repo = ThreatAssessmentRepository()

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
            return max(1, int(self._cfg('V13_HISTORY_WINDOW_DAYS',
                                       self.HISTORY_WINDOW_DAYS)))
        except (TypeError, ValueError):
            return self.HISTORY_WINDOW_DAYS

    def _max_analyses(self):
        try:
            return max(1, min(int(self._cfg('V13_MAX_HISTORY_ANALYSES',
                                           self.MAX_HISTORY_ANALYSES)), 500))
        except (TypeError, ValueError):
            return self.MAX_HISTORY_ANALYSES

    def _min_sample(self):
        try:
            return max(1, int(self._cfg('V13_MIN_HISTORY_SAMPLE',
                                       self.MIN_HISTORY_SAMPLE)))
        except (TypeError, ValueError):
            return self.MIN_HISTORY_SAMPLE

    # ------------------------------------------------------------- baseline

    def compute_baseline(self, user_id, current_analysis_id=None,
                         window_days=None, max_analyses=None):
        """Bounded per-metric baseline comparison for one user.

        Returns ``{'available', 'metrics': {name: {...}}, ...}`` where each
        metric entry is ``{metric, current, baseline, deviation,
        sample_size, window_days, availability}``.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('V13 historical baseline is disabled by configuration.')
        if user_id is None:
            return self._unavailable('No user provided; baseline cannot be computed.')

        window = window_days if window_days else self._window_days()
        limit = max_analyses if max_analyses else self._max_analyses()
        minimum = self._min_sample()
        try:
            window = max(1, int(window))
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            return self._unavailable('Invalid baseline bounds provided.')

        from datetime import timedelta
        since = _now() - timedelta(days=window)
        try:
            past = self.temporal_repo.get_user_analyses_in_window(
                user_id, since=since,
                exclude_analysis_id=current_analysis_id, limit=limit)
            past_ids = [a.id for a in past if getattr(a, 'id', None) is not None]
            series = self._collect_series(user_id, past_ids, current_analysis_id)
        except Exception as exc:
            db.session.rollback()
            logger.warning(f'V13 baseline query failed: {exc}')
            return self._unavailable('Historical baseline queries failed; rolled back.')

        metrics = {}
        for name in self.METRICS:
            current, history = series.get(name, (None, []))
            metrics[name] = self._compare(name, current, history, minimum, window)
        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'scope': 'user',
            'user_id': user_id,
            'current_analysis_id': current_analysis_id,
            'window_days': window,
            'analyses_considered': len(past_ids),
            'metrics': metrics,
            'disclaimer': self.DISCLAIMER,
        }

    # -------------------------------------------------------------- series

    def get_metric_series(self, user_id, past_ids, current_analysis_id):
        """Per-analysis value series: ``{metric: {'current': (aid, value),
        'history': [(aid, value), ...]}}``.

        Values come from real stored rows; unavailable values are omitted
        (never zero-filled). Public so Phase C cross-analysis comparison
        reuses the exact same collection logic (no duplication).
        """
        current = {name: (None, None, None) for name in self.METRICS}
        history = {name: [] for name in self.METRICS}
        if past_ids:
            for row in self.temporal_repo.get_historical_threat_assessments(
                    user_id, exclude_analysis_id=current_analysis_id,
                    limit=len(past_ids)):
                value = getattr(row, 'overall_threat_score', None)
                if value is not None:
                    history['threat'].append((
                        getattr(row, 'analysis_id', None), float(value),
                        getattr(row, 'id', None)))
            for row in self.temporal_repo.get_historical_media_scores(
                    user_id, exclude_analysis_id=current_analysis_id,
                    limit=len(past_ids)):
                value = getattr(row, 'overall_authenticity_score', None)
                if value is not None:
                    history['authenticity'].append((
                        getattr(row, 'analysis_id', None), float(value),
                        getattr(row, 'id', None)))
            means = self.temporal_repo.get_comment_metric_means(past_ids)
            for aid in past_ids:
                entry = means.get(aid) or {}
                for name in ('sentiment', 'toxicity', 'spam', 'duplicate'):
                    value = entry.get(name)
                    if value is not None:
                        history[name].append((aid, float(value), None))
            counts = self.temporal_repo.get_narrative_activity_counts(past_ids)
            for aid in past_ids:
                history['narrative_activity'].append(
                    (aid, float(counts.get(aid, 0)), None))

        if current_analysis_id is not None:
            row = self.threat_repo.get_for_analysis(current_analysis_id)
            if row is not None and getattr(row, 'overall_threat_score', None) is not None:
                current['threat'] = (current_analysis_id,
                                     float(row.overall_threat_score),
                                     getattr(row, 'id', None))
            from models.media_analysis import MediaAnalysis
            media = MediaAnalysis.query.filter_by(analysis_id=current_analysis_id).first()
            if media is not None and getattr(media, 'overall_authenticity_score', None) is not None:
                current['authenticity'] = (current_analysis_id,
                                           float(media.overall_authenticity_score),
                                           getattr(media, 'id', None))
            means = self.temporal_repo.get_comment_metric_means([current_analysis_id])
            entry = means.get(current_analysis_id) or {}
            for name in ('sentiment', 'toxicity', 'spam', 'duplicate'):
                if entry.get(name) is not None:
                    current[name] = (current_analysis_id, float(entry[name]), None)
            counts = self.temporal_repo.get_narrative_activity_counts([current_analysis_id])
            current['narrative_activity'] = (
                current_analysis_id, float(counts.get(current_analysis_id, 0)), None)

        return {name: {'current': current[name], 'history': history[name]}
                for name in self.METRICS}

    def _collect_series(self, user_id, past_ids, current_analysis_id):
        """Gather (current, [history]) value pairs per metric from storage."""
        series = self.get_metric_series(user_id, past_ids, current_analysis_id)
        out = {}
        for name in self.METRICS:
            entry = series[name]
            current_value = entry['current'][1]
            history_values = [value for _, value, _ in entry['history']]
            out[name] = (current_value, history_values)
        return out

    # -------------------------------------------------------------- compare

    def _compare(self, name, current, history, minimum, window):
        values = [v for v in (history or []) if v is not None]
        if current is None:
            return self._entry(name, None, None, None, len(values), window,
                               self.LABEL_UNAVAILABLE)
        if len(values) < minimum:
            return self._entry(name, current, None, None, len(values), window,
                               self.LABEL_INSUFFICIENT)
        baseline = sum(values) / len(values)
        deviation = current - baseline
        return self._entry(name, current, baseline, deviation, len(values),
                           window, self.classify_deviation(current, baseline))

    @classmethod
    def classify_deviation(cls, current, baseline):
        """Hedged label for a current value against a baseline mean."""
        if current is None or baseline is None:
            return cls.LABEL_UNAVAILABLE
        try:
            current = float(current)
            baseline = float(baseline)
        except (TypeError, ValueError):
            return cls.LABEL_UNAVAILABLE
        if baseline == 0:
            if current == 0:
                return cls.LABEL_TYPICAL
            return (cls.LABEL_SUBSTANTIAL_ABOVE if current > 0
                    else cls.LABEL_SUBSTANTIAL_BELOW)
        relative = (current - baseline) / abs(baseline)
        magnitude = abs(relative)
        if magnitude < cls.MODERATE_THRESHOLD:
            return cls.LABEL_TYPICAL
        if magnitude < cls.SUBSTANTIAL_THRESHOLD:
            return (cls.LABEL_MODERATE_ABOVE if relative > 0
                    else cls.LABEL_MODERATE_BELOW)
        return (cls.LABEL_SUBSTANTIAL_ABOVE if relative > 0
                else cls.LABEL_SUBSTANTIAL_BELOW)

    @staticmethod
    def _entry(name, current, baseline, deviation, sample_size, window,
               availability):
        return {
            'metric': name,
            'current': current,
            'baseline': baseline,
            'deviation': deviation,
            'sample_size': sample_size,
            'window_days': window,
            'availability': availability,
        }

    def _unavailable(self, reason):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'scope': 'user',
            'metrics': {},
            'reason': reason,
            'disclaimer': self.DISCLAIMER,
        }
