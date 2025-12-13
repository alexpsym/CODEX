"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import asyncio
import html
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from dotenv import load_dotenv

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

PAYSLIP_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class ManagedScript:
    """Represents a runnable Python script managed by the service."""

    name: str
    path: Path
    category: str = "Other"
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


async def _execute_payslip_audit(payslip: Path, timesheets: List[Path], output_path: Path) -> str:
    script_path = BASE_DIR / "payslip_audit" / "payslip_timesheet_audit.py"
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

    <script src=\"/static/dashboard.js\"></script>
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


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML_TEMPLATE


@app.get("/payslip-audit", response_class=HTMLResponse)
async def payslip_audit_page() -> str:
    return PAYSLIP_AUDIT_TEMPLATE


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    return JSONResponse(script_manager.list_scripts())


@app.post("/scripts/{script_name:path}/start")
async def start_script(script_name: str) -> JSONResponse:
    try:
        summary = await script_manager.start(script_name)
        return JSONResponse(summary)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime protection
        detail = f"Failed to start {script_name}: {exc}"
        print(detail)
        raise HTTPException(status_code=500, detail=detail) from exc


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
    if not script.is_running:
        await script.start()
    return JSONResponse({"status": "ok", "script": script_name})


@app.get("/health")
async def healthcheck() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/favicon.ico")
async def favicon() -> RedirectResponse:
    return RedirectResponse(url="https://render.com/favicon.ico")


app.mount("/static", StaticFiles(directory=BASE_DIR / "render" / "static"), name="static")
