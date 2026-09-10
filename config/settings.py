import os
from dotenv import load_dotenv
load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///socialsense.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': int(os.environ.get('DATABASE_POOL_SIZE', '10')),
        'max_overflow': int(os.environ.get('DATABASE_MAX_OVERFLOW', '20')),
        'pool_timeout': int(os.environ.get('DATABASE_POOL_TIMEOUT', '30')),
        'pool_recycle': int(os.environ.get('DATABASE_POOL_RECYCLE', '1800')),
        'pool_pre_ping': True,
    }
    SQLALCHEMY_ECHO = os.environ.get('DATABASE_ECHO', 'false').lower() == 'true'

    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = 86400 * 30

    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
    CELERY_TASK_TRACK_STARTED = True
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_WORKER_CONCURRENCY = int(os.environ.get('CELERY_WORKER_CONCURRENCY', '4'))
    CELERY_TASK_ACKS_LATE = True
    CELERY_WORKER_PREFETCH_MULTIPLIER = 1
    CELERY_TASK_SOFT_TIME_LIMIT = int(os.environ.get('MAX_JOB_RUNTIME', '600'))
    CELERY_TASK_TIME_LIMIT = int(os.environ.get('MAX_JOB_RUNTIME', '600')) + 60

    YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
    REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID', '')
    REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET', '')
    REDDIT_USER_AGENT = os.environ.get('REDDIT_USER_AGENT', 'SocialSenseAI/1.0')

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    MAX_CONCURRENT_JOBS = int(os.environ.get('MAX_CONCURRENT_JOBS', '4'))
    MAX_JOBS_PER_USER = int(os.environ.get('MAX_JOBS_PER_USER', '20'))
    MAX_JOB_RUNTIME = int(os.environ.get('MAX_JOB_RUNTIME', '600'))
    JOB_HISTORY_RETENTION_DAYS = int(os.environ.get('JOB_HISTORY_RETENTION_DAYS', '30'))
    MAX_JOB_RETRIES = int(os.environ.get('MAX_JOB_RETRIES', '3'))
    JOB_LOG_RETENTION_DAYS = int(os.environ.get('JOB_LOG_RETENTION_DAYS', '30'))
    NOTIFICATION_RETENTION_DAYS = int(os.environ.get('NOTIFICATION_RETENTION_DAYS', '30'))
    REPORT_RETENTION_DAYS = int(os.environ.get('REPORT_RETENTION_DAYS', '30'))
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports')

    ENABLE_TRANSCRIPT_ANALYSIS = os.environ.get('ENABLE_TRANSCRIPT_ANALYSIS', 'true').lower() == 'true'
    TRANSCRIPT_LANGUAGE_PRIORITY = os.environ.get('TRANSCRIPT_LANGUAGE_PRIORITY', 'en')
    TRANSCRIPT_CACHE_ENABLED = os.environ.get('TRANSCRIPT_CACHE_ENABLED', 'true').lower() == 'true'
    TRANSCRIPT_RETENTION_DAYS = int(os.environ.get('TRANSCRIPT_RETENTION_DAYS', '90'))
    ENABLE_TRANSCRIPT_FALLBACK_DEMO = os.environ.get('ENABLE_TRANSCRIPT_FALLBACK_DEMO', 'true').lower() == 'true'

    ENABLE_ENTITY_ANALYSIS = os.environ.get('ENABLE_ENTITY_ANALYSIS', 'true').lower() == 'true'
    ENABLE_ENTITY_SENTIMENT = os.environ.get('ENABLE_ENTITY_SENTIMENT', 'true').lower() == 'true'
    ENABLE_ENTITY_RISK = os.environ.get('ENABLE_ENTITY_RISK', 'true').lower() == 'true'
    MAX_ENTITIES_PER_ANALYSIS = int(os.environ.get('MAX_ENTITIES_PER_ANALYSIS', '100'))
    ENTITY_MIN_IMPORTANCE = int(os.environ.get('ENTITY_MIN_IMPORTANCE', '5'))

    ENABLE_CHANNEL_INTELLIGENCE = os.environ.get('ENABLE_CHANNEL_INTELLIGENCE', 'true').lower() == 'true'
    ENABLE_HISTORICAL_CONTEXT = os.environ.get('ENABLE_HISTORICAL_CONTEXT', 'true').lower() == 'true'
    MAX_HISTORY_VIDEOS = int(os.environ.get('MAX_HISTORY_VIDEOS', '100'))
    MAX_ENTITY_HISTORY = int(os.environ.get('MAX_ENTITY_HISTORY', '500'))

    ENABLE_MEDIA_ANALYSIS = os.environ.get('ENABLE_MEDIA_ANALYSIS', 'true').lower() == 'true'
    ENABLE_THUMBNAIL_ANALYSIS = os.environ.get('ENABLE_THUMBNAIL_ANALYSIS', 'true').lower() == 'true'
    ENABLE_AUDIO_ANALYSIS = os.environ.get('ENABLE_AUDIO_ANALYSIS', 'true').lower() == 'true'
    ENABLE_FRAME_ANALYSIS = os.environ.get('ENABLE_FRAME_ANALYSIS', 'true').lower() == 'true'
    ENABLE_METADATA_ANALYSIS = os.environ.get('ENABLE_METADATA_ANALYSIS', 'true').lower() == 'true'
    ENABLE_AUTHENTICITY_ENGINE = os.environ.get('ENABLE_AUTHENTICITY_ENGINE', 'true').lower() == 'true'
    MAX_VIDEO_FRAMES = int(os.environ.get('MAX_VIDEO_FRAMES', '30'))

    # V12 Narrative Intelligence (Phase C)
    ENABLE_NARRATIVE_INTELLIGENCE = os.environ.get('ENABLE_NARRATIVE_INTELLIGENCE', 'true').lower() == 'true'
    MAX_NARRATIVES_PER_ANALYSIS = int(os.environ.get('MAX_NARRATIVES_PER_ANALYSIS', '12'))
    NARRATIVE_MAX_PHRASES_PER_DOCUMENT = int(os.environ.get('NARRATIVE_MAX_PHRASES_PER_DOCUMENT', '20'))
    NARRATIVE_MAX_COMMENTS_SCANNED = int(os.environ.get('NARRATIVE_MAX_COMMENTS_SCANNED', '300'))
    NARRATIVE_MIN_DOCUMENT_FREQUENCY = int(os.environ.get('NARRATIVE_MIN_DOCUMENT_FREQUENCY', '2'))
    NARRATIVE_MERGE_THRESHOLD = float(os.environ.get('NARRATIVE_MERGE_THRESHOLD', '0.85'))
    NARRATIVE_REUSE_THRESHOLD = float(os.environ.get('NARRATIVE_REUSE_THRESHOLD', '0.90'))
    NARRATIVE_MAX_REUSE_CANDIDATES = int(os.environ.get('NARRATIVE_MAX_REUSE_CANDIDATES', '200'))
    NARRATIVE_MAX_RISK_CONTEXT_DOCS = int(os.environ.get('NARRATIVE_MAX_RISK_CONTEXT_DOCS', '20'))
    NARRATIVE_MAX_EVIDENCE_SAMPLES = int(os.environ.get('NARRATIVE_MAX_EVIDENCE_SAMPLES', '3'))
    NARRATIVE_MAX_EVIDENCE_TERMS = int(os.environ.get('NARRATIVE_MAX_EVIDENCE_TERMS', '8'))
    NARRATIVE_MAX_EVIDENCE_ENTITIES = int(os.environ.get('NARRATIVE_MAX_EVIDENCE_ENTITIES', '5'))
    NARRATIVE_EVIDENCE_SNIPPET_CHARS = int(os.environ.get('NARRATIVE_EVIDENCE_SNIPPET_CHARS', '160'))

    # V12 Coordination Detection (Phase D)
    ENABLE_COORDINATION_DETECTION = os.environ.get('ENABLE_COORDINATION_DETECTION', 'true').lower() == 'true'
    COORDINATION_MAX_COMMENTS_SCANNED = int(os.environ.get('COORDINATION_MAX_COMMENTS_SCANNED', '300'))
    COORDINATION_COMPARISON_BUDGET = int(os.environ.get('COORDINATION_COMPARISON_BUDGET', '2000'))
    COORDINATION_MAX_LEADERS = int(os.environ.get('COORDINATION_MAX_LEADERS', '50'))
    COORDINATION_TIMING_WINDOW_SECONDS = int(os.environ.get('COORDINATION_TIMING_WINDOW_SECONDS', '300'))
    COORDINATION_TIMING_PAIR_BUDGET = int(os.environ.get('COORDINATION_TIMING_PAIR_BUDGET', '2000'))
    COORDINATION_MAX_EVIDENCE_SAMPLES = int(os.environ.get('COORDINATION_MAX_EVIDENCE_SAMPLES', '3'))
    COORDINATION_MAX_EVIDENCE_ENTITIES = int(os.environ.get('COORDINATION_MAX_EVIDENCE_ENTITIES', '5'))
    COORDINATION_EVIDENCE_SNIPPET_CHARS = int(os.environ.get('COORDINATION_EVIDENCE_SNIPPET_CHARS', '120'))

    # V12 Propagation Intelligence (Phase E)
    ENABLE_PROPAGATION_INTELLIGENCE = os.environ.get('ENABLE_PROPAGATION_INTELLIGENCE', 'true').lower() == 'true'
    PROPAGATION_MAX_CANDIDATES = int(os.environ.get('PROPAGATION_MAX_CANDIDATES', '15'))
    PROPAGATION_MAX_COMPARISONS = int(os.environ.get('PROPAGATION_MAX_COMPARISONS', '40'))
    PROPAGATION_MAX_EVENTS = int(os.environ.get('PROPAGATION_MAX_EVENTS', '25'))
    PROPAGATION_MAX_EVIDENCE_ENTITIES = int(os.environ.get('PROPAGATION_MAX_EVIDENCE_ENTITIES', '5'))

    # V12 Temporal Intelligence (Phase F)
    ENABLE_TEMPORAL_INTELLIGENCE = os.environ.get('ENABLE_TEMPORAL_INTELLIGENCE', 'true').lower() == 'true'
    TEMPORAL_MAX_NARRATIVES = int(os.environ.get('TEMPORAL_MAX_NARRATIVES', '50'))
    TEMPORAL_MAX_OCCURRENCES = int(os.environ.get('TEMPORAL_MAX_OCCURRENCES', '200'))

    # V12 Threat Assessment (Phase G)
    ENABLE_THREAT_ASSESSMENT = os.environ.get('ENABLE_THREAT_ASSESSMENT', 'true').lower() == 'true'
    THREAT_MAX_INDICATORS = int(os.environ.get('THREAT_MAX_INDICATORS', '20'))
    THREAT_MAX_REASONS = int(os.environ.get('THREAT_MAX_REASONS', '20'))
    THREAT_MAX_LIMITATIONS = int(os.environ.get('THREAT_MAX_LIMITATIONS', '10'))

    USE_CELERY = os.environ.get('USE_CELERY', 'false').lower() == 'true'


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    USE_CELERY = False
