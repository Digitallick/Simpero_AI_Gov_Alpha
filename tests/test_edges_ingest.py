"""SIM-366: edges ingest. The parser's same_fact/contradicts edges land in the
`edges` table, resolved claim_ref -> claim id. A skipped edge (a missing endpoint
or a contract-invalid shape) is non-fatal -- the document's claims still land --
and edges are org-isolated by RLS."""

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

A = "1:10-17[#0]"
B = "1:400-410[#0]"
C = "1:800-810[#0]"
DANGLING = "9:999-999[#0]"  # never ingested -> a missing endpoint


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


def _payload() -> dict:
    return {
        "claims": [_claim(A, 10, 17, 100), _claim(B, 400, 410, 120), _claim(C, 800, 810, 140)],
        "edges": [
            # resolvable same_fact -- the happy path for a type alpha ingest writes.
            {"type": "same_fact", "from": A, "to": B, "basis": "corroborates"},
            # resolvable contradicts -- same resolvability as the skip below, so the
            # only thing that differs from it is the endpoint, not the edge type.
            {"type": "contradicts", "from": B, "to": C, "basis": "disagree"},
            # missing endpoint: `to` was never ingested -> skipped, not fatal. Same
            # type as a landing edge, isolating resolvability as the one variable.
            {"type": "contradicts", "from": A, "to": DANGLING, "basis": "dangling"},
            # contract-invalid: no `to` key. A hard e["to"] read would raise and roll
            # back every claim; it must instead be skipped, non-fatally.
            {"type": "same_fact", "from": A, "basis": "malformed"},
            # contract-invalid: a `type` the CHECK would also reject. Caught at the
            # seam, before it can fail the insert.
            {"type": "not_a_real_type", "from": A, "to": B, "basis": "bad type"},
        ],
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


async def _edges_and_claim_refs(org_key: str) -> tuple[list[Edge], dict[str, uuid.UUID]]:
    """(edges, claim_ref -> claim id) as this tenant sees them, under RLS."""
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": org_key})
        claims = (await session.execute(select(Claim))).scalars().all()
        edges = (await session.execute(select(Edge))).scalars().all()
    return list(edges), {c.claim_ref: c.id for c in claims if c.claim_ref is not None}


@requires_db
async def test_resolvable_edges_land_bad_ones_skip_and_rls_isolates_two_orgs() -> None:
    for org in (ORG, OTHER):
        _delete_org(org)
    try:
        await _run(_payload(), ORG, commit=True, session_id=uuid.uuid4())
        await _run(_payload(), OTHER, commit=True, session_id=uuid.uuid4())

        edges, ref_id = await _edges_and_claim_refs(ORG)

        # All three claims landed: the two skipped edges did not roll the doc back.
        assert set(ref_id) == {A, B, C}

        # Exactly the two RESOLVABLE, contract-valid edges landed -- both types,
        # each resolved claim_ref -> the right claim ids.
        assert len(edges) == 2
        landed = {(e.type, e.from_claim_id, e.to_claim_id, e.basis) for e in edges}
        assert landed == {
            ("same_fact", ref_id[A], ref_id[B], "corroborates"),
            ("contradicts", ref_id[B], ref_id[C], "disagree"),
        }

        # RLS: the other tenant has its OWN two edges, over its OWN claims, and the
        # id sets are disjoint -- a real second tenant, not an empty subquery.
        other_edges, other_ref_id = await _edges_and_claim_refs(OTHER)
        assert len(other_edges) == 2
        assert {e.id for e in edges}.isdisjoint({e.id for e in other_edges})
        assert {ref_id[A], ref_id[B], ref_id[C]}.isdisjoint(
            {other_ref_id[A], other_ref_id[B], other_ref_id[C]}
        )
        assert all(e.to_claim_id in set(other_ref_id.values()) for e in other_edges)
    finally:
        for org in (ORG, OTHER):
            _delete_org(org)


@requires_db
async def test_a_claim_with_a_live_edge_cannot_be_deleted_until_the_edge_is_gone() -> None:
    """The locked ON DELETE RESTRICT (deliberately not cascade): a claim an edge
    points at cannot be deleted while the edge stands. This is the constraint that
    forces re-ingest's ordered teardown -- drop edges, THEN claims (SIM-367)."""
    _delete_org(ORG)
    try:
        payload = {
            "claims": [_claim(A, 10, 17, 100), _claim(B, 400, 410, 120)],
            "edges": [{"type": "contradicts", "from": A, "to": B, "basis": "disagree"}],
        }
        await _run(payload, ORG, commit=True, session_id=uuid.uuid4())
        _, ref_id = await _edges_and_claim_refs(ORG)
        from_id = ref_id[A]

        assert psycopg2 is not None
        conn = psycopg2.connect(_owner_dsn())
        try:
            cur = conn.cursor()
            # Deleting the referenced claim while its edge stands is rejected:
            # SQLSTATE 23503 foreign_key_violation -- RESTRICT did its job.
            with pytest.raises(psycopg2.Error) as exc_info:
                cur.execute("DELETE FROM claims WHERE id = %s", (str(from_id),))
            assert exc_info.value.pgcode == "23503"
            conn.rollback()
            # And the ordered teardown succeeds: drop the edge first, then the claim
            # deletes cleanly -- exactly the order RESTRICT compels.
            cur.execute("DELETE FROM edges WHERE from_claim_id = %s", (str(from_id),))
            cur.execute("DELETE FROM claims WHERE id = %s", (str(from_id),))
            conn.commit()
        finally:
            conn.close()
    finally:
        _delete_org(ORG)
