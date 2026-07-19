import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base
from app.models.organisation import Funds, Organisation

# INSERT-only enforcement is at the database layer:
#   REVOKE UPDATE, DELETE ON ai_audit_log FROM dd_app;
# (see the migration that creates this table). Do NOT add application-level
# guards here — see human_audit_log.py for why.
#
# Table only, no writer yet: the LLM base wrapper that inserts rows here
# arrives with the real-pipeline effort. This model exists now so the schema
# is locked in ahead of that work.
#
# OD-1 (Option A) privacy rule: this table records hashes and references
# ONLY. Never add a column for prompt text, thinking/reasoning text, or raw
# tool inputs — that is exactly the class of data this table is designed to
# not hold.


class AiAuditLog(Base):
    """Append-only trail of every LLM/agent call (once the real pipeline has
    a writer). One row per model call: which agent, which model, what it cost
    in tokens/time, and a hash of the prompt it ran — never the prompt itself.
    """

    __tablename__ = "ai_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )

    # Tenant. Integer FK because organisation.id is a serial Integer — RLS joins
    # through to organisation.clerk_org_id, same idiom as funds/claims/human_audit_log.
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, index=True
    )
    fund_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(Funds.id), nullable=True)

    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # No FK: an agent's parent call may be pruned/rotated out independently —
    # same "reference, not enforced" choice as human_audit_log's deal_id/session_id.
    parent_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pass_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OD-1: a hash, never the prompt itself.
    system_prompt_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Names only (e.g. ["search_web", "read_file"]) — never call arguments/results.
    tool_call_names: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # No updated_at — audit log rows are write-once by definition.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
