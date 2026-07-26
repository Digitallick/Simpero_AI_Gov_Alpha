from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import _admin_actor, get_admin_db, require_platform_admin
from app.core.config import get_settings
from app.core.security import fetch_clerk_organization
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.schemas.common import SuccessResponse
from app.services.admin.clerk_admin import clerk_error_to_http, delete_organization

settings = get_settings()

# Separate module, same reason as platform_invitations.py/platform_members.py:
# keeps this deliberate, guarded cross-tenant path (target org from the path,
# not the caller's token) visibly distinct from the org-admin's own-org path.
router = APIRouter(prefix="/organizations/{clerk_org_id}", tags=["admin"])


@router.delete("", response_model=SuccessResponse)
async def delete_org(
    clerk_org_id: str,
    claims: dict[str, Any] = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> SuccessResponse:
    """Deletes a client org in Clerk only. Verified against the real Clerk
    Backend API: DELETE /organizations/{id} cascades — it succeeds even with
    active memberships and pending invitations present, no 404/409 guard
    needed here for that.

    Deliberately does NOT touch local DB rows (`organisation`, `users`,
    `funds`, `clerk_admin_users`) for the target org — get_admin_db's RLS
    clamp restricts this session to the platform admin's OWN org, so
    reaching into the target org's rows would require pointing SET LOCAL at
    an arbitrary org from within a route handler, a real RLS-crossing
    precedent deferred as a deliberate decision. Local rows are left as
    orphans for now.
    """
    if clerk_org_id == settings.simpero_platform_org_id:
        raise HTTPException(status_code=403, detail="Cannot delete the platform org")

    try:
        org = await fetch_clerk_organization(clerk_org_id)  # also gets the name for the audit row
        await delete_organization(clerk_org_id)
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_organization_deleted",
            "payload": {"clerk_org_id": clerk_org_id, "name": org["name"]},
        }
    )
    return SuccessResponse(success=True)
