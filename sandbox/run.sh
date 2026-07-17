#!/usr/bin/env bash
# Run the whole pipeline against the local sandbox on a CIM you provide.
#
#   ./sandbox/run.sh /path/to/your-cim.pdf [--entity "Target Co"] [--org demo]
#
# CONFIDENTIALITY: the CIM you pass is copied into sandbox/cim/, which is
# gitignored. It is never committed. Do not commit real deal documents.
#
# The two halves run as two processes across the C3 seam, exactly as in
# production: the parse service (a sibling repo) emits claims as JSON; the
# backend ingests that JSON into the local claims table. Neither shares a
# runtime, so the backend never needs docling.
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SANDBOX_DIR/.." && pwd)"
PARSER_DIR="${PARSER_REPO:-$BACKEND_DIR/../Simpero_Gov_AI_Services}"

ENTITY="Target Co"
ORG_KEY="sandbox_demo"
PDF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --entity) ENTITY="$2"; shift 2 ;;
    --org)    ORG_KEY="$2"; shift 2 ;;
    -*)       echo "unknown option: $1"; exit 1 ;;
    *)        PDF="$1"; shift ;;
  esac
done

[[ -n "$PDF" ]]        || { echo "usage: ./sandbox/run.sh <cim.pdf> [--entity NAME] [--org KEY]"; exit 1; }
[[ -f "$PDF" ]]        || { echo "error: no such file: $PDF"; exit 1; }
[[ -d "$PARSER_DIR" ]] || { echo "error: parse service repo not found at $PARSER_DIR"; echo "  clone Simpero_Gov_AI_Services beside this repo, or set PARSER_REPO=/path/to/it"; exit 1; }

# Copy the CIM into the gitignored local dir -- it never enters git.
mkdir -p "$SANDBOX_DIR/cim"
LOCAL_PDF="$SANDBOX_DIR/cim/$(basename "$PDF")"
cp "$PDF" "$LOCAL_PDF"
CLAIMS_JSON="$SANDBOX_DIR/cim/claims.json"

set -a
# shellcheck disable=SC1091
. "$SANDBOX_DIR/.env.sandbox"
set +a

echo "==> [1/2] parse + extract + emit  (parse service, $PARSER_DIR)"
( cd "$PARSER_DIR" && uv run python scripts/emit_claims.py "$LOCAL_PDF" --entity "$ENTITY" ) > "$CLAIMS_JSON"

echo "==> [2/2] ingest into the local claims spine  (backend, as dd_app)"
( cd "$BACKEND_DIR" && uv run python scripts/ingest_claims.py "$CLAIMS_JSON" --org-key "$ORG_KEY" --commit )

echo
echo "==> done. The claims are in the local database. Inspect them:"
echo "    docker exec -it simpero-sandbox-postgres-1 psql -U doadmin -d simpero \\"
echo "      -c \"SELECT entity, left(attribute,30) attr, value->>'raw' raw, value->>'normalized' normalized, page, status FROM claims ORDER BY page, char_start LIMIT 20;\""
