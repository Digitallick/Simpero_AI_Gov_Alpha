from app.core.config import get_settings

settings = get_settings()

# TODO: Replace this placeholder claim key with the actual Clerk JWT claim that holds the tenant ID.
# In Clerk's Organization-based multi-tenancy, this is typically "org_id" (present in session tokens
# when the user is a member of an organization). Confirm by inspecting a real token in the Clerk
# dashboard under: API Keys > Show JWT Template > decoded claims.
CLERK_TENANT_ID_CLAIM = "org_id"


def decode_clerk_jwt(token: str) -> dict:
    """
    Verify a Clerk-issued JWT and return its claims.

    Returns a dict with at minimum:
      - "tenant_id": str  (extracted from CLERK_TENANT_ID_CLAIM)
      - "user_id": str    (from the standard "sub" claim)
      - "raw_claims": dict

    Raises AuthenticationError on any verification failure.

    TODO — implement the following steps before production:

    1. Fetch Clerk's JWKS from settings.clerk_jwks_url:
           https://<your-clerk-frontend-api>/.well-known/jwks.json
       Use httpx with a short timeout. Cache the response with a TTL (e.g., 60 minutes)
       to avoid a network round-trip on every request and to prevent Clerk rate-limiting.

    2. Verify the JWT signature using python-jose:
           from jose import jwt, JWTError
           claims = jwt.decode(
               token,
               jwks,                          # fetched JWKS document
               algorithms=["RS256"],
               audience=settings.clerk_jwt_audience,
           )

    3. Validate required claims:
       - "exp" (expiry) — python-jose does this automatically
       - "aud" (audience) — python-jose does this when audience= is passed
       - CLERK_TENANT_ID_CLAIM — must be present and non-empty

    4. On JWTError or missing claims, raise AuthenticationError.
    """
    raise NotImplementedError(
        "decode_clerk_jwt is a stub. Implement JWKS verification before connecting to Clerk. "
        "See the TODO block above for required steps."
    )
