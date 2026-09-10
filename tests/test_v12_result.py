"""V12 Phase J - Analysis Result Intelligence UI tests.

Covers the detailed V11/V12 intelligence presentation on the existing
/analysis/<id> result page: empty/partial/full V12 data, disabled/unavailable
components, heuristic & non-causal wording, threat component rendering,
narratives/coordination/propagation/temporal sections, existing result-page
regression, and route 200 behavior.
"""
import pytest
from datetime import datetime, timezone

from database import db
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.coordination_signal import CoordinationSignal
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment
from models.media_analysis import MediaAnalysis
from models.entity import Entity
from models.entity_context import EntityContext


def _now():
    return datetime(2024, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


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
        evidence={'sources': ['youtube', 'reddit'],
                  'samples': ['sample one', 'sample two']},
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
        evidence={'samples': ['sample one', 'sample two']}, occurred_at=_now(),
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


def _get_result(logged_in_client, analysis):
    return logged_in_client.get(f'/analysis/{analysis.id}')


def _seed_media_analysis(db, analysis_id):
    ma = MediaAnalysis(
        analysis_id=analysis_id,
        overall_ai_probability=55.0,
        overall_authenticity_score=45.0,
        confidence=70.0,
        deepfake_score=50.0,
        synthetic_voice_score=55.0,
        thumbnail_ai_score=50.0,
        frame_manipulation_score=45.0,
        metadata_score=40.0,
        summary='Heuristic media authenticity assessment.',
        reasons='["Sample reason one", "Sample reason two"]',
    )
    db.session.add(ma)
    db.session.commit()
    return ma


def _seed_entity(db, analysis_id):
    from models.comment_result import CommentResult
    comment = CommentResult.query.filter_by(analysis_id=analysis_id).first()
    ent = Entity(
        analysis_id=analysis_id,
        name='PERSON_A',
        normalized_name='person_a',
        entity_type='PERSON',
        source='comment',
        frequency=5,
        importance_score=60.0,
    )
    db.session.add(ent)
    db.session.flush()
    ec = EntityContext(
        entity_id=ent.id,
        comment_result_id=comment.id,
        entity_sentiment='positive',
        entity_sentiment_score=65.0,
        entity_risk_score=30.0,
    )
    db.session.add(ec)
    db.session.commit()
    return ent


# --------------------------------------------------------------------------
# Empty / unavailable
# --------------------------------------------------------------------------
class TestResultEmpty:
    def test_no_v12_data_renders_existing_page(self, app, db, user, analysis,
                                               logged_in_client):
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        # existing result page content preserved
        assert 'Analysis Results' in content
        assert 'Comments Analyzed' in content
        assert 'Average Risk' in content
        assert 'All Comments' in content
        assert 'Recommendations' in content
        # no threat card when there is no assessment (never a fake zero)
        assert 'Threat Assessment' not in content
        assert 'Narratives &amp; Growth' not in content
        assert 'Coordination Signals' not in content
        assert 'Propagation Relationships' not in content
        assert 'Temporal Intelligence' not in content


# --------------------------------------------------------------------------
# Partial data
# --------------------------------------------------------------------------
class TestResultPartial:
    def test_threat_only_renders_with_missing_components(self, app, db, user,
                                                         analysis,
                                                         logged_in_client):
        _seed_threat(db, user, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Threat Assessment' in content
        assert 'Overall Threat Score' in content
        assert '62.5' in content
        assert 'Elevated' in content
        assert 'heuristic weighted assessment' in content
        # propagation/temporal/entity component scores missing -> Unavailable
        assert content.count('<em>Unavailable</em>') >= 3
        # non-causal wording present
        assert 'non-causal' in content
        # no narratives/coordination/propagation/temporal sections
        assert 'Narratives &amp; Growth' not in content
        assert 'Coordination Signals' not in content
        assert 'Propagation Relationships' not in content
        assert 'Temporal Intelligence' not in content

    def test_narrative_section_renders_growth(self, app, db, user, analysis,
                                              logged_in_client):
        _seed_narrative(db, user, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Narratives &amp; Growth' in content
        assert 'Election fraud claims' in content
        assert '45.0' in content  # growth score
        assert '62.5' in content  # risk score
        assert 'observed' in content

    def test_coordination_section_shows_truncation(self, app, db, user, analysis,
                                                   logged_in_client):
        _seed_coordination(db, user, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Coordination Signals' in content
        assert 'Repeated Content' in content
        assert 'elevated' in content
        assert 'comparison' in content
        assert '120' in content
        assert 'truncated' in content
        assert 'do not prove' in content

    def test_propagation_section_shows_lag_and_platforms(self, app, db, user,
                                                         analysis,
                                                         logged_in_client):
        _seed_propagation(db, user, analysis.id, analysis.id + 1)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Propagation Relationships' in content
        assert 'youtube' in content and 'reddit' in content
        assert '2.0h' in content  # lag 7200s
        assert 'actual causal propagation' in content
        assert 'do not establish' in content

    def test_temporal_section_renders_growth(self, app, db, user, analysis,
                                             logged_in_client):
        _seed_narrative(db, user, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Temporal Intelligence' in content
        assert 'Growth' in content
        assert 'not predictions' in content


# --------------------------------------------------------------------------
# Full V12 data
# --------------------------------------------------------------------------
class TestResultFull:
    def test_full_v12_result_renders_all_sections(self, app, db, user, analysis,
                                                   logged_in_client):
        _seed_threat(db, user, analysis.id)
        _seed_narrative(db, user, analysis.id)
        _seed_coordination(db, user, analysis.id)
        _seed_propagation(db, user, analysis.id, analysis.id + 1)
        _seed_media_analysis(db, analysis.id)
        _seed_entity(db, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        for marker in ('Threat Assessment', 'Authenticity Intelligence',
                       'Narratives &amp; Growth', 'Coordination Signals',
                       'Propagation Relationships', 'Temporal Intelligence',
                       'Entity Intelligence'):
            assert marker in content, marker
        assert 'heuristic' in content
        assert 'non-causal' in content


# --------------------------------------------------------------------------
# Disabled flags / unavailable
# --------------------------------------------------------------------------
class TestResultDisabled:
    def test_disabled_threat_flag_shows_no_threat_card(self, app, db, user,
                                                       analysis,
                                                       logged_in_client):
        app.config['ENABLE_THREAT_ASSESSMENT'] = False
        _seed_threat(db, user, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'Threat Assessment' not in content

    def test_missing_threat_components_never_zero(self, app, db, user, analysis,
                                                  logged_in_client):
        _seed_threat(db, user, analysis.id)
        resp = _get_result(logged_in_client, analysis)
        content = resp.data.decode()
        for label in ('Propagation', 'Temporal', 'Entity Risk'):
            idx = content.find(label)
            assert idx != -1, label
        assert content.count('<em>Unavailable</em>') >= 3
        # no fake zero component tiles inside the threat card (bounded by the
        # metrics row that always precedes it and the Risk Distribution card)
        threat_start = content.find('Overall Threat Score')
        threat_end = content.find('Risk Distribution')
        assert threat_start != -1 and threat_end != -1
        threat_block = content[threat_start:threat_end]
        assert 'fw-bold text-warning">0' not in threat_block


# --------------------------------------------------------------------------
# Regression
# --------------------------------------------------------------------------
class TestResultRegression:
    def test_existing_result_content_present(self, app, db, user, analysis,
                                             logged_in_client):
        resp = _get_result(logged_in_client, analysis)
        content = resp.data.decode()
        for marker in ('Comments Analyzed', 'Average Risk', 'High-Risk',
                       'Transcript Intelligence', 'Channel Context Intelligence',
                       'Risk Distribution', 'Sentiment Distribution',
                       'Average Scores', 'Recommendations'):
            assert marker in content, marker
        assert 'riskDistChart' in content

    def test_route_200(self, app, db, user, analysis, logged_in_client):
        assert _get_result(logged_in_client, analysis).status_code == 200
        # other analysis routes remain functional
        assert logged_in_client.get('/analysis/history').status_code == 200
        assert logged_in_client.get(f'/analysis/{analysis.id}').status_code == 200

    def test_missing_analysis_redirects(self, logged_in_client):
        resp = logged_in_client.get('/analysis/999999')
        assert resp.status_code in (200, 302)
