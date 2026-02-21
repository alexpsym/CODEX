"""Simple web UI for Bybit history downloads."""

import csv
import os
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, render_template_string, request, send_file

from env_helpers import load_bybit_live_env
import fetch_history
from bybit_credentials import resolve_bybit_credentials

BRISBANE_TZ = ZoneInfo("Australia/Brisbane")

load_bybit_live_env()

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bybit History</title>
<base href="{{ base_href }}">
<style>
body { background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; }
.container { display: flex; justify-content: space-around; margin-top: 50px; }
.box { background-color: #1e1e1e; padding: 20px; border-radius: 8px; width: 40%; }
label { display: block; margin-top: 10px; }
button { margin-top: 10px; padding: 10px; background-color: #333; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
select, input[type="date"] { width: 100%; padding: 5px; margin-top: 5px; background-color: #333; color: #fff; border: none; }
.hint { font-size: 0.85rem; color: #b0b0b0; margin-top: 5px; }
</style>
</head>
<body>
<div class="container">
  <div class="box">
    <h2>Trade History</h2>
    <form action="trade" method="post">
      <label for="trade-start">Start date</label>
      <input id="trade-start" type="date" name="start_date" />
      <label for="trade-end">End date</label>
      <input id="trade-end" type="date" name="end_date" />
      <p class="hint">Leave dates blank to use a quick range.</p>
      <label for="trade-period">Quick range</label>
      <select id="trade-period" name="period">
        <option value="week">Last Week</option>
        <option value="month">Last Month</option>
        <option value="all">All Time</option>
      </select>
      <label for="trade-mode">Environment</label>
      <select id="trade-mode" name="mode">
        <option value="live">Live</option>
        <option value="demo">Demo</option>
        <option value="testnet">Testnet</option>
      </select>
      <p class="hint">Demo uses Bybit Demo Trading account history (7-day retention).</p>
      <button type="submit">Generate</button>
    </form>
  </div>
  <div class="box">
    <h2>USDT Balance History</h2>
    <form action="balance" method="post">
      <label for="balance-start">Start date</label>
      <input id="balance-start" type="date" name="start_date" />
      <label for="balance-end">End date</label>
      <input id="balance-end" type="date" name="end_date" />
      <p class="hint">Leave dates blank to use a quick range.</p>
      <label for="balance-frequency">Frequency</label>
      <select id="balance-frequency" name="freq">
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
        <option value="monthly">Monthly</option>
      </select>
      <label for="balance-period">Quick range</label>
      <select id="balance-period" name="period">
        <option value="week">Last Week</option>
        <option value="month">Last Month</option>
        <option value="all">All Time</option>
      </select>
      <button type="submit">Generate</button>
    </form>
  </div>
</div>
</body>
</html>
"""


def _range_from_period(period: str) -> tuple[str, str]:
    """Return start and end dates for the given period option."""
    now = datetime.now(BRISBANE_TZ)
    if period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        # All time - Bybit only allows the last two years
        start = now - timedelta(days=730)
    return start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")


def open_browser(url: str) -> None:
    """Open the system browser to the given URL."""
    try:
        browser = webbrowser.get("windows-default")
    except webbrowser.Error:
        browser = webbrowser
    browser.open(url)


@app.route("/")
def index() -> str:
    """Render the main menu page."""
    base_href = os.getenv("APP_BASE_PATH", "/").rstrip("/") + "/"
    return render_template_string(HTML_TEMPLATE, base_href=base_href)


@app.post("/trade")
def trade() -> object:
    """Handle trade history export."""
    start = (request.form.get("start_date", "") or "").strip()
    end = (request.form.get("end_date", "") or "").strip()
    mode = (request.form.get("mode", "live") or "live").strip().lower()
    if not start or not end:
        period = request.form.get("period", "week")
        start, end = _range_from_period(period)
    filename = fetch_history.download_history(
        "linear", start, end, None, True, mode_override=mode
    )
    if filename is None:
        return "No transactions found for the selected date range."
    return send_file(filename, as_attachment=True)


@app.post("/balance")
def balance() -> object:
    """Handle USDT balance export."""
    freq = request.form.get("freq", "daily")
    start = (request.form.get("start_date", "") or "").strip()
    end = (request.form.get("end_date", "") or "").strip()
    if not start or not end:
        period = request.form.get("period", "week")
        start, end = _range_from_period(period)
    filename = export_balance_csv(start, end, freq)
    return send_file(filename, as_attachment=True)
def export_balance_csv(start_date: str, end_date: str, freq: str) -> str:
    """Return a CSV file of balances for the given period."""
    # pylint: disable=too-many-locals,too-many-branches,protected-access
    _mode, api_key, api_secret, _base_url, _key_source = resolve_bybit_credentials()
    if not api_key or not api_secret:
        raise EnvironmentError(
            "Bybit API credentials are missing. Provide BYBIT_API_KEY1/BYBIT_API_SECRET1 "
            "(or KEY2 for demo) or legacy BYBIT_API_KEY/BYBIT_API_SECRET."
        )
    if fetch_history.HTTP is None:
        raise ImportError("pybit module is required to download history")
    session = fetch_history.HTTP(api_key=api_key, api_secret=api_secret)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=BRISBANE_TZ)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=BRISBANE_TZ)

    earliest_ms = (
        int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        - fetch_history.TWO_YEARS_MS
        + fetch_history.LIMIT_CUSHION_MS
    )
    earliest_dt = datetime.fromtimestamp(earliest_ms / 1000, tz=BRISBANE_TZ)

    if start_dt < earliest_dt:
        start_dt = earliest_dt
    if end_dt < start_dt:
        end_dt = start_dt
    data: list[tuple[str, float]] = []
    prev_end: float = fetch_history._get_balance_before(
        session, int(start_dt.timestamp() * 1000)
    )
    current = start_dt
    while current <= end_dt:
        if freq == "weekly":
            next_dt = current + timedelta(days=7)
        elif freq == "monthly":
            year = current.year + (current.month) // 12
            month = current.month % 12 + 1
            next_dt = current.replace(year=year, month=month)
        else:
            next_dt = current + timedelta(days=1)
        chunk_logs: list[dict[str, object]] = []
        for chunk_start, chunk_end in fetch_history._date_range_chunks(
            int(current.timestamp() * 1000),
            int((next_dt - timedelta(milliseconds=1)).timestamp() * 1000),
            fetch_history.SEVEN_DAYS_MS,
        ):
            params = {
                "accountType": "UNIFIED",
                "startTime": chunk_start,
                "endTime": chunk_end,
            }
            for page in fetch_history._fetch_transaction_pages(session, **params):
                chunk_logs.extend(page)
        chunk_logs.sort(key=lambda r: int(r.get("transactionTime", 0)))
        end_bal = prev_end if prev_end is not None else 0.0
        for log in chunk_logs:
            coin = str(log.get("coin", ""))
            if not coin:
                continue
            change = fetch_history._pick_float(log, ("change", "cashFlow"))
            usd_delta = fetch_history._pick_float(log, ("usdValue",))
            if coin == "USDT":
                if change is not None:
                    end_bal += change
                elif usd_delta is not None:
                    end_bal += usd_delta
            else:
                if change is not None:
                    price = fetch_history._get_price(
                        session, coin, int(log.get("transactionTime", 0))
                    )
                    end_bal += change * price
                elif usd_delta is not None:
                    end_bal += usd_delta
        if prev_end is None and not chunk_logs:
            resp = session.get_wallet_balance(accountType="UNIFIED")
            coins = resp.get("result", {}).get("list", [{}])[0].get("coin", [])
            end_bal = 0.0
            for coin in coins:
                name = str(coin.get("coin"))
                if not name:
                    continue
                amount = fetch_history._pick_float(
                    coin, ("cashBalance", "walletBalance", "equity")
                )
                usd_value = fetch_history._pick_float(coin, ("usdValue",))
                if name == "USDT":
                    if amount is not None:
                        end_bal += amount
                    elif usd_value is not None:
                        end_bal += usd_value
                else:
                    if amount is not None:
                        price = fetch_history._get_price(
                            session, name, int(current.timestamp() * 1000)
                        )
                        end_bal += amount * price
                    elif usd_value is not None:
                        end_bal += usd_value
        prev_end = end_bal
        label = current.strftime("%Y-%m-%d") if freq == "daily" else (
            current.strftime("%Y-%m-%d") if freq == "weekly" else current.strftime("%Y-%m")
        )
        data.append((label, end_bal))
        current = next_dt
    fname = "usdt_balance_history.csv"
    with open(fname, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Period", "Balance"])
        for label, bal in data:
            writer.writerow([label, bal])
    return fname


def _serve_wsgi(app: Flask, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Serve *app* with a production-grade WSGI HTTP server."""

    try:
        from waitress import serve
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "Running app.py now requires the 'waitress' package. "
            "Install it with 'pip install waitress'."
        ) from exc

    serve(app, host=host, port=port)


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port_value = os.getenv("PORT", "5000")
    try:
        port = int(port_value)
    except ValueError:
        port = 5000
    url = f"http://{host}:{port}"
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
    if sys.stdout.isatty() and not is_render:
        open_browser(url)
    _serve_wsgi(app, host=host, port=port)
