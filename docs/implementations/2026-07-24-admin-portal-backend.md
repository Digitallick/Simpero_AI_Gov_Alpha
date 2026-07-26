# Admin Portal — Backend Implementation Summary

**Status:** Implemented, independently tested, not yet committed to git (working tree only as of this writing).
**Plan followed:** `docs/plans/admin-portal-backend.md` (Rev. 3), with amendments recorded below.
**Sessions:** 2026-07-23 → 2026-07-24 (initial implementation), a second, separate 2026-07-24 session (frontend e2e-testing follow-ups — see "Session 2" below), a third 2026-07-24 session (three frontend-contract changes — see "Session 3" below), a fourth 2026-07-24/25 session (member/admin role-change feature + two live bugs found and fixed — see "Session 4" below), and a fifth 2026-07-25 session (member/admin status lifecycle: soft-delete, a new cross-org removal endpoint, and closing both reactivation gaps — see "Session 5" below).

This document is a record of what was actually built, the decisions made during implementation that weren't nailed down in the plan, a bug that was found and fixed along the way, and what's deliberately left out of scope. Treat the plan doc as the design rationale (RLS reasoning, file:line citations against the pre-existing codebase) and this doc as the "what actually shipped + what's still open" record. **Where a later session reversed or extended an earlier decision, both are recorded — read the "Session 2"/"Session 3"/"Session 4"/"Session 5" callouts, they supersede the original text around them.**

---

## What this feature is

A two-tier admin portal mounted at `/api/admin`, separate from the product API:

