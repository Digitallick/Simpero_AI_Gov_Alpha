from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_claims
from app.core.exceptions import AuthorizationError
from app.core.security import fetch_clerk_organization
from app.models.clerk_admin_user import AdminType
from app.models.organisation import Organisation, OrgType, utc_now
from app.repo.AdminUserRepo import AdminUserRepo
from app.services.admin.clerk_admin import fetch_clerk_user_primary_email

settings = get_settings()


async def _ensure_org_provisioned(session: AsyncSession, claims: dict[str, Any]) -> int:
    """The org branch of app/core/dependencies.py::_ensure_user_provisioned,
    extracted rather than imported — that function also creates a `users`
    row, which admin provisioning must never do. Returns the org's integer
    PK, fetching + inserting the Organisation row from Clerk if it doesn't
    exist yet. RLS-safe: only ever touches the caller's own org."""
    org_pk = await session.scalar(
        select(Organisation.id).where(Organisation.clerk_org_id == claims["tenant_id"])
    )
    if org_pk is not None:
        return org_pk

    clerk_org = await fetch_clerk_organization(claims["tenant_id"])
    raw_type = (clerk_org.get("public_metadata") or {}).get("type")
    try:
        org_type = OrgType(raw_type) if raw_type else None
    except ValueError:
        org_type = None
    await session.execute(
        pg_insert(Organisation)
        .values(
            clerk_org_id=claims["tenant_id"],
            name=clerk_org.get("name") or claims["tenant_id"],
            type=org_type,
        )
        .on_conflict_do_nothing(index_elements=["clerk_org_id"])
    )
    org_pk = await session.scalar(
        select(Organisation.id).where(Organisation.clerk_org_id == claims["tenant_id"])
    )
    if org_pk is None:  # pragma: no cover — RLS misconfig would surface here
        raise RuntimeError("Organisation row not visible after provisioning")
    return org_pk


async def _ensure_admin_provisioned(session: AsyncSession, claims: dict[str, Any]) -> None:
    """JIT admin bootstrap + the D3 downgrade-only sync + re-invite
    reactivation. The ONE place the JWT org_role is trusted — guards
    authorize off the clerk_admin_users row, never off org_role directly."""
    repo = AdminUserRepo(session)
    existing = await repo.get_by_clerk_id(claims["user_id"])
    is_admin_now = claims.get("org_role") == "admin"

    if existing is not None and existing.status == "active":
        # D3 downgrade-only sync: restructured from _ensure_user_provisioned's
        # straight short-circuit — this branch must still run the sync
        # before returning, so it fires on every request an already-
        # provisioned admin makes. Strictly monotonic toward less access: an
        # active row whose current token says "not admin" gets revoked. The
        # JWT is trusted for de-authorization only.
        #
        # Grace window: a row this app just promoted (updated_at set by the
        # reactivate path within the last `grace` seconds) is exempted once —
        # it's likely a stale JWT that hasn't refreshed to reflect the
        # promotion yet, not a genuine out-of-band demotion. A real
        # Clerk-dashboard demotion hits a row whose updated_at predates this
        # app's involvement (or is NULL), so the grace check fails and D3
        # still deactivates same-request, exactly as before this window
        # existed.
        if not is_admin_now:
            grace = timedelta(seconds=settings.admin_role_sync_grace_seconds)
            if existing.updated_at is not None and (utc_now() - existing.updated_at) < grace:
                return
            await repo.deactivate(claims["user_id"])
        return  # active and still admin per JWT -> nothing to do

    if not is_admin_now:
        return  # not an admin per Clerk, and no active row -> no grant (fail closed)

    # Reaches here only when is_admin_now is True AND (no row exists yet OR
    # the existing row is inactive) — JIT-create and re-invite reactivation
    # are the same write: reactivate_or_create's ON CONFLICT DO UPDATE
    # handles both, so both paths converge here instead of the old
    # create-only upsert(). Safe to trust org_role for this: it can only be
    # True on the caller's current, signature-verified Clerk JWT, which
    # requires a real, auditable Clerk-side action (re-inviting them as
    # admin) to have happened — the same trust boundary the JIT-create path
    # below already relied on, just extended to a row that already exists.
    org_pk = await session.scalar(
        select(Organisation.id).where(Organisation.clerk_org_id == claims["tenant_id"])
    )  # guaranteed present — get_admin_db runs _ensure_org_provisioned first
    email = await fetch_clerk_user_primary_email(claims["user_id"])
    admin_type = (
        AdminType.platform
        if claims["tenant_id"] == settings.simpero_platform_org_id
        else AdminType.client
    )
    await repo.reactivate_or_create(
        {
            "clerk_user_id": claims["user_id"],
            "clerk_org_id": claims["tenant_id"],
            "org_id": org_pk,
            "email": email,
            "admin_type": admin_type,
        }
    )


