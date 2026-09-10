"""V12 Phase G - Threat Assessment tests.

Covers: all/partial/unavailable components, NULL vs 0 semantics, weight
renormalization, score/level boundaries, evidence coverage + agreement,
capability labels/limitations, persistence + rerun/idempotency, rollback
recovery, PostgreSQL-safe aggregation, SQLite compatibility, pipeline failure
isolation, and V11/V12 regression.
"""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import db as _db
from models.analysis import Analysis
from models.entity import Entity
from models.entity_context import EntityContext
from models.media_analysis import MediaAnalysis
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.coordination_signal import CoordinationSignal
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment
from repositories.threat_assessment_repository import ThreatAssessmentRepository
from services.threat_assessment_service import ThreatAssessmentService
from services.text_similarity_service import TextSimilarityService


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''
    TextSimilarityService.clear_caches()


def _svc(app):
    return ThreatAssessmentService()


def _make_analysis(db, user, platform='youtube'):
    analysis = Analysis(user_id=user.id, analysis_type=platform)
    db.session.add(analysis)
    db.session.commit()
    return analysis


def _component(component, available=True, score=50.0, reasons=None, indicators=None,
               **extra):
    """Build a component-contract dict for a specific component name.

    Each V12 engine exposes its score under a different key (max_risk_score for
    narrative, max_signal_score for coordination, etc.). This helper emits the
    right key so the extractor recognizes the component as available.
    """
    score_key = {
        'authenticity': 'overall_ai_probability',
        'coordination': 'max_signal_score',
        'narrative': 'max_risk_score',
        'propagation': 'max_propagation_score',
        'temporal': 'max_growth_score',
        'entity': 'entity_risk_score',
    }[component]
    base = {
        'available': available,
        'confidence': 60.0,
        'reasons': reasons or [f'[{component}] reason one'],
        'indicators': indicators or [],
    }
    if available:
        base[score_key] = score
    base.update(extra)
    return base


def _authenticity_comp(score=50.0, manipulation=30.0, available_components=('thumbnail',)):
    return {
        'available_components': list(available_components),
        'overall_ai_probability': score,
        'deepfake_score': manipulation,
        'synthetic_voice_score': manipulation,
        'frame_manipulation_score': manipulation,
        'confidence': 70.0,
        'reasons': ['[thumbnail] heuristic signal'],
        'indicators': ['uniform_colors'],
    }


def _add_entity_context(db, analysis, risk=40.0):
    """Create an entity + comment + EntityContext so the entity component is
    available for this analysis (entity risk is computed from stored rows)."""
    from models.comment_result import CommentResult
    entity = Entity(analysis_id=analysis.id, name='Tesla', normalized_name='Tesla',
                    entity_type=Entity.COMPANY, frequency=1)
    db.session.add(entity)
    db.session.commit()
    comment = CommentResult(analysis_id=analysis.id, comment_text='x', author='a')
    db.session.add(comment)
    db.session.commit()
    db.session.add(EntityContext(entity_id=entity.id, comment_result_id=comment.id,
                                 entity_risk_score=risk))
    db.session.commit()


