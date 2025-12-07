"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

SKIP_DIRS = {"render", "mt5-clone", ".venv", "venv", "__pycache__", ".git", "env"}
SKIP_FILES = {"__init__.py"}
MAX_LOG_LINES = 400

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


@dataclass
class ManagedScript:
    """Represents a runnable Python script managed by the service."""

    name: str
    path: Path
    process: Optional[asyncio.subprocess.Process] = None
    _log_lines: List[str] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def to_summary(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
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
        self.process = await asyncio.create_subprocess_exec(
            os.getenv("PYTHON", "python"),
            str(self.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.path.parent),
            env=env,
        )
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


def discover_scripts() -> List[ManagedScript]:
    """Return one ManagedScript per app folder using a chosen entrypoint."""

    scripts: List[ManagedScript] = []

    for app_dir in sorted(BASE_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        if app_dir.name in SKIP_DIRS or app_dir.name.startswith("."):
            continue

        entry_path: Optional[Path] = None
        for candidate in candidate_entrypoints(app_dir):
            if candidate.exists() and candidate.is_file():
                entry_path = candidate
                break

        if entry_path is None:
            py_files = sorted(p for p in app_dir.glob("*.py") if p.name not in SKIP_FILES and not p.name.startswith("test_"))
            if py_files:
                entry_path = py_files[0]

        if entry_path is not None:
            scripts.append(ManagedScript(name=app_dir.name, path=entry_path))

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
    <p class=\"meta\">Start, stop, and monitor any Python script in this repository (everything except the mt5-clone folder). Webhooks can be sent to <code>/webhook/&lt;script-name&gt;</code>.</p>
    <div class=\"toolbar\">
        <button class=\"refresh\" id=\"refresh-btn\">Refresh</button>
        <span id=\"status\" class=\"meta\">Loading scripts...</span>
    </div>
    <div id=\"grid\" class=\"grid\"></div>

    <script src=\"/static/dashboard.js\"></script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML_TEMPLATE


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    return JSONResponse(script_manager.list_scripts())


@app.post("/scripts/{script_name:path}/start")
async def start_script(script_name: str) -> JSONResponse:
    summary = await script_manager.start(script_name)
    return JSONResponse(summary)


@app.post("/scripts/{script_name:path}/stop")
async def stop_script(script_name: str) -> JSONResponse:
    summary = await script_manager.stop(script_name)
    return JSONResponse(summary)


@app.get("/logs/{script_name:path}")
async def read_logs(script_name: str) -> JSONResponse:
    return JSONResponse(script_manager.logs(script_name))


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
