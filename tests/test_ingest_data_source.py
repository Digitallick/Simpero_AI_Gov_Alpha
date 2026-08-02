"""app/jobs/tasks/ingest_data_source.py -- the async SAQ ingest job (SIM-216/
218 Phase 5).

Runs the job function directly (it takes no FastAPI dependencies -- it opens
its own AsyncSessionLocal/transaction and issues SET LOCAL app.org_id by
hand, see the module's own docstring), verifying outcomes against real
Postgres via owner_conn (bypasses RLS, same idiom as test_data_source_rls.py
and test_uploads_api.py). stream_and_hash is monkeypatched at its call site
in app.jobs.tasks.ingest_data_source -- no real Spaces/network call.
"""

import importlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.services.uploads.spaces import ObjectTooLargeError

# `app.jobs.tasks.__init__` does `from app.jobs.tasks.ingest_data_source import
# ingest_data_source`, which rebinds the package attribute `tasks.ingest_data_source`
# to the *function*, shadowing the submodule of the same name -- so
# `import app.jobs.tasks.ingest_data_source as job_module` would resolve to the
# function, not the module, and monkeypatching `job_module.stream_and_hash`
# would silently patch the wrong (function) object. importlib.import_module
# reads sys.modules directly, bypassing that package-attribute shadowing.
job_module = importlib.import_module("app.jobs.tasks.ingest_data_source")

_DECLARED_HASH = "a" * 64
_MISMATCH_HASH = "b" * 64


@pytest.fixture
def seeded_org(owner_conn) -> Iterator[dict[str, Any]]:
    clerk_org_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organisation (clerk_org_id, name, created_at) VALUES (%s, %s, now()) "
            "RETURNING id",
            (clerk_org_id, "Ingest Test Org"),
        )
        org_pk = cur.fetchone()[0]

    yield {"clerk_org_id": clerk_org_id, "org_pk": org_pk}

    with owner_conn.cursor() as cur:
        # human_audit_log first -- its org_id FK blocks the organisation
        # delete otherwise (same ordering as test_uploads_api.py's seeded_org).
        for table in ("human_audit_log", "data_source", "deals"):
            cur.execute(f"DELETE FROM {table} WHERE org_id = %s", (org_pk,))
        cur.execute("DELETE FROM organisation WHERE id = %s", (org_pk,))


@pytest.fixture
def seeded_deal(owner_conn, seeded_org) -> str:
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO deals (org_id, name) VALUES (%s, %s) RETURNING id",
            (seeded_org["org_pk"], "Ingest Test Deal"),
        )
        return str(cur.fetchone()[0])


def _seed_pending_data_source(
    owner_conn, org_pk: int, deal_id: str, declared_sha256: str = _DECLARED_HASH
) -> str:
    with owner_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO data_source (org_id, deal_id, storage_key, filename, declared_sha256) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (org_pk, deal_id, "org/key.pdf", "file.pdf", declared_sha256),
        )
        return str(cur.fetchone()[0])


def _fetch_data_source(owner_conn, data_source_id: str) -> dict[str, Any]:
    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT status, fingerprint, status_updated_at FROM data_source WHERE id = %s",
            (data_source_id,),
        )
        status, fingerprint, status_updated_at = cur.fetchone()
        return {
            "status": status,
            "fingerprint": fingerprint,
            "status_updated_at": status_updated_at,
        }


def _count_audit_rows(owner_conn, org_pk: int, data_source_id: str) -> int:
    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM human_audit_log WHERE org_id = %s "
            "AND event_type = 'document_upload_ingest_completed' "
            "AND payload ->> 'data_source_id' = %s",
            (org_pk, data_source_id),
        )
        return cur.fetchone()[0]


def _audit_row(owner_conn, org_pk: int, data_source_id: str) -> tuple[str | None, str | None]:
    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_email FROM human_audit_log WHERE org_id = %s "
            "AND event_type = 'document_upload_ingest_completed' "
            "AND payload ->> 'data_source_id' = %s",
            (org_pk, data_source_id),
        )
        return cur.fetchone()


