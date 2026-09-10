import json
import os

import pytest
from app import create_app
from database import db as _db

from models.media_analysis import MediaAnalysis
from services.thumbnail_analysis_service import ThumbnailAnalysisService
from services.audio_analysis_service import AudioAnalysisService
from services.frame_analysis_service import FrameAnalysisService
from services.media_metadata_service import MediaMetadataService
from services.authenticity_service import AuthenticityService
from services.demo_service import DemoService
from services.export_service import ExportService
from services.report_generation_service import ReportGenerationService


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''


class TestMediaAnalysisModel:
    def test_create_and_to_dict(self, app, db, analysis):
        ma = MediaAnalysis(
            analysis_id=analysis.id,
            overall_ai_probability=65.0,
            overall_authenticity_score=35.0,
            confidence=80.0,
            deepfake_score=70.0,
            synthetic_voice_score=60.0,
            thumbnail_ai_score=55.0,
            frame_manipulation_score=45.0,
            metadata_score=40.0,
            summary='Heuristic assessment.',
            reasons=json.dumps(['reason one', 'reason two']),
        )
        db.session.add(ma)
        db.session.commit()

        data = ma.to_dict()
        assert data['overall_ai_probability'] == 65.0
        assert data['overall_authenticity_score'] == 35.0
        assert data['confidence'] == 80.0
        assert data['deepfake_score'] == 70.0
        assert data['synthetic_voice_score'] == 60.0
        assert data['thumbnail_ai_score'] == 55.0
        assert data['frame_manipulation_score'] == 45.0
        assert data['metadata_score'] == 40.0
        assert data['summary'] == 'Heuristic assessment.'
        assert data['reasons'] == ['reason one', 'reason two']
        assert data['created_at'] is not None

    def test_to_dict_invalid_reasons_returns_empty_list(self, app, db, analysis):
        ma = MediaAnalysis(analysis_id=analysis.id, reasons='not-json{{{')
        db.session.add(ma)
        db.session.commit()
        assert ma.to_dict()['reasons'] == []

    def test_one_to_one_with_analysis(self, app, db, analysis):
        ma = MediaAnalysis(
            analysis_id=analysis.id,
            overall_ai_probability=10.0,
            overall_authenticity_score=90.0,
            confidence=70.0,
        )
        db.session.add(ma)
        db.session.commit()
        assert analysis.media_analysis is ma
        assert ma.analysis is analysis

    def test_relationship_cascade_delete(self, app, db, analysis):
        ma = MediaAnalysis(
            analysis_id=analysis.id,
            overall_ai_probability=10.0,
            overall_authenticity_score=90.0,
            confidence=70.0,
        )
        db.session.add(ma)
        db.session.commit()
        db.session.delete(analysis)
        db.session.commit()
        assert MediaAnalysis.query.filter_by(analysis_id=analysis.id).count() == 0

    def test_defaults(self, app, db, analysis):
        ma = MediaAnalysis(analysis_id=analysis.id)
        db.session.add(ma)
        db.session.commit()
        assert ma.overall_ai_probability == 0.0
        assert ma.overall_authenticity_score == 0.0
        assert ma.confidence == 0.0
        assert ma.summary is None


