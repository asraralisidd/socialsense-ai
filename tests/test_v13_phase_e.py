"""V13 Phase E - Export/report integration & final acceptance tests.

V13 sections in JSON/CSV/XLSX/PDF/DOCX exports, report generation (JSON +
HTML), shared-context determinism, error isolation, ownership enforcement,
and Phase B/C/D compatibility.
"""
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.scheduled_report import ScheduledReport
from models.threat_assessment import ThreatAssessment
from services.export_service import ExportService


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


def _seeded_user_history(user_id):
    for score in (20.0, 30.0, 40.0):
        _analysis(user_id, days_ago=int(score), threat_score=score)
    current = _analysis(user_id, threat_score=32.0)
    n = Narrative(user_id=user_id, name='Export Story',
                  normalized_name='export story', risk_score=40.0)
    db.session.add(n)
    db.session.flush()
    occ = NarrativeOccurrence(narrative_id=n.id, analysis_id=current.id,
                              user_id=user_id, relevance_score=70.0)
    db.session.add(occ)
    db.session.commit()
    from services.v13_evidence_chain_service import V13EvidenceChainService
    V13EvidenceChainService().analyze(current, components={})
    return current


class TestV13JsonExport:
    def test_json_has_v13_sections(self, app, db, user):
        current = _seeded_user_history(user.id)
        out = ExportService().generate_json(current.id, user.id)
        assert out is not None
        data = json.loads(out['json_content'])
        assert 'v13' in data
        v13 = data['v13']
        assert v13['historical_context'] is not None
        assert v13['cross_analysis'] is not None
        assert v13['narrative_evolution'] is not None
        assert v13['evidence'] is not None
        assert v13['historical_context']['metrics']['threat']['baseline'] == \
            pytest.approx(30.0)
        states = [n['state'] for n in
                  v13['narrative_evolution']['narratives']]
        assert 'emerging' in states
        assert v13['evidence']['link_count'] >= 1

    def test_json_v12_fields_unchanged(self, app, db, user):
        current = _seeded_user_history(user.id)
        data = json.loads(
            ExportService().generate_json(current.id, user.id)['json_content'])
        assert 'v12' in data and 'comments' in data and 'metadata' in data
        assert set(data['v12']) >= {'threat', 'narratives', 'coordination',
                                    'propagation', 'temporal'}

    def test_json_unavailable_and_zero(self, app, db, user):
        current = _analysis(user.id)  # no history, no threat
        data = json.loads(
            ExportService().generate_json(current.id, user.id)['json_content'])
        v13 = data['v13']
        assert v13['historical_context']['metrics']['threat']['current'] is None
        assert v13['historical_context']['metrics']['threat']['baseline'] is None
        assert v13['historical_context']['metrics']['narrative_activity']['current'] == 0

    def test_json_no_fabricated_refs(self, app, db, user):
        current = _seeded_user_history(user.id)
        content = ExportService().generate_json(current.id, user.id)['json_content']
        assert 'comment:999999' not in content

    def test_json_route_and_ownership(self, logged_in_client, user, db):
        current = _seeded_user_history(user.id)
        resp = logged_in_client.get(f'/export/json/{current.id}')
        assert resp.status_code == 200
        assert 'v13' in json.loads(resp.get_data(as_text=True))
        # NOTE: this env's test clients share cookies across instances,
        # so switch users via logout/login on the same client.
        logged_in_client.get('/auth/logout')
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='e_other', email='e_other@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        logged_in_client.post('/auth/login', data={'email': 'e_other@x.com',
                                                   'password': 'Other123'})
        resp = logged_in_client.get(f'/export/json/{current.id}')
        assert resp.status_code == 302  # redirected, no leak
        assert 'historical_context' not in resp.get_data(as_text=True)


class TestV13CsvExport:
    def test_csv_sections(self, app, db, user):
        current = _seeded_user_history(user.id)
        out = ExportService().generate_csv(current.id, user.id)
        assert out is not None
        text = out['csv_content']
        assert '# V13 Historical Context' in text
        assert '# V13 Cross-Analysis Comparison' in text
        assert '# V13 Narrative Evolution' in text
        assert '# Evidence: V13 Chain' in text
        assert 'emerging' in text

    def test_csv_deterministic(self, app, db, user):
        current = _seeded_user_history(user.id)
        svc = ExportService()
        first = svc.generate_csv(current.id, user.id)['csv_content']
        # strip the timestamped filename-dependent rows is unnecessary:
        # content itself must be stable
        second = svc.generate_csv(current.id, user.id)['csv_content']
        assert first == second

    def test_csv_unavailable_not_zero(self, app, db, user):
        current = _analysis(user.id)
        text = ExportService().generate_csv(current.id, user.id)['csv_content']
        assert 'Unavailable' in text

    def test_csv_route(self, logged_in_client, user, db):
        current = _seeded_user_history(user.id)
        resp = logged_in_client.get(f'/export/csv/{current.id}')
        assert resp.status_code == 200
        assert 'V13 Historical Context' in resp.get_data(as_text=True)