# --------------------------------------------------------------------------
# All components available
# --------------------------------------------------------------------------
class TestThreatAllComponents:
    def _all_available(self):
        return {
            'authenticity': _authenticity_comp(score=60.0),
            'coordination': _component('coordination', score=55.0),
            'narrative': _component('narrative', score=50.0),
            'propagation': _component('propagation', score=45.0),
            'temporal': _component('temporal', score=40.0),
        }

    def test_assessment_computed(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        result = svc.analyze(analysis, components=self._all_available())
        assert result['available'] is True
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta is not None
        assert ta.user_id == user.id
        assert 0.0 <= ta.overall_threat_score <= 100.0
        assert ta.threat_level in ThreatAssessment.LEVELS
        assert 0.0 <= ta.confidence <= 100.0

    def test_all_six_components_available(self, app, db, user):
        analysis = _make_analysis(db, user)
        _add_entity_context(db, analysis, risk=30.0)
        svc = _svc(app)
        result = svc.analyze(analysis, components=self._all_available())
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert set(ta.available_components) == {'authenticity', 'coordination',
                                                'narrative', 'propagation',
                                                'temporal', 'entity'}
        assert ta.missing_components == []

    def test_component_columns_populated(self, app, db, user):
        analysis = _make_analysis(db, user)
        # entity risk from stored EntityContext
        entity = Entity(analysis_id=analysis.id, name='Tesla',
                        normalized_name='Tesla', entity_type=Entity.COMPANY,
                        frequency=1)
        db.session.add(entity)
        db.session.commit()
        from models.comment_result import CommentResult
        comment = CommentResult(analysis_id=analysis.id, comment_text='x', author='a')
        db.session.add(comment)
        db.session.commit()
        db.session.add(EntityContext(entity_id=entity.id, comment_result_id=comment.id,
                                     entity_risk_score=40.0))
        db.session.commit()

        svc = _svc(app)
        svc.analyze(analysis, components=self._all_available())
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.authenticity_score == 60.0
        assert ta.manipulation_score == 30.0
        assert ta.coordination_score == 55.0
        assert ta.narrative_risk_score == 50.0
        assert ta.propagation_score == 45.0
        assert ta.temporal_score == 40.0
        assert ta.entity_risk_score == 40.0


# --------------------------------------------------------------------------
# Partial / unavailable components
# --------------------------------------------------------------------------
class TestThreatPartialComponents:
    def _partial(self):
        return {
            'authenticity': _authenticity_comp(score=70.0),
            # coordination and propagation unavailable
            'narrative': _component('narrative', score=60.0),
            'temporal': _component('temporal', score=50.0),
        }

    def test_missing_components_reported(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components=self._partial())
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert 'coordination' in ta.missing_components
        assert 'propagation' in ta.missing_components
        assert 'entity' in ta.missing_components
        assert 'authenticity' in ta.available_components

    def test_missing_component_not_counted_as_zero(self, app, db, user):
        """Unavailable component must be excluded, not treated as 0 threat."""
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = self._partial()
        # all high-threat available components
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert 'coordination' in ta.missing_components
        assert ta.coordination_score is None

    def test_only_one_component_available(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {'narrative': _component('narrative', score=80.0)}
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        # only narrative + entity (entity via DB may be None -> excluded)
        assert ta.narrative_risk_score == 80.0
        assert ta.coordination_score is None
        assert ta.propagation_score is None
        assert ta.overall_threat_score == 80.0  # single component = its score
        assert ta.threat_level == ThreatAssessment.level_for_score(80.0)

    def test_no_components_available_minimal(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={})
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.overall_threat_score == 0.0
        assert ta.threat_level == ThreatAssessment.LEVEL_MINIMAL
        assert ta.confidence == 0.0
        assert ta.available_components == []

    def test_none_components_key_is_unavailable(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {'coordination': _component('coordination', available=False, score=None)}
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.coordination_score is None
        assert 'coordination' in ta.missing_components


# --------------------------------------------------------------------------
# NULL vs zero semantics
# --------------------------------------------------------------------------
class TestThreatNullVsZero:
    def test_null_stays_null_in_db(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {'narrative': _component('narrative', score=50.0)}  # others missing
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.coordination_score is None
        assert ta.propagation_score is None
        assert ta.authenticity_score is None

    def test_zero_score_is_available_not_null(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {'narrative': _component('narrative', score=0.0)}  # zero but available
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.narrative_risk_score == 0.0
        assert 'narrative' in ta.available_components

    def test_to_dict_preserves_null(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        data = svc.get_analysis_threat_assessment(analysis.id)
        assert data['coordination_score'] is None
        assert data['narrative_risk_score'] == 50.0


# --------------------------------------------------------------------------
# Weight renormalization
# --------------------------------------------------------------------------
class TestThreatRenormalization:
    def test_overall_renormalized_over_available(self, app, db, user):
        """With only 2 of 6 components, weights renormalize over those 2."""
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=0.0),
            'narrative': _component('narrative', score=100.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        # renormalized weights over authenticity(0.25)+narrative(0.20)=0.45
        # overall = (0.25*0 + 0.20*100)/0.45 = 44.4
        expected = round((0.20 * 100.0) / (0.25 + 0.20), 1)
        assert abs(ta.overall_threat_score - expected) < 0.2

    def test_all_components_weighted(self, app, db, user):
        analysis = _make_analysis(db, user)
        _add_entity_context(db, analysis, risk=0.0)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=100.0),
            'coordination': _component('coordination', score=0.0),
            'narrative': _component('narrative', score=0.0),
            'propagation': _component('propagation', score=0.0),
            'temporal': _component('temporal', score=0.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        # authenticity weight 0.25 / total 1.0 = 25.0
        assert abs(ta.overall_threat_score - 25.0) < 0.2

    def test_weights_sum_to_one(self, app):
        assert abs(sum(ThreatAssessmentService.WEIGHTS.values()) - 1.0) < 0.001

    def test_renormalized_weights_stored(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        stored = ta.component_weights or {}
        assert set(stored.keys()) == {'authenticity', 'narrative'}


# --------------------------------------------------------------------------
# Score/level boundaries
# --------------------------------------------------------------------------
class TestThreatLevels:
    @pytest.mark.parametrize('score,level', [
        (0, ThreatAssessment.LEVEL_MINIMAL),
        (5, ThreatAssessment.LEVEL_MINIMAL),
        (9, ThreatAssessment.LEVEL_MINIMAL),
        (10, ThreatAssessment.LEVEL_LOW),
        (20, ThreatAssessment.LEVEL_LOW),
        (24, ThreatAssessment.LEVEL_LOW),
        (25, ThreatAssessment.LEVEL_MODERATE),
        (49, ThreatAssessment.LEVEL_MODERATE),
        (50, ThreatAssessment.LEVEL_ELEVATED),
        (74, ThreatAssessment.LEVEL_ELEVATED),
        (75, ThreatAssessment.LEVEL_HIGH),
        (100, ThreatAssessment.LEVEL_HIGH),
    ])
    def test_level_for_score(self, score, level):
        assert ThreatAssessment.level_for_score(score) == level

    def test_boundary_24_25(self, app):
        assert ThreatAssessment.level_for_score(24) == ThreatAssessment.LEVEL_LOW
        assert ThreatAssessment.level_for_score(25) == ThreatAssessment.LEVEL_MODERATE

    def test_boundary_49_50(self, app):
        assert ThreatAssessment.level_for_score(49) == ThreatAssessment.LEVEL_MODERATE
        assert ThreatAssessment.level_for_score(50) == ThreatAssessment.LEVEL_ELEVATED

    def test_boundary_74_75(self, app):
        assert ThreatAssessment.level_for_score(74) == ThreatAssessment.LEVEL_ELEVATED
        assert ThreatAssessment.level_for_score(75) == ThreatAssessment.LEVEL_HIGH


# --------------------------------------------------------------------------
# Coverage / agreement / capability
# --------------------------------------------------------------------------
class TestThreatExplainability:
    def test_evidence_coverage_fraction(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        # 2 of 6 components = 0.3333 (entity via DB may also be present -> >= 2/6)
        assert ta.evidence_coverage >= 2 / 6
        assert ta.evidence_coverage <= 1.0

    def test_agreement_high_when_scores_close(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=51.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.agreement_score >= 95.0  # 100 - |50-51| = 99

    def test_agreement_low_when_scores_diverge(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=10.0),
            'narrative': _component('narrative', score=90.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.agreement_score <= 25.0  # 100 - |10-90| = 20

    def test_capability_labels(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        labels = ta.capability_labels or {}
        assert labels.get('authenticity') == ThreatAssessment.CAPABILITY_HEURISTIC
        assert labels.get('narrative') == ThreatAssessment.CAPABILITY_HEURISTIC
        assert labels.get('coordination') == ThreatAssessment.CAPABILITY_UNAVAILABLE
        # never claim a trained model
        for name, cap in labels.items():
            assert cap in (ThreatAssessment.CAPABILITY_HEURISTIC,
                           ThreatAssessment.CAPABILITY_UNAVAILABLE)

    def test_limitations_hedged(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        joined = ' '.join(ta.limitations or []).lower()
        assert 'heuristic' in joined
        assert 'not a trained' in joined or 'does not' in joined
        assert 'future ml' in joined
        # no causal claim
        assert 'orchestration' in joined  # mentioned only to disclaim
        assert 'conspiracy' in joined

    def test_summary_is_explainable(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.summary
        assert 'heuristic' in ta.summary.lower()
        assert str(ta.overall_threat_score) in ta.summary

    def test_reasons_include_component_explanations(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0, reasons=['narrative reason text']),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        joined = ' '.join(ta.reasons or []).lower()
        assert '[authenticity]' in joined
        assert '[narrative]' in joined
        assert 'unavailable' in joined  # missing components explained

    def test_indicators_collected_bounded(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        comps = {
            'authenticity': _authenticity_comp(score=50.0),
            'narrative': _component('narrative', score=50.0, indicators=['n1', 'n2', 'n3']),
        }
        svc.analyze(analysis, components=comps)
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert 'n1' in (ta.indicators or [])
        assert 'uniform_colors' in (ta.indicators or [])
        assert len(ta.indicators) <= svc.MAX_INDICATORS

    def test_assessment_method_labelled(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta.assessment_method == ThreatAssessment.METHOD_HEURISTIC_WEIGHTED


# --------------------------------------------------------------------------
# Persistence + idempotency
# --------------------------------------------------------------------------
class TestThreatPersistence:
    def test_one_to_one_with_analysis(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        svc.analyze(analysis, components={'narrative': _component('narrative', score=70.0)})
        rows = ThreatAssessment.query.filter_by(analysis_id=analysis.id).all()
        assert len(rows) == 1  # updated, not duplicated

    def test_rerun_updates_in_place(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={'narrative': _component('narrative', score=20.0)})
        first = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        svc.analyze(analysis, components={'narrative': _component('narrative', score=80.0)})
        second = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert second.id == first.id
        assert second.narrative_risk_score == 80.0

    def test_user_scoping(self, app, db, user):
        from werkzeug.security import generate_password_hash
        from models.user import User
        other = User(username='other', email='o@example.com',
                     password_hash=generate_password_hash('x'))
        db.session.add(other)
        db.session.commit()
        a1 = _make_analysis(db, user)
        a2 = _make_analysis(db, other)
        svc = _svc(app)
        svc.analyze(a1, components={'narrative': _component('narrative', score=50.0)})
        svc.analyze(a2, components={'narrative': _component('narrative', score=50.0)})
        assert ThreatAssessment.query.filter_by(analysis_id=a1.id).first().user_id == user.id
        assert ThreatAssessment.query.filter_by(analysis_id=a2.id).first().user_id == other.id

    def test_cascade_delete_on_analysis(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        assert ThreatAssessment.query.count() == 1
        db.session.delete(analysis)
        db.session.commit()
        assert ThreatAssessment.query.count() == 0


# --------------------------------------------------------------------------
# PostgreSQL-safe aggregation
# --------------------------------------------------------------------------
class TestThreatPostgresCompatibility:
    def _sql(self, statement):
        return str(statement.compile(dialect=_db.engine.dialect))

    def test_level_distribution_group_by_explicit(self, app):
        from sqlalchemy import func
        statement = _db.session.query(
            ThreatAssessment.threat_level,
            func.count(ThreatAssessment.id),
        ).group_by(ThreatAssessment.threat_level).statement
        sql = self._sql(statement)
        assert 'GROUP BY' in sql
        assert 'threat_assessments.threat_level' in sql.split('GROUP BY')[1]

    def test_entity_risk_aggregate_executes(self, app, db, user):
        analysis = _make_analysis(db, user)
        entity = Entity(analysis_id=analysis.id, name='Tesla', normalized_name='Tesla',
                        entity_type=Entity.COMPANY, frequency=1)
        db.session.add(entity)
        db.session.commit()
        from models.comment_result import CommentResult
        comment = CommentResult(analysis_id=analysis.id, comment_text='x', author='a')
        db.session.add(comment)
        db.session.commit()
        db.session.add(EntityContext(entity_id=entity.id, comment_result_id=comment.id,
                                     entity_risk_score=55.0))
        db.session.commit()
        svc = _svc(app)
        entity_comp = svc._extract_entity(analysis)
        assert entity_comp['available'] is True
        assert entity_comp['score'] == 55.0

    def test_entity_risk_aggregate_empty(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        entity_comp = svc._extract_entity(analysis)
        assert entity_comp['available'] is False
        assert entity_comp['score'] is None

    def test_from_db_authenticity(self, app, db, user):
        analysis = _make_analysis(db, user)
        db.session.add(MediaAnalysis(analysis_id=analysis.id,
                                     overall_ai_probability=66.0,
                                     overall_authenticity_score=34.0,
                                     confidence=80.0, deepfake_score=70.0))
        db.session.commit()
        svc = _svc(app)
        comp = svc._from_db(analysis, 'authenticity')
        assert comp['available'] is True
        assert comp['score'] == 66.0
        assert comp['manipulation'] == 70.0

    def test_from_db_absent_is_unavailable(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        for name in ('authenticity', 'coordination', 'narrative', 'propagation',
                     'temporal', 'entity'):
            comp = svc._from_db(analysis, name)
            assert comp['available'] is False
            assert comp['score'] is None


# --------------------------------------------------------------------------
# Transaction safety + isolation
# --------------------------------------------------------------------------
class TestThreatTransactionSafety:
    def test_integrity_error_retries_then_succeeds(self, app, db, user, monkeypatch):
        """The retry loop inside ``_persist`` must run once on a commit-time
        IntegrityError (simulating a unique-constraint race) and then succeed."""
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        real_commit = db.session.commit
        calls = {'n': 0}

        def flaky_commit():
            calls['n'] += 1
            if calls['n'] == 1:
                raise IntegrityError('sim', {}, Exception('conflict'))
            return real_commit()

        monkeypatch.setattr(db.session, 'commit', flaky_commit)
        result = svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        assert calls['n'] == 2, 'should retry exactly once after rollback'
        ta = ThreatAssessment.query.filter_by(analysis_id=analysis.id).first()
        assert ta is not None
        assert ta.narrative_risk_score == 50.0

    def test_sqlalchemy_failure_rolls_back(self, app, db, user, monkeypatch):
        analysis = _make_analysis(db, user)
        svc = _svc(app)

        def boom(*args, **kwargs):
            raise SQLAlchemyError('db down')

        monkeypatch.setattr(svc, '_persist', boom)
        result = svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        assert result.get('available') is False
        assert ThreatAssessment.query.count() == 0
        # subsequent DB work works
        assert Analysis.query.count() >= 1

    def test_failure_isolation_in_pipeline(self, app, db, user):
        from services.analysis_service import AnalysisService
        service = AnalysisService()
        original = service.threat_service.analyze

        def boom(*args, **kwargs):
            raise RuntimeError('threat exploded')

        service.threat_service.analyze = boom
        try:
            result = service.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
            assert result['success'] is True
        finally:
            service.threat_service.analyze = original

    def test_feature_flag_off(self, app, db, user):
        app.config['ENABLE_THREAT_ASSESSMENT'] = False
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        result = svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        assert result.get('available') is False
        assert ThreatAssessment.query.count() == 0


# --------------------------------------------------------------------------
# Pipeline integration + read APIs
# --------------------------------------------------------------------------
class TestThreatIntegration:
    def test_youtube_pipeline_creates_assessment(self, app, db, user):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert result['success'] is True
        ta = ThreatAssessment.query.filter_by(
            analysis_id=result['analysis_id']).first()
        assert ta is not None
        assert ta.overall_threat_score > 0.0
        assert ta.threat_level in ThreatAssessment.LEVELS

    def test_reddit_pipeline_creates_assessment(self, app, db, user):
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_reddit_analysis(
            user.id, 'abc123', subreddit='technology', comment_limit=25)
        assert result['success'] is True
        ta = ThreatAssessment.query.filter_by(
            analysis_id=result['analysis_id']).first()
        assert ta is not None

    def test_flag_off_skips_assessment(self, app, db, user):
        app.config['ENABLE_THREAT_ASSESSMENT'] = False
        from services.analysis_service import AnalysisService
        result = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert result['success'] is True
        assert ThreatAssessment.query.filter_by(
            analysis_id=result['analysis_id']).count() == 0

    def test_v11_and_v12_stages_still_run(self, app, db, user):
        from models.media_analysis import MediaAnalysis
        from models.narrative import Narrative
        from models.coordination_signal import CoordinationSignal
        from models.propagation_event import PropagationEvent
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=25)
        assert MediaAnalysis.query.filter_by(analysis_id=r1['analysis_id']).first() is not None
        assert Narrative.query.count() > 0
        assert CoordinationSignal.query.count() > 0
        assert PropagationEvent.query.count() > 0
        assert ThreatAssessment.query.filter_by(
            analysis_id=r1['analysis_id']).first() is not None

    def test_read_api_analysis(self, app, db, user):
        analysis = _make_analysis(db, user)
        svc = _svc(app)
        svc.analyze(analysis, components={'narrative': _component('narrative', score=50.0)})
        data = svc.get_analysis_threat_assessment(analysis.id)
        assert data is not None
        assert 'overall_threat_score' in data
        assert 'capability' in data
        assert data['capability'] == 'heuristic'

    def test_read_api_user_summary(self, app, db, user):
        svc = _svc(app)
        for i in range(3):
            a = _make_analysis(db, user)
            svc.analyze(a, components={'narrative': _component('narrative', score=50.0 + i * 10)})
        summary = svc.get_user_threat_summary(user.id, limit=2)
        assert summary['total_assessments'] == 3
        assert len(summary['recent_assessments']) == 2
        assert summary['capability'] == 'heuristic'
        assert 'level_distribution' in summary

    def test_repository_bounded_queries(self, app, db, user):
        svc = _svc(app)
        for i in range(10):
            a = _make_analysis(db, user)
            svc.analyze(a, components={'narrative': _component('narrative', score=40.0 + i)})
        repo = ThreatAssessmentRepository()
        assert repo.count_for_user(user.id) == 10
        high = repo.get_high_threat_for_user(user.id, min_score=45.0)
        assert high
        assert all(a.overall_threat_score >= 45.0 for a in high)
        assert len(repo.get_for_user(user.id, limit=3)) == 3
        dist = repo.get_level_distribution(user.id)
        assert isinstance(dist, dict)

    def test_routes_still_functional(self, app, db, user, logged_in_client):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        r1 = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ', comment_limit=5)
        aid = r1['analysis_id']
        assert logged_in_client.get('/dashboard/').status_code == 200
        assert logged_in_client.get('/analysis/history').status_code == 200
        assert logged_in_client.get(f'/analysis/{aid}').status_code == 200
        assert logged_in_client.get(f'/export/csv/{aid}').status_code == 200
        assert logged_in_client.get(f'/export/json/{aid}').status_code == 200
        assert logged_in_client.get('/trends/').status_code == 200
