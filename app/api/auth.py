from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_claims, get_db
from app.models.organisation import Users
from app.schemas.auth import ProfileSyncRequest, ProfileSyncResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=UserResponse)
async def me(
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_db),
) -> Users:
    """The caller's own user row. get_db JIT-provisions it on first login,
    so a valid token always finds exactly one row here."""
    return (
        await db.execute(select(Users).where(Users.clerk_user_id == claims["user_id"]))
    ).scalar_one()


@router.post("/sync-profile", response_model=ProfileSyncResponse)
async def sync_profile(
    payload: ProfileSyncRequest,
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_db),
) -> ProfileSyncResponse:
    """
    Fill in name/email on the caller's Users row.

    Clerk's session token carries no name/email, so get_db JIT-creates the row
    with NULLs; the frontend calls this once after sign-in (and again whenever
    the Clerk profile changes) — same pattern as simpero_GOV_AI's auth.syncProfile.
    """
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    if fields:
        await db.execute(
            update(Users).where(Users.clerk_user_id == claims["user_id"]).values(**fields)
        )
    return ProfileSyncResponse(success=True)
