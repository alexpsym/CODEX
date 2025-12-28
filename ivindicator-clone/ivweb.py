"""Web UI for the IV indicator clone."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template_string, request

from ivcore import compute_snapshot, fetch_option_symbols
from ivlog import get_logger

app = Flask(__name__)
logger = get_logger(__name__)

DEFAULT_SYMBOL = os.getenv("IV_SYMBOL", "BTCUSDT")
DEFAULT_TIMEFRAME = os.getenv("IV_TIMEFRAME", "1h")
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "").rstrip("/")
HISTORY_LIMIT = 240

_history: Dict[str, List[dict]] = {}
_history_lock = threading.Lock()


def _history_key(symbol: str, timeframe: str, expiry: str | None) -> str:
    return f"{symbol}:{timeframe}:{expiry or 'nearest'}"


def _parse_expiry(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%y-%m-%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _append_history(key: str, snapshot: dict) -> List[dict]:
    point = {
        "timestamp": snapshot["timestamp"],
        "time_local": snapshot["time_local"],
        "spot": snapshot["spot"],
        "upper": snapshot["upper"],
        "lower": snapshot["lower"],
    }
    with _history_lock:
        series = _history.setdefault(key, [])
        series.append(point)
        if len(series) > HISTORY_LIMIT:
            series[:] = series[-HISTORY_LIMIT:]
        return list(series)


@app.get("/")
def index() -> str:
    return render_template_string(
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>IV Indicator</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
  <style>
    body { background: #0b1020; color: #e2e8f0; font-family: Arial, sans-serif; margin: 0; }
    header { padding: 20px 30px; border-bottom: 1px solid #1f2937; }
    h1 { margin: 0; font-size: 24px; }
    .controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 12px; }
    select, button { background: #111827; color: #e2e8f0; border: 1px solid #334155; padding: 6px 10px; border-radius: 6px; }
    button { cursor: pointer; }
    main { display: grid; grid-template-columns: 2fr 1fr; gap: 24px; padding: 24px 30px; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 16px; }
    .stat { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1f2937; font-size: 14px; }
    .stat:last-child { border-bottom: none; }
    .muted { color: #94a3b8; font-size: 12px; }
    canvas { width: 100%; height: 420px; }
  </style>
</head>
<body>
  <header>
    <h1>IV Indicator (Render)</h1>
    <div class="controls">
      <label>
        Symbol
        <select id="symbol">
          <option value="BTCUSDT">BTCUSDT</option>
          <option value="ETHUSDT">ETHUSDT</option>
          <option value="SOLUSDT">SOLUSDT</option>
        </select>
      </label>
      <label>
        Timeframe
        <select id="timeframe">
          <option value="1m">1m</option>
          <option value="5m">5m</option>
          <option value="15m">15m</option>
          <option value="30m">30m</option>
          <option value="1h" selected>1h</option>
          <option value="4h">4h</option>
          <option value="1d">1d</option>
          <option value="1w">1w</option>
          <option value="1mo">1mo</option>
        </select>
      </label>
      <button id="refresh">Refresh now</button>
      <button id="download">Download Screenshot</button>
      <span class="muted" id="status">Waiting for data...</span>
    </div>
  </header>
  <main>
    <div class="card">
      <canvas id="ivChart"></canvas>
    </div>
    <div class="card">
      <h3>Snapshot</h3>
      <div id="stats"></div>
    </div>
  </main>
<script>
  const statusEl = document.getElementById('status');
  const symbolEl = document.getElementById('symbol');
  const timeframeEl = document.getElementById('timeframe');
  const statsEl = document.getElementById('stats');
  const chartCtx = document.getElementById('ivChart');
  const defaultSymbol = "{{ default_symbol }}";
  const defaultTimeframe = "{{ default_timeframe }}";
  const appRoot = "{{ app_root }}";
  let chart;

  function buildAppUrl(path){
    if(!path){return appRoot || '';}
    if(appRoot){
      return `${appRoot}${path.startsWith('/') ? '' : '/'}${path}`;
    }
    return path.startsWith('/') ? path : `/${path}`;
  }

  function buildStats(snapshot){
    const rows = [
      ['Time', snapshot.time_local],
      ['IV %', snapshot.iv_percent.toFixed(2)],
      ['Spot', snapshot.spot.toFixed(2)],
      ['Upper', snapshot.upper.toFixed(2)],
      ['Lower', snapshot.lower.toFixed(2)],
      ['Move', snapshot.move.toFixed(2)],
      ['Skew', snapshot.skew ? snapshot.skew.toFixed(2) + '%' : 'n/a'],
      ['Call Vol', snapshot.call_volume.toLocaleString()],
      ['Put Vol', snapshot.put_volume.toLocaleString()],
      ['Call OI', snapshot.call_open_interest.toLocaleString()],
      ['Put OI', snapshot.put_open_interest.toLocaleString()],
      ['Expiry', snapshot.expiry]
    ];
    statsEl.innerHTML = rows.map(([label, value]) => (
      `<div class="stat"><span>${label}</span><span>${value}</span></div>`
    )).join('');
  }

  function updateChart(history){
    const labels = history.map(point => point.time_local.split(' ')[1]);
    const spot = history.map(point => point.spot);
    const upper = history.map(point => point.upper);
    const lower = history.map(point => point.lower);
    if(!chart){
      chart = new Chart(chartCtx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Spot', data: spot, borderColor: '#60a5fa', tension: 0.25 },
            { label: 'Upper', data: upper, borderColor: '#fbbf24', tension: 0.25 },
            { label: 'Lower', data: lower, borderColor: '#f87171', tension: 0.25 }
          ]
        },
        options: {
          responsive: true,
          scales: {
            x: { ticks: { color: '#94a3b8' } },
            y: { ticks: { color: '#94a3b8' } }
          },
          plugins: { legend: { labels: { color: '#e2e8f0' } } }
        }
      });
      return;
    }
    chart.data.labels = labels;
    chart.data.datasets[0].data = spot;
    chart.data.datasets[1].data = upper;
    chart.data.datasets[2].data = lower;
    chart.update();
  }

  async function fetchData(){
    const symbol = symbolEl.value;
    const timeframe = timeframeEl.value;
    const params = new URLSearchParams({ symbol, timeframe });
    statusEl.textContent = 'Updating...';
    try {
      const response = await fetch(`${buildAppUrl('/data')}?${params.toString()}`);
      const payload = await response.json();
      if(!response.ok){
        throw new Error(payload.error || 'Failed to fetch data');
      }
      buildStats(payload.snapshot);
      updateChart(payload.history);
      statusEl.textContent = `Updated ${payload.snapshot.time_local}`;
    } catch (err){
      statusEl.textContent = err.message;
    }
  }

  async function loadSymbols(){
    try {
      const response = await fetch(buildAppUrl('/symbols'));
      const payload = await response.json();
      if(!response.ok){
        throw new Error(payload.error || 'Failed to load symbols');
      }
      const symbols = payload.symbols || [];
      if(!symbols.length){
        return;
      }
      const current = symbolEl.value;
      symbolEl.innerHTML = symbols.map((symbol) => (
        `<option value="${symbol}">${symbol}</option>`
      )).join('');
      if (current && symbols.includes(current)) {
        symbolEl.value = current;
      } else if (defaultSymbol && symbols.includes(defaultSymbol)) {
        symbolEl.value = defaultSymbol;
      }
    } catch (err){
      console.error(err);
    }
  }

  async function downloadScreenshot(){
    statusEl.textContent = 'Capturing screenshot...';
    const canvas = await html2canvas(document.body, {
      scrollY: -window.scrollY,
      windowWidth: document.documentElement.scrollWidth,
      windowHeight: document.documentElement.scrollHeight,
    });
    const url = canvas.toDataURL('image/png');
    const link = document.createElement('a');
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    link.href = url;
    link.download = `iv-indicator-${timestamp}.png`;
    link.click();
    statusEl.textContent = 'Screenshot downloaded.';
  }

  document.getElementById('refresh').addEventListener('click', fetchData);
  document.getElementById('download').addEventListener('click', downloadScreenshot);
  symbolEl.addEventListener('change', fetchData);
  timeframeEl.addEventListener('change', fetchData);

  if (defaultSymbol) {
    symbolEl.value = defaultSymbol;
  }
  if (defaultTimeframe) {
    timeframeEl.value = defaultTimeframe;
  }

  loadSymbols().then(fetchData);
  setInterval(fetchData, 4000);
</script>
</body>
</html>
        """,
        default_symbol=DEFAULT_SYMBOL,
        default_timeframe=DEFAULT_TIMEFRAME,
        app_root=APP_BASE_PATH,
    )


@app.get("/data")
def data():
    symbol = request.args.get("symbol", DEFAULT_SYMBOL).upper()
    timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)
    expiry = request.args.get("expiry")
    expiry_dt = _parse_expiry(expiry)
    snapshot = compute_snapshot(symbol, timeframe, expiry_dt)
    if "error" in snapshot:
        return jsonify({"error": snapshot["error"]}), 503

    logger.info(
        "IV snapshot: symbol=%s timeframe=%s iv=%.2f%% spot=%.2f move=%.2f expiry=%s",
        snapshot["symbol"],
        snapshot["timeframe"],
        snapshot["iv_percent"],
        snapshot["spot"],
        snapshot["move"],
        snapshot["expiry"],
    )

    history_key = _history_key(symbol, timeframe, expiry)
    history = _append_history(history_key, snapshot)
    return jsonify({"snapshot": snapshot, "history": history})


@app.get("/symbols")
def symbols():
    return jsonify({"symbols": fetch_option_symbols()})


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
