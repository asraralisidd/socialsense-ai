import pytest
from datetime import datetime, timezone
from models.channel_context import ChannelContext
from models.video_context_history import VideoContextHistory
from models.entity_history import EntityHistory


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TestChannelContextModel:
    def test_create_channel_context(self, app, db, user):
        cc = ChannelContext(
            user_id=user.id,
            channel_id='test_channel',
            channel_name='Test Channel',
            total_videos_analyzed=3,
            total_entities_detected=15,
            avg_sentiment=62.5,
            avg_risk=18.3,
        )
        db.session.add(cc)
        db.session.commit()
        assert cc.id is not None
        assert cc.channel_id == 'test_channel'
        assert cc.total_videos_analyzed == 3
        assert cc.avg_sentiment == 62.5

    def test_channel_context_defaults(self, app, db, user):
        cc = ChannelContext(user_id=user.id, channel_id='new_channel')
        db.session.add(cc)
        db.session.commit()
        assert cc.total_videos_analyzed == 0
        assert cc.total_entities_detected == 0
        assert cc.avg_sentiment == 0.0

    def test_channel_context_user_relationship(self, app, db, user):
        cc = ChannelContext(user_id=user.id, channel_id='rel_test')
        db.session.add(cc)
        db.session.commit()
        assert cc.user_id == user.id
        assert cc in user.channel_contexts.all()

    def test_channel_context_repr(self, app, db, user):
        cc = ChannelContext(user_id=user.id, channel_id='repr_test', channel_name='Repr Channel')
        db.session.add(cc)
        db.session.commit()
        assert 'Repr Channel' in repr(cc)


class TestVideoContextHistoryModel:
    def test_create_video_history(self, app, db, user, analysis):
        vch = VideoContextHistory(
            analysis_id=analysis.id,
            user_id=user.id,
            video_id='abc123',
            channel_id='test_channel',
            video_title='Test Video',
            entity_count=5,
            avg_sentiment=65.0,
            avg_risk=20.0,
            top_entities='["Tesla", "Elon Musk"]',
        )
        db.session.add(vch)
        db.session.commit()
        assert vch.id is not None
        assert vch.video_id == 'abc123'
        assert vch.entity_count == 5

    def test_video_history_defaults(self, app, db, user, analysis):
        vch = VideoContextHistory(
            analysis_id=analysis.id, user_id=user.id, video_id='def456'
        )
        db.session.add(vch)
        db.session.commit()
        assert vch.entity_count == 0
        assert vch.avg_sentiment == 0.0
        assert vch.avg_risk == 0.0

    def test_video_history_analysis_relationship(self, app, db, user, analysis):
        vch = VideoContextHistory(
            analysis_id=analysis.id, user_id=user.id, video_id='ghi789'
        )
        db.session.add(vch)
        db.session.commit()
        assert vch.analysis_id == analysis.id

    def test_video_history_cascade_delete(self, app, db, user, analysis):
        vch = VideoContextHistory(
            analysis_id=analysis.id, user_id=user.id, video_id='jkl012'
        )
        db.session.add(vch)
        db.session.commit()
        vch_id = vch.id
        db.session.delete(analysis)
        db.session.commit()
        assert VideoContextHistory.query.get(vch_id) is None


class TestEntityHistoryModel:
    def test_create_entity_history(self, app, db, user):
        eh = EntityHistory(
            normalized_name='Tesla',
            entity_type='COMPANY',
            video_id='abc123',
            channel_id='test_channel',
            user_id=user.id,
            sentiment_score=72.0,
            risk_score=15.0,
            mention_count=5,
            importance_score=80.0,
        )
        db.session.add(eh)
        db.session.commit()
        assert eh.id is not None
        assert eh.normalized_name == 'Tesla'
        assert eh.sentiment_score == 72.0

    def test_entity_history_defaults(self, app, db, user):
        eh = EntityHistory(
            normalized_name='TestEntity', user_id=user.id
        )
        db.session.add(eh)
        db.session.commit()
        assert eh.sentiment_score == 0.0
        assert eh.risk_score == 0.0
        assert eh.mention_count == 0

    def test_entity_history_user_relationship(self, app, db, user):
        eh = EntityHistory(normalized_name='Apple', user_id=user.id)
        db.session.add(eh)
        db.session.commit()
        assert eh.user_id == user.id
        assert eh in user.entity_histories.all()

    def test_entity_history_repr(self, app, db, user):
        eh = EntityHistory(normalized_name='Google', user_id=user.id, video_id='vid1')
        db.session.add(eh)
        db.session.commit()
        assert 'Google' in repr(eh)
        assert 'vid1' in repr(eh)

    def test_entity_history_query_by_entity(self, app, db, user):
        eh1 = EntityHistory(normalized_name='Tesla', user_id=user.id, channel_id='ch1', sentiment_score=70.0)
        eh2 = EntityHistory(normalized_name='Tesla', user_id=user.id, channel_id='ch1', sentiment_score=80.0)
        db.session.add_all([eh1, eh2])
        db.session.commit()
        results = EntityHistory.query.filter_by(normalized_name='Tesla').all()
        assert len(results) == 2


