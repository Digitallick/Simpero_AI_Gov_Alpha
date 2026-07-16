# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                              # install / sync dependencies
docker compose up --build            # start PgBouncer + app (port 8000) + SAQ worker
uv run uvicorn app.main:app --reload # run app locally without Docker
uv run alembic upgrade head          # apply migrations
uv run alembic revision --autogenerate -m "description"  # generate migration

uv run pytest                        # run all tests
uv run pytest tests/path/test_foo.py::test_name  # single test
uv run pyright                       # type checking

uv run saq app.jobs.worker.settings              # run the SAQ job worker
uv run saq app.jobs.worker.settings --web        # run the worker with the SAQ web dashboard (port 8080)
```

Tests require a real PostgreSQL instance — SQLite will not work because tests exercise RLS policies and `current_setting()`, which are PostgreSQL-specific.

## Architecture

### Two Postgres roles, strictly separated

| Role      | Privileges               | Used by                            |
| --------- | ------------------------ | ---------------------------------- |
| `doadmin` | DDL only                 | Alembic via `ALEMBIC_DATABASE_URL` |
| `dd_app`  | DML only, RLS-restricted | App at runtime via `DATABASE_URL`  |

`ALEMBIC_DATABASE_URL` connects **directly** to the DigitalOcean cluster, bypassing PgBouncer — DDL is not safe in transaction-pooling mode. `DATABASE_URL` routes through PgBouncer. Never swap them.

### Tenant isolation via `SET LOCAL` + RLS

Every authenticated request flows through the `get_db` dependency (`app/dependencies.py`), which:

1. Verifies the Clerk JWT and extracts `org_id` (via `CLERK_TENANT_ID_CLAIM`).
2. Opens a transaction.
3. Issues `SET LOCAL app.org_id = '<org_id>'` as the **first** SQL statement.
4. Yields the session to the route handler.

RLS policies on each table filter rows using `current_setting('app.org_id')`. **Do not add `WHERE org_id = ...` clauses in application queries** — that would be redundant and would mask a broken RLS configuration.

`SET LOCAL` (not `SET` or `SET SESSION`) is mandatory because PgBouncer in transaction-pooling mode reclaims the backend connection after each transaction. `SET SESSION` would leak the tenant's `org_id` to the next client that receives that connection.

### PgBouncer + NullPool

SQLAlchemy uses `NullPool` (`app/database.py`) because PgBouncer is the connection pool. Maintaining SQLAlchemy's own pool on top would hold PgBouncer slots open between requests, causing connection exhaustion. PgBouncer is configured in transaction-pooling mode via `docker/pgbouncer.ini`.

Do not open DB connections at application startup — that would hold a PgBouncer slot indefinitely.

### Auth: Clerk JWT (stub — not yet implemented)

`app/core/security.py::decode_clerk_jwt` is currently a `NotImplementedError` stub. The TODO block in that file describes the required implementation: fetch Clerk's JWKS, verify the JWT signature with `python-jose`, validate `exp`/`aud`/`CLERK_TENANT_ID_CLAIM`. The tenant claim key is `CLERK_TENANT_ID_CLAIM = "org_id"`.

### Audit log immutability

`UPDATE` and `DELETE` on `audit_log` are revoked from `dd_app` at the database level. Do not add application-level guards — they can be bypassed and give false assurance. The `AuditLog` model in `app/models/audit_log.py` is commented out pending the table's full implementation.

### Job queue: SAQ + DigitalOcean Managed Valkey

Background jobs run on [SAQ](https://github.com/tobymao/saq), backed by a DigitalOcean Managed Valkey instance (`VALKEY_URL`).

- `app/jobs/queue.py::get_queue` returns a process-wide `Queue` singleton (`Queue.from_url` is lazy — no connection opens at import time, same no-connections-at-startup rule as Postgres).
- `app/jobs/tasks/` holds job functions; register new ones in `app/jobs/tasks/__init__.py::functions`.
- `app/jobs/worker.py` exposes the `settings: SettingsDict` the SAQ CLI runs against (`uv run saq app.jobs.worker.settings`).
- `VALKEY_URL` must use `rediss://` (TLS) — DO Valkey rejects unencrypted connections from outside its private network. `?ssl_cert_reqs=none` on the URL matches the `sslmode=require` posture already used for `DATABASE_URL`/`ALEMBIC_DATABASE_URL` (encrypts, doesn't pin the CA).
- `GET /health/queue` verifies connectivity the same way `/health/db` does for Postgres.
- Run `uv run saq app.jobs.worker.settings --web` for the built-in dashboard (port 8080) — do not expose this port publicly without auth in front of it.
- `docker-compose.yml`'s `worker` service runs the SAQ worker in its own container (same image as `app`, command overridden) — the `app` service only enqueues jobs, it never executes them.

### Request flow

```
HTTP request
  → CORS middleware
  → route handler (e.g. app/api/deals.py)
      → get_db dependency
          → decode_clerk_jwt (verify Clerk JWT, extract org_id)
          → AsyncSessionLocal() → session.begin()
          → SET LOCAL app.org_id = '<org_id>'
          → yield session
      → SQLAlchemy query (RLS filters automatically by app.org_id)
  → response
```

### Adding a new resource

1. Define the SQLAlchemy model in `app/models/` (inherits `Base` from `app/database.py`).
2. Import it in `app/models/__init__.py` so Alembic sees it.
3. Generate and apply a migration.
4. Define Pydantic schemas in `app/schemas/`.
5. Add a router in `app/api/` using `Depends(get_db)` — tenant context is automatic.
6. Register the router in `app/main.py`.

The `deals` route (`app/api/deals.py`) is the reference pattern for authenticated, tenant-scoped endpoints.
