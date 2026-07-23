"""ai audit log

Table only, no writer yet — the LLM base wrapper that inserts rows here
arrives with the real-pipeline effort (Vansh's call 2026-07-18, reversing
the earlier deferral of this table to that effort).

OD-1 (Option A): hashes and references only. No prompt text, thinking text,
or raw tool inputs is ever stored here — see the model's module docstring.

INSERT/SELECT only for dd_app — UPDATE/DELETE are revoked at the database
level in this same migration, same immutability guarantee as human_audit_log.
FORCE ROW LEVEL SECURITY on top of ENABLE, also matching human_audit_log:
this is an append-only audit table, so the table owner must not be exempt
from its own RLS policy either.

Revision ID: b26e963e0645
Revises: 418dc290d913
Create Date: 2026-07-18 00:15:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b26e963e0645"
down_revision: str | None = "418dc290d913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_audit_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("fund_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("pipeline_run_id", sa.UUID(), nullable=True),
        sa.Column("parent_agent_id", sa.UUID(), nullable=True),
        sa.Column("pass_number", sa.Integer(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("system_prompt_hash", sa.Text(), nullable=True),
        sa.Column("tool_call_names", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.ForeignKeyConstraint(["fund_id"], ["funds.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_audit_log_org_id"), "ai_audit_log", ["org_id"], unique=False)

    # RLS enabled + policy created in the same migration that creates the
    # table — same idiom as human_audit_log/funds/claims.
    op.execute("ALTER TABLE ai_audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON ai_audit_log
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)
    # FORCE, on top of ENABLE: see human_audit_log's migration for why a plain
    # ENABLE isn't enough for an append-only audit table (it exempts the
    # table owner from the policy; FORCE closes that gap).
    op.execute("ALTER TABLE ai_audit_log FORCE ROW LEVEL SECURITY")

    # The bootstrap migration's ALTER DEFAULT PRIVILEGES already granted
    # dd_app SELECT/INSERT/UPDATE/DELETE on every table doadmin creates
    # (including this one). Immutability is enforced by revoking UPDATE/DELETE
    # back off, here, in the same migration that creates the table — never as
    # an application-level guard.
    op.execute("REVOKE UPDATE, DELETE ON ai_audit_log FROM dd_app")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_audit_log NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON ai_audit_log")
    op.drop_index(op.f("ix_ai_audit_log_org_id"), table_name="ai_audit_log")
    op.drop_table("ai_audit_log")
