from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.schemas.deals import DealResponse

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("/", response_model=list[DealResponse])
async def list_deals(db: AsyncSession = Depends(get_db)) -> list[DealResponse]:
    """
    Authenticated endpoint demonstrating the tenant-context pattern.

    By the time this handler runs, get_db has already:
      - Verified the Clerk JWT
      - Opened a transaction
      - Issued SET LOCAL app.org_id = '<org_id>'

    Any query here automatically benefits from the RLS policy on the deals table,
    which filters rows by current_setting('app.org_id'). Do NOT add a manual
    WHERE org_id = ... — that would be redundant and could mask a broken RLS config.
    """
    # Stub: replace with actual Deal model query once the Deal model is implemented.
    result = await db.execute(text("SELECT 1"))
    _ = result.fetchall()
    return []
