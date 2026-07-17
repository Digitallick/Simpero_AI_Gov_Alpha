# Local sandbox

Run the whole pipeline on your machine — a CIM goes in, cited claims land in a local Postgres — with **zero cloud dependencies**. No DigitalOcean cluster, no firewall rule, no credentials.

```
./sandbox/up.sh                     # local Postgres + Valkey, roles, migrations
./sandbox/run.sh path/to/cim.pdf    # parse → extract → emit → ingest
./sandbox/down.sh                   # stop  (--wipe also deletes the data)
```

## What you need

- **Docker** (for local Postgres + Valkey — stock images, nothing custom builds)
- **uv** (runs the Python)
- **Both repos cloned side by side** — this backend and the parse service:
  ```
  parent/
    Simpero_AI_Gov_Alpha/          ← you are here
    Simpero_Gov_AI_Services/       ← the parse service
  ```
  `run.sh` finds the parser at `../Simpero_Gov_AI_Services` by default; override with `PARSER_REPO=/path/to/it`. Run `uv sync --all-extras --dev` in each repo once.

## What it does

`up.sh` starts two stock containers and applies the migrations:

| container | port | role |
|---|---|---|
| `postgres:16` | `localhost:5433` | the `simpero` database, owned by **doadmin**, plus the RLS-restricted **dd_app** — the same two roles as production, so RLS behaves identically |
| `valkey:8` | `localhost:6380` | job queue (the demo doesn't use it; here so the app itself can run) |

`run.sh` then runs the two halves of the C3 seam as two processes, exactly as production will:

```
your-cim.pdf
  → [parse service]  parse → extract → emit   → claims.json   (docling; no database)
  → [backend]        validate → INSERT as dd_app              (sqlalchemy; no docling)
  → claims in the local spine, tenant-isolated
```

The backend never imports docling and the parser never touches the database — the split holds locally just as it does in the cloud.

## Confidentiality — read this

**The CIM you pass is copied into `sandbox/cim/`, which is gitignored, and is never committed.** Real CIMs are confidential deal documents; do not commit them. There is deliberately no sample document in this repo — bring your own (any financial-statement PDF with a table under a scale header works well; the pipeline shines on income statements).

## Inspecting the result

```
docker exec -it simpero-sandbox-postgres-1 psql -U doadmin -d simpero -c \
  "SELECT entity, left(attribute,30) AS attr, value->>'raw' AS raw,
          value->>'normalized' AS normalized, page, status
   FROM claims ORDER BY page, char_start LIMIT 20;"
```

You'll see rows like `Revenue | FY2023 | $19,850 | 19850000 | 11 | proposed` — the raw value, the header-scaled number, the page it came from, and a status of `proposed` (cited, pending verification). Every one carries an exact character span and bbox; none is fabricated.

## How this differs from production

Deliberately simplified for local use — all correct, none load-bearing on the cloud:

- **No PgBouncer.** The app connects directly to local Postgres. `SET LOCAL` tenant scoping works fine on a direct connection; the pooler only matters for connection scaling in production.
- **Plain `redis://` Valkey**, not DO's TLS-only `rediss://`.
- **`dd_app` has a well-known local password** (`sandbox/.env.sandbox`) — a local-only container credential, not a secret.
- **Auth is the same stub as production** (`decode_clerk_jwt`), so the demo sets the tenant directly rather than decoding a Clerk JWT.

## Troubleshooting

- **`docker` or `uv` not found** — install them (links printed by `up.sh`).
- **parse service repo not found** — clone `Simpero_Gov_AI_Services` as a sibling, or set `PARSER_REPO`.
- **port 5433 / 6380 in use** — change the host ports in `docker-compose.yml` and the matching URLs in `.env.sandbox`.
- **start over from an empty database** — `./sandbox/down.sh --wipe` then `./sandbox/up.sh`.
