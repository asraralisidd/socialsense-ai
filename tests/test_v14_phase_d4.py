"""V14 Phase D4 - V13 read dedup & evidence batching tests.

Query-count reductions, functional parity (every V13 section), batching
semantics, ownership isolation, and the single-analysis 0-links edge.
"""
import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.threat_assessment import ThreatAssessment
from services.historical_context_service import HistoricalContextService
from services.v13_context_service import build_v13_context


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fixture(user_id, history=5, narratives=3, with_chain=True):
    from datetime import timedelta
    now = _now()
    for i in range(history):
        a = Analysis(user_id=user_id, analysis_type='youtube')
        db.session.add(a)
        db.session.flush()
        a.created_at = now - timedelta(days=i + 1)
        db.session.add(CommentResult(analysis_id=a.id, comment_text='c text here',
                                      author='a', sentiment_score=60.0))
        db.session.add(ThreatAssessment(analysis_id=a.id, user_id=user_id,
                                        overall_threat_score=20.0 + i * 5,
                                        threat_level='Low', confidence=50.0))
    cur = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(cur)
    db.session.flush()
    db.session.add(CommentResult(analysis_id=cur.id, comment_text='current c',
                                  author='a', sentiment_score=70.0))
    db.session.add(ThreatAssessment(analysis_id=cur.id, user_id=user_id,
                                    overall_threat_score=35.0,
                                    threat_level='Low', confidence=50.0))
    db.session.add(YouTubeAnalysis(analysis_id=cur.id, video_id=f'vid{cur.id}',
                                   is_demo=True))
    nids = []
    for k in range(narratives):
        n = Narrative(user_id=user_id, name=f'Topic {k}',
                      normalized_name=f'topic {k}', risk_score=40.0)
        db.session.add(n)
        db.session.flush()
        nids.append(n.id)
        db.session.add(NarrativeOccurrence(narrative_id=n.id,
                                            analysis_id=cur.id,
                                            user_id=user_id,
                                            relevance_score=70.0,
                                            occurred_at=now))
        for aid in [a.id for a in Analysis.query.filter(
                Analysis.user_id == user_id, Analysis.id != cur.id).all()][:2]:
            db.session.add(NarrativeOccurrence(
                narrative_id=n.id, analysis_id=aid, user_id=user_id,
                relevance_score=60.0, occurred_at=now - timedelta(days=60)))
    db.session.commit()
    if with_chain:
        from services.v13_evidence_chain_service import V13EvidenceChainService
        V13EvidenceChainService().analyze(cur, components={})
    return cur


def _counted(build):
    from sqlalchemy import event
    stmts = []

    def _c(conn, cur, stmt, params, context, executemany):
        stmts.append(stmt)

    event.listen(db.engine, 'before_cursor_execute', _c)
    try:
        result = build()
        return len(stmts), result
    finally:
        event.remove(db.engine, 'before_cursor_execute', _c)


class TestDedupAndBounds:
    def test_query_count_reduced(self, app, db, user):
        cur = _fixture(user.id, history=5, narratives=3)
        n, _ = _counted(lambda: build_v13_context(cur.id, user.id))
        assert n <= 26, n  # before: 30 at 1/0-history, 19 duped -> now <=26 at 5-history/3-narr

    def test_v13_output_parity(self, app, db, user):
        cur = _fixture(user.id, history=5, narratives=3)
        ctx = build_v13_context(cur.id, user.id)
        assert ctx['baseline'] and ctx['baseline']['metrics']['threat']['baseline'] == pytest.approx(30.0)
        assert ctx['comparison'] and len(ctx['comparison']['historical_analysis_ids']) == 5
        assert ctx['evolution'] and len(ctx['evolution']['narratives']) == 3
        assert ctx['evidence'] and ctx['evidence']['link_count'] >= 1
        for n in ctx['evolution']['narratives']:
            assert n['state'] in ('emerging', 'persistent', 'reappearing',
                                  'declining', 'insufficient_history', 'unavailable')

    def test_evidence_batching_and_stale(self, app, db, user):
        cur = _fixture(user.id, history=0, narratives=1)
        from services.v13_evidence_chain_service import V13EvidenceChainService
        svc = V13EvidenceChainService()
        first = svc.get_analysis_evidence_chain(cur.id)
        assert first is not None and first['link_count'] >= 1
        assessment = None
        n, again = _counted(lambda: svc.get_analysis_evidence_chain(cur.id, assessment=assessment))
        assert again['verified_count'] == first['verified_count']
        # Delete the first verifiable link to force one stale link
        first_verified = next((lnk for lnk in first['links'] if lnk.get('verified')), None)
        if first_verified is not None:
            table, sid = first_verified['source_table'], first_verified['source_id']
            from models.narrative import Narrative as N
            from models.threat_assessment import ThreatAssessment as T
            target = {'narratives': N, 'threat_assessments': T}.get(table)
            if target and sid:
                db.session.delete(db.session.get(target, sid))
                db.session.commit()
                refreshed = svc.get_analysis_evidence_chain(cur.id)
                assert refreshed['stale_count'] >= 1

    def test_ownership_isolation(self, app, db, user):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='d4other', email='d4oth@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        cur = _fixture(user.id, history=3, narratives=2)
        ctx = build_v13_context(cur.id, other.id)
        assert ctx['baseline'] is None or ctx['baseline']['analyses_considered'] == 0
        assert ctx['comparison'] is None or ctx['comparison']['historical_analysis_ids'] == []
        assert ctx['evolution'] is None or ctx['evolution']['narratives'] == []
        assert ctx['evidence'] is not None  # evidence is per-analysis, not per-user; still scoped by analysis_id

    def test_single_analysis_edge(self, app, db, user):
        cur = _fixture(user.id, history=0, narratives=0, with_chain=True)
        ctx = build_v13_context(cur.id, user.id)
        assert ctx['evidence'] is not None
        assert ctx['evolution'] is None or ctx['evolution']['narratives'] == []

    def test_historical_service_prefetch_direct(self, app, db, user):
        _fixture(user.id, history=3, narratives=1)
        cur = Analysis.query.filter_by(user_id=user.id).order_by(
            Analysis.id.desc()).first()
        svc = HistoricalContextService()
        pre = svc.build_prefetch(user.id, cur.id)
        assert pre is not None and len(pre['past_ids']) == 3
        a = svc.compute_baseline(user.id, cur.id, prefetch=pre)
        b = svc.compute_baseline(user.id, cur.id)
        assert a['metrics']['threat'] == b['metrics']['threat']
        assert a['metrics']['sentiment'] == b['metrics']['sentiment']

    def test_unavailable_vs_zero(self, app, db, user):
        cur = _fixture(user.id, history=0, narratives=0)
        ctx = build_v13_context(cur.id, user.id)
        threat = ctx['baseline']['metrics']['threat']
        assert threat['availability'] in ('insufficient_history', 'typical', 'unavailable')
        # threat is available here; a never-analyzed metric would be unavailable
        assert threat['baseline'] is None or isinstance(threat['baseline'], float)

    def test_config_caps(self, app):
        assert app.config['V13_MAX_LINKS_PER_ASSESSMENT'] == 25
        assert app.config['V13_MAX_HISTORY_ANALYSES'] == 20
        assert app.config['V13_HISTORY_WINDOW_DAYS'] == 90
