from database import db
from models.scheduled_report import ScheduledReport
from repositories.scheduled_report_repository import ScheduledReportRepository
from repositories.analysis_repository import AnalysisRepository
from services.notification_service import NotificationService
from models.notification import Notification
from datetime import datetime, timezone, timedelta
import json
import csv
import os


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ReportGenerationService:
    def __init__(self):
        self.report_repo = ScheduledReportRepository()
        self.analysis_repo = AnalysisRepository()
        self.notification_service = NotificationService()

    def generate_report(self, report_id, app):
        report = self.report_repo.get_by_id(report_id)
        if not report:
            return None

        from services.trend_service import TrendService
        trend = TrendService()
        data = trend.get_trends(report.user_id, days=30, platform=report.platform_filter)

        from services.analysis_service import AnalysisService
        analysis_service = AnalysisService()
        stats = analysis_service.get_dashboard_stats(report.user_id, platform=report.platform_filter or 'all')

        from models.comment_result import CommentResult
        from models.analysis import Analysis
        from sqlalchemy import desc
        risky = CommentResult.query.join(Analysis).filter(
            Analysis.user_id == report.user_id,
            CommentResult.risk_level.in_(['High', 'Critical']),
        ).order_by(desc(CommentResult.risk_score)).limit(10).all()

        channel_data = self._get_channel_report_data(report.user_id)

        report_data = {
            'generated_at': _now().isoformat(),
            'report_type': report.report_type,
            'platform_filter': report.platform_filter,
            'total_analyses': stats.get('total_analyses', 0) or 0,
            'total_comments': stats.get('total_comments', 0) or 0,
            'avg_risk': stats.get('avg_risk', 0) or 0,
            'avg_toxicity': stats.get('avg_toxicity', 0) or 0,
            'avg_spam': stats.get('avg_spam', 0) or 0,
            'trends': data,
            'entity_intelligence': self._get_entity_report_data(report.user_id),
            'channel_intelligence': channel_data,
            'authenticity_intelligence': self._get_authenticity_report_data(report.user_id),
            'v13_historical_context': self._get_v13_report_data(report.user_id),
            'top_risky_comments': [{
                'text': c.comment_text[:200] if c.comment_text else '',
                'risk_score': c.risk_score,
                'risk_level': c.risk_level,
                'author': c.author,
            } for c in risky],
        }

        upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join(app.root_path, 'reports'))
        os.makedirs(upload_folder, exist_ok=True)

        filename = f'report_{report.id}_{_now().strftime("%Y%m%d_%H%M%S")}'
        file_path = None

        if report.report_format == ScheduledReport.FORMAT_JSON:
            file_path = os.path.join(upload_folder, f'{filename}.json')
            with open(file_path, 'w') as f:
                json.dump(report_data, f, indent=2)
        elif report.report_format == ScheduledReport.FORMAT_CSV:
            file_path = os.path.join(upload_folder, f'{filename}.csv')
            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Metric', 'Value'])
                for k, v in report_data.items():
                    if isinstance(v, (int, float, str)):
                        writer.writerow([k, v])
        else:
            file_path = os.path.join(upload_folder, f'{filename}.html')
            html = self._render_html(report_data)
            with open(file_path, 'w') as f:
                f.write(html)

        report.last_file_path = file_path
        self.report_repo.update_next_run(report.id)

        self.notification_service.create(
            report.user_id, Notification.TYPE_REPORT_GENERATED,
            'Report Generated',
            f'Your {report.report_type} report is ready.',
            Notification.SEVERITY_SUCCESS,
        )

        return report_data

    def _get_entity_report_data(self, user_id):
        from models.entity import Entity
        from models.analysis import Analysis
        entities = Entity.query.join(Analysis).filter(Analysis.user_id == user_id).all()
        type_dist = {}
        total_risk = 0.0
        risk_count = 0
        for e in entities:
            type_dist[e.entity_type] = type_dist.get(e.entity_type, 0) + e.frequency
        from models.entity_context import EntityContext
        ecs = EntityContext.query.join(Entity).join(Analysis).filter(Analysis.user_id == user_id).all()
        for ec in ecs:
            total_risk += ec.entity_risk_score or 0.0
            risk_count += 1
        return {
            'total_entities': len(entities),
            'entity_type_distribution': type_dist,
            'avg_entity_risk': round(total_risk / max(risk_count, 1), 1),
        }

    def _get_channel_report_data(self, user_id):
        from models.channel_context import ChannelContext
        from models.video_context_history import VideoContextHistory
        from models.entity_history import EntityHistory
        channels = ChannelContext.query.filter_by(user_id=user_id).all()
        total_videos = sum(c.total_videos_analyzed or 0 for c in channels)
        total_entities = sum(c.total_entities_detected or 0 for c in channels)
        channel_list = []
        for c in channels:
            histories = VideoContextHistory.query.filter_by(
                user_id=user_id, channel_id=c.channel_id
            ).order_by(VideoContextHistory.processed_at.desc()).limit(5).all()
            top_entities_list = EntityHistory.query.filter_by(
                user_id=user_id, channel_id=c.channel_id
            ).with_entities(
                EntityHistory.normalized_name,
                EntityHistory.entity_type,
            ).distinct().limit(5).all()
            channel_list.append({
                'channel_name': c.channel_name or c.channel_id,
                'total_videos': c.total_videos_analyzed or 0,
                'total_entities': c.total_entities_detected or 0,
                'avg_sentiment': c.avg_sentiment or 0.0,
                'avg_risk': c.avg_risk or 0.0,
                'recent_videos': [
                    {'title': h.video_title, 'sentiment': h.avg_sentiment, 'risk': h.avg_risk}
                    for h in histories
                ],
                'top_entities': [
                    {'name': e.normalized_name, 'type': e.entity_type}
                    for e in top_entities_list
                ],
            })
        return {
            'total_channels': len(channels),
            'total_videos_analyzed': total_videos,
            'total_entities_detected': total_entities,
            'channels': channel_list,
        }

    def _get_authenticity_report_data(self, user_id):
        from models.analysis import Analysis
        from models.media_analysis import MediaAnalysis
        media_rows = MediaAnalysis.query.join(Analysis).filter(Analysis.user_id == user_id).all()
        total = len(media_rows)
        ai_videos = sum(1 for m in media_rows if (m.overall_ai_probability or 0.0) >= 60.0)
        authentic_videos = sum(1 for m in media_rows if (m.overall_authenticity_score or 0.0) >= 60.0)
        deepfake_count = sum(1 for m in media_rows if (m.deepfake_score or 0.0) >= 60.0)
        voice_clone_count = sum(1 for m in media_rows if (m.synthetic_voice_score or 0.0) >= 60.0)
        avg_authenticity = sum(m.overall_authenticity_score or 0.0 for m in media_rows) / max(total, 1)
        avg_ai = sum(m.overall_ai_probability or 0.0 for m in media_rows) / max(total, 1)
        return {
            'total_media_analyzed': total,
            'ai_videos': ai_videos,
            'authentic_videos': authentic_videos,
            'deepfake_count': deepfake_count,
            'voice_clone_count': voice_clone_count,
            'avg_authenticity': round(avg_authenticity, 1),
            'avg_ai_probability': round(avg_ai, 1),
        }

    def _get_v13_report_data(self, user_id):
        """Bounded V13 summary for the user's latest analysis.

        Single shared ``build_v13_context`` call (no duplicated logic).
        Returns None when no analysis exists or V13 is unavailable, so the
        report renders an unavailable state instead of fabricated data.
        """
        from models.analysis import Analysis
        latest = Analysis.query.filter_by(user_id=user_id).order_by(
            Analysis.created_at.desc(), Analysis.id.desc()).first()
        if not latest:
            return None
        try:
            from services.v13_context_service import build_v13_context
            context = build_v13_context(latest.id, user_id)
        except Exception:
            return None
        baseline = (context.get('baseline') or {}).get('metrics') or {}
        comparison = context.get('comparison') or {}
        evolution = context.get('evolution') or {}
        evidence = context.get('evidence') or {}
        return {
            'latest_analysis_id': latest.id,
            'metrics': {
                key: {
                    'current': m.get('current'),
                    'baseline': m.get('baseline'),
                    'deviation': m.get('deviation', m.get('delta')),
                    'sample_size': m.get('sample_size'),
                    'availability': m.get('availability'),
                }
                for key, m in baseline.items()
            },
            'historical_analysis_ids':
                comparison.get('historical_analysis_ids') or [],
            'window_days': comparison.get('window_days'),
            'narratives': [
                {
                    'name': n.get('name') or n.get('normalized_name'),
                    'state': n.get('state'),
                    'current_occurrences': n.get('current_occurrences'),
                    'prior_occurrences': n.get('prior_occurrences'),
                    'recent_occurrences': n.get('recent_occurrences'),
                    'first_seen_at': n.get('first_seen_at'),
                    'last_seen_at': n.get('last_seen_at'),
                }
                for n in (evolution.get('narratives') or [])[:20]
            ],
            'evidence_links': len(evidence.get('links') or []),
            'evidence_verified': evidence.get('verified_count'),
        }

    def _render_v13_html(self, v13):
        """Bounded V13 report section; unavailable renders explicitly."""
        import html as _html

        def _esc(value):
            return _html.escape(str(value)) if value is not None else 'Unavailable'

        def _num(value):
            return f'{float(value):.1f}' if value is not None else 'Unavailable'

        if not v13:
            return ('<h2>V13 Historical Context (heuristic)</h2>'
                    '<p>Unavailable &mdash; no analysis available.</p>')
        parts = ['<h2>V13 Historical Context (heuristic, non-causal)</h2>']
        parts.append(f"<p>Latest analysis: {v13.get('latest_analysis_id', 'Unavailable')} &middot; "
                     f"Window: {_esc(v13.get('window_days'))} days &middot; "
                     f"Historical analyses: {len(v13.get('historical_analysis_ids') or [])}</p>")
        metrics = v13.get('metrics') or {}
        if metrics:
            parts.append('<table><tr><th>Metric</th><th>Current</th><th>Historical</th>'
                         '<th>Change</th><th>Sample</th><th>Status</th></tr>')
            for key in sorted(metrics):
                m = metrics[key]
                parts.append(
                    f"<tr><td>{_esc(m.get('metric', key))}</td>"
                    f"<td>{_num(m.get('current'))}</td>"
                    f"<td>{_num(m.get('baseline'))}</td>"
                    f"<td>{_num(m.get('deviation'))}</td>"
                    f"<td>{m.get('sample_size') if m.get('sample_size') is not None else 'Unavailable'}</td>"
                    f"<td>{_esc(m.get('availability'))}</td></tr>")
            parts.append('</table>')
        narratives = v13.get('narratives') or []
        if narratives:
            parts.append('<h2>V13 Narrative Evolution (observed, non-causal)</h2>')
            parts.append('<table><tr><th>Narrative</th><th>State</th><th>Current</th>'
                         '<th>Prior</th><th>Recent</th><th>First Seen</th><th>Last Seen</th></tr>')
            for n in narratives:
                parts.append(
                    f"<tr><td>{_esc(n.get('name'))}</td>"
                    f"<td>{_esc(n.get('state'))}</td>"
                    f"<td>{n.get('current_occurrences')}</td>"
                    f"<td>{n.get('prior_occurrences')}</td>"
                    f"<td>{n.get('recent_occurrences')}</td>"
                    f"<td>{_esc(n.get('first_seen_at'))}</td>"
                    f"<td>{_esc(n.get('last_seen_at'))}</td></tr>")
            parts.append('</table>')
        parts.append(f"<p>Evidence links: {v13.get('evidence_links', 'Unavailable')} "
                     f"({v13.get('evidence_verified', 'Unavailable')} verified). "
                     f"Associations are descriptive and do not prove causation.</p>")
        return '\n'.join(parts)

    def _render_html(self, data):
        v13_section = self._render_v13_html(data.get('v13_historical_context'))
        return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SocialSense AI Report</title>
