"""V13 Phase C - Cross-Analysis Comparison & Narrative Evolution tests.

Backend only (no UI/export): comparison result contract, deterministic
per-user history selection, real-timestamp windows, unavailable-vs-zero,
delta calculation, the four evolution states plus insufficient/unavailable
branches, provenance integrity, evidence-chain compatibility, idempotency,
and non-causal wording.
"""
from datetime import datetime, timedelta, timezone

import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.threat_assessment import ThreatAssessment
from services.cross_analysis_service import CrossAnalysisService
from services.historical_context_service import HistoricalContextService


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _analysis(user_id, days_ago=None, threat_score=None, sentiment=None):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    if days_ago is not None:
        a.created_at = _now() - timedelta(days=days_ago)
    db.session.add(YouTubeAnalysis(analysis_id=a.id, video_id=f'vid{a.id}'))
    db.session.add(CommentResult(
        analysis_id=a.id, comment_text='stored comment text', author='a1',
        sentiment_score=sentiment if sentiment is not None else 60.0,
        toxicity_score=5.0, spam_score=10.0, duplicate_score=0.0))
    if threat_score is not None:
        db.session.add(ThreatAssessment(
            analysis_id=a.id, user_id=user_id,
            overall_threat_score=threat_score, threat_level='Low',
            confidence=50.0, evidence_coverage=0.5))
    db.session.commit()
    return a


def _narrative(user_id, name):
    n = Narrative(user_id=user_id, name=name,
                  normalized_name=name.lower(), risk_score=40.0)
    db.session.add(n)
    db.session.commit()
    return n


def _occurrence(narrative_id, analysis_id, user_id, days_ago=None):
    occ = NarrativeOccurrence(narrative_id=narrative_id,
                              analysis_id=analysis_id, user_id=user_id,
                              relevance_score=70.0)
    db.session.add(occ)
    db.session.flush()
    if days_ago is not None:
        occ.occurred_at = _now() - timedelta(days=days_ago)
    db.session.commit()
    return occ


def _other_user(db):
    from models.user import User
    from werkzeug.security import generate_password_hash
    other = User(username='phasec_other', email='phasec_other@x.com',
                 password_hash=generate_password_hash('x'))
    db.session.add(other)
    db.session.commit()
    return other