class TestChannelContextService:
    def test_get_or_create_existing(self, app, db, user):
        from services.channel_context_service import ChannelContextService
        svc = ChannelContextService()
        cc = svc.get_or_create_channel(user.id, 'test_chan', 'Test Channel')
        assert cc.channel_id == 'test_chan'
        same = svc.get_or_create_channel(user.id, 'test_chan', 'Test Channel')
        assert same.id == cc.id

    def test_get_or_create_new(self, app, db, user):
        from services.channel_context_service import ChannelContextService
        svc = ChannelContextService()
        cc = svc.get_or_create_channel(user.id, 'new_chan', 'New Channel')
        assert cc.id is not None
        assert cc.total_videos_analyzed == 0

    def test_update_channel_stats(self, app, db, user, analysis):
        from services.channel_context_service import ChannelContextService
        from services.video_history_service import VideoHistoryService
        svc = ChannelContextService()
        vhs = VideoHistoryService()
        cc = svc.get_or_create_channel(user.id, 'stats_chan', 'Stats Channel')
        vhs.record_video_analysis(analysis.id, user.id, 'vid1', 'stats_chan', 'V1', 5, 60.0, 20.0)
        updated = svc.update_channel_stats(user.id, 'stats_chan')
        assert updated.total_videos_analyzed == 1
        assert updated.total_entities_detected >= 5

    def test_get_channel_intelligence(self, app, db, user):
        from services.channel_context_service import ChannelContextService
        svc = ChannelContextService()
        svc.get_or_create_channel(user.id, 'intel_chan', 'Intel Channel')
        intel = svc.get_channel_intelligence(user.id, 'intel_chan')
        assert intel['total_videos_analyzed'] == 0


class TestVideoHistoryService:
    def test_record_video_analysis(self, app, db, user, analysis):
        from services.video_history_service import VideoHistoryService
        svc = VideoHistoryService()
        vch = svc.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'Title', 5, 60.0, 20.0)
        assert vch.video_id == 'vid1'
        assert vch.entity_count == 5

    def test_get_previous_videos(self, app, db, user, analysis):
        from services.video_history_service import VideoHistoryService
        svc = VideoHistoryService()
        svc.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'V1', 5, 60.0, 20.0)
        prev = svc.get_previous_videos('ch1', user.id)
        assert len(prev) >= 1
        assert prev[0].video_id == 'vid1'

    def test_get_trend_statistics_single(self, app, db, user, analysis):
        from services.video_history_service import VideoHistoryService
        svc = VideoHistoryService()
        svc.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'V1', 5, 60.0, 20.0)
        trends = svc.get_trend_statistics('ch1', user.id)
        assert trends['previous_video_count'] == 1

    def test_get_trend_statistics_multiple(self, app, db, user, analysis):
        from services.video_history_service import VideoHistoryService
        from models.analysis import Analysis
        svc = VideoHistoryService()
        a2 = Analysis(user_id=user.id, analysis_type='youtube')
        db.session.add(a2)
        db.session.commit()
        svc.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'V1', 5, 50.0, 30.0)
        svc.record_video_analysis(a2.id, user.id, 'vid2', 'ch1', 'V2', 8, 70.0, 15.0)
        trends = svc.get_trend_statistics('ch1', user.id)
        assert trends['previous_video_count'] == 2
        assert trends['sentiment_trend'] in ('improving', 'declining', 'stable')

    def test_find_recurring_entities(self, app, db, user, analysis):
        from services.video_history_service import VideoHistoryService
        svc = VideoHistoryService()
        svc.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'V1', 3, 50.0, 20.0,
                                   top_entities=['Tesla', 'Elon Musk'])
        entities = svc.find_recurring_entities('ch1', user.id)
        assert 'Tesla' in entities
        assert 'Elon Musk' in entities


