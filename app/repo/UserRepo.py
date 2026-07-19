from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organisation import Users
from app.repo.BaseRepo import BaseRepo


class UserRepo(BaseRepo[Users, dict]):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def create(self, data: dict, **kwargs: object) -> Users:
        """Create a new user in the database."""
        new_user = Users(**data)
        self.session.add(new_user)
        return new_user

    async def get_by_id(self, id: int) -> Users | None:
        return await self.session.get(Users, id)

    async def get_by_clerk_id(self, clerk_user_id: str) -> Users | None:
        return await self.session.scalar(select(Users).where(Users.clerk_user_id == clerk_user_id))

    async def upsert(self, data: dict) -> None:
        """JIT-provisioning insert: ON CONFLICT DO NOTHING on clerk_user_id,
        not a full insert-or-update — an existing user row is never
        overwritten by this path (name/email arrive later via
        POST /auth/sync-profile). Named to match the call site's intent,
        not literal upsert semantics.
        """
        await self.session.execute(
            pg_insert(Users).values(**data).on_conflict_do_nothing(index_elements=["clerk_user_id"])
        )
