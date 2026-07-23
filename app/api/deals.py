import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_claims, get_db
from app.models.deal import Deal
from app.repo.DealRepo import DealRepo
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.repo.SessionRepo import SessionRepo
from app.repo.UserRepo import UserRepo
from app.schemas.deals import (
    AvgAiScoreStat,
    DashboardStatsResponse,
    DdCompletionStat,
    DealRowResponse,
    DealStatusResponse,
    DealWithLatestMemoResponse,
    LatestMemoSessionResponse,
    LivePipelineRowResponse,
    PipelineStepResponse,
    PipelineValueStat,
    ValueDelta,
)
from app.services.dashboard_stats import compute_month_bounds, compute_pipeline_value_delta
from app.services.memo_summary import derive_pipeline_metrics
from app.services.pipeline_steps import no_job_steps

router = APIRouter(prefix="/deals", tags=["deals"])


async def _actor(db: AsyncSession, claims: dict[str, Any]) -> tuple[int, str, str | None]:
    """(org_id, actor_id, actor_email) for the audit rows this router appends."""
    user = await UserRepo(db).get_by_clerk_id(claims["user_id"])
    assert user is not None  # get_db JIT-provisions this row before the handler runs
    return user.org_id, claims["user_id"], user.email


def _no_job_status() -> DealStatusResponse:
    """Phase 1 has no job model yet — every deal reports this shape until
    Phase 2's job model lands. The frontend renders an empty state for it."""
    return DealStatusResponse(
        job_status="no_job",
        current_phase=None,
        steps=[
            PipelineStepResponse(
                phase=step["phase"], title=step["title"], detail=step["detail"], status="pending"
            )
            for step in no_job_steps()
        ],
    )


def _deal_row_response(deal: Deal) -> DealRowResponse:
    return DealRowResponse(
        id=str(deal.id),
        name=deal.name,
        gp_source=deal.gp_source,
        deal_size_min_usd=deal.deal_size_min_usd,
        deal_size_max_usd=deal.deal_size_max_usd,
        # Stringified JSON, per the frozen contract's DealRowShape.sectorTags
        # (parseSectorTags on the frontend) — not the real array `listPipeline`
        # returns for the same column.
        sector_tags=json.dumps(deal.sector_tags or []),
        state=deal.status,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
    )


@router.get("/pipeline", response_model=list[LivePipelineRowResponse])
async def list_pipeline(db: AsyncSession = Depends(get_db)) -> list[LivePipelineRowResponse]:
    """deals.listPipeline — Dashboard's Live Pipeline table."""
    deal_repo = DealRepo(db)
    session_repo = SessionRepo(db)
    deals = await deal_repo.list()

    rows: list[LivePipelineRowResponse] = []
    for deal in deals:
        # ponytail: one query per deal for its latest session (N+1) — fine at
        # pipeline-table scale; batch this (single query with a lateral join
        # or window function) if the pipeline table gets large.
        latest_session = await session_repo.latest_for_deal(deal.id)
        metrics = derive_pipeline_metrics(latest_session.memo_json if latest_session else None)
        rows.append(
            LivePipelineRowResponse(
                deal_id=str(deal.id),
                name=deal.name,
                gp_source=deal.gp_source or "",
                sector_tags=deal.sector_tags or [],
                state=deal.status,
                created_at=deal.created_at,
                agent_status=_no_job_status(),
                **metrics,
            )
        )
    return rows


@router.get("/dashboard-stats", response_model=DashboardStatsResponse)
async def dashboard_stats(db: AsyncSession = Depends(get_db)) -> DashboardStatsResponse:
    """deals.dashboardStats. See app/services/dashboard_stats.py for the
    "best-effort read against a types-only contract" caveat."""
    current_start, prior_start, prior_end = compute_month_bounds()
    agg = await DealRepo(db).dashboard_aggregates(current_start, prior_start, prior_end)

    return DashboardStatsResponse(
        window="month",
        total_deals=ValueDelta(
            value=agg["total_deals"],
            delta=agg["current_window_deals"] - agg["prior_window_deals"],
        ),
        pipeline_value_usd=PipelineValueStat(
            value=agg["total_pipeline_value"],
            delta=compute_pipeline_value_delta(
                agg["current_window_value"], agg["prior_window_value"]
            ),
        ),
        # No scoring/completion data in Phase 1 — there's no analyse pipeline
        # writing memo_json.scoringResult yet.
        avg_ai_score=AvgAiScoreStat(value=None, delta=None),
        dd_completion_pct=DdCompletionStat(value=0, delta_pp=0),
    )


@router.get("/{deal_id}", response_model=DealWithLatestMemoResponse)
async def get_deal(
    deal_id: uuid.UUID,
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_db),
) -> DealWithLatestMemoResponse:
    """deals.get -> DealWithLatestMemo. 404 falls out of RLS returning no
    row — not a manual ownership check."""
    deal = await DealRepo(db).get_by_id(deal_id)
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found")

    org_id, actor_id, actor_email = await _actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "document_access",
            "deal_id": deal_id,
        }
    )

    latest_session = await SessionRepo(db).latest_for_deal(deal_id)
    latest_memo_session: LatestMemoSessionResponse | None = None
    if latest_session is not None:
        latest_memo_session = LatestMemoSessionResponse(
            id=str(latest_session.id),
            session_id=str(latest_session.id),
            file_name=latest_session.file_name,
            memo_json=json.dumps(latest_session.memo_json),
            created_at=latest_session.created_at,
        )
    return DealWithLatestMemoResponse(
        deal=_deal_row_response(deal), latest_memo_session=latest_memo_session
    )


@router.get("/{deal_id}/status", response_model=DealStatusResponse)
async def get_deal_status(
    deal_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> DealStatusResponse:
    """deals.status -> DealStatusPayload. Phase 1 has no job model yet, so
    this is always the no_job shape (Phase 2 adds real jobStatus/currentPhase)."""
    deal = await DealRepo(db).get_by_id(deal_id)
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found")
    return _no_job_status()
