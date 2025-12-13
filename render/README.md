# Render deployment guide for the unified trading runner

This folder contains a lightweight FastAPI app (`render/master_service.py`) that discovers every Python script in this repository—trading bots, Excel utilities, and anything else except the `mt5-clone` folder—and lets you run them concurrently from a single Render starter instance. The app exposes:

- A web UI at `/` for starting/stopping scripts and tailing their logs.
- Webhook endpoints at `/webhook/<script-name>` so TradingView alerts can kick off a specific strategy.
- A health check at `/health` for uptime monitoring.

## 1) Local smoke test
1. Create a virtual environment (Python 3.10+ recommended) and install dependencies:
   ```bash
   pip install -r render/requirements.txt
   ```
   The requirements file now bundles the shared dependencies used across the repo's
   scripts (Flask, pandas, bs4, pybit, etc.) so running anything from the master
   control UI won't fail due to missing modules.
2. Add your secrets to a local `.env` file in the repository root (see **Environment variables** below).
3. Start the service locally:
   ```bash
   uvicorn render.master_service:app --host 0.0.0.0 --port 8000
   ```
4. Open `http://localhost:8000` to see the control panel. Logs for each script stream into the UI while they run.

## 2) Deploying to Render (single $7/month starter instance)
1. Push this repository to GitHub or GitLab so Render can import it.
2. In the Render dashboard, create a **Web Service** and point it to your repo.
3. Use these settings:
   - **Environment**: Python 3.10 or newer.
   - **Build Command**: `pip install -r render/requirements.txt`
   - **Start Command**: `uvicorn render.master_service:app --host 0.0.0.0 --port ${PORT:-10000}`
   - **Instance type**: Starter ($7/mo). This keeps one always-on worker.
4. Add environment variables in the Render dashboard (or create an **Environment Group** and attach it). These are injected into every managed script, so your TradingView credentials, Bybit keys, and OANDA keys are available without committing them to Git.
5. Click **Create Web Service**. Once live, Render will expose a public URL like `https://your-app.onrender.com`. Point your TradingView webhooks to `https://your-app.onrender.com/webhook/<script-name>`.

## 3) Environment variables
Place these in your Render dashboard or a local `.env` file at the repo root. Adjust the names to match the scripts you use (examples below come from the existing projects):

- `BYBIT_API_KEY`, `BYBIT_API_SECRET`, and any other Bybit settings used by the Bybit automation scripts.
- `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, and `OANDA_BASE_URL` for OANDA strategies.
- Any Telegram/Discord/API tokens needed by alerting code.
- `PORT` (Render sets this automatically; only override for local testing).
- Any other variables referenced by the individual scripts. Because the manager runs each script in its own subprocess with the shared environment, they will read the same `.env`/dashboard values.

## 4) How the master service works
- On startup it scans the repository for `*.py` files (skipping `mt5-clone`, virtualenv folders, and the `render` folder itself) and exposes each one in the UI.
- Clicking **Start** launches the chosen script as a background subprocess with unbuffered stdout; logs stream into the UI. Multiple scripts can run concurrently.
- Clicking **Stop** sends a graceful terminate signal, escalating to a kill if the script does not exit within 10 seconds.
- The webhook endpoint records the payload to the script log and starts the script if it is not already running.
- Common helpers like `pandas` and `xlwings` are bundled in `requirements.txt` so Excel utilities (for example `LEDGER-clone/earnings_report.py`) can start without missing-module errors.

## 5) TradingView webhook routing
Use the script path shown in the UI (for example `bybit-alert-clone/bybit_altcoin_monitor.py`) as the `<script-name>` in your TradingView webhook URL:
```
https://your-app.onrender.com/webhook/bybit-alert-clone/bybit_altcoin_monitor.py
```
Include any JSON payload your strategy expects. The manager does not modify the payload; it simply logs it and ensures the script is running.

## 6) Notes and tips
- The manager intentionally ignores the `mt5-clone` directory per your request.
- Because each script runs as its own Python process, they can block or run forever without stopping the others. Monitor CPU/RAM usage in Render if you run many at once.
- Logs are kept in-memory (last 400 lines per script) for quick viewing. Persist long-term logs by piping to a file inside each script if needed.
- If you need to pin a different Python binary, set the `PYTHON` environment variable and the manager will use it when spawning scripts.

This setup keeps everything on one Render Starter instance so you can replace SignalStack with your own always-on webhook listener and trading automation hub.
