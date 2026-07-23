# Local sandbox

Run the whole pipeline on your own machine — a CIM goes in, cited claims land in a local Postgres — with **no cloud database**. No DigitalOcean cluster, no firewall rule.

```
./sandbox/up.sh                     # local Postgres + Valkey, roles, migrations
./sandbox/run.sh path/to/cim.pdf    # parse → extract → emit → ingest
./sandbox/down.sh                   # stop  (--wipe also deletes the data)
```

The infrastructure needs no credentials. **Extraction has two tiers, and the prose tier does:** table extraction is deterministic and offline, but reading facts out of prose calls the Anthropic API, so the default run needs `ANTHROPIC_API_KEY` (see [§3](#3-run-the-pipeline-on-a-cim)). Pass `--tables-only` for a fully offline, key-free run.

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
# the full pipeline (default) — needs a key, see below:
export ANTHROPIC_API_KEY=sk-ant-...
./sandbox/run.sh /path/to/your-cim.pdf --entity "Target Co"

# or a fully offline, key-free run:
./sandbox/run.sh /path/to/your-cim.pdf --entity "Target Co" --tables-only
```

- `--entity` names the company the claims are about (optional; defaults to "Target Co").
- `--org` sets the demo tenant key (optional; defaults to `sandbox_demo`).

**Extraction tiers** — each a strict superset of the one above it:

| flag | tiers | model calls | key |
|---|---|---|---|
| `--tables-only` | tables | none | not needed |
| `--prose` | + numeric facts in prose | 1 / prose page | **required** |
| `--qualitative` *(default)* | + claims that carry no number | 2 / prose page | **required** |

The prose tiers call the Anthropic API and require **`ANTHROPIC_API_KEY`** (or `ANTHROPIC_AUTH_TOKEN`) exported in your shell. `run.sh` checks for it up front and stops before touching your CIM if it is absent. The key is **never** read from `sandbox/.env.sandbox` — that file is committed, and an API key does not belong in it; it comes from your environment only. Your parse-service checkout must be recent enough to have the prose tiers (the `--prose`/`--qualitative` flags on `emit_claims.py`); `git pull` it if `--tables-only` works but the prose tiers report an unknown flag.

**Confidentiality:** the CIM you pass is copied into `sandbox/cim/`, which is **gitignored and never committed**. Real deal documents are confidential — don't commit them. The prose tiers additionally **send each prose page's text to the Anthropic API**, so a real deal document leaves your machine on those tiers; `--tables-only` makes no network call at all. No sample document ships in this repo; bring your own (any financial-statement PDF with a table under a scale header works well — income statements are ideal).

`run.sh` narrates each step and, at the end, **prints the claims table it just wrote** so you can see exactly what landed:

```
[1/4] Copying the CIM into sandbox/cim/  (gitignored, never committed)   ✓
[2/4] Parse → extract → emit   (parse service; docling, no database)
      ✓ emitted 24 claims (24 cited, 0 missing), 0 flags
[3/4] Ingest into the local claims spine   (backend, as the dd_app app role)
      24 claims validated against the contract.
      dd_app, tenant 'sandbox_demo': sees 24 claims (inserted 24).
      dd_app, a DIFFERENT tenant: sees 0 claims (RLS isolation).
[4/4] Reading back the claims table
     entity     |      attribute      |   raw   | normalized | unit | page |  span   |  status
 ---------------+---------------------+---------+------------+------+------+---------+----------
  Target Co     | Revenue | FY2023    | $19,850 | 19850000.0 | USD  |    1 | 209-216 | proposed
  Target Co     | Gross Margin | FY23 | 9,150   | 9150000.0  | USD  |    1 | 295-300 | proposed
  Target Co     | Gross Margin % | F.. | 46.1%   | 46.1       | %    |    1 | 335-340 | proposed
      24 claims  |  proposed  |  scale from: explicit_in_value, page_header
```

Read it top to bottom:

- The `dd_app sees 24 / a different tenant sees 0` pair is the **tenant isolation proof** — enforced by Postgres row-level security, exercised as the `dd_app` app role.
- Every row carries the raw printed value, the scaled `normalized` number, an exact character `span`, and `status = proposed` (cited, pending verification). None is fabricated.
- The scaling is visible: currency is scaled from the `(in Thousands)` header (`$19,850` → 19,850,000), a bare `9,150` is scaled the same way, and a percent is left alone (`46.1%` → 46.1). That last distinction is the point of the value-type gate.

---

## 4. Query the claims yourself

`run.sh` already printed the table. To poke at it further:

```bash
docker compose -f sandbox/docker-compose.yml exec postgres \
  psql -U doadmin -d simpero -c "SELECT attribute, value->>'normalized' FROM claims;"
```

---

## 5. Tear down

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
