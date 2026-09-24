"""V14 Phase D2 - Export batching tests.

Output parity (CSV columns/order, JSON structure), ownership isolation,
comment limits, empty/missing data, bounded query counts independent of
comment volume, and XLSX/PDF/DOCX compatibility.
"""
import csv
import io
import json
import os

import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.comment_context import CommentContext
from models.entity import Entity
from models.entity_context import EntityContext
from models.entity_mention import EntityMention
from models.threat_assessment import ThreatAssessment
from services.export_service import ExportService


def _analysis(user_id):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    db.session.add(YouTubeAnalysis(analysis_id=a.id, video_id=f'vid{a.id}',
                                   video_title='T', channel_name='C',
                                   is_demo=True))
    db.session.commit()
    return a


def _comment(analysis_id, idx, with_related=True):
    c = CommentResult(analysis_id=analysis_id,
                      comment_text=f'stored comment {idx} text', author='a1',
                      sentiment='Positive', sentiment_score=70.0,
                      sentiment_confidence=0.8, sentiment_explanation='se',
                      spam_score=5.0, toxicity_score=6.0, duplicate_score=0.0,
                      ai_like_score=1.0, bot_score=2.0, bot_confidence=0.5,
                      bot_explanation='be', risk_score=15.0, risk_level='Low',
                      risk_explanation='re', recommendation='rec')
    db.session.add(c)
    db.session.flush()
    if with_related:
        db.session.add(CommentContext(
            comment_result_id=c.id, transcript_relevance_score=0.9,
            context_match_label='highly_relevant', reason='why'))
        for k in range(2):
            e = Entity(analysis_id=analysis_id, name=f'Ent{idx}_{k}',
                       normalized_name=f'ent{idx}_{k}', entity_type='company',
                       frequency=1, importance_score=10.0)
            db.session.add(e)
            db.session.flush()
            db.session.add(EntityMention(entity_id=e.id,
                                         comment_result_id=c.id,
                                         mention_text=f'Ent{idx}_{k}'))
            db.session.add(EntityContext(
                entity_id=e.id, comment_result_id=c.id,
                entity_sentiment='positive', entity_sentiment_score=75.0,
                entity_risk_score=20.0, entity_relevance_score=0.5))
    db.session.commit()
    return c


def _seed(n, user_id, related=True):
    a = _analysis(user_id)
    for idx in range(n):
        _comment(a.id, idx, with_related=related)
    return a


def _count_queries():
    from sqlalchemy import event
    state = {'n': 0}

    def _before(conn, cursor, statement, params, context, executemany):
        state['n'] += 1

    return state, _before


def _comment_rows(text):
    rows = list(csv.reader(io.StringIO(text)))
    idx = next(i for i, r in enumerate(rows) if r[:1] == ['Comment'])
    header = rows[idx]
    data = []
    for row in rows[idx + 1:]:
        if len(row) != len(header):
            break
        data.append(row)
    return header, data


class TestCsvParity:
    def test_columns_and_order(self, app, db, user):
        a = _seed(2, user.id)
        text = ExportService().generate_csv(a.id, user.id)['csv_content']
        header, data = _comment_rows(text)
        assert header[:5] == ['Comment', 'Author', 'Published At', 'Sentiment',
                              'Sentiment Score']
        assert 'Entity Risk Scores' in header
        assert 'Historical Context Score' in header
        assert len(data) == 2
        # first data row values from crafted fixtures
        first = data[0]
        assert first[0] == 'stored comment 0 text'
        assert first[25] == 'Ent0_0; Ent0_1'
        assert first[26] == 'company; company'
        assert first[27] == 'positive; positive'
        assert first[28] == '20.0; 20.0'
        assert first[29] == '0.5; 0.5'
        assert first[22] == '0.9'
        assert first[23] == 'Highly Relevant'
        assert first[24] == 'why'

    def test_missing_related_data(self, app, db, user):
        a = _analysis(user.id)
        _comment(a.id, 0, with_related=False)
        text = ExportService().generate_csv(a.id, user.id)['csv_content']
        _header, data = _comment_rows(text)
        assert len(data) == 1
        assert data[0][22] == '' and data[0][25] == '' and data[0][27] == ''

    def test_empty_analysis(self, app, db, user):
        a = _analysis(user.id)
        out = ExportService().generate_csv(a.id, user.id)
        assert out is not None and 'Comment' in out['csv_content']


