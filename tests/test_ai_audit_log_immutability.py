import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# No repo yet — table only, no writer until the real-pipeline effort adds the
# LLM base wrapper that inserts rows here. These tests exercise the table's
# DB-level guarantees directly, same as they will once that writer exists.


@pytest.fixture
def org_id(owner_conn, test_org_id) -> int:
    """Ensures an organisation row exists for the shared test tenant so
    ai_audit_log rows (FK org_id -> organisation.id) can be inserted.
    Seeded via the doadmin (table-owner) connection, which bypasses RLS.
    """
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organisation (clerk_org_id, name, created_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (clerk_org_id) DO NOTHING",
            (test_org_id, "Test Org"),
        )
        cur.execute("SELECT id FROM organisation WHERE clerk_org_id = %s", (test_org_id,))
        return cur.fetchone()[0]


async def test_dd_app_can_insert_and_select_ai_audit_log(db_session, org_id):
    await db_session.execute(
        text("INSERT INTO ai_audit_log (org_id, agent_name) VALUES (:org_id, :agent_name)"),
        {"org_id": org_id, "agent_name": "test_agent"},
    )
    result = await db_session.execute(
        text("SELECT agent_name FROM ai_audit_log WHERE org_id = :org_id"),
        {"org_id": org_id},
    )
    assert [row[0] for row in result.fetchall()] == ["test_agent"]


async def test_dd_app_cannot_update_ai_audit_log(db_session, org_id):
    await db_session.execute(
        text("INSERT INTO ai_audit_log (org_id, agent_name) VALUES (:org_id, 'test_agent')"),
        {"org_id": org_id},
    )
    # REVOKE UPDATE ON ai_audit_log FROM dd_app (see the migration that
    # creates the table) — this must fail at the database, not be caught by
    # any application-level guard.
    with pytest.raises(DBAPIError, match="permission denied"):
        await db_session.execute(text("UPDATE ai_audit_log SET agent_name = 'tampered'"))


async def test_dd_app_cannot_delete_ai_audit_log(db_session, org_id):
    await db_session.execute(
        text("INSERT INTO ai_audit_log (org_id, agent_name) VALUES (:org_id, 'test_agent')"),
        {"org_id": org_id},
    )
    # REVOKE DELETE ON ai_audit_log FROM dd_app — same guarantee as above.
    with pytest.raises(DBAPIError, match="permission denied"):
        await db_session.execute(text("DELETE FROM ai_audit_log"))
