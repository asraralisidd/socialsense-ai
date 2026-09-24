import pytest
from datetime import datetime, timezone, timedelta


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TestWorkerHealthModel:
    def test_create_worker(self, app, db):
        from models.worker_health import WorkerHealth
        w = WorkerHealth(worker_name='worker1', status=WorkerHealth.ONLINE)
        db.session.add(w)
        db.session.commit()
        assert w.id is not None
        assert w.worker_name == 'worker1'
        assert w.status == 'online'

    def test_worker_status_default(self, app, db):
        from models.worker_health import WorkerHealth
        w = WorkerHealth(worker_name='worker2')
        db.session.add(w)
        db.session.commit()
        assert w.status == 'online'

    def test_heartbeat_tracking(self, app, db):
        from models.worker_health import WorkerHealth
        w = WorkerHealth(worker_name='worker3', last_heartbeat_at=_now())
        db.session.add(w)
        db.session.commit()
        assert w.last_heartbeat_at is not None


class TestWorkerHealthRepository:
    def test_get_or_create(self, app, db):
        from repositories.worker_health_repository import WorkerHealthRepository
        repo = WorkerHealthRepository()
        w = repo.get_or_create('test_worker')
        assert w.id is not None
        assert w.worker_name == 'test_worker'

        w2 = repo.get_or_create('test_worker')
        assert w2.id == w.id

    def test_record_heartbeat(self, app, db):
        from repositories.worker_health_repository import WorkerHealthRepository
        repo = WorkerHealthRepository()
        w = repo.record_heartbeat('hb_worker', active_jobs=3, queue_length=5)
        assert w.status == 'online'
        assert w.active_jobs == 3
        assert w.queue_length == 5

    def test_mark_offline(self, app, db):
        from repositories.worker_health_repository import WorkerHealthRepository
        repo = WorkerHealthRepository()
        w = repo.record_heartbeat('offline_worker')
        repo.mark_offline('offline_worker')
        assert w.status == 'offline'

    def test_get_summary(self, app, db):
        from repositories.worker_health_repository import WorkerHealthRepository
        repo = WorkerHealthRepository()
        repo.record_heartbeat('w1')
        repo.record_heartbeat('w2')
        summary = repo.get_summary()
        assert summary['total'] == 2
        assert summary['online'] == 2


class TestScheduledAnalysisModel:
    def test_create_schedule(self, app, db, user):
        from models.scheduled_analysis import ScheduledAnalysis
        s = ScheduledAnalysis(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='dQw4w9WgXcQ', frequency='daily', next_run_at=_now(),
        )
        db.session.add(s)
        db.session.commit()
        assert s.id is not None
        assert s.frequency == 'daily'
        assert s.is_active is True


class TestScheduledAnalysisRepository:
    def test_get_user_schedules(self, app, db, user):
        from models.scheduled_analysis import ScheduledAnalysis
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        for i in range(3):
            repo.create(
                user_id=user.id, platform='youtube', source_type='url',
                source_input=f'test{i}', frequency='daily', next_run_at=_now(),
            )
        schedules = repo.get_user_schedules(user.id)
        assert len(schedules) == 3

    def test_get_due_schedules(self, app, db, user):
        from models.scheduled_analysis import ScheduledAnalysis
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily',
            next_run_at=_now() - timedelta(hours=1),
        )
        due = repo.get_due_schedules()
        assert len(due) >= 1

    def test_get_due_schedules_excludes_future(self, app, db, user):
        from models.scheduled_analysis import ScheduledAnalysis
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily',
            next_run_at=_now() + timedelta(days=1),
        )
        due = repo.get_due_schedules()
        assert len(due) == 0

    def test_update_next_run_once(self, app, db, user):
        from models.scheduled_analysis import ScheduledAnalysis
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        s = repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='once', next_run_at=_now(),
        )
        repo.update_next_run(s.id)
        assert s.is_active is False
        assert s.next_run_at is None

    def test_update_next_run_daily(self, app, db, user):
        from models.scheduled_analysis import ScheduledAnalysis
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        s = repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily', next_run_at=_now(),
        )
        repo.update_next_run(s.id)
        assert s.last_run_at is not None
        assert s.is_active is True


