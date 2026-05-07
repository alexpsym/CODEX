#!/usr/bin/env bash
# One-time Termux installer for the Trading Journal Brave home-screen shortcut.
# It copies the launcher into Termux:Widget's shortcut folder.

set -Eeuo pipefail

DOWNLOADS="/storage/emulated/0/Download"
SOURCE="${DOWNLOADS}/LaunchTradingJournalBrave.sh"
TARGET_DIR="${HOME}/.shortcuts"
TARGET="${TARGET_DIR}/Trading Journal.sh"

log() { printf '[journal-shortcut-install] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

if [[ ! -d "$DOWNLOADS" ]]; then
  fail "Downloads is not visible to Termux. Open Termux once and grant storage access."
fi

if [[ ! -f "$SOURCE" ]]; then
  fail "missing ${SOURCE}. Put LaunchTradingJournalBrave.sh in Android Downloads first."
fi

mkdir -p "$TARGET_DIR"
cp "$SOURCE" "$TARGET"
chmod 700 "$TARGET"

log "installed Termux:Widget shortcut script: ${TARGET}"
log "Add the Termux:Widget widget to your Android home screen and select/run: Trading Journal"
log "That shortcut launches the backend and opens Brave at http://127.0.0.1:8010/trading-journal"