# --------------------------------------------------------------------------
# Cross-analysis comparison
# --------------------------------------------------------------------------
class TestCrossAnalysisComparison:
    def test_service_creation(self, app, db, user):
        svc = CrossAnalysisService()
        assert svc.ENABLE_KEY == 'ENABLE_V13_HISTORICAL_BASELINE'
        assert svc.DECLINE_RATE_RATIO == 0.5

    def test_current_identification_and_history_selection(self, app, db, user):
        past = [_analysis(user.id, days_ago=d, threat_score=20.0 + d)
                for d in (1, 2, 3)]
        current = _analysis(user.id, threat_score=50.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        assert out['available'] is True
        assert out['current_analysis_id'] == current.id
        assert out['scope'] == 'user'
        assert current.id not in out['historical_analysis_ids']
        assert sorted(out['historical_analysis_ids']) == \
            out['historical_analysis_ids']  # deterministic
        assert set(out['historical_analysis_ids']) == {a.id for a in past}

    def test_current_excluded_from_own_history(self, app, db, user):
        current = _analysis(user.id, threat_score=50.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        assert current.id not in out['historical_analysis_ids']

    def test_per_user_isolation(self, app, db, user):
        other = _other_user(db)
        _analysis(other.id, days_ago=1, threat_score=99.0)
        mine = [_analysis(user.id, days_ago=d, threat_score=10.0)
                for d in (1, 2, 3)]
        current = _analysis(user.id, threat_score=12.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        for a in mine:
            assert a.id in out['historical_analysis_ids']
        assert len(out['historical_analysis_ids']) == 3
        assert out['metrics']['threat']['baseline'] == pytest.approx(10.0)

    def test_history_bounds_respected(self, app, db, user):
        for d in range(1, 7):
            _analysis(user.id, days_ago=d, threat_score=10.0)
        current = _analysis(user.id, threat_score=10.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id, max_analyses=2)
        assert len(out['historical_analysis_ids']) == 2

    def test_window_enforcement(self, app, db, user):
        for d in (5, 6, 7):
            _analysis(user.id, days_ago=d, threat_score=10.0)
        _analysis(user.id, days_ago=400, threat_score=90.0)
        current = _analysis(user.id, threat_score=10.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id, window_days=90)
        assert len(out['historical_analysis_ids']) == 3
        assert out['metrics']['threat']['baseline'] == pytest.approx(10.0)

    def test_metric_comparison_and_delta(self, app, db, user):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=score, threat_score=score)
        current = _analysis(user.id, threat_score=36.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        threat = out['metrics']['threat']
        assert threat['current'] == pytest.approx(36.0)
        assert threat['baseline'] == pytest.approx(30.0)
        assert threat['delta'] == pytest.approx(6.0)
        assert threat['sample_size'] == 3
        assert threat['availability'] == 'moderately_above_baseline'
        assert len(threat['history']) == 3
        for point in threat['history']:
            assert set(point) >= {'analysis_id', 'value', 'source_table',
                                  'source_id', 'ref'}
            assert point['source_table'] == 'threat_assessments'
            assert isinstance(point['source_id'], int)

    def test_unavailable_vs_zero(self, app, db, user):
        for _ in range(3):
            _analysis(user.id, days_ago=1)  # no threat rows anywhere
        current = _analysis(user.id)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        threat = out['metrics']['threat']
        assert threat['availability'] == 'unavailable'
        assert threat['current'] is None
        assert threat['baseline'] is None  # NOT zero

    def test_missing_and_insufficient_history(self, app, db, user):
        current = _analysis(user.id, threat_score=10.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        assert out['metrics']['threat']['availability'] == 'insufficient_history'
        _analysis(user.id, days_ago=1, threat_score=10.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        assert out['metrics']['threat']['availability'] == 'insufficient_history'

    def test_disabled_flag(self, app, db, user):
        app.config['ENABLE_V13_HISTORICAL_BASELINE'] = False
        out = CrossAnalysisService().compare_with_history(user.id, 1)
        assert out['available'] is False

    def test_idempotent_results(self, app, db, user):
        _analysis(user.id, days_ago=1, threat_score=10.0)
        current = _analysis(user.id, threat_score=10.0)
        svc = CrossAnalysisService()
        assert svc.compare_with_history(user.id, current.id) == \
            svc.compare_with_history(user.id, current.id)

    def test_limitations_and_disclaimer(self, app, db, user):
        current = _analysis(user.id, threat_score=10.0)
        out = CrossAnalysisService().compare_with_history(
            user.id, current.id)
        assert any('same user' in note for note in out['limitations'])
        assert 'causal' in out['disclaimer']
        text = ' '.join(out['limitations']) + out['disclaimer']
        assert 'causes' not in text and 'proves' not in text


# --------------------------------------------------------------------------
# Narrative evolution
# --------------------------------------------------------------------------
class TestNarrativeEvolution:
    def test_only_current_narratives_evaluated(self, app, db, user):
        current = _analysis(user.id)
        other_analysis = _analysis(user.id, days_ago=5)
        n_current = _narrative(user.id, 'Current Topic')
        n_old = _narrative(user.id, 'Old Topic')
        _occurrence(n_current.id, current.id, user.id, days_ago=0)
        _occurrence(n_old.id, other_analysis.id, user.id, days_ago=5)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        names = [n['normalized_name'] for n in out['narratives']]
        assert 'current topic' in names
        assert 'old topic' not in names

    def test_emerging(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Brand New Claim')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        assert out['narratives'][0]['state'] == 'emerging'

    def test_persistent(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Ongoing Theme')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        for d in (60, 70):
            a = _analysis(user.id, days_ago=d)
            _occurrence(n.id, a.id, user.id, days_ago=d)
        for d in (10, 20):
            a = _analysis(user.id, days_ago=d)
            _occurrence(n.id, a.id, user.id, days_ago=d)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        item = out['narratives'][0]
        assert item['state'] == 'persistent'
        assert item['prior_occurrences'] == 2
        assert item['recent_occurrences'] == 2

    def test_reappearing(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Returning Rumor')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        for d in (60, 70, 80):
            a = _analysis(user.id, days_ago=d)
            _occurrence(n.id, a.id, user.id, days_ago=d)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        item = out['narratives'][0]
        assert item['state'] == 'reappearing'
        assert item['recent_occurrences'] == 0

    def test_declining(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Fading Story')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        for d in (50, 55, 60, 65, 70, 75):
            a = _analysis(user.id, days_ago=d)
            _occurrence(n.id, a.id, user.id, days_ago=d)
        a = _analysis(user.id, days_ago=10)
        _occurrence(n.id, a.id, user.id, days_ago=10)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        assert out['narratives'][0]['state'] == 'declining'

    def test_deterministic_narrative_ordering(self, app, db, user):
        current = _analysis(user.id)
        for name in ('Zulu', 'Alpha', 'Mike'):
            n = _narrative(user.id, name)
            _occurrence(n.id, current.id, user.id, days_ago=0)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        names = [n['normalized_name'] for n in out['narratives']]
        assert names == sorted(names)

    def test_state_edge_cases(self, app, db, user):
        cls = CrossAnalysisService.classify_evolution
        assert cls(0, 5, 5)[0] == 'unavailable'  # not in current
        assert cls(1, 0, 0)[0] == 'emerging'
        assert cls(1, 0, 0, unpositioned=2)[0] == 'insufficient_history'
        assert cls(1, 3, 0)[0] == 'reappearing'
        assert cls(1, 6, 1)[0] == 'declining'  # 1.0 < 3.0*0.5
        assert cls(1, 2, 2)[0] == 'persistent'
        assert cls(-1, 2, 2)[0] == 'unavailable'  # invalid counts

    def test_out_of_window_history_ignored(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Ancient Tale')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        a = _analysis(user.id, days_ago=400)
        _occurrence(n.id, a.id, user.id, days_ago=400)
        out = CrossAnalysisService().narrative_evolution(
            user.id, current.id, window_days=90)
        assert out['narratives'][0]['state'] == 'emerging'

    def test_evidence_refs_are_real(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Sourced Story')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        a = _analysis(user.id, days_ago=60)
        occ = _occurrence(n.id, a.id, user.id, days_ago=60)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        refs = out['narratives'][0]['evidence_refs']
        assert len(refs) >= 1
        for ref in refs:
            assert ref['source_table'] == 'narrative_occurrences'
            assert isinstance(ref['source_id'], int)
        assert any(r['source_id'] == occ.id for r in refs)

    def test_timestamps_are_real(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Dated Story')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        item = out['narratives'][0]
        assert item['first_seen_at'] is not None
        assert item['last_seen_at'] is not None

    def test_no_cross_user_leakage(self, app, db, user):
        other = _other_user(db)
        current = _analysis(user.id)
        n = _narrative(other.id, 'Foreign Narrative')
        _occurrence(n.id, current.id, other.id, days_ago=0)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        assert out['narratives'] == []


# --------------------------------------------------------------------------
# Compatibility: evidence chain + Phase B + non-causal wording
# --------------------------------------------------------------------------
class TestPhaseCCompatibility:
    def test_evidence_chain_compatibility(self, app, db, user):
        from services.v13_evidence_chain_service import V13EvidenceChainService
        a = _analysis(user.id, threat_score=30.0)
        n = _narrative(user.id, 'Chained Story')
        _occurrence(n.id, a.id, user.id, days_ago=0)
        chain = V13EvidenceChainService().analyze(a, components={})
        compared = CrossAnalysisService().compare_with_history(user.id, a.id)
        evolved = CrossAnalysisService().narrative_evolution(user.id, a.id)
        assert chain['available'] is True
        assert compared['available'] is True
        assert evolved['available'] is True
        assert ThreatAssessment.query.filter_by(analysis_id=a.id).count() == 1

    def test_phase_b_baseline_still_works(self, app, db, user):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=int(score), threat_score=score)
        current = _analysis(user.id, threat_score=32.0)
        out = HistoricalContextService().compute_baseline(
            user.id, current_analysis_id=current.id)
        assert out['available'] is True
        assert out['metrics']['threat']['baseline'] == pytest.approx(30.0)

    def test_metric_series_shared_with_phase_b(self, app, db, user):
        _analysis(user.id, days_ago=1, threat_score=25.0)
        current = _analysis(user.id, threat_score=25.0)
        series = HistoricalContextService().get_metric_series(
            user.id, [a.id for a in Analysis.query.filter(
                Analysis.user_id == user.id,
                Analysis.id != current.id).all()], current.id)
        assert series['threat']['current'][1] == pytest.approx(25.0)
        assert len(series['threat']['history']) == 1

    def test_non_causal_wording(self, app, db, user):
        current = _analysis(user.id)
        n = _narrative(user.id, 'Causal Check')
        _occurrence(n.id, current.id, user.id, days_ago=0)
        out = CrossAnalysisService().narrative_evolution(user.id, current.id)
        blob = (out['disclaimer'] + ' '.join(
            n.get('limitation', '') + n.get('state', '')
            for n in out['narratives'])).lower()
        assert 'caus' in blob  # disclaimer mentions non-causal
        assert 'caused by' not in blob and 'proves' not in blob

    def test_repeated_calls_stable(self, app, db, user):
        _analysis(user.id, days_ago=1, threat_score=10.0)
        current = _analysis(user.id, threat_score=10.0)
        svc = CrossAnalysisService()
        assert svc.narrative_evolution(user.id, current.id) == \
            svc.narrative_evolution(user.id, current.id)