class TestNotificationModel:
    def test_create_notification(self, app, db, user):
        from models.notification import Notification
        n = Notification(
            user_id=user.id, type='job_completed', title='Test',
            severity='success',
        )
        db.session.add(n)
        db.session.commit()
        assert n.id is not None
        assert n.is_read is False


class TestNotificationRepository:
    def test_get_unread_count(self, app, db, user):
        from repositories.notification_repository import NotificationRepository
        repo = NotificationRepository()
        repo.create_notification(user.id, 'info', 'Test 1')
        repo.create_notification(user.id, 'info', 'Test 2')
        assert repo.get_unread_count(user.id) == 2

    def test_mark_as_read(self, app, db, user):
        from repositories.notification_repository import NotificationRepository
        repo = NotificationRepository()
        n = repo.create_notification(user.id, 'info', 'Test')
        repo.mark_as_read(n.id, user.id)
        assert n.is_read is True

    def test_mark_as_read_wrong_user(self, app, db, user):
        from repositories.notification_repository import NotificationRepository
        repo = NotificationRepository()
        n = repo.create_notification(user.id, 'info', 'Test')
        result = repo.mark_as_read(n.id, 999)
        assert result is None

    def test_mark_all_read(self, app, db, user):
        from repositories.notification_repository import NotificationRepository
        repo = NotificationRepository()
        repo.create_notification(user.id, 'info', 'T1')
        repo.create_notification(user.id, 'info', 'T2')
        repo.mark_all_read(user.id)
        assert repo.get_unread_count(user.id) == 0


class TestScheduledReportModel:
    def test_create_report(self, app, db, user):
        from models.scheduled_report import ScheduledReport
        r = ScheduledReport(
            user_id=user.id, report_type='daily', frequency='daily',
            report_format='html', next_run_at=_now(),
        )
        db.session.add(r)
        db.session.commit()
        assert r.id is not None
        assert r.is_active is True


class TestScheduledReportRepository:
    def test_get_due_reports(self, app, db, user):
        from models.scheduled_report import ScheduledReport
        from repositories.scheduled_report_repository import ScheduledReportRepository
        repo = ScheduledReportRepository()
        repo.create(
            user_id=user.id, report_type='daily', frequency='daily',
            report_format='html', next_run_at=_now() - timedelta(hours=1),
        )
        due = repo.get_due_reports()
        assert len(due) >= 1


class TestActivityLogModel:
    def test_create_activity(self, app, db, user):
        from models.activity_log import ActivityLog
        log = ActivityLog(
            user_id=user.id, action='login', description='User logged in',
        )
        db.session.add(log)
        db.session.commit()
        assert log.id is not None


class TestActivityLogRepository:
    def test_log_activity(self, app, db, user):
        from repositories.activity_log_repository import ActivityLogRepository
        repo = ActivityLogRepository()
        entry = repo.log_activity(user.id, 'login', 'Logged in')
        assert entry.id is not None
        assert entry.action == 'login'

    def test_get_user_activity(self, app, db, user):
        from repositories.activity_log_repository import ActivityLogRepository
        repo = ActivityLogRepository()
        repo.log_activity(user.id, 'login', 'Login')
        repo.log_activity(user.id, 'logout', 'Logout')
        logs = repo.get_user_activity(user.id)
        assert len(logs) == 2


class TestSchedulerService:
    def test_create_schedule(self, app, db, user):
        from services.scheduler_service import SchedulerService
        service = SchedulerService()
        schedule = service.create_schedule(
            user.id, 'youtube', 'dQw4w9WgXcQ', 'daily',
        )
        assert schedule.id is not None
        assert schedule.frequency == 'daily'
        assert schedule.next_run_at is not None

    def test_pause_resume(self, app, db, user):
        from services.scheduler_service import SchedulerService
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        s = repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily', next_run_at=_now(),
        )
        service = SchedulerService()
        paused = service.pause_schedule(s.id, user.id)
        assert paused.is_active is False

        resumed = service.resume_schedule(s.id, user.id)
        assert resumed.is_active is True

    def test_pause_wrong_user(self, app, db, user):
        from services.scheduler_service import SchedulerService
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        s = repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily', next_run_at=_now(),
        )
        service = SchedulerService()
        result = service.pause_schedule(s.id, 999)
        assert result is None

    def test_delete_schedule(self, app, db, user):
        from services.scheduler_service import SchedulerService
        from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
        repo = ScheduledAnalysisRepository()
        s = repo.create(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily', next_run_at=_now(),
        )
        service = SchedulerService()
        result = service.delete_schedule(s.id, user.id)
        assert result is True
        assert repo.get_by_id(s.id) is None


