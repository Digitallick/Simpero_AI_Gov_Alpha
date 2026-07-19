"""investment profiles

Contract-shaped per-org table only — the catalog's per-fund
investment_frameworks/workflow_stage_configs are deferred (decided
2026-07-18: only what alpha needs).

Revision ID: fb49da6a9bc0
Revises: dd80cdfaeb9a
Create Date: 2026-07-19 00:05:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fb49da6a9bc0"
down_revision: str | None = "dd80cdfaeb9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investment_profiles",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("firm_name", sa.Text(), nullable=True),
        sa.Column("firm_type", sa.Text(), nullable=True),
        sa.Column("aum_band", sa.Text(), nullable=True),
        sa.Column("mandate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id"),
    )
    op.create_index(
        op.f("ix_investment_profiles_org_id"), "investment_profiles", ["org_id"], unique=True
    )

    # RLS enabled + policy created in the same migration that creates the
    # table — same idiom as deals/funds/claims/sessions.
    op.execute("ALTER TABLE investment_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON investment_profiles
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON investment_profiles")
    op.drop_index(op.f("ix_investment_profiles_org_id"), table_name="investment_profiles")
    op.drop_table("investment_profiles")
