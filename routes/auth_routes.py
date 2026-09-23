from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from services.auth_service import AuthService

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')
auth_service = AuthService()


def is_safe_url(target):
    """Allow only local application paths for post-login redirects.

    Rejects absolute URLs (scheme://host), protocol-relative URLs (//host),
    backslash tricks, and non-string input to prevent open redirects.
    """
    if not target or not isinstance(target, str):
        return False
    target = target.strip()
    if not target.startswith('/') or target.startswith('//'):
        return False
    if '\\' in target:
        return False
    return True


def safe_redirect_target(target, fallback_endpoint='dashboard.index'):
    if is_safe_url(target):
        return target
    return url_for(fallback_endpoint)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        result = auth_service.register(
            username=request.form.get('username', ''),
            email=request.form.get('email', ''),
            password=request.form.get('password', ''),
            confirm_password=request.form.get('confirm_password', ''),
        )

        if result['success']:
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('auth.login'))
        else:
            for field, error in result['errors'].items():
                flash(error, 'danger')

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        result = auth_service.login(
            email=request.form.get('email', ''),
            password=request.form.get('password', ''),
            remember=request.form.get('remember') == 'on',
        )

        if result['success']:
            flash('Welcome back!', 'success')
            next_page = request.args.get('next')
            return redirect(safe_redirect_target(next_page))
        else:
            for field, error in result['errors'].items():
                flash(error, 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    auth_service.logout()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@auth_bp.route('/profile')
@login_required
def profile():
    data = auth_service.get_profile_data(current_user)
    return render_template('auth/profile.html', data=data)
