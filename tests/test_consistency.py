"""SIM-372: 3b consistency -- FinGround formula reconstruction. A computational
claim is re-derived from its operand claims; a match writes DERIVED_FROM
edges (one per operand); a mismatch writes CONTRADICTS edges (one per
operand) and flags the derived claim `formula_mismatch` -- never resolves
anything, every claim persists."""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models import Claim, Edge, Organisation
from app.models.organisation import OrgType
from app.services.consistency import ConsistencySummary, Rule, reconcile_consistency

ORG = "sim372-consistency-org"
OTHER = "sim372-consistency-other"


def _owner_dsn() -> str:
    return (
        os.environ.get("ALEMBIC_DATABASE_URL", "").replace("+psycopg2", "").replace("+asyncpg", "")
    )


def _db_available() -> bool:
    if not _owner_dsn():
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


def _delete_org(org_key: str) -> None:
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


def _claim(
    *,
    entity: str = "TestCo",
    attribute: str,
    normalized: float,
    period_year: int = 2024,
    period_kind: str = "A",
    claim_type: str = "numerical",
    value_type: str = "currency",
) -> Claim:
    return Claim(
        entity=entity,
        attribute=attribute,
        period_year=period_year,
        period_kind=period_kind,
        claim_type=claim_type,
        value={
            "raw": str(normalized),
            "normalized": normalized,
            "unit": "USD",
            "scale_multiplier": 1,
            "scale_source": "explicit_in_value",
            "value_type": value_type,
        },
        kind="pdf",
        page=1,
        char_start=0,
        char_end=1,
        status="proposed",
    )


async def _seed(org_key: str, claims: dict[str, Claim]) -> dict[str, uuid.UUID]:
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE dd_app"))
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": org_key})
        org = Organisation(
            clerk_org_id=org_key, name=f"consistency test ({org_key})", type=OrgType.PE_FIRM
        )
        session.add(org)
        await session.flush()
        for c in claims.values():
            c.org_id = org.id
        session.add_all(claims.values())
        await session.flush()
        return {label: c.id for label, c in claims.items()}


async def _run_consistency(org_key: str, run_id: str) -> ConsistencySummary:
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE dd_app"))
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": org_key})
        summary = await reconcile_consistency(session, data_source_id=None, run_id=run_id)
        await session.flush()
    return summary


async def _edges_and_claims(org_key: str) -> tuple[list[Edge], dict[uuid.UUID, Claim]]:
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": org_key})
        edges = (await session.execute(select(Edge))).scalars().all()
        claims = (await session.execute(select(Claim))).scalars().all()
    return list(edges), {c.id: c for c in claims}


def _gross_profit_claims(*, derived_value: float) -> dict[str, Claim]:
    return {
        "revenue": _claim(attribute="revenueUsd", normalized=1_000_000),
        "margin": _claim(attribute="grossMarginPct", normalized=0.20),
        "derived": _claim(
            attribute="grossProfitUsd", normalized=derived_value, claim_type="computational"
        ),
    }


@requires_db
async def test_matching_formula_writes_derived_from_edges() -> None:
    _delete_org(ORG)
    try:
        ids = await _seed(ORG, _gross_profit_claims(derived_value=200_000))  # 1_000_000 * 0.20
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 2
        assert summary.contradicts_edges == 0
        assert summary.claims_flagged == 0

        edges, claims = await _edges_and_claims(ORG)
        derived_from = [e for e in edges if e.type == "derived_from"]
        assert len(derived_from) == 2
        assert {e.from_claim_id for e in derived_from} == {ids["derived"]}
        assert {e.to_claim_id for e in derived_from} == {ids["revenue"], ids["margin"]}
        for e in derived_from:
            assert e.created_by == "consistency"
            assert e.run_id == "run-1"
            assert e.metadata_ is not None
            assert e.metadata_["rule"] == "gross_profit_from_revenue_and_margin"

        # Nothing flagged: the formula held.
        assert not claims[ids["derived"]].flags
    finally:
        _delete_org(ORG)


