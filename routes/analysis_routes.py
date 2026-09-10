from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from services.analysis_service import AnalysisService
from services.reddit_service import RedditService
from services.job_service import JobService
from services.v12_context_service import build_v12_context

analysis_bp = Blueprint('analysis', __name__, url_prefix='/analysis')
analysis_service = AnalysisService()
reddit_service = RedditService()
job_service = JobService()


def _build_v12_result_context(analysis_id, user_id, narrative_limit=5,
                              temporal_limit=5):
    """Bounded read-only V12 context for the analysis result page.

    Delegates to the shared context builder so the result page and every
    export format consume the exact same data contract.
    """
    return build_v12_context(analysis_id, user_id,
                             narrative_limit=narrative_limit,
                             temporal_limit=temporal_limit)


@analysis_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if request.method == 'POST':
        platform = request.form.get('platform', 'youtube')
        comment_limit = request.form.get('comment_limit', '100', type=int)
        if comment_limit not in (100, 500, 1000):
            comment_limit = 100

        if platform == 'reddit':
            source_input = request.form.get('reddit_input', '').strip()
            if not source_input:
                flash('Please enter a Reddit post URL, post ID, or subreddit name.', 'danger')
                return render_template('analysis/new.html')
        else:
            source_input = request.form.get('video_url', '').strip()
            if not source_input:
                flash('Please enter a YouTube URL or Video ID.', 'danger')
                return render_template('analysis/new.html')

        request_hash = job_service.compute_request_hash(current_user.id, platform, source_input)
        result = job_service.create_job(current_user.id, platform, source_input, comment_limit, request_hash)

        if not result['success']:
            flash(result['error'], 'danger')
            return render_template('analysis/new.html')

        if result.get('duplicate'):
            existing = job_service.get_job(result['job_id'], current_user.id)
            if existing and existing.status == 'COMPLETED' and existing.result_analysis_id:
                return redirect(url_for('analysis.result', analysis_id=existing.result_analysis_id))
            flash('Analysis already queued. Redirecting to job status.', 'info')
            return redirect(url_for('jobs.status', job_id=result['job_id']))

        flash('Analysis queued! You can monitor progress on the job status page.', 'info')
        return redirect(url_for('jobs.status', job_id=result['job_id']))

    from flask import current_app
    yt_demo = not current_app.config.get('YOUTUBE_API_KEY', '')
    has_reddit = bool(current_app.config.get('REDDIT_CLIENT_ID', '') and current_app.config.get('REDDIT_CLIENT_SECRET', ''))
    return render_template('analysis/new.html', is_demo=yt_demo, has_reddit_creds=has_reddit)


@analysis_bp.route('/<int:analysis_id>')
@login_required
def result(analysis_id):
    data = analysis_service.get_analysis_results(analysis_id, current_user.id)
    if not data:
        flash('Analysis not found.', 'danger')
        return redirect(url_for('dashboard.index'))

    v12 = _build_v12_result_context(analysis_id, current_user.id)
    return render_template('analysis/result.html', data=data, v12=v12)


@analysis_bp.route('/history')
@login_required
def history():
    analyses = analysis_service.get_all_user_analyses_with_data(current_user.id)
    return render_template('analysis/history.html', analyses=analyses)


@analysis_bp.route('/api/check-demo')
def check_demo():
    from flask import current_app
    is_demo = not current_app.config.get('YOUTUBE_API_KEY', '')
    return jsonify({'is_demo': is_demo})
