#!/data/data/com.termux/files/usr/bin/bash
set -u

log() { printf '%s\n' "[setup-journal-replica] $*"; }
fail() { printf '%s\n' "[setup-journal-replica] ERROR: $*" >&2; exit 1; }

log "starting 32-bit-safe Trading Journal Excel replica setup"
if ! command -v termux-setup-storage >/dev/null 2>&1; then
  fail "termux-setup-storage not found. Run inside Termux."
fi

yes y | termux-setup-storage >/dev/null 2>&1 || true
sleep 6

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_SRC="$SCRIPT_DIR/make_trading_journal_replica.py"
[ -f "$PY_SRC" ] || fail "make_trading_journal_replica.py not found next to setup script: $SCRIPT_DIR"

if command -v pkg >/dev/null 2>&1; then
  log "installing Termux Python packages"
  pkg update -y || true
  pkg install -y python python-pip || fail "could not install python/python-pip"
fi

if ! command -v python >/dev/null 2>&1; then
  fail "python not found after package install"
fi

log "installing pure-Python Excel packages only; no pandas"
python -m pip install --upgrade setuptools wheel >/dev/null 2>&1 || true
python -m pip install openpyxl xlrd==2.0.1 python-dateutil || fail "could not install openpyxl/xlrd/python-dateutil"

APP_HOME="$HOME/.codex-trading-journal-replica"
SHORTCUTS="$HOME/.shortcuts"
mkdir -p "$APP_HOME" "$SHORTCUTS"
cp "$PY_SRC" "$APP_HOME/make_trading_journal_replica.py"
chmod +x "$APP_HOME/make_trading_journal_replica.py"

cat > "$SHORTCUTS/Generate Journal Replica" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -u

log() { printf '%s\n' "[journal-replica] $*"; }
fail() { printf '%s\n' "[journal-replica] ERROR: $*" >&2; exit 1; }

REPO="/storage/emulated/0/Download/CODEX-master (4)/CODEX-master"
if [ ! -d "$REPO/journal" ]; then
  FOUND=""
  for root in "$HOME/storage/downloads" "/storage/emulated/0/Download" "/sdcard/Download"; do
    [ -d "$root" ] || continue
    FOUND="$(find "$root" -maxdepth 8 -type d -path '*/CODEX-master/journal' 2>/dev/null | head -n 1)"
    [ -n "$FOUND" ] && break
  done
  [ -n "$FOUND" ] || fail "CODEX-master/journal not found under Downloads"
  REPO="${FOUND%/journal}"
fi

SCRIPT="$HOME/.codex-trading-journal-replica/make_trading_journal_replica.py"
[ -f "$SCRIPT" ] || fail "replica generator missing: $SCRIPT. Rerun setup."

OUT="$REPO/journal/TradingJournal_Android_Replica.xlsx"
log "repo=$REPO"
log "source=$REPO/journal"
log "output=$OUT"
python "$SCRIPT" --repo "$REPO" --output "$OUT" || fail "replica generation failed"

if command -v termux-open >/dev/null 2>&1; then
  log "opening workbook with Android file handler"
  termux-open "$OUT" >/dev/null 2>&1 && exit 0
fi

if command -v am >/dev/null 2>&1; then
  am start -a android.intent.action.VIEW -d "file://$OUT" -t "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" >/dev/null 2>&1 && exit 0
fi

log "generated workbook is here: $OUT"
EOF
chmod +x "$SHORTCUTS/Generate Journal Replica"

cat > "$SHORTCUTS/Open Journal Replica" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -u
REPO="/storage/emulated/0/Download/CODEX-master (4)/CODEX-master"
OUT="$REPO/journal/TradingJournal_Android_Replica.xlsx"
if [ ! -f "$OUT" ]; then
  FOUND="$(find "$HOME/storage/downloads" "/storage/emulated/0/Download" "/sdcard/Download" -maxdepth 9 -type f -name 'TradingJournal_Android_Replica.xlsx' 2>/dev/null | head -n 1)"
  [ -n "$FOUND" ] && OUT="$FOUND"
fi
if [ ! -f "$OUT" ]; then
  echo "[journal-replica] ERROR: TradingJournal_Android_Replica.xlsx not found. Run Generate Journal Replica first."
  exit 1
fi
if command -v termux-open >/dev/null 2>&1; then
  termux-open "$OUT" >/dev/null 2>&1 && exit 0
fi
am start -a android.intent.action.VIEW -d "file://$OUT" -t "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" >/dev/null 2>&1 || true
EOF
chmod +x "$SHORTCUTS/Open Journal Replica"

log "installed Termux:Widget shortcuts:"
log "- Generate Journal Replica"
log "- Open Journal Replica"
log "Refresh the Termux widget, then tap Generate Journal Replica."
