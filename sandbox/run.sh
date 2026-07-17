#!/usr/bin/env bash
# Run the whole pipeline against the local sandbox on a CIM you provide, and
# print exactly what lands in the claims table.
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
COMPOSE=(docker compose -f "$SANDBOX_DIR/docker-compose.yml")

step() { printf '\n\033[1;36m[%s]\033[0m %s\n' "$1" "$2"; }
ok()   { printf '\033[1;32m      ✓ %s\033[0m\n' "$1"; }

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
"${COMPOSE[@]}" ps --status running 2>/dev/null | grep -q postgres || { echo "error: the sandbox is not running -- run ./sandbox/up.sh first"; exit 1; }

printf '\033[1m========================================================================\n'
printf '  Simpero local sandbox — running the pipeline\n'
printf '========================================================================\033[0m\n'
echo "    Input  : $PDF"
echo "    Entity : $ENTITY"
echo "    Tenant : $ORG_KEY"

set -a
# shellcheck disable=SC1091
. "$SANDBOX_DIR/.env.sandbox"
set +a

mkdir -p "$SANDBOX_DIR/cim"
LOCAL_PDF="$SANDBOX_DIR/cim/$(basename "$PDF")"
CLAIMS_JSON="$SANDBOX_DIR/cim/claims.json"
EMIT_LOG="$SANDBOX_DIR/cim/emit.log"

step "1/4" "Copying the CIM into sandbox/cim/  (gitignored, never committed)"
cp "$PDF" "$LOCAL_PDF"
ok "$(basename "$PDF")"

step "2/4" "Parse → extract → emit   (parse service; docling, no database — takes ~10-15s)"
if ( cd "$PARSER_DIR" && uv run python scripts/emit_claims.py "$LOCAL_PDF" --entity "$ENTITY" ) > "$CLAIMS_JSON" 2> "$EMIT_LOG"; then
  ok "$(grep -o 'emitted .*' "$EMIT_LOG" | tail -1)"
else
  echo "      parse/emit failed:"; sed 's/^/      /' "$EMIT_LOG"; exit 1
fi

step "3/4" "Ingest into the local claims spine   (backend, as the dd_app app role)"
echo "      validates every claim against the contract, then INSERTs under RLS"
# ENVIRONMENT=production only silences SQLAlchemy's SQL echo for clean output;
# it changes nothing on this path (see app/core/database.py).
( cd "$BACKEND_DIR" && ENVIRONMENT=production uv run python scripts/ingest_claims.py "$CLAIMS_JSON" --org-key "$ORG_KEY" --commit \
    | sed 's/^/      /' )

step "4/4" "Reading back the claims table   (what is actually stored)"
"${COMPOSE[@]}" exec -T postgres psql -U doadmin -d simpero -P pager=off -c "
  SELECT c.entity,
         left(c.attribute, 26)          AS attribute,
         c.value->>'raw'                AS raw,
         c.value->>'normalized'         AS normalized,
         c.value->>'unit'               AS unit,
         c.kind,
         c.page,
         c.char_start || '-' || c.char_end AS span,
         c.status
  FROM claims c
  JOIN organisation o ON o.id = c.org_id
  WHERE o.clerk_org_id = '$ORG_KEY'
  ORDER BY c.page, c.char_start;"

echo "      summary for tenant '$ORG_KEY':"
"${COMPOSE[@]}" exec -T postgres psql -U doadmin -d simpero -At -P pager=off -c "
  SELECT '        ' || count(*) || ' claims  |  ' ||
         string_agg(DISTINCT status, ', ') || '  |  scale from: ' ||
         string_agg(DISTINCT value->>'scale_source', ', ')
  FROM claims c JOIN organisation o ON o.id = c.org_id
  WHERE o.clerk_org_id = '$ORG_KEY';"

printf '\n\033[1;32m========================================================================\n'
printf '  Done. The claims above are stored in your local Postgres.\n'
printf '========================================================================\033[0m\n'
echo "    Query them yourself:"
echo "      docker compose -f sandbox/docker-compose.yml exec postgres \\"
echo "        psql -U doadmin -d simpero -c 'SELECT * FROM claims LIMIT 5;'"
