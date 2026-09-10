"""V12 Phase G - Threat Assessment Service.

Combines V11 authenticity + V12 narrative/coordination/propagation/temporal
evidence into a single explainable threat assessment per analysis, persisted
as a 1:1 ``ThreatAssessment`` row.

SCORING MODEL
-------------
The engine operates on the 6 components defined by
``ThreatAssessment.COMPONENTS``. Each component produces a score in 0-100
(threat direction: higher = more threat). The per-analysis weighted combination
is **renormalized** over whatever components are available (V11 ``V12`` pattern),
so a missing signal does not pull the overall score toward zero — it is simply
excluded.

CAPABILITY HONESTY
------------------
All components are heuristic (V11 authenticity, V12 rule-based engines, V8
entity risk). None are trained ML models. ``capability_labels`` records this
per component so no consumer can mistake a heuristic for a trained model.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db
from models.entity import Entity
from models.entity_context import EntityContext
from models.media_analysis import MediaAnalysis
from models.narrative_occurrence import NarrativeOccurrence
from models.narrative import Narrative
from models.coordination_signal import CoordinationSignal
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment
from repositories.threat_assessment_repository import ThreatAssessmentRepository

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ThreatAssessmentService:
    ENABLE_KEY = 'ENABLE_THREAT_ASSESSMENT'

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_weighted_renormalized'

    #: Base weights for the 6 components. Renormalized over whatever is
    #: available. Sums to 1.0.
    WEIGHTS = {
        'authenticity': 0.25,
        'coordination': 0.25,
        'narrative': 0.20,
        'propagation': 0.15,
        'temporal': 0.10,
        'entity': 0.05,
    }

    MAX_INDICATORS = 20
    MAX_REASONS = 20
    MAX_LIMITATIONS = 10

    def __init__(self):
        self.repo = ThreatAssessmentRepository()

    # ----------------------------------------------------------------- config

    def _cfg(self, key, default):
        try:
            from flask import current_app
            if current_app:
                return current_app.config.get(key, default)
        except Exception:
            pass
        return default

    # ---------------------------------------------------------------- entry

    def analyze(self, analysis, components=None):
        """Compute and persist a threat assessment for one analysis.

        ``components`` is an optional dict of pipeline outputs keyed by
        ``ThreatAssessment.COMPONENT_*`` (``authenticity``, ``narrative``,
        ``coordination``, ``propagation``, ``temporal``). When provided, the
        engine uses the live per-engine scores rather than re-querying stored
        data. ``entity`` is always computed from stored ``EntityContext`` rows
        (no V12 entity engine exists).

        Returns a component-contract dict.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('Threat assessment is disabled by configuration.')

        if analysis is None:
            return self._unavailable('No analysis provided; assessment cannot be computed.')

        try:
            raw = self._extract_all(analysis, components)
            persisted = self._persist(analysis, raw)
            if persisted is None:
                return self._unavailable(
                    'Threat assessment was computed but could not be persisted.')
            return self._build_result(persisted)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'Threat assessment failed: {exc}')
            return self._unavailable('Threat assessment failed; rolled back.')

    # --------------------------------------------------------- extract scores

    def _extract_all(self, analysis, components):
        """Build the raw component dict from pipeline outputs and DB queries."""
        raw = {}
        raw['authenticity'] = self._extract_authenticity(analysis, components)
        raw['coordination'] = self._extract_coordination(analysis, components)
        raw['narrative'] = self._extract_narrative(analysis, components)
        raw['propagation'] = self._extract_propagation(analysis, components)
        raw['temporal'] = self._extract_temporal(analysis, components)
        raw['entity'] = self._extract_entity(analysis)
        return raw

    def _extract_authenticity(self, analysis, components):
        comp = self._get_component(components, 'authenticity')
        if comp is None:
            return self._from_db(analysis, 'authenticity')
        available = bool(comp.get('available_components'))
        score = comp.get('overall_ai_probability')
        manipulation = self._max_many(
            comp.get('deepfake_score'),
            comp.get('synthetic_voice_score'),
            comp.get('frame_manipulation_score'),
        )
        return {
            'available': available and score is not None,
            'score': self._clamp(score) if score is not None else None,
            'manipulation': self._clamp(manipulation) if manipulation is not None else None,
            'confidence': comp.get('confidence'),
            'reasons': comp.get('reasons', [])[:3],
            'indicators': comp.get('indicators', [])[:5],
        }

    def _extract_coordination(self, analysis, components):
        comp = self._get_component(components, 'coordination')
        if comp is None:
            return self._from_db(analysis, 'coordination')
        available = comp.get('available', False)
        score = comp.get('max_signal_score')
        return {
            'available': available and score is not None,
            'score': self._clamp(score),
            'confidence': None,
            'reasons': comp.get('reasons', [])[:3],
            'indicators': comp.get('indicators', [])[:5],
        }

    def _extract_narrative(self, analysis, components):
        comp = self._get_component(components, 'narrative')
        if comp is None:
            return self._from_db(analysis, 'narrative')
        available = comp.get('available', False)
        score = comp.get('max_risk_score')
        return {
            'available': available and score is not None,
            'score': self._clamp(score),
            'confidence': None,
            'reasons': comp.get('reasons', [])[:3],
            'indicators': comp.get('indicators', [])[:5],
        }

    def _extract_propagation(self, analysis, components):
        comp = self._get_component(components, 'propagation')
        if comp is None:
            return self._from_db(analysis, 'propagation')
        available = comp.get('available', False)
        score = comp.get('max_propagation_score')
        return {
            'available': available and score is not None,
            'score': self._clamp(score),
            'confidence': None,
            'reasons': comp.get('reasons', [])[:3],
            'indicators': comp.get('indicators', [])[:5],
        }

    def _extract_temporal(self, analysis, components):
        comp = self._get_component(components, 'temporal')
        if comp is None:
            return self._from_db(analysis, 'temporal')
        available = comp.get('available', False)
        score = comp.get('max_growth_score')
        return {
            'available': available and score is not None,
            'score': self._clamp(score),
            'confidence': None,
            'reasons': comp.get('reasons', [])[:3],
            'indicators': comp.get('indicators', [])[:5],
        }

    def _extract_entity(self, analysis):
        """Entity risk computed from stored EntityContext rows (V8).

        Bounded to the analysis's entities. ``None`` when no entity contexts
        with risk scores exist.
        """
        row = db.session.query(
            func.avg(EntityContext.entity_risk_score).label('avg_risk'),
            func.count(EntityContext.id).label('count'),
        ).join(Entity, Entity.id == EntityContext.entity_id).filter(
            Entity.analysis_id == analysis.id
        ).one()
        if row.count == 0 or row.avg_risk is None:
            return {'available': False, 'score': None}
        return {
            'available': True,
            'score': self._clamp(round(float(row.avg_risk), 1)),
            'confidence': None,
            'reasons': [],
            'indicators': [],
        }

    # ------------------------------------------------ DB fallback (read-only)

    def _from_db(self, analysis, component):
        """Recompute a component from stored data when no pipeline dict was
        provided (e.g., after-the-fact read API or test without live outputs)."""
        if component == 'authenticity':
            ma = MediaAnalysis.query.filter_by(analysis_id=analysis.id).first()
            if ma is None:
                return {'available': False, 'score': None, 'manipulation': None}
            return {
                'available': True,
                'score': self._clamp(ma.overall_ai_probability),
                'manipulation': self._clamp(max(
                    ma.deepfake_score or 0.0, ma.synthetic_voice_score or 0.0,
                    ma.frame_manipulation_score or 0.0)),
                'confidence': ma.confidence,
                'reasons': [],
                'indicators': [],
            }
        if component == 'coordination':
            row = db.session.query(
                func.max(CoordinationSignal.score).label('max_score'),
                func.count(CoordinationSignal.id).label('count'),
            ).filter(CoordinationSignal.analysis_id == analysis.id).one()
            if row.count == 0 or row.max_score is None:
                return {'available': False, 'score': None}
            return {'available': True, 'score': self._clamp(round(float(row.max_score), 1)),
                    'confidence': None, 'reasons': [], 'indicators': []}
        if component == 'narrative':
            row = db.session.query(
                func.max(NarrativeOccurrence.risk_score).label('max_risk'),
                func.count(NarrativeOccurrence.id).label('count'),
            ).filter(NarrativeOccurrence.analysis_id == analysis.id).one()
            if row.count == 0 or row.max_risk is None:
                return {'available': False, 'score': None}
            return {'available': True, 'score': self._clamp(round(float(row.max_risk), 1)),
                    'confidence': None, 'reasons': [], 'indicators': []}
        if component == 'propagation':
            rows = PropagationEvent.query.filter(
                (PropagationEvent.source_analysis_id == analysis.id) |
                (PropagationEvent.target_analysis_id == analysis.id)
            ).all()
            if not rows:
                return {'available': False, 'score': None}
            max_score = max(e.propagation_score for e in rows)
            return {'available': True, 'score': self._clamp(round(float(max_score), 1)),
                    'confidence': None, 'reasons': [], 'indicators': []}
        if component == 'temporal':
            nids = [o.narrative_id for o in NarrativeOccurrence.query.filter_by(
                analysis_id=analysis.id).all()]
            if not nids:
                return {'available': False, 'score': None}
            narratives = Narrative.query.filter(
                Narrative.id.in_(nids)).all()
            if not narratives:
                return {'available': False, 'score': None}
            max_growth = max(n.growth_score for n in narratives)
            return {'available': True, 'score': self._clamp(round(float(max_growth), 1)),
                    'confidence': None, 'reasons': [], 'indicators': []}
        return {'available': False, 'score': None}

    # --------------------------------------------------------------- scoring

    def _compute_overall(self, raw):
        """Weighted + renormalized + confidence + agreement."""
        scores = {}
        for name in ThreatAssessment.COMPONENTS:
            entry = raw.get(name)
            if entry and entry.get('available'):
                scores[name] = entry['score']

        available = sorted(scores.keys())
        missing = sorted(n for n in ThreatAssessment.COMPONENTS if n not in scores)
        coverage = len(available) / len(ThreatAssessment.COMPONENTS)

        if not scores:
            return {
                'overall': 0.0, 'level': ThreatAssessment.LEVEL_MINIMAL,
                'confidence': 0.0, 'coverage': 0.0, 'agreement': 0.0,
                'available': available, 'missing': missing,
                'component_scores': {}, 'renormalized_weights': {},
            }

        total_weight = sum(self.WEIGHTS[n] for n in available)
        weighted = sum(self.WEIGHTS[n] * scores[n] for n in available)
        overall = self._clamp(round(weighted / total_weight, 1)) if total_weight > 0 else 0.0

        values = list(scores.values())
        agreement = 100.0 - (max(values) - min(values)) if len(values) > 1 else 85.0
        confidence = self._clamp(round(0.6 * coverage * 100.0 + 0.4 * agreement, 1))

        return {
            'overall': overall,
            'level': ThreatAssessment.level_for_score(overall),
            'confidence': confidence,
            'coverage': coverage,
            'agreement': agreement,
            'available': available,
            'missing': missing,
            'component_scores': scores,
            'renormalized_weights': {n: self.WEIGHTS[n] for n in available},
        }

    # ----------------------------------------------------------- persistence

    def _persist(self, analysis, raw):
        for attempt in (1, 2):
            try:
                result = self._compute_overall(raw)
                assessment = self.repo.get_for_analysis(analysis.id)
                if assessment is None:
                    assessment = ThreatAssessment(
                        analysis_id=analysis.id, user_id=analysis.user_id)
                    db.session.add(assessment)
                self._populate(assessment, raw, result)
                db.session.commit()
                return assessment
            except IntegrityError as exc:
                db.session.rollback()
                if attempt == 2:
                    logger.warning(f'Threat assessment persistence conflict: {exc}')
                    return None
            except SQLAlchemyError as exc:
                db.session.rollback()
                logger.warning(f'Threat assessment persistence failed: {exc}')
                return None
        return None

    def _populate(self, assessment, raw, result):
        scores = result['component_scores']
        assessment.overall_threat_score = result['overall']
        assessment.threat_level = result['level']
        assessment.confidence = result['confidence']
        assessment.evidence_coverage = result['coverage']
        assessment.agreement_score = result['agreement']

        assessment.authenticity_score = scores.get('authenticity')
        assessment.manipulation_score = (
            raw.get('authenticity', {}).get('manipulation')
            if raw.get('authenticity', {}).get('available') else None)
        assessment.coordination_score = scores.get('coordination')
        assessment.narrative_risk_score = scores.get('narrative')
        assessment.propagation_score = scores.get('propagation')
        assessment.temporal_score = scores.get('temporal')
        assessment.entity_risk_score = scores.get('entity')

        assessment.component_scores = result['component_scores']
        assessment.component_weights = result['renormalized_weights']
        assessment.available_components = result['available']
        assessment.missing_components = result['missing']
        assessment.capability_labels = self._capability_labels(result['available'])
        assessment.summary = self._summary(result)
        assessment.reasons = self._reasons(raw, result)
        assessment.indicators = self._indicators(raw)
        assessment.limitations = self._limitations()
        assessment.assessment_method = ThreatAssessment.METHOD_HEURISTIC_WEIGHTED
        return assessment

    # --------------------------------------------------- result construction

    def _capability_labels(self, available):
        labels = {}
        for name in available:
            if name == 'entity':
                labels[name] = ThreatAssessment.CAPABILITY_HEURISTIC
            else:
                labels[name] = ThreatAssessment.CAPABILITY_HEURISTIC
        # Mark downstream components that don't exist yet — for clarity
        for name in ThreatAssessment.COMPONENTS:
            if name not in available:
                labels[name] = ThreatAssessment.CAPABILITY_UNAVAILABLE
        return labels

    def _summary(self, result):
        return (
            f'Threat score {result["overall"]:.1f} ({result["level"]}), '
            f'confidence {result["confidence"]:.1f}%. '
            f'{len(result["available"])}/{len(ThreatAssessment.COMPONENTS)} '
            f'components available. '
            f'Assessment is heuristic and is not a trained ML verdict.'
        )

    def _reasons(self, raw, result):
        reasons = []
        reasons.append(
            f'Overall threat score {result["overall"]:.1f} ({result["level"]}) '
            f'with {result["confidence"]:.1f}% confidence, combining '
            f'{len(result["available"])}/{len(ThreatAssessment.COMPONENTS)} '
            f'available components.')
        for name in result['available']:
            entry = raw.get(name, {})
            score = result['component_scores'].get(name)
            label = ThreatAssessment.CAPABILITY_HEURISTIC
            reasons.append(
                f'[{name}] score={score:.1f} (capability: {label}). '
                f'{entry.get("reasons", [None])[0] if entry.get("reasons") else ""}')
        for name in result['missing']:
            reasons.append(
                f'[{name}] unavailable — excluded from the weighted combination '
                f'(not silently treated as zero).')
        reasons.append(
            'All components are heuristic or rule-based; no trained ML model '
            'is used to produce this assessment.')
        return reasons[:int(self._cfg('THREAT_MAX_REASONS', self.MAX_REASONS))]

    def _indicators(self, raw):
        indicators = []
        for name in ThreatAssessment.COMPONENTS:
            entry = raw.get(name, {})
            indicators.extend(entry.get('indicators', []) or [])
        return sorted(set(indicators))[:int(self._cfg('THREAT_MAX_INDICATORS', self.MAX_INDICATORS))]

    def _limitations(self):
        return [
            'Threat assessment is heuristic and rule-based; it is not a trained '
            'ML classifier of coordinated behaviour, threats or manipulation.',
            'Component scores are weighted and renormalized over available signals; '
            'a missing signal is excluded from the combination, not treated as zero.',
            'All capability labels are "heuristic" or "unavailable". A future ML '
            'upgrade (capability "future_ml") would replace individual components '
            'with trained models while keeping the same aggregation architecture.',
            'The assessment is a probabilistic indicator, not a definitive verdict.',
            'This assessment provides no evidence of intent, authorship, orchestration '
            'or conspiracy.',
        ][:int(self._cfg('THREAT_MAX_LIMITATIONS', self.MAX_LIMITATIONS))]

    def _build_result(self, assessment):
        data = assessment.to_dict()
        data['available'] = True
        data['capability'] = self.CAPABILITY
        data['detection_method'] = self.DETECTION_METHOD
        data['assessment_method'] = assessment.assessment_method
        return data

    def _unavailable(self, reason):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'assessment_method': ThreatAssessment.METHOD_UNAVAILABLE,
            'overall_threat_score': None,
            'threat_level': ThreatAssessment.LEVEL_MINIMAL,
            'confidence': 0.0,
            'evidence_coverage': 0.0,
            'agreement_score': 0.0,
            'component_scores': {},
            'available_components': [],
            'missing_components': [],
            'capability_labels': {},
            'summary': reason,
            'reasons': [reason],
            'indicators': [],
            'limitations': self._limitations(),
        }

    # ---------------------------------------------------------- read API

    def get_analysis_threat_assessment(self, analysis_id):
        """Read-only: one row for one analysis (contract-decorated)."""
        assessment = self.repo.get_for_analysis(analysis_id)
        if assessment is None:
            return None
        data = assessment.to_dict()
        data['available'] = True
        data['capability'] = self.CAPABILITY
        data['detection_method'] = self.DETECTION_METHOD
        return data

    def get_user_threat_summary(self, user_id, limit=10):
        """Bounded read-only user-level rollup."""
        top = self.repo.get_for_user(user_id, limit=limit)
        return {
            'total_assessments': self.repo.count_for_user(user_id),
            'level_distribution': self.repo.get_level_distribution(user_id),
            'recent_assessments': [a.to_dict() for a in top] if top else [],
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
        }

    # ---------------------------------------------------------------- utils

    @staticmethod
    def _get_component(components, name):
        if components is None:
            return None
        return components.get(name)

    @staticmethod
    def _max_many(*values):
        clean = [v for v in values if v is not None]
        return max(clean) if clean else None

    def _clamp(self, value):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0