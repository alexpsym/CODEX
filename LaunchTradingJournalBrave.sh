#!/usr/bin/env bash
# Android / Termux launcher for the CODEX Trading Journal.
# Opens Brave Browser on Android only.
# Repo expected at: /storage/emulated/0/Download/CODEX-master (4)

set -Eeuo pipefail

JOURNAL_PORT="${JOURNAL_PORT:-8010}"
JOURNAL_URL="http://127.0.0.1:${JOURNAL_PORT}/trading-journal"
JOURNAL_HEALTH_URL="http://127.0.0.1:${JOURNAL_PORT}/health"
JOURNAL_API_URL="http://127.0.0.1:${JOURNAL_PORT}/api/trading-journal"
JOURNAL_READY_TIMEOUT_SECONDS="${JOURNAL_READY_TIMEOUT_SECONDS:-90}"
DEFAULT_REPO_DIR="/storage/emulated/0/Download/CODEX-master (4)"
BRAVE_PACKAGES=(
  "com.brave.browser"
  "com.brave.browser_beta"
  "com.brave.browser_nightly"
)

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s\n' "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd -P)"
TERMUX_STATE_DIR="${HOME}/.codex-trading-journal"
ENV_FILE="${TERMUX_STATE_DIR}/env.env"
PID_FILE="${TERMUX_STATE_DIR}/uvicorn-worker.pid"
LOG_FILE="${TERMUX_STATE_DIR}/trading-journal.log"

mkdir -p "$TERMUX_STATE_DIR"

log() {
  printf '[journal-android] %s\n' "$*"
}

fail() {
  log "ERROR: $*" >&2
  exit 1
}

resolve_repo_dir() {
  if [[ -n "${CODEX_REPO_DIR:-}" ]]; then
    printf '%s\n' "$CODEX_REPO_DIR"
    return 0
  fi
  if [[ -f "${DEFAULT_REPO_DIR}/render/master_service.py" ]]; then
    printf '%s\n' "$DEFAULT_REPO_DIR"
    return 0
  fi
  if [[ -f "${SCRIPT_DIR}/render/master_service.py" ]]; then
    printf '%s\n' "$SCRIPT_DIR"
    return 0
  fi
  if [[ -f "${PWD}/render/master_service.py" ]]; then
    printf '%s\n' "$PWD"
    return 0
  fi
  return 1
}

REPO_DIR="$(resolve_repo_dir || true)"
[[ -n "$REPO_DIR" ]] || fail "repo not found. Expected ${DEFAULT_REPO_DIR}. If storage permission is missing, open Termux once and run termux-setup-storage."
[[ -f "${REPO_DIR}/render/master_service.py" ]] || fail "render/master_service.py not found under ${REPO_DIR}"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<ENVEOF
# Local Android/Termux env file for Trading Journal launcher.
# Add API keys here only if this journal profile needs them.
ENVEOF
fi

export APP_PROFILE="journal"
export TRADING_JOURNAL_ONLY="1"
export PYTHONUNBUFFERED="1"
export DROPBOX_SYNC_ENABLED="0"
export LOCAL_STATE_ONLY="1"
export TRADING_JOURNAL_SOURCE="local"
export TRADING_JOURNAL_ENABLE_LOCAL_IMPORT="1"
export TRADING_JOURNAL_BROKER_REFRESH_ENABLED="0"
export TRADING_JOURNAL_LOCAL_DIR="${TRADING_JOURNAL_LOCAL_DIR:-${REPO_DIR}/journal}"
export MASTER_ENV_DIR="$TERMUX_STATE_DIR"
export MASTER_ENV_FILE="$ENV_FILE"
export MASTER_ENV_PROTECTED_KEYS="APP_PROFILE,TRADING_JOURNAL_ONLY,TRADING_JOURNAL_SOURCE,TRADING_JOURNAL_ENABLE_LOCAL_IMPORT,TRADING_JOURNAL_BROKER_REFRESH_ENABLED,TRADING_JOURNAL_LOCAL_DIR,DROPBOX_SYNC_ENABLED,LOCAL_STATE_ONLY"
export CASHFLOW_CACHE_TTL_SECONDS="${CASHFLOW_CACHE_TTL_SECONDS:-3600}"

python_bin() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
  else
    printf '%s\n' "python"
  fi
}

PYTHON_EXE="$(python_bin)"

check_python() {
  command -v "$PYTHON_EXE" >/dev/null 2>&1 || fail "python not found in Termux. Install Python in Termux before using this launcher."
}

missing_modules_json() {
  "$PYTHON_EXE" - <<'PY'
import importlib.util, json
mods = {
    "fastapi": "fastapi==0.110.0",
    "uvicorn": "uvicorn==0.27.1",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "xlrd": "xlrd==2.0.1",
    "dotenv": "python-dotenv==1.0.1",
    "requests": "requests",
    "httpx": "httpx==0.27.2",
    "PIL": "Pillow",
    "dateutil": "python-dateutil",
    "multipart": "python-multipart==0.0.9",
}
missing = [pkg for mod, pkg in mods.items() if importlib.util.find_spec(mod) is None]
print(json.dumps(missing))
PY
}