@requires_db
async def test_mismatched_formula_flags_and_contradicts_never_resolves() -> None:
    _delete_org(ORG)
    try:
        ids = await _seed(ORG, _gross_profit_claims(derived_value=150_000))  # expected 200_000
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 0
        assert summary.contradicts_edges == 2
        assert summary.claims_flagged == 1

        edges, claims = await _edges_and_claims(ORG)
        contradicts = [e for e in edges if e.type == "contradicts"]
        assert len(contradicts) == 2
        for e in contradicts:
            assert e.from_claim_id < e.to_claim_id
            assert {e.from_claim_id, e.to_claim_id} <= {
                ids["derived"],
                ids["revenue"],
                ids["margin"],
            }
            assert ids["derived"] in (e.from_claim_id, e.to_claim_id)

        # The derived claim is flagged; operands are not (the formula, not
        # the operands, is what's suspect).
        assert "formula_mismatch" in (claims[ids["derived"]].flags or [])
        assert not claims[ids["revenue"]].flags
        assert not claims[ids["margin"]].flags

        # KEEP BOTH: every claim survives the conflict, nothing auto-resolved.
        assert set(claims) == {ids["revenue"], ids["margin"], ids["derived"]}
    finally:
        _delete_org(ORG)


@requires_db
async def test_within_tolerance_still_matches() -> None:
    """currency = relative 5%: 201_000 is within 5% of the recomputed 200_000."""
    _delete_org(ORG)
    try:
        await _seed(ORG, _gross_profit_claims(derived_value=201_000))
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 2
        assert summary.contradicts_edges == 0
    finally:
        _delete_org(ORG)


@requires_db
async def test_outside_tolerance_mismatches() -> None:
    """230_000 vs the recomputed 200_000 is 15% -- outside the 5% currency tolerance."""
    _delete_org(ORG)
    try:
        await _seed(ORG, _gross_profit_claims(derived_value=230_000))
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 0
        assert summary.contradicts_edges == 2
    finally:
        _delete_org(ORG)


@requires_db
async def test_missing_operand_skips_the_rule() -> None:
    _delete_org(ORG)
    try:
        claims = _gross_profit_claims(derived_value=200_000)
        del claims["margin"]  # only revenue present -- rule cannot be checked
        await _seed(ORG, claims)
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 0
        assert summary.contradicts_edges == 0
        assert summary.skipped_missing_operands >= 1

        edges, _ = await _edges_and_claims(ORG)
        assert edges == []
    finally:
        _delete_org(ORG)


@requires_db
async def test_non_computational_derived_claim_is_not_routed() -> None:
    """Routing is on claim_type == computational -- a claim that merely HAS
    the derived attribute name but isn't typed computational must be
    ignored, not treated as a formula result to verify."""
    _delete_org(ORG)
    try:
        claims = _gross_profit_claims(derived_value=999_999)  # would mismatch if checked
        claims["derived"].claim_type = "numerical"
        await _seed(ORG, claims)
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 0
        assert summary.contradicts_edges == 0
        edges, _ = await _edges_and_claims(ORG)
        assert edges == []
    finally:
        _delete_org(ORG)


@requires_db
async def test_ambiguous_operand_is_skipped_not_guessed() -> None:
    """Two conflicting revenue claims for the same entity/period -- this pass
    must not silently pick one; that's reconciliation's (SIM-371) job first."""
    _delete_org(ORG)
    try:
        claims = _gross_profit_claims(derived_value=200_000)
        claims["revenue_dup"] = _claim(attribute="revenueUsd", normalized=1_100_000)
        await _seed(ORG, claims)
        summary = await _run_consistency(ORG, "run-1")
        assert summary.derived_from_edges == 0
        assert summary.contradicts_edges == 0
        assert summary.skipped_missing_operands >= 1
    finally:
        _delete_org(ORG)


@requires_db
async def test_rerun_is_idempotent_via_the_unique_constraint() -> None:
    _delete_org(ORG)
    try:
        await _seed(ORG, _gross_profit_claims(derived_value=200_000))
        await _run_consistency(ORG, "run-1")
        edges_first, _ = await _edges_and_claims(ORG)
        assert len(edges_first) == 2

        await _run_consistency(ORG, "run-2")
        edges_second, _ = await _edges_and_claims(ORG)
        assert len(edges_second) == 2
        assert {e.id for e in edges_first} == {e.id for e in edges_second}
    finally:
        _delete_org(ORG)