class TestNotificationService:
    def test_create_notification(self, app, db, user):
        from services.notification_service import NotificationService
        service = NotificationService()
        n = service.create(user.id, 'job_completed', 'Test')
        assert n.id is not None

    def test_get_unread_count(self, app, db, user):
        from services.notification_service import NotificationService
        service = NotificationService()
        service.create(user.id, 'info', 'N1')
        service.create(user.id, 'info', 'N2')
        assert service.get_unread_count(user.id) == 2

    def test_mark_all_read(self, app, db, user):
        from services.notification_service import NotificationService
        service = NotificationService()
        service.create(user.id, 'info', 'N1')
        service.create(user.id, 'info', 'N2')
        service.mark_all_read(user.id)
        assert service.get_unread_count(user.id) == 0


class TestMonitoringService:
    def test_get_dashboard_stats(self, app, db, user):
        from services.monitoring_service import MonitoringService
        from repositories.job_repository import JobRepository
        repo = JobRepository()
        j = repo.create_job(
            user_id=user.id, platform='youtube', source_type='analysis',
            source_input='test', comment_limit=100, request_hash='mon_test',
        )
        j.status = 'COMPLETED'
        db.session.commit()

        service = MonitoringService()
        stats = service.get_dashboard_stats()
        assert stats['completed'] >= 1
        assert stats['total'] >= 1
        assert 'success_rate' in stats


class TestActivityService:
    def test_log_and_retrieve(self, app, db, user):
        from services.activity_service import ActivityService
        service = ActivityService()
        service.log(user.id, 'login', 'Logged in')
        service.log(user.id, 'logout', 'Logged out')

        logs = service.get_user_activity(user.id)
        assert len(logs) == 2
        assert logs[0].action == 'logout'
        assert logs[1].action == 'login'


class TestTrendService:
    def test_empty_trends(self, app, db, user):
        from services.trend_service import TrendService
        service = TrendService()
        data = service.get_trends(user.id, days=30)
        assert data['total_analyses'] == 0

    def test_trends_with_data(self, app, db, user):
        from services.trend_service import TrendService
        from models.analysis import Analysis
        from models.comment_result import CommentResult

        a = Analysis(user_id=user.id, analysis_type='youtube')
        db.session.add(a)
        db.session.commit()

        for i in range(3):
            c = CommentResult(
                analysis_id=a.id, comment_text=f'Comment {i}', author='Tester',
                sentiment_score=50.0, toxicity_score=10.0, spam_score=5.0,
                bot_score=2.0, risk_score=20.0, risk_level='Low',
            )
            db.session.add(c)
        db.session.commit()

        service = TrendService()
        data = service.get_trends(user.id, days=30)
        assert data['total_analyses'] >= 1
        assert data['avg_sentiment'] == 50.0


class TestIntelligenceService:
    def test_extract_keywords(self, app, db):
        from services.intelligence_service import IntelligenceService
        from models.comment_result import CommentResult
        comments = [
            CommentResult(id=1, comment_text='This is a great video about technology'),
            CommentResult(id=2, comment_text='Technology is amazing and great'),
        ]
        svc = IntelligenceService()
        keywords = svc.extract_keywords(comments, top_n=3)
        assert len(keywords) > 0
        assert any(k['word'] == 'technology' for k in keywords)

    def test_extract_toxic_terms(self, app, db):
        from services.intelligence_service import IntelligenceService
        from models.comment_result import CommentResult
        comments = [
            CommentResult(id=1, comment_text='you are stupid and ugly'),
            CommentResult(id=2, comment_text='what a dumb idiot'),
        ]
        svc = IntelligenceService()
        terms = svc.extract_toxic_terms(comments)
        assert len(terms) > 0

    def test_extract_spam_indicators(self, app, db):
        from services.intelligence_service import IntelligenceService
        from models.comment_result import CommentResult
        comments = [
            CommentResult(id=1, comment_text='click here to buy now'),
        ]
        svc = IntelligenceService()
        indicators = svc.extract_spam_indicators(comments)
        assert len(indicators) > 0

    def test_analyze_comments(self, app, db):
        from services.intelligence_service import IntelligenceService
        from models.comment_result import CommentResult
        comments = [
            CommentResult(id=1, comment_text='Great technology video'),
        ]
        svc = IntelligenceService()
        result = svc.analyze_comments(comments)
        assert 'keywords' in result
        assert 'phrases' in result
        assert 'risk_terms' in result


