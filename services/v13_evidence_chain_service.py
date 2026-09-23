"""V13 evidence-chain (provenance) service.

Phase B persists explainable links between intelligence claims and the
actual stored rows that produced them, on the existing
``ThreatAssessment.evidence_refs`` JSON column (no new tables).

Link schema (deterministic, JSON serializable, bounded)::

    {
      'claim': 'Narrative ... detected ...',   # factual, hedged, no causality
      'component': 'narrative',                # V12 engine name
      'source_table': 'narratives',            # real table, or None
      'source_id': 12,                         # real PK, verified, or None
      'ref': 'comment:34',                     # human-readable reference
      'snippet': '...',                        # truncated stored text only
      'score': 42.0,                           # numeric or None
      'evidence_type': 'narrative',            # row kind
    }

Rules
-----
* Never invent IDs, timestamps, snippets, or scores: every ``source_id``
  is verified against the database before persisting; unverifiable refs
  keep ``source_table``/``source_id`` as ``None`` with the original
  ``ref``/``snippet`` preserved.
* ``None`` (unavailable) is never converted to ``0`` or fabricated
  evidence; unavailable components contribute no links.
* Re-running for the same analysis overwrites ``evidence_refs``
  (idempotent - the column lives on the 1:1 assessment row).
* Failures return ``available: False`` and never raise into the pipeline.
"""
import logging

from sqlalchemy.exc import SQLAlchemyError

from database import db
from models.analysis import Analysis
from models.comment_result import CommentResult
from models.coordination_signal import CoordinationSignal
from models.entity import Entity
from models.narrative import Narrative
from models.narrative_occurrence import NarrativeOccurrence
from models.propagation_event import PropagationEvent
from models.threat_assessment import ThreatAssessment
from repositories.narrative_repository import NarrativeRepository
from repositories.propagation_repository import PropagationRepository
from repositories.threat_assessment_repository import ThreatAssessmentRepository

logger = logging.getLogger(__name__)

#: source_table -> model, for existence verification.
_SOURCE_MODELS = {
    'analyses': Analysis,
    'comment_results': CommentResult,
    'coordination_signals': CoordinationSignal,
    'entities': Entity,
    'narrative_occurrences': NarrativeOccurrence,
    'narratives': Narrative,
    'propagation_events': PropagationEvent,
    'threat_assessments': ThreatAssessment,
}


