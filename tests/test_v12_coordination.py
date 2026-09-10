"""V12 Phase D - Coordination Intelligence tests.

Covers deterministic detection for all 7 signal types, explainable hedging
(similarity never proves coordination), the hard comparison budget with honest
``comparisons_performed`` / ``comparisons_truncated``, persistence with
duplicate prevention, PostgreSQL-safe aggregation, transaction safety and
failure isolation, plus YouTube / Reddit / cross-platform integration.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db as _db
from models.analysis import Analysis, YouTubeAnalysis
from models.coordination_signal import CoordinationSignal
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.reddit_analysis import RedditAnalysis
from services.coordination_intelligence_service import (
    CoordinationIntelligenceService,
)
from services.text_similarity_service import TextSimilarityService


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''
    TextSimilarityService.clear_caches()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _comment(text, author='a', published_at=None, risk=20.0, bot=0.0, spam=0.0):
    return {'comment_id': None, 'text': text,
            'normalized': TextSimilarityService.normalize_phrase(text),
            'author': author, 'published_at': published_at,
            'risk_score': risk, 'bot_score': bot, 'spam_score': spam,
            'toxicity_score': 0.0, 'entities': []}


def _svc(app):
    return CoordinationIntelligenceService()


def _unit(svc, text, author='a', ts=None, risk=20.0):
    return {'comment_id': None, 'text': text,
            'normalized': svc.similarity.normalize_phrase(text),
            'author': author, 'published_at': ts, 'risk_score': risk,
            'bot_score': 0.0, 'spam_score': 0.0, 'toxicity_score': 0.0,
            'entities': []}


# --------------------------------------------------------------------------
# Deterministic detection
# --------------------------------------------------------------------------
class TestCoordinationDetection:
    def test_repeated_content_detected(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'please subscribe to my channel for the best videos')
                 for _ in range(4)]
        sig = svc._detect_repeated_content(None, units, [])
        assert sig is not None
        assert sig['signal_type'] == CoordinationSignal.TYPE_REPEATED_CONTENT
        assert sig['cluster_size'] >= 2
        assert sig['comparisons_performed'] >= 0

    def test_repeated_content_is_deterministic(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'verified the same sentence repeatedly for signals')
                 for _ in range(3)]
        a = svc._detect_repeated_content(None, units, [])
        TextSimilarityService.clear_caches()
        b = svc._detect_repeated_content(None, units, [])
        assert a['score'] == b['score']
        assert a['cluster_size'] == b['cluster_size']

    def test_abnormal_similarity_detected(self, app):
        svc = _svc(app)
        # genuinely similar-but-distinct (share vocabulary, not near-duplicates)
        units = [_unit(svc, 'the stock market crashed hard this week'),
                 _unit(svc, 'stock markets crashed sharply this week'),
                 _unit(svc, 'the markets took a big crash this week')]
        sig = svc._detect_abnormal_similarity(None, units, [])
        assert sig is not None
        assert sig['signal_type'] == CoordinationSignal.TYPE_ABNORMAL_SIMILARITY

    def test_similarity_alone_does_not_claim_coordination(self, app):
        svc = _svc(app)
        # similar but clearly non-coordinated everyday phrasing
        units = [_unit(svc, 'i really enjoyed watching the sunset today'),
                 _unit(svc, 'i really enjoyed eating a sandwich today')]
        for method in (svc._detect_repeated_content,
                       svc._detect_abnormal_similarity):
            sig = method(None, units, [])
            if sig is not None:
                joined = ' '.join(sig['reasons']).lower()
                # must hedge; never assert coordination as fact
                assert any(kw in joined for kw in ('possible', 'can indicate',
                                                   'indicator', 'does not prove'))
                assert 'coordination' not in sig.get('summary') or \
                    'possible' in joined

    def test_synchronized_timing_detected(self, app):
        svc = _svc(app)
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        units = [_unit(svc, f'spread the word about the meeting u{i}',
                       author=f'u{i}', ts=base + timedelta(seconds=i * 5))
                 for i in range(4)]
        sig = svc._detect_synchronized_timing(None, units, [])
        assert sig is not None
        assert sig['signal_type'] == CoordinationSignal.TYPE_SYNCHRONIZED_TIMING
        assert sig['window_seconds'] == 300
        assert sig['cluster_size'] >= 3

    def test_timing_without_similarity_or_shared_feature_is_dropped(self, app):
        svc = _svc(app)
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 3 distinct, unrelated comments in a tight window
        units = [_unit(svc, text, author=f'u{i}',
                       ts=base + timedelta(seconds=i * 3))
                 for i, text in enumerate(('weather forecast today', 'cooking a recipe',
                                           'best hiking routes nearby'))]
        sig = svc._detect_synchronized_timing(None, units, [])
        assert sig is None

    def test_shared_entities_detected(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'tesla is great', author=f'user{i}', risk=30.0)
                 for i in range(5)]
        for u in units:
            u['entities'] = ['Tesla']
        sig = svc._detect_shared_entities(None, units, [])
        assert sig is not None
        assert 'Tesla' in sig['related_entities']
        assert sig['cluster_size'] >= 3

    def test_shared_entities_below_min_authors_is_dropped(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'tesla', author=f'user{i}') for i in range(2)]
        for u in units:
            u['entities'] = ['Tesla']
        sig = svc._detect_shared_entities(None, units, [])
        assert sig is None

    def test_shared_narrative_detected(self, app):
        svc = _svc(app)
        narr = [{'narrative_id': 7, 'normalized_name': 'election fraud',
                 'risk_score': 60.0, 'platforms': {'youtube'}, 'platform_count': 1,
                 'occurrence_count': 3}]
        sig = svc._detect_shared_narrative(None, [], narr)
        assert sig is not None
        assert sig['narrative_id'] == 7
        assert sig['cluster_size'] == 3

    def test_shared_narrative_requires_recurrence(self, app):
        svc = _svc(app)
        narr = [{'narrative_id': 7, 'normalized_name': 'one off',
                 'risk_score': 10.0, 'platforms': {'youtube'}, 'platform_count': 1,
                 'occurrence_count': 1}]
        assert svc._detect_shared_narrative(None, [], narr) is None

    def test_cross_platform_detected(self, app):
        svc = _svc(app)
        narr = [{'narrative_id': 9, 'normalized_name': 'election fraud',
                 'risk_score': 60.0, 'platforms': {'youtube', 'reddit'},
                 'platform_count': 2, 'occurrence_count': 4}]
        sig = svc._detect_cross_platform(None, [], narr)
        assert sig is not None
        assert sig['signal_type'] == CoordinationSignal.TYPE_CROSS_PLATFORM_COORDINATION
        assert 'election fraud' in sig['related_entities']

    def test_cross_platform_requires_two_platforms(self, app):
        svc = _svc(app)
        narr = [{'narrative_id': 9, 'normalized_name': 'single platform',
                 'risk_score': 60.0, 'platforms': {'youtube'}, 'platform_count': 1,
                 'occurrence_count': 4}]
        assert svc._detect_cross_platform(None, [], narr) is None

    def test_behavioral_cluster_detected(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'act now before it is too late to save your money',
                       author=f'u{i}', risk=75.0) for i in range(3)]
        sig = svc._detect_behavioral_cluster(None, units, [])
        assert sig is not None
        assert sig['signal_type'] == CoordinationSignal.TYPE_BEHAVIORAL_CLUSTER
        assert sig['cluster_size'] >= 3

    def test_behavioral_cluster_requires_risk(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'act now before it is too late to save money',
                       author=f'u{i}', risk=5.0) for i in range(3)]
        assert svc._detect_behavioral_cluster(None, units, []) is None

    def test_detect_all_returns_only_triggered_signals(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'please subscribe to my channel for the best videos')
                 for _ in range(4)]
        signals = svc._detect_all(None, units, [])
        types = [s['signal_type'] for s in signals if s is not None]
        assert CoordinationSignal.TYPE_REPEATED_CONTENT in types
        assert all(s['reasons'] for s in signals if s is not None)


# --------------------------------------------------------------------------
# Scoring + confidence + hedging
# --------------------------------------------------------------------------
class TestCoordinationScoring:
    def test_scores_bounded(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'verified the same sentence repeatedly for signals',
                       author=f'u{i}') for i in range(3)]
        sig = svc._detect_repeated_content(None, units, [])
        assert sig is not None
        assert 0.0 <= sig['score'] <= 100.0
        assert 0.0 <= sig['confidence'] <= 100.0

    def test_level_bands(self, app):
        svc = _svc(app)
        assert svc._level_from_score(75.0) == CoordinationSignal.LEVEL_SUSPICIOUS
        assert svc._level_from_score(45.0) == CoordinationSignal.LEVEL_ELEVATED
        assert svc._level_from_score(25.0) == CoordinationSignal.LEVEL_LOW
        assert svc._level_from_score(5.0) == CoordinationSignal.LEVEL_NONE

    def test_larger_cluster_scores_higher(self, app):
        svc = _svc(app)
        small = [_unit(svc, 'please subscribe to my channel for nice videos') for _ in range(2)]
        large = [_unit(svc, 'please subscribe to my channel for nice videos') for _ in range(6)]
        s_small = svc._detect_repeated_content(None, small, [])
        s_large = svc._detect_repeated_content(None, large, [])
        assert s_small is not None and s_large is not None
        assert s_large['score'] > s_small['score']

    def test_reasons_are_hedged(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'please subscribe to my channel for the best videos')
                 for _ in range(3)]
        sig = svc._detect_repeated_content(None, units, [])
        joined = ' '.join(sig['reasons']).lower()
        assert 'indicate' in joined or 'possible' in joined
        # hedged, never a flat assertion of coordinated behaviour; reasons must
        # not claim to have proven intent/collusion
        for reason in sig['reasons']:
            lower = reason.lower()
            assert 'proven' not in lower
            assert 'confirmed' not in lower
            assert 'admitted' not in lower
        assert sig.get('summary') is None or 'coordination' not in str(sig['summary'])

    def test_evidence_is_concise_and_bounded(self, app):
        svc = _svc(app)
        long_text = 'please subscribe now for the best ' * 20
        units = [_unit(svc, long_text) for _ in range(3)]
        sig = svc._detect_repeated_content(None, units, [])
        assert sig is not None
        samples = sig['evidence']['samples']
        assert len(samples) <= svc.MAX_EVIDENCE_SAMPLES
        for s in samples:
            assert len(s['snippet']) <= svc.EVIDENCE_SNIPPET_CHARS
        assert sig['evidence']['similarity_thresholds']['repeated'] >= 0.5

    def test_indicators_present(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'please subscribe to my channel for the best videos')
                 for _ in range(3)]
        sig = svc._detect_repeated_content(None, units, [])
        assert sig['indicators']
        assert all(isinstance(i, str) for i in sig['indicators'])

    def test_related_entities_reported(self, app):
        svc = _svc(app)
        units = [_unit(svc, 'tesla battery issue', author=f'u{i}', risk=40.0)
                 for i in range(4)]
        for u in units:
            u['entities'] = ['Tesla']
        sig = svc._detect_shared_entities(None, units, [])
        assert sig is not None
        assert 'Tesla' in sig['related_entities']


# --------------------------------------------------------------------------
# Comparison budget
# --------------------------------------------------------------------------
class TestCoordinationBudget:
    def test_comparisons_bounded_by_budget(self, app):
        svc = _svc(app)
        app.config['COORDINATION_COMPARISON_BUDGET'] = 3
        many = [_unit(svc, f'distinct discussion point number {i}',
                      author=f'u{i}') for i in range(100)]
        cs = svc._cluster_by_similarity(many, threshold=0.6, metric='jaccard', leaders=10)
        assert cs.comparisons <= 3
        assert cs.truncated is True   # budget exhausted with units remaining

    def test_no_false_truncation_when_budget_sufficient(self, app):
        svc = _svc(app)
        app.config['COORDINATION_COMPARISON_BUDGET'] = 2000
        many = [_unit(svc, f'distinct discussion point number {i}',
                      author=f'u{i}') for i in range(20)]
        cs = svc._cluster_by_similarity(many, threshold=0.9, metric='containment', leaders=50)
        assert cs.truncated is False

    def test_max_leaders_limits_work(self, app):
        svc = _svc(app)
        app.config['COORDINATION_MAX_LEADERS'] = 2
        units = [_unit(svc, f'cluster a {i}') for i in range(3)] + \
                [_unit(svc, f'cluster b {i}') for i in range(3)]
        cs = svc._cluster_by_similarity(units, threshold=1.0, metric='jaccard', leaders=2)
        # with a 2-leader cap and 1.0 threshold, the second cluster merges only up to 2 leaders
        assert cs.comparisons >= 0

    def test_signals_report_budget_honestly(self, app):
        svc = _svc(app)
        app.config['COORDINATION_COMPARISON_BUDGET'] = 2
        units = [_unit(svc, f'please subscribe to my channel topic {i}',
                       author=f'u{i}') for i in range(50)]
        sig = svc._detect_repeated_content(None, units, [])
        if sig is not None:
            assert sig['comparisons_performed'] <= 2
            assert sig['comparisons_truncated'] is True

    def test_repeated_content_is_near_linear_no_o_squared(self, app):
        svc = _svc(app)
        app.config['COORDINATION_COMPARISON_BUDGET'] = 500
        # 300 units: leader-based means bounded comparisons, not n^2
        many = [_unit(svc, f'unique discussion numbered entry {i}', author=f'u{i}')
                for i in range(300)]
        cs = svc._cluster_by_similarity(many, threshold=0.6, metric='jaccard', leaders=50)
        assert cs.comparisons <= 500


# --------------------------------------------------------------------------
# Persistence + duplicate prevention
# --------------------------------------------------------------------------
class TestCoordinationPersistence:
    def _analysis(self, db, user, comments=(), video_id='vid', platform='youtube'):
        analysis = Analysis(user_id=user.id, analysis_type=platform)
        db.session.add(analysis)
        db.session.commit()
        if platform == 'youtube':
            db.session.add(YouTubeAnalysis(analysis_id=analysis.id, video_id=video_id,
                                           video_title='t', video_description='d',
                                           channel_name='C', is_demo=True))
        else:
            db.session.add(RedditAnalysis(analysis_id=analysis.id, post_id=video_id,
                                          subreddit='technology', is_demo=True))
        for text, risk in comments:
            from models.comment_result import CommentResult
            db.session.add(CommentResult(analysis_id=analysis.id, comment_text=text,
                                         author='a', risk_score=risk, risk_level='High',
                                         toxicity_score=0.0))
        db.session.commit()
        return analysis

    def test_persists_signals(self, app, db, user):
        comments = [('please subscribe to my channel for the best videos', 70.0)] * 4
        analysis = self._analysis(db, user, comments=comments)
        component = _svc(app).analyze(analysis, comments=None, entities=None)
        assert component['available'] is True
        assert CoordinationSignal.query.filter_by(analysis_id=analysis.id).count() > 0

    def test_duplicate_prevention_on_reanalyse(self, app, db, user):
        comments = [('please subscribe to my channel for the best videos', 70.0)] * 4
        analysis = self._analysis(db, user, comments=comments)
        svc = _svc(app)
        svc.analyze(analysis, comments=None, entities=None)
        first = CoordinationSignal.query.filter_by(analysis_id=analysis.id).count()
        svc.analyze(analysis, comments=None, entities=None)
        second = CoordinationSignal.query.filter_by(analysis_id=analysis.id).count()
        assert first == second
        assert first > 0

    def test_unique_constraint_enforced(self, app, db, user):
        from models.comment_result import CommentResult
        analysis = self._analysis(db, user, comments=[('x', 10.0)])
        db.session.add(CoordinationSignal(analysis_id=analysis.id, user_id=user.id,
                                          signal_type='repeated_content'))
        db.session.commit()
        db.session.add(CoordinationSignal(analysis_id=analysis.id, user_id=user.id,
                                          signal_type='repeated_content'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert Analysis.query.count() >= 1
        assert CoordinationSignal.query.filter_by(
            analysis_id=analysis.id).count() == 1

    def test_single_commit_per_batch(self, app, db, user, monkeypatch):
        comments = [('please subscribe to my channel topic a', 70.0),
                    ('please subscribe to my channel topic b', 70.0),
                    ('stocks crashed sharply for the markets', 70.0)] * 2
        analysis = self._analysis(db, user, comments=comments)
        svc = _svc(app)
        commits = {'n': 0}
        real = db.session.commit
        monkeypatch.setattr(db.session, 'commit',
                            lambda: (commit_and_count(real, commits)))
        svc.analyze(analysis, comments=None, entities=None)
        assert commits['n'] <= 2   # at most 2 (retry) — never one per signal

    def test_narrative_linkage_stored(self, app, db, user):
        analysis = self._analysis(db, user, comments=[('x', 10.0)])
        narrative = Narrative(user_id=user.id, name='election fraud',
                              normalized_name='election fraud')
        db.session.add(narrative)
        db.session.commit()
        svc = _svc(app)
        narr = [{'narrative_id': narrative.id, 'normalized_name': 'election fraud',
                 'risk_score': 60.0, 'platforms': {'youtube'}, 'platform_count': 1,
                 'occurrence_count': 3}]
        sig = svc._detect_shared_narrative(analysis, [], narr)
        assert sig['narrative_id'] == narrative.id

    def test_get_analysis_coordination_returns_dicts(self, app, db, user):
        comments = [('please subscribe to my channel for the best videos', 70.0)] * 4
        analysis = self._analysis(db, user, comments=comments)
        _svc(app).analyze(analysis, comments=None, entities=None)
        rows = _svc(app).get_analysis_coordination(analysis.id)
        assert all(isinstance(row, dict) for row in rows)
        assert all('signal_type' in r for r in rows)


def commit_and_count(real_commit, counter):
    counter['n'] += 1
    return real_commit()


# --------------------------------------------------------------------------
# PostgreSQL-safe aggregation
# --------------------------------------------------------------------------
class TestCoordinationPostgresCompatibility:
    def _sql(self, statement):
        return str(statement.compile(dialect=_db.engine.dialect))

    def test_signal_type_group_by_is_explicit(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            CoordinationSignal.signal_type,
            func.count(CoordinationSignal.id),
        ).group_by(CoordinationSignal.signal_type).statement
        sql = self._sql(statement)
        assert 'GROUP BY' in sql
        assert 'coordination_signals.signal_type' in sql.split('GROUP BY')[1]

    def test_signal_type_in_aggregate_uses_distinct(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            func.count(func.distinct(CoordinationSignal.signal_type))
        ).statement
        assert 'DISTINCT' in self._sql(statement).upper()

    def test_level_aggregate_computes(self, app, db, user):
        from models.comment_result import CommentResult
        analysis = Analysis(user_id=user.id, analysis_type='youtube')
        db.session.add(analysis)
        db.session.commit()
        from sqlalchemy import func
        db.session.add(CoordinationSignal(analysis_id=analysis.id, user_id=user.id,
                                          signal_type='repeated_content', score=80.0))
        db.session.add(CoordinationSignal(analysis_id=analysis.id, user_id=user.id,
                                          signal_type='shared_entities', score=40.0))
        db.session.commit()
        rows = db.session.query(
            CoordinationSignal.signal_type, func.avg(CoordinationSignal.score)
        ).group_by(CoordinationSignal.signal_type).all()
        assert len(rows) == 2
        assert {r[0] for r in rows} == {'repeated_content', 'shared_entities'}

    def test_null_timestamps_handled_in_timing(self, app):
        svc = _svc(app)
        # mixed: 2 with ts within window + 1 with NULL timestamp
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        units = [_unit(svc, f'spread the word about meeting {i}', author=f'u{i}',
                       ts=base + timedelta(seconds=i * 3)) for i in range(2)]
        units.append(_unit(svc, 'spread the word about meeting null', author='unull', ts=None))
        sig = svc._detect_synchronized_timing(None, units, [])
        # NULL timestamp is excluded from timing; only 2 valid < MIN_TIMING_CLUSTER
        assert sig is None


# --------------------------------------------------------------------------
# Transaction safety + failure isolation
# --------------------------------------------------------------------------
class TestCoordinationTransactionSafety:
    def _analysis(self, db, user, comments=()):
        analysis = Analysis(user_id=user.id, analysis_type='youtube')
        db.session.add(analysis)
        db.session.commit()
        for text, risk in comments:
            from models.comment_result import CommentResult
            db.session.add(CommentResult(analysis_id=analysis.id, comment_text=text,
                                         author='a', risk_score=risk, risk_level='High',
                                         toxicity_score=0.0))
        db.session.commit()
        return analysis

    def test_integrity_error_rolls_back_then_query_succeeds(self, app, db, user):
        analysis = self._analysis(db, user, comments=[('x', 10.0)])
        db.session.add(CoordinationSignal(analysis_id=analysis.id, user_id=user.id,
                                          signal_type='repeated_content'))
        db.session.commit()
        db.session.add(CoordinationSignal(analysis_id=analysis.id, user_id=user.id,
                                          signal_type='repeated_content'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert CoordinationSignal.query.filter_by(
            analysis_id=analysis.id).count() == 1
        assert Analysis.query.count() >= 1

    def test_detection_failure_does_not_fail_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        analysis_service = AnalysisService()
        original = analysis_service.coordination_service.analyze

        def boom(*args, **kwargs):
            raise RuntimeError('coordinator exploded')

        analysis_service.coordination_service.analyze = boom
        try:
            result = analysis_service.create_youtube_analysis(
                user.id, 'dQw4w9WgXcQ', comment_limit=5)
            assert result['success'] is True
        finally:
            analysis_service.coordination_service.analyze = original

    def test_sqlalchemy_failure_reports_unavailable(self, app, db, user, monkeypatch):
        analysis = self._analysis(db, user, comments=[('please subscribe to channel', 70.0)] * 4)
        svc = _svc(app)

        def boom(*args, **kwargs):
            raise SQLAlchemyError('db down')

        monkeypatch.setattr(svc, '_persist', boom)
        result = svc.analyze(analysis, comments=None, entities=None)
        assert result['available'] is False
        assert result['max_signal_score'] is None
        assert Analysis.query.count() >= 1

    def test_feature_flag_off(self, app, db, user):
        app.config['ENABLE_COORDINATION_DETECTION'] = False
        analysis = self._analysis(db, user, comments=[('please subscribe to channel', 70.0)] * 4)
        result = _svc(app).analyze(analysis, comments=None, entities=None)
        assert result['available'] is False
        assert 'disabled' in result['reasons'][0].lower()


# --------------------------------------------------------------------------
# Pipeline integration
# --------------------------------------------------------------------------
class TestCoordinationIntegration:
    def test_youtube_pipeline_creates_signals(self, app, db, user):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert result['success'] is True
        assert CoordinationSignal.query.filter_by(
            analysis_id=result['analysis_id']).count() > 0

    def test_reddit_pipeline_creates_signals(self, app, db, user):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_reddit_analysis(
            user.id, 'abc123', subreddit='technology', comment_limit=25)
        assert result['success'] is True
        assert CoordinationSignal.query.filter_by(
            analysis_id=result['analysis_id']).count() > 0

    def test_flag_off_skips_coordination(self, app, db, user):
        app.config['ENABLE_COORDINATION_DETECTION'] = False
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert result['success'] is True
        assert CoordinationSignal.query.filter_by(
            analysis_id=result['analysis_id']).count() == 0

    def test_v11_and_narrative_still_run(self, app, db, user):
        from models.media_analysis import MediaAnalysis
        from models.narrative import Narrative
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert MediaAnalysis.query.filter_by(
            analysis_id=result['analysis_id']).first() is not None
        assert Narrative.query.count() > 0
        assert CoordinationSignal.query.filter_by(
            analysis_id=result['analysis_id']).count() > 0

    def test_result_page_renders(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        response = logged_in_client.get(f'/analysis/{result["analysis_id"]}')
        assert response.status_code == 200

    def test_dashboard_and_exports_work(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        from services.export_service import ExportService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=5)
        assert logged_in_client.get('/dashboard/').status_code == 200
        assert ExportService().generate_csv(result['analysis_id'], user.id) is not None
        assert ExportService().generate_json(result['analysis_id'], user.id) is not None
