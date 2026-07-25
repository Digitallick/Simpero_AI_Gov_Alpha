from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import (
    _admin_actor,
    _set_org_scope,
    get_admin_db,
    require_platform_admin,
)
from app.core.config import get_settings
from app.models.clerk_admin_user import AdminType, ClerkAdminUser
from app.models.organisation import Users, utc_now
from app.repo.AdminUserRepo import AdminUserRepo
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.schemas.admin.members import OrgMemberResponse, UpdateMemberRoleRequest
from app.schemas.common import SuccessResponse
from app.services.admin.clerk_admin import (
    clerk_error_to_http,
    fetch_clerk_user_primary_email,
    list_organization_memberships,
    remove_organization_membership,
    update_organization_membership,
)

settings = get_settings()

_CLERK_ROLE = {"member": "org:member", "admin": "org:admin"}

# A separate module (not members.py) for the same reason platform_invitations.py
# is separate from invitations.py — keeps this deliberate, guarded cross-tenant
# path (target org from the path, not the caller's token) visibly distinct
# from the org-admin's own-org path.
router = APIRouter(prefix="/organizations/{clerk_org_id}/members", tags=["admin"])


def _member_name(public_user_data: dict[str, Any]) -> str | None:
    parts = [public_user_data.get("first_name"), public_user_data.get("last_name")]
    name = " ".join(p for p in parts if p)
    return name or None


def _to_org_member_response(m: dict[str, Any]) -> OrgMemberResponse:
    return OrgMemberResponse(
        id=m["id"],
        user_id=m["public_user_data"]["user_id"],
        name=_member_name(m["public_user_data"]),
        email=m["public_user_data"].get("identifier"),
        role=m["role"],
        status="active",  # every live Clerk membership is definitionally active
    )


def _inactive_local_user_to_org_member_response(user: Users) -> OrgMemberResponse:
    """A removed member's Clerk membership is gone, so there's no membership
    id to reuse — the frontend's primary key for these actions is
    clerk_user_id anyway (see the PATCH/DELETE endpoints below), so it
    doubles as both `id` and `user_id` here."""
    return OrgMemberResponse(
        id=user.clerk_user_id,
        user_id=user.clerk_user_id,
        name=user.name,
        email=user.email,
        role=_CLERK_ROLE[user.role],
        status="inactive",
    )


@router.get("", response_model=list[OrgMemberResponse])
async def list_org_members(
    clerk_org_id: str,
    claims: dict[str, Any] = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> list[OrgMemberResponse]:
    """Members of an arbitrary client org: live Clerk memberships (always
    active) merged with locally-soft-deleted `users` rows for that org
    (removed members, whose Clerk membership is fully revoked and so would
    never appear in the live list otherwise). get_admin_db's RLS clamp
    restricts the session to the platform admin's own org by default, so the
    local read below uses _set_org_scope to re-point at the target org first
    — same pattern the PATCH/DELETE endpoints in this file already use.

    Dedup: a local inactive row is only included if its clerk_user_id is NOT
    also in the live Clerk list. If someone was removed and re-invited and
    their Clerk membership is active again but their local row hasn't been
    reactivated yet (they haven't logged back in), live Clerk data wins —
    showing them twice would be wrong.
    """
    try:
        memberships = await list_organization_memberships(clerk_org_id)
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    live_clerk_user_ids = {m["public_user_data"]["user_id"] for m in memberships}

    await _set_org_scope(db, clerk_org_id)
    local_inactive = await db.scalars(select(Users).where(Users.status == "inactive"))

    result = [_to_org_member_response(m) for m in memberships]
    result += [
        _inactive_local_user_to_org_member_response(user)
        for user in local_inactive
        if user.clerk_user_id not in live_clerk_user_ids
    ]
    return result


@router.patch("/{clerk_user_id}", response_model=OrgMemberResponse)
async def update_org_member_role(
    clerk_org_id: str,
    clerk_user_id: str,
    payload: UpdateMemberRoleRequest,
    claims: dict[str, Any] = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> OrgMemberResponse:
    """Cross-org role change. Platform admins are expected to hold no
    client-org membership (R1 reversed — see organizations.py's
    remove_organization_membership call), so the caller should never be able
    to be the target here — but that removal is best-effort and can fail
    (see test_create_org_membership_removal_failure_does_not_fail_the_request),
    which would leave a platform admin as a real member of a client org. The
    explicit self-guard below closes that gap rather than relying solely on
    R1's removal succeeding.

    get_admin_db's session stays clamped to the platform admin's own org for
    the whole request; the local DB writes below use _set_org_scope to
    legitimately re-point app.org_id to the TARGET org for that work (the
    org_isolation RLS policy would otherwise return/affect nothing for a
    plain query against the target org's rows), then re-point it back to the
    platform org for the audit write. All of this shares get_admin_db's one
    already-open transaction — same non-atomicity every other admin route
    already accepts between an external Clerk call and its local audit write.
    """
    if clerk_org_id == settings.simpero_platform_org_id:
        raise HTTPException(status_code=403, detail="Cannot manage members of the platform org")
    if clerk_user_id == claims["user_id"]:
        # Defense-in-depth: should be unreachable given R1's membership
        # removal, but that removal is best-effort and can fail — see
        # docstring above.
        raise HTTPException(status_code=403, detail="Cannot change your own role")

    try:
        memberships = await list_organization_memberships(clerk_org_id)
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc
    current = next(
        (m for m in memberships if m["public_user_data"]["user_id"] == clerk_user_id), None
    )
    if current is None:
        raise HTTPException(status_code=404, detail="Member not found in organization")

    new_role = payload.role
    if _CLERK_ROLE[new_role] == current["role"]:
        return _to_org_member_response(current)

    if current["role"] == "org:admin" and new_role == "member":
        await _set_org_scope(db, clerk_org_id)
        active_admins = await db.scalar(
            select(func.count())
            .select_from(ClerkAdminUser)
            .where(ClerkAdminUser.status == "active")
        )
        if active_admins is not None and active_admins <= 1:
            raise HTTPException(status_code=403, detail="Cannot demote the last active admin")

    try:
        updated = await update_organization_membership(
            clerk_org_id, clerk_user_id, _CLERK_ROLE[new_role]
        )
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    await _set_org_scope(db, clerk_org_id)
    if new_role == "admin":
        await AdminUserRepo(db).reactivate_if_exists(clerk_user_id, AdminType.client)
    else:
        await AdminUserRepo(db).deactivate(clerk_user_id)

    local_user = await db.scalar(select(Users).where(Users.clerk_user_id == clerk_user_id))
    users_role_updated = local_user is not None
    if local_user is not None:
        local_user.role = new_role
        # Flush now, while app.org_id is still the TARGET org: autoflush
        # would otherwise defer this UPDATE until the next query (the audit
        # write below), by which point _set_org_scope has re-pointed RLS to
        # the platform org and the row would no longer be visible to update.
        await db.flush()

    await _set_org_scope(db, claims["tenant_id"])
    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_member_role_changed",
            "payload": {
                "target_clerk_org_id": clerk_org_id,
                "target_clerk_user_id": clerk_user_id,
                "target_email": await fetch_clerk_user_primary_email(clerk_user_id),
                "old_role": current["role"],
                "new_role": _CLERK_ROLE[new_role],
                "clerk_membership_role_updated": True,
                "users_role_updated": users_role_updated,
            },
        }
    )

    return _to_org_member_response(updated)


