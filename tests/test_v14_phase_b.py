"""V14 Phase B - Auth hardening tests.

Admin-role authorization, login rate limiting (fake + live Redis),
session/secret configuration, next-URL validation, and the v14_001
migration round-trip with the existing-user default.
"""
import pytest

from database import db
from models.user import User

ADMIN_PATHS = [
    ('/admin/monitoring', 'GET'),
    ('/admin/monitoring/data', 'GET'),
    ('/admin/health', 'GET'),
    ('/admin/maintenance', 'GET'),
    ('/admin/mark-stale-failed', 'POST'),
    ('/admin/scheduler/run', 'POST'),
]


def _login(client, email, password):
    return client.post('/auth/login', data={'email': email,
                                            'password': password})


def _make_admin(user):
    user.role = User.ROLE_ADMIN
    db.session.commit()


class TestAdminAuthorization:
    def test_user_cannot_access_admin(self, logged_in_client, user):
        assert user.role == User.ROLE_USER
        assert user.is_admin is False
        for path, method in ADMIN_PATHS:
            resp = logged_in_client.open(path, method=method)
            assert resp.status_code == 403, path

    def test_anonymous_redirected_to_login(self, client):
        for path, method in ADMIN_PATHS:
            resp = client.open(path, method=method)
            assert resp.status_code == 302, path

    def test_admin_can_access(self, logged_in_client, user):
        _make_admin(user)
        assert user.is_admin is True
        for path, method in ADMIN_PATHS:
            resp = logged_in_client.open(path, method=method)
            assert resp.status_code in (200, 302), (path, resp.status_code)

    def test_registration_creates_non_admin(self, client, db):
        client.post('/auth/register', data={
            'username': 'newbie', 'email': 'newbie@x.com',
            'password': 'Strong123', 'confirm_password': 'Strong123'})
        row = User.query.filter_by(email='newbie@x.com').first()
        assert row is not None and row.role == User.ROLE_USER
        assert row.is_admin is False

    def test_role_not_client_controllable(self, client, db):
        client.post('/auth/register', data={
            'username': 'sneaky', 'email': 'sneaky@x.com',
            'password': 'Strong123', 'confirm_password': 'Strong123',
            'role': 'admin', 'is_admin': 'true'})
        row = User.query.filter_by(email='sneaky@x.com').first()
        assert row is not None and row.role == User.ROLE_USER


class TestExistingAuthIntact:
    def test_login_logout_profile(self, client, user):
        assert _login(client, 'test@example.com', 'TestPass123').status_code == 302
        assert client.get('/auth/profile').status_code == 200
        assert client.get('/auth/logout').status_code == 302

    def test_invalid_password_still_rejected(self, client, user):
        resp = _login(client, 'test@example.com', 'Wrong123')
        assert resp.status_code == 200  # re-rendered with error
        assert b'Invalid email or password' in resp.data


class _FakeRedisService:
    """In-memory stand-in honoring RedisService.rate_limit semantics."""

    def __init__(self, store=None, deny=False, down=False):
        self.store = store if store is not None else {}
        self.deny = deny
        self.down = down

    def rate_limit(self, key, max_attempts=10, window=60):
        if self.down:
            raise ConnectionError('redis down')
        if self.deny:
            return False
        import time
        now = int(time.time())
        hits = [t for t in self.store.get(key, []) if t > now - window]
        if len(hits) >= max_attempts:
            self.store[key] = hits
            return False
        hits.append(now)
        self.store[key] = hits
        return True


def _unskip_testing(app):
    app.config['TESTING'] = False
    return app


