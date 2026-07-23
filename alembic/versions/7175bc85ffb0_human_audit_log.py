"""human audit log

Append-only trail of every human action (mutations, exports, sign-out, ...).
INSERT/SELECT only for dd_app — UPDATE/DELETE are revoked at the database
level in this same migration, which is the actual immutability guarantee.
There is no application-level check anywhere for this and there must never
be one (it would be bypassable and would give false assurance).

Revision ID: 7175bc85ffb0
Revises: c9aaf2c46b16
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7175bc85ffb0"
down_revision: str | None = "c9aaf2c46b16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "human_audit_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("actor_email", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("deal_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_human_audit_log_org_id"), "human_audit_log", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_human_audit_log_event_type"), "human_audit_log", ["event_type"], unique=False
    )
    op.create_index(
        op.f("ix_human_audit_log_deal_id"), "human_audit_log", ["deal_id"], unique=False
    )
    op.create_index(
        op.f("ix_human_audit_log_session_id"), "human_audit_log", ["session_id"], unique=False
    )

    # RLS is enabled in the same migration that creates the table — a window
    # with the table unprotected is a window where any org can read another
    # org's audit trail, and ENABLE ROW LEVEL SECURITY with no policy yet would
    # silently return zero rows rather than error. Same idiom as funds/claims.
    op.execute("ALTER TABLE human_audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON human_audit_log
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)
    # FORCE, on top of ENABLE: a plain ENABLE exempts the table owner (doadmin)
    # from its own RLS policy, so a blocking policy alone is bypassed by
    # whoever owns the table. This is the one table in the repo where that
    # gap matters — audit rows must stay unreadable-outside-org and
    # unmodifiable even to the owning role, not just to dd_app.
    op.execute("ALTER TABLE human_audit_log FORCE ROW LEVEL SECURITY")

    # The bootstrap migration's ALTER DEFAULT PRIVILEGES already granted
    # dd_app SELECT/INSERT/UPDATE/DELETE on every table doadmin creates
    # (including this one). Immutability is enforced by revoking UPDATE/DELETE
    # back off, here, in the same migration that creates the table — never as
    # an application-level guard.
    op.execute("REVOKE UPDATE, DELETE ON human_audit_log FROM dd_app")


def downgrade() -> None:
    op.execute("ALTER TABLE human_audit_log NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON human_audit_log")
    op.drop_index(op.f("ix_human_audit_log_session_id"), table_name="human_audit_log")
    op.drop_index(op.f("ix_human_audit_log_deal_id"), table_name="human_audit_log")
    op.drop_index(op.f("ix_human_audit_log_event_type"), table_name="human_audit_log")
    op.drop_index(op.f("ix_human_audit_log_org_id"), table_name="human_audit_log")
    op.drop_table("human_audit_log")
