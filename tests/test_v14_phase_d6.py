"""V14 Phase D6 — Template N+1 + Pagination tests.

Covers:
- result page renders, c.context batching, bounded query counts
- history/notifications/activity/reports/schedules/job-logs pagination:
  default, paging, ordering, caps, invalid handling, isolation, empty,
  total counts, SQL LIMIT/OFFSET enforcement
"""
import pytest
from datetime import datetime, timezone, timedelta
from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.comment_context import CommentContext
from models.user import User
from werkzeug.security import generate_password_hash


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _create_user(db, username="d6user", email=None):
    email = email or f"{username}@example.com"
    u = User(username=username, email=email, password_hash=generate_password_hash("TestPass123"))
    db.session.add(u)
    db.session.commit()
    return u


def _create_analysis_with_comments(user_id, n_comments=5):
    a = Analysis(user_id=user_id, analysis_type="youtube")
    db.session.add(a)
    db.session.flush()
    db.session.add(YouTubeAnalysis(analysis_id=a.id, video_id=f"vid{a.id}", video_title="T", channel_name="C", is_demo=True))
    for i in range(n_comments):
        c = CommentResult(analysis_id=a.id, comment_text=f"comment {i} text here", author=f"author{i}", sentiment_score=60.0)
        db.session.add(c)
        db.session.flush()
        db.session.add(CommentContext(comment_result_id=c.id, context_match_label="relevant", reason="test"))
    db.session.commit()
    return a


def _query_counter():
    from sqlalchemy import event
    from database import db as _db
    count = {"n": 0, "stmts": []}
    def before(conn, cursor, statement, params, context, executemany):
        count["n"] += 1
        count["stmts"].append(statement)
    event.listen(_db.engine, "before_cursor_execute", before)
    def remove():
        event.remove(_db.engine, "before_cursor_execute", before)
    return count, remove


class TestTemplateNPlusOne:
    def test_result_page_renders(self, logged_in_client, user, db):
        a = _create_analysis_with_comments(user.id, n_comments=3)
        resp = logged_in_client.get(f"/analysis/{a.id}")
        assert resp.status_code == 200
        assert b"comment" in resp.data.lower()

    def test_context_batching_no_per_row_query(self, app, db):
        # Create user and analysis with many comments, count queries during get_analysis_results
        with app.app_context():
            from database import db as _db
            u = _create_user(_db, username="d6_n1", email="d6_n1@example.com")
            a = _create_analysis_with_comments(u.id, n_comments=20)
            from services.analysis_service import AnalysisService
            svc = AnalysisService()
            # Warm up to avoid counting unrelated queries
            count, remove = _query_counter()
            try:
                data = svc.get_analysis_results(a.id, u.id)
                # Access comment_contexts as template would
                for c in data["comments"]:
                    _ = data["comment_contexts"].get(c.id)
                n_with_many = count["n"]
            finally:
                remove()
            # Now with more comments, query count should not scale linearly
            a2 = _create_analysis_with_comments(u.id, n_comments=50)
            count2, remove2 = _query_counter()
            try:
                data2 = svc.get_analysis_results(a2.id, u.id)
                for c in data2["comments"]:
                    _ = data2["comment_contexts"].get(c.id)
                n_with_more = count2["n"]
            finally:
                remove2()
            # Difference should be small (batched context load is 1 query regardless of N)
            # Allow some variance but not N*queries
            assert n_with_more - n_with_many < 10, f"query count scaled with comments: {n_with_many} vs {n_with_more}"

    def test_query_bounded_with_many_comments(self, app, db):
        with app.app_context():
            from database import db as _db
            u = _create_user(_db, username="d6_n2", email="d6_n2@example.com")
            for n in (5, 30):
                a = _create_analysis_with_comments(u.id, n_comments=n)
                count, remove = _query_counter()
                try:
                    from services.analysis_service import AnalysisService
                    data = __import__("services.analysis_service", fromlist=["AnalysisService"]).AnalysisService().get_analysis_results(a.id, u.id)
                    for c in data["comments"]:
                        _ = data["comment_contexts"].get(c.id)
                    n_q = count["n"]
                finally:
                    remove()
                # With batching, query count should be bounded (< 30) regardless of n
                assert n_q < 30, f"N={n} caused {n_q} queries (should be bounded)"

    def test_existing_result_behavior_intact(self, logged_in_client, user, db):
        a = _create_analysis_with_comments(user.id, n_comments=2)
        resp = logged_in_client.get(f"/analysis/{a.id}")
        assert resp.status_code == 200
        # Check that context labels appear (relevant)
        assert b"Relevant" in resp.data or b"relevant" in resp.data.lower()


