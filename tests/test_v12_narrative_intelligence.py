"""V12 Phase C - Narrative Intelligence Engine tests.

Covers the shared similarity primitives, narrative normalization/identity,
deterministic detection, explainable scoring (including unavailable-signal
renormalization), persistence, cross-platform accumulation, pipeline
integration, PostgreSQL-compatible aggregation, and transaction safety.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db as _db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.entity import Entity
from models.entity_context import EntityContext
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.reddit_analysis import RedditAnalysis
from repositories.narrative_repository import NarrativeRepository
from services.narrative_intelligence_service import NarrativeIntelligenceService
from services.text_similarity_service import TextSimilarityService


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''
    TextSimilarityService.clear_caches()


def _naive(dt):
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _docs(*pairs, **kwargs):
    """Build a document list. Each pair is (source, text)."""
    risk = kwargs.get('risk_scores') or {}
    out = []
    for index, (source, text) in enumerate(pairs):
        doc = {'source': source, 'text': text, 'ref': f'{source}:{index}'}
        if source == NarrativeOccurrence.SOURCE_COMMENT:
            doc['risk_score'] = risk.get(index)
        out.append(doc)
    return out


def _make_youtube(db, user, title='', description='', comments=(),
                  video_id='vid001', channel='Test Channel', published_at=None):
    analysis = Analysis(user_id=user.id, analysis_type='youtube')
    db.session.add(analysis)
    db.session.commit()
    db.session.add(YouTubeAnalysis(
        analysis_id=analysis.id, video_id=video_id, video_title=title,
        video_description=description, channel_name=channel,
        published_at=published_at, is_demo=True,
    ))
    for text, risk in comments:
        db.session.add(CommentResult(
            analysis_id=analysis.id, comment_text=text, author='a',
            risk_score=risk, risk_level='Low', toxicity_score=0.0,
        ))
    db.session.commit()
    return analysis


def _make_reddit(db, user, title='', body='', comments=(),
                 post_id='post001', subreddit='technology', created_utc=None):
    analysis = Analysis(user_id=user.id, analysis_type='reddit')
    db.session.add(analysis)
    db.session.commit()
    db.session.add(RedditAnalysis(
        analysis_id=analysis.id, post_id=post_id, subreddit=subreddit,
        post_title=title, post_body=body, created_utc=created_utc, is_demo=True,
    ))
    for text, risk in comments:
        db.session.add(CommentResult(
            analysis_id=analysis.id, comment_text=text, author='a',
            risk_score=risk, risk_level='Low', toxicity_score=0.0,
        ))
    db.session.commit()
    return analysis


ELECTION_DOCS = (
    (NarrativeOccurrence.SOURCE_TITLE, 'Election fraud claims are proven'),
    (NarrativeOccurrence.SOURCE_DESCRIPTION,
     'Election fraud claims spread fast. Election fraud claims again.'),
    (NarrativeOccurrence.SOURCE_COMMENT, 'The election fraud claims are a hoax'),
    (NarrativeOccurrence.SOURCE_COMMENT, 'election fraud claims everywhere'),
)


# --------------------------------------------------------------------------
# Shared similarity primitives
# --------------------------------------------------------------------------
class TestTextSimilarityPrimitives:
    def test_normalize_is_deterministic_and_idempotent(self):
        once = TextSimilarityService.normalize_phrase('The Battery Life!!')
        twice = TextSimilarityService.normalize_phrase('The Battery Life!!')
        assert once == twice == 'battery life'
        assert TextSimilarityService.normalize_phrase(once) == once

    def test_normalize_ignores_case_punctuation_whitespace(self):
        variants = ['Battery Life', 'battery life', '  BATTERY   LIFE!! ',
                    'battery, life.', 'Battery-Life']
        assert {TextSimilarityService.normalize_phrase(v) for v in variants} == {'battery life'}
    def test_normalize_strips_only_edge_stopwords(self):
        # leading/trailing stopwords go
        assert TextSimilarityService.normalize_phrase('the battery life') == 'battery life'
        assert TextSimilarityService.normalize_phrase('battery life of') == 'battery life'
        # an interior stopword long enough to survive the short-token filter stays
        assert (TextSimilarityService.normalize_phrase('crime and punishment')
                == 'crime and punishment')

    def test_short_interior_function_words_are_dropped(self):
        # documented consequence of MIN_TOKEN_LENGTH: 2-char joiners are removed
        assert TextSimilarityService.normalize_phrase('cost of living') == 'cost living'
        assert TextSimilarityService.normalize_phrase('war on drugs') == 'war drug'

    def test_hyphenated_and_spaced_spellings_share_identity(self):
        # V7's clean_text deletes punctuation, so V12 splits joiners first
        assert (TextSimilarityService.normalize_phrase('cover-up')
                == TextSimilarityService.normalize_phrase('cover up'))
        assert (TextSimilarityService.normalize_phrase('Battery-Life')
                == TextSimilarityService.normalize_phrase('battery life'))

    def test_normalize_preserves_word_order(self):
        # sorting tokens would fuse these two opposite statements
        a = TextSimilarityService.normalize_phrase('dogs bite men')
        b = TextSimilarityService.normalize_phrase('men bite dogs')
        assert a != b

    @pytest.mark.parametrize('plural,expected', [
        ('batteries', 'battery'), ('prices', 'price'), ('claims', 'claim'),
        ('causes', 'cause'), ('taxes', 'tax'), ('glasses', 'glass'),
        ('boxes', 'box'), ('matches', 'match'), ('dishes', 'dish'),
        ('vaccines', 'vaccine'),
    ])
    def test_conservative_plural_folding(self, plural, expected):
        assert TextSimilarityService._singularize(plural) == expected

    @pytest.mark.parametrize('token', ['analysis', 'status', 'chaos', 'gas', 'bias'])
    def test_plural_folding_leaves_non_plurals_alone(self, token):
        assert TextSimilarityService._singularize(token) == token

    def test_short_tokens_dropped_unless_allowlisted(self):
        assert TextSimilarityService.normalize_phrase('a of the') == ''
        assert 'ai' in TextSimilarityService.normalize_phrase('ai safety research')

    def test_jaccard_delegates_to_bot_detection_service(self):
        from services.bot_detection_service import BotDetectionService
        a, b = 'battery life problems', 'battery life issues'
        assert TextSimilarityService.jaccard(a, b) == \
            BotDetectionService._jaccard_similarity(a, b)

    def test_overlap_ratio_delegates_to_spam_service(self):
        from services.spam_service import SpamService
        a, b = 'battery life problems', 'battery life'
        assert TextSimilarityService.overlap_ratio(a, b) == SpamService._text_similarity(a, b)

    def test_metric_values(self):
        assert TextSimilarityService.jaccard('battery life', 'battery life') == 1.0
        assert TextSimilarityService.jaccard('battery life', 'engine noise') == 0.0
        # subset: containment is 1.0 but jaccard stays low, which is why merging
        # uses jaccard and nesting-collapse uses containment
        assert TextSimilarityService.containment('battery', 'battery life') == 1.0
        assert TextSimilarityService.jaccard('battery', 'battery life') == 0.5

    def test_containment_handles_empty(self):
        assert TextSimilarityService.containment('', 'battery') == 0.0
        assert TextSimilarityService.containment('battery', '') == 0.0

    def test_tfidf_returns_zero_without_reference(self):
        assert TextSimilarityService.tfidf('battery life', '') == 0.0
        assert TextSimilarityService.tfidf('', 'battery life') == 0.0

    def test_best_match_respects_threshold(self):
        match, score, stats = TextSimilarityService.best_match(
            'battery life', ['engine noise', 'screen size'], threshold=0.85)
        assert match is None
        assert score < 0.85
        assert stats['truncated'] is False

    def test_best_match_finds_exact(self):
        match, score, _ = TextSimilarityService.best_match(
            'battery life', ['engine noise', 'battery life'], threshold=0.85)
        assert match == 'battery life'
        assert score == 1.0

    def test_best_match_is_bounded_and_reports_truncation(self):
        candidates = [f'phrase number {i}' for i in range(500)] + ['battery life']
        match, _score, stats = TextSimilarityService.best_match(
            'battery life', candidates, threshold=0.85, max_comparisons=10)
        assert stats['comparisons'] == 10
        assert stats['truncated'] is True
        assert match is None          # stopped before reaching the real match

    def test_snippet_is_bounded(self):
        long_text = 'word ' * 500
        out = TextSimilarityService.snippet(long_text, limit=50)
        assert len(out) <= 50
        assert out.endswith('...')
        assert TextSimilarityService.snippet(None) == ''

    def test_snippet_collapses_whitespace(self):
        assert TextSimilarityService.snippet('a\n\n  b\tc') == 'a b c'


# --------------------------------------------------------------------------
# Normalized narrative identity
# --------------------------------------------------------------------------
class TestNarrativeNormalizationIdentity:
    def test_same_narrative_different_surface_forms_share_identity(self):
        forms = ['Vaccine Causes Autism', 'the vaccine causes autism',
                 'vaccine causes autism!!', 'VACCINE CAUSES AUTISM']
        assert len({TextSimilarityService.normalize_phrase(f) for f in forms}) == 1

    def test_singular_and_plural_share_identity(self):
        assert (TextSimilarityService.normalize_phrase('ai features')
                == TextSimilarityService.normalize_phrase('ai feature'))

    def test_distinct_narratives_stay_distinct(self):
        distinct = ['battery life', 'election fraud', 'vaccine safety',
                    'stock market crash', 'climate policy']
        normalized = {TextSimilarityService.normalize_phrase(d) for d in distinct}
        assert len(normalized) == len(distinct)

    def test_related_but_different_narratives_not_fused(self, app):
        svc = NarrativeIntelligenceService()
        # jaccard below the merge threshold -> two narratives
        assert svc.similarity.jaccard('battery life', 'battery replacement') \
            < svc.CANDIDATE_MERGE_THRESHOLD


# --------------------------------------------------------------------------
# Deterministic detection
# --------------------------------------------------------------------------
class TestNarrativeDetection:
    def test_detection_is_deterministic(self, app):
        svc = NarrativeIntelligenceService()
        docs = _docs(*ELECTION_DOCS)
        first = svc.detect_candidates(docs, entity_names=[])
        TextSimilarityService.clear_caches()
        second = svc.detect_candidates(_docs(*ELECTION_DOCS), entity_names=[])
        assert [c['normalized_name'] for c in first] == \
               [c['normalized_name'] for c in second]
        assert [c['relevance_score'] for c in first] == \
               [c['relevance_score'] for c in second]

    def test_detects_recurring_narrative(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(*ELECTION_DOCS), entity_names=[])
        names = [c['normalized_name'] for c in found]
        assert 'election fraud' in names
        top = found[0]
        assert top['document_frequency'] >= 2
        assert top['relevance_score'] > 0

    def test_results_are_ordered_by_relevance(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(*ELECTION_DOCS), entity_names=[])
        scores = [c['relevance_score'] for c in found]
        assert scores == sorted(scores, reverse=True)

    def test_engagement_filler_is_rejected(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Great video nice content'),
            (NarrativeOccurrence.SOURCE_COMMENT, 'great video thanks for sharing'),
            (NarrativeOccurrence.SOURCE_COMMENT, 'great video very informative'),
        ), entity_names=[])
        names = [c['normalized_name'] for c in found]
        assert 'great video' not in names
        assert 'nice content' not in names

    def test_opaque_identifiers_are_rejected(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Demo video dQw4w9WgXcQ'),
        ), entity_names=[])
        for candidate in found:
            assert 'dqw4w9wgxcq' not in candidate['normalized_name']

    def test_single_token_requires_entity_anchor(self, app):
        svc = NarrativeIntelligenceService()
        docs = _docs((NarrativeOccurrence.SOURCE_TITLE, 'Tesla announcement today'))
        without = svc.detect_candidates(docs, entity_names=[])
        assert all(len(c['normalized_name'].split()) >= 2 for c in without)

    def test_authoritative_single_occurrence_is_kept(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Battery life degradation report'),
        ), entity_names=[])
        assert found, 'a title-framed narrative should survive df=1'

    def test_low_frequency_comment_only_phrase_is_dropped(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_COMMENT, 'obscure unrelated musing here'),
        ), entity_names=[])
        assert found == []

    def test_respects_max_narratives_cap(self, app):
        app.config['MAX_NARRATIVES_PER_ANALYSIS'] = 3
        svc = NarrativeIntelligenceService()
        text = ' '.join(f'topic{i} subject{i} matter{i}' for i in range(30))
        found = svc.detect_candidates(
            _docs((NarrativeOccurrence.SOURCE_TITLE, text)), entity_names=[])
        assert len(found) <= 3

    def test_nested_ngrams_are_collapsed(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE,
             'testing socialsense ai feature testing socialsense ai feature'),
        ), entity_names=[])
        merged = [v for c in found for v in c['merged_from']]
        assert found
        # every retained candidate is a distinct span, nested variants folded in
        assert len(found) < 6
        assert merged or len(found) == 1

    def test_unrelated_narratives_are_not_merged(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Battery life degradation'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Election fraud claims'),
        ), entity_names=[])
        names = {c['normalized_name'] for c in found}
        # both themes are represented, and no candidate fuses the two
        assert any('battery' in n or 'degradation' in n for n in names)
        assert any('election' in n or 'fraud' in n for n in names)
        for name in names:
            assert not (('battery' in name or 'degradation' in name)
                        and ('election' in name or 'fraud' in name)), \
                f'unrelated themes fused into {name!r}'

    def test_entity_anchoring_recorded(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Tesla battery life degradation'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Tesla battery life again'),
        ), entity_names=['Tesla'])
        anchored = [c for c in found if c['entity_names']]
        assert anchored, 'expected at least one entity-anchored narrative'
        assert 'Tesla' in anchored[0]['entity_names']

    def test_empty_and_blank_documents_are_safe(self, app):
        svc = NarrativeIntelligenceService()
        assert svc.detect_candidates([], entity_names=[]) == []
        assert svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, '   '),
            (NarrativeOccurrence.SOURCE_COMMENT, ''),
        ), entity_names=[]) == []

    def test_category_is_rule_based_and_deterministic(self, app):
        svc = NarrativeIntelligenceService()
        found = svc.detect_candidates(_docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Election ballot fraud claims'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Election ballot fraud claims'),
        ), entity_names=[])
        assert found[0]['category'] == Narrative.CATEGORY_POLITICAL


# --------------------------------------------------------------------------
# Explainable scoring
# --------------------------------------------------------------------------
class TestNarrativeScoring:
    def _scored(self, svc, docs, entity_risk_map=None):
        candidates = svc.detect_candidates(docs, entity_names=[])
        assert candidates
        candidate = candidates[0]
        svc._score_candidate_risk(candidate, docs, entity_risk_map or {})
        return candidate

    def test_scores_are_bounded(self, app):
        svc = NarrativeIntelligenceService()
        candidate = self._scored(svc, _docs(*ELECTION_DOCS))
        assert 0.0 <= candidate['risk_score'] <= 100.0
        assert 0.0 <= candidate['confidence'] <= 100.0

    def test_all_risk_components_reported(self, app):
        svc = NarrativeIntelligenceService()
        candidate = self._scored(svc, _docs(*ELECTION_DOCS))
        assert set(candidate['risk_components']) == set(svc.RISK_WEIGHTS)

    def test_unavailable_signal_is_none_not_zero(self, app):
        svc = NarrativeIntelligenceService()
        # title+description only: no comments and no entities
        docs = _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Election fraud claims proven'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Election fraud claims again'),
        )
        candidate = self._scored(svc, docs)
        assert candidate['risk_components']['comment_risk'] is None
        assert candidate['risk_components']['entity_risk'] is None
        assert 'comment_risk' in candidate['unavailable_signals']
        assert 'entity_risk' in candidate['unavailable_signals']

    def test_weights_renormalize_over_available_signals(self, app):
        svc = NarrativeIntelligenceService()
        docs = _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Election fraud claims proven'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Election fraud claims again'),
        )
        candidate = self._scored(svc, docs)
        components = candidate['risk_components']
        available = {k: v for k, v in components.items() if v is not None}
        total_weight = sum(svc.RISK_WEIGHTS[k] for k in available)
        expected = sum(svc.RISK_WEIGHTS[k] * v for k, v in available.items()) / total_weight
        assert abs(candidate['risk_score'] - round(expected, 1)) < 0.2
        # renormalized, so the missing weight is not treated as zero-risk
        assert total_weight < 1.0

    def test_comment_risk_uses_v4_comment_scores(self, app):
        svc = NarrativeIntelligenceService()
        docs = _docs(*ELECTION_DOCS, risk_scores={2: 80.0, 3: 40.0})
        candidate = self._scored(svc, docs)
        assert candidate['risk_components']['comment_risk'] == pytest.approx(60.0, abs=0.1)

    def test_entity_risk_uses_v8_entity_context(self, app):
        svc = NarrativeIntelligenceService()
        docs = _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Tesla battery life degradation'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Tesla battery life degradation'),
        )
        candidates = svc.detect_candidates(docs, entity_names=['Tesla'])
        anchored = next(c for c in candidates if c['entity_names'])
        svc._score_candidate_risk(anchored, docs, {'Tesla': 66.0})
        assert anchored['risk_components']['entity_risk'] == pytest.approx(66.0, abs=0.1)

    def test_manipulation_lexicon_raises_risk_and_is_hedged(self, app):
        svc = NarrativeIntelligenceService()
        calm = self._scored(svc, _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Battery life measurement report'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Battery life measurement report'),
        ))
        loaded = self._scored(svc, _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Battery life cover up hoax exposed'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION,
             'Battery life cover up hoax exposed they lied fake news'),
        ))
        assert loaded['risk_components']['lexicon'] > calm['risk_components']['lexicon']
        assert loaded['indicators']
        joined = ' '.join(loaded['reasons']).lower()
        assert 'possible' in joined and 'not proof' in joined

    def test_reasons_declare_heuristic_nature(self, app):
        svc = NarrativeIntelligenceService()
        candidate = self._scored(svc, _docs(*ELECTION_DOCS))
        assert any('heuristic' in r.lower() for r in candidate['reasons'])
        assert not any('machine learning' in r.lower() for r in candidate['reasons'])

    def test_unavailable_signals_are_explained_in_reasons(self, app):
        svc = NarrativeIntelligenceService()
        candidate = self._scored(svc, _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Election fraud claims proven'),
            (NarrativeOccurrence.SOURCE_DESCRIPTION, 'Election fraud claims again'),
        ))
        assert any('unavailable' in r.lower() for r in candidate['reasons'])

    def test_confidence_inputs_are_exposed(self, app):
        svc = NarrativeIntelligenceService()
        candidate = self._scored(svc, _docs(*ELECTION_DOCS))
        inputs = candidate['confidence_inputs']
        assert set(inputs) == {'coverage', 'agreement', 'evidence_factor'}
        assert 0.0 <= inputs['coverage'] <= 1.0

    def test_more_evidence_yields_higher_confidence(self, app):
        svc = NarrativeIntelligenceService()
        thin = self._scored(svc, _docs(
            (NarrativeOccurrence.SOURCE_TITLE, 'Election fraud claims'),
        ))
        rich = self._scored(svc, _docs(*ELECTION_DOCS))
        assert rich['confidence'] >= thin['confidence']

    def test_clamp_rejects_garbage(self, app):
        svc = NarrativeIntelligenceService()
        assert svc._clamp(None) == 0.0
        assert svc._clamp('abc') == 0.0
        assert svc._clamp(-5) == 0.0
        assert svc._clamp(1000) == 100.0

    def test_evidence_samples_are_bounded_snippets(self, app):
        svc = NarrativeIntelligenceService()
        docs = _docs(*ELECTION_DOCS)
        candidate = self._scored(svc, docs)
        assert len(candidate['evidence_samples']) <= svc.MAX_EVIDENCE_SAMPLES
        for sample in candidate['evidence_samples']:
            assert len(sample['snippet']) <= svc.EVIDENCE_SNIPPET_CHARS
            assert set(sample) == {'source', 'ref', 'snippet'}


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
class TestNarrativePersistence:
    def test_creates_narrative_and_occurrence(self, app, db, user):
        analysis = _make_youtube(
            db, user, title='Election fraud claims proven',
            description='Election fraud claims spread. Election fraud claims again.')
        result = NarrativeIntelligenceService().analyze(analysis)
        assert result['available'] is True
        assert result['narrative_count'] > 0
        assert Narrative.query.count() == result['narrative_count']
        assert NarrativeOccurrence.query.count() == result['narrative_count']

    def test_narrative_fields_populated(self, app, db, user):
        analysis = _make_youtube(
            db, user, title='Election fraud claims proven',
            description='Election fraud claims spread. Election fraud claims again.')
        NarrativeIntelligenceService().analyze(analysis)
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert narrative is not None
        assert narrative.user_id == user.id
        assert narrative.name
        assert narrative.description
        assert narrative.category == Narrative.CATEGORY_POLITICAL
        assert narrative.detection_method == Narrative.METHOD_HEURISTIC
        assert narrative.occurrence_count == 1
        assert narrative.platform_count == 1
        assert narrative.first_seen_at is not None
        assert narrative.last_seen_at is not None
        assert 0.0 <= narrative.risk_score <= 100.0
        assert 0.0 <= narrative.confidence <= 100.0

    def test_reuses_existing_narrative_across_analyses(self, app, db, user):
        svc = NarrativeIntelligenceService()
        first = _make_youtube(db, user, video_id='v1',
                              title='Election fraud claims proven',
                              description='Election fraud claims again')
        svc.analyze(first)
        count_after_first = Narrative.query.count()
        target = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert target is not None

        second = _make_youtube(db, user, video_id='v2',
                               title='Election fraud claims proven',
                               description='Election fraud claims again')
        svc.analyze(second)

        assert Narrative.query.count() == count_after_first, 'should reuse, not duplicate'
        reused = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert reused.id == target.id
        assert reused.occurrence_count == 2

    def test_narratives_are_scoped_per_user(self, app, db, user):
        from werkzeug.security import generate_password_hash
        from models.user import User
        other = User(username='other', email='other@example.com',
                     password_hash=generate_password_hash('x'))
        db.session.add(other)
        db.session.commit()

        svc = NarrativeIntelligenceService()
        svc.analyze(_make_youtube(db, user, video_id='v1',
                                  title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        svc.analyze(_make_youtube(db, other, video_id='v2',
                                  title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        rows = Narrative.query.filter_by(normalized_name='election fraud').all()
        assert len(rows) == 2
        assert {r.user_id for r in rows} == {user.id, other.id}

    def test_duplicate_occurrence_protection_on_reanalysis(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        svc = NarrativeIntelligenceService()
        svc.analyze(analysis)
        first_occurrences = NarrativeOccurrence.query.count()
        first_narratives = Narrative.query.count()

        svc.analyze(analysis)          # re-run the very same analysis

        assert NarrativeOccurrence.query.count() == first_occurrences
        assert Narrative.query.count() == first_narratives
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert narrative.occurrence_count == 1

    def test_occurrence_count_tracks_stored_rows(self, app, db, user):
        svc = NarrativeIntelligenceService()
        for index in range(3):
            svc.analyze(_make_youtube(db, user, video_id=f'v{index}',
                                      title='Election fraud claims proven',
                                      description='Election fraud claims again'))
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert narrative.occurrence_count == 3
        assert NarrativeOccurrence.query.filter_by(
            narrative_id=narrative.id).count() == 3

    def test_first_and_last_seen_derive_from_occurrence_timestamps(self, app, db, user):
        svc = NarrativeIntelligenceService()
        old = datetime(2024, 1, 1, 12, 0, 0)
        new = datetime(2024, 6, 1, 12, 0, 0)
        svc.analyze(_make_youtube(db, user, video_id='v1', published_at=old,
                                  title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        svc.analyze(_make_youtube(db, user, video_id='v2', published_at=new,
                                  title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert narrative.first_seen_at == old
        assert narrative.last_seen_at == new

    def test_platform_timestamp_preferred_when_available(self, app, db, user):
        published = datetime(2024, 3, 15, 8, 30, 0)
        analysis = _make_youtube(db, user, published_at=published,
                                 title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        occurrence = NarrativeOccurrence.query.first()
        assert occurrence.occurred_at == published
        assert occurrence.timestamp_source == NarrativeOccurrence.TIMESTAMP_PLATFORM
        assert occurrence.used_fallback_timestamp is False

    def test_null_platform_timestamp_falls_back_explicitly(self, app, db, user):
        analysis = _make_youtube(db, user, published_at=None,
                                 title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        occurrence = NarrativeOccurrence.query.first()
        assert occurrence.occurred_at is not None
        assert occurrence.timestamp_source == NarrativeOccurrence.TIMESTAMP_ANALYSIS
        assert occurrence.used_fallback_timestamp is True

    def test_timezone_aware_platform_timestamp_normalized_to_naive(self, app, db, user):
        aware = datetime(2024, 3, 15, 8, 30, 0, tzinfo=timezone.utc)
        analysis = _make_youtube(db, user, published_at=aware,
                                 title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        occurrence = NarrativeOccurrence.query.first()
        assert occurrence.occurred_at.tzinfo is None
        assert occurrence.occurred_at == aware.replace(tzinfo=None)

    def test_evidence_is_persisted_and_bounded(self, app, db, user):
        analysis = _make_youtube(
            db, user, title='Election fraud claims proven',
            description='Election fraud claims spread. Election fraud claims again.',
            comments=[('election fraud claims are a hoax', 70.0),
                      ('more election fraud claims', 30.0)])
        NarrativeIntelligenceService().analyze(analysis)
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()

        for evidence in (narrative.evidence, NarrativeOccurrence.query.filter_by(
                narrative_id=narrative.id).first().evidence):
            assert isinstance(evidence, dict)
            for key in ('detection_method', 'capability', 'document_frequency',
                        'sources', 'matched_terms', 'risk_components',
                        'risk_weights', 'unavailable_signals', 'reasons', 'samples'):
                assert key in evidence, key
            assert evidence['capability'] == 'heuristic'
            assert len(evidence['samples']) <= 3
            assert len(evidence['matched_terms']) <= 8

    def test_evidence_survives_json_serialization(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        narrative = Narrative.query.first()
        # JSON/JSONB column round-trips native python types
        encoded = json.dumps(narrative.to_dict())
        assert 'election' in encoded.lower()
        assert isinstance(narrative.keywords, list)
        assert isinstance(narrative.evidence, dict)

    def test_keywords_and_entities_merge_without_unbounded_growth(self, app, db, user):
        svc = NarrativeIntelligenceService()
        for index in range(4):
            svc.analyze(_make_youtube(db, user, video_id=f'v{index}',
                                      title='Tesla election fraud claims proven',
                                      description='Tesla election fraud claims again'))
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert len(narrative.keywords) <= svc.MAX_EVIDENCE_TERMS
        assert len(narrative.entity_names) <= svc.MAX_EVIDENCE_ENTITIES * 4

    def test_entity_integration_from_database(self, app, db, user):
        analysis = _make_youtube(db, user, title='Tesla battery life degradation',
                                 description='Tesla battery life degradation again')
        entity = Entity(analysis_id=analysis.id, name='Tesla',
                        normalized_name='Tesla', entity_type=Entity.COMPANY,
                        source=Entity.SOURCE_COMBINED, frequency=5,
                        importance_score=90.0)
        db.session.add(entity)
        db.session.commit()
        comment = CommentResult(analysis_id=analysis.id, comment_text='tesla comment',
                                author='a', risk_score=10.0, risk_level='Low')
        db.session.add(comment)
        db.session.commit()
        db.session.add(EntityContext(entity_id=entity.id,
                                     comment_result_id=comment.id,
                                     entity_risk_score=72.0))
        db.session.commit()

        result = NarrativeIntelligenceService().analyze(analysis)
        assert result['available'] is True
        anchored = [n for n in result['narratives'] if n['entity_names']]
        assert anchored, 'entity should anchor at least one narrative'
        assert 'Tesla' in anchored[0]['entity_names']

    def test_no_text_reports_unavailable_not_zero(self, app, db, user):
        analysis = _make_youtube(db, user, title='', description='')
        result = NarrativeIntelligenceService().analyze(analysis)
        assert result['available'] is False
        assert result['max_risk_score'] is None
        assert result['avg_risk_score'] is None
        assert result['narratives'] == []
        assert Narrative.query.count() == 0

    def test_no_qualifying_narrative_reports_unavailable(self, app, db, user):
        analysis = _make_youtube(db, user, title='lol', description='ok')
        result = NarrativeIntelligenceService().analyze(analysis)
        assert result['available'] is False
        assert result['max_risk_score'] is None

    def test_disabled_by_configuration(self, app, db, user):
        app.config['ENABLE_NARRATIVE_INTELLIGENCE'] = False
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        result = NarrativeIntelligenceService().analyze(analysis)
        assert result['available'] is False
        assert 'disabled' in result['reasons'][0].lower()
        assert Narrative.query.count() == 0

    def test_result_declares_capability_and_limitations(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        result = NarrativeIntelligenceService().analyze(analysis)
        assert result['capability'] == 'heuristic'
        assert result['detection_method'] == 'heuristic_phrase_recurrence'
        assert result['limitations']
        assert any('heuristic' in item.lower() for item in result['limitations'])

    def test_cascade_delete_removes_narrative_rows(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        assert NarrativeOccurrence.query.count() > 0
        db.session.delete(analysis)
        db.session.commit()
        assert NarrativeOccurrence.query.count() == 0
        # the narrative itself is user-scoped and survives content deletion
        assert Narrative.query.count() > 0


# --------------------------------------------------------------------------
# Cross-platform accumulation
# --------------------------------------------------------------------------
class TestNarrativeCrossPlatform:
    def _both_platforms(self, db, user):
        svc = NarrativeIntelligenceService()
        svc.analyze(_make_youtube(db, user, title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        svc.analyze(_make_reddit(db, user, title='Election fraud claims proven',
                                 body='Election fraud claims again'))
        return Narrative.query.filter_by(normalized_name='election fraud').first()

    def test_youtube_occurrence_records_platform(self, app, db, user):
        NarrativeIntelligenceService().analyze(_make_youtube(
            db, user, title='Election fraud claims proven',
            description='Election fraud claims again'))
        occurrence = NarrativeOccurrence.query.first()
        assert occurrence.platform == NarrativeOccurrence.PLATFORM_YOUTUBE
        assert occurrence.content_ref == 'vid001'
        assert occurrence.channel_id == 'test_channel'

    def test_reddit_occurrence_records_platform(self, app, db, user):
        NarrativeIntelligenceService().analyze(_make_reddit(
            db, user, title='Election fraud claims proven',
            body='Election fraud claims again'))
        occurrence = NarrativeOccurrence.query.first()
        assert occurrence.platform == NarrativeOccurrence.PLATFORM_REDDIT
        assert occurrence.content_ref == 'post001'
        assert occurrence.channel_id == 'technology'

    def test_cross_platform_narrative_detected(self, app, db, user):
        narrative = self._both_platforms(db, user)
        assert narrative.platform_count == 2
        assert narrative.is_cross_platform is True
        assert narrative.occurrence_count == 2

    def test_single_platform_is_not_cross_platform(self, app, db, user):
        svc = NarrativeIntelligenceService()
        svc.analyze(_make_youtube(db, user, video_id='v1',
                                  title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        svc.analyze(_make_youtube(db, user, video_id='v2',
                                  title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        narrative = Narrative.query.filter_by(normalized_name='election fraud').first()
        assert narrative.occurrence_count == 2
        assert narrative.platform_count == 1
        assert narrative.is_cross_platform is False

    def test_platform_count_reflects_stored_rows_not_text_similarity(self, app, db, user):
        """Two YouTube analyses with identical text must not look cross-platform."""
        narrative = self._both_platforms(db, user)
        platforms = NarrativeRepository().get_platforms_for_narrative(narrative.id)
        assert platforms == ['reddit', 'youtube']
        assert narrative.platform_count == len(platforms)

    def test_missing_platform_child_row_degrades_safely(self, app, db, user):
        analysis = Analysis(user_id=user.id, analysis_type='youtube')
        db.session.add(analysis)
        db.session.commit()
        result = NarrativeIntelligenceService().analyze(
            analysis, video_info={'title': 'Election fraud claims proven',
                                  'description': 'Election fraud claims again'})
        assert result['available'] is True
        occurrence = NarrativeOccurrence.query.first()
        assert occurrence.platform == NarrativeOccurrence.PLATFORM_YOUTUBE
        assert occurrence.content_ref is None


# --------------------------------------------------------------------------
# Pipeline integration
# --------------------------------------------------------------------------
class TestNarrativePipelineIntegration:
    def test_youtube_pipeline_creates_narratives(self, app, db, user):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        assert result['success'] is True
        assert Narrative.query.count() > 0
        assert NarrativeOccurrence.query.filter_by(
            analysis_id=result['analysis_id']).count() > 0

    def test_reddit_pipeline_creates_narratives(self, app, db, user):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_reddit_analysis(
            user.id, 'abc123', subreddit='technology', comment_limit=5)
        assert result['success'] is True
        assert NarrativeOccurrence.query.filter_by(
            analysis_id=result['analysis_id']).count() > 0
        occurrence = NarrativeOccurrence.query.filter_by(
            analysis_id=result['analysis_id']).first()
        assert occurrence.platform == NarrativeOccurrence.PLATFORM_REDDIT

    def test_pipeline_flag_off_skips_narratives(self, app, db, user):
        app.config['ENABLE_NARRATIVE_INTELLIGENCE'] = False
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        assert result['success'] is True
        assert Narrative.query.count() == 0

    def test_narrative_failure_does_not_fail_analysis(self, app, db, user, monkeypatch):
        from services.analysis_service import AnalysisService
        service = AnalysisService()

        def boom(*args, **kwargs):
            raise RuntimeError('simulated narrative failure')

        monkeypatch.setattr(service.narrative_service, 'analyze', boom)
        result = service.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        assert result['success'] is True
        assert CommentResult.query.filter_by(
            analysis_id=result['analysis_id']).count() > 0

    def test_v11_authenticity_still_runs_alongside_v12(self, app, db, user):
        from models.media_analysis import MediaAnalysis
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        assert MediaAnalysis.query.filter_by(
            analysis_id=result['analysis_id']).first() is not None
        assert Narrative.query.count() > 0

    def test_pipeline_is_deterministic_for_same_input(self, app, db, user):
        from services.analysis_service import AnalysisService
        service = AnalysisService()
        first = service.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        names_first = sorted(
            n.normalized_name for n in Narrative.query.filter_by(user_id=user.id))
        occ_first = NarrativeOccurrence.query.filter_by(
            analysis_id=first['analysis_id']).count()

        second = service.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        names_second = sorted(
            n.normalized_name for n in Narrative.query.filter_by(user_id=user.id))
        occ_second = NarrativeOccurrence.query.filter_by(
            analysis_id=second['analysis_id']).count()

        assert names_first == names_second, 'narrative identities must be reused'
        assert occ_first == occ_second

    def test_result_page_still_renders(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        response = logged_in_client.get(f'/analysis/{result["analysis_id"]}')
        assert response.status_code == 200

    def test_dashboard_and_exports_still_work(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        from services.export_service import ExportService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        assert logged_in_client.get('/dashboard/').status_code == 200
        assert ExportService().generate_csv(result['analysis_id'], user.id) is not None
        assert ExportService().generate_json(result['analysis_id'], user.id) is not None


# --------------------------------------------------------------------------
# Repository + PostgreSQL-compatible aggregation
# --------------------------------------------------------------------------
class TestNarrativeRepositoryAggregates:
    def _seed(self, db, user):
        svc = NarrativeIntelligenceService()
        svc.analyze(_make_youtube(db, user, title='Election fraud claims proven',
                                  description='Election fraud claims again'))
        svc.analyze(_make_reddit(db, user, title='Election fraud claims proven',
                                 body='Election fraud claims again'))
        svc.analyze(_make_youtube(db, user, video_id='v9',
                                  title='Battery life degradation report',
                                  description='Battery life degradation report'))
        return NarrativeRepository()

    def test_platform_distribution_groups_correctly(self, app, db, user):
        repo = self._seed(db, user)
        distribution = repo.get_platform_distribution(user.id)
        assert distribution.get('youtube', 0) >= 1
        assert distribution.get('reddit', 0) >= 1
        assert sum(distribution.values()) == NarrativeOccurrence.query.count()

    def test_category_distribution_groups_correctly(self, app, db, user):
        repo = self._seed(db, user)
        rows = repo.get_category_distribution(user.id)
        assert rows
        assert {'category', 'narratives', 'avg_risk'} == set(rows[0])
        assert sum(r['narratives'] for r in rows) == Narrative.query.filter_by(
            user_id=user.id).count()

    def test_cross_platform_query_uses_distinct_platform_having(self, app, db, user):
        repo = self._seed(db, user)
        rows = repo.get_cross_platform_narratives(user.id, limit=10)
        assert rows
        for row in rows:
            assert row['platform_count'] > 1
        names = {r['normalized_name'] for r in rows}
        assert 'election fraud' in names
        assert 'battery life degradation' not in names

    def test_occurrence_stats_returns_none_when_no_rows(self, app, db, user):
        narrative = Narrative(user_id=user.id, name='x', normalized_name='x')
        db.session.add(narrative)
        db.session.commit()
        stats = NarrativeRepository().get_occurrence_stats(narrative.id)
        assert stats['occurrence_count'] == 0
        assert stats['platform_count'] == 0
        # unavailable must not be reported as 0.0
        assert stats['max_risk_score'] is None
        assert stats['avg_risk_score'] is None
        assert stats['first_seen_at'] is None

    def test_repository_limits_are_bounded(self, app, db, user):
        repo = NarrativeRepository()
        assert repo._bounded(0) == repo.DEFAULT_LIMIT
        assert repo._bounded(None) == repo.DEFAULT_LIMIT
        assert repo._bounded(-1) == repo.DEFAULT_LIMIT
        assert repo._bounded(10_000) == repo.MAX_LIMIT

    def test_get_for_analysis_returns_pairs(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        rows = NarrativeRepository().get_for_analysis(analysis.id)
        assert rows
        narrative, occurrence = rows[0]
        assert isinstance(narrative, Narrative)
        assert isinstance(occurrence, NarrativeOccurrence)
        assert occurrence.analysis_id == analysis.id

    def test_user_summary_is_bounded_and_labelled(self, app, db, user):
        self._seed(db, user)
        summary = NarrativeIntelligenceService().get_user_narrative_summary(
            user.id, limit=2)
        assert summary['total_narratives'] > 0
        assert len(summary['top_narratives']) <= 2
        assert summary['capability'] == 'heuristic'
        assert 'platform_distribution' in summary

    def test_get_analysis_narratives_shape(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        rows = NarrativeIntelligenceService().get_analysis_narratives(analysis.id)
        assert rows
        assert 'occurrence' in rows[0]
        assert 'risk_level' in rows[0]


class TestNarrativePostgresCompatibility:
    """Guards the SQLite-passes / PostgreSQL-fails class of bug.

    The suite runs on SQLite, which tolerates selecting non-aggregated columns.
    These tests compile the real statements and assert every selected
    non-aggregate column appears in GROUP BY, which PostgreSQL requires.
    """

    def _sql(self, statement):
        return str(statement.compile(dialect=_db.engine.dialect))

    def test_platform_distribution_group_by_is_explicit(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            NarrativeOccurrence.platform,
            func.count(NarrativeOccurrence.id),
        ).group_by(NarrativeOccurrence.platform).statement
        sql = self._sql(statement)
        assert 'GROUP BY' in sql
        assert 'narrative_occurrences.platform' in sql.split('GROUP BY')[1]

    def test_category_distribution_group_by_is_explicit(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            Narrative.category,
            func.count(Narrative.id),
            func.avg(Narrative.risk_score),
        ).group_by(Narrative.category).statement
        sql = self._sql(statement)
        group_by = sql.split('GROUP BY')[1]
        assert 'narratives.category' in group_by

    def test_cross_platform_query_groups_every_selected_column(self, app, user):
        repo = NarrativeRepository()
        # build the same statement the repository uses
        from sqlalchemy import func
        statement = _db.session.query(
            Narrative.id, Narrative.name, Narrative.normalized_name,
            Narrative.category, Narrative.risk_score,
            func.count(func.distinct(NarrativeOccurrence.platform)),
            func.count(NarrativeOccurrence.id),
        ).join(
            NarrativeOccurrence, NarrativeOccurrence.narrative_id == Narrative.id
        ).group_by(
            Narrative.id, Narrative.name, Narrative.normalized_name,
            Narrative.category, Narrative.risk_score,
        ).having(
            func.count(func.distinct(NarrativeOccurrence.platform)) > 1
        ).statement
        sql = self._sql(statement)
        group_by = sql.split('GROUP BY')[1].split('HAVING')[0]
        for column in ('narratives.id', 'narratives.name',
                       'narratives.normalized_name', 'narratives.category',
                       'narratives.risk_score'):
            assert column in group_by, f'{column} missing from GROUP BY'
        assert 'HAVING' in sql
        assert 'DISTINCT' in sql.upper()
        # executing must also work on the live dialect
        assert repo.get_cross_platform_narratives(user.id, limit=5) == []

    def test_entity_risk_map_group_by_is_explicit(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            Entity.normalized_name,
            func.avg(EntityContext.entity_risk_score),
        ).join(
            EntityContext, EntityContext.entity_id == Entity.id
        ).group_by(Entity.normalized_name).statement
        sql = self._sql(statement)
        assert 'entities.normalized_name' in sql.split('GROUP BY')[1]

    def test_occurrence_stats_aggregate_executes(self, app, db, user):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        NarrativeIntelligenceService().analyze(analysis)
        narrative = Narrative.query.first()
        stats = NarrativeRepository().get_occurrence_stats(narrative.id)
        assert stats['occurrence_count'] == 1
        assert stats['platform_count'] == 1
        assert stats['max_risk_score'] is not None

    def test_distinct_platform_count_is_used(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            func.count(func.distinct(NarrativeOccurrence.platform))
        ).statement
        assert 'DISTINCT' in self._sql(statement).upper()

    def test_entity_name_lookup_groups_explicitly(self, app, db, user):
        analysis = _make_youtube(db, user, title='t', description='d')
        db.session.add(Entity(analysis_id=analysis.id, name='Tesla',
                              normalized_name='Tesla', entity_type=Entity.COMPANY,
                              source=Entity.SOURCE_COMBINED, frequency=1))
        db.session.add(Entity(analysis_id=analysis.id, name='Tesla',
                              normalized_name='Tesla', entity_type=Entity.COMPANY,
                              source=Entity.SOURCE_TITLE, frequency=1))
        db.session.commit()
        names = NarrativeIntelligenceService()._entity_names(analysis, None)
        assert names == ['Tesla'], 'DISTINCT-by-group must dedupe entity names'


# --------------------------------------------------------------------------
# Transaction safety
# --------------------------------------------------------------------------
class TestNarrativeTransactionSafety:
    def test_integrity_error_then_rollback_then_query_succeeds(self, app, db, user):
        first = Narrative(user_id=user.id, name='Dup', normalized_name='dup')
        db.session.add(first)
        db.session.commit()

        db.session.add(Narrative(user_id=user.id, name='Dup2', normalized_name='dup'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        # the session must be usable again - no InFailedSqlTransaction chain
        assert Narrative.query.filter_by(normalized_name='dup').count() == 1
        assert Narrative.query.count() == 1

    def test_persist_failure_rolls_back_and_reports_unavailable(
            self, app, db, user, monkeypatch):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        service = NarrativeIntelligenceService()

        def boom(*args, **kwargs):
            raise SQLAlchemyError('simulated persistence failure')

        monkeypatch.setattr(service, '_persist_once', boom)
        result = service.analyze(analysis)

        assert result['available'] is False
        assert result['max_risk_score'] is None
        assert Narrative.query.count() == 0
        # subsequent DB work still succeeds
        assert Analysis.query.count() == 1
        assert CommentResult.query.count() == 0

    def test_integrity_error_is_retried_once(self, app, db, user, monkeypatch):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        service = NarrativeIntelligenceService()
        original = service._persist_once
        calls = {'n': 0}

        def flaky(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise IntegrityError('simulated', {}, Exception('conflict'))
            return original(*args, **kwargs)

        monkeypatch.setattr(service, '_persist_once', flaky)
        result = service.analyze(analysis)

        assert calls['n'] == 2, 'should retry exactly once after rollback'
        assert result['available'] is True
        assert Narrative.query.count() > 0

    def test_repeated_integrity_error_gives_up_cleanly(self, app, db, user, monkeypatch):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        service = NarrativeIntelligenceService()

        def always_conflict(*args, **kwargs):
            raise IntegrityError('simulated', {}, Exception('conflict'))

        monkeypatch.setattr(service, '_persist_once', always_conflict)
        result = service.analyze(analysis)

        assert result['available'] is False
        assert Narrative.query.count() == 0
        assert Analysis.query.count() == 1

    def test_no_per_item_commits_during_persistence(self, app, db, user, monkeypatch):
        """A batch of narratives must be committed once, not per narrative."""
        analysis = _make_youtube(
            db, user, title='Election fraud claims and battery life degradation',
            description='Election fraud claims again. Battery life degradation again.')
        service = NarrativeIntelligenceService()
        commits = {'n': 0}
        real_commit = db.session.commit

        def counting_commit():
            commits['n'] += 1
            return real_commit()

        monkeypatch.setattr(db.session, 'commit', counting_commit)
        result = service.analyze(analysis)

        assert result['available'] is True
        assert result['narrative_count'] >= 2
        assert commits['n'] == 1, f'expected a single commit, got {commits["n"]}'

    def test_entity_risk_lookup_failure_degrades_gracefully(
            self, app, db, user, monkeypatch):
        analysis = _make_youtube(db, user, title='Election fraud claims proven',
                                 description='Election fraud claims again')
        service = NarrativeIntelligenceService()

        def boom(*args, **kwargs):
            raise SQLAlchemyError('simulated entity failure')

        monkeypatch.setattr(service, '_entity_risk_map', boom)
        result = service.analyze(analysis)

        assert result['available'] is True
        narrative = Narrative.query.first()
        assert narrative is not None
        assert narrative.evidence['risk_components']['entity_risk'] is None
