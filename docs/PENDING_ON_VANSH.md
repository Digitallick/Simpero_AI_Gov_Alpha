# Pending on Vansh — DO droplet deploy pipeline

Everything the code needs is written (`.github/workflows/deploy.yml`, `terraform/`, `docker-compose.prod.yml`, `Caddyfile`). None of it can run end-to-end until the steps below are done — they're all manual, DO-console/GitHub-console actions that only someone with account access can do. Full rationale for each decision lives in `docs/plans/do-droplet-deployment.md`; this doc is just the walkthrough.

Do these roughly in order — later steps depend on earlier ones.

**Status as of this writing: steps 1, 2, 3, 4, 6, 7, 8 done. Step 10 done for staging. Step 5 done for staging only — production's Postgres/Valkey clusters are deliberately deferred, not needed yet. Staging is fully ready for step 9 (first real run). Production is not — its `terraform-plan`/`apply` will fail until step 5's production cluster IDs are filled in, and step 10's production secrets can't be fully set until those clusters exist (production `DATABASE_URL`, `PGBOUNCER_DB_HOST`, etc. depend on them).**

---

## 1. ~~Confirm the Spaces region~~ — Done

`terraform/backend-staging.hcl` and `backend-production.hcl` are both set to `region = "tor1"`. Just make sure `simpero-tf-state-production` (step 3 below) actually gets created in `tor1` when you make it — if it lands in a different region, that file's `region`/`endpoints.s3` need to change to match its own bucket, not stay copied from staging's.

---

## 2. ~~Confirm object versioning is enabled on `simpero-tf-state-staging`~~ — Done

This is the **only recovery mechanism** if a leaked credential, a bad `terraform destroy`, or a stray delete from either this repo or the frontend repo ever touches state it shouldn't (see plan §1 for why — the two repos' state shares a bucket with no way to fence them apart at the DO level).

**`doctl` doesn't support this — DigitalOcean's own docs say versioning can only be enabled via the S3-compatible API, not the console.** Use the AWS CLI pointed at the Spaces endpoint.

**Important: this specifically needs your account's full-access Spaces key, not the bucket-scoped/limited key(s) from step 4 below.** Confirmed via DO's docs: bucket-configuration operations (versioning, lifecycle rules, bucket policies, CORS) are only permitted for full-access keys — a limited-access key gets `AccessDenied` on `PutBucketVersioning` even with read/write/delete object permissions on that exact bucket, by design. This is a one-time step using your own account credentials; it doesn't change anything about CI only ever using a bucket-scoped key going forward.

```bash
export AWS_ACCESS_KEY_ID=<your account's full-access Spaces key>
export AWS_SECRET_ACCESS_KEY=<its secret>

# Check current status
aws s3api get-bucket-versioning \
  --bucket simpero-tf-state-staging \
  --endpoint-url https://tor1.digitaloceanspaces.com

# If the above returns nothing (versioning off), turn it on:
aws s3api put-bucket-versioning \
  --bucket simpero-tf-state-staging \
  --endpoint-url https://tor1.digitaloceanspaces.com \
  --versioning-configuration Status=Enabled
```

Do this before this bucket is used for any real `terraform apply`.

---

## 3. ~~Create the `simpero-tf-state-production` bucket~~ — Done

**Steps:**
1. Create the bucket, region `tor1` (matches staging, keeps both `.hcl` files consistent) — via the console (Spaces → Create Bucket) or the API/CLI, your choice; bucket *creation* isn't versioning-restricted the way enabling versioning is.
2. **Immediately** enable versioning on it, same commands as step 2 above but with `--bucket simpero-tf-state-production` — do this right away so there's no window where it's unprotected.
3. Nothing else needs configuring on the bucket itself — **do not** attach a bucket policy (see step 4 below for why).

---

## 4. ~~Get Spaces credentials for both buckets from whoever manages the frontend repo's keys~~ — Done

You decided to share the frontend repo's (`Simpero_AI_Gov_Web`) Spaces key pair(s) rather than create dedicated ones for this repo. Since DigitalOcean's bucket-scoped ("limited access") keys can only be granted to **one bucket each**, you need two separate key pairs — one per bucket.

**Steps:**
1. Ask whoever manages `Simpero_AI_Gov_Web`'s DO Spaces access:
   - Does an existing key already have **read, write, and delete** access to `simpero-tf-state-staging`? If yes, get that key's Access Key ID and Secret Access Key.
   - If no such key exists yet, or you'd rather not reuse it, create a new **limited-access key** scoped to `simpero-tf-state-staging` with read/write/delete permission (DO console → Spaces → API → "Spaces access keys" → "Create limited access key" or equivalent — grant it to the one bucket, not account-wide).