@requires_db
async def test_ptl_group_cim_2018f_holds_against_the_real_income_statement() -> None:
    """End-to-end against tests/test_data/1st-App-H-PTL-Group-CIM.pdf's actual
    Income Statement (p.11, 2018F column, CAD thousands): Revenue 17,146,
    COGS 13,515, Gross Margin 3,631, Operating costs 1,672, Normalized EBITDA
    1,959. Both relationships hold exactly. See
    benchmarks/consistency/ptl_group_cim.yaml for the full hand-verified set
    this pass doesn't yet cover (ratios, period-over-period growth)."""
    _delete_org(ORG)
    try:
        ids = await _seed(
            ORG,
            {
                "revenue": _claim(attribute="revenueUsd", normalized=17_146, period_year=2018),
                "cogs": _claim(attribute="cogsUsd", normalized=13_515, period_year=2018),
                "gross_margin": _claim(
                    attribute="grossMarginUsd",
                    normalized=3_631,
                    period_year=2018,
                    claim_type="computational",
                ),
                "opex": _claim(attribute="operatingCostsUsd", normalized=1_672, period_year=2018),
                "ebitda": _claim(
                    attribute="ebitdaUsd",
                    normalized=1_959,
                    period_year=2018,
                    claim_type="computational",
                ),
            },
        )
        summary = await _run_consistency(ORG, "ptl-2018f")
        assert summary.derived_from_edges == 4  # 2 operands x 2 rules
        assert summary.contradicts_edges == 0
        assert summary.claims_flagged == 0

        edges, claims = await _edges_and_claims(ORG)
        rules_seen = {e.metadata_["rule"] for e in edges if e.metadata_}
        assert rules_seen == {
            "gross_margin_from_revenue_and_cogs",
            "ebitda_from_gross_margin_and_opex",
        }
        assert set(claims) == set(ids.values())
    finally:
        _delete_org(ORG)


@requires_db
async def test_rls_isolates_two_orgs() -> None:
    for org in (ORG, OTHER):
        _delete_org(org)
    try:
        await _seed(ORG, _gross_profit_claims(derived_value=200_000))
        await _seed(OTHER, _gross_profit_claims(derived_value=150_000))  # mismatch on purpose

        await _run_consistency(ORG, "run-1")
        await _run_consistency(OTHER, "run-1")

        org_edges, _ = await _edges_and_claims(ORG)
        other_edges, _ = await _edges_and_claims(OTHER)
        assert {e.type for e in org_edges} == {"derived_from"}
        assert {e.type for e in other_edges} == {"contradicts"}
        assert {e.id for e in org_edges}.isdisjoint({e.id for e in other_edges})
    finally:
        for org in (ORG, OTHER):
            _delete_org(org)


@requires_db
async def test_tolerance_is_selected_by_the_derived_claims_value_type() -> None:
    """The derived claim's value_type drives the tolerance. A ratio derived
    (winRate = wins/total) claimed at 0.90 against a recomputed 0.20 must
    CONTRADICT -- under the old flat 1% + $1 floor it would have matched, the
    exact vacuous-ratio bug the value_type table fixes."""
    ratio_rule = Rule(
        name="win_rate_from_wins_and_total",
        derived_attribute="winRateRatio",
        operand_attributes=("winsCount", "totalCount"),
        formula=lambda o: o["winsCount"] / o["totalCount"] if o["totalCount"] else float("nan"),
    )
    _delete_org(ORG)
    try:
        await _seed(
            ORG,
            {
                "wins": _claim(attribute="winsCount", normalized=20, value_type="count"),
                "total": _claim(attribute="totalCount", normalized=100, value_type="count"),
                "ratio": _claim(
                    attribute="winRateRatio",
                    normalized=0.90,
                    claim_type="computational",
                    value_type="ratio",
                ),
            },
        )
        async with AsyncSessionLocal() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE dd_app"))
            await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": ORG})
            summary = await reconcile_consistency(
                session, data_source_id=None, run_id="run-ratio", rules=(ratio_rule,)
            )
            await session.flush()
        assert summary.contradicts_edges == 2  # one per operand
        assert summary.derived_from_edges == 0
    finally:
        _delete_org(ORG)
