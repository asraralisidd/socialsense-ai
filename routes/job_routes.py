from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from services.job_service import JobService

job_bp = Blueprint('jobs', __name__, url_prefix='/analysis/jobs')
job_service = JobService()


@job_bp.route('/create', methods=['POST'])
@login_required
def create():
    platform = request.form.get('platform', 'youtube')
    source_input = request.form.get('source_input', '').strip()
    comment_limit = request.form.get('comment_limit', 100, type=int)
    if comment_limit not in (100, 500, 1000):
        comment_limit = 100

    if not source_input:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'Input is required.'}), 400
        flash('Input is required.', 'danger')
        return redirect(url_for('analysis.new'))

    request_hash = job_service.compute_request_hash(current_user.id, platform, source_input)
    result = job_service.create_job(current_user.id, platform, source_input, comment_limit, request_hash)

    if not result['success']:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(result), 400
        flash(result['error'], 'danger')
        return redirect(url_for('analysis.new'))

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(result)

    flash('Analysis queued!', 'info')
    return redirect(url_for('jobs.status', job_id=result['job_id']))


@job_bp.route('')
@job_bp.route('/')
@login_required
def list_jobs():
    page = request.args.get('page', 1, type=int)
    if not page or page < 1:
        page = 1
    status = request.args.get('status', None)
    limit = 20
    offset = (page - 1) * limit
    jobs = job_service.get_jobs_for_user(current_user.id, limit=limit, offset=offset, status=status)
    total = job_service.job_repo.count_jobs_for_user(current_user.id, status=status)
    total_pages = (total + limit - 1) // limit if total else 1
    return render_template('jobs/history.html', jobs=jobs, current_status=status, page=page, total_pages=total_pages, total=total)


@job_bp.route('/<int:job_id>')
@login_required
def status(job_id):
    job = job_service.get_job(job_id, current_user.id)
    if not job:
        flash('Job not found.', 'danger')
        return redirect(url_for('dashboard.index'))
    return render_template('jobs/status.html', job=job)


@job_bp.route('/<int:job_id>/status')
@login_required
def job_status_api(job_id):
    data = job_service.get_job_status(job_id, current_user.id)
    if not data:
        return jsonify({'error': 'Job not found.'}), 404
    return jsonify(data)


@job_bp.route('/<int:job_id>/cancel', methods=['POST'])
@login_required
def cancel(job_id):
    result = job_service.cancel_job(job_id, current_user.id)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(result)
    if result['success']:
        flash('Job cancelled.', 'info')
    else:
        flash(result['error'], 'danger')
    return redirect(url_for('jobs.status', job_id=job_id))


@job_bp.route('/<int:job_id>/retry', methods=['POST'])
@login_required
def retry(job_id):
    result = job_service.retry_job(job_id, current_user.id)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(result)
    if result['success']:
        flash('Job queued for retry.', 'info')
    else:
        flash(result['error'], 'danger')
    return redirect(url_for('jobs.status', job_id=job_id))


@job_bp.route('/<int:job_id>/logs')
@login_required
def logs(job_id):
    page = request.args.get('page', 1, type=int)
    if not page or page < 1:
        page = 1
    limit = request.args.get('limit', 50, type=int)
    if not limit or limit < 1 or limit > 100:
        limit = 50
    offset = (page - 1) * limit
    logs = job_service.get_job_logs(job_id, current_user.id, limit=limit, offset=offset)
    if logs is None:
        return jsonify({'error': 'Job not found.'}), 404
    data = [{
        'id': l.id,
        'timestamp': l.timestamp.isoformat() if l.timestamp else None,
        'level': l.level,
        'step': l.step,
        'message': l.message,
    } for l in logs]
    return jsonify(data)


@job_bp.route('/metrics')
@login_required
def metrics():
    data = job_service.get_dashboard_metrics(current_user.id)
    return jsonify(data)
