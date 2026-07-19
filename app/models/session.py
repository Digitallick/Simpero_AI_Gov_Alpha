import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base
from app.models.deal import Deal
from app.models.organisation import Organisation, Users


class Session(Base):
    """One IC-memo analysis run for a deal. SLIM shape (decided 2026-07-19):
    the legacy denormalized summary stats (claims counts, match_rate,
    verdict/score, page_count, selected_frameworks) are NOT columns here —
    they're computed from memo_json at read time in the history/deals
    response layer.

    Named `Session`/`sessions` (not `MemoSession`/`memo_sessions` — renamed
    2026-07-19, Vansh's call): this IS the sessionId the wire contract keys
    on, and the wire contract (sessionId keys, /history paths) is unaffected
    by this rename — it only touches the DB table/model/repo internals.

    # ponytail: computed-at-read-time is fine at <=50 analyses/mo; if
    # History gets slow, re-denormalize those stats onto this table.
    """

    __tablename__ = "sessions"

    # This IS the sessionId the API keys on — no separate session identifier.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )

    # Tenant. Integer FK because organisation.id is a serial Integer — RLS joins
    # through to organisation.clerk_org_id, same idiom as deals/funds/claims.
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(Users.id), nullable=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(Deal.id), nullable=False, index=True
    )

    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Full ICMemoResult (deliverables included) — null until the analysis job
    # that produces it completes. No writer in Phase 1; Phase 2's worker fills
    # this in.
    memo_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    composed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
