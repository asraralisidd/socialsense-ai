"""V12 Phase I - Dashboard Intelligence UI tests.

Covers: empty/partial/full V12 dashboard rendering, unavailable != zero
presentation, disabled intelligence flags, and each V12 section (threat,
narrative, coordination, propagation, temporal, authenticity). Also verifies
the existing dashboard regression and route smoke tests stay green.
"""
import pytest
from datetime import datetime, timezone

from database import db
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.coordination_signal import CoordinationSignal
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment


def _now():
    return datetime(2024, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Helpers to seed V12 rows directly (bounded, deterministic)
# --------------------------------------------------------------------------
def _seed_threat(db, user, analysis_id, score=62.5, level='Elevated'):
    ta = ThreatAssessment(
        analysis_id=analysis_id,
        user_id=user.id,
        overall_threat_score=score,
        threat_level=level,
        confidence=72.0,
        evidence_coverage=0.66,
        agreement_score=80.0,
        authenticity_score=40.0,
        coordination_score=30.0,
        narrative_risk_score=62.5,
        propagation_score=None,
        temporal_score=None,
        entity_risk_score=None,
        component_scores={
            'authenticity': 40.0, 'coordination': 30.0, 'narrative': 62.5,
        },
        component_weights={'authenticity': 0.25, 'coordination': 0.25,
                           'narrative': 0.20},
        available_components=['authenticity', 'coordination', 'narrative'],
        missing_components=['propagation', 'temporal', 'entity'],
        capability_labels={
            'authenticity': 'heuristic', 'coordination': 'heuristic',
            'narrative': 'heuristic', 'propagation': 'unavailable',
            'temporal': 'unavailable', 'entity': 'unavailable',
        },
        summary='Heuristic threat assessment.',
        reasons=['Heuristic weighted combination.'],
        indicators=['signal'],
        limitations=['Not a trained ML classifier.'],
        assessment_method='heuristic_weighted',
    )
    db.session.add(ta)
    db.session.commit()
    return ta


def _seed_narrative(db, user, analysis_id, name='Election fraud claims'):
    narrative = Narrative(
        user_id=user.id,
        name=name,
        normalized_name=name.lower(),
        category='political',
        risk_score=62.5,
        confidence=0.7,
        growth_score=45.0,
        occurrence_count=2,
        platform_count=2,
        keywords=['fraud', 'election'],
        entity_names=['PERSON_A'],
        evidence={'sources': ['youtube', 'reddit']},
        detection_method='heuristic',
        first_seen_at=_now(),
        last_seen_at=_now(),
    )
    db.session.add(narrative)
    db.session.flush()
    db.session.add(NarrativeOccurrence(
        narrative_id=narrative.id, analysis_id=analysis_id, user_id=user.id,
        platform='youtube', source='comment', relevance_score=0.8,
        risk_score=62.5, match_count=3,
        evidence={'samples': ['sample']}, occurred_at=_now(),
    ))
    db.session.commit()
    return narrative


def _seed_coordination(db, user, analysis_id):
    signal = CoordinationSignal(
        analysis_id=analysis_id,
        user_id=user.id,
        signal_type='repeated_content',
        score=58.0,
        confidence=0.65,
        level='elevated',
        cluster_size=4,
        comparisons_performed=120,
        comparisons_truncated=True,
        window_seconds=3600,
        summary='Repeated phrasing across comments.',
        reasons=['Repeated content'],
        indicators=['repetition'],
        evidence={'sample_text': 'repeat'},
        related_entities=['PERSON_A'],
        detection_method='heuristic',
        detected_at=_now(),
    )
    db.session.add(signal)
    db.session.commit()
    return signal


def _seed_propagation(db, user, source_id, target_id):
    event = PropagationEvent(
        user_id=user.id,
        narrative_id=None,
        source_analysis_id=source_id,
        target_analysis_id=target_id,
        source_platform='youtube',
        target_platform='reddit',
        source_ref='vid1',
        target_ref='post1',
        relationship_type='potentially_propagated',
        direction='source_to_target',
        propagation_score=55.0,
        confidence=0.6,
        similarity_score=0.7,
        lag_seconds=7200,
        shared_entities=['PERSON_A'],
        reasons=['Shared entities and timing'],
        evidence={'samples': ['sample']},
        detection_method='heuristic',
        occurred_at=_now(),
    )
    db.session.add(event)
    db.session.commit()
    return event


def _run_youtube(app, db, user):
    from services.analysis_service import AnalysisService
    result = AnalysisService().create_youtube_analysis(
        user.id, 'dQw4w9WgXcQ', comment_limit=5)
    assert result['success'] is True
    return result['analysis_id']


# --------------------------------------------------------------------------
# Empty / partial data
# --------------------------------------------------------------------------
class TestDashboardEmpty:
    def test_empty_dashboard_renders_unavailable_states(self, logged_in_client):
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'V12 Intelligence is heuristic and non-causal' in content
        assert 'Unavailable' in content
        assert 'insufficient evidence' in content.lower()

    def test_empty_threat_section_shows_unavailable(self, logged_in_client):
        resp = logged_in_client.get('/dashboard/')
        content = resp.data.decode()
        assert 'Threat Assessment' in content
        assert 'Unavailable — insufficient evidence' in content


class TestDashboardPartial:
    def test_threat_only_renders_with_missing_components(self, app, db, user,
                                                         logged_in_client):
        _seed_threat(db, user, 999)
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Threat Assessment' in content
        assert 'Elevated' in content
        assert '62.5' in content
        assert 'heuristic' in content
        assert 'Limitations' in content
        # propagation/temporal/entity are missing -> must NOT render as 0
        assert content.count('Unavailable') >= 3

    def test_narrative_section_renders_growth(self, app, db, user, analysis,
                                              logged_in_client):
        _seed_narrative(db, user, analysis.id)
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Narratives &amp; Growth' in content
        assert 'Election fraud claims' in content
        assert 'cross-platform' in content
        assert '45.0' in content  # growth score
        assert '62.5' in content  # risk score

    def test_coordination_section_shows_truncation_state(self, app, db, user,
                                                         analysis,
                                                         logged_in_client):
        _seed_coordination(db, user, analysis.id)
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Coordination Signals' in content
        assert 'Repeated Content' in content
        assert 'elevated' in content
        assert 'Comparisons' in content
        assert 'truncated' in content

    def test_propagation_section_shows_lag_and_platforms(self, app, db, user,
                                                         analysis,
                                                         logged_in_client):
        _seed_propagation(db, user, analysis.id, analysis.id + 1)
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Propagation Relationships' in content
        assert 'youtube' in content and 'reddit' in content
        assert '2h' in content  # lag 7200s
        assert 'do not prove' in content

    def test_temporal_section_renders_growth(self, app, db, user, analysis,
                                             logged_in_client):
        _seed_narrative(db, user, analysis.id)
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Temporal Intelligence' in content
        assert 'Growth' in content
        assert '45.0' in content


# --------------------------------------------------------------------------
# Full V12 data
# --------------------------------------------------------------------------
class TestDashboardFull:
    def test_full_v12_dashboard_renders_all_sections(self, app, db, user,
                                                     analysis,
                                                     logged_in_client):
        _seed_threat(db, user, analysis.id)
        _seed_narrative(db, user, analysis.id)
        _seed_coordination(db, user, analysis.id)
        _seed_propagation(db, user, analysis.id, analysis.id + 1)
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        content = resp.data.decode()
        for marker in ('Threat Assessment', 'Narratives &amp; Growth',
                       'Coordination Signals', 'Propagation Relationships',
                       'Temporal Intelligence', 'Authenticity Intelligence'):
            assert marker in content, marker


# --------------------------------------------------------------------------
# Disabled flags
# --------------------------------------------------------------------------
class TestDashboardDisabledFlags:
    def test_disabled_threat_flag_shows_unavailable(self, app, db, user,
                                                    analysis,
                                                    logged_in_client):
        app.config['ENABLE_THREAT_ASSESSMENT'] = False
        resp = logged_in_client.get('/dashboard/')
        assert resp.status_code == 200
        assert 'Unavailable — insufficient evidence' in resp.data.decode()


# --------------------------------------------------------------------------
# Unavailable != zero presentation
# --------------------------------------------------------------------------
class TestDashboardUnavailable:
    def test_authenticity_unavailable_when_no_videos(self, app, db, user,
                                                     logged_in_client):
        resp = logged_in_client.get('/dashboard/')
        content = resp.data.decode()
        assert 'Avg Authenticity' in content
        # no authenticity data -> Unavailable, not 0
        assert 'Unavailable' in content
        # the Avg Authenticity cell renders Unavailable, never a literal 0
        idx = content.find('Avg Authenticity')
        assert idx != -1
        cell = content[idx:idx + 120]
        assert 'Unavailable' in cell
        assert '0' not in cell.replace('Avg Authenticity', '')

    def test_entity_risk_unavailable_when_no_data(self, app, db, user,
                                                  logged_in_client):
        resp = logged_in_client.get('/dashboard/')
        content = resp.data.decode()
        assert 'Entity Risk' in content
        assert 'Unavailable' in content

    def test_missing_component_never_zero(self, app, db, user, analysis,
                                          logged_in_client):
        _seed_threat(db, user, analysis.id)
        resp = logged_in_client.get('/dashboard/')
        content = resp.data.decode()
        # propagation/temporal/entity components are missing -> Unavailable text
        for label in ('Propagation', 'Temporal', 'Entity Risk'):
            idx = content.find(label)
            assert idx != -1, label
        # the component_scores dict only has 3 keys; template renders Unavailable
        assert content.count('<em>Unavailable</em>') >= 3


# --------------------------------------------------------------------------
# Regression
# --------------------------------------------------------------------------
class TestDashboardRegression:
    def test_existing_dashboard_metrics_present(self, app, db, user, analysis,
                                                logged_in_client):
        resp = logged_in_client.get('/dashboard/')
        content = resp.data.decode()
        assert 'Total Analyses' in content
        assert 'Comments Processed' in content
        assert 'Recent Analyses' in content
        assert 'riskDistributionChart' in content
        assert 'sentimentChart' in content

    def test_route_smoke_all_200(self, app, db, user, analysis,
                                 logged_in_client):
        _seed_threat(db, user, analysis.id)
        assert logged_in_client.get('/dashboard/').status_code == 200
        assert logged_in_client.get('/analysis/history').status_code == 200
        assert logged_in_client.get(f'/analysis/{analysis.id}').status_code == 200
        assert logged_in_client.get(f'/export/csv/{analysis.id}').status_code == 200
        assert logged_in_client.get(f'/export/json/{analysis.id}').status_code == 200
        assert logged_in_client.get('/trends/').status_code == 200

    def test_dashboard_platform_filter(self, app, db, user, analysis,
                                       logged_in_client):
        assert logged_in_client.get('/dashboard/?platform=youtube').status_code == 200
        assert logged_in_client.get('/dashboard/?platform=reddit').status_code == 200
        assert logged_in_client.get('/dashboard/?platform=invalid').status_code == 200
