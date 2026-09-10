from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from database import db


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_column():
    """PostgreSQL-first JSON storage.

    Compiles to ``JSONB`` on PostgreSQL (the production database) and to
    ``JSON`` everywhere else, so the SQLite-backed test suite keeps working.
    """
    return db.JSON().with_variant(JSONB(), 'postgresql')


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


class Narrative(db.Model):
    """A recurring theme/claim detected across a user's analyzed content.

    Narratives are detected **heuristically** (lexical + entity + temporal
    signals). ``detection_method`` records how a given narrative was derived so
    that no output can be mistaken for a trained ML classification.
    """

    __tablename__ = 'narratives'

    CATEGORY_UNKNOWN = 'unknown'
    CATEGORY_GENERAL = 'general'
    CATEGORY_POLITICAL = 'political'
    CATEGORY_HEALTH = 'health'
    CATEGORY_FINANCIAL = 'financial'
    CATEGORY_TECHNOLOGY = 'technology'
    CATEGORY_SOCIAL = 'social'
    CATEGORY_CRISIS = 'crisis'
    CATEGORY_PROMOTIONAL = 'promotional'
    CATEGORY_CONFLICT = 'conflict'

    CATEGORIES = (
        CATEGORY_UNKNOWN,
        CATEGORY_GENERAL,
        CATEGORY_POLITICAL,
        CATEGORY_HEALTH,
        CATEGORY_FINANCIAL,
        CATEGORY_TECHNOLOGY,
        CATEGORY_SOCIAL,
        CATEGORY_CRISIS,
        CATEGORY_PROMOTIONAL,
        CATEGORY_CONFLICT,
    )

    METHOD_HEURISTIC = 'heuristic'
    METHOD_RULE_BASED = 'rule_based'
    METHOD_TRANSCRIPT_BASED = 'transcript_based'
    METHOD_METADATA_BASED = 'metadata_based'
    METHOD_FALLBACK = 'fallback'
    METHOD_UNAVAILABLE = 'unavailable'

    RISK_LOW = 'Low'
    RISK_MEDIUM = 'Medium'
    RISK_HIGH = 'High'
    RISK_CRITICAL = 'Critical'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)

    name = db.Column(db.String(300), nullable=False)
    normalized_name = db.Column(db.String(300), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(40), nullable=False, default=CATEGORY_UNKNOWN)

    risk_score = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    growth_score = db.Column(db.Float, nullable=False, default=0.0)

    occurrence_count = db.Column(db.Integer, nullable=False, default=0)
    platform_count = db.Column(db.Integer, nullable=False, default=0)

    keywords = db.Column(_json_column(), nullable=True)
    entity_names = db.Column(_json_column(), nullable=True)
    evidence = db.Column(_json_column(), nullable=True)

    detection_method = db.Column(db.String(40), nullable=False, default=METHOD_HEURISTIC)

    first_seen_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    user = db.relationship(
        'User',
        backref=db.backref('narratives', lazy='dynamic', cascade='all, delete-orphan'),
    )
    occurrences = db.relationship(
        'NarrativeOccurrence',
        backref='narrative',
        lazy='dynamic',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        db.UniqueConstraint('user_id', 'normalized_name', name='uq_narratives_user_normalized'),
        db.Index('ix_narratives_user_last_seen', 'user_id', 'last_seen_at'),
        db.Index('ix_narratives_user_risk', 'user_id', 'risk_score'),
    )

    @property
    def risk_level(self):
        score = self.risk_score or 0.0
        if score > 75:
            return self.RISK_CRITICAL
        if score > 50:
            return self.RISK_HIGH
        if score > 25:
            return self.RISK_MEDIUM
        return self.RISK_LOW

    @property
    def is_cross_platform(self):
        return (self.platform_count or 0) > 1

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'normalized_name': self.normalized_name,
            'description': self.description,
            'category': self.category,
            'risk_score': self.risk_score,
            'risk_level': self.risk_level,
            'confidence': self.confidence,
            'growth_score': self.growth_score,
            'occurrence_count': self.occurrence_count,
            'platform_count': self.platform_count,
            'is_cross_platform': self.is_cross_platform,
            'keywords': _as_list(self.keywords),
            'entity_names': _as_list(self.entity_names),
            'evidence': _as_dict(self.evidence),
            'detection_method': self.detection_method,
            'first_seen_at': self.first_seen_at.isoformat() if self.first_seen_at else None,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<Narrative {self.normalized_name} ({self.category}) risk={self.risk_score}>'
