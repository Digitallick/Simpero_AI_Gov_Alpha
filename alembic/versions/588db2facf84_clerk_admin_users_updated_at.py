"""clerk admin users updated_at

Nullable `updated_at` on `clerk_admin_users`, `onupdate`-maintained at the
column level (see the model) — lets the D3 downgrade sync distinguish a row
this app just touched (promote/reactivate) from a stale/out-of-band one, to
avoid clobbering a promotion still in its JWT-refresh grace window. Nullable,
no backfill: every pre-existing row reads as NULL -> no grace given -> D3
behaves exactly as it did before this migration until a row is first touched
by the new reactivate path.

Revision ID: 588db2facf84
Revises: a1b2c3d4e5f6
Create Date: 2026-07-24 13:43:57.094084

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "588db2facf84"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clerk_admin_users", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("clerk_admin_users", "updated_at")
