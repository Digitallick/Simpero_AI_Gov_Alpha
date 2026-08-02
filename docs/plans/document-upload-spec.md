# Document Upload — Spec (input to implementation plan)

> **Superseded.** This doc captures the reasoning behind the original design
> decisions, but several specifics below were overridden later — see
> `docs/plans/document-upload-presigned-urls.md` (the authoritative plan) and
> `docs/implementations/2026-07-31-document-upload.md` (what actually shipped).
> Notably: the Storage section's "prefer a separate bucket" recommendation was
> reversed (`PARSER_SPACES_BUCKET` is reused), the object key shape shown here
> is an earlier draft, and TTL/size are shown as open questions here but are
> long since fixed (10 min / 10 MB).

**Covers three Linear tickets as one feature** (they share one request flow and
one table — do not plan them as three independent, sequential pieces of work):

- **SIM-220** (FS-A-ONBD-13) — presigned-URL upload mechanism + fingerprinting
- **SIM-216** (FS-A-ONBD-9) — `data_source` registry (append-only)
- **SIM-218** (FS-A-ONBD-11) — upload guards (type/size, dedupe, scanned-doc flag)

**Status:** For the architect subagent. This is a spec of the decisions
already made in conversation with the ticket owner (Vansh) — verify every
claim against the actual code before treating it as fact, then produce a
phased implementation plan in the style of `docs/plans/admin-portal-backend.md`
(verified findings with file:line, then phases).

---

## Why these three tickets are one unit of work

- `data_source` (SIM-216) is the table SIM-220 must write the fingerprint
  into — `app/models/chunk.py` already has a `document_id` column with no FK
  "because the documents/data_sources table doesn't exist yet." SIM-220 has
  no acceptance-criteria-satisfying place to record a fingerprint without it.
- SIM-216 sub-task 2 ("wire the upload path to write a row on every
  successful upload") is the same upload path SIM-220 builds. One `/complete`
  handler, one row written.
- SIM-218's guards split across the same request flow SIM-220 defines (see
  below) — they are not a separable ticket, they're checks bolted onto
  specific steps of SIM-220's flow.

## Request flow

Three network calls, client-driven, matching the existing tenant/auth
pattern (`get_db` / Clerk JWT / `SET LOCAL app.org_id`):

1. **`POST /uploads/presigned-url`** — control plane, synchronous, authenticated
   route like any other. Input: `deal_id`, `filename`, declared `size`,
   declared `sha256` (see below). Output: presigned PUT URL + `upload_id`.
2. **`PUT <presigned-url>`** — data plane. Client → object storage directly.
   Never touches the app server, never appears in its logs/body-size limits.
3. **`POST /uploads/{upload_id}/complete`** — control plane. Client calls
   this after the PUT succeeds. Creates the `data_source` row
   (`status=pending`) and enqueues the async verification job.

## Decision: dedupe check happens at step 1, not after upload

