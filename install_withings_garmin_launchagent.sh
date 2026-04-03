#!/bin/zsh
set -euo pipefail

LABEL="com.jc.withings-garmin-sync"
HOUR="8"
MINUTE="0"
LOAD_NOW="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hour)
      HOUR="$2"
      shift 2
      ;;
    --minute)
      MINUTE="$2"
      shift 2
      ;;
    --no-load)
      LOAD_NOW="0"
      shift
      ;;
    *)
      echo "Option inconnue: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
TEMPLATE="$SCRIPT_DIR/launchagents/$LABEL.plist.template"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
RUN_SCRIPT="$SCRIPT_DIR/run_withings_garmin_sync.sh"
LOG_DIR="$SCRIPT_DIR/logs"
STDOUT_PATH="$LOG_DIR/withings_garmin_sync.out.log"
STDERR_PATH="$LOG_DIR/withings_garmin_sync.err.log"
ENV_FILE="$SCRIPT_DIR/.withings_garmin_sync.env"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template introuvable: $TEMPLATE" >&2
  exit 1
fi

if [[ ! -x "$RUN_SCRIPT" ]]; then
  chmod +x "$RUN_SCRIPT"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Fichier d'environnement manquant: $ENV_FILE" >&2
  echo "Copie .withings_garmin_sync.env.example vers .withings_garmin_sync.env puis renseigne GARMIN_EMAIL et GARMIN_PASSWORD." >&2
  exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

TEMPLATE_PATH="$TEMPLATE" \
RUN_SCRIPT_PATH="$RUN_SCRIPT" \
WORKDIR_PATH="$SCRIPT_DIR" \
SYNC_HOUR="$HOUR" \
SYNC_MINUTE="$MINUTE" \
STDOUT_FILE="$STDOUT_PATH" \
STDERR_FILE="$STDERR_PATH" \
PLIST_FILE="$PLIST_PATH" \
python3 - <<'PY'
import os
from pathlib import Path

template = Path(os.environ["TEMPLATE_PATH"]).read_text(encoding="utf-8")
hour = os.environ["SYNC_HOUR"]
minute = os.environ["SYNC_MINUTE"]
content = (
    template
    .replace("__RUN_SCRIPT__", os.environ["RUN_SCRIPT_PATH"])
    .replace("__WORKDIR__", os.environ["WORKDIR_PATH"])
    .replace("__HOUR__", hour)
    .replace("__MINUTE__", minute)
    .replace("__STDOUT__", os.environ["STDOUT_FILE"])
    .replace("__STDERR__", os.environ["STDERR_FILE"])
)
content = content.replace(f"<string>{hour}</string>", f"<integer>{hour}</integer>", 1)
content = content.replace(f"<string>{minute}</string>", f"<integer>{minute}</integer>", 1)
Path(os.environ["PLIST_FILE"]).write_text(content, encoding="utf-8")
PY

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

if [[ "$LOAD_NOW" == "1" ]]; then
  launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
else
  echo "Plist généré dans $PLIST_PATH"
fi

echo "LaunchAgent installé: $PLIST_PATH"
echo "Horaire: ${HOUR}h${MINUTE}"
echo "Logs: $STDOUT_PATH et $STDERR_PATH"
