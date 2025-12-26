"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import asyncio
import html
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import httpx
from starlette.responses import RedirectResponse

from payslip_audit.tesseract import (
    TESSERACT_MISSING_MESSAGE,
    _resolve_tesseract_binary,
    is_tesseract_available,
)
from bybit_monitor import bybit_altcoin_monitor as bybit_monitor

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

SKIP_DIRS = {"render", "mt5-clone", ".venv", "venv", "__pycache__", ".git", "env", "youtube"}
SKIP_DIRS_NORMALIZED = {name.casefold() for name in SKIP_DIRS}
SKIP_FILES = {"__init__.py"}
MAX_LOG_LINES = 400
PAYSLIP_REPORT_NAME = "audit_report.pdf"
PAYSLIP_UPLOAD_ROOT = BASE_DIR / "render" / "uploads" / "payslip"
PAYSLIP_ALLOWED_IMAGES = {".jpg", ".jpeg", ".png"}
WEB_APPS = {"cryptocalculator-clone"}

ENTRY_OVERRIDES = {
    "Crypto-Scanner-clone": ["continuous_scan.py", "scan.py"],
    "LEDGER-clone": ["process_entries.py"],
    "PUSH": ["PUSH.py"],
    "bybit_monitor": ["bybit_altcoin_monitor.py"],
    "bybithistory-clone": ["app.py"],
    "coinspot-clone": ["coinspot_history.py"],
    "cryptocalculator-clone": ["cryptocalculator_web.py", "cryptocalculator.py"],
    "download_video": ["download_video.py"],
    "ema-bounce-clone": ["ema-bounce.py"],
    "extractor": ["extract_all_files.py"],
    "fxscanner-oanda-clone": ["forex_scanner.py"],
    "fxweekend-clone": ["liquidate.py"],
    "ivindicator-clone": ["ivapp.py", "ivindicator.py"],
    "oanda-calculator-clone": ["oanda_calculator_web.py", "oanda_api.py"],
    "oanda_history-clone": ["oanda_history.py"],
    "optionstrader-clone": ["optionstrader.py", "alert_server.py"],
    "payslip_audit": ["payslip_timesheet_audit.py"],
    "viddl-clone": ["master.py", "vid.py"],
}

LOG_FILE_OVERRIDES: Dict[str, Path] = {}

BYBIT_SETTINGS_PATH = bybit_monitor.SETTINGS_PATH

PAYSLIP_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class ManagedScript:
    """Represents a runnable Python script managed by the service."""

    name: str
    path: Path
    category: str = "Other"
    log_file: Optional[Path] = None
    process: Optional[asyncio.subprocess.Process] = None
    port: Optional[int] = None
    _log_lines: List[str] = field(default_factory=list)
    last_output_at: Optional[float] = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def to_summary(self) -> Dict[str, object]:
        return {
            "id": self.name,
            "name": self.name,
            "path": str(self.path),
            "category": self.category,
            "running": self.is_running,
            "return_code": None if self.process is None else self.process.returncode,
            "open_url": script_open_url(self),
            "logs_url": script_logs_url(self.name),
            "last_output_at": self.last_output_at,
        }

    def add_log(self, line: str) -> None:
        cleaned = line.rstrip("\n")
        if cleaned:
            self._log_lines.append(cleaned)
            if len(self._log_lines) > MAX_LOG_LINES:
                self._log_lines = self._log_lines[-MAX_LOG_LINES :]
            self.last_output_at = time.time()

    def logs(self) -> List[str]:
        if self.log_file is not None:
            try:
                if self.log_file.exists():
                    stat = self.log_file.stat()
                    content = self.log_file.read_text(encoding="utf-8", errors="replace")
                    lines = content.splitlines()
                    if lines:
                        self.last_output_at = stat.st_mtime
                    return lines[-MAX_LOG_LINES :]
            except Exception as exc:  # pragma: no cover - defensive fallback
                return [f"Unable to read log file {self.log_file}: {exc}"]

        return list(self._log_lines)

    def log_snapshot(self, cursor: int = 0) -> Dict[str, object]:
        lines = self.logs()
        safe_cursor = max(0, min(cursor, len(lines)))
        new_lines = lines[safe_cursor:]
        return {
            "lines": new_lines,
            "cursor": safe_cursor + len(new_lines),
            "total": len(lines),
            "last_output_at": self.last_output_at,
        }

    async def start(self) -> None:
        if self.is_running:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"Script not found: {self.path}")

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{BASE_DIR}:{current_pythonpath}" if current_pythonpath else str(BASE_DIR)
        )
        if self.name in WEB_APPS:
            if self.port is None:
                self.port = _allocate_port()
            env["PORT"] = str(self.port)
            env["HOST"] = "127.0.0.1"

        try:
            self.process = await asyncio.create_subprocess_exec(
                os.getenv("PYTHON", "python"),
                "-u",
                str(self.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.path.parent),
                env=env,
            )
        except Exception as exc:
            self.add_log(f"Failed to start: {exc}")
            raise

        asyncio.create_task(self._capture_output())

    async def _capture_output(self) -> None:
        assert self.process is not None
        if self.process.stdout is None:
            return

        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                self.add_log(line.decode("utf-8", errors="replace"))
        finally:
            await self.process.wait()
            self.port = None

    async def stop(self) -> None:
        if not self.is_running:
            return
        assert self.process is not None
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()


