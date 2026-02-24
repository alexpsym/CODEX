(() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const tbody = q('#tj-table tbody');
  const empty = q('#tj-empty');
  const status = q('#tj-status');
  const filterInput = q('#tj-filter');

  let activeFlags = new Set();
  let autoRefreshTimer = null;

  function normYes(v) {
    return ['yes', 'y', 'true', '1'].includes(String(v ?? '').trim().toLowerCase());
  }
  let state = {
    rows: [],
    sortKey: 'close_time',
    sortDir: localStorage.getItem('tj.sortDir') || 'desc',
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

  const setStatus = (msg) => { status.textContent = msg || ''; };

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, { cache: 'no-store', ...options });
    const text = await res.text();
    if (!res.ok) throw new Error(`${res.status} ${text}`);
    try { return JSON.parse(text); } catch { return {}; }
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
      localStorage.setItem('tj.flags', JSON.stringify(Array.from(activeFlags)));
      localStorage.setItem('tj.view', state.view || 'trades');
    } catch {}
  }

  try {
    state.sortKey = localStorage.getItem('tj.sortKey') || state.sortKey;
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

  function distanceLabel(item, kind) {
    const isFx = (item?.asset_class || '').toLowerCase() === 'fx';
    const val = isFx ? (kind === 'sl' ? item.avg_sl_distance_pips : item.avg_tp_distance_pips)
                     : (kind === 'sl' ? item.avg_sl_distance_quote : item.avg_tp_distance_quote);
    const suffix = isFx ? ' pips' : ` ${item.quote_currency || 'quote'}`;
    return Number.isFinite(Number(val)) ? `${fmtNum(val, isFx ? 1 : 6)}${suffix}` : '—';
  }

  function renderInstrumentView(stats) {
    const body = q('#tj-inst-table tbody');
    const emptyInst = q('#tj-inst-empty');
    if (!body) return;
    body.innerHTML = '';
    const list = Array.isArray(stats?.by_instrument) ? stats.by_instrument : [];
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
        <td style="text-align:right">${distanceLabel(item,'sl')}</td>
        <td style="text-align:right">${distanceLabel(item,'tp')}</td>`;
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
      const pnl = Number(r.net_profit ?? r.realized_pnl);
      const bal = Number(r.balance_after_trade);
      const ccy = r.balance_after_trade_currency || r.currency || '';
      const tr = document.createElement('tr');
      tr.innerHTML = `
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
        <div style="font-size:1.1rem;font-weight:600">${fmtNum(b.balance, 2)} ${b.currency || ''}</div>
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
      ['Most wins instrument', stats?.instrument_with_most_wins?.symbol || '—'],
      ['Most losses instrument', stats?.instrument_with_most_losses?.symbol || '—'],
    ];
    cards.forEach(([label, value]) => {
      const div = document.createElement('div');
      div.className = 'bal-card';
      div.innerHTML = `<div class="muted">${label}</div><div style="font-size:1.05rem;font-weight:600">${typeof value === 'number' ? fmtNum(value, 6) : (value ?? '—')}</div>`;
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

  async function load() {
    try {
      setStatus('Loading…');
      const filter = (filterInput.value || '').trim();

      let journal = await fetchJson(`/api/trading-journal${filter ? `?filter=${encodeURIComponent(filter)}` : ''}`);
      if ((!journal.items || !journal.items.length) && !filter) {
        await fetchJson('/api/trading-journal/sync', { method: 'POST' });
        journal = await fetchJson('/api/trading-journal');
      }

      const balances = await fetchJson('/api/trading-journal/balances');
      state.rows = Array.isArray(journal.items) ? journal.items : [];
      state.stats = journal.stats || null;

      persistUiState();
      renderAll();
      renderBalances(Array.isArray(balances.items) ? balances.items : []);
      renderStats(state.stats);
      setStatus(`Updated ${new Date().toLocaleTimeString()}`);
    } catch (e) {
      console.error(e);
      setStatus(`Load failed: ${e.message}`);
    }
  }

  q('#tj-filter-btn')?.addEventListener('click', () => { persistUiState(); load(); });
  q('#tj-view-trades-btn')?.addEventListener('click', () => { state.view = 'trades'; applyView(); });
  q('#tj-view-inst-btn')?.addEventListener('click', () => { state.view = 'instrument'; applyView(); });
  q('#tj-clear-btn')?.addEventListener('click', () => { filterInput.value = ''; persistUiState(); load(); });
  q('#tj-sync-btn')?.addEventListener('click', async () => {
    try {
      setStatus('Syncing…');
      await fetchJson('/api/trading-journal/sync', { method: 'POST' });
      await load();
    } catch (e) {
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

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-flag]');
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
  autoRefreshTimer = setInterval(load, 15000);
})();
