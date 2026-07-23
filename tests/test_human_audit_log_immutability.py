import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.repo.HumanAuditRepo import HumanAuditRepo


@pytest.fixture
def org_id(owner_conn, test_org_id) -> int:
    """Ensures an organisation row exists for the shared test tenant so
    human_audit_log rows (FK org_id -> organisation.id) can be inserted.
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


async def test_dd_app_can_insert_and_select_human_audit_log(db_session, org_id):
    repo = HumanAuditRepo(db_session)
    await repo.append({"org_id": org_id, "event_type": "test_event"})
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT event_type FROM human_audit_log WHERE org_id = :org_id"),
        {"org_id": org_id},
    )
    assert [row[0] for row in result.fetchall()] == ["test_event"]


async def test_dd_app_cannot_update_human_audit_log(db_session, org_id):
    repo = HumanAuditRepo(db_session)
    await repo.append({"org_id": org_id, "event_type": "test_event"})
    await db_session.flush()

    # REVOKE UPDATE ON human_audit_log FROM dd_app (see the migration that
    # creates the table) — this must fail at the database, not be caught by
    # any application-level guard.
    with pytest.raises(DBAPIError, match="permission denied"):
        await db_session.execute(text("UPDATE human_audit_log SET event_type = 'tampered'"))


async def test_dd_app_cannot_delete_human_audit_log(db_session, org_id):
    repo = HumanAuditRepo(db_session)
    await repo.append({"org_id": org_id, "event_type": "test_event"})
    await db_session.flush()

    # REVOKE DELETE ON human_audit_log FROM dd_app — same guarantee as above.
    with pytest.raises(DBAPIError, match="permission denied"):
        await db_session.execute(text("DELETE FROM human_audit_log"))
