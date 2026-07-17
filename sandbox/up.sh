#!/usr/bin/env bash
# Bring up the local sandbox: Postgres + Valkey, then apply migrations.
# Idempotent -- safe to re-run. Removes the DigitalOcean cluster from the loop
# entirely; nothing here touches the real database.
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SANDBOX_DIR/.." && pwd)"
COMPOSE=(docker compose -f "$SANDBOX_DIR/docker-compose.yml")

step() { printf '\n\033[1;36m[%s]\033[0m %s\n' "$1" "$2"; }
ok()   { printf '\033[1;32m      ✓ %s\033[0m\n' "$1"; }

printf '\033[1m========================================================================\n'
printf '  Simpero local sandbox — bringing up local Postgres + Valkey\n'
printf '========================================================================\033[0m\n'

step "1/3" "Checking prerequisites"
command -v docker >/dev/null || { echo "error: docker is required (https://docs.docker.com/get-docker/)"; exit 1; }
command -v uv     >/dev/null || { echo "error: uv is required (https://docs.astral.sh/uv/)"; exit 1; }
docker info >/dev/null 2>&1  || { echo "error: the Docker daemon is not running (start Docker Desktop, or run 'colima start')"; exit 1; }
ok "docker and uv found, daemon reachable"

step "2/3" "Starting containers (postgres:16, valkey:8)"
echo "      first run downloads ~150 MB of images, so this can take a minute..."
"${COMPOSE[@]}" up -d --wait
ok "postgres → localhost:5433   (healthy)"
ok "valkey   → localhost:6380   (healthy)"

step "3/3" "Applying database migrations (as doadmin)"
echo "      creates the organisation / funds / claims tables, RLS policies, and grants"
set -a
# shellcheck disable=SC1091
. "$SANDBOX_DIR/.env.sandbox"
set +a
( cd "$BACKEND_DIR" && uv run alembic upgrade head )
REV="$( cd "$BACKEND_DIR" && uv run alembic current 2>/dev/null | grep -v '^INFO' | head -1 )"
ok "schema at ${REV:-head}"

printf '\n\033[1;32m========================================================================\n'
printf '  Sandbox is up.\n'
printf '========================================================================\033[0m\n'
echo "    Postgres : localhost:5433   (db simpero, roles doadmin / dd_app)"
echo "    Valkey   : localhost:6380"
echo
echo "    Next:  ./sandbox/run.sh /path/to/your-cim.pdf"
echo "    Stop:  ./sandbox/down.sh          (add --wipe to delete the data)"
