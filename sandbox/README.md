# Local sandbox

Run the whole pipeline on your own machine — a CIM goes in, cited claims land in a local Postgres, and an interactive HTML report opens in your browser — with **zero cloud dependencies**. No DigitalOcean cluster, no firewall rule, no credentials.

```
./sandbox/up.sh                     # local Postgres + Valkey, roles, migrations
./sandbox/run.sh path/to/cim.pdf    # parse → extract → emit → ingest → report
./sandbox/down.sh                   # stop  (--wipe also deletes the data)
```

These instructions are verified end-to-end on macOS (Apple Silicon) with Colima.

---

## 1. Prerequisites

You need **uv**, a **Docker runtime**, and **both repos cloned side by side**.

### 1a. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1b. A Docker runtime

Either works. **Colima** is recommended for a terminal workflow — it's free, needs no GUI, and no license. (Docker Desktop is fine too if you already have it; skip to 1c.)

```bash
brew install colima docker docker-compose

# Homebrew does not auto-link the compose plugin. Link it once so `docker compose` works:
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/lib/docker/cli-plugins/docker-compose" ~/.docker/cli-plugins/docker-compose

# Start the Docker VM (first run downloads a small Linux image; a few minutes):
colima start
```

Confirm the daemon is up:

```bash
docker info    # should print "Server Version: ..." with no "Cannot connect" error
```

### 1c. Both repos, side by side

The pipeline is two services. The sandbox lives in this backend repo and calls the parse service, which it expects as a **sibling directory**:

```
your-projects/
  Simpero_AI_Gov_Alpha/          ← this repo
  Simpero_Gov_AI_Services/       ← the parse service (git clone it here)
```

```bash
# from the directory that contains Simpero_AI_Gov_Alpha:
git clone https://github.com/Digitallick/Simpero_Gov_AI_Services.git

# install deps in BOTH repos, once:
( cd Simpero_AI_Gov_Alpha    && uv sync --all-extras --dev )
( cd Simpero_Gov_AI_Services && uv sync --all-extras --dev )
```

If you keep the parse service somewhere else, set `PARSER_REPO=/path/to/Simpero_Gov_AI_Services` before running `run.sh`.

---

## 2. Bring it up

```bash
cd Simpero_AI_Gov_Alpha
./sandbox/up.sh
```

This pulls `pgvector/pgvector:pg16` (stock Postgres 16 with the pgvector extension bundled — the cloud database has pgvector, so the sandbox matches it) and `valkey:8`, starts them, waits until they're healthy, and applies the migrations. You should see:

```
==> starting Postgres + Valkey
 Container simpero-sandbox-postgres-1  Healthy
 Container simpero-sandbox-valkey-1    Healthy
==> applying migrations (as doadmin)
 Running upgrade aace95a1c412 -> 60a151dd80b0, claims spine
==> sandbox is up.
    Postgres : localhost:5433  (db simpero, roles doadmin / dd_app)
    Valkey   : localhost:6380
```

`up.sh` is idempotent — safe to re-run.

---

## 3. Run the pipeline on a CIM

```bash
./sandbox/run.sh /path/to/your-cim.pdf --entity "Target Co"
```

- `--entity` names the company the claims are about (optional; defaults to "Target Co").
- `--org` sets the demo tenant key (optional; defaults to `sandbox_demo`).

**Confidentiality:** the CIM you pass is copied into `sandbox/cim/`, which is **gitignored and never committed**. Real deal documents are confidential — don't commit them. No sample document ships in this repo; bring your own (any financial-statement PDF with a table under a scale header works well — income statements are ideal).

`run.sh` narrates the five steps demo-style — a live spinner with elapsed time during the parse (the slow step on long documents), then a summary of what landed instead of a raw table dump — and finishes by opening the HTML report in your browser:

```
[1/5] Copying the CIM into sandbox/cim/  (gitignored, never committed)   ✓
[2/5] Parse → extract → emit   (docling layout analysis)
      ⠹ analyzing the document… 23s          ← spinner, replaced on completion by:
      ✓ emitted 576 claims (573 cited, 3 missing), 0 flags
[3/5] Ingest into the local claims spine   (backend, as the dd_app app role)
      576 claims validated against the contract.
      dd_app, tenant 'sandbox_demo': sees 576 claims (inserted 576).
      dd_app, a DIFFERENT tenant: sees 0 claims (RLS isolation).
[4/5] Verifying what landed   (reading THIS run's claims back from Postgres)
      Claims stored ............ 576
      With exact citation ...... 573   (page + character span + word-level boxes)
      Missing citation ......... 3     (nothing resolved — recorded honestly, no fabrication)
      Distinct attributes ...... 533
      Pages with claims ........ 37    (pages 5-49 of the document)
[5/5] Building the interactive report   (rendered pages + click-through citations)
```

Read it top to bottom:

