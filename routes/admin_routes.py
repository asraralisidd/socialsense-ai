from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user
from services.monitoring_service import MonitoringService
from services.system_health_service import MaintenanceService, SystemHealthService
from repositories.job_repository import JobRepository

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
monitoring_service = MonitoringService()
maintenance_service = MaintenanceService()


def admin_required(view):
    """Require an authenticated admin user (role derives from storage).

    Must be applied after (outside) ``login_required``. Non-admin users
    receive 403; the role is never accepted from client input.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(
                current_user, 'is_admin', False):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@admin_bp.route('/monitoring')
@login_required
@admin_required
def monitoring():
    stats = monitoring_service.get_dashboard_stats()
    return render_template('admin/monitoring.html', stats=stats)


@admin_bp.route('/monitoring/data')
@login_required
@admin_required
def monitoring_data():
    stats = monitoring_service.get_dashboard_stats()
    return jsonify(stats)


@admin_bp.route('/health')
@login_required
@admin_required
def health():
    svc = SystemHealthService()
    data = svc.get_health()
    return render_template('admin/health.html', health=data)


@admin_bp.route('/maintenance', methods=['GET', 'POST'])
@login_required
@admin_required
def maintenance():
    if request.method == 'POST':
        from flask import current_app
        results = maintenance_service.cleanup_all(current_app._get_current_object())
        flash(f'Cleanup complete: {results}', 'success')
        return redirect(url_for('admin.maintenance'))
    job_repo = JobRepository()
    stuck_count = job_repo.count_stuck_jobs()
    return render_template('admin/maintenance.html', stuck_count=stuck_count)


@admin_bp.route('/mark-stale-failed', methods=['POST'])
@login_required
@admin_required
def mark_stale_failed():
    from flask import current_app
    from services.background_worker import BackgroundWorker
    from services.job_queue_interface import ThreadPoolQueueProvider
    worker = BackgroundWorker(ThreadPoolQueueProvider(max_workers=1))
    worker.recover_stuck_jobs(current_app._get_current_object())
    flash('Stale jobs marked as failed.', 'success')
    return redirect(url_for('admin.maintenance'))


@admin_bp.route('/scheduler/run', methods=['POST'])
@login_required
@admin_required
def run_scheduler():
    from flask import current_app
    from services.scheduler_service import SchedulerService
    from services.report_generation_service import ReportGenerationService
    sched = SchedulerService()
    rpt = ReportGenerationService()
    sched_count = sched.process_due_schedules(current_app._get_current_object())
    rpt_count = rpt.process_due_reports(current_app._get_current_object())
    flash(f'Scheduler ran: {sched_count} analyses queued, {rpt_count} reports generated.', 'success')
    return redirect(url_for('admin.maintenance'))