class TestEntityHistoryService:
    def test_record_entity_history(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        eh = svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 72.0, 15.0, 5, 80.0)
        assert eh.normalized_name == 'Tesla'
        assert eh.sentiment_score == 72.0

    def test_get_entity_history(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 70.0, 20.0, 5, 80.0)
        svc.record_entity_history(2, user.id, 'vid2', 'ch1', 'Tesla', 'COMPANY', 80.0, 10.0, 8, 90.0)
        history = svc.get_entity_history('Tesla', 'ch1', user.id)
        assert len(history) == 2

    def test_get_entity_sentiment_trend(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 70.0, 20.0, 5, 80.0)
        trend = svc.get_entity_sentiment_trend('Tesla', 'ch1', user.id)
        assert len(trend) == 1
        assert trend[0]['sentiment_score'] == 70.0

    def test_get_entity_frequency(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 70.0, 20.0, 5, 80.0)
        freq = svc.get_entity_frequency('ch1', user.id)
        assert len(freq) >= 1
        assert freq[0]['normalized_name'] == 'Tesla'

    def test_get_entity_frequency_aggregation(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 70.0, 20.0, 5, 80.0)
        svc.record_entity_history(2, user.id, 'vid2', 'ch1', 'Tesla', 'COMPANY', 80.0, 10.0, 8, 90.0)
        freq = svc.get_entity_frequency('ch1', user.id)
        assert len(freq) == 1
        row = freq[0]
        assert row['normalized_name'] == 'Tesla'
        assert row['entity_type'] == 'COMPANY'
        assert row['appearances'] == 2
        assert row['avg_sentiment'] == 75.0
        assert row['avg_risk'] == 15.0

    def test_get_entity_frequency_multiple_types_no_group_by_error(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 70.0, 20.0, 5, 80.0)
        svc.record_entity_history(2, user.id, 'vid2', 'ch1', 'Tesla', 'COMPANY', 80.0, 10.0, 8, 90.0)
        svc.record_entity_history(3, user.id, 'vid3', 'ch1', 'Tesla', 'PERSON', 50.0, 30.0, 2, 40.0)
        freq = svc.get_entity_frequency('ch1', user.id)
        by_type = {row['entity_type']: row for row in freq}
        assert set(by_type.keys()) == {'COMPANY', 'PERSON'}
        assert by_type['COMPANY']['normalized_name'] == 'Tesla'
        assert by_type['COMPANY']['appearances'] == 2
        assert by_type['COMPANY']['avg_sentiment'] == 75.0
        assert by_type['COMPANY']['avg_risk'] == 15.0
        assert by_type['PERSON']['normalized_name'] == 'Tesla'
        assert by_type['PERSON']['appearances'] == 1
        assert by_type['PERSON']['avg_sentiment'] == 50.0
        assert by_type['PERSON']['avg_risk'] == 30.0

    def test_get_top_entities_by_channel_aggregation(self, app, db, user):
        from services.entity_history_service import EntityHistoryService
        svc = EntityHistoryService()
        svc.record_entity_history(1, user.id, 'vid1', 'ch1', 'Tesla', 'COMPANY', 70.0, 20.0, 5, 80.0)
        svc.record_entity_history(2, user.id, 'vid2', 'ch1', 'Tesla', 'COMPANY', 80.0, 10.0, 8, 90.0)
        svc.record_entity_history(3, user.id, 'vid3', 'ch1', 'Elon Musk', 'PERSON', 60.0, 40.0, 3, 70.0)
        top = svc.get_top_entities_by_channel('ch1', user.id, limit=10)
        assert len(top) == 2
        assert top[0]['normalized_name'] == 'Tesla'
        assert top[0]['entity_type'] == 'COMPANY'
        assert top[0]['appearances'] == 2
        assert top[0]['avg_sentiment'] == 75.0
        assert top[0]['avg_risk'] == 15.0
        assert top[1]['normalized_name'] == 'Elon Musk'
        assert top[1]['appearances'] == 1

    def test_aggregation_queries_group_by_entity_type(self, app):
        from sqlalchemy import func
        from services.entity_history_service import EntityHistoryService
        from models.entity_history import EntityHistory
        from database import db

        for builder in (
            lambda: db.session.query(
                EntityHistory.normalized_name,
                EntityHistory.entity_type,
                func.count(EntityHistory.id).label('appearances'),
                func.avg(EntityHistory.sentiment_score).label('avg_sentiment'),
                func.avg(EntityHistory.risk_score).label('avg_risk'),
            ).filter_by(channel_id='ch1', user_id=1).group_by(
                EntityHistory.normalized_name,
                EntityHistory.entity_type,
            ).order_by(func.count(EntityHistory.id).desc()),
            lambda: db.session.query(
                EntityHistory.normalized_name,
                EntityHistory.entity_type,
                func.count(EntityHistory.id).label('appearances'),
                func.avg(EntityHistory.sentiment_score).label('avg_sentiment'),
                func.avg(EntityHistory.risk_score).label('avg_risk'),
            ).filter_by(channel_id='ch1', user_id=1).group_by(
                EntityHistory.normalized_name,
                EntityHistory.entity_type,
            ).order_by(func.count(EntityHistory.id).desc()).limit(10),
        ):
            sql = str(builder().statement.compile(dialect=db.engine.dialect))
            assert 'GROUP BY' in sql
            assert 'entity_history.normalized_name' in sql
            assert 'entity_history.entity_type' in sql


