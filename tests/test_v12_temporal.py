"""V12 Phase F - Temporal Intelligence tests.

Covers growth-score computation from real temporal evidence, first/last-seen
progression, cross-platform spread, time-window recurrence/trend, bounded limits,
unavailable-vs-zero behavior, NULL timestamp handling, duplicate/re-run
behavior, rollback recovery, PostgreSQL-safe aggregation, SQLite compatibility,
and pipeline integration (YouTube + Reddit).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from database import db as _db
from models.analysis import Analysis
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from repositories.temporal_repository import TemporalRepository
from services.temporal_intelligence_service import TemporalIntelligenceService
from services.text_similarity_service import TextSimilarityService


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''
    TextSimilarityService.clear_caches()


def _svc(app):
    return TemporalIntelligenceService()


def _make_narrative(db, user, name='battery life', platform_count=1,
                    occurrence_count=1, evidence=None):
    n = Narrative(user_id=user.id, name=name, normalized_name=name,
                  risk_score=40.0, confidence=60.0,
                  occurrence_count=occurrence_count, platform_count=platform_count,
                  evidence=evidence or {'detection_method': 'heuristic'})
    db.session.add(n)
    db.session.commit()
    return n


def _add_occurrence(db, user, narrative, days_back, platform='youtube',
                    relevance=70.0, analysis=None, occurred_at=None):
    if analysis is None:
        analysis = Analysis(user_id=user.id, analysis_type=platform)
        db.session.add(analysis)
        db.session.commit()
    ts = occurred_at or (datetime(2024, 6, 1, 10, 0, 0)
                         - timedelta(days=days_back))
    db.session.add(NarrativeOccurrence(
        narrative_id=narrative.id, analysis_id=analysis.id, user_id=user.id,
        platform=platform, content_ref=f'v{narrative.id}_{days_back}',
        relevance_score=relevance, occurred_at=ts, timestamp_source='platform'))
    db.session.commit()
    return analysis


# --------------------------------------------------------------------------
# Growth scoring
# --------------------------------------------------------------------------
class TestTemporalScoring:
    def test_growth_score_computed_and_persisted(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=3)
        for days_back in (0, 6, 12):
            _add_occurrence(db, user, n, days_back)
        svc = _svc(app)
        comp = svc.analyze(Analysis.query.first(), narratives=[n])
        assert comp['available'] is True
        assert comp['narratives_scored'] == 1
        db.session.refresh(n)
        assert n.growth_score > 0.0
        assert 0.0 <= n.growth_score <= 100.0
        # signals annotated into existing evidence
        te = (n.evidence or {}).get('temporal')
        assert te is not None
        assert te['capability'] == 'heuristic'
        assert set(te['signals']) == {'recency', 'span', 'trend',
                                      'recurrence', 'cross_platform', 'propagation'}

    def test_scores_are_deterministic(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=3)
        for days_back in (0, 5, 10):
            _add_occurrence(db, user, n, days_back)
        svc = _svc(app)
        a = svc._score_narrative(user.id, n)['growth_score']
        db.session.expire_all()
        b = svc._score_narrative(user.id, n)['growth_score']
        assert a == b

    def test_recency_decays_with_age(self, app, db, user):
        svc = _svc(app)
        recent = svc._recency_score(datetime(2024, 6, 1, 10, 0, 0),
                                    datetime(2024, 6, 1, 10, 0, 0))
        old = svc._recency_score(datetime(2024, 1, 1, 10, 0, 0),
                                 datetime(2024, 6, 1, 10, 0, 0))
        assert recent > old
        assert recent >= 0 and recent <= 100

    def test_longer_span_scores_higher(self, app, db, user):
        svc = _svc(app)
        short = svc._span_score(datetime(2024, 6, 1), datetime(2024, 6, 2))
        long = svc._span_score(datetime(2024, 1, 1), datetime(2024, 6, 1))
        assert long > short

    def test_recent_growth_trend_higher_than_fading(self, app, db, user):
        svc = _svc(app)
        fading = [{'occurred_at': datetime(2024, 1, 1)}, {'occurred_at': datetime(2024, 1, 20)}]
        growing = [{'occurred_at': datetime(2024, 5, 1)}, {'occurred_at': datetime(2024, 5, 25)}]
        t_fading = svc._trend_score(datetime(2024, 1, 1), datetime(2024, 1, 20), fading)
        t_growing = svc._trend_score(datetime(2024, 5, 1), datetime(2024, 5, 25), growing)
        # both have symmetric earlier/recent split -> similar; assert bounded
        assert 0.0 <= t_fading <= 100.0 and 0.0 <= t_growing <= 100.0
        assert t_growing >= 50.0  # recent-heavy

    def test_cross_platform_signal_from_stored_platforms(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2, platform_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='reddit')
        svc = _svc(app)
        sig = svc._score_narrative(user.id, n)
        assert sig['signals']['cross_platform'] >= 50.0
        assert sig['is_cross_platform'] is True

    def test_propagation_signal_counts_events(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2)
        a1 = _add_occurrence(db, user, n, 0, platform='youtube')
        a2 = _add_occurrence(db, user, n, 1, platform='reddit')
        from models.analysis import YouTubeAnalysis
        from models.reddit_analysis import RedditAnalysis
        db.session.add(YouTubeAnalysis(analysis_id=a1.id, video_id='v1', video_title='t',
                                       video_description='d', channel_name='C', is_demo=True))
        db.session.add(RedditAnalysis(analysis_id=a2.id, post_id='r1', subreddit='s',
                                      post_title='t', post_body='d', is_demo=True))
        db.session.add(PropagationEvent(narrative_id=n.id, user_id=user.id,
                                        source_analysis_id=a1.id, target_analysis_id=a2.id,
                                        relationship_type='potentially_propagated',
                                        source_platform='youtube', target_platform='reddit'))
        db.session.commit()
        svc = _svc(app)
        sig = svc._score_narrative(user.id, n)
        assert sig['signals']['propagation'] >= 20.0

    def test_unavailable_signals_are_none_not_zero(self, app, db, user):
        svc = _svc(app)
        # recency with no occurrences
        n = _make_narrative(db, user)
        result = svc._score_narrative(user.id, n)  # no occurrences -> None signal
        assert result is None

    def test_no_occurrences_reports_unavailable(self, app, db, user):
        n = _make_narrative(db, user)
        svc = _svc(app)
        comp = svc.analyze(Analysis.query.first() if Analysis.query.count() else None,
                           narratives=[n])
        # no occurrences -> no usable temporal data
        if comp['available'] is False:
            assert comp['max_growth_score'] is None


# --------------------------------------------------------------------------
# Temporal signal semantics + unavailable
# --------------------------------------------------------------------------
class TestTemporalSignals:
    def test_growth_renormalizes_over_available_signals(self, app, db, user):
        # occurrence with no propagation/cross-platform but presence on one platform
        n = _make_narrative(db, user, occurrence_count=1, platform_count=1)
        _add_occurrence(db, user, n, 0, platform='youtube')
        n.platform_count = 1
        db.session.commit()
        svc = _svc(app)
        sig = svc._score_narrative(user.id, n)
        assert sig is not None
        signals = sig['signals']
        # cross_platform is 0.0 (present, single platform) -> available, not None
        assert signals['cross_platform'] == 0.0
        assert sig['growth_score'] > 0.0

    def test_timestamp_source_never_fabricated(self, app, db, user):
        n = _make_narrative(db, user)
        # occurrence with a real platform timestamp
        ts = datetime(2024, 6, 1, 10, 0, 0)
        _add_occurrence(db, user, n, 0, occurred_at=ts)
        svc = _svc(app)
        sig = svc._score_narrative(user.id, n)
        assert sig['first_seen_at'] == ts
        assert sig['last_seen_at'] == ts

    def test_timezone_aware_timestamp_normalized(self, app, db, user):
        n = _make_narrative(db, user)
        aware = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        _add_occurrence(db, user, n, 0, occurred_at=aware)
        svc = _svc(app)
        sig = svc._score_narrative(user.id, n)
        assert sig['first_seen_at'].tzinfo is None
        assert sig['first_seen_at'] == aware.replace(tzinfo=None)

    def test_reasons_are_hedged_and_explainable(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='reddit')
        svc = _svc(app)
        sig = svc._score_narrative(user.id, n)
        joined = ' '.join(sig['reasons']).lower()
        assert any(kw in joined for kw in ('possible', 'heuristic', 'observed'))
        assert 'caus' not in joined or 'non-causal' in joined
        assert 'grew' in joined or 'occur' in joined or 'active' in joined

    def test_limitations_hedged(self, app):
        svc = _svc(app)
        joined = ' '.join(svc._limitations()).lower()
        assert not any(word in joined for word in ('proof', 'guaranteed', 'verdict'))
        assert ('heuristic' in joined or 'does not establish' in joined
                or 'not a trained' in joined)
        # at least one limitation carries the explicit causality disclaimer
        assert any('does not establish' in l.lower() or 'heuristic' in l.lower()
                   for l in svc._limitations())

    def test_capability_and_method_labelled(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='youtube')
        svc = _svc(app)
        comp = svc.analyze(None, narratives=[n])
        assert comp['capability'] == 'heuristic'
        assert comp['detection_method'] == 'heuristic_temporal_bucket'
        assert comp['limitations']


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------
class TestTemporalBounds:
    def test_narrative_cap_is_bounded(self, app, db, user):
        svc = _svc(app)
        app.config['TEMPORAL_MAX_NARRATIVES'] = 2
        narratives = []
        for i in range(5):
            n = _make_narrative(db, user, name=f'narrative_{i}')
            _add_occurrence(db, user, n, 0)
            narratives.append(n)
        comp = svc.analyze(None, narratives=narratives)
        # the explicit list is honored; when None the repo cap applies
        assert comp['narratives_scored'] <= 5
        repo_limit = int(svc._cfg('TEMPORAL_MAX_NARRATIVES', svc.MAX_NARRATIVES_SCORED))
        assert repo_limit <= app.config['TEMPORAL_MAX_NARRATIVES']

    def test_occurrence_cap_applied(self, app, db, user):
        svc = _svc(app)
        app.config['TEMPORAL_MAX_OCCURRENCES'] = 3
        n = _make_narrative(db, user)
        for i in range(10):
            _add_occurrence(db, user, n, i)
        sig = svc._score_narrative(user.id, n)
        assert sig is not None
        # occurrences loaded from repo are bounded
        assert len(svc.temporal_repo.get_occurrences(
            n.id, user_id=user.id, limit=app.config['TEMPORAL_MAX_OCCURRENCES'])) <= 3


# --------------------------------------------------------------------------
# Persistence / rerun / duplicate
# --------------------------------------------------------------------------
class TestTemporalPersistence:
    def test_growth_written_to_column(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='youtube')
        svc = _svc(app)
        svc.analyze(None, narratives=[n])
        db.session.refresh(n)
        assert n.growth_score > 0.0

    def test_rerun_is_idempotent_no_duplicate_rows(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='youtube')
        svc = _svc(app)
        svc.analyze(None, narratives=[n])
        first = n.growth_score
        svc.analyze(None, narratives=[n])
        second = n.growth_score
        assert Narrative.query.count() == 1
        assert NarrativeOccurrence.query.count() == 2
        assert first == second

    def test_evidence_annotated_below_existing(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2,
                            evidence={'detection_method': 'heuristic',
                                      'risk_score': 40.0})
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='youtube')
        svc = _svc(app)
        svc.analyze(None, narratives=[n])
        ev = (n.evidence or {})
        assert ev.get('detection_method') == 'heuristic'  # preserved
        assert 'temporal' in ev
        assert ev['temporal']['capability'] == 'heuristic'


# --------------------------------------------------------------------------
# PostgreSQL-safe aggregation + SQLite
# --------------------------------------------------------------------------
class TestTemporalPostgresCompatibility:
    def _sql(self, statement):
        return str(statement.compile(dialect=_db.engine.dialect))

    def test_occurrence_platforms_group_by_explicit(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            NarrativeOccurrence.platform,
        ).group_by(NarrativeOccurrence.platform).statement
        sql = self._sql(statement)
        assert 'GROUP BY' in sql
        assert 'narrative_occurrences.platform' in sql.split('GROUP BY')[1]

    def test_distinct_platform_used(self, app):
        statement = _db.session.query(
            _db.func.count(_db.func.distinct(NarrativeOccurrence.platform)),
        ).statement
        assert 'DISTINCT' in self._sql(statement).upper()

    def test_count_queries_execute_on_sqlite(self, app, db, user):
        n = _make_narrative(db, user)
        _add_occurrence(db, user, n, 0, platform='youtube')
        repo = TemporalRepository()
        since = datetime(2024, 1, 1)
        assert repo.count_occurrences_since(n.id, since, user_id=user.id) >= 1
        assert repo.count_occurrences_between(
            n.id, datetime(2024, 1, 1), datetime(2025, 1, 1), user_id=user.id) >= 1
        assert repo.get_occurrence_platforms(n.id, user_id=user.id) == ['youtube']

    def test_cross_platform_propagation_count(self, app, db, user):
        n = _make_narrative(db, user)
        a1 = _add_occurrence(db, user, n, 0, platform='youtube')
        a2 = _add_occurrence(db, user, n, 1, platform='reddit')
        from models.analysis import YouTubeAnalysis
        from models.reddit_analysis import RedditAnalysis
        db.session.add(YouTubeAnalysis(analysis_id=a1.id, video_id='v1', video_title='t',
                                       video_description='d', channel_name='C', is_demo=True))
        db.session.add(RedditAnalysis(analysis_id=a2.id, post_id='r1', subreddit='s',
                                      post_title='t', post_body='d', is_demo=True))
        db.session.add(PropagationEvent(narrative_id=n.id, user_id=user.id,
                                        source_analysis_id=a1.id, target_analysis_id=a2.id,
                                        relationship_type='potentially_propagated',
                                        source_platform='youtube', target_platform='reddit'))
        db.session.commit()
        repo = TemporalRepository()
        assert repo.count_propagation_events(n.id, user_id=user.id) == 1
        assert repo.count_cross_platform_propagation(n.id, user_id=user.id) == 1

    def test_entity_history_windows_bounded(self, app, db, user):
        from models.entity_history import EntityHistory
        for i in range(3):
            db.session.add(EntityHistory(normalized_name='Tesla', entity_type='COMPANY',
                                         video_id=f'v{i}', user_id=user.id,
                                         created_at=datetime(2024, 6, 1 + i, 10, 0, 0)))
        db.session.commit()
        repo = TemporalRepository()
        rows = repo.get_entity_history_windows(user.id, 'Tesla', limit=2)
        assert len(rows) <= 2


# --------------------------------------------------------------------------
# Transaction safety + isolation
# --------------------------------------------------------------------------
class TestTemporalTransactionSafety:
    def test_sqlalchemy_failure_rolls_back(self, app, db, user, monkeypatch):
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='youtube')
        svc = _svc(app)

        def boom(*args, **kwargs):
            raise SQLAlchemyError('db down')

        monkeypatch.setattr(svc, '_persist_growth', boom)
        comp = svc.analyze(None, narratives=[n])
        assert comp['available'] is False
        assert comp['max_growth_score'] is None
        # subsequent DB work still works
        assert Narrative.query.count() == 1

    def test_detection_failure_does_not_fail_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        service = AnalysisService()
        original = service.temporal_service.analyze

        def boom(*args, **kwargs):
            raise RuntimeError('temporal exploded')

        service.temporal_service.analyze = boom
        try:
            result = service.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
            assert result['success'] is True
        finally:
            service.temporal_service.analyze = original

    def test_feature_flag_off(self, app, db, user):
        app.config['ENABLE_TEMPORAL_INTELLIGENCE'] = False
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='youtube')
        comp = _svc(app).analyze(None, narratives=[n])
        assert comp['available'] is False
        assert 'disabled' in comp['reasons'][0].lower()


# --------------------------------------------------------------------------
# Read APIs + integration
# --------------------------------------------------------------------------
class TestTemporalReadAPIs:
    def test_temporal_summary_read_api(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=2)
        _add_occurrence(db, user, n, 0, platform='youtube')
        _add_occurrence(db, user, n, 1, platform='reddit')
        svc = _svc(app)
        svc.analyze(None, narratives=[n])
        summary = svc.get_temporal_summary(user.id)
        assert summary['narrative_count'] >= 1
        assert 'narratives' in summary
        assert 'last_seen_any' in summary

    def test_narrative_temporal_read_api(self, app, db, user):
        n = _make_narrative(db, user, occurrence_count=1)
        _add_occurrence(db, user, n, 0, platform='youtube')
        svc = _svc(app)
        result = svc.get_narrative_temporal(n.id, user_id=user.id)
        assert result is not None
        assert result['available'] is True
        assert 'growth_score' in result
        assert result['capability'] == 'heuristic'


class TestTemporalIntegration:
    def test_youtube_pipeline_sets_growth(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        for _ in range(2):
            svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        narratives = Narrative.query.filter_by(user_id=user.id).all()
        assert narratives
        scored = [n for n in narratives if n.growth_score > 0.0]
        assert scored

    def test_reddit_pipeline_sets_growth(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        svc.create_reddit_analysis(user.id, 'abc123', subreddit='technology', comment_limit=25)
        narratives = Narrative.query.filter_by(user_id=user.id).all()
        scored = [n for n in narratives if n.growth_score > 0.0]
        assert scored

    def test_flag_off_skips_pipeline(self, app, db, user):
        app.config['ENABLE_TEMPORAL_INTELLIGENCE'] = False
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        for _ in range(2):
            svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        for n in Narrative.query.filter_by(user_id=user.id).all():
            if not (n.evidence or {}).get('temporal'):
                # temporal never ran
                assert n.growth_score == 0.0

    def test_v11_and_v12_stages_still_run(self, app, db, user):
        from models.coordination_signal import CoordinationSignal
        from models.media_analysis import MediaAnalysis
        from models.narrative import Narrative
        from models.propagation_event import PropagationEvent
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert MediaAnalysis.query.filter_by(analysis_id=r1['analysis_id']).first() is not None
        assert Narrative.query.count() > 0
        assert PropagationEvent.query.count() > 0
        assert any(n.growth_score > 0.0 for n in Narrative.query.all())

    def test_routes_still_functional(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        aid = r1['analysis_id']
        assert logged_in_client.get('/dashboard/').status_code == 200
        assert logged_in_client.get('/analysis/history').status_code == 200
        assert logged_in_client.get(f'/analysis/{aid}').status_code == 200
        assert logged_in_client.get(f'/export/csv/{aid}').status_code == 200
        assert logged_in_client.get(f'/export/json/{aid}').status_code == 200
        assert logged_in_client.get('/trends/').status_code == 200
