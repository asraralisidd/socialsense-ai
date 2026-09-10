from datetime import datetime, timezone
from flask import current_app
from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.reddit_analysis import RedditAnalysis
from models.comment_result import CommentResult
from repositories.analysis_repository import AnalysisRepository
from repositories.comment_result_repository import CommentResultRepository
from repositories.reddit_analysis_repository import RedditAnalysisRepository
from services.youtube_service import YouTubeService, YouTubeAPIError
from services.reddit_service import RedditService, RedditAPIError, RedditDemoService
from services.demo_service import DemoService
from services.risk_scoring_service import RiskScoringService
from services.sentiment_service import SentimentService
from services.toxicity_service import ToxicityService
from services.spam_service import SpamService
from services.bot_detection_service import BotDetectionService
from services.risk_engine import RiskEngine
from services.transcript_service import TranscriptService
from services.transcript_processing_service import TranscriptProcessingService
from services.context_matching_service import ContextMatchingService
from models.video_transcript import VideoTranscript
from models.comment_context import CommentContext
from services.entity_extraction_service import EntityExtractionService
from services.entity_resolution_service import EntityResolutionService
from services.entity_intelligence_service import EntityIntelligenceService
from services.entity_sentiment_service import EntitySentimentService
from services.entity_risk_service import EntityRiskService
from services.entity_summary_service import EntitySummaryService
from models.entity import Entity
from models.entity_mention import EntityMention
from models.entity_context import EntityContext
from models.channel_context import ChannelContext
from models.video_context_history import VideoContextHistory
from models.entity_history import EntityHistory
from services.channel_context_service import ChannelContextService
from services.video_history_service import VideoHistoryService
from services.entity_history_service import EntityHistoryService
from services.context_intelligence_service import ContextIntelligenceService
from services.authenticity_service import AuthenticityService
from models.media_analysis import MediaAnalysis
from services.narrative_intelligence_service import NarrativeIntelligenceService
from services.coordination_intelligence_service import CoordinationIntelligenceService
from services.propagation_intelligence_service import PropagationIntelligenceService
from services.temporal_intelligence_service import TemporalIntelligenceService
from services.threat_assessment_service import ThreatAssessmentService


