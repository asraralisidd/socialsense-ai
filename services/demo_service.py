import random
from datetime import datetime, timezone
from services.reddit_service import RedditDemoService


class DemoService:
    DEMO_VIDEOS = [
        {
            'video_id': 'dQw4w9WgXcQ',
            'title': 'Demo Video - Product Review',
            'description': 'A comprehensive review of the latest tech product. We test all features and give our honest opinion.',
            'channel': 'Demo Channel',
            'published_at': datetime(2024, 1, 15, tzinfo=timezone.utc),
            'view_count': 125000,
            'like_count': 8200,
            'comment_count': 25,
        },
        {
            'video_id': 'jNQXAC9IVRw',
            'title': 'Demo Video - Tech Tutorial',
            'description': 'Step by step tutorial showing you how to get started. Perfect for beginners.',
            'channel': 'Demo Tech',
            'published_at': datetime(2024, 3, 22, tzinfo=timezone.utc),
            'view_count': 89000,
            'like_count': 5400,
            'comment_count': 25,
        },
    ]

    DEMO_COMMENTS = [
        {"text": "Great video! Very informative and well presented.", "author": "User123", "published_at": datetime(2024, 1, 16, tzinfo=timezone.utc)},
        {"text": "First comment! Love this content.", "author": "FastViewer99", "published_at": datetime(2024, 1, 15, tzinfo=timezone.utc)},
        {"text": "Check out my channel for similar content! link in bio", "author": "PromoQueen", "published_at": datetime(2024, 1, 17, tzinfo=timezone.utc)},
        {"text": "This is terrible. Worst video I've ever seen. You should delete this.", "author": "Hater2000", "published_at": datetime(2024, 1, 18, tzinfo=timezone.utc)},
        {"text": "Nice video. Thanks for sharing.", "author": "CasualViewer", "published_at": datetime(2024, 1, 19, tzinfo=timezone.utc)},
        {"text": "BUY NOW!!! Limited offer!!! Click the link!!!", "author": "SpamBot_X", "published_at": datetime(2024, 1, 20, tzinfo=timezone.utc)},
        {"text": "I completely agree with your points. Well said.", "author": "ThoughtfulMike", "published_at": datetime(2024, 2, 1, tzinfo=timezone.utc)},
        {"text": "Meh, it was okay I guess.", "author": "NeutralNancy", "published_at": datetime(2024, 2, 3, tzinfo=timezone.utc)},
        {"text": "This changed my life. Thank you so much for making this.", "author": "GratefulFan", "published_at": datetime(2024, 2, 5, tzinfo=timezone.utc)},
        {"text": "I have analyzed the market trends and this aligns with Q4 projections. The strategic implementation shows clear ROI potential.", "author": "BizAnalystPro", "published_at": datetime(2024, 2, 10, tzinfo=timezone.utc)},
        {"text": "Nice try, but this misses the mark completely.", "author": "Critic101", "published_at": datetime(2024, 2, 12, tzinfo=timezone.utc)},
        {"text": "subscribe to my channel please please please please please", "author": "DesperateGamer", "published_at": datetime(2024, 2, 15, tzinfo=timezone.utc)},
        {"text": "Wow just wow amazing content keep it up!", "author": "Enthusiast22", "published_at": datetime(2024, 2, 18, tzinfo=timezone.utc)},
        {"text": "This is so boring I fell asleep watching zzzzz", "author": "SleepyViewer", "published_at": datetime(2024, 3, 1, tzinfo=timezone.utc)},
        {"text": "Great video! Very informative and well presented.", "author": "CloneUser1", "published_at": datetime(2024, 3, 5, tzinfo=timezone.utc)},
        {"text": "Great video! Very informative!", "author": "CloneUser2", "published_at": datetime(2024, 3, 5, tzinfo=timezone.utc)},
        {"text": "I think you should consider the alternative perspective on this topic.", "author": "DebateBro", "published_at": datetime(2024, 3, 8, tzinfo=timezone.utc)},
        {"text": "Click here to win a free iPhone!!! http://spam-link.com", "author": "ScamAlert", "published_at": datetime(2024, 3, 10, tzinfo=timezone.utc)},
        {"text": "I love your channel. Been watching for years. This is definitely one of your best.", "author": "LoyalSub", "published_at": datetime(2024, 3, 12, tzinfo=timezone.utc)},
        {"text": "The production quality is outstanding. The editing and pacing are perfect. The content is well-researched. The presentation is clear. The examples are relevant. The conclusion is compelling.", "author": "OverlyStructured", "published_at": datetime(2024, 3, 15, tzinfo=timezone.utc)},
        {"text": "lol", "author": "LaughingGuy", "published_at": datetime(2024, 3, 18, tzinfo=timezone.utc)},
        {"text": "I disagree with your analysis of the situation.", "author": "DisagreeDan", "published_at": datetime(2024, 3, 20, tzinfo=timezone.utc)},
        {"text": "spam spam spam spam spam spam spam spam spam spam", "author": "SpamBot_Y", "published_at": datetime(2024, 3, 22, tzinfo=timezone.utc)},
        {"text": "Perfect timing! I was just looking for this information.", "author": "LuckyViewer", "published_at": datetime(2024, 3, 25, tzinfo=timezone.utc)},
        {"text": "Not bad, could be better though.", "author": "MehSayer", "published_at": datetime(2024, 3, 28, tzinfo=timezone.utc)},
    ]

    def get_video_info(self):
        video = random.choice(self.DEMO_VIDEOS)
        return {
            'video_id': video['video_id'],
            'title': video['title'],
            'description': video['description'],
            'channel': video['channel'],
            'published_at': video['published_at'],
            'view_count': video['view_count'],
            'like_count': video['like_count'],
            'comment_count': len(self.DEMO_COMMENTS),
            'is_demo': True,
        }

    def get_video_info_by_id(self, video_id):
        return {
            'video_id': video_id,
            'title': f'Demo Video ({video_id})',
            'description': 'This is a demo video used for testing SocialSense AI features.',
            'channel': 'Demo Channel',
            'published_at': datetime(2024, 6, 1, tzinfo=timezone.utc),
            'view_count': 50000,
            'like_count': 3200,
            'comment_count': len(self.DEMO_COMMENTS),
            'is_demo': True,
        }

    def get_comments(self):
        return self.DEMO_COMMENTS

    def get_reddit_subreddit_info(self, subreddit_name):
        return RedditDemoService().get_subreddit_info(subreddit_name)

    def get_reddit_post_info(self, post_id, subreddit=None):
        return RedditDemoService().get_post_info(post_id, subreddit)

    def get_reddit_comments(self):
        return RedditDemoService().get_comments()

    def get_demo_transcript_segments(self):
        return [
            {'start': 0.0, 'end': 4.0, 'text': 'Welcome to this video where we explore the latest technology trends.'},
            {'start': 4.0, 'end': 8.5, 'text': 'Today we are reviewing a brand new product that has generated a lot of discussion.'},
            {'start': 8.5, 'end': 13.0, 'text': 'The product features an innovative design with high quality materials.'},
            {'start': 13.0, 'end': 18.0, 'text': 'Many users have reported positive experiences with the battery life.'},
            {'start': 18.0, 'end': 23.5, 'text': 'However there are some concerns about the pricing strategy.'},
            {'start': 23.5, 'end': 28.0, 'text': 'The customer support team has been responsive to feedback.'},
            {'start': 28.0, 'end': 33.0, 'text': 'Overall this product represents a significant step forward in the industry.'},
            {'start': 33.0, 'end': 38.0, 'text': 'We recommend watching the full review for more detailed analysis.'},
        ]

    def get_demo_transcript_full_text(self):
        return ' '.join(s['text'] for s in self.get_demo_transcript_segments())

    def get_demo_keywords(self):
        return [('technology', 8), ('product', 6), ('review', 4), ('design', 3), ('quality', 3),
                ('battery', 3), ('pricing', 2), ('support', 2), ('feedback', 2), ('industry', 2)]

    def get_demo_topics(self):
        return ['technology', 'product review', 'design', 'battery life', 'pricing']

    def get_demo_entities(self):
        return [
            {'name': 'Elon Musk', 'normalized_name': 'Elon Musk', 'entity_type': 'PERSON', 'source': 'combined', 'frequency': 8, 'importance_score': 95.0, 'relevance_score': 78.5, 'relationship': 'discussed_across_sources'},
            {'name': 'Tesla', 'normalized_name': 'Tesla', 'entity_type': 'COMPANY', 'source': 'combined', 'frequency': 6, 'importance_score': 80.0, 'relevance_score': 65.0, 'relationship': 'discussed_across_sources'},
            {'name': 'SpaceX', 'normalized_name': 'SpaceX', 'entity_type': 'COMPANY', 'source': 'transcript', 'frequency': 3, 'importance_score': 45.0, 'relevance_score': 35.0, 'relationship': 'discussed_in_transcript'},
            {'name': 'Model 3', 'normalized_name': 'Model 3', 'entity_type': 'PRODUCT', 'source': 'comment', 'frequency': 4, 'importance_score': 55.0, 'relevance_score': 42.0, 'relationship': 'discussed_in_comments'},
            {'name': 'Joe Rogan', 'normalized_name': 'Joe Rogan', 'entity_type': 'PERSON', 'source': 'transcript', 'frequency': 2, 'importance_score': 30.0, 'relevance_score': 22.0, 'relationship': 'discussed_in_transcript'},
        ]

    def get_demo_entity_sentiments(self):
        return [
            {'entity_name': 'Elon Musk', 'overall_sentiment': 'positive', 'average_score': 72.0},
            {'entity_name': 'Tesla', 'overall_sentiment': 'positive', 'average_score': 78.0},
            {'entity_name': 'SpaceX', 'overall_sentiment': 'positive', 'average_score': 65.0},
            {'entity_name': 'Model 3', 'overall_sentiment': 'neutral', 'average_score': 52.0},
            {'entity_name': 'Joe Rogan', 'overall_sentiment': 'neutral', 'average_score': 55.0},
        ]

    def get_demo_entity_risks(self):
        return [
            {'entity_name': 'Elon Musk', 'average_risk_score': 35.0},
            {'entity_name': 'Tesla', 'average_risk_score': 20.0},
            {'entity_name': 'SpaceX', 'average_risk_score': 5.0},
            {'entity_name': 'Model 3', 'average_risk_score': 10.0},
            {'entity_name': 'Joe Rogan', 'average_risk_score': 15.0},
        ]

    def get_demo_entity_summary(self):
        return {
            'total_entities': 5,
            'most_discussed_person': 'Elon Musk',
            'most_discussed_company': 'Tesla',
            'most_discussed_product': 'Model 3',
            'most_controversial_entity': 'Elon Musk',
            'most_positive_entity': 'Tesla',
            'most_negative_entity': 'Joe Rogan',
            'entity_type_distribution': {'PERSON': 10, 'COMPANY': 9, 'PRODUCT': 4},
        }

    def get_demo_entity_hourly(self):
        return [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 4, 2]

    def get_demo_entity_type_counts(self):
        return [{'type': 'PERSON', 'count': 10}, {'type': 'COMPANY', 'count': 9}, {'type': 'PRODUCT', 'count': 4}]

    def get_demo_channel_intelligence(self):
        return {
            'total_videos_analyzed': 3,
            'total_entities_detected': 15,
            'avg_sentiment': 62.5,
            'avg_risk': 18.3,
        }

    def get_demo_context_intelligence(self):
        return {
            'historical_context': 85,
            'entity_recurrence': 72,
            'topic_recurrence': 68,
            'channel_relevance': 78,
            'trend_score': 62,
            'trend_direction': 'positive',
            'channel_data': {
                'total_videos_analyzed': 3,
                'total_entities_detected': 15,
                'avg_sentiment': 62.5,
                'avg_risk': 18.3,
            },
            'trends': {
                'previous_video_count': 2,
                'sentiment_trend': 'improving',
                'risk_trend': 'improving',
                'avg_sentiment_change': 12.0,
                'avg_risk_change': -8.0,
                'entity_count_trend': 3,
            },
            'top_entities': [
                {'normalized_name': 'Tesla', 'entity_type': 'COMPANY', 'appearances': 3, 'avg_sentiment': 72.0, 'avg_risk': 15.0},
                {'normalized_name': 'Elon Musk', 'entity_type': 'PERSON', 'appearances': 3, 'avg_sentiment': 65.0, 'avg_risk': 30.0},
                {'normalized_name': 'SpaceX', 'entity_type': 'COMPANY', 'appearances': 2, 'avg_sentiment': 68.0, 'avg_risk': 5.0},
            ],
            'recurring_topics': [
                {'topic': 'technology', 'appearances': 3},
                {'topic': 'product review', 'appearances': 2},
                {'topic': 'innovation', 'appearances': 2},
            ],
        }

    def get_demo_trend_statistics(self):
        return {
            'previous_video_count': 2,
            'sentiment_trend': 'improving',
            'risk_trend': 'improving',
            'avg_sentiment_change': 12.0,
            'avg_risk_change': -8.0,
            'entity_count_trend': 3,
        }

    def get_demo_video_history(self):
        return [
            {
                'video_id': 'abc123',
                'video_title': 'Tesla Earnings Report',
                'entity_count': 8,
                'avg_sentiment': 58.0,
                'avg_risk': 22.0,
            },
            {
                'video_id': 'def456',
                'video_title': 'Tesla Launch Event',
                'entity_count': 6,
                'avg_sentiment': 65.0,
                'avg_risk': 15.0,
            },
        ]

    def get_demo_entity_history(self):
        return [
            {'normalized_name': 'Tesla', 'entity_type': 'COMPANY', 'appearances': 3, 'avg_sentiment': 72.0, 'avg_risk': 15.0},
            {'normalized_name': 'Elon Musk', 'entity_type': 'PERSON', 'appearances': 3, 'avg_sentiment': 65.0, 'avg_risk': 30.0},
            {'normalized_name': 'SpaceX', 'entity_type': 'COMPANY', 'appearances': 2, 'avg_sentiment': 68.0, 'avg_risk': 5.0},
            {'normalized_name': 'Model 3', 'entity_type': 'PRODUCT', 'appearances': 2, 'avg_sentiment': 52.0, 'avg_risk': 10.0},
        ]

    def get_demo_video_comparison(self):
        return {
            'has_history': True,
            'sentiment_vs_avg': 12.5,
            'risk_vs_avg': -8.0,
            'entity_count_vs_avg': 2.0,
            'channel_avg_sentiment': 58.0,
            'channel_avg_risk': 20.0,
            'channel_avg_entity_count': 6.0,
        }

    def get_demo_authenticity(self):
        """Return demo authenticity scenarios. Clearly labeled simulated."""
        from services.authenticity_service import AuthenticityService
        return AuthenticityService().get_demo_authenticity()
