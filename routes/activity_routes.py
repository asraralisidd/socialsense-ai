from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from services.activity_service import ActivityService

activity_bp = Blueprint('activity', __name__, url_prefix='/activity')
activity_service = ActivityService()


@activity_bp.route('')
@activity_bp.route('/')
@login_required
def timeline():
    page = request.args.get('page', 1, type=int)
    if not page or page < 1:
        page = 1
    limit = 20
    offset = (page - 1) * limit
    logs = activity_service.get_user_activity(current_user.id, limit=limit, offset=offset)
    total = activity_service.count_user_activity(current_user.id)
    total_pages = (total + limit - 1) // limit if total else 1
    return render_template('activity/timeline.html', logs=logs, page=page, total_pages=total_pages, total=total)


@activity_bp.route('/data')
@login_required
def data():
    page = request.args.get('page', 1, type=int)
    if not page or page < 1:
        page = 1
    limit = request.args.get('limit', 50, type=int)
    if not limit or limit < 1 or limit > 100:
        limit = 50
    offset = (page - 1) * limit
    logs = activity_service.get_user_activity(current_user.id, limit=limit, offset=offset)
    return jsonify([{
        'id': l.id, 'action': l.action, 'description': l.description,
        'resource_type': l.resource_type, 'resource_id': l.resource_id,
        'created_at': l.created_at.isoformat() if l.created_at else None,
    } for l in logs])
