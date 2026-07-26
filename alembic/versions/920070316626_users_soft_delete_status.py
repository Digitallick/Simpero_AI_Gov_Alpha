"""users soft delete status

Soft-delete on `users`: member removal (both own-org and cross-org admin
paths) now flips `status` -> 'inactive' and stamps `deactivated_at` instead
of deleting the row, so a re-invited member's history/local id survives and
JIT provisioning on their next login can reactivate the same row. No RLS/
policy change needed — the existing org_isolation policy on `users` already
covers all columns.

Revision ID: 920070316626
Revises: 588db2facf84
Create Date: 2026-07-25 12:16:51.297347

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "920070316626"
down_revision: str | None = "588db2facf84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
    )
    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "deactivated_at")
    op.drop_column("users", "status")
