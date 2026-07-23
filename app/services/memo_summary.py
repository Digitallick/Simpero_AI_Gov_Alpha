"""Derives legacy denormalized summary/metric fields from a Session's
memo_json (the full ICMemoResult) at read time — the SLIM sessions table
doesn't persist them (see app/models/session.py's docstring).

Defensive throughout: memo_json can be None (job not complete — the only
case Phase 1 ever actually sees, since there's no writer yet) or missing
fields (older/partial real memos, once the real pipeline exists).

# ponytail: recomputing per read is fine at <=50 analyses/mo; re-denormalize
# onto sessions if History/Dashboard reads get slow.
"""

from typing import Any


def summarize_history(memo_json: dict[str, Any] | None) -> dict[str, Any]:
    """history.list's per-session summary fields (claims counts, match rate,
    verdict/score, page count, selected frameworks)."""
    if not memo_json:
        return {
            "page_count": None,
            "claims_extracted": None,
            "claims_matched": None,
            "claims_flagged": None,
            "match_rate": None,
            "verdict": None,
            "score": None,
            "selected_frameworks": None,
        }
    scorecard = memo_json.get("scorecard") or {}
    recommendation = (memo_json.get("deliverable") or {}).get("recommendation") or {}
    verdict = (recommendation.get("verdict") or {}).get("value")
    score = (recommendation.get("score") or {}).get("value")
    return {
        "page_count": memo_json.get("pageCount"),
        "claims_extracted": scorecard.get("claimsExtracted"),
        "claims_matched": scorecard.get("claimsMatched"),
        "claims_flagged": scorecard.get("claimsFlagged"),
        "match_rate": scorecard.get("matchRate"),
        "verdict": verdict,
        "score": score,
        "selected_frameworks": memo_json.get("selectedFrameworks"),
    }


_ACTION_PILL_BY_VERDICT = {"PROCEED": "approve", "CAUTION": "review", "DECLINE": "decline"}


def derive_pipeline_metrics(memo_json: dict[str, Any] | None) -> dict[str, Any]:
    """listPipeline's per-deal derived columns (dealMetrics + scoringResult).
    actionPill's verdict->pill mapping is inferred (PROCEED/CAUTION/DECLINE
    read naturally as approve/review/decline) — the frozen contract only
    types the three pill values, not the mapping that produces them.
    """
    if not memo_json:
        return {
            "valuation_usd": None,
            "ev_revenue": None,
            "ai_score": None,
            "mandate_fit_pct": None,
            "irr_pct": None,
            "action_pill": None,
            "metric_discrepancy_fields": None,
        }
    deal_metrics = memo_json.get("dealMetrics") or {}
    scoring = memo_json.get("scoringResult") or {}
    recommendation = (memo_json.get("deliverable") or {}).get("recommendation") or {}
    verdict = (recommendation.get("verdict") or {}).get("value")
    discrepancies = memo_json.get("dealMetricDiscrepancies")
    return {
        "valuation_usd": (deal_metrics.get("valuationPostUsd") or {}).get("value"),
        "ev_revenue": (deal_metrics.get("evRevenue") or {}).get("value"),
        "ai_score": scoring.get("aiScore"),
        "mandate_fit_pct": scoring.get("mandateFitPct"),
        "irr_pct": (deal_metrics.get("irrPct") or {}).get("value"),
        "action_pill": _ACTION_PILL_BY_VERDICT.get(verdict) if isinstance(verdict, str) else None,
        "metric_discrepancy_fields": (
            [d.get("field") for d in discrepancies if isinstance(d, dict) and d.get("field")]
            if isinstance(discrepancies, list)
            else None
        ),
    }
