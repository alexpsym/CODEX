(() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const tbody = q('#tj-table tbody');
  const empty = q('#tj-empty');
  const status = q('#tj-status');
  const filterInput = q('#tj-filter');

  let activeFlags = new Set();
  let state = {
    rows: [],
    sortKey: 'close_time',
    sortDir: 'desc',
    stats: null,
  };

  const fmtNum = (v, d = 2) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: d }) : '—';
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
    let out = rows;
    if (activeFlags.has('errors')) out = out.filter((r) => (r.metrics && r.metrics.error) || r.entry_comments === 'Error');
    if (activeFlags.has('breakeven')) out = out.filter((r) => String(r.breakeven || '').toLowerCase() === 'yes');
    if (activeFlags.has('held_news')) out = out.filter((r) => String(r.metrics?.held_through_news || '').toLowerCase() === 'yes');
    if (activeFlags.has('spiked_out')) out = out.filter((r) => String(r.metrics?.spiked_out || '').toLowerCase() === 'yes');
    if (activeFlags.has('early_close')) out = out.filter((r) => String(r.metrics?.early_close || '').toLowerCase() === 'yes');
    return out;
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
        <td>${fmtNum(r.qty, 6)}${r.qty_unit === 'lots' ? ' lot' : ''}</td>
        <td>${fmtNum(r.entry_price, 6)}</td>
        <td>${fmtNum(r.exit_price, 6)}</td>
        <td>${fmtNum(r.stop_loss, 6)}</td>
        <td>${fmtNum(r.take_profit, 6)}</td>
        <td>${fmtNum(r.commission ?? r.fees, 4)}</td>
        <td class="num ${Number.isFinite(pnl) ? (pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '')) : ''}">${fmtNum(pnl, 4)} ${r.realized_pnl_currency || r.currency || ''}</td>
        <td>${Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '—'}</td>
        <td>${r.breakeven || '—'}</td>
        <td>${r.status || '—'}</td>
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
      ['Avg stop loss', stats?.totals?.avg_stop_loss],
      ['Avg target', stats?.totals?.avg_take_profit],
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
  }

  function toggle(flag) {
    if (activeFlags.has(flag)) activeFlags.delete(flag);
    else activeFlags.add(flag);
    qa('.tj-chip[data-flag]').forEach((btn) => {
      const on = activeFlags.has(btn.dataset.flag || '');
      btn.style.opacity = on ? '1' : '0.7';
      btn.style.outline = on ? '1px solid #60a5fa' : 'none';
    });
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

      renderAll();
      renderBalances(Array.isArray(balances.items) ? balances.items : []);
      renderStats(state.stats);
      setStatus(`Updated ${new Date().toLocaleTimeString()}`);
    } catch (e) {
      console.error(e);
      setStatus(`Load failed: ${e.message}`);
    }
  }

  q('#tj-filter-btn')?.addEventListener('click', load);
  q('#tj-clear-btn')?.addEventListener('click', () => { filterInput.value = ''; load(); });
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
      renderAll();
    });
  });

  q('#btn-errors')?.addEventListener('click', () => toggle('errors'));
  q('#btn-breakeven')?.addEventListener('click', () => toggle('breakeven'));
  q('#btn-held-news')?.addEventListener('click', () => toggle('held_news'));
  q('#btn-spiked-out')?.addEventListener('click', () => toggle('spiked_out'));
  q('#btn-early-close')?.addEventListener('click', () => toggle('early_close'));

  filterInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(); });

  load();
  setInterval(load, 15000);
})();
