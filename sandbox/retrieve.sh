#!/usr/bin/env bash
# Retrieval-mode sandbox UI: upload a PDF, ask a question, see the chunks.
# The interactive sibling of run.sh's static claims report. Needs ./up.sh first
# (local Postgres + migrations). Sparse-only without VOYAGE_API_KEY; export it
# for full dense + sparse retrieval.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8010}"

# Sandbox connection (localhost:5433, doadmin owner / dd_app app role). Clerk +
# Valkey get harmless placeholders so Settings() builds; this app touches neither.
export DATABASE_URL="postgresql+asyncpg://dd_app:sandbox_dd_app@localhost:5433/simpero"
export ALEMBIC_DATABASE_URL="postgresql+psycopg2://doadmin:sandbox_doadmin@localhost:5433/simpero"
export CLERK_SECRET_KEY="${CLERK_SECRET_KEY:-sandbox}"
export CLERK_JWT_AUDIENCE="${CLERK_JWT_AUDIENCE:-sandbox}"
export CLERK_JWKS_URL="${CLERK_JWKS_URL:-https://example.test/.well-known/jwks.json}"
export VALKEY_URL="${VALKEY_URL:-redis://localhost:6379/0}"
export ENVIRONMENT=production

MODE="sparse-only (set VOYAGE_API_KEY for dense)"
[ -n "${VOYAGE_API_KEY:-}" ] && MODE="dense + sparse"
echo "Retrieval sandbox → http://localhost:${PORT}    mode: ${MODE}"
echo "(Ctrl-C to stop)"

# Open the browser once the server is up.
( sleep 1.5 && (open "http://localhost:${PORT}" >/dev/null 2>&1 || true) ) &

cd "$BACKEND_DIR"
exec env PYTHONPATH="$BACKEND_DIR" uv run uvicorn sandbox.retrieve:app --host 127.0.0.1 --port "$PORT"
