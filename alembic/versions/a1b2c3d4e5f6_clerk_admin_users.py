"""clerk admin users

Admin portal (app/api/admin) identity + authorization source of truth.
Mutable tenant projection, same org_isolation RLS shape as users/organisation
(no FORCE, no REVOKE — that combo is specific to human_audit_log's
immutability guarantee, not this table).

Revision ID: a1b2c3d4e5f6
Revises: 7b4b05b6d9c8
Create Date: 2026-07-23 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "7b4b05b6d9c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clerk_admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=64), nullable=False),
        sa.Column("clerk_org_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=100), nullable=True),
        sa.Column("admin_type", sa.Enum("platform", "client", name="admintype"), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_clerk_admin_users_clerk_user_id"),
        "clerk_admin_users",
        ["clerk_user_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_clerk_admin_users_clerk_org_id"),
        "clerk_admin_users",
        ["clerk_org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_clerk_admin_users_org_id"), "clerk_admin_users", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_clerk_admin_users_email"), "clerk_admin_users", ["email"], unique=False
    )

    # RLS enabled in the SAME migration that creates the table — same idiom
    # as users/organisation/funds/etc. No FORCE, no GRANT, no REVOKE: this is
    # a mutable tenant projection, not the immutable audit trail. The
    # bootstrap migration's ALTER DEFAULT PRIVILEGES FOR ROLE doadmin already
    # grants dd_app full DML on any table doadmin creates.
    op.execute("ALTER TABLE clerk_admin_users ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON clerk_admin_users
            FOR ALL TO dd_app
            USING (clerk_org_id = current_setting('app.org_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON clerk_admin_users")
    op.execute("ALTER TABLE clerk_admin_users DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_clerk_admin_users_email"), table_name="clerk_admin_users")
    op.drop_index(op.f("ix_clerk_admin_users_org_id"), table_name="clerk_admin_users")
    op.drop_index(op.f("ix_clerk_admin_users_clerk_org_id"), table_name="clerk_admin_users")
    op.drop_index(op.f("ix_clerk_admin_users_clerk_user_id"), table_name="clerk_admin_users")
    op.drop_table("clerk_admin_users")
    # sa.Enum(...) auto-creates the pg type on upgrade; drop it explicitly on
    # downgrade (op.drop_table alone does not drop the enum type).
    op.execute("DROP TYPE IF EXISTS admintype")
