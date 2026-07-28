"""merge chunks table and corroboration events heads

Revision ID: 222c301f378f
Revises: 6c8bc5907f94, e960a3366cf7
Create Date: 2026-07-28 12:53:44.601161

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "222c301f378f"
down_revision: str | Sequence[str] | None = ("6c8bc5907f94", "e960a3366cf7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
