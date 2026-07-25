from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import _admin_actor, get_admin_db, require_org_admin
from app.core.config import get_settings
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.schemas.admin.invitations import (
    CreateInvitationRequest,
    InvitationResponse,
    PendingInvitationResponse,
)
from app.schemas.common import SuccessResponse
from app.services.admin.clerk_admin import (
    clerk_created_at,
    clerk_error_to_http,
    create_organization_invitation,
    list_organization_invitations,
    revoke_organization_invitation,
)

settings = get_settings()

router = APIRouter(prefix="/invitations", tags=["admin"])


@router.post("", response_model=InvitationResponse, status_code=201)
async def create_invitation(
    payload: CreateInvitationRequest,
    claims: dict[str, Any] = Depends(require_org_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> InvitationResponse:
    """Creates a Clerk org invitation into the CALLER's org (claims["tenant_id"]
    — never from the body), role forced to org:member. redirect_url ->
    product sign-up (D1)."""
    if payload.role != "member":
        # Defense-in-depth: the schema's Literal["member"] already rejects
        # any other value with a 422 before this ever runs.
        raise HTTPException(status_code=403, detail="Only member invitations are supported here")

    try:
        invitation = await create_organization_invitation(
            org_id=claims["tenant_id"],
            email=payload.email_address,
            role="org:member",
            redirect_url=f"{settings.app_base_url}/sign-up",
            inviter_user_id=claims["user_id"],
        )
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_invitation_created",
            "payload": {
                "email": payload.email_address,
                "role": "member",
                "clerk_invitation_id": invitation["id"],
            },
        }
    )
    return InvitationResponse(
        id=invitation["id"],
        email_address=invitation["email_address"],
        status=invitation["status"],
        created_at=clerk_created_at(invitation),
    )


@router.get("", response_model=list[PendingInvitationResponse])
async def list_invitations(
    claims: dict[str, Any] = Depends(require_org_admin),
) -> list[PendingInvitationResponse]:
    """Pending invitations for the caller's org."""
    try:
        invitations = await list_organization_invitations(claims["tenant_id"], status="pending")
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc
    return [
        PendingInvitationResponse(
            id=inv["id"],
            email_address=inv["email_address"],
            role=inv["role"],
            status=inv["status"],
            created_at=clerk_created_at(inv),
        )
        for inv in invitations
    ]


@router.delete("/{invitation_id}", response_model=SuccessResponse)
async def revoke_invitation(
    invitation_id: str,
    claims: dict[str, Any] = Depends(require_org_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> SuccessResponse:
    """Revoke a pending Clerk invitation for the caller's org."""
    try:
        await revoke_organization_invitation(
            org_id=claims["tenant_id"],
            invitation_id=invitation_id,
            requesting_user_id=claims["user_id"],
        )
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_invitation_revoked",
            "payload": {"clerk_invitation_id": invitation_id},
        }
    )
    return SuccessResponse(success=True)
