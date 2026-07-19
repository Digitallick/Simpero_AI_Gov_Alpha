import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_claims, get_db
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.repo.SessionRepo import SessionRepo
from app.repo.UserRepo import UserRepo
from app.schemas.common import SuccessResponse
from app.schemas.history import ClearAllResponse, HistoryGetResponse, HistorySummaryResponse
from app.services.memo_summary import summarize_history

router = APIRouter(prefix="/history", tags=["history"])


async def _actor(db: AsyncSession, claims: dict[str, Any]) -> tuple[int, str, str | None]:
    """(org_id, actor_id, actor_email) for the audit rows this router appends."""
    user = await UserRepo(db).get_by_clerk_id(claims["user_id"])
    assert user is not None  # get_db JIT-provisions this row before the handler runs
    return user.org_id, claims["user_id"], user.email


@router.get("", response_model=list[HistorySummaryResponse])
async def list_history(db: AsyncSession = Depends(get_db)) -> list[HistorySummaryResponse]:
    """history.list — summaries only, no full memo_json (see history.get for that)."""
    sessions = await SessionRepo(db).list_for_org()
    return [
        HistorySummaryResponse(
            id=str(s.id),
            session_id=str(s.id),
            deal_id=str(s.deal_id) if s.deal_id else None,
            file_name=s.file_name,
            created_at=s.created_at,
            composed_at=s.composed_at,
            **summarize_history(s.memo_json),
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=HistoryGetResponse | None)
async def get_history(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> HistoryGetResponse | None:
    """history.get — full ICMemoResult + dealId. None (not 404) when the
    session doesn't exist or isn't visible under RLS."""
    session_row = await SessionRepo(db).get_by_id(session_id)
    if session_row is None:
        return None
    return HistoryGetResponse(
        memo=session_row.memo_json or {},
        deal_id=str(session_row.deal_id) if session_row.deal_id else None,
    )


@router.delete("/{session_id}", response_model=SuccessResponse)
async def delete_history(
    session_id: uuid.UUID,
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    deleted = await SessionRepo(db).delete(session_id)
    org_id, actor_id, actor_email = await _actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "memo_deleted",
            "session_id": session_id,
            "payload": {"deleted": deleted},
        }
    )
    return SuccessResponse(success=deleted)


@router.delete("", response_model=ClearAllResponse)
async def clear_all_history(
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_db),
) -> ClearAllResponse:
    deleted_count = await SessionRepo(db).delete_all()
    org_id, actor_id, actor_email = await _actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "history_cleared",
            "payload": {"deleted_count": deleted_count},
        }
    )
    return ClearAllResponse(success=True, deleted_count=deleted_count)
