from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.investment_profile import InvestmentProfile
from app.repo.BaseRepo import BaseRepo


class InvestmentProfileRepo(BaseRepo[InvestmentProfile, dict]):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def create(self, data: dict, **kwargs: object) -> InvestmentProfile:
        # Plain insert only — the upsert (create-or-update on org_id) that
        # investmentProfile.upsert needs arrives in Phase 2.
        profile = InvestmentProfile(**data)
        self.session.add(profile)
        return profile

    async def get_by_id(self, id: object) -> InvestmentProfile | None:
        return await self.session.get(InvestmentProfile, id)

    async def get_for_org(self) -> InvestmentProfile | None:
        """One row per org (org_id is UNIQUE) — RLS scopes this to the
        request's org, no WHERE org_id needed."""
        result = await self.session.execute(select(InvestmentProfile).limit(1))
        return result.scalar_one_or_none()
