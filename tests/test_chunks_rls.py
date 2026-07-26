import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


@pytest.fixture
def org_a_id(owner_conn, test_org_id) -> int:
    """The organisation backing the test session's own app.org_id."""
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organisation (clerk_org_id, name, created_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (clerk_org_id) DO NOTHING",
            (test_org_id, "Org A"),
        )
        cur.execute("SELECT id FROM organisation WHERE clerk_org_id = %s", (test_org_id,))
        return cur.fetchone()[0]


@pytest.fixture
def org_b_id(owner_conn) -> Iterator[int]:
    """A second, real organisation — seeded via the doadmin connection
    (bypasses RLS) so it exists as a valid FK target distinct from org A."""
    org_b_clerk_id = f"test-tenant-b-{uuid.uuid4().hex[:8]}"
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organisation (clerk_org_id, name, created_at) VALUES (%s, %s, now()) "
            "RETURNING id",
            (org_b_clerk_id, "Org B"),
        )
        org_b_pk = cur.fetchone()[0]

    yield org_b_pk

    with owner_conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE org_id = %s", (org_b_pk,))
        cur.execute("DELETE FROM organisation WHERE id = %s", (org_b_pk,))


@pytest.fixture
def org_b_chunk_id(owner_conn, org_b_id) -> str:
    """A chunk belonging to org B, seeded via the doadmin connection
    (bypasses RLS) — a dd_app session scoped to org A's app.org_id could
    never create this row itself.
    """
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunks (org_id, content) VALUES (%s, %s) RETURNING id",
            (org_b_id, "Org B's chunk text"),
        )
        return str(cur.fetchone()[0])


async def test_org_isolation_hides_other_org_chunk(db_session, org_a_id, org_b_chunk_id):
    result = await db_session.execute(
        text("SELECT id FROM chunks WHERE id = :id"), {"id": org_b_chunk_id}
    )
    assert result.first() is None

    all_rows = await db_session.execute(text("SELECT id FROM chunks"))
    assert all(str(row[0]) != org_b_chunk_id for row in all_rows.fetchall())


async def test_org_isolation_still_shows_own_org_chunk(db_session, org_a_id, org_b_chunk_id):
    insert_result = await db_session.execute(
        text("INSERT INTO chunks (org_id, content) VALUES (:org_id, :content) RETURNING id"),
        {"org_id": org_a_id, "content": "Org A's chunk text"},
    )
    own_chunk_id = insert_result.scalar()
    await db_session.flush()

    fetched = await db_session.execute(
        text("SELECT content FROM chunks WHERE id = :id"), {"id": own_chunk_id}
    )
    row = fetched.first()
    assert row is not None
    assert row[0] == "Org A's chunk text"

    all_rows = await db_session.execute(text("SELECT id FROM chunks"))
    ids = [str(r[0]) for r in all_rows.fetchall()]
    assert str(own_chunk_id) in ids
    assert org_b_chunk_id not in ids


async def test_content_tsv_generated_from_content(db_session, org_a_id):
    """content_tsv is a Postgres-generated column — this asserts Postgres
    actually populates it from `content`, not that application code does."""
    result = await db_session.execute(
        text(
            "INSERT INTO chunks (org_id, content) VALUES (:org_id, :content) "
            "RETURNING content_tsv::text"
        ),
        {"org_id": org_a_id, "content": "revenue grew significantly"},
    )
    content_tsv = result.scalar()
    assert content_tsv is not None
    assert "revenu" in content_tsv  # tsvector stems to the lexeme


async def test_org_isolation_rejects_cross_org_insert(db_session, org_a_id, org_b_id):
    """Read-side isolation (above) isn't the whole guarantee — a dd_app
    session scoped to org A must also be unable to *write* a chunk claiming
    org B's (real, existing) org_id. The `FOR ALL ... USING (...)` policy has
    no explicit WITH CHECK, so Postgres reuses USING for INSERT/UPDATE too;
    this proves that actually holds rather than assuming it from the policy
    shape. org_b_id must be a real row, not an arbitrary integer — otherwise
    the FK constraint would reject the insert before RLS ever gets a say.
    """
    with pytest.raises(DBAPIError, match="row-level security"):
        await db_session.execute(
            text("INSERT INTO chunks (org_id, content) VALUES (:org_id, :content)"),
            {"org_id": org_b_id, "content": "spoofed org_id"},
        )


async def test_embedding_rejects_wrong_dimension(db_session, org_a_id):
    """The column is fixed at 1024 dims — pgvector must reject a mismatched
    vector at insert time, not silently truncate/pad it."""
    wrong_dim_literal = "[" + ",".join(["0.1"] * 10) + "]"
    with pytest.raises(DBAPIError):
        await db_session.execute(
            text(
                "INSERT INTO chunks (org_id, content, embedding) "
                "VALUES (:org_id, :content, CAST(:embedding AS vector))"
            ),
            {"org_id": org_a_id, "content": "x", "embedding": wrong_dim_literal},
        )
