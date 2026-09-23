"""V14 Phase C2 - Password reset/recovery tests."""
import pytest

from database import db
from models.user import User
from services.auth_service import AuthService


def _svc():
    return AuthService()


def _token_for(email):
    return _svc().request_password_reset(email)['token']


class TestForgotPassword:
    def test_existing_email(self, app, db, user):
        out = _svc().request_password_reset('test@example.com')
        assert out['success'] is True
        assert out['token']  # exposed: TESTING config (dev-only mechanism)
        assert 'message' in out and out['message']

    def test_unknown_email(self, app, db, user):
        out = _svc().request_password_reset('nobody@nowhere.example')
        assert out['success'] is True
        assert out['token'] is None

    def test_identical_response_no_enumeration(self, app, db, user):
        known = _svc().request_password_reset('test@example.com')
        unknown = _svc().request_password_reset('nobody@nowhere.example')
        assert known['success'] == unknown['success'] is True
        assert known['message'] == unknown['message']
        assert known['errors'] == unknown['errors'] == {}

    def test_forgot_page_and_link(self, client):
        assert client.get('/auth/password/forgot').status_code == 200
        body = client.get('/auth/login').get_data(as_text=True)
        assert 'Forgot password?' in body

    def test_forgot_route_uniform(self, client, user):
        for email in ('test@example.com', 'ghost@nowhere.example'):
            resp = client.post('/auth/password/forgot',
                               data={'email': email})
            assert resp.status_code == 200
            assert b'If an account exists' in resp.data

    def test_forgot_throttled(self, app, db, user, monkeypatch):
        from services.auth_service import AuthService as Svc

        class Deny:
            def rate_limit(self, *a, **k):
                return False

        monkeypatch.setattr('services.redis_service.RedisService', Deny)
        app.config['TESTING'] = False
        try:
            out = Svc().request_password_reset('test@example.com')
            assert out['success'] is False
            assert 'Too many reset attempts' in out['errors']['general']
            assert out['token'] is None
        finally:
            app.config['TESTING'] = True

    def test_forgot_redis_down_degrades_open(self, app, db, user, monkeypatch):
        class Down:
            def rate_limit(self, *a, **k):
                raise ConnectionError('redis down')

        monkeypatch.setattr('services.redis_service.RedisService', Down)
        app.config['TESTING'] = False
        try:
            out = AuthService().request_password_reset('test@example.com')
            assert out['success'] is True
        finally:
            app.config['TESTING'] = True


class TestResetToken:
    def test_valid_token(self, app, db, user):
        token = _token_for('test@example.com')
        found, reason = _svc().verify_reset_token(token)
        assert reason is None and found.id == user.id

    def test_token_contains_no_sensitive_data(self, app, db, user):
        token = _token_for('test@example.com')
        assert user.password_hash not in token
        assert 'test@example.com' not in token

    def test_expired_token(self, app, db, user, monkeypatch):
        token = _token_for('test@example.com')
        monkeypatch.setattr(AuthService, '_reset_token_max_age',
                            lambda self: -3600)
        found, reason = _svc().verify_reset_token(token)
        assert found is None and reason

    def test_malformed_token(self, app, db, user):
        for bad in (None, '', 'not-a-token', 'a.b', 12345):
            found, reason = _svc().verify_reset_token(bad)
            assert found is None and reason

    def test_tampered_token(self, app, db, user):
        token = _token_for('test@example.com')
        # Tamper a middle character: the final base64 quantum may contain
        # padding bits where a flip does not alter the decoded signature.
        mid = len(token) // 2
        tampered = token[:mid] + ('A' if token[mid] != 'A' else 'B') + token[mid + 1:]
        found, reason = _svc().verify_reset_token(tampered)
        assert found is None and reason

    def test_unknown_account(self, app, db):
        found, reason = _svc().verify_reset_token(
            _svc().generate_reset_token(
                type('U', (), {'id': 999999, 'password_hash': 'x'})()))
        assert found is None and reason

    def test_invalid_after_password_change(self, app, db, user):
        token = _token_for('test@example.com')
        user.password_hash = 'changed-hash'
        db.session.commit()
        found, reason = _svc().verify_reset_token(token)
        assert found is None and reason


