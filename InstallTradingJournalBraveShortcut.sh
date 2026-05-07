#!/usr/bin/env bash
# One-time Termux installer for the Trading Journal Brave home-screen shortcut.
# It copies the launcher into Termux:Widget's shortcut folder.

set -Eeuo pipefail

DOWNLOADS="/storage/emulated/0/Download"
CANDIDATE_SOURCES=(
  "/storage/emulated/0/Download/CODEX-master/CODEX-master/LaunchTradingJournalBrave.sh"
  "/sdcard/Download/CODEX-master/CODEX-master/LaunchTradingJournalBrave.sh"
  "${HOME}/storage/downloads/CODEX-master/CODEX-master/LaunchTradingJournalBrave.sh"
  "/storage/emulated/0/Download/LaunchTradingJournalBrave.sh"
)
SOURCE=""
TARGET_DIR="${HOME}/.shortcuts"
TARGET="${TARGET_DIR}/Trading Journal.sh"

log() { printf '[journal-shortcut-install] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

if [[ ! -d "$DOWNLOADS" ]]; then
  fail "Downloads is not visible to Termux. Open Termux once and grant storage access."
fi

for candidate in "${CANDIDATE_SOURCES[@]}"; do
  if [[ -f "$candidate" ]]; then
    SOURCE="$candidate"
    break
  fi
done

if [[ -z "$SOURCE" ]]; then
  fail "missing LaunchTradingJournalBrave.sh. Checked: ${CANDIDATE_SOURCES[*]}"
fi

mkdir -p "$TARGET_DIR"
cp "$SOURCE" "$TARGET"
chmod 700 "$TARGET"

log "installed Termux:Widget shortcut script: ${TARGET}"
log "Add the Termux:Widget widget to your Android home screen and select/run: Trading Journal"
log "That shortcut launches the backend and opens Brave at http://127.0.0.1:8010/trading-journal"
