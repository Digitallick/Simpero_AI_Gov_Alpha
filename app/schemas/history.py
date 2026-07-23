from datetime import datetime
from typing import Any, Literal

from app.schemas.common import CamelModel, SuccessResponse


class HistorySummaryResponse(CamelModel):
    """history.list row. The frozen contract types this output as `any` —
    shape below matches the legacy denormalized summary fields, computed
    from memo_json at read time (see app/services/memo_summary.py)."""

    id: str  # == session_id; the frontend's list rows key off this
    session_id: str
    deal_id: str | None
    file_name: str
    created_at: datetime
    composed_at: datetime | None

    page_count: int | None
    claims_extracted: int | None
    claims_matched: int | None
    claims_flagged: int | None
    match_rate: float | None
    verdict: Literal["PROCEED", "CAUTION", "DECLINE"] | None
    score: float | None
    selected_frameworks: list[str] | None


class HistoryGetResponse(CamelModel):
    memo: dict[str, Any]
    deal_id: str | None


class ClearAllResponse(SuccessResponse):
    deleted_count: int