- **Platform admins** — Simpero-internal staff, members of a dedicated Simpero Clerk org. They create client organizations, seed each with one account-manager (client-admin) invitation, list existing client orgs (including who's active vs. removed), invite `org:member` **or** `org:admin` users directly into any client org, change any member's role, remove members, and delete client orgs outright.
- **Client admins** (account managers, `org:admin` in a client org) — invite/manage `org:member` product users, change member roles (member ↔ admin), and remove members, **within their own org only**.

Admin identity is authoritative in a new Postgres table (`clerk_admin_users`), not the Clerk JWT — the JWT is only trusted at two specific points (JIT provisioning/re-invite reactivation, and revocation sync), everywhere else authorization reads the table. As of Session 4, a person can legitimately hold **both** an active `users` row (product access) and an active `clerk_admin_users` row (admin access) at the same time — promoting a member to admin keeps both in sync rather than being mutually exclusive, a deliberate reopening of what was originally an "admin-only, no dual role" design (see Session 4 below).

---

## What was built

### Database

- **New table `clerk_admin_users`** (migration `alembic/versions/a1b2c3d4e5f6_clerk_admin_users.py`, `down_revision` chains to the prior head `fb49da6a9bc0`): `id`, `clerk_user_id` (unique), `clerk_org_id`, `org_id` (FK → `organisation.id`), `email` (nullable), `admin_type` (enum `admintype`: `platform` | `client`), `status` (default `active`), `created_at`.
- **RLS**: `ENABLE ROW LEVEL SECURITY` + a `FOR ALL TO dd_app USING (clerk_org_id = current_setting('app.org_id', true))` policy — identical shape to the existing `users`/`organisation` policies. No `FORCE`, no explicit `GRANT`/`REVOKE` (relies on the existing `ALTER DEFAULT PRIVILEGES FOR ROLE doadmin` bootstrap, same as every other tenant table).
- Verified clean `upgrade head` → `downgrade -1` → `upgrade head` round-trip, including direct schema inspection (table/enum/indexes/policy fully removed and restored, not just "no error").

### New Python modules

| File | Purpose |
|---|---|
| `app/models/clerk_admin_user.py` | `ClerkAdminUser` ORM model + `AdminType` enum (`platform`/`client`) |
| `app/repo/AdminUserRepo.py` | `get_by_clerk_id`, `upsert` (JIT-create only, `DO NOTHING`), `deactivate`, `reactivate_or_create` (Session 4, own-org promote — `DO UPDATE`), `reactivate_if_exists` (Session 4, cross-org promote — `UPDATE`-only, never inserts) |
| `app/core/admin_dependencies.py` | `get_admin_db` (RLS-clamped session + JIT provisioning), `_ensure_org_provisioned`, `_ensure_admin_provisioned` (JIT create + downgrade sync + Session 5 re-invite reactivation), `require_org_admin`, `require_platform_admin`, `_admin_actor` (audit-actor resolution), `_set_org_scope` (Session 4, RLS re-pointing for cross-org local writes) |
| `app/services/admin/clerk_admin.py` | Raw-`httpx` Clerk Backend API adapter: `create_organization`, `list_organizations`, `create_organization_invitation`, `list_organization_invitations`, `revoke_organization_invitation`, `list_organization_memberships`, `remove_organization_membership`, `update_organization_membership` (Session 4), `delete_organization` (Session 3), `fetch_clerk_user`, `fetch_clerk_user_primary_email`, plus `clerk_error_to_http`/`clerk_created_at` helpers |
| `app/schemas/admin/{context,invitations,members,organizations}.py` | Pydantic request/response models, all extending `CamelModel` (camelCase on the wire) |
| `app/api/admin/__init__.py` | Mounts all admin sub-routers under `prefix="/admin"` |
| `app/api/admin/context.py` | `GET /admin/context` |
| `app/api/admin/invitations.py` | `POST/GET/DELETE /admin/invitations` (org-admin, own org only) |
| `app/api/admin/members.py` | `GET/DELETE /admin/members` (org-admin, own org only) |
| `app/api/admin/organizations.py` | `POST/GET /admin/organizations` (platform-admin) |
| `app/api/admin/platform_invitations.py` | `POST /admin/organizations/{clerk_org_id}/invitations` (platform-admin, D2) |
| `app/api/admin/platform_members.py` | `GET /admin/organizations/{clerk_org_id}/members` (platform-admin) — Session 2 addition |
| `app/api/admin/platform_organization_delete.py` | `DELETE /admin/organizations/{clerk_org_id}` (platform-admin) — Session 3 addition, see below |
| `tests/test_admin_portal.py` | Admin-portal test file; grew 29 → 31 → 34 tests across Sessions 1–2, gained Session 3's coverage (full suite 90 passed), Session 4's role-change coverage (full suite 105 → 107 after a follow-up fix), and Session 5's status-lifecycle coverage (full suite 114 → 116) — guards, JIT provisioning, RLS isolation, downgrade/reactivation sync, contract tests per endpoint |
| `app/models/organisation.py` | `Users` gained `status`/`deactivated_at` (Session 5, soft-delete) |
| `app/repo/UserRepo.py` | `reactivate` (Session 5 — product-side re-invite reactivation, mirrors `AdminUserRepo`'s equivalent) |

### Config / dependencies

- `app/core/config.py`: added `simpero_platform_org_id: str = ""` (fails closed when unset) and `app_base_url: str = "http://localhost:3000"`.
- `.env.example`: documented both settings plus the one-time bootstrap note ("grant a human `org:admin` in the Simpero Clerk org").
- `app/main.py`: one import + one `include_router(admin.router, prefix=API_PREFIX)` line.
- `app/models/__init__.py`: registered `ClerkAdminUser`/`AdminType` for Alembic.
- `pyproject.toml`: added `email-validator>=2.3.0` (required by Pydantic's `EmailStr` used in the invitation/org-creation request schemas — no other new runtime dependency).

### API surface (final)

All under `/api/admin`, auth via existing `get_claims` (Clerk bearer), all responses `CamelModel` (camelCase), errors `{"detail": "..."}`.

- `GET /admin/context` → `{ isPlatformAdmin, isOrgAdmin, org }` — capability/display endpoint, no guard dependency of its own (any authenticated caller can read their own context).
- `POST /admin/invitations` *(guard: `require_org_admin`)* → invite `org:member` into the caller's own org.
- `GET /admin/invitations` *(guard: `require_org_admin`)* → pending invitations for the caller's own org.
- `DELETE /admin/invitations/{invitation_id}` *(guard: `require_org_admin`)* → revoke a pending invitation.
- `GET /admin/members` *(guard: `require_org_admin`)* → product users (`users` table) in the caller's own org, **both active and inactive** (Session 5 — no status filter; inactive/removed members stay visible so they can be re-invited from the same screen). Each row carries `status: "active" | "inactive"` (Session 5 addition to `MemberResponse`).
- `PATCH /admin/members/{user_id}` *(guard: `require_org_admin`, Session 4 addition)* → change a member's role (`"member"` ↔ `"admin"`), keeping the Clerk org membership role, `users.role`, and `clerk_admin_users` in sync. Self-change and last-active-admin-demote both blocked (403). Same-role request is a silent no-op (200, no Clerk call, no writes).
- `DELETE /admin/members/{user_id}` *(guard: `require_org_admin`)* → remove a member; self-removal and last-active-admin-removal both blocked (403). **Soft-delete as of Session 5** — the `users` row stays in the table (`status="inactive"`, `deactivated_at` set), not hard-deleted. If the target held an active `clerk_admin_users` row (a promoted dual-state member), that's deactivated too (Session 4 fix — see below); Clerk org membership is always revoked regardless of local row state.
- `POST /admin/organizations` *(guard: `require_platform_admin`)* → create a client org in Clerk; seeds one `org:admin` invitation **unless `accountManagerEmail` is omitted** (Session 3 — see below), in which case `invitation` is `null` in the response and no invitation is created.
- `GET /admin/organizations` *(guard: `require_platform_admin`)* → list client orgs from Clerk, excluding the platform org.
- `POST /admin/organizations/{clerk_org_id}/invitations` *(guard: `require_platform_admin`, D2)* → invite into an **arbitrary** client org; `role` is `"member"` (→ `org:member`, redirects to `/sign-up`) or, as of Session 3, `"admin"` (→ `org:admin`, redirects to `/admin/sign-up`). **Session 4 bug fix:** no longer passes `inviter_user_id` — see below.
- `GET /admin/organizations/{clerk_org_id}/members` *(guard: `require_platform_admin`, Session 2 addition)* → list an arbitrary client org's members. **As of Session 5, this is a merge**, not a pure Clerk read: live Clerk memberships (always `status: "active"`) plus locally-soft-deleted `users` rows for that org (`status: "inactive"`) that AREN'T also live Clerk members (dedup — live Clerk wins if both exist, e.g. someone removed then re-invited but not yet logged back in). See "Session 5 additions" below.
- `PATCH /admin/organizations/{clerk_org_id}/members/{clerk_user_id}` *(guard: `require_platform_admin`, Session 4 addition)* → cross-org role change, same sync semantics as the own-org PATCH above but best-effort on the local `users` write (the target org's local row may not exist). Keyed by `clerk_user_id`, not a local int id.
- `DELETE /admin/organizations/{clerk_org_id}/members/{clerk_user_id}` *(guard: `require_platform_admin`, Session 5 addition)* → cross-org member removal, mirrors the own-org DELETE's soft-delete + `clerk_admin_users` deactivation semantics, best-effort on the local write.
- `DELETE /admin/organizations/{clerk_org_id}` *(guard: `require_platform_admin`, Session 3 addition)* → delete a client org in Clerk (only — no local DB cascade). See "Session 3 additions" below.

---

## Decisions made during implementation (amendments to the Rev. 3 plan)

The plan explicitly left several questions open for a human (its "Open questions / risks" section, R1–R6). These were resolved in conversation during this session:

### R1 — `created_by` on Clerk org creation: resolved, then reversed

The plan flagged that Clerk's `POST /organizations` requires a `created_by` user, who becomes an admin member of the new org in Clerk — meaning a platform admin could, in principle, switch their active Clerk org and read a client's product data through RLS. The plan's own default recommendation was "remove-after-create" (immediately revoke that membership) or a dedicated bot user.

**Session 1 decision:** neither. Platform admins are internal Simpero staff who are *meant* to be able to see client data — this isn't a hole. `create_organization(name, org_type, created_by=claims["user_id"])` and nothing further. No `remove_organization_membership` call after creation, no bot/service account.

**Session 2 (2026-07-24) — reversed by explicit product decision:** manual end-to-end testing of the frontend against a live backend surfaced a firmer requirement: platform admins must only ever be members of the Simpero platform org, never of client orgs, full stop — regardless of whether they're *permitted* to see client data through some other means. `create_org` (`app/api/admin/organizations.py`) now calls `remove_organization_membership` immediately after creating the org and seeding the admin invitation, stripping the calling platform admin's own membership. Clerk's API still requires `created_by` to seed an initial admin member — that's unavoidable — so the pattern is now "create, then immediately remove," not "create without."

- **Best-effort, not transactional.** A Clerk-side failure on the removal call does not fail the request or roll back the already-created org/invitation. The outcome is recorded on the `admin_organization_created` audit row as a new boolean field, `creator_membership_removed` — a failed cleanup is discoverable via the audit trail rather than silently swallowed, but it doesn't block the org-creation flow.
- Files: `app/api/admin/organizations.py` (`create_org`), docstring update in `app/services/admin/clerk_admin.py` (`create_organization`).

### R3 — last-admin lockout protection on `DELETE /admin/members`: resolved as an intentional no-op
The plan asked whether removing an org's last admin should be blocked. On inspection this doesn't actually attach to this endpoint: `DELETE /admin/members` only ever targets rows in the local `users` table (product `org:member` users), and client admins are explicitly **admin-only** — they never get a `users` row. There's also no admin-removal endpoint in this plan at all (deferred to BACKLOG). So "last admin" can't structurally be checked here.

**Decision:** add the defensive check anyway, as inert forward-compat scaffolding: `app/api/admin/members.py`'s `remove_member` queries the count of active admins for the org before deleting, discards the result, and is marked:
```python
# ponytail: <=1 active admins for the removed row's org — irrelevant to
# member deletion today since admins hold no users row (R3); becomes
# load-bearing once DELETE/PATCH /admin/admins exists.
```
This costs one extra DB round-trip per member removal today and enforces nothing. **Flagged by the independent test pass as dead code worth a cleanup or activation pass** — see Gaps below.

### `isOrgAdmin` in `GET /admin/context`: switched from JWT-derived to table-derived
Originally implemented as `claims.get("org_role") == "admin"` (reading Clerk's JWT claim directly). This was inconsistent with the rest of the security model, where every real guard (`require_org_admin`, `require_platform_admin`) deliberately ignores `org_role` outside of provisioning and checks the `clerk_admin_users` table instead.

**Fixed to:** `isOrgAdmin = (row is not None and row.status == "active")`, no filter on `admin_type` — matching `require_org_admin`'s exact check, including the intentional overlap where a platform admin's own row also satisfies it. `isPlatformAdmin` was originally left as a bare JWT-tenant-id-vs-settings comparison (`claims["tenant_id"] == settings.simpero_platform_org_id`), by design per the plan.

**Correction, bug found later the same day (2026-07-24), fixed:** that bare tenant comparison turned out to be a real bug, not a deliberate design choice worth keeping — it only checks whether the caller's *active Clerk org* is the platform org, not whether they actually hold platform-admin privileges. Any ordinary product member of the Simpero platform org got `isPlatformAdmin: true` back, even with zero admin rights (client-facing impact was limited — the real guard, `require_platform_admin`, was and is correct, so nothing was actually exposed; this was a client-context-flag bug that let such a user into the frontend's admin shell UI only to have every real request 403). Fixed to mirror `require_platform_admin`'s exact check: active `clerk_admin_users` row, `admin_type == AdminType.platform`, AND the tenant match — reusing the same `admin_row` already fetched for `isOrgAdmin`, no extra query. Regression tests added for both gaps this closed (a platform-org member with no admin row, and one with an active row but `admin_type == client`).

This matters concretely in the demotion case: by the time `GET /admin/context`'s handler runs, `get_admin_db` has already executed the downgrade sync (see below) for this request, so the table-derived version correctly flips to `false` immediately, whereas a JWT-derived version would have stayed stale (`true`) until the caller's next token refresh.

### `admin_type` enum values: kept as `platform`/`client` (not renamed)
Considered renaming `"client"` → `"org_admin"` for clarity; decided against it since it's an internal DB value never returned to the API as-is, and the migration hadn't shipped anywhere yet so the churn wasn't worth it.

---

## A real bug found and fixed during implementation

**The transaction-scoping bug in `get_admin_db`:** the plan's own pseudocode for `get_admin_db` ran `_ensure_admin_provisioned` (which includes the D3 downgrade sync — flipping a demoted admin's row to `inactive`) inside the *same* transaction it then `yield`s to the route handler. `require_org_admin`/`require_platform_admin` evaluate *after* that yield point and raise `AuthorizationError` on a demoted caller — but FastAPI throws that exception back into the dependency generator at the yield point, and `session.begin()`'s `__aexit__` rolls back the **entire** transaction on any exception, including the deactivate `UPDATE` that had already executed earlier in the same transaction.

Net effect if left as specified: the D3 downgrade sync would have been a complete no-op in production. The one request where it's supposed to fire (a demoted admin's first post-demotion request) is by construction always the request the guard also rejects — so the row would never actually persist as `inactive`.

**Fix:** `get_admin_db` now opens **two sequential transactions** on the same session — provisioning (JIT create + D3 sync) commits independently first; a second transaction then opens (re-issuing `SET LOCAL`, consistent with the existing "PgBouncer may hand a different backend connection per transaction" discipline already documented for `get_db`) before yielding to the handler. This doesn't change `get_admin_db`'s external contract, RLS behavior, or any endpoint's request/response shape — only *when* the provisioning write durably commits.

Verified end-to-end (not just via the app-level test): seeded an active admin row, presented a demoted token, hit a guarded endpoint, confirmed via a genuinely separate DB connection (`owner_conn`, not the same transaction) that the row was durably `inactive` in the database afterward.

---

## Session 2 (2026-07-24) additions

Prompted by manual end-to-end testing of the Admin Portal frontend against a live local backend stack, which surfaced the R1 reversal above plus one missing capability. This was a **separate session from Session 1** working on the same repo — when Session 1's own implementer/tester agents were checked against `git status` directly (see [[verify-subagent-reports-against-git-status]] in memory), `app/api/admin/platform_members.py` was found already present without having been requested in any Session-1 brief. It initially looked like undisclosed subagent scope creep; it was in fact Session 2's work, landed on the same working tree. Recorded here now with its actual rationale.

### New endpoint: `GET /api/admin/organizations/{clerk_org_id}/members`

Previously, `GET /admin/members` only let an **org admin** see their own org's members (RLS-scoped to the caller's own org). There was no way for a **platform admin** to see members of an arbitrary client org.

- Platform-admin-guarded (`require_platform_admin`), target org taken from the path — same cross-tenant pattern as `platform_invitations.py`.
- Reads **live from Clerk's membership API** (`GET /organizations/{org_id}/memberships`), not the local RLS-scoped `users` table — the target org's local `users` rows may not exist at all if nobody there has signed into the product yet.
- New service function `list_organization_memberships` in `app/services/admin/clerk_admin.py`. **Response shape was verified against the real Clerk API** (queried directly through the running local container, not guessed from docs). Confirmed shape: top-level `{data: [...], total_count}`, each membership item has `id`, `role` (`"org:admin"` / `"org:member"`), `public_user_data: {user_id, first_name, last_name, identifier (=email), ...}`.
- New router module `app/api/admin/platform_members.py` — kept **separate** from `members.py` deliberately, mirroring why `platform_invitations.py` is separate from `invitations.py`: cross-tenant paths (target org from the path, not the caller's token) stay visibly distinct from the org-admin's own-org path.
- New schema `OrgMemberResponse` in `app/schemas/admin/members.py` — **distinct from `MemberResponse`**: `id` here is a Clerk org-membership id (string, e.g. `orgmem_...`), not a local `users.id` (int). Don't conflate the two — `MemberResponse.id` is still what `DELETE /members/{user_id}` expects.
- Registered in `app/api/admin/__init__.py`.

### Audit logging addition

`admin_organization_created`'s payload gained `creator_membership_removed: bool` (see the R1 reversal above) — `true` when the best-effort `remove_organization_membership` cleanup call succeeded, `false` if it failed. The org-creation request itself still succeeds either way.

### Test coverage (`tests/test_admin_portal.py`, 31 → 34 tests, all passing)

- Extended `test_create_org_success_no_local_insert` to assert `remove_organization_membership` is called with the right org/user id after org creation, and that the audit payload's `creator_membership_removed` is `True`.
- New `test_create_org_membership_removal_failure_does_not_fail_the_request` — asserts a Clerk failure on the removal step still returns 201 and records `creator_membership_removed: False` on the audit row.
- New `test_platform_members_non_platform_admin_denied_no_clerk_call` — guard denies a non-platform-admin caller with zero Clerk calls made.
- New `test_platform_members_list_success_maps_fields` — asserts the Clerk membership response maps correctly to `OrgMemberResponse` (`userId`, `email`, `name` from first+last, `role`).

`pyright` and `ruff` both clean on all touched files.

### Operational gotcha surfaced (not a code change)

`settings.simpero_platform_org_id` (env var `SIMPERO_PLATFORM_ORG_ID`) defaults to `""` and **fails closed** by design — if unset, `isPlatformAdmin` is `false` for literally everyone regardless of their actual Clerk org membership, and `require_platform_admin` denies everyone too. This was unset in the local dev `.env` during Session 2 and caused a confusing "platform admin always lands on the org-admin page" symptom before being traced back to config rather than code. **Worth a startup-time warning log if this is likely to bite again** (e.g. a fresh clone or a new environment) — not implemented yet, noted under Gaps below.

### Known follow-up, not done in Session 2

No backend-side auth changes were needed for the e2e-testing gap discovered on the frontend side (no Clerk sign-in wiring exists in the frontend's Playwright suite) — that's entirely a frontend/test-infrastructure item, not a backend change. Noted only so it isn't mistaken for a backend TODO.

---

## Session 3 (2026-07-24) additions

Prompted by three specific frontend contract requirements from an org-detail page already built in `Simpero_AI_Gov_Web` against these endpoints — without these three changes the frontend's calls 404/403/422. Implemented via the same implementer → tester subagent pipeline as prior sessions, with one real architectural fork surfaced and resolved by the repo owner directly before implementation started (see below), consistent with this feature's established "ask, don't assume" practice.

### 1. `accountManagerEmail` becomes optional on org creation

`CreateOrganizationRequest.account_manager_email` (`app/schemas/admin/organizations.py`) is now `EmailStr | None = None`; `CreateOrgResult.invitation` is now `InvitationResponse | None = None`. `create_org` (`app/api/admin/organizations.py`) skips `create_organization_invitation` entirely when the email is omitted — `seed_invitation_id` in the audit payload is `None` in that case, and the response's `invitation` field is `null`. The R1 membership-removal step (`remove_organization_membership` + `creator_membership_removed`, see Session 2 above) is unaffected — it still runs unconditionally regardless of whether a seed invitation was created.

### 2. Platform admin can invite an org **admin**, not just a member, into an arbitrary client org

`CreateInvitationRequest.role` (`app/schemas/admin/invitations.py`) widened from `Literal["member"]` to `Literal["member", "admin"] = "member"` — **this schema is shared** between the org-admin's own-org endpoint (`app/api/admin/invitations.py`) and the platform-admin cross-tenant endpoint (`app/api/admin/platform_invitations.py`), and the two now enforce different things at the endpoint layer:

- `app/api/admin/invitations.py`'s `create_invitation` (own-org, `require_org_admin`) — **guard left untouched**, still hard-rejects `role: "admin"` with 403. Only a platform admin may create admin invitations; an org admin inviting into their own org may still only invite members. Regression-tested explicitly.
- `app/api/admin/platform_invitations.py`'s `invite_into_org` (cross-tenant, `require_platform_admin`, renamed from `invite_member_into_org` since it now handles both roles) — the `role != "member"` 403 guard was removed. Two lookup dicts map the accepted role to Clerk's role string and the correct sign-up redirect: `member → org:member, /sign-up`; `admin → org:admin, /admin/sign-up` — the admin-role redirect mirrors the exact precedent `create_org`'s own seed invitation already used (D1). The audit payload's `"role"` field now reflects whichever role was actually invited, instead of a hardcoded `"member"`.

### 3. New endpoint: `DELETE /admin/organizations/{clerk_org_id}` — Clerk-only, DB cascade deliberately deferred

Platform admins can now delete a client org outright. New `delete_organization(org_id)` in `app/services/admin/clerk_admin.py` (bodyless `DELETE /organizations/{org_id}`, same idiom as `remove_organization_membership`); new router `app/api/admin/platform_organization_delete.py`, guarded by `require_platform_admin`, `clerk_org_id` from the path only.

- **Guard ordering:** the `clerk_org_id == settings.simpero_platform_org_id → 403` check (same check `platform_invitations.py` already does) runs as the literal first statement in the handler, before any Clerk API call — verified by direct code reading, not just a passing test.
- **Empirically verified real Clerk behavior** (queried against the live Clerk Backend API from inside the running dev container, using the real secret key without ever logging/printing it): `DELETE /organizations/{id}` **cascades cleanly on Clerk's side** — it succeeds (`200 {"deleted": true}`) even when the org still has active memberships or pending invitations, no pre-removal/pre-revocation step needed. Tested against two disposable orgs (one with a pending invitation only, one with an actual active `org:admin` member).
- The org is fetched via `fetch_clerk_organization` before deletion, purely to capture its `name` for the audit row; if that fetch 404s, the 404 propagates and nothing is deleted.
- Audited as `admin_organization_deleted`, payload `{clerk_org_id, name}`. Return shape matches `revoke_invitation`'s existing convention: `response_model=SuccessResponse`, `SuccessResponse(success=True)`, default 200 (not a bare 204 — nothing else in this router surface uses 204).

**A real, deliberate architectural gap — not a bug:** this endpoint does **not** touch any local DB rows (`organisation`, `users`, `clerk_admin_users`, `funds`) belonging to the deleted org. `get_admin_db`'s RLS clamp (`org_isolation` policy: `USING (clerk_org_id = current_setting('app.org_id', true))`, identical shape on all three tenant tables) restricts a platform admin's DB session to their **own** org — a platform admin's session cannot see, let alone delete, another org's rows under RLS as currently designed. Making this work would mean pointing `SET LOCAL app.org_id` at an arbitrary target org from within a guarded route handler — a real RLS-crossing precedent nothing in this codebase does today, even in the already-cross-tenant `platform_invitations.py`/`platform_members.py` (both of those only ever call Clerk's API cross-tenant; they never write to another org's DB rows). Given the two options — build that RLS-crossing mechanism now, or ship Clerk-only deletion and leave local rows as orphans — the repo owner explicitly chose to defer it. There's also no FK `ON DELETE CASCADE` from `funds`/`users`/`clerk_admin_users` to `organisation.id`, so even a same-session delete of the `organisation` row would need children deleted first in the right order; this was never attempted. **Net effect:** after this endpoint runs, `organisation`/`clerk_admin_users`/`users` rows for the deleted org (if any were ever JIT-provisioned — e.g. the account manager had signed in at least once) remain in Postgres, referencing a Clerk org that no longer exists. See "Out of scope" below.

### Session 3 verification

- `uv run pytest`: 90 passed (full repo-wide suite), run independently by both the implementer and, separately, the tester subagent (which fixed its own environment setup rather than reusing the implementer's, and re-ran the full suite from scratch). Also independently spot-checked by re-reading the diffs directly against both agents' reports.
- `uv run pyright`: 0 errors — confirmed independently three times (implementer, tester, and directly).
- `uv run ruff check`: clean — same triple confirmation.
- New tests added to `tests/test_admin_portal.py`: org creation with no seed email (asserts `invitation: null`, no Clerk invitation call, audit reflects it); platform invite with `role: "admin"` (asserts `org:admin` + `/admin/sign-up`) and `role: "member"` (regression, still `org:member` + `/sign-up`); the org-admin's own-org endpoint still 403s on `role: "admin"` (regression, verified by reading the test body and the guarded route it hits, not just its pass/fail status); delete-org success + audit, platform-org-self-delete-denied, nonexistent-org 404, non-platform-admin denied.
- **One weak test found and fixed by the tester subagent:** `test_delete_org_platform_org_denied_no_clerk_call` was named for and asserted "no Clerk call happened" (`calls == []`) but never actually monkeypatched `fetch_clerk_organization`/`delete_organization` to populate `calls` in the first place — the assertion was vacuously true regardless of whether the guard fired correctly. Every sibling `*_denied_no_clerk_call` test in the file uses a `_record_and_fail(calls)` monkeypatch so a leaked call actually raises; this test now does too. Re-verified full suite + pyright/ruff clean after the fix.

---

## Session 4 (2026-07-24/25) — member/admin role-change feature, plus two live bugs

Prompted by a new product requirement: change a member's role between `"member"` and `"admin"` directly from the Members list, for both an org admin managing their own team and a platform admin managing an arbitrary client org's team. Given the size and that it broke a documented invariant, this went through the architect subagent first (design plan), then implementer, then tester — the same pipeline as prior sessions, with the architect's plan reviewed and one gap in it (see below) resolved by the repo owner directly before implementation.

### The two real design problems the architect found (not guessable from the spec alone)

**1. A JWT-staleness race against the existing D3 downgrade sync.** Promoting someone updates their Clerk org membership immediately, but their already-issued session JWT won't reflect `org:admin` until it next refreshes client-side (confirmed: `app/core/security.py::decode_clerk_jwt` does no live re-verification against Clerk, it only checks the signature on whatever bearer token the client presents — no refresh in the loop). If the target hits any admin-guarded endpoint with a stale token in that window, the existing D3 downgrade-only sync (`_ensure_admin_provisioned`) would see `status == "active"` (just reactivated) and the stale JWT's `org_role != "admin"`, and immediately deactivate the row the promotion just activated — silently, and (per the original invariant) *permanently*, since inactive rows were never auto-reactivated.

**Fix:** a new nullable `updated_at` column on `ClerkAdminUser` (`onupdate=utc_now` at the column level) plus `settings.admin_role_sync_grace_seconds` (default 120, unverified against Clerk's actual token-refresh cadence — flagged, not confirmed, see Gaps below). D3's condition became: a row touched by an explicit in-app reactivate within the last `grace` seconds is exempted from deactivation once. A genuine out-of-band Clerk-dashboard demotion hits a row whose `updated_at` predates this app's involvement (or is `NULL`), so the grace check fails immediately and D3 still deactivates same-request — no regression for that case.

**2. An RLS wall the cross-org endpoint's spec didn't account for.** `get_admin_db`'s session stays clamped to the platform admin's own org (`app.org_id`) for the whole request. The cross-org PATCH needs to write to the TARGET org's `clerk_admin_users`/`users` rows — under the `org_isolation` RLS policy, a plain query against those rows returns/affects nothing while scoped to the wrong org (exactly why the existing `GET .../members` never touched the local DB at all). The architect's first design (`scoped_txn`, opening a fresh `session.begin()` per scope) turned out to be structurally impossible: `get_admin_db` already holds ONE open transaction for the entire yielded request (FastAPI's dependency-with-yield mechanics run the route handler strictly inside that block) — a nested `session.begin()` on the same session raises `InvalidRequestError` every time, confirmed empirically by the implementer before reporting it rather than picking a fix blind.

**Fix (simpler than the original plan, not a compromise):** `_set_org_scope(session, clerk_org_id)` — just re-issues `SELECT set_config('app.org_id', :tid, true)` on the already-open session, no new transaction. `SET LOCAL` persists for the remainder of the current transaction and can be re-issued as many times as needed; RLS reads it fresh per statement. All writes made this way (target-org writes, then the audit write back in the caller's own org) end up sharing get_admin_db's one transaction — which is actually *more* consistent with the rest of this codebase (e.g. `create_org`'s Clerk call + its audit write already accept "one local transaction, no special-casing" as the norm) than the originally-planned cross-transaction independence would have been.

**A third bug, found by the implementer mid-build, not designed for up front:** setting `local_user.role = new_role` (an ORM attribute set) needs an explicit `await db.flush()` before `_set_org_scope` re-points RLS to the platform org for the audit write — otherwise SQLAlchemy's autoflush defers the UPDATE until the next query (the audit INSERT), by which point it runs under the wrong org's RLS and silently affects 0 rows. The tester verified this empirically by temporarily removing the `flush()` and confirming the relevant test actually failed without it (not just trusting the fix by inspection) — this became the standard verification bar for every subsequent RLS-rescoping test in Sessions 4–5.

### What shipped

- `UpdateMemberRoleRequest(CamelModel)` — `role: Literal["member", "admin"]` (`app/schemas/admin/members.py`), shared by both new PATCH endpoints.
- `update_organization_membership(org_id, member_user_id, role)` in `clerk_admin.py` — `PATCH /organizations/{org_id}/memberships/{user_id}`, `{"role": ...}` — verified live against real Clerk from inside the running dev container, matches the plan's guessed shape exactly.
- `AdminUserRepo.reactivate_or_create` (own-org promote, `ON CONFLICT DO UPDATE`) and `reactivate_if_exists` (cross-org promote, `UPDATE`-only — never inserts, since an insert needs a valid `org_id` FK to a local `Organisation` row for the target org that may not exist; best-effort skip mirrors the same reasoning already used for the `Users.role` write).
- Own-org `PATCH /admin/members/{user_id}` (`members.py`) and cross-org `PATCH /admin/organizations/{clerk_org_id}/members/{clerk_user_id}` (`platform_members.py`, new endpoint in an existing file) — guard order: self-change → last-admin (demote only) → no-op check → Clerk call → local writes → audit.
- **Cross-org self-guard gap, found by the tester, fixed:** the cross-org endpoint's original docstring claimed a self-guard was unnecessary ("platform admins hold no client-org membership, so the caller can never be the target") — true only because R1's post-creation membership removal is *best-effort* and can fail. Added an explicit `clerk_user_id == claims["user_id"]` guard anyway, defense-in-depth, same shape as every other "should be unreachable but cheap to guard anyway" check already in this codebase.

### Two live bugs found and fixed after this feature shipped, same session

**Bug A — `remove_member` never synced `clerk_admin_users`, and `deactivate()` never set `updated_at`.** Once role-change let a member hold both an active `users` row and an active `clerk_admin_users` row simultaneously, `DELETE /admin/members/{user_id}` (which predates this feature and only ever touched `users`) could delete the product row while leaving a dangling *active* admin identity behind — real `/admin` portal access with no corresponding product user. Fixed: `remove_member` now looks up the target's `clerk_admin_users` row and deactivates it too if active; the previously-inert R3 last-admin count check (see the original R3 section above) became load-bearing here for the first time, for real, since a person deleted via this endpoint could now legitimately be an org's last admin. Separately, `AdminUserRepo.deactivate()`'s bulk `update()` statement was found to never populate `updated_at` — the column's `onupdate=utc_now` only fires on ORM-flush updates, not this Core-style bulk statement — silently breaking the D3 grace window's own dependency on that timestamp being accurate. Fixed by setting it explicitly in the same `.values()` call. Both fixed directly (small, precisely scoped, no subagent dispatch needed), with new tests for each.

**Bug B — cross-org platform invitations were 403ing on every call in the live dev app.** Surfaced by the repo owner from actual container logs, not a test. Root cause, traced via the DB query logs plus one clarifying round-trip to check the actual response body: `POST /admin/organizations/{clerk_org_id}/invitations` was passing `inviter_user_id=claims["user_id"]` to `create_organization_invitation` — but Clerk's API requires the inviter to be an actual member of `org_id`, and platform admins are deliberately never members of pre-existing client orgs (R1). Clerk correctly rejected every call with `"not a member"`. The other two `inviter_user_id` call sites (`invitations.py`'s own-org invite, and `organizations.py`'s org-creation seed invite) are legitimate — in both, the caller genuinely is a member of the target org at the moment of the call. Fixed by dropping the parameter from the one bad call site (it's optional in the service function's signature). Live-verified fixed via the dev container's hot reload (no restart needed, volume-mounted source + `uvicorn --reload`).

### Session 4 test coverage and verification

Full suite grew 90 → 105 (role-change feature) → 107 (Bug A fix) → 107 unchanged in shape (Bug B, service-layer only, one assertion added to an existing test). `pyright`/`ruff` clean throughout, confirmed independently by implementer, tester, and directly by the repo owner at each step — not just trusted from subagent reports. The tester's independent pass found and fixed two vacuous test assertions (a `calls == []` check that never actually monkeypatched anything to populate `calls`, and a last-admin-guard test where the platform org's and target org's admin counts happened to coincide, masking a possible scoping bug) — both are exactly the class of "test passes for the wrong reason" bug this feature's whole pipeline is designed to catch before it ships.

---

## Session 5 (2026-07-25) — status lifecycle: soft-delete, cross-org removal, and closing both reactivation gaps

A consolidated follow-up prompt explicitly superseded several earlier draft asks about deleting members/deactivating admin rows — this section reflects what actually shipped, not the interim drafts. Delivered in two implementer/tester rounds because a real product-decision reversal happened mid-stream (see below).

### Round 1: soft-delete + new cross-org DELETE endpoint

- **Migration + model**: `users` gained `status` (`String(50)`, default `"active"`) and `deactivated_at` (nullable `DateTime`) — no RLS/policy change needed, the existing `org_isolation` policy on `users` already covers new columns.
- Own-org `DELETE /admin/members/{user_id}` switched from a hard `delete(Users)` to a soft-delete (`target.status = "inactive"`, `target.deactivated_at = utc_now()` on the already-fetched ORM object) — everything else in that handler (guards, Clerk revocation, `clerk_admin_users` deactivation from Session 4's Bug A fix) was untouched.
- New cross-org `DELETE /admin/organizations/{clerk_org_id}/members/{clerk_user_id}` (`platform_members.py`) — built by directly mirroring the cross-org PATCH's proven `_set_org_scope` pattern rather than re-deriving it: platform-org-target guard → self-guard → fetch current Clerk role (to decide if a last-admin check applies) → last-admin guard (RLS re-scoped to the target org) → unconditional Clerk membership revocation (hard-fails the request on error, not best-effort — same as the own-org endpoint) → `clerk_admin_users` deactivation if the target was an admin → best-effort local soft-delete (skip silently if no local row) → audit back in the caller's own org.
- Audit payloads extended (informational fields, not behavior changes): `admin_member_removed` gained `clerk_membership_revoked`/`admin_role_deactivated`; `admin_member_role_changed` gained `clerk_membership_role_updated`/`users_role_updated`.
- **Product-side fix, a deliberate, narrow, confirmed exception to the "admin and product code never share logic" rule** (added to `CLAUDE.md` this same session): `app/core/dependencies.py::_ensure_user_provisioned` (JIT provisioning for the *product* API, nothing to do with `/api/admin`) previously returned early for any existing `users` row regardless of status — a previously-removed, later-re-invited member logging back in would stay stuck `status="inactive"` forever. Fixed with a new `UserRepo.reactivate()` (only sets `status`/`deactivated_at`, doesn't touch role/name/email JIT logic, doesn't change `upsert()`'s `ON CONFLICT DO NOTHING` semantics). Framed as "keeping one shared table's lifecycle consistent across its two legitimate writers," not admin logic reaching into product code — the repo owner confirmed this framing directly rather than it being assumed.
- Also fixed in this round: `test_platform_invite_success_audited_in_simpero_trail`'s audit-row query wasn't scoped by `org_id`, so it was intermittently picking up a real leftover row from manual testing against the live dev app (a different, real `organisation` row than the test's own freshly-seeded fixture). Test-only fix, scoped the query — not a code bug.

### Round 2: a reversed product decision on visibility, plus the matching admin-side reactivation gap

Between rounds, product reversed an earlier decision: `list_members`/`list_org_members` were originally going to filter out inactive/removed rows (matching how Round 1 was speced) — the final call is the opposite: **inactive members stay visible**, so an admin can see who's been removed and re-invite them from the same screen.

- `list_members` (own-org): dropped its `.where(Users.status == "active")` filter entirely; `MemberResponse` gained a required `status: Literal["active", "inactive"]` field.
- `list_org_members` (cross-org) — this one needed a real design decision, not a mechanical filter change: it reads live from Clerk only, by original design, specifically because a client org's local rows may not exist. A removed member's Clerk membership is fully revoked, so they'd never appear in a live Clerk read at all — "show inactive members here too" is impossible without also reading local data. Confirmed with the repo owner (two real options existed: merge local data in, or accept the cross-org view just can't show removed members) before building the merge: `list_org_members` now takes a `db` dependency for the first time, re-points RLS to the target org via `_set_org_scope`, reads local `users` rows with `status="inactive"`, and **dedups** against the live Clerk list by `clerk_user_id` (a person who was removed then re-invited but hasn't logged back in yet would otherwise appear twice, once from each source — live Clerk always wins). `OrgMemberResponse` gained the same `status` field; a local-only (inactive) entry reuses `clerk_user_id` for both `id` and `user_id` fields since there's no Clerk membership id left to use.
- **The matching gap on the admin side, found while speccing the fix above, not from the original ask:** `_ensure_admin_provisioned`'s D3 sync could deactivate an admin row but never reactivate one — a re-invited, previously-demoted-or-removed admin logging back in with a fresh `org_role: admin` JWT stayed stuck locked out forever, unlike the product-side `_ensure_user_provisioned` fix from Round 1. This is a **deliberate reversal of a previously-tested invariant** ("D3 is revoke-only, never re-activate" was explicit in the original Rev. 3 plan and had its own passing test). Reasoned through and confirmed safe without a fresh architect round (the same trust boundary the original JIT-create branch already relied on — reactivation can only fire when the caller's *current, signature-verified* JWT says `org_role: admin`, which requires a real, auditable Clerk-side action to be true): restructured so the JIT-create and reactivate-on-relogin paths converge on the same `reactivate_or_create` call (which already handles both insert and update via `ON CONFLICT DO UPDATE`), instead of the old create-only `upsert()`. The old test (`test_r6_inactive_row_never_reactivated`) was renamed and its assertion inverted (`test_admin_provisioned_reactivates_inactive_row_on_readmit_jwt`) — with a comment explaining why, and a sibling test (`test_guard_inactive_row_denied`) adjusted to keep testing the genuinely distinct case that doesn't collide with the new path (an inactive row with a JWT that does NOT say admin must still stay inactive and denied).

### Session 5 test coverage and verification

Full suite grew 107 (end of Session 4) → 114 (Round 1: soft-delete + cross-org DELETE + product-side reactivation) → 116 (Round 2: visibility merge + admin-side reactivation). `pyright`/`ruff` clean throughout, confirmed independently at every step by implementer, tester, and directly. The tester's independent passes for this session: (a) empirically re-broke the `db.flush()` fix in the new cross-org DELETE and confirmed the test actually fails without it, same rigor as Session 4's PATCH endpoint; (b) confirmed the D3 downgrade-sync's *existing* behavior (active row + non-admin JWT → still deactivates, grace window still works) is byte-for-byte unchanged by the reactivation restructure, not just that the new behavior works; (c) built a genuine same-`clerk_user_id`-in-both-sources test for the merge/dedup logic rather than trusting a disjoint-sets test that could pass by coincidence; (d) flagged (informational, not fixed) that the dedup test alone can't distinguish correct dedup from a missing `_set_org_scope` call — the separate merge test already covers that regression class, so no gap in practice, just noted for anyone touching this code later.

---

## Verification performed

- `uv run pytest`: 34/34 admin tests passing (81 total repo-wide including pre-existing tests), run twice independently against a real Postgres instance (RLS/`current_setting()` require real Postgres — SQLite doesn't work for this repo's test suite).
- `uv run pyright`: 0 errors, 0 warnings.
- `uv run ruff check` / `ruff format --check`: clean on all new/changed files (some pre-existing, unrelated files elsewhere in the repo have format drift — not touched, not part of this feature).
- `alembic upgrade head` → `downgrade -1` → `upgrade head`: clean round-trip, verified via direct schema inspection at each step (table, `admintype` enum, indexes, RLS policy).
- **RLS isolation**, verified at the raw SQL level (not just through the ORM/app tests): a session clamped to org A cannot `SELECT` an org-B row (returns empty), cannot `UPDATE`/deactivate it (0 rows affected), and is rejected by `WITH CHECK` on `INSERT` with a mismatched `clerk_org_id`.
- **JIT provisioning**: first admin request creates exactly one `clerk_admin_users` row with the correct `admin_type`, and explicitly does **not** create a product `users` row for the same person.
- **Fail-closed guard behavior**: `require_platform_admin` denies every request (before touching the DB) when `simpero_platform_org_id` is empty/unset.
- One test-hygiene bug was found and fixed independently by the tester pass: `test_members_never_returns_other_org_rows` seeded rows without a `try/finally`, so a failed assertion could leak rows into the shared dev database permanently (confirmed this had actually happened from an earlier run). Fixed.

---

## Out of scope / deliberately not built

Carried over from the plan's own "Out of scope" and BACKLOG sections, still accurate:

- **Admin user that is also a product user — RESOLVED as of Session 4, not built the way originally sketched.** The plan's two sketched options (an opt-in dual-provisioning flag, or a `clerk_admin_users.product_user_id` link column) were never built — instead, promoting a member to admin (Session 4's role-change feature) now simply keeps their existing `users` row active alongside a new/reactivated `clerk_admin_users` row. Simpler than either original option, delivered as a side effect of a differently-motivated feature rather than a dedicated migration.
- **Re-activating a demoted-then-re-promoted admin — RESOLVED as of Sessions 4–5, not via the originally-sketched `DELETE/PATCH /admin/admins` endpoint.** Session 4's `PATCH /admin/members/{user_id}` (and its cross-org sibling) already let an org admin explicitly re-promote a demoted person. Session 5 closed the remaining passive-relogin gap: a re-invited admin (removed or demoted, then re-invited via the ordinary invite endpoints) now gets their `clerk_admin_users` row reactivated automatically on next login, not just via an explicit admin action. The D3 sync is no longer strictly one-directional — see Session 5 above for the full "deliberate invariant reversal" writeup and why it's safe.
- **Enforced last-admin-removal protection — RESOLVED as of Session 4.** The scaffolded-but-inert R3 check in `remove_member` became load-bearing for real once a deleted member could also be an org's last admin (Session 4's Bug A). Now enforced on all four remove/demote endpoints (own-org and cross-org, DELETE and PATCH).
- **`/admin/sign-up` frontend route.** Cross-repo dependency on `Simpero_AI_Gov_Web` (frontend team owns it) — the backend already seeds invitations with `redirect_url = <app_base_url>/admin/sign-up`, but that route needs to exist on the frontend before the client-admin seed flow is usable end-to-end. Not verified as existing.
- **Real per-environment config values.** `SIMPERO_PLATFORM_ORG_ID` and `APP_BASE_URL` are `CHANGEME`/localhost placeholders in `.env.example`; each deployed environment needs its own real Clerk platform-org id and frontend URL set before the platform-admin surface will do anything (it fails closed until then).
- **Migration DDL role assumption (R5).** The new migration relies on the existing `ALTER DEFAULT PRIVILEGES FOR ROLE doadmin` bootstrap to auto-grant `dd_app` DML. If the DDL role ever switches to `dd_owner`, a matching default-privileges grant needs to exist first or admin requests will fail closed.
- **Soft-delete for member removal (R2) — RESOLVED as of Session 5.** `users` gained `status`/`deactivated_at`; removal is a soft-delete on both the own-org and cross-org DELETE endpoints. See Session 5 above.
- **Startup-time warning for unset `SIMPERO_PLATFORM_ORG_ID`.** Currently fails closed silently (see Session 2's "Operational gotcha" above) — a confusing symptom to debug blind. A log line at app startup when this setting is empty would save the next person the same trace-back. Still not built.
- **Frontend Playwright/Clerk sign-in wiring.** Surfaced during Session 2's e2e testing as a gap, but it's frontend-owned test infrastructure, not a backend item — noted here only so it isn't mistaken for backend scope.
- **DB cascade on org deletion (Session 3).** `DELETE /admin/organizations/{clerk_org_id}` deletes the Clerk org only; local `organisation`/`clerk_admin_users`/`users` rows for that org (if JIT-provisioned) are left as orphans referencing a now-nonexistent Clerk org. Deliberately deferred — see the "Session 3 additions" section above for the full RLS-clamp reasoning. **Session 4's `_set_org_scope` helper is exactly the "scoped mechanism to run DML against a target org's rows from a platform-admin-guarded handler" this gap was waiting on** — the primitive now exists and is proven in production use (both cross-org PATCH endpoints and the cross-org DELETE), so building the cascade is no longer blocked on inventing that mechanism, only on the remaining open call: explicit child-before-parent delete ordering (none of `funds`/`users`/`clerk_admin_users` → `organisation.id` have `ON DELETE CASCADE`), and whether `funds` (real deal/portfolio data) should ever be cascade-deleted automatically at all versus requiring a separate, explicit confirmation step. Still not built — flagging that the hard part is now solved, not that the feature is done.
- **`admin_role_sync_grace_seconds` default (120s, Session 4) is unverified against Clerk's actual client-side session-token refresh cadence.** Both the architect and the implementer tried and couldn't get a confident number (Clerk's Backend API doesn't expose it; it's client-SDK behavior, not observable without a live browser session). Shipped as a conservative default rather than a confirmed value — if promotions are ever observed to flicker back to inactive in production, this is the first place to look.

## Not yet committed

As of this document (including Sessions 4 and 5), none of this work has been committed to git — it exists only in the working tree. Review `git status`/`git diff` before committing; `pyproject.toml`/`uv.lock` changes (the new `email-validator` dependency) and every new file across all five sessions (`app/api/admin/platform_organization_delete.py`, the two Session 4/5 migrations, `app/core/admin_dependencies.py`, etc.) are included in the diff and should be committed together. `CLAUDE.md` also gained a new "Admin portal — strictly separate from the product portal" section this arc, documenting the boundary Session 5's `UserRepo.reactivate()` fix deliberately, narrowly crossed — worth committing alongside, not separately, since it explains why that one exception exists.
