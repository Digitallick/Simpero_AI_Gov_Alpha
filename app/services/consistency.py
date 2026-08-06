"""SIM-372: 3b Consistency -- related-fact arithmetic + FinGround formula
reconstruction (design doc Step 3b; arXiv:2604.23588).

Re-executes a computational claim's formula OUTSIDE the parser, from its
operand claims, and compares -- this is what the FinGround paper's 43%
missed-computational-error number is about: a uniform detector that only
checks a claim against its own citation never catches the case where the
citation is byte-exact but the ARITHMETIC connecting it to other claims is
wrong. Routes on `claim_type == "computational"` (SIM-364).

Match -> DERIVED_FROM edges, one row per operand (derived -> operand),
`metadata_={"rule": ..., "operands": [...]}`, `created_by="consistency"`.
Mismatch -> CONTRADICTS edges, one row per operand (same cardinality as
DERIVED_FROM, so the edge graph stays navigable the same way regardless of
outcome), plus the `formula_mismatch` flag on the DERIVED claim (that flag
already exists in the claims contract, reserved for exactly this). Per
SIM-372's acceptance: a mismatch never resolves anything -- every claim
involved persists untouched apart from that one flag.

HONEST SCOPE, not the full ~15-30 relationships: this repo has no committed
canonical attribute vocabulary (E2/SIM-344 lives upstream in the parser
repo/its own data, not as a file here) to hardcode real relationship
definitions against. Inventing plausible-looking attribute names here would
be guessing at a contract this module cannot verify. Instead, the engine
below is genuinely rule-driven (`Rule` + `DEFAULT_RULES`), and the default
catalog implements the fixed-arity, single-entity relationships the ticket
names that fit that shape (revenue x margin = gross profit; ebitda / revenue
= margin; pre + investment = post). Deliberately NOT implemented, and not
faked: "segments sum to total" (variable arity, cross-entity), "two years
imply the stated growth" (needs period-pair matching, not one period), and
"table figure = narrative figure modulo adjustments" (needs a fuzzy-match
concept beyond a tolerance). Extend DEFAULT_RULES once the real attribute
vocabulary is confirmed against the parser's actual output.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim, Edge
from app.services.tolerance import values_match

# Match vs mismatch uses the shared value_type-keyed tolerance table
# (app/services/tolerance.py): currency/ratio relative 5%, percent absolute
# 100 bp, count/date/text exact. The DERIVED claim's value_type selects the
# rule -- a recomputed grossMarginUsd (currency) tolerates 5%, a marginPct
# (percent) tolerates 100 bp, and a ratio no longer gets an absolute floor
# that made sub-1 comparisons vacuous.


@dataclass(frozen=True)
class Rule:
    name: str
    derived_attribute: str
    operand_attributes: tuple[str, ...]
    formula: Callable[[dict[str, float]], float]


# Starter catalog -- see the module docstring for what this covers and what
# it deliberately does not.
DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(
        name="gross_profit_from_revenue_and_margin",
        derived_attribute="grossProfitUsd",
        operand_attributes=("revenueUsd", "grossMarginPct"),
        formula=lambda o: o["revenueUsd"] * o["grossMarginPct"],
    ),
    Rule(
        name="margin_from_ebitda_and_revenue",
        derived_attribute="marginPct",
        operand_attributes=("ebitdaUsd", "revenueUsd"),
        formula=lambda o: o["ebitdaUsd"] / o["revenueUsd"] if o["revenueUsd"] else float("nan"),
    ),
    Rule(
        name="post_money_from_pre_money_and_investment",
        derived_attribute="postMoneyValuationUsd",
        operand_attributes=("preMoneyValuationUsd", "investmentAmountUsd"),
        formula=lambda o: o["preMoneyValuationUsd"] + o["investmentAmountUsd"],
    ),
    # SIM-373: matches the two subtraction-shaped relationships hand-verified
    # against tests/test_data/1st-App-H-PTL-Group-CIM.pdf's income statement
    # (see benchmarks/consistency/ptl_group_cim.yaml) -- added so that
    # benchmark has at least one real document's data this engine can
    # actually be scored against today, not just illustrative rules.
    Rule(
        name="gross_margin_from_revenue_and_cogs",
        derived_attribute="grossMarginUsd",
        operand_attributes=("revenueUsd", "cogsUsd"),
        formula=lambda o: o["revenueUsd"] - o["cogsUsd"],
    ),
    Rule(
        name="ebitda_from_gross_margin_and_opex",
        derived_attribute="ebitdaUsd",
        operand_attributes=("grossMarginUsd", "operatingCostsUsd"),
        formula=lambda o: o["grossMarginUsd"] - o["operatingCostsUsd"],
    ),
)


def _base(claim: Claim) -> float:
    """An operand's value in a canonical BASE unit for arithmetic: a percent
    (face value 28.5) becomes its ratio (0.285), so `revenue * margin` is
    dimensionally correct whether the margin arrived as "28.5%" (value_type
    percent) or "0.285" (value_type ratio). currency/ratio/count are already
    base. Read from value_type -- never assumed -- so the same rule works
    across documents that express the same field differently."""
    v = float(claim.value["normalized"])
    return v / 100.0 if claim.value.get("value_type") == "percent" else v


def _canonical_from_to(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


@dataclass
class ConsistencySummary:
    derived_from_edges: int = 0
    contradicts_edges: int = 0
    claims_flagged: int = 0
    rules_evaluated: int = 0
    skipped_missing_operands: int = 0
    not_implemented: list[str] = field(
        default_factory=lambda: [
            "segments_sum_to_total (variable arity, cross-entity)",
            "two_years_imply_stated_growth (needs period-pair matching)",
            "table_figure_matches_narrative_modulo_adjustments (needs fuzzy match)",
        ]
    )


async def reconcile_consistency(
    session: AsyncSession,
    *,
    data_source_id: uuid.UUID | None,
    run_id: str,
    rules: Sequence[Rule] = DEFAULT_RULES,
) -> ConsistencySummary:
    """Formula-reconstruction pass over one document's computational claims.

    `session` must already be RLS-scoped by the caller (same contract as
    app/services/reconciliation.py and memory_scope.py). `data_source_id`
    narrows to one document, same reasoning as reconciliation: a formula
    only makes sense within one document's own claims.
    """
    stmt = select(Claim).where(Claim.value["normalized"].isnot(None))
    stmt = stmt.where(
        Claim.data_source_id.is_(None)
        if data_source_id is None
        else Claim.data_source_id == data_source_id
    )
    claims = list((await session.scalars(stmt)).all())

    # (entity, period_year, period_kind, attribute) -> claims sharing that key.
    # Ambiguous when >1 claim shares a key (e.g. two conflicting revenue
    # claims for the same period) -- skipped rather than guessing which one
    # is "the" operand; that ambiguity is reconciliation's (SIM-371) job to
    # resolve first, not this pass's to silently pick a side on.
    by_key: dict[tuple[str, int | None, str | None, str], list[Claim]] = {}
    for c in claims:
        by_key.setdefault((c.entity, c.period_year, c.period_kind, c.attribute), []).append(c)

    summary = ConsistencySummary()
    for rule in rules:
        summary.rules_evaluated += 1
        derived_candidates = [
            c
            for key, group in by_key.items()
            if key[3] == rule.derived_attribute and len(group) == 1
            for c in group
            if c.claim_type == "computational"
        ]
        for derived in derived_candidates:
            await _check_rule(session, derived, rule, by_key, run_id=run_id, summary=summary)
    return summary


async def _check_rule(
    session: AsyncSession,
    derived: Claim,
    rule: Rule,
    by_key: dict[tuple[str, int | None, str | None, str], list[Claim]],
    *,
    run_id: str,
    summary: ConsistencySummary,
) -> None:
    operands: dict[str, Claim] = {}
    for attr in rule.operand_attributes:
        key = (derived.entity, derived.period_year, derived.period_kind, attr)
        group = by_key.get(key)
        if not group or len(group) != 1:
            # Missing, or ambiguous (>1 candidate) -- see the module note on
            # by_key above. Either way, this rule cannot be evaluated for
            # this derived claim.
            summary.skipped_missing_operands += 1
            return
        operands[attr] = group[0]

    # Operands in base units; the formula yields a base result, which is then
    # put into the DERIVED claim's storage unit before comparing -- a percent is
    # stored face value (its ratio x 100) -- so both the comparison and its
    # value_type tolerance run in the derived's native units.
    operand_values = {attr: _base(c) for attr, c in operands.items()}
    derived_vt = derived.value.get("value_type", "")
    expected_base = rule.formula(operand_values)
    expected = expected_base * 100.0 if derived_vt == "percent" else expected_base
    actual = float(derived.value["normalized"])
    matches = values_match(expected, actual, derived_vt)

    for operand in operands.values():
        if matches:
            await _write_edge(
                session,
                org_id=derived.org_id,
                from_claim_id=derived.id,
                to_claim_id=operand.id,
                type_="derived_from",
                basis=f"{rule.name}: recomputed {expected:.4g} matches claimed {actual:.4g}",
                run_id=run_id,
                metadata_={"rule": rule.name, "operands": [str(o.id) for o in operands.values()]},
            )
            summary.derived_from_edges += 1
        else:
            from_id, to_id = _canonical_from_to(derived.id, operand.id)
            await _write_edge(
                session,
                org_id=derived.org_id,
                from_claim_id=from_id,
                to_claim_id=to_id,
                type_="contradicts",
                basis=f"{rule.name}: recomputed {expected:.4g} vs claimed {actual:.4g}",
                run_id=run_id,
                metadata_={"rule": rule.name, "value_delta": expected - actual},
            )
            summary.contradicts_edges += 1

    # formula_mismatch: reserved in the claims contract's flags enum for
    # exactly this -- a re-executed formula that disagrees with its own
    # claimed value. Flags the DERIVED claim only; operands are not at
    # fault for a formula that combines them incorrectly.
    if not matches and (not derived.flags or "formula_mismatch" not in derived.flags):
        derived.flags = [*(derived.flags or []), "formula_mismatch"]
        summary.claims_flagged += 1


async def _write_edge(
    session: AsyncSession,
    *,
    org_id: int,
    from_claim_id: uuid.UUID,
    to_claim_id: uuid.UUID,
    type_: str,
    basis: str,
    run_id: str,
    metadata_: dict,
) -> None:
    stmt = (
        pg_insert(Edge)
        .values(
            org_id=org_id,
            from_claim_id=from_claim_id,
            to_claim_id=to_claim_id,
            type=type_,
            basis=basis,
            created_by="consistency",
            run_id=run_id,
            metadata_=metadata_,
        )
        .on_conflict_do_nothing(constraint="uq_edges_org_from_to_type")
    )
    await session.execute(stmt)
