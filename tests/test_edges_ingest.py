"""SIM-366: edges ingest. The parser's same_fact/contradicts edges land in the
`edges` table, resolved claim_ref -> claim id; a missing endpoint is skipped (not
fatal), and edges are org-isolated by RLS."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models import Claim, Edge
from scripts.ingest_claims import _run

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]

ORG = "sim366-edges-org"
OTHER = "sim366-edges-other"


def _owner_dsn() -> str:
    return (
        os.environ.get("ALEMBIC_DATABASE_URL", "").replace("+psycopg2", "").replace("+asyncpg", "")
    )


def _db_available() -> bool:
    if psycopg2 is None or not _owner_dsn():
        return False
    try:
        conn = psycopg2.connect(_owner_dsn())
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.edges')")
        row = cur.fetchone()
        conn.close()
        return row is not None and row[0] is not None
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="no pgvector Postgres with the edges table (run ./sandbox/up.sh)"
)


def _claim(ref: str, char_start: int, char_end: int, normalized: int) -> dict:
    return {
        "claim_ref": ref,
        "claim_type": "numerical",
        "entity": "TestCo",
        "attribute": "revenue",
        "value": {
            "raw": f"${normalized}",
            "normalized": normalized,
            "unit": "USD",
            "scale_multiplier": 1,
            "scale_source": "explicit_in_value",
            "value_type": "currency",
        },
        "location": {
            "kind": "pdf",
            "file": "cim.pdf",
            "page": 1,
            "char_start": char_start,
            "char_end": char_end,
        },
        "status": "proposed",
        "verification_method": None,
        "flags": [],
    }


def _delete_org(org_key: str) -> None:
    assert psycopg2 is not None
    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    cur = conn.cursor()
    for table in ("edges", "claims"):
        cur.execute(
            f"DELETE FROM {table} WHERE org_id IN "
            "(SELECT id FROM organisation WHERE clerk_org_id = %s)",
            (org_key,),
        )
    cur.execute("DELETE FROM organisation WHERE clerk_org_id = %s", (org_key,))
    conn.close()


@requires_db
async def test_edges_land_resolved_a_missing_endpoint_skips_and_rls_isolates() -> None:
    payload = {
        "claims": [_claim("1:10-17[#0]", 10, 17, 100), _claim("1:400-410[#0]", 400, 410, 120)],
        "edges": [
            # resolvable: both endpoints are ingested claims
            {
                "type": "contradicts",
                "from": "1:10-17[#0]",
                "to": "1:400-410[#0]",
                "basis": "disagree",
            },
            # missing endpoint: `to` was never ingested -> skipped, not fatal
            {
                "type": "same_fact",
                "from": "1:10-17[#0]",
                "to": "9:999-999[#0]",
                "basis": "dangling",
            },
        ],
    }
    for org in (ORG, OTHER):
        _delete_org(org)
    try:
        await _run(payload, ORG, commit=True, session_id=uuid.uuid4())

        async with AsyncSessionLocal() as session, session.begin():
            await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": ORG})
            claims = (await session.execute(select(Claim))).scalars().all()
            claim_by_ref = {c.claim_ref: c.id for c in claims}
            edges = (await session.execute(select(Edge))).scalars().all()

        # only the resolvable edge landed; the dangling one was skipped, not fatal
        assert len(edges) == 1
        edge = edges[0]
        assert edge.type == "contradicts"
        assert edge.basis == "disagree"
        # and it resolved claim_ref -> the right claim ids
        assert edge.from_claim_id == claim_by_ref["1:10-17[#0]"]
        assert edge.to_claim_id == claim_by_ref["1:400-410[#0]"]

        # RLS: a different tenant sees none of them
        async with AsyncSessionLocal() as session, session.begin():
            await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": OTHER})
            assert (await session.execute(select(Edge))).scalars().all() == []
    finally:
        for org in (ORG, OTHER):
            _delete_org(org)
