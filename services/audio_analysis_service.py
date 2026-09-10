import logging
import math
import re
import zlib

logger = logging.getLogger(__name__)


class AudioAnalysisService:
    """Heuristic audio/voice analysis for possible synthetic-voice indicators.

    IMPORTANT: SocialSense AI does NOT have access to the actual audio waveform
    of analyzed videos. This service performs TRANSCRIPT-BASED TEXT INFERENCE
    only (sentence length variance, vocabulary diversity, filler-word patterns,
    repetitive phrasing). It never pretends to inspect the real waveform.
    """

    FILLER_WORDS = {
        'um', 'uh', 'er', 'ah', 'like', 'you know', 'i mean', 'sort of',
        'kind of', 'actually', 'basically', 'literally', 'hmm', 'huh',
    }
    HIGH_TEMPO_FILLERS = {
        'check this out', 'stay tuned', 'don\'t forget to subscribe',
        'hit that like button', 'smash that bell', 'watch till the end',
        'subscribe now',
    }

    DEMO_SCENARIOS = [
        {
            'label': 'voice_clone_risk',
            'voice_clone_probability': 74.0,
            'speech_consistency': 42.0,
            'score': 68.0,
            'reasons': [
                'Unusually uniform sentence lengths may indicate synthetic pacing.',
                'Low vocabulary diversity detected in speech patterns.',
                'Highly repetitive phrasing patterns observed.',
            ],
            'indicators': [
                'uniform_sentence_length',
                'low_vocabulary_diversity',
                'repetitive_phrasing',
            ],
        },
        {
            'label': 'natural_speech',
            'voice_clone_probability': 12.0,
            'speech_consistency': 88.0,
            'score': 10.0,
            'reasons': [
                'Natural sentence length variation observed.',
                'Healthy vocabulary diversity detected.',
                'Filler-word patterns consistent with human speech.',
            ],
            'indicators': [],
        },
        {
            'label': 'mixed_uncertain',
            'voice_clone_probability': 45.0,
            'speech_consistency': 60.0,
            'score': 42.0,
            'reasons': [
                'Some repetitive phrasing patterns detected.',
                'Overall speech variation is moderate.',
            ],
            'indicators': [
                'some_repetitive_phrasing',
            ],
        },
    ]

    def analyze(self, transcript_text=None, transcript_segments=None, demo_mode=False, key=''):
        """Analyze transcript text for possible synthetic-voice indicators.

        Returns:
            dict: voice_clone_probability, speech_consistency, score, reasons,
                  indicators, available, analysis_mode
        """
        if demo_mode:
            result = self._demo_analysis(key)
            result['analysis_mode'] = 'transcript_simulation'
            return result

        text = (transcript_text or '').strip()
        segments = transcript_segments or []
        if not text and not segments:
            return {
                'voice_clone_probability': 0.0,
                'speech_consistency': 0.0,
                'score': 0.0,
                'reasons': ['No transcript text available for speech analysis.'],
                'indicators': [],
                'available': False,
                'analysis_mode': 'unavailable',
            }

        if not text and segments:
            text = ' '.join(s.get('text', '') for s in segments if s.get('text'))

        sentences = self._split_sentences(text)
        words = re.findall(r"[a-zA-Z']+", text.lower())
        if not words:
            return {
                'voice_clone_probability': 0.0,
                'speech_consistency': 0.0,
                'score': 0.0,
                'reasons': ['Transcript contains no usable words for speech analysis.'],
                'indicators': [],
                'available': False,
                'analysis_mode': 'transcript',
            }

        sentence_lengths = [len(re.findall(r"[a-zA-Z']+", s)) for s in sentences if s]
        length_variance = self._relative_variance(sentence_lengths)
        vocabulary_diversity = self._vocabulary_diversity(words)
        filler_ratio = self._filler_ratio(text)
        repetition_ratio = self._repetition_ratio(sentences)

        indicators = []
        reasons = []
        probability = 0.0

        if length_variance < 0.25:
            probability += 30.0
            indicators.append('uniform_sentence_length')
            reasons.append('Unusually uniform sentence lengths may indicate synthetic pacing.')
        if vocabulary_diversity < 0.45:
            probability += 30.0
            indicators.append('low_vocabulary_diversity')
            reasons.append('Low vocabulary diversity detected in speech patterns.')
        if repetition_ratio > 0.20:
            probability += 25.0
            indicators.append('repetitive_phrasing')
            reasons.append('Highly repetitive phrasing patterns observed.')
        if filler_ratio > 0.18:
            probability += 10.0
            indicators.append('excessive_filler_words')
            reasons.append('Excessive filler-word usage may indicate scripted speech.')
        if probability > 0 and probability < 20:
            probability = 20.0
        if not indicators:
            reasons.append('No strong synthetic-voice indicators detected in transcript patterns.')
            probability = 8.0

        probability = round(min(probability, 100.0), 1)
        speech_consistency = round(max(0.0, 100.0 - probability), 1)

        return {
            'voice_clone_probability': probability,
            'speech_consistency': speech_consistency,
            'score': probability,
            'reasons': reasons,
            'indicators': indicators,
            'available': True,
            'analysis_mode': 'transcript',
        }

    def _demo_analysis(self, key=''):
        if key is None:
            key = ''
        idx = zlib.crc32(str(key).encode('utf-8')) % len(self.DEMO_SCENARIOS)
        scenario = self.DEMO_SCENARIOS[idx]
        result = {
            'voice_clone_probability': scenario['voice_clone_probability'],
            'speech_consistency': scenario['speech_consistency'],
            'score': scenario['score'],
            'reasons': list(scenario['reasons']),
            'indicators': list(scenario['indicators']),
            'available': True,
            'simulated': True,
        }
        result['reasons'].append(
            'Simulated demo analysis - based on sample transcript patterns, not the real audio waveform.'
        )
        return result

    def analyze_speech_consistency(self, text):
        """Compute a 0-100 consistency score for a transcript (text inference)."""
        if not text:
            return 50.0
        sentences = self._split_sentences(text)
        words = re.findall(r"[a-zA-Z']+", text.lower())
        if not words:
            return 50.0
        length_variance = self._relative_variance([len(re.findall(r"[a-zA-Z']+", s)) for s in sentences if s])
        diversity = self._vocabulary_diversity(words)
        consistency = 100.0 - min(length_variance * 80.0, 50.0) - min((1.0 - min(diversity, 1.0)) * 50.0, 30.0)
        return round(max(0.0, min(100.0, consistency)), 1)

    def _split_sentences(self, text):
        parts = re.split(r'[.!?]+', text)
        return [p.strip() for p in parts if p.strip()]

    def _relative_variance(self, values):
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        if mean <= 0:
            return 0.0
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance) / mean

    def _vocabulary_diversity(self, words):
        if not words:
            return 0.0
        return len(set(words)) / len(words)

    def _filler_ratio(self, text):
        lower = text.lower()
        count = 0
        for filler in self.FILLER_WORDS:
            count += len(re.findall(rf'\b{re.escape(filler)}\b', lower))
        total = len(re.findall(r"\b\w+\b", lower))
        if total <= 0:
            return 0.0
        return count / total

    def _repetition_ratio(self, sentences):
        if len(sentences) < 3:
            return 0.0
        seen = set()
        repeated = 0
        for s in sentences:
            normalized = re.sub(r'[^a-z ]', '', s.lower()).strip()
            if not normalized:
                continue
            if normalized in seen:
                repeated += 1
            seen.add(normalized)
        return repeated / len(sentences)
