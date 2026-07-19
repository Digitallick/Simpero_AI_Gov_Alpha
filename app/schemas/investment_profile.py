from datetime import datetime
from typing import Any

from app.schemas.common import CamelModel


class InvestmentProfileResponse(CamelModel):
    firm_name: str | None
    firm_type: str | None
    aum_band: str | None
    mandate: dict[str, Any]
    weights: dict[str, Any]
    updated_at: datetime
