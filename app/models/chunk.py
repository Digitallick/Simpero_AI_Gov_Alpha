import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base
from app.models.organisation import Organisation

# Embedding dimension is 1024, voyage-4-large's default Matryoshka size (it also
# supports 256/512/2048). 1024 is the largest that fits pgvector's 2000-dim HNSW
# limit on the `vector` type; 2048 would require `halfvec`. Changing it later
# means a new migration + full re-embed, not a column-type tweak.
EMBEDDING_DIM = 1024


class Chunk(Base):
    """One retrievable slice of text or table cut out of an uploaded document
    (Epic 8 — Retrieval). Search target for both dense (embedding) and sparse
    (content_tsv) lookups; not itself a claim — claims.chunk_id points back
    here once claim emission is wired to it (out of scope for this table).

    No FK on document_id: the documents/data_sources table doesn't exist yet,
    same forward-reference situation as claims.data_source_id/chunk_id.
    """

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )

    # Tenant. Integer FK because organisation.id is a serial Integer — RLS
    # joins through to organisation.clerk_org_id, same idiom as claims/deals.
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Nullable: a chunk can be written before it's embedded (extraction and
    # embedding are separate steps). embedding_version tracks which model
    # produced it, for re-embedding when the model changes.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Generated column (see migration): kept in sync with `content` by
    # Postgres itself, not application code, so it can never drift. Named
    # content_tsv to match the column name SIM-240's hybrid_search reads.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True), nullable=True
    )

    # What kind of source content this chunk holds (e.g. "text", "table",
    # "chart"). Populated by AE-A-RETR-1/2, not this table's own concern —
    # nullable and unconstrained here.
    element_type: Mapped[str | None] = mapped_column(Text, nullable=True)

    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
