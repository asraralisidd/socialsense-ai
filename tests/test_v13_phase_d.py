"""V13 Phase D - Historical intelligence UI integration tests.

Result-page integration of the Phase B/C backends: section rendering,
unavailable/insufficient/zero distinctions, user scoping, evidence
verification badges, V13 failure isolation, and V12/dashboard/trends/auth
compatibility. No brittle HTML assertions beyond stable markers.
"""
from datetime import datetime, timedelta, timezone

import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.threat_assessment import ThreatAssessment


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _analysis(user_id, days_ago=None, threat_score=None, sentiment=60.0):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    if days_ago is not None:
        a.created_at = _now() - timedelta(days=days_ago)
    db.session.add(YouTubeAnalysis(
        analysis_id=a.id, video_id=f'vid{a.id}', video_title='T',
        channel_name='C', is_demo=True))
    db.session.add(CommentResult(
        analysis_id=a.id, comment_text='stored comment text', author='a1',
        sentiment_score=sentiment, toxicity_score=5.0,
        spam_score=10.0, duplicate_score=0.0))
    if threat_score is not None:
        db.session.add(ThreatAssessment(
            analysis_id=a.id, user_id=user_id,
            overall_threat_score=threat_score, threat_level='Low',
            confidence=50.0, evidence_coverage=0.5))
    db.session.commit()
    return a


def _narrative_occurrence(user_id, analysis_id, name, days_ago=0):
    n = Narrative(user_id=user_id, name=name,
                  normalized_name=name.lower(), risk_score=40.0)
    db.session.add(n)
    db.session.flush()
    occ = NarrativeOccurrence(narrative_id=n.id, analysis_id=analysis_id,
                              user_id=user_id, relevance_score=70.0)
    db.session.add(occ)
    db.session.flush()
    occ.occurred_at = _now() - timedelta(days=days_ago)
    db.session.commit()
    return n, occ


def _rich_analysis(user, days_ago=None, threat_score=32.0):
    return _analysis(user.id, days_ago=days_ago, threat_score=threat_score)


def _build_chain(analysis):
    from services.v13_evidence_chain_service import V13EvidenceChainService
    return V13EvidenceChainService().analyze(analysis, components={
        'narrative': {
            'available': True,
            'evidence': {'samples': [
                {'source': 'title', 'ref': 'title',
                 'snippet': 'stored video title text'},
            ]},
        },
    })


class TestV13ResultSections:
    def test_sections_load_for_authorized_user(self, logged_in_client, user, db):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=int(score), threat_score=score)
        current = _rich_analysis(user)
        _narrative_occurrence(user.id, current.id, 'Visible Story')
        _build_chain(current)
        resp = logged_in_client.get(f'/analysis/{current.id}')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Historical Context' in body
        assert 'Cross-Analysis Comparison' in body
        assert 'Narrative Evolution' in body
        assert 'Why This Assessment?' in body

    def test_baseline_values_rendered(self, logged_in_client, user, db):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=int(score), threat_score=score)
        current = _rich_analysis(user, threat_score=32.0)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert '30.0' in body  # baseline mean
        assert 'typical' in body

    def test_insufficient_history_state(self, logged_in_client, user, db):
        current = _rich_analysis(user)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert resp_ok(body)
        assert 'insufficient history' in body

    def test_unavailable_not_zero(self, logged_in_client, user, db):
        for _ in range(3):
            _analysis(user.id, days_ago=1)  # no threat rows at all
        current = _analysis(user.id)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert resp_ok(body)
        assert 'Unavailable' in body

    def test_zero_remains_zero(self, logged_in_client, user, db):
        for _ in range(3):
            _analysis(user.id, days_ago=1, threat_score=10.0)
        current = _analysis(user.id, threat_score=10.0)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert resp_ok(body)
        # narrative_activity is genuinely 0.0 -> rendered numerically
        assert 'Narrative Activity' in body
        assert '>0.0<' in body

    def test_current_not_shown_as_historical(self, logged_in_client, user, db):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=int(score), threat_score=score)
        current = _rich_analysis(user)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert '3 prior analyses' in body

    def test_evolution_states_render(self, logged_in_client, user, db):
        current = _rich_analysis(user)
        _narrative_occurrence(user.id, current.id, 'Fresh Claim')
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert 'emerging' in body
        assert 'Fresh Claim' in body

    def test_real_timestamps_displayed(self, logged_in_client, user, db):
        current = _rich_analysis(user)
        _narrative_occurrence(user.id, current.id, 'Dated Claim')
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        today = _now().date().isoformat()
        assert today in body

    def test_evidence_verified_badges(self, logged_in_client, user, db):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=int(score), threat_score=score)
        current = _rich_analysis(user)
        _narrative_occurrence(user.id, current.id, 'Evidenced Claim')
        _build_chain(current)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert 'Why This Assessment?' in body
        assert 'Verified' in body
        assert 'Unverified' in body  # title sample has no verifiable row

    def test_missing_evidence_handled(self, logged_in_client, user, db):
        current = _analysis(user.id)  # no threat row -> no chain
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert resp_ok(body)
        assert 'Why This Assessment?' not in body

    def test_no_fabricated_references(self, logged_in_client, user, db):
        current = _rich_analysis(user)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert 'comment:999999' not in body
        assert 'evidence_refs:nonexistent' not in body


def resp_ok(body):
    return 'Historical Context' in body or 'Cross-Analysis Comparison' in body


class TestV13IsolationAndCompatibility:
    def test_v13_disabled_leaves_v12_intact(self, logged_in_client, user, db, app):
        app.config['ENABLE_V13_HISTORICAL_BASELINE'] = False
        app.config['ENABLE_V13_EVIDENCE_CHAIN'] = False
        current = _rich_analysis(user)
        resp = logged_in_client.get(f'/analysis/{current.id}')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Cross-Analysis Comparison' not in body
        assert 'Why This Assessment?' not in body

    def test_v12_sections_still_present(self, logged_in_client, user, db):
        current = _rich_analysis(user)
        body = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert 'Threat Assessment' in body

    def test_dashboard_trends_intact(self, logged_in_client):
        assert logged_in_client.get('/dashboard/').status_code == 200
        assert logged_in_client.get('/trends/').status_code == 200

    def test_auth_required(self, client, user, db):
        current = _rich_analysis(user)
        resp = client.get(f'/analysis/{current.id}')
        assert resp.status_code == 302

    def test_no_cross_user_leakage(self, client, user, db):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='d_other', email='d_other@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        current = _rich_analysis(user)
        client.post('/auth/login', data={'email': 'd_other@x.com',
                                         'password': 'Other123'})
        resp = client.get(f'/analysis/{current.id}')
        assert resp.status_code == 302  # redirected, not leaked
        assert 'Historical Context' not in resp.get_data(as_text=True)

    def test_bounded_many_analyses(self, logged_in_client, user, db):
        for i in range(30):
            _analysis(user.id, days_ago=(i % 20) + 1, threat_score=10.0 + i)
        current = _rich_analysis(user)
        resp = logged_in_client.get(f'/analysis/{current.id}')
        assert resp.status_code == 200

    def test_repeat_loads_deterministic(self, logged_in_client, user, db):
        for score in (20.0, 30.0, 40.0):
            _analysis(user.id, days_ago=int(score), threat_score=score)
        current = _rich_analysis(user)
        logged_in_client.get(f'/analysis/{current.id}')  # consume login flash
        first = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        second = logged_in_client.get(f'/analysis/{current.id}').get_data(as_text=True)
        assert first == second
