import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session
from app.repo.BaseRepo import BaseRepo


class SessionRepo(BaseRepo[Session, dict]):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def create(self, data: dict, **kwargs: object) -> Session:
        session_row = Session(**data)
        self.session.add(session_row)
        return session_row

    async def get_by_id(self, id: object) -> Session | None:
        return await self.session.get(Session, id)

    async def list_for_org(self) -> list[Session]:
        """RLS scopes this to the request's org — no WHERE org_id here."""
        result = await self.session.execute(select(Session).order_by(Session.created_at.desc()))
        return list(result.scalars().all())

    async def latest_for_deal(self, deal_id: uuid.UUID) -> Session | None:
        result = await self.session.execute(
            select(Session)
            .where(Session.deal_id == deal_id)
            .order_by(Session.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def delete(self, id: uuid.UUID) -> bool:
        """Returns True if a row was deleted. RLS scopes the delete to the
        request's org, same as every other query here."""
        result = await self.session.execute(
            delete(Session).where(Session.id == id).returning(Session.id)
        )
        return result.first() is not None

    async def delete_all(self) -> int:
        """Deletes every session RLS makes visible to this request (i.e. the
        caller's own org) and returns the count deleted."""
        count = await self.session.scalar(select(func.count()).select_from(Session))
        await self.session.execute(delete(Session))
        return count or 0