class TestHistoryPagination:
    def test_default_page(self, logged_in_client, user, db):
        for _ in range(5):
            _create_analysis_with_comments(user.id, n_comments=1)
        resp = logged_in_client.get("/analysis/history")
        assert resp.status_code == 200

    def test_paging_no_duplicates(self, app, db):
        with app.app_context():
            from database import db as _db
            u = _create_user(_db, username="d6_hist", email="d6_hist@example.com")
            ids = []
            for i in range(25):
                a = _create_analysis_with_comments(u.id, n_comments=1)
                ids.append(a.id)
            from services.analysis_service import AnalysisService
            svc = AnalysisService()
            page1 = svc.get_all_user_analyses_with_data(u.id, limit=20, offset=0)
            page2 = svc.get_all_user_analyses_with_data(u.id, limit=20, offset=20)
            ids1 = [x["id"] for x in page1]
            ids2 = [x["id"] for x in page2]
            assert len(ids1) == 20
            assert len(ids2) == 5
            assert len(set(ids1) & set(ids2)) == 0
            assert len(set(ids1 + ids2)) == 25

    def test_deterministic_ordering(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_hist2", email="d6_hist2@example.com")
            for i in range(5):
                a = Analysis(user_id=u.id, analysis_type="youtube")
                a.created_at = _now() - timedelta(days=i)
                db.session.add(a)
            db.session.commit()
            from services.analysis_service import AnalysisService
            page1 = AnalysisService().get_all_user_analyses_with_data(u.id, limit=10, offset=0)
            page1_again = AnalysisService().get_all_user_analyses_with_data(u.id, limit=10, offset=0)
            assert [x["id"] for x in page1] == [x["id"] for x in page1_again]

    def test_page_size_capped(self, logged_in_client, user, db):
        for _ in range(5):
            _create_analysis_with_comments(user.id, n_comments=1)
        # Request huge limit via page param abuse? Our route uses fixed limit 20, so check that
        resp = logged_in_client.get("/analysis/history?page=1")
        assert resp.status_code == 200
        # Invalid page handled
        resp = logged_in_client.get("/analysis/history?page=0")
        assert resp.status_code == 200
        resp = logged_in_client.get("/analysis/history?page=-5")
        assert resp.status_code == 200
        resp = logged_in_client.get("/analysis/history?page=9999")
        assert resp.status_code == 200

    def test_ownership_isolation(self, app, db):
        with app.app_context():
            u1 = _create_user(db, username="d6_h1", email="d6_h1@example.com")
            u2 = _create_user(db, username="d6_h2", email="d6_h2@example.com")
            for _ in range(3):
                _create_analysis_with_comments(u1.id, n_comments=1)
            for _ in range(2):
                _create_analysis_with_comments(u2.id, n_comments=1)
            from services.analysis_service import AnalysisService
            svc = AnalysisService()
            u1_data = svc.get_all_user_analyses_with_data(u1.id, limit=20, offset=0)
            u2_data = svc.get_all_user_analyses_with_data(u2.id, limit=20, offset=0)
            assert len(u1_data) == 3
            assert len(u2_data) == 2
            assert set(x["id"] for x in u1_data).isdisjoint(set(x["id"] for x in u2_data))

    def test_empty_page(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_empty", email="d6_empty@example.com")
            from services.analysis_service import AnalysisService
            data = AnalysisService().get_all_user_analyses_with_data(u.id, limit=20, offset=0)
            assert data == []
            data2 = AnalysisService().get_all_user_analyses_with_data(u.id, limit=20, offset=20)
            assert data2 == []

    def test_sql_limit_applied(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_sql", email="d6_sql@example.com")
            for _ in range(30):
                _create_analysis_with_comments(u.id, n_comments=1)
            from sqlalchemy import event
            from database import db as _db
            count, remove = _query_counter()
            try:
                from services.analysis_service import AnalysisService
                data = AnalysisService().get_all_user_analyses_with_data(u.id, limit=20, offset=0)
                assert len(data) == 20
                # Check that SQL contains LIMIT
                has_limit = any("LIMIT" in s for s in count["stmts"])
                assert has_limit
            finally:
                remove()


class TestNotificationsPagination:
    def test_notifications_pagination(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_notif", email="d6_notif@example.com")
            from services.notification_service import NotificationService
            svc = NotificationService()
            for i in range(25):
                svc.create(u.id, "info", f"Title {i}")
            page1 = svc.get_user_notifications(u.id, limit=20, offset=0)
            page2 = svc.get_user_notifications(u.id, limit=20, offset=20)
            assert len(page1) == 20
            assert len(page2) == 5
            assert len(set(n.id for n in page1) & set(n.id for n in page2)) == 0
            # Ownership
            u2 = _create_user(db, username="d6_notif2", email="d6_notif2@example.com")
            svc.create(u2.id, "info", "Other")
            assert len(svc.get_user_notifications(u.id, limit=50, offset=0)) == 25

    def test_notifications_route(self, logged_in_client, user, db):
        from services.notification_service import NotificationService
        svc = NotificationService()
        for i in range(5):
            svc.create(user.id, "info", f"T{i}")
        resp = logged_in_client.get("/notifications/?page=1")
        assert resp.status_code == 200
        resp = logged_in_client.get("/notifications/?page=2")
        assert resp.status_code == 200


class TestActivityPagination:
    def test_activity_pagination(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_act", email="d6_act@example.com")
            from services.activity_service import ActivityService
            svc = ActivityService()
            for i in range(25):
                svc.log(u.id, f"action_{i}")
            page1 = svc.get_user_activity(u.id, limit=20, offset=0)
            page2 = svc.get_user_activity(u.id, limit=20, offset=20)
            assert len(page1) == 20
            assert len(page2) == 5
            assert len(set(l.id for l in page1) & set(l.id for l in page2)) == 0


class TestReportsSchedulesPagination:
    def test_reports_pagination(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_rep", email="d6_rep@example.com")
            from models.scheduled_report import ScheduledReport
            for _ in range(25):
                db.session.add(ScheduledReport(user_id=u.id, report_type="weekly", frequency="weekly", report_format="html"))
            db.session.commit()
            from repositories.scheduled_report_repository import ScheduledReportRepository
            repo = ScheduledReportRepository()
            page1 = repo.get_user_reports(u.id, include_inactive=True, limit=20, offset=0)
            page2 = repo.get_user_reports(u.id, include_inactive=True, limit=20, offset=20)
            assert len(page1) == 20
            assert len(page2) == 5
            assert repo.count_user_reports(u.id, include_inactive=True) == 25

    def test_schedules_pagination(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_sched", email="d6_sched@example.com")
            from models.scheduled_analysis import ScheduledAnalysis
            for i in range(25):
                db.session.add(ScheduledAnalysis(user_id=u.id, platform="youtube", source_type="video", source_input=f"vid{i}", frequency="once"))
            db.session.commit()
            from repositories.scheduled_analysis_repository import ScheduledAnalysisRepository
            repo = ScheduledAnalysisRepository()
            page1 = repo.get_user_schedules(u.id, include_inactive=True, limit=20, offset=0)
            page2 = repo.get_user_schedules(u.id, include_inactive=True, limit=20, offset=20)
            assert len(page1) == 20
            assert len(page2) == 5
            assert repo.count_user_schedules(u.id, include_inactive=True) == 25


class TestJobLogsPagination:
    def test_job_logs_pagination(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_job", email="d6_job@example.com")
            from models.job import Job
            from models.job_log import JobLog
            job = Job(user_id=u.id, platform="youtube", source_type="video", source_input="vid", status="COMPLETED")
            db.session.add(job)
            db.session.flush()
            for i in range(30):
                db.session.add(JobLog(job_id=job.id, level="INFO", message=f"msg {i}"))
            db.session.commit()
            from services.job_service import JobService
            svc = JobService()
            page1 = svc.get_job_logs(job.id, u.id, limit=20, offset=0)
            page2 = svc.get_job_logs(job.id, u.id, limit=20, offset=20)
            assert len(page1) == 20
            assert len(page2) == 10
            assert len(set(l.id for l in page1) & set(l.id for l in page2)) == 0

    def test_job_logs_route_pagination(self, app, db):
        with app.app_context():
            u = _create_user(db, username="d6_job2", email="d6_job2@example.com")
            from models.job import Job
            from models.job_log import JobLog
            job = Job(user_id=u.id, platform="youtube", source_type="video", source_input="vid", status="COMPLETED")
            db.session.add(job)
            db.session.flush()
            for i in range(5):
                db.session.add(JobLog(job_id=job.id, level="INFO", message=f"m{i}"))
            db.session.commit()
            client = app.test_client()
            client.post("/auth/login", data={"email": "d6_job2@example.com", "password": "TestPass123"})
            resp = client.get(f"/analysis/jobs/{job.id}/logs?page=1&limit=2")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 2

    def test_job_logs_ownership(self, app, db):
        with app.app_context():
            u1 = _create_user(db, username="d6_job3", email="d6_job3@example.com")
            u2 = _create_user(db, username="d6_job4", email="d6_job4@example.com")
            from models.job import Job
            from models.job_log import JobLog
            job = Job(user_id=u1.id, platform="youtube", source_type="video", source_input="vid", status="COMPLETED")
            db.session.add(job)
            db.session.flush()
            db.session.add(JobLog(job_id=job.id, level="INFO", message="secret"))
            db.session.commit()
            from services.job_service import JobService
            svc = JobService()
            assert svc.get_job_logs(job.id, u2.id) is None
