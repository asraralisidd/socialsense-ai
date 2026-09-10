import logging
import zlib
from flask import current_app

logger = logging.getLogger(__name__)


class ThumbnailAnalysisService:
    """Heuristic thumbnail analysis for possible AI-generated media indicators.

    The analysis is heuristic and EXPLAINABLE: it reports possible synthetic
    indicators (e.g. extreme color uniformity, repeated patterns, over-smoothing)
    but never claims definitive AI generation.
    """

    DEMO_SCENARIOS = [
        {
            'label': 'ai_generated',
            'thumbnail_ai_probability': 78.0,
            'score': 72.0,
            'reasons': [
                'Extreme color uniformity detected across thumbnail regions.',
                'Repeated synthetic patterns observed in background areas.',
                'Unnaturally smooth gradients suggest digital generation.',
            ],
            'indicators': [
                'extreme_color_uniformity',
                'repeated_synthetic_patterns',
                'over_smoothing',
            ],
        },
        {
            'label': 'authentic',
            'thumbnail_ai_probability': 15.0,
            'score': 12.0,
            'reasons': [
                'Natural color variance observed across thumbnail regions.',
                'No repeated synthetic patterns detected.',
                'Texture detail levels are consistent with camera capture.',
            ],
            'indicators': [],
        },
        {
            'label': 'mixed_uncertain',
            'thumbnail_ai_probability': 52.0,
            'score': 48.0,
            'reasons': [
                'Some regions show unusually uniform color distribution.',
                'Overall texture detail is mixed, limiting confidence.',
                'Possible post-processing or enhancement detected.',
            ],
            'indicators': [
                'partial_color_uniformity',
                'possible_enhancement',
            ],
        },
    ]

    def analyze(self, video_id='', title='', thumbnail_url=None, demo_mode=False):
        """Analyze a thumbnail and return explainable AI-likelihood indicators.

        Returns:
            dict: thumbnail_ai_probability, score, reasons, indicators, available
        """
        if demo_mode:
            return self._demo_analysis(video_id)
        url = thumbnail_url
        if not url and video_id:
            url = f'https://img.youtube.com/vi/{video_id}/hqdefault.jpg'
        if not url:
            return self._fallback_analysis('No thumbnail available for analysis.')
        try:
            return self._real_analysis(url, title)
        except Exception as e:
            logger.warning(f'Thumbnail analysis failed: {e}')
            return self._fallback_analysis(f'Thumbnail analysis unavailable: {e}')

    def _demo_analysis(self, video_id=''):
        scenario = self._select_demo_scenario(video_id)
        result = {
            'thumbnail_ai_probability': scenario['thumbnail_ai_probability'],
            'score': scenario['score'],
            'reasons': list(scenario['reasons']),
            'indicators': list(scenario['indicators']),
            'available': True,
            'simulated': True,
        }
        result['reasons'].append('Simulated demo analysis - results are illustrative, not real media inspection.')
        return result

    def _select_demo_scenario(self, key):
        if key is None:
            key = ''
        idx = zlib.crc32(str(key).encode('utf-8')) % len(self.DEMO_SCENARIOS)
        return self.DEMO_SCENARIOS[idx]

    def _fallback_analysis(self, reason):
        return {
            'thumbnail_ai_probability': 0.0,
            'score': 0.0,
            'reasons': [reason],
            'indicators': [],
            'available': False,
        }

    def _real_analysis(self, url, title):
        image = self._download_image(url)
        if image is None:
            return self._fallback_analysis('Thumbnail image could not be retrieved.')

        check_results = []
        uniformity = self._check_extrema_consistency(image)
        patterns = self._check_repeated_patterns(image)
        smoothing = self._estimate_smoothing(image)
        for check in (uniformity, patterns, smoothing):
            if check is not None:
                check_results.append(check)

        reasons = []
        indicators = []
        total_weight = 0.0
        weighted = 0.0
        for check in check_results:
            total_weight += check['weight']
            weighted += check['weight'] * check['score']
            if check['indicators']:
                indicators.extend(check['indicators'])
            if check['score'] >= 55:
                reasons.append(check['reason'])

        if total_weight <= 0:
            return self._fallback_analysis('Thumbnail analysis produced no signals.')

        ai_probability = round(weighted / total_weight, 1)
        ai_probability = max(0.0, min(100.0, ai_probability))

        if not reasons:
            reasons.append('No strong synthetic indicators detected in thumbnail.')

        return {
            'thumbnail_ai_probability': ai_probability,
            'score': ai_probability,
            'reasons': reasons,
            'indicators': list(dict.fromkeys(indicators)),
            'available': True,
            'simulated': False,
        }

    def _download_image(self, url):
        try:
            import requests
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return None
            from PIL import Image
            from io import BytesIO
            return Image.open(BytesIO(response.content)).convert('RGB')
        except Exception as e:
            logger.warning(f'Thumbnail download failed: {e}')
            return None

    def _check_extrema_consistency(self, image):
        try:
            from PIL import Image
            small = image.resize((64, 36))
            grayscale = small.convert('L')
            pixels = list(grayscale.getdata())
            if not pixels:
                return None
            min_v = min(pixels)
            max_v = max(pixels)
            spread = max_v - min_v
            region_means = []
            w, h = small.size
            block_w, block_h = max(w // 4, 1), max(h // 4, 1)
            for by in range(0, h - block_h + 1, block_h):
                for bx in range(0, w - block_w + 1, block_w):
                    region = grayscale.crop((bx, by, bx + block_w, by + block_h))
                    values = list(region.getdata())
                    if values:
                        region_means.append(sum(values) / len(values))
            if not region_means:
                return None
            mean_variance = (max(region_means) - min(region_means)) if len(region_means) > 1 else 0.0

            score = 0.0
            indicators = []
            reason = ''
            if spread < 60:
                score += 45.0
                indicators.append('extreme_color_uniformity')
                reason = 'Extreme color uniformity detected across thumbnail regions.'
            elif spread < 90:
                score += 25.0
                indicators.append('reduced_color_variance')
                reason = 'Reduced color variance may indicate synthetic rendering.'
            if mean_variance < 8:
                score += 30.0
                indicators.append('flat_region_distribution')
                reason = reason or 'Unusually flat region distribution observed.'
            return {'score': min(score, 90.0), 'weight': 0.4, 'reason': reason, 'indicators': indicators}
        except Exception as e:
            logger.warning(f'Extrema consistency check failed: {e}')
            return None

    def _check_repeated_patterns(self, image):
        try:
            from PIL import Image
            small = image.resize((48, 27))
            w, h = small.size
            block_w, block_h = max(w // 3, 1), max(h // 3, 1)
            blocks = []
            for by in range(0, h - block_h + 1, block_h):
                for bx in range(0, w - block_w + 1, block_w):
                    blocks.append((bx, by, small.crop((bx, by, bx + block_w, by + block_h)).convert('L')))
            duplicates = 0
            comparisons = 0
            for i in range(len(blocks)):
                for j in range(i + 1, len(blocks)):
                    comparisons += 1
                    diff = self._image_diff(blocks[i][2], blocks[j][2])
                    if diff < 8:
                        duplicates += 1
            if comparisons == 0:
                return None
            duplicate_ratio = duplicates / comparisons
            score = duplicate_ratio * 100.0
            indicators = []
            reason = ''
            if score >= 45:
                indicators.append('repeated_synthetic_patterns')
                reason = 'Repeated synthetic patterns observed in background areas.'
            elif score >= 25:
                indicators.append('possible_repeated_patterns')
                reason = 'Possible repeated patterns detected in thumbnail regions.'
            return {'score': min(score, 90.0), 'weight': 0.35, 'reason': reason, 'indicators': indicators}
        except Exception as e:
            logger.warning(f'Repeated pattern check failed: {e}')
            return None

    def _image_diff(self, img_a, img_b):
        try:
            from PIL import ImageChops
            diff = ImageChops.difference(img_a, img_b)
            histogram = diff.histogram()
            total = sum(histogram)
            if total == 0:
                return 0.0
            weighted_sum = sum(i * count for i, count in enumerate(histogram))
            return weighted_sum / total
        except Exception:
            return 255.0

    def _estimate_smoothing(self, image):
        try:
            from PIL import Image
            from io import BytesIO
            small = image.resize((96, 54))
            bytes_out = BytesIO()
            small.save(bytes_out, format='JPEG', quality=95)
            size_high = bytes_out.tell()
            bytes_out = BytesIO()
            small.save(bytes_out, format='JPEG', quality=40)
            size_low = bytes_out.tell()
            if size_low <= 0 or size_high <= 0:
                return None
            compression_ratio = size_high / size_low
            score = 0.0
            indicators = []
            reason = ''
            if compression_ratio > 2.8:
                score = 75.0
                indicators.append('over_smoothing')
                reason = 'Unnaturally smooth gradients suggest digital generation.'
            elif compression_ratio > 2.2:
                score = 40.0
                indicators.append('possible_over_smoothing')
                reason = 'Compression profile may indicate heavy smoothing or upscaling.'
            return {'score': score, 'weight': 0.25, 'reason': reason, 'indicators': indicators}
        except Exception as e:
            logger.warning(f'Smoothing estimate failed: {e}')
            return None