<style>
body {{ font-family: system-ui; background: #1a1d23; color: #e9ecef; padding: 2rem; max-width: 800px; margin: auto; }}
h1, h2 {{ color: #0d6efd; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
th, td {{ padding: 0.5rem; text-align: left; border-bottom: 1px solid #2c3034; }}
th {{ color: #adb5bd; }}
.risk-high {{ color: #dc3545; }}
.risk-critical {{ color: #dc3545; font-weight: bold; }}
</style></head><body>
<h1>SocialSense AI Report</h1>
<p>Generated: {data['generated_at']}</p>
<h2>Summary</h2>
<table>
<tr><th>Total Analyses</th><td>{data['total_analyses']}</td></tr>
<tr><th>Total Comments</th><td>{data['total_comments']}</td></tr>
<tr><th>Average Risk</th><td>{data['avg_risk']}</td></tr>
<tr><th>Average Toxicity</th><td>{data['avg_toxicity']}</td></tr>
<tr><th>Average Spam</th><td>{data['avg_spam']}</td></tr>
</table>
<h2>Top Risky Comments</h2>
<table>
<tr><th>Author</th><th>Comment</th><th>Risk</th></tr>
{"".join(f'<tr><td>{c["author"]}</td><td>{c["text"][:100]}</td><td class="risk-{c["risk_level"].lower()}">{c["risk_score"]}</td></tr>' for c in data['top_risky_comments'])}
</table>
<h2>Entity Intelligence</h2>
<table>
<tr><th>Total Entities</th><td>{data.get('entity_intelligence', {}).get('total_entities', 0)}</td></tr>
<tr><th>Average Entity Risk</th><td>{data.get('entity_intelligence', {}).get('avg_entity_risk', 0)}</td></tr>
</table>
<h2>Channel Intelligence</h2>
<table>
<tr><th>Total Channels</th><td>{data.get('channel_intelligence', {}).get('total_channels', 0)}</td></tr>
<tr><th>Total Videos Analyzed</th><td>{data.get('channel_intelligence', {}).get('total_videos_analyzed', 0)}</td></tr>
<tr><th>Total Entities Detected</th><td>{data.get('channel_intelligence', {}).get('total_entities_detected', 0)}</td></tr>
</table>
<h2>Authenticity Intelligence (heuristic)</h2>
<table>
<tr><th>Media Analyzed</th><td>{data.get('authenticity_intelligence', {}).get('total_media_analyzed', 0)}</td></tr>
<tr><th>AI Videos</th><td>{data.get('authenticity_intelligence', {}).get('ai_videos', 0)}</td></tr>
<tr><th>Authentic Videos</th><td>{data.get('authenticity_intelligence', {}).get('authentic_videos', 0)}</td></tr>
<tr><th>Deepfake Risk Count</th><td>{data.get('authenticity_intelligence', {}).get('deepfake_count', 0)}</td></tr>
<tr><th>Voice Clone Count</th><td>{data.get('authenticity_intelligence', {}).get('voice_clone_count', 0)}</td></tr>
<tr><th>Avg Authenticity</th><td>{data.get('authenticity_intelligence', {}).get('avg_authenticity', 0)}</td></tr>
<tr><th>Avg AI Probability</th><td>{data.get('authenticity_intelligence', {}).get('avg_ai_probability', 0)}</td></tr>
</table>
{v13_section}
<p style="text-align:center;margin-top:2rem;color:#6c757d;">Generated by SocialSense AI v11.0</p>
</body></html>'''

    def process_due_reports(self, app):
        due = self.report_repo.get_due_reports()
        count = 0
        for report in due:
            self.generate_report(report.id, app)
            count += 1
        return count
