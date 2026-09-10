import logging
import zlib
from flask import current_app

logger = logging.getLogger(__name__)


class FrameAnalysisService:
    """Heuristic video frame analysis for possible manipulation indicators.

    IMPORTANT: SocialSense AI does not download or decode video files. When
    actual video frame data is unavailable (the normal case), this service
    gracefully reports the limitation instead of pretending frames were
    analyzed. Demo mode returns clearly-labeled simulated results.
    """

    DEMO_SCENARIOS = [
        {
            'label': 'manipulated',
            'manipulation_probability': 71.0,
            'face_consistency': 45.0,
            'temporal_consistency': 52.0,
            'score': 66.0,
            'reasons': [
                'Simulated frame-to-frame inconsistencies detected.',
                'Simulated facial feature variation across sampled frames.',
                'Possible temporal discontinuity in motion patterns.',
            ],
            'indicators': [
                'frame_to_frame_inconsistency',
                'facial_feature_variation',
                'temporal_discontinuity',
            ],
        },
        {
            'label': 'authentic',
            'manipulation_probability': 14.0,
            'face_consistency': 90.0,
            'temporal_consistency': 92.0,
            'score': 12.0,
            'reasons': [
                'Simulated frames show consistent facial features.',
                'Simulated motion patterns are temporally coherent.',
                'No simulated manipulation indicators detected.',
            ],
            'indicators': [],
        },
        {
            'label': 'mixed_uncertain',
            'manipulation_probability': 48.0,
            'face_consistency': 66.0,
            'temporal_consistency': 61.0,
            'score': 44.0,
            'reasons': [
                'Simulated frames show minor temporal inconsistencies.',
                'Overall facial consistency is moderate.',
            ],
            'indicators': [
                'minor_temporal_inconsistency',
            ],
        },
    ]

    def analyze(self, video_id='', video_url=None, demo_mode=False, max_frames=None):
        """Analyze video frames for manipulation indicators.

        Returns:
            dict: manipulation_probability, face_consistency, temporal_consistency,
                  score, reasons, indicators, available
        """
        if demo_mode:
            return self._demo_analysis(video_id)

        if max_frames is None:
            max_frames = current_app.config.get('MAX_VIDEO_FRAMES', 30) if current_app else 30

        frames = self._extract_frames(video_url or video_id)
        if not frames:
            return {
                'manipulation_probability': 0.0,
                'face_consistency': 0.0,
                'temporal_consistency': 0.0,
                'score': 0.0,
                'reasons': [
                    'No video frame data available. Frame-level manipulation analysis '
                    'was not performed - the video stream is not downloaded or decoded.'
                ],
                'indicators': [],
                'available': False,
            }

        return self._analyze_frames(frames, max_frames)

    def _demo_analysis(self, video_id=''):
        if video_id is None:
            video_id = ''
        idx = zlib.crc32(str(video_id).encode('utf-8')) % len(self.DEMO_SCENARIOS)
        scenario = self.DEMO_SCENARIOS[idx]
        result = {
            'manipulation_probability': scenario['manipulation_probability'],
            'face_consistency': scenario['face_consistency'],
            'temporal_consistency': scenario['temporal_consistency'],
            'score': scenario['score'],
            'reasons': list(scenario['reasons']),
            'indicators': list(scenario['indicators']),
            'available': True,
            'simulated': True,
        }
        result['reasons'].append(
            'Simulated demo analysis - no real video frames were inspected.'
        )
        return result

    def _extract_frames(self, video_url_or_id):
        """Attempt to obtain frames. Returns [] when video data is unavailable."""
        try:
            if video_url_or_id and str(video_url_or_id).startswith(('http://', 'https://')):
                logger.info('Video download/decode is not supported; skipping frame extraction.')
                return []
        except Exception as e:
            logger.warning(f'Frame extraction setup failed: {e}')
        return []

    def _analyze_frames(self, frames, max_frames=30):
        """Heuristic analysis of an in-memory frame list (list of PIL Images)."""
        sampled = frames[:max_frames]
        if len(sampled) < 2:
            return {
                'manipulation_probability': 0.0,
                'face_consistency': 0.0,
                'temporal_consistency': 0.0,
                'score': 0.0,
                'reasons': ['Insufficient frames available for analysis.'],
                'indicators': [],
                'available': False,
            }

        diffs = []
        for i in range(1, len(sampled)):
            try:
                from PIL import ImageChops
                a = sampled[i - 1].convert('L')
                b = sampled[i].convert('L')
                diff = ImageChops.difference(a, b)
                hist = diff.histogram()
                total = sum(hist)
                if total:
                    diffs.append(sum(idx * count for idx, count in enumerate(hist)) / total)
            except Exception:
                continue

        if not diffs:
            return {
                'manipulation_probability': 0.0,
                'face_consistency': 0.0,
                'temporal_consistency': 0.0,
                'score': 0.0,
                'reasons': ['Frames could not be processed for comparison.'],
                'indicators': [],
                'available': False,
            }

        mean_diff = sum(diffs) / len(diffs)
        irregularity = self._relative_variance(diffs)
        face_consistency = self._estimate_face_consistency(sampled)
        temporal_consistency = round(max(0.0, min(100.0, 100.0 - mean_diff * 2.0)), 1)

        indicators = []
        reasons = []
        probability = 0.0

        if irregularity > 0.6:
            probability += 35.0
            indicators.append('frame_to_frame_inconsistency')
            reasons.append('Irregular frame-to-frame differences detected.')
        if face_consistency < 60.0:
            probability += 25.0
            indicators.append('facial_feature_variation')
            reasons.append('Facial feature consistency is low across sampled frames.')
        if temporal_consistency < 55.0:
            probability += 30.0
            indicators.append('temporal_discontinuity')
            reasons.append('Possible temporal discontinuity in motion patterns.')

        if not indicators:
            reasons.append('No strong manipulation indicators detected in sampled frames.')
            probability = 10.0

        probability = round(min(probability, 100.0), 1)
        return {
            'manipulation_probability': probability,
            'face_consistency': face_consistency,
            'temporal_consistency': temporal_consistency,
            'score': probability,
            'reasons': reasons,
            'indicators': indicators,
            'available': True,
        }

    def analyze_blinking_patterns(self, frames):
        """Heuristic blinking-pattern check. Returns (naturalness_score, note)."""
        if not frames:
            return 50.0, 'No frames available for blinking analysis.'
        return 50.0, 'Blinking analysis requires real face-tracking data; not performed.'

    def check_temporal_consistency(self, frames):
        """Heuristic temporal consistency check. Returns 0-100 score."""
        if not frames:
            return 0.0
        from PIL import ImageChops
        diffs = []
        for i in range(1, len(frames)):
            a = frames[i - 1].convert('L')
            b = frames[i].convert('L')
            hist = ImageChops.difference(a, b).histogram()
            total = sum(hist)
            if total:
                diffs.append(sum(j * c for j, c in enumerate(hist)) / total)
        if not diffs:
            return 0.0
        mean_diff = sum(diffs) / len(diffs)
        return round(max(0.0, min(100.0, 100.0 - mean_diff * 2.0)), 1)

    def _estimate_face_consistency(self, frames):
        try:
            from PIL import ImageStat
            stats = []
            for frame in frames:
                stat = ImageStat.Stat(frame.convert('L'))
                stats.append(stat.mean[0])
            if not stats:
                return 50.0
            variance = self._relative_variance(stats)
            return round(max(0.0, min(100.0, 100.0 - variance * 150.0)), 1)
        except Exception:
            return 50.0

    def _relative_variance(self, values):
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        if mean <= 0:
            return 0.0
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return (variance ** 0.5) / mean
