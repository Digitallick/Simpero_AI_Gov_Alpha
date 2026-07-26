# FS-A-SEC-1 — Encryption & Transit Security Confirmation

Date: 2026-07-26
Ticket: SIM-226 / FS-A-SEC-1 (Epic 5 — Security)

## Objective

Confirm and record, in one place, that Simpero's data is encrypted both at
rest and in transit — for internal assurance and so the answer is ready for
a design partner's security questionnaire without re-deriving it each time.

Three things needed confirming:
1. Postgres (Managed Database) encrypts data at rest.
2. Spaces (object storage) encrypts data at rest.
3. All network traffic is encrypted in transit (TLS).

## Summary

| # | Item | Status |
|---|---|---|
| 1 | Postgres encryption at rest | ⏳ Needs DO console confirmation |
| 2 | Spaces encryption at rest | ⏳ Needs DO console confirmation |
| 3 | TLS in transit | ✅ Confirmed from code (one accepted internal exception, see below) |

## 1. Postgres encryption at rest

Not verifiable from the codebase — this is a DigitalOcean cluster-level
setting, not something that shows up in application config. DigitalOcean
enables encryption at rest by default for all Managed Database clusters, but
that needs an explicit look rather than an assumption.

**To confirm:** DO Console → Databases → `db-pgsql-tor1-13122-do-user-38781341-0`
→ Settings → confirm encryption at rest, and record the confirmation here
(who checked, when, what it said).

## 2. Spaces encryption at rest

Same situation. The bucket in use is `simpero-cim-xlsx-upload` (region
`tor1`). DigitalOcean encrypts Spaces objects at rest via server-side AES-256
by default, but needs the same explicit confirmation.

**To confirm:** DO Console → Spaces → `simpero-cim-xlsx-upload` → confirm
encryption at rest, and record the confirmation here.

## 3. TLS in transit — confirmed from code

| Hop | Evidence |
|---|---|
| Alembic (migrations) → DO Postgres cluster, direct | `ALEMBIC_DATABASE_URL` requires `sslmode=require` |
| PgBouncer → DO Postgres cluster (backend) | `server_tls_sslmode = require` in `docker/pgbouncer.ini` |
| App → DO Managed Valkey | `VALKEY_URL` must use `rediss://`; DO Valkey rejects unencrypted connections from outside its private network |

**One accepted exception:** the app → PgBouncer hop (`DATABASE_URL`, runtime)
is not itself TLS-encrypted — there are no TLS params on this connection in
`app/core/database.py`. This relies on the app and PgBouncer sharing a
private network (same DO VPC / same deployment), not on-the-wire encryption.
Not treated as a gap today, but worth revisiting if that topology ever
changes (e.g. PgBouncer becomes reachable over a public hop).

**Still open:** how client (browser) → app traffic is TLS-terminated. No
infra-as-code exists in this repo (no `.do/app.yaml`) describing the public
deployment. If served via DO App Platform, TLS is automatic/managed for the
platform domain and any custom domain with a DO-managed cert — needs
confirming against however this app is actually deployed.

## Open items

1. Confirm Postgres cluster encryption at rest in the DO console; update
   this doc.
2. Confirm Spaces bucket encryption at rest in the DO console; update this
   doc.
3. Confirm how the app is deployed publicly and that client→app traffic is
   TLS-terminated there; update this doc.

This document stays the single source of truth for these three
confirmations — update it in place rather than creating a new one when the
open items are resolved.
