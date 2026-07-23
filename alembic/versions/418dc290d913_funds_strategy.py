"""funds strategy

Additive column only — no PK/type migration of funds (locked decision).

Revision ID: 418dc290d913
Revises: 2f9ca0724bb9
Create Date: 2026-07-18 00:10:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "418dc290d913"
down_revision: str | None = "2f9ca0724bb9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("funds", sa.Column("strategy", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("funds", "strategy")
