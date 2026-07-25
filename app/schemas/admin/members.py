from typing import Literal

from app.schemas.common import CamelModel


class UpdateMemberRoleRequest(CamelModel):
    role: Literal["member", "admin"]


class MemberResponse(CamelModel):
    id: int
    clerk_user_id: str
    name: str | None
    email: str | None
    role: str
    status: Literal["active", "inactive"]


class OrgMemberResponse(CamelModel):
    """Platform-admin cross-org member view (GET
    /organizations/{clerk_org_id}/members) — sourced from Clerk's membership
    API merged with locally-soft-deleted `users` rows (see list_org_members).
    `id` is the Clerk org membership id for live members, or the
    `clerk_user_id` for a locally-inactive row with no Clerk membership left
    — distinct from MemberResponse, whose int id backs the org-admin's
    own-org DELETE /members/{user_id}."""

    id: str
    user_id: str
    name: str | None
    email: str | None
    role: str
    status: Literal["active", "inactive"]
