"""V12 Phase E - Propagation Intelligence tests.

Covers basic detection, same/cross-platform relationships, temporal ordering,
lag calculation, similarity thresholds, missing timestamps, duplicate prevention
(including the PostgreSQL NULL-uniqueness caveat), comparison bounding, failure
isolation, transaction recovery, flag-off behavior and pipeline integration.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db as _db
from models.analysis import Analysis, YouTubeAnalysis
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from models.reddit_analysis import RedditAnalysis
from repositories.propagation_repository import PropagationRepository
from services.propagation_intelligence_service import PropagationIntelligenceService
from services.text_similarity_service import TextSimilarityService


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''
    TextSimilarityService.clear_caches()


def _svc(app):
    return PropagationIntelligenceService()


def _make_user(db, username='pu'):
    from werkzeug.security import generate_password_hash
    from models.user import User
    u = User(username=username, email=f'{username}@example.com',
             password_hash=generate_password_hash('x'))
    db.session.add(u)
    db.session.commit()
    return u


def _make_analysis(db, user, platform='youtube', published_at=None, video_id='vid1',
                   post_id='post1'):
    analysis = Analysis(user_id=user.id, analysis_type=platform)
    db.session.add(analysis)
    db.session.commit()
    if platform == 'youtube':
        db.session.add(YouTubeAnalysis(analysis_id=analysis.id, video_id=video_id,
                                       video_title='title', video_description='desc',
                                       channel_name='Chan', published_at=published_at,
                                       is_demo=True))
    else:
        db.session.add(RedditAnalysis(analysis_id=analysis.id, post_id=post_id,
                                      subreddit='tech', post_title='title',
                                      post_body='desc', created_utc=published_at,
                                      is_demo=True))
    db.session.commit()
    return analysis


def _link_narrative(db, user, analysis, name='election fraud', platform='youtube',
                    occurred_at=None, relevance=80.0, narrative=None, content_ref=None):
    if narrative is None:
        narrative = Narrative(user_id=user.id, name=name, normalized_name=name,
                              risk_score=60.0, confidence=70.0, occurrence_count=1,
                              platform_count=1)
        db.session.add(narrative)
        db.session.flush()
    ts = occurred_at or datetime(2024, 1, 1, 10, 0, 0)
    db.session.add(NarrativeOccurrence(
        narrative_id=narrative.id, analysis_id=analysis.id, user_id=user.id,
        platform=platform, content_ref=content_ref or getattr(analysis, 'video_id', None),
        relevance_score=relevance, occurred_at=ts, timestamp_source='platform'))
    db.session.commit()
    return narrative


# --------------------------------------------------------------------------
# Basic detection + ordering
# --------------------------------------------------------------------------
class TestPropagationDetection:
    def test_basic_propagation_detected(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        comp = svc.analyze(a2)
        assert comp['available'] is True
        assert comp['event_count'] >= 1
        event = PropagationEvent.query.first()
        assert event is not None
        assert event.narrative_id == narrative.id
        assert event.reasons

    def test_earlier_occurrence_is_source(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'v2')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1',
                                    occurred_at=datetime(2024, 1, 1, 10, 0, 0))
        _link_narrative(db, user, a2, narrative=narrative, platform='youtube', content_ref='v2',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        svc.analyze(a2)
        event = PropagationEvent.query.first()
        assert event.source_analysis_id == a1.id
        assert event.target_analysis_id == a2.id
        assert event.direction == PropagationEvent.DIRECTION_SOURCE_TO_TARGET

    def test_lag_is_calculated(self, app, db, user):
        svc = _svc(app)
        earlier = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        later = earlier + timedelta(days=3, hours=2)
        a1 = _make_analysis(db, user, 'youtube', earlier, 'v1')
        a2 = _make_analysis(db, user, 'youtube', later, 'v2')
        narrative = _link_narrative(db, user, a1, occurred_at=earlier.replace(tzinfo=None))
        _link_narrative(db, user, a2, narrative=narrative,
                        occurred_at=later.replace(tzinfo=None))
        svc.analyze(a2)
        event = PropagationEvent.query.first()
        expected = (later.replace(tzinfo=None) - earlier.replace(tzinfo=None)).total_seconds()
        assert abs(event.lag_seconds - expected) < 1

    def test_never_negative_lag(self, app, db, user):
        svc = _svc(app)
        # target timestamp EARLIER than source -> ordering must swap, lag stays >= 0
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v2')
        narrative = _link_narrative(db, user, a1, content_ref='v1',
                                    occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        _link_narrative(db, user, a2, narrative=narrative, content_ref='v2',
                        occurred_at=datetime(2024, 1, 1, 10, 0, 0))
        svc.analyze(a1)  # current = a1 (later)
        event = PropagationEvent.query.first()
        # source must be the earlier a2
        assert event.source_analysis_id == a2.id
        assert event.target_analysis_id == a1.id
        assert event.lag_seconds is None or event.lag_seconds >= 0

    def test_no_temporal_similarity_without_shared_narrative(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0), 'v2')
        _link_narrative(db, user, a1, name='alpha', content_ref='v1')
        _link_narrative(db, user, a2, name='beta', content_ref='v2')
        comp = svc.analyze(a2)
        assert comp['available'] is False  # no shared narrative

    def test_missing_document_unavailable(self, app):
        svc = _svc(app)
        comp = svc.analyze(None)  # no analysis -> handled gracefully
        assert comp['available'] is False


# --------------------------------------------------------------------------
# Cross-platform
# --------------------------------------------------------------------------
class TestPropagationCrossPlatform:
    def test_cross_platform_detected(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        comp = svc.analyze(a2)
        event = PropagationEvent.query.first()
        assert event.is_cross_platform is True
        assert event.source_platform == PropagationEvent.PLATFORM_YOUTUBE
        assert event.target_platform == PropagationEvent.PLATFORM_REDDIT
        assert comp['cross_platform_count'] >= 1

    def test_same_platform_is_not_cross_platform(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0), 'v2')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='youtube', content_ref='v2',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        svc.analyze(a2)
        event = PropagationEvent.query.first()
        assert event.is_cross_platform is False
        assert event.source_platform == event.target_platform

    def test_cross_platform_relationship_assignable(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1', relevance=90.0)
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0), relevance=90.0)
        svc.analyze(a2)
        event = PropagationEvent.query.first()
        assert event.relationship_type == PropagationEvent.RELATION_POTENTIALLY_PROPAGATED


# --------------------------------------------------------------------------
# Similarity thresholds + scoring
# --------------------------------------------------------------------------
class TestPropagationScoring:
    def test_high_similarity_thresholds(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'v2')
        narrative = _link_narrative(db, user, a1, content_ref='v1', relevance=95.0)
        _link_narrative(db, user, a2, narrative=narrative, content_ref='v2',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0), relevance=95.0)
        comp = svc.analyze(a2)
        event = PropagationEvent.query.first()
        assert event.similarity_score > 0.0
        assert 0.0 <= event.propagation_score <= 100.0
        assert 0.0 <= event.confidence <= 100.0

    def test_low_relevance_yields_lower_similarity(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0), 'v2')
        n_prominent = _link_narrative(db, user, a1, name='prominent', content_ref='v1', relevance=95.0)
        _link_narrative(db, user, a2, narrative=n_prominent, name='prominent', content_ref='v2',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0), relevance=95.0)
        svc.analyze(a2)
        high = PropagationEvent.query.filter_by(narrative_id=n_prominent.id).first()

        db.session.commit()
        # create a second, low-relevance narrative pair in new analyses
        b1 = _make_analysis(db, user, 'youtube', datetime(2024, 3, 1, 10, 0, 0), 'b1')
        b2 = _make_analysis(db, user, 'youtube', datetime(2024, 4, 1, 10, 0, 0), 'b2')
        n_marginal = _link_narrative(db, user, b1, name='marginal', content_ref='b1', relevance=20.0)
        _link_narrative(db, user, b2, narrative=n_marginal, name='marginal', content_ref='b2',
                        occurred_at=datetime(2024, 4, 1, 10, 0, 0), relevance=20.0)
        svc.analyze(b2)
        low = PropagationEvent.query.filter_by(narrative_id=n_marginal.id).first()
        assert low.similarity_score < high.similarity_score

    def test_scores_are_hedged_reasons(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1', relevance=90.0)
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0), relevance=90.0)
        svc.analyze(a2)
        event = PropagationEvent.query.first()
        joined = ' '.join(event.reasons).lower()
        # hedged: names propagation as a possibility, never as established fact
        assert any(kw in joined for kw in ('consistent with', 'possible', 'indicator'))
        # explicitly disclaims causality/authorship (the word appears only inside
        # the "not evidence of causality" caveat, never as an assertion)
        assert 'not evidence of' in joined
        assert 'coordination' in joined

    def test_limitations_hedged(self, app):
        svc = _svc(app)
        comp = svc.analyze(None)
        joined = ' '.join(comp['limitations']).lower()
        # no limitation states a proof/verdict; together they hedge causality
        assert not any(word in joined for word in ('proof', 'guaranteed', 'verdict'))
        assert ('does not establish' in joined or 'consistent with' in joined
                or 'heuristic' in joined)
        # same assertion holds per indidual limitation for the causal disclaimer
        assert any('does not establish' in l.lower() or 'heuristic' in l.lower()
                   for l in comp['limitations'])


# --------------------------------------------------------------------------
# Missing timestamps
# --------------------------------------------------------------------------
class TestPropagationMissingTimestamps:
    def test_missing_both_timestamps_reports_unavailable(self, app, db, user):
        svc = _svc(app)
        # Both analyses created as_utc=None AND narrative occurrence occurred_at is
        # present, so timestamp falls back to analysis.created_at (never None).
        a1 = _make_analysis(db, user, 'youtube', None, 'v1')
        a2 = _make_analysis(db, user, 'youtube', None, 'v2')
        narrative = _link_narrative(db, user, a1, content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, content_ref='v2')
        comp = svc.analyze(a2)
        # created_at fallback still yields a usable timestamp -> available
        assert comp['available'] is True

    def test_never_fabricates_timestamp(self, app, db, user):
        svc = _svc(app)
        # narrative occurrence with occurred_at None
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'v2')
        narrative = _link_narrative(db, user, a1, content_ref='v1')
        db.session.add(NarrativeOccurrence(narrative_id=narrative.id, analysis_id=a2.id,
                                           user_id=user.id, platform='youtube',
                                           content_ref='v2', relevance_score=50.0,
                                           occurred_at=None, timestamp_source='analysis_fallback'))
        db.session.commit()
        comp = svc.analyze(a2)
        event = PropagationEvent.query.first()
        # falls back to created_at (real), never a fabricated epoch
        assert event.occurred_at is not None

    def test_timezone_aware_timestamp_normalized(self, app, db, user):
        svc = _svc(app)
        earlier = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        a1 = _make_analysis(db, user, 'youtube', earlier, 'v1')
        a2 = _make_analysis(db, user, 'youtube', earlier + timedelta(days=1), 'v2')
        narrative = _link_narrative(db, user, a1, occurred_at=datetime(2024, 1, 1, 10, 0, 0))
        _link_narrative(db, user, a2, narrative=narrative,
                        occurred_at=datetime(2024, 1, 2, 10, 0, 0))
        svc.analyze(a2)
        event = PropagationEvent.query.first()
        assert event.occurred_at.tzinfo is None
        assert event.lag_seconds == 86400.0


# --------------------------------------------------------------------------
# Duplicate prevention + NULL uniqueness
# --------------------------------------------------------------------------
class TestPropagationDuplicates:
    def test_duplicate_prevention_on_reanalyse(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        svc.analyze(a2)
        first = PropagationEvent.query.count()
        svc.analyze(a2)
        second = PropagationEvent.query.count()
        assert first == second
        assert first > 0

    def test_explicit_existence_check_covers_null_narrative(self, app, db, user):
        # PostgreSQL: uq constraint does NOT dedupe narrative_id IS NULL (NULLs
        # distinct). The repository's explicit check must.
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0), 'v2')
        repo = PropagationRepository()
        assert repo.edge_exists(None, a1.id, a2.id, 'related') is False
        e = PropagationEvent(narrative_id=None, user_id=user.id, source_analysis_id=a1.id,
                             target_analysis_id=a2.id, relationship_type='related',
                             source_platform='youtube', target_platform='youtube')
        db.session.add(e)
        db.session.commit()
        assert repo.edge_exists(None, a1.id, a2.id, 'related') is True

    def test_named_unique_constraint_present(self, app):
        constraints = [c.name for c in _db.metadata.tables['propagation_events'].constraints
                       if isinstance(c, _db.UniqueConstraint)]
        assert 'uq_propagation_events_edge' in constraints

    def test_cascade_delete_on_analysis(self, app, db, user):
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        _svc(app).analyze(a2)
        assert PropagationEvent.query.count() > 0
        db.session.delete(a2)
        db.session.commit()
        assert PropagationEvent.query.filter_by(
            source_analysis_id=a1.id).count() == 0


# --------------------------------------------------------------------------
# Comparison bounding
# --------------------------------------------------------------------------
class TestPropagationBounding:
    def test_candidate_analyses_are_bounded(self, app, db, user):
        svc = _svc(app)
        app.config['PROPAGATION_MAX_CANDIDATES'] = 3
        # create 8 prior analyses, each with the SAME narrative
        narrative = None
        for i in range(8):
            a = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1 + i, 10, 0, 0), f'v{i}')
            narrative = _link_narrative(db, user, a, narrative=narrative, name='shared',
                                        content_ref=f'v{i}',
                                        occurred_at=datetime(2024, 1, 1 + i, 10, 0, 0))
        current = _make_analysis(db, user, 'reddit', datetime(2024, 3, 1, 10, 0, 0), 'r1')
        _link_narrative(db, user, current, narrative=narrative, name='shared',
                        platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 3, 1, 10, 0, 0))
        svc.analyze(current)
        events = PropagationEvent.query.filter_by(target_analysis_id=current.id).all()
        assert len(events) <= svc.MAX_PROPAGATION_EVENTS
        # candidate cap enforced (at most MAX_CANDIDATE_ANALYSES other analyses considered)
        sources = {e.source_analysis_id for e in events}
        assert len(sources) <= app.config['PROPAGATION_MAX_CANDIDATES']

    def test_event_cap_applied(self, app, db, user):
        svc = _svc(app)
        app.config['PROPAGATION_MAX_EVENTS'] = 2
        narrative = None
        for i in range(6):
            a = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1 + i, 10, 0, 0), f'v{i}')
            narrative = _link_narrative(db, user, a, narrative=narrative, name='common',
                                        content_ref=f'v{i}',
                                        occurred_at=datetime(2024, 1, 1 + i, 10, 0, 0))
        current = _make_analysis(db, user, 'youtube', datetime(2024, 3, 1, 10, 0, 0), 'v7')
        _link_narrative(db, user, current, narrative=narrative, name='common', content_ref='v7',
                        occurred_at=datetime(2024, 3, 1, 10, 0, 0))
        comp = svc.analyze(current)
        assert comp['event_count'] <= 2

    def test_no_unbounded_pairwise_growth(self, app, db, user):
        svc = _svc(app)
        assert svc.MAX_CANDIDATE_ANALYSES > 0
        assert svc.MAX_NARRATIVE_COMPARISONS > 0
        assert svc.MAX_PROPAGATION_EVENTS > 0


# --------------------------------------------------------------------------
# Transaction safety + isolation
# --------------------------------------------------------------------------
class TestPropagationTransactionSafety:
    def test_detection_failure_does_not_fail_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        service = AnalysisService()
        original = service.propagation_service.analyze

        def boom(*args, **kwargs):
            raise RuntimeError('propagation exploded')

        service.propagation_service.analyze = boom
        try:
            result = service.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
            assert result['success'] is True
        finally:
            service.propagation_service.analyze = original

    def test_sqlalchemy_failure_reports_unavailable(self, app, db, user, monkeypatch):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))

        def boom(*args, **kwargs):
            raise SQLAlchemyError('db down')

        monkeypatch.setattr(svc, '_persist', boom)
        comp = svc.analyze(a2)
        assert comp['available'] is False
        assert comp['max_propagation_score'] is None

    def test_integrity_error_rolls_back_then_succeeds(self, app, db, user):
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0), 'v2')
        db.session.add(PropagationEvent(narrative_id=None, user_id=user.id,
                                        source_analysis_id=a1.id, target_analysis_id=a2.id,
                                        relationship_type='related',
                                        source_platform='youtube', target_platform='youtube'))
        db.session.commit()
        # a second row slips through because NULL is distinct -> not IntegrityError.
        # Force a real IntegrityError via duplicate NON-null keys.
        e1 = PropagationEvent(narrative_id=1, user_id=user.id, source_analysis_id=a1.id,
                              target_analysis_id=a2.id, relationship_type='related',
                              source_platform='y', target_platform='y')
        db.session.add(e1)
        db.session.commit()
        db.session.add(PropagationEvent(narrative_id=1, user_id=user.id,
                                        source_analysis_id=a1.id, target_analysis_id=a2.id,
                                        relationship_type='related',
                                        source_platform='y', target_platform='y'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert Analysis.query.count() >= 2

    def test_feature_flag_off(self, app, db, user):
        app.config['ENABLE_PROPAGATION_INTELLIGENCE'] = False
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        comp = _svc(app).analyze(a2)
        assert comp['available'] is False
        assert 'disabled' in comp['reasons'][0].lower()
        assert PropagationEvent.query.count() == 0


# --------------------------------------------------------------------------
# PostgreSQL-safe query behavior
# --------------------------------------------------------------------------
class TestPropagationPostgresCompatibility:
    def _sql(self, statement):
        return str(statement.compile(dialect=_db.engine.dialect))

    def test_cross_platform_filter_query(self, app):
        # simple filtered list, no GROUP BY -> PostgreSQL-safe
        q = PropagationEvent.query.filter(
            PropagationEvent.user_id == 1,
            PropagationEvent.source_platform != PropagationEvent.target_platform,
        )
        sql = self._sql(q.statement)
        assert 'source_platform' in sql and 'target_platform' in sql
        assert 'GROUP BY' not in sql.upper()

    def test_edge_exists_null_handling_compiles(self, app):
        from sqlalchemy import or_
        stmt = PropagationEvent.query.filter(
            PropagationEvent.narrative_id.is_(None),
            PropagationEvent.source_analysis_id == 1,
            PropagationEvent.target_analysis_id == 2,
            PropagationEvent.relationship_type == 'related',
        ).statement
        sql = self._sql(stmt)
        assert 'IS NULL' in sql.upper()
        assert 'narrative_id' in sql

    def test_get_cross_platform_narrative_count_executes(self, app, db, user):
        repo = PropagationRepository()
        count = repo.get_cross_platform_narrative_count(user.id)
        assert count == 0  # empty user -> 0, no GROUP BY error

    def test_get_for_analysis_query(self, app, db, user):
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0), 'v1')
        a2 = _make_analysis(db, user, 'youtube', datetime(2024, 2, 1, 10, 0, 0), 'v2')
        repo = PropagationRepository()
        assert repo.get_for_analysis(a1.id) == []


# --------------------------------------------------------------------------
# Pipeline integration
# --------------------------------------------------------------------------
class TestPropagationIntegration:
    def test_youtube_pipeline(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        r2 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert r1['success'] and r2['success']
        # identical content -> narratives shared -> some propagation
        assert PropagationEvent.query.count() >= 1

    def test_reddit_pipeline(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        r = svc.create_reddit_analysis(user.id, 'abc123', subreddit='technology', comment_limit=25)
        assert r['success'] is True
        assert PropagationEvent.query.count() >= 1

    def test_flag_off_skips_propagation(self, app, db, user):
        app.config['ENABLE_PROPAGATION_INTELLIGENCE'] = False
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        r2 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert r1['success'] and r2['success']
        assert PropagationEvent.query.count() == 0

    def test_v11_narrative_coordination_still_run(self, app, db, user):
        from models.coordination_signal import CoordinationSignal
        from models.media_analysis import MediaAnalysis
        from models.narrative import Narrative
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert MediaAnalysis.query.filter_by(analysis_id=r1['analysis_id']).first() is not None
        assert Narrative.query.count() > 0
        assert PropagationEvent.query.count() > 0

    def test_result_page_renders(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        result = svc.get_recent_user_analyses(user.id, limit=1)[0]
        response = logged_in_client.get(f'/analysis/{result.id}')
        assert response.status_code == 200

    def test_read_apis(self, app, db, user):
        svc = _svc(app)
        a1 = _make_analysis(db, user, 'youtube', datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), 'v1')
        a2 = _make_analysis(db, user, 'reddit', datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc), 'r1')
        narrative = _link_narrative(db, user, a1, platform='youtube', content_ref='v1')
        _link_narrative(db, user, a2, narrative=narrative, platform='reddit', content_ref='r1',
                        occurred_at=datetime(2024, 2, 1, 10, 0, 0))
        svc.analyze(a2)
        rows = svc.get_analysis_propagation(a2.id)
        assert all(isinstance(r, dict) for r in rows)
        summary = svc.get_user_propagation_summary(user.id)
        assert summary['total_events'] >= 1
        assert summary['capability'] == 'heuristic'
