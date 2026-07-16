from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.security import CLERK_TENANT_ID_CLAIM, decode_clerk_jwt
from app.core.database import AsyncSessionLocal


async def get_current_user(authorization: str = Header(...)) -> dict:
    """Extract and verify the Clerk JWT from the Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        return decode_clerk_jwt(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


async def get_db(
    authorization: str = Header(...),
) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that:
      1. Verifies the Clerk JWT and extracts org_id.
      2. Opens an async DB session.
      3. Issues SET LOCAL app.org_id as the FIRST statement inside the transaction.
      4. Yields the session to the route handler.
      5. Commits on success, rolls back on error, always closes.

    WHY SET LOCAL (not SET or SET SESSION):
      PgBouncer in transaction-pooling mode assigns a Postgres backend connection to a client
      only for the duration of one transaction. After commit/rollback, the backend connection
      is reclaimed and may be handed to a different tenant's next request.

      SET SESSION (connection-scoped) would persist on the connection after the transaction
      ends — leaking tenant_id to the next tenant that gets that connection from PgBouncer.

      SET LOCAL is transaction-scoped: it is automatically reset when the transaction ends,
      making it safe regardless of which backend connection PgBouncer assigns.

    WHY THIS MUST BE THE FIRST STATEMENT IN THE TRANSACTION:
      PostgreSQL evaluates RLS policies at statement execution time using current_setting().
      If any query runs before SET LOCAL, it sees NULL or a stale tenant_id and either
      returns no rows or returns the wrong rows.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")

    try:
        claims = decode_clerk_jwt(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    tenant_id = claims.get(CLERK_TENANT_ID_CLAIM)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"JWT missing required claim '{CLERK_TENANT_ID_CLAIM}' — "
                "cannot establish tenant context"
            ),
        )

    async with AsyncSessionLocal() as session, session.begin():
        # SET LOCAL must be the first SQL in this transaction — see docstring above.
        await session.execute(
            text("SET LOCAL app.org_id = :tid"),
            {"tid": tenant_id},
        )
        try:
            yield session
        except Exception:
            # session.begin() context manager handles rollback on exception
            raise
