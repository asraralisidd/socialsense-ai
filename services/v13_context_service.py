"""V13 read-only context for the analysis result page (Phase D).

Assembles the Phase B/C service outputs into one bounded, UI-ready dict::

    {
      'baseline': HistoricalContextService.compute_baseline(...) or None,
      'comparison': CrossAnalysisService.compare_with_history(...) or None,
      'evolution': CrossAnalysisService.narrative_evolution(...) or None,
      'evidence': V13EvidenceChainService.get_analysis_evidence_chain(...) or None,
    }

Each section is isolated: a failure in one section logs a warning and
yields ``None`` (rendered as an unavailable state) without breaking the
other sections or the core result page. No business logic lives here
beyond delegation; no writes are performed.
"""
import logging

logger = logging.getLogger(__name__)

V13_DISCLAIMER = (
    'Historical context is descriptive and associative, not causal: '
    'a change or deviation does not prove manipulation, coordination, '
    'or threat.'
)


def build_v13_context(analysis_id, user_id):
    """Bounded read-only V13 context for one analysis (same user scope).

    A single request-local prefetch supplies the threat row, metric series,
    and history ids once; baseline, comparison, and evidence reuse that
    prefetch with zero duplicate reads. Fallback to per-section queries
    on prefetch failure, exactly as before.
    """
    context = {'baseline': None, 'comparison': None,
               'evolution': None, 'evidence': None}
    if analysis_id is None or user_id is None:
        return context

    prefetch = None
    try:
        from services.historical_context_service import HistoricalContextService
        prefetch = HistoricalContextService().build_prefetch(
            user_id, current_analysis_id=analysis_id)
    except Exception as exc:
        logger.warning(f'V13 prefetch failed: {exc}')
        prefetch = None

    try:
        from services.historical_context_service import HistoricalContextService
        result = HistoricalContextService().compute_baseline(
            user_id, current_analysis_id=analysis_id, prefetch=prefetch)
        if isinstance(result, dict) and result.get('available'):
            context['baseline'] = result
    except Exception as exc:
        logger.warning(f'V13 baseline context failed: {exc}')

    try:
        from services.cross_analysis_service import CrossAnalysisService
        svc = CrossAnalysisService()
        result = svc.compare_with_history(user_id, analysis_id, prefetch=prefetch)
        if isinstance(result, dict) and result.get('available'):
            context['comparison'] = result
        result = svc.narrative_evolution(user_id, analysis_id)
        if isinstance(result, dict) and result.get('available'):
            context['evolution'] = result
    except Exception as exc:
        logger.warning(f'V13 comparison/evolution context failed: {exc}')

    try:
        from services.v13_evidence_chain_service import V13EvidenceChainService
        assessment = None
        if prefetch is not None:
            assessment = prefetch.get('threat_row')
        result = V13EvidenceChainService().get_analysis_evidence_chain(
            analysis_id, assessment=assessment)
        if isinstance(result, dict) and result.get('available'):
            context['evidence'] = result
    except Exception as exc:
        logger.warning(f'V13 evidence context failed: {exc}')

    return context
