from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_dependencies import get_admin_db
from app.core.config import get_settings
from app.core.dependencies import get_claims
from app.models.clerk_admin_user import AdminType
from app.models.organisation import Organisation
from app.repo.AdminUserRepo import AdminUserRepo
from app.schemas.admin.context import AdminContextResponse, OrgSummary

settings = get_settings()

router = APIRouter(tags=["admin"])


@router.get("/context", response_model=AdminContextResponse)
async def get_context(
    claims: dict[str, Any] = Depends(get_claims),
    db: AsyncSession = Depends(get_admin_db),
) -> AdminContextResponse:
    """Capability context for the admin portal shell. No require_* guard —
    any authenticated caller can read this; the mutating admin endpoints
    enforce the real authorization. isPlatformAdmin mirrors
    require_platform_admin's exact check (active clerk_admin_users row,
    admin_type == platform, AND the tenant match) — the tenant match alone
    is not sufficient, since an ordinary product member of the platform org
    would otherwise incorrectly read as a platform admin. isOrgAdmin mirrors
    require_org_admin's exact check: the clerk_admin_users row is
    authoritative, not the JWT org_role claim (which get_admin_db has
    already synced by the time this runs, but is never read directly here)."""
    org = await db.scalar(
        select(Organisation).where(Organisation.clerk_org_id == claims["tenant_id"])
    )
    assert org is not None  # get_admin_db JIT-provisions this row before the handler runs
    admin_row = await AdminUserRepo(db).get_by_clerk_id(claims["user_id"])
    return AdminContextResponse(
        is_platform_admin=(
            admin_row is not None
            and admin_row.status == "active"
            and admin_row.admin_type == AdminType.platform
            and claims["tenant_id"] == settings.simpero_platform_org_id
        ),
        is_org_admin=admin_row is not None and admin_row.status == "active",
        org=OrgSummary(
            clerk_org_id=org.clerk_org_id,
            name=org.name,
            type=org.type.value if org.type else None,
        ),
    )
