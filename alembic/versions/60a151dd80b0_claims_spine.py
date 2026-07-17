"""claims spine

The deterministic table every downstream pass (Verify, Compose, Score, AskMe,
audit) reads from. One spine for all three source formats: they differ only in
the shape of their location, so the format-specific columns are null per row.

The shape is defined by contracts/claims.schema.json, not here. The CHECKs below
are that contract's rules made non-bypassable in the database — application code
can be wrong, a second writer can be added, but a CHECK holds.

Deliberately absent:
  file       — the contract makes it debug-only; data_source_id is the identity.
  element_id — no elements table in alpha (Option C, 2026-07-17). table_group_id
               + fragment_order carry table grouping instead.

Revision ID: 60a151dd80b0
Revises: aace95a1c412
Create Date: 2026-07-17 02:59:58.536969

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "60a151dd80b0"
down_revision: str | None = "aace95a1c412"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("data_source_id", sa.UUID(), nullable=True),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("attribute", sa.Text(), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=True),
        sa.Column("period_kind", sa.String(length=1), nullable=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sheet", sa.Text(), nullable=True),
        sa.Column("cell_ref", sa.String(length=16), nullable=True),
        sa.Column("paragraph", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("verification_method", sa.String(length=24), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("table_group_id", sa.UUID(), nullable=True),
        sa.Column("fragment_order", sa.Integer(), nullable=True),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'pdf' AND page IS NOT NULL) OR (kind = 'xlsx' AND sheet IS NOT NULL AND cell_ref IS NOT NULL) OR (kind = 'docx' AND paragraph IS NOT NULL)",
            name="ck_claims_locator_matches_kind",
        ),
        sa.CheckConstraint("kind IN ('pdf', 'xlsx', 'docx')", name="ck_claims_kind"),
        sa.CheckConstraint(
            "period_kind IS NULL OR period_kind IN ('A', 'E', 'P')", name="ck_claims_period_kind"
        ),
        sa.CheckConstraint(
            "status <> 'missing' OR (char_start IS NULL AND char_end IS NULL)",
            name="ck_claims_missing_has_no_span",
        ),
        sa.CheckConstraint(
            "status = 'missing' OR kind = 'xlsx' OR (char_start IS NOT NULL AND char_end IS NOT NULL)",
            name="ck_claims_found_requires_span",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'cited', 'rejected', 'missing', 'conflicted', 'inconclusive', 'partially_verified', 'verified')",
            name="ck_claims_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('cited', 'conflicted', 'inconclusive', 'partially_verified', 'verified') OR verification_method IS NOT NULL",
            name="ck_claims_checked_requires_method",
        ),
        sa.CheckConstraint(
            "verification_method IS NULL OR verification_method IN ('exact_span', 'formula_reexecution', 'direct_read', 'reranker', 'human_review')",
            name="ck_claims_verification_method",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claims_attribute"), "claims", ["attribute"], unique=False)
    op.create_index(op.f("ix_claims_deal_id"), "claims", ["deal_id"], unique=False)
    op.create_index(op.f("ix_claims_org_id"), "claims", ["org_id"], unique=False)
    op.create_index(op.f("ix_claims_session_id"), "claims", ["session_id"], unique=False)
    op.create_index(op.f("ix_claims_status"), "claims", ["status"], unique=False)

    # Claims are tenant data, so RLS is enabled HERE — in the same migration
    # that creates the table, not a later one. A window in which the table
    # exists without RLS is a window in which any org can read any other org's
    # claims, and it would not fail loudly.
    #
    # The policy is created in the same migration for the mirror-image reason:
    # ENABLE ROW LEVEL SECURITY with no policy is default-deny, so dd_app would
    # silently see ZERO rows — no error, no permission denied, just an empty
    # result set that looks like "nothing was extracted".
    #
    # RLS applies to dd_app because the table is owned by doadmin (migrations
    # run as doadmin) and dd_app is not the owner. Non-forced RLS skips the
    # owner, so a claims table created by dd_app would silently bypass this.
    op.execute("ALTER TABLE claims ENABLE ROW LEVEL SECURITY")

    # Isolated by joining to the organisation that owns them — the same shape as
    # funds' policy in aace95a1c412. `true` on current_setting means "return
    # NULL if unset" rather than raising: an unset app.org_id then matches
    # nothing, which is the correct fail-closed behaviour.
    op.execute("""
        CREATE POLICY org_isolation ON claims
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)


def downgrade() -> None:
    # DROP TABLE removes the policy with it; dropped explicitly so the
    # downgrade reads as the exact inverse of the upgrade.
    op.execute("DROP POLICY IF EXISTS org_isolation ON claims")
    op.drop_index(op.f("ix_claims_status"), table_name="claims")
    op.drop_index(op.f("ix_claims_session_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_org_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_deal_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_attribute"), table_name="claims")
    op.drop_table("claims")
