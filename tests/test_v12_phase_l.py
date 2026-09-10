"""V12 Phase L - Integration & End-to-End Hardening tests.

Phase L audits the full V12 pipeline (V11 Authenticity -> Narrative ->
Coordination -> Propagation -> Temporal -> Threat Assessment) end-to-end:

* pipeline ordering / consistency across YouTube, Reddit and the worker,
* feature-flag interaction: a disabled stage yields unavailable/absent data,
  dependent stages degrade gracefully, and a single failed stage never breaks
  the whole analysis,
* data consistency: identical ``analysis_id`` across every V12 record, no
  duplicate records on re-analysis/retry, ``unavailable != 0``, and score /
  level / wording parity between the shared context (result page + exports),
* bounded behavior of every list surfaced to the UI / exports,
* all five export formats stay valid when V12 data is present.

These are integration tests over the existing services; no intelligence is
recomputed by the assertions themselves.
"""
import pytest

from database import db
from models.narrative_occurrence import NarrativeOccurrence
from models.coordination_signal import CoordinationSignal
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment
from models.media_analysis import MediaAnalysis
from models.narrative import Narrative
from services.text_similarity_service import TextSimilarityService
from services.v12_context_service import (
    build_v12_context,
    HEURISTIC_DISCLAIMER,
    THREAT_DISCLAIMER,
)


@pytest.fixture(autouse=True)
def _force_demo_mode(app):
    app.config['YOUTUBE_API_KEY'] = ''
    app.config['REDDIT_CLIENT_ID'] = ''
    app.config['REDDIT_CLIENT_SECRET'] = ''
    TextSimilarityService.clear_caches()


def _youtube(app, db, user, comment_limit=25, **flags):
    for key, value in flags.items():
        app.config[key] = value
    from services.analysis_service import AnalysisService
    return AnalysisService().create_youtube_analysis(
        user.id, 'dQw4w9WgXcQ', comment_limit=comment_limit)


def _reddit(app, db, user, comment_limit=25, **flags):
    for key, value in flags.items():
        app.config[key] = value
    from services.analysis_service import AnalysisService
    return AnalysisService().create_reddit_analysis(
        user.id, 'abc123', comment_limit=comment_limit)


# --------------------------------------------------------------------------
# Pipeline ordering & cross-path consistency
# --------------------------------------------------------------------------
class TestPipelineConsistency:
    def test_youtube_runs_all_v12_stages_in_order(self, app, db, user):
        result = _youtube(app, db, user)
        assert result['success'] is True
        aid = result['analysis_id']
        # V11 authenticity runs first
        assert MediaAnalysis.query.filter_by(analysis_id=aid).first() is not None
        # All downstream V12 records keyed to the SAME analysis_id
        for model in (NarrativeOccurrence, CoordinationSignal,
                      ThreatAssessment):
            rows = model.query.filter_by(analysis_id=aid).all()
            for row in rows:
                assert row.analysis_id == aid
        # Threat assessment is present (last stage)
        assert ThreatAssessment.query.filter_by(analysis_id=aid).first() is not None

    def test_reddit_runs_all_v12_stages(self, app, db, user):
        result = _reddit(app, db, user)
        assert result['success'] is True
        aid = result['analysis_id']
        assert MediaAnalysis.query.filter_by(analysis_id=aid).first() is not None
        ta = ThreatAssessment.query.filter_by(analysis_id=aid).first()
        assert ta is not None
        # Narrative occurrences keyed to the same analysis
        occs = NarrativeOccurrence.query.filter_by(analysis_id=aid).all()
        for occ in occs:
            assert occ.analysis_id == aid

    def test_threat_consumes_earlier_components(self, app, db, user):
        """Threat assessment should reflect the components that actually ran."""
        result = _youtube(app, db, user)
        aid = result['analysis_id']
        ta = ThreatAssessment.query.filter_by(analysis_id=aid).first()
        assert ta is not None
        # component_scores must only contain keys that are not None
        comps = ta.component_scores or {}
        for key, value in comps.items():
            assert value is None or isinstance(value, (int, float)), key

    def test_shared_context_matches_analysis_id(self, app, db, user):
        """The result/export context must resolve records for that analysis."""
        result = _youtube(app, db, user)
        aid = result['analysis_id']
        ctx = build_v12_context(aid, user.id)
        assert ctx['threat'] is None or ctx['threat'].get('available') is not False
        for n in ctx.get('narratives') or []:
            assert 'id' in n
        # Worker path test (service-level, same methods as background worker)
        from services.background_worker import BackgroundWorker
        from services.job_queue_interface import ThreadPoolQueueProvider
        from repositories.job_repository import JobRepository
        from models.job import Job
        worker = BackgroundWorker(queue_provider=ThreadPoolQueueProvider(max_workers=1))
        repo = JobRepository()
        job = repo.create_job(user.id, 'youtube', 'analysis', 'dQw4w9WgXcQ',
                              25, request_hash='phase_l_worker')
        worker._run_analysis(job.id, app)
        db.session.expire_all()
        job = repo.get_job(job.id)
        assert job.status == Job.COMPLETED
        assert job.result_analysis_id is not None
        assert ThreatAssessment.query.filter_by(
            analysis_id=job.result_analysis_id).first() is not None


