"""V14 Phase C3 - Self-service per-analysis deletion tests."""
import os

import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.entity import Entity
from models.job import Job
from models.job_log import JobLog
from models.media_analysis import MediaAnalysis
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.coordination_signal import CoordinationSignal
from models.propagation_event import PropagationEvent
from models.report_export import ReportExport
from models.threat_assessment import ThreatAssessment
from models.user import User
from services.analysis_service import AnalysisService


def _analysis(user_id, threat_score=30.0):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    db.session.add(YouTubeAnalysis(analysis_id=a.id, video_id=f'vid{a.id}',
                                   video_title='T', channel_name='C',
                                   is_demo=True))
    db.session.add(CommentResult(
        analysis_id=a.id, comment_text='stored comment text', author='a1',
        sentiment_score=60.0, toxicity_score=5.0, spam_score=10.0,
        duplicate_score=0.0))
    db.session.add(Entity(analysis_id=a.id, name='Acme', normalized_name='acme',
                          entity_type='company', frequency=2, importance_score=50.0))
    db.session.add(MediaAnalysis(analysis_id=a.id))
    n = Narrative(user_id=user_id, name=f'Gone Story {a.id}',
                  normalized_name=f'gone story {a.id}', risk_score=40.0)
    db.session.add(n)
    db.session.flush()
    db.session.add(NarrativeOccurrence(narrative_id=n.id, analysis_id=a.id,
                                       user_id=user_id, relevance_score=70.0))
    db.session.add(CoordinationSignal(analysis_id=a.id, user_id=user_id,
                                      signal_type='repeated_content', score=55.0))
    db.session.add(PropagationEvent(user_id=user_id, source_analysis_id=a.id,
                                    target_analysis_id=a.id))
    if threat_score is not None:
        db.session.add(ThreatAssessment(
            analysis_id=a.id, user_id=user_id,
            overall_threat_score=threat_score, threat_level='Low',
            confidence=50.0, evidence_coverage=0.5))
    db.session.commit()
    return a


def _job(user_id, analysis_id, status):
    job = Job(user_id=user_id, platform='youtube', source_type='video',
              source_input='dQw4w9WgXcQ', status=status,
              result_analysis_id=analysis_id)
    db.session.add(job)
    db.session.flush()
    db.session.add(JobLog(job_id=job.id, message='m'))
    db.session.commit()
    return job


def _export_file(app, analysis_id, name='del_me.csv'):
    folder = app.config['UPLOAD_FOLDER']
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'w') as f:
        f.write('x')
    db.session.add(ReportExport(analysis_id=analysis_id, format_type='csv',
                                file_path=path))
    db.session.commit()
    return path


def _counts(analysis_id):
    return {
        'analysis': Analysis.query.filter_by(id=analysis_id).count(),
        'youtube': YouTubeAnalysis.query.filter_by(analysis_id=analysis_id).count(),
        'comments': CommentResult.query.filter_by(analysis_id=analysis_id).count(),
        'entities': Entity.query.filter_by(analysis_id=analysis_id).count(),
        'media': MediaAnalysis.query.filter_by(analysis_id=analysis_id).count(),
        'occurrences': NarrativeOccurrence.query.filter_by(analysis_id=analysis_id).count(),
        'signals': CoordinationSignal.query.filter_by(analysis_id=analysis_id).count(),
        'threat': ThreatAssessment.query.filter_by(analysis_id=analysis_id).count(),
        'exports': ReportExport.query.filter_by(analysis_id=analysis_id).count(),
    }


class TestSuccessfulDeletion:
    def test_owner_deletes_completed_analysis(self, app, db, user):
        a = _analysis(user.id)
        assert sum(_counts(a.id).values()) > 5
        out = AnalysisService().delete_user_analysis(a.id, user.id)
        assert out == {'success': True}
        assert sum(_counts(a.id).values()) == 0

    def test_completed_and_failed_jobs_do_not_block(self, app, db, user):
        a = _analysis(user.id)
        _job(user.id, a.id, Job.COMPLETED)
        _job(user.id, a.id, Job.FAILED)
        out = AnalysisService().delete_user_analysis(a.id, user.id)
        assert out['success'] is True
        assert Job.query.filter_by(result_analysis_id=a.id).count() == 0
        assert JobLog.query.count() == 0

    def test_repeated_deletion_safe(self, app, db, user):
        a = _analysis(user.id)
        assert AnalysisService().delete_user_analysis(a.id, user.id)['success'] is True
        again = AnalysisService().delete_user_analysis(a.id, user.id)
        assert again['success'] is False and again.get('not_found') is True

    def test_route_and_history_button(self, logged_in_client, user, db):
        a = _analysis(user.id)
        body = logged_in_client.get('/analysis/history').get_data(as_text=True)
        assert f'/analysis/{a.id}/delete' in body
        resp = logged_in_client.post(f'/analysis/{a.id}/delete')
        assert resp.status_code == 302
        assert Analysis.query.filter_by(id=a.id).count() == 0

    def test_destructive_get_cannot_delete(self, logged_in_client, user, db):
        a = _analysis(user.id)
        assert logged_in_client.get(f'/analysis/{a.id}/delete').status_code == 405
        assert Analysis.query.filter_by(id=a.id).count() == 1

    def test_unauthenticated_redirected(self, client, db, user):
        a = _analysis(user.id)
        assert client.post(f'/analysis/{a.id}/delete').status_code == 302
        assert Analysis.query.filter_by(id=a.id).count() == 1


