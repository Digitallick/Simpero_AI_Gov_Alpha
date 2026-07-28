import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base
from app.models.claim import Claim
from app.models.organisation import Organisation

# INSERT-only enforcement is at the database layer:
#   REVOKE UPDATE, DELETE ON corroboration_events FROM dd_app;
# (see the migration that creates this table). Do NOT add application-level
# guards here — see human_audit_log.py for why.
#
# Table only, no writer yet: nothing in this codebase performs an outside-
# source check (OFAC screen, entity lookup, ...) yet, so nothing appends here
# until that lands. This model exists now so the schema is locked in ahead of
# that work, same situation as ai_audit_log.py before its LLM wrapper landed.


class CorroborationEvent(Base):
    """Permanent, tamper-proof record of every outside-source check ever run
    against a claim. Strictly additive by construction: this table has no
    column that could hold a claim's original value, so there is no code
    path here that could overwrite one — only claims.status changes (e.g. to
    `conflicted`), and that happens on the claims table, not this one.
    """

    __tablename__ = "corroboration_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )

    # Tenant. Integer FK because organisation.id is a serial Integer — RLS joins
    # through to organisation.clerk_org_id, same idiom as claims/human_audit_log.
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, index=True
    )

    # Which claim this check ran against. Real FK (unlike chunks.document_id)
    # because claims already exists.
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(Claim.id), nullable=False, index=True
    )

    # Which outside source ran the check, e.g. "ofac_screen", "entity_lookup".
    outside_source: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # The full result payload the outside source returned. Read-only history —
    # this is what a claim's status change (e.g. to `conflicted`) is based on,
    # never a replacement for the claim's own document-sourced value.
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # No updated_at — audit log rows are write-once by definition.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
