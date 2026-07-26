"""ingest_chunks: the seam-JSON -> Chunk-row translation (SIM-338).

The pure translation helpers, without a DB or the embedder: scale_context folds
into content, per-block spans collapse to a covering range, the source sha256
maps to a stable UUID document_id, and a dry-run row carries no stale embedding
version. The full ingest (RLS, embed, insert) is a demo script exercised against
a real Postgres, like ingest_claims.
"""

from __future__ import annotations

import uuid

from scripts.ingest_chunks import _document_uuid, _fold_content, _row_from_chunk, _span


def _chunk(**kw) -> dict:
    base: dict = {
        "content": "Revenue grew to $15M",
        "element_type": "prose",
        "page": 3,
        "document_id": "a" * 64,
        "spans": [[10, 30]],
    }
    base.update(kw)
    return base


def test_fold_content_prefixes_scale_context_when_present() -> None:
    folded = _fold_content(_chunk(scale_context="$ in millions"))
    assert folded == "[$ in millions] Revenue grew to $15M"


def test_fold_content_is_untouched_without_scale_context() -> None:
    assert _fold_content(_chunk()) == "Revenue grew to $15M"


def test_span_collapses_multiple_blocks_to_covering_range() -> None:
    assert _span(_chunk(spans=[[10, 30], [40, 55], [5, 12]])) == (5, 55)


def test_span_is_none_for_a_chunk_without_spans() -> None:
    assert _span(_chunk(spans=[])) == (None, None)


def test_document_uuid_is_stable_and_distinct_per_document() -> None:
    a = _document_uuid("a" * 64)
    assert a == _document_uuid("a" * 64)  # stable: chunks of one doc share it
    assert a != _document_uuid("b" * 64)  # distinct documents differ
    assert isinstance(a, uuid.UUID)


def test_row_from_chunk_maps_columns_and_folds_content() -> None:
    row = _row_from_chunk(
        _chunk(scale_context="$ in millions"),
        org_id=7,
        embedding=[0.1] * 4,
        embedding_version="voyage-4-large:1024",
    )
    assert row.org_id == 7
    assert row.document_id == _document_uuid("a" * 64)
    assert row.content == "[$ in millions] Revenue grew to $15M"
    assert row.element_type == "prose"
    assert row.page == 3
    assert row.char_start == 10 and row.char_end == 30
    assert row.embedding == [0.1] * 4
    assert row.embedding_version == "voyage-4-large:1024"


def test_row_from_chunk_drops_version_when_there_is_no_embedding() -> None:
    # A dry-run row has no embedding; it must not carry a stale version either.
    row = _row_from_chunk(
        _chunk(), org_id=1, embedding=None, embedding_version="voyage-4-large:1024"
    )
    assert row.embedding is None
    assert row.embedding_version is None