class AnalysisService:
    def __init__(self):
        self.analysis_repo = AnalysisRepository()
        self.comment_repo = CommentResultRepository()
        self.reddit_repo = RedditAnalysisRepository()
        self.youtube_service = YouTubeService()
        self.reddit_service = RedditService()
        self.reddit_demo = RedditDemoService()
        self.demo_service = DemoService()
        self.risk_scorer = RiskScoringService()
        self.transcript_service = TranscriptService()
        self.transcript_processor = TranscriptProcessingService()
        self.context_matcher = ContextMatchingService()
        self.entity_extractor = EntityExtractionService()
        self.entity_resolver = EntityResolutionService()
        self.entity_intelligence = EntityIntelligenceService()
        self.entity_sentiment = EntitySentimentService()
        self.entity_risk = EntityRiskService()
        self.entity_summary = EntitySummaryService()
        self.channel_service = ChannelContextService()
        self.video_history = VideoHistoryService()
        self.entity_history = EntityHistoryService()
        self.context_intelligence = ContextIntelligenceService()
        self.authenticity_service = AuthenticityService()
        self.narrative_service = NarrativeIntelligenceService()
        self.coordination_service = CoordinationIntelligenceService()
        self.propagation_service = PropagationIntelligenceService()
        self.temporal_service = TemporalIntelligenceService()
        self.threat_service = ThreatAssessmentService()

    def create_youtube_analysis(self, user_id, video_url, comment_limit=100, progress_callback=None):
        video_id = self.youtube_service.extract_video_id(video_url)
        if not video_id:
            return {'success': False, 'error': 'Invalid YouTube URL or Video ID.'}

        api_key = current_app.config.get('YOUTUBE_API_KEY', '')
        is_demo = not api_key

        video_info = None
        comments = []
        api_error = None

        if not is_demo:
            try:
                video_info = self.youtube_service.fetch_video_info(video_id)
                comments = self.youtube_service.fetch_comments(video_id, max_results=comment_limit)
            except YouTubeAPIError as e:
                api_error = e.error_type
                flash_message = str(e)
                return {'success': False, 'error': flash_message, 'api_error': e.error_type}
        else:
            video_info = self.demo_service.get_video_info_by_id(video_id)
            comments = self.demo_service.get_comments()

        if not video_info:
            video_info = {
                'video_id': video_id,
                'title': f'Video ({video_id})',
                'description': '',
                'channel': 'Unknown',
                'published_at': None,
                'view_count': 0,
                'like_count': 0,
                'comment_count': 0,
            }

        if not comments:
            comments = []

        analysis = self.analysis_repo.create(user_id=user_id, analysis_type='youtube')

        yt_analysis = YouTubeAnalysis(
            analysis_id=analysis.id,
            video_id=video_info['video_id'],
            video_title=video_info.get('title', 'Unknown Title'),
            video_description=video_info.get('description', ''),
            channel_name=video_info.get('channel', 'Unknown'),
            published_at=video_info.get('published_at'),
            view_count=video_info.get('view_count', 0),
            like_count=video_info.get('like_count', 0),
            comment_count=len(comments),
            comment_limit=comment_limit,
            is_demo=is_demo,
            api_error=api_error,
        )
        db.session.add(yt_analysis)
        db.session.commit()

        all_texts = [c['text'] for c in comments]
        for comment in comments:
            self._analyze_and_store_comment_v2(analysis.id, comment, all_texts)

        self._generate_analysis_summary(analysis.id)

        transcript_obj = None
        if current_app.config.get('ENABLE_TRANSCRIPT_ANALYSIS', True):
            try:
                if is_demo:
                    transcript_obj = self.transcript_service.store_demo_transcript(yt_analysis.id, video_id)
                else:
                    lang = current_app.config.get('TRANSCRIPT_LANGUAGE_PRIORITY', 'en')
                    transcript_obj = self.transcript_service.fetch_and_store(yt_analysis.id, video_id, language=lang)

                if transcript_obj and not transcript_obj.is_available and current_app.config.get('ENABLE_TRANSCRIPT_FALLBACK_DEMO', False):
                    fallback_comments = comments if not is_demo else None
                    transcript_obj = self.transcript_service.store_fallback_generated(
                        yt_analysis.id, video_id,
                        title=video_info.get('title', ''),
                        description=video_info.get('description', ''),
                        comments=fallback_comments,
                    )

                if transcript_obj and transcript_obj.is_available:
                    top_keywords = self.transcript_processor.extract_keywords(transcript_obj.transcript_text or '', 20)
                    top_phrases = self.transcript_processor.extract_phrases(transcript_obj.transcript_text or '', 2, 4)
                    segments = self.transcript_service.get_segments(transcript_obj.id)

                    transcript_obj.topics = ', '.join(self.transcript_processor.get_topics(
                        [seg.text for seg in segments], 5))
                    transcript_obj.summary = self._generate_transcript_summary(transcript_obj.transcript_text or '')

                    comment_results = self.comment_repo.get_by_analysis_id(analysis.id)
                    for cr in comment_results:
                        context_data = self.context_matcher.compute_context(
                            cr.comment_text,
                            transcript_obj.transcript_text or '',
                            segments,
                            top_keywords,
                            top_phrases,
                        )
                        self.context_matcher.create_comment_context(cr.id, transcript_obj.id, context_data)

                    transcript_obj.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    db.session.commit()
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Transcript processing failed: {e}')

        entities = []
        entity_sentiments = []
        entity_risks = []
        entity_summary_data = {}
        comment_results_list = []
        if current_app.config.get('ENABLE_ENTITY_ANALYSIS', True):
            try:
                entity_comments = comments if not is_demo else self.demo_service.get_comments()
                comment_results_list = self.comment_repo.get_by_analysis_id(analysis.id)
                extracted = self.entity_extractor.extract_from_texts(
                    title=video_info.get('title', ''),
                    description=video_info.get('description', ''),
                    transcript_text=transcript_obj.transcript_text if transcript_obj else '',
                    comments=entity_comments,
                )
                total_segments = transcript_obj.segment_count if transcript_obj and transcript_obj.is_available else 0
                intelligence = self.entity_intelligence.compute_intelligence(
                    extracted, len(entity_comments), total_segments
                )

                for ent_data in intelligence:
                    entity = Entity(
                        analysis_id=analysis.id,
                        name=ent_data['name'],
                        normalized_name=ent_data['normalized_name'],
                        entity_type=ent_data['entity_type'],
                        source=ent_data['source'],
                        frequency=ent_data['frequency'],
                        importance_score=ent_data['importance_score'],
                    )
                    db.session.add(entity)
                    db.session.flush()
                    entities.append(entity)

                    if current_app.config.get('ENABLE_ENTITY_SENTIMENT', True) or current_app.config.get('ENABLE_ENTITY_RISK', True):
                        for cr in comment_results_list:
                            mention = EntityMention(
                                entity_id=entity.id,
                                comment_result_id=cr.id,
                                mention_text=ent_data['name'],
                                mention_source=ent_data['source'],
                                context_snippet=cr.comment_text[:200],
                            )
                            db.session.add(mention)

                db.session.commit()

                new_entity_list = [{'name': e.normalized_name, 'entity_type': e.entity_type, 'source': e.source, 'frequency': e.frequency, 'importance_score': e.importance_score} for e in entities]

                if current_app.config.get('ENABLE_ENTITY_SENTIMENT', True) and comment_results_list:
                    entity_sentiments = self.entity_sentiment.compute_entity_sentiments(new_entity_list, comment_results_list)
                    for es in entity_sentiments:
                        for mention in es['mentions']:
                            ec = EntityContext.query.filter_by(
                                entity_id=[e.id for e in entities if e.normalized_name == es['entity_name']][0],
                                comment_result_id=mention['comment_result_id'],
                            ).first()
                            if not ec:
                                ec = EntityContext(
                                    entity_id=[e.id for e in entities if e.normalized_name == es['entity_name']][0],
                                    comment_result_id=mention['comment_result_id'],
                                    entity_sentiment=mention['sentiment'],
                                    entity_sentiment_score=mention['score'],
                                    entity_risk_score=0.0,
                                    entity_relevance_score=0.0,
                                    entity_context_label=EntityContext.LABEL_UNKNOWN,
                                    reason=mention['reason'],
                                )
                                db.session.add(ec)
                    db.session.commit()

                if current_app.config.get('ENABLE_ENTITY_RISK', True) and comment_results_list:
                    entity_risks = self.entity_risk.compute_entity_risks(new_entity_list, comment_results_list)
                    for er in entity_risks:
                        for detail in er['details']:
                            ec = EntityContext.query.filter_by(
                                entity_id=[e.id for e in entities if e.normalized_name == er['entity_name']][0],
                                comment_result_id=detail['comment_result_id'],
                            ).first()
                            if ec:
                                ec.entity_risk_score = detail['risk_score']
                                if ec.reason:
                                    ec.reason += '; ' + '; '.join(detail['reasons'])
                                else:
                                    ec.reason = '; '.join(detail['reasons'])
                    db.session.commit()

                entity_summary_data = self.entity_summary.generate_summary(entities, entity_sentiments, entity_risks)

            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Entity intelligence failed: {e}')

        if current_app.config.get('ENABLE_CHANNEL_INTELLIGENCE', True):
            try:
                channel_id = video_info.get('channel', 'Unknown').replace(' ', '_').lower()
                channel_name = video_info.get('channel', 'Unknown')
                channel_obj = self.channel_service.get_or_create_channel(
                    user_id, channel_id, channel_name
                )
                entity_names = [e.normalized_name for e in entities]
                entity_count = len(entities)
                avg_sent = analysis.average_sentiment or 50.0
                avg_r = analysis.average_risk if hasattr(analysis, 'average_risk') and analysis.average_risk else 0.0

                self.video_history.record_video_analysis(
                    analysis.id, user_id, video_id, channel_id,
                    video_info.get('title', ''), entity_count,
                    avg_sent, avg_r,
                    top_entities=entity_names[:10],
                )

                for entity in entities:
                    entity_avg_sent = 50.0
                    entity_avg_risk = 0.0
                    ecs = EntityContext.query.filter_by(entity_id=entity.id).all()
                    if ecs:
                        entity_avg_sent = sum(ec.entity_sentiment_score or 50.0 for ec in ecs) / len(ecs)
                        entity_avg_risk = sum(ec.entity_risk_score or 0.0 for ec in ecs) / len(ecs)
                    self.entity_history.record_entity_history(
                        analysis.id, user_id, video_id, channel_id,
                        entity.normalized_name, entity.entity_type,
                        entity_avg_sent, entity_avg_risk,
                        entity.frequency or 0, entity.importance_score or 0.0,
                    )

                self.channel_service.update_channel_stats(user_id, channel_id)

                context_intel = self.context_intelligence.compute_intelligence(
                    user_id, channel_id, analysis.id,
                    entity_names=entity_names,
                    current_sentiment=avg_sent,
                    current_risk=avg_r,
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Channel intelligence failed: {e}')

        v12_components = {}

        self._notify_progress(progress_callback, 82, 'Running Authenticity Engine')
        if current_app.config.get('ENABLE_AUTHENTICITY_ENGINE', True):
            try:
                transcript_text = transcript_obj.transcript_text if transcript_obj and transcript_obj.is_available else None
                v12_components['authenticity'] = self.authenticity_service.analyze(
                    analysis,
                    video_info=video_info,
                    transcript_text=transcript_text,
                    thumbnail_url=None,
                    demo_mode=is_demo,
                    key=video_id,
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Authenticity intelligence failed: {e}')

        self._notify_progress(progress_callback, 85, 'V12 Narrative Intelligence')
        if current_app.config.get('ENABLE_NARRATIVE_INTELLIGENCE', True):
            try:
                narrative_transcript = transcript_obj.transcript_text if transcript_obj and transcript_obj.is_available else None
                v12_components['narrative'] = self.narrative_service.analyze(
                    analysis,
                    video_info=video_info,
                    transcript_text=narrative_transcript,
                    entities=entities or None,
                    comments=comment_results_list or None,
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Narrative intelligence failed: {e}')

        self._notify_progress(progress_callback, 88, 'V12 Coordination Detection')
        if current_app.config.get('ENABLE_COORDINATION_DETECTION', True):
            try:
                v12_components['coordination'] = self.coordination_service.analyze(
                    analysis,
                    comments=comment_results_list or None,
                    entities=entities or None,
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Coordination intelligence failed: {e}')

        self._notify_progress(progress_callback, 91, 'V12 Cross-Platform Linking')
        if current_app.config.get('ENABLE_PROPAGATION_INTELLIGENCE', True):
            try:
                v12_components['propagation'] = self.propagation_service.analyze(analysis)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Propagation intelligence failed: {e}')

        self._notify_progress(progress_callback, 94, 'V12 Temporal Intelligence')
        if current_app.config.get('ENABLE_TEMPORAL_INTELLIGENCE', True):
            try:
                v12_components['temporal'] = self.temporal_service.analyze(analysis)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Temporal intelligence failed: {e}')

        self._notify_progress(progress_callback, 97, 'V12 Threat Assessment')
        if current_app.config.get('ENABLE_THREAT_ASSESSMENT', True):
            try:
                self.threat_service.analyze(analysis, components=v12_components)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Threat assessment failed: {e}')

        return {
            'success': True,
            'analysis_id': analysis.id,
            'is_demo': is_demo,
            'comment_count': len(comments),
            'transcript_available': bool(transcript_obj and transcript_obj.is_available),
        }

    def create_reddit_analysis(self, user_id, post_id, subreddit=None, input_type='post', comment_limit=100, progress_callback=None):
        has_creds = bool(current_app.config.get('REDDIT_CLIENT_ID', '') and current_app.config.get('REDDIT_CLIENT_SECRET', ''))
        is_demo = not has_creds

        if not is_demo:
            try:
                post_info = self.reddit_service.fetch_post_info(post_id)
                comments = self.reddit_service.fetch_comments(post_id, max_results=comment_limit)
            except RedditAPIError as e:
                return {'success': False, 'error': str(e), 'api_error': e.error_type}
        else:
            post_info = self.reddit_demo.get_post_info(post_id, subreddit)
            comments = self.reddit_demo.get_comments()

        if not comments:
            comments = []

        analysis = self.analysis_repo.create(user_id=user_id, analysis_type='reddit')

        reddit = RedditAnalysis(
            analysis_id=analysis.id,
            post_id=post_info['post_id'],
            subreddit=post_info.get('subreddit', ''),
            post_title=post_info.get('title', 'Unknown Post'),
            post_body=post_info.get('body', ''),
            post_author=post_info.get('author', '[deleted]'),
            post_score=post_info.get('score', 0),
            upvote_ratio=post_info.get('upvote_ratio', 0.0),
            comment_count=len(comments),
            comment_limit=comment_limit,
            permalink=post_info.get('permalink', ''),
            created_utc=post_info.get('created_utc'),
            is_demo=is_demo,
        )
        db.session.add(reddit)
        db.session.commit()

        all_texts = [c['text'] for c in comments]
        for comment in comments:
            self._analyze_and_store_comment_v2(analysis.id, comment, all_texts)

        self._generate_analysis_summary(analysis.id)

        entities = []
        entity_sentiments = []
        entity_risks = []
        entity_summary_data = {}
        comment_results_list = []
        if current_app.config.get('ENABLE_ENTITY_ANALYSIS', True):
            try:
                entity_comments = comments if not is_demo else self.reddit_demo.get_comments()
                comment_results_list = self.comment_repo.get_by_analysis_id(analysis.id)
                extracted = self.entity_extractor.extract_from_texts(
                    title=post_info.get('title', ''),
                    description=post_info.get('body', ''),
                    comments=entity_comments,
                )
                intelligence = self.entity_intelligence.compute_intelligence(
                    extracted, len(entity_comments), 0
                )
                for ent_data in intelligence:
                    entity = Entity(
                        analysis_id=analysis.id,
                        name=ent_data['name'],
                        normalized_name=ent_data['normalized_name'],
                        entity_type=ent_data['entity_type'],
                        source=ent_data['source'],
                        frequency=ent_data['frequency'],
                        importance_score=ent_data['importance_score'],
                    )
                    db.session.add(entity)
                    db.session.flush()
                    entities.append(entity)
                    if current_app.config.get('ENABLE_ENTITY_SENTIMENT', True) or current_app.config.get('ENABLE_ENTITY_RISK', True):
                        for cr in comment_results_list:
                            mention = EntityMention(
                                entity_id=entity.id,
                                comment_result_id=cr.id,
                                mention_text=ent_data['name'],
                                mention_source=ent_data['source'],
                                context_snippet=cr.comment_text[:200],
                            )
                            db.session.add(mention)
                db.session.commit()
                new_entity_list = [{'name': e.normalized_name, 'entity_type': e.entity_type, 'source': e.source, 'frequency': e.frequency, 'importance_score': e.importance_score} for e in entities]
                if current_app.config.get('ENABLE_ENTITY_SENTIMENT', True) and comment_results_list:
                    entity_sentiments = self.entity_sentiment.compute_entity_sentiments(new_entity_list, comment_results_list)
                    for es in entity_sentiments:
                        for mention in es['mentions']:
                            ec = EntityContext.query.filter_by(
                                entity_id=[e.id for e in entities if e.normalized_name == es['entity_name']][0],
                                comment_result_id=mention['comment_result_id'],
                            ).first()
                            if not ec:
                                ec = EntityContext(
                                    entity_id=[e.id for e in entities if e.normalized_name == es['entity_name']][0],
                                    comment_result_id=mention['comment_result_id'],
                                    entity_sentiment=mention['sentiment'],
                                    entity_sentiment_score=mention['score'],
                                    entity_risk_score=0.0,
                                    entity_relevance_score=0.0,
                                    entity_context_label=EntityContext.LABEL_UNKNOWN,
                                    reason=mention['reason'],
                                )
                                db.session.add(ec)
                    db.session.commit()
                if current_app.config.get('ENABLE_ENTITY_RISK', True) and comment_results_list:
                    entity_risks = self.entity_risk.compute_entity_risks(new_entity_list, comment_results_list)
                    for er in entity_risks:
                        for detail in er['details']:
                            ec = EntityContext.query.filter_by(
                                entity_id=[e.id for e in entities if e.normalized_name == er['entity_name']][0],
                                comment_result_id=detail['comment_result_id'],
                            ).first()
                            if ec:
                                ec.entity_risk_score = detail['risk_score']
                                if ec.reason:
                                    ec.reason += '; ' + '; '.join(detail['reasons'])
                                else:
                                    ec.reason = '; '.join(detail['reasons'])
                    db.session.commit()
                entity_summary_data = self.entity_summary.generate_summary(entities, entity_sentiments, entity_risks)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Entity intelligence failed: {e}')

        if current_app.config.get('ENABLE_CHANNEL_INTELLIGENCE', True):
            try:
                channel_id = post_info.get('subreddit', 'reddit').replace(' ', '_').lower()
                channel_name = f"r/{post_info.get('subreddit', 'reddit')}"
                channel_obj = self.channel_service.get_or_create_channel(
                    user_id, channel_id, channel_name
                )
                entity_names = [e.normalized_name for e in entities]
                entity_count = len(entities)
                avg_sent = analysis.average_sentiment or 50.0
                avg_r = analysis.average_risk if hasattr(analysis, 'average_risk') and analysis.average_risk else 0.0

                if current_app.config.get('ENABLE_HISTORICAL_CONTEXT', True):
                    self.video_history.record_video_analysis(
                        analysis.id, user_id, post_info.get('post_id', ''),
                        channel_id, post_info.get('title', ''), entity_count,
                        avg_sent, avg_r,
                        top_entities=entity_names[:10],
                    )

                    for entity in entities:
                        entity_avg_sent = 50.0
                        entity_avg_risk = 0.0
                        ecs = EntityContext.query.filter_by(entity_id=entity.id).all()
                        if ecs:
                            entity_avg_sent = sum(ec.entity_sentiment_score or 50.0 for ec in ecs) / len(ecs)
                            entity_avg_risk = sum(ec.entity_risk_score or 0.0 for ec in ecs) / len(ecs)
                        self.entity_history.record_entity_history(
                            analysis.id, user_id, post_info.get('post_id', ''),
                            channel_id, entity.normalized_name, entity.entity_type,
                            entity_avg_sent, entity_avg_risk,
                            entity.frequency or 0, entity.importance_score or 0.0,
                        )

                    self.channel_service.update_channel_stats(user_id, channel_id)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Channel intelligence failed: {e}')

        v12_components = {}

        self._notify_progress(progress_callback, 82, 'Running Authenticity Engine')
        if current_app.config.get('ENABLE_AUTHENTICITY_ENGINE', True):
            try:
                v12_components['authenticity'] = self.authenticity_service.analyze(
                    analysis,
                    video_info=post_info,
                    transcript_text=post_info.get('body', ''),
                    thumbnail_url=None,
                    demo_mode=is_demo,
                    key=post_info.get('post_id', ''),
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Authenticity intelligence failed: {e}')

        self._notify_progress(progress_callback, 85, 'V12 Narrative Intelligence')
        if current_app.config.get('ENABLE_NARRATIVE_INTELLIGENCE', True):
            try:
                v12_components['narrative'] = self.narrative_service.analyze(
                    analysis,
                    video_info=post_info,
                    transcript_text=None,
                    entities=entities or None,
                    comments=comment_results_list or None,
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Narrative intelligence failed: {e}')

        self._notify_progress(progress_callback, 88, 'V12 Coordination Detection')
        if current_app.config.get('ENABLE_COORDINATION_DETECTION', True):
            try:
                v12_components['coordination'] = self.coordination_service.analyze(
                    analysis,
                    comments=comment_results_list or None,
                    entities=entities or None,
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Coordination intelligence failed: {e}')

        self._notify_progress(progress_callback, 91, 'V12 Cross-Platform Linking')
        if current_app.config.get('ENABLE_PROPAGATION_INTELLIGENCE', True):
            try:
                v12_components['propagation'] = self.propagation_service.analyze(analysis)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Propagation intelligence failed: {e}')

        self._notify_progress(progress_callback, 94, 'V12 Temporal Intelligence')
        if current_app.config.get('ENABLE_TEMPORAL_INTELLIGENCE', True):
            try:
                v12_components['temporal'] = self.temporal_service.analyze(analysis)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Temporal intelligence failed: {e}')

        self._notify_progress(progress_callback, 97, 'V12 Threat Assessment')
        if current_app.config.get('ENABLE_THREAT_ASSESSMENT', True):
            try:
                self.threat_service.analyze(analysis, components=v12_components)
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(f'Threat assessment failed: {e}')

        return {
            'success': True,
            'analysis_id': analysis.id,
            'is_demo': is_demo,
            'comment_count': len(comments),
            'entity_count': len(entities),
        }

    def _notify_progress(self, callback, percent, step):
        """Safely invoke an optional progress callback.

        A broken callback (e.g., database connection issue) must never fail the
        analysis.  The callback is wrapped in a try/except that logs the error
        and continues.
        """
        if callback is None:
            return
        try:
            callback(percent, step)
        except Exception:
            current_app.logger.warning('Progress callback failed (non-fatal).')

    def _analyze_and_store_comment_v2(self, analysis_id, comment, all_texts):
        text = comment['text']

        sentiment = SentimentService.analyze(text)
        toxicity = ToxicityService.analyze(text)
        spam = SpamService.analyze(text, all_texts)
        bot = BotDetectionService.analyze(text, all_texts)
        duplicate = self.risk_scorer.calculate_duplicate_score(text, all_texts)
        risk = RiskEngine.calculate(sentiment, toxicity, spam, bot, duplicate['score'])

        result = CommentResult(
            analysis_id=analysis_id,
            comment_text=text,
            author=comment.get('author', 'Unknown'),
            published_at=comment.get('published_at'),
            spam_score=spam['spam_score'],
            spam_explanation=spam['explanation'],
            spam_confidence=spam['confidence'],
            toxicity_score=toxicity['toxicity_score'],
            toxicity_explanation=toxicity['explanation'],
            toxicity_confidence=toxicity['confidence'],
            sentiment=sentiment['label'],
            sentiment_score=sentiment['score'],
            sentiment_explanation=sentiment['explanation'],
            sentiment_confidence=sentiment['confidence'],
            duplicate_score=duplicate['score'],
            duplicate_explanation=duplicate['explanation'],
            ai_like_score=bot['bot_score'],
            ai_like_explanation=bot['explanation'],
            bot_score=bot['bot_score'],
            bot_explanation=bot['explanation'],
            bot_confidence=bot['confidence'],
            risk_score=risk['final_risk_score'],
            risk_level=risk['final_risk_label'],
            risk_explanation='; '.join(risk['reasons']),
            recommendation=risk['recommendation'],
        )
        db.session.add(result)
        db.session.commit()

    def _generate_analysis_summary(self, analysis_id):
        comments = self.comment_repo.get_by_analysis_id(analysis_id)
        if not comments:
            return

        total = len(comments)
        sentiments = {'Positive': 0, 'Neutral': 0, 'Negative': 0}
        toxicity_levels = {'Low': 0, 'Medium': 0, 'High': 0, 'Critical': 0}
        spam_levels = {'Low': 0, 'Medium': 0, 'High': 0, 'Critical': 0}
        risk_levels = {'Low': 0, 'Medium': 0, 'High': 0, 'Critical': 0}
        total_sentiment = 0.0
        total_toxicity = 0.0
        total_spam = 0.0
        total_bot = 0.0
        critical_count = 0
        concerns = set()

        for c in comments:
            sentiments[c.sentiment or 'Neutral'] = sentiments.get(c.sentiment, 0) + 1
            total_sentiment += c.sentiment_score or 50.0
            total_toxicity += c.toxicity_score or 0.0
            total_spam += c.spam_score or 0.0
            total_bot += c.bot_score or 0.0

            if c.risk_level == 'Critical':
                critical_count += 1
            if c.risk_level:
                risk_levels[c.risk_level] = risk_levels.get(c.risk_level, 0) + 1

            if c.toxicity_score and c.toxicity_score >= 40:
                concerns.add('Toxic language')
            if c.spam_score and c.spam_score >= 40:
                concerns.add('Spam content')
            if c.bot_score and c.bot_score >= 40:
                concerns.add('Automated content')
            if c.duplicate_score and c.duplicate_score >= 40:
                concerns.add('Duplicate posting')

        avg_sentiment = round(total_sentiment / total, 1) if total else 50.0
        avg_toxicity = round(total_toxicity / total, 1) if total else 0.0
        avg_spam = round(total_spam / total, 1) if total else 0.0
        avg_bot = round(total_bot / total, 1) if total else 0.0

        sentiment_pct = {
            k: round(v / total * 100, 1) if total else 0.0
            for k, v in sentiments.items()
        }

        overall_risk = 'Low'
        avg_risk_score = (avg_toxicity * 0.35 + avg_spam * 0.25 + avg_bot * 0.20 + abs(avg_sentiment - 50) * 0.10)
        if avg_risk_score > 25:
            overall_risk = 'Medium'
        if avg_risk_score > 50:
            overall_risk = 'High'
        if avg_risk_score > 75:
            overall_risk = 'Critical'

        summary_parts = [
            f"Comments analyzed: {total}",
            f"Positive: {sentiment_pct.get('Positive', 0)}%",
            f"Neutral: {sentiment_pct.get('Neutral', 0)}%",
            f"Negative: {sentiment_pct.get('Negative', 0)}%",
            f"Average Toxicity: {avg_toxicity}%",
            f"High Risk Comments: {critical_count}",
            f"Overall Community Health: {overall_risk} Risk",
        ]

        if concerns:
            summary_parts.append("Key Concerns:")
            for concern in sorted(concerns):
                summary_parts.append(f"- {concern}")

        summary = '\n'.join(summary_parts)

        analysis = self.analysis_repo.get_by_id(analysis_id)
        if analysis:
            analysis.average_sentiment = avg_sentiment
            analysis.average_toxicity = avg_toxicity
            analysis.average_spam = avg_spam
            analysis.average_bot_score = avg_bot
            analysis.critical_comments_count = critical_count
            analysis.analysis_summary = summary
            db.session.commit()

    def _analyze_and_store_comment(self, analysis_id, comment, all_texts):
        text = comment['text']

        spam = self.risk_scorer.calculate_spam_score(text)
        toxicity = self.risk_scorer.calculate_toxicity_score(text)
        sentiment = self.risk_scorer.calculate_sentiment(text)
        duplicate = self.risk_scorer.calculate_duplicate_score(text, all_texts)
        ai_like = self.risk_scorer.calculate_ai_like_score(text)
        bot = self.risk_scorer.calculate_bot_score(text, all_texts)
        risk = self.risk_scorer.calculate_risk_score(
            spam['score'], toxicity['score'], sentiment['score'],
            duplicate['score'], ai_like['score'], bot['score']
        )

        result = CommentResult(
            analysis_id=analysis_id,
            comment_text=text,
            author=comment.get('author', 'Unknown'),
            published_at=comment.get('published_at'),
            spam_score=spam['score'],
            spam_explanation=spam['explanation'],
            toxicity_score=toxicity['score'],
            toxicity_explanation=toxicity['explanation'],
            sentiment=sentiment['label'],
            sentiment_score=sentiment['score'],
            sentiment_explanation=sentiment['explanation'],
            duplicate_score=duplicate['score'],
            duplicate_explanation=duplicate['explanation'],
            ai_like_score=ai_like['score'],
            ai_like_explanation=ai_like['explanation'],
            bot_score=bot['score'],
            bot_explanation=bot['explanation'],
            risk_score=risk['score'],
            risk_level=risk['level'],
            risk_explanation=risk['explanation'],
        )
        db.session.add(result)
        db.session.commit()

    def get_analysis_results(self, analysis_id, user_id):
        analysis = self.analysis_repo.get_user_analysis_with_reddit(analysis_id, user_id)
        if not analysis:
            return None

        comments = self.comment_repo.get_by_analysis_id(analysis_id)
        high_risk = self.comment_repo.get_high_risk_by_analysis(analysis_id)
        top_spam = self.comment_repo.get_top_spam_by_analysis(analysis_id, limit=5)
        top_toxic = self.comment_repo.get_top_toxic_by_analysis(analysis_id, limit=5)
        sentiment_dist = self.comment_repo.get_sentiment_distribution(analysis_id)
        risk_dist = self.comment_repo.get_risk_distribution(analysis_id)
        averages = self.comment_repo.get_average_scores_by_analysis(analysis_id)

        youtube = analysis.youtube_analysis
        reddit = analysis.reddit_analysis

        transcript = None
        context_distribution = None
        if youtube and youtube.transcript:
            transcript = youtube.transcript
            context_distribution = self.comment_repo.get_context_distribution(analysis_id)

        entities_list = []
        entity_summary_data_list = {}
        context_intelligence = None
        channel_data = {}
        if youtube or (analysis.analysis_type == 'reddit' and reddit):
            entities_list = Entity.query.filter_by(analysis_id=analysis_id).order_by(Entity.importance_score.desc()).all()
            if entities_list:
                from services.entity_summary_service import EntitySummaryService
                entity_sentiments_list = EntityContext.query.filter(
                    EntityContext.entity_id.in_([e.id for e in entities_list])
                ).with_entities(
                    EntityContext.entity_id,
                    EntityContext.entity_sentiment,
                    EntityContext.entity_sentiment_score,
                ).all()
                entity_risks_list = EntityContext.query.filter(
                    EntityContext.entity_id.in_([e.id for e in entities_list])
                ).with_entities(
                    EntityContext.entity_id,
                    EntityContext.entity_risk_score,
                ).all()
                sent_group = {}
                for eid, sent, score in entity_sentiments_list:
                    if eid not in sent_group:
                        sent_group[eid] = []
                    sent_group[eid].append({'sentiment': sent, 'score': score})
                risk_group = {}
                for eid, score in entity_risks_list:
                    if eid not in risk_group:
                        risk_group[eid] = []
                    risk_group[eid].append(score)
                sentiments_for_summary = []
                for e in entities_list:
                    avg_s = round(sum(s['score'] for s in sent_group.get(e.id, [])) / max(len(sent_group.get(e.id, [])), 1), 1)
                    overall = 'positive' if avg_s > 60 else 'negative' if avg_s < 40 else 'neutral'
                    sentiments_for_summary.append({'entity_name': e.normalized_name, 'overall_sentiment': overall, 'average_score': avg_s})
                risks_for_summary = []
                for e in entities_list:
                    avg_r = round(sum(risk_group.get(e.id, [0])) / max(len(risk_group.get(e.id, [])), 1), 1)
                    risks_for_summary.append({'entity_name': e.normalized_name, 'average_risk_score': avg_r})
                entity_summary_data_list = EntitySummaryService().generate_summary(entities_list, sentiments_for_summary, risks_for_summary)

            if youtube and current_app.config.get('ENABLE_CHANNEL_INTELLIGENCE', True):
                try:
                    channel_id = youtube.channel_name.replace(' ', '_').lower() if youtube.channel_name else 'unknown'
                    context_intel = self.context_intelligence.compute_intelligence(
                        user_id, channel_id, analysis_id,
                        entity_names=[e.normalized_name for e in entities_list],
                        current_sentiment=averages.get('avg_sentiment', 50.0),
                        current_risk=averages.get('avg_risk', 0.0),
                    )
                    channel_obj = self.channel_service.get_channel_intelligence(user_id, channel_id)
                    channel_data = channel_obj
                    context_intelligence = context_intel
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.warning(f'Context intelligence lookup failed: {e}')

        result = {
            'analysis': analysis,
            'comments': comments,
            'high_risk': high_risk,
            'top_spam': top_spam,
            'top_toxic': top_toxic,
            'sentiment_distribution': sentiment_dist,
            'risk_distribution': risk_dist,
            'averages': averages,
            'comment_count': len(comments),
            'transcript': transcript,
            'context_distribution': context_distribution,
            'entity_summary': entity_summary_data_list if youtube or (reddit and analysis.analysis_type == 'reddit') else {},
            'context_intelligence': context_intelligence,
            'channel_data': channel_data,
            'media_analysis': self.authenticity_service.get_media_analysis(analysis_id),
        }

        entity_objects = Entity.query.filter_by(analysis_id=analysis_id).order_by(Entity.importance_score.desc()).limit(20).all()
        result['entities'] = entity_objects if youtube or (reddit and analysis.analysis_type == 'reddit') else []

        if analysis.analysis_type == 'reddit' and reddit:
            result['reddit'] = reddit
            result['is_demo'] = reddit.is_demo
            result['api_error'] = reddit.api_error
            result['youtube'] = None
        elif youtube:
            result['youtube'] = youtube
            result['is_demo'] = youtube.is_demo
            result['api_error'] = youtube.api_error
            result['reddit'] = None
        else:
            return None

        return result

    def get_recent_user_analyses(self, user_id, limit=10):
        return self.analysis_repo.get_recent_by_user(user_id, limit)

    def get_dashboard_stats(self, user_id, platform='all'):
        analyses = self.analysis_repo.get_by_user_id(user_id)
        if platform != 'all':
            analyses = [a for a in analyses if a.analysis_type == platform]
        total_analyses = len(analyses)
        total_comments = 0
        total_spam = 0
        total_toxicity = 0
        total_sentiment = 0
        total_ai = 0
        total_risk = 0
        total_bot = 0
        analysis_count = 0
        critical_count = 0
        transcript_count = 0

        for analysis in analyses:
            averages = self.comment_repo.get_average_scores_by_analysis(analysis.id)
            comment_count = self.comment_repo.count_by_analysis(analysis.id)
            total_comments += comment_count
            critical_count += analysis.critical_comments_count or 0
            if comment_count > 0:
                total_spam += averages['avg_spam']
                total_toxicity += averages['avg_toxicity']
                total_sentiment += averages['avg_sentiment']
                total_ai += averages['avg_ai_like']
                total_risk += averages['avg_risk']
                total_bot += averages.get('avg_bot', 0)
                analysis_count += 1
            yt = analysis.youtube_analysis
            if yt and yt.transcript:
                transcript_count += 1

        entity_count = 0
        entity_risk_total = 0.0
        entity_risk_count = 0
        entity_type_counts = {'PERSON': 0, 'COMPANY': 0, 'PRODUCT': 0, 'LOCATION': 0, 'OTHER': 0}
        entity_risk_low = 0
        entity_risk_medium = 0
        entity_risk_high = 0
        entity_risk_critical = 0
        for analysis in analyses:
            ents = Entity.query.filter_by(analysis_id=analysis.id).all()
            entity_count += len(ents)
            for e in ents:
                et = e.entity_type if e.entity_type in entity_type_counts else 'OTHER'
                entity_type_counts[et] = entity_type_counts.get(et, 0) + e.frequency
            ecs = EntityContext.query.join(Entity).filter(Entity.analysis_id == analysis.id).all()
            for ec in ecs:
                rs = ec.entity_risk_score or 0.0
                entity_risk_total += rs
                entity_risk_count += 1
                if rs > 75:
                    entity_risk_critical += 1
                elif rs > 50:
                    entity_risk_high += 1
                elif rs > 25:
                    entity_risk_medium += 1
                else:
                    entity_risk_low += 1

        community_health = 'Low'
        avg_all_risk = total_risk / max(analysis_count, 1)
        if avg_all_risk > 25:
            community_health = 'Medium'
        if avg_all_risk > 50:
            community_health = 'High'
        if avg_all_risk > 75:
            community_health = 'Critical'

        ai_videos = 0
        authentic_videos = 0
        deepfake_count = 0
        voice_clone_count = 0
        authenticity_total = 0.0
        authenticity_count = 0
        for analysis in analyses:
            ma = analysis.media_analysis
            if ma:
                authenticity_total += ma.overall_authenticity_score or 0.0
                authenticity_count += 1
                if (ma.overall_ai_probability or 0.0) >= 60.0:
                    ai_videos += 1
                elif (ma.overall_authenticity_score or 0.0) >= 60.0:
                    authentic_videos += 1
                if (ma.deepfake_score or 0.0) >= 60.0:
                    deepfake_count += 1
                if (ma.synthetic_voice_score or 0.0) >= 60.0:
                    voice_clone_count += 1

        return {
            'total_analyses': total_analyses,
            'total_comments': total_comments,
            'avg_spam': round(total_spam / max(analysis_count, 1), 1),
            'avg_toxicity': round(total_toxicity / max(analysis_count, 1), 1),
            'avg_sentiment': round(total_sentiment / max(analysis_count, 1), 1),
            'avg_ai_like': round(total_ai / max(analysis_count, 1), 1),
            'avg_bot': round(total_bot / max(analysis_count, 1), 1),
            'avg_risk': round(total_risk / max(analysis_count, 1), 1),
            'critical_comments': critical_count,
            'community_health': community_health,
            'transcript_count': transcript_count,
            'entity_count': entity_count,
            'avg_entity_risk': round(entity_risk_total / max(entity_risk_count, 1), 1),
            'entity_type_counts': entity_type_counts,
            'entity_risk_low': entity_risk_low,
            'entity_risk_medium': entity_risk_medium,
            'entity_risk_high': entity_risk_high,
            'entity_risk_critical': entity_risk_critical,
            'total_videos_analyzed': VideoContextHistory.query.filter_by(user_id=user_id).count(),
            'ai_videos': ai_videos,
            'authentic_videos': authentic_videos,
            'deepfake_count': deepfake_count,
            'voice_clone_count': voice_clone_count,
            'avg_authenticity': round(authenticity_total / max(authenticity_count, 1), 1),
            'authenticity_count': authenticity_count,
            'entity_risk_count': entity_risk_count,
        }

    def get_all_user_analyses_with_data(self, user_id, limit=None):
        analyses = self.analysis_repo.get_by_user_id(user_id)
        if limit:
            analyses = analyses[:limit]
        results = []
        for a in analyses:
            averages = self.comment_repo.get_average_scores_by_analysis(a.id)
            comment_count = self.comment_repo.count_by_analysis(a.id)
            yt = a.youtube_analysis
            reddit = a.reddit_analysis
            if a.analysis_type == 'reddit' and reddit:
                title = reddit.post_title or 'N/A'
                identifier = reddit.post_id
                is_demo = reddit.is_demo
                platform = 'reddit'
            elif yt:
                title = yt.video_title or 'N/A'
                identifier = yt.video_id
                is_demo = yt.is_demo
                platform = 'youtube'
            else:
                title = 'N/A'
                identifier = 'N/A'
                is_demo = False
                platform = 'unknown'
            has_transcript = bool(yt and yt.transcript) if platform == 'youtube' else False
            entity_count = Entity.query.filter_by(analysis_id=a.id).count()
            results.append({
                'id': a.id,
                'title': title,
                'identifier': identifier,
                'platform': platform,
                'comment_count': comment_count,
                'avg_risk': averages['avg_risk'],
                'is_demo': is_demo,
                'has_transcript': has_transcript,
                'entity_count': entity_count,
                'created_at': a.created_at,
            })
        return results

    def _generate_transcript_summary(self, transcript_text):
        if not transcript_text:
            return None
        words = transcript_text.split()
        word_count = len(words)
        if word_count <= 100:
            return transcript_text
        sentences = transcript_text.replace('!', '.').replace('?', '.').split('.')
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        key_sentences = sentences[:5]
        summary = '. '.join(key_sentences)
        if not summary.endswith('.'):
            summary += '.'
        return summary
