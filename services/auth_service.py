import logging
import re
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, logout_user, login_required
from repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    LOGIN_RATE_LIMIT_KEY = 'login_attempts'
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300

    def __init__(self, user_repository=None):
        self.user_repo = user_repository or UserRepository()

    def _login_allowed(self, email):
        """Sliding-window login gate reusing RedisService.rate_limit().

        Redis is optional: any Redis failure degrades open (login proceeds)
        with a warning, matching the application's graceful-degradation
        convention. Every call records one attempt; exceeding the maximum
        within the window denies the attempt.

        The check is skipped when the app runs with TESTING config so the
        shared-credential test suite cannot throttle itself; throttled and
        Redis-down behavior are covered by focused tests using a fake
        RedisService.
        """
        try:
            from flask import current_app
            if current_app and current_app.config.get('TESTING'):
                return True
            max_attempts = int(current_app.config.get(
                'LOGIN_RATE_LIMIT_MAX_ATTEMPTS',
                self.LOGIN_RATE_LIMIT_MAX_ATTEMPTS))
            window = int(current_app.config.get(
                'LOGIN_RATE_LIMIT_WINDOW_SECONDS',
                self.LOGIN_RATE_LIMIT_WINDOW_SECONDS))
        except Exception:
            max_attempts = self.LOGIN_RATE_LIMIT_MAX_ATTEMPTS
            window = self.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        try:
            from services.redis_service import RedisService
            key = f'{self.LOGIN_RATE_LIMIT_KEY}:{email.strip().lower()}'
            return RedisService().rate_limit(key, max_attempts, window)
        except Exception as exc:
            logger.warning(f'Login rate-limit unavailable, allowing: {exc}')
            return True

    def _validate_password(self, password):
        """Shared password policy: minimum 8 characters with upper/lower/digit.

        Returns an error string or None when the password complies.
        """
        if not password or len(password) < 8:
            return 'Password must be at least 8 characters.'
        if not re.search(r'[A-Z]', password):
            return 'Password must contain an uppercase letter.'
        if not re.search(r'[a-z]', password):
            return 'Password must contain a lowercase letter.'
        if not re.search(r'[0-9]', password):
            return 'Password must contain a number.'
        return None

    def register(self, username, email, password, confirm_password):
        errors = {}

        if not username or len(username.strip()) < 3:
            errors['username'] = 'Username must be at least 3 characters.'
        elif self.user_repo.username_exists(username):
            errors['username'] = 'Username already taken.'

        if not email or '@' not in email:
            errors['email'] = 'Valid email is required.'
        elif self.user_repo.email_exists(email):
            errors['email'] = 'Email already registered.'

        password_error = self._validate_password(password)
        if password_error:
            errors['password'] = password_error

        if password != confirm_password:
            errors['confirm_password'] = 'Passwords do not match.'

        if errors:
            return {'success': False, 'errors': errors}

        password_hash = generate_password_hash(password)
        user = self.user_repo.create(
            username=username.strip(),
            email=email.strip().lower(),
            password_hash=password_hash
        )

        return {'success': True, 'user': user}

    def login(self, email, password, remember=False):
        errors = {}

        if not email:
            errors['email'] = 'Email is required.'
        if not password:
            errors['password'] = 'Password is required.'

        if errors:
            return {'success': False, 'errors': errors}

        if not self._login_allowed(email):
            return {'success': False,
                    'errors': {'general': 'Too many login attempts. Please try again later.'}}

        user = self.user_repo.get_by_email(email.strip().lower())
        if not user or not check_password_hash(user.password_hash, password):
            return {'success': False, 'errors': {'general': 'Invalid email or password.'}}

        login_user(user, remember=remember)
        return {'success': True, 'user': user}

    def logout(self):
        logout_user()

    def change_password(self, user, current_password, new_password,
                        confirm_password):
        """Change the password of the given (already authenticated) user.

        Verifies the current password first; nothing is modified unless
        every check passes. On success the stored hash is replaced and the
        current session is invalidated via ``logout_user()``, forcing
        re-authentication. Never exposes hashes.
        """
        errors = {}

        if user is None:
            return {'success': False,
                    'errors': {'general': 'Authentication required.'}}

        if not current_password or not check_password_hash(
                user.password_hash, current_password):
            errors['current_password'] = 'Current password is incorrect.'

        password_error = self._validate_password(new_password)
        if password_error:
            errors['new_password'] = password_error
        elif check_password_hash(user.password_hash, new_password):
            errors['new_password'] = \
                'New password must be different from the current password.'

        if new_password != confirm_password:
            errors['confirm_password'] = 'Passwords do not match.'

        if errors:
            return {'success': False, 'errors': errors}

        from database import db
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        logout_user()
        return {'success': True, 'errors': {}}

    def get_profile_data(self, user):
        analysis_count = self.user_repo.count_analyses(user.id) if hasattr(self.user_repo, 'count_analyses') else 0
        recent_analyses = self.user_repo.get_recent_analyses(user.id)

        return {
            'username': user.username,
            'email': user.email,
            'created_at': user.created_at,
            'analysis_count': analysis_count,
            'recent_analyses': recent_analyses,
        }
