"""V14 Phase D7 — Cleanup/maintenance hardening tests."""
import os
import pytest
from datetime import datetime, timezone, timedelta
from database import db
from models.job import Job
from models.job_log import JobLog
from models.user import User
from repositories.job_repository import JobRepository
from werkzeug.security import generate_password_hash


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _user(username="d7user"):
    u = User(username=username, email=f"{username}@example.com", password_hash=generate_password_hash("TestPass123"))
    db.session.add(u)
    db.session.commit()
    return u


def _job(user_id, status, started_at=None, created_at=None):
    j = Job(user_id=user_id, platform="youtube", source_type="video", source_input="vid", status=status)
    if started_at is not None:
        j.started_at = started_at
    if created_at is not None:
        j.created_at = created_at
    db.session.add(j)
    db.session.flush()
    return j


class TestCountOptimization:
    def test_stuck_count_parity(self, app, db):
        u = _user("d7cnt1")
        repo = JobRepository()
        # Recent running job (not stuck) + old stuck job
        recent = _job(u.id, Job.RUNNING, started_at=_now() - timedelta(minutes=5))
        old = _job(u.id, Job.RUNNING, started_at=_now() - timedelta(hours=5))
        db.session.commit()
        assert repo.count_stuck_jobs() == 1
        assert len(repo.get_stuck_jobs()) == 1
        # Both count and full fetch agree
        assert repo.count_stuck_jobs() == len(repo.get_stuck_jobs())

    def test_empty_count(self, app, db):
        assert JobRepository().count_stuck_jobs() == 0
        assert JobRepository().get_stuck_jobs() == []


class TestStuckJobs:
    def test_recent_not_stuck(self, app, db):
        u = _user("d7recent")
        _job(u.id, Job.RUNNING, started_at=_now() - timedelta(minutes=2))
        _job(u.id, Job.PENDING, started_at=_now() - timedelta(minutes=2))
        db.session.commit()
        assert JobRepository().get_stuck_jobs() == []

    def test_old_is_stuck(self, app, db):
        u = _user("d7old")
        _job(u.id, Job.RUNNING, started_at=_now() - timedelta(hours=3))
        db.session.commit()
        assert len(JobRepository().get_stuck_jobs()) == 1

    def test_threshold_deterministic(self, app, db):
        u = _user("d7thresh")
        # Exactly at threshold +1s should be stuck; threshold is 2*MAX_JOB_RUNTIME (default 600*2=1200s =20min)
        # Use explicit max_age_seconds to make deterministic
        repo = JobRepository()
        _job(u.id, Job.RUNNING, started_at=_now() - timedelta(seconds=1210))
        _job(u.id, Job.RUNNING, started_at=_now() - timedelta(seconds=100))
        db.session.commit()
        assert len(repo.get_stuck_jobs(max_age_seconds=1200)) == 1
        assert len(repo.get_stuck_jobs(max_age_seconds=60)) == 2

    def test_states_preserved(self, app, db):
        u = _user("d7states")
        _job(u.id, Job.COMPLETED, started_at=_now() - timedelta(hours=5))
        _job(u.id, Job.FAILED, started_at=_now() - timedelta(hours=5))
        _job(u.id, Job.CANCELLED, started_at=_now() - timedelta(hours=5))
        _job(u.id, Job.RUNNING, started_at=_now() - timedelta(hours=5))
        db.session.commit()
        stuck = JobRepository().get_stuck_jobs()
        assert len(stuck) == 1
        assert stuck[0].status == Job.RUNNING


class TestCleanupBatching:
    def test_cleanup_respects_limit(self, app, db):
        u = _user("d7clean1")
        repo = JobRepository()
        for _ in range(5):
            _job(u.id, Job.COMPLETED, created_at=_now() - timedelta(days=40))
        db.session.commit()
        n1 = repo.cleanup_old_jobs(days=30, limit=2)
        assert n1 == 2
        n2 = repo.cleanup_old_jobs(days=30, limit=10)
        assert n2 == 3
        assert repo.cleanup_old_jobs(days=30) == 0

    def test_cleanup_ordering_deterministic(self, app, db):
        u = _user("d7clean2")
        repo = JobRepository()
        for i in range(3):
            _job(u.id, Job.COMPLETED, created_at=_now() - timedelta(days=40+i))
        db.session.commit()
        first_batch = repo.cleanup_old_jobs(days=30, limit=1)
        assert first_batch == 1
        remaining = Job.query.filter_by(user_id=u.id).count()
        assert remaining == 2

    def test_notification_cleanup_batched(self, app, db):
        from repositories.notification_repository import NotificationRepository
        u = _user("d7notif")
        repo = NotificationRepository()
        for _ in range(4):
            repo.create_notification(u.id, "info", "t")
            # Backdate
            n = repo.model.query.filter_by(user_id=u.id).order_by(repo.model.created_at.desc()).first()
            n.created_at = _now() - timedelta(days=40)
            db.session.commit()
        assert repo.delete_old_notifications(days=30, limit=2) == 2
        assert repo.delete_old_notifications(days=30) == 2

    def test_activity_cleanup_batched(self, app, db):
        from repositories.activity_log_repository import ActivityLogRepository
        u = _user("d7act")
        repo = ActivityLogRepository()
        for _ in range(4):
            repo.log_activity(u.id, "test")
            row = repo.model.query.filter_by(user_id=u.id).order_by(repo.model.created_at.desc()).first()
            row.created_at = _now() - timedelta(days=40)
            db.session.commit()
        assert repo.delete_old_logs(days=30, limit=2) == 2


class TestFileCleanup:
    def test_missing_file_safe(self, app):
        from services.analysis_service import AnalysisService
        AnalysisService._remove_export_files(["/tmp/opencode/missing_d7.csv"])
        AnalysisService._remove_export_files([os.path.join(app.config["UPLOAD_FOLDER"], "missing_d7.csv")])

    def test_contained_path_cleanable(self, app):
        from services.analysis_service import AnalysisService
        folder = app.config["UPLOAD_FOLDER"]
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "d7_test.csv")
        with open(path, "w") as f:
            f.write("x")
        assert os.path.isfile(path)
        AnalysisService._remove_export_files([path])
        assert not os.path.exists(path)

    def test_outside_path_never_deleted(self, app, tmp_path):
        from services.analysis_service import AnalysisService
        outside = tmp_path / "keep.csv"
        outside.write_text("important")
        AnalysisService._remove_export_files([str(outside)])
        assert outside.is_file()


class TestTrendDays:
    def test_days_zero_clamped(self, logged_in_client):
        assert logged_in_client.get("/trends/?days=0").status_code == 200
        assert logged_in_client.get("/trends/data?days=0").status_code == 200

    def test_negative_days_clamped(self, logged_in_client):
        assert logged_in_client.get("/trends/?days=-5").status_code == 200
        assert logged_in_client.get("/trends/data?days=-10").status_code == 200

    def test_normal_days_unchanged(self, logged_in_client):
        for days in (1, 7, 30):
            assert logged_in_client.get(f"/trends/?days={days}").status_code == 200
            assert logged_in_client.get(f"/trends/data?days={days}").status_code == 200