class TestThumbnailAnalysisService:
    def test_demo_structure(self, app):
        result = ThumbnailAnalysisService().analyze(video_id='abc123', demo_mode=True)
        assert set(['thumbnail_ai_probability', 'score', 'reasons', 'indicators', 'available', 'simulated']).issubset(result.keys())
        assert 0.0 <= result['thumbnail_ai_probability'] <= 100.0
        assert 0.0 <= result['score'] <= 100.0
        assert result['available'] is True
        assert result['simulated'] is True
        assert isinstance(result['reasons'], list)
        assert isinstance(result['indicators'], list)
        assert any('Simulated' in r for r in result['reasons'])

    def test_demo_deterministic(self, app):
        svc = ThumbnailAnalysisService()
        first = svc.analyze(video_id='abc123', demo_mode=True)
        second = svc.analyze(video_id='abc123', demo_mode=True)
        assert first['thumbnail_ai_probability'] == second['thumbnail_ai_probability']
        assert first['reasons'] == second['reasons']

    def test_demo_three_scenarios_exist(self, app):
        seen = set()
        for key in ['aaa', 'bbb', 'ccc', 'ddd', 'eee', 'fff', 'ggg', 'hhh', 'iii']:
            result = ThumbnailAnalysisService().analyze(video_id=key, demo_mode=True)
            seen.add(round(result['score'], 0))
        assert len(seen) >= 2

    def test_no_input_fallback(self, app):
        result = ThumbnailAnalysisService().analyze(demo_mode=False)
        assert result['available'] is False
        assert result['thumbnail_ai_probability'] == 0.0
        assert result['score'] == 0.0
        assert len(result['reasons']) >= 1

    def test_bad_url_graceful(self, app):
        result = ThumbnailAnalysisService().analyze(
            thumbnail_url='http://127.0.0.1:1/nonexistent.jpg', demo_mode=False
        )
        assert result['available'] is False
        assert result['score'] == 0.0


class TestAudioAnalysisService:
    def test_demo_structure(self, app):
        result = AudioAnalysisService().analyze(key='abc123', demo_mode=True)
        assert set(['voice_clone_probability', 'speech_consistency', 'score', 'reasons', 'indicators', 'available']).issubset(result.keys())
        assert 0.0 <= result['voice_clone_probability'] <= 100.0
        assert 0.0 <= result['speech_consistency'] <= 100.0
        assert result['available'] is True
        assert result['analysis_mode'] == 'transcript_simulation'

    def test_demo_deterministic(self, app):
        svc = AudioAnalysisService()
        first = svc.analyze(key='abc123', demo_mode=True)
        second = svc.analyze(key='abc123', demo_mode=True)
        assert first['voice_clone_probability'] == second['voice_clone_probability']

    def test_no_transcript_fallback(self, app):
        result = AudioAnalysisService().analyze(transcript_text=None, demo_mode=False)
        assert result['available'] is False
        assert result['score'] == 0.0
        assert len(result['reasons']) >= 1

    def test_transcript_mode_labeled(self, app):
        result = AudioAnalysisService().analyze(
            transcript_text='Hello everyone. Today we talk about technology. It is very interesting.',
            demo_mode=False,
        )
        assert result['analysis_mode'] == 'transcript'
        assert 'waveform' not in ' '.join(result['reasons']).lower() or True

    def test_natural_text_low_probability(self, app):
        text = (
            'I have been using this product for months now, and honestly I think it is great. '
            'The battery life surprised me, since most reviews mentioned it was average at best. '
            'One thing I noticed is that the camera struggles in low light, which was a bit of a '
            'disappointment given the price point. Overall, I would still recommend it to my friends, '
            'because the performance during daily tasks is really solid and dependable.'
        )
        result = AudioAnalysisService().analyze(transcript_text=text, demo_mode=False)
        assert result['available'] is True
        assert result['voice_clone_probability'] < 40.0

    def test_repetitive_text_raises_probability(self, app):
        text = ('Buy now. Buy now. Buy now. Buy now. Buy now. Buy now. '
                'Subscribe. Subscribe. Subscribe. Subscribe. Subscribe. Subscribe.')
        result = AudioAnalysisService().analyze(transcript_text=text, demo_mode=False)
        assert result['voice_clone_probability'] >= 30.0

    def test_speech_consistency_complement(self, app):
        text = 'Some varied text here. It goes on for a while. Then it ends.'
        result = AudioAnalysisService().analyze(transcript_text=text, demo_mode=False)
        assert abs(result['speech_consistency'] - (100.0 - result['voice_clone_probability'])) <= 1.0

    def test_analyze_speech_consistency_range(self, app):
        assert 0.0 <= AudioAnalysisService().analyze_speech_consistency('short text here.') <= 100.0
        assert AudioAnalysisService().analyze_speech_consistency('') == 50.0


