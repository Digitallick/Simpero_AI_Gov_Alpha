from datetime import datetime
from typing import Literal

from pydantic import EmailStr

from app.schemas.common import CamelModel


class CreateInvitationRequest(CamelModel):
    email_address: EmailStr
    # Shared between app/api/admin/invitations.py (org-admin's own-org
    # invites) and app/api/admin/platform_invitations.py (platform admin
    # inviting into an arbitrary client org). This schema only bounds the
    # value to "member" | "admin" — which of the two is actually accepted is
    # enforced per-endpoint: invitations.py still guards down to "member"
    # only, while platform_invitations.py accepts both.
    role: Literal["member", "admin"] = "member"


class InvitationResponse(CamelModel):
    """POST /admin/invitations and POST /admin/organizations/{id}/invitations
    both return this shape (no role — the caller already knows it forced
    "member")."""

    id: str
    email_address: str
    status: str
    created_at: datetime


class PendingInvitationResponse(CamelModel):
    """GET /admin/invitations list shape — includes role, unlike the POST
    response, since a listing can't assume the reader already knows it."""

    id: str
    email_address: str
    role: str
    status: str
    created_at: datetime
