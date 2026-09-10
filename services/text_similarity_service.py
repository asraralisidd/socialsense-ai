"""V12 shared text-similarity primitives.

This module is the single tunable home for the similarity maths V12 needs. It
deliberately **delegates** to the canonical implementations that already exist
in V1-V11 instead of reimplementing them, so there is exactly one definition of
each metric in the codebase:

    jaccard(a, b)        -> BotDetectionService._jaccard_similarity
                            |A n B| / |A u B|
    overlap_ratio(a, b)  -> SpamService._text_similarity
                            |A n B| / max(|A|, |B|)
    tfidf(a, b)          -> ContextMatchingService._tfidf_similarity
                            asymmetric TF-IDF weighted overlap of a against b

``containment`` and ``normalize_phrase`` are new: no equivalent existed in
V1-V11, so they are defined here rather than duplicated.

Everything here is **heuristic and deterministic** - identical inputs always
produce identical outputs, which is what makes the V12 tests reproducible. No
model is trained or loaded, and no external service is contacted.
"""
import re
from functools import lru_cache

from services.transcript_processing_service import TranscriptProcessingService, STOPWORDS


@lru_cache(maxsize=4096)
def _tokens_cached(text):
    """Tokenize via the existing cleaner. Cached because V12 compares the same
    strings repeatedly; ``maxsize`` keeps memory bounded on modest hardware."""
    return tuple(TranscriptProcessingService.clean_text(text or '').split())


@lru_cache(maxsize=4096)
def _normalize_cached(text):
    return TextSimilarityService._normalize_phrase_uncached(text)


