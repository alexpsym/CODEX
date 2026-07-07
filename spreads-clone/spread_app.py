"""Local Spread Monitor web app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from flask import Flask, jsonify, render_template_string

from oanda_spreads import fetch_oanda_current_spreads, get_available_oanda_symbols
from spread_core import SpreadMonitorState, refresh_interval_from_env
from symbols import build_symbol_universe


ROOT_DIR = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT_DIR / "render" / "data" / "spread_monitor_cache.json"
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "").rstrip("/")

app = Flask(__name__)


def _symbol_provider() -> Iterable[str]:
    oanda_symbols = get_available_oanda_symbols()
    return build_symbol_universe(oanda_symbols=oanda_symbols)


OANDA_STATE = SpreadMonitorState(
    CACHE_PATH,
    symbol_provider=_symbol_provider,
    oanda_current_fetcher=fetch_oanda_current_spreads,
    refresh_interval_seconds=refresh_interval_from_env(),
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
    .header-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
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
    button.secondary {
      border-color: #475569;
      background: #1e293b;
      color: #dbeafe;
    }
    button:disabled { opacity: 0.6; cursor: wait; }
    main { padding: 18px 22px 26px; }
    .selector-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(220px, 1fr));
      gap: 14px;
      max-width: 720px;
      margin: 12vh auto 0;
    }
    .broker-card {
      min-height: 132px;
      border-color: #334155;
      background: #111827;
      color: #f8fafc;
      font-size: 24px;
      border-radius: 8px;
      box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.12);
    }
    .broker-card:focus,
    .broker-card:hover {
      border-color: #60a5fa;
      background: #172033;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
      color: #cbd5e1;
      font-size: 13px;
    }
    .mode-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
      color: #cbd5e1;
      font-size: 13px;
    }
    .import-meta { color: #94a3b8; }
    .table-wrap {
      width: 100%;
      overflow: auto;
      max-height: calc(100vh - 270px);
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
    table.current-only {
      width: 100%;
      min-width: 420px;
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
    table.current-only th:not(.symbol-col),
    table.current-only td:not(.symbol-col) {
      width: auto;
      min-width: 180px;
      max-width: none;
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
    table.current-only .broker-value {
      max-width: none;
    }
    .broker-error {
      flex: 0 0 100%;
      color: #94a3b8;
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .spread-neutral { color: #e2e8f0; }
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
    [hidden] { display: none !important; }
    @media (max-width: 720px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 14px; }
      .selector-grid { grid-template-columns: 1fr; margin-top: 28px; }
      table { min-width: 1500px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1 id="page-title">Spread Monitor</h1>
      <div class="meta">
        <span id="last-refresh">Last refresh: waiting</span>
        <span id="next-refresh">Auto-refresh: waiting</span>
        <span id="status">Loading OANDA spreads.</span>
      </div>
    </div>
    <div class="header-actions">
      <button id="back-btn" class="secondary" type="button" hidden>Back</button>
      <button id="refresh-btn" type="button" hidden>Refresh</button>
    </div>
  </header>
  <main>
    <div id="selector-view" class="selector-grid" aria-label="Spread source selector" hidden>
      <button class="broker-card" type="button" data-broker="oanda">Oanda</button>
    </div>

    <section id="monitor-view">
      <div class="legend" aria-label="Spread display note">
        <span>Spread values are shown as percentage of bid/ask midpoint.</span>
      </div>
      <div class="messages" id="messages"></div>
      <div class="table-wrap">
        <table id="spread-table">
          <thead id="spread-head"></thead>
          <tbody id="spread-body">
            <tr><td class="empty">Loading OANDA spread data.</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
<script>
  const appRoot = "{{ app_root }}";
  const selectorView = document.getElementById('selector-view');
  const monitorView = document.getElementById('monitor-view');
  const pageTitle = document.getElementById('page-title');
  const backBtn = document.getElementById('back-btn');
  const refreshBtn = document.getElementById('refresh-btn');
  const statusEl = document.getElementById('status');
  const lastRefreshEl = document.getElementById('last-refresh');
  const nextRefreshEl = document.getElementById('next-refresh');
  const messagesEl = document.getElementById('messages');
  const tableEl = document.getElementById('spread-table');
  const headEl = document.getElementById('spread-head');
  const bodyEl = document.getElementById('spread-body');
  let refreshTimer = null;
  let statusPollTimer = null;
  let nextRefreshAt = 0;
  let latestPayload = null;
  let currentBroker = null;
  let hideOandaCacheUntilFresh = false;
  let sortState = { column: null, direction: 'asc' };

  const brokerLabels = {
    oanda: 'OANDA',
  };

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

  function currentBrokerKeys() {
    return ['oanda'];
  }

  function brokerData(cell, ...keys) {
    const lookupKeys = keys.length ? keys : currentBrokerKeys();
    for (const key of lookupKeys) {
      const value = cell?.[key];
      if (value && typeof value === 'object') return value;
    }
    return {};
  }

  function spreadNumber(data) {
    const raw = data?.spread_pct;
    if (raw === null || raw === undefined || raw === '') return NaN;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? value : NaN;
  }

  function brokerLine(label, data) {
    const category = data?.category || 'unavailable';
    const spreadValue = spreadNumber(data);
    const error = scalarMessage(data?.error || data?.message || data?.reason);
    const sourceTime = scalarMessage(data?.updated_at);
    let value = scalarMessage(data?.display);
    const unavailable = !Number.isFinite(spreadValue);
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
    if (timeframe === 'current_spread') {
      const value = spreadNumber(currentSpreadData(row));
      return Number.isFinite(value) ? value : null;
    }
    const cell = row?.cells?.[timeframe] || {};
    const value = spreadNumber(brokerData(cell));
    return Number.isFinite(value) ? value : null;
  }

  function currentSpreadData(row) {
    const direct = row?.current_spread;
    if (direct && typeof direct === 'object') return direct;
    return brokerData(row?.cells?.CURRENT || row?.cells?.current || {});
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
    if (payload?.current_only) {
      renderCurrentSpreadTable(payload);
      return;
    }
    if (tableEl) tableEl.classList.remove('current-only');
    const timeframes = Array.isArray(payload.timeframes) ? payload.timeframes : [];
    headEl.innerHTML = `<tr><th class="symbol-col sortable" data-sort-column="symbol" data-sort-indicator="${sortIndicator('symbol')}">Symbol</th>` +
      timeframes.map((tf) => `<th class="sortable" data-sort-column="${escapeHtml(tf)}" data-sort-indicator="${sortIndicator(tf)}">${escapeHtml(tf)}</th>`).join('') +
      '</tr>';
    const rows = sortedRows(payload);
    if (!rows.length) {
      const emptyText = 'No OANDA spread rows are available yet.';
      bodyEl.innerHTML = `<tr><td class="empty" colspan="${Math.max(1, timeframes.length + 1)}">${escapeHtml(emptyText)}</td></tr>`;
      return;
    }
    const label = brokerLabels[currentBroker] || 'Spread';
    bodyEl.innerHTML = rows.map((row) => {
      const cells = timeframes.map((tf) => {
        const cell = row.cells?.[tf] || {};
        return '<td>' + brokerLine(label, brokerData(cell)) + '</td>';
      }).join('');
      return `<tr><td class="symbol-col">${escapeHtml(row.display_symbol || row.symbol)}</td>${cells}</tr>`;
    }).join('');
  }

  function currentSpreadCell(data) {
    const category = data?.category || 'unavailable';
    const spreadValue = spreadNumber(data);
    const error = scalarMessage(data?.error || data?.message || data?.reason);
    const sourceTime = scalarMessage(data?.updated_at);
    let value = scalarMessage(data?.display);
    if (!value && Number.isFinite(spreadValue)) value = `${spreadValue.toFixed(4)}%`;
    if (!value) value = error || 'Unavailable';
    const titleText = error || (sourceTime ? `Source timestamp: ${sourceTime}` : '');
    const title = titleText ? ` title="${escapeHtml(titleText)}"` : '';
    const errorLine = error && !Number.isFinite(spreadValue)
      ? `<span class="broker-error">${escapeHtml(error)}</span>`
      : '';
    return `<div class="broker-line spread-${escapeHtml(category)}"${title}>` +
      `<span class="broker-value">${escapeHtml(value)}</span>` +
      errorLine +
      `</div>`;
  }

  function renderCurrentSpreadTable(payload) {
    if (tableEl) tableEl.classList.add('current-only');
    headEl.innerHTML = `<tr><th class="symbol-col sortable" data-sort-column="symbol" data-sort-indicator="${sortIndicator('symbol')}">Instrument</th>` +
      `<th class="sortable" data-sort-column="current_spread" data-sort-indicator="${sortIndicator('current_spread')}">Current Spread</th></tr>`;
    const rows = sortedRows(payload || {});
    if (!rows.length) {
      const label = brokerLabels[currentBroker] || 'Spread';
      bodyEl.innerHTML = `<tr><td class="empty" colspan="2">No ${escapeHtml(label)} current spread rows are available yet.</td></tr>`;
      return;
    }
    bodyEl.innerHTML = rows.map((row) => {
      return `<tr><td class="symbol-col">${escapeHtml(row.display_symbol || row.symbol)}</td><td>${currentSpreadCell(currentSpreadData(row))}</td></tr>`;
    }).join('');
  }

  function refreshIntervalSeconds(payload) {
    const seconds = Number(payload?.refresh_interval_seconds);
    return Number.isFinite(seconds) && seconds > 0 ? seconds : 300;
  }

  function refreshTimeoutSeconds(payload) {
    const seconds = Number(payload?.refresh?.global_timeout_seconds || payload?.refresh?.diagnostics?.global_timeout_seconds);
    return Number.isFinite(seconds) && seconds > 0 ? seconds : 120;
  }

  function renderLoadingTable(text) {
    const currentOnly = currentBroker === 'oanda' || latestPayload?.current_only;
    if (tableEl) tableEl.classList.toggle('current-only', currentOnly);
    const colSpan = currentOnly ? 2 : (latestPayload?.timeframes?.length ? latestPayload.timeframes.length + 1 : 10);
    bodyEl.innerHTML = `<tr><td class="empty" colspan="${colSpan}">${escapeHtml(text)}</td></tr>`;
  }

  function refreshProgressText(payload) {
    const diag = payload?.refresh?.diagnostics || {};
    const completed = Number(diag.completed_request_count || 0);
    const failed = Number(diag.failed_request_count || 0);
    const skipped = Number(diag.skipped_request_count || 0);
    const total = Number(diag.total_requests_planned || 0);
    const elapsed = Number(diag.elapsed_seconds || payload?.refresh?.elapsed_seconds || 0);
    const current = [diag.current_symbol, diag.current_timeframe].map(scalarMessage).filter(Boolean).join(' ');
    const base = total ? `${completed}/${total} requests complete, ${failed} failed, ${skipped} skipped` : 'Preparing requests';
    return `${base}; elapsed ${Math.round(elapsed)}s${current ? `; fetching ${current}` : ''}`;
  }

  function timedOutMessage(payload) {
    const timeout = refreshTimeoutSeconds(payload);
    const progress = refreshProgressText(payload);
    return `OANDA refresh timed out after ${timeout} seconds. ${progress}`;
  }

  function clearAutoRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = null;
    nextRefreshAt = 0;
  }

  function scheduleNext(payload) {
    clearAutoRefresh();
    if (currentBroker !== 'oanda') {
      nextRefreshEl.textContent = 'Auto-refresh: 5 min';
      return;
    }
    const seconds = refreshIntervalSeconds(payload);
    nextRefreshAt = Date.now() + seconds * 1000;
    refreshTimer = setTimeout(() => refreshData(), seconds * 1000);
    updateCountdown();
  }

  function updateCountdown() {
    if (currentBroker !== 'oanda' || !nextRefreshAt) return;
    const remaining = Math.max(0, Math.round((nextRefreshAt - Date.now()) / 1000));
    if (nextRefreshEl) nextRefreshEl.textContent = `Auto-refresh: ${Math.ceil(remaining / 60)} min`;
  }

  function applyPayload(payload, source, options = {}) {
    latestPayload = payload || {};
    const running = isRefreshRunning(payload);
    const timedOut = payload?.refresh_state === 'timed_out' || payload?.refresh?.state === 'timed_out';
    const suppressOandaRows = currentBroker === 'oanda' && hideOandaCacheUntilFresh && running;
    renderMessages(payload || {});
    if (suppressOandaRows) {
      renderLoadingTable(`Refreshing OANDA spreads... ${refreshProgressText(payload || {})}`);
    } else {
      renderTable(payload || {});
    }
    const successful = payload?.ok === true && !payloadHasFailures(payload) && !running;
    if (currentBroker === 'oanda') {
      if (!running) hideOandaCacheUntilFresh = false;
      if (options.updateLastRefresh && successful) {
        lastRefreshEl.textContent = `Last refresh: ${fmtTime(payload?.last_refresh_finished_at || payload?.generated_at)}`;
      } else if (!options.updateLastRefresh) {
        lastRefreshEl.textContent = `Last refresh: ${fmtTime(payload?.last_refresh_finished_at || payload?.generated_at)}`;
      }
      if (timedOut) {
        statusEl.textContent = timedOutMessage(payload);
      } else if (running) {
        statusEl.textContent = `Refreshing OANDA spreads... ${refreshProgressText(payload)}`;
      } else if (payload?.ok === false || messageArray(payload?.errors).length) {
        statusEl.textContent = scalarMessage(payload?.error) || 'OANDA spread refresh needs data.';
      } else if (messageArray(payload?.warnings).length) {
        statusEl.textContent = 'OANDA refresh completed with unavailable rows.';
      } else {
        statusEl.textContent = source;
      }
      refreshBtn.disabled = running;
      if (running) {
        clearAutoRefresh();
        nextRefreshEl.textContent = 'Auto-refresh paused while refreshing';
      } else {
        scheduleNext(payload || {});
      }
      return;
    }

    statusEl.textContent = source || 'OANDA spread data loaded.';
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (_err) { payload = {}; }
    if (!response.ok) {
      const detail = scalarMessage(payload.detail || payload.error || payload.message || payload.errors?.[0]) || text || response.statusText;
      const error = new Error(detail);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function endpoint(action) {
    return buildUrl(`/api/spreads/${currentBroker}/${action}`);
  }

  async function loadStatus() {
    try {
      const payload = await fetchJson(endpoint('status'));
      applyPayload(payload, 'Cached OANDA data loaded.');
      if (currentBroker === 'oanda' && isRefreshRunning(payload)) queueStatusPoll();
    } catch (err) {
      statusEl.textContent = scalarMessage(err) || 'Failed to load spread status.';
      messagesEl.innerHTML = `<div class="message error">${escapeHtml(statusEl.textContent)}</div>`;
      if (currentBroker === 'oanda') scheduleNext({});
    }
  }

  async function refreshData(options = {}) {
    if (currentBroker !== 'oanda') return;
    refreshBtn.disabled = true;
    if (options.initial) hideOandaCacheUntilFresh = true;
    statusEl.textContent = 'Refreshing OANDA spreads...';
    messagesEl.innerHTML = '';
    renderLoadingTable('Refreshing OANDA spreads...');
    clearAutoRefresh();
    let keepDisabled = false;
    try {
      const payload = await fetchJson(endpoint('refresh'), { method: 'POST' });
      applyPayload(payload, isRefreshRunning(payload) ? 'OANDA refresh started.' : 'OANDA refresh complete.', { updateLastRefresh: true });
      keepDisabled = isRefreshRunning(payload);
      if (keepDisabled) queueStatusPoll();
    } catch (err) {
      hideOandaCacheUntilFresh = false;
      statusEl.textContent = scalarMessage(err) || 'OANDA refresh failed.';
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
    if (currentBroker !== 'oanda') return;
    try {
      const payload = await fetchJson(endpoint('status'));
      applyPayload(payload, isRefreshRunning(payload) ? 'OANDA refresh running.' : 'OANDA refresh complete.', { updateLastRefresh: true });
      if (isRefreshRunning(payload)) queueStatusPoll();
    } catch (err) {
      statusEl.textContent = scalarMessage(err) || 'Failed to poll OANDA refresh status.';
      messagesEl.innerHTML = `<div class="message error">${escapeHtml(statusEl.textContent)}</div>`;
      refreshBtn.disabled = false;
      scheduleNext({});
    }
  }

  function selectBroker(broker) {
    currentBroker = 'oanda';
    latestPayload = null;
    hideOandaCacheUntilFresh = broker === 'oanda';
    sortState = { column: null, direction: 'asc' };
    if (statusPollTimer) clearTimeout(statusPollTimer);
    clearAutoRefresh();
    selectorView.hidden = true;
    monitorView.hidden = false;
    backBtn.hidden = true;
    refreshBtn.hidden = false;
    pageTitle.textContent = 'OANDA Spread Monitor';
    lastRefreshEl.textContent = 'Last refresh: waiting';
    nextRefreshEl.textContent = 'Auto-refresh: 5 min';
    statusEl.textContent = 'Refreshing OANDA spreads...';
    messagesEl.innerHTML = '';
    headEl.innerHTML = '';
    bodyEl.innerHTML = '<tr><td class="empty">Refreshing OANDA spreads...</td></tr>';
    refreshData({ initial: true });
  }

  function showSelector() {
    currentBroker = null;
    latestPayload = null;
    hideOandaCacheUntilFresh = false;
    if (statusPollTimer) clearTimeout(statusPollTimer);
    clearAutoRefresh();
    selectorView.hidden = false;
    monitorView.hidden = true;
    backBtn.hidden = true;
    refreshBtn.hidden = true;
    pageTitle.textContent = 'Spread Monitor';
    lastRefreshEl.textContent = 'Last refresh: waiting';
    nextRefreshEl.textContent = 'Auto-refresh: select a broker';
    statusEl.textContent = 'Choose a spread source.';
  }

  selectorView.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-broker]');
    if (!button) return;
    selectBroker(button.dataset.broker);
  });
  backBtn.addEventListener('click', showSelector);
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
  selectBroker('oanda');
</script>
</body>
</html>
"""


@app.get("/")
def index() -> str:
    return render_template_string(PAGE_TEMPLATE, app_root=APP_BASE_PATH)


@app.get("/api/spreads/oanda/status")
def oanda_spread_status():
    return jsonify(OANDA_STATE.status())


@app.post("/api/spreads/oanda/refresh")
def oanda_spread_refresh():
    return jsonify(OANDA_STATE.start_refresh())


@app.get("/api/spreads/status")
def spread_status():
    return oanda_spread_status()


@app.post("/api/spreads/refresh")
def spread_refresh():
    return oanda_spread_refresh()


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")
    print(f"Spread Monitor starting on {host}:{port}", flush=True)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
