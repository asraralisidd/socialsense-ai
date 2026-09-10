from flask import current_app
from database import db
from models.entity_history import EntityHistory
from models.entity import Entity
from models.entity_context import EntityContext


class EntityHistoryService:
    def record_entity_history(self, analysis_id, user_id, video_id, channel_id,
                              normalized_name, entity_type, sentiment_score,
                              risk_score, mention_count, importance_score):
        history = EntityHistory(
            normalized_name=normalized_name,
            entity_type=entity_type,
            video_id=video_id,
            channel_id=channel_id,
            analysis_id=analysis_id,
            user_id=user_id,
            sentiment_score=round(sentiment_score, 1),
            risk_score=round(risk_score, 1),
            mention_count=mention_count,
            importance_score=round(importance_score, 1),
        )
        db.session.add(history)
        db.session.commit()
        return history

    def get_entity_history(self, normalized_name, channel_id=None, user_id=None, limit=20):
        query = EntityHistory.query.filter_by(normalized_name=normalized_name)
        if channel_id:
            query = query.filter_by(channel_id=channel_id)
        if user_id:
            query = query.filter_by(user_id=user_id)
        return query.order_by(EntityHistory.created_at.asc()).limit(limit).all()

    def get_entity_frequency(self, channel_id, user_id):
        from sqlalchemy import func
        results = db.session.query(
            EntityHistory.normalized_name,
            EntityHistory.entity_type,
            func.count(EntityHistory.id).label('appearances'),
            func.avg(EntityHistory.sentiment_score).label('avg_sentiment'),
            func.avg(EntityHistory.risk_score).label('avg_risk'),
        ).filter_by(channel_id=channel_id, user_id=user_id).group_by(
            EntityHistory.normalized_name,
            EntityHistory.entity_type,
        ).order_by(func.count(EntityHistory.id).desc()).all()

        return [
            {
                'normalized_name': r.normalized_name,
                'entity_type': r.entity_type,
                'appearances': r.appearances,
                'avg_sentiment': round(r.avg_sentiment or 0.0, 1),
                'avg_risk': round(r.avg_risk or 0.0, 1),
            }
            for r in results
        ]

    def get_entity_sentiment_trend(self, normalized_name, channel_id=None, user_id=None, limit=10):
        records = self.get_entity_history(normalized_name, channel_id, user_id, limit)
        return [
            {
                'video_id': r.video_id,
                'sentiment_score': r.sentiment_score,
                'risk_score': r.risk_score,
                'mention_count': r.mention_count,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]

    def get_top_entities_by_channel(self, channel_id, user_id, limit=10):
        from sqlalchemy import func
        results = db.session.query(
            EntityHistory.normalized_name,
            EntityHistory.entity_type,
            func.count(EntityHistory.id).label('appearances'),
            func.avg(EntityHistory.sentiment_score).label('avg_sentiment'),
            func.avg(EntityHistory.risk_score).label('avg_risk'),
        ).filter_by(channel_id=channel_id, user_id=user_id).group_by(
            EntityHistory.normalized_name,
            EntityHistory.entity_type,
        ).order_by(func.count(EntityHistory.id).desc()).limit(limit).all()

        return [
            {
                'normalized_name': r.normalized_name,
                'entity_type': r.entity_type,
                'appearances': r.appearances,
                'avg_sentiment': round(r.avg_sentiment or 0.0, 1),
                'avg_risk': round(r.avg_risk or 0.0, 1),
            }
            for r in results
        ]
