"""V12 Phase N - Configuration & Engine Bounds tests.

Phase N surfaces the existing V12 controls (feature flags + bounded-engine
configuration) into the official configuration surface (``config/settings.py``
and ``.env.example``) and verifies the engines actually consume them.

These tests verify:

* every V12 feature flag and bound default exists in ``app.config`` with the
  documented default,
* env-var overrides are honoured (matching the project's existing config
  conventions: ``int``/``float``/``bool`` parsing),
* ``current_app.config`` overrides used by the rest of the test-suite still
  work (services read via ``_cfg``),
* the engines consume the surfaced values (bounds and flags),
* invalid config values are rejected per the project's existing conventions
  (``int(...)``/``float(...)`` raising ``ValueError``),
* feature-disable behaviour: a disabled stage yields unavailable/absent data.

No engine/scoring behaviour is changed by the assertions here.
"""
import importlib
import re

import pytest

from config import settings as settings_module


# --------------------------------------------------------------------------
# Documented defaults (must match config/settings.py and .env.example)
# --------------------------------------------------------------------------

V12_DEFAULTS = {
    # Narrative Intelligence
    'ENABLE_NARRATIVE_INTELLIGENCE': True,
    'MAX_NARRATIVES_PER_ANALYSIS': 12,
    'NARRATIVE_MAX_PHRASES_PER_DOCUMENT': 20,
    'NARRATIVE_MAX_COMMENTS_SCANNED': 300,
    'NARRATIVE_MIN_DOCUMENT_FREQUENCY': 2,
    'NARRATIVE_MERGE_THRESHOLD': 0.85,
    'NARRATIVE_REUSE_THRESHOLD': 0.90,
    'NARRATIVE_MAX_REUSE_CANDIDATES': 200,
    'NARRATIVE_MAX_RISK_CONTEXT_DOCS': 20,
    'NARRATIVE_MAX_EVIDENCE_SAMPLES': 3,
    'NARRATIVE_MAX_EVIDENCE_TERMS': 8,
    'NARRATIVE_MAX_EVIDENCE_ENTITIES': 5,
    'NARRATIVE_EVIDENCE_SNIPPET_CHARS': 160,
    # Coordination Detection
    'ENABLE_COORDINATION_DETECTION': True,
    'COORDINATION_MAX_COMMENTS_SCANNED': 300,
    'COORDINATION_COMPARISON_BUDGET': 2000,
    'COORDINATION_MAX_LEADERS': 50,
    'COORDINATION_TIMING_WINDOW_SECONDS': 300,
    'COORDINATION_TIMING_PAIR_BUDGET': 2000,
    'COORDINATION_MAX_EVIDENCE_SAMPLES': 3,
    'COORDINATION_MAX_EVIDENCE_ENTITIES': 5,
    'COORDINATION_EVIDENCE_SNIPPET_CHARS': 120,
    # Propagation Intelligence
    'ENABLE_PROPAGATION_INTELLIGENCE': True,
    'PROPAGATION_MAX_CANDIDATES': 15,
    'PROPAGATION_MAX_COMPARISONS': 40,
    'PROPAGATION_MAX_EVENTS': 25,
    'PROPAGATION_MAX_EVIDENCE_ENTITIES': 5,
    # Temporal Intelligence
    'ENABLE_TEMPORAL_INTELLIGENCE': True,
    'TEMPORAL_MAX_NARRATIVES': 50,
    'TEMPORAL_MAX_OCCURRENCES': 200,
    # Threat Assessment
    'ENABLE_THREAT_ASSESSMENT': True,
    'THREAT_MAX_INDICATORS': 20,
    'THREAT_MAX_REASONS': 20,
    'THREAT_MAX_LIMITATIONS': 10,
}

