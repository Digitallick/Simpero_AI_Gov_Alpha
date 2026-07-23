import uuid
from collections.abc import Iterator

import pytest

from app.repo.DealRepo import DealRepo
from app.repo.SessionRepo import SessionRepo


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
def org_b_session_id(owner_conn) -> Iterator[str]:
    """A session belonging to a *different* org (with its own deal), seeded
    via the doadmin connection (bypasses RLS) — a dd_app session scoped to
    org A's app.org_id could never create these rows itself.
    """
    org_b_clerk_id = f"test-tenant-b-{uuid.uuid4().hex[:8]}"
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organisation (clerk_org_id, name, created_at) VALUES (%s, %s, now()) "
            "RETURNING id",
            (org_b_clerk_id, "Org B"),
        )
        org_b_pk = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO deals (org_id, name) VALUES (%s, %s) RETURNING id",
            (org_b_pk, "Org B's deal"),
        )
        deal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sessions (org_id, deal_id, file_name) VALUES (%s, %s, %s) RETURNING id",
            (org_b_pk, deal_id, "org-b-deck.pdf"),
        )
        session_id = cur.fetchone()[0]

    yield str(session_id)

    with owner_conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
        cur.execute("DELETE FROM deals WHERE id = %s", (deal_id,))
        cur.execute("DELETE FROM organisation WHERE id = %s", (org_b_pk,))


async def test_org_isolation_hides_other_org_session(db_session, org_a_id, org_b_session_id):
    repo = SessionRepo(db_session)

    # A session scoped to org A cannot fetch org B's session by id — RLS
    # makes it look like the row doesn't exist, not a permission error.
    assert await repo.get_by_id(org_b_session_id) is None

    # ... nor does it show up in an unscoped list query. No `WHERE org_id =`
    # in the repo — RLS alone must do the filtering.
    all_sessions = await repo.list_for_org()
    assert all(str(s.id) != org_b_session_id for s in all_sessions)


async def test_org_isolation_still_shows_own_org_session(db_session, org_a_id, org_b_session_id):
    deal_repo = DealRepo(db_session)
    session_repo = SessionRepo(db_session)

    own_deal = await deal_repo.create({"org_id": org_a_id, "name": "Org A's deal"})
    await db_session.flush()
    own_session = await session_repo.create(
        {"org_id": org_a_id, "deal_id": own_deal.id, "file_name": "org-a-deck.pdf"}
    )
    await db_session.flush()

    fetched = await session_repo.get_by_id(own_session.id)
    assert fetched is not None
    assert fetched.file_name == "org-a-deck.pdf"

    latest = await session_repo.latest_for_deal(own_deal.id)
    assert latest is not None
    assert latest.id == own_session.id

    all_sessions = await session_repo.list_for_org()
    assert any(s.id == own_session.id for s in all_sessions)
    assert all(str(s.id) != org_b_session_id for s in all_sessions)
