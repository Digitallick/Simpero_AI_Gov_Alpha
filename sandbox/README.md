# Local sandbox

Run the whole pipeline on your own machine — a CIM goes in, cited claims land in a local Postgres — with **zero cloud dependencies**. No DigitalOcean cluster, no firewall rule, no credentials.

```
./sandbox/up.sh                     # local Postgres + Valkey, roles, migrations
./sandbox/run.sh path/to/cim.pdf    # parse → extract → emit → ingest
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

This pulls `postgres:16` and `valkey:8` (first run only), starts them, waits until they're healthy, and applies the migrations. You should see:

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

Expected output:

```
==> [1/2] parse + extract + emit  (parse service)
emitted 24 claims (24 cited, 0 missing), 0 flags
==> [2/2] ingest into the local claims spine  (backend, as dd_app)
24 claims validated against the contract.
dd_app, tenant 'sandbox_demo': sees 24 claims (inserted 24).
dd_app, a DIFFERENT tenant: sees 0 claims (RLS isolation).
--commit: persisting.
```

That last pair of lines is the tenant isolation proof: the owning tenant sees the claims, a different tenant sees none — enforced by Postgres row-level security, exercised as the `dd_app` app role.

---

## 4. Inspect the claims

```bash
docker exec -it simpero-sandbox-postgres-1 psql -U doadmin -d simpero -c \
  "SELECT entity, left(attribute,30) AS attr, value->>'raw' AS raw,
          value->>'normalized' AS normalized, page, status
   FROM claims ORDER BY page, char_start LIMIT 20;"
```

```
    entity     |            attr            |   raw   | normalized | page |  status
---------------+----------------------------+---------+------------+------+----------
 Target Co     | Revenue | FY2023           | $19,850 | 19850000.0 |    1 | proposed
 Target Co     | Cost of Goods Sold | FY202 | 7,100   | 7100000.0  |    1 | proposed
```

Each row carries the raw printed value, the header-scaled number, the page it came from, and `status = proposed` (cited, pending verification). Note the second row: `7,100` with no dollar sign is still scaled to 7,100,000 from the `(in Thousands)` header. Every claim has an exact character span and bbox; none is fabricated.

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
