"""Shared V12 export/result context builder.

This is the single source of truth for the V11/V12 intelligence data contract
used by the analysis result page and every export format (JSON, CSV, XLSX,
PDF, DOCX). It only reads from the existing V11/V12 read APIs and never
recomputes intelligence or scores. Each section degrades to an explicit
unavailable/empty state instead of raising.
"""
from flask import current_app

from services.threat_assessment_service import ThreatAssessmentService
from services.narrative_intelligence_service import NarrativeIntelligenceService
from services.coordination_intelligence_service import CoordinationIntelligenceService
from services.propagation_intelligence_service import PropagationIntelligenceService
from services.temporal_intelligence_service import TemporalIntelligenceService

threat_service = ThreatAssessmentService()
narrative_service = NarrativeIntelligenceService()
coordination_service = CoordinationIntelligenceService()
propagation_service = PropagationIntelligenceService()
temporal_service = TemporalIntelligenceService()


def build_v12_context(analysis_id, user_id, narrative_limit=5,
                      temporal_limit=5):
    """Bounded read-only V11/V12 intelligence context.

    Returns a dict with the same keys the result page template consumes:
    ``threat``, ``narratives``, ``coordination``, ``propagation``, ``temporal``.
    Empty lists / ``None`` mean "no data or disabled"; consumers render those
    as "Unavailable" / "Insufficient evidence", never as zero.
    """
    v12 = {}

    try:
        if not current_app.config.get('ENABLE_THREAT_ASSESSMENT', True):
            v12['threat'] = None
        else:
            v12['threat'] = threat_service.get_analysis_threat_assessment(
                analysis_id)
    except Exception:
        v12['threat'] = None

    try:
        if not current_app.config.get('ENABLE_NARRATIVE_INTELLIGENCE', True):
            v12['narratives'] = []
        else:
            v12['narratives'] = narrative_service.get_analysis_narratives(
                analysis_id, limit=narrative_limit)
    except Exception:
        v12['narratives'] = []

    try:
        if not current_app.config.get('ENABLE_COORDINATION_DETECTION', True):
            v12['coordination'] = []
        else:
            v12['coordination'] = coordination_service.get_analysis_coordination(
                analysis_id)
    except Exception:
        v12['coordination'] = []

    try:
        if not current_app.config.get('ENABLE_PROPAGATION_INTELLIGENCE', True):
            v12['propagation'] = []
        else:
            v12['propagation'] = propagation_service.get_analysis_propagation(
                analysis_id)
    except Exception:
        v12['propagation'] = []

    temporal = []
    try:
        if current_app.config.get('ENABLE_TEMPORAL_INTELLIGENCE', True):
            for n in v12['narratives'][:temporal_limit]:
                row = temporal_service.get_narrative_temporal(
                    n['id'], user_id=user_id)
                if row and row.get('available'):
                    temporal.append(row)
    except Exception:
        temporal = []
    v12['temporal'] = temporal

    return v12


HEURISTIC_DISCLAIMER = (
    'These results are heuristic and non-causal. They describe observed '
    'patterns in comments and platform activity and do not prove intent, '
    'authorship, conspiracy, or actual causal propagation.'
)

THREAT_DISCLAIMER = (
    'Threat assessment is a heuristic, non-causal combination of available '
    'component signals. It indicates relative risk based on observed '
    'indicators and is not a definitive classification or proof of intent, '
    'authorship, or coordination.'
)