def candidate_entrypoints(app_dir: Path) -> List[Path]:
    app_name = app_dir.name
    candidates: List[str] = []

    if app_name in ENTRY_OVERRIDES:
        candidates.extend(ENTRY_OVERRIDES[app_name])

    candidates.extend(
        [
            "main.py",
            "app.py",
            "run.py",
            "server.py",
            f"{app_name}.py",
            "wsgi.py",
        ]
    )

    seen: set[str] = set()
    ordered: List[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(app_dir / candidate)
    return ordered


def categorize_script(script_path: Path) -> str:
    """Return a high-level category name for ``script_path``."""

    folder = script_path.parent.name.lower()
    filename = script_path.name.lower()
    full = f"{folder}/{filename}"

    other_explicit = {
        "download_video",
        "extractor",
        "viddl-clone",
        "payslip_audit",
        "push",
    }

    if "ledger-clone" in full:
        return "Excel"

    if any(keyword in folder for keyword in ("fx", "oanda", "forex")):
        return "Forex"

    crypto_keywords = (
        "crypto",
        "bybit",
        "coinspot",
        "optionstrader",
        "ema-bounce",
        "ivin",
    )
    if any(keyword in folder or keyword in filename for keyword in crypto_keywords):
        return "Crypto"

    if folder in other_explicit:
        return "Other"

    return "Other"


def _encoded_script_name(script_name: str) -> str:
    """Encode a script name for safe URL usage while keeping slashes intact."""

    return quote(script_name, safe="/")


def script_open_url(script: ManagedScript) -> str:
    """Return the preferred UI URL for a script."""

    return f"/scripts/view/{_encoded_script_name(script.name)}"


def script_logs_url(script_name: str) -> str:
    """Return the JSON logs API endpoint for a script."""

    return f"/logs/{_encoded_script_name(script_name)}"


def _payslip_session_dir(session_id: str) -> Path:
    return PAYSLIP_UPLOAD_ROOT / session_id


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


TESSERACT_MISSING_DETAIL = TESSERACT_MISSING_MESSAGE


def ensure_tesseract_available() -> None:
    """Raise an HTTP 500 with clear guidance when Tesseract is absent."""

    if not is_tesseract_available():
        raise HTTPException(status_code=500, detail=TESSERACT_MISSING_DETAIL)


async def _execute_payslip_audit(payslip: Path, timesheets: List[Path], output_path: Path) -> str:
    script_path = BASE_DIR / "payslip_audit" / "payslip_timesheet_audit.py"

    ensure_tesseract_available()

    command = [
        os.getenv("PYTHON", "python"),
        str(script_path),
        "--payslip",
        str(payslip),
        "--timesheet",
    ] + [str(path) for path in timesheets] + ["--output", str(output_path)]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(script_path.parent),
    )
    stdout, _ = await process.communicate()
    log_output = stdout.decode("utf-8", errors="replace") if stdout else ""

    if process.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Audit failed with exit code {process.returncode}.\n{log_output}",
        )

    if not output_path.exists():
        raise HTTPException(status_code=500, detail="Audit completed but no report was produced.")

    return log_output


