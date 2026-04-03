#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
export PATH="/Users/jc/.nvm/versions/node/v22.22.0/bin:$PATH"
ENV_FILE="$SCRIPT_DIR/.withings_garmin_sync.env"
LOG_DIR="$SCRIPT_DIR/logs"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

mkdir -p "$LOG_DIR"

cd "$SCRIPT_DIR"
exec /usr/bin/env python3 "$SCRIPT_DIR/sync_withings_to_garmin.py" --config-file "$SCRIPT_DIR/mcp_config.json" "$@"
