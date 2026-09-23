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


@auth_bp.route('/password/change', methods=['POST'])
@login_required
def password_change():
    """Self-service password change for the current user only.

    No target user is accepted from input; the operation applies solely
    to ``current_user``. Success invalidates the session (see
    ``AuthService.change_password``) and redirects to login.
    """
    result = auth_service.change_password(
        current_user,
        request.form.get('current_password', ''),
        request.form.get('new_password', ''),
        request.form.get('confirm_password', ''),
    )
    if result['success']:
        flash('Password changed. Please log in again.', 'success')
        return redirect(url_for('auth.login'))
    for error in result['errors'].values():
        flash(error, 'danger')
    return redirect(url_for('auth.profile'))


@auth_bp.route('/profile')
@login_required
def profile():
    data = auth_service.get_profile_data(current_user)
    return render_template('auth/profile.html', data=data)


@auth_bp.route('/password/forgot', methods=['GET', 'POST'])
def password_forgot():
    """Start account recovery. Never reveals whether the email exists.

    The reset token is handed back ONLY through the development-only
    mechanism (see AuthService); production responses carry no token
    because no email delivery channel exists yet.
    """
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    token = None
    if request.method == 'POST':
        result = auth_service.request_password_reset(
            request.form.get('email', ''))
        if not result['success']:
            for error in result['errors'].values():
                flash(error, 'danger')
            return render_template('auth/forgot.html')
        flash(result['message'], 'info')
        token = result.get('token')
    return render_template('auth/forgot.html', dev_token=token)


@auth_bp.route('/password/reset/<token>', methods=['GET', 'POST'])
def password_reset(token):
    """Consume a reset token. Operates only on the token-bound account."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        result = auth_service.reset_password(
            token,
            request.form.get('new_password', ''),
            request.form.get('confirm_password', ''),
        )
        if result['success']:
            flash('Password has been reset. Please log in.', 'success')
            return redirect(url_for('auth.login'))
        for error in result['errors'].values():
            flash(error, 'danger')
    user, reason = auth_service.verify_reset_token(token)
    if user is None:
        flash(reason, 'danger')
        return redirect(url_for('auth.password_forgot'))
    return render_template('auth/reset.html', token=token)