class TestRateLimiting:
    def test_allowed_login_passes(self, app, db, user, monkeypatch):
        import services.auth_service as mod
        monkeypatch.setattr('services.redis_service.RedisService', _FakeRedisService)
        _unskip_testing(app)
        try:
            from services.auth_service import AuthService
            out = AuthService().login('test@example.com', 'TestPass123')
            assert out['success'] is True
        finally:
            app.config['TESTING'] = True

    def test_throttled_login_blocked(self, app, db, user, monkeypatch):
        import services.auth_service as mod
        monkeypatch.setattr('services.redis_service.RedisService',
                            lambda *a, **k: _FakeRedisService(deny=True))
        _unskip_testing(app)
        try:
            from services.auth_service import AuthService
            out = AuthService().login('test@example.com', 'TestPass123')
            assert out['success'] is False
            assert 'Too many login attempts' in out['errors']['general']
        finally:
            app.config['TESTING'] = True

    def test_redis_down_degrades_open(self, app, db, user, monkeypatch):
        import services.auth_service as mod
        monkeypatch.setattr('services.redis_service.RedisService',
                            lambda *a, **k: _FakeRedisService(down=True))
        _unskip_testing(app)
        try:
            from services.auth_service import AuthService
            out = AuthService().login('test@example.com', 'TestPass123')
            assert out['success'] is True
        finally:
            app.config['TESTING'] = True

    def test_repeated_failures_eventually_throttle(self, app, db, user,
                                                  monkeypatch):
        import services.auth_service as mod
        store = {}
        monkeypatch.setattr('services.redis_service.RedisService',
                            lambda *a, **k: _FakeRedisService(store=store))
        _unskip_testing(app)
        try:
            from services.auth_service import AuthService
            svc = AuthService()
            for _ in range(10):
                out = svc.login('test@example.com', 'Wrong123')
                assert 'Invalid email or password' in out['errors']['general']
            out = svc.login('test@example.com', 'Wrong123')
            assert 'Too many login attempts' in out['errors']['general']
        finally:
            app.config['TESTING'] = True

    def test_live_redis_window_reset(self, app, db, user):
        import time
        from services.redis_service import RedisService
        svc = RedisService()
        try:
            assert svc.check_connection() is True
        except Exception:
            pytest.skip('no live redis')
        key = 'v14phaseb:probe'
        svc.delete(key)
        assert svc.rate_limit(key, 2, 1) is True
        assert svc.rate_limit(key, 2, 1) is True
        assert svc.rate_limit(key, 2, 1) is False
        time.sleep(1.2)
        assert svc.rate_limit(key, 2, 1) is True
        svc.delete(key)


class TestSessionAndSecretConfig:
    def test_development_allows_http(self, app):
        assert app.config['SESSION_COOKIE_HTTPONLY'] is True
        assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'
        assert app.config['SESSION_COOKIE_SECURE'] is False
        assert app.config['REMEMBER_COOKIE_HTTPONLY'] is True

    def test_production_requires_secure_cookies(self):
        from config import ProductionConfig
        assert ProductionConfig.SESSION_COOKIE_SECURE is True
        assert ProductionConfig.REMEMBER_COOKIE_SECURE is True

    def test_production_boots_with_secret(self, monkeypatch):
        monkeypatch.setenv('FLASK_ENV', 'production')
        monkeypatch.setenv('SECRET_KEY', 'test-production-secret-xyz')
        from app import create_app
        prod = create_app()
        try:
            assert prod.config['SESSION_COOKIE_SECURE'] is True
        finally:
            prod = None

    def test_production_refuses_missing_secret(self, monkeypatch):
        monkeypatch.setenv('FLASK_ENV', 'production')
        monkeypatch.delenv('SECRET_KEY', raising=False)
        from app import create_app
        with pytest.raises(RuntimeError):
            create_app()


