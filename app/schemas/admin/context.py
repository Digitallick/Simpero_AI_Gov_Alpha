from typing import Literal

from app.schemas.common import CamelModel


class OrgSummary(CamelModel):
    clerk_org_id: str
    name: str
    type: Literal["PE Firm", "Family Office"] | None = None


class AdminContextResponse(CamelModel):
    is_platform_admin: bool
    is_org_admin: bool
    org: OrgSummary
