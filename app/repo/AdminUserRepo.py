from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clerk_admin_user import AdminType, ClerkAdminUser
from app.models.organisation import utc_now
from app.repo.BaseRepo import BaseRepo


class AdminUserRepo(BaseRepo[ClerkAdminUser, dict]):
    """Mirrors UserRepo's shape. clerk_admin_users is the admin-portal
    authorization source of truth — see app/core/admin_dependencies.py."""

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def create(self, data: dict, **kwargs: object) -> ClerkAdminUser:
        new_admin = ClerkAdminUser(**data)
        self.session.add(new_admin)
        return new_admin

    async def get_by_id(self, id: int) -> ClerkAdminUser | None:
        return await self.session.get(ClerkAdminUser, id)

    async def get_by_clerk_id(self, clerk_user_id: str) -> ClerkAdminUser | None:
        return await self.session.scalar(
            select(ClerkAdminUser).where(ClerkAdminUser.clerk_user_id == clerk_user_id)
        )

    async def upsert(self, data: dict) -> None:
        """JIT-provisioning insert: ON CONFLICT DO NOTHING on clerk_user_id —
        an existing admin row is never overwritten by this path. Same
        rationale as UserRepo.upsert."""
        await self.session.execute(
            pg_insert(ClerkAdminUser)
            .values(**data)
            .on_conflict_do_nothing(index_elements=["clerk_user_id"])
        )

    async def deactivate(self, clerk_user_id: str) -> None:
        """D3 downgrade-only sync + member-removal cleanup: flips status ->
        'inactive'. RLS (org_isolation) scopes this UPDATE to the caller's
        own org regardless of clerk_user_id. `updated_at` is set explicitly
        — the column's onupdate=utc_now only fires on ORM unit-of-work
        flushes, not this Core-style bulk update(), so it would otherwise go
        stale on every call (the D3 grace window in _ensure_admin_provisioned
        depends on this being accurate)."""
        await self.session.execute(
            update(ClerkAdminUser)
            .where(ClerkAdminUser.clerk_user_id == clerk_user_id)
            .values(status="inactive", updated_at=utc_now())
        )

    async def reactivate_or_create(self, data: dict) -> None:
        """Own-org promote path (PATCH /admin/members/{user_id}). ON CONFLICT
        DO UPDATE — distinct from upsert()'s DO NOTHING. The one path allowed
        to resurrect an inactive row, since it's driven by an explicit
        in-app promote action, not the passive D3 sync."""
        await self.session.execute(
            pg_insert(ClerkAdminUser)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["clerk_user_id"],
                set_={
                    "status": "active",
                    "admin_type": data["admin_type"],
                    "updated_at": utc_now(),
                },
            )
        )

    async def reactivate_if_exists(self, clerk_user_id: str, admin_type: AdminType) -> bool:
        """Cross-org promote path (PATCH .../organizations/{clerk_org_id}/members/{clerk_user_id})
        — best-effort, UPDATE-only, never inserts: an insert would need a
        valid `org_id` FK to a local Organisation row for the target org,
        which may not exist. Returns whether a row matched, so the caller can
        skip silently if not (mirrors the Users-row best-effort skip)."""
        result = await self.session.execute(
            update(ClerkAdminUser)
            .where(ClerkAdminUser.clerk_user_id == clerk_user_id)
            .values(status="active", admin_type=admin_type)
            .returning(ClerkAdminUser.id)
        )
        return result.first() is not None
