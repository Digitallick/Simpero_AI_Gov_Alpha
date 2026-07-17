# Simpero Backend

FastAPI backend for the Simpero AI-powered due diligence platform.

## Stack

- **FastAPI** + **uvicorn** — ASGI web framework
- **SQLAlchemy (async)** + **asyncpg** — async ORM with Postgres
- **PgBouncer** (transaction pooling mode) — connection pooler
- **DigitalOcean Managed Postgres** — database
- **Clerk** — authentication and multi-tenancy (JWT + Organizations)
- **Alembic** — database migrations
- **uv** — dependency management

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker + Docker Compose
- A DigitalOcean managed Postgres cluster (dev)

## Local Setup

```bash
# 1. Install dependencies
uv sync

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env — set DATABASE_URL, ALEMBIC_DATABASE_URL, CLERK_* values

# 3. Configure PgBouncer
# Edit docker/pgbouncer.ini — replace DO_CLUSTER_HOST_PLACEHOLDER with your DO cluster host
# Edit docker/userlist.txt — replace CHANGEME_MD5_HASH with the real dd_app md5 hash

# 4. Start PgBouncer + app
docker compose up --build

# 5. Run migrations (in a separate terminal)
uv run alembic upgrade head
```

## Architecture Notes

### Role split: doadmin vs dd_app

Two Postgres roles exist with separate responsibilities:

- **doadmin**: DDL privileges only. Used by Alembic via `ALEMBIC_DATABASE_URL`. Never at runtime.
- **dd_app**: DML only, RLS-restricted. Used by the app at runtime via `DATABASE_URL` (through PgBouncer).

### Tenant isolation: SET LOCAL under PgBouncer transaction pooling

Each authenticated request issues `SET LOCAL app.org_id = '<org_id>'` as the first SQL in its transaction. RLS policies filter rows by `current_setting('app.org_id')` — no `WHERE org_id = ...` in app code.

`SET LOCAL` (not `SET`) is used because PgBouncer reclaims the backend connection after each transaction. A `SET` would leak org_id to the next client's connection.

### Audit log immutability

`UPDATE` and `DELETE` on `audit_log` are revoked from `dd_app` at the DB level. App code does not enforce this.

### Document parsing lives in a separate repo

Docling-based PDF/XLSX/DOCX parsing moved out of this repo into its own
standalone FastAPI service, **Simpero_Gov_AI_Services** (split out
2026-07-17, history preserved via `git filter-repo`). This app does not
parse documents itself. That service currently exposes only a synchronous
`POST /parse` HTTP endpoint — this app has no live integration with it yet
(`app/jobs/parse_client.py` is unwired scaffolding for a possible future
async path; see CLAUDE.md's "Document parsing" section before relying on    
it).
