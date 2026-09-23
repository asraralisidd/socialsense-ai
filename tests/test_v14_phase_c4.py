"""V14 Phase C4 - Self-service account deletion tests."""
import os

import pytest

from database import db
from models.activity_log import ActivityLog
from models.analysis import Analysis, YouTubeAnalysis
from models.channel_context import ChannelContext
from models.comment_result import CommentResult
from models.entity import Entity
from models.job import Job
from models.job_log import JobLog
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.notification import Notification
from models.report_export import ReportExport
from models.scheduled_analysis import ScheduledAnalysis
from models.scheduled_report import ScheduledReport
from models.threat_assessment import ThreatAssessment
from models.user import User


def _footprint(user_id, tag='x', threat_score=30.0):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    db.session.add(YouTubeAnalysis(analysis_id=a.id, video_id=f'vid{a.id}',
                                   video_title='T', channel_name='C',
                                   is_demo=True))
    db.session.add(CommentResult(
        analysis_id=a.id, comment_text='stored text', author='a1',
        sentiment_score=60.0, toxicity_score=5.0, spam_score=10.0,
        duplicate_score=0.0))
    db.session.add(Entity(analysis_id=a.id, name='Acme', normalized_name='acme',
                          entity_type='company', frequency=1, importance_score=10.0))
    n = Narrative(user_id=user_id, name=f'Story {tag} {a.id}',
                  normalized_name=f'story {tag} {a.id}', risk_score=40.0)
    db.session.add(n)
    db.session.flush()
    db.session.add(NarrativeOccurrence(narrative_id=n.id, analysis_id=a.id,
                                       user_id=user_id, relevance_score=70.0))
    if threat_score is not None:
        db.session.add(ThreatAssessment(
            analysis_id=a.id, user_id=user_id,
            overall_threat_score=threat_score, threat_level='Low',
            confidence=50.0, evidence_coverage=0.5))
    db.session.add(Notification(user_id=user_id, type='info', title='t'))
    db.session.add(ActivityLog(user_id=user_id, action='test_action'))
    db.session.add(ScheduledAnalysis(user_id=user_id, platform='youtube',
                                     source_type='video', source_input='vid'))
    db.session.add(ScheduledReport(user_id=user_id))
    db.session.add(ChannelContext(user_id=user_id, channel_id='chan1'))
    db.session.commit()
    return a


def _job(user_id, status):
    job = Job(user_id=user_id, platform='youtube', source_type='video',
              source_input='vid', status=status)
    db.session.add(job)
    db.session.flush()
    db.session.add(JobLog(job_id=job.id, message='m'))
    db.session.commit()
    return job


def _delete(client, password='TestPass123', username='testuser', **extra):
    data = {'current_password': password, 'username_confirm': username}
    data.update(extra)
    return client.post('/auth/account/delete', data=data)


def _user_table_counts(uid):
    from models.coordination_signal import CoordinationSignal
    from models.entity_history import EntityHistory
    from models.media_analysis import MediaAnalysis
    from models.propagation_event import PropagationEvent
    from models.video_context_history import VideoContextHistory
    return {
        'analyses': Analysis.query.filter_by(user_id=uid).count(),
        'comments': CommentResult.query.join(Analysis).filter(
            Analysis.user_id == uid).count(),
        'entities': Entity.query.join(Analysis).filter(
            Analysis.user_id == uid).count(),
        'jobs': Job.query.filter_by(user_id=uid).count(),
        'job_logs': JobLog.query.join(Job).filter(Job.user_id == uid).count(),
        'notifications': Notification.query.filter_by(user_id=uid).count(),
        'activity': ActivityLog.query.filter_by(user_id=uid).count(),
        'sched_a': ScheduledAnalysis.query.filter_by(user_id=uid).count(),
        'sched_r': ScheduledReport.query.filter_by(user_id=uid).count(),
        'channels': ChannelContext.query.filter_by(user_id=uid).count(),
        'narratives': Narrative.query.filter_by(user_id=uid).count(),
        'occurrences': NarrativeOccurrence.query.filter_by(user_id=uid).count(),
        'signals': CoordinationSignal.query.filter_by(user_id=uid).count(),
        'events': PropagationEvent.query.filter_by(user_id=uid).count(),
        'threats': ThreatAssessment.query.filter_by(user_id=uid).count(),
        'media': MediaAnalysis.query.join(Analysis).filter(
            Analysis.user_id == uid).count(),
        'entity_hist': EntityHistory.query.filter_by(user_id=uid).count(),
        'video_hist': VideoContextHistory.query.filter_by(user_id=uid).count(),
        'exports': ReportExport.query.join(Analysis).filter(
            Analysis.user_id == uid).count(),
    }


