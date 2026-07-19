import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base
from app.models.organisation import Organisation

# INSERT-only enforcement is at the database layer:
#   REVOKE UPDATE, DELETE ON human_audit_log FROM dd_app;
# (see the migration that creates this table). Do NOT add application-level
# guards here — they can be bypassed by another code path or a future
# developer and would give false assurance of immutability.


class HumanAuditLog(Base):
    """Append-only trail of every human action (mutations, exports, sign-out,
    ...). Sole write path is HumanAuditRepo.append(); there is no update/delete
    path anywhere in the app, and the database refuses them regardless.
    """

    __tablename__ = "human_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )

    # Tenant. Integer FK because organisation.id is a serial Integer — RLS joins
    # through to organisation.clerk_org_id, same idiom as funds/claims.
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, index=True
    )

    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Denormalized so an audit row still reads sensibly even if the user is
    # later renamed/removed — audit stability over normalization here.
    actor_email: Mapped[str | None] = mapped_column(String(100), nullable=True)

    event_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # No updated_at — audit log rows are write-once by definition.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