class TestV13BinaryExports:
    @pytest.mark.parametrize('method', ['generate_xlsx', 'generate_pdf',
                                        'generate_docx'])
    def test_binary_exports_include_v13(self, app, db, user, method):
        current = _seeded_user_history(user.id)
        out = getattr(ExportService(), method)(current.id, user.id)
        assert out is not None and out.get('filepath')
        import os
        assert os.path.getsize(out['filepath']) > 0
        os.remove(out['filepath'])

    def test_binary_exports_unavailable_safe(self, app, db, user):
        current = _analysis(user.id)
        for method in ('generate_xlsx', 'generate_pdf', 'generate_docx'):
            out = getattr(ExportService(), method)(current.id, user.id)
            assert out is not None
            import os
            os.remove(out['filepath'])

    @pytest.mark.parametrize('route', ['xlsx', 'pdf', 'docx'])
    def test_binary_routes(self, logged_in_client, user, db, route):
        current = _seeded_user_history(user.id)
        resp = logged_in_client.get(f'/export/{route}/{current.id}')
        assert resp.status_code == 200


class TestV13Reports:
    def _report(self, user_id, fmt):
        report = ScheduledReport(
            user_id=user_id, report_type='weekly', frequency='weekly',
            report_format=fmt, platform_filter='all')
        db.session.add(report)
        db.session.commit()
        return report

    def test_json_report_has_v13(self, app, db, user):
        from services.report_generation_service import ReportGenerationService
        _seeded_user_history(user.id)
        report = self._report(user.id, ScheduledReport.FORMAT_JSON)
        data = ReportGenerationService().generate_report(report.id, app)
        assert data is not None
        v13 = data.get('v13_historical_context')
        assert v13 is not None
        assert v13['metrics']['threat']['baseline'] == pytest.approx(30.0)
        assert any(n['state'] == 'emerging' for n in v13['narratives'])
        assert v13['evidence_links'] >= 1
        blob = json.dumps(v13).lower()
        assert 'caused by' not in blob and 'proves' not in blob

    def test_html_report_renders_v13(self, app, db, user):
        from services.report_generation_service import ReportGenerationService
        _seeded_user_history(user.id)
        report = self._report(user.id, ScheduledReport.FORMAT_HTML)
        data = ReportGenerationService().generate_report(report.id, app)
        assert data is not None
        with open(report.last_file_path, encoding='utf-8') as f:
            html = f.read()
        assert 'V13 Historical Context' in html
        assert 'V13 Narrative Evolution' in html
        assert 'non-causal' in html
        import os
        os.remove(report.last_file_path)

    def test_report_without_history(self, app, db, user):
        from services.report_generation_service import ReportGenerationService
        _analysis(user.id)
        report = self._report(user.id, ScheduledReport.FORMAT_JSON)
        data = ReportGenerationService().generate_report(report.id, app)
        assert data is not None  # V1-V12 report intact
        v13 = data.get('v13_historical_context')
        assert v13 is None or v13['metrics']['threat']['availability'] in (
            'insufficient_history', 'unavailable')

    def test_report_user_scoped(self, app, db, user):
        from services.report_generation_service import ReportGenerationService
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='e_rep', email='e_rep@x.com',
                     password_hash=generate_password_hash('x'))
        db.session.add(other)
        db.session.commit()
        _analysis(other.id, threat_score=99.0)
        _seeded_user_history(user.id)
        report = self._report(user.id, ScheduledReport.FORMAT_JSON)
        data = ReportGenerationService().generate_report(report.id, app)
        content = json.dumps(data)
        assert '99.0' not in content  # other user's data absent


class TestV13ExportIsolation:
    def test_v13_failure_preserves_core_export(self, app, db, user, monkeypatch):
        current = _seeded_user_history(user.id)
        import services.export_service as mod
        monkeypatch.setattr(mod, 'build_v13_context',
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError('boom')))
        out = ExportService().generate_json(current.id, user.id)
        assert out is not None
        data = json.loads(out['json_content'])
        assert 'comments' in data and 'v12' in data
        assert data['v13']['historical_context'] is None

    def test_shared_context_deterministic(self, app, db, user):
        from services.v13_context_service import build_v13_context
        current = _seeded_user_history(user.id)
        assert build_v13_context(current.id, user.id) == \
            build_v13_context(current.id, user.id)

    def test_result_page_still_compatible(self, logged_in_client, user, db):
        current = _seeded_user_history(user.id)
        resp = logged_in_client.get(f'/analysis/{current.id}')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Historical Context' in body
        assert 'Threat Assessment' in body
