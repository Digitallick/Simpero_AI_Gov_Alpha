from datetime import datetime

from app.schemas.common import CamelModel


class ActivityRowResponse(CamelModel):
    id: str
    created_at: datetime
    action: str
    session_id: str | None
    job_id: str | None  # always null in Phase 1 — no job model yet


class RecentActivityResponse(CamelModel):
    total: int
    warnings: int
    critical: int
    rows: list[ActivityRowResponse]