2. Repeat for `simpero-tf-state-production` — this one **must** be a new key, since the bucket didn't exist until step 3 above, so nothing could already have a grant on it.
3. **Do not** request or accept a full-account Spaces key for either of these — the workflow only ever needs bucket-scoped access.
4. **Important, can't be undone**: a key's bucket grant is fixed when the key is created — you can rename a key afterward, but you can't re-scope it to a different bucket. If a key ends up wrongly scoped, the fix is to delete it and create a new one (and update the GitHub secret), not edit it in place.
5. **Do not apply a bucket policy to either bucket.** Doing so permanently blocks creating limited-access keys for that bucket afterward (and the reverse is also true — an existing limited-access key blocks adding a policy later). Leave both buckets without a policy.
6. **Do not add a lifecycle rule that expires old object versions** on either bucket. Versioning (step 2/3) is the only safety net this setup has — an expiration rule would quietly delete the very history that's supposed to protect you.

You'll end up with two Access Key ID / Secret Access Key pairs — one per bucket. Keep both handy for step 8.

---

## 5. Provision separate Postgres and Valkey clusters for staging and production

**Staging: done** (`dc41daf7-3ccd-43c6-8409-9ef6b8b647e2` Postgres, `bc62dad7-0e89-4323-b53b-2099b5a2fc29` Valkey, already in `staging.tfvars`).

**Production: deliberately deferred, not needed at the moment.** `terraform/production.tfvars` still has `postgres_cluster_id = "REPLACE_ME"` / `valkey_cluster_id = "REPLACE_ME"`. This is fine to leave as-is for now — it only becomes a blocker when you actually try to dispatch `deploy.yml` with `environment: production` and `run_terraform: true` (that run will fail at `terraform plan`, cleanly, with a clear "REPLACE_ME is not a valid value" or similar error — not silently). Staging is fully unblocked and doesn't depend on this. Come back to this step whenever production is actually needed:

1. List existing database clusters and their IDs:
   ```bash
   curl -s -X GET "https://api.digitalocean.com/v2/databases" \
     -H "Authorization: Bearer $DO_TOKEN" | jq '.databases[] | {id, name, engine, region}'
   ```
   Find or create a Postgres cluster and a Valkey cluster for production — must stay separate from staging's clusters above (see plan §1 for why: shared clusters break the Terraform DB-firewall setup and undermine tenant isolation).
2. Fill in `terraform/production.tfvars` with the real IDs.
3. Before production's **first** real `terraform apply`, check what's already trusted on both new clusters via the API (same command as for staging, below), so Terraform doesn't silently drop something relied on.

**Also still pending regardless of production's status — do this now for staging's two clusters**, before staging's first real `terraform apply` (§9):
```bash
curl -s -X GET "https://api.digitalocean.com/v2/databases/dc41daf7-3ccd-43c6-8409-9ef6b8b647e2/firewall" \
  -H "Authorization: Bearer $DO_TOKEN" | jq '.rules'
curl -s -X GET "https://api.digitalocean.com/v2/databases/bc62dad7-0e89-4323-b53b-2099b5a2fc29/firewall" \
  -H "Authorization: Bearer $DO_TOKEN" | jq '.rules'
```

---

## 6. ~~Generate two deploy SSH keypairs (one per environment)~~ — Done

Staging and production each need their **own** keypair — reusing one keypair across both environments' Terraform state risks DigitalOcean deduplicating the key and one environment's `apply` silently failing or colliding with the other's.

**Steps (run twice, once per environment):**
1. ```
   ssh-keygen -t ed25519 -f staging_deploy_key -C "simpero-deploy-staging"
   ssh-keygen -t ed25519 -f production_deploy_key -C "simpero-deploy-production"
   ```
2. For each keypair, you'll use:
   - The **public** key content (`.pub` file) → goes into that environment's `TF_VAR_ssh_public_key` GitHub secret (step 8).
   - The **private** key content → goes into that environment's `DROPLET_SSH_PRIVATE_KEY` GitHub secret (step 8).
3. Once both are safely stored as GitHub secrets, delete the local copies (or keep them somewhere secure — no workflow reads them from disk, they only need to exist as secrets).

---

