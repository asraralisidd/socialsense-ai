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


class ThreatAssessment(db.Model):
    """Consolidated V12 assessment for one analysis (one-to-one).

    Component score columns are **nullable on purpose**: ``NULL`` means the
    signal was unavailable and was excluded from the weighted combination, which
    is materially different from a genuine ``0.0``. The engine renormalizes the
    remaining weights rather than treating a missing signal as "no threat".

    ``capability_labels`` records, per component, whether the contribution is
    Implemented / Heuristic / Fallback / Future ML so that no consumer can
    mistake a heuristic for a trained model.
    """

    __tablename__ = 'threat_assessments'

    LEVEL_MINIMAL = 'Minimal'
    LEVEL_LOW = 'Low'
    LEVEL_MODERATE = 'Moderate'
    LEVEL_ELEVATED = 'Elevated'
    LEVEL_HIGH = 'High'

    LEVELS = (LEVEL_MINIMAL, LEVEL_LOW, LEVEL_MODERATE, LEVEL_ELEVATED, LEVEL_HIGH)

    COMPONENT_AUTHENTICITY = 'authenticity'
    COMPONENT_COORDINATION = 'coordination'
    COMPONENT_NARRATIVE = 'narrative'
    COMPONENT_PROPAGATION = 'propagation'
    COMPONENT_TEMPORAL = 'temporal'
    COMPONENT_ENTITY = 'entity'

    COMPONENTS = (
        COMPONENT_AUTHENTICITY,
        COMPONENT_COORDINATION,
        COMPONENT_NARRATIVE,
        COMPONENT_PROPAGATION,
        COMPONENT_TEMPORAL,
        COMPONENT_ENTITY,
    )

    CAPABILITY_IMPLEMENTED = 'implemented'
    CAPABILITY_HEURISTIC = 'heuristic'
    CAPABILITY_FALLBACK = 'fallback'
    CAPABILITY_FUTURE_ML = 'future_ml'
    CAPABILITY_UNAVAILABLE = 'unavailable'

    METHOD_HEURISTIC_WEIGHTED = 'heuristic_weighted'
    METHOD_UNAVAILABLE = 'unavailable'

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id', ondelete='CASCADE'),
                            nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)

    overall_threat_score = db.Column(db.Float, nullable=False, default=0.0)
    threat_level = db.Column(db.String(20), nullable=False, default=LEVEL_MINIMAL)
    confidence = db.Column(db.Float, nullable=False, default=0.0)

    # NULL == signal unavailable (excluded from weighting), not zero threat.
    authenticity_score = db.Column(db.Float, nullable=True)
    manipulation_score = db.Column(db.Float, nullable=True)
    coordination_score = db.Column(db.Float, nullable=True)
    narrative_risk_score = db.Column(db.Float, nullable=True)
    propagation_score = db.Column(db.Float, nullable=True)
    temporal_score = db.Column(db.Float, nullable=True)
    entity_risk_score = db.Column(db.Float, nullable=True)

    evidence_coverage = db.Column(db.Float, nullable=False, default=0.0)
    agreement_score = db.Column(db.Float, nullable=False, default=0.0)

    component_scores = db.Column(_json_column(), nullable=True)
    component_weights = db.Column(_json_column(), nullable=True)
    available_components = db.Column(_json_column(), nullable=True)
    missing_components = db.Column(_json_column(), nullable=True)
    capability_labels = db.Column(_json_column(), nullable=True)

    summary = db.Column(db.Text, nullable=True)
    reasons = db.Column(_json_column(), nullable=True)
    indicators = db.Column(_json_column(), nullable=True)
    limitations = db.Column(_json_column(), nullable=True)

    assessment_method = db.Column(db.String(40), nullable=False,
                                  default=METHOD_HEURISTIC_WEIGHTED)

    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    analysis = db.relationship(
        'Analysis',
        backref=db.backref('threat_assessment', uselist=False,
                           cascade='all, delete-orphan'),
    )
    user = db.relationship(
        'User',
        backref=db.backref('threat_assessments', lazy='dynamic',
                           cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.Index('ix_threat_assessments_user_created', 'user_id', 'created_at'),
        db.Index('ix_threat_assessments_user_score', 'user_id', 'overall_threat_score'),
    )

    @classmethod
    def level_for_score(cls, score):
        value = score or 0.0
        if value >= 75:
            return cls.LEVEL_HIGH
        if value >= 50:
            return cls.LEVEL_ELEVATED
        if value >= 25:
            return cls.LEVEL_MODERATE
        if value >= 10:
            return cls.LEVEL_LOW
        return cls.LEVEL_MINIMAL

    def to_dict(self):
        return {
            'id': self.id,
            'analysis_id': self.analysis_id,
            'overall_threat_score': self.overall_threat_score,
            'threat_level': self.threat_level,
            'confidence': self.confidence,
            'authenticity_score': self.authenticity_score,
            'manipulation_score': self.manipulation_score,
            'coordination_score': self.coordination_score,
            'narrative_risk_score': self.narrative_risk_score,
            'propagation_score': self.propagation_score,
            'temporal_score': self.temporal_score,
            'entity_risk_score': self.entity_risk_score,
            'evidence_coverage': self.evidence_coverage,
            'agreement_score': self.agreement_score,
            'component_scores': _as_dict(self.component_scores),
            'component_weights': _as_dict(self.component_weights),
            'available_components': _as_list(self.available_components),
            'missing_components': _as_list(self.missing_components),
            'capability_labels': _as_dict(self.capability_labels),
            'summary': self.summary,
            'reasons': _as_list(self.reasons),
            'indicators': _as_list(self.indicators),
            'limitations': _as_list(self.limitations),
            'assessment_method': self.assessment_method,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return (f'<ThreatAssessment analysis={self.analysis_id} '
                f'score={self.overall_threat_score} level={self.threat_level}>')