Original design (Shany's SIM-220 comment) computed the fingerprint in an
async ingest job after the file was already fully uploaded — for a
duplicate, that means the user waits through the whole upload only to be
told afterward it was pointless. Rejected for that reason.

**Revised:** the browser hashes the file locally before calling
`/uploads/presigned-url` (`crypto.subtle.digest("SHA-256", ...)` — native, no
library). That hash rides in the presign request. The server does a
**synchronous** `SELECT` against `data_source.fingerprint` for that
org/deal. Match → 409, no presigned URL issued, zero bytes ever uploaded.
No match → sign and return the URL as usual.

This also resolves what would otherwise be a circular dependency: ONBD-11's
"dedupe via file hash" sub-task no longer needs SIM-220's async ingest job to
have run first — it's satisfied entirely at presign time.

**Trust boundary this creates:** a client-declared hash is not proof of the
uploaded bytes. Treat it as a fast pre-flight check only, never as the
authoritative record. The async job (step 3 below) still re-hashes the
actual stored object and either confirms the match or flags a discrepancy —
it stops being the dedupe decision-maker and becomes an integrity verifier.

## `data_source` schema (SIM-216)

One row per upload. Fields (adjust names to match repo convention, verify
against `Chunk`/`HumanAuditLog` column style):

- `id` (UUID, PK)
- `org_id` (Integer, FK → `organisation.id` — **not** the Clerk string org
  id; matches the convention in `Chunk`/`HumanAuditLog`)
- `deal_id` (UUID)
- `storage_key` (Text — the object key in Spaces)
- `filename` (Text)
- `declared_sha256` (Text — what the client claimed at presign time)
- `fingerprint` (Text, nullable — the async job's verified hash; null until
  ingest completes)
- `status` (pending → verified | quarantined | ocr_needed | mismatch)
- `created_at`

**Append-only at the DB level** — copy the exact pattern already in the repo
for `human_audit_log` (`alembic/versions/7175bc85ffb0_human_audit_log.py`):
`ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + org-isolation
policy scoped `FOR ALL TO dd_app`, then `REVOKE UPDATE, DELETE ON
data_source FROM dd_app` in the same migration. No application-level
immutability guard — per CLAUDE.md, that would be bypassable and give false
assurance.

One caveat this table introduces that `human_audit_log` didn't have: the
async job needs to move a row from `pending` to `verified`/`quarantined` —
that's an `UPDATE`, and this table is about to have `UPDATE` revoked from
`dd_app` entirely. Resolve this explicitly in the plan (candidates: the
ingest job runs as a role that still has UPDATE and a narrower revoke that
still blocks it post-verification, e.g. via a trigger or a status check
constraint that only allows one transition; or the row is genuinely
insert-only and status lives on a separate mutable table). Do not hand-wave
this — it's the one place "append-only" and "the row has a lifecycle" are in
tension.

## Async ingest job (SAQ, `app/jobs/tasks/`)

Enqueued by `/complete`, not triggered by a storage event (this stack has no
S3-event/Lambda equivalent — SAQ + Valkey is the only async primitive that
exists here, per `app/jobs/queue.py` and `CLAUDE.md`'s job-queue section).

Responsibilities, in one job (reads the object once):

1. Stream the object from storage, compute SHA-256 (chunked, never full
   buffer).
2. Compare to `declared_sha256` → `verified` or `mismatch`.
3. Scanned/image-only detection (ONBD-11 sub-task 3) — **flag OCR-needed
   only, never run OCR or any confidence-capping logic** (explicitly
   descoped by founder instruction 2026-07-20, see SIM-218 sub-task 4).
4. Update the `data_source` row's status (see the append-only caveat above
   — resolve before implementing).

**Open problem for the architect to size, not silently solve:** detecting
"scanned/image-only" means inspecting whether the document has a real text
layer. This app deliberately dropped Docling/pypdf in the
Simpero_Gov_AI_Services split (`CLAUDE.md`, "Document parsing: split out")
and cannot do this locally anymore. The only viable path today is the async
job making a **synchronous** HTTP call to that service's `POST /parse` —
which ties the ingest job's latency to Docling's per-document parse time,
in tension with "async, decoupled" framing. Flag this explicitly rather than
assuming it away; if it's a blocker, sub-task 3 may need to be descoped from
this pass and ticketed separately.

## Guards resolved at presign time (ONBD-11 sub-tasks 1 and 2)

Both run synchronously inside `POST /uploads/presigned-url`, before any URL is
signed:

- File type / size reject (declared, not measured — nothing has been
  uploaded yet) → clear 4xx with a specific reason.
- Dedupe check, per the revised design above.

## Storage / config gaps to verify and close

- This app has **no** app-level Spaces settings today. The only
  `SPACES_*` env vars (`PARSER_SPACES_*`) are explicitly scoped to the
  parser service's document-cache bucket (`simpero-cim-xlsx-upload`), read
  by `ParserSettings`, not this app. Decide: reuse that bucket with a new
  key prefix, or provision a separate bucket/credential pair. Given DO
  Spaces keys are bucket-scoped (not prefix-scoped) — see
  `simpero-tf-state-staging`/`-production` precedent in the deploy docs —
  reusing the parser's bucket+key hands upload-presign code the same blast
  radius as the parser cache. Recommend a separate bucket/key unless there's
  a reason not to; confirm with Vansh either way, don't default silently.
- Object key shape: `org/{organisation.id}/deals/{deal_id}/uploads/{upload_id}/{filename}`
  (integer org id, not Clerk's string id — matches `Chunk`'s RLS-join
  convention). Presigned policy's condition should scope to that prefix.
- Presigned URL TTL: short (minutes, not hours) — confirm exact value with
  Vansh, this spec doesn't fix a number.

## Explicitly out of scope

- OCR execution, confidence-capping (SIM-218 sub-task 4 — founder removed
  this from Alpha 2026-07-20, do not build it).
- Any change to `audit_log`/`human_audit_log` — unrelated table, mentioned
  here only as the immutability pattern to copy.

## Linear housekeeping (not blocking, just inaccurate today)

- SIM-220 lists dependencies on SIM-84 (done) and ONBD-11, but not SIM-216 —
  despite needing `data_source` to exist to satisfy its own acceptance
  criteria.
- SIM-218 (ONBD-11) lists "Org + fund setup" as its dependency, but sub-tasks
  1–2 as revised here actually depend on SIM-220's presign endpoint existing
  (the checks live inside it), not the other way the tickets currently imply.
