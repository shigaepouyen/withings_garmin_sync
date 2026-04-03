#!/bin/zsh
set -euo pipefail

LABEL="com.jc.withings-garmin-sync"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

if [[ -f "$PLIST_PATH" ]]; then
  rm "$PLIST_PATH"
  echo "LaunchAgent supprimé: $PLIST_PATH"
else
  echo "Aucun LaunchAgent à supprimer: $PLIST_PATH"
fi
