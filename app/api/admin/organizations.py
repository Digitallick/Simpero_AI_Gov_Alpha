from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import _admin_actor, get_admin_db, require_platform_admin
from app.core.config import get_settings
from app.models.organisation import OrgType
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.schemas.admin.invitations import InvitationResponse
from app.schemas.admin.organizations import (
    CreateOrganizationRequest,
    CreateOrgResult,
    OrganizationResponse,
)
from app.services.admin.clerk_admin import (
    clerk_created_at,
    clerk_error_to_http,
    create_organization,
    create_organization_invitation,
    list_organizations,
    remove_organization_membership,
)

settings = get_settings()

router = APIRouter(prefix="/organizations", tags=["admin"])


@router.post("", response_model=CreateOrgResult, status_code=201)
async def create_org(
    payload: CreateOrganizationRequest,
    claims: dict[str, Any] = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> CreateOrgResult:
    """Creates the org in Clerk (public_metadata.type from OrgType), then
    seeds an org:admin invitation with the admin-sign-up redirect_url (D1) —
    unless `account_manager_email` is omitted, in which case no invitation is
    created and the org is left with no seed invite. No local `organisation`
    row is inserted — it stays lazily JIT-provisioned on the account
    manager's first admin request.

    R1 (reversed — platform admins must not accumulate client-org
    memberships): Clerk's POST /organizations requires `created_by` to make
    someone the initial admin member, so the calling platform admin is
    momentarily a member of the new org — then immediately removed below.
    Platform admins get their org visibility from GET /organizations and
    GET /organizations/{id}/members (Clerk-API-direct, no membership
    required), never from being a member themselves. Removal is best-effort
    (a Clerk-side failure here must not undo the org/invitation that already
    exist) — its outcome is recorded on the audit row instead of raised. This
    runs unconditionally, regardless of whether a seed invitation was created.
    """
    org_type = OrgType(payload.type) if payload.type else None
    try:
        clerk_org = await create_organization(
            name=payload.name, org_type=org_type, created_by=claims["user_id"]
        )
        invitation = None
        if payload.account_manager_email is not None:
            invitation = await create_organization_invitation(
                org_id=clerk_org["id"],
                email=payload.account_manager_email,
                role="org:admin",
                redirect_url=f"{settings.app_base_url}/admin/sign-up",
                inviter_user_id=claims["user_id"],
            )
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    try:
        await remove_organization_membership(clerk_org["id"], claims["user_id"])
        creator_membership_removed = True
    except httpx.HTTPError:
        creator_membership_removed = False

    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_organization_created",
            "payload": {
                "clerk_org_id": clerk_org["id"],
                "name": payload.name,
                "type": payload.type,
                "account_manager_email": payload.account_manager_email,
                "seed_invitation_id": invitation["id"] if invitation else None,
                "creator_membership_removed": creator_membership_removed,
            },
        }
    )
    return CreateOrgResult(
        clerk_org_id=clerk_org["id"],
        name=clerk_org["name"],
        type=payload.type,
        invitation=InvitationResponse(
            id=invitation["id"],
            email_address=invitation["email_address"],
            status=invitation["status"],
            created_at=clerk_created_at(invitation),
        )
        if invitation
        else None,
    )


@router.get("", response_model=list[OrganizationResponse])
async def list_orgs(
    claims: dict[str, Any] = Depends(require_platform_admin),
) -> list[OrganizationResponse]:
    """Client orgs from Clerk's Backend API, excluding the Simpero platform org."""
    try:
        orgs = await list_organizations()
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc
    return [
        OrganizationResponse(
            clerk_org_id=org["id"],
            name=org["name"],
            type=(org.get("public_metadata") or {}).get("type"),
            created_at=clerk_created_at(org),
        )
        for org in orgs
        if org["id"] != settings.simpero_platform_org_id
    ]