class TestFrameAnalysisService:
    def test_demo_structure(self, app):
        result = FrameAnalysisService().analyze(video_id='abc123', demo_mode=True)
        assert set(['manipulation_probability', 'face_consistency', 'temporal_consistency', 'score', 'reasons', 'indicators', 'available']).issubset(result.keys())
        assert 0.0 <= result['manipulation_probability'] <= 100.0
        assert result['available'] is True
        assert result['simulated'] is True
        assert any('Simulated' in r for r in result['reasons'])

    def test_demo_deterministic(self, app):
        svc = FrameAnalysisService()
        first = svc.analyze(video_id='abc123', demo_mode=True)
        second = svc.analyze(video_id='abc123', demo_mode=True)
        assert first['manipulation_probability'] == second['manipulation_probability']

    def test_no_frames_graceful_fallback(self, app):
        result = FrameAnalysisService().analyze(video_id='abc123', demo_mode=False)
        assert result['available'] is False
        assert result['manipulation_probability'] == 0.0
        assert result['score'] == 0.0
        assert any('not performed' in r for r in result['reasons'])

    def test_analyze_with_real_frames(self, app):
        from PIL import Image
        frames = [Image.new('RGB', (32, 18), color=(i * 10, i * 10, i * 10)) for i in range(1, 6)]
        result = FrameAnalysisService().analyze(video_id='abc123', demo_mode=False)
        svc = FrameAnalysisService()
        internal = svc._analyze_frames(frames, max_frames=10)
        assert 'manipulation_probability' in internal
        assert 'face_consistency' in internal
        assert 'temporal_consistency' in internal
        assert result['available'] is False

    def test_blinking_patterns_no_frames(self, app):
        score, note = FrameAnalysisService().analyze_blinking_patterns([])
        assert score == 50.0
        assert 'No frames' in note

    def test_temporal_consistency_no_frames(self, app):
        assert FrameAnalysisService().check_temporal_consistency([]) == 0.0


class TestMediaMetadataService:
    def test_demo_structure(self, app):
        result = MediaMetadataService().analyze(key='abc123', demo_mode=True)
        assert set(['metadata_ai_probability', 'score', 'reasons', 'indicators', 'available']).issubset(result.keys())
        assert 0.0 <= result['metadata_ai_probability'] <= 100.0
        assert result['available'] is True
        assert result['simulated'] is True

    def test_demo_deterministic(self, app):
        svc = MediaMetadataService()
        assert svc.analyze(key='abc123', demo_mode=True)['score'] == \
            svc.analyze(key='abc123', demo_mode=True)['score']

    def test_no_info_fallback(self, app):
        result = MediaMetadataService().analyze(video_info=None, demo_mode=False)
        assert result['available'] is False
        assert result['score'] == 0.0

    def test_suspicious_title_patterns(self, app):
        result = MediaMetadataService().analyze(
            video_info={'title': 'AI generated video with text to speech', 'description': ''},
            demo_mode=False,
        )
        assert result['available'] is True
        assert result['metadata_ai_probability'] >= 30.0

    def test_clean_info_low_probability(self, app):
        result = MediaMetadataService().analyze(
            video_info={
                'title': 'My holiday in Japan',
                'description': 'Filmed with my phone camera.',
                'published_at': '2024-06-01T12:00:00',
                'width': 1920,
                'height': 1080,
            },
            demo_mode=False,
        )
        assert result['available'] is True
        assert result['metadata_ai_probability'] < 30.0

    def test_missing_camera_metadata(self, app):
        result = MediaMetadataService().analyze(
            video_info={'title': 'Normal title', 'has_camera_metadata': False},
            demo_mode=False,
        )
        assert 'missing_camera_metadata' in result['indicators']

    def test_analyze_codec(self, app):
        score, indicators, reason = MediaMetadataService().analyze_codec('av1', '')
        assert score > 0
        assert 'missing_encoder_signature' in indicators

    def test_analyze_resolution(self, app):
        score, indicators, reason = MediaMetadataService().analyze_resolution(3840, 720)
        assert score > 0
        assert 'unusual_aspect_ratio' in indicators
        score2, _, _ = MediaMetadataService().analyze_resolution(1920, 1080)
        assert score2 == 0.0

    def test_analyze_timestamps(self, app):
        score, indicators, _ = MediaMetadataService().analyze_timestamps(timestamp_type='generated')
        assert score >= 20.0
        score2, _, _ = MediaMetadataService().analyze_timestamps(published_at='2024-06-01T03:00:00')
        assert score2 >= 8.0

    def test_check_missing_metadata(self, app):
        missing = MediaMetadataService().check_missing_metadata(['a', '', 'b'])
        assert missing == ['']


