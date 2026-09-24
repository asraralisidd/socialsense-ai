from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from services.scheduler_service import SchedulerService

schedule_bp = Blueprint('schedules', __name__, url_prefix='/analysis/schedules')
scheduler_service = SchedulerService()


@schedule_bp.route('')
@schedule_bp.route('/')
@login_required
def list_schedules():
    page = request.args.get('page', 1, type=int)
    if not page or page < 1:
        page = 1
    limit = 20
    offset = (page - 1) * limit
    schedules = scheduler_service.schedule_repo.get_user_schedules(current_user.id, include_inactive=True, limit=limit, offset=offset)
    total = scheduler_service.schedule_repo.count_user_schedules(current_user.id, include_inactive=True)
    total_pages = (total + limit - 1) // limit if total else 1
    return render_template('schedules/list.html', schedules=schedules, page=page, total_pages=total_pages, total=total)


@schedule_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        platform = request.form.get('platform', 'youtube')
        source_input = request.form.get('source_input', '').strip()
        frequency = request.form.get('frequency', 'once')
        comment_limit = request.form.get('comment_limit', 100, type=int)
        if comment_limit not in (100, 500, 1000):
            comment_limit = 100

        if not source_input:
            flash('Source input is required.', 'danger')
            return render_template('schedules/create.html')

        schedule = scheduler_service.create_schedule(
            current_user.id, platform, source_input, frequency, comment_limit,
        )
        flash(f'Scheduled {frequency} analysis created!', 'success')
        return redirect(url_for('schedules.list_schedules'))

    return render_template('schedules/create.html')


@schedule_bp.route('/<int:schedule_id>')
@login_required
def detail(schedule_id):
    schedule = scheduler_service.schedule_repo.get_by_id(schedule_id)
    if not schedule or schedule.user_id != current_user.id:
        flash('Schedule not found.', 'danger')
        return redirect(url_for('schedules.list_schedules'))
    return render_template('schedules/detail.html', schedule=schedule)


@schedule_bp.route('/<int:schedule_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(schedule_id):
    schedule = scheduler_service.schedule_repo.get_by_id(schedule_id)
    if not schedule or schedule.user_id != current_user.id:
        flash('Schedule not found.', 'danger')
        return redirect(url_for('schedules.list_schedules'))

    if request.method == 'POST':
        schedule.source_input = request.form.get('source_input', schedule.source_input)
        schedule.frequency = request.form.get('frequency', schedule.frequency)
        schedule.comment_limit = request.form.get('comment_limit', schedule.comment_limit, type=int)
        from database import db
        db.session.commit()
        flash('Schedule updated!', 'success')
        return redirect(url_for('schedules.list_schedules'))

    return render_template('schedules/edit.html', schedule=schedule)


@schedule_bp.route('/<int:schedule_id>/pause', methods=['POST'])
@login_required
def pause(schedule_id):
    result = scheduler_service.pause_schedule(schedule_id, current_user.id)
    if not result:
        flash('Schedule not found.', 'danger')
    else:
        flash('Schedule paused.', 'info')
    return redirect(url_for('schedules.list_schedules'))


@schedule_bp.route('/<int:schedule_id>/resume', methods=['POST'])
@login_required
def resume(schedule_id):
    result = scheduler_service.resume_schedule(schedule_id, current_user.id)
    if not result:
        flash('Schedule not found.', 'danger')
    else:
        flash('Schedule resumed.', 'success')
    return redirect(url_for('schedules.list_schedules'))


@schedule_bp.route('/<int:schedule_id>/delete', methods=['POST'])
@login_required
def delete(schedule_id):
    result = scheduler_service.delete_schedule(schedule_id, current_user.id)
    if not result:
        flash('Schedule not found.', 'danger')
    else:
        flash('Schedule deleted.', 'info')
    return redirect(url_for('schedules.list_schedules'))