async def _run_job(
    monkeypatch: pytest.MonkeyPatch,
    data_source_id: str,
    clerk_org_id: str,
    *,
    hash_result: str | None = None,
    hash_error: Exception | None = None,
) -> list[str]:
    calls: list[str] = []

    def fake_stream_and_hash(key: str, max_bytes: int | None = None) -> str:
        calls.append(key)
        if hash_error is not None:
            raise hash_error
        assert hash_result is not None
        return hash_result

    monkeypatch.setattr(job_module, "stream_and_hash", fake_stream_and_hash)

    await job_module.ingest_data_source(
        {},
        data_source_id=data_source_id,
        clerk_org_id=clerk_org_id,
        storage_key="org/key.pdf",
        declared_sha256=_DECLARED_HASH,
    )
    return calls


async def test_matching_hash_marks_verified_and_writes_one_audit_row(
    owner_conn, seeded_org, seeded_deal, monkeypatch
):
    data_source_id = _seed_pending_data_source(owner_conn, seeded_org["org_pk"], seeded_deal)

    await _run_job(
        monkeypatch, data_source_id, seeded_org["clerk_org_id"], hash_result=_DECLARED_HASH
    )

    row = _fetch_data_source(owner_conn, data_source_id)
    assert row["status"] == "verified"
    assert row["fingerprint"] == _DECLARED_HASH
    assert row["status_updated_at"] is not None

    assert _count_audit_rows(owner_conn, seeded_org["org_pk"], data_source_id) == 1
    actor_id, actor_email = _audit_row(owner_conn, seeded_org["org_pk"], data_source_id)
    assert actor_id == "Internal System"
    assert actor_email == "Internal System"


async def test_mismatched_hash_marks_mismatch_fingerprint_still_set(
    owner_conn, seeded_org, seeded_deal, monkeypatch
):
    data_source_id = _seed_pending_data_source(owner_conn, seeded_org["org_pk"], seeded_deal)

    await _run_job(
        monkeypatch, data_source_id, seeded_org["clerk_org_id"], hash_result=_MISMATCH_HASH
    )

    row = _fetch_data_source(owner_conn, data_source_id)
    assert row["status"] == "mismatch"
    # Fingerprint is always the actual computed value, even on a mismatch --
    # never left null.
    assert row["fingerprint"] == _MISMATCH_HASH
    assert row["status_updated_at"] is not None
    assert _count_audit_rows(owner_conn, seeded_org["org_pk"], data_source_id) == 1


async def test_oversized_object_marks_quarantined_fingerprint_stays_null(
    owner_conn, seeded_org, seeded_deal, monkeypatch
):
    data_source_id = _seed_pending_data_source(owner_conn, seeded_org["org_pk"], seeded_deal)

    await _run_job(
        monkeypatch,
        data_source_id,
        seeded_org["clerk_org_id"],
        hash_error=ObjectTooLargeError("too big"),
    )

    row = _fetch_data_source(owner_conn, data_source_id)
    assert row["status"] == "quarantined"
    assert row["fingerprint"] is None
    assert row["status_updated_at"] is not None
    assert _count_audit_rows(owner_conn, seeded_org["org_pk"], data_source_id) == 1


async def test_second_run_against_terminal_row_is_a_no_op(
    owner_conn, seeded_org, seeded_deal, monkeypatch
):
    data_source_id = _seed_pending_data_source(owner_conn, seeded_org["org_pk"], seeded_deal)

    await _run_job(
        monkeypatch, data_source_id, seeded_org["clerk_org_id"], hash_result=_DECLARED_HASH
    )
    first_run = _fetch_data_source(owner_conn, data_source_id)
    assert first_run["status"] == "verified"

    # Second invocation against the now-terminal row: the idempotency guard
    # must skip the UPDATE (and stream_and_hash) entirely -- not rely on the
    # trigger raising as the only guard.
    calls = await _run_job(
        monkeypatch, data_source_id, seeded_org["clerk_org_id"], hash_result=_MISMATCH_HASH
    )
    assert calls == []  # stream_and_hash never called on the idempotency-skip path

    second_run = _fetch_data_source(owner_conn, data_source_id)
    assert second_run["status"] == "verified"
    assert second_run["fingerprint"] == _DECLARED_HASH
    assert second_run["status_updated_at"] == first_run["status_updated_at"]

    # No second audit row written on the skipped run.
    assert _count_audit_rows(owner_conn, seeded_org["org_pk"], data_source_id) == 1


async def test_missing_row_raises_instead_of_silently_no_oping(
    owner_conn, seeded_org, seeded_deal, monkeypatch
):
    missing_id = str(uuid.uuid4())

    with pytest.raises(ValueError, match="not found"):
        await _run_job(
            monkeypatch, missing_id, seeded_org["clerk_org_id"], hash_result=_DECLARED_HASH
        )