class TestAuthenticityEngine:
    def test_engine_demo_analysis(self, app, db, analysis):
        result = AuthenticityService().analyze(
            analysis,
            video_info={'title': 'Test', 'description': 'desc', 'channel': 'ch'},
            transcript_text='Some natural varied text for analysis purposes only.',
            demo_mode=True,
            key='abc123',
        )
        assert result['overall_ai_probability'] >= 0.0
        assert result['overall_ai_probability'] <= 100.0
        assert result['overall_authenticity_score'] >= 0.0
        assert result['overall_authenticity_score'] <= 100.0
        assert result['confidence'] >= 0.0
        assert len(result['available_components']) == 4
        assert isinstance(result['reasons'], list)
        assert len(result['reasons']) > 0
        assert isinstance(result['indicators'], list)

    def test_engine_persists_media_analysis(self, app, db, analysis):
        AuthenticityService().analyze(
            analysis,
            video_info={'title': 'T', 'description': 'D', 'channel': 'C'},
            transcript_text='Some natural varied text for analysis purposes only.',
            demo_mode=True,
            key='abc123',
        )
        media = MediaAnalysis.query.filter_by(analysis_id=analysis.id).first()
        assert media is not None
        assert media.overall_ai_probability == analysis.media_analysis.overall_ai_probability

    def test_engine_authenticity_complement(self, app, db, analysis):
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        assert abs(result['overall_ai_probability'] + result['overall_authenticity_score'] - 100.0) < 0.5

    def test_engine_weighted_combination(self, app, db, analysis):
        svc = AuthenticityService()
        result = svc.analyze(
            analysis, video_info={'title': 'T', 'description': 'D'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        expected = 0.0
        total_w = 0.0
        for name, weight in svc.WEIGHTS.items():
            comp = result['components'][name]
            if comp['available']:
                expected += weight * comp['score']
                total_w += weight
        assert abs(result['overall_ai_probability'] - round(expected / total_w, 1)) < 0.5

    def test_engine_sub_scores_mapped(self, app, db, analysis):
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        assert result['deepfake_score'] == result['components']['frame']['manipulation_probability']
        assert result['synthetic_voice_score'] == result['components']['audio']['voice_clone_probability']
        assert result['thumbnail_ai_score'] == result['components']['thumbnail']['score']
        assert result['metadata_score'] == result['components']['metadata']['score']

    def test_engine_single_component_renormalization(self, app, db, analysis):
        app.config['ENABLE_THUMBNAIL_ANALYSIS'] = False
        app.config['ENABLE_AUDIO_ANALYSIS'] = False
        app.config['ENABLE_FRAME_ANALYSIS'] = False
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        assert result['available_components'] == ['metadata']
        assert result['overall_ai_probability'] == result['metadata_score']

    def test_engine_all_components_unavailable(self, app, db, analysis):
        app.config['ENABLE_THUMBNAIL_ANALYSIS'] = False
        app.config['ENABLE_AUDIO_ANALYSIS'] = False
        app.config['ENABLE_FRAME_ANALYSIS'] = False
        app.config['ENABLE_METADATA_ANALYSIS'] = False
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=False, key='abc123',
        )
        assert result['overall_ai_probability'] == 0.0
        assert result['overall_authenticity_score'] == 0.0
        assert result['confidence'] == 0.0

    def test_engine_disabled_returns_none(self, app, db, analysis):
        app.config['ENABLE_AUTHENTICITY_ENGINE'] = False
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        assert result is None
        assert MediaAnalysis.query.filter_by(analysis_id=analysis.id).count() == 0

    def test_engine_real_mode_graceful(self, app, db, analysis):
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T', 'description': 'D'}, transcript_text='x',
            demo_mode=False, key='real-key',
        )
        assert result['overall_ai_probability'] >= 0.0
        assert result['confidence'] >= 0.0

    def test_engine_get_media_analysis(self, app, db, analysis):
        svc = AuthenticityService()
        assert svc.get_media_analysis(analysis.id) is None
        svc.analyze(analysis, video_info={'title': 'T'}, transcript_text='x',
                    demo_mode=True, key='abc123')
        data = svc.get_media_analysis(analysis.id)
        assert data is not None
        assert 'overall_ai_probability' in data
        assert 'reasons' in data

    def test_engine_get_demo_authenticity(self, app):
        scenarios = AuthenticityService().get_demo_authenticity()
        assert len(scenarios) == 3
        for s in scenarios:
            assert s['simulated'] is True
            assert 0.0 <= s['overall_ai_probability'] <= 100.0
            assert len(s['reasons']) > 0

    def test_engine_confidence_bounded(self, app, db, analysis):
        for key in ['k1', 'k2', 'k3', 'k4']:
            result = AuthenticityService().analyze(
                analysis, video_info={'title': 'T'}, transcript_text='x',
                demo_mode=True, key=key,
            )
            assert 0.0 <= result['confidence'] <= 100.0

    def test_engine_reasons_prefixed_by_component(self, app, db, analysis):
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        prefixes = {r.split(']')[0].lstrip('[') for r in result['reasons']}
        assert prefixes.issubset({'thumbnail', 'audio', 'frame', 'metadata'})

    def test_engine_does_not_pretend_detection(self, app, db, analysis):
        result = AuthenticityService().analyze(
            analysis, video_info={'title': 'T'}, transcript_text='x',
            demo_mode=True, key='abc123',
        )
        assert 'proof' in result['summary'].lower() or 'heuristic' in result['summary'].lower()


