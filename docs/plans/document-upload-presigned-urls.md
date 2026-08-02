# Document Upload — Presigned URLs, Registry, Guards (SIM-220 / SIM-216 / SIM-218) — Backend Implementation Plan

**Repo:** `/Users/vanshkhanna/Documents/Simpero/Simpero_AI_Gov_Alpha`
**Status:** For implementer subagent. Every claim in `docs/plans/document-upload-spec.md` verified against actual code; discrepancies called out in-line. Standalone, phased, precise.
**Input:** `docs/plans/document-upload-spec.md` (Vansh's spec, written after discussion with the ticket owner — treated as a set of claims to verify, not fact).

---

## What this covers

One feature, three Linear tickets, one request flow, one table:

- **SIM-220** — presigned-URL upload + client-side fingerprinting
- **SIM-216** — `data_source` registry
- **SIM-218** — upload guards (type/size, dedupe, scanned-doc flag)

Flow: `POST /uploads/presigned-url` (control plane, sync) → client `PUT`s directly to Spaces (data plane, never touches this app) → `POST /uploads/{upload_id}/complete` (control plane, creates the row + enqueues async verification).

---

## Verified findings (file:line)

**RLS / tenant convention (spec claim: confirmed)**
- `app/models/chunk.py:38-40`, `app/models/claim.py:89-91`, `app/models/deal.py:29-31` — all three use `org_id: Mapped[int] = mapped_column(Integer, ForeignKey(Organisation.id), ...)`. `organisation.id` is a serial `Integer` (`app/models/organisation.py:23`); `organisation.clerk_org_id` is the Clerk string, unique (`organisation.py:24`).
- `alembic/versions/aace95a1c412_rls_policies.py:34-41`, `6c8bc5907f94_chunks_table.py:109-116`, `e960a3366cf7_corroboration_events.py:78-85` — every tenant table with an Integer `org_id` FK gets the identical policy shape: `USING (org_id IN (SELECT id FROM organisation WHERE clerk_org_id = current_setting('app.org_id', true)))`. **This is the exact policy `data_source` gets.**
- `app/core/dependencies.py:127-134` — confirms the GUC is `app.org_id`, set via `SELECT set_config('app.org_id', :tid, true)` bound to `claims["tenant_id"]` (the Clerk org id string), as the transaction's first statement. Spec's "Integer `org_id`, not Clerk string" claim is **correct** and matches every existing table.

**Forward-reference claims (spec claim: confirmed, both)**
- `app/models/chunk.py:26-28`: *"No FK on document_id: the documents/data_sources table doesn't exist yet, same forward-reference situation as claims.data_source_id/chunk_id."* Verified — `document_id` is a bare nullable UUID column, no `ForeignKey(...)`. **Note:** this is a verbatim quote of the current source comment, predating the `data_source` naming decision — it still says "data_source." Phase 7 (below) should update this comment's wording to match the real table name when it adds the FK, not just the FK itself.
- `app/models/claim.py:93-103`: `deal_id`, `session_id`, `data_source_id`, `chunk_id` are all bare nullable UUIDs, same comment verbatim. Confirmed. `data_source_id`'s forward-reference block is resolved once `data_source` exists — see Phase 7 for the FK it gets (name unchanged, per `contracts/claims.schema.json`).
- **What the spec didn't check:** `deals` (migration `2f9ca0724bb9`) already exists and precedes `claims`'/`chunks`' own migrations in the chain in the case of chunks (`chunks` is `6c8bc5907f94`, well after `2f9ca0724bb9`) — so unlike `data_source_id`, a `deal_id` FK is **not** blocked by a forward-reference today. `data_source.deal_id` should be a real `ForeignKey(Deal.id)`, not a bare UUID — see schema below. **`claims.deal_id` itself was re-examined and is now also in scope (see Phase 7):** `claims` predates `deals` in the chain (created at `60a151dd80b0`, before `deals` at `2f9ca0724bb9`), which is why it's still a bare UUID — but that's a historical ordering issue, not a permanent one, since `deals` exists well before the current head. Addable now, pending a data-integrity check described in Phase 7.

**Config / Spaces (spec claim: confirmed)**
- `app/core/config.py:6-30` — no `spaces_*` fields of any kind. Only `simpero_platform_org_id`/`app_base_url` (admin-portal additions) exist beyond the DB/Clerk/Valkey basics.
- `.env.example:44-58` — `PARSER_SPACES_*` vars are explicitly scoped: *"Parser service (services/parser) ... PARSER_ prefix; read by ParserSettings, not the app."* Bucket `simpero-cim-xlsx-upload`. Confirmed: this app has zero Spaces config of its own today.
- **One thing the spec understated:** `pyproject.toml:16,21-25` — `boto3>=1.34.0` is **already a dependency**, kept specifically for this: *"boto3 stays: kept for future Spaces access from this app (e.g. an upload path), not because anything here uses it yet."* No new dependency needed for presigned-URL generation.

**Job queue / registration pattern (spec claim: confirmed, plus a hazard)**
- `app/jobs/tasks/__init__.py:1-3` — registration is a flat `functions = [example_task]` list; `app/jobs/tasks/example.py:4` shows the signature shape: `async def fn(ctx: Context, *, kwarg=...) -> T`.
- `app/jobs/queue.py:10-14` — `get_queue()` is a process-wide singleton, `Queue.from_url(settings.valkey_url, name="simpero")`, lazy (no connection at import time).
- **Hazard confirmed, not in the spec:** `app/jobs/parse_client.py:1-38` already defines a **second, separate** SAQ `Queue` instance (`get_parse_queue()`, `name="parse"`) targeting Simpero_Gov_AI_Services' worker on the *same* Valkey instance — deliberately distinct from `get_queue()`'s `"simpero"` queue, because `"simpero"`'s `functions` list only knows this app's own tasks. **The new ingest job registers on `get_queue()` ("simpero"), executed by *this app's own* SAQ worker (`docker-compose.yml`'s `worker` service) — it must never be enqueued on `get_parse_queue()`.** Getting this backwards silently drops the job with no error on either side (documented failure mode in `CLAUDE.md`).

