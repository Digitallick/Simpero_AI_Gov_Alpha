"""data source fks

Follow-up to d6d2fe8f27ae (data_source): now that data_source exists, backfill
the FKs its own creation unblocked on two other tables' forward-referencing
columns (see docs/plans/document-upload-presigned-urls.md's "Forward-reference
claims" section for why each was left a bare UUID originally):

- chunks.document_id -> data_source.id. Table has no populated rows yet (per
  its own model docstring), so no backfill/orphan check was needed.
- claims.data_source_id -> data_source.id. Column name is unchanged --
  contracts/claims.schema.json names this field data_source_id and is a
  cross-repo contract (also CI-validated by Simpero_Gov_AI_Services); a
  DB-level FK doesn't touch that contract's name or shape at all.
- claims.deal_id -> deals.id. Gated on a one-time orphaned-row check run
  against the real database by Vansh directly (this implementation
  environment could not reach the DO cluster to run it itself):
  `SELECT count(*) FROM claims WHERE deal_id IS NOT NULL AND NOT EXISTS
  (SELECT 1 FROM deals WHERE deals.id = claims.deal_id)` -- confirmed zero.
  No backfill needed; added as a plain FK like the other two.

Revision ID: 77be2ddc60a0
Revises: d6d2fe8f27ae
Create Date: 2026-07-31 16:03:05.711982

"""

from collections.abc import Sequence

from alembic import op

revision: str = "77be2ddc60a0"
down_revision: str | Sequence[str] | None = "d6d2fe8f27ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_chunks_document_id_data_source",
        "chunks",
        "data_source",
        ["document_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_claims_data_source_id_data_source",
        "claims",
        "data_source",
        ["data_source_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_claims_deal_id_deals",
        "claims",
        "deals",
        ["deal_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_claims_deal_id_deals", "claims", type_="foreignkey")
    op.drop_constraint("fk_claims_data_source_id_data_source", "claims", type_="foreignkey")
    op.drop_constraint("fk_chunks_document_id_data_source", "chunks", type_="foreignkey")