- The `dd_app sees 576 / a different tenant sees 0` pair is the **tenant isolation proof** — enforced by Postgres row-level security, exercised as the `dd_app` app role.
- The step-4 summary is scoped to **this run's `session_id`** — earlier runs for the same tenant stay in the table but don't inflate the numbers. (The "tenant sees N" line in step 3 is the tenant's all-runs total; wipe first if you want it to match.)
- Every claim carries the raw printed value, the scaled `normalized` number, an exact character span, and `status = proposed` (cited, pending verification). None is fabricated.

For a pristine client demo where every count matches the run, start clean: `./sandbox/down.sh --wipe && ./sandbox/up.sh`.

---

## 4. The HTML report

`run.sh` finishes by rebuilding `sandbox/report.html`'s data and opening it
in your browser (re-open it any time — no server needed). It shows:

- **Header + stat cards** — entity, source file, tenant, page range, and
  counts for total claims, proposed, missing citation, distinct attributes.
- **Search across every column** — attribute, raw, normalized, unit, scale
  source, page, span, status, flags — combined with status filter pills
  (All / Proposed / Missing / …, built from the statuses actually present)
  and a live "Showing X of Y" count.
- **A paginated table** — 50 rows per page with Prev/Next; the pager hides
  itself when everything fits on one page. Long attribute names wrap so the
  other columns stay visible.
- **Click-through citations** — any cited row has a clickable page number:
  a modal opens with the actual rendered PDF page, the clicked claim's
  word-boxes highlighted in gold and every other cited claim on that page
  outlined in teal (Esc / click outside / × closes). `missing` rows stay
  plain text — they have no span/bbox to show.

The report always reflects the **latest run** for the tenant (scoped by
`session_id`), not the accumulation of every run before it.

**Confidentiality by construction:** `report.html` is a data-free template,
committed to git. Everything confidential — the claims and the page images,
rendered at 144 DPI with pypdfium2 from the parser repo's venv — is written
to `sandbox/cim/report_data.js`, which the template loads via a `<script>`
tag (works from `file://`, no server needed). `cim/` is gitignored, so no
CIM content can leak onto git. Only pages carrying at least one bbox-cited
claim are rendered.

The data file is a static snapshot — re-run `run.sh` to refresh it, or
rebuild just the report without re-parsing (from this repo's root):

```bash
uv run --project ../Simpero_Gov_AI_Services python sandbox/export_report.py \
  --pdf "sandbox/cim/your-cim.pdf"     # --pdf optional when cim/ holds exactly one PDF
```

---

## 5. Query the claims yourself

The report and step-4 summary cover the common questions; for anything else,
go straight at the database:

```bash
docker compose -f sandbox/docker-compose.yml exec postgres \
  psql -U doadmin -d simpero -c "SELECT attribute, value->>'normalized' FROM claims;"
```

---

## 6. Tear down

```bash
./sandbox/down.sh          # stop the containers, keep the data
./sandbox/down.sh --wipe   # stop and DELETE the local database (clean slate)
colima stop                # stop the Docker VM entirely (frees its ~2 GB RAM)
```

To start over from an empty database: `./sandbox/down.sh --wipe` then `./sandbox/up.sh`.

---

## How this differs from production

Deliberately simplified for local use — all correct, none load-bearing on the cloud:

- **No PgBouncer.** The app connects directly to local Postgres. `SET LOCAL` tenant scoping works fine on a direct connection; the pooler only matters for connection scaling in production.
- **Plain `redis://` Valkey**, not DO's TLS-only `rediss://`.
- **`dd_app` has a well-known local password** (in `sandbox/.env.sandbox`) — a local-only container credential, not a secret.
- **Auth is the same stub as production** (`decode_clerk_jwt`), so the demo sets the tenant context directly rather than decoding a Clerk JWT.

Otherwise it's faithful: the same two Postgres roles (`doadmin` owns and migrates; `dd_app` is RLS-restricted), the same migrations, the same RLS policies, and the same two-process seam — the parse service emits claims as JSON, the backend ingests them, neither sharing a runtime.

---

## Troubleshooting

| symptom | fix |
|---|---|
| `docker: 'compose' is not a docker command` | link the plugin (step 1b): `ln -sfn "$(brew --prefix)/lib/docker/cli-plugins/docker-compose" ~/.docker/cli-plugins/docker-compose` |
| `Cannot connect to the Docker daemon` | `colima start` (or launch Docker Desktop) |
| `error: parse service repo not found` | clone `Simpero_Gov_AI_Services` as a sibling, or set `PARSER_REPO=/path/to/it` |
| `port 5433 / 6380 already in use` | change the host ports in `sandbox/docker-compose.yml` and the matching URLs in `sandbox/.env.sandbox` |
| migrations fail on first `up.sh` | the DB may still be initializing — re-run `./sandbox/up.sh` (it's idempotent), or `./sandbox/down.sh --wipe` and start fresh |
| want a completely clean slate | `./sandbox/down.sh --wipe && ./sandbox/up.sh` |
