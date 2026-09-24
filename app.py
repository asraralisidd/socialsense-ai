import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template
from dotenv import load_dotenv

load_dotenv()


def setup_logging(app):
    log_level = logging.DEBUG if app.debug else logging.INFO

    handler = RotatingFileHandler(
        os.path.join(app.root_path, 'logs', 'socialsense.log'),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setLevel(log_level)
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )
    handler.setFormatter(formatter)

    sqlalchemy_logger = logging.getLogger('sqlalchemy.engine')
    sqlalchemy_logger.setLevel(logging.WARNING)

    celery_logger = logging.getLogger('celery')
    celery_logger.setLevel(log_level)
    celery_logger.addHandler(handler)

    redis_logger = logging.getLogger('redis')
    redis_logger.setLevel(logging.WARNING)

    app.logger.addHandler(handler)
    app.logger.setLevel(log_level)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    return handler


def create_app(config_name=None):
    app = Flask(__name__)

    if config_name == 'testing':
        app.config.from_object('config.TestingConfig')
    elif os.environ.get('FLASK_ENV') == 'production':
        app.config.from_object('config.ProductionConfig')
        if not os.environ.get('SECRET_KEY'):
            raise RuntimeError(
                'SECRET_KEY environment variable is required in production.')
    else:
        app.config.from_object('config.DevelopmentConfig')

    app.config['YOUTUBE_API_KEY'] = os.environ.get('YOUTUBE_API_KEY', '')
    app.config['REDDIT_CLIENT_ID'] = os.environ.get('REDDIT_CLIENT_ID', '')
    app.config['REDDIT_CLIENT_SECRET'] = os.environ.get('REDDIT_CLIENT_SECRET', '')
    app.config['REDDIT_USER_AGENT'] = os.environ.get('REDDIT_USER_AGENT', 'SocialSenseAI/1.0')
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'reports')
    app.config['MAX_CONCURRENT_JOBS'] = int(os.environ.get('MAX_CONCURRENT_JOBS', '4'))
    app.config['MAX_JOBS_PER_USER'] = int(os.environ.get('MAX_JOBS_PER_USER', '20'))
    app.config['MAX_JOB_RUNTIME'] = int(os.environ.get('MAX_JOB_RUNTIME', '600'))
    app.config['JOB_HISTORY_RETENTION_DAYS'] = int(os.environ.get('JOB_HISTORY_RETENTION_DAYS', '30'))
    app.config['MAX_JOB_RETRIES'] = int(os.environ.get('MAX_JOB_RETRIES', '3'))

    if not app.config.get('TESTING'):
        os.makedirs(os.path.join(app.root_path, 'logs'), exist_ok=True)
        setup_logging(app)

    from database import init_db
    init_db(app)

    from flask_login import LoginManager
    from models.user import User

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect(app)

    from routes import auth_bp, dashboard_bp, analysis_bp, export_bp, job_bp, \
        schedule_bp, notification_bp, activity_bp, report_bp, trend_bp, admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(job_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(trend_bp)
    app.register_blueprint(admin_bp)

    from routes.health_routes import health_bp
    app.register_blueprint(health_bp)

    use_celery = app.config.get('USE_CELERY', False)

    from services.background_worker import BackgroundWorker
    worker = BackgroundWorker(use_celery=use_celery)
    if not use_celery:
        worker.recover_stuck_jobs(app)
    app.config['_worker'] = worker

    if use_celery:
        try:
            from services.redis_service import redis_service
            redis_service.check_connection()
            app.logger.info('Redis connection established')
        except Exception as e:
            app.logger.warning(f'Redis connection failed: {e}')

    from services.redis_service import redis_service
    app.config['_redis_service'] = redis_service

    @app.context_processor
    def inject_globals():
        is_demo = not app.config.get('YOUTUBE_API_KEY', '')
        has_reddit = bool(app.config.get('REDDIT_CLIENT_ID', '') and app.config.get('REDDIT_CLIENT_SECRET', ''))
        unread_count = 0
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                from services.notification_service import NotificationService
                unread_count = NotificationService().get_unread_count(current_user.id)
        except Exception:
            pass
        return {'is_demo_mode': is_demo, 'has_reddit_creds': has_reddit, 'nav_job_bp': job_bp, 'unread_notifications': unread_count}

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
