"""corroboration events

Permanent, tamper-proof record of every outside-source check the system ever
runs against a claim (an OFAC screen, an entity lookup, ...). Same
INSERT-only pattern as human_audit_log (7175bc85ffb0): rows append, UPDATE
and DELETE are revoked at the database level, and RLS is FORCEd so even the
owning role can't read/modify across orgs or bypass immutability.

Strictly additive by construction, not by convention: this table has no
column that could hold a claim's original document-sourced value, so there
is no code path here that could overwrite one. A claim's status changing
(e.g. to `conflicted`) happens on the claims table itself, not this one.

No writer yet — nothing in this codebase performs an outside-source check
yet, so nothing appends here until that lands (same situation ai_audit_log
was in before its LLM wrapper existed).

Revision ID: e960a3366cf7
Revises: 920070316626
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e960a3366cf7"
down_revision: str | None = "920070316626"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corroboration_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("outside_source", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_corroboration_events_org_id"), "corroboration_events", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_corroboration_events_claim_id"),
        "corroboration_events",
        ["claim_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_corroboration_events_outside_source"),
        "corroboration_events",
        ["outside_source"],
        unique=False,
    )

    # Tenant data, so RLS is enabled HERE — in the same migration that creates
    # the table. Same reasoning as claims/human_audit_log: a window with the
    # table unprotected is a window where any org can read another org's
    # corroboration history, and ENABLE with no policy yet is default-deny
    # (silent zero rows, not an error).
    op.execute("ALTER TABLE corroboration_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON corroboration_events
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)
    # FORCE, on top of ENABLE: same as human_audit_log — a plain ENABLE
    # exempts the table owner (doadmin) from its own policy. This table is a
    # "permanent, tamper-proof record" by the ticket's own description, so
    # that gap matters here too, not just for human_audit_log.
    op.execute("ALTER TABLE corroboration_events FORCE ROW LEVEL SECURITY")

    # The bootstrap migration's ALTER DEFAULT PRIVILEGES already granted
    # dd_app SELECT/INSERT/UPDATE/DELETE on every table doadmin creates.
    # Immutability is enforced by revoking UPDATE/DELETE back off, here, in
    # the same migration that creates the table — never as an
    # application-level guard.
    op.execute("REVOKE UPDATE, DELETE ON corroboration_events FROM dd_app")


def downgrade() -> None:
    op.execute("ALTER TABLE corroboration_events NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON corroboration_events")
    op.drop_index(op.f("ix_corroboration_events_outside_source"), table_name="corroboration_events")
    op.drop_index(op.f("ix_corroboration_events_claim_id"), table_name="corroboration_events")
    op.drop_index(op.f("ix_corroboration_events_org_id"), table_name="corroboration_events")
    op.drop_table("corroboration_events")
