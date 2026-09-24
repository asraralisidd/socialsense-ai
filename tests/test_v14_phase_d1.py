"""V14 Phase D1 - Dashboard/history bounds tests.

Fixed query budgets, SQL-level limits, user isolation, output parity
against independently computed expectations, and empty-history behavior.
"""
import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.entity import Entity
from models.entity_context import EntityContext
from models.media_analysis import MediaAnalysis
from repositories.analysis_repository import AnalysisRepository
from services.analysis_service import AnalysisService


def _comment(analysis_id, **scores):
    base = dict(sentiment_score=50.0, toxicity_score=5.0, spam_score=10.0,
                duplicate_score=0.0, ai_like_score=0.0, bot_score=0.0,
                risk_score=20.0)
    base.update(scores)
    c = CommentResult(analysis_id=analysis_id, comment_text='stored text',
                      author='a1', **base)
    db.session.add(c)
    db.session.flush()
    return c


def _seed_pair(user_id):
    """Two analyses with hand-computable aggregates. Returns (a1, a2)."""
    a1 = Analysis(user_id=user_id, analysis_type='youtube')
    a1.critical_comments_count = 3
    db.session.add(a1)
    db.session.flush()
    db.session.add(YouTubeAnalysis(analysis_id=a1.id, video_id='vid1',
                                   video_title='T1', channel_name='C',
                                   is_demo=True))
    _comment(a1.id, sentiment_score=60.0, spam_score=0.0, risk_score=10.0)
    _comment(a1.id, sentiment_score=80.0, spam_score=0.0, risk_score=30.0)
    e1 = Entity(analysis_id=a1.id, name='Ann', normalized_name='ann',
                entity_type='PERSON', frequency=2, importance_score=50.0)
    e2 = Entity(analysis_id=a1.id, name='Zed', normalized_name='zed',
                entity_type='WEIRD', frequency=1, importance_score=10.0)
    db.session.add_all([e1, e2])
    db.session.flush()
    c0 = CommentResult.query.filter_by(analysis_id=a1.id).first()
    for risk in (80.0, 60.0, 30.0, 10.0, None):
        db.session.add(EntityContext(entity_id=e1.id,
                                     comment_result_id=c0.id,
                                     entity_risk_score=risk))
    db.session.add(MediaAnalysis(analysis_id=a1.id,
                                 overall_ai_probability=70.0,
                                 overall_authenticity_score=50.0,
                                 deepfake_score=65.0,
                                 synthetic_voice_score=10.0))
    a2 = Analysis(user_id=user_id, analysis_type='reddit')
    db.session.add(a2)
    db.session.flush()
    _comment(a2.id, sentiment_score=40.0, spam_score=100.0, risk_score=90.0)
    db.session.commit()
    return a1, a2


def _query_count():
    from sqlalchemy import event
    state = {'n': 0}

    def _before(conn, cursor, statement, params, context, executemany):
        state['n'] += 1

    return state, _before