class V13EvidenceChainService:
    """Leaf service: database/model/repository primitives only."""

    ENABLE_KEY = 'ENABLE_V13_EVIDENCE_CHAIN'

    MAX_LINKS = 25
    MAX_SNIPPET_CHARS = 160

    CAPABILITY = 'heuristic'
    DETECTION_METHOD = 'heuristic_evidence_chain'

    COMPONENT_ORDER = (
        'authenticity',
        'narrative',
        'coordination',
        'propagation',
        'temporal',
        'entity',
    )

    # V12 evidence ``source`` labels produced by the engines, mapped to the
    # real table they describe. Labels without a reliable row mapping stay
    # unmapped (links keep ref/snippet, source stays None).
    SOURCE_TABLE_BY_LABEL = {
        'comment': 'comment_results',
        'comment_result': 'comment_results',
    }

    def __init__(self):
        self.threat_repo = ThreatAssessmentRepository()
        self.narrative_repo = NarrativeRepository()
        self.propagation_repo = PropagationRepository()

    # ------------------------------------------------------------------ cfg

    def _cfg(self, key, default):
        try:
            from flask import current_app
            if current_app:
                return current_app.config.get(key, default)
        except Exception:
            pass
        return default

    # -------------------------------------------------------------- analyze

    def analyze(self, analysis, components=None):
        """Build and persist evidence links for one analysis.

        ``components`` is the live ``v12_components`` dict from the
        pipeline. Links are also derived from persisted V12 rows so the
        chain survives DB re-reads. Returns a result dict.
        """
        if not self._cfg(self.ENABLE_KEY, True):
            return self._unavailable('V13 evidence chain is disabled by configuration.')

        if analysis is None or getattr(analysis, 'id', None) is None:
            return self._unavailable('No analysis provided; evidence chain cannot be built.')

        try:
            assessment = self.threat_repo.get_for_analysis(analysis.id)
            if assessment is None:
                return self._unavailable(
                    'No persisted threat assessment for this analysis; '
                    'evidence chain requires the V12 threat stage to have run.')

            max_links = self._max_links()
            candidates = []
            candidates.extend(self._links_from_persisted_rows(analysis, assessment))
            candidates.extend(self._links_from_live_samples(analysis, components))
            links = self._verify_and_bound(candidates, max_links)
            truncated = len(candidates) > len(links)

            assessment.evidence_refs = links
            db.session.commit()
            return self._build_result(assessment, links, truncated)
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning(f'V13 evidence chain failed: {exc}')
            return self._unavailable('Evidence chain failed; rolled back.')

    # ------------------------------------------------------------ read API

    def get_analysis_evidence_chain(self, analysis_id):
        """Read-only: reconstruct the chain from the DB with verification."""
        assessment = self.threat_repo.get_for_analysis(analysis_id)
        if assessment is None or not assessment.evidence_refs:
            return None
        stored = assessment.evidence_refs if isinstance(assessment.evidence_refs, list) else []
        links = [self._reverify(link) for link in stored if isinstance(link, dict)]
        verified = sum(1 for link in links if link.get('verified'))
        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'analysis_id': analysis_id,
            'links': links,
            'link_count': len(links),
            'verified_count': verified,
            'stale_count': len(links) - verified,
        }

    # ------------------------------------------------------- persisted rows

    def _links_from_persisted_rows(self, analysis, assessment):
        """One link per stored V12 row for this analysis (all verifiable)."""
        links = []
        aid = analysis.id

        links.append(self._link(
            claim=(f'Overall threat score {assessment.overall_threat_score} '
                   f'({assessment.threat_level}) is a heuristic combination; '
                   'see component links for supporting evidence.'),
            component='threat',
            source_table='threat_assessments',
            source_id=assessment.id,
            ref=f'threat_assessment:{assessment.id}',
            snippet=None,
            score=self._number(assessment.overall_threat_score),
            evidence_type='assessment',
        ))

        try:
            pairs = self.narrative_repo.get_for_analysis(aid) or []
        except SQLAlchemyError:
            pairs = []
        for pair in pairs:
            # get_for_analysis returns (Narrative, NarrativeOccurrence)
            # rows (SQLAlchemy Row objects); accept bare rows too.
            row = None
            try:
                candidate = pair[0]
            except (TypeError, IndexError, KeyError):
                candidate = pair
            if candidate is not None and hasattr(candidate, 'id'):
                row = candidate
            elif hasattr(pair, 'id'):
                row = pair
            if row is None:
                continue
            name = getattr(row, 'name', None) or 'unnamed narrative'
            links.append(self._link(
                claim=(f"Narrative '{name}' detected for this analysis "
                       f'(risk {getattr(row, "risk_score", None)}).'),
                component='narrative',
                source_table='narratives',
                source_id=getattr(row, 'id', None),
                ref=f'narrative:{getattr(row, "id", None)}',
                snippet=None,
                score=self._number(getattr(row, 'risk_score', None)),
                evidence_type='narrative',
            ))

        try:
            occurrences = self.narrative_repo.get_occurrences_for_analysis(aid) or []
        except SQLAlchemyError:
            occurrences = []
        for row in occurrences:
            links.append(self._link(
                claim=(f'Narrative occurrence links narrative '
                       f'{getattr(row, "narrative_id", None)} to this analysis.'),
                component='narrative',
                source_table='narrative_occurrences',
                source_id=getattr(row, 'id', None),
                ref=f'narrative_occurrence:{getattr(row, "id", None)}',
                snippet=None,
                score=self._number(getattr(row, 'relevance_score', None)),
                evidence_type='occurrence',
            ))

        try:
            signals = CoordinationSignal.query.filter_by(analysis_id=aid).all()
        except SQLAlchemyError:
            signals = []
        for row in signals:
            links.append(self._link(
                claim=(f"Coordination signal '{getattr(row, 'signal_type', None)}' "
                       f'observed (level {getattr(row, "level", None)}).'),
                component='coordination',
                source_table='coordination_signals',
                source_id=getattr(row, 'id', None),
                ref=f'coordination_signal:{getattr(row, "id", None)}',
                snippet=None,
                score=self._number(getattr(row, 'score', None)),
                evidence_type='signal',
            ))

        try:
            events = self.propagation_repo.get_for_analysis(aid) or []
        except SQLAlchemyError:
            events = []
        for row in events:
            links.append(self._link(
                claim=(f"Relationship '{getattr(row, 'relationship_type', None)}' "
                       f'observed with analysis {getattr(row, "target_analysis_id", None)}.'),
                component='propagation',
                source_table='propagation_events',
                source_id=getattr(row, 'id', None),
                ref=f'propagation_event:{getattr(row, "id", None)}',
                snippet=None,
                score=self._number(getattr(row, 'propagation_score', None)),
                evidence_type='event',
            ))

        return [link for link in links if link is not None]

    # -------------------------------------------------------- live samples

    def _links_from_live_samples(self, analysis, components):
        """Links from live engine evidence samples (verified before use)."""
        if not isinstance(components, dict):
            return []
        links = []
        for component in self.COMPONENT_ORDER:
            comp = components.get(component)
            if not isinstance(comp, dict) or not comp.get('available'):
                continue
            evidence = comp.get('evidence')
            samples = []
            if isinstance(evidence, dict):
                raw = evidence.get('samples')
                if isinstance(raw, list):
                    samples = raw
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                link = self._link_from_sample(component, sample)
                if link is not None:
                    links.append(link)
        return links

    def _link_from_sample(self, component, sample):
        source = sample.get('source')
        ref = sample.get('ref')
        table = self.SOURCE_TABLE_BY_LABEL.get(source) if isinstance(source, str) else None
        source_id = self._parse_comment_ref(ref) if table == 'comment_results' else None
        claim_bits = [component, 'evidence']
        if isinstance(ref, str) and ref:
            claim_bits.append(f'ref {ref}')
        return self._link(
            claim=' '.join(claim_bits) + ' observed in analyzed content.',
            component=component,
            source_table=table,
            source_id=source_id,
            ref=ref if isinstance(ref, str) else None,
            snippet=sample.get('snippet'),
            score=self._number(sample.get('risk_score', sample.get('relevance_score'))),
            evidence_type='sample',
        )

    @staticmethod
    def _parse_comment_ref(ref):
        """Extract a comment PK from ``'comment:<id>'``; None otherwise."""
        if not isinstance(ref, str) or not ref.startswith('comment:'):
            return None
        try:
            value = int(ref.split(':', 1)[1])
        except (ValueError, IndexError):
            return None
        return value if value > 0 else None

    # ---------------------------------------------------------- build/verify

    def _link(self, claim, component, source_table, source_id,
              ref, snippet, score, evidence_type):
        if not isinstance(claim, str) or not claim:
            return None
        if source_id is not None and not isinstance(source_id, int):
            return None
        return {
            'claim': claim,
            'component': component,
            'source_table': source_table,
            'source_id': source_id,
            'ref': ref if isinstance(ref, str) else None,
            'snippet': self._snippet(snippet),
            'score': score,
            'evidence_type': evidence_type,
        }

    def _snippet(self, value):
        if value is None:
            return None
        try:
            text = str(value)
        except Exception:
            return None
        if not text:
            return None
        limit = self._snippet_chars()
        return text[:limit]

    def _verify_and_bound(self, candidates, max_links):
        """Keep only DB-verified links (unverifiable keep None ids), bounded."""
        by_table = {}
        for link in candidates:
            table = link.get('source_table')
            sid = link.get('source_id')
            if table in _SOURCE_MODELS and isinstance(sid, int):
                by_table.setdefault(table, set()).add(sid)
        existing = {}
        for table, ids in by_table.items():
            try:
                rows = _SOURCE_MODELS[table].query.filter(
                    _SOURCE_MODELS[table].id.in_(sorted(ids))).all()
                existing[table] = {getattr(r, 'id', None) for r in rows}
            except SQLAlchemyError:
                existing[table] = set()
        ordered = sorted(
            candidates,
            key=lambda l: (
                self._component_rank(l.get('component')),
                str(l.get('source_table') or ''),
                l.get('source_id') if isinstance(l.get('source_id'), int) else -1,
                str(l.get('ref') or ''),
            ),
        )
        links = []
        for link in ordered:
            table = link.get('source_table')
            sid = link.get('source_id')
            if table in _SOURCE_MODELS and isinstance(sid, int):
                if sid not in existing.get(table, set()):
                    continue  # invented or deleted row: never persist
                link['verified'] = True
            else:
                link['verified'] = False
            links.append(link)
            if len(links) >= max_links:
                break
        return links

    def _reverify(self, link):
        """Re-check one persisted link against the live database."""
        table = link.get('source_table')
        sid = link.get('source_id')
        verified = False
        if table in _SOURCE_MODELS and isinstance(sid, int):
            try:
                verified = db.session.get(_SOURCE_MODELS[table], sid) is not None
            except SQLAlchemyError:
                verified = False
        out = dict(link)
        out['verified'] = verified
        return out

    def _component_rank(self, component):
        try:
            return self.COMPONENT_ORDER.index(component)
        except ValueError:
            return len(self.COMPONENT_ORDER)

    # ---------------------------------------------------------------- helpers

    def _max_links(self):
        try:
            value = int(self._cfg('V13_MAX_LINKS_PER_ASSESSMENT', self.MAX_LINKS))
        except (TypeError, ValueError):
            return self.MAX_LINKS
        return max(1, min(value, 200))

    def _snippet_chars(self):
        try:
            value = int(self._cfg('V13_EVIDENCE_SNIPPET_CHARS', self.MAX_SNIPPET_CHARS))
        except (TypeError, ValueError):
            return self.MAX_SNIPPET_CHARS
        return max(16, min(value, 2000))

    @staticmethod
    def _number(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number

    def _build_result(self, assessment, links, truncated):
        return {
            'available': True,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'analysis_id': assessment.analysis_id,
            'links': links,
            'link_count': len(links),
            'truncated': truncated,
        }

    def _unavailable(self, reason):
        return {
            'available': False,
            'capability': self.CAPABILITY,
            'detection_method': self.DETECTION_METHOD,
            'analysis_id': None,
            'links': [],
            'link_count': 0,
            'truncated': False,
            'reason': reason,
        }
