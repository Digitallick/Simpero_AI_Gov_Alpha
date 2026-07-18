"""Demo: ingest claims JSON (from the parse service) into the claims spine.

The right half of the C3 seam -- reads the JSON scripts/emit_claims.py produced
in the parse repo, validates each claim against contracts/claims.schema.json,
and INSERTs into the claims table through the app's real model + session, as the
app role, under a tenant context. This is the embryo of SIM-63's backend ingest.

SAFETY, by construction:
- Dry run by DEFAULT. Everything happens inside one transaction that is ROLLED
  BACK unless --commit is passed. A plain run leaves the database exactly as it
  found it.
- Inserts run as dd_app (via SET LOCAL ROLE), not the owner, so RLS is actually
  exercised -- the same path the app takes. doadmin would bypass RLS and prove
  nothing.
- --org-key names a demo tenant; the demo organisation row is created in the
  same transaction, so it too vanishes on rollback.

    uv run python scripts/ingest_claims.py claims.json --org-key demo_e2e_ptl
    uv run python scripts/ingest_claims.py claims.json --org-key demo_e2e_ptl --commit

The nested seam location ({kind, page, char_start, ...}) is flattened into the
table's per-format columns here -- that translation IS the ingest's job.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models import Claim, Organisation
from app.models.organisation import OrgType

_CONTRACT = Path(__file__).parent.parent / "contracts" / "claims.schema.json"

# location keys that map to their own flat column; everything else in a location
# (kind is handled explicitly; file/document_* are debug-only and not stored).
_LOCATION_COLUMNS = ("page", "char_start", "char_end", "bbox", "sheet", "cell_ref", "paragraph")


def _row_from_claim(claim: dict, org_id: int, session_id: uuid.UUID) -> Claim:
    """One seam-JSON claim -> one Claim ORM row, flattening the location."""
    location = claim["location"]
    row = Claim(
        org_id=org_id,
        # Which run produced this claim. Tagging every row makes two extraction
        # runs over the same document independently queryable in one table --
        # which is what a golden-set diff needs: compare THIS run against THAT
        # one, rather than against whatever happens to be in the table.
        session_id=session_id,
        entity=claim["entity"],
        attribute=claim["attribute"],
        value=claim["value"],
        # The parse service's emit.py cannot carry a period yet, but the
        # contract and this table both have the columns, so anything upstream
        # that did determine one is preserved rather than dropped on the floor.
        period_year=claim.get("period_year"),
        period_kind=claim.get("period_kind"),
        status=claim["status"],
        verification_method=claim.get("verification_method"),
        section=claim.get("section"),
        flags=claim.get("flags") or None,
        kind=location["kind"],
    )
    for key in _LOCATION_COLUMNS:
        if key in location:
            setattr(row, key, location[key])
    return row


def _validate(claims: list[dict]) -> None:
    """Fail loudly before any write if the JSON does not match the contract.

    The seam's whole point: a shape the store cannot hold must be caught here,
    not discovered as a half-written row.
    """
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(json.loads(_CONTRACT.read_text()))
    for i, claim in enumerate(claims):
        errors = sorted(validator.iter_errors(claim), key=str)
        if errors:
            raise SystemExit(f"claim {i} violates the contract: {errors[0].message}")


async def _run(payload: dict, org_key: str, commit: bool, session_id: uuid.UUID) -> None:
    claims = payload["claims"]
    _validate(claims)
    print(f"{len(claims)} claims validated against the contract.")
    print(f"session_id for this run: {session_id}")

    async with AsyncSessionLocal() as session, session.begin():
        # Establish the app's identity and tenant FIRST, so the entire insert
        # path runs exactly as the app would: as dd_app (not the table owner, so
        # RLS applies), under a tenant context. This works whether the connection
        # is dd_app directly (the sandbox) or an admin role (SET ROLE then drops
        # to dd_app) -- and it means the organisation insert below is itself
        # subject to RLS's WITH CHECK, not slipped in as the owner.
        await session.execute(text("SET LOCAL ROLE dd_app"))
        # set_config(..., is_local=true) is the parameterizable form of
        # `SET LOCAL app.org_id = ...`. A bare `SET LOCAL x = :p` cannot bind
        # a parameter under asyncpg (SET is not a preparable statement) --
        # which is a latent bug in app/core/dependencies.py::get_db today.
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": org_key})

        # The demo tenant. Get-or-create: in reality the org already exists (it
        # comes from signup), and re-running the demo must not trip the unique
        # clerk_org_id. The SELECT runs under RLS, so it can only ever find the
        # current tenant's own row.
        org = await session.scalar(select(Organisation).where(Organisation.clerk_org_id == org_key))
        if org is None:
            # WITH CHECK passes because clerk_org_id equals app.org_id, set above.
            org = Organisation(
                clerk_org_id=org_key, name=f"E2E demo ({org_key})", type=OrgType.PE_FIRM
            )
            session.add(org)
            await session.flush()  # assigns org.id
        org_id = org.id

        session.add_all(_row_from_claim(c, org_id, session_id) for c in claims)
        await session.flush()

        # Read back as dd_app under this tenant: the app's own view.
        seen = await session.scalar(select(func.count()).select_from(Claim))
        print(f"dd_app, tenant {org_key!r}: sees {seen} claims (inserted {len(claims)}).")

        # Prove isolation: a different tenant sees none of them.
        await session.execute(text("SELECT set_config('app.org_id', 'some_other_org', true)"))
        other = await session.scalar(select(func.count()).select_from(Claim))
        print(f"dd_app, a DIFFERENT tenant: sees {other} claims (RLS isolation).")

        if commit:
            print("--commit: persisting.")
        else:
            # Nothing above this line survives.
            raise _Rollback()


class _Rollback(Exception):
    """Sentinel to abandon the transaction on a dry run without an error exit."""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claims_json", type=Path)
    parser.add_argument("--org-key", required=True, help="clerk_org_id for the demo tenant.")
    parser.add_argument("--commit", action="store_true", help="Persist. Default is a dry run.")
    parser.add_argument(
        "--session-id",
        help="Tag every claim with this run id (UUID). Defaults to a fresh one. "
        "Use it to compare two extraction runs over the same document.",
    )
    args = parser.parse_args(argv)

    payload = json.loads(args.claims_json.read_text())
    session_id = uuid.UUID(args.session_id) if args.session_id else uuid.uuid4()
    try:
        asyncio.run(_run(payload, args.org_key, args.commit, session_id))
    except _Rollback:
        print("dry run: transaction rolled back, database unchanged. Pass --commit to persist.")


if __name__ == "__main__":
    main()
