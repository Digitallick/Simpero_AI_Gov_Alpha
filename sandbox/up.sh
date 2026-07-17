#!/usr/bin/env bash
# Bring up the local sandbox: Postgres + Valkey, then apply migrations.
# Idempotent -- safe to re-run. Removes the DigitalOcean cluster from the loop
# entirely; nothing here touches the real database.
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SANDBOX_DIR/.." && pwd)"

command -v docker >/dev/null || { echo "error: docker is required (https://docs.docker.com/get-docker/)"; exit 1; }
command -v uv >/dev/null || { echo "error: uv is required (https://docs.astral.sh/uv/)"; exit 1; }

echo "==> starting Postgres + Valkey"
docker compose -f "$SANDBOX_DIR/docker-compose.yml" up -d --wait

echo "==> applying migrations (as doadmin)"
# --wait above blocks until the healthchecks pass, so Postgres is ready. Load
# the sandbox env and run the backend's own alembic.
set -a
# shellcheck disable=SC1091
. "$SANDBOX_DIR/.env.sandbox"
set +a
( cd "$BACKEND_DIR" && uv run alembic upgrade head )

echo
echo "==> sandbox is up."
echo "    Postgres : localhost:5433  (db simpero, roles doadmin / dd_app)"
echo "    Valkey   : localhost:6380"
echo
echo "    Run the pipeline:  ./sandbox/run.sh /path/to/your-cim.pdf"
echo "    Tear down:         ./sandbox/down.sh   (add --wipe to delete the data)"