class TestNextValidation:
    def test_safe_targets(self, app):
        from routes.auth_routes import is_safe_url
        assert is_safe_url('/dashboard/') is True
        assert is_safe_url('/analysis/12') is True
        assert is_safe_url('/auth/profile') is True

    def test_malicious_targets_rejected(self, app):
        from routes.auth_routes import is_safe_url
        for bad in ('https://evil.example/x', 'http://evil.example',
                    '//evil.example/x', '///evil.example', 'javascript:alert(1)',
                    '\\\\evil\\share', '', None, 'dashboard/', 'java\tscript:1'):
            assert is_safe_url(bad) is False, bad

    def test_login_redirect_allows_safe_next(self, client, user):
        resp = client.post('/auth/login?next=/analysis/7',
                           data={'email': 'test@example.com',
                                 'password': 'TestPass123'})
        assert resp.status_code == 302
        assert resp.location.endswith('/analysis/7')

    def test_login_redirect_blocks_open_redirect(self, client, user):
        resp = client.post('/auth/login?next=https://evil.example/x',
                           data={'email': 'test@example.com',
                                 'password': 'TestPass123'})
        assert resp.status_code == 302
        assert 'evil.example' not in resp.location


class TestV14Migration:
    def test_chain_and_single_head(self, app):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'v14mig', 'migrations/versions/v14_001_user_roles.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == 'v14_001'
        assert module.down_revision == 'v13_001'
        from alembic.script import ScriptDirectory
        import os
        with app.app_context():
            heads = ScriptDirectory(
                os.path.join(app.root_path, 'migrations')).get_heads()
            assert heads == ['v14_001']

    def test_upgrade_gives_existing_users_safe_default(self, tmp_path):
        """Isolated round-trip in a subprocess.

        Runs in a subprocess because Config binds SQLALCHEMY_DATABASE_URI
        at import time from the checked-in .env (live PG); the subprocess
        env overrides it before any import, so live PG is never touched.
        """
        import os
        import subprocess
        import sys
        dbfile = tmp_path / 'v14mig.db'
        code = (
            "from app import create_app; from database import db;"
            "app = create_app('development');"
            "ctx = app.app_context(); ctx.push();"
            "from sqlalchemy import text;"
            "db.session.execute(text('DROP TABLE IF EXISTS users'));"
            "db.session.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(80), email VARCHAR(120), password_hash VARCHAR(256), created_at DATETIME, updated_at DATETIME)'));"
            "db.session.execute(text(\"INSERT INTO users (username, email, password_hash) VALUES ('legacy', 'legacy@x.com', 'hash')\"));"
            "db.session.execute(text('CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)'));"
            "db.session.execute(text('DELETE FROM alembic_version'));"
            "db.session.execute(text(\"INSERT INTO alembic_version VALUES ('v13_001')\"));"
            "db.session.commit();"
            "from flask_migrate import upgrade, downgrade;"
            "from sqlalchemy import inspect;"
            "upgrade(revision='v14_001');"
            "role = db.session.execute(text(\"SELECT role FROM users WHERE username='legacy'\")).scalar();"
            "assert role == 'user', role;"
            "assert 'role' in {c['name'] for c in inspect(db.engine).get_columns('users')};"
            "downgrade(revision='v13_001');"
            "assert 'role' not in {c['name'] for c in inspect(db.engine).get_columns('users')};"
            "upgrade(revision='v14_001');"
            "assert 'role' in {c['name'] for c in inspect(db.engine).get_columns('users')};"
            "print('ROUNDTRIP-OK')"
        )
        env = dict(os.environ)
        env['DATABASE_URL'] = f'sqlite:///{dbfile}'
        env['YOUTUBE_API_KEY'] = ''
        env['REDDIT_CLIENT_ID'] = ''
        env['REDDIT_CLIENT_SECRET'] = ''
        proc = subprocess.run(
            [sys.executable, '-c', code], capture_output=True, text=True,
            cwd='/home/asrar/socialsense-ai', env=env, timeout=120)
        assert 'ROUNDTRIP-OK' in proc.stdout, proc.stderr[-2000:]

    def test_model_default_is_non_admin(self, app, db):
        row = User(username='plain', email='plain@x.com',
                   password_hash='x')
        db.session.add(row)
        db.session.commit()
        assert row.role == 'user'
        assert row.is_admin is False
