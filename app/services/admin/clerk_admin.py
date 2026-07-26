"""Clerk Backend API adapter for the admin portal. Every raw httpx call for
admin routes is confined here, mirroring app/core/security.py's
fetch_clerk_organization idiom (raw httpx, 5.0s timeout, Bearer secret key).

SDK decision: raw httpx, not the clerk-backend-api SDK — consistency with
the existing fetch_clerk_organization, ~6 endpoints, no new dependency in a
security-sensitive path, trivially mockable in tests.
"""

from datetime import UTC, datetime
from typing import Any, cast

import httpx
from fastapi import HTTPException

from app.core.config import get_settings
from app.models.organisation import OrgType

settings = get_settings()

CLERK_API_BASE = "https://api.clerk.com/v1"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.clerk_secret_key}"}


def clerk_created_at(data: dict[str, Any]) -> datetime:
    """Clerk timestamps (organizations, invitations, ...) are unix ms."""
    return datetime.fromtimestamp(data["created_at"] / 1000, tz=UTC)


def clerk_error_to_http(exc: httpx.HTTPError) -> HTTPException:
    """Translate a Clerk Backend API failure into the HTTPException a route
    should raise. Network/timeout errors -> 503 (our problem, not the
    caller's). Clerk 5xx -> 502. Clerk 4xx -> the same status code, with
    Clerk's own error message as detail (never leak secrets/full payloads)."""
    if isinstance(exc, httpx.HTTPStatusError):
        resp = exc.response
        if resp.status_code >= 500:
            return HTTPException(status_code=502, detail="Clerk API error")
        try:
            body = resp.json()
            detail = (body.get("errors") or [{}])[0].get("message") or resp.text
        except ValueError:
            detail = resp.text
        return HTTPException(status_code=resp.status_code, detail=detail)
    return HTTPException(status_code=503, detail="Unable to reach Clerk API")


async def create_organization(
    name: str, org_type: OrgType | None, created_by: str
) -> dict[str, Any]:
    """POST /organizations. `created_by` becomes an admin MEMBER of the new
    Clerk org — Clerk requires an initial admin to create one. The caller
    (organizations.py's create_org) removes this membership immediately
    after; platform admins are not meant to remain members of client orgs."""
    payload: dict[str, Any] = {"name": name, "created_by": created_by}
    if org_type is not None:
        payload["public_metadata"] = {"type": org_type.value}
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{CLERK_API_BASE}/organizations", headers=_headers(), json=payload
        )
        resp.raise_for_status()
        return resp.json()


async def list_organizations(limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    """GET /organizations?limit=&offset=. Caller (the router) filters out the
    platform org and handles pagination beyond one page if needed."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{CLERK_API_BASE}/organizations",
            headers=_headers(),
            params={"limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        body = resp.json()
        return cast("list[dict[str, Any]]", body["data"] if isinstance(body, dict) else body)


async def create_organization_invitation(
    org_id: str,
    email: str,
    role: str,
    redirect_url: str,
    inviter_user_id: str | None = None,
) -> dict[str, Any]:
    """POST /organizations/{org_id}/invitations. redirect_url is a required
    parameter (D1) — no hardcoded default here; callers build it from
    settings.app_base_url based on which portal the invited role lands in."""
    payload: dict[str, Any] = {
        "email_address": email,
        "role": role,
        "redirect_url": redirect_url,
    }
    if inviter_user_id is not None:
        payload["inviter_user_id"] = inviter_user_id
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{CLERK_API_BASE}/organizations/{org_id}/invitations",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def list_organization_invitations(
    org_id: str, status: str = "pending", limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    """GET /organizations/{org_id}/invitations?status=&limit=&offset=."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{CLERK_API_BASE}/organizations/{org_id}/invitations",
            headers=_headers(),
            params={"status": status, "limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        body = resp.json()
        return cast("list[dict[str, Any]]", body["data"] if isinstance(body, dict) else body)


async def revoke_organization_invitation(
    org_id: str, invitation_id: str, requesting_user_id: str
) -> dict[str, Any]:
    """POST /organizations/{org_id}/invitations/{invitation_id}/revoke."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{CLERK_API_BASE}/organizations/{org_id}/invitations/{invitation_id}/revoke",
            headers=_headers(),
            json={"requesting_user_id": requesting_user_id},
        )
        resp.raise_for_status()
        return resp.json()


async def list_organization_memberships(
    org_id: str, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    """GET /organizations/{org_id}/memberships?limit=&offset=. Each item's
    public_user_data carries user_id/identifier(email)/first_name/last_name;
    role is the raw "org:admin" | "org:member" string."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{CLERK_API_BASE}/organizations/{org_id}/memberships",
            headers=_headers(),
            params={"limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        body = resp.json()
        return cast("list[dict[str, Any]]", body["data"] if isinstance(body, dict) else body)


async def remove_organization_membership(org_id: str, member_user_id: str) -> None:
    """DELETE /organizations/{org_id}/memberships/{member_user_id}."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.delete(
            f"{CLERK_API_BASE}/organizations/{org_id}/memberships/{member_user_id}",
            headers=_headers(),
        )
        resp.raise_for_status()


async def update_organization_membership(
    org_id: str, member_user_id: str, role: str
) -> dict[str, Any]:
    """PATCH /organizations/{org_id}/memberships/{member_user_id}, body
    {"role": role}. Verified against the live Clerk Backend API from inside
    the running dev container: returns 200 with the full updated
    organization_membership object (same shape as list_organization_memberships'
    items), role reflecting the new value."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.patch(
            f"{CLERK_API_BASE}/organizations/{org_id}/memberships/{member_user_id}",
            headers=_headers(),
            json={"role": role},
        )
        resp.raise_for_status()
        return resp.json()


async def delete_organization(org_id: str) -> None:
    """DELETE /organizations/{org_id}."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.delete(f"{CLERK_API_BASE}/organizations/{org_id}", headers=_headers())
        resp.raise_for_status()


async def fetch_clerk_user(clerk_user_id: str) -> dict[str, Any]:
    """GET /users/{user_id} — returns email_addresses + primary_email_address_id."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{CLERK_API_BASE}/users/{clerk_user_id}", headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def fetch_clerk_user_primary_email(clerk_user_id: str) -> str | None:
    """Extract the primary email address string, or None on any failure.
    Email is display/audit only and refreshable later — admin provisioning
    must not fail just because this lookup is unreachable (see
    _ensure_admin_provisioned in app/core/admin_dependencies.py)."""
    try:
        user = await fetch_clerk_user(clerk_user_id)
    except httpx.HTTPError:
        return None
    primary_id = user.get("primary_email_address_id")
    for addr in user.get("email_addresses") or []:
        if addr.get("id") == primary_id:
            return addr.get("email_address")
    return None
