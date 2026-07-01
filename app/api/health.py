from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.schemas.health import DbHealthResponse, HealthResponse

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    # No auth dependency — must remain reachable even if Clerk or the DB is down.
    return HealthResponse(status="ok", environment=settings.environment)


@router.get("/health/db", response_model=DbHealthResponse)
async def db_health_check() -> DbHealthResponse:
    # No auth dependency — liveness probe must not require a valid JWT.
    # Connects via dd_app through PgBouncer to verify the full connection path.
    # Does NOT set app.org_id — RLS policies are not evaluated on SELECT 1.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return DbHealthResponse(status="ok", database="reachable")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unreachable: {exc}",
        )
