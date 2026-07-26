"""chunks table

Retrieval's search target (Epic 8): every piece of text/table cut out of an
uploaded document, so search can find it later. Dense (embedding, HNSW) and
sparse (content_tsv, GIN) indexes both live on this one table — a chunk is
retrievable by either path.

content_tsv is a generated column (STORED), kept in sync with `content` by
Postgres itself rather than application code, so it can never drift out of
sync with the text it indexes. Named content_tsv (not sparse_search, its
name in the first draft of this migration) to match the column name SIM-240's
hybrid_search query already reads — see app/services/retrieval.py's module
docstring for the full chunks-table contract that module depends on.

element_type classifies what kind of source content a chunk holds (e.g.
"text", "table", "chart") — added because SIM-240 reads it to build
ChunkHit.element_type. Nullable and unconstrained here: this migration only
owns the schema, not populating it (that's AE-A-RETR-1/2's job).

No FK on document_id: the documents/data_sources table doesn't exist yet,
same forward-reference situation claims.data_source_id/chunk_id are already
in. embedding is nullable — a chunk can be written before it's embedded,
extraction and embedding being separate steps.

Depends on the pgvector extension (9e796d5efdb7, already in the chain) and
the RLS/org_isolation pattern established by claims (60a151dd80b0) and
human_audit_log (7175bc85ffb0).

Revision ID: 6c8bc5907f94
Revises: 920070316626
Create Date: 2026-07-24 09:52:32.578848

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

from alembic import op

revision: str = "6c8bc5907f94"
down_revision: str | None = "920070316626"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_version", sa.Text(), nullable=True),
        sa.Column(
            "content_tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=True,
        ),
        sa.Column("element_type", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chunks_org_id"), "chunks", ["org_id"], unique=False)
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False)

    # Dense search: HNSW over cosine distance — the standard pairing for
    # normalized text embeddings (voyage-4-large).
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # Sparse search: GIN over the generated tsvector column.
    op.create_index(
        "ix_chunks_content_tsv_gin",
        "chunks",
        ["content_tsv"],
        unique=False,
        postgresql_using="gin",
    )

    # Chunks are tenant data, so RLS is enabled HERE — in the same migration
    # that creates the table. Same reasoning as claims (60a151dd80b0): a
    # window with the table unprotected would let any org read another org's
    # chunks, and ENABLE ROW LEVEL SECURITY with no policy yet is
    # default-deny, so a delayed policy would silently return zero rows
    # rather than error.
    op.execute("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON chunks
            FOR ALL TO dd_app
            USING (org_id IN (
                SELECT id FROM organisation
                WHERE clerk_org_id = current_setting('app.org_id', true)
            ))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON chunks")
    op.drop_index("ix_chunks_content_tsv_gin", table_name="chunks")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_org_id"), table_name="chunks")
    op.drop_table("chunks")
