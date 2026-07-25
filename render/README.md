# Render deployment guide for the unified trading runner

This folder contains a lightweight FastAPI app (`render/master_service.py`) that discovers every Python script in this repository—trading bots and anything else except the `mt5-clone` and `LEDGER-clone` folders—and lets you run them concurrently from a single Render starter instance. The app exposes:

- A web UI at `/` for starting/stopping scripts and tailing their logs.
- Generic `/webhook` routes are retired for trading execution. The calculator now emits server-authored TradingView payloads that post to `/api/calculator/webhook`.
- A health check at `/health` for uptime monitoring.

## 1) Local smoke test
1. Create a virtual environment (Python 3.10+ recommended) and install dependencies:
   ```bash
   pip install -r render/requirements.txt
   ```
   The requirements file now bundles the shared dependencies used across the repo's
   scripts (Flask, pandas, bs4, pybit, etc.) so running anything from the master
   control UI won't fail due to missing modules.
2. Add your secrets to `C:\GPT\env.env` (or override with `MASTER_ENV_DIR` / `MASTER_ENV_FILE`; see **Environment variables** below). A repo-root `.env` is optional fallback only.
3. Start the service locally:
   ```bash
   uvicorn render.master_service:app --host 0.0.0.0 --port 8000
   ```
4. Open `http://localhost:8000` to see the control panel. Logs for each script stream into the UI while they run.

## 2) Deploying to Render (single $7/month starter instance)
1. Push this repository to GitHub or GitLab so Render can import it.
2. In the Render dashboard, create a **Web Service** and point it to your repo.
3. Use these settings (Docker deploy recommended):
   - **Runtime**: Docker. Render will build from the root `Dockerfile`, which installs `ffmpeg` for the YouTube mp3 conversion flow. Leave the Build/Start command fields blank so Render honors the Dockerfile (it already runs `uvicorn render.master_service:app --host 0.0.0.0 --port ${PORT:-10000}`).
   - **Instance type**: Starter ($7/mo). This keeps one always-on worker.
4. Add environment variables in the Render dashboard (or create an **Environment Group** and attach it). These are injected into every managed script, so your TradingView credentials, Bybit keys, and OANDA keys are available without committing them to Git.
5. Click **Create Web Service**. Once live, Render will expose a public URL like `https://your-app.onrender.com`. For calculator-generated alerts, point TradingView to `https://your-app.onrender.com/api/calculator/webhook`.

## 3) Environment variables
Place these in your Render dashboard or in your local external env file (`C:\GPT\env.env` by default). Adjust the names to match the scripts you use (examples below come from the existing projects):

- `BYBIT_API_KEY`, `BYBIT_API_SECRET`, and any other Bybit settings used by the Bybit automation scripts.
- `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, and `OANDA_BASE_URL` for OANDA strategies.
- Any Telegram/Discord/API tokens needed by alerting code.
- `PORT` (Render sets this automatically; only override for local testing).
- `MASTER_ENV_FILE` overrides the exact local env file path used by `shared.env_bootstrap`.
- `MASTER_ENV_DIR` overrides the directory searched for `env.env`, `.env`, `scanner.env`, and `master.env` when `MASTER_ENV_FILE` is not explicitly set.
- `AUTOSTART_SCRIPTS` to choose additional scripts that boot automatically. On Render, `fxweekend-clone` is always included as the sole FX Weekend execution authority; values such as `OFF`, `ALL`, or a comma-separated list affect only the other eligible scripts.
- `AUTOSTART_EXCLUDE` to skip matching optional scripts from the resolved autostart list. It cannot remove the Render-owned `fxweekend-clone` executor.
- Any other variables referenced by the individual scripts. Because the manager runs each script in its own subprocess with the shared environment, they will read the same `.env`/dashboard values.

## 4) How the master service works
- On startup it scans the repository for `*.py` files (skipping `mt5-clone`, virtualenv folders, `LEDGER-clone`, and the `render` folder itself) and exposes each remaining entrypoint in the UI.
- Render always supervises exactly one `fxweekend-clone` child, even when `AUTOSTART_SCRIPTS` is `OFF` or `AUTOSTART_EXCLUDE` names it. Durable FX Weekend settings still control whether that child is allowed to act; local profiles never start an FX Weekend fallback.
- Clicking **Start** launches the chosen script as a background subprocess with unbuffered stdout; logs stream into the UI. Multiple scripts can run concurrently.
- Clicking **Stop** sends a graceful terminate signal, escalating to a kill if the script does not exit within 10 seconds.
- The webhook endpoint records the payload to the script log and starts the script if it is not already running.
  

## 5) TradingView webhook routing
Use the calculator UI (`/merged/calculator`) to generate the exact JSON payload and copy it directly into TradingView.

- **Endpoint:** `https://your-app.onrender.com/api/calculator/webhook`
- **Payload source:** always paste the server-authored `webhook_payload_json` from the calculator quote result.
- The API creates a pending-webhook record when the quote is generated (Webhook = Yes), then removes it automatically after a successful execution so Open Orders/Positions does not show duplicates.
- If execution fails, the pending row remains with an error field so you can retry or diagnose the issue.

## 6) Notes and tips
- The manager intentionally ignores the `mt5-clone` directory per your request.
- Scanner (Bybit/OANDA monitor) is local-only and is not part of the Render-hosted merged dashboard. Run `run_scanner_local.bat` on a local Windows machine when you need scanner windows.
- Because each script runs as its own Python process, they can block or run forever without stopping the others. Monitor CPU/RAM usage in Render if you run many at once.
- Logs are kept in-memory (last 400 lines per script) for quick viewing. Persist long-term logs by piping to a file inside each script if needed.
- If you need to pin a different Python binary, set the `PYTHON` environment variable and the manager will use it when spawning scripts.

This setup keeps everything on one Render Starter instance so you can replace SignalStack with your own always-on webhook listener and trading automation hub.

## 7) Local calculator with Render-owned TradingView webhooks
- To generate Render-owned TradingView webhook JSON from the local calculator, set `RENDER_CALCULATOR_BASE_URL=https://<your-render-service>.onrender.com` in `C:\GPT\env.env`, restart local master, then use **Webhook=Yes** in local `/merged/calculator`.
- The webhook URL shown in the local UI should be the Render URL, not localhost.
- The pending webhook record is created on Render (execution owner), so TradingView must post to Render.
- `Webhook=No` calculations remain local and unchanged.
