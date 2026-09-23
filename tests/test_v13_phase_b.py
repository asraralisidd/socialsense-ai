"""V13 Phase B - Historical Foundation & Evidence Provenance tests.

Backend only (no UI/export): evidence-chain creation/persistence/re-read,
bounds, idempotent re-analysis, v13_001 migration round-trip, bounded
historical queries incl. NULL-timestamp semantics, baseline calculation,
insufficient/unavailable handling, deviation classification, config bounds,
and V12 compatibility.
"""
from datetime import datetime, timedelta, timezone

import pytest

from database import db
from models.analysis import Analysis, YouTubeAnalysis
from models.comment_result import CommentResult
from models.coordination_signal import CoordinationSignal
from models.media_analysis import MediaAnalysis
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment
from repositories.temporal_repository import TemporalRepository
from services.historical_context_service import HistoricalContextService
from services.v13_evidence_chain_service import V13EvidenceChainService


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _analysis(user_id, days_ago=None, with_comments=True, threat_score=None):
    a = Analysis(user_id=user_id, analysis_type='youtube')
    db.session.add(a)
    db.session.flush()
    if days_ago is not None:
        a.created_at = _now() - timedelta(days=days_ago)
    db.session.add(YouTubeAnalysis(analysis_id=a.id, video_id=f'vid{a.id}'))
    if with_comments:
        for i in range(3):
            db.session.add(CommentResult(
                analysis_id=a.id, comment_text=f'comment {i} text here',
                author=f'author{i}', sentiment_score=60.0 + i,
                toxicity_score=5.0, spam_score=10.0, duplicate_score=0.0))
    if threat_score is not None:
        db.session.add(ThreatAssessment(
            analysis_id=a.id, user_id=user_id,
            overall_threat_score=threat_score, threat_level='Low',
            confidence=50.0, evidence_coverage=0.5))
    db.session.commit()
    return a


def _v12_rows(user_id, analysis_id):
    n = Narrative(user_id=user_id, name='Test Narrative',
                  normalized_name='test narrative', risk_score=40.0)
    db.session.add(n)
    db.session.flush()
    occ = NarrativeOccurrence(narrative_id=n.id, analysis_id=analysis_id,
                              user_id=user_id, relevance_score=70.0)
    sig = CoordinationSignal(analysis_id=analysis_id, user_id=user_id,
                             signal_type='repeated_content', score=55.0)
    db.session.add_all([occ, sig])
    db.session.flush()
    return n, occ, sig


def _comment(user_id, analysis_id):
    c = CommentResult(analysis_id=analysis_id, comment_text='real stored text',
                      author='someone')
    db.session.add(c)
    db.session.commit()
    return c


def _live_components(comment_id):
    return {
        'narrative': {
            'available': True,
            'evidence': {'samples': [
                {'source': 'comment', 'ref': f'comment:{comment_id}',
                 'snippet': 'real stored text', 'risk_score': 44.0},
                {'source': 'comment', 'ref': 'comment:999999',
                 'snippet': 'invented ref must not persist', 'risk_score': 90.0},
            ]},
            'reasons': ['lexicon hit'],
        },
        'coordination': {'available': False},
        'propagation': {'available': False},
        'temporal': {'available': False},
        'authenticity': {'available': False},
        'entity': {'available': False},
    }


