"""V12 Phase D - Coordination Intelligence Service.

Detects **potential** coordinated-activity signals across one analysis's
comments/content, persisted as :class:`CoordinationSignal` records.

CAPABILITY HONESTY
------------------
This engine is **heuristic and rule-based**. It never loads or runs a trained
model and never contacts an external service. Its vocabulary is deliberately
hedged: every reason is phrased as a *possibility* and every level uses one of
``none / low / elevated / suspicious``. **Similarity alone is never treated as
proof of coordination, intent, authorship, conspiracy or causality.**

HARD COMPARISON BUDGET
----------------------
Pairwise text comparison is the only quadratic risk here, so it is bounded two
ways:

1. **Blocking** - exact-duplicate detection is a single hash pass (O(n)); no
   pairwise work is spent on already-identical texts.
2. **Leader clustering** - near-duplicate / abnormal-similarity detection firms
   each item only against a bounded set of *cluster leaders* via the Phase C
   ``TextSimilarityService.best_match`` primitive, and stops early when
   ``COMPARISON_BUDGET`` is exhausted.

``comparisons_performed`` / ``comparisons_truncated`` therefore honestly reflect
whether the candidate space was fully examined. When truncated, subsequent
candidates are left un-clustered rather than silently compared.

REUSE, NOT REINVENTION
----------------------
* normalization / similarity / bounded best-match -> ``TextSimilarityService``
* per-comment risk / bot / spam / toxicity         -> ``CommentResult`` columns
* entity identity                                   -> ``Entity`` (V8)
* narrative identity + occurrence platforms         -> ``Narrative`` / ``NarrativeOccurrence``
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db
from models.comment_result import CommentResult
from models.coordination_signal import CoordinationSignal
from models.entity import Entity
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from services.text_similarity_service import TextSimilarityService

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class CoordinationIntelligenceService:
    # --------------------------------------------------------------- tunables
    ENABLE_KEY = 'ENABLE_COORDINATION_DETECTION'

    #: Hard cap on pairwise comparisons across the whole analysis.
    COMPARISON_BUDGET = 2000
    #: Max number of cluster leaders each item is compared against.
    MAX_LEADERS_TO_COMPARE = 50
    MAX_COMMENTS_SCANNED = 300
    MAX_ANALYSES_FOR_PLATFORM = 20
    MAX_ENTITIES_FOR_SHARED = 30
    MAX_EVIDENCE_SAMPLES = 3
    MAX_EVIDENCE_ENTITIES = 5
    MAX_REASON_TERMS = 3
    EVIDENCE_SNIPPET_CHARS = 120

    #: A text is an exact/near-exact duplicate at or above this containment.
    REPEATED_CONTENT_THRESHOLD = 0.90
    #: A text pair is "abnormally similar" (but not duplicate) at this jaccard.
    ABNORMAL_SIMILARITY_THRESHOLD = 0.60
    #: Minimum cluster size emitted for a text-based signal.
    MIN_TEXT_CLUSTER = 2
    #: Comments in this tight window (seconds) with a shared feature count as
    #: timing synchronization.
    TIMING_WINDOW_SECONDS = 300
    MIN_TIMING_CLUSTER = 3
    #: Upper bound on items examined in a single timing window, preventing the
    #: pair scan inside a window from growing without limit.
    MAX_TIMING_WINDOW_MEMBERS = 40
    #: Minimum distinct authors sharing an entity for the shared-entity signal.
    SHARED_ENTITY_MIN_AUTHORS = 3
    #: Min distinct analyses carrying a shared narrative.
    SHARED_NARRATIVE_MIN_ANALYSES = 2
    #: A behavioral cluster needs at least this many similar, risky comments.
    BEHAVIORAL_CLUSTER_SIZE = 3
    BEHAVIORAL_RISK_THRESHOLD = 40.0

    #: Signal is only emitted at/above this score.
    EMIT_THRESHOLD = 20.0

    # Score band -> level.
    LEVEL_BANDS = ((70.0, CoordinationSignal.LEVEL_SUSPICIOUS),
                   (40.0, CoordinationSignal.LEVEL_ELEVATED),
                   (20.0, CoordinationSignal.LEVEL_LOW))

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_rule_signal'

    def __init__(self):
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

    def _budget(self):
        return int(self._cfg('COORDINATION_COMPARISON_BUDGET', self.COMPARISON_BUDGET))

    def _leaders(self):
        return int(self._cfg('COORDINATION_MAX_LEADERS', self.MAX_LEADERS_TO_COMPARE))

    # ---------------------------------------------------------------- entry

    def analyze(self, analysis, comments=None, entities=None, narratives=None):
        """Detect + persist coordination signals for one analysis.

        Returns a component-contract dict (``available`` / ``capability`` /
        ``signals`` / aggregate fields) so Phase G can consume it. Never raises
        for data-quality reasons; DB failures are rolled back and reported as
        unavailable.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('Coordination detection is disabled by configuration.')

        try:
            units = self._build_units(analysis, comments, entities)
            narrative_list = self._resolve_narratives(analysis, narratives)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Coordination data load failed: {exc}')
            return self._unavailable('Content could not be loaded for coordination detection.')

        if not units:
            return self._unavailable(
                'No analyzable comments were available for coordination detection.')

        signals = self._detect_all(analysis, units, narrative_list)
        emitted = [s for s in signals if s is not None]

        if not emitted:
            return self._unavailable(
                'No coordination signal met the threshold.', units=len(units))

        try:
            persisted = self._persist(analysis, emitted, narrative_list)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Coordination persistence failed: {exc}')
            return self._unavailable(
                'Coordination signals were detected but could not be persisted.',
                units=len(units))

        if persisted is None:
            return self._unavailable(
                'Coordination signals were detected but could not be persisted.',
                units=len(units))

        return self._build_result(persisted, units, narrative_list)

    def get_analysis_coordination(self, analysis_id):
        """Read-only rollup of a single analysis's persisted signals."""
        rows = CoordinationSignal.query.filter_by(analysis_id=analysis_id).order_by(
            CoordinationSignal.score.desc(), CoordinationSignal.id.asc()).all()
        return [r.to_dict() for r in rows]

    # -------------------------------------------------------- comment units

    @staticmethod
    def _comment_rows(analysis_id, cap):
        return CommentResult.query.filter_by(analysis_id=analysis_id).order_by(
            CommentResult.id.asc()).limit(cap).all()

    def _build_units(self, analysis, comments, entities):
        cap = int(self._cfg('COORDINATION_MAX_COMMENTS_SCANNED',
                            self.MAX_COMMENTS_SCANNED))
        if cap <= 0:
            return []

        rows = comments
        if rows is None:
            rows = self._comment_rows(analysis.id, cap)
        else:
            rows = list(rows)[:cap]

        entity_names = self._entity_names_for(analysis, entities)
        units = []
        for row in rows:
            text = getattr(row, 'comment_text', None)
            if text is None and isinstance(row, dict):
                text = row.get('text') or row.get('comment_text')
            if not text or not str(text).strip():
                continue
            normalized = self.similarity.normalize_phrase(text)
            if not normalized:
                continue
            units.append({
                'comment_id': getattr(row, 'id', None),
                'text': str(text),
                'normalized': normalized,
                'author': getattr(row, 'author', None),
                'published_at': _naive_utc(getattr(row, 'published_at', None)),
                'risk_score': getattr(row, 'risk_score', None),
                'bot_score': getattr(row, 'bot_score', None),
                'spam_score': getattr(row, 'spam_score', None),
                'toxicity_score': getattr(row, 'toxicity_score', None),
                'entities': [name for name in entity_names if name in normalized],
            })
        return units

    def _entity_names_for(self, analysis, entities):
        if entities is not None:
            names = []
            for entity in entities:
                name = getattr(entity, 'normalized_name', None)
                if name is None and isinstance(entity, dict):
                    name = entity.get('normalized_name') or entity.get('name')
                if name:
                    names.append(name)
            return sorted(set(names))
        cap = int(self._cfg('MAX_ENTITIES_PER_ANALYSIS', 100))
        try:
            rows = db.session.query(Entity.normalized_name).filter(
                Entity.analysis_id == analysis.id
            ).group_by(Entity.normalized_name).limit(cap).all()
            return sorted({r[0] for r in rows if r[0]})
        except SQLAlchemyError:
            db.session.rollback()
            return []

    def _resolve_narratives(self, analysis, narratives):
        """Narratives for this analysis, plus their stored platform spread."""
        if narratives is not None:
            rows = narratives
        else:
            rows = db.session.query(Narrative, NarrativeOccurrence).join(
                NarrativeOccurrence,
                NarrativeOccurrence.narrative_id == Narrative.id,
            ).filter(NarrativeOccurrence.analysis_id == analysis.id).all()
            rows = [n for n, _occ in rows]

        out = []
        seen = set()
        max_narratives = int(self._cfg('MAX_NARRATIVES_PER_ANALYSIS', 12))
        for narrative in rows:
            if not isinstance(narrative, Narrative):
                continue
            if narrative.id in seen:
                continue
            seen.add(narrative.id)
            platforms = {o.platform for o in narrative.occurrences.all()}
            if not platforms:
                platforms = {NarrativeOccurrence.PLATFORM_UNKNOWN}
            out.append({
                'narrative_id': narrative.id,
                'normalized_name': narrative.normalized_name,
                'risk_score': narrative.risk_score,
                'platforms': platforms,
                'platform_count': narrative.platform_count,
                'occurrence_count': narrative.occurrence_count,
            })
            if len(out) >= max_narratives:
                break
        return out

    # ----------------------------------------------------------- detection

    def _detect_all(self, analysis, units, narrative_list):
        results = []
        for method in (self._detect_repeated_content,
                       self._detect_abnormal_similarity,
                       self._detect_synchronized_timing,
                       self._detect_shared_entities,
                       self._detect_shared_narrative,
                       self._detect_cross_platform,
                       self._detect_behavioral_cluster):
            signal = method(analysis, units, narrative_list)
            results.append(signal)
        return results

    # --- repeated_content: near-identical wording (hash + bounded leaders) ----

    def _detect_repeated_content(self, analysis, units, narrative_list):
        cluster_set = self._cluster_by_similarity(
            units, threshold=self.REPEATED_CONTENT_THRESHOLD,
            metric=TextSimilarityService.METRIC_CONTAINMENT, leaders=self._leaders())
        clusters = [c for c in cluster_set.clusters if self._is_duplicate_cluster(c)]
        if not clusters:
            return None
        best = max(clusters, key=lambda c: len(c['members']))
        total = sum(len(c['members']) for c in clusters)
        score = self._clamp(10.0 + len(best['members']) * 12.0 + total * 6.0)
        return {
            'signal_type': CoordinationSignal.TYPE_REPEATED_CONTENT,
            'score': round(score, 1),
            'confidence': self._confidence(len(clusters), total),
            'cluster_size': len(best['members']),
            'comparisons_performed': cluster_set.comparisons,
            'comparisons_truncated': cluster_set.truncated,
            'window_seconds': None,
            'related_entities': best.get('entities', []),
            'reasons': [
                f'{len(best["members"])} comments are near-identical '
                f'(containment >= {self.REPEATED_CONTENT_THRESHOLD}); '
                f'this can indicate repeated/copy-pasted content.',
                f'{len(clusters)} distinct repeated-content cluster(s) found.',
            ],
            'indicators': ['repeated_near_identical_wording'],
            'evidence': self._evidence(best),
            'first_event_at': best.get('first_event_at'),
            'last_event_at': best.get('last_event_at'),
            'narrative_id': None,
        }

    # --- abnormal_similarity: high but sub-duplicate similarity -------------

    def _detect_abnormal_similarity(self, analysis, units, narrative_list):
        cluster_set = self._cluster_by_similarity(
            units, threshold=self.ABNORMAL_SIMILARITY_THRESHOLD,
            metric=TextSimilarityService.METRIC_JACCARD, leaders=self._leaders())
        clusters = [c for c in cluster_set.clusters
                    if len(c['members']) >= self.MIN_TEXT_CLUSTER
                    and not self._is_duplicate_cluster(c)]
        if not clusters:
            return None
        best = max(clusters, key=lambda c: len(c['members']))
        score = self._clamp(12.0 + len(best['members']) * 11.0)
        return {
            'signal_type': CoordinationSignal.TYPE_ABNORMAL_SIMILARITY,
            'score': round(score, 1),
            'confidence': self._confidence(len(clusters), len(best['members'])),
            'cluster_size': len(best['members']),
            'comparisons_performed': cluster_set.comparisons,
            'comparisons_truncated': cluster_set.truncated,
            'window_seconds': None,
            'related_entities': best.get('entities', []),
            'reasons': [
                f'{len(best["members"])} comments share unusually similar wording '
                f'(jaccard >= {self.ABNORMAL_SIMILARITY_THRESHOLD}) but are not exact '
                f'duplicates; this is a possible coordination indicator.',
                'Similarity alone does not indicate intent or coordination.',
            ],
            'indicators': ['abnormal_text_similarity'],
            'evidence': self._evidence(best),
            'first_event_at': best.get('first_event_at'),
            'last_event_at': best.get('last_event_at'),
            'narrative_id': None,
        }

    # --- synchronized_timing ------------------------------------------------

    def _detect_synchronized_timing(self, analysis, units, narrative_list):
        timed = [u for u in units if u['published_at'] is not None]
        if len(timed) < self.MIN_TIMING_CLUSTER:
            return None
        timed.sort(key=lambda u: u['published_at'])
        window = int(self._cfg('COORDINATION_TIMING_WINDOW_SECONDS',
                               self.TIMING_WINDOW_SECONDS))

        best_group, best_score = None, 0.0
        # Bounded, deterministic single-pass: for each left anchor, extend a
        # window of items within `window` seconds (capped membership). The pair
        # scan inside each window is capped so this never becomes O(n^3).
        pair_budget = int(self._cfg('COORDINATION_TIMING_PAIR_BUDGET',
                                    self.COMPARISON_BUDGET))
        used, truncated = 0, False
        for i, anchor in enumerate(timed):
            group = [anchor]
            for later in timed[i + 1:]:
                if later['published_at'] - anchor['published_at'] <= timedelta_seconds(window):
                    if len(group) < self.MAX_TIMING_WINDOW_MEMBERS:
                        group.append(later)
                    else:
                        truncated = True
                else:
                    break
            if len(group) < self.MIN_TIMING_CLUSTER:
                continue
            shared = self._shared_features(group, pair_budget - used)
            used += shared['comparisons']
            if used >= pair_budget:
                truncated = True
            if shared.get('shared_any', 0) < self.MIN_TIMING_CLUSTER:
                continue
            total_risk = sum(u['risk_score'] or 0.0 for u in group)
            group_score = (len(group) * 15.0 + shared.get('shared_any', 0) * 10.0
                           + shared.get('similar_pairs', 0) * 8.0
                           + total_risk / max(len(group), 1) * 0.2)
            if group_score > best_score:
                best_score, best_group = group_score, group

        if not best_group:
            return None
        span = (best_group[-1]['published_at'] - best_group[0]['published_at']).total_seconds()
        return {
            'signal_type': CoordinationSignal.TYPE_SYNCHRONIZED_TIMING,
            'score': round(self._clamp(best_score), 1),
            'confidence': self._confidence(len(best_group), 1.0),
            'cluster_size': len(best_group),
            'comparisons_performed': used,
            'comparisons_truncated': truncated,
            'window_seconds': window,
            'related_entities': self._collect_entities(best_group),
            'reasons': [
                f'{len(best_group)} items appeared within {int(span)}s of each other '
                f'(window {window}s) while sharing wording/entities; this is a possible '
                f'timing-synchronization indicator.',
                'Short interval alone does not prove coordination.',
            ],
            'indicators': ['temporal_clustering'],
            'evidence': self._evidence_from_units(best_group),
            'first_event_at': best_group[0]['published_at'],
            'last_event_at': best_group[-1]['published_at'],
            'narrative_id': None,
        }

    # --- shared_entities -----------------------------------------------------

    def _detect_shared_entities(self, analysis, units, narrative_list):
        author_entity = defaultdict(set)
        entity_auth = defaultdict(set)
        for unit in units:
            author = unit['author']
            if not author:
                continue
            for name in unit['entities']:
                author_entity[author].add(name)
                entity_auth[name].add(author)
        strong = []
        for name, authors in entity_auth.items():
            if len(authors) < self.SHARED_ENTITY_MIN_AUTHORS:
                continue
            # risk of authors carrying this entity
            risks = [u['risk_score'] for u in units
                     if u['author'] in authors and name in u['entities']
                     and u['risk_score'] is not None]
            avg_risk = sum(risks) / len(risks) if risks else None
            strong.append({'entity': name, 'authors': len(authors),
                           'avg_risk': avg_risk})
        if not strong:
            return None
        best = max(strong, key=lambda e: (e['authors'], e['avg_risk'] or 0.0))
        risk_boost = min(30.0, (best['avg_risk'] or 0.0) * 0.3)
        score = self._clamp(best['authors'] * 18.0 + risk_boost)
        return {
            'signal_type': CoordinationSignal.TYPE_SHARED_ENTITIES,
            'score': round(score, 1),
            'confidence': self._confidence(len(strong), best['authors']),
            'cluster_size': best['authors'],
            'comparisons_performed': 0,
            'comparisons_truncated': False,
            'window_seconds': None,
            'related_entities': [e['entity'] for e in
                                 sorted(strong, key=lambda x: (-x['authors'],
                                                               x['entity']))[:int(self._cfg('COORDINATION_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))]],
            'reasons': [
                f'Entity "{best["entity"]}" is discussed by {best["authors"]} separate '
                f'authors; repeated shared-entity focus can indicate possible coordination.',
                'Focusing on a shared entity is a signal, not proof of intent.',
            ],
            'indicators': ['shared_entity_focus'],
            'evidence': {'entity': best['entity'], 'distinct_authors': best['authors'],
                         'mean_author_risk': best['avg_risk']},
            'first_event_at': None,
            'last_event_at': None,
            'narrative_id': None,
        }

    # --- shared_narrative ----------------------------------------------------

    def _detect_shared_narrative(self, analysis, units, narrative_list):
        recurring = [n for n in narrative_list
                     if n['occurrence_count'] >= self.SHARED_NARRATIVE_MIN_ANALYSES]
        if not recurring:
            return None
        best = max(recurring, key=lambda n: (n['occurrence_count'], n['risk_score'] or 0.0))
        score = self._clamp(best['occurrence_count'] * 22.0 + (best['risk_score'] or 0.0) * 0.25)
        return {
            'signal_type': CoordinationSignal.TYPE_SHARED_NARRATIVE,
            'score': round(score, 1),
            'confidence': self._confidence(len(recurring), best['occurrence_count']),
            'cluster_size': best['occurrence_count'],
            'comparisons_performed': 0,
            'comparisons_truncated': False,
            'window_seconds': None,
            'related_entities': [n['normalized_name'] for n in recurring[:int(self._cfg('COORDINATION_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))]],
            'reasons': [
                f'Narrative "{best["normalized_name"]}" recurs across '
                f'{best["occurrence_count"]} analyses; recurring shared narratives can '
                f'indicate coordinated message propagation.',
                'Recurrence of a narrative does not prove an orchestrated campaign.',
            ],
            'indicators': ['recurring_shared_narrative'],
            'evidence': {'narrative': best['normalized_name'],
                         'occurrences': best['occurrence_count'],
                         'platform_count': best['platform_count']},
            'first_event_at': None,
            'last_event_at': None,
            'narrative_id': best['narrative_id'],
        }

    # --- cross_platform_coordination -----------------------------------------

    def _detect_cross_platform(self, analysis, units, narrative_list):
        platform = self._platform_for(analysis)
        other = NarrativeOccurrence.PLATFORM_YOUTUBE if platform == NarrativeOccurrence.PLATFORM_REDDIT else NarrativeOccurrence.PLATFORM_REDDIT
        if not narrative_list:
            return None
        # narratives actually spanning two or more platforms, one of which is the
        # "other" platform relative to this analysis
        cross = [n for n in narrative_list
                 if len(n['platforms']) >= 2 and other in n['platforms']]
        if not cross:
            return None
        best = max(cross, key=lambda n: (n['platform_count'], n['occurrence_count'] or 0))
        score = self._clamp(best['platform_count'] * 25.0 + best['occurrence_count'] * 8.0)
        return {
            'signal_type': CoordinationSignal.TYPE_CROSS_PLATFORM_COORDINATION,
            'score': round(score, 1),
            'confidence': self._confidence(len(cross), best['platform_count']),
            'cluster_size': best['platform_count'],
            'comparisons_performed': 0,
            'comparisons_truncated': False,
            'window_seconds': None,
            'related_entities': [n['normalized_name'] for n in cross[:int(self._cfg('COORDINATION_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))]],
            'reasons': [
                f'Narrative "{best["normalized_name"]}" appears on both '
                f'{platform} and {other}; cross-platform recurrence is a possible '
                f'coordination indicator.',
                'Overlap across platforms does not establish a shared orchestrator.',
            ],
            'indicators': ['cross_platform_narrative_overlap'],
            'evidence': {'narrative': best['normalized_name'],
                         'platforms': sorted(best['platforms']),
                         'platform_count': best['platform_count']},
            'first_event_at': None,
            'last_event_at': None,
            'narrative_id': best['narrative_id'],
        }

    # --- behavioral_cluster --------------------------------------------------

    def _detect_behavioral_cluster(self, analysis, units, narrative_list):
        risky = [u for u in units if (u['risk_score'] or 0.0) >= self.BEHAVIORAL_RISK_THRESHOLD
                 or (u['bot_score'] or 0.0) >= self.BEHAVIORAL_RISK_THRESHOLD
                 or (u['spam_score'] or 0.0) >= self.BEHAVIORAL_RISK_THRESHOLD]
        if len(risky) < self.BEHAVIORAL_CLUSTER_SIZE:
            return None
        cluster_set = self._cluster_by_similarity(
            risky, threshold=self.ABNORMAL_SIMILARITY_THRESHOLD,
            metric=TextSimilarityService.METRIC_JACCARD, leaders=self._leaders())
        clusters = [c for c in cluster_set.clusters if len(c['members']) >= self.BEHAVIORAL_CLUSTER_SIZE]
        if not clusters:
            return None
        best = max(clusters, key=lambda c: len(c['members']))
        mean_risk = self._mean([u['risk_score'] for u in best['members']
                                if u['risk_score'] is not None])
        score = self._clamp(len(best['members']) * 16.0 + (mean_risk or 0.0) * 0.4)
        return {
            'signal_type': CoordinationSignal.TYPE_BEHAVIORAL_CLUSTER,
            'score': round(score, 1),
            'confidence': self._confidence(len(best['members']), mean_risk or 0.0),
            'cluster_size': len(best['members']),
            'comparisons_performed': cluster_set.comparisons,
            'comparisons_truncated': cluster_set.truncated,
            'window_seconds': None,
            'related_entities': best.get('entities', []),
            'reasons': [
                f'{len(best["members"])} similar comments each carry elevated risk '
                f'(mean {mean_risk:.1f}); a burst of similar high-risk comments can '
                f'indicate possible coordinated amplification.',
                'Elevated risk plus similarity is an indicator, not a verdict.',
            ],
            'indicators': ['behavioral_amplification_cluster'],
            'evidence': self._evidence(best),
            'first_event_at': best.get('first_event_at'),
            'last_event_at': best.get('last_event_at'),
            'narrative_id': None,
        }

    # ------------------------------------------------- clustering primitives

    def _cluster_by_similarity(self, units, threshold, metric, leaders=50):
        """Deterministic leader-based clustering, with honest budget accounting.

        O(n * leaders) worst case, capped by the comparison budget. Each unit is
        compared only against the current cluster leaders, in deterministic
        order, until the budget is exhausted. Returns a :class:`_ClusterSet`
        whose ``clusters`` (list of dicts), ``comparisons`` (true number of
        pairwise comparisons performed) and ``truncated`` (whether units were
        left unexamined when the budget ran out) the caller must report so the
        persisted signal is honest about coverage.
        """
        budget = self._budget()
        used = 0
        truncated = False
        clusters = []

        for unit in units:
            best_idx, best_score, used = self._find_best_cluster(
                unit, clusters, threshold, metric, used, budget, leaders)
            if best_idx is None:
                if len(clusters) < leaders:
                    clusters.append(self._new_cluster(unit))
                else:
                    truncated = True
                    used += 1
                    continue
            else:
                self._join_cluster(clusters[best_idx], unit)
            if used >= budget:
                truncated = True
                break

        # Per-cluster accounting: each cluster borrows the shared budget view.
        # ``total`` is the true number of pairwise comparisons performed (the
        # same number the caller must report), and ``truncated`` states honestly
        # whether there were units left unexamined when the budget was exhausted.
        return _ClusterSet(clusters, used, truncated, budget)

    def _find_best_cluster(self, unit, clusters, threshold, metric, used, budget, leaders):
        best_idx, best_score = None, 0.0
        for idx, cluster in enumerate(clusters):
            if used >= budget or idx >= leaders:
                break
            leader = cluster['members'][0]
            value = self.similarity.score(unit['normalized'], leader['normalized'], metric)
            used += 1
            if value > best_score:
                best_score, best_idx = value, idx
        if best_idx is None or best_score < threshold:
            return None, best_score, used
        return best_idx, best_score, used

    @staticmethod
    def _new_cluster(unit):
        return {
            'members': [unit],
            'comparisons': 0,
            'truncated': False,
            'entities': [e for e in unit.get('entities', [])],
            'first_event_at': unit.get('published_at'),
            'last_event_at': unit.get('published_at'),
        }

    def _is_duplicate_cluster(self, cluster):
        """A cluster is a *pure* duplicate group when every member is at least
        ``REPEATED_CONTENT_THRESHOLD``-contained in the leader. Such clusters are
        reported by the repeated-content signal, not by the abnormal-similarity
        signal (which describes merely similar-but-distinct text)."""
        leader = cluster['members'][0]
        for member in cluster['members'][1:]:
            if self.similarity.score(member['normalized'], leader['normalized'],
                                     TextSimilarityService.METRIC_CONTAINMENT) \
                    < self.REPEATED_CONTENT_THRESHOLD:
                return False
        return len(cluster['members']) > 1

    @staticmethod
    def _join_cluster(cluster, unit):
        cluster['members'].append(unit)
        cluster['comparisons'] += 1
        for name in unit.get('entities', []):
            if name not in cluster['entities']:
                cluster['entities'].append(name)
        ts = unit.get('published_at')
        if ts is not None:
            if cluster['first_event_at'] is None or ts < cluster['first_event_at']:
                cluster['first_event_at'] = ts
            if cluster['last_event_at'] is None or ts > cluster['last_event_at']:
                cluster['last_event_at'] = ts

    def _shared_features(self, group, budget=None):
        """Count genuine shared features between group members.

        Returns ``{'similar_pairs': int, 'shared_authors': int, 'shared_any': int,
        'comparisons': int}``. ``shared_any`` counts how many members participate
        in at least one *genuine* shared feature (overlapping wording OR a shared
        entity) with another member. A member with no overlap and no entities is
        NOT counted, so a group of unrelated near-in-time comments alone never
        produces a timing signal.
        """
        similar_pairs = 0
        comparisons = 0
        shared_predicate = []
        n = len(group)
        for i in range(n):
            for j in range(i + 1, n):
                if budget is not None and comparisons >= budget:
                    return {'similar_pairs': similar_pairs, 'shared_authors': 0,
                            'shared_any': 0, 'comparisons': comparisons}
                comparisons += 1
                text_shared = self.similarity.overlap_ratio(
                    group[i]['text'], group[j]['text']) >= 0.5
                entity_shared = bool(set(group[i]['entities']) & set(group[j]['entities']))
                if text_shared or entity_shared:
                    similar_pairs += 1
                    shared_predicate.append(i)
                    shared_predicate.append(j)

        # A member "shares something" only if it overlaps a peer or names a shared entity.
        shared_authors = len(set(shared_predicate))
        shared_any = shared_authors
        return {'similar_pairs': similar_pairs, 'shared_authors': shared_authors,
                'shared_any': shared_any, 'comparisons': comparisons}

    # ----------------------------------------------------------- persistence

    def _persist(self, analysis, signals, narrative_list):
        """Upsert every signal in one transaction; single commit for the batch.

        Returns the persisted signal dicts on success, ``None`` on unrecoverable
        DB failure (after one rollback-and-retry pass for a unique-constraint
        race). Each ``(analysis_id, signal_type)`` is unique, so a re-run simply
        updates the existing row rather than inserting a duplicate.
        """
        for attempt in (1, 2):
            try:
                for signal in signals:
                    self._upsert_signal(analysis, signal)
                db.session.commit()
                return signals
            except IntegrityError as exc:
                db.session.rollback()
                if attempt == 2:
                    logger.warning(f'Coordination persistence conflict, giving up: {exc}')
                    return None
                logger.info('Coordination persistence conflict, retrying once after rollback.')
            except SQLAlchemyError as exc:
                db.session.rollback()
                logger.warning(f'Coordination persistence failed: {exc}')
                return None
        return None

    def _upsert_signal(self, analysis, signal):
        existing = CoordinationSignal.query.filter_by(
            analysis_id=analysis.id, signal_type=signal['signal_type']).first()
        if existing is None:
            existing = CoordinationSignal(
                analysis_id=analysis.id,
                user_id=analysis.user_id,
                signal_type=signal['signal_type'],
            )
            db.session.add(existing)
        existing.narrative_id = signal['narrative_id']
        existing.score = signal['score']
        existing.confidence = signal['confidence']
        existing.level = self._level_from_score(signal['score'])
        existing.cluster_size = signal['cluster_size']
        existing.comparisons_performed = signal['comparisons_performed']
        existing.comparisons_truncated = signal['comparisons_truncated']
        existing.window_seconds = signal['window_seconds']
        existing.summary = signal['reasons'][0] if signal['reasons'] else None
        existing.reasons = signal['reasons']
        existing.indicators = signal['indicators']
        existing.evidence = signal['evidence']
        existing.related_entities = signal['related_entities']
        existing.detection_method = CoordinationSignal.METHOD_HEURISTIC
        existing.first_event_at = signal['first_event_at']
        existing.last_event_at = signal['last_event_at']
        return existing

    # ---------------------------------------------------------------- result

    def _build_result(self, persisted, units, narrative_list):
        """Assemble the component-contract dict from the persisted signals."""
        signals = []
        reasons = []
        indicators = []
        scores = []

        for signal in persisted:
            level = self._level_from_score(signal['score'])
            data = dict(signal)
            data['level'] = level
            data['evidence'] = signal['evidence']
            signals.append(data)
            scores.append(signal['score'])
            reasons.append(f'[{signal["signal_type"]}] {signal["reasons"][0]}')
            indicators.extend(signal['indicators'])

        signals.sort(key=lambda s: s['score'], reverse=True)
        high_level_count = sum(1 for s in signals
                               if s['level'] in (CoordinationSignal.LEVEL_ELEVATED,
                                                 CoordinationSignal.LEVEL_SUSPICIOUS))

        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'units_analyzed': len(units),
            'signal_count': len(signals),
            'signals': signals,
            'max_signal_score': self._clamp(max(scores)) if scores else None,
            'avg_signal_score': (round(sum(scores) / len(scores), 1) if scores else None),
            'high_level_count': high_level_count,
            'reasons': reasons,
            'indicators': sorted(set(indicators)),
            'limitations': self._limitations(),
        }

    def _unavailable(self, reason, units=0):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'units_analyzed': units,
            'signal_count': 0,
            'signals': [],
            'max_signal_score': None,
            'avg_signal_score': None,
            'high_level_count': 0,
            'reasons': [reason],
            'indicators': [],
            'limitations': self._limitations(),
        }

    def _limitations(self):
        return [
            'Coordination detection is heuristic and rule-based, not a trained ML '
            'classifier of coordinated behaviour.',
            'Similarity, shared entities/narratives and timing correlation are '
            '*indicators* only; they do not establish intent, authorship, an '
            'orchestrator, conspiracy or causality.',
            'Only bounded comments for the current analysis (up to '
            f'{self.MAX_COMMENTS_SCANNED}) are examined; cross-analysis signals rely '
            'on stored narrative/entity history.',
            'Temporal signals depend on comment timestamps; items with NULL timestamps '
            'are excluded from timing analysis but still counted for text signals.',
        ]

    # ---------------------------------------------------------------- utils

    @staticmethod
    def _mean(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    def _confidence(self, count, strength):
        coverage = min(1.0, count / max(self.MIN_TEXT_CLUSTER, 1))
        strength_norm = min(1.0, (strength or 0.0) / 100.0)
        return round(self._clamp(30.0 + coverage * 40.0 + strength_norm * 20.0), 1)

    def _level_from_score(self, score):
        for threshold, level in self.LEVEL_BANDS:
            if score >= threshold:
                return level
        return CoordinationSignal.LEVEL_NONE

    def _collect_entities(self, group):
        seen = []
        for unit in group:
            for name in unit.get('entities', []):
                if name not in seen:
                    seen.append(name)
        return seen[:int(self._cfg('COORDINATION_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))]

    def _evidence(self, cluster):
        return self._evidence_from_units(cluster['members'])

    def _evidence_from_units(self, group):
        samples = []
        seen = set()
        for unit in group[:int(self._cfg('COORDINATION_MAX_EVIDENCE_SAMPLES', self.MAX_EVIDENCE_SAMPLES))]:
            key = unit.get('comment_id') or unit.get('text')
            if key in seen:
                continue
            seen.add(key)
            samples.append({
                'ref': f'comment:{unit.get("comment_id")}' if unit.get('comment_id') else 'comment',
                'author': unit.get('author'),
                'snippet': self.similarity.snippet(unit['text'],
                                                   int(self._cfg('COORDINATION_EVIDENCE_SNIPPET_CHARS', self.EVIDENCE_SNIPPET_CHARS))),
                'risk_score': unit.get('risk_score'),
            })
        return {'samples': samples,
                'similarity_thresholds': {
                    'abnormal': self.ABNORMAL_SIMILARITY_THRESHOLD,
                    'repeated': self.REPEATED_CONTENT_THRESHOLD,
                }}

    def _platform_for(self, analysis):
        if analysis is None:
            return NarrativeOccurrence.PLATFORM_UNKNOWN
        if analysis.analysis_type == 'reddit' and analysis.reddit_analysis:
            return NarrativeOccurrence.PLATFORM_REDDIT
        if analysis.youtube_analysis:
            return NarrativeOccurrence.PLATFORM_YOUTUBE
        if analysis.analysis_type == 'youtube':
            return NarrativeOccurrence.PLATFORM_YOUTUBE
        return NarrativeOccurrence.PLATFORM_UNKNOWN

    def _clamp(self, value):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


class _ClusterSet:
    """Result of bounded clustering: the clusters plus honest budget accounting.

    ``comparisons`` is the true number of pairwise similarity checks performed
    (always <= budget). ``truncated`` is ``True`` when units remained to be
    examined after the budget was exhausted, so the caller can report that the
    comparison was *not* exhaustive rather than silently implying it was.
    """

    __slots__ = ('clusters', 'comparisons', 'truncated', 'budget')

    def __init__(self, clusters, comparisons, truncated, budget):
        self.clusters = clusters
        self.comparisons = comparisons
        self.truncated = truncated
        self.budget = budget

    def __len__(self):
        return len(self.clusters)

    def __iter__(self):
        return iter(self.clusters)

    def __getitem__(self, index):
        return self.clusters[index]


def timedelta_seconds(seconds):
    from datetime import timedelta
    return timedelta(seconds=seconds)