class TestSystemHealthService:
    def test_get_health(self, app, db, user):
        from services.system_health_service import SystemHealthService
        with app.app_context():
            from flask import current_app
            current_app.config['YOUTUBE_API_KEY'] = ''
        service = SystemHealthService()
        health = service.get_health()
        assert 'database' in health
        assert health['database'] == 'connected'
        assert 'app_version' in health
        assert health['app_version'] == '10'


class TestMaintenanceService:
    def test_cleanup(self, app, db, user):
        from services.system_health_service import MaintenanceService
        from models.activity_log import ActivityLog
        for i in range(3):
            log = ActivityLog(
                user_id=user.id, action='login', description=f'Test {i}',
                created_at=_now() - timedelta(days=60),
            )
            db.session.add(log)
        db.session.commit()

        service = MaintenanceService()
        with app.app_context():
            results = service.cleanup_all(app)
        assert 'job_logs' in results


class TestMonitoringRoutes:
    # V14 Phase B: /admin/* requires the admin role. Normal users get 403
    # (these tests previously asserted the insecure 200 behavior).
    def test_monitoring_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/admin/monitoring')
        assert response.status_code == 403

    def test_health_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/admin/health')
        assert response.status_code == 403

    def test_maintenance_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/admin/maintenance')
        assert response.status_code == 403

    def test_monitoring_requires_auth(self, app, db, client):
        response = client.get('/admin/monitoring')
        assert response.status_code == 302