install_missing_deps() {
  local missing_json
  missing_json="$(missing_modules_json)"
  [[ "$missing_json" != "[]" ]] || return 0

  log "missing Python packages: ${missing_json}"
  log "attempting pip install into Termux Python..."

  "$PYTHON_EXE" -m pip --version >/dev/null 2>&1 || fail "pip unavailable in Termux Python."

  "$PYTHON_EXE" -m pip install --upgrade \
    fastapi==0.110.0 \
    uvicorn==0.27.1 \
    python-dotenv==1.0.1 \
    python-multipart==0.0.9 \
    python-dateutil \
    requests \
    httpx==0.27.2 \
    pandas \
    openpyxl \
    Pillow \
    xlrd==2.0.1 || {
      log "pip install failed. On Termux, repair packages and rerun this launcher."
      log "Needed Termux packages are usually: python clang rust libjpeg-turbo libpng freetype openblas"
      return 1
    }

  missing_json="$(missing_modules_json)"
  [[ "$missing_json" == "[]" ]] || fail "dependencies still missing after install: ${missing_json}"
}

http_probe() {
  local url="$1"
  "$PYTHON_EXE" - "$url" <<'PY' >/dev/null 2>&1
import sys
from urllib.request import urlopen
url = sys.argv[1]
with urlopen(url, timeout=2) as r:
    status = getattr(r, "status", None) or r.getcode()
    body = r.read(4096).decode("utf-8", "replace").strip()
if url.endswith("/health"):
    raise SystemExit(0 if status == 200 and body == "ok" else 1)
raise SystemExit(0 if status in (200, 202) else 1)
PY
}

wait_for_url() {
  local url="$1" label="$2" waited=0
  while ! http_probe "$url"; do
    waited=$((waited + 1))
    if (( waited >= JOURNAL_READY_TIMEOUT_SECONDS )); then
      return 1
    fi
    sleep 1
  done
  log "${label} ready after ${waited}s"
}

android_package_installed() {
  local pkg="$1"
  command -v pm >/dev/null 2>&1 && pm path "$pkg" >/dev/null 2>&1
}

open_in_brave() {
  command -v am >/dev/null 2>&1 || fail "Android activity manager 'am' not available. Open manually in Brave: ${JOURNAL_URL}"
  local pkg
  for pkg in "${BRAVE_PACKAGES[@]}"; do
    if android_package_installed "$pkg"; then
      am start -a android.intent.action.VIEW -d "$JOURNAL_URL" -p "$pkg" >/dev/null 2>&1 && return 0
    fi
  done
  fail "Brave Browser is not installed or not visible to Termux. Install Brave, then rerun this launcher."
}

worker_is_alive() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

start_worker() {
  if worker_is_alive; then
    log "worker already running with PID $(cat "$PID_FILE")"
    return 0
  fi
  : > "$LOG_FILE"
  nohup bash "$SCRIPT_PATH" --worker >> "$LOG_FILE" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" > "$PID_FILE"
  log "started worker PID ${pid}; log: ${LOG_FILE}"
}

run_worker() {
  check_python
  cd "$REPO_DIR" || fail "failed to cd to ${REPO_DIR}"
  log "worker started"
  log "repo=${REPO_DIR}"
  log "env=${MASTER_ENV_FILE}"
  log "journal_local_dir=${TRADING_JOURNAL_LOCAL_DIR}"

  while true; do
    install_missing_deps || exit 1
    log "starting uvicorn on 127.0.0.1:${JOURNAL_PORT}"
    "$PYTHON_EXE" -m uvicorn render.master_service:app --host 127.0.0.1 --port "$JOURNAL_PORT"
    code=$?
    log "uvicorn exited with ${code}; restarting in 3s"
    sleep 3
  done
}

main() {
  if [[ "${1:-}" == "--worker" ]]; then
    run_worker
    return
  fi

  check_python
  log "repo=${REPO_DIR}"
  log "journal=${JOURNAL_URL}"

  if http_probe "$JOURNAL_HEALTH_URL" && http_probe "$JOURNAL_API_URL"; then
    log "journal already running"
  else
    start_worker
    wait_for_url "$JOURNAL_HEALTH_URL" "health" || {
      tail -80 "$LOG_FILE" >&2 || true
      fail "backend did not become healthy within ${JOURNAL_READY_TIMEOUT_SECONDS}s; browser not opened"
    }
    wait_for_url "$JOURNAL_API_URL" "journal endpoint" || {
      tail -80 "$LOG_FILE" >&2 || true
      fail "journal API did not become ready within ${JOURNAL_READY_TIMEOUT_SECONDS}s; browser not opened"
    }
  fi

  open_in_brave
  log "Trading Journal launch requested in Brave Browser."
}

main "$@"