class TestAccountDeletion:
    def test_owner_deletes_own_account(self, logged_in_client, user, db):
        _footprint(user.id)
        assert sum(_user_table_counts(user.id).values()) > 10
        resp = _delete(logged_in_client)
        assert resp.status_code == 302
        assert User.query.filter_by(id=user.id).count() == 0
        assert sum(_user_table_counts(user.id).values()) == 0

    def test_session_invalidated(self, logged_in_client, user, db):
        _delete(logged_in_client)
        assert logged_in_client.get('/auth/profile').status_code == 302

    def test_credentials_dead(self, client, user, db):
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _delete(client)
        client.get('/auth/logout')
        resp = client.post('/auth/login', data={'email': 'test@example.com',
                                                'password': 'TestPass123'})
        assert resp.status_code == 200
        assert b'Invalid email or password' in resp.data

    def test_reset_tokens_dead(self, client, user, db):
        from services.auth_service import AuthService
        token = AuthService().request_password_reset(
            'test@example.com')['token']
        assert token
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _delete(client)
        found, reason = AuthService().verify_reset_token(token)
        assert found is None and reason

    def test_wrong_password_blocks(self, logged_in_client, user, db):
        _footprint(user.id)
        resp = _delete(logged_in_client, password='Wrong123')
        assert resp.status_code == 302
        assert User.query.filter_by(id=user.id).count() == 1
        assert sum(_user_table_counts(user.id).values()) > 0

    def test_wrong_username_blocks(self, logged_in_client, user, db):
        _footprint(user.id)
        resp = _delete(logged_in_client, username='someone_else')
        assert resp.status_code == 302
        assert User.query.filter_by(id=user.id).count() == 1

    def test_unauthenticated_redirected(self, client, db, user):
        _footprint(user.id)
        assert _delete(client).status_code == 302
        assert User.query.filter_by(id=user.id).count() == 1

    def test_get_cannot_delete(self, logged_in_client, user, db):
        _footprint(user.id)
        assert logged_in_client.get('/auth/account/delete').status_code == 405
        assert User.query.filter_by(id=user.id).count() == 1

    def test_ignores_target_user_input(self, client, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='deltarget', email='deltarget@x.com',
                     password_hash=generate_password_hash('Target123'))
        db.session.add(other)
        db.session.commit()
        _footprint(other.id, tag='victim')
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _delete(client, extra={'user_id': other.id, 'email': 'deltarget@x.com'})
        assert User.query.filter_by(id=other.id).count() == 1
        assert sum(_user_table_counts(other.id).values()) > 0
        # attacker's own account IS deleted (operation is strictly self)
        assert User.query.filter_by(id=user.id).count() == 0


class TestSoleAdmin:
    def test_sole_admin_refused(self, logged_in_client, user, db):
        user.role = User.ROLE_ADMIN
        db.session.commit()
        resp = _delete(logged_in_client)
        assert resp.status_code == 302
        assert User.query.filter_by(id=user.id).count() == 1
        assert user.is_admin is True  # not silently demoted

    def test_admin_with_peer_can_delete(self, client, db, user):
        from werkzeug.security import generate_password_hash
        peer = User(username='peeradmin', email='peer@x.com',
                    password_hash=generate_password_hash('Peer1234'),
                    role=User.ROLE_ADMIN)
        db.session.add(peer)
        db.session.commit()
        user.role = User.ROLE_ADMIN
        db.session.commit()
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        resp = _delete(client)
        assert resp.status_code == 302
        assert User.query.filter_by(id=user.id).count() == 0
        assert User.query.filter_by(id=peer.id).count() == 1


class TestJobHandling:
    def test_running_and_pending_handled(self, logged_in_client, user, db):
        _footprint(user.id)
        _job(user.id, Job.RUNNING)
        _job(user.id, Job.PENDING)
        resp = _delete(logged_in_client)
        assert resp.status_code == 302
        assert User.query.filter_by(id=user.id).count() == 0
        assert Job.query.filter_by(user_id=user.id).count() == 0
        assert JobLog.query.count() == 0

    def test_terminal_jobs_removed(self, logged_in_client, user, db):
        _footprint(user.id)
        for status in (Job.COMPLETED, Job.FAILED, Job.CANCELLED, Job.TIMEOUT):
            _job(user.id, status)
        assert _delete(logged_in_client).status_code == 302
        assert User.query.filter_by(id=user.id).count() == 0
        assert Job.query.filter_by(user_id=user.id).count() == 0


class TestFilesAndIsolation:
    def test_owned_files_removed(self, logged_in_client, user, db, app):
        a = _footprint(user.id)
        folder = app.config['UPLOAD_FOLDER']
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'acct_del_me.csv')
        with open(path, 'w') as f:
            f.write('x')
        db.session.add(ReportExport(analysis_id=a.id, format_type='csv',
                                    file_path=path))
        sched = ScheduledReport.query.filter_by(user_id=user.id).first()
        rpath = os.path.join(folder, 'acct_sched.html')
        with open(rpath, 'w') as f:
            f.write('y')
        sched.last_file_path = rpath
        db.session.commit()
        assert _delete(logged_in_client).status_code == 302
        assert not os.path.exists(path)
        assert not os.path.exists(rpath)

    def test_missing_and_outside_files_safe(self, logged_in_client, user, db,
                                           app, tmp_path):
        a = _footprint(user.id)
        db.session.add(ReportExport(analysis_id=a.id, format_type='csv',
                                    file_path=os.path.join(
                                        app.config['UPLOAD_FOLDER'],
                                        'never-existed.csv')))
        outside = tmp_path / 'keep.csv'
        outside.write_text('important')
        sched = ScheduledReport.query.filter_by(user_id=user.id).first()
        sched.last_file_path = str(outside)
        db.session.commit()
        assert _delete(logged_in_client).status_code == 302
        assert User.query.filter_by(id=user.id).count() == 0
        assert outside.is_file()

    def test_unrelated_user_intact(self, client, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='bystander', email='bystander@x.com',
                     password_hash=generate_password_hash('Bystander1'))
        db.session.add(other)
        db.session.commit()
        _footprint(other.id, tag='keep')
        other_counts = _user_table_counts(other.id)
        assert sum(other_counts.values()) > 0
        _footprint(user.id, tag='gone')
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        assert _delete(client).status_code == 302
        assert User.query.filter_by(id=other.id).count() == 1
        assert _user_table_counts(other.id) == other_counts
        again = client.post('/auth/login', data={'email': 'bystander@x.com',
                                                 'password': 'Bystander1'})
        assert again.status_code == 302

    def test_profile_shows_danger_zone(self, logged_in_client, user):
        body = logged_in_client.get('/auth/profile').get_data(as_text=True)
        assert 'Danger Zone' in body
        assert 'username_confirm' in body
