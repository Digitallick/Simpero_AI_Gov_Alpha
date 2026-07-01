from alembic import op

revision = "xxxx"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Schema access
    op.execute("GRANT USAGE ON SCHEMA public TO dd_app")

    # Default privileges — covers tables created by doadmin (current migration role)
    # When you switch ALEMBIC_DATABASE_URL to dd_owner, add a matching block for dd_owner.
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE doadmin IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dd_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE doadmin IN SCHEMA public
            GRANT USAGE ON SEQUENCES TO dd_app
    """)



def downgrade() -> None:
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE doadmin IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM dd_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE doadmin IN SCHEMA public
            REVOKE USAGE ON SEQUENCES FROM dd_app
    """)
    op.execute("REVOKE USAGE ON SCHEMA public FROM dd_app")