class TestJobGuards:
    def test_running_job_blocks(self, app, db, user):
        a = _analysis(user.id)
        _job(user.id, a.id, Job.RUNNING)
        out = AnalysisService().delete_user_analysis(a.id, user.id)
        assert out['success'] is False and out.get('blocked') is True
        assert Analysis.query.filter_by(id=a.id).count() == 1

    def test_pending_job_blocks(self, app, db, user):
        a = _analysis(user.id)
        _job(user.id, a.id, Job.PENDING)
        out = AnalysisService().delete_user_analysis(a.id, user.id)
        assert out['success'] is False and out.get('blocked') is True
        assert Analysis.query.filter_by(id=a.id).count() == 1

    def test_blocked_route_message(self, logged_in_client, user, db):
        from services.job_service import JobService
        a = _analysis(user.id)
        job = _job(user.id, a.id, Job.PENDING)
        JobService().cancel_job(job.id, user.id)
        # cancelled job must not block
        out = AnalysisService().delete_user_analysis(a.id, user.id)
        assert out['success'] is True


class TestIsolation:
    def test_cannot_delete_other_users_analysis(self, app, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='del_other', email='del_other@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        theirs = _analysis(other.id)
        out = AnalysisService().delete_user_analysis(theirs.id, user.id)
        assert out['success'] is False and out.get('not_found') is True
        assert Analysis.query.filter_by(id=theirs.id).count() == 1

    def test_cross_user_route_returns_history(self, client, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='del_oth2', email='del_oth2@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        theirs = _analysis(other.id)
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        resp = client.post(f'/analysis/{theirs.id}/delete')
        assert resp.status_code == 302
        assert Analysis.query.filter_by(id=theirs.id).count() == 1

    def test_nonexistent_analysis(self, app, db, user):
        out = AnalysisService().delete_user_analysis(999999, user.id)
        assert out['success'] is False and out.get('not_found') is True

    def test_unrelated_records_preserved(self, app, db, user):
        keep = _analysis(user.id)
        gone = _analysis(user.id)
        keep_narratives = Narrative.query.filter_by(user_id=user.id).count()
        out = AnalysisService().delete_user_analysis(gone.id, user.id)
        assert out['success'] is True
        assert Analysis.query.filter_by(id=keep.id).count() == 1
        assert ThreatAssessment.query.filter_by(analysis_id=keep.id).count() == 1
        # shared user-level narrative rows are not analysis trash
        assert Narrative.query.filter_by(user_id=user.id).count() == keep_narratives
        assert User.query.filter_by(id=user.id).count() == 1


class TestFilesystemCleanup:
    def test_export_files_removed(self, app, db, user):
        a = _analysis(user.id)
        path = _export_file(app, a.id)
        assert os.path.isfile(path)
        assert AnalysisService().delete_user_analysis(a.id, user.id)['success'] is True
        assert not os.path.exists(path)

    def test_missing_files_tolerated(self, app, db, user):
        a = _analysis(user.id)
        db.session.add(ReportExport(analysis_id=a.id, format_type='csv',
                                    file_path=os.path.join(
                                        app.config['UPLOAD_FOLDER'],
                                        'never-existed.csv')))
        db.session.commit()
        assert AnalysisService().delete_user_analysis(a.id, user.id)['success'] is True

    def test_outside_paths_never_touched(self, app, db, user, tmp_path):
        a = _analysis(user.id)
        outside = tmp_path / 'keep_me.csv'
        outside.write_text('important')
        db.session.add(ReportExport(analysis_id=a.id, format_type='csv',
                                    file_path=str(outside)))
        db.session.commit()
        assert AnalysisService().delete_user_analysis(a.id, user.id)['success'] is True
        assert outside.is_file()
