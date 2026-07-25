from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import _admin_actor, get_admin_db, require_org_admin
from app.core.config import get_settings
from app.models.clerk_admin_user import AdminType, ClerkAdminUser
from app.models.organisation import Users, utc_now
from app.repo.AdminUserRepo import AdminUserRepo
from app.repo.HumanAuditRepo import HumanAuditRepo
from app.schemas.admin.members import MemberResponse, UpdateMemberRoleRequest
from app.schemas.common import SuccessResponse
from app.services.admin.clerk_admin import (
    clerk_error_to_http,
    remove_organization_membership,
    update_organization_membership,
)

settings = get_settings()

router = APIRouter(prefix="/members", tags=["admin"])

_CLERK_ROLE = {"member": "org:member", "admin": "org:admin"}


def _status(value: str) -> Literal["active", "inactive"]:
    return cast(Literal["active", "inactive"], value)


@router.get("", response_model=list[MemberResponse])
async def list_members(
    db: AsyncSession = Depends(get_admin_db),
    claims: dict[str, Any] = Depends(require_org_admin),
) -> list[MemberResponse]:
    """From the RLS-scoped `users` table — no WHERE org_id here, RLS alone
    restricts this to the caller's own org (members ARE product users;
    reading under RLS is fine, only *provisioning* an admin as a product
    user is avoided). Soft-deleted (removed) members stay visible, with
    status="inactive", so admins can see who's been removed and re-invite
    them from the same screen."""
    result = await db.execute(select(Users))
    return [
        MemberResponse(
            id=user.id,
            clerk_user_id=user.clerk_user_id,
            name=user.name,
            email=user.email,
            role=user.role,
            status=_status(user.status),
        )
        for user in result.scalars().all()
    ]


@router.delete("/{user_id}", response_model=SuccessResponse)
async def remove_member(
    user_id: int,
    claims: dict[str, Any] = Depends(require_org_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> SuccessResponse:
    """Member-removal semantics: primary = revoke the Clerk org membership;
    secondary = soft-delete the local `users` row (status -> 'inactive',
    deactivated_at stamped; RLS-scoped). Reversible by re-inviting — the row
    is reactivated on the re-invited member's next login, see
    _ensure_user_provisioned in app/core/dependencies.py.

    Since the role-change feature (PATCH /admin/members/{user_id}) lets a
    member hold an active `clerk_admin_users` row at the same time as their
    `users` row, this also deactivates that admin row if one exists — a
    deleted member must not be left with dangling `/admin` portal access.
    The last-active-admin guard below now covers this endpoint too, not just
    the PATCH demote path (was previously scaffolded-but-inert here, per R3,
    back when admins could never also hold a `users` row)."""
    target = await db.get(Users, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.clerk_user_id == claims["user_id"]:
        # The caller has no `users` row (admins are admin-only), so this can
        # only ever fire if an admin is somehow also the target row — kept
        # as a guard anyway since the target is looked up by path param, not
        # assumed absent.
        raise HTTPException(status_code=403, detail="Cannot remove yourself")

    target_admin_row = await AdminUserRepo(db).get_by_clerk_id(target.clerk_user_id)
    target_is_active_admin = target_admin_row is not None and target_admin_row.status == "active"
    if target_is_active_admin:
        active_admins = await db.scalar(
            select(func.count())
            .select_from(ClerkAdminUser)
            .where(ClerkAdminUser.status == "active")
        )
        if active_admins is not None and active_admins <= 1:
            raise HTTPException(status_code=403, detail="Cannot remove the last active admin")

    try:
        await remove_organization_membership(claims["tenant_id"], target.clerk_user_id)
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    removed_clerk_user_id, removed_email = target.clerk_user_id, target.email
    target.status = "inactive"
    target.deactivated_at = utc_now()
    if target_is_active_admin:
        await AdminUserRepo(db).deactivate(removed_clerk_user_id)

    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_member_removed",
            "payload": {
                "removed_clerk_user_id": removed_clerk_user_id,
                "removed_email": removed_email,
                "admin_role_deactivated": target_is_active_admin,
                "clerk_membership_revoked": True,
            },
        }
    )
    return SuccessResponse(success=True)


@router.patch("/{user_id}", response_model=MemberResponse)
async def update_member_role(
    user_id: int,
    payload: UpdateMemberRoleRequest,
    claims: dict[str, Any] = Depends(require_org_admin),
    db: AsyncSession = Depends(get_admin_db),
) -> MemberResponse:
    """Own-org role change — keeps `users.role`, the Clerk org membership
    role, and `clerk_admin_users` in sync. Guard order matters: self-change,
    last-admin, and no-op checks all happen before the external Clerk call
    (mirrors remove_member's discipline of never mutating Clerk speculatively)."""
    target = await db.get(Users, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.clerk_user_id == claims["user_id"]:
        raise HTTPException(status_code=403, detail="Cannot change your own role")

    old_role = target.role
    new_role = payload.role
    if old_role == "admin" and new_role == "member":
        active_admins = await db.scalar(
            select(func.count())
            .select_from(ClerkAdminUser)
            .where(ClerkAdminUser.status == "active")
        )
        if active_admins is not None and active_admins <= 1:
            raise HTTPException(status_code=403, detail="Cannot demote the last active admin")

    if new_role == old_role:
        return MemberResponse(
            id=target.id,
            clerk_user_id=target.clerk_user_id,
            name=target.name,
            email=target.email,
            role=target.role,
            status=_status(target.status),
        )

    try:
        await update_organization_membership(
            claims["tenant_id"], target.clerk_user_id, _CLERK_ROLE[new_role]
        )
    except httpx.HTTPError as exc:
        raise clerk_error_to_http(exc) from exc

    org_id, actor_id, actor_email = await _admin_actor(db, claims)
    if new_role == "admin":
        admin_type = (
            AdminType.platform
            if claims["tenant_id"] == settings.simpero_platform_org_id
            else AdminType.client
        )
        await AdminUserRepo(db).reactivate_or_create(
            {
                "clerk_user_id": target.clerk_user_id,
                "clerk_org_id": claims["tenant_id"],
                "org_id": org_id,
                "email": target.email,
                "admin_type": admin_type,
            }
        )
    else:
        await AdminUserRepo(db).deactivate(target.clerk_user_id)

    target.role = new_role

    await HumanAuditRepo(db).append(
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "event_type": "admin_member_role_changed",
            "payload": {
                "target_clerk_user_id": target.clerk_user_id,
                "target_email": target.email,
                "old_role": old_role,
                "new_role": new_role,
                "clerk_membership_role_updated": True,
                # target is always looked up by local int id, so a `users`
                # row is guaranteed to exist and be updated on this path.
                "users_role_updated": True,
            },
        }
    )
    return MemberResponse(
        id=target.id,
        clerk_user_id=target.clerk_user_id,
        name=target.name,
        email=target.email,
        role=target.role,
        status=_status(target.status),
    )
