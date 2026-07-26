from datetime import datetime
from typing import Literal

from pydantic import EmailStr

from app.schemas.admin.invitations import InvitationResponse
from app.schemas.common import CamelModel


class CreateOrganizationRequest(CamelModel):
    name: str
    type: Literal["PE Firm", "Family Office"] | None = None
    account_manager_email: EmailStr | None = None


class OrganizationResponse(CamelModel):
    clerk_org_id: str
    name: str
    type: Literal["PE Firm", "Family Office"] | None = None
    created_at: datetime


class CreateOrgResult(CamelModel):
    clerk_org_id: str
    name: str
    type: Literal["PE Firm", "Family Office"] | None = None
    invitation: InvitationResponse | None = None
