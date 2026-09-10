from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from services.analysis_service import AnalysisService
from services.job_service import JobService
from services.database_health_service import database_health_service
from services.redis_service import redis_service
from services.threat_assessment_service import ThreatAssessmentService
from services.narrative_intelligence_service import NarrativeIntelligenceService
from services.coordination_intelligence_service import CoordinationIntelligenceService
from services.propagation_intelligence_service import PropagationIntelligenceService
from services.temporal_intelligence_service import TemporalIntelligenceService

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
analysis_service = AnalysisService()
job_service = JobService()
threat_service = ThreatAssessmentService()
narrative_service = NarrativeIntelligenceService()
coordination_service = CoordinationIntelligenceService()
propagation_service = PropagationIntelligenceService()
temporal_service = TemporalIntelligenceService()


def _build_v12_context(user_id, recent, threat_limit=3, narrative_limit=5,
                       propagation_limit=5, temporal_limit=5, coord_limit=6):
    """Bounded read-only V12 dashboard context.

    All data comes from existing read APIs; no intelligence is recomputed here.
    Every section is wrapped so a disabled flag or empty table degrades to an
    "Unavailable / Insufficient evidence" state instead of crashing the page.
    """
    v12 = {}

    try:
        v12['threat'] = threat_service.get_user_threat_summary(user_id, limit=threat_limit)
    except Exception:
        v12['threat'] = {
            'total_assessments': 0, 'level_distribution': {},
            'recent_assessments': [], 'capability': 'heuristic',
            'detection_method': None,
        }

    try:
        v12['narrative'] = narrative_service.get_user_narrative_summary(
            user_id, limit=narrative_limit)
    except Exception:
        v12['narrative'] = {
            'total_narratives': 0, 'top_narratives': [],
            'category_distribution': [], 'platform_distribution': {},
            'cross_platform_narratives': [], 'capability': 'heuristic',
            'detection_method': None,
        }

    try:
        coord_signals = []
        for a in (recent or []):
            signals = coordination_service.get_analysis_coordination(a['id'])
            coord_signals.extend(signals)
            if len(coord_signals) >= coord_limit:
                break
        v12['coordination'] = coord_signals[:coord_limit]
    except Exception:
        v12['coordination'] = []

    try:
        v12['propagation'] = propagation_service.get_user_propagation_summary(
            user_id, limit=propagation_limit)
    except Exception:
        v12['propagation'] = {
            'total_events': 0, 'cross_platform_events': 0,
            'cross_platform_narratives': 0, 'top_events': [],
            'capability': 'heuristic', 'detection_method': None,
        }

    try:
        v12['temporal'] = temporal_service.get_temporal_summary(
            user_id, limit=temporal_limit)
    except Exception:
        v12['temporal'] = {
            'narrative_count': 0, 'first_seen_any': None,
            'last_seen_any': None, 'narratives': [],
        }

    return v12


@dashboard_bp.route('/')
@login_required
def index():
    platform = request.args.get('platform', 'all')
    if platform not in ('all', 'youtube', 'reddit'):
        platform = 'all'
    stats = analysis_service.get_dashboard_stats(current_user.id, platform=platform)
    recent = analysis_service.get_all_user_analyses_with_data(current_user.id, limit=10)
    job_stats = job_service.get_dashboard_metrics(current_user.id)

    v12 = _build_v12_context(current_user.id, recent)

    db_ok = database_health_service.check_connectivity()
    redis_ok = redis_service.check_connection()
    pool_usage = database_health_service.get_pool_usage()

    celery_ok = False
    celery_active = 0
    try:
        from celery_app import celery_app
        inspector = celery_app.control.inspect()
        active = inspector.active() or {}
        celery_active = sum(len(tasks) for tasks in active.values())
        celery_ok = True
    except Exception:
        pass

    infra = {
        'database': 'connected' if db_ok else 'error',
        'redis': 'connected' if redis_ok else 'disconnected',
        'celery': 'connected' if celery_ok else 'disconnected',
        'pool_checkedout': pool_usage.get('checkedout', 0),
        'pool_size': pool_usage.get('size', 0),
        'active_tasks': celery_active,
    }

    return render_template('dashboard/dashboard.html', stats=stats, analyses=recent,
                           current_platform=platform, job_stats=job_stats, infra=infra,
                           v12=v12)