## 7. ~~Create the 4 GitHub Environments~~ — Done

GitHub → this repo → **Settings → Environments → New environment**. Create all four:

| Environment name | Required reviewers? |
|---|---|
| `staging-plan` | No |
| `production-plan` | No |
| `staging` | **Yes — add yourself** |
| `production` | **Yes — add yourself** |

For `staging` and `production`, after creating them, go into each one's settings and add **"Required reviewers"**, with yourself as the reviewer.

**Deployment branches, also done**: `staging` allows `main` + `staging`; `production` allows `main` only; `staging-plan`/`production-plan` now mirror their gated counterparts (`main`+`staging` / `main` only respectively) — closes the gap where `terraform-plan` could otherwise run with real credentials from any branch, even though it can't mutate infrastructure. No required-reviewer rule on the `-plan` pair — that's still the only thing distinguishing them from their gated counterparts.

---

## 8. ~~Add the GitHub secrets~~ — Done

Two kinds: **repo-level** (same value regardless of environment) and **Environment-scoped** (different value per environment, set inside each Environment's own secrets page).

### Repo-level secrets
GitHub → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `TF_VAR_do_token` | A DigitalOcean API token (Settings → API → Generate New Token, write scope) — one token, works for both environments |
| `GHCR_PAT` | A classic GitHub PAT with `read:packages` scope only (github.com/settings/tokens) |

### Environment-scoped secrets
For **each** of the 4 Environments created in step 7, go to that Environment's page → **Environment secrets → Add secret**. Note: `TF_VAR_ssh_public_key` and the two Spaces credentials need to be set in **both** the `-plan` Environment and its matching gated Environment (e.g. both `staging-plan` and `staging`) — this duplication is unavoidable, GitHub doesn't support one Environment inheriting another's secrets.

**`staging-plan`:**
| Secret | Value |
|---|---|
| `TF_VAR_ssh_public_key` | Staging keypair's public key content (step 6) |
| `SPACES_ACCESS_KEY_ID` | Staging bucket's Spaces access key ID (step 4) |
| `SPACES_SECRET_ACCESS_KEY` | Staging bucket's Spaces secret key (step 4) |

**`staging`:** (same three secrets, same values, plus:)
| Secret | Value |
|---|---|
| `TF_VAR_ssh_public_key` | Same as `staging-plan` |
| `SPACES_ACCESS_KEY_ID` | Same as `staging-plan` |
| `SPACES_SECRET_ACCESS_KEY` | Same as `staging-plan` |
| `DROPLET_HOST` | *(leave empty for now — you'll fill this in after step 10, once the droplet exists and has an IP)* |
| `DROPLET_SSH_PRIVATE_KEY` | Staging keypair's private key content (step 6) |

**`production-plan`:** same as `staging-plan`, but with production's key pair and production's bucket's Spaces credentials.

**`production`:** same as `staging`, but with production's values throughout.

---

## 9. First real run — provision the infrastructure

Once steps 1–8 are done, go to **Actions → Deploy → Run workflow**:
1. Leave "Use workflow from" on `main` (or whichever branch has this pipeline merged).
2. Set **environment** to `staging`.
3. Check **run_terraform**.
4. Click **Run workflow**.

This runs: CI checks → image build/push → `terraform-plan` (no approval needed) → **pause for your approval** (you'll get a GitHub notification/see it in the Actions tab under "Review deployments") → `terraform-apply` → **a second pause for approval** → `deploy`.

- At the first pause, **read the Terraform plan output** before approving — it should show it creating one droplet, one SSH key, one firewall, and two database firewall rules. If it shows anything unexpected (especially anything about *destroying* something), stop and investigate before approving.
- After `terraform-apply` succeeds, find the `droplet_ip` value in that job's output/logs.
- Step 10's staging secrets are already in place, so the second pause (the `deploy` job) can be approved straight through — no need to stop and go do anything first.

Repeat the same dispatch with **environment: production** once staging is confirmed working end to end, and once step 5 + step 10 are done for production.

---

## 10. Add `.env` values as GitHub Environment secrets — Done for staging, production pending on step 5

**Changed, 2026-07-28**: `.env` is no longer hand-created on the droplet — `deploy.yml`'s `deploy` job now generates `/opt/simpero/.env` on every run, from that environment's GitHub Environment secrets (`staging` or `production`). Add each of these as an Environment secret (same place as `DROPLET_HOST`/`DROPLET_SSH_PRIVATE_KEY` from step 8):

| Secret | Staging value | Production value |
|---|---|---|
| `DATABASE_URL` | dd_app connection string via pgbouncer (see CLAUDE.md for the exact shape) | " |
| `ALEMBIC_DATABASE_URL` | doadmin connection string, direct to the cluster, bypassing pgbouncer | " |
| `VALKEY_URL` | `rediss://default:...@<staging valkey host>:25061?ssl_cert_reqs=none` | production's Valkey |
| `CLERK_SECRET_KEY` | from Clerk dashboard | " |
| `CLERK_JWKS_URL` | `https://<clerk-frontend-api>/.well-known/jwks.json` | " |
| `ENVIRONMENT` | `staging` | `production` |
| `CORS_ALLOWED_ORIGINS` | `https://app-staging.simpero.com` | `https://app.simpero.com` |
| `SIMPERO_PLATFORM_ORG_ID` | Clerk platform-org id | " |
| `APP_BASE_URL` | `https://app-staging.simpero.com` | `https://app.simpero.com` |
| `PARSER_SPACES_BUCKET`, `PARSER_SPACES_REGION`, `PARSER_SPACES_ENDPOINT_URL`, `PARSER_SPACES_ACCESS_KEY_ID`, `PARSER_SPACES_SECRET_ACCESS_KEY` | parser doc-cache Spaces bucket details (separate from the Terraform-state buckets) | " |
| `DD_APP_PASSWORD` | that environment's `dd_app` role password | " |
| `BACKEND_HOSTNAME` | `api-staging.simpero.com` | `api.simpero.com` |
| `PGBOUNCER_DB_HOST` | staging Postgres cluster's private host | production's |
| `PGBOUNCER_DB_PORT` | `25060` | `25060` |
| `PGBOUNCER_DB_NAME` | `simpero` | `simpero` |

That's ~18 secrets per environment, ~36 total. Tedious but one-time — after this, a credential rotation just means updating the one secret, no SSH needed.

**If you add a new required setting later** (a new `.env.example` var), remember it needs updating in **three** places: `.env.example` itself, the `deploy.yml` heredoc's key list (`.github/workflows/deploy.yml`, "Write .env from GitHub secrets" step), and the GitHub Environment secrets for both `staging` and `production` — missing the `deploy.yml` update is the sharper failure mode, since the secret can exist in GitHub and still silently never reach the droplet.

Once all secrets for an environment are set, go back to the pending Actions run and **approve the `deploy` gate** — the `.env` file will be generated fresh as part of that job.

---

## 11. Update the `DROPLET_HOST` secret and add the DNS record

1. Now that the droplet exists and you have its IP (from step 9's `terraform-apply` output), go back to GitHub → the `staging` (or `production`) Environment → edit `DROPLET_HOST` → set it to that IP.
2. At whichever provider actually hosts `simpero.com`'s DNS (not DigitalOcean, per earlier discussion), add an A record:
   - `api-staging.simpero.com` → staging droplet's IP
   - `api.simpero.com` → production droplet's IP
3. Give DNS a few minutes to propagate, then re-run the `deploy` job (or just re-dispatch the workflow with `run_terraform` unchecked) — the health-check step at the end (`curl` against `/health`, `/health/db`, `/health/queue`) should now succeed, since Caddy can finally get a valid TLS cert for the real hostname.

---

## Open items, not blocking, your call whenever

- ~~Droplet size~~ — Done: staging `s-1vcpu-1gb`, production `s-2vcpu-2gb`.
- Restrict SSH (port 22) to your own IP in the firewall, instead of open-to-all-with-key-only-auth.
- A DigitalOcean Reserved IP per droplet, so future droplet recreation doesn't force a DNS + secret update.
- CORS: `app/main.py` needs `https://app.simpero.com` and `https://app-staging.simpero.com` explicitly allow-listed (no wildcard, since the frontend sends `credentials: include`) — separate piece of work, not part of this pipeline.
- ~~What `services.simpero.com` / `services-staging.simpero.com` is for~~ — Resolved: it's the document-parsing/AI service (`Simpero_Gov_AI_Services`), consuming job triggers this repo publishes via the shared Valkey queue — not direct HTTP from a browser, so no CORS change needed here. This directly answers the "confirm with the team" open question in this repo's own `CLAUDE.md` (Document parsing section) about whether `app/jobs/parse_client.py`'s enqueued jobs will ever actually be consumed — worth a closer look at that integration separately from this deployment work.
