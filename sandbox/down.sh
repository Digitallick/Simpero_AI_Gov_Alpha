#!/usr/bin/env bash
# Stop the sandbox. By default the data survives a restart; --wipe deletes it.
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--wipe" ]]; then
  echo "==> stopping and DELETING the sandbox database volume"
  docker compose -f "$SANDBOX_DIR/docker-compose.yml" down --volumes
else
  echo "==> stopping the sandbox (data kept; --wipe to delete it)"
  docker compose -f "$SANDBOX_DIR/docker-compose.yml" down
fi
