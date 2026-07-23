import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base
from app.models.organisation import Organisation

# Contract-shaped per-org table only — the catalog's per-fund
# investment_frameworks/workflow_stage_configs are deferred (decided
# 2026-07-18: only what alpha needs).


class InvestmentProfile(Base):
    __tablename__ = "investment_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )

    # One profile per org. Integer FK + UNIQUE because organisation.id is a
    # serial Integer — RLS joins through to organisation.clerk_org_id, same
    # idiom as deals/funds/claims.
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, unique=True, index=True
    )

    firm_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    firm_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    aum_band: Mapped[str | None] = mapped_column(Text, nullable=True)
    mandate: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    weights: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
