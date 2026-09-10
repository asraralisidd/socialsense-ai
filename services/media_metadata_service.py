import logging
import re
import zlib
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MediaMetadataService:
    """Heuristic metadata analysis for possible AI-generated media indicators.

    Checks available metadata signals: resolution/aspect ratio, timestamp
    patterns, missing camera/exif metadata, and suspicious title/description
    patterns common in synthetic media. Never claims definitive detection.
    """

    SUSPICIOUS_TITLE_PATTERNS = [
        r'ai\s+generated',
        r'deepfake',
        r'synthetic\s+(?:video|voice|media)',
        r'text\s+to\s+speech',
        r'tts\s+',
        r'automated\s+(?:video|content)',
    ]
    SUSPICIOUS_DESCRIPTION_PATTERNS = [
        r'this\s+(?:video|content)\s+was\s+generated',
        r'created\s+with\s+ai',
        r'text\s+to\s+speech',
        r'auto[- ]?generated',
        r'template\s+(?:video|content)',
    ]

    DEMO_SCENARIOS = [
        {
            'label': 'metadata_suspicious',
            'metadata_ai_probability': 69.0,
            'score': 65.0,
            'reasons': [
                'Missing camera and exif metadata consistent with synthetic media.',
                'Unusual upload timestamp pattern observed.',
                'Description contains auto-generated content markers.',
            ],
            'indicators': [
                'missing_camera_metadata',
                'unusual_timestamp_pattern',
                'auto_generated_markers',
            ],
        },
        {
            'label': 'metadata_clean',
            'metadata_ai_probability': 11.0,
            'score': 9.0,
            'reasons': [
                'Metadata fields are consistent with organic content.',
                'No suspicious upload patterns detected.',
                'No auto-generated markers in title or description.',
            ],
            'indicators': [],
        },
        {
            'label': 'metadata_mixed',
            'metadata_ai_probability': 46.0,
            'score': 42.0,
            'reasons': [
                'Some metadata fields are missing or unusual.',
                'Title and description look organic overall.',
            ],
            'indicators': [
                'partial_missing_metadata',
            ],
        },
    ]

    def analyze(self, video_info=None, demo_mode=False, key=''):
        """Analyze available media metadata.

        Args:
            video_info (dict): optional keys - video_id, title, description,
                channel, published_at, view_count, like_count, comment_count,
                width, height, resolution, aspect_ratio, codec, encoder,
                fps, duration_seconds, has_camera_metadata, timestamp_type

        Returns:
            dict: metadata_ai_probability, score, reasons, indicators, available
        """
        if demo_mode:
            return self._demo_analysis(key)

        info = video_info or {}
        if not info:
            return {
                'metadata_ai_probability': 0.0,
                'score': 0.0,
                'reasons': ['No metadata available for analysis.'],
                'indicators': [],
                'available': False,
            }

        indicators = []
        reasons = []
        probability = 0.0

        title = str(info.get('title') or '')
        description = str(info.get('description') or '')
        resolution = info.get('resolution') or info.get('width') or None
        aspect_ratio = info.get('aspect_ratio')
        codec = info.get('codec')
        encoder = info.get('encoder')
        has_camera_metadata = info.get('has_camera_metadata')
        timestamp_type = info.get('timestamp_type')
        published_at = info.get('published_at')

        title_matches = [p for p in self.SUSPICIOUS_TITLE_PATTERNS if re.search(p, title, re.IGNORECASE)]
        desc_matches = [p for p in self.SUSPICIOUS_DESCRIPTION_PATTERNS if re.search(p, description, re.IGNORECASE)]

        if title_matches:
            probability += 30.0
            indicators.append('synthetic_markers_in_title')
            reasons.append('Title contains markers commonly found in synthetic media.')
        if desc_matches:
            probability += 25.0
            indicators.append('auto_generated_markers')
            reasons.append('Description contains auto-generated content markers.')

        if has_camera_metadata is False:
            probability += 25.0
            indicators.append('missing_camera_metadata')
            reasons.append('Missing camera metadata consistent with synthetic media.')
        elif has_camera_metadata is None:
            if resolution or aspect_ratio or codec:
                if not encoder or encoder in ('Unknown', ''):
                    probability += 10.0
                    indicators.append('missing_encoder_signature')
                    reasons.append('Missing encoder signature in metadata.')
            else:
                probability += 5.0
                indicators.append('incomplete_metadata')
                reasons.append('Some metadata fields are missing or incomplete.')

        if timestamp_type == 'generated':
            probability += 20.0
            indicators.append('unusual_timestamp_pattern')
            reasons.append('Unusual timestamp pattern detected in metadata.')
        elif timestamp_type == 'missing':
            probability += 10.0
            indicators.append('missing_timestamps')
            reasons.append('Timestamp metadata is missing.')

        if published_at is not None:
            try:
                if isinstance(published_at, str):
                    published_at = datetime.fromisoformat(published_at)
                if hasattr(published_at, 'tzinfo') and published_at.tzinfo is not None:
                    published_at = published_at.replace(tzinfo=None)
                if published_at.hour < 4:
                    probability += 8.0
                    indicators.append('off_hours_upload')
                    reasons.append('Upload timestamp falls in off-peak hours.')
            except (ValueError, TypeError):
                pass

        if resolution:
            try:
                if isinstance(resolution, str):
                    match = re.match(r'^(\d{3,5})[xX*](\d{3,5})$', resolution)
                    if match:
                        width, height = int(match.group(1)), int(match.group(2))
                    else:
                        width = height = 0
                elif isinstance(resolution, (list, tuple)):
                    width, height = int(resolution[0]), int(resolution[1])
                else:
                    width = height = 0
                if width and height:
                    ratio = width / height
                    if ratio > 2.4 or ratio < 1.1:
                        probability += 12.0
                        indicators.append('unusual_aspect_ratio')
                        reasons.append('Unusual aspect ratio may indicate template-generated media.')
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        if probability <= 0:
            reasons.append('No suspicious metadata indicators detected.')
            probability = 6.0
        elif not indicators:
            probability = max(probability, 15.0)

        probability = round(min(probability, 100.0), 1)
        return {
            'metadata_ai_probability': probability,
            'score': probability,
            'reasons': reasons,
            'indicators': indicators,
            'available': True,
        }

    def _demo_analysis(self, key=''):
        if key is None:
            key = ''
        idx = zlib.crc32(str(key).encode('utf-8')) % len(self.DEMO_SCENARIOS)
        scenario = self.DEMO_SCENARIOS[idx]
        result = {
            'metadata_ai_probability': scenario['metadata_ai_probability'],
            'score': scenario['score'],
            'reasons': list(scenario['reasons']),
            'indicators': list(scenario['indicators']),
            'available': True,
            'simulated': True,
        }
        result['reasons'].append(
            'Simulated demo analysis - real metadata fields were not inspected.'
        )
        return result

    def analyze_codec(self, codec, encoder):
        """Check codec/encoder signatures. Returns (score, indicators, reason)."""
        if not codec and not encoder:
            return 0.0, [], 'No codec information available.'
        indicators = []
        score = 0.0
        reason = ''
        if not encoder or encoder in ('Unknown', ''):
            score += 20.0
            indicators.append('missing_encoder_signature')
            reason = 'Missing encoder signature in metadata.'
        if codec and str(codec).lower() in ('vp9', 'av1'):
            score += 5.0
            indicators.append('modern_codec')
            reason = reason or 'Modern codec without camera provenance information.'
        return round(min(score, 50.0), 1), indicators, reason

    def analyze_resolution(self, width, height):
        """Check resolution/aspect ratio. Returns (score, indicators, reason)."""
        if not width or not height:
            return 0.0, [], 'No resolution information available.'
        ratio = width / height
        if ratio > 2.4 or ratio < 1.1:
            return 12.0, ['unusual_aspect_ratio'], 'Unusual aspect ratio may indicate template-generated media.'
        return 0.0, [], ''

    def analyze_timestamps(self, published_at=None, timestamp_type=None):
        """Check timestamp patterns. Returns (score, indicators, reason)."""
        indicators = []
        score = 0.0
        reason = ''
        if timestamp_type == 'generated':
            score += 20.0
            indicators.append('unusual_timestamp_pattern')
            reason = 'Unusual timestamp pattern detected in metadata.'
        elif timestamp_type == 'missing':
            score += 10.0
            indicators.append('missing_timestamps')
            reason = 'Timestamp metadata is missing.'
        if published_at is not None:
            try:
                if isinstance(published_at, str):
                    published_at = datetime.fromisoformat(published_at)
                if hasattr(published_at, 'tzinfo') and published_at.tzinfo is not None:
                    published_at = published_at.replace(tzinfo=None)
                if published_at.hour < 4:
                    score += 8.0
                    indicators.append('off_hours_upload')
                    reason = reason or 'Upload timestamp falls in off-peak hours.'
            except (ValueError, TypeError):
                pass
        return round(min(score, 30.0), 1), indicators, reason

    def check_missing_metadata(self, fields):
        """Return list of missing metadata field names."""
        return [f for f in fields if not f]