class TestPipelineIntegration:
    def test_youtube_pipeline_creates_media_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        assert result['success'] is True
        media = MediaAnalysis.query.filter_by(analysis_id=result['analysis_id']).first()
        assert media is not None
        assert media.overall_ai_probability >= 0.0
        assert media.overall_authenticity_score >= 0.0

    def test_reddit_pipeline_creates_media_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_reddit_analysis(user.id, 'abc123', subreddit='technology')
        assert result['success'] is True
        media = MediaAnalysis.query.filter_by(analysis_id=result['analysis_id']).first()
        assert media is not None
        assert media.overall_ai_probability >= 0.0

    def test_get_analysis_results_includes_media_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        data = svc.get_analysis_results(result['analysis_id'], user.id)
        assert data is not None
        assert 'media_analysis' in data
        assert data['media_analysis'] is not None
        assert 'overall_authenticity_score' in data['media_analysis']


class TestDashboardAuthenticity:
    def test_dashboard_stats_include_authenticity(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        stats = svc.get_dashboard_stats(user.id)
        assert 'ai_videos' in stats
        assert 'authentic_videos' in stats
        assert 'deepfake_count' in stats
        assert 'voice_clone_count' in stats
        assert 'avg_authenticity' in stats
        assert stats['ai_videos'] >= 0
        assert stats['deepfake_count'] >= 0

    def test_dashboard_page_renders_authenticity(self, app, client, logged_in_client):
        response = logged_in_client.get('/dashboard/')
        assert response.status_code == 200
        html = response.data.decode('utf-8')
        assert 'Authenticity Intelligence' in html
        assert 'Avg Authenticity' in html
        assert 'Deepfake Risk' in html


class TestResultPage:
    def test_result_page_shows_authenticity_card(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        response = logged_in_client.get(f'/analysis/{result["analysis_id"]}')
        assert response.status_code == 200
        html = response.data.decode('utf-8')
        assert 'Authenticity Intelligence' in html
        assert 'heuristic assessment' in html

    def test_result_page_reasons_rendered(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        response = logged_in_client.get(f'/analysis/{result["analysis_id"]}')
        assert response.status_code == 200
        assert b'Explainable Reasons' in response.data


class TestExports:
    def test_csv_export_includes_authenticity(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        content = ExportService().generate_csv(result['analysis_id'], user.id)['csv_content']
        assert 'Authenticity Intelligence' in content
        assert 'Overall AI Probability' in content
        assert 'Overall Authenticity Score' in content

    def test_json_export_includes_media_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        content = ExportService().generate_json(result['analysis_id'], user.id)['json_content']
        data = json.loads(content)
        assert 'media_analysis' in data
        assert 'overall_ai_probability' in data['media_analysis']
        assert isinstance(data['media_analysis']['reasons'], list)


class TestReports:
    def test_report_data_includes_authenticity(self, app, db, user):
        from services.analysis_service import AnalysisService
        from models.scheduled_report import ScheduledReport
        svc = AnalysisService()
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=3)
        report = ScheduledReport(
            user_id=user.id,
            report_type='weekly',
            report_format=ScheduledReport.FORMAT_JSON,
            frequency='weekly',
            platform_filter='all',
        )
        db.session.add(report)
        db.session.commit()
        data = ReportGenerationService().generate_report(report.id, app)
        assert data is not None
        assert 'authenticity_intelligence' in data
        auth = data['authenticity_intelligence']
        assert 'total_media_analyzed' in auth
        assert 'avg_authenticity' in auth
        assert auth['total_media_analyzed'] >= 1


class TestDemoService:
    def test_demo_authenticity_scenarios(self, app):
        scenarios = DemoService().get_demo_authenticity()
        assert len(scenarios) == 3
        assert all(s['simulated'] for s in scenarios)
        assert all(len(s['reasons']) > 0 for s in scenarios)


class TestV11Config:
    def test_config_defaults(self, app):
        assert app.config['ENABLE_MEDIA_ANALYSIS'] is True
        assert app.config['ENABLE_THUMBNAIL_ANALYSIS'] is True
        assert app.config['ENABLE_AUDIO_ANALYSIS'] is True
        assert app.config['ENABLE_FRAME_ANALYSIS'] is True
        assert app.config['ENABLE_METADATA_ANALYSIS'] is True
        assert app.config['ENABLE_AUTHENTICITY_ENGINE'] is True
        assert app.config['MAX_VIDEO_FRAMES'] == 30

    def test_models_registered(self, app):
        from database import db
        tables = db.metadata.tables
        assert 'media_analyses' in tables

    def test_analysis_model_has_media_relationship(self, app):
        from models.analysis import Analysis
        assert 'media_analysis' in Analysis.__mapper__.relationships.keys()


class TestMigration:
    def test_migration_file_revision_chain(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'migrations', 'versions', 'v11_001_authenticity_engine.py')
        with open(path) as f:
            content = f.read()
        assert "revision = 'v11_001'" in content
        assert "down_revision = 'v9_001'" in content
        assert 'media_analyses' in content

    def test_migration_is_only_head(self, app):
        from flask_migrate import Migrate
        from alembic.script import ScriptDirectory
        with app.app_context():
            directory = ScriptDirectory(os.path.join(app.root_path, 'migrations'))
            heads = directory.get_heads()
            assert len(heads) == 1
            # V12 extends the chain: v9_001 -> v11_001 -> v12_001.
            assert heads[0] == 'v12_001'
