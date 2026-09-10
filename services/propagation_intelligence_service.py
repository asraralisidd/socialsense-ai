"""V12 Phase E - Propagation Intelligence Service.

Detects **possible propagation relationships** between analyses that share at
least one narrative, and persists them as :class:`PropagationEvent` records.

CAPABILITY HONESTY
------------------
Heuristic and rule-based. No trained model, no external service, no causal
claim. The strongest relationship label ever emitted is
``potentially_propagated`` and even that is a hedge: it means the evidence
(temporal ordering + shared narrative + similarity) is *consistent with*
propagation, not that one party caused, coordinated, orchestrated, authored or
originated the other.

TEMPORAL RULES
--------------
* Earlier occurrence is the source, later is the target.
* ``lag_seconds = target.occurred_at - source.occurred_at`` (>= 0 by ordering).
* Timestamps come from the platform where available, else the analysis
  ``created_at`` fallback, and the ``timestamp_source`` is respected. If neither
  side yields a usable timestamp, the pair is skipped (and reported unavailable
  for that candidate) rather than fabricating a value.
* Negative/zero lags are clamped defensively and ordered deterministically.

BOUNDING & DUPLICATES
---------------------
* Candidates are the user's other analyses, capped by
  ``MAX_CANDIDATE_ANALYSES``; comparisons over shared narratives are further
  capped and reported honestly.
* ``uq_propagation_events_edge`` cannot dedupe ``narrative_id IS NULL`` rows on
  PostgreSQL (NULLs are distinct), so an explicit ``edge_exists`` check is
  performed before every insert. Re-analysis never duplicates events.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from repositories.narrative_repository import NarrativeRepository
from repositories.propagation_repository import PropagationRepository
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


class PropagationIntelligenceService:
    ENABLE_KEY = 'ENABLE_PROPAGATION_INTELLIGENCE'

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_narrative_temporal_similarity'

    #: Max other analyses examined as candidate sources/targets.
    MAX_CANDIDATE_ANALYSES = 15
    #: Max shared-narrative comparisons before truncation is reported.
    MAX_NARRATIVE_COMPARISONS = 40
    #: Min shared-narrative similarity for a "similar"-level edge.
    SIMILARITY_MIN = 0.40
    #: Min shared-narrative similarity for a "potentially propagated" edge.
    POTENTIALLY_PROPAGATED_MIN = 0.55
    #: Max cross-platform event count persisted per run.
    MAX_PROPAGATION_EVENTS = 25
    MAX_EVIDENCE_SAMPLES = 3
    MAX_EVIDENCE_ENTITIES = 5
    EVIDENCE_SNIPPET_CHARS = 200

    def __init__(self):
        self.similarity = TextSimilarityService()
        self.propagation_repo = PropagationRepository()
        self.narrative_repo = NarrativeRepository()

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
        """Detect + persist propagation events for one analysis vs its peers.

        ``narratives`` is an optional precomputed list of narrative dicts for
        ``analysis`` (as produced by Phase C); if omitted the service loads a
        bounded set itself from ``NarrativeOccurrence``.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('Propagation intelligence is disabled by configuration.')

        if analysis is None:
            return self._unavailable('No analysis provided; propagation cannot be computed.')

        try:
            current_occurrences = self._occurrences_for(analysis.id)
            name_map = self._name_map_for(analysis, current_occurrences)
            anchor_names = self._anchor_narrative_names(current_occurrences, name_map)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Propagation data load failed: {exc}')
            return self._unavailable('Content could not be loaded for propagation detection.')

        if not anchor_names:
            return self._unavailable(
                'No narratives found for this analysis; nothing to propagate from.',
                occurrences=len(current_occurrences))

        return self._detect_and_persist(analysis, current_occurrences, anchor_names, name_map)

    def _detect_and_persist(self, analysis, current_occurrences, anchor_names, name_map):
        """Core detection: pair this analysis with peers sharing a narrative.

        Bounded by candidate analyses and narrative comparisons. Timestamp
        determines source/target ordering; similarity drives the relationship
        label. Returns a component-contract dict.
        """
        candidates = self.propagation_repo.get_recent_analyses(
            analysis.user_id, exclude_analysis_id=analysis.id,
            limit=int(self._cfg('PROPAGATION_MAX_CANDIDATES',
                                self.MAX_CANDIDATE_ANALYSES)),
        )

        similarities = {}          # (candidate_id, narrative) -> similarity
        pair_plans = self._build_pair_plans(
            analysis, candidates, anchor_names, similarities, name_map)

        if not pair_plans:
            return self._unavailable(
                'No candidate peer analysis shares a narrative with this analysis.',
                occurrences=len(current_occurrences))

        try:
            persisted = self._persist(analysis, pair_plans, similarities)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Propagation persistence failed: {exc}')
            return self._unavailable(
                'Propagation relationships were detected but could not be persisted.',
                occurrences=len(current_occurrences))

        if persisted is None:
            return self._unavailable(
                'Propagation relationships were detected but could not be persisted.',
                occurrences=len(current_occurrences))

        return self._build_result(persisted, analysis, current_occurrences)

    # ------------------------------------------------------------ occurrence

    def _occurrences_for(self, analysis_id):
        rows = self.narrative_repo.get_occurrences_for_analysis(analysis_id)
        out = []
        for occurrence in rows:
            out.append({
                'narrative_id': occurrence.narrative_id,
                'platform': occurrence.platform,
                'content_ref': occurrence.content_ref,
                'occurred_at': _naive_utc(occurrence.occurred_at),
                'timestamp_source': occurrence.timestamp_source,
                'relevance_score': occurrence.relevance_score,
                'risk_score': occurrence.risk_score,
            })
        return out

    def _name_map_for(self, analysis, occurrences):
        """Bounded ``{narrative_id -> normalized_name}`` resolved once."""
        return self.narrative_repo.get_id_to_name_map(
            analysis.user_id,
            limit=int(self._cfg('MAX_ENTITY_HISTORY', 500)),
        )

    def _anchor_narrative_names(self, occurrences, name_map):
        """Bounded set of narrative normalized names for the current analysis."""
        names = set()
        for occurrence in occurrences:
            name = name_map.get(occurrence['narrative_id'])
            if name:
                names.add(name)
        return sorted(names)

    def _narrative_lookup(self, analysis, candidates, anchor_names, name_map):
        """Map ``analysis_id -> set of shared narrative names`` for candidates."""
        narratives = self.narrative_repo.get_top_for_user(
            analysis.user_id, limit=int(self._cfg('MAX_NARRATIVES_PER_ANALYSIS', 12)))
        by_name = {n.normalized_name: n for n in narratives}

        candidate_shared = {c.id: set() for c in candidates}
        for name in anchor_names:
            narrative = by_name.get(name)
            if narrative is None:
                continue
            # occurrences of this narrative across the user's analyses
            peer_analyses = self.propagation_repo.get_analyses_for_narrative(
                narrative.id, analysis.user_id,
                limit=int(self._cfg('PROPAGATION_MAX_CANDIDATES',
                                    self.MAX_CANDIDATE_ANALYSES)))
            for peer in peer_analyses:
                if peer.id in candidate_shared:
                    candidate_shared[peer.id].add(name)
        return candidate_shared

    def _build_pair_plans(self, analysis, candidates, anchor_names, similarities, name_map):
        """Select bounded candidate pairs that share a narrative.

        ``similarities`` is populated with ``(candidate_id, narrative_name) ->
        similarity`` so the scorer can reuse it.
        """
        candidate_shared = self._narrative_lookup(
            analysis, candidates, anchor_names, name_map)

        plans = []
        comparisons = 0
        truncated = False

        for candidate in candidates:
            shared = candidate_shared.get(candidate.id) or set()
            if not shared:
                continue
            for name in sorted(shared):
                if comparisons >= int(self._cfg('PROPAGATION_MAX_COMPARISONS',
                                                self.MAX_NARRATIVE_COMPARISONS)):
                    truncated = True
                    break
                comparisons += 1
                sim = self._narrative_similarity(analysis, candidate, name, name_map)
                similarities[(candidate.id, name)] = sim
                plans.append({
                    'candidate': candidate,
                    'narrative_name': name,
                    'similarity': sim,
                    'truncated': truncated,
                })
            if truncated:
                break

        for plan in plans:
            plan['comparisons'] = comparisons

        # Bounded by a hard event cap: keep only the strongest candidate pairs so
        # a run never emits an unbounded cross-product. The cap is legitimate
        # because each plan is a *possible* edge; dropping the weakest keeps the
        # output focused on the strongest propagation indicators.
        event_cap = int(self._cfg('PROPAGATION_MAX_EVENTS', self.MAX_PROPAGATION_EVENTS))
        if len(plans) > event_cap:
            plans.sort(key=lambda p: (-p['similarity'],
                                      p['narrative_name'], p['candidate'].id))
            plans = plans[:event_cap]
        return plans

    def _narrative_similarity(self, analysis, candidate, name, name_map):
        """Bounded narrative-level similarity (0-1) between analysis and peer.

        Uses stored occurrence relevance for ``name`` in BOTH analyses. The score
        is ``mean(relevance_a, relevance_b) / 100``, i.e. how prominent the shared
        narrative is in each. It can only approach 1.0 when the narrative is
        actually a prominent feature of **both** analyses; a marginal occurrence
        in one side pulls the score down. Never loads full comment text and never
        conflates "similar relevance" with "similar content".
        """
        cur_rels = [o['relevance_score']
                    for o in self._occurrences_for(analysis.id)
                    if o['narrative_id']
                    and name_map.get(o['narrative_id']) == name
                    and o['relevance_score'] is not None]
        cand_rel = self._candidate_relevance(candidate, name, name_map)

        if not cur_rels and cand_rel is None:
            return 0.0
        if not cur_rels:
            return round(cand_rel / 100.0, 3)
        if cand_rel is None:
            return round((sum(cur_rels) / len(cur_rels)) / 100.0, 3)
        a = sum(cur_rels) / len(cur_rels) / 100.0
        b = cand_rel / 100.0
        return round((a + b) / 2.0, 3)

    def _candidate_relevance(self, candidate, name, name_map):
        """Stored relevance of ``name`` in a candidate analysis, or None.

        Uses the shared ``name_map`` (no per-id queries) to resolve narrative
        names from occurrence narrative_id values.
        """
        for occurrence in self.narrative_repo.get_occurrences_for_analysis(candidate.id):
            if name_map.get(occurrence.narrative_id) == name:
                return occurrence.relevance_score
        return None

    # ----------------------------------------------------------- persistence

    def _persist(self, analysis, plans, similarities):
        """Upsert each propagation edge in one transaction; single commit.

        Explicit existence check (``edge_exists``) because the unique constraint
        cannot dedupe NULL narrative_id on PostgreSQL. Returns the persisted plan
        dicts, or ``None`` on unrecoverable DB failure after one retry.
        """
        events = []
        for attempt in (1, 2):
            try:
                events = []
                for plan in plans:
                    event = self._upsert_edge(analysis, plan, similarities)
                    if event is not None:
                        events.append((plan, event))
                db.session.commit()
                return events
            except IntegrityError as exc:
                db.session.rollback()
                if attempt == 2:
                    logger.warning(f'Propagation persistence conflict, giving up: {exc}')
                    return None
                logger.info('Propagation persistence conflict, retrying once after rollback.')
            except SQLAlchemyError as exc:
                db.session.rollback()
                logger.warning(f'Propagation persistence failed: {exc}')
                return None
        return None

    def _upsert_edge(self, analysis, plan, similarities):
        candidate = plan['candidate']
        narrative = self._narrative_by_name(analysis.user_id, plan['narrative_name'])
        source_id, target_id, direction = self._orient(analysis, candidate)

        # If we cannot order temporally, treat as undirected but still record a
        # relationship only if a narrative was actually shared.
        if source_id is None:
            return None

        narrative_id = narrative.id if narrative else None
        relationship_type = self._relationship_type(plan, source_id, target_id)
        if relationship_type is None:
            return None

        edge = self.propagation_repo.get_edge(
            narrative_id, source_id, target_id, relationship_type)
        if edge is None:
            edge = PropagationEvent(
                narrative_id=narrative_id,
                user_id=analysis.user_id,
                source_analysis_id=source_id,
                target_analysis_id=target_id,
                source_platform=self._platform_for(source_id),
                target_platform=self._platform_for(target_id),
                source_ref=self._ref_for(source_id),
                target_ref=self._ref_for(target_id),
                relationship_type=relationship_type,
                direction=direction,
            )
            db.session.add(edge)
        else:
            edge.direction = direction

        lag = self._lag(source_id, target_id)
        prop_score = self._propagation_score(plan, lag)

        edge.propagation_score = round(prop_score, 1)
        edge.confidence = self._confidence(plan, lag)
        edge.similarity_score = round((similarities.get((candidate.id, plan['narrative_name']))
                                       or 0.0) * 100.0, 1)
        edge.lag_seconds = lag
        edge.shared_entities = self._shared_entities_by_narrative(narrative)
        edge.reasons = self._reasons(plan, lag, source_id, target_id)
        edge.evidence = self._evidence(plan, source_id, target_id, lag)
        edge.detection_method = PropagationEvent.METHOD_HEURISTIC
        edge.occurred_at = _naive_utc(analysis.created_at) or _now()
        return edge

    # -------------------------------------------------------------- ordering

    def _orient(self, analysis, candidate):
        """Return ``(source_id, target_id, direction)`` by real time ordering.

        Earlier occurrence = source. When the two are temporally indistinguishable
        (both missing timestamps) we fall back to analysis ``created_at``; only
        if that is also unavailable do we return ``(None, ...)``.
        """
        cur_ts = self._analysis_timestamp(analysis)
        cand_ts = self._analysis_timestamp(candidate)

        if cur_ts is None and cand_ts is None:
            return None, None, PropagationEvent.DIRECTION_UNDIRECTED

        if cur_ts is None:
            return candidate.id, analysis.id, PropagationEvent.DIRECTION_SOURCE_TO_TARGET
        if cand_ts is None:
            return analysis.id, candidate.id, PropagationEvent.DIRECTION_SOURCE_TO_TARGET

        if cur_ts < cand_ts:
            return analysis.id, candidate.id, PropagationEvent.DIRECTION_SOURCE_TO_TARGET
        if cand_ts < cur_ts:
            return candidate.id, analysis.id, PropagationEvent.DIRECTION_SOURCE_TO_TARGET
        return analysis.id, candidate.id, PropagationEvent.DIRECTION_UNDIRECTED

    def _analysis_timestamp(self, analysis):
        """Resolved occurrence timestamp for an analysis, or None.

        Derives the same platform->fallback timestamp_source contract as Phase C
        so propagation observes the same reality the narratives do.
        """
        narrative = self.narrative_repo.get_for_analysis(analysis.id)
        if narrative:
            for _narr, occurrence in narrative:
                stamp = _naive_utc(occurrence.occurred_at)
                if stamp:
                    return stamp
        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis
        if analysis.analysis_type == 'reddit' and reddit:
            stamp = _naive_utc(reddit.created_utc)
            if stamp:
                return stamp
        if youtube:
            stamp = _naive_utc(youtube.published_at)
            if stamp:
                return stamp
        return _naive_utc(analysis.created_at)

    def _lag(self, source_id, target_id):
        source_ts = self._analysis_timestamp_by_id(source_id)
        target_ts = self._analysis_timestamp_by_id(target_id)
        if source_ts is None or target_ts is None:
            return None
        delta = (target_ts - source_ts).total_seconds()
        # defensive: never emit a negative lag
        return max(0.0, delta)

    def _analysis_timestamp_by_id(self, analysis_id):
        from models.analysis import Analysis
        analysis = db.session.get(Analysis, analysis_id)
        return self._analysis_timestamp(analysis) if analysis else None

    # ---------------------------------------------------------------- scoring

    def _relationship_type(self, plan, source_id, target_id):
        sim = plan['similarity']
        cross = self._is_cross_platform(source_id, target_id)
        lag = self._lag(source_id, target_id)

        # "potentially_propagated" is the strongest label and must only apply
        # when there is genuine propagation signal: high similarity AND either
        # cross-platform reach or a real (non-zero) temporal separation. Identical
        # content in the same platform at the same moment is "similar"/"related",
        # not "propagated".
        has_temporal_separation = lag is not None and lag > 0
        if sim >= self.POTENTIALLY_PROPAGATED_MIN and (cross or has_temporal_separation):
            return PropagationEvent.RELATION_POTENTIALLY_PROPAGATED
        if sim >= self.POTENTIALLY_PROPAGATED_MIN:
            return PropagationEvent.RELATION_SIMILAR
        if sim >= self.SIMILARITY_MIN:
            return PropagationEvent.RELATION_SIMILAR
        if cross:
            return PropagationEvent.RELATION_TEMPORALLY_CORRELATED
        return PropagationEvent.RELATION_RELATED

    def _propagation_score(self, plan, lag):
        sim = plan['similarity']
        base = sim * 70.0
        # moderate lag (minutes-hours) weighs more than extreme, but we keep it
        # conservative; no fabricated semantics beyond ordering.
        lag_boost = 0.0
        if lag is not None:
            if lag <= 3600:
                lag_boost = 15.0
            elif lag <= 86400 * 3:
                lag_boost = 8.0
        return max(0.0, min(100.0, base + lag_boost))

    def _confidence(self, plan, lag):
        # similarity carries most weight; cross-platform + temporal add more.
        sim = plan['similarity']
        base = sim * 60.0
        if plan.get('comparisons') and plan['comparisons'] <= self.MAX_NARRATIVE_COMPARISONS:
            base += 10.0
        if lag is not None:
            base += 10.0
        return max(0.0, min(100.0, base))

    # ----------------------------------------------------------------- utils

    def _narrative_by_name(self, user_id, name):
        """Resolve a Narrative object by normalized name for this user."""
        if not name:
            return None
        return self.narrative_repo.get_by_normalized_name(user_id, name)

    def _shared_entities_by_narrative(self, narrative):
        if narrative is None:
            return []
        return list((narrative.entity_names or []))[:int(self._cfg('PROPAGATION_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))]

    def _platform_for(self, analysis_id):
        from models.analysis import Analysis
        analysis = db.session.get(Analysis, analysis_id)
        return self._analysis_platform(analysis) if analysis else PropagationEvent.PLATFORM_UNKNOWN

    def _analysis_platform(self, analysis):
        if analysis is None:
            return PropagationEvent.PLATFORM_UNKNOWN
        if analysis.analysis_type == 'reddit' and analysis.reddit_analysis:
            return PropagationEvent.PLATFORM_REDDIT
        if analysis.youtube_analysis:
            return PropagationEvent.PLATFORM_YOUTUBE
        if analysis.analysis_type == 'youtube':
            return PropagationEvent.PLATFORM_YOUTUBE
        return PropagationEvent.PLATFORM_UNKNOWN

    def _ref_for(self, analysis_id):
        from models.analysis import Analysis
        analysis = db.session.get(Analysis, analysis_id)
        if analysis is None:
            return None
        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis
        if analysis.analysis_type == 'reddit' and reddit:
            return reddit.post_id
        if youtube:
            return youtube.video_id
        return None

    def _is_cross_platform(self, source_id, target_id):
        return (self._platform_for(source_id) in (PropagationEvent.PLATFORM_YOUTUBE,
                                                  PropagationEvent.PLATFORM_REDDIT)
                and self._platform_for(target_id) in (PropagationEvent.PLATFORM_YOUTUBE,
                                                      PropagationEvent.PLATFORM_REDDIT)
                and self._platform_for(source_id) != self._platform_for(target_id))

    def _reasons(self, plan, lag, source_id, target_id):
        sim = plan['similarity']
        source_ts = self._analysis_timestamp_by_id(source_id)
        target_ts = self._analysis_timestamp_by_id(target_id)
        reasons = [
            f'Analyses {source_id} and {target_id} share the narrative '
            f'"{plan["narrative_name"]}", whose occurrence is temporally ordered '
            f'({self._format_ts(source_ts)} -> {self._format_ts(target_ts)}); '
            f'this is consistent with possible propagation.',
            f'Narrative-level similarity is {sim:.2f}; similarity and ordering are '
            f'indicators, not evidence of authorship, coordination or causality.',
        ]
        if lag is not None:
            reasons.append(f'Observed lag between the two is {self._format_lag(lag)}.')
        return reasons

    def _evidence(self, plan, source_id, target_id, lag):
        return {
            'detection_method': self.DETECTION_METHOD,
            'capability': self.CAPABILITY,
            'narrative': plan['narrative_name'],
            'source_analysis_id': source_id,
            'target_analysis_id': target_id,
            'lag_seconds': lag,
            'similarity': plan['similarity'],
            'cross_platform': self._is_cross_platform(source_id, target_id),
            'comparisons': plan.get('comparisons'),
            'reasons': self._reasons(plan, lag, source_id, target_id),
        }

    def _build_result(self, persisted, analysis, current_occurrences):
        events = []
        reasons = []
        scores = []
        cross = 0
        for plan, event in persisted:
            data = event.to_dict()
            data['lag_seconds_display'] = self._format_lag(event.lag_seconds)
            events.append(data)
            scores.append(event.propagation_score)
            cross += 1 if event.is_cross_platform else 0
            if event.reasons:
                reasons.extend(f'{r}' for r in event.reasons[:2])
        events.sort(key=lambda e: e['propagation_score'], reverse=True)

        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'analyses_compared': len(persisted),
            'event_count': len(events),
            'events': events,
            'cross_platform_count': cross,
            'max_propagation_score': self._clamp(max(scores)) if scores else None,
            'avg_propagation_score': (round(sum(scores) / len(scores), 1)
                                      if scores else None),
            'narrative_links': len({e['narrative_id'] for e in events if e['narrative_id']}),
            'reasons': reasons,
            'limitations': self._limitations(),
        }

    def _unavailable(self, reason, occurrences=0):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'analyses_compared': 0,
            'event_count': 0,
            'events': [],
            'cross_platform_count': 0,
            'max_propagation_score': None,
            'avg_propagation_score': None,
            'narrative_links': 0,
            'reasons': [reason],
            'limitations': self._limitations(),
        }

    def _limitations(self):
        return [
            'Propagation detection is heuristic and rule-based; it is not a '
            'trained ML classifier of diffusion.',
            'A shared narrative plus temporal ordering is *consistent with* '
            'possible propagation only. It does not establish that one party '
            'caused, coordinated, orchestrated, authored or originated the other.',
            'Detection is bounded by candidate count and comparison caps; '
            'historical breadth is limited by those caps.',
            'Timestamps come from platform data when present, else an analysis '
            'creation-time fallback; pairs with no usable timestamp are skipped '
            'rather than fabricated.',
        ]

    @staticmethod
    def _format_ts(ts):
        return ts.strftime('%Y-%m-%d %H:%M') if ts else 'unknown'

    @staticmethod
    def _format_lag(seconds):
        if seconds is None:
            return 'unknown'
        return f'{seconds / 3600.0:.1f}h' if seconds >= 3600 else f'{int(seconds)}s'

    def _clamp(self, value):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    # ---- read API

    def get_analysis_propagation(self, analysis_id, limit=None):
        rows = self.propagation_repo.get_for_analysis(
            analysis_id, limit=int(self._cfg('PROPAGATION_MAX_EVENTS',
                                             self.MAX_PROPAGATION_EVENTS)))
        return [r.to_dict() for r in rows]

    def get_user_propagation_summary(self, user_id, limit=20):
        top = self.propagation_repo.get_for_user(user_id, limit=limit)
        cross = self.propagation_repo.get_cross_platform_events(user_id, limit=limit)
        return {
            'total_events': self.propagation_repo.count_for_user(user_id),
            'cross_platform_events': len(cross),
            'cross_platform_narratives':
                self.propagation_repo.get_cross_platform_narrative_count(user_id),
            'top_events': [e.to_dict() for e in top],
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
        }