class TestResetFlow:
    def test_successful_reset(self, client, user, db):
        token = _token_for('test@example.com')
        resp = client.post(f'/auth/password/reset/{token}',
                           data={'new_password': 'BrandNew123',
                                 'confirm_password': 'BrandNew123'})
        assert resp.status_code == 302
        assert '/auth/login' in resp.location

    def test_new_password_works_old_rejected(self, client, user, db):
        token = _token_for('test@example.com')
        client.post(f'/auth/password/reset/{token}',
                    data={'new_password': 'BrandNew123',
                          'confirm_password': 'BrandNew123'})
        old = client.post('/auth/login', data={'email': 'test@example.com',
                                               'password': 'TestPass123'})
        assert old.status_code == 200
        assert b'Invalid email or password' in old.data
        new = client.post('/auth/login', data={'email': 'test@example.com',
                                               'password': 'BrandNew123'})
        assert new.status_code == 302

    def test_replay_after_reset_fails(self, client, user, db):
        token = _token_for('test@example.com')
        client.post(f'/auth/password/reset/{token}',
                    data={'new_password': 'BrandNew123',
                          'confirm_password': 'BrandNew123'})
        resp = client.post(f'/auth/password/reset/{token}',
                           data={'new_password': 'Another123',
                                  'confirm_password': 'Another123'})
        assert resp.status_code == 302
        assert 'password/forgot' in resp.location
        # second password was NOT applied; first reset stands
        again = client.post('/auth/login', data={'email': 'test@example.com',
                                                 'password': 'BrandNew123'})
        assert again.status_code == 302

    def test_weak_and_mismatch_rejected(self, client, user, db):
        before = User.query.filter_by(email='test@example.com').first().password_hash
        token = _token_for('test@example.com')
        resp = client.post(f'/auth/password/reset/{token}',
                           data={'new_password': 'weak',
                                  'confirm_password': 'weak'})
        assert resp.status_code == 200
        resp = client.post(f'/auth/password/reset/{token}',
                           data={'new_password': 'GoodPass1',
                                  'confirm_password': 'GoodPass2'})
        assert resp.status_code == 200
        after = User.query.filter_by(email='test@example.com').first().password_hash
        assert after == before

    def test_reset_page_guards(self, client, user, db):
        assert client.get('/auth/password/reset/bogus-token').status_code == 302
        token = _token_for('test@example.com')
        assert client.get(f'/auth/password/reset/{token}').status_code == 200

    def test_authenticated_users_redirected(self, logged_in_client):
        assert logged_in_client.get('/auth/password/forgot').status_code == 302
        assert logged_in_client.get('/auth/password/reset/sometoken').status_code == 302

    def test_cross_user_isolation(self, client, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='resetvictim', email='victim@x.com',
                     password_hash=generate_password_hash('Victim123'))
        db.session.add(other)
        db.session.commit()
        victim_hash = User.query.filter_by(email='victim@x.com').first().password_hash
        token = _token_for('test@example.com')
        client.post(f'/auth/password/reset/{token}',
                    data={'new_password': 'BrandNew123',
                          'confirm_password': 'BrandNew123'})
        assert User.query.filter_by(email='victim@x.com').first().password_hash == victim_hash

    def test_no_hash_leakage(self, client, user, db):
        token = _token_for('test@example.com')
        body = client.get(f'/auth/password/reset/{token}').get_data(as_text=True)
        assert user.password_hash not in body
        assert 'pbkdf2' not in body

    def test_production_hides_token(self, app, db, user):
        app.config['TESTING'] = False
        try:
            out = AuthService().request_password_reset('test@example.com')
            assert out['success'] is True
            assert out['token'] is None
            assert out['message'] == AuthService.RESET_REQUEST_MESSAGE
        finally:
            app.config['TESTING'] = True
