"""data source

Registry of uploaded documents (SIM-216). Not audit-log-shaped like
human_audit_log/corroboration_events -- data_source is a resource with a
lifecycle (closer to deals), so it does not get a blanket
REVOKE UPDATE/DELETE. Instead:

- Identity/history columns (org_id, deal_id, storage_key, filename,
  declared_sha256, created_at, id) are append-only: REVOKE UPDATE, DELETE
  ... FROM dd_app, same idiom as human_audit_log/corroboration_events.
- The three lifecycle columns (status, fingerprint, status_updated_at) get a
  narrow GRANT UPDATE (...) back -- the one legitimate write the async
  ingest job performs, moving a row from 'pending' to a terminal status.
- A BEFORE UPDATE trigger (data_source_enforce_one_way_status) makes that
  transition truly one-way, even against the table owner (doadmin): triggers
  fire for every role, unlike GRANT/REVOKE, which the owning role bypasses by
  virtue of owning the table. This is the part that makes the "one legitimate
  UPDATE, ever" guarantee airtight -- column-grant alone would still let a
  future doadmin fix-up query re-verify/flip status a second time.

This is a new pattern in this repo (column-level GRANT + enforcement
trigger), not a reflex reuse of human_audit_log's blanket-immutability idiom
-- reviewed and decided deliberately (see docs/plans/document-upload-presigned-urls.md).

Revision ID: d6d2fe8f27ae
Revises: 222c301f378f
Create Date: 2026-07-31 14:52:11.326575

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d6d2fe8f27ae"
down_revision: str | Sequence[str] | None = "222c301f378f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_source",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("declared_sha256", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        # No server_default: stays NULL until the ingest job's one legitimate
        # UPDATE moves status off 'pending' -- see the model/table docstring.
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'quarantined', 'ocr_needed', 'mismatch')",
            name="ck_data_source_status",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_data_source_org_id"), "data_source", ["org_id"], unique=False)
    op.create_index(op.f("ix_data_source_deal_id"), "data_source", ["deal_id"], unique=False)
    op.create_index(
        op.f("ix_data_source_declared_sha256"), "data_source", ["declared_sha256"], unique=False
    )
    op.create_index(op.f("ix_data_source_status"), "data_source", ["status"], unique=False)

    # Tenant data, so RLS is enabled HERE -- in the same migration that
    # creates the table. Same reasoning as claims/chunks/human_audit_log: a
    # window with the table unprotected is a window where any org can read
    # another org's document filenames/storage keys, and ENABLE ROW LEVEL
    # SECURITY with no policy yet is default-deny (silent zero rows, not an
    # error).
    op.execute("ALTER TABLE data_source ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON data_source
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)
    # FORCE, on top of ENABLE: a plain ENABLE exempts the table owner
    # (doadmin) from its own RLS policy. Rationale here differs from
    # human_audit_log's (which is about immutability) -- this is about the
    # row containing sensitive deal-document filenames/storage keys, and the
    # same table-owner-bypass gap matters just as much.
    op.execute("ALTER TABLE data_source FORCE ROW LEVEL SECURITY")

    # The bootstrap migration's ALTER DEFAULT PRIVILEGES already granted
    # dd_app SELECT/INSERT/UPDATE/DELETE on every table doadmin creates.
    # Narrow that back down: everything except the three lifecycle columns is
    # append-only, full stop -- that IS SIM-216's "append-only" guarantee,
    # scoped to the columns that actually describe an immutable historical
    # fact.
    op.execute("REVOKE UPDATE, DELETE ON data_source FROM dd_app")
    # NARROW exception: only the columns the async ingest job legitimately
    # owns. status_updated_at rides along with status/fingerprint because it
    # changes in the same UPDATE, not a separate one.
    op.execute("GRANT UPDATE (status, fingerprint, status_updated_at) ON data_source TO dd_app")

    # The part that makes it airtight: a BEFORE UPDATE trigger enforcing the
    # transition is truly one-way. Triggers fire for EVERY role, including the
    # table owner (doadmin) -- unlike GRANT/REVOKE, which the owner bypasses
    # by virtue of owning the table. This closes the one gap column-grant
    # alone leaves open: nothing (no future migration, no ad-hoc doadmin
    # fix-up query) can ever re-verify, un-verify, or flip status a second
    # time.
    op.execute("""
        CREATE FUNCTION data_source_enforce_one_way_status() RETURNS trigger AS $$
        BEGIN
            IF OLD.status <> 'pending' THEN
                RAISE EXCEPTION 'data_source % status is final once left pending (was %)',
                    OLD.id, OLD.status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_data_source_one_way_status
            BEFORE UPDATE ON data_source
            FOR EACH ROW EXECUTE FUNCTION data_source_enforce_one_way_status()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_data_source_one_way_status ON data_source")
    op.execute("DROP FUNCTION IF EXISTS data_source_enforce_one_way_status()")
    op.execute("REVOKE UPDATE (status, fingerprint, status_updated_at) ON data_source FROM dd_app")
    op.execute("GRANT UPDATE, DELETE ON data_source TO dd_app")
    op.execute("ALTER TABLE data_source NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON data_source")
    op.drop_index(op.f("ix_data_source_status"), table_name="data_source")
    op.drop_index(op.f("ix_data_source_declared_sha256"), table_name="data_source")
    op.drop_index(op.f("ix_data_source_deal_id"), table_name="data_source")
    op.drop_index(op.f("ix_data_source_org_id"), table_name="data_source")
    op.drop_table("data_source")
