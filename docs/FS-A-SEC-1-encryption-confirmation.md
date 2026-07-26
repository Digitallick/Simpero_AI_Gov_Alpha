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
| 1 | Postgres encryption at rest | ✅ Confirmed — DigitalOcean platform default |
| 2 | Spaces encryption at rest | ✅ Confirmed — DigitalOcean platform default |
| 3 | TLS in transit | ✅ Confirmed from code (one accepted internal exception, see below) |

## 1. Postgres encryption at rest — confirmed

Not a per-cluster toggle in the DO console — there is nothing to enable,
because DigitalOcean encrypts every Managed Database cluster at rest by
default, as a platform guarantee, not a customer-configurable setting. Per
DigitalOcean's own Shared Responsibility Model for Managed Databases:

> "Data in all Managed Database clusters is encrypted at rest with LUKS
> (Linux Unified Key Setup)."

> "As a platform as a service offering, DigitalOcean maintains the security
> of the infrastructure Managed Databases is hosted on."

This is DigitalOcean's responsibility under the shared responsibility model,
not ours to configure — confirmed 2026-07-26 against
https://www.digitalocean.com/security/shared-responsibility-model-managed-databases.

## 2. Spaces encryption at rest — confirmed

Same situation — no toggle exists in the bucket Settings page because it
isn't configurable; it's on by default for every Spaces bucket. Per
DigitalOcean's own Shared Responsibility Model for Spaces:

> "Data on Spaces is encrypted at rest, which helps to minimize the risk of
> a data breach via malicious hardware access."

Confirmed 2026-07-26 against
https://www.digitalocean.com/security/shared-responsibility-model-spaces.
(DO also documents an optional customer-side extra layer via the s3cmd
`encrypt` flag for particularly sensitive objects — not required for this
confirmation, noted for future reference if ever needed.)

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

1. ~~Confirm Postgres cluster encryption at rest~~ — done, DigitalOcean
   platform default (see above).
2. ~~Confirm Spaces bucket encryption at rest~~ — done, DigitalOcean platform
   default (see above).
3. Confirm how the app is deployed publicly and that client→app traffic is
   TLS-terminated there; update this doc. Not a hard blocker for this
   ticket's core ask — no infra-as-code exists in this repo to check it
   against, so it needs whoever manages the actual deployment to confirm.

This document stays the single source of truth for these three
confirmations — update it in place rather than creating a new one if
anything here changes.