**Router pattern (`app/api/deals.py` as template — confirmed usable as-is)**
- `deals.py:34-38` — `_actor(db, claims)` resolves `(org_id, actor_id, actor_email)` via `UserRepo(db).get_by_clerk_id`, asserting non-None because `get_db` JIT-provisions it. Same pattern reused for the new router's audit writes.
- `deals.py:73-76,128-138` — every route takes `db: AsyncSession = Depends(get_db)`; RLS scopes queries with no manual `WHERE org_id=`. 404s fall out of RLS returning no row, never a manual ownership check.

**Audit-log immutability pattern to copy exactly (spec's proposed pattern — see resolution below, it is NOT what `data_source` should get verbatim)**
- `alembic/versions/7175bc85ffb0_human_audit_log.py:62-87` — `ENABLE ROW LEVEL SECURITY` in the same migration as `CREATE TABLE`, then `FORCE ROW LEVEL SECURITY` (defeats table-owner RLS bypass), then `REVOKE UPDATE, DELETE ON human_audit_log FROM dd_app` — relying on `bootstrap_dd_app_privliges.py`'s `ALTER DEFAULT PRIVILEGES FOR ROLE doadmin ... GRANT ... TO dd_app` having already granted full DML, then narrowing it back down. `e960a3366cf7_corroboration_events.py` repeats the identical pattern verbatim.
- **This is a true audit trail: every row is one immutable fact, forever.** `data_source` is not that — see below.

**Role naming note:** this repo's actual DDL role is `doadmin` (not `dd_owner` — confirmed via `bootstrap_dd_app_privliges.py:16` and `CLAUDE.md`). This plan uses `doadmin`/`dd_app` throughout to match the codebase, not the generic `dd_owner` naming.

---

## The append-only vs. lifecycle tension — resolution

The spec is right that this is the crux and right to refuse a hand-wave. Here is the concrete resolution, and why the alternatives it floated are worse.

**Diagnosis:** `data_source` is not actually an audit-log-shaped table. `human_audit_log`/`corroboration_events` are permanent factual records of an event that already finished happening — nothing about them ever needs to change. `data_source` is a **resource with a lifecycle** — much closer in nature to `deals` (which has `status` + `updated_at` and no revoke at all) than to `human_audit_log`. Copying the audit-log's blanket `REVOKE UPDATE` verbatim is a category error: it would make the one legitimate state transition (the ingest job moving `pending` → terminal status) impossible for the only runtime role that exists (`dd_app`), and none of the fix-it-in-app-code options are acceptable per `CLAUDE.md`.

**Rejected alternatives (from the spec's own candidate list):**
- *"The ingest job runs as a role that still has UPDATE"* — rejected outright. This repo has exactly two DB roles by design (`doadmin` for DDL, `dd_app` for runtime DML); the SAQ worker is part of "the app" at runtime, not a migration tool. Giving it a third credential/role breaks the two-role model this whole codebase is built on, for no benefit a narrower grant doesn't already give.
- *"Split mutable status onto a second table"* — rejected as unnecessary complexity. It adds a join to every read of a data source's state, a second row-lifecycle to reason about, and gives no guarantee that column-level privileges don't already provide more simply.

**Resolution: table-level immutability for identity/history, column-level exception for the two lifecycle fields, plus a DB trigger that makes the one legitimate transition truly one-way — even against the table owner.**

In the same migration that creates the table:

```sql
-- 1. Standard mutable-tenant-table RLS (like deals/chunks/claims), PLUS FORCE
--    (decided by Vansh: required, not just recommended):
ALTER TABLE data_source ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON data_source
    FOR ALL TO dd_app
    USING (org_id IN (SELECT id FROM organisation WHERE clerk_org_id = current_setting('app.org_id', true)));
ALTER TABLE data_source FORCE ROW LEVEL SECURITY;
-- Rationale differs from human_audit_log's (immutability) — this is about the row
-- containing sensitive deal-document filenames/storage keys; the same
-- table-owner-bypass gap that mattered for human_audit_log/corroboration_events
-- (cross-org visibility even to doadmin) applies here too.

-- 2. Table-level lockdown, same idiom as human_audit_log:
REVOKE UPDATE, DELETE ON data_source FROM dd_app;

-- 3. NARROW exception: only the three columns the ingest job legitimately owns.
--    Everything else (org_id, deal_id, storage_key, filename, declared_sha256,
--    created_at, id) can never be UPDATEd by dd_app, full stop — that IS
--    SIM-216's "append-only" guarantee, just scoped to the columns that
--    actually describe an immutable historical fact. status_updated_at added
--    alongside status/fingerprint (decided by Vansh) — it changes in the same
--    UPDATE as status, so it needs the same exception, not a separate one.
GRANT UPDATE (status, fingerprint, status_updated_at) ON data_source TO dd_app;

-- 4. The part that makes it airtight: a BEFORE UPDATE trigger enforcing the
--    transition is truly one-way. Triggers fire for EVERY role, including the
--    table owner (doadmin) — unlike GRANT/REVOKE, which the owner bypasses by
--    virtue of owning the table. This closes the one gap column-grant alone
--    leaves open: nothing (no future migration, no ad-hoc doadmin fix-up
--    query) can ever re-verify, un-verify, or flip status a second time.
CREATE FUNCTION data_source_enforce_one_way_status() RETURNS trigger AS $$
BEGIN
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'data_source % status is final once left pending (was %)',
            OLD.id, OLD.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_data_source_one_way_status
    BEFORE UPDATE ON data_source
    FOR EACH ROW EXECUTE FUNCTION data_source_enforce_one_way_status();
```

Net effect: `dd_app` (and everyone else) can never touch `id`/`org_id`/`deal_id`/`storage_key`/`filename`/`declared_sha256`/`created_at` after INSERT — genuinely append-only for the historical facts. The ingest job gets exactly one legitimate `UPDATE ... SET status = ..., fingerprint = ... WHERE id = :id AND status = 'pending'` per row, ever, enforced at the database level, not in application code. This is a new pattern in this repo (column-level GRANT + enforcement trigger) — flag it to Vansh as a deliberate, reviewed departure from the human_audit_log idiom, not a reflex reuse of it.

---

## Data model: `data_source`

New ORM model `app/models/data_source.py`.

| Column | Type | Constraints | Rationale |
|---|---|---|---|
| `id` | `UUID` | PK, `server_default=gen_random_uuid()` | Same idiom as `deals`/`claims`/`chunks`. |
| `org_id` | `Integer` FK→`organisation.id` | NOT NULL, index | Standard tenant column; RLS joins through `clerk_org_id` like every other Integer-`org_id` table. |
| `deal_id` | `UUID` FK→`deals.id` | NOT NULL, index | **Upgraded from the spec's bare UUID** — `deals` already exists in the migration chain (unlike when `claims`/`chunks` were authored), so nothing blocks a real FK here. |
| `storage_key` | `Text` | NOT NULL | Spaces object key. Server-derived, never trusted from the client at `/complete` — see request contract. |
| `filename` | `Text` | NOT NULL | Original filename, as declared at presign time. |
| `declared_sha256` | `String(64)` | NOT NULL | Client-computed hash (hex digest, fixed 64 chars), the pre-flight dedupe key. Never treated as proof of the uploaded bytes. |
| `fingerprint` | `String(64)` | nullable | The async job's verified hash. NULL until ingest completes. One of three columns `dd_app` may ever UPDATE. |
| `status` | `String(16)` | NOT NULL, default `'pending'`, `CHECK IN ('pending','verified','quarantined','ocr_needed','mismatch')` | Lifecycle. One-way `pending → terminal`, enforced by the trigger above — never revisited. |
| `status_updated_at` | `DateTime(timezone=True)` | nullable, **no** `server_default` (stays NULL until the ingest job runs) | **Decided by Vansh.** Set once, by the ingest job, in the same `UPDATE` that moves `status` off `pending` (`status_updated_at = now()` alongside `status = ...`) — not touched at INSERT time, since a row that's still `pending` has never had a status *change*, only an initial value. Third column, alongside `status`/`fingerprint`, in the `GRANT UPDATE (...)` exception below. |
| `created_at` | `DateTime(timezone=True)` | `server_default=now()` | Standard idiom. |

Indexes: `org_id`, `deal_id`, `declared_sha256` (dedupe lookups filter on this), `status` (worth adding — the ingest job and any future "needs attention" UI will filter by it).

---

## Migration outline (house style — matches `6c8bc5907f94`/`e960a3366cf7`)

New file `alembic/versions/<rev>_data_source.py`. `down_revision` = current head, **`222c301f378f`** (the merge of the `chunks_table`/`corroboration_events` branches — verified by tracing every `revision`/`down_revision` pair in `alembic/versions/`; nothing supersedes it).

Contents, in order: `create_table` (columns above) → indexes → `ENABLE ROW LEVEL SECURITY` → `org_isolation` policy → `FORCE ROW LEVEL SECURITY` (required — decided) → `REVOKE UPDATE, DELETE ... FROM dd_app` → `GRANT UPDATE (status, fingerprint, status_updated_at) ... TO dd_app` → trigger function + trigger. `downgrade()` reverses in the opposite order (drop trigger + function first, then the grants/revokes, `NO FORCE`, drop policy, disable RLS, drop indexes, drop table) — same shape as `human_audit_log`'s and `corroboration_events`' `downgrade()`.

**Runs as `doadmin`** (`ALEMBIC_DATABASE_URL`), never `dd_app` — standard rule, no exception here.

---

## Storage / Spaces configuration

**Decided by Vansh (overrides this plan's earlier recommendation to use a separate bucket):** reuse the existing `PARSER_SPACES_BUCKET` — same bucket the parser service already uses for its document cache. Accepted trade-off: this app's upload-presign code gets the same credential blast radius as the parser's cache, same shape of trade Vansh already accepted for the shared Terraform-state Spaces buckets ([[deployment-environments-and-shared-state]]) — consistent with an established preference, not a new risk pattern for this project. Org isolation still holds despite the shared bucket/credential: a presigned PUT URL is signed for one exact object key, not a prefix, so scoping comes from the app deriving that key deterministically from the authenticated caller's own org — not from any IAM condition.

**Object key shape (decided by Vansh):**
```
{PARSER_SPACES_BUCKET}/{org_name}-{clerk_org_id}/{deal_id}/{upload_id}-{filename}
```
`org_name` + `clerk_org_id` (both strings) replace this plan's earlier integer-`org_id` proposal for the *storage key* specifically — this is fine and does not conflict with the RLS convention: RLS's `org_id` (Integer, FK to `organisation.id`) governs SQL rows only, S3 keys are a separate namespace with no RLS involvement. `clerk_org_id` was confirmed as the right field via `app/models/organisation.py:24`. `upload_id` is prepended to `filename` (not specified verbatim by Vansh, added here) to avoid a key collision if two uploads in the same deal share a filename — flagging this addition explicitly rather than silently assuming it matches intent. `org_name` needs sanitizing for S3-safe characters (strip/replace anything outside `[A-Za-z0-9._-]`) before use in a key — implementer detail, not a design decision.

**Revised after initial implementation (decided by Vansh): no separate `SPACES_*` env vars at all.** The first pass gave this app its own `spaces_*` `Settings` fields backed by a duplicate `SPACES_*` `.env` block carrying the same values as `PARSER_SPACES_*` — Vansh flagged the duplication and had it removed. `app/core/config.py`'s `spaces_*` fields now read directly from the existing `PARSER_SPACES_*` env vars via `Field(validation_alias="PARSER_SPACES_BUCKET")` etc. — same attribute names (`settings.spaces_bucket`, so `app/services/uploads/spaces.py` needed no changes), just aliased to the var that already exists, no `ParserSettings` import needed since that class doesn't exist in this app (parsing moved to `Simpero_Gov_AI_Services`). No new `.env.example` block at all — the existing `PARSER_SPACES_*` block gained a comment noting it now serves two purposes. **No new bucket-provisioning step** — this removes Phase 0's earlier "manual DO console step" blocker; only confirm the existing access key already has the needed permissions (it should, being scoped to the whole bucket already).

`boto3` is already a dependency (see verified findings) — no new package. A small adapter module, `app/services/uploads/spaces.py`, wraps `boto3.client("s3", endpoint_url=..., ...)` and exposes:
- `build_object_key(org_name: str, clerk_org_id: str, deal_id: UUID, upload_id: UUID, filename: str) -> str` — the deterministic key shape above.
- `presign_put(key: str, ttl_seconds: int) -> str` — `generate_presigned_url("put_object", ...)`.
- `head_object(key: str) -> bool` — existence check, used at `/complete` (see below).
- `stream_and_hash(key: str) -> str` — chunked `get_object` read for the ingest job, never buffering the whole object.

**Decided by Vansh:** max upload size is **10 MB**. Presigned **PUT** stays (POST + policy conditions considered and declined — more frontend/policy-document complexity than this threat model calls for). Three enforcement layers, decided as a set:

1. **Client-side pre-check (frontend, `Simpero_AI_Gov_Web` — not this repo).** Before the app ever calls `/presigned-url`, it checks the selected file's size/type locally (`file.size`, `file.type`) and, on failure, **throws the error directly in the user's face — no network call at all.** Cheap: the client already reads the file locally to compute the SHA-256 dedup hash, so this reuses work already happening. This is the primary UX path for the honest case (wrong file picked by mistake); it is **not** a security boundary — trivially bypassed by anything that talks to the API directly instead of through the web app's JS (curl, a modified page, a non-browser client). This backend plan documents the contract the frontend is expected to honor; implementing it is cross-repo, out of scope here.
2. **Declared-size/type guard at `/presigned-url`** (already in this plan, step 1 below) — server-side, catches anything that skips layer 1, whether by bypass or because a non-web client calls the API directly. Still only a declared-value check (`size` in the request body), not a measurement of real bytes — nothing has been uploaded yet at this point.
3. **Hard ceiling in the ingest job (Phase 5), decided here as a new addition:** since presigned PUT has no way to reject an oversized object at the storage layer itself, the ingest job — the one place that actually reads the real bytes — bails out and marks the row `quarantined` if the object exceeds a hard ceiling while streaming (e.g. stop reading and quarantine once bytes read exceed 10 MB, rather than reading an arbitrarily large object to completion first). This is the actual enforcement backstop; layers 1–2 are UX/fast-fail only. Exact ceiling value (10 MB flat, or a small multiplier for benign overhead) not fixed here — reasonable default is the same 10 MB, no multiplier, since the declared-size guard already establishes 10 MB as the real ceiling.

---

## Request/response contract (resolves an ambiguity the spec leaves implicit)

The spec doesn't say what `/complete` needs in its body to reconstruct state from a stateless `/presigned-url` call. Resolved here, stateless by design (no new cache/session infra):

**`POST /uploads/presigned-url`** — `Depends(get_db)` (RLS-scoped dedupe SELECT). Body: `{deal_id, filename, size, declared_sha256}`.
1. Type/size guard against `filename`/`size` (declared, not measured) → specific 4xx reason on reject.
2. Dedupe SELECT, **scoped to `declared_sha256 OR fingerprint`, not `fingerprint` alone** — see bug fix below.
3. Generate `upload_id = uuid4()` server-side. Derive `storage_key` via `build_object_key(org_name, clerk_org_id, deal_id, upload_id, filename)`.
4. Presign a PUT to `storage_key`, **TTL = 10 minutes (decided by Vansh)**.
5. Response: `{upload_id, presigned_url, storage_key}`. **No DB write in this handler** — matches the spec's framing that the row is created at `/complete`.

**`POST /uploads/{upload_id}/complete`** — `Depends(get_db)`. Body: `{deal_id, filename, declared_sha256}` (the client re-sends exactly what it already holds from its own `/presigned-url` call — no new client-side state).
1. Server **recomputes** `storage_key` from `(org_name, clerk_org_id, deal_id, upload_id, filename)` the same deterministic way — never trusts a client-supplied storage key. This is the same "don't trust client-supplied identifiers, derive them" posture RLS already applies to `org_id`.
2. `head_object(storage_key)` — confirms the PUT actually happened before creating a row. **Gap the spec didn't address:** nothing stops a client from calling `/complete` without ever uploading; without this check, a phantom `pending` row would be created that can never be deleted (`REVOKE DELETE`) and would sit forever failing the ingest job. Missing object → 4xx, no row created.
3. Insert the `data_source` row (`status='pending'`), via a `DataSourceRepo.create()` mirroring `DealRepo.create()`'s `session.add()`-then-flush-on-commit pattern.
4. Enqueue the ingest job on `get_queue()` ("simpero" queue — **not** `parse_client.py`'s "parse" queue, see verified findings) with `{data_source_id, clerk_org_id: claims["tenant_id"], storage_key, declared_sha256}`.
5. **Decided by Vansh: required.** Append a `human_audit_log` row, `event_type="document_upload_completed"`, via `HumanAuditRepo(db).append(...)`, using the same `_actor()` pattern `deals.py` already uses — consistent with how every other meaningful mutation in this codebase is audited. Goes beyond the three tickets' literal text (none of SIM-220/216/218 mention it explicitly) but is now part of this plan's scope, not optional.
6. Response: the created row's id/status.

**Bug found in the spec's own dedupe design, fixed here:** the spec says the presign-time dedupe SELECT checks `data_source.fingerprint`. But `fingerprint` stays NULL until the async job runs — a second upload of the same file *before the first one's ingest job has completed* would find no match and sail through, defeating the whole point of doing dedupe at presign time. The SELECT must check `declared_sha256 = :hash OR fingerprint = :hash`, scoped to the deal. **Decided by Vansh: `mismatch` rows are excluded from the dedupe match** — a prior upload that failed integrity verification doesn't block a fresh, legitimate re-upload of the same file. `find_dedupe_candidate` filters `status != 'mismatch'` in addition to matching `declared_sha256 OR fingerprint`.

---

## Async ingest job — tenant context (extra scrutiny: this runs outside any FastAPI request)

`app/jobs/tasks/ingest_data_source.py`, registered in `app/jobs/tasks/__init__.py`'s `functions` list alongside `example_task`.

**The one non-obvious, load-bearing detail:** this job runs in the SAQ worker process, with no `Depends(get_db)` and no request lifecycle. It must replicate `get_db`'s `SET LOCAL` discipline by hand, or it either bypasses RLS or crashes against it:

```
async def ingest_data_source(ctx, *, data_source_id: str, clerk_org_id: str, storage_key: str, declared_sha256: str) -> None:
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SELECT set_config('app.org_id', :tid, true)"), {"tid": clerk_org_id})
        # ... stream_and_hash(storage_key), compare to declared_sha256, then:
        await DataSourceRepo(session).update_status(data_source_id, status=..., fingerprint=...)  # sets status_updated_at = now() internally
        await HumanAuditRepo(session).append(
            event_type="document_upload_ingest_completed",
            payload={"data_source_id": data_source_id, "status": status, "fingerprint": fingerprint},
            actor_id="Internal System", actor_email="Internal System",  # decided by Vansh — see note below
        )
```

This is why the `/complete` handler must pass `claims["tenant_id"]` (the Clerk org id string), not just the integer `org_id`, as a job kwarg — the GUC `app.org_id` is always compared against `organisation.clerk_org_id`, never the integer PK. Missing this detail is the single most likely way an implementer accidentally either breaks RLS (no `SET LOCAL` at all) or reintroduces a `WHERE org_id=` app-side workaround the codebase's convention forbids.

`DataSourceRepo.update_status()` is the **sole write path** to the mutable columns, mirroring `HumanAuditRepo.append()`'s "sole write path" doc convention — it issues `UPDATE data_source SET status=:s, fingerprint=:f, status_updated_at=now() WHERE id=:id` and nothing else ever calls a bare `session.add`/`update` on this model. `status_updated_at` is set inside the repo method itself (server-side `now()`), not passed in as a caller-supplied value — there is exactly one legitimate transition per row (enforced by the trigger), so there is never a case where the caller needs to supply a different timestamp. The DB-level column grant + trigger (above) enforce the rest; this repo method exists for a single clean call site, not as the actual security boundary.

**Decided by Vansh: the ingest job also writes a `human_audit_log` row, one per run, regardless of outcome** (`verified`/`quarantined`/`mismatch` all get logged — the point is a complete trail of what happened to every upload, not just the good outcomes). **New precedent worth flagging:** every existing `human_audit_log` write in this codebase is request-driven, with a real `actor_id`/`actor_email` resolved via `_actor(db, claims)`. This is the first *system*-initiated write — there is no human actor at the point the SAQ worker runs. **Decided by Vansh:** rather than leaving the nullable `actor_id`/`actor_email` columns NULL, both are set to the literal string `"Internal System"` — this reads correctly in any audit UI/export without that surface needing to special-case a NULL actor (no "renders as blank" risk). Simplification worth naming: `actor_id` on every other row is a real Clerk user id, never free text — `"Internal System"` breaks that implicit invariant for this one write path. Low-risk (nothing in this plan queries/joins `human_audit_log.actor_id` against a user table), but if a future feature ever does treat `actor_id` as a foreign identifier, this row will need to be excluded or handled specially.

Job responsibilities: stream + chunked SHA-256, **bailing out and marking `quarantined` if bytes read exceed the 10 MB ceiling before the stream finishes** (the real enforcement backstop — see Storage/Spaces section); otherwise compare to `declared_sha256` → `verified`/`mismatch`; write the audit row above. **Scanned/image-only detection is explicitly NOT implemented here** — see next section.

---

## Open problem, sized not designed: scanned/image-only detection

**Filed as [SIM-350](https://linear.app/simpero/issue/SIM-350/scannedimage-only-document-detection-needs-a-text-layer-inspection)** — split out of this plan for separate grooming, not part of Phase 8 below until that ticket resolves. Sizing it:

- This app dropped Docling/pypdf in the Simpero_Gov_AI_Services split and cannot inspect a document's text layer locally.
- The only path today is the ingest job making an HTTP call to Simpero_Gov_AI_Services' `POST /parse` — synchronous per that service's current shape (confirmed: `CLAUDE.md`'s "Document parsing: split out" section states that service exposes only a sync `POST /parse`, no queue/worker of its own yet). A sync call from inside this app's own async ingest job ties the job's latency to Docling's per-document parse time — in tension with "async, decoupled."
- `app/jobs/parse_client.py` already exists as scaffolding for an *alternate* design (this app enqueuing onto a `"parse"` Valkey queue for an async worker on the other side) — but per its own docstring and `CLAUDE.md`, **that worker does not exist yet** in Simpero_Gov_AI_Services. It's a candidate to revive if Vansh decides to go the async-worker route instead of a sync `/parse` call, not something to wire up by default.
- **This is explicitly cross-repo, cross-boundary territory** — Simpero_Gov_AI_Services and its parsing pipeline are outside this plan's scope regardless of which shape it takes. Whoever owns that service's roadmap needs to weigh in before this sub-task is scoped further; it may need to be split into its own ticket rather than shipped in this pass, per the spec's own framing.
- Everything else in this plan (`/presigned-url`, `/complete`, dedupe, hash verification) never reads document *content* — only bytes/hashes/filenames — so it does not cross the parsing-pipeline boundary. This open problem is the one place a future addition would, which is exactly why it's being sized here and not casually implemented.

---

## Folder / file layout

```
app/
  models/
    data_source.py            # NEW — DataSource model
  repo/
    DataSourceRepo.py          # NEW — create, get_dedupe_candidates, update_status (sole write path)
  schemas/
    uploads.py                 # NEW — PresignRequest/Response, CompleteRequest/Response (extend CamelModel)
  services/uploads/
    __init__.py
    spaces.py                  # NEW — boto3 adapter: build_object_key, presign_put, head_object, stream_and_hash
  api/
    uploads.py                 # NEW — POST /uploads/presigned-url, POST /uploads/{upload_id}/complete
  jobs/tasks/
    ingest_data_source.py      # NEW — registered in tasks/__init__.py's `functions`
  core/
    config.py                  # + spaces_* settings
alembic/versions/
    <rev>_data_source.py      # NEW — table + RLS + column-grant + one-way trigger (down_revision = 222c301f378f)
.env.example                    # + SPACES_* documented block (values match PARSER_SPACES_*)
app/main.py                     # + one import + one include_router(uploads.router, prefix=API_PREFIX) line
```

Order matters: migration → model → repo → Spaces adapter → schemas → router → job registration. Each phase below should not start until the previous one's acceptance criteria pass.

---

## Phased implementation plan

### Phase 0 — Config
- Add `spaces_*` settings (values mirror `PARSER_SPACES_*` — same bucket, decided by Vansh); update `.env.example`.
- No new bucket/credential provisioning needed — confirm the existing Spaces access key already reaches `PARSER_SPACES_BUCKET` (it should).
**Acceptance:** settings load with empty defaults in dev without crashing; `ruff`/`pyright` clean.

### Phase 1 — Migration + model + repo
- `app/models/data_source.py`; register in `app/models/__init__.py`.
- Migration: table (including `status_updated_at`, nullable, no server default) + indexes + RLS policy + `FORCE` + `REVOKE UPDATE, DELETE` + `GRANT UPDATE (status, fingerprint, status_updated_at)` + one-way trigger. `down_revision = "222c301f378f"`.
- `app/repo/DataSourceRepo.py`: `create`, `get_by_id`, `find_dedupe_candidate(deal_id, hash)` (matches `declared_sha256 OR fingerprint`, excludes `status='mismatch'` — decided), `update_status(id, status, fingerprint)` (sets `status_updated_at = now()` server-side, not a caller-supplied param).
**Acceptance:** `alembic upgrade head`/`downgrade -1` clean. DB-backed RLS check (same shape as the admin-portal plan's Phase 0): org-A `dd_app` session can INSERT/SELECT its own row, cannot see an org-B row seeded via `owner_conn`. New row's `status_updated_at` is NULL on INSERT. **Column-grant check:** an org-A session can `UPDATE ... SET status=..., status_updated_at=now()` but `UPDATE ... SET filename=...` raises a permission error. **Trigger check:** update a row once (`pending`→`verified`, `status_updated_at` set) succeeds; a second update to the same row (any column, any role including via `owner_conn`) raises the trigger's exception.

### Phase 2 — Spaces adapter
- `app/services/uploads/spaces.py`: `build_object_key`, `presign_put`, `head_object`, `stream_and_hash` (chunked, never full-buffer).
**Acceptance:** unit tests against a mocked/local S3-compatible endpoint (or `moto`) for key shape, presign call shape, and chunked hashing correctness (assert it never loads the whole object into memory — verify by construction/mock call pattern, not by measuring memory).

### Phase 3 — `POST /uploads/presigned-url`
- `app/schemas/uploads.py`: `PresignRequest`/`PresignResponse`.
- `app/api/uploads.py`: guard order — type/size reject → dedupe SELECT (fixed condition above) → `uuid4()` → derive key → presign → respond. No DB write.
**Acceptance:** declared `size > 10 MB` or wrong type → specific 4xx, no Spaces call; duplicate `declared_sha256` for the same deal (including one still `pending`, not yet verified) → 409, no presigned URL issued; happy path returns `{upload_id, presigned_url, storage_key}` with `storage_key` matching `{org_name}-{clerk_org_id}/{deal_id}/{upload_id}-{filename}`.

### Phase 4 — `POST /uploads/{upload_id}/complete`
- Recompute `storage_key` server-side (never trust client-supplied); `head_object` check; insert row; enqueue job on `get_queue()`; append a `human_audit_log` row (`event_type="document_upload_completed"`) via `HumanAuditRepo(db).append(...)` — required, not optional.
**Acceptance:** calling `/complete` without a prior successful PUT (object absent) → 4xx, no row created, no audit row; happy path creates one `status='pending'` row, enqueues exactly one job on the `"simpero"` queue (assert via a mocked/inspected queue, not `parse_client.py`'s queue), and writes exactly one `human_audit_log` row; RLS confirmed — the row is invisible to an org-B session via `owner_conn`.

### Phase 5 — Async ingest job
- `app/jobs/tasks/ingest_data_source.py`; register in `tasks/__init__.py`. Replicates `SET LOCAL app.org_id` by hand (no `Depends(get_db)` available in a worker context) using `clerk_org_id` passed as a job kwarg.
**Acceptance:** job run against a seeded `pending` row (`status_updated_at` NULL beforehand): matching hash → `verified` + `fingerprint` set + `status_updated_at` set to the run's timestamp + one `human_audit_log` row (`event_type="document_upload_ingest_completed"`, `actor_id`/`actor_email` both `"Internal System"`); mismatched hash → `mismatch`, `fingerprint` still set to the actual computed value (never left null on a mismatch — the whole point is recording what was actually there), `status_updated_at` still set, and an audit row still written (mismatches get logged too, not just successes). **Oversized-object check:** an object exceeding 10 MB → job stops streaming partway through (does not read the whole object), row → `quarantined`, audit row still written, `fingerprint` left NULL (never computed — the point of bailing early is not paying the cost of hashing an object that's being rejected anyway). Second invocation against the now-terminal row → trigger exception surfaces as a job failure, not a silent no-op (confirm this is the desired behavior — a job that raises on a row already handled should probably be idempotent-safe by checking `status == 'pending'` before attempting the UPDATE at all, not relying on the trigger as the only guard; add that guard in the job itself as a courtesy, not a substitute for the DB-level one) — and must not write a second audit row or move `status_updated_at` on that rejected retry.

### Phase 6 — Wire the router, tests, docs
- `app/main.py`: one import + one `include_router` line.
- Contract tests per endpoint (`ApiTestClient`, `dependency_overrides` for `get_claims`, mocked Spaces adapter).
- Update `.env.example` + README with the new settings and the Spaces bucket provisioning step.
**Acceptance:** `pytest` green with Postgres; `ruff`/`pyright` clean.

### Phase 7 (flag before starting — cross-epic, not blocking) — add the FKs `data_source` unblocks, plus `claims.deal_id`
- Now that `data_source` exists, add `ForeignKey(DataSource.id)` to `chunk.py:document_id` in a follow-up migration (table has no populated rows yet per its own docstring, so this is low-risk).
- **Decided by Vansh:** `claims.data_source_id` also gets `ForeignKey(DataSource.id)` in the same follow-up migration — **FK only, column name stays `data_source_id`, no rename.** `contracts/claims.schema.json:30` names this field `data_source_id` and is cross-repo (also owned/CI-validated by `Simpero_Gov_AI_Services`); `claim.py:12-15`'s own rule ("the contract wins, this file is the bug if they disagree") governs the column's *name and JSON shape*, not whether it carries a DB-level FK, so adding referential integrity here doesn't touch the contract at all — confirmed safe on that basis. A rename was considered and explicitly rejected: the schema already uses `document_id` for a different, narrower field nested inside each format's `location` block (`location.document_id`/`document_name`/`document_version`), and reusing that name at the top level would mean "document_id" means two different things depending on nesting — not worth the collision, and not worth a cross-repo contract edit for a rename alone.
- **Decided by Vansh: `claims.deal_id` also gets `ForeignKey(Deal.id)` in this same migration** — reversing this plan's earlier "out of scope" call. Verified against the actual migration chain (traced every `revision`/`down_revision` in `alembic/versions/`): `claims` was created at `60a151dd80b0` (`claims_spine`), several migrations *before* `deals` at `2f9ca0724bb9` — that ordering, not a permanent limitation, is why `deal_id` was left a bare UUID. `deals` is now upstream of the current head, so the FK is addable, but **only after a data check**, not blindly: query `SELECT count(*) FROM claims WHERE deal_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM deals WHERE deals.id = claims.deal_id)` against the real dev DB before writing the migration. Zero rows (expected — this table is described as having no populated rows yet in this plan's other findings) → plain `ALTER TABLE claims ADD CONSTRAINT ... FOREIGN KEY (deal_id) REFERENCES deals(id)`, no backfill needed. Any orphaned rows found → **stop and surface to Vansh rather than guessing** (silently nulling or deleting existing `deal_id` values would be a data-loss decision, not an implementer's call to make).
- **Cross-epic note:** `chunk.py`/`claim.py` belong to Retrieval/Claims-spine epics. Still, ping whoever owns those tables before touching them. Confirm `contracts/test_claims_contract.py` still passes unchanged (it validates JSON shape, not DB constraints, so it should be unaffected — verify rather than assume).

### Phase 8 (tracked separately) — Scanned/image-only detection
- Tracked as [SIM-350](https://linear.app/simpero/issue/SIM-350/scannedimage-only-document-detection-needs-a-text-layer-inspection), for grooming with the team. Do not implement until that ticket resolves the sync-vs-async-worker call.

---

## Risks / open questions for a human

- **Dedupe-window bug (fixed in this plan, confirm the fix):** the spec's literal text ("SELECT against `data_source.fingerprint`") would miss duplicates uploaded while a prior upload's ingest job hasn't finished. This plan's `find_dedupe_candidate` checks `declared_sha256 OR fingerprint`. Confirm the `mismatch`-exclusion call (should a previously-corrupted upload block a legitimate re-upload of the same file? recommend no, not decided here).
- **Bucket, size limit, and key shape are now decided** (10 MB; `PARSER_SPACES_BUCKET`; `{org_name}-{clerk_org_id}/{deal_id}/{upload_id}-{filename}`). **Presigned TTL is still an unset number** — get a concrete minutes value before Phase 3 ships.
- ~~PUT vs. POST presigning~~ — **decided by Vansh: PUT stays.** Enforcement instead comes from three layers (client pre-check, declared-size guard at `/presigned-url`, hard-ceiling bail-out in the ingest job) — see Storage/Spaces section.
- ~~`FORCE ROW LEVEL SECURITY` on `data_source`~~ — **decided by Vansh: required.**
- **The column-grant + one-way-trigger pattern is new to this codebase.** No existing table uses it. Flag for extra review given the RLS/tenant/role boundary this touches.
- ~~Audit-log row on `/complete`~~ — **decided by Vansh: required.** Not a literal requirement of any of the three tickets, but now locked into Phase 4's acceptance criteria.
- ~~Audit-log row on the ingest job's outcome~~ — **decided by Vansh: also required**, every run regardless of outcome (`verified`/`quarantined`/`mismatch`), with `actor_id`/`actor_email` set to the literal `"Internal System"` (see "Async ingest job" section for why this is a new precedent worth a second look — first non-request-driven `human_audit_log` write in the codebase, and the first row where `actor_id` isn't a real Clerk user id).
- **Scanned/image-only detection (SIM-218 sub-task 3)** is explicitly unresolved and may need its own ticket — see the dedicated section above.

---

## Out of scope / do not touch

- Everything under `contracts/` (`claims.schema.json`) — no field renamed, no shape change. Phase 7 adds a DB-level FK on the already-contract-named `data_source_id` column only; the contract file itself is untouched.
- `app/api/admin/*`, `app/core/admin_dependencies.py` — separate surface, not touched by this plan.
- `human_audit_log`/`corroboration_events`/`audit_log` tables and migrations — mentioned only as the immutability pattern this plan deliberately does **not** copy verbatim (see resolution section for why).
- OCR execution, confidence-capping — founder-descoped 2026-07-20, per the spec; do not build.
- Simpero_Gov_AI_Services itself and any change to `app/jobs/parse_client.py` — cross-repo, flagged as Phase 8's decision point, not implemented here.
- Renaming `claims.data_source_id` — considered and explicitly declined (see Phase 7): would require editing `contracts/claims.schema.json` in two repos and collides in name with the schema's existing, unrelated `location.document_id` field.
- The client-side size/type pre-check (Storage/Spaces section) — lives in `Simpero_AI_Gov_Web`, a separate repo, not this one. This plan documents the expected contract only; the actual frontend implementation is out of scope here.
