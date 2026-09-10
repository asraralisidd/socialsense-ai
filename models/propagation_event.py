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


class PropagationEvent(db.Model):
    """An observed relationship between two analyzed items.

    Relationship vocabulary is intentionally non-causal. ``related`` /
    ``similar`` / ``temporally_correlated`` / ``shared_entity`` assert only
    observation; ``potentially_propagated`` is the strongest label V12 will ever
    emit and still hedges. Nothing here claims causality.

    Two foreign keys point at ``analyses``, so both relationships declare
    ``foreign_keys`` explicitly to keep the join unambiguous.
    """

    __tablename__ = 'propagation_events'

    RELATION_RELATED = 'related'
    RELATION_SIMILAR = 'similar'
    RELATION_TEMPORALLY_CORRELATED = 'temporally_correlated'
    RELATION_SHARED_ENTITY = 'shared_entity'
    RELATION_POTENTIALLY_PROPAGATED = 'potentially_propagated'

    RELATIONSHIP_TYPES = (
        RELATION_RELATED,
        RELATION_SIMILAR,
        RELATION_TEMPORALLY_CORRELATED,
        RELATION_SHARED_ENTITY,
        RELATION_POTENTIALLY_PROPAGATED,
    )

    PLATFORM_YOUTUBE = 'youtube'
    PLATFORM_REDDIT = 'reddit'
    PLATFORM_UNKNOWN = 'unknown'

    DIRECTION_SOURCE_TO_TARGET = 'source_to_target'
    DIRECTION_UNDIRECTED = 'undirected'

    METHOD_HEURISTIC = 'heuristic'
    METHOD_RULE_BASED = 'rule_based'
    METHOD_FALLBACK = 'fallback'
    METHOD_UNAVAILABLE = 'unavailable'

    id = db.Column(db.Integer, primary_key=True)
    narrative_id = db.Column(db.Integer, db.ForeignKey('narratives.id', ondelete='CASCADE'),
                             nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    source_analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id', ondelete='CASCADE'),
                                   nullable=False, index=True)
    target_analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id', ondelete='CASCADE'),
                                   nullable=False, index=True)

    source_platform = db.Column(db.String(20), nullable=False, default=PLATFORM_UNKNOWN)
    target_platform = db.Column(db.String(20), nullable=False, default=PLATFORM_UNKNOWN)
    source_ref = db.Column(db.String(200), nullable=True)
    target_ref = db.Column(db.String(200), nullable=True)

    relationship_type = db.Column(db.String(40), nullable=False,
                                  default=RELATION_RELATED, index=True)
    direction = db.Column(db.String(20), nullable=False, default=DIRECTION_UNDIRECTED)

    propagation_score = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    similarity_score = db.Column(db.Float, nullable=False, default=0.0)
    lag_seconds = db.Column(db.Float, nullable=True)

    shared_entities = db.Column(_json_column(), nullable=True)
    reasons = db.Column(_json_column(), nullable=True)
    evidence = db.Column(_json_column(), nullable=True)

    detection_method = db.Column(db.String(40), nullable=False, default=METHOD_HEURISTIC)

    occurred_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    source_analysis = db.relationship(
        'Analysis',
        foreign_keys=[source_analysis_id],
        backref=db.backref('propagation_events_out', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )
    target_analysis = db.relationship(
        'Analysis',
        foreign_keys=[target_analysis_id],
        backref=db.backref('propagation_events_in', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )
    user = db.relationship(
        'User',
        backref=db.backref('propagation_events', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )
    narrative = db.relationship(
        'Narrative',
        backref=db.backref('propagation_events', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )

    __table_args__ = (
        # NOTE: PostgreSQL treats NULLs as distinct, so this constraint does not
        # deduplicate rows whose narrative_id IS NULL. The service layer performs
        # an explicit existence check before insert to cover that case.
        db.UniqueConstraint('narrative_id', 'source_analysis_id', 'target_analysis_id',
                            'relationship_type', name='uq_propagation_events_edge'),
        db.Index('ix_propagation_events_narrative_ts', 'narrative_id', 'occurred_at'),
        db.Index('ix_propagation_events_source_target',
                 'source_analysis_id', 'target_analysis_id'),
        db.Index('ix_propagation_events_user_ts', 'user_id', 'occurred_at'),
    )

    @property
    def is_cross_platform(self):
        known = (self.PLATFORM_YOUTUBE, self.PLATFORM_REDDIT)
        return (self.source_platform in known and self.target_platform in known
                and self.source_platform != self.target_platform)

    def to_dict(self):
        return {
            'id': self.id,
            'narrative_id': self.narrative_id,
            'source_analysis_id': self.source_analysis_id,
            'target_analysis_id': self.target_analysis_id,
            'source_platform': self.source_platform,
            'target_platform': self.target_platform,
            'source_ref': self.source_ref,
            'target_ref': self.target_ref,
            'relationship_type': self.relationship_type,
            'direction': self.direction,
            'propagation_score': self.propagation_score,
            'confidence': self.confidence,
            'similarity_score': self.similarity_score,
            'lag_seconds': self.lag_seconds,
            'is_cross_platform': self.is_cross_platform,
            'shared_entities': _as_list(self.shared_entities),
            'reasons': _as_list(self.reasons),
            'evidence': _as_dict(self.evidence),
            'detection_method': self.detection_method,
            'occurred_at': self.occurred_at.isoformat() if self.occurred_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return (f'<PropagationEvent {self.source_analysis_id}->{self.target_analysis_id} '
                f'({self.relationship_type}) score={self.propagation_score}>')
