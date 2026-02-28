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
  // Auto-refresh cadence increased from the old 15s interval to 60s scheduled refreshes.
  const AUTO_REFRESH_MS = 60000;
  let loadInFlight = false;
  let activeAbort = null;
  let syncWatchTimer = null;

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
  };
  try { filterInput.value = localStorage.getItem('tj.filter') || ''; } catch {}

  const fmtNum = (v, d = 2) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) > 0 && Math.abs(n) < Math.pow(10, -Math.max(2, d)) && Math.abs(n) < 1e-4) {
      return n.toPrecision(8);
    }
    return n.toLocaleString(undefined, { maximumFractionDigits: d });
  };
  const fmtQty = (v, row) => {
    const n = Number(v);
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
    const n = Number(seconds);
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

  const setStatus = (msg) => { status.textContent = msg || ''; };

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, { cache: 'no-store', ...options });
    const text = await res.text();
    if (!res.ok) throw new Error(`${res.status} ${text}`);
    try { return JSON.parse(text); } catch { return {}; }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function waitForSync(signal) {
    while (true) {
      const st = await fetchJson('/api/trading-journal/sync/status', { signal });
      const p = Number(st?.progress);
      const msg = st?.message || 'Syncing…';
      setLoading(Number.isFinite(p) ? p : 20, msg);
      if (!st?.running) {
        if (st?.ok === false) throw new Error(st?.error || st?.message || 'Sync failed');
        return st;
      }
      await sleep(500);
    }
  }

  function watchSyncCompletion() {
    if (syncWatchTimer) return;
    const poll = async () => {
      try {
        const st = await fetchJson('/api/trading-journal/sync/status');
        if (st?.running) {
          syncWatchTimer = setTimeout(poll, 900);
          return;
        }
        syncWatchTimer = null;
        if (st?.ok === false) {
          setStatus(`Background sync failed: ${st?.error || st?.message || 'unknown error'}`);
          return;
        }
        localStorage.setItem('tj_last_auto_sync_ms', String(Date.now()));
        await load({ silent: true, skipAutoSync: true });
      } catch (e) {
        syncWatchTimer = setTimeout(poll, 1500);
      }
    };
    syncWatchTimer = setTimeout(poll, 800);
  }

  async function triggerBackgroundSync() {
    try {
      const st = await fetchJson('/api/trading-journal/sync/status');
      if (!st?.running) {
        await fetchJson('/api/trading-journal/sync', { method: 'POST' });
      }
      setStatus('Background Dropbox sync running…');
      watchSyncCompletion();
    } catch (e) {
      console.warn('Background sync skipped:', e);
    }
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
    } catch {}
  }

  try {
    state.sortKey = localStorage.getItem('tj.sortKey') || state.sortKey;
    state.sortDir = localStorage.getItem('tj.sortDir') || state.sortDir;
    state.instSortKey = localStorage.getItem('tj.instSortKey') || state.instSortKey;
    state.instSortDir = localStorage.getItem('tj.instSortDir') || state.instSortDir;
    const savedFlags = JSON.parse(localStorage.getItem('tj.flags') || '[]');
    if (Array.isArray(savedFlags)) savedFlags.forEach((f) => activeFlags.add(String(f)));
  } catch {}

  function fmtProfitPct(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return `${fmtNum(n, 4)}%`;
  }

  function fmtR(v) {
    const n = Number(v);
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
      if (key === 'wins') return Number.isFinite(Number(item?.wins)) ? Number(item.wins) : null;
      if (key === 'losses') return Number.isFinite(Number(item?.losses)) ? Number(item.losses) : null;
      if (key === 'break_even') return Number.isFinite(Number(item?.break_even)) ? Number(item.break_even) : null;
      if (key === 'avg_sl_w') return pickNum(item, 'avg_sl_distance_pips_wins', 'avg_sl_distance_quote_wins');
      if (key === 'avg_sl_l') return pickNum(item, 'avg_sl_distance_pips_losses', 'avg_sl_distance_quote_losses');
      if (key === 'avg_tp_w') return pickNum(item, 'avg_tp_distance_pips_wins', 'avg_tp_distance_quote_wins');
      if (key === 'avg_tp_l') return pickNum(item, 'avg_tp_distance_pips_losses', 'avg_tp_distance_quote_losses');
      if (key === 'avg_duration') return Number.isFinite(Number(item?.avg_trade_duration_seconds)) ? Number(item.avg_trade_duration_seconds) : null;
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
      tr.innerHTML = `
        <td>${item.symbol || '—'}</td>
        <td>${item.asset_class || '—'}</td>
        <td style="text-align:right">${fmtNum(item.total_trades,0)}</td>
        <td style="text-align:right">${fmtNum(item.wins,0)}</td>
        <td style="text-align:right">${fmtNum(item.losses,0)}</td>
        <td style="text-align:right">${fmtNum(item.break_even,0)}</td>
        <td style="text-align:right">${distanceLabel(item,'sl','wins')}</td>
        <td style="text-align:right">${distanceLabel(item,'sl','losses')}</td>
        <td style="text-align:right">${distanceLabel(item,'tp','wins')}</td>
        <td style="text-align:right">${distanceLabel(item,'tp','losses')}</td>
        <td style="text-align:right">${fmtDuration(item.avg_trade_duration_seconds)}</td>`;
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
    const topScroll = q('#tj-top-scroll');
    const tradesBtn = q('#tj-view-trades-btn');
    const instBtn = q('#tj-view-inst-btn');
    const showInst = state.view === 'instrument';
    tradesWrap?.classList.toggle('hidden', showInst);
    topScroll?.classList.toggle('hidden', showInst);
    instWrap?.classList.toggle('hidden', !showInst);
    [tradesBtn, instBtn].forEach((b) => { if (b) { b.style.outline='none'; b.style.opacity='0.8'; }});
    if (showInst) { if (instBtn) { instBtn.style.outline='1px solid #60a5fa'; instBtn.style.opacity='1'; } }
    else { if (tradesBtn) { tradesBtn.style.outline='1px solid #60a5fa'; tradesBtn.style.opacity='1'; } }
    persistUiState();
  }

  function sortRows(rows) {
    const out = [...rows];
    const { sortKey, sortDir } = state;
    const dir = sortDir === 'asc' ? 1 : -1;
    out.sort((a, b) => {
      let av = a?.[sortKey], bv = b?.[sortKey];
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

  function renderRows(rows) {
    const sorted = sortRows(rows);
    tbody.innerHTML = '';
    if (!sorted.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    for (const r of sorted) {
      const rowType = String(r.row_type || 'trade').toLowerCase();
      const isCashflow = rowType === 'cashflow';
      const pnl = Number(r.net_profit ?? r.realized_pnl);
      const bal = Number(r.balance_after_trade ?? r.cashflow_new_balance);
      const ccy = r.balance_after_trade_currency || r.currency || '';
      const tr = document.createElement('tr');

      if (isCashflow) {
        const amt = Number(r.cashflow_amount);
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
          <td>${Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '—'}</td>
          <td>—</td>
          <td>—</td>
        `;
        tbody.appendChild(tr);
        continue;
      }

      tr.innerHTML = `
        <td>${fmtTime(r.open_time)}</td>
        <td>${fmtTime(r.close_time || r.open_time)}</td>
        <td>${r.account_label || r.account || '—'}</td>
        <td title="${r.symbol_raw || r.symbol || ''}">${r.symbol || '—'}</td>
        <td>${r.side || '—'}</td>
        <td>${r.setup || '—'}</td>
        <td>${fmtQty(r.qty, r)}${r.qty_unit === 'lots' ? ' lot' : ''}</td>
        <td>${fmtNum(r.entry_price, 6)}</td>
        <td>${fmtNum(r.exit_price, 6)}</td>
        <td>${fmtNum(r.stop_loss, 6)}</td>
        <td>${fmtNum(r.take_profit, 6)}</td>
        <td>${fmtNum(r.commission ?? r.fees, 4)} ${r.commission_currency || r.fee_currency || ''}</td>
        <td class="num ${Number.isFinite(pnl) ? (pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '')) : ''}">${fmtNum(pnl, 4)} ${r.realized_pnl_currency || r.currency || ''}</td>
        <td class="num ${Number(r.profit_pct) > 0 ? 'pos' : (Number(r.profit_pct) < 0 ? 'neg' : '')}">${fmtProfitPct(r.profit_pct)}</td>
        <td class="num ${Number(r.r_multiple) > 0 ? 'pos' : (Number(r.r_multiple) < 0 ? 'neg' : '')}">${fmtR(r.r_multiple)}</td>
        <td>${Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '—'}</td>
        <td>${fmtDuration(r.trade_duration_seconds)}</td>
        <td>${r.breakeven || '—'}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  function renderBalances(items) {
    const wrap = q('#tj-balances');
    wrap.innerHTML = '';
    (items || []).forEach((b) => {
      const div = document.createElement('div');
      div.className = 'bal-card';
      div.innerHTML = `
        <div class="muted">${b.label || b.account || 'Account'}</div>
        <div style="font-size:1.0rem;font-weight:600">${fmtNum(b.balance, (() => { const c = String(b.currency || '').toUpperCase(); if (c === 'AUD' || c === 'USD') return 2; if (c === 'USDT') return 8; return 6; })())} ${b.currency || ''}</div>
        ${b.missing_balance ? `<div class="muted">Balance not found in workbook</div>` : ''}
      `;
      wrap.appendChild(div);
    });
  }

  function renderStats(stats) {
    const wrap = q('#tj-stats');
    if (!wrap) return;
    wrap.innerHTML = '';
    if (!stats) return;

    const cards = [
      ['Total trades', stats?.totals?.trades],
      ['Wins', stats?.totals?.wins],
      ['Losses', stats?.totals?.losses],
      ['Break even', stats?.totals?.break_even],
      ['Unique instruments', stats?.totals?.unique_instruments],
      ['Crypto instruments', stats?.totals?.crypto_instruments],
      ['Forex instruments', stats?.totals?.fx_instruments],
      ['Avg stop loss', stats?.totals?.avg_stop_loss],
      ['Avg target', stats?.totals?.avg_take_profit],
      ['Avg profit %', stats?.totals?.avg_profit_pct],
      ['Avg R', stats?.totals?.avg_r_multiple],
      ['Avg FX duration', fmtDuration(stats?.totals?.avg_fx_duration_seconds)],
      ['Avg crypto duration', fmtDuration(stats?.totals?.avg_crypto_duration_seconds)],
      ['Most wins instrument', stats?.instrument_with_most_wins?.symbol || '—'],
      ['Most losses instrument', stats?.instrument_with_most_losses?.symbol || '—'],
    ];
    cards.forEach(([label, value]) => {
      const div = document.createElement('div');
      div.className = 'bal-card';
      div.innerHTML = `<div class="muted">${label}</div><div style="font-size:0.95rem;font-weight:600">${typeof value === 'number' ? fmtNum(value, 6) : (value ?? '—')}</div>`;
      wrap.appendChild(div);
    });

  }

  function renderAll() {
    const filtered = applyFlagFilters(state.rows);
    renderRows(filtered);
    renderSortIndicators();
    renderInstrumentView(state.stats);
    applyView();
    syncTopScrollbar();
    persistUiState();
  }

  function toggle(flag) {
    if (activeFlags.has(flag)) activeFlags.delete(flag);
    else activeFlags.add(flag);
    qa('.tj-chip[data-flag]').forEach((btn) => {
      const on = activeFlags.has(btn.dataset.flag || '');
      btn.style.opacity = on ? '1' : '0.7';
      btn.style.outline = on ? '1px solid #60a5fa' : 'none';
    });
    persistUiState();
    renderAll();
  }

  async function load({ silent = false, skipAutoSync = false } = {}) {
    if (loadInFlight) return;
    loadInFlight = true;
    // Cancel any prior request chain so rapid manual actions cannot overlap with refresh work.
    if (activeAbort) { try { activeAbort.abort(); } catch {} }
    activeAbort = new AbortController();
    const signal = activeAbort.signal;
    try {
      setStatus(silent ? 'Refreshing…' : 'Loading…');
      if (!silent) setLoading(5, 'Loading…');
      const filter = (filterInput.value || '').trim();

      if (!silent) setLoading(15, 'Fetching journal…');
      let journal = await fetchJson(`/api/trading-journal${filter ? `?filter=${encodeURIComponent(filter)}` : ''}`, { signal });

      // Auto-sync from Dropbox on load (throttled) so Excel workbooks are picked up even when
      // live webhook trades already exist. This runs in the background and does not block UI load.
      if (!silent && !skipAutoSync) {
        try {
          const st = await fetchJson('/api/trading-journal/sync/status', { signal });
          const lastFinished = new Date(st?.finished_at || 0).getTime() || 0;
          const localLast = Number(localStorage.getItem('tj_last_auto_sync_ms') || 0) || 0;
          const now = Date.now();
          const minMs = 5 * 60 * 1000;
          const anchor = Math.max(lastFinished, localLast);
          if (!st?.running && (now - anchor > minMs)) {
            triggerBackgroundSync();
          }
        } catch (e) {
          // Ignore auto-sync errors; manual Sync now remains available.
          console.warn('Auto-sync skipped:', e);
        }
      }

      const hasItems = Array.isArray(journal?.items) && journal.items.length > 0;
      if (!hasItems && !silent && !skipAutoSync) {
        // Empty journal should still render immediately; sync in background and refresh when ready.
        triggerBackgroundSync();
      }

      if (!silent) setLoading(85, 'Fetching balances…');
      const balances = await fetchJson('/api/trading-journal/balances', { signal });
      state.rows = Array.isArray(journal.items) ? journal.items : [];
      state.stats = journal.stats || null;

      persistUiState();
      if (!silent) setLoading(95, 'Rendering…');
      renderAll();
      renderBalances(Array.isArray(balances.items) ? balances.items : []);
      renderStats(state.stats);
      setStatus(`Updated ${new Date().toLocaleTimeString()}`);
      if (!silent) { setLoading(100, 'Done'); hideLoading(); }
    } catch (e) {
      if (e && (e.name === 'AbortError' || e.code === 20)) return;
      console.error(e);
      if (!silent) hideLoading();
      setStatus(`Load failed: ${e.message}`);
    } finally {
      loadInFlight = false;
      scheduleAutoRefresh();
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
    if (document.hidden) return;
    autoRefreshTimer = setTimeout(() => load({ silent: true }), AUTO_REFRESH_MS);
  }

  // When the tab is hidden we stop the timer and abort active network work to reduce backend pressure.
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

  q('#tj-filter-btn')?.addEventListener('click', () => { persistUiState(); load(); });
  q('#tj-view-trades-btn')?.addEventListener('click', () => { state.view = 'trades'; applyView(); });
  q('#tj-view-inst-btn')?.addEventListener('click', () => { state.view = 'instrument'; applyView(); });
  q('#tj-clear-btn')?.addEventListener('click', () => { filterInput.value = ''; persistUiState(); load(); });
  q('#tj-sync-btn')?.addEventListener('click', async () => {
    try {
      setStatus('Syncing…');
      setLoading(10, 'Syncing from Dropbox…');
      await fetchJson('/api/trading-journal/sync', { method: 'POST' });
      await waitForSync();
      await load();
    } catch (e) {
      hideLoading();
      setStatus(`Sync failed: ${e.message}`);
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
    const btn = e.target?.closest ? e.target.closest('button[data-flag]') : null;
    if (!btn) return;
    const flag = btn.dataset.flag;
    if (!flag) return;
    toggle(flag);
    btn.classList.toggle('active', activeFlags.has(flag));
  });

  filterInput?.addEventListener('input', persistUiState);
  filterInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(); });

  qa('.tj-chip[data-flag]').forEach((btn) => { const on = activeFlags.has(btn.dataset.flag || ''); btn.classList.toggle('active', on); btn.style.opacity = on ? '1' : '0.7'; btn.style.outline = on ? '1px solid #60a5fa' : 'none'; });
  applyView();
  load();
  scheduleAutoRefresh();
})();
