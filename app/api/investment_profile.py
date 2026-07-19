from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.repo.InvestmentProfileRepo import InvestmentProfileRepo
from app.schemas.investment_profile import InvestmentProfileResponse

router = APIRouter(prefix="/investment-profile", tags=["investment-profile"])


@router.get("", response_model=InvestmentProfileResponse | None)
async def get_investment_profile(
    db: AsyncSession = Depends(get_db),
) -> InvestmentProfileResponse | None:
    """investmentProfile.get — null (never 404) when the org has no profile
    row yet; keeps the UI's empty states."""
    profile = await InvestmentProfileRepo(db).get_for_org()
    if profile is None:
        return None
    return InvestmentProfileResponse(
        firm_name=profile.firm_name,
        firm_type=profile.firm_type,
        aum_band=profile.aum_band,
        mandate=profile.mandate or {},
        weights=profile.weights or {},
        updated_at=profile.updated_at,
    )