# Keys surfaced in settings.py that correspond to evidence/list bounds whose
# class-constant default must also live in the consuming service (used to
# check engine consumption below).
EVIDENCE_BOUND_KEYS = [
    'NARRATIVE_MAX_RISK_CONTEXT_DOCS',
    'NARRATIVE_MAX_EVIDENCE_SAMPLES',
    'NARRATIVE_MAX_EVIDENCE_TERMS',
    'NARRATIVE_MAX_EVIDENCE_ENTITIES',
    'NARRATIVE_EVIDENCE_SNIPPET_CHARS',
    'COORDINATION_MAX_EVIDENCE_SAMPLES',
    'COORDINATION_MAX_EVIDENCE_ENTITIES',
    'COORDINATION_EVIDENCE_SNIPPET_CHARS',
    'PROPAGATION_MAX_EVIDENCE_ENTITIES',
    'THREAT_MAX_INDICATORS',
    'THREAT_MAX_REASONS',
    'THREAT_MAX_LIMITATIONS',
]


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''


# --------------------------------------------------------------------------
# Surface presence + defaults
# --------------------------------------------------------------------------

def test_all_v12_defaults_present_in_app_config(app):
    """Every V12 flag/bound from the official surface is loaded into
    ``app.config`` with the documented default."""
    for key, default in V12_DEFAULTS.items():
        assert key in app.config, f'{key} missing from app.config'
        assert app.config[key] == default, (
            f'{key} default is {app.config[key]!r}, expected {default!r}')


def test_all_v12_keys_in_settings_config_class():
    """Every documented V12 key is defined on the Config class itself."""
    for key in V12_DEFAULTS:
        assert hasattr(settings_module.Config, key), (
            f'{key} not defined in config/settings.py')


def test_v12_defaults_match_class_constants(app):
    """Surfaced defaults equal the engines' class-constant defaults, so the
    official surface introduces no behaviour change."""
    from services.narrative_intelligence_service import NarrativeIntelligenceService
    from services.coordination_intelligence_service import CoordinationIntelligenceService
    from services.propagation_intelligence_service import PropagationIntelligenceService
    from services.temporal_intelligence_service import TemporalIntelligenceService
    from services.threat_assessment_service import ThreatAssessmentService

    mapping = {
        'NARRATIVE_MAX_RISK_CONTEXT_DOCS': ('narrative', 'MAX_RISK_CONTEXT_DOCS'),
        'NARRATIVE_MAX_EVIDENCE_SAMPLES': ('narrative', 'MAX_EVIDENCE_SAMPLES'),
        'NARRATIVE_MAX_EVIDENCE_TERMS': ('narrative', 'MAX_EVIDENCE_TERMS'),
        'NARRATIVE_MAX_EVIDENCE_ENTITIES': ('narrative', 'MAX_EVIDENCE_ENTITIES'),
        'NARRATIVE_EVIDENCE_SNIPPET_CHARS': ('narrative', 'EVIDENCE_SNIPPET_CHARS'),
        'COORDINATION_MAX_EVIDENCE_SAMPLES': ('coordination', 'MAX_EVIDENCE_SAMPLES'),
        'COORDINATION_MAX_EVIDENCE_ENTITIES': ('coordination', 'MAX_EVIDENCE_ENTITIES'),
        'COORDINATION_EVIDENCE_SNIPPET_CHARS': ('coordination', 'EVIDENCE_SNIPPET_CHARS'),
        'PROPAGATION_MAX_EVIDENCE_ENTITIES': ('propagation', 'MAX_EVIDENCE_ENTITIES'),
        'THREAT_MAX_INDICATORS': ('threat', 'MAX_INDICATORS'),
        'THREAT_MAX_REASONS': ('threat', 'MAX_REASONS'),
        'THREAT_MAX_LIMITATIONS': ('threat', 'MAX_LIMITATIONS'),
    }
    instances = {
        'narrative': NarrativeIntelligenceService(),
        'coordination': CoordinationIntelligenceService(),
        'propagation': PropagationIntelligenceService(),
        'temporal': TemporalIntelligenceService(),
        'threat': ThreatAssessmentService(),
    }
    for cfg_key, (svc, attr) in mapping.items():
        assert app.config[cfg_key] == getattr(instances[svc], attr), (
            f'{cfg_key} surface default {app.config[cfg_key]!r} != '
            f'{svc}.{attr} {getattr(instances[svc], attr)!r}')


