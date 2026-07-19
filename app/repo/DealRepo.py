from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import Deal
from app.repo.BaseRepo import BaseRepo


class DealRepo(BaseRepo[Deal, dict]):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def create(self, data: dict, **kwargs: object) -> Deal:
        deal = Deal(**data)
        self.session.add(deal)
        return deal

    async def get_by_id(self, id: object) -> Deal | None:
        return await self.session.get(Deal, id)

    async def list(self) -> list[Deal]:
        """RLS scopes this to the request's org — no WHERE org_id here."""
        result = await self.session.execute(select(Deal).order_by(Deal.created_at.desc()))
        return list(result.scalars().all())

    async def dashboard_aggregates(
        self, current_start: datetime, prior_start: datetime, prior_end: datetime
    ) -> dict:
        """Backs deals.dashboardStats — one round trip for every count/sum it
        needs. RLS scopes all of it to the request's org."""
        size_expr = func.coalesce(Deal.deal_size_max_usd, Deal.deal_size_min_usd, 0)
        row = (
            await self.session.execute(
                select(
                    func.count().label("total_deals"),
                    func.coalesce(func.sum(size_expr), 0).label("total_pipeline_value"),
                    func.count(Deal.created_at >= current_start).filter(
                        Deal.created_at >= current_start
                    ),
                    func.coalesce(
                        func.sum(size_expr).filter(Deal.created_at >= current_start), 0
                    ),
                    func.count().filter(
                        Deal.created_at >= prior_start, Deal.created_at < prior_end
                    ),
                    func.coalesce(
                        func.sum(size_expr).filter(
                            Deal.created_at >= prior_start, Deal.created_at < prior_end
                        ),
                        0,
                    ),
                )
            )
        ).one()
        return {
            "total_deals": row[0],
            "total_pipeline_value": row[1],
            "current_window_deals": row[2],
            "current_window_value": row[3],
            "prior_window_deals": row[4],
            "prior_window_value": row[5],
        }
