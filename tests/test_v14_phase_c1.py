"""V14 Phase C1 - Authenticated password change tests."""
import pytest

from database import db
from models.user import User


def _change(client, current, new, confirm, extra=None):
    data = {'current_password': current, 'new_password': new,
            'confirm_password': confirm}
    if extra:
        data.update(extra)
    return client.post('/auth/password/change', data=data)


def _hash(email):
    return User.query.filter_by(email=email).first().password_hash


class TestPasswordChange:
    def test_success(self, logged_in_client, user):
        resp = _change(logged_in_client, 'TestPass123', 'NewPass456', 'NewPass456')
        assert resp.status_code == 302
        assert '/auth/login' in resp.location

    def test_login_with_new_password(self, client, user):
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _change(client, 'TestPass123', 'NewPass456', 'NewPass456')
        client.get('/auth/logout')
        resp = client.post('/auth/login', data={'email': 'test@example.com',
                                                'password': 'NewPass456'})
        assert resp.status_code == 302
        assert client.get('/auth/profile').status_code == 200

    def test_old_password_no_longer_works(self, client, user):
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _change(client, 'TestPass123', 'NewPass456', 'NewPass456')
        client.get('/auth/logout')
        resp = client.post('/auth/login', data={'email': 'test@example.com',
                                                'password': 'TestPass123'})
        assert resp.status_code == 200  # re-rendered with error
        assert b'Invalid email or password' in resp.data

    def test_wrong_current_password(self, logged_in_client, user):
        before = _hash('test@example.com')
        resp = _change(logged_in_client, 'Wrong123', 'NewPass456', 'NewPass456')
        assert resp.status_code == 302
        assert _hash('test@example.com') == before
        assert logged_in_client.get('/auth/profile').status_code == 200

    def test_weak_new_password(self, logged_in_client, user):
        before = _hash('test@example.com')
        for weak in ('short', 'alllowercase1', 'ALLUPPERCASE1', 'NoDigitsHere'):
            resp = _change(logged_in_client, 'TestPass123', weak, weak)
            assert resp.status_code == 302
        assert _hash('test@example.com') == before

    def test_mismatched_confirmation(self, logged_in_client, user):
        before = _hash('test@example.com')
        resp = _change(logged_in_client, 'TestPass123', 'NewPass456', 'OtherPass789')
        assert resp.status_code == 302
        assert _hash('test@example.com') == before

    def test_same_as_current_rejected(self, logged_in_client, user):
        before = _hash('test@example.com')
        resp = _change(logged_in_client, 'TestPass123', 'TestPass123', 'TestPass123')
        assert resp.status_code == 302
        assert _hash('test@example.com') == before

    def test_session_invalidated(self, logged_in_client, user):
        _change(logged_in_client, 'TestPass123', 'NewPass456', 'NewPass456')
        assert logged_in_client.get('/auth/profile').status_code == 302

    def test_unauthenticated_redirected(self, client):
        resp = _change(client, 'TestPass123', 'NewPass456', 'NewPass456')
        assert resp.status_code == 302

    def test_ignores_target_user_input(self, client, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='victim', email='victim@x.com',
                     password_hash=generate_password_hash('Victim123'))
        db.session.add(other)
        db.session.commit()
        victim_hash = _hash('victim@x.com')
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _change(client, 'TestPass123', 'NewPass456', 'NewPass456',
                extra={'user_id': other.id, 'email': 'victim@x.com',
                       'username': 'victim'})
        assert _hash('victim@x.com') == victim_hash

    def test_other_user_unaffected(self, client, db, user):
        from werkzeug.security import generate_password_hash
        other = User(username='bystander', email='bystander@x.com',
                     password_hash=generate_password_hash('Bystander1'))
        db.session.add(other)
        db.session.commit()
        client.post('/auth/login', data={'email': 'test@example.com',
                                         'password': 'TestPass123'})
        _change(client, 'TestPass123', 'NewPass456', 'NewPass456')
        client.get('/auth/logout')
        resp = client.post('/auth/login', data={'email': 'bystander@x.com',
                                                'password': 'Bystander1'})
        assert resp.status_code == 302

    def test_profile_page_has_change_form(self, logged_in_client, user):
        body = logged_in_client.get('/auth/profile').get_data(as_text=True)
        assert 'Change Password' in body
        assert 'current_password' in body

    def test_hash_never_exposed(self, logged_in_client, user):
        body = logged_in_client.get('/auth/profile').get_data(as_text=True)
        assert _hash('test@example.com') not in body
        assert 'pbkdf2' not in body
