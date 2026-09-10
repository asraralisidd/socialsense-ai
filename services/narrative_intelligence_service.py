"""V12 Narrative Intelligence Engine (Phase C).

Detects recurring narratives/themes across analyzed content and persists them as
``Narrative`` + ``NarrativeOccurrence``.

CAPABILITY HONESTY
------------------
This engine is **heuristic and rule-based**. It performs no model training, loads
no ML model, and contacts no external API. Detection is lexical + entity +
recurrence based, and every stored score carries the inputs that produced it.
The capability vocabulary used throughout is the one declared on
``ThreatAssessment``: ``implemented`` / ``heuristic`` / ``fallback`` /
``future_ml``.

REUSE, NOT REINVENTION
----------------------
* tokenizing / cleaning / keywords / phrases -> ``TranscriptProcessingService`` (V7)
* similarity metrics                         -> ``TextSimilarityService`` (V12, which
                                                itself delegates to V4/V7)
* per-comment risk, toxicity, spam, bot      -> ``CommentResult`` columns (V4)
* entity identity + per-entity risk          -> ``Entity`` / ``EntityContext`` (V8)

UNAVAILABLE != ZERO
-------------------
A risk component that cannot be computed is recorded as ``None`` and excluded
from the weighted combination, whose remaining weights are renormalized (the V11
``AuthenticityService`` pattern). It is never silently coerced to ``0.0``.

BOUNDS
------
Comments scanned, phrases per document, candidates, reuse comparisons, evidence
samples and snippet lengths are all capped by the class constants below. Phase N
will surface these in ``config/settings.py``; until then each is overridable via
``current_app.config`` and falls back to the constant.
"""
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db
from models.comment_result import CommentResult
from models.entity import Entity
from models.entity_context import EntityContext
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from repositories.narrative_repository import NarrativeRepository
from services.text_similarity_service import TextSimilarityService
from services.transcript_processing_service import TranscriptProcessingService

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value):
    """Normalize any datetime to naive UTC.

    Platform timestamps arrive tz-aware (YouTube/Reddit APIs) while every column
    in this project is naive. Mixing the two raises TypeError in comparisons and
    in MIN/MAX, so everything is normalized on the way in.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class NarrativeIntelligenceService:
    # ------------------------------------------------------------- tunables
    MAX_NARRATIVES_PER_ANALYSIS = 12
    MAX_COMMENTS_SCANNED = 300
    MAX_PHRASES_PER_DOCUMENT = 20
    MIN_PHRASE_TOKENS = 2
    MAX_PHRASE_TOKENS = 4
    MIN_NORMALIZED_LENGTH = 5
    MIN_DOCUMENT_FREQUENCY = 2
    MAX_RISK_CONTEXT_DOCS = 20
    MAX_EVIDENCE_SAMPLES = 3
    MAX_EVIDENCE_TERMS = 8
    MAX_EVIDENCE_ENTITIES = 5
    EVIDENCE_SNIPPET_CHARS = 160

    #: Two candidate phrases whose normalized forms reach this Jaccard score are
    #: treated as one narrative *within a single analysis*. Deliberately high so
    #: unrelated themes are never fused.
    CANDIDATE_MERGE_THRESHOLD = 0.85
    #: Threshold for reusing an existing stored narrative when the normalized
    #: name is not an exact match. Higher than the merge threshold because this
    #: decision spans analyses.
    NARRATIVE_REUSE_THRESHOLD = 0.90
    #: Hard cap on similarity comparisons during reuse lookup.
    MAX_REUSE_CANDIDATES = 200

    #: Authoritative sources: the content's own framing. A phrase appearing here
    #: is kept even with document frequency 1.
    AUTHORITATIVE_SOURCES = (
        NarrativeOccurrence.SOURCE_TITLE,
        NarrativeOccurrence.SOURCE_DESCRIPTION,
        NarrativeOccurrence.SOURCE_TRANSCRIPT,
    )
    SOURCE_PRIORITY = (
        NarrativeOccurrence.SOURCE_TITLE,
        NarrativeOccurrence.SOURCE_DESCRIPTION,
        NarrativeOccurrence.SOURCE_TRANSCRIPT,
        NarrativeOccurrence.SOURCE_COMMENT,
    )

    #: Risk component weights. Renormalized over whatever is available.
    RISK_WEIGHTS = {
        'lexicon': 0.30,
        'comment_risk': 0.30,
        'entity_risk': 0.20,
        'spread': 0.20,
    }

    #: Manipulation-marker lexicon. Presence is a *signal*, never proof; the
    #: generated reasons are phrased accordingly.
    RISK_LEXICON = {
        'absolutist_claim': (8, (
            'proven', 'undeniable', 'guaranteed', 'irrefutable', 'always',
            'never', 'everyone knows', 'nobody can deny', 'obviously', 'clearly',
            '100 percent', 'without a doubt',
        )),
        'distrust_conspiracy': (14, (
            'cover up', 'coverup', 'cover-up', 'they don t want you to know',
            'dont want you to know', 'hoax', 'fake news', 'mainstream media',
            'wake up', 'psyop', 'false flag', 'controlled opposition',
            'the truth about', 'exposed', 'silenced', 'they lied',
            'agenda', 'narrative control',
        )),
        'urgency_fear': (10, (
            'urgent', 'before it is too late', 'before its too late', 'collapse',
            'crisis', 'emergency', 'banned', 'censored', 'deleted', 'act now',
            'last chance', 'shocking', 'you won t believe', 'panic',
        )),
        'health_misinfo': (12, (
            'miracle cure', 'big pharma', 'detox', 'natural cure', 'toxin',
            'untested', 'side effects they hide', 'doctors hate',
        )),
        'financial_hype': (10, (
            'to the moon', 'get rich', 'guaranteed return', 'risk free',
            'pump', 'easy money', 'double your money', 'financial freedom',
        )),
    }

    #: Rule-based category assignment. Highest hit count wins; ties break
    #: alphabetically so the result is deterministic.
    CATEGORY_LEXICON = {
        Narrative.CATEGORY_POLITICAL: (
            'election', 'vote', 'voter', 'government', 'senator', 'president',
            'policy', 'parliament', 'campaign', 'democrat', 'republican',
            'minister', 'congress', 'legislation', 'ballot',
        ),
        Narrative.CATEGORY_HEALTH: (
            'vaccine', 'virus', 'covid', 'doctor', 'medicine', 'health',
            'disease', 'treatment', 'hospital', 'pandemic', 'patient', 'clinical',
        ),
        Narrative.CATEGORY_FINANCIAL: (
            'stock', 'market', 'crypto', 'bitcoin', 'invest', 'earning',
            'revenue', 'price', 'inflation', 'economy', 'tariff', 'profit',
            'valuation', 'dividend',
        ),
        Narrative.CATEGORY_TECHNOLOGY: (
            'software', 'app', 'device', 'chip', 'phone', 'computer',
            'algorithm', 'battery', 'hardware', 'update', 'feature', 'processor',
            'ai', 'model',
        ),
        Narrative.CATEGORY_CRISIS: (
            'war', 'attack', 'disaster', 'earthquake', 'flood', 'shooting',
            'evacuation', 'casualty', 'wildfire', 'outbreak',
        ),
        Narrative.CATEGORY_PROMOTIONAL: (
            'buy', 'discount', 'offer', 'subscribe', 'sponsor', 'giveaway',
            'promo', 'coupon', 'affiliate', 'link in bio',
        ),
        Narrative.CATEGORY_CONFLICT: (
            'protest', 'riot', 'clash', 'dispute', 'boycott', 'lawsuit',
            'ban', 'controversy', 'backlash', 'feud',
        ),
        Narrative.CATEGORY_SOCIAL: (
            'community', 'culture', 'family', 'school', 'relationship',
            'student', 'neighbour', 'neighbor', 'society',
        ),
    }

    DETECTION_METHOD = 'heuristic_phrase_recurrence'
    CAPABILITY = 'heuristic'

    #: Engagement filler. A phrase made only of these is chatter, not a
    #: narrative ("great video", "thanks for sharing"). Kept deliberately small
    #: and domain-specific so real themes ("battery life", "fake news") survive.
    FILLER_TOKENS = frozenset({
        'great', 'nice', 'good', 'bad', 'best', 'worst', 'awesome', 'amazing',
        'love', 'like', 'hate', 'true', 'thanks', 'thank', 'please', 'first',
        'video', 'videos', 'content', 'post', 'posts', 'channel', 'comment',
        'comments', 'share', 'sharing', 'informative', 'said', 'agree',
        'keep', 'watching', 'watch', 'subscribe', 'sub', 'guy', 'guys',
        'thing', 'things', 'stuff', 'lol', 'wow', 'yeah', 'okay',
    })

    #: Opaque identifiers (video ids, hashes): >=8 chars, letters+digits mixed.
    _OPAQUE_ID_RE = re.compile(r'^(?=.*\d)(?=.*[a-z])[a-z0-9]{8,}$')

    def __init__(self):
        self.processor = TranscriptProcessingService()
        self.similarity = TextSimilarityService()
        self.narrative_repo = NarrativeRepository()
        self._generic_phrases = None

    # ------------------------------------------------------- filler handling

    def _generic_phrase_set(self):
        """Normalized stock engagement phrases.

        Reuses V4's ``BotDetectionService.GENERIC_PHRASES`` rather than
        maintaining a second copy of the same vocabulary.
        """
        if self._generic_phrases is None:
            from services.bot_detection_service import BotDetectionService
            self._generic_phrases = frozenset(
                self.similarity.normalize_phrase(p)
                for p in BotDetectionService.GENERIC_PHRASES
            ) - {''}
        return self._generic_phrases

    def _meaningful_tokens(self, tokens):
        """Tokens that could plausibly carry a narrative."""
        return [t for t in tokens
                if t not in self.FILLER_TOKENS
                and not self._OPAQUE_ID_RE.match(t)]

    # --------------------------------------------------------------- config

    def _cfg(self, key, default):
        """Config override with a class-constant fallback.

        Tolerates being called outside an application context so the pure
        detection helpers stay unit-testable.
        """
        try:
            from flask import current_app
            if current_app:
                return current_app.config.get(key, default)
        except Exception:
            pass
        return default

    # ----------------------------------------------------------- public API

    def analyze(self, analysis, video_info=None, transcript_text=None,
                entities=None, comments=None):
        """Detect and persist narratives for one analysis.

        Returns a component-contract dict shaped like the V11 authenticity
        components so Phase G can consume it directly. Never raises for
        data-quality reasons; DB failures are rolled back and reported as
        unavailable.
        """
        if not self._cfg('ENABLE_NARRATIVE_INTELLIGENCE', True):
            return self._unavailable('Narrative intelligence is disabled by configuration.')

        try:
            documents = self._build_documents(analysis, video_info, transcript_text, comments)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Narrative document loading failed: {exc}')
            return self._unavailable('Content could not be loaded for narrative detection.')

        if not documents:
            return self._unavailable(
                'No analyzable text was available for narrative detection.')

        entity_names = self._entity_names(analysis, entities)
        try:
            entity_risk_map = self._entity_risk_map(analysis.id)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Narrative entity-risk lookup failed: {exc}')
            entity_risk_map = {}

        candidates = self.detect_candidates(documents, entity_names=entity_names)
        if not candidates:
            return self._unavailable(
                'No recurring narrative met the detection thresholds.',
                documents=len(documents))

        for candidate in candidates:
            self._score_candidate_risk(candidate, documents, entity_risk_map)

        persisted = self._persist(analysis, candidates)
        if persisted is None:
            return self._unavailable(
                'Narratives were detected but could not be persisted.',
                documents=len(documents))

        return self._build_result(persisted, documents)

    def detect_candidates(self, documents, entity_names=None):
        """Pure, deterministic candidate detection. Performs no database work.

        ``documents`` is a list of dicts with at least ``source`` and ``text``.
        Identical input always yields an identically ordered output.
        """
        entity_token_map = self._entity_token_map(entity_names)
        entity_name_set = frozenset(
            n for n in (self.similarity.normalize_phrase(name)
                        for name in (entity_names or [])) if n
        )
        max_phrases = int(self._cfg('NARRATIVE_MAX_PHRASES_PER_DOCUMENT',
                                    self.MAX_PHRASES_PER_DOCUMENT))
        candidates = {}

        for index, doc in enumerate(documents):
            text = doc.get('text') or ''
            if not text.strip():
                continue
            phrases = self.processor.extract_phrases(
                text, self.MIN_PHRASE_TOKENS, self.MAX_PHRASE_TOKENS)[:max_phrases]

            seen_in_doc = set()
            for phrase, count in phrases:
                normalized = self.similarity.normalize_phrase(phrase)
                tokens = normalized.split()
                anchored = self._anchor_entities(tokens, entity_token_map)

                if not self._is_acceptable(normalized, tokens, anchored, entity_name_set):
                    continue

                entry = candidates.get(normalized)
                if entry is None:
                    entry = {
                        'normalized_name': normalized,
                        'surface_counts': {},
                        'total_frequency': 0,
                        'document_indices': set(),
                        'sources': set(),
                        'entities': set(),
                        'merged_from': set(),
                    }
                    candidates[normalized] = entry

                entry['surface_counts'][phrase] = entry['surface_counts'].get(phrase, 0) + count
                entry['total_frequency'] += count
                entry['sources'].add(doc.get('source') or NarrativeOccurrence.SOURCE_COMBINED)
                entry['entities'].update(anchored)
                if normalized not in seen_in_doc:
                    entry['document_indices'].add(index)
                    seen_in_doc.add(normalized)

        accepted = [c for c in candidates.values() if self._passes_threshold(c)]
        merged = self._merge_candidates(accepted)
        merged = self._collapse_nested_candidates(merged)

        for candidate in merged:
            candidate['document_frequency'] = len(candidate['document_indices'])
            candidate['name'] = self._display_name(candidate)
            candidate['relevance_score'] = self._relevance_score(candidate)
            candidate['category'] = self._classify_category(candidate)
            candidate['keywords'] = self._candidate_keywords(candidate)
            candidate['entity_names'] = sorted(candidate['entities'])

        merged.sort(key=lambda c: (-c['relevance_score'], c['normalized_name']))
        limit = int(self._cfg('MAX_NARRATIVES_PER_ANALYSIS',
                              self.MAX_NARRATIVES_PER_ANALYSIS))
        return merged[:max(limit, 0)]

    def get_analysis_narratives(self, analysis_id, limit=None):
        """Narratives attached to one analysis, for the result page / exports."""
        limit = limit or int(self._cfg('MAX_NARRATIVES_PER_ANALYSIS', self.MAX_NARRATIVES_PER_ANALYSIS))
        rows = self.narrative_repo.get_for_analysis(analysis_id, limit=limit)
        out = []
        for narrative, occurrence in rows:
            data = narrative.to_dict()
            data['occurrence'] = occurrence.to_dict()
            out.append(data)
        return out

    def get_user_narrative_summary(self, user_id, limit=10):
        """Bounded user-level rollup. Read-only."""
        top = self.narrative_repo.get_top_for_user(user_id, limit=limit)
        return {
            'total_narratives': self.narrative_repo.count_for_user(user_id),
            'top_narratives': [n.to_dict() for n in top],
            'category_distribution': self.narrative_repo.get_category_distribution(user_id),
            'platform_distribution': self.narrative_repo.get_platform_distribution(user_id),
            'cross_platform_narratives':
                self.narrative_repo.get_cross_platform_narratives(user_id, limit=limit),
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
        }

    # -------------------------------------------------------- document build

    def _build_documents(self, analysis, video_info, transcript_text, comments):
        """Assemble the bounded (source, text) corpus for one analysis."""
        documents = []
        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis
        info = video_info or {}

        title = info.get('title')
        if title is None:
            title = (youtube.video_title if youtube else None) or \
                    (reddit.post_title if reddit else None)
        body = info.get('description') or info.get('body')
        if body is None:
            body = (youtube.video_description if youtube else None) or \
                   (reddit.post_body if reddit else None)

        if title and str(title).strip():
            documents.append({'source': NarrativeOccurrence.SOURCE_TITLE,
                              'text': str(title), 'ref': 'title'})
        if body and str(body).strip():
            documents.append({'source': NarrativeOccurrence.SOURCE_DESCRIPTION,
                              'text': str(body), 'ref': 'description'})

        if transcript_text is None and youtube is not None:
            transcript = youtube.transcript
            if transcript is not None and transcript.is_available:
                transcript_text = transcript.transcript_text
        if transcript_text and str(transcript_text).strip():
            documents.append({'source': NarrativeOccurrence.SOURCE_TRANSCRIPT,
                              'text': str(transcript_text), 'ref': 'transcript'})

        for row in self._load_comments(analysis.id, comments):
            documents.append(row)

        return documents

    def _load_comments(self, analysis_id, comments):
        """Bounded comment load. Never pulls a whole user's history.

        Prefers already-in-memory ``CommentResult`` rows handed in by the
        pipeline (avoids an N+1 re-query); otherwise issues one bounded,
        deterministically ordered query.
        """
        cap = int(self._cfg('NARRATIVE_MAX_COMMENTS_SCANNED', self.MAX_COMMENTS_SCANNED))
        if cap <= 0:
            return []

        rows = comments
        if rows is None:
            rows = CommentResult.query.filter_by(analysis_id=analysis_id).order_by(
                CommentResult.id.asc()).limit(cap).all()
        else:
            rows = list(rows)[:cap]

        documents = []
        for row in rows:
            text = getattr(row, 'comment_text', None)
            if text is None and isinstance(row, dict):
                text = row.get('text') or row.get('comment_text')
            if not text or not str(text).strip():
                continue
            documents.append({
                'source': NarrativeOccurrence.SOURCE_COMMENT,
                'text': str(text),
                'ref': f'comment:{getattr(row, "id", "")}'.rstrip(':'),
                'risk_score': getattr(row, 'risk_score', None),
                'toxicity_score': getattr(row, 'toxicity_score', None),
            })
        return documents

    def _entity_names(self, analysis, entities):
        """Normalized entity names for anchoring. Bounded by V8's own cap."""
        if entities is not None:
            names = []
            for entity in entities:
                name = getattr(entity, 'normalized_name', None)
                if name is None and isinstance(entity, dict):
                    name = entity.get('normalized_name') or entity.get('name')
                if name:
                    names.append(name)
            return sorted(set(names))
        try:
            cap = int(self._cfg('MAX_ENTITIES_PER_ANALYSIS', 100))
            rows = db.session.query(Entity.normalized_name).filter(
                Entity.analysis_id == analysis.id
            ).group_by(Entity.normalized_name).limit(cap).all()
            return sorted({r[0] for r in rows if r[0]})
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Narrative entity-name lookup failed: {exc}')
            return []

    def _entity_token_map(self, entity_names):
        mapping = {}
        for name in entity_names or []:
            normalized = self.similarity.normalize_phrase(name)
            if not normalized:
                continue
            for token in normalized.split():
                mapping.setdefault(token, set()).add(name)
        return mapping

    def _anchor_entities(self, tokens, entity_token_map):
        anchored = set()
        for token in tokens:
            names = entity_token_map.get(token)
            if names:
                anchored.update(names)
        return anchored

    def _entity_risk_map(self, analysis_id):
        """Mean V8 entity risk per entity name.

        PostgreSQL-safe aggregate: the single selected non-aggregate column is
        listed explicitly in GROUP BY.
        """
        rows = db.session.query(
            Entity.normalized_name,
            func.avg(EntityContext.entity_risk_score).label('avg_risk'),
        ).join(
            EntityContext, EntityContext.entity_id == Entity.id
        ).filter(
            Entity.analysis_id == analysis_id
        ).group_by(
            Entity.normalized_name
        ).all()
        return {r.normalized_name: float(r.avg_risk)
                for r in rows if r.avg_risk is not None}

    # ------------------------------------------------------------ candidates

    def _is_acceptable(self, normalized, tokens, anchored, entity_names):
        """Conservative acceptance test.

        Rejects: too-short forms, stock engagement phrases, and anything whose
        tokens are all filler or opaque identifiers. A single-token narrative is
        accepted only when that token *is* a detected entity in its own right
        (so "tesla" survives but "video" does not).
        """
        if len(normalized) < self.MIN_NORMALIZED_LENGTH or not tokens:
            return False
        if normalized in self._generic_phrase_set():
            return False

        meaningful = self._meaningful_tokens(tokens)
        if not meaningful:
            return False
        if len(meaningful) >= 2:
            return True
        return meaningful[0] in entity_names or normalized in entity_names

    def _passes_threshold(self, candidate):
        """Keep recurring phrases, plus anything the content itself framed."""
        min_df = int(self._cfg('NARRATIVE_MIN_DOCUMENT_FREQUENCY',
                               self.MIN_DOCUMENT_FREQUENCY))
        if len(candidate['document_indices']) >= min_df:
            return True
        return bool(candidate['sources'].intersection(self.AUTHORITATIVE_SOURCES))

    def _merge_candidates(self, candidates):
        """Fuse near-identical candidates within one analysis.

        Conservative: uses Jaccard over normalized token sets at
        ``CANDIDATE_MERGE_THRESHOLD``. "battery" and "battery life" score 0.5 and
        stay distinct; "the battery life" normalizes to "battery life" and is
        already identical before this step.
        """
        ordered = sorted(
            candidates,
            key=lambda c: (-len(c['document_indices']), -c['total_frequency'],
                           c['normalized_name']),
        )
        threshold = float(self._cfg('NARRATIVE_MERGE_THRESHOLD',
                                    self.CANDIDATE_MERGE_THRESHOLD))
        accepted = []
        for candidate in ordered:
            target, score, _stats = self.similarity.best_match(
                candidate['normalized_name'], accepted, threshold=threshold,
                metric=TextSimilarityService.METRIC_JACCARD,
                key=lambda c: c['normalized_name'],
            )
            if target is None:
                accepted.append(candidate)
                continue
            for surface, count in candidate['surface_counts'].items():
                target['surface_counts'][surface] = \
                    target['surface_counts'].get(surface, 0) + count
            target['total_frequency'] += candidate['total_frequency']
            target['document_indices'].update(candidate['document_indices'])
            target['sources'].update(candidate['sources'])
            target['entities'].update(candidate['entities'])
            target['merged_from'].add(candidate['normalized_name'])
        return accepted

    def _collapse_nested_candidates(self, candidates):
        """Collapse nested n-grams that describe the *same* textual span.

        The phrase extractor emits every 2-4 token window, so one theme yields
        overlapping variants ("socialsense ai", "testing socialsense ai",
        "testing socialsense ai feature"). Two conditions must BOTH hold before
        collapsing, which keeps this deduplication rather than over-merging:

          1. one token set fully contains the other (containment == 1.0), and
          2. the narrower candidate appears in no document the kept one misses
             (its document set is a subset) - i.e. it adds no new coverage.

        Unrelated narratives share no containment relationship and are never
        touched. The retained form is the one with the widest document coverage,
        then the shorter (more general) phrase, so narratives stay comparable
        across content.
        """
        ordered = sorted(
            candidates,
            key=lambda c: (-len(c['document_indices']),
                           len(c['normalized_name'].split()),
                           c['normalized_name']),
        )
        accepted = []
        for candidate in ordered:
            host = None
            for existing in accepted:
                if self.similarity.containment(existing['normalized_name'],
                                               candidate['normalized_name']) < 1.0:
                    continue
                if candidate['document_indices'].issubset(existing['document_indices']):
                    host = existing
                    break
            if host is None:
                accepted.append(candidate)
                continue
            for surface, count in candidate['surface_counts'].items():
                host['surface_counts'][surface] = \
                    host['surface_counts'].get(surface, 0) + count
            host['total_frequency'] += candidate['total_frequency']
            host['sources'].update(candidate['sources'])
            host['entities'].update(candidate['entities'])
            host['merged_from'].add(candidate['normalized_name'])
        return accepted

    def _display_name(self, candidate):
        """Most frequent original surface form; alphabetical tie-break."""
        if not candidate['surface_counts']:
            return candidate['normalized_name']
        return sorted(candidate['surface_counts'].items(),
                      key=lambda kv: (-kv[1], kv[0]))[0][0]

    def _relevance_score(self, candidate):
        score = 0.0
        score += min(len(candidate['document_indices']), 8) * 8.0
        score += min(candidate['total_frequency'], 12) * 2.0
        if NarrativeOccurrence.SOURCE_TITLE in candidate['sources']:
            score += 20.0
        if NarrativeOccurrence.SOURCE_DESCRIPTION in candidate['sources']:
            score += 10.0
        if NarrativeOccurrence.SOURCE_TRANSCRIPT in candidate['sources']:
            score += 10.0
        if candidate['entities']:
            score += 12.0
        if len(candidate['sources']) > 1:
            score += 8.0
        return self._clamp(score)

    def _candidate_keywords(self, candidate):
        terms = sorted(candidate['surface_counts'].items(), key=lambda kv: (-kv[1], kv[0]))
        keywords = [candidate['normalized_name']]
        for surface, _count in terms:
            cleaned = self.processor.clean_text(surface)
            if cleaned and cleaned not in keywords:
                keywords.append(cleaned)
            if len(keywords) >= int(self._cfg('NARRATIVE_MAX_EVIDENCE_TERMS', self.MAX_EVIDENCE_TERMS)):
                break
        return keywords

    def _classify_category(self, candidate):
        haystack = ' '.join([candidate['normalized_name']]
                            + list(candidate['surface_counts'].keys())).lower()
        hits = {}
        for category, terms in self.CATEGORY_LEXICON.items():
            count = sum(1 for term in terms if term in haystack)
            if count:
                hits[category] = count
        if not hits:
            return Narrative.CATEGORY_GENERAL
        return sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    # ---------------------------------------------------------------- scoring

    def _score_candidate_risk(self, candidate, documents, entity_risk_map):
        """Attach explainable risk + confidence to a candidate.

        Components that cannot be computed are ``None`` (unavailable) and are
        excluded from the weighted mean, whose weights are then renormalized.
        """
        matched = [documents[i] for i in sorted(candidate['document_indices'])
                   if 0 <= i < len(documents)]
        context_docs = matched[:int(self._cfg('NARRATIVE_MAX_RISK_CONTEXT_DOCS', self.MAX_RISK_CONTEXT_DOCS))]

        lexicon_score, lexicon_hits = self._lexicon_score(candidate, context_docs)

        comment_docs = [d for d in matched
                        if d.get('source') == NarrativeOccurrence.SOURCE_COMMENT
                        and d.get('risk_score') is not None]
        comment_risk = None
        if comment_docs:
            comment_risk = self._clamp(
                sum(float(d['risk_score']) for d in comment_docs) / len(comment_docs))

        entity_scores = [entity_risk_map[name] for name in candidate['entities']
                         if name in entity_risk_map]
        entity_risk = None
        if entity_scores:
            entity_risk = self._clamp(sum(entity_scores) / len(entity_scores))

        spread = self._clamp(len(candidate['document_indices']) * 10.0
                             + (len(candidate['sources']) - 1) * 15.0)

        components = {
            'lexicon': lexicon_score,
            'comment_risk': comment_risk,
            'entity_risk': entity_risk,
            'spread': spread,
        }
        available = {k: v for k, v in components.items() if v is not None}
        unavailable = sorted(k for k, v in components.items() if v is None)

        total_weight = sum(self.RISK_WEIGHTS[k] for k in available)
        if total_weight > 0:
            weighted = sum(self.RISK_WEIGHTS[k] * v for k, v in available.items())
            risk_score = self._clamp(round(weighted / total_weight, 1))
        else:
            risk_score = 0.0

        coverage = len(available) / len(self.RISK_WEIGHTS)
        values = list(available.values())
        agreement = 100.0 - (max(values) - min(values)) if len(values) > 1 else 70.0
        evidence_factor = min(100.0, len(candidate['document_indices']) * 20.0
                              + candidate['total_frequency'] * 3.0)
        confidence = self._clamp(round(
            0.45 * coverage * 100.0 + 0.30 * agreement + 0.25 * evidence_factor, 1))

        reasons = self._candidate_reasons(candidate, components, lexicon_hits, unavailable)

        candidate['risk_score'] = risk_score
        candidate['confidence'] = confidence
        candidate['risk_components'] = components
        candidate['unavailable_signals'] = unavailable
        candidate['lexicon_hits'] = lexicon_hits
        candidate['reasons'] = reasons
        candidate['indicators'] = sorted(lexicon_hits.keys())
        candidate['confidence_inputs'] = {
            'coverage': round(coverage, 3),
            'agreement': round(agreement, 1),
            'evidence_factor': round(evidence_factor, 1),
        }
        candidate['evidence_samples'] = [
            {'source': d.get('source'), 'ref': d.get('ref'),
             'snippet': self.similarity.snippet(d.get('text'),
                                                int(self._cfg('NARRATIVE_EVIDENCE_SNIPPET_CHARS',
                                                              self.EVIDENCE_SNIPPET_CHARS)))}
            for d in matched[:int(self._cfg('NARRATIVE_MAX_EVIDENCE_SAMPLES',
                                            self.MAX_EVIDENCE_SAMPLES))]
        ]
        return candidate

    def _lexicon_score(self, candidate, context_docs):
        haystack = ' '.join(
            [self.processor.clean_text(candidate['normalized_name'])]
            + [self.processor.clean_text(d.get('text') or '') for d in context_docs]
        )
        score = 0.0
        hits = {}
        for group, (weight, terms) in self.RISK_LEXICON.items():
            matched_terms = [t for t in terms if t in haystack]
            if matched_terms:
                hits[group] = sorted(matched_terms)[:3]
                score += weight * min(len(matched_terms), 3)
        return self._clamp(score), hits

    def _candidate_reasons(self, candidate, components, lexicon_hits, unavailable):
        """Human-readable, hedged explanations."""
        reasons = []
        df = len(candidate['document_indices'])
        if df > 1:
            reasons.append(
                f'Appears in {df} separate pieces of analyzed text '
                f'({candidate["total_frequency"]} total mentions).')
        else:
            reasons.append(
                'Framed by the content itself (title, description or transcript).')
        if len(candidate['sources']) > 1:
            reasons.append('Recurs across multiple sources: '
                           + ', '.join(sorted(candidate['sources'])) + '.')
        if candidate['entities']:
            reasons.append('Anchored to detected entities: '
                           + ', '.join(sorted(candidate['entities'])[:int(self._cfg('NARRATIVE_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))])
                           + '.')
        for group, terms in sorted(lexicon_hits.items()):
            reasons.append(
                f'Possible {group.replace("_", " ")} language present '
                f'({", ".join(terms)}) - a signal, not proof.')
        if components.get('comment_risk') is not None:
            reasons.append(
                f'Mean risk of the comments carrying this narrative is '
                f'{components["comment_risk"]:.1f} (from V4 per-comment scoring).')
        for name in unavailable:
            reasons.append(f'Signal unavailable and excluded from scoring: {name}.')
        reasons.append('Detection is heuristic (lexical, entity and recurrence based); '
                       'it is not a trained classifier.')
        return reasons

    # ------------------------------------------------------------ persistence

    def _persist(self, analysis, candidates):
        """Create/reuse narratives and their occurrences.

        One commit for the whole batch - never per item. On IntegrityError the
        session is rolled back and the batch is retried once, at which point the
        conflicting narrative is found by exact lookup. Returns ``None`` if
        persistence ultimately failed.
        """
        for attempt in (1, 2):
            try:
                return self._persist_once(analysis, candidates)
            except IntegrityError as exc:
                db.session.rollback()
                if attempt == 2:
                    logger.warning(f'Narrative persistence conflict, giving up: {exc}')
                    return None
                logger.info('Narrative persistence conflict, retrying once after rollback.')
            except SQLAlchemyError as exc:
                db.session.rollback()
                logger.warning(f'Narrative persistence failed: {exc}')
                return None
        return None

    def _persist_once(self, analysis, candidates):
        platform = self._platform_for(analysis)
        channel_id = self._channel_id_for(analysis)
        content_ref = self._content_ref_for(analysis)
        occurred_at, timestamp_source = self._resolve_occurred_at(analysis)

        reuse_pool = self.narrative_repo.get_recent_for_user(
            analysis.user_id,
            limit=int(self._cfg('NARRATIVE_MAX_REUSE_CANDIDATES',
                                self.MAX_REUSE_CANDIDATES)),
        )
        reuse_index = {n.normalized_name: n for n in reuse_pool}

        touched = []
        for candidate in candidates:
            narrative = self._get_or_create_narrative(
                analysis.user_id, candidate, reuse_pool, reuse_index)
            self._upsert_occurrence(
                narrative, analysis, candidate, platform, channel_id,
                content_ref, occurred_at, timestamp_source)
            touched.append((narrative, candidate))

        db.session.flush()

        results = []
        for narrative, candidate in touched:
            self._refresh_narrative_stats(narrative, candidate)
            results.append({'narrative': narrative, 'candidate': candidate})

        db.session.commit()
        return results

    def _get_or_create_narrative(self, user_id, candidate, reuse_pool, reuse_index):
        normalized = candidate['normalized_name']

        narrative = reuse_index.get(normalized)
        if narrative is None:
            narrative = self.narrative_repo.get_by_normalized_name(user_id, normalized)

        if narrative is None:
            # Bounded fuzzy reuse so trivial variants do not create near-duplicates.
            match, score, stats = self.similarity.best_match(
                normalized, reuse_pool,
                threshold=float(self._cfg('NARRATIVE_REUSE_THRESHOLD',
                                          self.NARRATIVE_REUSE_THRESHOLD)),
                metric=TextSimilarityService.METRIC_JACCARD,
                max_comparisons=int(self._cfg('NARRATIVE_MAX_REUSE_CANDIDATES',
                                              self.MAX_REUSE_CANDIDATES)),
                key=lambda n: n.normalized_name,
            )
            candidate['reuse_lookup'] = {
                'matched': match is not None,
                'score': score,
                'comparisons': stats['comparisons'],
                'truncated': stats['truncated'],
            }
            narrative = match

        if narrative is None:
            narrative = Narrative(
                user_id=user_id,
                name=candidate['name'],
                normalized_name=normalized,
                category=candidate['category'],
                detection_method=Narrative.METHOD_HEURISTIC,
                risk_score=candidate['risk_score'],
                confidence=candidate['confidence'],
                keywords=candidate['keywords'],
                entity_names=candidate['entity_names'],
                first_seen_at=_now(),
                last_seen_at=_now(),
            )
            db.session.add(narrative)
            db.session.flush()            # need the id for the occurrence
            reuse_index[normalized] = narrative
            reuse_pool.append(narrative)
            candidate['created'] = True
        else:
            candidate['created'] = False
            narrative.keywords = self._merge_unique(
                narrative.keywords, candidate['keywords'],
                int(self._cfg('NARRATIVE_MAX_EVIDENCE_TERMS', self.MAX_EVIDENCE_TERMS)))
            narrative.entity_names = self._merge_unique(
                narrative.entity_names, candidate['entity_names'],
                int(self._cfg('NARRATIVE_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES)) * 4)
            if narrative.category in (Narrative.CATEGORY_UNKNOWN,
                                      Narrative.CATEGORY_GENERAL):
                narrative.category = candidate['category']

        candidate['description'] = self._describe(candidate)
        narrative.description = candidate['description']
        narrative.evidence = self._build_evidence(candidate)
        return narrative

    def _upsert_occurrence(self, narrative, analysis, candidate, platform,
                           channel_id, content_ref, occurred_at, timestamp_source):
        """Explicit existence check, then update-in-place or insert.

        Checked by (narrative_id, analysis_id) rather than including ``source``,
        so a re-run that resolves a different primary source updates the existing
        row instead of adding a second one. The DB constraint remains as a guard.
        """
        source = self._primary_source(candidate['sources'])
        occurrence = self.narrative_repo.get_occurrence_for_analysis(
            narrative.id, analysis.id) if narrative.id else None

        if occurrence is None:
            occurrence = NarrativeOccurrence(
                narrative_id=narrative.id,
                analysis_id=analysis.id,
                user_id=analysis.user_id,
                platform=platform,
                source=source,
                channel_id=channel_id,
                content_ref=content_ref,
                occurred_at=occurred_at,
                timestamp_source=timestamp_source,
            )
            db.session.add(occurrence)
            candidate['occurrence_created'] = True
        else:
            candidate['occurrence_created'] = False
            occurrence.platform = platform
            occurrence.source = source
            occurrence.channel_id = channel_id
            occurrence.content_ref = content_ref
            occurrence.occurred_at = occurred_at
            occurrence.timestamp_source = timestamp_source

        occurrence.relevance_score = candidate['relevance_score']
        occurrence.risk_score = candidate['risk_score']
        occurrence.match_count = candidate['total_frequency']
        occurrence.evidence = self._build_evidence(candidate)
        candidate['occurrence'] = occurrence
        return occurrence

    def _refresh_narrative_stats(self, narrative, candidate):
        """Recompute rollups from stored rows, never from inference."""
        stats = self.narrative_repo.get_occurrence_stats(narrative.id)

        narrative.occurrence_count = stats['occurrence_count']
        narrative.platform_count = stats['platform_count']
        if stats['first_seen_at']:
            narrative.first_seen_at = stats['first_seen_at']
        if stats['last_seen_at']:
            narrative.last_seen_at = stats['last_seen_at']

        # Strongest observed instance, derivable from stored occurrences.
        if stats['max_risk_score'] is not None:
            narrative.risk_score = self._clamp(round(stats['max_risk_score'], 1))
        else:
            narrative.risk_score = candidate['risk_score']

        recurrence = min(100.0, stats['occurrence_count'] * 25.0
                         + max(stats['platform_count'] - 1, 0) * 25.0)
        inputs = candidate['confidence_inputs']
        narrative.confidence = self._clamp(round(
            0.40 * inputs['coverage'] * 100.0
            + 0.25 * inputs['agreement']
            + 0.20 * inputs['evidence_factor']
            + 0.15 * recurrence, 1))

        # growth_score is computed by the Phase F temporal engine.
        candidate['recurrence_factor'] = round(recurrence, 1)
        candidate['stats'] = stats
        return narrative

    # ---------------------------------------------------------------- helpers

    def _build_evidence(self, candidate):
        """Concise, bounded evidence. Snippets, never raw content blobs."""
        return {
            'detection_method': self.DETECTION_METHOD,
            'capability': self.CAPABILITY,
            'document_frequency': len(candidate['document_indices']),
            'total_frequency': candidate['total_frequency'],
            'sources': sorted(candidate['sources']),
            'matched_terms': candidate['keywords'][:int(self._cfg('NARRATIVE_MAX_EVIDENCE_TERMS', self.MAX_EVIDENCE_TERMS))],
            'entities': candidate['entity_names'][:int(self._cfg('NARRATIVE_MAX_EVIDENCE_ENTITIES', self.MAX_EVIDENCE_ENTITIES))],
            'merged_variants': sorted(candidate['merged_from'])[:int(self._cfg('NARRATIVE_MAX_EVIDENCE_TERMS', self.MAX_EVIDENCE_TERMS))],
            'relevance_score': candidate['relevance_score'],
            'risk_score': candidate['risk_score'],
            'risk_components': candidate['risk_components'],
            'risk_weights': self.RISK_WEIGHTS,
            'unavailable_signals': candidate['unavailable_signals'],
            'confidence_inputs': candidate['confidence_inputs'],
            'indicators': candidate['indicators'],
            'reasons': candidate['reasons'],
            'samples': candidate['evidence_samples'],
        }

    def _describe(self, candidate):
        return (f'Recurring narrative "{candidate["name"]}" '
                f'({candidate["category"]}) detected in '
                f'{len(candidate["document_indices"])} text source(s) via '
                f'heuristic phrase recurrence.')

    def _merge_unique(self, existing, incoming, cap):
        out = []
        for value in list(existing or []) + list(incoming or []):
            if value and value not in out:
                out.append(value)
            if len(out) >= cap:
                break
        return out

    def _primary_source(self, sources):
        if len(sources) > 1:
            return NarrativeOccurrence.SOURCE_COMBINED
        for source in self.SOURCE_PRIORITY:
            if source in sources:
                return source
        return NarrativeOccurrence.SOURCE_COMBINED

    def _platform_for(self, analysis):
        if analysis.analysis_type == 'reddit' and analysis.reddit_analysis:
            return NarrativeOccurrence.PLATFORM_REDDIT
        if analysis.youtube_analysis:
            return NarrativeOccurrence.PLATFORM_YOUTUBE
        if analysis.analysis_type == 'youtube':
            return NarrativeOccurrence.PLATFORM_YOUTUBE
        return NarrativeOccurrence.PLATFORM_UNKNOWN

    def _channel_id_for(self, analysis):
        """Channel slug, derived identically to the V9 history pipeline so
        Phase E can join V12 narratives onto V9 channel history."""
        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis
        if analysis.analysis_type == 'reddit' and reddit:
            return (reddit.subreddit or 'reddit').replace(' ', '_').lower()
        if youtube:
            return (youtube.channel_name or 'Unknown').replace(' ', '_').lower()
        return None

    def _content_ref_for(self, analysis):
        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis
        if analysis.analysis_type == 'reddit' and reddit:
            return reddit.post_id
        if youtube:
            return youtube.video_id
        return None

    def _resolve_occurred_at(self, analysis):
        """Real platform timestamp when present, else the analysis timestamp.

        NULL platform timestamps are common, so the fallback is explicit and
        recorded in ``timestamp_source`` rather than hidden.
        """
        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis

        if analysis.analysis_type == 'reddit' and reddit:
            stamp = _naive_utc(reddit.created_utc)
            if stamp:
                return stamp, NarrativeOccurrence.TIMESTAMP_PLATFORM
        if youtube:
            stamp = _naive_utc(youtube.published_at)
            if stamp:
                return stamp, NarrativeOccurrence.TIMESTAMP_PLATFORM

        return (_naive_utc(analysis.created_at) or _now(),
                NarrativeOccurrence.TIMESTAMP_ANALYSIS)

    def _build_result(self, persisted, documents):
        narratives = []
        risk_values = []
        indicators = set()
        reasons = []

        for item in persisted:
            narrative, candidate = item['narrative'], item['candidate']
            data = narrative.to_dict()
            data.update({
                'relevance_score': candidate['relevance_score'],
                'occurrence_risk_score': candidate['risk_score'],
                'created': candidate.get('created', False),
                'occurrence_created': candidate.get('occurrence_created', False),
                'unavailable_signals': candidate['unavailable_signals'],
                'reasons': candidate['reasons'],
                'indicators': candidate['indicators'],
            })
            narratives.append(data)
            risk_values.append(narrative.risk_score or 0.0)
            indicators.update(candidate['indicators'])
            reasons.extend(f'[{narrative.normalized_name}] {r}'
                           for r in candidate['reasons'][:3])

        narratives.sort(key=lambda n: (-(n.get('relevance_score') or 0.0),
                                       n.get('normalized_name') or ''))

        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'documents_analyzed': len(documents),
            'narrative_count': len(narratives),
            'narratives': narratives,
            'top_narrative': narratives[0]['name'] if narratives else None,
            'max_risk_score': self._clamp(max(risk_values)) if risk_values else None,
            'avg_risk_score': (self._clamp(round(sum(risk_values) / len(risk_values), 1))
                               if risk_values else None),
            'cross_platform_count': sum(1 for n in narratives
                                        if n.get('is_cross_platform')),
            'reasons': reasons,
            'indicators': sorted(indicators),
            'limitations': self._limitations(),
        }

    def _unavailable(self, reason, documents=0):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'documents_analyzed': documents,
            'narrative_count': 0,
            'narratives': [],
            'top_narrative': None,
            # None, not 0.0 - "unavailable" must stay distinguishable.
            'max_risk_score': None,
            'avg_risk_score': None,
            'cross_platform_count': 0,
            'reasons': [reason],
            'indicators': [],
            'limitations': self._limitations(),
        }

    def _limitations(self):
        return [
            'Narrative detection is heuristic (lexical, entity and recurrence '
            'based). It is not a trained or semantic ML classifier.',
            'Phrase normalization is deliberately conservative, so paraphrased '
            'narratives that share no vocabulary are not merged.',
            'Manipulation-marker vocabulary indicates a possible signal only; it '
            'is not evidence of intent or coordination.',
            'Cross-platform status reflects stored occurrences only and is never '
            'inferred from textual similarity alone.',
            'Narrative growth over time is not scored in this phase.',
        ]

    def _clamp(self, value):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
