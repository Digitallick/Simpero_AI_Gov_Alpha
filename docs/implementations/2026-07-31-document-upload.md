# Document Upload (Presigned URLs, Registry, Guards) — Implementation Summary

**Status:** Implemented (Phases 0–7 complete), not yet committed to git (working tree only as of this writing).
**Plan followed:** `docs/plans/document-upload-presigned-urls.md`, with `docs/plans/document-upload-spec.md` as design-history input (now marked superseded — see that file's header note).
**Sessions:** 2026-07-30 (Linear ticket review, spec authored through conversation with Vansh, architect plan produced) → 2026-07-31 (remaining open decisions closed, implementation via an orchestrator subagent across all 8 phases, live-DB verification of the `claims.deal_id` FK, and a side investigation into an unrelated `claims` schema-drift report) → 2026-08-02 (Session 2 — Spaces config simplification, see below).

This document is the "what actually shipped" record. Treat the plan doc as the design rationale (verified file:line findings, the append-only/lifecycle trigger resolution, the request/response contract) and this doc as what was actually built, what deviated from the plan during implementation, and what's still open. **Where Session 2 reverses an earlier decision, it supersedes the original text around it — read that section, don't trust the "What was built" section's original Config bullet in isolation.**

---

## What this feature is

Combines three Linear tickets into one feature, sharing one request flow and one table:

- **SIM-220** (FS-A-ONBD-13) — presigned-URL upload mechanism + SHA-256 fingerprinting.
- **SIM-216** (FS-A-ONBD-9) — `data_source` registry (append-only, one row per upload).
- **SIM-218** (FS-A-ONBD-11) — upload guards (type/size reject, dedupe via hash, scanned-doc flagging).

Flow: `POST /uploads/presigned-url` (control plane, synchronous — client-side size/type pre-check and SHA-256 hashing happen before this call, in the frontend) → client `PUT`s directly to DigitalOcean Spaces (data plane, never touches the app server) → `POST /uploads/{upload_id}/complete` (creates the `data_source` row, enqueues async verification).

---

## What was built

### Database

- **New table `data_source`** (migration `alembic/versions/d6d2fe8f27ae_data_source.py`): `id`, `org_id` (Integer FK → `organisation.id`), `deal_id` (FK → `deals.id`), `storage_key`, `filename`, `declared_sha256`, `fingerprint` (nullable), `status` (`pending`/`verified`/`quarantined`/`ocr_needed`/`mismatch`), `status_updated_at` (nullable, no server default), `created_at`.
- **Append-only enforcement — a new pattern for this repo, not a copy of `human_audit_log`'s:** `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + standard org-isolation policy, then `REVOKE UPDATE, DELETE ... FROM dd_app`, then `GRANT UPDATE (status, fingerprint, status_updated_at) ... TO dd_app` (a narrow column-level exception, not a blanket revoke — `data_source` is a lifecycle resource, not a pure audit log), then a `BEFORE UPDATE` trigger (`data_source_enforce_one_way_status`) that raises once `status` has already left `pending`. The trigger is what closes the gap column-grant alone leaves open: it fires against every role, including the table owner (`doadmin`), so no future migration or ad-hoc fix-up query can ever re-verify or flip status a second time.
- **Follow-up FK migration** (`alembic/versions/77be2ddc60a0_data_source_fks.py`), gated on `data_source` existing:
  - `chunks.document_id → data_source.id` — no orphan check needed (table has no populated rows).
  - `claims.data_source_id → data_source.id` — **column name unchanged**, deliberately. `contracts/claims.schema.json` names this field `data_source_id` and is a cross-repo contract (also CI-validated by `Simpero_Gov_AI_Services`); a rename was considered and rejected during planning (it would've collided with the schema's existing, unrelated `location.document_id` field). Adding a DB-level FK doesn't touch the contract's name or shape at all.
  - `claims.deal_id → deals.id` — added in a second pass, after the live orphan-check below came back zero. `chunk.py`'s stale comment (still saying "documents/data_sources table doesn't exist yet") was updated to match the real table name while adding its FK.

### New Python modules

| File | Purpose |
|---|---|
| `app/models/data_source.py` | `DataSource` ORM model |
| `app/repo/DataSourceRepo.py` | `create`, `get_by_id`, `find_dedupe_candidate` (matches `declared_sha256 OR fingerprint`, excludes `status='mismatch'`), `update_status` (sole write path to the mutable columns — sets `status_updated_at = now()` server-side) |
| `app/services/uploads/spaces.py` | `boto3`-based Spaces adapter: `build_object_key`, `presign_put`, `head_object`, `stream_and_hash` (chunked, raises `ObjectTooLargeError` past a size ceiling — never buffers the whole object) |
| `app/schemas/uploads.py` | `PresignRequest`/`PresignResponse`, `CompleteRequest`/`CompleteResponse` |
| `app/api/uploads.py` | `POST /uploads/presigned-url`, `POST /uploads/{upload_id}/complete` |
| `app/jobs/tasks/ingest_data_source.py` | Async SAQ job: replicates `SET LOCAL app.org_id` by hand (no `Depends(get_db)` in a worker context), streams + hashes, compares to `declared_sha256` → `verified`/`mismatch`, bails and marks `quarantined` on an oversized object, writes a `human_audit_log` row every run regardless of outcome |
| `tests/test_data_source_rls.py`, `test_uploads_spaces.py`, `test_uploads_api.py`, `test_ingest_data_source.py`, `test_uploads_wiring.py` | New test coverage for the above |

**Modified, not new:**
- `app/models/chunk.py`, `app/models/claim.py` — the two/three new FKs (see Database above).
- `tests/test_chunks_rls.py` — a pre-existing fixture inserted a `Chunk` with a fabricated random `document_id`; the new FK now (correctly) rejects it. Updated to leave `document_id` NULL, since it was irrelevant to what that test actually asserts (content_tsv generation).
- `app/jobs/tasks/__init__.py` — registered `ingest_data_source` in `functions`.
- `app/main.py` — one import + one `include_router(uploads.router, prefix=API_PREFIX)` line.
- `app/models/__init__.py` — registered `DataSource` for Alembic.
- `app/core/config.py`, `.env.example` — `spaces_*` settings (values intended to mirror `PARSER_SPACES_*` — same bucket, reused deliberately, see Decisions below).
- `README.md` — new settings + Spaces bucket note documented.

### API surface (final)

- `POST /uploads/presigned-url` — body `{deal_id, filename, size, declared_sha256}`. Type/size guard (10 MB) → dedupe check (`declared_sha256 OR fingerprint`, excluding `mismatch` rows) → presign a PUT, **TTL = 10 minutes**. No DB write in this handler.
- `POST /uploads/{upload_id}/complete` — body `{deal_id, filename, declared_sha256}`. Recomputes `storage_key` server-side (never trusts a client-supplied key), `head_object` existence check (rejects a phantom "complete" call with no actual upload), inserts the `data_source` row (`status='pending'`), enqueues the ingest job on `get_queue()` ("simpero" queue — never `parse_client.py`'s "parse" queue), writes a `human_audit_log` row (`event_type="document_upload_completed"`).

---

## Decisions made during implementation (amendments/clarifications to the plan)

- **Two stale contradictions in the plan doc's own "Risks" appendix, resolved without reopening them:** the Risks section still said presigned TTL was "unset" and the mismatch-dedupe-exclusion was "not decided," even though both were pinned down unambiguously elsewhere in the same document (10 minutes; exclude `mismatch`). Implemented per the unambiguous decision, not the stale note.
- **File-type whitelist** — never specified anywhere in the plan. Implementer defaulted to `.pdf .doc .docx .xls .xlsx .csv .pptx` and flagged it explicitly as a placeholder rather than guessing silently. **Needs Vansh's confirmation** — easy one-line change if a different list is wanted. This is also the same list the frontend plan (`Simpero_AI_Gov_Web`) needs to match exactly, or the client-side fast-fail check will drift from what the server actually enforces.
- **No custom outbox/retry logic built for the enqueue-before-commit race** the plan flagged in Phase 4 — SAQ's actual configured defaults (`retries=1`, `retry_delay=0`) already absorb it; building bespoke retry infrastructure would have been unrequested scope.
- **Idempotency guard added to the ingest job** beyond what the plan strictly required: it checks `status == 'pending'` before attempting the re-hash/UPDATE at all, so a duplicate/retried job invocation short-circuits cleanly instead of relying on the DB trigger's exception as the only guard (the trigger still exists and still fires if this guard is ever bypassed — belt and suspenders, not a replacement).
- **Real bug found and fixed:** `data_source_id` arrives at the ingest job as a plain string (SAQ job kwarg), which needs an explicit `UUID(...)` conversion before it reaches asyncpg's native UUID column type — would have failed at runtime on every job invocation if missed.
- **`claims.deal_id`'s FK was implemented in two passes.** The plan originally called this out of scope (claims predates deals in the migration chain); that was revisited and moved in-scope, gated on a live orphan-check query the implementation environment couldn't run itself (no network path to the real DO cluster from any subagent's sandbox — confirmed independently in the main session too, via a hanging `alembic current`). Vansh ran the check directly against the real database (`SELECT count(*) FROM claims WHERE deal_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM deals WHERE deals.id = claims.deal_id)` → `0`), and the FK was added to the same migration file as a follow-up edit.

---

## Side investigation: `claim_kind`/`assertion_class` schema-drift report

Unrelated to this feature, surfaced mid-session by a report from Vansh's junior: *"claims table is missing 2 columns (claim_kind, assertion_class); alembic thinks it's already applied, so a normal upgrade will skip it — needs a manual re-apply."*

- Verified migration `7b4b05b6d9c8_claim_kind_and_assertion_class_on_claims.py` is correct and complete in code (adds both columns + three check constraints); `app/models/claim.py` expects both columns.
- Could not verify the *live* database's actual state from this session (same DO-cluster-unreachable limitation as above).
- Wrote an idempotent manual-reapply SQL script (`ADD COLUMN IF NOT EXISTS` + `DO` blocks checking `pg_constraint` before each `ADD CONSTRAINT`) as a precaution, deliberately not touching `alembic_version`.
- Vansh ran it directly against the real database: **both columns and all three constraints already existed.** The junior's report was a false alarm — nothing was actually broken, and the "fix" was a no-op. Flagged as worth a follow-up with the junior to find out what they actually checked (wrong environment, stale info, etc.), so the same false signal doesn't get acted on again.

No code change resulted from this thread — included here only because it happened during this arc and the outcome (schema is fine) is worth having on record.

---

## Cross-repo work

- **`Simpero_AI_Gov_Web`** (frontend, separate repo): an `architect` subagent scoped to that repo produced `docs/plans/deal-document-upload-frontend.md`, covering the client-side pipeline (validate → hash via `crypto.subtle.digest` → presign → raw `PUT` → complete), against the finalized backend contract. Plan-only — no frontend code written yet. It flags one real product decision this backend work doesn't resolve: **where the upload UI actually mounts** (no "Documents" tab or per-deal document list exists in the frontend today), plus smaller open items (client/server file-type-allowlist drift, the API's snake_case field naming vs. the rest of that app's camelCase convention, and no `GET` endpoint yet for polling a single upload's post-`/complete` status).

---

## Session 2 (2026-08-02) — Spaces config simplification

Vansh noticed the first pass's `SPACES_*` `.env` block was a byte-for-byte duplicate of the existing `PARSER_SPACES_*` block (same bucket, same `CHANGEME` placeholders) and asked for the duplication removed — use the parser's vars directly instead of a second copy.

**Changed:**
- `app/core/config.py`: the five `spaces_*` fields (attribute names unchanged, so `app/services/uploads/spaces.py` needed no edits) now read via `Field(validation_alias="PARSER_SPACES_BUCKET")` etc., instead of their own `SPACES_*`-named env vars.
- `.env.example`: the standalone `SPACES_*` block (the one from "What was built" → Config above) was deleted entirely. The existing `PARSER_SPACES_*` block's comment was rewritten to note it now serves two purposes (parser cache + this app's uploads) instead of one.
- `README.md`: the "Needs `SPACES_BUCKET`, ..." line updated to point at `PARSER_SPACES_*` instead.
- `docs/plans/document-upload-presigned-urls.md`'s Storage/Spaces section amended in place to record this as the final state (see that doc directly, not repeated here).

**Not changed:** the actual bucket/credentials/values — this was a config-plumbing simplification only, zero behavior change. `settings.spaces_bucket` (and friends) resolve to the identical values as before, just sourced from one env var name instead of two.

---

## Verification performed

- `uv run pyright`: 0 errors, 0 warnings — run directly in the main session (not just relayed from the implementation subagent).
- `uv run alembic current`: confirmed unreachable from this session's sandbox (hangs against the real DO cluster) — independently corroborates every implementation phase's stated reason for testing against a disposable local Postgres instead of the real cluster.
- `uv run pytest`: **197 passed** reported by the implementation orchestrator (each phase's DB-backed tests — RLS isolation, column-grant behavior, one-way trigger behavior — run against a disposable Docker Postgres container, since `ALEMBIC_DATABASE_URL`/`DATABASE_URL` point at the real DO cluster). Not independently reproduced end-to-end in the main session (no local test-Postgres was stood up here); cross-checked instead via direct `git status`/file-content inspection matching the reported change set exactly.
- `contracts/test_claims_contract.py`: 22/22 passing per the orchestrator's report — unaffected, since the FK work is DB-level only and touches no JSON shape.
- **`claims.deal_id` orphan-check**, run directly against the real database by Vansh: `0` rows.
- **`claim_kind`/`assertion_class` verification queries**, run directly against the real database by Vansh: both columns present, all three constraints present.

---

## Out of scope / deliberately not built

- **Phase 8 — scanned/image-only document detection.** Filed as [SIM-350](https://linear.app/simpero/issue/SIM-350/scannedimage-only-document-detection-needs-a-text-layer-inspection), blocked on a cross-repo product/architecture decision (a synchronous call from the ingest job to `Simpero_Gov_AI_Services`' `POST /parse`, vs. reviving the dormant async-worker scaffolding already sitting unused in `app/jobs/parse_client.py`). Not implemented here.
- **The frontend implementation itself.** Plan only (see Cross-repo work above); no code written in `Simpero_AI_Gov_Web`.
- **A `GET` endpoint for polling a single upload/document's status.** Not built. The frontend plan explicitly needs this before any real-time "pending → verified" UI can exist; sizing it is a small, separate backend addition, not part of this pass.
- **OCR execution, confidence-capping.** Founder-descoped from Alpha 2026-07-20 (SIM-218 sub-task 4); not built, not planned.
- **Presigned POST + storage-level policy conditions** (as an alternative to presigned PUT) — considered and declined; enforcement instead comes from three layers (client-side pre-check, declared-size guard at `/uploads/presigned-url`, and the ingest job's streaming size ceiling), judged sufficient for this threat model without the extra frontend/policy-document complexity POST would add.

## Not yet committed

None of this work has been committed to git — it exists only in the working tree. New files: the two `data_source`-related Alembic migrations, `app/models/data_source.py`, `app/repo/DataSourceRepo.py`, `app/services/uploads/` (adapter), `app/schemas/uploads.py`, `app/api/uploads.py`, `app/jobs/tasks/ingest_data_source.py`, five new test files, and this repo's two planning docs (`docs/plans/document-upload-spec.md`, `docs/plans/document-upload-presigned-urls.md`) plus this implementation doc. Modified: `.env.example`, `README.md`, `app/core/config.py`, `app/jobs/tasks/__init__.py`, `app/main.py`, `app/models/__init__.py`, `app/models/chunk.py`, `app/models/claim.py`, `tests/test_chunks_rls.py`. Review `git status`/`git diff` before committing — everything above belongs in one coherent change set together.
