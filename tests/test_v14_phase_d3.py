"""V14 Phase D3 - Report history bounds tests.

SQL-level bounds, output parity against hand-computed expectations,
deterministic ordering, ownership isolation, empty history, scheduled
batch caps, and bounded query counts independent of history size.
"""
from datetime import datetime, timedelta, timezone

import pytest

from database import db
from models.analysis import Analysis
from models.channel_context import ChannelContext
from models.comment_result import CommentResult
from models.entity import Entity
from models.entity_context import EntityContext
from models.media_analysis import MediaAnalysis
from models.scheduled_report import ScheduledReport
from repositories.scheduled_report_repository import ScheduledReportRepository
from services.report_generation_service import ReportGenerationService


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _analysis(user_id):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    c = CommentResult(analysis_id=a.id, comment_text='stored text', author='a1')
    db.session.add(c)
    db.session.flush()
    return a, c


def _seed_history(user_id, n):
    for _ in range(n):
        a, c = _analysis(user_id)
        for k in range(3):
            e = Entity(analysis_id=a.id, name=f'E{k}', normalized_name=f'e{k}',
                       entity_type='company', frequency=2, importance_score=10.0)
            db.session.add(e)
            db.session.flush()
            db.session.add(EntityContext(
                entity_id=e.id, comment_result_id=c.id,
                entity_risk_score=40.0))
        db.session.add(MediaAnalysis(
            analysis_id=a.id, overall_ai_probability=70.0,
            overall_authenticity_score=50.0, deepfake_score=10.0,
            synthetic_voice_score=10.0))
    db.session.add(ChannelContext(user_id=user_id, channel_id='chan1',
                                  channel_name='Chan',
                                  total_videos_analyzed=n,
                                  total_entities_detected=3 * n))
    db.session.commit()


def _report(user_id, fmt=ScheduledReport.FORMAT_JSON):
    rep = ScheduledReport(user_id=user_id, report_type='weekly',
                          frequency='weekly', report_format=fmt,
                          platform_filter='all')
    db.session.add(rep)
    db.session.commit()
    return rep


def _count_queries():
    from sqlalchemy import event
    state = {'n': 0}

    def _before(conn, cursor, statement, params, context, executemany):
        state['n'] += 1

    return state, _before


class TestReportBounds:
    def test_query_count_independent_of_history(self, app, db, user):
        from sqlalchemy import event
        counts = []
        for total in (2, 8):
            for _ in range(total - Analysis.query.filter_by(
                    user_id=user.id).count()):
                _analysis(user.id)
            rep = _report(user.id)
            state, hook = _count_queries()
            event.listen(db.engine, 'before_cursor_execute', hook)
            try:
                assert ReportGenerationService().generate_report(
                    rep.id, app) is not None
            finally:
                event.remove(db.engine, 'before_cursor_execute', hook)
            counts.append(state['n'])
            db.session.delete(rep)
            db.session.commit()
        assert counts[0] == counts[1], counts

    def test_output_parity(self, app, db, user):
        _seed_history(user.id, 4)
        rep = _report(user.id)
        data = ReportGenerationService().generate_report(rep.id, app)
        assert data['total_analyses'] == 4
        ent = data['entity_intelligence']
        assert ent['total_entities'] == 12
        assert ent['entity_type_distribution'] == {'company': 24}
        assert ent['avg_entity_risk'] == 40.0
        auth = data['authenticity_intelligence']
        assert auth['total_media_analyzed'] == 4
        assert auth['ai_videos'] == 4
        assert auth['authentic_videos'] == 0
        assert auth['deepfake_count'] == 0
        assert auth['voice_clone_count'] == 0
        assert auth['avg_authenticity'] == 50.0
        assert auth['avg_ai_probability'] == 70.0
        assert data['channel_intelligence']['total_channels'] == 1
        assert data['channel_intelligence']['total_videos_analyzed'] == 4
        assert set(data) >= {'trends', 'top_risky_comments',
                             'v13_historical_context'}

    def test_empty_history(self, app, db, user):
        rep = _report(user.id)
        data = ReportGenerationService().generate_report(rep.id, app)
        assert data is not None
        assert data['total_analyses'] == 0
        assert data['entity_intelligence']['total_entities'] == 0
        assert data['entity_intelligence']['avg_entity_risk'] == 0.0
        assert data['authenticity_intelligence']['total_media_analyzed'] == 0
        assert data['authenticity_intelligence']['avg_authenticity'] == 0.0

    def test_user_isolation(self, app, db, user):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='d3other', email='d3other@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        _seed_history(other.id, 3)
        _seed_history(user.id, 1)
        rep = _report(user.id)
        data = ReportGenerationService().generate_report(rep.id, app)
        assert data['total_analyses'] == 1
        assert data['entity_intelligence']['total_entities'] == 3


class TestDueBatch:
    def _due_reports(self, user_id, n):
        reps = []
        for i in range(n):
            rep = ScheduledReport(
                user_id=user_id, report_type='weekly', frequency='weekly',
                report_format='json', platform_filter='all', is_active=True,
                next_run_at=_now() - timedelta(minutes=i + 1))
            db.session.add(rep)
            reps.append(rep)
        db.session.commit()
        return reps

    def test_batch_bound_and_order(self, app, db, user):
        self._due_reports(user.id, 5)
        batch = ScheduledReportRepository().get_due_reports(limit=2)
        assert len(batch) == 2
        assert batch[0].next_run_at <= batch[1].next_run_at
        assert batch[0].id != batch[1].id

    def test_process_bounded_remainder_stays_due(self, app, db, user):
        self._due_reports(user.id, 5)
        svc = ReportGenerationService()
        assert svc.process_due_reports(app, batch_size=2) == 2
        remaining = ScheduledReportRepository().get_due_reports()
        assert len(remaining) == 3
        assert svc.process_due_reports(app, batch_size=10) == 3
        assert ScheduledReportRepository().get_due_reports() == []

    def test_default_batch_uses_config(self, app, db, user):
        assert app.config['REPORT_MAX_DUE_BATCH'] == 20
        self._due_reports(user.id, 2)
        assert ReportGenerationService().process_due_reports(app) == 2