# --------------------------------------------------------------------------
# Env-var overrides (existing conventions)
# --------------------------------------------------------------------------

def test_env_override_int(monkeypatch):
    monkeypatch.setenv('NARRATIVE_MAX_EVIDENCE_SAMPLES', '7')
    monkeypatch.setenv('COORDINATION_COMPARISON_BUDGET', '5000')
    importlib.reload(settings_module)
    try:
        assert settings_module.Config.NARRATIVE_MAX_EVIDENCE_SAMPLES == 7
        assert settings_module.Config.COORDINATION_COMPARISON_BUDGET == 5000
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


def test_env_override_float(monkeypatch):
    monkeypatch.setenv('NARRATIVE_MERGE_THRESHOLD', '0.50')
    importlib.reload(settings_module)
    try:
        assert settings_module.Config.NARRATIVE_MERGE_THRESHOLD == 0.50
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


def test_env_override_bool(monkeypatch):
    monkeypatch.setenv('ENABLE_THREAT_ASSESSMENT', 'false')
    importlib.reload(settings_module)
    try:
        assert settings_module.Config.ENABLE_THREAT_ASSESSMENT is False
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


def test_invalid_int_env_raises(monkeypatch):
    """Matches the project convention: invalid int env values raise at load."""
    monkeypatch.setenv('NARRATIVE_MAX_EVIDENCE_SAMPLES', 'not-a-number')
    try:
        with pytest.raises(ValueError):
            importlib.reload(settings_module)
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


def test_invalid_float_env_raises(monkeypatch):
    monkeypatch.setenv('NARRATIVE_MERGE_THRESHOLD', 'abc')
    try:
        with pytest.raises(ValueError):
            importlib.reload(settings_module)
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


# --------------------------------------------------------------------------
# current_app.config overrides still work + engines consume surfaced values
# --------------------------------------------------------------------------

def test_current_app_config_override_still_consumed(app):
    """Setting ``app.config['NARRATIVE_MAX_EVIDENCE_TERMS']`` changes the
    engine's output bounds (no duplicated config logic inside services)."""
    from services.narrative_intelligence_service import NarrativeIntelligenceService

    service = NarrativeIntelligenceService()
    app.config['NARRATIVE_MAX_EVIDENCE_TERMS'] = 2
    candidate = {'surface_counts': {'alpha': 5, 'beta': 4, 'gamma': 3},
                 'normalized_name': 'seed'}
    keywords = service._candidate_keywords(candidate)
    assert len(keywords) <= 2


def test_feature_flag_disables_engine(app, db, analysis):
    """A disabled V12 flag makes the stage unavailable (feature-disable)."""
    from services.coordination_intelligence_service import CoordinationIntelligenceService
    from services.propagation_intelligence_service import PropagationIntelligenceService
    from services.temporal_intelligence_service import TemporalIntelligenceService

    for flag, svc in [
        ('ENABLE_COORDINATION_DETECTION', CoordinationIntelligenceService()),
        ('ENABLE_PROPAGATION_INTELLIGENCE', PropagationIntelligenceService()),
        ('ENABLE_TEMPORAL_INTELLIGENCE', TemporalIntelligenceService()),
    ]:
        app.config[flag] = False
        result = svc.analyze(analysis=analysis)
        assert result.get('available') is False, (
            f'{flag} disable did not mark the stage unavailable')
        app.config[flag] = True


def test_env_file_mentions_every_surfaced_key():
    """.env.example documents every surfaced V12 key (no config-only-in-code)."""
    env_example = (settings_module.os.path.join(
        settings_module.os.path.dirname(settings_module.os.path.dirname(
            settings_module.__file__)), '.env.example'))
    with open(env_example) as fh:
        content = fh.read()
    for key in V12_DEFAULTS:
        assert re.search(rf'^{key}=', content, re.M), (
            f'{key} missing from .env.example')
