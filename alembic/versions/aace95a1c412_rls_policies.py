"""rls_policies

Revision ID: aace95a1c412
Revises: 30277d26774a
Create Date: 2026-06-30 16:16:27.978527

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'aace95a1c412'
down_revision: Union[str, None] = '30277d26774a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE organisation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE funds        ENABLE ROW LEVEL SECURITY")

    # dd_app can only see the organisation whose clerk_org_id matches the current request's org.
    op.execute("""
        CREATE POLICY org_isolation ON organisation
            FOR ALL TO dd_app
            USING (clerk_org_id = current_setting('app.org_id', true))
    """)

    # funds are isolated by joining to the organisation that owns them.
    op.execute("""
        CREATE POLICY org_isolation ON funds
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON funds")
    op.execute("DROP POLICY IF EXISTS org_isolation ON organisation")
    op.execute("ALTER TABLE funds        DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organisation DISABLE ROW LEVEL SECURITY")
