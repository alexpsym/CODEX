"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import asyncio
import html
import json
import contextlib
import os
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from uuid import uuid4

import base64

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from dotenv import load_dotenv

from payslip_audit.tesseract import (
    TESSERACT_MISSING_MESSAGE,
    _resolve_tesseract_binary,
    is_tesseract_available,
)
from YOUTUBE import yt as youtube_downloader

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

SKIP_DIRS = {"render", "mt5-clone", ".venv", "venv", "__pycache__", ".git", "env"}
SKIP_FILES = {"__init__.py"}
MAX_LOG_LINES = 400
PAYSLIP_REPORT_NAME = "audit_report.pdf"
PAYSLIP_UPLOAD_ROOT = BASE_DIR / "render" / "uploads" / "payslip"
PAYSLIP_ALLOWED_IMAGES = {".jpg", ".jpeg", ".png"}

ENTRY_OVERRIDES = {
    "Crypto-Scanner-clone": ["continuous_scan.py", "scan.py"],
    "LEDGER-clone": ["process_entries.py"],
    "PUSH": ["PUSH.py"],
    "YOUTUBE": ["yt.py"],
    "bybit-alert-clone": ["bybit_altcoin_monitor.py"],
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

LOG_FILE_OVERRIDES: Dict[str, Path] = {
    "YOUTUBE": BASE_DIR / "YOUTUBE" / "yt_error_log.txt",
}

YOUTUBE_STORAGE_ROOT = Path(os.getenv("YOUTUBE_STORAGE_ROOT", BASE_DIR / "render" / "uploads" / "youtube"))
YOUTUBE_COOKIES_DIR = YOUTUBE_STORAGE_ROOT
YOUTUBE_DOWNLOAD_DIR = Path(os.getenv("YOUTUBE_DOWNLOAD_DIR", YOUTUBE_STORAGE_ROOT / "downloads"))
YOUTUBE_COOKIES_UPLOAD = YOUTUBE_COOKIES_DIR / "cookies.txt"
YOUTUBE_COOKIES_ENV = YOUTUBE_COOKIES_DIR / "cookies.from_env.txt"
YOUTUBE_MIN_FREE_BYTES = int(os.getenv("YOUTUBE_MIN_FREE_BYTES", "3000000000"))

_YOUTUBE_COOKIE_STATE: Dict[str, Optional[str]] = {"path": None, "source": None, "error": None}


def _chmod_quiet(path: Path) -> None:
    try:
        path.chmod(0o600)
    except PermissionError:
        return


def _set_cookie_state(path: Optional[Path], source: Optional[str], error: Optional[str]) -> None:
    _YOUTUBE_COOKIE_STATE["path"] = str(path) if path else None
    _YOUTUBE_COOKIE_STATE["source"] = source
    _YOUTUBE_COOKIE_STATE["error"] = error


def _initialize_youtube_cookies_from_env() -> None:
    """Load cookies from env or existing upload without leaking contents."""

    env_b64 = os.getenv("YTDLP_COOKIES_B64", "")
    env_path = os.getenv("YTDLP_COOKIES_PATH", "")

    if env_b64:
        try:
            decoded = base64.b64decode(env_b64, validate=True)
            YOUTUBE_COOKIES_ENV.write_bytes(decoded)
            _chmod_quiet(YOUTUBE_COOKIES_ENV)
            _set_cookie_state(YOUTUBE_COOKIES_ENV, "env_b64", None)
            return
        except Exception as exc:  # noqa: BLE001 - safe status only
            _set_cookie_state(None, None, f"Failed to load YTDLP_COOKIES_B64: {exc}")

    if env_path:
        path = Path(env_path)
        if path.exists():
            _set_cookie_state(path, "env_path", None)
            return
        _set_cookie_state(None, None, f"YTDLP_COOKIES_PATH does not exist: {path}")

    if YOUTUBE_COOKIES_UPLOAD.exists():
        _set_cookie_state(YOUTUBE_COOKIES_UPLOAD, "upload", None)
    else:
        _set_cookie_state(None, None, None)


def _log_disk_capacity(path: Path, min_free_bytes: int, log: Callable[[str], None]) -> bool:
    """Record disk free space around ``path`` and return True when above the threshold."""

    usage = shutil.disk_usage(path)
    free_gib = usage.free / (1024**3)
    required_gib = min_free_bytes / (1024**3)
    log(
        f"Disk free at {path}: {free_gib:.2f} GiB available; "
        f"require at least {required_gib:.2f} GiB before starting."
    )

    if usage.free < min_free_bytes:
        log("Insufficient disk space; aborting download request to avoid partial files.")
        return False

    return True


def youtube_cookies_path() -> Optional[str]:
    """Return the active cookies file path, preferring env-sourced secrets."""

    return _YOUTUBE_COOKIE_STATE.get("path")


def youtube_cookies_status() -> Dict[str, Optional[str]]:
    return dict(_YOUTUBE_COOKIE_STATE)

PAYSLIP_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
YOUTUBE_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
_initialize_youtube_cookies_from_env()
YOUTUBE_JOBS: Dict[str, YouTubeJob] = {}


@dataclass
class YouTubeJob:
    job_id: str
    urls: List[str]
    status: str = "pending"
    phase: str = "pending"
    logs: List[str] = field(default_factory=list)
    downloads: List[Dict[str, str]] = field(default_factory=list)
    progress: Dict[str, Optional[object]] = field(
        default_factory=lambda: {
            "percent": None,
            "downloaded_bytes": None,
            "total_bytes": None,
            "speed": None,
            "eta": None,
        }
    )
    last_update: float = field(default_factory=lambda: time.time())
    subscribers: List[asyncio.Queue] = field(default_factory=list)

    def add_log(self, line: str) -> None:
        cleaned = line.rstrip("\n")
        if not cleaned:
            return
        self.logs.append(cleaned)
        if len(self.logs) > MAX_LOG_LINES:
            self.logs = self.logs[-MAX_LOG_LINES :]
        self.last_update = time.time()
        self._publish("log", {"line": cleaned, "timestamp": self.last_update})

    def set_status(self, status: str, phase: Optional[str] = None) -> None:
        self.status = status
        if phase:
            self.phase = phase
        self.last_update = time.time()
        self._publish("state", self.snapshot())

    def finish(self, status: str, phase: Optional[str] = None) -> None:
        self.set_status(status, phase)
        self._publish("finished", self.snapshot())

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.last_update = time.time()
        self._publish("state", self.snapshot())

    def set_progress(self, update: Dict[str, object]) -> None:
        self.progress.update(update)
        self.last_update = time.time()
        self._publish("progress", {**self.progress, "timestamp": self.last_update})

    def add_download(self, filename: str) -> None:
        self.downloads.append({"filename": filename, "url": f"/api/youtube/files/{filename}"})
        self.last_update = time.time()
        self._publish("downloads", self.downloads)

    def snapshot(self) -> Dict[str, object]:
        return {
            "job_id": self.job_id,
            "urls": self.urls,
            "status": self.status,
            "phase": self.phase,
            "logs": self.logs,
            "downloads": self.downloads,
            "progress": self.progress,
            "last_update": self.last_update,
        }

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def _publish(self, event: str, payload: Dict[str, object]) -> None:
        for queue in list(self.subscribers):
            queue.put_nowait((event, payload))


@dataclass
class ManagedScript:
    """Represents a runnable Python script managed by the service."""

    name: str
    path: Path
    category: str = "Other"
    log_file: Optional[Path] = None
    process: Optional[asyncio.subprocess.Process] = None
    _log_lines: List[str] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def to_summary(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "category": self.category,
            "running": self.is_running,
            "return_code": None if self.process is None else self.process.returncode,
        }

    def add_log(self, line: str) -> None:
        cleaned = line.rstrip("\n")
        if cleaned:
            self._log_lines.append(cleaned)
            if len(self._log_lines) > MAX_LOG_LINES:
                self._log_lines = self._log_lines[-MAX_LOG_LINES :]

    def logs(self) -> List[str]:
        if self.log_file is not None:
            try:
                if self.log_file.exists():
                    content = self.log_file.read_text(encoding="utf-8", errors="replace")
                    lines = content.splitlines()
                    return lines[-MAX_LOG_LINES :]
            except Exception as exc:  # pragma: no cover - defensive fallback
                return [f"Unable to read log file {self.log_file}: {exc}"]

        return list(self._log_lines)

    async def start(self) -> None:
        if self.is_running:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"Script not found: {self.path}")

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")

        try:
            self.process = await asyncio.create_subprocess_exec(
                os.getenv("PYTHON", "python"),
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
        "youtube",
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


def _payslip_session_dir(session_id: str) -> Path:
    return PAYSLIP_UPLOAD_ROOT / session_id


TESSERACT_MISSING_DETAIL = TESSERACT_MISSING_MESSAGE


def _find_youtube_job(job_id: str) -> YouTubeJob:
    try:
        return YOUTUBE_JOBS[job_id]
    except KeyError as exc:  # pragma: no cover - runtime path
        raise HTTPException(status_code=404, detail="Job not found") from exc


async def _heartbeat(job: YouTubeJob) -> None:
    while job.status == "running":
        job._publish("heartbeat", {"timestamp": time.time(), "phase": job.phase})
        await asyncio.sleep(2)


async def _run_youtube_job(job: YouTubeJob) -> None:
    job.set_status("running", phase="starting")
    heartbeat_task = asyncio.create_task(_heartbeat(job))

    loop = asyncio.get_running_loop()

    def _log(message: str) -> None:
        loop.call_soon_threadsafe(job.add_log, message)

    def _progress(update: Dict[str, object]) -> None:
        loop.call_soon_threadsafe(job.set_progress, update)

    try:
        if not _log_disk_capacity(YOUTUBE_DOWNLOAD_DIR, YOUTUBE_MIN_FREE_BYTES, _log):
            job.finish("error", phase="insufficient_disk")
            return

        downloaded_paths = await asyncio.to_thread(
            youtube_downloader.download_links,
            job.urls,
            _log,
            youtube_cookies_path(),
            YOUTUBE_DOWNLOAD_DIR,
            _progress,
        )

        for path in downloaded_paths:
            path = Path(path)
            if path.parent == YOUTUBE_DOWNLOAD_DIR and path.is_file():
                job.add_download(path.name)

        if downloaded_paths:
            job.finish("completed", phase="finished")
        else:
            job.finish("error", phase="failed")

    except Exception:  # noqa: BLE001 - surface full stack to log
        _log("Unexpected error while running yt-dlp:")
        _log(traceback.format_exc())
        job.finish("error", phase="exception")
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


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
        if app_dir.name in SKIP_DIRS or app_dir.name.startswith("."):
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

    def list_scripts(self) -> List[Dict[str, object]]:
        return sorted((script.to_summary() for script in self._scripts.values()), key=lambda s: s["name"])

    def get(self, name: str) -> ManagedScript:
        try:
            return self._scripts[name]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Script not found") from exc

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


script_manager = ScriptManager(discover_scripts())
app = FastAPI(title="Render Master Script", version="1.0")


ASSET_VERSION = "v7"

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
        .path { color: #94a3b8; font-size: 0.9rem; word-break: break-all; }
        .pill { display: inline-block; padding: 0.3rem 0.65rem; border-radius: 999px; font-weight: 700; font-size: 0.9rem; }
        .running { background: #22c55e22; color: #86efac; }
        .stopped { background: #ef444422; color: #fecdd3; }
        .actions { display: flex; gap: 0.5rem; margin: 0.75rem 0; }
        button { padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 700; }
        .start { background: #22c55e; color: #052e16; }
        .stop { background: #ef4444; color: #fff7ed; }
        pre { background: #0a0f1b; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; overflow: auto; max-height: 260px; white-space: pre-wrap; margin: 0; }
        .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
        .refresh { background: #3b82f6; color: #eaf2ff; }
    </style>
</head>
<body>
    <h1>Render Master Control</h1>
    <p class=\"meta\">Pick a category to see its scripts. From there you can start, stop, and monitor anything in this repository (everything except the mt5-clone folder). Webhooks can be sent to <code>/webhook/&lt;script-name&gt;</code>.</p>
    <div class=\"toolbar\">
        <button class=\"refresh\" id=\"refresh-btn\">Refresh</button>
        <span id=\"status\" class=\"meta\">Loading scripts...</span>
    </div>
    <div id=\"grid\" class=\"grid\"></div>

    <script src=\"/static/dashboard.js?ver={asset_version}\"></script>
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
    <pre id=\"log-box\">Loading logs...</pre>

    <script>
        window.RENDER_LOG_VIEW = {{
            scriptName: {script_name_json}
        }};
    </script>
    <script src=\"/static/log_viewer.js\"></script>
</body>
</html>"""

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


YOUTUBE_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>YouTube Downloader</title>
    <style>
        :root { color-scheme: light dark; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0b1220; color: #e2e8f0; }
        h1 { margin-top: 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 1.5rem; max-width: 960px; margin: 0 auto; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); }
        .meta { color: #94a3b8; margin-bottom: 0.75rem; line-height: 1.5; }
        .input { width: 100%; min-height: 120px; border-radius: 10px; border: 1px solid #1f2937; background: #0a0f1b; color: #e2e8f0; padding: 0.75rem; font-size: 1rem; }
        .actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; align-items: center; }
        button { padding: 0.7rem 1.2rem; border-radius: 12px; border: none; cursor: pointer; font-weight: 700; }
        .primary { background: #22c55e; color: #052e16; }
        .secondary { background: #334155; color: #e2e8f0; text-decoration: none; display: inline-flex; align-items: center; }
        .status { margin-top: 1rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        .badge { display: inline-block; padding: 0.35rem 0.7rem; border-radius: 999px; background: #1f2937; color: #cbd5e1; font-weight: 700; font-size: 0.95rem; }
        .log { background: #0a0f1b; border: 1px solid #1f2937; border-radius: 10px; padding: 0.75rem; margin-top: 1rem; white-space: pre-wrap; color: #e5e7eb; min-height: 140px; }
        .downloads { margin-top: 1rem; display: flex; flex-direction: column; gap: 0.4rem; }
        .download-row { display: flex; gap: 0.5rem; align-items: center; color: #cbd5e1; }
        .download-row a { color: #bef264; text-decoration: none; font-weight: 700; }
        .download-row code { background: #0b1220; padding: 0.15rem 0.4rem; border-radius: 8px; border: 1px solid #1f2937; color: #e2e8f0; }
        .progress-row { display: flex; gap: 0.75rem; align-items: center; margin: 0.5rem 0 1rem; }
        .spinner { width: 28px; height: 28px; border: 3px solid #1f2937; border-top-color: #3b82f6; border-radius: 50%; animation: spin 1s linear infinite; }
        .progress-shell { flex: 1; }
        .progress-track { background: #0b1220; border: 1px solid #1f2937; border-radius: 10px; height: 12px; overflow: hidden; }
        .progress-bar { height: 100%; background: linear-gradient(90deg, #3b82f6, #22c55e); width: 0%; transition: width 0.3s ease; }
        .meta-row { display: flex; gap: 1rem; flex-wrap: wrap; color: #cbd5e1; font-size: 0.9rem; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>YouTube Downloader</h1>
        <p class=\"meta\">Paste one or more media URLs (YouTube, etc.). The downloader will fetch best audio and convert to mp3 using yt-dlp.</p>
        <textarea id=\"url-input\" class=\"input\" placeholder=\"Enter one or more URLs, separated by spaces or commas\"></textarea>
        <div class=\"actions\">
            <button id=\"download-btn\" class=\"primary\">Start Download</button>
            <a href=\"/\" class=\"secondary\">Back to dashboard</a>
        </div>
        <div class=\"badge\">Authentication</div>
        <p class=\"meta\">Some videos require a signed-in session. Upload a <code>cookies.txt</code> exported from your YouTube account or set the <code>YTDLP_COOKIES_B64</code> env var (base64 of the file). The server stores the file privately and never logs its contents.</p>
        <div class=\"actions\">
            <input id=\"cookies-file\" type=\"file\" accept=\".txt\" style=\"display:none\" />
            <button id=\"upload-cookies-btn\" class=\"secondary\">Upload cookies.txt</button>
            <span id=\"cookie-status\" class=\"meta\">Checking cookies status...</span>
        </div>
        <div id=\"status\" class=\"status\">Ready to download.</div>
        <div id=\"progress-row\" class=\"progress-row\" style=\"display:none\"> 
            <div class=\"spinner\" id=\"spinner\"></div>
            <div class=\"progress-shell\">
                <div class=\"meta\" id=\"phase\">Awaiting start...</div>
                <div class=\"progress-track\">
                    <div class=\"progress-bar\" id=\"progress-bar\"></div>
                </div>
                <div class=\"meta-row\">
                    <span id=\"progress-text\">0% — pending</span>
                    <span id=\"last-update\"></span>
                </div>
            </div>
        </div>
        <div id=\"log\" class=\"log\">Logs will appear here.</div>
        <div class=\"badge\">Downloads</div>
        <p class=\"meta\">Completed mp3 files are saved on the server and exposed below for download.</p>
        <div id=\"downloads\" class=\"downloads\"></div>
    </div>

    <script src=\"/static/youtube.js?ver={asset_version}\"></script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML_TEMPLATE.replace("{asset_version}", ASSET_VERSION)


@app.get("/youtube", response_class=HTMLResponse)
async def youtube_page() -> str:
    return YOUTUBE_TEMPLATE.replace("{asset_version}", ASSET_VERSION)


@app.get("/payslip-audit", response_class=HTMLResponse)
async def payslip_audit_page() -> str:
    return PAYSLIP_AUDIT_TEMPLATE


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    return JSONResponse(script_manager.list_scripts())


@app.post("/api/youtube/download")
async def youtube_download(payload: Dict[str, str] = Body(...)) -> JSONResponse:
    raw_urls = str(payload.get("urls", "")).strip()
    if not raw_urls:
        raise HTTPException(status_code=400, detail="Please provide at least one URL to download.")

    urls = sorted(youtube_downloader._parse_urls(raw_urls))  # noqa: SLF001 - reuse existing parser
    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs found in the request.")

    job_id = str(uuid4())
    job = YouTubeJob(job_id=job_id, urls=urls, status="queued", phase="queued")
    YOUTUBE_JOBS[job_id] = job

    job.add_log(f"Queued {len(urls)} URL(s) for download.")
    job.add_log(f"Using cookies file: {youtube_cookies_path() or 'none'}")

    asyncio.create_task(_run_youtube_job(job))

    return JSONResponse({"job_id": job_id, "status": job.status})


@app.post("/api/youtube/cookies")
async def upload_youtube_cookies(file: UploadFile = File(...)) -> JSONResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded cookies file is empty.")

    if len(data) > 1_000_000:
        raise HTTPException(status_code=413, detail="Cookies file is too large.")

    YOUTUBE_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    YOUTUBE_COOKIES_UPLOAD.write_bytes(data)
    _chmod_quiet(YOUTUBE_COOKIES_UPLOAD)
    _set_cookie_state(YOUTUBE_COOKIES_UPLOAD, "upload", None)

    return JSONResponse(
        {
            "detail": "Cookies uploaded. Future downloads will use this file.",
            "source": "upload",
            "path": str(YOUTUBE_COOKIES_UPLOAD),
        }
    )


@app.get("/api/youtube/cookies/status")
async def youtube_cookies_status_api() -> JSONResponse:
    status = youtube_cookies_status()
    status.update({"configured": bool(status.get("path"))})
    return JSONResponse(status)


@app.get("/api/youtube/files/{filename}")
async def youtube_download_file(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    target = YOUTUBE_DOWNLOAD_DIR / safe_name

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Requested file not found.")

    return FileResponse(
        target,
        media_type="audio/mpeg",
        filename=safe_name,
    )


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


def _sse_event(event: str, data: Dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/youtube/jobs/{job_id}")
async def youtube_job_status(job_id: str) -> JSONResponse:
    job = _find_youtube_job(job_id)
    return JSONResponse(job.snapshot())


@app.get("/api/youtube/jobs/{job_id}/events")
async def youtube_job_events(job_id: str) -> StreamingResponse:
    job = _find_youtube_job(job_id)
    queue = job.subscribe()

    async def event_stream():
        try:
            yield _sse_event("state", job.snapshot())
            while True:
                event, payload = await queue.get()
                yield _sse_event(event, payload)
                if event in {"finished", "error"}:
                    break
        finally:
            job.unsubscribe(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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

    if script_name == "YOUTUBE":
        return JSONResponse(
            {
                "redirect": "/youtube",
                "detail": "Open the YouTube downloader UI to submit URLs.",
            }
        )

    script = script_manager.get(script_name)

    if script.is_running:
        return JSONResponse({"status": "already_running", **script.to_summary()})

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