class TestDashboardBounds:
    def test_query_count_fixed(self, app, db, user):
        from models.user import User as _U  # noqa
        for i in range(8):
            a = Analysis(user_id=user.id, analysis_type='youtube')
            db.session.add(a)
            db.session.flush()
            _comment(a.id)
        db.session.commit()
        state, hook = _query_count()
        from sqlalchemy import event
        event.listen(db.engine, 'before_cursor_execute', hook)
        try:
            AnalysisService().get_dashboard_stats(user.id)
        finally:
            event.remove(db.engine, 'before_cursor_execute', hook)
        assert state['n'] <= 12, state['n']

    def test_query_count_independent_of_history_size(self, app, db, user):
        from sqlalchemy import event
        counts = []
        for total in (2, 6):
            for i in range(total - Analysis.query.filter_by(
                    user_id=user.id).count()):
                a = Analysis(user_id=user.id, analysis_type='youtube')
                db.session.add(a)
            db.session.commit()
            state, hook = _query_count()
            event.listen(db.engine, 'before_cursor_execute', hook)
            try:
                AnalysisService().get_dashboard_stats(user.id)
            finally:
                event.remove(db.engine, 'before_cursor_execute', hook)
            counts.append(state['n'])
        assert counts[0] == counts[1], counts

    def test_output_parity(self, app, db, user):
        _seed_pair(user.id)
        stats = AnalysisService().get_dashboard_stats(user.id)
        assert stats['total_analyses'] == 2
        assert stats['total_comments'] == 3
        assert stats['critical_comments'] == 3
        # means-of-per-analysis-means: spam (0 + 100)/2, sentiment (70+40)/2
        assert stats['avg_spam'] == 50.0
        assert stats['avg_sentiment'] == 55.0
        assert stats['avg_toxicity'] == 5.0
        assert stats['avg_risk'] == pytest.approx((20.0 + 90.0) / 2)
        assert stats['community_health'] == 'High'  # avg risk 55
        assert stats['transcript_count'] == 0
        assert stats['entity_count'] == 2
        assert stats['entity_type_counts']['PERSON'] == 2
        assert stats['entity_type_counts']['OTHER'] == 1
        assert stats['avg_entity_risk'] == pytest.approx((80 + 60 + 30 + 10 + 0) / 5)
        assert stats['entity_risk_critical'] == 1
        assert stats['entity_risk_high'] == 1
        assert stats['entity_risk_medium'] == 1
        assert stats['entity_risk_low'] == 2  # 10.0 and None->0.0
        assert stats['entity_risk_count'] == 5
        assert stats['ai_videos'] == 1
        assert stats['authentic_videos'] == 0  # elif: ai branch won
        assert stats['deepfake_count'] == 1
        assert stats['voice_clone_count'] == 0
        assert stats['avg_authenticity'] == 50.0
        assert stats['authenticity_count'] == 1

    def test_platform_filter(self, app, db, user):
        _seed_pair(user.id)
        stats = AnalysisService().get_dashboard_stats(user.id, platform='youtube')
        assert stats['total_analyses'] == 1
        assert stats['avg_spam'] == 0.0

    def test_empty_history(self, app, db, user):
        stats = AnalysisService().get_dashboard_stats(user.id)
        assert stats['total_analyses'] == 0
        assert stats['total_comments'] == 0
        assert stats['avg_risk'] == 0.0
        assert stats['community_health'] == 'Low'
        assert stats['entity_type_counts'] == {'PERSON': 0, 'COMPANY': 0,
                                               'PRODUCT': 0, 'LOCATION': 0,
                                               'OTHER': 0}
        assert stats['avg_entity_risk'] == 0.0
        assert stats['avg_authenticity'] == 0.0

    def test_user_isolation(self, app, db, user):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='d1other', email='d1other@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        _seed_pair(other.id)
        stats = AnalysisService().get_dashboard_stats(user.id)
        assert stats['total_analyses'] == 0
        stats = AnalysisService().get_dashboard_stats(other.id)
        assert stats['total_analyses'] == 2


class TestHistoryBounds:
    def test_limit_enforced_in_sql(self, app, db, user):
        for _ in range(5):
            db.session.add(Analysis(user_id=user.id, analysis_type='youtube'))
        db.session.commit()
        rows = AnalysisRepository().get_by_user_id(user.id, limit=2)
        assert len(rows) == 2
        newest = Analysis.query.filter_by(user_id=user.id).order_by(
            Analysis.created_at.desc()).first()
        assert rows[0].id == newest.id

    def test_history_query_count_fixed(self, app, db, user):
        for _ in range(6):
            a = Analysis(user_id=user.id, analysis_type='youtube')
            db.session.add(a)
            db.session.flush()
            _comment(a.id)
        db.session.commit()
        from sqlalchemy import event
        state, hook = _query_count()
        event.listen(db.engine, 'before_cursor_execute', hook)
        try:
            rows = AnalysisService().get_all_user_analyses_with_data(user.id)
        finally:
            event.remove(db.engine, 'before_cursor_execute', hook)
        assert len(rows) == 6
        assert state['n'] <= 8, state['n']

    def test_history_output_shape(self, app, db, user):
        a1, _ = _seed_pair(user.id)
        rows = AnalysisService().get_all_user_analyses_with_data(user.id)
        assert len(rows) == 2
        first = [r for r in rows if r['id'] == a1.id][0]
        assert first['title'] == 'T1'
        assert first['identifier'] == 'vid1'
        assert first['platform'] == 'youtube'
        assert first['comment_count'] == 2
        assert first['avg_risk'] == 20.0
        assert first['is_demo'] is True
        assert first['has_transcript'] is False
        assert first['entity_count'] == 2
        assert set(first) == {'id', 'title', 'identifier', 'platform',
                              'comment_count', 'avg_risk', 'is_demo',
                              'has_transcript', 'entity_count', 'created_at'}

    def test_history_limit_and_empty(self, app, db, user):
        for _ in range(3):
            db.session.add(Analysis(user_id=user.id, analysis_type='youtube'))
        db.session.commit()
        assert len(AnalysisService().get_all_user_analyses_with_data(
            user.id, limit=2)) == 2
        assert AnalysisService().get_all_user_analyses_with_data(999999) == []
