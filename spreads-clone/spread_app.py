"""Local Spread Monitor web app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from flask import Flask, jsonify, render_template_string

from mt5_spreads import available_mt5_symbols, fetch_mt5_spread_samples, preflight_mt5_environment
from oanda_spreads import fetch_oanda_spread_samples, get_available_oanda_symbols
from spread_core import SpreadMonitorState
from symbols import build_symbol_universe


ROOT_DIR = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT_DIR / "render" / "data" / "spread_monitor_cache.json"
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "").rstrip("/")

app = Flask(__name__)


def _symbol_provider() -> Iterable[str]:
    oanda_symbols = get_available_oanda_symbols()
    try:
        mt5_symbols = available_mt5_symbols()
    except Exception:
        mt5_symbols = []
    return build_symbol_universe(oanda_symbols=oanda_symbols, mt5_symbols=mt5_symbols)


STATE = SpreadMonitorState(
    CACHE_PATH,
    symbol_provider=_symbol_provider,
    oanda_fetcher=fetch_oanda_spread_samples,
    mt5_fetcher=fetch_mt5_spread_samples,
    mt5_preflight=preflight_mt5_environment,
)


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Spread Monitor</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0;
      background: #0b1220;
      color: #e2e8f0;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 22px;
      border-bottom: 1px solid #243044;
      background: #111827;
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      color: #94a3b8;
      font-size: 13px;
    }
    button {
      border: 1px solid #3b82f6;
      background: #2563eb;
      color: #eff6ff;
      border-radius: 6px;
      padding: 7px 12px;
      font-weight: 800;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: wait; }
    main { padding: 18px 22px 26px; }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
      color: #cbd5e1;
      font-size: 13px;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .legend-swatch {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      border: 1px solid currentColor;
    }
    .table-wrap {
      width: 100%;
      overflow: auto;
      max-height: calc(100vh - 230px);
      border: 1px solid #243044;
      border-radius: 8px;
      background: #0f172a;
    }
    table {
      width: max-content;
      min-width: 1620px;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid #243044;
      border-right: 1px solid #243044;
      padding: 10px 9px;
      vertical-align: top;
      text-align: left;
      font-size: 12px;
    }
    th:last-child, td:last-child { border-right: 0; }
    tr:last-child td { border-bottom: 0; }
    thead th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #111827;
      color: #cbd5e1;
      font-size: 12px;
      text-align: center;
    }
    thead th.sortable { cursor: pointer; user-select: none; }
    thead th.sortable::after {
      content: attr(data-sort-indicator);
      margin-left: 6px;
      color: #94a3b8;
    }
    .symbol-col {
      width: 132px;
      min-width: 132px;
      font-weight: 900;
      color: #f8fafc;
      position: sticky;
      left: 0;
      z-index: 2;
      background: #111827;
    }
    th:not(.symbol-col), td:not(.symbol-col) {
      width: 164px;
      min-width: 164px;
      max-width: 164px;
    }
    .broker-line {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      margin: 2px 0;
      line-height: 1.35;
      min-width: 0;
      flex-wrap: wrap;
    }
    .broker-name {
      color: #94a3b8;
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .broker-value {
      flex: 0 0 auto;
      max-width: 76px;
      text-align: right;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: clip;
      font-variant-numeric: tabular-nums;
    }
    .broker-error {
      flex: 0 0 100%;
      color: #94a3b8;
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .spread-low { color: #86efac; }
    .spread-medium { color: #facc15; }
    .spread-high { color: #fb7185; }
    .spread-unavailable { color: #64748b; }
    .messages {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
    }
    .message {
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 9px 11px;
      background: #111827;
      color: #cbd5e1;
      font-size: 13px;
      line-height: 1.4;
    }
    .message.error { border-color: #7f1d1d; color: #fecdd3; }
    .empty {
      padding: 22px;
      color: #94a3b8;
      text-align: center;
    }
    @media (max-width: 720px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 14px; }
      table { min-width: 1500px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Spread Monitor</h1>
      <div class="meta">
        <span id="last-refresh">Last refresh: waiting</span>
        <span id="next-refresh">Auto-refresh: 5 min</span>
        <span id="status">Loading cache...</span>
      </div>
    </div>
    <button id="refresh-btn" type="button">Refresh</button>
  </header>
  <main>
    <div class="legend" aria-label="Spread percentile legend">
      <span class="legend-item spread-low"><span class="legend-swatch"></span>Low</span>
      <span class="legend-item spread-medium"><span class="legend-swatch"></span>Medium</span>
      <span class="legend-item spread-high"><span class="legend-swatch"></span>High</span>
      <span class="legend-item spread-unavailable"><span class="legend-swatch"></span>Unavailable</span>
    </div>
    <div class="messages" id="messages"></div>
    <div class="table-wrap">
      <table>
        <thead id="spread-head"></thead>
        <tbody id="spread-body">
          <tr><td class="empty">Loading spread data...</td></tr>
        </tbody>
      </table>
    </div>
  </main>
<script>
  const appRoot = "{{ app_root }}";
  const refreshBtn = document.getElementById('refresh-btn');
  const statusEl = document.getElementById('status');
  const lastRefreshEl = document.getElementById('last-refresh');
  const nextRefreshEl = document.getElementById('next-refresh');
  const messagesEl = document.getElementById('messages');
  const headEl = document.getElementById('spread-head');
  const bodyEl = document.getElementById('spread-body');
  let refreshTimer = null;
  let statusPollTimer = null;
  let nextRefreshAt = 0;
  let latestPayload = null;
  let sortState = { column: null, direction: 'asc' };

  function buildUrl(path) {
    if (!path.startsWith('/')) path = '/' + path;
    return appRoot ? appRoot + path : path;
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function fmtTime(value) {
    if (!value) return 'never';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function scalarMessage(value) {
    if (value == null || value === '') return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (typeof value === 'object') {
      for (const key of ['message', 'error', 'detail', 'reason', 'status']) {
        const nested = scalarMessage(value[key]);
        if (nested) return nested;
      }
      try { return JSON.stringify(value); } catch (_err) { return String(value); }
    }
    return String(value);
  }

  function messageArray(value) {
    if (!Array.isArray(value)) return [];
    return value.map(scalarMessage).filter(Boolean);
  }

  function payloadHasFailures(payload) {
    return messageArray(payload?.errors).length > 0 || messageArray(payload?.warnings).length > 0;
  }

  function isRefreshRunning(payload) {
    return payload?.refresh_state === 'running' || payload?.refresh?.state === 'running' || payload?.status === 'refresh_in_progress';
  }

  function renderMessages(payload) {
    const errors = messageArray(payload?.errors);
    const warnings = messageArray(payload?.warnings);
    messagesEl.innerHTML = '';
    for (const message of errors) {
      const div = document.createElement('div');
      div.className = 'message error';
      div.textContent = message;
      messagesEl.appendChild(div);
    }
    for (const message of warnings.slice(0, 12)) {
      const div = document.createElement('div');
      div.className = 'message';
      div.textContent = message;
      messagesEl.appendChild(div);
    }
  }

  function brokerData(cell, ...keys) {
    for (const key of keys) {
      const value = cell?.[key];
      if (value && typeof value === 'object') return value;
    }
    return {};
  }

  function spreadNumber(data) {
    const raw = data?.spread_pct;
    if (raw === null || raw === undefined || raw === '') return NaN;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : NaN;
  }

  function brokerLine(label, data) {
    const category = data?.category || 'unavailable';
    const spreadValue = spreadNumber(data);
    const error = scalarMessage(data?.error || data?.message || data?.reason);
    const sourceTime = scalarMessage(data?.updated_at);
    let value = scalarMessage(data?.display);
    const unavailable = category === 'unavailable' || !Number.isFinite(spreadValue);
    if (unavailable) value = '';
    if (!value && Number.isFinite(spreadValue)) value = `${spreadValue.toFixed(4)}%`;
    if (!value) value = error || unavailable ? 'Unavailable' : 'n/a';
    const titleText = error || (sourceTime ? `Source timestamp: ${sourceTime}` : '');
    const title = titleText ? ` title="${escapeHtml(titleText)}"` : '';
    const errorLine = error && unavailable
      ? `<span class="broker-error">${escapeHtml(error)}</span>`
      : '';
    return `<div class="broker-line spread-${escapeHtml(category)}"${title}>` +
      `<span class="broker-name">${escapeHtml(label)}:</span>` +
      `<span class="broker-value">${escapeHtml(value)}</span>` +
      errorLine +
      `</div>`;
  }

  function sortIndicator(column) {
    if (sortState.column !== column) return '';
    return sortState.direction === 'asc' ? 'asc' : 'desc';
  }

  function cellSortValue(row, timeframe) {
    const cell = row?.cells?.[timeframe] || {};
    const values = [
      brokerData(cell, 'oanda'),
      brokerData(cell, 'pepperstone_razor', 'pepperstone'),
    ].map((item) => spreadNumber(item)).filter(Number.isFinite);
    return values.length ? Math.max(...values) : null;
  }

  function sortedRows(payload) {
    const rows = Array.isArray(payload.rows) ? [...payload.rows] : [];
    if (!sortState.column) return rows;
    const direction = sortState.direction === 'desc' ? -1 : 1;
    rows.sort((a, b) => {
      if (sortState.column === 'symbol') {
        const left = scalarMessage(a.display_symbol || a.symbol).toLowerCase();
        const right = scalarMessage(b.display_symbol || b.symbol).toLowerCase();
        return left.localeCompare(right) * direction;
      }
      const left = cellSortValue(a, sortState.column);
      const right = cellSortValue(b, sortState.column);
      if (left == null && right == null) {
        return scalarMessage(a.symbol).localeCompare(scalarMessage(b.symbol));
      }
      if (left == null) return 1;
      if (right == null) return -1;
      return (left - right) * direction;
    });
    return rows;
  }

  function renderTable(payload) {
    const timeframes = Array.isArray(payload.timeframes) ? payload.timeframes : [];
    headEl.innerHTML = `<tr><th class="symbol-col sortable" data-sort-column="symbol" data-sort-indicator="${sortIndicator('symbol')}">Symbol</th>` +
      timeframes.map((tf) => `<th class="sortable" data-sort-column="${escapeHtml(tf)}" data-sort-indicator="${sortIndicator(tf)}">${escapeHtml(tf)}</th>`).join('') +
      '</tr>';
    const rows = sortedRows(payload);
    if (!rows.length) {
      bodyEl.innerHTML = `<tr><td class="empty" colspan="${Math.max(1, timeframes.length + 1)}">No spread rows are available yet.</td></tr>`;
      return;
    }
    bodyEl.innerHTML = rows.map((row) => {
      const cells = timeframes.map((tf) => {
        const cell = row.cells?.[tf] || {};
        return '<td>' +
          brokerLine('OANDA', brokerData(cell, 'oanda')) +
          brokerLine('Pepperstone Razor', brokerData(cell, 'pepperstone_razor', 'pepperstone')) +
          '</td>';
      }).join('');
      return `<tr><td class="symbol-col">${escapeHtml(row.display_symbol || row.symbol)}</td>${cells}</tr>`;
    }).join('');
  }

  function refreshIntervalSeconds(payload) {
    const seconds = Number(payload?.refresh_interval_seconds);
    return Number.isFinite(seconds) && seconds > 0 ? seconds : 300;
  }

  function scheduleNext(payload) {
    if (refreshTimer) clearTimeout(refreshTimer);
    const seconds = refreshIntervalSeconds(payload);
    nextRefreshAt = Date.now() + seconds * 1000;
    refreshTimer = setTimeout(() => refreshData(), seconds * 1000);
    updateCountdown();
  }

  function updateCountdown() {
    const remaining = Math.max(0, Math.round((nextRefreshAt - Date.now()) / 1000));
    if (nextRefreshEl) nextRefreshEl.textContent = `Auto-refresh: ${Math.ceil(remaining / 60)} min`;
  }

  function applyPayload(payload, source, options = {}) {
    latestPayload = payload || {};
    renderMessages(payload || {});
    renderTable(payload || {});
    const running = isRefreshRunning(payload);
    const successful = payload?.ok === true && !payloadHasFailures(payload) && !running;
    if (options.updateLastRefresh && successful) {
      lastRefreshEl.textContent = `Last refresh: ${fmtTime(payload?.last_refresh_finished_at || payload?.generated_at)}`;
    } else if (!options.updateLastRefresh) {
      lastRefreshEl.textContent = `Last refresh: ${fmtTime(payload?.last_refresh_finished_at || payload?.generated_at)}`;
    }
    if (running) {
      statusEl.textContent = 'Refresh running; showing cache.';
    } else if (payload?.ok === false || messageArray(payload?.errors).length) {
      statusEl.textContent = scalarMessage(payload?.error) || 'Spread refresh failed.';
    } else if (messageArray(payload?.warnings).length) {
      statusEl.textContent = 'Refresh completed with unavailable sources.';
    } else {
      statusEl.textContent = source;
    }
    refreshBtn.disabled = running;
    if (running) {
      if (refreshTimer) clearTimeout(refreshTimer);
      nextRefreshEl.textContent = 'Auto-refresh paused while refreshing';
    } else {
      scheduleNext(payload || {});
    }
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (_err) { payload = {}; }
    if (!response.ok) {
      const detail = scalarMessage(payload.detail || payload.error || payload.message) || text || response.statusText;
      throw new Error(detail);
    }
    return payload;
  }

  async function loadStatus() {
    try {
      const payload = await fetchJson(buildUrl('/api/spreads/status'));
      applyPayload(payload, 'Cached data loaded.');
      if (isRefreshRunning(payload)) queueStatusPoll();
    } catch (err) {
      statusEl.textContent = scalarMessage(err) || 'Failed to load spread status.';
      messagesEl.innerHTML = `<div class="message error">${escapeHtml(statusEl.textContent)}</div>`;
      scheduleNext({});
    }
  }

  async function refreshData() {
    refreshBtn.disabled = true;
    statusEl.textContent = 'Refreshing spreads...';
    let keepDisabled = false;
    try {
      const payload = await fetchJson(buildUrl('/api/spreads/refresh'), { method: 'POST' });
      applyPayload(payload, isRefreshRunning(payload) ? 'Refresh started.' : 'Refresh complete.', { updateLastRefresh: true });
      keepDisabled = isRefreshRunning(payload);
      if (keepDisabled) queueStatusPoll();
    } catch (err) {
      statusEl.textContent = scalarMessage(err) || 'Refresh failed.';
      messagesEl.innerHTML = `<div class="message error">${escapeHtml(statusEl.textContent)}</div>`;
      scheduleNext({});
    } finally {
      if (!keepDisabled) refreshBtn.disabled = false;
    }
  }

  function queueStatusPoll() {
    if (statusPollTimer) clearTimeout(statusPollTimer);
    statusPollTimer = setTimeout(pollRefreshStatus, 2000);
  }

  async function pollRefreshStatus() {
    try {
      const payload = await fetchJson(buildUrl('/api/spreads/status'));
      applyPayload(payload, isRefreshRunning(payload) ? 'Refresh running.' : 'Refresh complete.', { updateLastRefresh: true });
      if (isRefreshRunning(payload)) queueStatusPoll();
    } catch (err) {
      statusEl.textContent = scalarMessage(err) || 'Failed to poll spread status.';
      messagesEl.innerHTML = `<div class="message error">${escapeHtml(statusEl.textContent)}</div>`;
      refreshBtn.disabled = false;
      scheduleNext({});
    }
  }

  refreshBtn.addEventListener('click', refreshData);
  headEl.addEventListener('click', (event) => {
    const header = event.target.closest('th[data-sort-column]');
    if (!header) return;
    const column = header.dataset.sortColumn;
    if (sortState.column === column) {
      sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
    } else {
      sortState = { column, direction: 'asc' };
    }
    renderTable(latestPayload || {});
  });
  setInterval(updateCountdown, 1000);
  loadStatus();
</script>
</body>
</html>
"""


@app.get("/")
def index() -> str:
    return render_template_string(PAGE_TEMPLATE, app_root=APP_BASE_PATH)


@app.get("/api/spreads/status")
def spread_status():
    return jsonify(STATE.status())


@app.post("/api/spreads/refresh")
def spread_refresh():
    return jsonify(STATE.start_refresh())


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")
    print(f"Spread Monitor starting on {host}:{port}", flush=True)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
