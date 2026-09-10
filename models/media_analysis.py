import json
from datetime import datetime, timezone
from database import db


class MediaAnalysis(db.Model):
    __tablename__ = 'media_analyses'

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=False, unique=True, index=True)
    overall_ai_probability = db.Column(db.Float, nullable=False, default=0.0)
    overall_authenticity_score = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    deepfake_score = db.Column(db.Float, nullable=False, default=0.0)
    synthetic_voice_score = db.Column(db.Float, nullable=False, default=0.0)
    thumbnail_ai_score = db.Column(db.Float, nullable=False, default=0.0)
    frame_manipulation_score = db.Column(db.Float, nullable=False, default=0.0)
    metadata_score = db.Column(db.Float, nullable=False, default=0.0)
    summary = db.Column(db.Text, nullable=True)
    reasons = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        reasons = []
        try:
            if self.reasons:
                reasons = json.loads(self.reasons)
                if not isinstance(reasons, list):
                    reasons = []
        except (ValueError, TypeError):
            reasons = []
        return {
            'overall_ai_probability': self.overall_ai_probability,
            'overall_authenticity_score': self.overall_authenticity_score,
            'confidence': self.confidence,
            'deepfake_score': self.deepfake_score,
            'synthetic_voice_score': self.synthetic_voice_score,
            'thumbnail_ai_score': self.thumbnail_ai_score,
            'frame_manipulation_score': self.frame_manipulation_score,
            'metadata_score': self.metadata_score,
            'summary': self.summary,
            'reasons': reasons,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<MediaAnalysis {self.id} (analysis {self.analysis_id})>'