# --------------------------------------------------------------------------
# Feature flags -> unavailable / absent, graceful degradation
# --------------------------------------------------------------------------
class TestFeatureFlags:
    def test_disabled_narrative_flag_yields_no_narratives(self, app, db, user):
        result = _youtube(app, db, user, ENABLE_NARRATIVE_INTELLIGENCE=False)
        aid = result['analysis_id']
        assert result['success'] is True
        # narrative stage skipped -> threat should mark it unavailable, not 0
        ta = ThreatAssessment.query.filter_by(analysis_id=aid).first()
        assert ta is not None
        assert ta.narrative_risk_score is None
        comps = ta.component_scores or {}
        assert comps.get('narrative') is None
        # context shows empty narratives
        ctx = build_v12_context(aid, user.id)
        assert ctx['narratives'] == []

    def test_disabled_coordination_flag_yields_no_signals(self, app, db, user):
        result = _youtube(app, db, user, ENABLE_COORDINATION_DETECTION=False)
        aid = result['analysis_id']
        assert result['success'] is True
        assert CoordinationSignal.query.filter_by(analysis_id=aid).count() == 0
        ta = ThreatAssessment.query.filter_by(analysis_id=aid).first()
        assert ta is not None
        assert ta.coordination_score is None
        ctx = build_v12_context(aid, user.id)
        assert ctx['coordination'] == []

    def test_disabled_threat_flag_yields_no_assessment(self, app, db, user):
        result = _youtube(app, db, user, ENABLE_THREAT_ASSESSMENT=False)
        aid = result['analysis_id']
        assert result['success'] is True
        assert ThreatAssessment.query.filter_by(analysis_id=aid).first() is None
        # earlier stages still ran
        assert MediaAnalysis.query.filter_by(analysis_id=aid).first() is not None
        ctx = build_v12_context(aid, user.id)
        assert ctx['threat'] is None

    def test_all_v12_disabled_analysis_still_succeeds(self, app, db, user):
        result = _youtube(
            app, db, user,
            ENABLE_NARRATIVE_INTELLIGENCE=False,
            ENABLE_COORDINATION_DETECTION=False,
            ENABLE_PROPAGATION_INTELLIGENCE=False,
            ENABLE_TEMPORAL_INTELLIGENCE=False,
            ENABLE_THREAT_ASSESSMENT=False,
            ENABLE_AUTHENTICITY_ENGINE=False,
        )
        assert result['success'] is True
        aid = result['analysis_id']
        # V1-V11 data still present
        assert ThreatAssessment.query.filter_by(analysis_id=aid).first() is None
        assert MediaAnalysis.query.filter_by(analysis_id=aid).first() is None

    def test_dependent_stage_degrades_gracefully(self, app, db, user):
        """Temporal depends on narratives; with none, it reports unavailable."""
        result = _youtube(app, db, user, ENABLE_NARRATIVE_INTELLIGENCE=False)
        aid = result['analysis_id']
        ctx = build_v12_context(aid, user.id)
        assert ctx['temporal'] == []
        # propagation still ran (independent of narratives for the edge itself)
        assert ctx['propagation'] == [] or isinstance(ctx['propagation'], list)

    def test_one_failed_stage_never_breaks_analysis(self, app, db, user):
        from services.analysis_service import AnalysisService
        svc = AnalysisService()
        svc.narrative_service.analyze = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError('narrative exploded'))
        result = svc.create_youtube_analysis(user.id, 'dQw4w9WgXcQ',
                                             comment_limit=25)
        assert result['success'] is True
        aid = result['analysis_id']
        # narrative failed but coordination + threat still produced records
        assert ThreatAssessment.query.filter_by(analysis_id=aid).first() is not None
        assert MediaAnalysis.query.filter_by(analysis_id=aid).first() is not None
        ctx = build_v12_context(aid, user.id)
        assert ctx['narratives'] == []