async def get_admin_db(
    claims: dict[str, Any] = Depends(get_claims),
) -> AsyncGenerator[AsyncSession, None]:
    """Admin-route analogue of app/core/dependencies.py::get_db — same
    SET-LOCAL-first / transaction-scoping discipline (see that function's
    docstring for the full PgBouncer rationale), but provisions the admin
    identity row (clerk_admin_users), not a product `users` row. Client
    admins are admin-only: this never calls _ensure_user_provisioned.

    Provisioning runs in its OWN transaction, committed before the second
    (yielded) transaction opens — deliberately NOT a single
    `session.begin(); ...; yield` like get_db's. A guard (require_org_admin /
    require_platform_admin) evaluates AFTER this generator yields, and
    FastAPI throws a guard's AuthorizationError back into this generator at
    the yield point; if provisioning shared that transaction, the resulting
    rollback would silently undo the D3 downgrade sync's UPDATE on every
    single request that needs it — the exact request where an admin has
    just been demoted always ends in the guard rejecting, so the deactivate
    would never durably persist. Splitting into two transactions on the same
    session (each re-issuing SET LOCAL, since PgBouncer may hand a different
    backend connection to the next transaction either way) makes the JIT
    create / D3 sync commit independently of whatever the rest of the
    request does.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.org_id', :tid, true)"),
                {"tid": claims["tenant_id"]},
            )
            await _ensure_org_provisioned(session, claims)
            await _ensure_admin_provisioned(session, claims)
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.org_id', :tid, true)"),
                {"tid": claims["tenant_id"]},
            )
            yield session


async def _set_org_scope(session: AsyncSession, clerk_org_id: str) -> None:
    """Re-points RLS for the remainder of get_admin_db's already-open
    transaction (the cross-org platform-admin write path's only way around
    the org_isolation RLS policy — see platform_members.py's role-change
    endpoint). SET LOCAL persists until that transaction ends, not until some
    inner block exits, so re-issuing it mid-handler safely re-scopes
    subsequent queries on this session to a different org — no nested
    transaction (none would work anyway: the handler already runs inside
    get_admin_db's open session.begin() block). All writes made under
    different scopes this way share ONE transaction with the rest of the
    request — same non-atomicity every other admin route already accepts
    between an external Clerk call and its local audit write.
    """
    await session.execute(
        text("SELECT set_config('app.org_id', :tid, true)"), {"tid": clerk_org_id}
    )


async def require_org_admin(
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_admin_db),
) -> dict[str, Any]:
    """clerk_admin_users is authoritative — this does NOT trust the JWT
    org_role directly. get_admin_db has already provisioned the row (for a
    legitimate admin) and run the D3 downgrade sync before this runs."""
    row = await AdminUserRepo(db).get_by_clerk_id(claims["user_id"])
    if row is None or row.status != "active":
        raise AuthorizationError("Org admin privileges required")
    return claims


async def require_platform_admin(
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_admin_db),
) -> dict[str, Any]:
    """Fails closed when simpero_platform_org_id is unset — the platform
    surface then denies everyone rather than defaulting open."""
    platform_org = settings.simpero_platform_org_id
    if not platform_org:
        raise AuthorizationError("Platform admin surface not configured")
    row = await AdminUserRepo(db).get_by_clerk_id(claims["user_id"])
    if (
        row is None
        or row.status != "active"
        or row.admin_type != AdminType.platform
        or claims["tenant_id"] != platform_org
    ):
        raise AuthorizationError("Platform admin privileges required")
    return claims


async def _admin_actor(db: AsyncSession, claims: dict[str, Any]) -> tuple[int, str, str | None]:
    """(org_id, actor_id, actor_email) for the audit rows admin routers
    append — resolved from the clerk_admin_users row (not UserRepo; admins
    have no `users` row). Shared here since every mutating admin router
    (invitations/members/organizations/platform_invitations) needs it."""
    row = await AdminUserRepo(db).get_by_clerk_id(claims["user_id"])
    assert row is not None  # provisioned by get_admin_db; guard already confirmed active
    return row.org_id, claims["user_id"], row.email
