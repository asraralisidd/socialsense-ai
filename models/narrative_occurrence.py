from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from database import db


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_column():
    """JSONB on PostgreSQL, JSON elsewhere (SQLite test suite)."""
    return db.JSON().with_variant(JSONB(), 'postgresql')


def _as_dict(value):
    return value if isinstance(value, dict) else {}


class NarrativeOccurrence(db.Model):
    """Links a :class:`Narrative` to one analyzed piece of content.

    ``occurred_at`` is always populated: the service layer prefers the real
    content timestamp (video ``published_at`` / Reddit ``created_utc`` /
    comment ``published_at``) and falls back to the analysis creation time when
    the platform timestamp is NULL, so temporal queries never see NULLs.
    """

    __tablename__ = 'narrative_occurrences'

    PLATFORM_YOUTUBE = 'youtube'
    PLATFORM_REDDIT = 'reddit'
    PLATFORM_UNKNOWN = 'unknown'

    SOURCE_TITLE = 'title'
    SOURCE_DESCRIPTION = 'description'
    SOURCE_TRANSCRIPT = 'transcript'
    SOURCE_COMMENT = 'comment'
    SOURCE_COMBINED = 'combined'

    TIMESTAMP_PLATFORM = 'platform'
    TIMESTAMP_ANALYSIS = 'analysis_fallback'

    id = db.Column(db.Integer, primary_key=True)
    narrative_id = db.Column(db.Integer, db.ForeignKey('narratives.id', ondelete='CASCADE'),
                             nullable=False, index=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)

    platform = db.Column(db.String(20), nullable=False, default=PLATFORM_UNKNOWN, index=True)
    source = db.Column(db.String(20), nullable=False, default=SOURCE_COMBINED)
    channel_id = db.Column(db.String(200), nullable=True, index=True)
    content_ref = db.Column(db.String(200), nullable=True, index=True)

    relevance_score = db.Column(db.Float, nullable=False, default=0.0)
    risk_score = db.Column(db.Float, nullable=False, default=0.0)
    match_count = db.Column(db.Integer, nullable=False, default=0)

    evidence = db.Column(_json_column(), nullable=True)

    timestamp_source = db.Column(db.String(20), nullable=False, default=TIMESTAMP_ANALYSIS)
    occurred_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    analysis = db.relationship(
        'Analysis',
        backref=db.backref('narrative_occurrences', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )
    user = db.relationship(
        'User',
        backref=db.backref('narrative_occurrences', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.UniqueConstraint('narrative_id', 'analysis_id', 'source',
                            name='uq_narrative_occurrence_narrative_analysis_source'),
        db.Index('ix_narrative_occurrences_narrative_ts', 'narrative_id', 'occurred_at'),
        db.Index('ix_narrative_occurrences_user_ts', 'user_id', 'occurred_at'),
    )

    @property
    def used_fallback_timestamp(self):
        return self.timestamp_source == self.TIMESTAMP_ANALYSIS

    def to_dict(self):
        return {
            'id': self.id,
            'narrative_id': self.narrative_id,
            'analysis_id': self.analysis_id,
            'platform': self.platform,
            'source': self.source,
            'channel_id': self.channel_id,
            'content_ref': self.content_ref,
            'relevance_score': self.relevance_score,
            'risk_score': self.risk_score,
            'match_count': self.match_count,
            'evidence': _as_dict(self.evidence),
            'timestamp_source': self.timestamp_source,
            'used_fallback_timestamp': self.used_fallback_timestamp,
            'occurred_at': self.occurred_at.isoformat() if self.occurred_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return (f'<NarrativeOccurrence narrative={self.narrative_id} '
                f'analysis={self.analysis_id} ({self.platform})>')