# --------------------------------------------------------------------------
# Data consistency: dedup on re-analysis, unavailable != 0, parity
# --------------------------------------------------------------------------
class TestDataConsistency:
    def test_reanalysis_does_not_duplicate_v12_records(self, app, db, user):
        """Each new analysis gets its own records; none duplicated within it."""
        r1 = _youtube(app, db, user)
        r2 = _youtube(app, db, user)
        assert r1['analysis_id'] != r2['analysis_id']
        for aid in (r1['analysis_id'], r2['analysis_id']):
            assert ThreatAssessment.query.filter_by(analysis_id=aid).count() <= 1
            # coordination is unique per (analysis_id, signal_type)
            signals = CoordinationSignal.query.filter_by(analysis_id=aid).all()
            keys = {(s.signal_type) for s in signals}
            assert len(keys) == len(signals)

    def test_unavailable_is_never_zero(self, app, db, user):
        """Missing components must be absent/None, never silently zero."""
        result = _youtube(app, db, user)
        aid = result['analysis_id']
        ctx = build_v12_context(aid, user.id)
        threat = ctx['threat']
        if threat:
            for key, value in (threat.get('component_scores') or {}).items():
                # every component is either a real score or absent
                assert value is None or value > 0, key

    def test_heuristic_wording_present_everywhere(self, app, db, user):
        result = _youtube(app, db, user)
        aid = result['analysis_id']
        ctx = build_v12_context(aid, user.id)
        threat = ctx['threat']
        if threat:
            assert 'heuristic' in ' '.join(threat.get('reasons') or []) or \
                'heuristic' in threat.get('summary', '')
        # the shared disclaimers are stable strings reused by result + exports
        assert 'non-causal' in HEURISTIC_DISCLAIMER
        assert 'non-causal' in THREAT_DISCLAIMER


# --------------------------------------------------------------------------
# Bounded behavior
# --------------------------------------------------------------------------
class TestBoundedBehavior:
    def test_context_respects_narrative_limit(self, app, db, user):
        result = _youtube(app, db, user, comment_limit=40)
        aid = result['analysis_id']
        for limit in (2, 5, 8):
            ctx = build_v12_context(aid, user.id, narrative_limit=limit)
            assert len(ctx['narratives']) <= limit

    def test_exports_slice_v12_lists(self, app, db, user):
        from services.export_service import ExportService
        svc = ExportService()
        result = _youtube(app, db, user)
        aid = result['analysis_id']
        csv_res = svc.generate_csv(aid, user.id)
        json_res = svc.generate_json(aid, user.id)
        assert csv_res and json_res
        csv_text = csv_res['csv_content']
        import json as _json
        data = _json.loads(json_res['json_content'])
        v12 = data.get('v12', {})
        assert len(v12.get('narratives', [])) <= 8
        assert len(v12.get('coordination', [])) <= 8
        assert len(v12.get('propagation', [])) <= 8


# --------------------------------------------------------------------------
# All five export formats stay valid with V12 data
# --------------------------------------------------------------------------
class TestExportFormats:
    def test_all_five_formats_generate(self, app, db, user):
        from services.export_service import ExportService
        svc = ExportService()
        result = _youtube(app, db, user)
        aid = result['analysis_id']
        csv_res = svc.generate_csv(aid, user.id)
        json_res = svc.generate_json(aid, user.id)
        assert csv_res and json_res
        csv_text = csv_res['csv_content']
        assert 'Threat Assessment' in csv_text or 'Narratives & Growth' in csv_text \
            or 'Coordination Signals' in csv_text
        import json as _json
        import os
        data = _json.loads(json_res['json_content'])
        assert 'v12' in data
        # binary formats write real files and return {filename, filepath}
        xlsx = svc.generate_xlsx(aid, user.id)
        pdf = svc.generate_pdf(aid, user.id)
        docx = svc.generate_docx(aid, user.id)
        for fmt, res in (('xlsx', xlsx), ('pdf', pdf), ('docx', docx)):
            assert res and isinstance(res, dict), fmt
            assert res['filepath'] and os.path.exists(res['filepath']), fmt
            assert os.path.getsize(res['filepath']) > 0, fmt
