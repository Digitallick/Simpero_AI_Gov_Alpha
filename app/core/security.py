import time
from typing import Any

import httpx
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

settings = get_settings()

_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0


async def _get_jwks(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch Clerk's JWKS, cached with a TTL to avoid a round-trip per request."""
    global _jwks_cache, _jwks_fetched_at
    fresh = time.monotonic() - _jwks_fetched_at < _JWKS_TTL_SECONDS
    if _jwks_cache is not None and fresh and not force_refresh:
        return _jwks_cache
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(settings.clerk_jwks_url)
        resp.raise_for_status()
    jwks: dict[str, Any] = resp.json()
    _jwks_cache = jwks
    _jwks_fetched_at = time.monotonic()
    return jwks


def _extract_org(claims: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (clerk_org_id, org_role) from the token claims.

    Clerk's current (v2) session token nests org info as claims["o"]["id"] /
    ["o"]["rol"]; older (v1) tokens use flat org_id / org_role ("org:admin").
    Support both — same approach as simpero_GOV_AI's clerkAuth.ts.
    """
    o = claims.get("o")
    if isinstance(o, dict) and isinstance(o.get("id"), str) and o["id"]:
        rol = o.get("rol")
        return o["id"], rol.removeprefix("org:") if isinstance(rol, str) else None
    org_id = claims.get("org_id")
    role = claims.get("org_role")
    return (
        org_id if isinstance(org_id, str) and org_id else None,
        role.removeprefix("org:") if isinstance(role, str) else None,
    )


async def decode_clerk_jwt(token: str) -> dict[str, Any]:
    """
    Verify a Clerk-issued JWT (RS256 via the instance's JWKS) and return:
      - "tenant_id": str        (Clerk org id — "no org, no access")
      - "user_id": str          (the "sub" claim)
      - "org_role": str | None  (e.g. "admin", "member")
      - "raw_claims": dict

    Raises AuthenticationError on any verification failure.

    Audience is intentionally not validated: Clerk's default session tokens
    carry no "aud" claim (matches the working setup in simpero_GOV_AI).
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise AuthenticationError("Malformed token") from exc

    def _find_key(jwks: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")),
            None,
        )

    key = _find_key(await _get_jwks())
    if key is None:
        # Unknown kid — Clerk may have rotated keys; refresh the cache once.
        key = _find_key(await _get_jwks(force_refresh=True))
    if key is None:
        raise AuthenticationError("Token signed with unknown key")

    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})
    except JWTError as exc:
        raise AuthenticationError(f"Token verification failed: {exc}") from exc

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthenticationError("Token missing 'sub' claim")

    tenant_id, org_role = _extract_org(claims)
    if not tenant_id:
        raise AuthenticationError("Token has no active organization")

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "org_role": org_role,
        "raw_claims": claims,
    }


async def fetch_clerk_organization(clerk_org_id: str) -> dict[str, Any]:
    """Fetch org details (name, public_metadata, ...) from Clerk's Backend API."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/organizations/{clerk_org_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
        )
        resp.raise_for_status()
        return resp.json()