# --------------------------------------------------------------------------
# 1-3. creation / persistence / DB re-read
# --------------------------------------------------------------------------
class TestEvidenceChainCore:
    def test_creation_from_live_and_persisted(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        n, occ, sig = _v12_rows(user.id, a.id)
        c = _comment(user.id, a.id)
        svc = V13EvidenceChainService()
        result = svc.analyze(a, components=_live_components(c.id))
        assert result['available'] is True
        assert result['link_count'] > 0
        tables = {link['source_table'] for link in result['links']}
        assert 'threat_assessments' in tables
        assert 'narratives' in tables
        assert 'comment_results' in tables
        for link in result['links']:
            assert set(link) >= {'claim', 'component', 'source_table',
                                 'source_id', 'ref', 'snippet', 'score',
                                 'evidence_type'}
            assert link['claim'] and link['component'] and link['evidence_type']

    def test_persisted_to_assessment_row(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        _v12_rows(user.id, a.id)
        c = _comment(user.id, a.id)
        V13EvidenceChainService().analyze(a, components=_live_components(c.id))
        db.session.expire_all()
        row = ThreatAssessment.query.filter_by(analysis_id=a.id).first()
        assert isinstance(row.evidence_refs, list) and len(row.evidence_refs) > 0
        assert 'evidence_refs' in row.to_dict()

    def test_db_reread_reconstructs_chain(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        _v12_rows(user.id, a.id)
        c = _comment(user.id, a.id)
        svc = V13EvidenceChainService()
        first = svc.analyze(a, components=_live_components(c.id))
        db.session.expire_all()
        reread = svc.get_analysis_evidence_chain(a.id)
        assert reread is not None and reread['available'] is True
        assert reread['link_count'] == first['link_count']
        assert reread['verified_count'] == reread['link_count']
        assert reread['stale_count'] == 0

    def test_invented_ids_never_persist(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        c = _comment(user.id, a.id)
        result = V13EvidenceChainService().analyze(
            a, components=_live_components(c.id))
        refs = [link['ref'] for link in result['links']]
        assert 'comment:999999' not in refs
        for link in result['links']:
            if link['source_table'] in ('comment_results', 'narratives',
                                        'threat_assessments'):
                assert isinstance(link['source_id'], int)


# --------------------------------------------------------------------------
# 4-6. missing evidence / unavailable / bounds
# --------------------------------------------------------------------------
class TestEvidenceChainSafety:
    def test_missing_assessment_row_is_unavailable(self, app, db, user):
        a = _analysis(user.id)  # no ThreatAssessment row
        result = V13EvidenceChainService().analyze(a, components={})
        assert result['available'] is False
        assert result['links'] == []

    def test_unavailable_components_contribute_nothing(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        result = V13EvidenceChainService().analyze(a, components={
            'narrative': {'available': False},
            'coordination': {'available': False},
        })
        assert result['available'] is True
        kinds = {link['component'] for link in result['links']}
        assert 'coordination' not in kinds
        # only persisted-row links (threat assessment itself)
        assert result['link_count'] >= 1

    def test_none_analysis_is_unavailable(self, app, db, user):
        result = V13EvidenceChainService().analyze(None, components={})
        assert result['available'] is False

    def test_disabled_flag_skips_stage(self, app, db, user):
        app.config['ENABLE_V13_EVIDENCE_CHAIN'] = False
        a = _analysis(user.id, threat_score=30.0)
        result = V13EvidenceChainService().analyze(a, components={})
        assert result['available'] is False
        row = ThreatAssessment.query.filter_by(analysis_id=a.id).first()
        assert row.evidence_refs is None

    def test_links_bounded(self, app, db, user):
        app.config['V13_MAX_LINKS_PER_ASSESSMENT'] = 2
        a = _analysis(user.id, threat_score=30.0)
        _v12_rows(user.id, a.id)
        c = _comment(user.id, a.id)
        result = V13EvidenceChainService().analyze(
            a, components=_live_components(c.id))
        assert result['link_count'] <= 2
        assert result['truncated'] is True

    def test_snippet_bounded(self, app, db, user):
        app.config['V13_EVIDENCE_SNIPPET_CHARS'] = 20
        a = _analysis(user.id, threat_score=30.0)
        c = _comment(user.id, a.id)
        result = V13EvidenceChainService().analyze(
            a, components=_live_components(c.id))
        for link in result['links']:
            if link['snippet'] is not None:
                assert len(link['snippet']) <= 20

    def test_deterministic_ordering(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        _v12_rows(user.id, a.id)
        c = _comment(user.id, a.id)
        svc = V13EvidenceChainService()
        first = svc.analyze(a, components=_live_components(c.id))
        second = svc.analyze(a, components=_live_components(c.id))
        assert first['links'] == second['links']


# --------------------------------------------------------------------------
# 7. idempotent re-analysis
# --------------------------------------------------------------------------
class TestEvidenceChainIdempotency:
    def test_rerun_overwrites_without_duplicates(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        _v12_rows(user.id, a.id)
        c = _comment(user.id, a.id)
        svc = V13EvidenceChainService()
        first = svc.analyze(a, components=_live_components(c.id))
        second = svc.analyze(a, components=_live_components(c.id))
        assert second['link_count'] == first['link_count']
        assert ThreatAssessment.query.filter_by(analysis_id=a.id).count() == 1

    def test_pipeline_hook_does_not_break_v12(self, app, db, user):
        from services.analysis_service import AnalysisService
        app.config['YOUTUBE_API_KEY'] = ''
        out = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=10)
        assert out['success'] is True
        row = ThreatAssessment.query.filter_by(
            analysis_id=out['analysis_id']).first()
        assert row is not None  # V12 threat stage intact

    def test_pipeline_hook_disabled_leaves_v12_unchanged(self, app, db, user):
        from services.analysis_service import AnalysisService
        app.config['YOUTUBE_API_KEY'] = ''
        app.config['ENABLE_V13_EVIDENCE_CHAIN'] = False
        out = AnalysisService().create_youtube_analysis(
            user.id, 'dQw4w9WgXcQ', comment_limit=10)
        assert out['success'] is True
        row = ThreatAssessment.query.filter_by(
            analysis_id=out['analysis_id']).first()
        assert row is not None and row.evidence_refs is None


# --------------------------------------------------------------------------
# 8-9. migration round-trip (isolated file DB, never live PG)
# --------------------------------------------------------------------------
class TestV13Migration:
    def _file_app(self, tmp_path):
        import os
        os.environ['DATABASE_URL'] = f'sqlite:///{tmp_path}/v13mig.db'
        os.environ.setdefault('YOUTUBE_API_KEY', '')
        os.environ.setdefault('REDDIT_CLIENT_ID', '')
        os.environ.setdefault('REDDIT_CLIENT_SECRET', '')
        from app import create_app as _create
        return _create('development')

    def test_upgrade_adds_nullable_column(self, tmp_path):
        from flask_migrate import upgrade
        test_app = self._file_app(tmp_path)
        with test_app.app_context():
            from sqlalchemy import inspect
            upgrade(revision='v13_001')
            cols = {c['name']: c for c in inspect(db.engine).get_columns(
                'threat_assessments')}
            assert 'evidence_refs' in cols
            assert cols['evidence_refs']['nullable'] is True
            rev = db.session.execute(
                db.text('SELECT version_num FROM alembic_version')).scalar()
            assert rev == 'v13_001'

    def test_downgrade_removes_only_v13_column(self, tmp_path):
        from flask_migrate import upgrade, downgrade
        test_app = self._file_app(tmp_path)
        with test_app.app_context():
            from sqlalchemy import inspect
            upgrade(revision='v13_001')
            before = set(c['name'] for c in inspect(db.engine).get_columns(
                'threat_assessments'))
            downgrade(revision='v12_001')
            after = set(c['name'] for c in inspect(db.engine).get_columns(
                'threat_assessments'))
            assert before - after == {'evidence_refs'}
            assert inspect(db.engine).has_table('narratives')
            assert inspect(db.engine).has_table('threat_assessments')
            upgrade(revision='v13_001')
            assert 'evidence_refs' in set(
                c['name'] for c in inspect(db.engine).get_columns(
                    'threat_assessments'))

    def test_down_revision_is_v12(self, app):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'v13mig', 'migrations/versions/v13_001_historical_context_intelligence.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.down_revision == 'v12_001'
        assert module.revision == 'v13_001'


# --------------------------------------------------------------------------
# 10-12. historical queries: LIMIT / window / NULL
# --------------------------------------------------------------------------
class TestHistoricalQueries:
    def test_limit_is_respected(self, app, db, user):
        repo = TemporalRepository()
        for i in range(5):
            _analysis(user.id, days_ago=i)
        rows = repo.get_user_analyses_in_window(user.id, limit=2)
        assert len(rows) == 2
        rows = repo.get_historical_threat_assessments(user.id, limit=2)
        assert len(rows) <= 2

    def test_window_filtering(self, app, db, user):
        from datetime import timedelta
        repo = TemporalRepository()
        _analysis(user.id, days_ago=5)
        _analysis(user.id, days_ago=200)
        rows = repo.get_user_analyses_in_window(
            user.id, since=_now() - timedelta(days=90))
        assert len(rows) == 1
        rows = repo.get_user_analyses_in_window(user.id, since=None)
        assert len(rows) == 2

    def test_window_excludes_other_users(self, app, db, user):
        from models.user import User
        from werkzeug.security import generate_password_hash
        other = User(username='other', email='o@x.com',
                     password_hash=generate_password_hash('x'))
        db.session.add(other)
        db.session.commit()
        _analysis(user.id, days_ago=1)
        _analysis(other.id, days_ago=1)
        rows = TemporalRepository().get_user_analyses_in_window(user.id)
        assert len(rows) == 1 and rows[0].user_id == user.id

    def test_null_timestamps_never_fabricated(self, app, db, user):
        from datetime import timedelta
        repo = TemporalRepository()
        n = Narrative(user_id=user.id, name='n', normalized_name='n')
        db.session.add(n)
        db.session.commit()
        # occurrence with a real timestamp far outside the window
        a = _analysis(user.id)
        db.session.add(NarrativeOccurrence(
            narrative_id=n.id, analysis_id=a.id, user_id=user.id,
            occurred_at=_now() - timedelta(days=400)))
        db.session.commit()
        assert repo.count_occurrences_in_window(
            n.id, since=_now() - timedelta(days=90)) == 0
        assert repo.count_occurrences_in_window(n.id, since=None) == 1
        # the window filter itself excludes NULLs by construction
        import sqlalchemy
        q = NarrativeOccurrence.query.filter(
            NarrativeOccurrence.occurred_at.isnot(None))
        assert 'IS NOT NULL' in str(q.statement.compile(
            compile_kwargs={'literal_binds': True})).upper()

    def test_deterministic_ordering(self, app, db, user):
        repo = TemporalRepository()
        ids = [_analysis(user.id, days_ago=i).id for i in (3, 1, 2)]
        rows = repo.get_user_analyses_in_window(user.id)
        got = [r.id for r in rows]
        assert got == sorted(got, key=lambda _: 0)  # structural
        created = [r.created_at for r in rows]
        assert created == sorted(created, reverse=True)
        assert len(ids) == 3

    def test_comment_means_and_activity_counts(self, app, db, user):
        repo = TemporalRepository()
        a = _analysis(user.id, threat_score=10.0)
        means = repo.get_comment_metric_means([a.id, 999999])
        assert a.id in means and 999999 not in means
        assert means[a.id]['sentiment'] == pytest.approx(61.0)
        assert means[a.id]['n'] > 0
        counts = repo.get_narrative_activity_counts([a.id])
        assert counts.get(a.id, 0) == 0  # genuine zero, no occurrences


# --------------------------------------------------------------------------
# 13-16. baseline + deviation
# --------------------------------------------------------------------------
class TestHistoricalBaseline:
    def _history(self, user_id, scores):
        aids = []
        for i, score in enumerate(scores):
            aids.append(_analysis(user_id, days_ago=i + 1,
                                 threat_score=score).id)
        return aids

    def test_baseline_calculation(self, app, db, user):
        self._history(user.id, [20.0, 30.0, 40.0])
        current = _analysis(user.id, threat_score=32.0)
        out = HistoricalContextService().compute_baseline(
            user.id, current_analysis_id=current.id)
        assert out['available'] is True
        assert out['scope'] == 'user'
        threat = out['metrics']['threat']
        assert threat['baseline'] == pytest.approx(30.0)
        assert threat['current'] == pytest.approx(32.0)
        assert threat['sample_size'] == 3
        assert threat['availability'] == 'typical'
        assert 'causal' in out['disclaimer']

    def test_insufficient_history(self, app, db, user):
        self._history(user.id, [20.0])
        current = _analysis(user.id, threat_score=35.0)
        out = HistoricalContextService().compute_baseline(
            user.id, current_analysis_id=current.id)
        assert out['metrics']['threat']['availability'] == 'insufficient_history'
        assert out['metrics']['threat']['baseline'] is None

    def test_unavailable_current_metric(self, app, db, user):
        self._history(user.id, [20.0, 30.0, 40.0])
        current = _analysis(user.id)  # no threat row
        out = HistoricalContextService().compute_baseline(
            user.id, current_analysis_id=current.id)
        threat = out['metrics']['threat']
        assert threat['availability'] == 'unavailable'
        assert threat['current'] is None
        assert threat['baseline'] is None  # unavailable, NOT zero

    def test_unavailable_values_ignored_not_zeroed(self, app, db, user):
        self._history(user.id, [20.0, 30.0, 40.0])
        current = _analysis(user.id, threat_score=33.0)
        out = HistoricalContextService().compute_baseline(
            user.id, current_analysis_id=current.id)
        # authenticity has no rows at all -> unavailable, baseline None
        auth = out['metrics']['authenticity']
        assert auth['availability'] in ('unavailable', 'insufficient_history')
        assert auth['baseline'] is None

    def test_deviation_classification(self, app, db, user):
        cls = HistoricalContextService.classify_deviation
        assert cls(100.0, 100.0) == 'typical'
        assert cls(105.0, 100.0) == 'typical'
        assert cls(115.0, 100.0) == 'moderately_above_baseline'
        assert cls(140.0, 100.0) == 'substantially_above_baseline'
        assert cls(85.0, 100.0) == 'moderately_below_baseline'
        assert cls(60.0, 100.0) == 'substantially_below_baseline'
        assert cls(0.0, 0.0) == 'typical'
        assert cls(5.0, 0.0) == 'substantially_above_baseline'
        assert cls(None, 100.0) == 'unavailable'
        assert cls(100.0, None) == 'unavailable'

    def test_disabled_baseline_flag(self, app, db, user):
        app.config['ENABLE_V13_HISTORICAL_BASELINE'] = False
        out = HistoricalContextService().compute_baseline(user.id)
        assert out['available'] is False


# --------------------------------------------------------------------------
# 17. configuration bounds
# --------------------------------------------------------------------------
class TestV13Configuration:
    def test_defaults_present(self, app):
        for key in ('ENABLE_V13_EVIDENCE_CHAIN',
                    'ENABLE_V13_HISTORICAL_BASELINE',
                    'V13_MAX_LINKS_PER_ASSESSMENT',
                    'V13_MAX_HISTORY_ANALYSES',
                    'V13_HISTORY_WINDOW_DAYS',
                    'V13_MIN_HISTORY_SAMPLE',
                    'V13_EVIDENCE_SNIPPET_CHARS'):
            assert key in app.config, key

    def test_defaults_match_service_constants(self, app):
        assert app.config['V13_MAX_LINKS_PER_ASSESSMENT'] == \
            V13EvidenceChainService.MAX_LINKS == 25
        assert app.config['V13_MAX_HISTORY_ANALYSES'] == \
            HistoricalContextService.MAX_HISTORY_ANALYSES == 20
        assert app.config['V13_HISTORY_WINDOW_DAYS'] == \
            HistoricalContextService.HISTORY_WINDOW_DAYS == 90
        assert app.config['V13_MIN_HISTORY_SAMPLE'] == \
            HistoricalContextService.MIN_HISTORY_SAMPLE == 3
        assert app.config['V13_EVIDENCE_SNIPPET_CHARS'] == \
            V13EvidenceChainService.MAX_SNIPPET_CHARS == 160

    def test_bounds_are_capped(self, app, db, user):
        app.config['V13_MAX_LINKS_PER_ASSESSMENT'] = 100000
        a = _analysis(user.id, threat_score=30.0)
        result = V13EvidenceChainService().analyze(a, components={})
        assert result['link_count'] <= 200
        out = HistoricalContextService().compute_baseline(
            user.id, max_analyses=100000)
        assert out['available'] is True
        assert out['analyses_considered'] <= 500


# --------------------------------------------------------------------------
# 18. V12 compatibility
# --------------------------------------------------------------------------
class TestV12Compatibility:
    def test_threat_to_dict_still_valid_without_refs(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        row = ThreatAssessment.query.filter_by(analysis_id=a.id).first()
        data = row.to_dict()
        assert data['evidence_refs'] == []
        assert data['overall_threat_score'] == 30.0

    def test_v12_context_unaffected(self, app, db, user):
        from services.v12_context_service import build_v12_context
        a = _analysis(user.id, threat_score=30.0)
        ctx = build_v12_context(a.id, user.id)
        assert 'threat' in ctx and 'narratives' in ctx

    def test_pre_v13_rows_read_cleanly(self, app, db, user):
        a = _analysis(user.id, threat_score=30.0)
        svc = V13EvidenceChainService()
        assert svc.get_analysis_evidence_chain(a.id) is None
