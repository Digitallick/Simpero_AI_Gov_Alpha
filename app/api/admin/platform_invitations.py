from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import _admin_actor, get_admin_db, require_platform_admin
from app.core.config import get_settings
from app.core.security import fetch_clerk_organization
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.schemas.admin.invitations import CreateInvitationRequest, InvitationResponse
from app.services.admin.clerk_admin import (
    clerk_created_at,
    clerk_error_to_http,
    create_organization_invitation,
)

settings = get_settings()

# A separate module (not invitations.py) keeps this deliberate, guarded
# cross-tenant path visibly distinct from the org-admin path — see D2 in the
# plan. The get_admin_db session stays clamped to the platform admin's own
# (Simpero) org the whole time; only the Clerk Backend API call targets
# {clerk_org_id}. No DB write ever targets the client org.
router = APIRouter(prefix="/organizations/{clerk_org_id}/invitations", tags=["admin"])


_CLERK_ROLE = {"member": "org:member", "admin": "org:admin"}
_REDIRECT_PATH = {"member": "/sign-up", "admin": "/admin/sign-up"}


@router.post("", response_model=InvitationResponse, status_code=201)
async def invite_into_org(
    clerk_org_id: str,
    payload: CreateInvitationRequest,
    claims: dict[str, Any] = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> InvitationResponse:
    """Invites an org:member or org:admin product user into the client org
    named by the {clerk_org_id} path param — the one deliberate, guarded
    exception to "target org comes from the token" (the platform admin's own
    token is scoped to the Simpero org). Unlike invitations.py's own-org
    endpoint, both roles the shared schema allows are accepted here."""
    if clerk_org_id == settings.simpero_platform_org_id:
        raise HTTPException(status_code=403, detail="Cannot invite into the platform org")

    try:
        await fetch_clerk_organization(clerk_org_id)  # existence check; Clerk 404 -> 404
        invitation = await create_organization_invitation(
            org_id=clerk_org_id,
            email=payload.email_address,
            role=_CLERK_ROLE[payload.role],
            redirect_url=f"{settings.app_base_url}{_REDIRECT_PATH[payload.role]}",
            # No inviter_user_id: Clerk requires the inviter to be an actual
            # member of clerk_org_id, and platform admins deliberately never
            # are (R1) — passing claims["user_id"] here 403s with Clerk's
            # own "not a member" error on every call. Unlike organizations.py's
            # seed invitation (created while the platform admin is still
            # momentarily a member, before removal) or invitations.py's
            # own-org invite (caller genuinely is a member of their own org),
            # there's no valid Clerk member to attribute this invite to.
        )
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    # Audited in the SIMPERO org's own trail (get_admin_db clamps the
    # session to the platform admin's own org) — the client org is captured
    # in the payload instead. Correct and unavoidable given the RLS clamp;
    # the Clerk invitation still targets the client org via the API.
    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_member_invited_by_platform",
            "payload": {
                "target_clerk_org_id": clerk_org_id,
                "email": payload.email_address,
                "role": payload.role,
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
