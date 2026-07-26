"""deals

Revision ID: 2f9ca0724bb9
Revises: 7175bc85ffb0
Create Date: 2026-07-18 00:05:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2f9ca0724bb9"
down_revision: str | None = "7175bc85ffb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_DEAL_STATUS = "sourcing"


def upgrade() -> None:
    op.create_table(
        "deals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        # Nullable: the frontend's deals.create contract has no fund field yet.
        sa.Column("fund_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("gp_source", sa.Text(), nullable=True),
        sa.Column("deal_size_min_usd", sa.BigInteger(), nullable=True),
        sa.Column("deal_size_max_usd", sa.BigInteger(), nullable=True),
        sa.Column("sector_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), server_default=DEFAULT_DEAL_STATUS, nullable=False),
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
        sa.ForeignKeyConstraint(["fund_id"], ["funds.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_deals_org_id"), "deals", ["org_id"], unique=False)
    op.create_index(op.f("ix_deals_fund_id"), "deals", ["fund_id"], unique=False)

    # RLS enabled + policy created in the same migration that creates the
    # table, same idiom as funds (aace95a1c412) and claims (60a151dd80b0).
    # dd_app's SELECT/INSERT/UPDATE/DELETE on this table already comes from
    # the bootstrap migration's ALTER DEFAULT PRIVILEGES — no explicit GRANT
    # needed here, mirroring how funds/claims don't grant either.
    op.execute("ALTER TABLE deals ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON deals
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON deals")
    op.drop_index(op.f("ix_deals_fund_id"), table_name="deals")
    op.drop_index(op.f("ix_deals_org_id"), table_name="deals")
    op.drop_table("deals")