class TestJsonParity:
    def test_structure_and_values(self, app, db, user):
        a = _seed(2, user.id)
        data = json.loads(ExportService().generate_json(
            a.id, user.id)['json_content'])
        assert set(data) >= {'analysis_id', 'metadata', 'comments', 'v12', 'v13'}
        assert len(data['comments']) == 2
        first = data['comments'][0]
        assert first['comment_text'] == 'stored comment 0 text'
        assert first['context_relevance_score'] == 0.9
        assert first['context_match_label'] == 'Highly Relevant'
        assert first['context_reason'] == 'why'
        assert first['entity_mentions'] == [{'name': 'Ent0_0', 'type': 'company'},
                                            {'name': 'Ent0_1', 'type': 'company'}]
        assert first['entity_sentiments'][0] == {'entity': 'Ent0_0',
                                                 'sentiment': 'positive',
                                                 'score': 75.0}
        assert first['entity_risks'][0] == {'entity': 'Ent0_0', 'risk_score': 20.0}

    def test_missing_related_data(self, app, db, user):
        a = _analysis(user.id)
        _comment(a.id, 0, with_related=False)
        data = json.loads(ExportService().generate_json(
            a.id, user.id)['json_content'])
        first = data['comments'][0]
        assert first['context_relevance_score'] is None
        assert first['entity_mentions'] == []
        assert first['entity_sentiments'] == []
        assert first['entity_risks'] == []

    def test_v12_v13_sections_present(self, app, db, user):
        a = _seed(1, user.id)
        db.session.add(ThreatAssessment(
            analysis_id=a.id, user_id=user.id, overall_threat_score=30.0,
            threat_level='Low', confidence=50.0, evidence_coverage=0.5))
        db.session.commit()
        data = json.loads(ExportService().generate_json(
            a.id, user.id)['json_content'])
        assert data['v12']['threat']['overall_threat_score'] == 30.0
        assert set(data['v13']) >= {'historical_context', 'cross_analysis',
                                    'narrative_evolution', 'evidence'}


class TestQueryBounds:
    def _run_counts(self, user_id, aid, svc):
        from sqlalchemy import event
        results = {}
        for name, fn in (('csv', svc.generate_csv),
                         ('json', svc.generate_json)):
            state, hook = _count_queries()
            event.listen(db.engine, 'before_cursor_execute', hook)
            try:
                assert fn(aid, user_id) is not None
            finally:
                event.remove(db.engine, 'before_cursor_execute', hook)
            results[name] = state['n']
        return results

    def test_counts_bounded_as_comments_grow(self, app, db, user):
        svc = ExportService()
        # Seed both first so each export sees an identical 1-analysis
        # history set; only the target comment count differs.
        small = _seed(10, user.id)
        big = _seed(60, user.id)
        small_counts = self._run_counts(user.id, small.id, svc)
        big_counts = self._run_counts(user.id, big.id, svc)
        assert big_counts['csv'] <= small_counts['csv'] + 6, (small_counts, big_counts)
        assert big_counts['json'] <= small_counts['json'] + 6, (small_counts, big_counts)
        assert big_counts['csv'] < 100 and big_counts['json'] < 100

    def test_comment_limit_preserved(self, app, db, user):
        a = _seed(25, user.id)
        data = json.loads(ExportService().generate_json(
            a.id, user.id)['json_content'])
        assert data['comment_count'] == 25
        assert len(data['comments']) == 25


class TestOwnership:
    def test_cross_user_export_denied(self, app, db, user, client):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='d2other', email='d2other@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        mine = _seed(2, user.id)
        theirs = _seed(2, other.id)
        assert ExportService().generate_json(theirs.id, user.id) is None
        assert ExportService().generate_csv(theirs.id, user.id) is None
        data = json.loads(ExportService().generate_json(
            mine.id, user.id)['json_content'])
        texts = [c['comment_text'] for c in data['comments']]
        assert all('stored comment' in t for t in texts)
        assert len(texts) == 2  # none of the other user's comments leak

    def test_other_user_rows_never_batched(self, app, db, user):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='d2oth2', email='d2oth2@x.com',
                     password_hash=generate_password_hash('Other123'))
        db.session.add(other)
        db.session.commit()
        _seed(3, other.id)
        mine = _seed(1, user.id)
        maps = ExportService()._batched_comment_maps(
            CommentResult.query.filter_by(analysis_id=mine.id).all())
        assert len(maps) == 1


class TestDocFormats:
    @pytest.mark.parametrize('method', ['generate_xlsx', 'generate_pdf',
                                        'generate_docx'])
    def test_binary_exports_work(self, app, db, user, method):
        a = _seed(3, user.id)
        out = getattr(ExportService(), method)(a.id, user.id)
        assert out is not None and os.path.getsize(out['filepath']) > 0
        os.remove(out['filepath'])

    def test_binary_exports_skip_comment_load(self, app, db, user):
        from sqlalchemy import event
        a = _seed(20, user.id)
        svc = ExportService()
        state, hook = _count_queries()
        event.listen(db.engine, 'before_cursor_execute', hook)
        try:
            out = svc.generate_xlsx(a.id, user.id)
            assert out is not None
        finally:
            event.remove(db.engine, 'before_cursor_execute', hook)
            try:
                os.remove(out['filepath'])
            except OSError:
                pass
        assert state['n'] < 60, state['n']

    def test_routes(self, logged_in_client, user, db):
        a = _seed(2, user.id)
        for fmt in ('csv', 'json', 'xlsx', 'pdf', 'docx'):
            assert logged_in_client.get(f'/export/{fmt}/{a.id}').status_code == 200