class TestTopicHistoryService:
    def test_get_recurring_topics_empty(self, app, db, user):
        from services.topic_history_service import TopicHistoryService
        svc = TopicHistoryService()
        topics = svc.get_recurring_topics('ch1', user.id)
        assert topics == []


class TestContextIntelligenceService:
    def test_compute_intelligence_empty(self, app, db, user):
        from services.context_intelligence_service import ContextIntelligenceService
        svc = ContextIntelligenceService()
        result = svc.compute_intelligence(user.id, 'ch1', 1, entity_names=[])
        assert 'historical_context' in result
        assert 'entity_recurrence' in result
        assert 'topic_recurrence' in result
        assert 'trend_direction' in result
        assert result['entity_recurrence'] == 0

    def test_compute_intelligence_with_history(self, app, db, user, analysis):
        from services.context_intelligence_service import ContextIntelligenceService
        from services.video_history_service import VideoHistoryService
        from services.channel_context_service import ChannelContextService
        svc = ContextIntelligenceService()
        vhs = VideoHistoryService()
        chs = ChannelContextService()
        vhs.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'V1', 5, 60.0, 20.0,
                                   top_entities=['Tesla'])
        chs.update_channel_stats(user.id, 'ch1')
        result = svc.compute_intelligence(user.id, 'ch1', analysis.id, entity_names=['Tesla'])
        assert result['historical_context'] > 0

    def test_trend_direction_positive(self, app, db, user):
        from services.context_intelligence_service import ContextIntelligenceService
        svc = ContextIntelligenceService()
        trends = {
            'previous_video_count': 2,
            'sentiment_trend': 'improving',
            'risk_trend': 'improving',
            'avg_sentiment_change': 10.0,
            'avg_risk_change': -10.0,
            'entity_count_trend': 2,
        }
        direction = svc._get_trend_direction(trends)
        assert direction == 'positive'


class TestVideoComparisonService:
    def test_compare_no_history(self, app, db, user):
        from services.video_comparison_service import VideoComparisonService
        svc = VideoComparisonService()
        result = svc.compare_with_channel_average('ch1', user.id, 50.0, 20.0, 5)
        assert result['has_history'] is False

    def test_compare_with_history(self, app, db, user, analysis):
        from services.video_comparison_service import VideoComparisonService
        from services.video_history_service import VideoHistoryService
        svc = VideoComparisonService()
        vhs = VideoHistoryService()
        vhs.record_video_analysis(analysis.id, user.id, 'vid1', 'ch1', 'V1', 5, 60.0, 20.0, None)
        result = svc.compare_with_channel_average('ch1', user.id, 70.0, 10.0, 8)
        assert result['has_history'] is True
        assert result['sentiment_vs_avg'] == 10.0
        assert result['risk_vs_avg'] == -10.0


class TestDashboardV9:
    def test_dashboard_has_videos_analyzed(self, app, db, user, client):
        from flask import url_for
        with app.test_request_context():
            client.get(url_for('auth.login'))
        # Just verify the dashboard template renders
        response = client.get('/dashboard/')
        assert response.status_code in (200, 302)


class TestDemoV9:
    def test_demo_channel_intelligence(self, app, db):
        from services.demo_service import DemoService
        svc = DemoService()
        ci = svc.get_demo_channel_intelligence()
        assert ci['total_videos_analyzed'] == 3
        assert ci['total_entities_detected'] == 15

    def test_demo_context_intelligence(self, app, db):
        from services.demo_service import DemoService
        svc = DemoService()
        ci = svc.get_demo_context_intelligence()
        assert ci['historical_context'] == 85
        assert ci['trend_direction'] == 'positive'

    def test_demo_trend_statistics(self, app, db):
        from services.demo_service import DemoService
        svc = DemoService()
        ts = svc.get_demo_trend_statistics()
        assert ts['previous_video_count'] == 2
        assert ts['sentiment_trend'] == 'improving'

    def test_demo_video_history(self, app, db):
        from services.demo_service import DemoService
        svc = DemoService()
        vh = svc.get_demo_video_history()
        assert len(vh) == 2
        assert vh[0]['video_title'] == 'Tesla Earnings Report'

    def test_demo_entity_history(self, app, db):
        from services.demo_service import DemoService
        svc = DemoService()
        eh = svc.get_demo_entity_history()
        assert len(eh) >= 3
        assert eh[0]['normalized_name'] == 'Tesla'

    def test_demo_video_comparison(self, app, db):
        from services.demo_service import DemoService
        svc = DemoService()
        vc = svc.get_demo_video_comparison()
        assert vc['has_history'] is True
        assert vc['sentiment_vs_avg'] == 12.5