def discover_scripts() -> List[ManagedScript]:
    """Return one ManagedScript per app folder using a chosen entrypoint."""

    scripts: List[ManagedScript] = []

    for app_dir in sorted(BASE_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        if app_dir.name.casefold() in SKIP_DIRS_NORMALIZED or app_dir.name.startswith("."):
            continue

        # Special-case LEDGER: every .py is treated as its own managed script.
        if app_dir.name == "LEDGER-clone":
            for py_file in sorted(
                p
                for p in app_dir.glob("*.py")
                if p.name not in SKIP_FILES and not p.name.startswith("test_")
            ):
                scripts.append(
                    ManagedScript(
                        name=f"{app_dir.name}/{py_file.name}",
                        path=py_file,
                        category=categorize_script(py_file),
                        log_file=LOG_FILE_OVERRIDES.get(app_dir.name),
                    )
                )
            continue

        entry_path: Optional[Path] = None
        for candidate in candidate_entrypoints(app_dir):
            if candidate.exists() and candidate.is_file():
                entry_path = candidate
                break

        if entry_path is None:
            py_files = sorted(
                p
                for p in app_dir.glob("*.py")
                if p.name not in SKIP_FILES and not p.name.startswith("test_")
            )
            if py_files:
                entry_path = py_files[0]

        if entry_path is not None:
            scripts.append(
                ManagedScript(
                    name=app_dir.name,
                    path=entry_path,
                    category=categorize_script(entry_path),
                    log_file=LOG_FILE_OVERRIDES.get(app_dir.name),
                )
            )

    return scripts


class ScriptManager:
    """Keeps track of runnable scripts and their processes."""

    def __init__(self, scripts: Iterable[ManagedScript]):
        self._scripts: Dict[str, ManagedScript] = {script.name: script for script in scripts}
        self._aliases: Dict[str, str] = {}

        for script in scripts:
            self._register_aliases(script.name)
            self._register_aliases(script.path.name, canonical=script.name)
            self._register_aliases(script.path.stem, canonical=script.name)

    def _normalize(self, name: str) -> str:
        trimmed = name.strip().strip("/")
        return trimmed.replace("-", "_").casefold()

    def _register_aliases(self, alias: str, canonical: Optional[str] = None) -> None:
        target = alias if canonical is None else canonical
        normalized = self._normalize(alias)
        self._aliases.setdefault(normalized, target)

    def _resolve_name(self, name: str) -> str:
        if name in self._scripts:
            return name

        normalized = self._normalize(name)
        if normalized in self._aliases:
            return self._aliases[normalized]

        raise HTTPException(status_code=404, detail=f"Script not found: {name}")

    def list_scripts(self) -> List[Dict[str, object]]:
        return sorted((script.to_summary() for script in self._scripts.values()), key=lambda s: s["name"])

    def get(self, name: str) -> ManagedScript:
        resolved = self._resolve_name(name)
        return self._scripts[resolved]

    async def start(self, name: str) -> Dict[str, object]:
        script = self.get(name)
        await script.start()
        return script.to_summary()

    async def stop(self, name: str) -> Dict[str, object]:
        script = self.get(name)
        await script.stop()
        return script.to_summary()

    def logs(self, name: str) -> List[str]:
        return self.get(name).logs()

    def log_snapshot(self, name: str, cursor: int = 0) -> Dict[str, object]:
        return self.get(name).log_snapshot(cursor)


script_manager = ScriptManager(discover_scripts())
app = FastAPI(title="Render Master Script", version="1.0")


ASSET_VERSION = ""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Render Master Control</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; margin-bottom: 1.5rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1.25rem; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .row { display: flex; justify-content: space-between; gap: 1rem; align-items: center; }
        .nav-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        .path { color: #94a3b8; font-size: 0.9rem; word-break: break-all; }
        .pill { display: inline-block; padding: 0.3rem 0.65rem; border-radius: 999px; font-weight: 700; font-size: 0.9rem; }
        .running { background: #22c55e22; color: #86efac; }
        .stopped { background: #ef444422; color: #fecdd3; }
        .actions { display: flex; gap: 0.5rem; margin: 0.75rem 0; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .start { background: #22c55e; color: #052e16; }
        .stop { background: #ef4444; color: #fff7ed; }
        .secondary { background: #1f2937; color: #cbd5e1; }
        .settings-card { margin-top: 0.5rem; padding: 0.75rem; border: 1px solid #1f2937; border-radius: 10px; background: #0d1728; display: flex; flex-direction: column; gap: 0.75rem; }
        .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.5rem; }
        .settings-card label { display: flex; flex-direction: column; gap: 0.25rem; font-weight: 700; color: #cbd5e1; font-size: 0.95rem; }
        .settings-card input { padding: 0.55rem 0.75rem; border-radius: 10px; border: 1px solid #1f2937; background: #0a0f1b; color: #e5e7eb; }
        .badge { display: inline-block; padding: 0.35rem 0.7rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; }
        .badge-error { background: #7f1d1d; color: #fecdd3; }
        pre { background: #0a0f1b; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; overflow: auto; max-height: 260px; white-space: pre-wrap; margin: 0; }
        .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
        .refresh { background: #3b82f6; color: #eaf2ff; }
    </style>
</head>
<body>
    <div class=\"nav-bar\">
        <button class=\"secondary\" id=\"nav-back\">Back</button>
        <button class=\"secondary\" id=\"nav-forward\">Forward</button>
    </div>
    <h1>Render Master Control</h1>
    <p class=\"meta\">Pick a category to see its scripts. From there you can start, stop, and monitor anything in this repository (everything except the mt5-clone folder). Webhooks can be sent to <code>/webhook/&lt;script-name&gt;</code>.</p>
    <div class=\"toolbar\">
        <button class=\"refresh\" id=\"refresh-btn\">Refresh</button>
        <span id=\"status\" class=\"meta\">Loading scripts...</span>
    </div>
    <div id=\"grid\" class=\"grid\"></div>

    <script src=\"/static/dashboard.js\"></script>
</body>
</html>"""


CATEGORY_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Scripts - {category}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; margin-bottom: 1.5rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem; display: flex; align-items: center; justify-content: center; text-align: center; min-height: 84px; }
        .script-btn { width: 100%; padding: 0.8rem 1rem; border-radius: 10px; border: none; font-weight: 700; background: #1f2937; color: #e2e8f0; cursor: pointer; }
        .nav-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .secondary { background: #1f2937; color: #cbd5e1; }
    </style>
</head>
<body data-category=\"{category}\">
    <div class=\"nav-bar\">
        <button class=\"secondary\" id=\"nav-back\">Back</button>
        <button class=\"secondary\" id=\"nav-forward\">Forward</button>
    </div>
    <h1>{category} scripts</h1>
    <p class=\"meta\">Select a script to view its page.</p>
    <div id=\"grid\" class=\"grid\"></div>
    <script src=\"/static/category_page.js\"></script>
</body>
</html>"""


SCRIPT_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Script - {script_name}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; }
        .nav-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        .actions { display: flex; gap: 0.5rem; margin: 1rem 0; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .start { background: #22c55e; color: #052e16; }
        .stop { background: #ef4444; color: #fff7ed; }
        .secondary { background: #1f2937; color: #cbd5e1; }
        .panel { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem; margin-bottom: 1rem; }
        .log-box { background: #0a0f1b; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; white-space: pre-wrap; overflow-wrap: anywhere; min-height: 220px; max-height: 360px; overflow: auto; border: 1px solid #1f2937; }
        iframe { width: 100%; height: 520px; border: 1px solid #1f2937; border-radius: 12px; background: #0a0f1b; }
    </style>
</head>
<body data-script-name=\"{script_name}\" data-has-ui=\"{has_ui}\">
    <div class=\"nav-bar\">
        <button class=\"secondary\" id=\"nav-back\">Back</button>
        <button class=\"secondary\" id=\"nav-forward\">Forward</button>
    </div>
    <h1>{script_name}</h1>
    <p class=\"meta\" id=\"script-status\">Loading status...</p>
    <div class=\"actions\">
        <button class=\"start\" id=\"start-btn\">Start</button>
        <button class=\"stop\" id=\"stop-btn\">Stop</button>
    </div>
    <div class=\"panel\">
        <strong>Logs</strong>
        <div class=\"log-box\" id=\"log-box\">Waiting for output...</div>
    </div>
    <div class=\"panel\" id=\"app-panel\" style=\"display:none;\">
        <strong>App UI</strong>
        <iframe id=\"app-frame\" title=\"Script UI\"></iframe>
    </div>
    <script src=\"/static/script_page.js\"></script>
</body>
</html>"""


LOG_VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Logs - {script_name}</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 1.5rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; }
        .controls { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1rem; }
        button { padding: 0.6rem 1rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        #refresh-btn { background: #3b82f6; color: #eaf2ff; }
        #save-log-btn { background: #22c55e; color: #052e16; }
        #log-box { background: #0a0f1b; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; white-space: pre-wrap; overflow-wrap: anywhere; min-height: 320px; border: 1px solid #1f2937; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .badge { display: inline-block; padding: 0.35rem 0.7rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; }
        .settings-card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem; margin: 1rem 0; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-top: 0.5rem; }
        .settings-grid label { display: flex; flex-direction: column; gap: 0.35rem; font-weight: 700; color: #cbd5e1; }
        .settings-grid input { padding: 0.55rem 0.75rem; border-radius: 10px; border: 1px solid #1f2937; background: #0a0f1b; color: #e5e7eb; }
        .secondary { background: #1f2937; color: #cbd5e1; }
        .settings-header { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; }
        .settings-header .meta { margin: 0; }
    </style>
</head>
<body data-script-name=\"{script_name}\">
    <h1>Logs for {script_name}</h1>
    <p class=\"meta\">Live output is streamed here so you can keep the main control panel clean.</p>
    <div class=\"controls\">
        <span class=\"badge\" id=\"line-count\">0 lines</span>
        <button id=\"refresh-btn\">Refresh</button>
        <button id=\"save-log-btn\">Save log</button>
    </div>
    <div class=\"settings-card\" id=\"bybit-settings\" style=\"display:none;\">
        <div class=\"settings-header\">
            <div>
                <strong>Bybit monitor settings</strong>
                <p class=\"meta\">Adjust scan interval and alert threshold without restarting.</p>
            </div>
            <span class=\"badge\" id=\"bybit-settings-status\">&nbsp;</span>
        </div>
        <div class=\"settings-grid\">
            <label>Wait between scans (seconds)
                <input type=\"number\" min=\"1\" step=\"1\" id=\"bybit-wait-seconds\" />
            </label>
            <label>Alert threshold (% change)
                <input type=\"number\" min=\"0.1\" step=\"0.1\" id=\"bybit-threshold\" />
            </label>
        </div>
        <div class=\"controls\">
            <button id=\"bybit-save-settings\">Save settings</button>
            <button class=\"secondary\" id=\"bybit-reload-settings\">Reset</button>
        </div>
    </div>
    <pre id=\"log-box\">Loading logs...</pre>

    <script>
        window.RENDER_LOG_VIEW = {{
            scriptName: {script_name_json}
        }};
    </script>
    <script src=\"/static/log_viewer.js\"></script>
</body>
</html>"""


PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
PROXY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
PROXY_STRIP_HEADERS = {"content-encoding", "content-length"}

PAYSLIP_AUDIT_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Payslip Audit Upload</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 960px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; line-height: 1.5; }
        .drop-zone { border: 2px dashed #334155; border-radius: 14px; padding: 2rem; text-align: center; background: #0a0f1b; transition: border-color 0.2s ease, background 0.2s ease; }
        .drop-zone.dragover { border-color: #38bdf8; background: #0b1930; }
        .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; }
        button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
        .primary { background: #22c55e; color: #052e16; }
        .secondary { background: #334155; color: #e2e8f0; }
        .status { margin-top: 1rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        ul { margin: 0.5rem 0 0; padding-left: 1.25rem; color: #cbd5e1; }
        .badge { display: inline-block; padding: 0.35rem 0.65rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; font-size: 0.9rem; }
        .log { background: #0a0f1b; border: 1px solid #1f2937; border-radius: 10px; padding: 0.75rem; margin-top: 1rem; white-space: pre-wrap; color: #e5e7eb; min-height: 120px; }
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>Payslip Audit Upload</h1>
        <p class=\"meta\">Upload the payslip PDF plus all related timesheet screenshots. Drag and drop the files into the window below or use the file picker. The audit will begin automatically once the uploads are validated.</p>
        <div class=\"badge\">Step 1</div>
        <h3>Upload payslip and timesheets</h3>
        <div id=\"drop-zone\" class=\"drop-zone\">
            <p><strong>Drag &amp; drop your payslip PDF and timesheet images here</strong></p>
            <p class=\"meta\">Accepted formats: PDF, JPG, JPEG, PNG. The payslip file is required along with at least one timesheet image.</p>
            <input id=\"file-input\" type=\"file\" multiple accept=\".pdf,.jpg,.jpeg,.png\" style=\"display:none\" />
            <div class=\"actions\">
                <button id=\"pick-btn\" class=\"secondary\">Choose files</button>
                <button id=\"clear-btn\" class=\"secondary\">Clear selection</button>
            </div>
            <ul id=\"file-list\"></ul>
        </div>

        <div class=\"badge\" style=\"margin-top:1.5rem\">Step 2</div>
        <h3>Run audit</h3>
        <p class=\"meta\">When you are ready, start the upload. The report will download automatically after the audit finishes.</p>
        <div class=\"actions\">
            <button id=\"upload-btn\" class=\"primary\">Upload &amp; Start Audit</button>
            <a href=\"/\" class=\"secondary\" style=\"text-decoration:none; display:inline-flex; align-items:center;\">Back to dashboard</a>
        </div>
        <div id=\"status\" class=\"status\">Select your payslip PDF and timesheet screenshots to begin.</div>
        <div id=\"log\" class=\"log\">Awaiting upload...</div>
    </div>

    <script>
        window.PAYSLIP_AUDIT_CONFIG = {
            uploadEndpoint: '/api/payslip-audit/run',
            reportBase: '/api/payslip-audit/report/',
        };
    </script>
    <script src=\"/static/payslip_audit.js\"></script>
</body>
</html>"""




@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML_TEMPLATE.replace("{asset_version}", ASSET_VERSION)


@app.get("/category/{category}", response_class=HTMLResponse)
async def category_page(category: str) -> str:
    safe_category = html.escape(category)
    return CATEGORY_TEMPLATE.replace("{category}", safe_category)


@app.get("/scripts/view/{script_name:path}", response_class=HTMLResponse)
async def script_page(script_name: str) -> str:
    script = script_manager.get(script_name)
    safe_name = html.escape(script.name)
    has_ui = "true" if script.name in WEB_APPS else "false"
    return (
        SCRIPT_PAGE_TEMPLATE.replace("{script_name}", safe_name).replace("{has_ui}", has_ui)
    )




@app.get("/payslip-audit", response_class=HTMLResponse)
async def payslip_audit_page() -> str:
    return PAYSLIP_AUDIT_TEMPLATE


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    return JSONResponse(script_manager.list_scripts())


def _read_bybit_settings() -> Dict[str, float]:
    try:
        settings = bybit_monitor.get_runtime_settings(force=True)
        settings["push_ready"] = bybit_monitor.push_notifications_ready()
        return settings
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to load settings: {exc}") from exc


def _update_bybit_settings(payload: Dict[str, object]) -> Dict[str, float]:
    try:
        wait_seconds = payload.get("wait_seconds") if isinstance(payload, dict) else None
        percent_threshold = payload.get("percent_threshold") if isinstance(payload, dict) else None
        return bybit_monitor.update_runtime_settings(
            wait_seconds=int(wait_seconds) if wait_seconds is not None else None,
            percent_threshold=float(percent_threshold) if percent_threshold is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}") from exc




def _render_log_view(script_name: str) -> str:
    """Return the HTML log viewer for a known script."""

    safe_name = html.escape(script_name)
    return (
        LOG_VIEWER_TEMPLATE.replace("{script_name}", safe_name)
        .replace("{script_name_json}", json.dumps(script_name))
    )


@app.get("/logs/view/{script_name:path}", response_class=HTMLResponse)
async def view_logs(script_name: str) -> str:
    # Ensure the script exists so we don't render a viewer for an unknown path.
    script_manager.get(script_name)
    return _render_log_view(script_name)


@app.api_route("/apps/{script_name:path}", methods=PROXY_METHODS)
@app.api_route("/apps/{script_name:path}/{path:path}", methods=PROXY_METHODS)
async def proxy_app(script_name: str, request: Request, path: str = "") -> Response:
    script = script_manager.get(script_name)
    if not script.is_running or not script.port:
        raise HTTPException(status_code=404, detail=f"{script_name} is not running.")

    target = f"http://127.0.0.1:{script.port}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    body = await request.body()

    async with httpx.AsyncClient(follow_redirects=False) as client:
        resp = await client.request(
            request.method,
            target,
            content=body,
            headers=headers,
        )

    filtered_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in PROXY_HOP_HEADERS | PROXY_STRIP_HEADERS
    }
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=filtered_headers,
    )


@app.get("/api/bybit-monitor/settings")
async def bybit_monitor_settings() -> JSONResponse:
    return JSONResponse(_read_bybit_settings())


@app.post("/api/bybit-monitor/settings")
async def update_bybit_monitor_settings(payload: Dict[str, object]) -> JSONResponse:
    return JSONResponse(_update_bybit_settings(payload))



@app.post("/api/bybit-monitor/push-test")
async def bybit_monitor_push_test() -> JSONResponse:
    try:
        result = bybit_monitor.send_push_test()
        configured = bool(result.get("configured"))
        status_code = 200 if (result.get("sent") or not configured) else 400
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500, detail=f"Failed to send Telegram alert test: {exc}"
        ) from exc


async def _background_start(script: ManagedScript) -> None:
    """Start a script without tying its output or failures to the HTTP response."""

    try:
        await script.start()
    except Exception as exc:  # pragma: no cover - runtime protection
        # Capture failures in the per-script log instead of surfacing them to the caller.
        script.add_log(f"Failed to start: {exc}")


@app.post("/scripts/{script_name:path}/start")
async def start_script(script_name: str) -> JSONResponse:
    # Never launch the payslip audit script directly; force users to the upload flow
    # so the required files can be provided first.
    if script_name == "payslip_audit":
        return JSONResponse(
            {
                "redirect": "/payslip-audit",
                "detail": "Upload your payslip PDF and timesheets to begin the audit.",
            }
        )

    script = script_manager.get(script_name)

    if script.is_running:
        return JSONResponse({"status": "already_running", **script.to_summary()})

    if script.name in WEB_APPS and script.port is None:
        script.port = _allocate_port()

    asyncio.create_task(_background_start(script))

    # Respond immediately so no script output can leak into the HTTP response cycle.
    return JSONResponse({"status": "starting", **script.to_summary()}, status_code=202)


@app.post("/scripts/{script_name:path}/stop")
async def stop_script(script_name: str) -> JSONResponse:
    try:
        summary = await script_manager.stop(script_name)
        return JSONResponse(summary)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to stop {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/logs/", include_in_schema=False)
async def logs_index() -> RedirectResponse:
    return RedirectResponse("/")


@app.get("/api/logs/", include_in_schema=False)
@app.get("/api/logs", include_in_schema=False)
async def api_logs_root(
    request: Request,
    cursor: int = 0,
    script: Optional[str] = None,
    name: Optional[str] = None,
) -> JSONResponse:
    """Compatibility endpoint for fetching logs without embedding the script in the path.

    The log viewer historically called `/api/logs/` with only a `cursor` query param. Try to
    resolve the script name from explicit `script`/`name` params or, as a last resort, from
    the referer header that points back to `/logs/view/<script>`.
    """

    script_name = script or name

    if not script_name:
        referer = request.headers.get("referer") or request.headers.get("referrer")
        if referer:
            parsed = urlparse(referer)
            path = parsed.path.rstrip("/")
            if path.startswith("/logs/view/"):
                script_name = unquote(path.split("/logs/view/", 1)[1])

    if not script_name:
        # Keep the shape consistent with the standard log endpoint while remaining a 200
        # response so the UI can render gracefully.
        return JSONResponse({"lines": [], "cursor": 0, "detail": "No script specified"})

    try:
        snapshot = script_manager.log_snapshot(script_name, cursor)
        return JSONResponse(snapshot)
    except HTTPException as exc:
        if exc.status_code == 404:
            return JSONResponse(
                {"lines": [], "cursor": 0, "detail": exc.detail}, status_code=200
            )
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to read logs for {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/api/logs/{script_name:path}")
async def api_logs(script_name: str, cursor: int = 0) -> JSONResponse:
    try:
        snapshot = script_manager.log_snapshot(script_name, cursor)
        return JSONResponse(snapshot)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to read logs for {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.get("/logs/{script_name:path}")
async def read_logs(script_name: str) -> JSONResponse:
    try:
        return JSONResponse(script_manager.logs(script_name))
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to read logs for {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


@app.post("/api/payslip-audit/run")
async def upload_and_run_payslip_audit(files: List[UploadFile] = File(...)) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Please upload a payslip PDF and at least one timesheet image.")

    ensure_tesseract_available()

    session_id = uuid4().hex
    session_dir = _payslip_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    saved_files: List[Path] = []
    for upload in files:
        filename = Path(upload.filename or "upload").name
        destination = session_dir / filename
        destination.write_bytes(await upload.read())
        saved_files.append(destination)

    payslips = [path for path in saved_files if path.suffix.lower() == ".pdf"]
    timesheets = [path for path in saved_files if path.suffix.lower() in PAYSLIP_ALLOWED_IMAGES]

    if not payslips:
        raise HTTPException(status_code=400, detail="A payslip PDF is required.")
    if not timesheets:
        raise HTTPException(status_code=400, detail="At least one timesheet image (JPG/PNG) is required.")

    output_path = session_dir / PAYSLIP_REPORT_NAME
    log_output = await _execute_payslip_audit(payslips[0], sorted(timesheets), output_path)

    return JSONResponse(
        {
            "session_id": session_id,
            "download_url": f"/api/payslip-audit/report/{session_id}",
            "log": log_output,
        }
    )


@app.get("/api/payslip-audit/report/{session_id}")
async def download_payslip_report(session_id: str) -> FileResponse:
    report_path = _payslip_session_dir(session_id) / PAYSLIP_REPORT_NAME
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found. Please rerun the audit.")
    return FileResponse(report_path, filename=PAYSLIP_REPORT_NAME, media_type="application/pdf")


@app.post("/webhook/{script_name:path}")
async def webhook(script_name: str, request: Request) -> JSONResponse:
    payload = await request.body()
    script = script_manager.get(script_name)
    script.add_log(f"Webhook received: {payload.decode('utf-8', errors='replace')}")

    if script.name == "payslip_audit":
        script.add_log("Webhook ignored: upload flow required via /payslip-audit")
        return JSONResponse(
            {
                "status": "payslip_audit requires upload flow",
                "redirect": "/payslip-audit",
            }
        )

    if not script.is_running:
        await script.start()
    return JSONResponse({"status": "ok", "script": script_name})


@app.get("/health")
async def healthcheck() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/debug/tesseract")
def debug_tesseract() -> JSONResponse:
    import shutil

    return JSONResponse(
        {
            "PATH": os.environ.get("PATH"),
            "which_tesseract": shutil.which("tesseract"),
            "resolved": _resolve_tesseract_binary(),
            "available": is_tesseract_available(),
        }
    )


@app.get("/favicon.ico")
async def favicon() -> RedirectResponse:
    return RedirectResponse(url="https://render.com/favicon.ico")


app.mount("/static", StaticFiles(directory=BASE_DIR / "render" / "static"), name="static")