@router.delete("/{clerk_user_id}", response_model=SuccessResponse)
async def remove_org_member(
    clerk_org_id: str,
    clerk_user_id: str,
    claims: dict[str, Any] = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> SuccessResponse:
    """Cross-org member removal — mirrors update_org_member_role's guard
    order, _set_org_scope re-pointing, and autoflush-before-rescoping
    discipline (see that function's docstring for the full rationale).
    Clerk membership revocation is unconditional and hard-fails the request
    on error (matches remove_member's own-org hard-fail semantics, not
    best-effort); the local `users` row soft-delete stays best-effort, same
    as the PATCH endpoint's local-write skip when no row exists yet."""
    if clerk_org_id == settings.simpero_platform_org_id:
        raise HTTPException(status_code=403, detail="Cannot manage members of the platform org")
    if clerk_user_id == claims["user_id"]:
        # Same defense-in-depth self-guard as update_org_member_role — see
        # that function's docstring for why this can't rely solely on R1's
        # best-effort membership removal.
        raise HTTPException(status_code=403, detail="Cannot remove yourself")

    try:
        memberships = await list_organization_memberships(clerk_org_id)
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc
    current = next(
        (m for m in memberships if m["public_user_data"]["user_id"] == clerk_user_id), None
    )
    if current is None:
        raise HTTPException(status_code=404, detail="Member not found in organization")

    target_is_admin = current["role"] == "org:admin"
    if target_is_admin:
        await _set_org_scope(db, clerk_org_id)
        active_admins = await db.scalar(
            select(func.count())
            .select_from(ClerkAdminUser)
            .where(ClerkAdminUser.status == "active")
        )
        if active_admins is not None and active_admins <= 1:
            raise HTTPException(status_code=403, detail="Cannot remove the last active admin")

    try:
        await remove_organization_membership(clerk_org_id, clerk_user_id)
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    await _set_org_scope(db, clerk_org_id)
    if target_is_admin:
        await AdminUserRepo(db).deactivate(clerk_user_id)

    local_user = await db.scalar(select(Users).where(Users.clerk_user_id == clerk_user_id))
    if local_user is not None:
        local_user.status = "inactive"
        local_user.deactivated_at = utc_now()
        # Flush now, while app.org_id is still the TARGET org — see
        # update_org_member_role's docstring for why this must happen before
        # _set_org_scope re-points RLS away for the audit write below.
        await db.flush()

    await _set_org_scope(db, claims["tenant_id"])
    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_member_removed",
            "payload": {
                "target_clerk_org_id": clerk_org_id,
                "target_clerk_user_id": clerk_user_id,
                "target_email": await fetch_clerk_user_primary_email(clerk_user_id),
                "admin_role_deactivated": target_is_admin,
                "clerk_membership_revoked": True,
            },
        }
    )
    return SuccessResponse(success=True)