class TextSimilarityService:
    """Deterministic, bounded similarity helpers for the V12 engines."""

    #: Tokens shorter than this are dropped during normalization unless they are
    #: whitelisted (see ``_SHORT_TOKEN_ALLOWLIST``).
    MIN_TOKEN_LENGTH = 3

    #: Short tokens that carry real meaning in this domain and must survive
    #: normalization.
    _SHORT_TOKEN_ALLOWLIST = frozenset({'ai', 'ev', 'us', 'uk', 'eu', 'un', 'fbi',
                                        'cia', 'gdp', 'ceo', 'cfo', 'irs', 'fda',
                                        'who', 'nhs', 'nsa', 'gpt', 'llm', 'api',
                                        '5g', '4g', 'tv', 'pc', 'os'})

    METRIC_JACCARD = 'jaccard'
    METRIC_OVERLAP = 'overlap'
    METRIC_CONTAINMENT = 'containment'

    #: Separators that join words. ``TranscriptProcessingService.clean_text``
    #: *deletes* punctuation, so "cover-up" would become "coverup" and fail to
    #: match "cover up". V12 splits these into spaces first. The V7 cleaner is
    #: left untouched.
    _SEPARATOR_RE = re.compile(r'[-_/\\|+.,:;]+')

    # ------------------------------------------------------------------ tokens

    @staticmethod
    def tokens(text):
        """Ordered token tuple for ``text`` (cleaned, lowercased)."""
        return _tokens_cached(text or '')

    @staticmethod
    def token_set(text):
        return frozenset(_tokens_cached(text or ''))

    # ----------------------------------------------------------- normalization

    @staticmethod
    def normalize_phrase(text):
        """Deterministic, conservative narrative-identity normalization.

        Steps, in order:
          1. split word-joining separators ("cover-up" -> "cover up") so that
             hyphenated and spaced spellings share one identity
          2. clean (lowercase, strip punctuation, collapse whitespace) via the
             existing ``TranscriptProcessingService.clean_text``
          3. drop **edge** stopwords only - interior stopwords are preserved so
             "dogs bite men" and "men bite dogs" stay distinct
          4. fold simple English plurals (conservative rules, see
             ``_singularize``)
          5. drop tokens shorter than ``MIN_TOKEN_LENGTH`` unless allowlisted,
             which also removes short interior function words ("of", "in")

        Token **order is preserved** - deliberately not sorted, because sorting
        would collapse semantically different phrases onto one identity.
        """
        return _normalize_cached(text or '')

    @staticmethod
    def _normalize_phrase_uncached(text):
        prepared = TextSimilarityService._SEPARATOR_RE.sub(' ', text or '')
        tokens = list(TranscriptProcessingService.clean_text(prepared).split())

        # 2. edge stopwords only
        while tokens and tokens[0] in STOPWORDS:
            tokens.pop(0)
        while tokens and tokens[-1] in STOPWORDS:
            tokens.pop()

        out = []
        for tok in tokens:
            tok = TextSimilarityService._singularize(tok)
            if (len(tok) >= TextSimilarityService.MIN_TOKEN_LENGTH
                    or tok in TextSimilarityService._SHORT_TOKEN_ALLOWLIST):
                out.append(tok)
        return ' '.join(out)

    @staticmethod
    def _singularize(token):
        """Conservative plural folding. Only the rules below are applied; no
        general stemmer is used, to avoid collapsing unrelated words.

        The bias is deliberately toward UNDER-folding: leaving a plural
        unfolded keeps two narratives distinct (safe), whereas over-folding
        would fuse unrelated narratives (unsafe).
        """
        if len(token) > 4 and token.endswith('ies'):
            return token[:-3] + 'y'                     # batteries -> battery
        if len(token) > 4 and token.endswith('sses'):
            return token[:-2]                           # glasses -> glass
        if len(token) > 4 and token.endswith(('xes', 'zes', 'ches', 'shes')):
            return token[:-2]                           # taxes -> tax, matches -> match
        if (len(token) > 3 and token.endswith('s')
                and not token.endswith(('ss', 'us', 'is', 'as', 'os'))):
            return token[:-1]                           # prices -> price, causes -> cause
        return token

    # -------------------------------------------------------------- metrics

    @staticmethod
    def jaccard(text_a, text_b):
        """|A n B| / |A u B|. Delegates to the V4 bot-detection implementation."""
        from services.bot_detection_service import BotDetectionService
        return BotDetectionService._jaccard_similarity(text_a or '', text_b or '')

    @staticmethod
    def overlap_ratio(text_a, text_b):
        """|A n B| / max(|A|, |B|). Delegates to the V4 spam implementation."""
        from services.spam_service import SpamService
        return SpamService._text_similarity(text_a or '', text_b or '')

    @staticmethod
    def containment(text_a, text_b):
        """|A n B| / min(|A|, |B|).

        New in V12: measures whether the smaller phrase is subsumed by the
        larger one. No V1-V11 equivalent existed.
        """
        a = TextSimilarityService.token_set(text_a)
        b = TextSimilarityService.token_set(text_b)
        if not a or not b:
            return 0.0
        return len(a & b) / min(len(a), len(b))

    @staticmethod
    def tfidf(text, reference_text):
        """Asymmetric TF-IDF weighted overlap of ``text`` against
        ``reference_text``. Delegates to the V7 context-matching implementation.
        """
        from services.context_matching_service import ContextMatchingService
        svc = ContextMatchingService()
        cleaned = TranscriptProcessingService.clean_text(text or '')
        if not cleaned or not (reference_text or '').strip():
            return 0.0
        return svc._tfidf_similarity(cleaned, reference_text)

    @classmethod
    def score(cls, text_a, text_b, metric=METRIC_JACCARD):
        if metric == cls.METRIC_OVERLAP:
            return cls.overlap_ratio(text_a, text_b)
        if metric == cls.METRIC_CONTAINMENT:
            return cls.containment(text_a, text_b)
        return cls.jaccard(text_a, text_b)

    # ------------------------------------------------------- bounded matching

    @classmethod
    def best_match(cls, target, candidates, threshold=0.85,
                   metric=METRIC_JACCARD, max_comparisons=None, key=None):
        """Highest-scoring candidate at or above ``threshold``.

        Always **bounded**: stops after ``max_comparisons`` comparisons and
        reports whether it truncated, so callers can be honest about coverage.

        Returns ``(candidate, score, stats)`` where ``stats`` is
        ``{'comparisons': int, 'truncated': bool}``. ``candidate`` is ``None``
        when nothing reached the threshold.
        """
        best, best_score = None, 0.0
        comparisons = 0
        truncated = False

        for candidate in candidates or []:
            if max_comparisons is not None and comparisons >= max_comparisons:
                truncated = True
                break
            text = key(candidate) if key else candidate
            comparisons += 1
            value = cls.score(target, text, metric)
            if value > best_score:
                best, best_score = candidate, value

        if best_score < threshold:
            return None, round(best_score, 4), {'comparisons': comparisons,
                                                'truncated': truncated}
        return best, round(best_score, 4), {'comparisons': comparisons,
                                            'truncated': truncated}

    # ------------------------------------------------------------- utilities

    @staticmethod
    def snippet(text, limit=160):
        """Bounded, whitespace-collapsed excerpt for evidence storage.

        V12 stores short snippets rather than raw content blobs.
        """
        if not text:
            return ''
        collapsed = re.sub(r'\s+', ' ', str(text)).strip()
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[:limit - 3].rstrip() + '...'

    @staticmethod
    def clear_caches():
        """Test/maintenance hook - drops the memoization caches."""
        _tokens_cached.cache_clear()
        _normalize_cached.cache_clear()
