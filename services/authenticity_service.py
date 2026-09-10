import json
import logging
from flask import current_app
from database import db
from models.media_analysis import MediaAnalysis
from services.thumbnail_analysis_service import ThumbnailAnalysisService
from services.audio_analysis_service import AudioAnalysisService
from services.frame_analysis_service import FrameAnalysisService
from services.media_metadata_service import MediaMetadataService

logger = logging.getLogger(__name__)


class AuthenticityService:
    """Master authenticity intelligence engine.

    Combines explainable heuristic signals from the thumbnail, audio (text
    inference), frame, and metadata components into a weighted authenticity
    assessment. All outputs are PROBABILITIES and may indicate possible
    synthetic media; they never constitute proof of AI generation.
    """

    WEIGHTS = {
        'thumbnail': 0.25,
        'audio': 0.25,
        'frame': 0.30,
        'metadata': 0.20,
    }

    def __init__(self):
        self.thumbnail_service = ThumbnailAnalysisService()
        self.audio_service = AudioAnalysisService()
        self.frame_service = FrameAnalysisService()
        self.metadata_service = MediaMetadataService()

    def analyze(self, analysis, video_info=None, transcript_text=None,
                thumbnail_url=None, demo_mode=False, key=''):
        """Run all enabled components, combine, and persist a MediaAnalysis."""
        if not current_app.config.get('ENABLE_AUTHENTICITY_ENGINE', True):
            return None

        enabled_media = current_app.config.get('ENABLE_MEDIA_ANALYSIS', True)
        components = {}

        if enabled_media and current_app.config.get('ENABLE_THUMBNAIL_ANALYSIS', True):
            try:
                components['thumbnail'] = self.thumbnail_service.analyze(
                    video_id=key,
                    title=(video_info or {}).get('title', ''),
                    thumbnail_url=thumbnail_url,
                    demo_mode=demo_mode,
                )
            except Exception as e:
                logger.warning(f'Thumbnail component failed: {e}')
                components['thumbnail'] = self._unavailable('Thumbnail component failed.')

        if enabled_media and current_app.config.get('ENABLE_AUDIO_ANALYSIS', True):
            try:
                segments = None
                components['audio'] = self.audio_service.analyze(
                    transcript_text=transcript_text,
                    transcript_segments=segments,
                    demo_mode=demo_mode,
                    key=key,
                )
            except Exception as e:
                logger.warning(f'Audio component failed: {e}')
                components['audio'] = self._unavailable('Audio component failed.')

        if enabled_media and current_app.config.get('ENABLE_FRAME_ANALYSIS', True):
            try:
                components['frame'] = self.frame_service.analyze(
                    video_id=key,
                    demo_mode=demo_mode,
                )
            except Exception as e:
                logger.warning(f'Frame component failed: {e}')
                components['frame'] = self._unavailable('Frame component failed.')

        if enabled_media and current_app.config.get('ENABLE_METADATA_ANALYSIS', True):
            try:
                components['metadata'] = self.metadata_service.analyze(
                    video_info=self._build_metadata_info(video_info),
                    demo_mode=demo_mode,
                    key=key,
                )
            except Exception as e:
                logger.warning(f'Metadata component failed: {e}')
                components['metadata'] = self._unavailable('Metadata component failed.')

        result = self._compute_authenticity(components)
        self._persist(analysis, result)
        return result

    def _build_metadata_info(self, video_info):
        info = video_info or {}
        return {
            'video_id': info.get('video_id', ''),
            'title': info.get('title', ''),
            'description': info.get('description') or info.get('body', ''),
            'channel': info.get('channel') or info.get('subreddit', ''),
            'published_at': info.get('published_at') or info.get('created_utc'),
            'view_count': info.get('view_count'),
            'like_count': info.get('like_count'),
            'comment_count': info.get('comment_count'),
            'width': info.get('width'),
            'height': info.get('height'),
            'resolution': info.get('resolution'),
            'aspect_ratio': info.get('aspect_ratio'),
            'codec': info.get('codec'),
            'encoder': info.get('encoder'),
            'has_camera_metadata': info.get('has_camera_metadata'),
            'timestamp_type': info.get('timestamp_type'),
        }

    def _unavailable(self, reason):
        return {
            'available': False,
            'score': 0.0,
            'reasons': [reason],
            'indicators': [],
        }

    def _compute_authenticity(self, components):
        available = {name: c for name, c in components.items() if c.get('available')}
        total_weight = sum(self.WEIGHTS[name] for name in available)

        if not available or total_weight <= 0:
            return {
                'overall_ai_probability': 0.0,
                'overall_authenticity_score': 0.0,
                'confidence': 0.0,
                'deepfake_score': 0.0,
                'synthetic_voice_score': 0.0,
                'thumbnail_ai_score': 0.0,
                'frame_manipulation_score': 0.0,
                'metadata_score': 0.0,
                'summary': 'Authenticity assessment could not be computed: no analyzable media components were available.',
                'reasons': ['No analyzable media components were available.'],
                'indicators': [],
                'available_components': list(available.keys()),
                'components': components,
            }

        weighted = 0.0
        for name, component in available.items():
            weighted += self.WEIGHTS[name] * component.get('score', 0.0)
        overall_ai = self._clamp(round(weighted / total_weight, 1))
        authenticity = self._clamp(round(100.0 - overall_ai, 1))

        coverage = len(available) / len(self.WEIGHTS)
        scores = [c.get('score', 0.0) for c in available.values()]
        agreement = 100.0 - (max(scores) - min(scores)) if len(scores) > 1 else 85.0
        confidence = self._clamp(round(0.6 * coverage * 100.0 + 0.4 * agreement, 1))

        deepfake = self._clamp(available.get('frame', {}).get('manipulation_probability', 0.0) or 0.0)
        voice = self._clamp(available.get('audio', {}).get('voice_clone_probability', 0.0) or 0.0)
        thumbnail = self._clamp(available.get('thumbnail', {}).get('score', 0.0) or 0.0)
        frame = self._clamp(available.get('frame', {}).get('score', 0.0) or 0.0)
        metadata = self._clamp(available.get('metadata', {}).get('score', 0.0) or 0.0)

        all_reasons = []
        for name in ('thumbnail', 'audio', 'frame', 'metadata'):
            component = available.get(name)
            if component:
                for reason in component.get('reasons', []):
                    all_reasons.append(f'[{name}] {reason}')

        summary = (
            f'The media shows a possible AI-generated likelihood of {overall_ai:.1f}% '
            f'with an estimated authenticity score of {authenticity:.1f} '
            f'(confidence {confidence:.1f}%). This is a heuristic, explainable '
            f'assessment and does not constitute proof that the media is synthetic.'
        )

        return {
            'overall_ai_probability': overall_ai,
            'overall_authenticity_score': authenticity,
            'confidence': confidence,
            'deepfake_score': deepfake,
            'synthetic_voice_score': voice,
            'thumbnail_ai_score': thumbnail,
            'frame_manipulation_score': frame,
            'metadata_score': metadata,
            'summary': summary,
            'reasons': all_reasons,
            'indicators': list(dict.fromkeys(
                ind for component in available.values() for ind in component.get('indicators', [])
            )),
            'available_components': list(available.keys()),
            'components': components,
        }

    def _persist(self, analysis, result):
        try:
            existing = analysis.media_analysis
            if existing:
                existing.overall_ai_probability = result['overall_ai_probability']
                existing.overall_authenticity_score = result['overall_authenticity_score']
                existing.confidence = result['confidence']
                existing.deepfake_score = result['deepfake_score']
                existing.synthetic_voice_score = result['synthetic_voice_score']
                existing.thumbnail_ai_score = result['thumbnail_ai_score']
                existing.frame_manipulation_score = result['frame_manipulation_score']
                existing.metadata_score = result['metadata_score']
                existing.summary = result['summary']
                existing.reasons = json.dumps(result['reasons'])
                db.session.commit()
                return
            media_analysis = MediaAnalysis(
                analysis_id=analysis.id,
                overall_ai_probability=result['overall_ai_probability'],
                overall_authenticity_score=result['overall_authenticity_score'],
                confidence=result['confidence'],
                deepfake_score=result['deepfake_score'],
                synthetic_voice_score=result['synthetic_voice_score'],
                thumbnail_ai_score=result['thumbnail_ai_score'],
                frame_manipulation_score=result['frame_manipulation_score'],
                metadata_score=result['metadata_score'],
                summary=result['summary'],
                reasons=json.dumps(result['reasons']),
            )
            db.session.add(media_analysis)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f'Failed to persist media analysis: {e}')

    def get_media_analysis(self, analysis_id):
        media = MediaAnalysis.query.filter_by(analysis_id=analysis_id).first()
        return media.to_dict() if media else None

    def get_demo_authenticity(self):
        """Demo scenarios for the demo analysis page. Clearly labeled simulated."""
        return [
            {
                'label': 'ai_generated',
                'overall_ai_probability': 74.0,
                'overall_authenticity_score': 26.0,
                'confidence': 82.0,
                'deepfake_score': 71.0,
                'synthetic_voice_score': 74.0,
                'thumbnail_ai_score': 78.0,
                'frame_manipulation_score': 66.0,
                'metadata_score': 69.0,
                'summary': 'This simulated media shows strong possible AI-generation indicators across multiple components.',
                'reasons': [
                    '[thumbnail] Extreme color uniformity detected across thumbnail regions.',
                    '[audio] Unusually uniform sentence lengths may indicate synthetic pacing.',
                    '[frame] Simulated frame-to-frame inconsistencies detected.',
                    '[metadata] Missing camera metadata consistent with synthetic media.',
                ],
                'indicators': [
                    'extreme_color_uniformity',
                    'uniform_sentence_length',
                    'frame_to_frame_inconsistency',
                    'missing_camera_metadata',
                ],
                'simulated': True,
            },
            {
                'label': 'authentic',
                'overall_ai_probability': 13.0,
                'overall_authenticity_score': 87.0,
                'confidence': 88.0,
                'deepfake_score': 14.0,
                'synthetic_voice_score': 12.0,
                'thumbnail_ai_score': 12.0,
                'frame_manipulation_score': 12.0,
                'metadata_score': 9.0,
                'summary': 'This simulated media shows strong authenticity indicators with no significant synthetic signals.',
                'reasons': [
                    '[thumbnail] Natural color variance observed across thumbnail regions.',
                    '[audio] Natural sentence length variation observed.',
                    '[frame] Simulated frames show consistent facial features.',
                    '[metadata] Metadata fields are consistent with organic content.',
                ],
                'indicators': [],
                'simulated': True,
            },
            {
                'label': 'mixed_uncertain',
                'overall_ai_probability': 49.0,
                'overall_authenticity_score': 51.0,
                'confidence': 64.0,
                'deepfake_score': 48.0,
                'synthetic_voice_score': 45.0,
                'thumbnail_ai_score': 48.0,
                'frame_manipulation_score': 44.0,
                'metadata_score': 42.0,
                'summary': 'This simulated media shows mixed indicators; authenticity cannot be determined with confidence.',
                'reasons': [
                    '[thumbnail] Some regions show unusually uniform color distribution.',
                    '[audio] Some repetitive phrasing patterns detected.',
                    '[frame] Simulated frames show minor temporal inconsistencies.',
                    '[metadata] Some metadata fields are missing or unusual.',
                ],
                'indicators': [
                    'partial_color_uniformity',
                    'some_repetitive_phrasing',
                    'minor_temporal_inconsistency',
                    'partial_missing_metadata',
                ],
                'simulated': True,
            },
        ]

    def _clamp(self, value):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
