"""Unified Render-friendly service for running repo scripts with a web UI."""
from __future__ import annotations

import asyncio
import json
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

IGNORE_DIRS = {"render", ".git", "__pycache__", "mt5-clone", ".venv", "env", "venv"}
IGNORE_FILES = {"__init__.py"}
MAX_LOG_LINES = 400


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


def discover_scripts(base_dir: Path) -> List[ManagedScript]:
    scripts: List[ManagedScript] = []
    for path in base_dir.rglob("*.py"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name in IGNORE_FILES:
            continue
        relative = path.relative_to(base_dir)
        name = str(relative)
        scripts.append(ManagedScript(name=name, path=path))
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


script_manager = ScriptManager(discover_scripts(BASE_DIR))
app = FastAPI(title="Render Master Script", version="1.0")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <title>Render Master Control</title>
    <style>
        :root {{ color-scheme: light dark; }}
        body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem; background: #0f172a; color: #e2e8f0; }}
        h1 {{ margin-top: 0; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 1.25rem; margin-bottom: 1rem; box-shadow: 0 12px 30px rgba(0,0,0,0.25); }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; }}
        button {{ padding: 0.55rem 0.9rem; border-radius: 10px; border: none; cursor: pointer; font-weight: 600; }}
        .start {{ background: #22c55e; color: #052e16; }}
        .stop {{ background: #ef4444; color: #fff7ed; }}
        .pill {{ display: inline-block; padding: 0.3rem 0.65rem; border-radius: 999px; font-weight: 700; font-size: 0.9rem; }}
        .running {{ background: #22c55e22; color: #86efac; }}
        .stopped {{ background: #ef444422; color: #fecdd3; }}
        pre {{ background: #0b1220; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; overflow: auto; max-height: 320px; white-space: pre-wrap; }}
        .actions {{ display: flex; gap: 0.5rem; margin-top: 0.5rem; }}
    </style>
</head>
<body>
    <h1>Render Master Control</h1>
    <p>Start, stop, and monitor any Python script in this repository (trading bots, Excel utilities, everything except the mt5-clone folder). Webhooks can be sent to <code>/webhook/&lt;script-name&gt;</code>.</p>
    <div id=\"grid\" class=\"grid\"></div>
<script>
const scripts = __SCRIPTS__;
const grid = document.getElementById('grid');

function statusPill(script) {
    const pill = document.createElement('span');
    pill.className = 'pill ' + (script.running ? 'running' : 'stopped');
    pill.textContent = script.running ? 'Running' : 'Stopped';
    return pill;
}

async function refresh() {
    const response = await fetch('/scripts');
    const data = await response.json();
    grid.innerHTML = '';
    data.forEach(script => {
        const card = document.createElement('div');
        card.className = 'card';
        const header = document.createElement('div');
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        const title = document.createElement('div');
        title.innerHTML = `<strong>${script.name}</strong><br/><small>${script.path}</small>`;
        header.appendChild(title);
        header.appendChild(statusPill(script));
        card.appendChild(header);
        const actions = document.createElement('div');
        actions.className = 'actions';
        const startBtn = document.createElement('button');
        startBtn.className = 'start';
        startBtn.textContent = 'Start';
        startBtn.onclick = () => modify(script.name, 'start');
        const stopBtn = document.createElement('button');
        stopBtn.className = 'stop';
        stopBtn.textContent = 'Stop';
        stopBtn.onclick = () => modify(script.name, 'stop');
        actions.appendChild(startBtn);
        actions.appendChild(stopBtn);
        card.appendChild(actions);
        const logBox = document.createElement('pre');
        logBox.id = `log-${script.name}`;
        logBox.textContent = 'Loading logs...';
        card.appendChild(logBox);
        grid.appendChild(card);
        loadLogs(script.name);
    });
}

async function modify(name, action) {
    await fetch(`/scripts/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
    setTimeout(refresh, 500);
}

async function loadLogs(name) {
    const box = document.getElementById(`log-${name}`);
    if (!box) return;
    const res = await fetch(`/logs/${encodeURIComponent(name)}`);
    const lines = await res.json();
    box.textContent = lines.join('\n');
}

setInterval(() => { refresh(); }, 5000);
refresh();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    scripts_json = json.dumps(script_manager.list_scripts())
    return HTML_TEMPLATE.replace("__SCRIPTS__", scripts_json)


@app.get("/scripts")
async def list_scripts() -> JSONResponse:
    return JSONResponse(script_manager.list_scripts())


@app.post("/scripts/{script_name}/start")
async def start_script(script_name: str) -> JSONResponse:
    summary = await script_manager.start(script_name)
    return JSONResponse(summary)


@app.post("/scripts/{script_name}/stop")
async def stop_script(script_name: str) -> JSONResponse:
    summary = await script_manager.stop(script_name)
    return JSONResponse(summary)


@app.get("/logs/{script_name}")
async def read_logs(script_name: str) -> JSONResponse:
    return JSONResponse(script_manager.logs(script_name))


@app.post("/webhook/{script_name}")
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


app.mount("/static", StaticFiles(directory=BASE_DIR / "render"), name="static")
