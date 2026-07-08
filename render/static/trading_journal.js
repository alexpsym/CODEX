(() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const tbody = q('#tj-table tbody');
  const empty = q('#tj-empty');
  const status = q('#tj-status');
  const filterInput = q('#tj-filter');
  const loading = q('#tj-loading');
  const loadingText = q('#tj-loading-text');
  const loadingBar = q('#tj-loading-bar');
  const loadingPct = q('#tj-loading-pct');
  const addBtn = q('#tj-add-btn');
  const editorModal = q('#tj-editor-modal');
  const editorForm = q('#tj-editor-form');
  const editorTitle = q('#tj-editor-title');
  const editorErr = q('#tj-editor-error');
  const editorCancelBtn = q('#tj-editor-cancel');
  const editorSaveBtn = q('#tj-editor-save');
  const statTradeFilterBtn = q('#tj-stat-trade-filter-btn');

  const setLoading = (pct, msg) => {
    if (loadingText) loadingText.textContent = msg || '';
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    if (loadingBar) loadingBar.style.width = `${p}%`;
    if (loadingPct) loadingPct.textContent = `${Math.round(p)}%`;
    if (loading) loading.style.display = 'flex';
  };
  const hideLoading = () => { if (loading) loading.style.display = 'none'; };

  let activeFlags = new Set();
  let autoRefreshTimer = null;
  const AUTO_REFRESH_MS = 60 * 60 * 1000;
  let loadInFlight = false;
  let activeAbort = null;
  const TJ_CACHE_DB = 'trading_journal_cache_v1';
  const TJ_CACHE_SCHEMA_VERSION = 2;
  const TJ_JS_VERSION = String(window.TRADING_JOURNAL_JS_VERSION || '');
  const TJ_CACHE_KEY = 'combined_payload';

  function normYes(v) {
    return ['yes', 'y', 'true', '1'].includes(String(v ?? '').trim().toLowerCase());
  }
  let state = {
    rows: [],
    sortKey: 'close_time',
    sortDir: localStorage.getItem('tj.sortDir') || 'desc',
    instSortKey: localStorage.getItem('tj.instSortKey') || 'total_trades',
    instSortDir: localStorage.getItem('tj.instSortDir') || 'desc',
    stats: null,
    view: localStorage.getItem('tj.view') || 'trades',
    calMonth: localStorage.getItem('tj.calMonth') || new Date().toISOString().slice(0, 7),
    editorOpen: false,
    editorDirty: false,
    saveInFlight: false,
    editingRowId: null,
    editingIsCreate: false,
    diagnostics: null,
    renderedRows: [],
    statTradeFilter: null,
    oandaRepairAttempted: false,
    oandaRepairRefreshPending: false,
    oandaRepairLastError: '',
  };
  try { filterInput.value = localStorage.getItem('tj.filter') || ''; } catch {}

  const asNum = (v) => {
    if (v === null || v === undefined || v === '') return NaN;
    const n = Number(v);
    return Number.isFinite(n) ? n : NaN;
  };

  const fmtNum = (v, d = 2) => {
    const n = asNum(v);
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) > 0 && Math.abs(n) < Math.pow(10, -Math.max(2, d)) && Math.abs(n) < 1e-4) {
      return n.toPrecision(8);
    }
    return n.toLocaleString(undefined, { maximumFractionDigits: d });
  };
  const fmtQty = (v, row) => {
    const n = asNum(v);
    if (!Number.isFinite(n)) return '—';
    const d = qtyPrecision(row);
    let out = n.toLocaleString(undefined, { maximumFractionDigits: d });
    if (Number(out.replace(/,/g, '')) === 0 && n !== 0) out = n.toPrecision(Math.min(12, Math.max(4, d)));
    return out;
  };

  const fmtTime = (v) => {
    if (!v) return '—';
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('en-AU', { timeZone: 'Australia/Brisbane' });
  };

  const fmtDuration = (seconds) => {
    const n = asNum(seconds);
    if (!Number.isFinite(n) || n < 0) return '—';
    const total = Math.round(n);
    const d = Math.floor(total / 86400);
    const h = Math.floor((total % 86400) / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const parts = [];
    if (d) parts.push(`${d}d`);
    if (h || d) parts.push(`${h}h`);
    if (m || h || d) parts.push(`${m}m`);
    parts.push(`${s}s`);
    return parts.join(' ');
  };
  const escapeCsvCell = (value) => {
    const text = String(value ?? '');
    if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
    return text;
  };

  // Browser trade columns are intentionally grouped for editing/readability;
  // workbook export/sync order is defined separately by TRADE_LOG_HEADERS in Python.
  const TRADE_COLUMNS = [
    { key: 'open_time', header: 'Open Time', value: (r) => fmtTime(r.open_time) },
    { key: 'close_time', header: 'Close Time', value: (r) => fmtTime(r.close_time || r.open_time) },
    { key: 'account_label', header: 'Account', value: (r) => r.account_label || r.account || '—' },
    { key: 'symbol', header: 'Symbol', value: (r) => r.symbol || '—' },
    { key: 'side', header: 'Side', value: (r) => isMonthlyAudRevalRow(r) ? 'NOTE' : (r.side || '—') },
    { key: 'timeframe', header: 'Timeframe', value: (r) => r.timeframe || r.metrics?.timeframe || '—' },
    { key: 'is_test_trade', header: 'Test', value: (r) => String(r.is_test_trade) === 'true' ? 'Yes' : (String(r.is_test_trade) === 'false' ? 'No' : '—') },
    { key: 'setup', header: 'Setup', value: (r) => r.setup || '—' },
    { key: 'qty', header: 'Qty', value: (r) => `${fmtQty(r.qty, r)}${r.qty_unit === 'lots' ? ' lot' : ''}` },
    { key: 'entry_price', header: 'Entry', value: (r) => fmtNum(r.entry_price, 6) },
    { key: 'exit_price', header: 'Exit', value: (r) => fmtNum(r.exit_price, 6) },
    { key: 'stop_loss', header: 'Stop Loss', value: (r) => fmtNum(r.stop_loss, 6) },
    { key: 'stop_loss_distance_pct', header: 'Stop Loss Distance', value: (r) => fmtPctSmall(priceDistancePct(r.entry_price, r.stop_loss, r.stop_loss_distance_pct), 2) },
    { key: 'take_profit', header: 'Target', value: (r) => fmtNum(r.take_profit, 6) },
    { key: 'target_distance_pct', header: 'Target Distance', value: (r) => fmtPctSmall(priceDistancePct(r.entry_price, r.take_profit, r.target_distance_pct), 2) },
    { key: 'commission', header: 'Commission', value: (r) => `${fmtNum(r.commission ?? r.fees, 4)} ${r.commission_currency || r.fee_currency || ''}`.trim() || '—' },
    { key: 'net_profit', header: 'Net Profit', value: (r) => `${fmtNum(rowPnlValue(r), 4)} ${rowPnlCurrency(r)}`.trim() || '—' },
    { key: 'profit_pct', header: 'Profit %', value: (r) => fmtProfitPct(r.result_pct ?? r.profit_pct) },
    { key: 'r_multiple', header: 'R-Multiple', value: (r) => fmtR(r.r_multiple) },
    { key: 'balance_after_trade', header: 'Balance After', value: (r) => { const bal = asNum(r.analysis_balance_after_trade ?? r.balance_after_trade ?? r.cashflow_new_balance); const ccy = r.balance_after_trade_currency || r.currency || ''; return Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '—'; } },
    { key: 'trade_duration_seconds', header: 'Trade Duration', value: (r) => fmtDuration(r.trade_duration_seconds) },
    { key: 'breakeven', header: 'Breakeven', value: (r) => r.breakeven || '—' },
    { key: 'chart', header: 'Chart', value: (r) => { if (isMonthlyAudRevalRow(r)) return ''; return (r.id && String(r.source || '').toLowerCase() !== 'manual') ? 'Chart' : ''; } },
    { key: 'actions', header: 'Actions', value: (r) => { if (isMonthlyAudRevalRow(r)) return ''; return (r.is_manual || String(r.source || '').toLowerCase() === 'manual') ? 'Edit / Delete' : 'Edit'; } },
  ];

  const setStatus = (msg) => { status.textContent = msg || ''; };
  const MISSING_XLRD_STATUS = 'Local .xls journal workbooks require xlrd. Restart the journal launcher so dependencies can be installed automatically.';
  const isMissingXlrdError = (value) => String(value ?? '').toUpperCase().includes('MISSING_XLRD_FOR_XLS');
  const diagnosticsErrorText = (value) => String(value?.message ?? value ?? '').trim();
  const isBalanceAnchorWarning = (value) => diagnosticsErrorText(value).toLowerCase().includes('missing balance anchor');
  const isParseSyncError = (value) => diagnosticsErrorText(value).length > 0 && !isBalanceAnchorWarning(value);
  const BYBIT_DEMO_PURGE_NOTE = 'Bybit Demo workbook is blank; old Bybit Demo rows purged';
  const formatSyncFailureStatus = (err) => {
    const raw = String(err?.message || err || '');
    if (isMissingXlrdError(raw) || raw.toLowerCase().includes('local .xls journal workbooks require xlrd')) {
      return MISSING_XLRD_STATUS;
    }
    return compactErrorMessage(raw, 'Request failed');
  };
  const compactErrorMessage = (detail, fallback = 'Request failed') => {
    const pick = (obj, keys) => {
      for (const key of keys) {
        const value = obj?.[key];
        if (typeof value === 'string' && value.trim()) return value.trim();
      }
      return '';
    };
    let msg = '';
    if (typeof detail === 'string') msg = detail.trim();
    else if (detail && typeof detail === 'object') {
      msg = pick(detail, ['message', 'error', 'warning']) || pick(detail?.detail || {}, ['message', 'error', 'warning']);
      if (!msg && typeof detail?.detail === 'string') msg = detail.detail.trim();
    }
    if (!msg) msg = fallback;
    return String(msg).replace(/\s+/g, ' ').trim().slice(0, 300);
  };
  const isoToInput = (v) => {
    if (!v) return '';
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const inputToIso = (v) => {
    const text = String(v || '').trim();
    if (!text) return '';
    const d = new Date(text);
    return Number.isNaN(d.getTime()) ? text : d.toISOString();
  };

  function syncActionButtons() {
    if (addBtn) addBtn.disabled = state.saveInFlight;
    if (editorSaveBtn) editorSaveBtn.disabled = state.saveInFlight;
  }

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, { cache: 'no-store', ...options });
    const text = await res.text();
    let payload = {};
    let parsed = true;
    try { payload = text ? JSON.parse(text) : {}; } catch { parsed = false; }
    if (!res.ok) {
      if (!parsed && text) {
        throw new Error(`${res.status} Sync failed: server returned non-JSON error`);
      }
      const detail = payload?.detail ?? payload;
      throw new Error(`${res.status} ${compactErrorMessage(detail, text || res.statusText || 'Request failed')}`);
    }
    return payload;
  }

  function isAbortError(err, signal) {
    const msg = String(err?.message || '').toLowerCase();
    return err?.name === 'AbortError'
      || err?.cause?.name === 'AbortError'
      || !!signal?.aborted
      || msg.includes('aborted')
      || msg.includes('signal is aborted');
  }

  async function fetchNamedJson(label, url, options = {}) {
    try {
      return await fetchJson(url, options);
    } catch (err) {
      if (isAbortError(err, options?.signal)) throw err;
      throw new Error(`${label}: ${err?.message || err}`);
    }
  }

  function openCacheDb() {
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) return reject(new Error('IndexedDB unavailable'));
      const req = indexedDB.open(TJ_CACHE_DB, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('payloads')) db.createObjectStore('payloads');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    });
  }

  async function readCachedPayload() {
    try {
      const db = await openCacheDb();
      const payload = await new Promise((resolve) => {
        const tx = db.transaction('payloads', 'readonly');
        const req = tx.objectStore('payloads').get(TJ_CACHE_KEY);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      });
      if (!payload || typeof payload !== 'object') return null;
      if ((payload.cache_schema_version || 0) !== TJ_CACHE_SCHEMA_VERSION) return null;
      if (String(payload.js_version || '') !== TJ_JS_VERSION) return null;
      const balances = Array.isArray(payload?.balances?.items) ? payload.balances.items : [];
      const demo = balances.find((b) => String(b?.label || b?.account || '').toUpperCase() === 'OANDA DEMO');
      if (demo && String(demo.balance_source || '') === 'cashflow_anchor_plus_trades' && !demo.stale_balance_warning) return null;
      return payload;
    } catch {
      return null;
    }
  }

  async function writeCachedPayload(payload) {
    if (!payload || !Array.isArray(payload?.journal?.items)) return;
    try {
      const db = await openCacheDb();
      await new Promise((resolve, reject) => {
        const tx = db.transaction('payloads', 'readwrite');
        tx.objectStore('payloads').put({
          ...payload,
          cache_schema_version: TJ_CACHE_SCHEMA_VERSION,
          js_version: TJ_JS_VERSION,
          fetched_at: new Date().toISOString(),
        }, TJ_CACHE_KEY);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => reject(tx.error || new Error('IndexedDB write failed'));
      });
    } catch {}
  }

  function applyTextFilter(rows) {
    const raw = (filterInput?.value || '').trim();
    if (!raw) return [...rows];
    const norm = (str) => String(str ?? '')
      .toLowerCase()
      .replace(/[_-]+/g, ' ')
      .replace(/[^a-z0-9 ]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const tokens = norm(raw).split(' ').filter(Boolean);
    if (!tokens.length) return [...rows];
    return (rows || []).filter((r) => {
      const searchable = [
        r?.symbol,
        r?.symbol_raw,
        r?.account_label,
        r?.account,
        r?.source,
        r?.id,
        r?.row_type,
        r?.result_cash,
        r?.result_currency,
        r?.open_time,
        r?.close_time,
        r?.raw_refs?.period_month,
        r?.sheet,
        r?.side,
        r?.status,
        r?.setup,
        r?.timeframe,
        r?.is_test_trade,
        r?.notes,
        r?.pre_trade_comments,
        r?.entry_comments,
        r?.trade_management,
        r?.exit_comments,
      ];
      const metrics = r?.metrics;
      if (metrics && typeof metrics === 'object') {
        try { searchable.push(...Object.values(metrics)); } catch {}
      }
      const hay = norm(searchable.map((x) => String(x ?? '')).join(' '));
      return tokens.every((t) => hay.includes(t));
    });
  }

  function applyFlagFilters(rows) {
    const hasAnyKey = (record, keys) => {
      const metrics = record?.metrics || {};
      const pools = [record || {}, metrics];
      return pools.some((pool) => keys.some((key) => {
        const v = pool[key] ?? pool[key.toUpperCase()] ?? pool[key.toLowerCase()];
        return normYes(v) || (!!v && !['false', '0', 'no', 'n'].includes(String(v).trim().toLowerCase()));
      }));
    };

    let out = [...rows];
    const tradeFlagActive = ['breakeven','held_news','spiked_out','early_close'].some((f) => activeFlags.has(f));
    if (tradeFlagActive) out = out.filter((r) => isTradeRow(r));
    if (activeFlags.has('errors')) {
      out = out.filter((r) => hasAnyKey(r, ['errors', 'error']));
    }
    if (activeFlags.has('breakeven')) {
      out = out.filter((r) => normYes(r.breakeven));
    }
    if (activeFlags.has('held_news')) {
      out = out.filter((r) => hasAnyKey(r, ['held_through_news', 'held_news']));
    }
    if (activeFlags.has('spiked_out')) {
      out = out.filter((r) => hasAnyKey(r, ['spiked_out', 'spike_out']));
    }
    if (activeFlags.has('early_close')) {
      out = out.filter((r) => hasAnyKey(r, ['early_close', 'closed_early']));
    }
    return out;
  }


  function syncFlagButtons() {
    qa('.tj-chip[data-flag]').forEach((btn) => {
      const on = activeFlags.has(btn.dataset.flag || '');
      btn.classList.toggle('active', on);
      btn.style.opacity = on ? '1' : '0.7';
      btn.style.outline = on ? '1px solid #60a5fa' : 'none';
    });
  }

  function clearStatTradeFilter({ clearAllFilters = false, render = true, status: statusMessage = true } = {}) {
    state.statTradeFilter = null;
    if (clearAllFilters) {
      if (filterInput) filterInput.value = '';
      activeFlags.clear();
      syncFlagButtons();
      state.view = 'trades';
    }
    if (render) renderAll();
    if (statusMessage) setStatus('Trade filter cleared; showing all trades.');
  }

  function getFilteredRows() {
    let filtered = applyFlagFilters(applyTextFilter(state.rows));
    const rowId = state.statTradeFilter?.rowId;
    if (rowId) {
      const exists = (state.rows || []).some((row) => String(row?.id || '') === String(rowId));
      if (!exists) {
        state.statTradeFilter = null;
      } else {
        filtered = filtered.filter((row) => String(row?.id || '') === String(rowId));
      }
    }
    return filtered;
  }

  function renderStatTradeFilterButton() {
    if (!statTradeFilterBtn) return;
    const active = state.statTradeFilter?.rowId && state.statTradeFilter?.label;
    if (!active) {
      statTradeFilterBtn.classList.add('hidden');
      statTradeFilterBtn.textContent = '';
      statTradeFilterBtn.title = '';
      statTradeFilterBtn.setAttribute('aria-label', '');
      return;
    }
    statTradeFilterBtn.textContent = state.statTradeFilter.label;
    statTradeFilterBtn.classList.remove('hidden');
    const hint = 'Clear linked trade filter and show all trades';
    statTradeFilterBtn.title = hint;
    statTradeFilterBtn.setAttribute('aria-label', hint);
  }

  function qtyPrecision(row) {
    if (row?.qty_unit === 'lots') {
      const qty = Math.abs(Number(row?.qty));
      if (!Number.isFinite(qty)) return 4;
      if (qty >= 1) return 2;
      if (qty >= 0.1) return 3;
      return 6;
    }
    const qty = Number(row?.qty);
    if (!Number.isFinite(qty)) return 6;
    if (Math.abs(qty) > 0 && Math.abs(qty) < 1e-8) return 18;
    if (Math.abs(qty) < 0.01) return 12;
    return 8;
  }


  function persistUiState() {
    try {
      localStorage.setItem('tj.filter', filterInput.value || '');
      localStorage.setItem('tj.sortKey', state.sortKey || '');
      localStorage.setItem('tj.sortDir', state.sortDir || 'desc');
      localStorage.setItem('tj.instSortKey', state.instSortKey || '');
      localStorage.setItem('tj.instSortDir', state.instSortDir || 'desc');
      localStorage.setItem('tj.flags', JSON.stringify(Array.from(activeFlags)));
      localStorage.setItem('tj.view', state.view || 'trades');
      localStorage.setItem('tj.calMonth', state.calMonth || new Date().toISOString().slice(0, 7));
    } catch {}
  }

  try {
    state.sortKey = localStorage.getItem('tj.sortKey') || state.sortKey;
    state.sortDir = localStorage.getItem('tj.sortDir') || state.sortDir;
    state.instSortKey = localStorage.getItem('tj.instSortKey') || state.instSortKey;
    state.instSortDir = localStorage.getItem('tj.instSortDir') || state.instSortDir;
    state.calMonth = localStorage.getItem('tj.calMonth') || state.calMonth;
    const savedFlags = JSON.parse(localStorage.getItem('tj.flags') || '[]');
    if (Array.isArray(savedFlags)) savedFlags.forEach((f) => activeFlags.add(String(f)));
  } catch {}



  function rowTypeOf(row) {
    return String(row?.row_type || 'trade').toLowerCase();
  }

  function isTradeRow(row) {
    const rowType = rowTypeOf(row);
    return !rowType || rowType === 'trade';
  }

  function isCashflowRow(row) {
    return rowTypeOf(row) === 'cashflow';
  }

  function isMonthlyAudRevalRow(row) {
    return rowTypeOf(row) === 'monthly_aud_reval';
  }

  function rowPnlValue(row) {
    return isMonthlyAudRevalRow(row) ? asNum(row?.result_cash) : asNum(row?.net_profit ?? row?.realized_pnl);
  }

  function rowPnlCurrency(row) {
    if (isMonthlyAudRevalRow(row)) return String(row?.result_currency || 'AUD');
    return String(row?.realized_pnl_currency || row?.currency || '');
  }
  function fmtProfitPct(v) {
    const n = asNum(v);
    if (!Number.isFinite(n)) return '—';
    return `${fmtNum(n, 4)}%`;
  }

  function priceDistancePct(entry, level, fallbackPct) {
    const e = asNum(entry);
    const l = asNum(level);
    if (Number.isFinite(e) && Number.isFinite(l) && e > 0 && l > 0) return Math.abs(l - e) / e * 100;
    const fb = asNum(fallbackPct);
    if (!Number.isFinite(fb)) return NaN;
    // API rows use percent points here; Excel percentage fractions are
    // normalized server-side while reading workbook cells.
    return fb;
  }

  function fmtPctSmall(v, d = 2) {
    const n = asNum(v);
    if (!Number.isFinite(n)) return '—';
    return `${fmtNum(n, d)}%`;
  }

  function fmtR(v) {
    const n = asNum(v);
    if (!Number.isFinite(n)) return '—';
    return `${fmtNum(n, 3)}R`;
  }

  function distanceLabel(item, kind, variant = 'all') {
    const isFx = (item?.asset_class || '').toLowerCase() === 'fx';
    const suffix = isFx ? ' pips' : ` ${item.quote_currency || 'USDT'}`;

    const keyMap = isFx
      ? {
          sl: { all: 'avg_sl_distance_pips', wins: 'avg_sl_distance_pips_wins', losses: 'avg_sl_distance_pips_losses' },
          tp: { all: 'avg_tp_distance_pips', wins: 'avg_tp_distance_pips_wins', losses: 'avg_tp_distance_pips_losses' },
        }
      : {
          sl: { all: 'avg_sl_distance_quote', wins: 'avg_sl_distance_quote_wins', losses: 'avg_sl_distance_quote_losses' },
          tp: { all: 'avg_tp_distance_quote', wins: 'avg_tp_distance_quote_wins', losses: 'avg_tp_distance_quote_losses' },
        };

    const key = keyMap?.[kind]?.[variant];
    const val = key ? item?.[key] : undefined;
    return Number.isFinite(Number(val)) ? `${fmtNum(val, isFx ? 1 : 6)}${suffix}` : '—';
  }

  function recommendationFromAverages(kind, winAvg, lossAvg) {
    const win = asNum(winAvg);
    const loss = asNum(lossAvg);
    const label = kind === 'stop' ? 'stop loss' : 'target';
    if (!Number.isFinite(win) || !Number.isFinite(loss)) return 'Need wins & losses';
    const tolerance = Math.max(1e-9, Math.max(Math.abs(win), Math.abs(loss)) * 1e-9);
    if (Math.abs(win - loss) <= tolerance) return `Keep ${label}`;
    return win < loss ? `Reduce ${label}` : `Increase ${label}`;
  }

  function recommendationTone(text) {
    const lower = String(text || '').toLowerCase();
    if (lower.startsWith('reduce ') || lower.startsWith('increase ')) return 'tj-rec-action';
    if (lower.startsWith('keep ')) return 'tj-rec-keep';
    return 'tj-rec-muted';
  }

  function directRecommendation(item, kind) {
    if (!item) return '';
    return String(
      kind === 'stop'
        ? (item.stop_recommendation || item['Stop Loss Recommendation'] || '')
        : (item.target_recommendation || item['Target Recommendation'] || '')
    ).trim();
  }

  function instrumentRecommendation(item, kind) {
    const direct = directRecommendation(item, kind);
    if (direct) return direct;
    if (kind === 'stop') {
      return recommendationFromAverages(
        'stop',
        item?.avg_sl_pct_wins ?? item?.avg_sl_distance_pips_wins ?? item?.avg_sl_distance_quote_wins,
        item?.avg_sl_pct_losses ?? item?.avg_sl_distance_pips_losses ?? item?.avg_sl_distance_quote_losses
      );
    }
    return 'Need more target data';
  }

  function overallRecommendation(kind) {
    const risk = state.stats?.groups?.risk_expectancy || {};
    const direct = directRecommendation(risk, kind);
    if (direct) return direct;
    return kind === 'stop'
      ? recommendationFromAverages('stop', risk.avg_stop_pct_winners, risk.avg_stop_pct_losers)
      : 'Need more target data';
  }

  function rowRecommendation(row, kind) {
    const rowType = rowTypeOf(row);
    if (rowType && rowType !== 'trade') return '';
    const symbol = String(row?.symbol || '').trim().toUpperCase();
    const item = (Array.isArray(state.stats?.by_instrument) ? state.stats.by_instrument : [])
      .find((candidate) => String(candidate?.symbol || '').trim().toUpperCase() === symbol);
    if (kind === 'target') {
      return directRecommendation(item, 'target') || overallRecommendation('target');
    }
    const symbolText = instrumentRecommendation(item, kind);
    return symbolText && symbolText !== 'Need wins & losses' ? symbolText : overallRecommendation(kind);
  }

  function renderInstrumentView(stats) {
    const body = q('#tj-inst-table tbody');
    const emptyInst = q('#tj-inst-empty');
    if (!body) return;
    body.innerHTML = '';
    const listRaw = Array.isArray(stats?.by_instrument) ? stats.by_instrument : [];
    const key = state.instSortKey || 'total_trades';
    const dir = (state.instSortDir || 'desc') === 'asc' ? 1 : -1;

    const pickNum = (item, fxKey, cryptoKey) => {
      const isFx = String(item?.asset_class || '').toLowerCase() === 'fx';
      const v = isFx ? item?.[fxKey] : item?.[cryptoKey];
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };

    const getVal = (item) => {
      if (key === 'symbol') return String(item?.symbol || '');
      if (key === 'asset_class') return String(item?.asset_class || '');
      if (key === 'total_trades') return Number.isFinite(Number(item?.total_trades)) ? Number(item.total_trades) : null;
      if (key === 'long_trades') return Number.isFinite(Number(item?.long_trades)) ? Number(item.long_trades) : null;
      if (key === 'short_trades') return Number.isFinite(Number(item?.short_trades)) ? Number(item.short_trades) : null;
      if (key === 'wins') return Number.isFinite(Number(item?.wins)) ? Number(item.wins) : null;
      if (key === 'losses') return Number.isFinite(Number(item?.losses)) ? Number(item.losses) : null;
      if (key === 'break_even') return Number.isFinite(Number(item?.break_even)) ? Number(item.break_even) : null;
      if (key === 'long_wins') return Number.isFinite(Number(item?.long_wins)) ? Number(item.long_wins) : null;
      if (key === 'long_losses') return Number.isFinite(Number(item?.long_losses)) ? Number(item.long_losses) : null;
      if (key === 'short_wins') return Number.isFinite(Number(item?.short_wins)) ? Number(item.short_wins) : null;
      if (key === 'short_losses') return Number.isFinite(Number(item?.short_losses)) ? Number(item.short_losses) : null;
      if (key === 'avg_sl_w') return pickNum(item, 'avg_sl_distance_pips_wins', 'avg_sl_distance_quote_wins');
      if (key === 'avg_sl_l') return pickNum(item, 'avg_sl_distance_pips_losses', 'avg_sl_distance_quote_losses');
      if (key === 'stop_rec') return instrumentRecommendation(item, 'stop');
      if (key === 'avg_tp_w') return pickNum(item, 'avg_tp_distance_pips_wins', 'avg_tp_distance_quote_wins');
      if (key === 'avg_tp_l') return pickNum(item, 'avg_tp_distance_pips_losses', 'avg_tp_distance_quote_losses');
      if (key === 'target_rec') return instrumentRecommendation(item, 'target');
      if (key === 'avg_duration') return Number.isFinite(Number(item?.avg_trade_duration_seconds)) ? Number(item.avg_trade_duration_seconds) : null;
      if (key === 'min_trade_duration_seconds') return Number.isFinite(Number(item?.min_trade_duration_seconds)) ? Number(item.min_trade_duration_seconds) : null;
      if (key === 'max_trade_duration_seconds') return Number.isFinite(Number(item?.max_trade_duration_seconds)) ? Number(item.max_trade_duration_seconds) : null;
      return null;
    };

    const list = listRaw.slice().sort((a, b) => {
      const av = getVal(a);
      const bv = getVal(b);
      const aNull = av === null || av === undefined || (typeof av === 'number' && !Number.isFinite(av));
      const bNull = bv === null || bv === undefined || (typeof bv === 'number' && !Number.isFinite(bv));
      if (aNull && bNull) return 0;
      if (aNull) return 1;
      if (bNull) return -1;

      if (typeof av === 'number' && typeof bv === 'number') return dir * (av - bv);
      return dir * String(av).localeCompare(String(bv));
    });
    if (!list.length) {
      if (emptyInst) emptyInst.style.display = 'block';
      return;
    }
    if (emptyInst) emptyInst.style.display = 'none';
    list.forEach((item) => {
      const tr = document.createElement('tr');
      tr.dataset.symbol = String(item.symbol || '');
      tr.dataset.assetClass = String(item.asset_class || '');
      tr.innerHTML = `
        <td>${item.symbol || '—'}</td>
        <td>${item.asset_class || '—'}</td>
        <td style="text-align:right">${fmtNum(item.total_trades,0)}</td>
        <td style="text-align:right">${fmtNum(item.long_trades,0)}</td>
        <td style="text-align:right">${fmtNum(item.short_trades,0)}</td>
        <td style="text-align:right">${fmtNum(item.wins,0)}</td>
        <td style="text-align:right">${fmtNum(item.losses,0)}</td>
        <td style="text-align:right">${fmtNum(item.break_even,0)}</td>
        <td style="text-align:right">${fmtNum(item.long_wins,0)}</td>
        <td style="text-align:right">${fmtNum(item.long_losses,0)}</td>
        <td style="text-align:right">${fmtNum(item.short_wins,0)}</td>
        <td style="text-align:right">${fmtNum(item.short_losses,0)}</td>
        <td style="text-align:right">${distanceLabel(item,'sl','wins')}</td>
        <td style="text-align:right">${distanceLabel(item,'sl','losses')}</td>
        <td class="tj-rec ${recommendationTone(instrumentRecommendation(item,'stop'))}">${instrumentRecommendation(item,'stop')}</td>
        <td style="text-align:right">${distanceLabel(item,'tp','wins')}</td>
        <td style="text-align:right">${distanceLabel(item,'tp','losses')}</td>
        <td class="tj-rec ${recommendationTone(instrumentRecommendation(item,'target'))}">${instrumentRecommendation(item,'target')}</td>
        <td style="text-align:right">${fmtDuration(item.avg_trade_duration_seconds)}</td>
        <td style="text-align:right">${fmtDuration(item.min_trade_duration_seconds)}</td>
        <td style="text-align:right">${fmtDuration(item.max_trade_duration_seconds)}</td>`;
      body.appendChild(tr);
    });
  }

  function syncTopScrollbar() {
    const top = q('#tj-top-scroll');
    const topInner = q('#tj-top-scroll > div');
    const wrap = q('#tj-trades-wrap');
    const table = q('#tj-table');
    if (!top || !topInner || !wrap || !table) return;
    topInner.style.width = `${table.scrollWidth}px`;
    let guard = false;
    top.onscroll = () => { if (guard) return; guard = true; wrap.scrollLeft = top.scrollLeft; guard = false; };
    wrap.onscroll = () => { if (guard) return; guard = true; top.scrollLeft = wrap.scrollLeft; guard = false; };
  }

  function applyView() {
    const tradesWrap = q('#tj-trades-wrap');
    const instWrap = q('#tj-inst-view');
    const calWrap = q('#tj-cal-view');
    const equityWrap = q('#tj-equity-view');
    const topScroll = q('#tj-top-scroll');
    const tradesBtn = q('#tj-view-trades-btn');
    const instBtn = q('#tj-view-inst-btn');
    const calBtn = q('#tj-view-cal-btn');
    const equityBtn = q('#tj-view-equity-btn');
    const showTrades = state.view === 'trades';
    const showInst = state.view === 'instrument';
    const showCal = state.view === 'calendar';
    const showEquity = state.view === 'equity';
    tradesWrap?.classList.toggle('hidden', !showTrades);
    topScroll?.classList.toggle('hidden', !showTrades);
    instWrap?.classList.toggle('hidden', !showInst);
    calWrap?.classList.toggle('hidden', !showCal);
    equityWrap?.classList.toggle('hidden', !showEquity);

    [tradesBtn, instBtn, calBtn, equityBtn].forEach((b) => { if (b) { b.style.outline='none'; b.style.opacity='0.8'; }});
    if (showInst && instBtn) { instBtn.style.outline='1px solid #60a5fa'; instBtn.style.opacity='1'; }
    if (showCal && calBtn) { calBtn.style.outline='1px solid #60a5fa'; calBtn.style.opacity='1'; }
    if (showEquity && equityBtn) { equityBtn.style.outline='1px solid #60a5fa'; equityBtn.style.opacity='1'; }
    if (showTrades && tradesBtn) { tradesBtn.style.outline='1px solid #60a5fa'; tradesBtn.style.opacity='1'; }
    persistUiState();
  }

  function _monthBounds(ym) {
    const m = /^\d{4}-\d{2}$/.test(String(ym || '')) ? ym : new Date().toISOString().slice(0, 7);
    const [yy, mm] = m.split('-').map(Number);
    const start = new Date(Date.UTC(yy, mm - 1, 1));
    const end = new Date(Date.UTC(yy, mm, 1));
    return { start, end, ym: `${yy.toString().padStart(4, '0')}-${String(mm).padStart(2, '0')}` };
  }

  function _monthShift(ym, delta) {
    const b = _monthBounds(ym);
    const dt = new Date(Date.UTC(b.start.getUTCFullYear(), b.start.getUTCMonth() + delta, 1));
    return `${dt.getUTCFullYear().toString().padStart(4, '0')}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}`;
  }

  function renderCalendarView(rows) {
    const grid = q('#tj-cal-grid');
    const title = q('#tj-cal-title');
    if (!grid) return;
    const { start, end, ym } = _monthBounds(state.calMonth);
    state.calMonth = ym;
    if (title) title.textContent = start.toLocaleString('en-AU', { month: 'long', year: 'numeric', timeZone: 'UTC' });

    const daily = new Map();
    const inMonth = (d) => d >= start && d < end;
    (rows || []).forEach((r) => {
      const dtRaw = r?.close_time || r?.open_time;
      const dt = dtRaw ? new Date(dtRaw) : null;
      if (!dt || Number.isNaN(dt.getTime()) || !inMonth(dt)) return;
      const key = dt.toISOString().slice(0, 10);
      const rowType = rowTypeOf(r);
      const isTrade = rowType === 'trade' || !rowType;
      if (rowType === 'monthly_aud_reval') return;
      const isTestTrade = isTrade && String(r?.is_test_trade ?? '').toLowerCase() === 'true';
      if (isTestTrade) return;
      if (!daily.has(key)) daily.set(key, { trades: 0, fx: 0, crypto: 0, pnlByCcy: new Map() });
      const d = daily.get(key);
      if (isTrade) {
        d.trades += 1;
        if (String(r?.asset_class || '').toLowerCase() === 'fx') d.fx += 1;
        if (String(r?.asset_class || '').toLowerCase() === 'crypto') d.crypto += 1;
        const pnl = rowPnlValue(r);
        const ccy = rowPnlCurrency(r).trim().toUpperCase();
        if (Number.isFinite(pnl) && ccy) d.pnlByCcy.set(ccy, (d.pnlByCcy.get(ccy) || 0) + pnl);
      }
    });

    const firstDow = (start.getUTCDay() + 6) % 7;
    const daysInMonth = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
    grid.innerHTML = '';
    ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach((name) => {
      const hd = document.createElement('div');
      hd.className = 'cal-dow';
      hd.textContent = name;
      grid.appendChild(hd);
    });

    for (let i = 0; i < firstDow; i += 1) {
      const pad = document.createElement('div');
      pad.className = 'cal-day empty';
      grid.appendChild(pad);
    }

    for (let day = 1; day <= daysInMonth; day += 1) {
      const key = `${ym}-${String(day).padStart(2, '0')}`;
      const d = daily.get(key) || { trades: 0, fx: 0, crypto: 0, pnlByCcy: new Map() };
      const card = document.createElement('div');

      const pnlEntries = Array.from(d.pnlByCcy.entries()).filter(([, v]) => Number.isFinite(Number(v)));
      let pnlRef = null;
      if (pnlEntries.length === 1) {
        pnlRef = pnlEntries[0][1];
      } else if (pnlEntries.length > 1) {
        const aud = pnlEntries.find(([ccy]) => ccy === 'AUD');
        if (aud) pnlRef = aud[1];
        else pnlRef = pnlEntries.slice().sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0][1];
      }
      const pnlClass = pnlRef === null ? '' : (pnlRef > 1e-12 ? 'pnl-pos' : (pnlRef < -1e-12 ? 'pnl-neg' : 'pnl-flat'));
      card.className = `cal-day ${d.trades > 0 ? 'has-trades' : ''} ${d.trades > 0 && pnlClass ? pnlClass : ''}`.trim();

      const pnlTop = pnlEntries
        .slice()
        .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
        .slice(0, 2);

      card.innerHTML = `
        <div class="cal-day-num">${day}</div>
        <div class="cal-lines">
          ${d.trades > 0 ? `<div>Trades: <b>${d.trades}</b></div>` : '<div class="muted">No trades</div>'}
          ${d.trades > 0 ? `<div class="muted">FX ${d.fx} · Crypto ${d.crypto}</div>` : ''}
          ${pnlTop.map(([ccy, v]) => `<div class="${v > 1e-12 ? 'num pos' : (v < -1e-12 ? 'num neg' : 'muted')}">P/L ${fmtNum(v, 2)} ${ccy}</div>`).join('')}
        </div>
      `;
      grid.appendChild(card);
    }
  }

  let _equityResizeWired = false;
  function renderEquityView(rows) {
    const wrap = q('#tj-equity-wrap');
    if (!wrap) return;
    wrap.innerHTML = '';
    const byAccount = new Map();

    (rows || []).forEach((r) => {
      const account = String(r?.account_label || r?.account || 'Unknown').trim() || 'Unknown';
      const dtRaw = r?.close_time || r?.open_time;
      const ts = dtRaw ? new Date(dtRaw).getTime() : NaN;
      if (String(r?.is_test_trade ?? '').toLowerCase() === 'true' || isMonthlyAudRevalRow(r)) return;
      const bal = asNum(r?.analysis_balance_after_trade ?? r?.balance_after_trade ?? r?.cashflow_new_balance);
      if (!Number.isFinite(ts) || !Number.isFinite(bal)) return;
      if (!byAccount.has(account)) byAccount.set(account, []);
      byAccount.get(account).push({ ts, bal });
    });

    if (!byAccount.size) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No equity data available.';
      wrap.appendChild(empty);
      return;
    }

    const drawCanvas = (canvas, pts) => {
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(280, Math.floor(rect.width || canvas.clientWidth || 900));
      const height = 220;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const pad = { l: 42, r: 12, t: 12, b: 24 };
      const minX = pts[0].ts;
      const maxX = pts[pts.length - 1].ts;
      const minY = Math.min(...pts.map((p) => p.bal));
      const maxY = Math.max(...pts.map((p) => p.bal));
      const spanX = Math.max(1, maxX - minX);
      const spanY = Math.max(1e-9, maxY - minY);

      const x = (v) => pad.l + ((v - minX) / spanX) * (width - pad.l - pad.r);
      const y = (v) => height - pad.b - ((v - minY) / spanY) * (height - pad.t - pad.b);

      ctx.strokeStyle = '#334155';
      ctx.lineWidth = 1;
      for (let i = 0; i < 4; i += 1) {
        const gy = pad.t + (i / 3) * (height - pad.t - pad.b);
        ctx.beginPath();
        ctx.moveTo(pad.l, gy);
        ctx.lineTo(width - pad.r, gy);
        ctx.stroke();
      }

      ctx.strokeStyle = '#60a5fa';
      ctx.lineWidth = 2;
      ctx.beginPath();
      pts.forEach((p, i) => {
        const xx = x(p.ts);
        const yy = y(p.bal);
        if (i === 0) ctx.moveTo(xx, yy);
        else ctx.lineTo(xx, yy);
      });
      ctx.stroke();

      const last = pts[pts.length - 1];
      ctx.fillStyle = '#93c5fd';
      ctx.beginPath();
      ctx.arc(x(last.ts), y(last.bal), 3.5, 0, Math.PI * 2);
      ctx.fill();
    };

    Array.from(byAccount.entries()).sort((a,b)=>a[0].localeCompare(b[0])).forEach(([account, pts]) => {
      pts.sort((a, b) => a.ts - b.ts);
      const card = document.createElement('div');
      card.className = 'equity-card';
      const minBal = Math.min(...pts.map((p) => p.bal));
      const maxBal = Math.max(...pts.map((p) => p.bal));
      card.innerHTML = `
        <div class="equity-head">
          <div><strong>${account}</strong></div>
          <div class="muted">${pts.length} points · Min ${fmtNum(minBal, 2)} · Max ${fmtNum(maxBal, 2)}</div>
        </div>
      `;
      const canvas = document.createElement('canvas');
      canvas.className = 'equity-canvas';
      card.appendChild(canvas);
      wrap.appendChild(card);
      drawCanvas(canvas, pts);
    });

    if (!_equityResizeWired) {
      _equityResizeWired = true;
      window.addEventListener('resize', () => {
        if (state.view === 'equity') renderEquityView(getFilteredRows());
      });
    }
  }

  function sortRows(rows) {
    const out = [...rows];
    const { sortKey, sortDir } = state;
    const dir = sortDir === 'asc' ? 1 : -1;
    out.sort((a, b) => {
      let av = a?.[sortKey], bv = b?.[sortKey];
      if (sortKey === 'net_profit') {
        av = rowPnlValue(a);
        bv = rowPnlValue(b);
      }
      if (sortKey === 'close_time' || sortKey === 'open_time') {
        av = av ? new Date(av).getTime() : -Infinity;
        bv = bv ? new Date(bv).getTime() : -Infinity;
      }
      const an = Number(av), bn = Number(bv);
      const bothNum = Number.isFinite(an) && Number.isFinite(bn);
      if (bothNum) return (an - bn) * dir;
      const as = String(av ?? '').toLowerCase();
      const bs = String(bv ?? '').toLowerCase();
      if (as < bs) return -1 * dir;
      if (as > bs) return 1 * dir;
      return 0;
    });
    return out;
  }

  function renderSortIndicators() {
    qa('#tj-table thead th[data-sort]').forEach((th) => {
      const key = th.dataset.sort;
      const base = th.textContent.replace(/[ ▲▼]$/, '');
      th.textContent = base + (key === state.sortKey ? (state.sortDir === 'asc' ? ' ▲' : ' ▼') : '');
      th.style.cursor = 'pointer';
    });
  }

  const EDIT_FIELDS = [
    'open_time', 'close_time', 'symbol', 'side', 'timeframe', 'is_test_trade', 'setup', 'qty', 'qty_unit',
    'entry_price', 'exit_price', 'stop_loss', 'take_profit', 'commission', 'net_profit',
    'balance_after_trade', 'breakeven', 'notes', 'account', 'account_label', 'currency',
  ];
  const MANUAL_ACCOUNT_FIELDS = new Set(['account', 'account_label', 'currency', 'qty_unit']);

  function setEditorError(msg) {
    if (editorErr) editorErr.textContent = msg || '';
  }

  function openEditor(row) {
    if (!editorForm || !editorModal) return;
    const creating = !row;
    const rec = row || {};
    state.editingIsCreate = creating;
    state.editingRowId = rec.id || null;
    state.editorOpen = true;
    state.editorDirty = false;
    setEditorError('');
    if (editorTitle) editorTitle.textContent = creating ? 'Add trade' : 'Edit trade';

    EDIT_FIELDS.forEach((name) => {
      const el = editorForm.elements.namedItem(name);
      if (!el) return;
      const isDate = name === 'open_time' || name === 'close_time';
      const v = rec?.[name];
      el.value = isDate ? isoToInput(v) : (v ?? '');
    });

    const isManual = creating || !!rec?.is_manual || String(rec?.source || '').toLowerCase() === 'manual';
    EDIT_FIELDS.forEach((name) => {
      const el = editorForm.elements.namedItem(name);
      if (!el || !MANUAL_ACCOUNT_FIELDS.has(name)) return;
      el.disabled = !isManual;
    });

    editorModal.classList.add('open');
    editorModal.setAttribute('aria-hidden', 'false');
    syncActionButtons();
  }

  function closeEditor() {
    if (!editorModal || !editorForm) return;
    editorModal.classList.remove('open');
    editorModal.setAttribute('aria-hidden', 'true');
    state.editorOpen = false;
    state.editorDirty = false;
    state.editingIsCreate = false;
    state.editingRowId = null;
    state.saveInFlight = false;
    setEditorError('');
    syncActionButtons();
  }

  function collectEditorPayload() {
    if (!editorForm) return {};
    const payload = {};
    for (const key of EDIT_FIELDS) {
      const el = editorForm.elements.namedItem(key);
      if (!el || el.disabled) continue;
      const raw = String(el.value ?? '');
      const trimmed = raw.trim();
      if (!trimmed) continue;
      payload[key] = (key === 'open_time' || key === 'close_time') ? inputToIso(trimmed) : trimmed;
    }
    return payload;
  }

  function renderRows(rows) {
    const sorted = sortRows(rows);
    tbody.innerHTML = '';
    if (!sorted.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    for (const r of sorted) {
      const rowType = rowTypeOf(r);
      const isCashflow = isCashflowRow(r);
      const isMonthlyAud = isMonthlyAudRevalRow(r);
      const pnl = rowPnlValue(r);
      const bal = asNum(r.analysis_balance_after_trade ?? r.balance_after_trade ?? r.cashflow_new_balance);
      const ccy = r.balance_after_trade_currency || r.currency || '';
      const tr = document.createElement('tr');

      if (isMonthlyAud) {
        const pnlCcy = rowPnlCurrency(r) || 'AUD';
        const cls = Number.isFinite(pnl) ? (pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '')) : '';
        tr.className = 'tj-monthly-aud-reval';
        tr.title = 'Monthly Bybit Live AUD P/L note; excluded from trading metrics.';
        tr.innerHTML = `
          <td>${fmtTime(r.open_time || r.close_time)}</td>
          <td>${fmtTime(r.close_time || r.open_time)}</td>
          <td>${r.account_label || r.account || 'Bybit Live'}</td>
          <td>${r.symbol || 'MONTHLY AUD P/L'}</td>
          <td>NOTE</td>
          <td>—</td>
          <td>—</td>
          <td>${r.setup || 'Monthly Bybit Live AUD P/L note - excluded from metrics'}</td>
          <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
          <td class="num ${cls}">${fmtNum(pnl, 4)} ${pnlCcy}</td>
          <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td></td><td></td>
        `;
        tbody.appendChild(tr);
        continue;
      }

      if (isCashflow) {
        const amt = asNum(r.cashflow_amount);
        const flowCls = Number.isFinite(amt) ? (amt > 0 ? 'pos' : (amt < 0 ? 'neg' : '')) : '';
        const baseLabel = r.side || (amt > 0 ? 'DEPOSIT' : (amt < 0 ? 'WITHDRAWAL' : 'CASHFLOW'));
        const amtLabel = Number.isFinite(amt) ? ` (${fmtNum(amt, 2)} ${ccy})` : '';
        const flowLabel = `${baseLabel}${amtLabel}`;
        tr.innerHTML = `
          <td>${fmtTime(r.open_time || r.close_time)}</td>
          <td>${fmtTime(r.close_time || r.open_time)}</td>
          <td>${r.account_label || r.account || '—'}</td>
          <td>${r.symbol || 'CASHFLOW'}</td>
          <td class="num ${flowCls}">${flowLabel}</td>
          <td>—</td>
          <td>—</td>
          <td title="${r.cashflow_reason || ''}">${r.cashflow_reason || r.setup || '—'}</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>${Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '—'}</td>
          <td>—</td>
          <td>—</td>
          <td></td>
          <td></td>
        `;
        tbody.appendChild(tr);
        continue;
      }

      if (r.id) tr.setAttribute('data-row-id', String(r.id));
      tr.innerHTML = `
        <td>${fmtTime(r.open_time)}</td>
        <td>${fmtTime(r.close_time || r.open_time)}</td>
        <td>${r.account_label || r.account || '—'}</td>
        <td title="${r.symbol_raw || r.symbol || ''}">${r.symbol || '—'}</td>
        <td>${r.side || '—'}</td>
        <td>${r.timeframe || r.metrics?.timeframe || '—'}</td>
        <td>${String(r.is_test_trade) === 'true' ? 'Yes' : (String(r.is_test_trade) === 'false' ? 'No' : '—')}</td>
        <td>${r.setup || '—'}</td>
        <td>${fmtQty(r.qty, r)}${r.qty_unit === 'lots' ? ' lot' : ''}</td>
        <td>${fmtNum(r.entry_price, 6)}</td>
        <td>${fmtNum(r.exit_price, 6)}</td>
        <td>${fmtNum(r.stop_loss, 6)}</td>
        <td>${fmtPctSmall(priceDistancePct(r.entry_price, r.stop_loss, r.stop_loss_distance_pct), 2)}</td>
        <td>${fmtNum(r.take_profit, 6)}</td>
        <td>${fmtPctSmall(priceDistancePct(r.entry_price, r.take_profit, r.target_distance_pct), 2)}</td>
        <td>${fmtNum(r.commission ?? r.fees, 4)} ${r.commission_currency || r.fee_currency || ''}</td>
        <td class="num ${Number.isFinite(pnl) ? (pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '')) : ''}">${fmtNum(pnl, 4)} ${rowPnlCurrency(r)}</td>
        <td class="num ${asNum(r.result_pct ?? r.profit_pct) > 0 ? 'pos' : (asNum(r.result_pct ?? r.profit_pct) < 0 ? 'neg' : '')}">${fmtProfitPct(r.result_pct ?? r.profit_pct)}</td>
        <td class="num ${asNum(r.r_multiple) > 0 ? 'pos' : (asNum(r.r_multiple) < 0 ? 'neg' : '')}">${fmtR(r.r_multiple)}</td>
        <td>${Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '—'}</td>
        <td>${fmtDuration(r.trade_duration_seconds)}</td>
        <td>${r.breakeven || '—'}</td>
        <td>${r.id && String(r.source || '').toLowerCase() !== 'manual' ? `<a href="/trade-chart/${encodeURIComponent(r.id)}" target="_blank" rel="noopener">Chart</a>` : ''}</td>
        <td>
          <button type="button" data-action="edit" data-row-id="${String(r.id || '').replace(/"/g, '&quot;')}">Edit</button>
          ${(r.is_manual || String(r.source || '').toLowerCase() === 'manual') ? `<button type="button" class="btn-danger" data-action="delete" data-row-id="${String(r.id || '').replace(/"/g, '&quot;')}">Delete</button>` : ''}
        </td>
      `;
      tbody.appendChild(tr);
    }
  }

  function renderBalances(items) {
    const wrap = q('#tj-balances');
    wrap.innerHTML = '';
    if (!Array.isArray(items) || !items.length) {
      const div = document.createElement('div');
      div.className = 'bal-card muted';
      div.textContent = 'No balances available yet.';
      wrap.appendChild(div);
      return;
    }
    (items || []).forEach((b) => {
      const div = document.createElement('div');
      div.className = 'bal-card';
      const label = String(b.label || b.account || 'Account');
      const source = String(b.balance_source || b.source || 'unknown');
      const asOf = b.as_of ? ` · ${fmtTime(b.as_of)}` : '';
      const staleOverridden = !!b.stale_cashflow_overridden;
      const staleOandaBackfill = label.toUpperCase() === 'OANDA DEMO' && !!state?.diagnostics?.stale_oanda_demo_balance_not_backfilled;
      const oandaBackfillErr = label.toUpperCase() === 'OANDA DEMO' ? String(state?.diagnostics?.oanda_export_append_error || '') : '';
      const oandaRuntimeErr = label.toUpperCase() === 'OANDA DEMO' ? String(state?.oandaRepairLastError || '') : '';
      div.innerHTML = `
        <div class="muted">${label}</div>
        <div style="font-size:1.0rem;font-weight:600">${fmtNum(b.balance, (() => { const c = String(b.currency || '').toUpperCase(); if (c === 'AUD' || c === 'USD') return 2; if (c === 'USDT') return 8; return 6; })())} ${b.currency || ''}</div>
        <div class="muted" style="font-size:0.8rem" title="${source}${asOf}">Source: ${source}${asOf}</div>
        ${staleOverridden ? '<div class="muted" style="font-size:0.78rem">Using OANDA export/API balance; stale cashflow anchor ignored.</div>' : ''}
        ${staleOandaBackfill ? `<div class="muted" style="font-size:0.78rem;color:#b91c1c">OANDA demo export exists but was not applied. Balance is stale.${state?.diagnostics?.latest_export_balance ? ` Latest export: ${fmtNum(state.diagnostics.latest_export_balance, 2)} AUD.` : ''}</div>` : ''}
        ${oandaBackfillErr.includes('MISSING_XLRD_FOR_XLS') ? `<div class="muted" style="font-size:0.78rem;color:#b91c1c">Install xlrd in the journal runtime, then rerun OANDA history backfill.</div>` : ''}
        ${oandaRuntimeErr ? `<div class="muted" style="font-size:0.78rem;color:#b91c1c">${oandaRuntimeErr}</div>` : ''}
        ${b.stale_balance_warning && !b.repair_export_available ? `<div class="muted" style="font-size:0.78rem;color:#b91c1c">OANDA DEMO balance is stale. Run OANDA demo history export/backfill.</div>` : ''}
        ${b.stale_balance_warning ? `<button class="tj-repair-oanda" style="margin-top:6px">Repair OANDA DEMO</button>` : ''}
      `;
      wrap.appendChild(div);
      const repairBtn = div.querySelector('.tj-repair-oanda');
      if (repairBtn) {
        repairBtn.addEventListener('click', async () => {
          try {
            const r = await fetch('/api/trading-journal/oanda-demo/repair-balance', { method: 'POST' });
            const p = await r.json();
            if (!r.ok || p.ok === false) throw new Error(p.error || p.message || 'Repair failed');
            setStatus('OANDA DEMO repair applied. Refreshing journal…', false);
            schedulePostRepairRefresh();
          } catch (e) {
            alert(String(e?.message || e));
          }
        });
      }
    });
  }

  function schedulePostRepairRefresh() {
    if (state.oandaRepairRefreshPending) return;
    state.oandaRepairRefreshPending = true;
    setTimeout(async () => {
      if (loadInFlight) {
        state.oandaRepairRefreshPending = false;
        schedulePostRepairRefresh();
        return;
      }
      state.oandaRepairRefreshPending = false;
      await load({ silent: false, skipAutoSync: true, preserveStatus: true, statusOverride: 'OANDA repair applied. Refreshing…' });
    }, 50);
  }

  async function maybeAutoRepairOandaDemo(items) {
    if (state.oandaRepairAttempted) return;
    const demo = (items || []).find((b) => String(b?.label || b?.account || '').toUpperCase() === 'OANDA DEMO');
    if (!demo || !demo.stale_balance_warning || !demo.repair_available || !demo.repair_endpoint) return;
    if (!demo.repair_export_available) return;
    if (String(demo.repair_blocked_reason || '').toUpperCase() === 'MISSING_XLRD_FOR_XLS') return;
    state.oandaRepairAttempted = true;
    try {
      const r = await fetch(demo.repair_endpoint, { method: 'POST' });
      const p = await r.json();
      if (!r.ok || p.ok === false) throw new Error(p.error || p.message || 'Repair failed');
      schedulePostRepairRefresh();
    } catch (err) {
      state.oandaRepairLastError = String(err?.message || err);
    }
  }
  function fmtMoneyValue(value, ccy, decimals = 2) { return `${ccy || 'UNKNOWN'} ${fmtNum(value, decimals)}`; }
  function formatTradeFilterLabel(row, fallbackLabel) {
    if (String(fallbackLabel || '').trim()) return String(fallbackLabel).trim();
    const symbol = row?.symbol || row?.symbol_raw || 'Trade';
    const stamp = row?.close_time || row?.open_time;
    const d = stamp ? new Date(stamp) : null;
    const dateOnly = d && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : String(stamp || '').slice(0, 10);
    return dateOnly ? `${symbol} · ${dateOnly}` : String(symbol);
  }
  function fmtMoneyBreakdown(bucket, key, decimals = 2) {
    const map = bucket?.money_by_currency?.[key];
    if (map && typeof map === 'object' && Object.keys(map).length) return Object.entries(map).map(([c, v]) => fmtMoneyValue(v, c, decimals)).join(' / ');
    const legacy = bucket?.[key];
    const ccy = bucket?.currency || (bucket?.money_by_currency?.currencies?.[0]) || 'UNKNOWN';
    return Number.isFinite(asNum(legacy)) ? fmtMoneyValue(legacy, ccy, decimals) : '—';
  }
  function moneyTone(bucket, key) {
    const map = bucket?.money_by_currency?.[key];
    const vals = map && typeof map === 'object' ? Object.values(map).map((v) => asNum(v)).filter((v) => Number.isFinite(v) && v !== 0) : [asNum(bucket?.[key])].filter((v) => Number.isFinite(v) && v !== 0);
    if (!vals.length) return 'tj-stat-neutral';
    if (vals.every((v) => v > 0)) return 'tj-stat-winner';
    if (vals.every((v) => v < 0)) return 'tj-stat-loser';
    return 'tj-stat-neutral';
  }

  function renderStats(stats) {
    const wrap = q('#tj-stats');
    if (!wrap) return;
    wrap.innerHTML = '';
    if (!stats) return;

    wrap.className = 'tj-stats-dashboard';
    const g = stats?.groups || {};
    const byMarket = { ...(g?.by_market || {}) };
    if (!byMarket.overall || !byMarket.fx || !byMarket.crypto) {
      (g?.market_breakdown || []).forEach((m) => {
        const lbl = String(m?.label || '').toLowerCase();
        if (lbl === 'overall') byMarket.overall = byMarket.overall || m;
        else if (lbl === 'forex' || lbl === 'fx') byMarket.fx = byMarket.fx || m;
        else if (lbl === 'crypto') byMarket.crypto = byMarket.crypto || m;
      });
    }
    byMarket.overall = byMarket.overall || g?.overview || stats?.totals || {};

    const toneBySign = (value) => {
      const n = asNum(value);
      if (!Number.isFinite(n) || n === 0) return 'tj-stat-neutral';
      return n > 0 ? 'tj-stat-positive' : 'tj-stat-negative';
    };
    const escHtml = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const escAttr = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const fmtDateOnly = (v) => { if (!v) return ''; const d = new Date(v); return Number.isNaN(d.getTime()) ? String(v).slice(0,10) : d.toISOString().slice(0,10); };
    function fmtStatTradeJump(ref) { if (!ref || typeof ref !== 'object' || !ref.id) return ''; const label = `${ref.symbol || 'Trade'}${fmtDateOnly(ref.date || ref.close_time || ref.open_time) ? ` · ${fmtDateOnly(ref.date || ref.close_time || ref.open_time)}` : ''}`; return `<button type="button" class="tj-stat-jump" data-jump-row-id="${escAttr(ref.id)}" data-jump-row-label="${escAttr(label)}">${escHtml(label)}</button>`; }
    function fmtLeader(leader, countKey) {
      const count = asNum(leader?.[countKey]);
      if (!leader || !leader.symbol || !Number.isFinite(count) || count <= 0) return '—';
      const suffix = countKey === 'wins' ? 'wins' : 'losses';
      return `${leader.symbol} — ${Math.round(count)} ${suffix}`;
    }
    const row = (label, value, valueCls='tj-stat-neutral', labelCls='tj-stat-neutral', detail='') => `<tr class="tj-stat-row"><td class="tj-stat-label ${labelCls}">${escHtml(label)}</td><td class="tj-stat-value ${valueCls}">${escHtml(value ?? '—')}</td><td class="tj-stat-detail">${detail || ''}</td></tr>`;
    const sec = (title, rows) => `<section class="tj-stats-section"><div class="tj-stats-title">${title}</div><table class="tj-stats-table">${rows.length ? rows.join('') : row('No data', '—')}</table></section>`;
    const core = (m) => [
      row('Trades', m?.trades),
      row('Wins', m?.wins, 'tj-stat-winner'),
      row('Losses', m?.losses, 'tj-stat-loser'),
      row('Break-even', m?.break_even),
      row('Win rate', fmtPctSmall(m?.win_rate_pct)),
      row('Net P/L', fmtMoneyBreakdown(m, 'net_profit_total'), moneyTone(m, 'net_profit_total')),
      row('Gross gain', fmtMoneyBreakdown(m, 'gross_gain'), 'tj-stat-winner'),
      row('Gross loss', fmtMoneyBreakdown(m, 'gross_loss'), 'tj-stat-loser'),
      row('Avg result %', fmtPctSmall(m?.avg_result_pct), toneBySign(m?.avg_result_pct)),
      row('Max loss %', fmtPctSmall(m?.min_result_pct), 'tj-stat-loser', 'tj-stat-neutral', fmtStatTradeJump(m?.metric_sources?.min_result_pct)),
      row('Max win %', fmtPctSmall(m?.max_result_pct), 'tj-stat-winner', 'tj-stat-neutral', fmtStatTradeJump(m?.metric_sources?.max_result_pct)),
      row('Avg R', fmtR(m?.avg_r_multiple), toneBySign(m?.avg_r_multiple)),
      row('Max R loss', fmtR(m?.min_r_multiple), 'tj-stat-loser', 'tj-stat-neutral', fmtStatTradeJump(m?.metric_sources?.min_r_multiple)),
      row('Max R win', fmtR(m?.max_r_multiple), 'tj-stat-winner', 'tj-stat-neutral', fmtStatTradeJump(m?.metric_sources?.max_r_multiple)),
      row('Max gain', fmtMoneyBreakdown(m, 'max_gain'), 'tj-stat-winner', 'tj-stat-neutral', fmtStatTradeJump(m?.metric_sources?.max_gain)),
      row('Max loss', fmtMoneyBreakdown(m, 'max_loss'), 'tj-stat-loser', 'tj-stat-neutral', fmtStatTradeJump(m?.metric_sources?.max_loss)),
      row('Avg stop %', fmtPctSmall(m?.avg_stop_pct)),
      row('Avg target %', fmtPctSmall(m?.avg_target_pct)),
      row('Avg duration', fmtDuration(m?.avg_duration_seconds)),
    ];
    const risk = g?.risk_expectancy || stats?.totals || {};
    const dur = g?.duration || stats?.totals || {};
    const leaders = g?.leaders || {};

    const sections = [
      { key: 'overall', rows: core(byMarket.overall), title: 'Overall' },
      { key: 'winners', rows: [row('Avg stop %', fmtPctSmall(risk?.avg_stop_pct_winners)), row('Avg target %', fmtPctSmall(risk?.avg_target_pct_winners)), row('Avg result %', fmtPctSmall(risk?.avg_result_pct_winners), 'tj-stat-winner'), row('Avg R', fmtR(risk?.avg_r_multiple_winners), 'tj-stat-winner')], title: 'Winners' },
      { key: 'losers', rows: [row('Avg stop %', fmtPctSmall(risk?.avg_stop_pct_losers)), row('Avg target %', fmtPctSmall(risk?.avg_target_pct_losers)), row('Avg result %', fmtPctSmall(risk?.avg_result_pct_losers), 'tj-stat-loser'), row('Avg R', fmtR(risk?.avg_r_multiple_losers), 'tj-stat-loser')], title: 'Losers' },
      { key: 'drawdown', rows: [row('Max drawdown', fmtPctSmall(risk?.max_drawdown_pct), 'tj-stat-drawdown'), row('Avg drawdown', fmtPctSmall(risk?.avg_drawdown_pct), 'tj-stat-drawdown')], title: 'Drawdown' },
      { key: 'duration', rows: [row('Overall avg', fmtDuration(dur?.overall_avg_seconds)), row('Overall shortest', fmtDuration(dur?.overall_shortest_seconds), 'tj-stat-neutral', 'tj-stat-neutral', fmtStatTradeJump(dur?.metric_sources?.overall_shortest_seconds)), row('Overall longest', fmtDuration(dur?.overall_longest_seconds), 'tj-stat-neutral', 'tj-stat-neutral', fmtStatTradeJump(dur?.metric_sources?.overall_longest_seconds)), row('FX shortest', fmtDuration(dur?.fx_shortest_seconds), 'tj-stat-neutral', 'tj-stat-neutral', fmtStatTradeJump(dur?.metric_sources?.fx_shortest_seconds)), row('FX longest', fmtDuration(dur?.fx_longest_seconds), 'tj-stat-neutral', 'tj-stat-neutral', fmtStatTradeJump(dur?.metric_sources?.fx_longest_seconds)), row('Crypto shortest', fmtDuration(dur?.crypto_shortest_seconds), 'tj-stat-neutral', 'tj-stat-neutral', fmtStatTradeJump(dur?.metric_sources?.crypto_shortest_seconds)), row('Crypto longest', fmtDuration(dur?.crypto_longest_seconds), 'tj-stat-neutral', 'tj-stat-neutral', fmtStatTradeJump(dur?.metric_sources?.crypto_longest_seconds))], title: 'Duration' },
      { key: 'fx', rows: core(byMarket.fx || {}), title: 'FX' },
      { key: 'crypto', rows: core(byMarket.crypto || {}), title: 'Crypto' },
      { key: 'leaders', rows: [row('Overall most wins', fmtLeader(leaders?.most_wins_instrument, 'wins'), 'tj-stat-winner'), row('Overall most losses', fmtLeader(leaders?.most_losses_instrument, 'losses'), 'tj-stat-loser'), row('FX most wins', fmtLeader(leaders?.fx_most_wins_instrument, 'wins'), 'tj-stat-winner'), row('FX most losses', fmtLeader(leaders?.fx_most_losses_instrument, 'losses'), 'tj-stat-loser'), row('Crypto most wins', fmtLeader(leaders?.crypto_most_wins_instrument, 'wins'), 'tj-stat-winner'), row('Crypto most losses', fmtLeader(leaders?.crypto_most_losses_instrument, 'losses'), 'tj-stat-loser')], title: 'Instrument leaders' },
    ].map((section) => ({ ...section, html: sec(section.title, section.rows), weight: section.rows.length + 1 }));

    const pick = (key) => sections.find((section) => section.key === key);
    const columns = [
      { items: [], weight: 0 },
      { items: [], weight: 0 },
      { items: [], weight: 0 },
    ];
    const pushSection = (columnIndex, section) => {
      if (!section) return;
      columns[columnIndex].items.push(section);
      columns[columnIndex].weight += section.weight;
    };

    pushSection(0, pick('overall'));
    pushSection(1, pick('winners'));
    pushSection(2, pick('losers'));

    ['drawdown', 'duration', 'fx', 'crypto', 'leaders'].forEach((key) => {
      const section = pick(key);
      if (!section) return;
      let target = 0;
      for (let i = 1; i < columns.length; i += 1) {
        if (columns[i].weight < columns[target].weight) target = i;
      }
      pushSection(target, section);
    });

    wrap.innerHTML = columns
      .map((column) => `<div class="tj-stats-column">${column.items.map((section) => section.html).join('')}</div>`)
      .join('');

  }

  function jumpToTradeRow(rowId, labelFromLink = '') {
    if (!rowId) return setStatus('No trade row id available for this statistic.');
    const row = (state.rows || []).find((r) => String(r?.id || '') === String(rowId));
    if (!row) {
      state.statTradeFilter = null;
      renderStatTradeFilterButton();
      setStatus('Trade row not found in the current journal data.');
      return;
    }
    state.view = 'trades';
    if (filterInput) filterInput.value = '';
    activeFlags.clear();
    syncFlagButtons();
    state.statTradeFilter = { rowId: String(rowId), label: formatTradeFilterLabel(row, labelFromLink) };
    renderAll();
    requestAnimationFrame(() => {
      const rows = qa('#tj-table tbody tr[data-row-id]');
      const tr = rows.find((el) => String(el.dataset.rowId || '') === String(rowId));
      if (!tr) return setStatus('Trade row not found in the current journal data.');
      tr.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      tr.classList.add('tj-row-highlight');
      window.setTimeout(() => tr.classList.remove('tj-row-highlight'), 2500);
      setStatus(`Showing linked trade only: ${state.statTradeFilter?.label || formatTradeFilterLabel(row, labelFromLink)}.`);
    });
  }

  function renderAll() {
    const filtered = getFilteredRows();
    state.renderedRows = [...filtered];
    renderRows(filtered);
    renderSortIndicators();
    renderInstrumentView(state.stats);
    renderCalendarView(filtered);
    renderEquityView(filtered);
    applyView();
    renderStatTradeFilterButton();
    syncTopScrollbar();
    persistUiState();
  }

  function exportShownTrades() {
    try {
      const rows = Array.isArray(state.renderedRows) ? state.renderedRows : [];
      const headers = TRADE_COLUMNS.map((c) => c.header);
      const csvRows = [headers.map(escapeCsvCell).join(',')];
      rows.forEach((row) => {
        csvRows.push(TRADE_COLUMNS.map((c) => escapeCsvCell(c.value(row))).join(','));
      });
      const now = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      const filename = `trading-journal-ui-export-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.csv`;
      const blob = new Blob(['\uFEFF' + csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus(`Exported ${rows.length} shown trade row(s).`);
    } catch (err) {
      setStatus(`Export failed: ${err?.message || err}`);
    }
  }

  function toggle(flag) {
    if (activeFlags.has(flag)) activeFlags.delete(flag);
    else activeFlags.add(flag);
    syncFlagButtons();
    persistUiState();
    renderAll();
  }

  async function load({ silent = false, skipAutoSync = false, preserveStatus = false, statusOverride = '' } = {}) {
    if (loadInFlight) return { ok: false, error: 'Load already in progress' };
    loadInFlight = true;
    const controller = new AbortController();
    activeAbort = controller;
    const signal = controller.signal;
    const ownsVisibleOverlay = !silent;
    try {
      if (!preserveStatus) setStatus(statusOverride || (silent ? 'Refreshing…' : 'Loading…'));
      if (!silent) {
        const cached = await readCachedPayload();
        if (cached?.journal && cached?.balances && cached?.diagnostics) {
          state.rows = Array.isArray(cached.journal.items) ? cached.journal.items : [];
          state.stats = cached.journal.stats || null;
          state.diagnostics = cached.diagnostics || null;
          renderAll();
          renderBalances(Array.isArray(cached.balances.items) ? cached.balances.items : []);
          await maybeAutoRepairOandaDemo(Array.isArray(cached.balances.items) ? cached.balances.items : []);
          renderStats(state.stats);
          setStatus('Cached data shown, refreshing…');
          hideLoading();
        }
      }
      if (!silent) setLoading(5, 'Loading…');
      if (!silent) setLoading(15, 'Fetching journal…');
      const journalPromise = fetchNamedJson('/api/trading-journal', '/api/trading-journal', { signal });
      const diagnosticsPromise = fetchNamedJson('/api/trading-journal/diagnostics', '/api/trading-journal/diagnostics', { signal });
      const balancesPromise = fetchNamedJson('/api/trading-journal/balances', '/api/trading-journal/balances', { signal });
      let journal = await journalPromise;
      const journalPending = Number(journal?.pending ? 1 : 0) === 1;

      if (!silent) setLoading(80, 'Fetching diagnostics…');
      const diagnostics = await diagnosticsPromise;
      if (!silent) setLoading(88, 'Fetching balances…');
      const balances = await balancesPromise;
      const nextRows = Array.isArray(journal.items) ? journal.items : [];
      const nextStats = journal.stats || null;
      const excelOnly = !!journal?.excel_only;
      if (!state.editorOpen && !state.editorDirty && !state.saveInFlight) {
        if (!journalPending) {
          state.rows = nextRows;
          state.stats = nextStats;
        }
        state.diagnostics = diagnostics || null;
        if (addBtn) addBtn.style.display = excelOnly ? 'none' : '';
      }

      persistUiState();
      if (!silent) setLoading(95, 'Rendering…');
      renderAll();
      renderBalances(Array.isArray(balances.items) ? balances.items : []);
      await maybeAutoRepairOandaDemo(Array.isArray(balances.items) ? balances.items : []);
      const diagErrors = Array.isArray(diagnostics?.errors) ? diagnostics.errors : [];
      diagErrors
        .filter((msg) => String(msg || '').toLowerCase().includes('bybit') && String(msg || '').toLowerCase().includes('balance'))
        .forEach((msg) => {
          const wrap = q('#tj-balances');
          const div = document.createElement('div');
          div.className = 'bal-card muted';
          div.textContent = String(msg);
          wrap.appendChild(div);
        });
      renderStats(state.stats);
      const marketRows = Array.isArray(nextStats?.groups?.market_breakdown) ? nextStats.groups.market_breakdown : [];
      const fxCount = marketRows
        .filter((m) => String(m?.label || '').toLowerCase().includes('fx') || String(m?.label || '').toLowerCase().includes('forex'))
        .reduce((acc, m) => acc + (Number(m?.trades) || 0), 0);
      const actualRowsTotal = Number(journal?.count ?? nextRows.length ?? 0);
      const diagnosticRowsTotal = Number(diagnostics?.rows_total || 0);
      const rowsTotal = Math.max(actualRowsTotal, diagnosticRowsTotal);
      const diagnosticsErrors = Array.isArray(diagnostics?.errors) ? diagnostics.errors : [];
      const hasParseSyncErrors = diagnosticsErrors.some((err) => isParseSyncError(err));
      const hasBybitDemoPurgeNotice = diagnosticsErrors.some((err) => diagnosticsErrorText(err).toLowerCase().includes('bybit demo workbook is blank') || diagnosticsErrorText(err).toLowerCase().includes('old bybit demo rows purged'));
      const hasBalanceAnchorWarnings = diagnosticsErrors.some((err) => isBalanceAnchorWarning(err));
      const workbookSourcesSeen = Number(
        diagnostics?.workbook_sources_seen
        ?? (Number(diagnostics?.local_workbooks_seen || 0) + Number(diagnostics?.dropbox_workbooks_seen || 0))
      );
      const noSources = workbookSourcesSeen === 0;
      const quarantinedRows = Number(diagnostics?.quarantined_rows || 0);
      const lowRowCount = actualRowsTotal < 20 && rowsTotal < 20;
      const workbookFxRows = Number(diagnostics?.rows_by_asset_class?.fx || 0);
      const shouldWarnZeroFx = fxCount === 0 && workbookFxRows > 0;
      if (!state.editorOpen && !state.editorDirty) {
        if (journalPending) {
          if (Array.isArray(state.rows) && state.rows.length > 0) {
            setStatus('Cached browser data shown while journal cache is building. This will refresh automatically.');
          } else {
            setStatus('Journal data is still being prepared. Please refresh shortly.');
          }
          
        } else if (journal?.snapshot_stale) {
          setStatus('Cached journal shown. Refresh to load latest workbook changes.');
        } else if (journal?.warning) {
          setStatus(String(journal.warning));
        } else
        if (hasParseSyncErrors || hasBalanceAnchorWarnings || rowsTotal === 0 || lowRowCount || shouldWarnZeroFx) {
          const reasons = [];
          if (hasParseSyncErrors) reasons.push('parse/import errors');
          if (hasBalanceAnchorWarnings) reasons.push('balance anchor missing');
          if (rowsTotal === 0) reasons.push('no journal rows loaded');
          if (lowRowCount) reasons.push('suspiciously low row count');
          if (shouldWarnZeroFx) reasons.push('zero FX rows');
          if (hasBybitDemoPurgeNotice) reasons.push(BYBIT_DEMO_PURGE_NOTE);
          const dropped = Number(diagnostics?.duplicate_rows_dropped || 0);
          if (!preserveStatus) setStatus(`Warning: Trading Journal diagnostics require attention (${reasons.join(', ')}; rows=${rowsTotal}; duplicates dropped=${dropped}).`);
        } else if (actualRowsTotal > 0 && (quarantinedRows > 0 || Number(diagnostics?.repaired_time_order_rows || 0) > 0)) {
          const repaired = Number(diagnostics?.repaired_time_order_rows || 0);
          const details = Array.isArray(diagnostics?.invalid_time_order_row_details) ? diagnostics.invalid_time_order_row_details : [];
          const currentNonBybitInvalid = details.filter((d) => !String(d?.account_label || d?.source || '').toLowerCase().includes('bybit'));
          if (currentNonBybitInvalid.length > 0) {
            if (!preserveStatus) setStatus(`Info: ${actualRowsTotal} journal rows loaded; diagnostics include ${currentNonBybitInvalid.length} quarantined row(s).`);
          } else {
            if (!preserveStatus) setStatus(`Info: ${actualRowsTotal} journal rows loaded; repaired time-order rows=${repaired}.`);
          }
        } else if (noSources && actualRowsTotal > 0) {
          if (!preserveStatus) setStatus(`Info: ${actualRowsTotal} journal rows loaded; no Excel workbook imports detected.`);
        } else {
          if (!preserveStatus) setStatus(`Updated ${new Date().toLocaleTimeString()}`);
        }
      }
      if (!silent) { setLoading(100, 'Done'); hideLoading(); }
      if (!journalPending) {
        await writeCachedPayload({
          journal,
          diagnostics,
          balances,
          fetched_at: new Date().toISOString(),
        });
      }
      return { ok: true, rowsLoaded: nextRows.length, journal, diagnostics, balances };
    } catch (e) {
      if (isAbortError(e, signal)) {
        if (ownsVisibleOverlay) hideLoading();
        return { ok: false, error: 'Request aborted' };
      }
      console.error(e);
      if (ownsVisibleOverlay) hideLoading();
      const errorMsg = compactErrorMessage(e?.message || e, 'Load failed');
      if (!preserveStatus) setStatus(`Load failed: ${errorMsg}`);
      return { ok: false, error: errorMsg };
    } finally {
      loadInFlight = false;
      if (activeAbort === controller) activeAbort = null;
      scheduleAutoRefresh();
      syncActionButtons();
    }
  }

  function stopAutoRefresh() {
    if (autoRefreshTimer) {
      clearTimeout(autoRefreshTimer);
      autoRefreshTimer = null;
    }
  }

  function scheduleAutoRefresh() {
    stopAutoRefresh();
    if (document.hidden || state.editorOpen || state.editorDirty || state.saveInFlight) return;
    autoRefreshTimer = setTimeout(() => load({ silent: true }), AUTO_REFRESH_MS);
  }

  // When the tab is hidden we stop the timer.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopAutoRefresh();
      if (activeAbort) { try { activeAbort.abort(); } catch {} }
      return;
    }
    scheduleAutoRefresh();
  });

  // Also clean up on navigation away so no refresh request survives route changes.
  window.addEventListener('pagehide', () => {
    stopAutoRefresh();
    if (activeAbort) { try { activeAbort.abort(); } catch {} }
  });

  q('#tj-filter-btn')?.addEventListener('click', () => { state.statTradeFilter = null; persistUiState(); renderAll(); });
  q('#tj-view-trades-btn')?.addEventListener('click', () => { state.view = 'trades'; applyView(); });
  q('#tj-view-inst-btn')?.addEventListener('click', () => { state.view = 'instrument'; applyView(); });
  q('#tj-view-cal-btn')?.addEventListener('click', () => { state.view = 'calendar'; applyView(); renderCalendarView(getFilteredRows()); });
  q('#tj-view-equity-btn')?.addEventListener('click', () => { state.view = 'equity'; applyView(); renderEquityView(getFilteredRows()); });
  q('#tj-cal-prev')?.addEventListener('click', () => { state.calMonth = _monthShift(state.calMonth, -1); persistUiState(); renderCalendarView(getFilteredRows()); });
  q('#tj-cal-next')?.addEventListener('click', () => { state.calMonth = _monthShift(state.calMonth, 1); persistUiState(); renderCalendarView(getFilteredRows()); });
  q('#tj-clear-btn')?.addEventListener('click', () => { clearStatTradeFilter({ clearAllFilters: true }); });
  q('#tj-export-btn')?.addEventListener('click', exportShownTrades);
  statTradeFilterBtn?.addEventListener('click', (e) => { e.preventDefault(); clearStatTradeFilter({ clearAllFilters: true }); });
  addBtn?.addEventListener('click', () => {
    openEditor(null);
    stopAutoRefresh();
  });
    editorCancelBtn?.addEventListener('click', () => {
    closeEditor();
    scheduleAutoRefresh();
  });
  editorForm?.addEventListener('input', () => {
    if (!state.editorOpen) return;
    state.editorDirty = true;
    syncActionButtons();
  });
  editorForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (state.saveInFlight) return;
    state.saveInFlight = true;
    syncActionButtons();
    setEditorError('');
    try {
      const payload = collectEditorPayload();
      const isCreate = state.editingIsCreate;
      const url = isCreate ? '/api/trading-journal/rows' : `/api/trading-journal/rows/${encodeURIComponent(state.editingRowId || '')}`;
      const method = isCreate ? 'POST' : 'PATCH';
      await fetchJson(url, {
        method,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setStatus(isCreate ? 'Trade added.' : 'Trade updated.');
      closeEditor();
      await load({ silent: true, skipAutoSync: true });
    } catch (err) {
      const msg = err?.message || String(err || 'Save failed');
      setEditorError(msg);
      setStatus(`Save failed: ${msg}`);
      state.saveInFlight = false;
      state.editorOpen = true;
      state.editorDirty = true;
      syncActionButtons();
    }
  });

  qa('#tj-table thead th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (!key) return;
      if (state.sortKey === key) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
      else { state.sortKey = key; state.sortDir = 'asc'; }
      persistUiState();
      renderAll();
    });
  });


  qa('#tj-inst-table thead th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (!key) return;
      if (state.instSortKey === key) state.instSortDir = state.instSortDir === 'asc' ? 'desc' : 'asc';
      else { state.instSortKey = key; state.instSortDir = 'desc'; }
      persistUiState();
      renderAll();
    });
  });

  document.addEventListener('click', (e) => {
    const jumpEl = e.target?.closest ? e.target.closest('[data-jump-row-id]') : null;
    if (jumpEl) { e.preventDefault(); jumpToTradeRow(jumpEl.dataset.jumpRowId || '', jumpEl.dataset.jumpRowLabel || ''); return; }
    const btn = e.target?.closest ? e.target.closest('button[data-flag]') : null;
    if (!btn) return;
    const flag = btn.dataset.flag;
    if (!flag) return;
    state.statTradeFilter = null;
    toggle(flag);
    btn.classList.toggle('active', activeFlags.has(flag));
  });
  document.addEventListener('click', async (e) => {
    const actionEl = e.target?.closest ? e.target.closest('[data-action][data-row-id]') : null;
    if (!actionEl) return;
    const rowId = actionEl.dataset.rowId || '';
    const action = actionEl.dataset.action || '';
    const row = (state.rows || []).find((r) => String(r?.id || '') === rowId);
    if (!row) {
      setStatus('Row not found.');
      return;
    }
    if (action === 'edit') {
      openEditor(row);
      stopAutoRefresh();
      return;
    }
    if (action === 'delete') {
      if (!window.confirm('Delete this manual trade?')) return;
      try {
        state.saveInFlight = true;
        syncActionButtons();
        await fetchJson(`/api/trading-journal/rows/${encodeURIComponent(rowId)}`, { method: 'DELETE' });
        setStatus('Trade deleted.');
        await load({ silent: true, skipAutoSync: true });
      } catch (err) {
        setStatus(`Delete failed: ${err?.message || err}`);
      } finally {
        state.saveInFlight = false;
        syncActionButtons();
      }
    }
  });

  filterInput?.addEventListener('input', persistUiState);
  filterInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') renderAll(); });
  syncActionButtons();

  const handleSyncResultDiagnostics = (syncResult) => {
    if (syncResult?.ok === false) {
      const snapshotError = compactErrorMessage(syncResult?.error || 'Sync failed', 'Sync failed');
      setStatus(`Load failed: ${snapshotError}`);
      return;
    }
  };

  syncFlagButtons();
  applyView();
  load().then(handleSyncResultDiagnostics);
  scheduleAutoRefresh();
})();