class TestScheduleRoutes:
    def test_schedule_list(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/analysis/schedules/')
        assert response.status_code == 200
        assert b'Schedules' in response.data

    def test_schedule_create_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/analysis/schedules/create')
        assert response.status_code == 200

    def test_schedule_create_post(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post('/analysis/schedules/create', data={
            'platform': 'youtube', 'source_input': 'dQw4w9WgXcQ',
            'frequency': 'daily', 'comment_limit': 100,
        })
        assert response.status_code == 302

    def test_schedule_requires_auth(self, app, db, client):
        response = client.get('/analysis/schedules/')
        assert response.status_code == 302


class TestNotificationRoutes:
    def test_notification_list(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/notifications/')
        assert response.status_code == 200
        assert b'Notifications' in response.data

    def test_unread_count_api(self, app, db, user, client):
        from models.notification import Notification
        db.session.add(Notification(user_id=user.id, type='info', title='Test'))
        db.session.commit()

        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/notifications/api/unread-count')
        assert response.status_code == 200
        data = response.get_json()
        assert data['count'] >= 1

    def test_mark_all_read(self, app, db, user, client):
        from models.notification import Notification
        db.session.add(Notification(user_id=user.id, type='info', title='T1'))
        db.session.add(Notification(user_id=user.id, type='info', title='T2'))
        db.session.commit()

        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post('/notifications/read-all')
        assert response.status_code == 302


class TestActivityRoutes:
    def test_activity_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/activity/')
        assert response.status_code == 200
        assert b'Activity' in response.data


class TestReportRoutes:
    def test_report_list(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/reports/')
        assert response.status_code == 200
        assert b'Reports' in response.data

    def test_report_create_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/reports/create')
        assert response.status_code == 200

    def test_report_create_post(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post('/reports/create', data={
            'frequency': 'daily', 'platform_filter': '', 'report_format': 'json',
        })
        assert response.status_code == 302


class TestTrendRoutes:
    def test_trends_page(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/trends/')
        assert response.status_code == 200
        assert b'Trends' in response.data

    def test_trends_data_api(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/trends/data?days=30&platform=all')
        assert response.status_code == 200
        data = response.get_json()
        assert 'total_analyses' in data


class TestReportGenerationService:
    def test_generate_report(self, app, db, user):
        from services.report_generation_service import ReportGenerationService
        from repositories.scheduled_report_repository import ScheduledReportRepository
        repo = ScheduledReportRepository()
        report = repo.create(
            user_id=user.id, report_type='daily', frequency='daily',
            report_format='json', next_run_at=_now(),
        )
        with app.app_context():
            service = ReportGenerationService()
            data = service.generate_report(report.id, app)
            assert data is not None
            assert 'total_analyses' in data

    def test_process_due_reports(self, app, db, user):
        from services.report_generation_service import ReportGenerationService
        from repositories.scheduled_report_repository import ScheduledReportRepository
        repo = ScheduledReportRepository()
        repo.create(
            user_id=user.id, report_type='daily', frequency='daily',
            report_format='json', next_run_at=_now() - timedelta(hours=1),
        )
        with app.app_context():
            service = ReportGenerationService()
            count = service.process_due_reports(app)
            assert count >= 1


class TestScheduleDetailRoutes:
    def test_schedule_detail(self, app, db, user, client):
        from models.scheduled_analysis import ScheduledAnalysis
        s = ScheduledAnalysis(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily', next_run_at=_now(),
        )
        db.session.add(s)
        db.session.commit()
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get(f'/analysis/schedules/{s.id}')
        assert response.status_code == 200

    def test_schedule_detail_not_found(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/analysis/schedules/999')
        assert response.status_code == 302

    def test_schedule_delete(self, app, db, user, client):
        from models.scheduled_analysis import ScheduledAnalysis
        s = ScheduledAnalysis(
            user_id=user.id, platform='youtube', source_type='url',
            source_input='test', frequency='daily', next_run_at=_now(),
        )
        db.session.add(s)
        db.session.commit()
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post(f'/analysis/schedules/{s.id}/delete')
        assert response.status_code == 302


class TestNotificationMarkRead:
    def test_mark_read(self, app, db, user, client):
        from models.notification import Notification
        n = Notification(user_id=user.id, type='info', title='Test')
        db.session.add(n)
        db.session.commit()
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post(f'/notifications/{n.id}/read',
                               headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True


class TestActivityDataAPI:
    def test_activity_data(self, app, db, user, client):
        from models.activity_log import ActivityLog
        db.session.add(ActivityLog(user_id=user.id, action='login', description='Test'))
        db.session.commit()
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.get('/activity/data')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) >= 1


class TestAdminRoutes:
    # V14 Phase B: scheduler/maintenance POSTs require the admin role.
    def test_scheduler_run(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post('/admin/scheduler/run')
        assert response.status_code == 403

    def test_mark_stale(self, app, db, user, client):
        client.post('/auth/login', data={
            'email': 'test@example.com', 'password': 'TestPass123',
        })
        response = client.post('/admin/mark-stale-failed')
        assert response.status_code == 403


class TestWorkerHealthEdgeCases:
    def test_degraded_status(self, app, db):
        from models.worker_health import WorkerHealth
        w = WorkerHealth(worker_name='degraded', status=WorkerHealth.DEGRADED)
        db.session.add(w)
        db.session.commit()
        assert w.status == 'degraded'

    def test_get_summary_empty(self, app, db):
        from repositories.worker_health_repository import WorkerHealthRepository
        repo = WorkerHealthRepository()
        summary = repo.get_summary()
        assert summary['total'] == 0


class TestIntelligenceEdgeCases:
    def test_empty_comments(self, app, db):
        from services.intelligence_service import IntelligenceService
        svc = IntelligenceService()
        result = svc.analyze_comments([])
        assert result['keywords'] == []
        assert result['phrases'] == []
        assert result['risk_terms'] == []

    def test_keywords_empty_text(self, app, db):
        from services.intelligence_service import IntelligenceService
        from models.comment_result import CommentResult
        comments = [CommentResult(id=1, comment_text='a an the')]
        svc = IntelligenceService()
        keywords = svc.extract_keywords(comments)
        assert len(keywords) == 0


class TestSchedulerServiceEdgeCases:
    def test_create_all_frequencies(self, app, db, user):
        from services.scheduler_service import SchedulerService
        service = SchedulerService()
        for freq in ['once', 'daily', 'weekly', 'monthly']:
            s = service.create_schedule(user.id, 'youtube', f'test_{freq}', freq)
            assert s.frequency == freq
            assert s.next_run_at is not None

    def test_process_due_schedules_empty(self, app, db, user):
        from services.scheduler_service import SchedulerService
        with app.app_context():
            service = SchedulerService()
            count = service.process_due_schedules(app)
            assert count == 0
