from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from database import db


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_column():
    """JSONB on PostgreSQL, JSON elsewhere (SQLite test suite)."""
    return db.JSON().with_variant(JSONB(), 'postgresql')


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


class CoordinationSignal(db.Model):
    """A single **potential** coordinated-behaviour signal for one analysis.

    These are heuristic signals, never verdicts. ``level`` deliberately uses
    hedged vocabulary (``elevated`` / ``suspicious``) and the service layer
    phrases every reason as a possibility.

    ``comparisons_performed`` / ``comparisons_truncated`` exist so the UI and
    exports can state honestly how much of the candidate space was examined
    before the configured comparison budget was exhausted.
    """

    __tablename__ = 'coordination_signals'

    # Canonical Phase D signal vocabulary (emitted by
    # CoordinationIntelligenceService). Kept as first-class constants so the
    # analysis pipeline, result page and exports all agree on the strings.
    TYPE_REPEATED_CONTENT = 'repeated_content'
    TYPE_SYNCHRONIZED_TIMING = 'synchronized_timing'
    TYPE_SHARED_ENTITIES = 'shared_entities'
    TYPE_SHARED_NARRATIVE = 'shared_narrative'
    TYPE_ABNORMAL_SIMILARITY = 'abnormal_similarity'
    TYPE_CROSS_PLATFORM_COORDINATION = 'cross_platform_coordination'
    TYPE_BEHAVIORAL_CLUSTER = 'behavioral_cluster'

    SIGNAL_TYPES = (
        TYPE_REPEATED_CONTENT,
        TYPE_SYNCHRONIZED_TIMING,
        TYPE_SHARED_ENTITIES,
        TYPE_SHARED_NARRATIVE,
        TYPE_ABNORMAL_SIMILARITY,
        TYPE_CROSS_PLATFORM_COORDINATION,
        TYPE_BEHAVIORAL_CLUSTER,
    )

    # Retained for backwards compatibility with any calibration payload or
    # earlier V12 intermediate state; NOT emitted by the Phase D service.
    TYPE_DUPLICATE_TEXT = 'duplicate_text'
    TYPE_NEAR_DUPLICATE_TEXT = 'near_duplicate_text'
    TYPE_TEMPORAL_SYNCHRONIZATION = 'temporal_synchronization'
    TYPE_ENTITY_SYNCHRONIZATION = 'entity_synchronization'
    TYPE_LINK_SYNCHRONIZATION = 'link_synchronization'
    TYPE_BEHAVIORAL_AMPLIFICATION = 'behavioral_amplification'
    TYPE_CROSS_PLATFORM_REPETITION = 'cross_platform_repetition'

    LEVEL_NONE = 'none'
    LEVEL_LOW = 'low'
    LEVEL_ELEVATED = 'elevated'
    LEVEL_SUSPICIOUS = 'suspicious'

    LEVELS = (LEVEL_NONE, LEVEL_LOW, LEVEL_ELEVATED, LEVEL_SUSPICIOUS)

    METHOD_HEURISTIC = 'heuristic'
    METHOD_RULE_BASED = 'rule_based'
    METHOD_FALLBACK = 'fallback'
    METHOD_UNAVAILABLE = 'unavailable'

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    narrative_id = db.Column(db.Integer, db.ForeignKey('narratives.id', ondelete='SET NULL'),
                             nullable=True, index=True)

    signal_type = db.Column(db.String(40), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    level = db.Column(db.String(20), nullable=False, default=LEVEL_NONE)

    cluster_size = db.Column(db.Integer, nullable=False, default=0)
    comparisons_performed = db.Column(db.Integer, nullable=False, default=0)
    comparisons_truncated = db.Column(db.Boolean, nullable=False, default=False)
    window_seconds = db.Column(db.Integer, nullable=True)

    summary = db.Column(db.Text, nullable=True)
    reasons = db.Column(_json_column(), nullable=True)
    indicators = db.Column(_json_column(), nullable=True)
    evidence = db.Column(_json_column(), nullable=True)
    related_entities = db.Column(_json_column(), nullable=True)

    detection_method = db.Column(db.String(40), nullable=False, default=METHOD_HEURISTIC)

    first_event_at = db.Column(db.DateTime, nullable=True)
    last_event_at = db.Column(db.DateTime, nullable=True)
    detected_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    analysis = db.relationship(
        'Analysis',
        backref=db.backref('coordination_signals', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )
    user = db.relationship(
        'User',
        backref=db.backref('coordination_signals', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )
    narrative = db.relationship('Narrative', backref=db.backref('coordination_signals',
                                                                lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('analysis_id', 'signal_type',
                            name='uq_coordination_signals_analysis_type'),
        db.Index('ix_coordination_signals_analysis_type', 'analysis_id', 'signal_type'),
        db.Index('ix_coordination_signals_user_detected', 'user_id', 'detected_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'analysis_id': self.analysis_id,
            'narrative_id': self.narrative_id,
            'signal_type': self.signal_type,
            'score': self.score,
            'confidence': self.confidence,
            'level': self.level,
            'cluster_size': self.cluster_size,
            'comparisons_performed': self.comparisons_performed,
            'comparisons_truncated': self.comparisons_truncated,
            'window_seconds': self.window_seconds,
            'summary': self.summary,
            'reasons': _as_list(self.reasons),
            'indicators': _as_list(self.indicators),
            'evidence': _as_dict(self.evidence),
            'related_entities': _as_list(self.related_entities),
            'detection_method': self.detection_method,
            'first_event_at': self.first_event_at.isoformat() if self.first_event_at else None,
            'last_event_at': self.last_event_at.isoformat() if self.last_event_at else None,
            'detected_at': self.detected_at.isoformat() if self.detected_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return (f'<CoordinationSignal {self.signal_type} analysis={self.analysis_id} '
                f'score={self.score} level={self.level}>')
