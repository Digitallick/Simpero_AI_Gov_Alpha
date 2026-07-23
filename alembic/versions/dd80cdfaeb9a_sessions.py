"""sessions

SLIM shape (decided 2026-07-19): the legacy denormalized summary stats
(claims counts, match_rate, verdict/score, page_count, selected_frameworks)
are not columns here — computed from memo_json at read time.

Named `sessions`, not `memo_sessions` (renamed 2026-07-19, Vansh's call) —
same slim columns, same RLS. The wire contract (sessionId keys, /history
paths) is unaffected; this only touches the DB table/model/repo internals.

Revision ID: dd80cdfaeb9a
Revises: b26e963e0645
Create Date: 2026-07-19 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "dd80cdfaeb9a"
down_revision: str | None = "b26e963e0645"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("deal_id", sa.UUID(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("memo_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("composed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sessions_org_id"), "sessions", ["org_id"], unique=False)
    op.create_index(op.f("ix_sessions_deal_id"), "sessions", ["deal_id"], unique=False)

    # RLS enabled + policy created in the same migration that creates the
    # table — same idiom as deals/funds/claims. Not an audit table, so no
    # FORCE and no REVOKE — full DML via the bootstrap default privileges.
    op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON sessions
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON sessions")
    op.drop_index(op.f("ix_sessions_deal_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_org_id"), table_name="sessions")
    op.drop_table("sessions")
