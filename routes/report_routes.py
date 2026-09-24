from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from services.report_generation_service import ReportGenerationService
from repositories.scheduled_report_repository import ScheduledReportRepository

report_bp = Blueprint('reports', __name__, url_prefix='/reports')
report_service = ReportGenerationService()
report_repo = ScheduledReportRepository()


@report_bp.route('')
@report_bp.route('/')
@login_required
def list_reports():
    page = request.args.get('page', 1, type=int)
    if not page or page < 1:
        page = 1
    limit = 20
    offset = (page - 1) * limit
    reports = report_repo.get_user_reports(current_user.id, include_inactive=True, limit=limit, offset=offset)
    total = report_repo.count_user_reports(current_user.id, include_inactive=True)
    total_pages = (total + limit - 1) // limit if total else 1
    return render_template('reports/list.html', reports=reports, page=page, total_pages=total_pages, total=total)


@report_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        frequency = request.form.get('frequency', 'daily')
        platform_filter = request.form.get('platform_filter', '') or None
        report_format = request.form.get('report_format', 'html')

        from models.scheduled_report import ScheduledReport
        from datetime import datetime, timezone, timedelta

        def _now():
            return datetime.now(timezone.utc).replace(tzinfo=None)

        next_run = _now()
        if frequency == ScheduledReport.FREQ_DAILY:
            next_run = _now() + timedelta(days=1)
        elif frequency == ScheduledReport.FREQ_WEEKLY:
            next_run = _now() + timedelta(weeks=1)
        elif frequency == ScheduledReport.FREQ_MONTHLY:
            next_run = _now() + timedelta(days=30)

        report = report_repo.create(
            user_id=current_user.id, report_type=frequency, frequency=frequency,
            platform_filter=platform_filter, report_format=report_format, next_run_at=next_run,
        )

        from flask import current_app
        report_service.generate_report(report.id, current_app._get_current_object())

        flash(f'Scheduled {frequency} report created!', 'success')
        return redirect(url_for('reports.list_reports'))

    return render_template('reports/create.html')


@report_bp.route('/<int:report_id>/generate', methods=['POST'])
@login_required
def generate_now(report_id):
    report = report_repo.get_by_id(report_id)
    if not report or report.user_id != current_user.id:
        flash('Report not found.', 'danger')
        return redirect(url_for('reports.list_reports'))

    from flask import current_app
    report_service.generate_report(report.id, current_app._get_current_object())
    flash('Report generated!', 'success')
    return redirect(url_for('reports.list_reports'))


@report_bp.route('/<int:report_id>/toggle', methods=['POST'])
@login_required
def toggle(report_id):
    report = report_repo.get_by_id(report_id)
    if not report or report.user_id != current_user.id:
        flash('Report not found.', 'danger')
        return redirect(url_for('reports.list_reports'))
    report.is_active = not report.is_active
    from database import db
    db.session.commit()
    status = 'resumed' if report.is_active else 'paused'
    flash(f'Report {status}.', 'info')
    return redirect(url_for('reports.list_reports'))


@report_bp.route('/<int:report_id>/download')
@login_required
def download(report_id):
    report = report_repo.get_by_id(report_id)
    if not report or report.user_id != current_user.id:
        flash('Report not found.', 'danger')
        return redirect(url_for('reports.list_reports'))
    if not report.last_file_path:
        flash('No file available.', 'warning')
        return redirect(url_for('reports.list_reports'))
    from flask import send_file
    return send_file(report.last_file_path, as_attachment=True)
