# Plan: Deploy to DigitalOcean Droplets via GitHub Actions

Status: staging/production expansion planned, not yet implemented against the two real environments (the original single-droplet version of this pipeline **is** implemented — see §7 for what's already built and what changes under this revision).

## 0. What this covers

**One** manually-triggered (`workflow_dispatch`) GitHub Actions workflow, `deploy.yml`, covering the whole pipeline for **two independent environments — staging and production**, each its own droplet, own Terraform state, own DO Postgres/Valkey cluster:

1. Run the existing test/lint/typecheck/build/audit/contract suite (reused from `ci.yml`, not duplicated).
2. Build the app image, push to GHCR (identical artifact for both environments — environment differences live in each droplet's `.env`, not in the image).
3. Optionally (input toggle) provision/update cloud infrastructure for the selected environment — Terraform plan, then a gated apply — against a DigitalOcean Spaces remote state backend **shared with the frontend repo** (`Simpero_AI_Gov_Web`), one bucket per environment.
4. Deploy the artifact to that environment's droplet — SSH, run migrations, restart the stack.

`.github/workflows/ci.yml` itself is barely touched — it gains a `workflow_call` trigger so `deploy.yml` can reuse its jobs, but its existing `pull_request`/`push` automatic-validation behavior is unchanged.

No Kubernetes, no load balancer, no blue/green, no secrets manager — a brief container restart during deploy is acceptable at this scale, for both environments.

## 1. Architecture decisions

### Remote state: shared Spaces buckets, per-environment prefix

- **State buckets are shared with the frontend repo, not dedicated to this repo.** `simpero-tf-state-staging` and `simpero-tf-state-production` — each bucket holds both repos' state, this repo under a `backend/` prefix (`backend/staging.tfstate`, `backend/production.tfstate`), the frontend repo under its own `frontend/` prefix. **Neither bucket is created or imported by this repo's Terraform** — both are created manually, outside any Terraform state, by Vansh.
- **Accepted risk, stated plainly, not to be rediscovered as a surprise later:** DO Spaces access keys scope to an entire bucket, not a prefix, and DO bucket policies are incompatible with limited-access keys. Any credential with access to these buckets can read/write/delete *both* repos' state — there is no DO-side mechanism to fence the `backend/` and `frontend/` prefixes apart. Vansh has explicitly accepted this trade for one-shared-bucket-per-environment simplicity. **Bucket versioning is the sole recovery mechanism** (leaked credential, bad `terraform destroy`, stray recursive delete from either repo) — must be confirmed enabled on both buckets before either is used for a real `apply` (see §5, currently unconfirmed).
- **No bucket policy, ever, on either bucket** — applying one permanently prevents creating limited-access keys for that bucket (and vice versa: an existing limited-access key blocks adding a policy later). **No lifecycle expiration rules either** — since versioning is the only recovery path, expiring old object versions would silently destroy that recovery path.
- **Partial backend config, resolved per environment at `terraform init` time** via `-backend-config=terraform/backend-<environment>.hcl`. `versions.tf` holds only the flags that don't vary by environment; bucket/key/region/endpoint live in `backend-staging.hcl`/`backend-production.hcl`.
- **Corrected S3-backend arguments for the current Terraform version** (verified against HashiCorp's current docs — the previously-implemented `versions.tf` had two real gaps, not just style issues):
  - `endpoints = { s3 = "..." }` (an object) — **not** the old top-level `endpoint` string argument, which is deprecated. The currently-implemented file uses the deprecated form; fix this regardless of the environment split.
  - `skip_s3_checksum = true` — needed for DO Spaces compatibility, and **missing entirely** from the currently-implemented file (a real gap, not a naming fix).
  - `skip_credentials_validation`, `skip_region_validation`, `skip_requesting_account_id`, `skip_metadata_api_check` — all current, all required.
  - `use_path_style = true` — current name (not the deprecated `force_path_style`); already correct in the implemented file.
  - **`use_lockfile = true`** (new) — Terraform's native S3-backend state locking, GA since 1.11. Worth adding now: the same Spaces key already needs write access for the lock object anyway, and two repos now share these buckets, so real locking closes a gap the single `concurrency:` group can't (it doesn't protect against someone running Terraform locally in an emergency).
  - **`required_version = ">= 1.11.0"`** (bumped from `>= 1.5`, needed for the `endpoints` block and GA locking), and **`hashicorp/setup-terraform@v3`'s `terraform_version` input pinned to an exact patch** — not left to resolve "latest." A shared-state setup makes an unplanned Terraform version drift across two repos' pipelines meaningfully more consequential than it was for one dedicated dev bucket.
  - Because `use_lockfile` writes a lock object during `plan`, **the Spaces key used by `terraform-plan` cannot be read-only**, even though the separate DO API token used for `plan` legitimately can be.

### Two environments, one Terraform config

- **`main.tf` is parameterized by a new `environment` variable** (`"staging"` | `"production"`, validated), threaded into every DO resource's name (`digitalocean_droplet.app`, `digitalocean_ssh_key.deploy`, `digitalocean_firewall.app` all get a `-${var.environment}` suffix) — required, not cosmetic, since DO resource names must stay unique account-wide across both droplets.
- **Separate `.tfvars` per environment** (`staging.tfvars`, `production.tfvars`) carrying `region`, `droplet_size`, `postgres_cluster_id`, `valkey_cluster_id` — kept separate per environment regardless of whether the underlying values ever coincide, so the design doesn't hardcode an assumption either way.
- **Separate Postgres and Valkey clusters per environment — confirmed, not shared.** A shared cluster would both (a) break the current `digitalocean_database_firewall` design, since each environment's independent Terraform state would each own an authoritative, full-replace firewall resource for the *same* cluster — every apply in either environment would silently un-trust the other's droplet — and (b) undermine this app's own tenant-isolation premise: RLS isolates customer orgs from each other, not staging from production, so a shared cluster would give a staging migration or bad query a live path to production data with nothing in the codebase defending against it. No Terraform rework is needed as a result of this decision — the per-environment `.tfvars` design already accommodates separate cluster IDs with zero extra code.
- **Separate deploy SSH keypairs per environment, not one shared keypair.** Two independent Terraform states each registering the same public key as a `digitalocean_ssh_key` resource risks a DO-side fingerprint collision on whichever `apply` runs second (DO dedupes SSH keys by key material, not just name).
- **`environment`'s value flows into Terraform as `TF_VAR_environment` from the workflow's `inputs.environment`** — not duplicated inside the `.tfvars` files, one source of truth.
- **No "dev" environment retained.** The original single-droplet plan's "dev" terminology is retired in favor of the two real, named environments given directly by Vansh (`api.simpero.com` / `api-staging.simpero.com`).

### Droplet-level setup (unchanged from the original plan, applies identically to both environments)

- **Droplet image:** DO's "Docker on Ubuntu" marketplace image (`docker-20-04` — the slug has stayed this even as DO updated the underlying base to Ubuntu 22.04; verified against DO's live marketplace listing, not guessed).
- **`user_data` (cloud-init)** creates the `deploy` user, installs that environment's deploy public key, `mkdir -p /opt/simpero`, then **last** disables SSH password auth (`ssh_pwauth: false`). `digitalocean_droplet.ssh_keys` (a separate droplet-creation argument) is the real fallback login path if the cloud-init user-creation step fails partway.
- **`digitalocean_firewall` needs explicit `outbound_rule` blocks, not just inbound** — DO Cloud Firewalls deny-by-default in both directions. Allow-all egress is fine at this scale.
- **`digitalocean_database_firewall` is authoritative/full-replace**, not additive. Existing trusted sources on both clusters must be checked by hand (`doctl databases firewalls list <cluster-id>`) before each environment's first real apply. Rules use `type = "droplet"` (self-healing if the droplet is ever replaced).
- **Any edit to `user_data`/`cloud-init.yaml.tpl` forces `digitalocean_droplet` replacement** — new IP, total loss of that droplet's `/opt/simpero` including its hand-maintained `.env`. Back up `.env` before any apply touching the droplet resource, in either environment, independently.

### Deployment (app image + SSH), and the two-environment reshape of `deploy.yml`

- **`docker-publish` needs no change for the environment split** — same image, same tag scheme (`github.sha`), deployed to whichever droplet the dispatch targets. Environment differences live entirely in each droplet's own `.env` and now its `Caddyfile`/`docker-compose.prod.yml` env vars (below), not in the artifact. Deliberate, not an oversight.
- **New `workflow_dispatch` input: `environment`** (`choice`: `staging` | `production`, **default `staging`** — biases the "forgot to pick" case toward the lower-blast-radius target). The existing `run_terraform` boolean input is unchanged.
- **`terraform-plan`/`terraform-apply` select the right backend/vars file by string-interpolating `inputs.environment`** (`-backend-config=terraform/backend-${{ inputs.environment }}.hcl`, `-var-file=terraform/${{ inputs.environment }}.tfvars`) — safe, since `inputs.environment` is constrained to the two declared `choice` values before the run starts, no injection surface.
- **`concurrency:` group is now per-environment**, not one blanket group for the whole file: `group: deploy-${{ github.workflow }}-${{ inputs.environment }}`. A blanket group would needlessly serialize an unrelated staging deploy behind an in-flight production one (different state files, different droplets — no shared resource to protect).
- **GitHub Actions gotcha, verified — dynamic `environment:` needs the object form.** `environment: ${{ inputs.environment }}` (string shorthand) does not reliably resolve a dynamic value in that position; must use `environment:\n  name: ${{ inputs.environment }}` (and the `-plan` variants below). Easy to miss, called out explicitly for whoever implements this.
- **The `deploy` job's skip-tolerance logic from the original design is unchanged and doesn't need re-verification**: `if: always() && needs.docker-publish.result == 'success' && (needs.terraform-apply.result == 'success' || needs.terraform-apply.result == 'skipped')` — the added `environment` dimension only affects which backend/vars/secrets get selected upstream, it doesn't touch this condition at all.
- **No `matrix:`** — one dispatch targets exactly one environment by design; a matrix would imply "run both every time," which isn't the intended routine behavior (you deploy staging, then separately, deliberately, production).

### Secrets and GitHub Environments — 4 Environments, not 2 (a real gap, not just tidiness)

The naive design — one GitHub Environment per deployment target (`staging`, `production`), each gated with a required reviewer, holding that environment's secrets — breaks `terraform-plan`: **any job that references a gated Environment inherits its approval gate**, and `terraform-plan` needs environment-specific credentials (the Spaces key, to read/lock the right state file) while needing to stay **ungated** — that's the entire point of reviewing a plan before it's approved. Fix: **four** Environments.

| Environment | Gated (required reviewer)? | Holds | Referenced by |
|---|---|---|---|
| `staging-plan` | No | `TF_VAR_ssh_public_key`, `SPACES_ACCESS_KEY_ID`, `SPACES_SECRET_ACCESS_KEY` (staging bucket grant) | `terraform-plan` (staging) |
| `production-plan` | No | Same shape, production bucket grant | `terraform-plan` (production) |
| `staging` | Yes (Vansh) | Same Spaces/SSH-key values as `staging-plan` (applying the same plan needs the same backend/provider config — reference, don't duplicate into a second copy that could drift) + `DROPLET_HOST`, `DROPLET_SSH_PRIVATE_KEY` (staging droplet) | `terraform-apply` (staging), `deploy` (staging) |
| `production` | Yes (Vansh) | Same shape as `staging`, production values | `terraform-apply` (production), `deploy` (production) |

**Repo-level secrets** (not Environment-scoped — genuinely identical regardless of target environment):
- `TF_VAR_do_token` — one DO account, one token.

**Dropped entirely as a secret:** `DROPLET_SSH_USER` — it's the literal `deploy` in both environments, not sensitive; hardcode it in the workflow instead of keeping a non-secret value in sync across 4 places. **Also `GHCR_PAT`** (2026-07-28 revision): originally a classic PAT (bot account vs. personal account considered), then a GitHub App — but GHCR does not accept GitHub App installation tokens, a confirmed, current platform limitation (`docker login` succeeds, `docker pull` is denied). Replaced with the workflow's own built-in `GITHUB_TOKEN` (`permissions: packages: read` added to the `deploy` job) — GHCR does accept this, and `docker-publish` already proves the pattern works for the push side. No bot account, no App, no extra secret to create or rotate, ever.

**Two separate approval prompts per environment, per run** (confirmed in the original single-environment design and unchanged in principle): `terraform-apply` and `deploy` referencing the same Environment (`staging` or `production`) produces two distinct pauses, since GitHub evaluates Environment protection per job, not deduplicated per Environment per run.

### Caddy and pgbouncer — one shared file each, parameterized, not duplicated per environment

- **`Caddyfile` stays a single file**, using Caddy's native env-var interpolation in the site-address position (`{$VAR}`, resolved at Caddyfile-parse time — verified to work specifically here, unlike the runtime `{env.VAR}` placeholder which doesn't apply in this position):
  ```
  {$BACKEND_HOSTNAME} {
      reverse_proxy app:8000
  }
  ```
  `BACKEND_HOSTNAME` is set in each droplet's own hand-maintained `.env` — `api.simpero.com` (production) or `api-staging.simpero.com` (staging) — same mechanism as everything else that legitimately differs per droplet. No new workflow logic needed to select a file.
- **`docker-compose.prod.yml`'s `pgbouncer` service currently hardcodes the Postgres cluster hostname inline** in its entrypoint heredoc — a real bug once staging and production use separate clusters (confirmed above), not just an environment-split nicety. Fix: parameterize `host`/`port`/`dbname` from `.env` (`PGBOUNCER_DB_HOST`, `PGBOUNCER_DB_PORT`, `PGBOUNCER_DB_NAME`), same pattern as the Caddy fix. One shared `docker-compose.prod.yml` then works correctly for both environments.
- **No repo checkout on either droplet.** Only `docker-compose.prod.yml`, `Caddyfile`, and `.env` live in `/opt/simpero/` — unchanged from the original design, now true independently for both droplets.
- **Revision, 2026-07-28: `.env` is now generated by the `deploy` job on every deploy, from that environment's GitHub Environment secrets — reversing the original "hand-maintained, never touched by any workflow" decision.** Vansh's explicit call: all `.env` values live as GitHub Environment secrets (`staging`/`production`), and the `deploy` job writes `/opt/simpero/.env` via a quoted heredoc over SSH (`cat > .env <<'SIMPERO_ENV_EOF' ... SIMPERO_ENV_EOF`, `chmod 600`) immediately after copying the compose file and Caddyfile, before pull/migrate/restart. The original reasoning against this (multi-line-blob secret masking, all-credentials-touched-on-every-rotation) is superseded, not wrong in isolation — Vansh has weighed that trade differently now that per-environment GitHub Environment secrets (already needed for `DROPLET_HOST`/`DROPLET_SSH_PRIVATE_KEY`/etc.) are the established pattern here, and one more per-key `.env` var added to that same list is a smaller marginal cost than maintaining a second, out-of-band file by hand per droplet. GitHub's automatic secret masking in logs still applies per-value (each `secrets.X` reference is individually known and masked), so this doesn't reintroduce a "one giant blob" masking problem — it's N individually-masked values, not one.
- **Consequence: `.env` on the droplet is now fully disposable** — it's overwritten on every `deploy` run, so the GitHub secrets are the single source of truth, not the file on disk. No more "back up `.env` before a droplet replacement" step (§6) — losing the droplet loses nothing that isn't already sitting in GitHub Environment secrets.
- **Migrations run on the droplet**, not from the Actions runner — unchanged, applies per-environment.
- **Image tagged by commit SHA** — unchanged, same tag used for both environments' deploys of that commit.

### Informational, not actioned in this plan (flagged, not guessed at)

- Vansh referred to `api.simpero.com`/`api-staging.simpero.com` as "App Platform hostnames" (DigitalOcean App Platform, a PaaS — a different deployment mechanism than this droplet-based pipeline). Read as informational/org-wide naming context, not a signal to abandon the droplet approach — his concrete instructions this round were unambiguously about restructuring Terraform state for droplet infrastructure, and a full platform switch would make the detailed cloud-init/firewall/Caddy work pointless, which he'd say explicitly rather than leave to inference. **Worth confirming directly with Vansh if this ever seems contradictory again.**
- **CORS**: `app/main.py`'s CORSMiddleware needs `https://app.simpero.com` and `https://app-staging.simpero.com` explicitly allow-listed (no wildcard — frontend uses `credentials: include`). This is a FastAPI app-code concern, not Terraform/`deploy.yml` — explicitly out of scope for this plan, flagged here so it isn't lost.
- **`services.simpero.com` / `services-staging.simpero.com`** — resolved (2026-07-28): the document-parsing/AI service (`Simpero_Gov_AI_Services`), consuming job triggers this repo publishes via the shared Valkey queue rather than direct HTTP — this is what `CLAUDE.md`'s "Document parsing" section calls confirming whether `app/jobs/parse_client.py`'s enqueued jobs will ever be consumed. No CORS or Caddy-routing implication for this repo's deployment, since it's queue-based, not browser-facing.

## 2. File layout

```
terraform/
  versions.tf             # required_version >= 1.11.0; partial backend "s3" block —
                           # only skip_*, use_path_style, use_lockfile. No bucket/key/
                           # region/endpoints here — those are per-environment now.
  variables.tf             # + `environment` (string, validated: staging | production)
                           # do_token, ssh_public_key, region, droplet_size,
                           # postgres_cluster_id, valkey_cluster_id — unchanged in shape
  main.tf                  # every DO resource name suffixed "-${var.environment}"
                           # digitalocean_database_firewall x2 unchanged in shape
                           # (safe because clusters are confirmed separate per environment)
  outputs.tf               # droplet_ip — unchanged, no suffix needed
  cloud-init.yaml.tpl       # unchanged — identical template for both environments,
                           # differs only in which ssh_public_key is interpolated at apply
  backend-staging.hcl       # bucket = simpero-tf-state-staging, key = backend/staging.tfstate,
  backend-production.hcl    # bucket = simpero-tf-state-production, key = backend/production.tfstate
                           # both: region + endpoints.s3 = actual Spaces region (§5 — unconfirmed)
  staging.tfvars            # region, droplet_size, postgres_cluster_id, valkey_cluster_id
  production.tfvars         # same shape, production's own values
```

`.github/workflows/ci.yml` — one addition: `workflow_call:` in the existing `on:` block, nothing else changed.

`.github/workflows/deploy.yml` — reshaped per §1/§4: new `environment` choice input, per-environment backend/vars-file selection, per-environment `concurrency:` group, 4-Environment secrets wiring.

`Caddyfile`, `docker-compose.prod.yml` — both stay single, shared files, parameterized via `.env` per §1's "Caddy and pgbouncer" section.

## 3. GitHub repo configuration required

**Repo-level secrets:**

| Secret | Holds |
|---|---|
| `TF_VAR_do_token` | DO API token |

(No GHCR credential secret — the `deploy` job's built-in `GITHUB_TOKEN`, scoped via `permissions: packages: read`, authenticates the droplet's pull. See §1.)

**Environment-scoped secrets** — see §1's table for the full 4-Environment breakdown (`staging-plan`, `production-plan` ungated; `staging`, `production` gated with Vansh as required reviewer).

**`.env` contents are now GitHub Environment secrets too** (per the revision above) — one set per environment (`staging`, `production`), in addition to `DROPLET_HOST`/`DROPLET_SSH_PRIVATE_KEY`: `DATABASE_URL`, `ALEMBIC_DATABASE_URL`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `VALKEY_URL`, `ENVIRONMENT`, `CORS_ALLOWED_ORIGINS`, `SIMPERO_PLATFORM_ORG_ID`, `APP_BASE_URL`, `PARSER_SPACES_BUCKET`, `PARSER_SPACES_REGION`, `PARSER_SPACES_ENDPOINT_URL`, `PARSER_SPACES_ACCESS_KEY_ID`, `PARSER_SPACES_SECRET_ACCESS_KEY`, `DD_APP_PASSWORD`, `BACKEND_HOSTNAME`, `PGBOUNCER_DB_HOST`, `PGBOUNCER_DB_PORT`, `PGBOUNCER_DB_NAME` (mirrors `.env.example` plus the deploy-specific vars introduced for the Caddy/pgbouncer parameterization). All plain Environment secrets, not split into GitHub's separate non-secret "Variables" — simplest option, consistent with how `DROPLET_HOST` etc. are already handled, even though several of these (e.g. `BACKEND_HOSTNAME`, `PGBOUNCER_DB_PORT`) aren't actually sensitive.

## 4. Workflow behavior

**`.github/workflows/deploy.yml`** — inputs: `environment` (`choice`: `staging` | `production`, default `staging`), `run_terraform` (boolean, default `false`). `concurrency:` group per environment.

```
job ci:
  uses: ./.github/workflows/ci.yml     # reusable call, same for both environments

job docker-publish (needs: ci):
  checkout @ github.sha
  build + push ghcr.io/<owner>/simpero-ai-gov-alpha:${{ github.sha }}, :latest
  # No environment-specific logic — same artifact for both.

job terraform-plan (needs: ci; if: inputs.run_terraform; environment: { name: '${{ inputs.environment }}-plan' }):
  terraform init -backend-config=terraform/backend-${{ inputs.environment }}.hcl
  terraform plan -var-file=terraform/${{ inputs.environment }}.tfvars -out=tfplan
  upload tfplan as artifact (name includes environment, to avoid collision if ever run concurrently)

job terraform-apply (needs: terraform-plan; if: inputs.run_terraform; environment: { name: '${{ inputs.environment }}' }):
  download tfplan artifact
  terraform init -backend-config=terraform/backend-${{ inputs.environment }}.hcl
  terraform apply tfplan   # the exact downloaded plan, never a fresh one

job deploy (needs: [docker-publish, terraform-apply]; environment: { name: '${{ inputs.environment }}' }):
  if: always() && needs.docker-publish.result == 'success'
      && (needs.terraform-apply.result == 'success' || needs.terraform-apply.result == 'skipped')
  scp docker-compose.prod.yml + Caddyfile → /opt/simpero/  (that environment's droplet, via DROPLET_HOST)
  ssh (user: literal "deploy", not a secret):
    docker login ghcr.io (GITHUB_TOKEN, via `packages: read` on this job — no GHCR_PAT)
    IMAGE_TAG=${{ github.sha }} docker compose -f docker-compose.prod.yml pull
    IMAGE_TAG=${{ github.sha }} docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head
    IMAGE_TAG=${{ github.sha }} docker compose -f docker-compose.prod.yml up -d
  curl --fail --retry 5 against /health, /health/db, /health/queue (from the runner, against that environment's droplet)
```

Failure fails the job visibly; no auto-rollback. Two separate approval pauses occur when `run_terraform` is true (`terraform-apply` then `deploy`, both against the same gated Environment); one pause (`deploy` only) when it's false.

## 5. Dependencies from Vansh's side

> **This is the section that blocks everything else.** Nothing in §4 can run end-to-end until all of these are done.

**Blocking, new this round:**
1. **Create the `simpero-tf-state-production` bucket** (same process as the already-created `simpero-tf-state-staging`).
2. **Confirm the actual Spaces region for both buckets.** The previous plan assumed `tor1` for a now-superseded dedicated bucket — the shared buckets' real region isn't stated anywhere yet, and it directly determines `region`/`endpoints.s3` in both `backend-*.hcl` files. Don't assume `tor1` carries over.
3. **Confirm object versioning is enabled on both buckets before either environment's first real `apply`.** This is the sole recovery mechanism for the shared-bucket risk (§1) — treat as a literal go/no-go gate, not an assumption. *(Status as of this writing: unconfirmed — Vansh to check and confirm.)*
4. **Get the shared Spaces credential(s) from whoever manages the frontend repo's keys** — one existing-or-added grant on `simpero-tf-state-staging` (readwrite+delete — confirm it already has this grant, or ask for it), and one **new** grant on `simpero-tf-state-production` (must be new — bucket grants are fixed at creation and the bucket doesn't exist yet).
5. **Generate a second (production) deploy SSH keypair** — distinct from staging's, per §1's collision-avoidance reasoning.
6. **Create the 4 GitHub Environments** (`staging-plan`, `production-plan`, `staging`, `production`) and their secrets per §3, with Vansh as required reviewer on the two gated ones.
7. **Provision separate Postgres and Valkey clusters for staging and production** (confirmed decision, §1) — note their cluster IDs for `staging.tfvars`/`production.tfvars`.
8. **Audit existing trusted sources on all DB clusters** (`doctl databases firewalls list <cluster-id>`) before each environment's first real apply — `digitalocean_database_firewall` is full-replace.

**Unchanged from the original plan, still needed:**
9. Add a DNS A record per environment (`api.simpero.com` → production droplet IP, `api-staging.simpero.com` → staging droplet IP) once each `terraform-apply` succeeds and its `droplet_ip` output is known.
10. **Superseded by the 2026-07-28 revision** — `BACKEND_HOSTNAME` is no longer hand-edited into a droplet file; it's one of the GitHub Environment secrets in item 11.
11. **Add all ~18 `.env` values as GitHub Environment secrets**, per environment (`staging`, `production`) — see §3's full list. The `deploy` job generates `/opt/simpero/.env` from these on every run; nothing is hand-created on the droplet anymore. Get these right *before* the first real dispatch with `run_terraform` unchecked — an incomplete set means the app crash-loops against a partially-empty generated `.env`, same failure mode as before, just a different place to fix it (GitHub secrets, not SSH).

**Open, not blocking, decide when convenient:**
- Droplet size per environment — production may warrant larger than staging; both currently default to `s-2vcpu-2gb` unless overridden per `.tfvars`.
- Restrict SSH (port 22) to Vansh's own IP vs. open-to-all-with-key-only-auth, per environment.
- Reserved IP per droplet — decouples DNS/secrets from any future droplet recreation.
- CORS allow-list update in `app/main.py` for `app.simpero.com`/`app-staging.simpero.com` (flagged in §1, separate pass).
- ~~`services.simpero.com`/`services-staging.simpero.com` purpose~~ — resolved, see §1.

## 6. Known risks

- **Shared-bucket blast radius**: any Spaces credential touching either bucket can read/write/delete both repos' state. Versioning is the only recovery mechanism — see §5 item 3, currently unconfirmed.
- **Droplet replacement breaks DNS + `DROPLET_HOST`**, independently per environment — any `user_data` change forces replacement, expect to redo DNS + the relevant Environment's `DROPLET_HOST` secret afterward. `.env` itself is no longer a casualty of this (2026-07-28 revision) — it's regenerated from GitHub secrets on the next `deploy` run regardless.
- **`digitalocean_database_firewall` full-replace semantics** — mitigated by the manual audit in §5 item 8, not by anything automatic. Now applies twice (once per environment's cluster).
- **DO API token in a repo-level secret is real account-level power**, shared across both environments — reachable by anyone who can both dispatch the workflow (`run_terraform: true`) and clear the relevant gated Environment. Document who has both.
- **`.env` drift, reshaped by the 2026-07-28 revision, not eliminated**: a new required var landing in `.env.example` still won't automatically reach either environment — now it means updating the `deploy.yml` heredoc's key list *and* adding the corresponding secret to both `staging` and `production` Environments, three places instead of one file per droplet. Forgetting the `deploy.yml` heredoc update is a new, sharper failure mode than before: the secret can exist in GitHub and still never reach the droplet if the heredoc doesn't reference it. Manual checklist item, same spirit as before, one more place to touch.
- **Secrets sprawl**: ~18 `.env` values now live as GitHub Environment secrets per environment (~36 total across staging+production), on top of the deploy/Terraform secrets already there. Anyone who can read a gated Environment's secrets (via a workflow run they can trigger, or repo admin access) can see everything the app needs to run, including `CLERK_SECRET_KEY` and DB credentials — no worse than the credentials already being deployment-time secrets, but a larger single blast radius than a per-droplet file that only existed on that one machine.
- **GHCR PAT permissions** are asserted, not verified — treat the first deploy (either environment) as the real test.
- **SSH open to all sources** in the default `digitalocean_firewall` rule (key-only auth) unless restricted per environment, per §5's open call.
- **No state locking prior to `use_lockfile`** — now addressed (§1) by adding native S3-backend locking, given two repos now share these buckets; previously only a single `concurrency:` group protected against overlapping CI runs, which didn't cover a local emergency `terraform` invocation.
- **Four-Environment secrets surface** is more moving parts than the original single-environment design's one `infra-apply` gate — necessary specifically to keep `terraform-plan` ungated while still scoping credentials per environment (§1). Don't collapse this back to 2 Environments without re-solving the plan-gating problem it exists to avoid.

## 7. What's already implemented

The staging/production revision described in this document **is implemented**: `.github/workflows/ci.yml` (`workflow_call` trigger added), `.github/workflows/deploy.yml` (full staging/production job graph), `terraform/` (per-environment `.tfvars`/`backend-*.hcl`, `main.tf` resource-name suffixing, `cloud-init.yaml.tpl`), `docker-compose.prod.yml`, `Caddyfile` — all at repo root (moved out of a `deploy/` subdirectory, 2026-07-28). Staging is in active use; production's DB clusters remain deliberately deferred (see `docs/PENDING_ON_VANSH.md` §5).

## 8. `destroy.yml` — deliberate, separate teardown workflow (2026-07-29)

A `workflow_dispatch`-only workflow, `.github/workflows/destroy.yml`, added after staging's first droplet needed a clean recreation to test a cloud-init fix (see below).

**Root cause correction, 2026-07-28:** the droplet's cloud-init consistently failed with `Failed loading yaml blob. unacceptable character #x0080 ... position 121`, leaving the `deploy` user never created. Initially misdiagnosed as a corrupted `TF_VAR_ssh_public_key` secret value (terminal/clipboard corruption) — `gh secret set < file` was used to rule that out, but the *identical* error at the *identical* byte position recurred on a completely fresh keypair and droplet, disproving that theory. The actual cause: `terraform/cloud-init.yaml.tpl`'s own comments contained an em dash (`—`, UTF-8 bytes `E2 80 94`) — byte offset 121 is exactly the middle byte of that sequence. Whatever parses this file as `user_data` on the droplet side isn't UTF-8-aware and choked on that byte read in isolation. This bug was present from the very first droplet and would have hit every subsequent one regardless of key correctness. Fixed by removing the non-ASCII character from the template (confirmed via a byte-level scan that the file is now pure ASCII, and via a rendered-template YAML parse test). Lesson: cloud-init `user_data` templates in this repo must stay pure ASCII — no em dashes, smart quotes, or other typographic characters, even in comments.

**Second cloud-init bug found once the first was fixed, 2026-07-28:** with the em-dash issue resolved, `deploy.yml`'s SCP step started failing with `tar: docker-compose.prod.yml: Cannot open: Permission denied`. Cause: `cloud-init.yaml.tpl`'s `runcmd` ran `mkdir -p /opt/simpero` as root (runcmd's default execution context) but never `chown`'d it — the `deploy` user had no write access to a directory it needed to own. Never surfaced earlier because every prior droplet failed before reaching this step. Fixed by adding `chown deploy:deploy /opt/simpero` immediately after the `mkdir` in `runcmd`.

**Health check bug found and fixed, 2026-07-28:** the healthcheck step curled the bare `DROPLET_HOST` IP directly, which was never going to work regardless of DNS - confirmed against Caddy's own docs, a site block matches purely on the `Host` header / TLS SNI, and a request to the bare IP sends `Host: <ip>`, not the configured hostname, so Caddy would 404 it every time. The original plan doc/comments attributed this step's expected failure solely to DNS not being live yet; that was incomplete - it was structurally broken independent of DNS too. Fixed with `curl --resolve BACKEND_HOSTNAME:443:DROPLET_HOST` (and `:80`), which pins the hostname to the droplet's IP for that one request, forcing the correct Host header and SNI without needing real DNS to resolve at curl's connection time. This step still needs DNS to have been live at some point before it can pass, though: Caddy can only present a valid cert if Let's Encrypt's own validation request (made over the real internet, unaffected by `--resolve`) already succeeded - so it still fails until DNS + a first successful cert issuance have happened, just for the right reason now (a TLS/cert error, not a silent 404).

**Swap + UFW rate-limit fixes, 2026-07-28:** with the deploy user and permissions now working, `deploy.yml`'s SCP/SSH steps started intermittently failing with `dial tcp ***:22: i/o timeout`, consistently *after* the actual file transfer had already succeeded (folder created, tarball extracted). Two contributing causes investigated and addressed:
- Added a 1G swapfile to `cloud-init.yaml.tpl` (small droplets like `s-1vcpu-1gb` ship with zero swap) as a precaution against OOM kills during the deploy-time memory spike (Docker pull + migration + container restart, all during an active SSH session) - checked via `sudo dmesg | grep -i "killed process\|out of memory"` on a live failure and found no OOM evidence that time, so this wasn't confirmed as the actual cause, but it's cheap, standard practice, and kept regardless.
- **Confirmed actual cause**: the marketplace image's default UFW config has `22/tcp LIMIT IN` - rate-limits to roughly 6 connections per 30s per source IP. `appleboy/scp-action`'s underlying `drone-scp` opens a *new* SSH connection for each operation (detect OS, create folder, untar, cleanup) in quick succession, tripping this limit; later connections get silently dropped (not rejected - dropped, which is exactly what produces "i/o timeout" rather than a clean auth/refused error). Fixed in `cloud-init.yaml.tpl`'s `runcmd`: `ufw delete limit 22/tcp` + `ufw allow 22/tcp` (syntax confirmed against DigitalOcean's own UFW tutorial). This local rate limit wasn't adding meaningful protection anyway - real access control already happens one layer out, at the DO cloud firewall (`digitalocean_firewall.app`), which UFW sits behind.
- **Bonus hardening while touching UFW**: the same marketplace image also leaves the Docker remote API ports (2375/2376) open (`ALLOW IN Anywhere`) in UFW by default. Not currently reachable in practice (the DO cloud firewall only allows 22/80/443 inbound, sitting in front of UFW), but closed them locally too (`ufw delete allow 2375/tcp` / `2376/tcp`) as cheap defense-in-depth against an unauthenticated Docker API (root-equivalent RCE) ever being exposed by a future firewall misconfiguration.

- **Kept entirely separate from `deploy.yml`**, per §1's original decision that `destroy` shouldn't be a toggle in the routine deploy button.
- **Mirrors `terraform-plan`/`terraform-apply`'s plan-then-gated-apply pattern exactly**: an ungated `terraform-destroy-plan` job (`${{ inputs.environment }}-plan` Environment, runs `terraform plan -destroy`, uploads the plan), then a gated `terraform-destroy-apply` job (`${{ inputs.environment }}` Environment, required reviewer, applies the *exact* downloaded destroy plan).
- **Extra safety**: a required `confirm_environment` text input that must exactly match the `environment` dropdown selection, checked in `terraform-destroy-plan`'s first step — fails loudly (red job, clear error) rather than silently skipping, so a typo doesn't quietly proceed.
- **No new secrets or Environments** — reuses everything `deploy.yml` already has.
- **Shared `concurrency:` group with `deploy.yml`** (`infra-${{ inputs.environment }}`, not `github.workflow`-keyed, which would differ per file) — a deploy and a destroy against the same environment can never race each other, on top of Terraform's own `use_lockfile` state locking.
- **Known gap, not automated**: after a destroy, that environment's `DROPLET_HOST` secret and DNS record point at a droplet that no longer exists. Nothing in this pipeline holds the broad repo-admin credentials needed to auto-clear a GitHub secret (deliberately — same narrow-credential posture as everywhere else in this setup), so this is a manual follow-up after the next successful recreate.

## 9. DigitalOcean Projects — assign each environment's droplet (2026-07-29)

Each environment's droplet is assigned into a pre-existing DO Project (`terraform/main.tf`: `data "digitalocean_project"` lookup by name + `digitalocean_project_resources` resource), shared across the frontend, backend (this repo), and services repos — each assigns its own resources into the same project, same sharing pattern as the Spaces state buckets.

- **Not created by this repo's Terraform** — looked up by name via a data source, same reasoning as the shared Spaces buckets: avoids ownership conflicts if another repo's Terraform also touches the same project.
- **Only the droplet is assignable, confirmed against DO's own OpenAPI spec.** DO Projects support exactly 9 resource types: App Platform App, Database, Domain, Droplet, Floating IP, Kubernetes Cluster, Load Balancer, Space, Volume. **Neither Firewalls nor SSH keys are on that list** — `digitalocean_firewall.app` and `digitalocean_ssh_key.deploy` simply cannot be assigned to a project at all, regardless of intent; they stay account-wide. Not a limitation of this implementation — a hard constraint of the DO API itself.
- **New required variable**: `do_project_name`, per-environment `.tfvars` (`staging.tfvars`: `"staging"`, `production.tfvars`: `"prod"`) — must exactly match the DO Project's actual name (case-sensitive), which Vansh creates/confirms directly in the DO console. No default, same posture as `postgres_cluster_id`/`valkey_cluster_id`.
- Uses the droplet resource's own computed `urn` attribute (`digitalocean_droplet.app.urn`) rather than constructing the `do:droplet:<id>` URN string by hand — avoids drift if DO ever changes the URN format.
