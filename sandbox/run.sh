#!/usr/bin/env bash
# Run the whole pipeline against the local sandbox on a CIM you provide, and
# print exactly what lands in the claims table.
#
#   ./sandbox/run.sh /path/to/your-cim.pdf [--entity "Target Co"] [--org demo]
#                    [--tables-only | --prose | --qualitative]
#
# TIERS (default --qualitative -- the full pipeline the sandbox exists to show):
#   --tables-only   deterministic table extraction; no model, no key.
#   --prose         + numeric facts stated in prose      (one model call / prose page).
#   --qualitative   + claims that carry no number         (two model calls / prose page).
# The prose tiers call the Anthropic API and need ANTHROPIC_API_KEY (or
# ANTHROPIC_AUTH_TOKEN) in your environment. This script checks for it up front
# and stops before touching your CIM if it is missing -- so set it, or pass
# --tables-only for a key-free run.
#
# CONFIDENTIALITY: the CIM you pass is copied into sandbox/cim/, which is
# gitignored. It is never committed. Do not commit real deal documents. The
# prose tiers additionally SEND each prose page's text to the Anthropic API --
# a real deal document leaves your machine on those tiers; --tables-only never
# makes a network call.
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
TIER_FLAG="--qualitative"   # default: the full pipeline
TIER_NAME="tables + prose + qualitative"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --entity)       ENTITY="$2"; shift 2 ;;
    --org)          ORG_KEY="$2"; shift 2 ;;
    --tables-only)  TIER_FLAG="";              TIER_NAME="tables only";                 shift ;;
    --prose)        TIER_FLAG="--prose";       TIER_NAME="tables + prose";              shift ;;
    --qualitative)  TIER_FLAG="--qualitative"; TIER_NAME="tables + prose + qualitative"; shift ;;
    -*)             echo "unknown option: $1"; exit 1 ;;
    *)              PDF="$1"; shift ;;
  esac
done

[[ -n "$PDF" ]]        || { echo "usage: ./sandbox/run.sh <cim.pdf> [--entity NAME] [--org KEY] [--tables-only|--prose|--qualitative]"; exit 1; }
[[ -f "$PDF" ]]        || { echo "error: no such file: $PDF"; exit 1; }
[[ -d "$PARSER_DIR" ]] || { echo "error: parse service repo not found at $PARSER_DIR"; echo "  clone Simpero_Gov_AI_Services beside this repo, or set PARSER_REPO=/path/to/it"; exit 1; }

# The prose tiers call the Anthropic API. Fail here, before copying the CIM or
# starting any work, rather than part way through -- and do NOT read the key
# from sandbox/.env.sandbox (that file is committed; an API key never belongs in
# it). It must come from your own environment.
if [[ -n "$TIER_FLAG" && -z "${ANTHROPIC_API_KEY:-}" && -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
  echo "error: $TIER_FLAG needs ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in your environment."
  echo "  export it in your shell, or re-run with --tables-only for a key-free run."
  exit 1
fi

# `docker compose ps` occasionally returns empty when the daemon is briefly busy
# (right after another compose call), so a single check is racy -- ask for the
# postgres container by name and retry once before believing it is down.
postgres_running() {
  [[ -n "$("${COMPOSE[@]}" ps postgres --status running --format '{{.Name}}' 2>/dev/null)" ]]
}
postgres_running || { sleep 2; postgres_running; } \
  || { echo "error: the sandbox is not running -- run ./sandbox/up.sh first"; exit 1; }

printf '\033[1m========================================================================\n'
printf '  Simpero local sandbox — running the pipeline\n'
printf '========================================================================\033[0m\n'
echo "    Input  : $PDF"
echo "    Entity : $ENTITY"
echo "    Tenant : $ORG_KEY"
echo "    Tiers  : $TIER_NAME"

set -a
# shellcheck disable=SC1091
. "$SANDBOX_DIR/.env.sandbox"
set +a

mkdir -p "$SANDBOX_DIR/cim"
LOCAL_PDF="$SANDBOX_DIR/cim/$(basename "$PDF")"
CLAIMS_JSON="$SANDBOX_DIR/cim/claims.json"
EMIT_LOG="$SANDBOX_DIR/cim/emit.log"

step "1/4" "Copying the CIM into sandbox/cim/  (gitignored, never committed)"
if [[ "$PDF" -ef "$LOCAL_PDF" ]]; then
  ok "$(basename "$PDF") (already in sandbox/cim/)"
else
  cp "$PDF" "$LOCAL_PDF"
  ok "$(basename "$PDF")"
fi

# Empty tier flag (tables only) must not become an empty argv entry, so the
# flag is built as an array. The parser subprocess inherits this shell's
# environment, so ANTHROPIC_API_KEY reaches it without being echoed anywhere.
EMIT_ARGS=(scripts/emit_claims.py "$LOCAL_PDF" --entity "$ENTITY")
[[ -n "$TIER_FLAG" ]] && EMIT_ARGS+=("$TIER_FLAG")
if [[ -n "$TIER_FLAG" ]]; then
  step "2/4" "Parse → extract → emit   ($TIER_NAME; prose tiers call the model, one/two per prose page — minutes on a long CIM)"
else
  step "2/4" "Parse → extract → emit   (tables only; docling, no database, no network — ~10-15s)"
fi
if ( cd "$PARSER_DIR" && uv run python "${EMIT_ARGS[@]}" ) > "$CLAIMS_JSON" 2> "$EMIT_LOG"; then
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
