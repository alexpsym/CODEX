(() => {
  const q = (s) => document.querySelector(s);
  const tbody = q('#tj-table tbody');
  const empty = q('#tj-empty');
  const status = q('#tj-status');
  const filterInput = q('#tj-filter');

  const fmtNum = (v, d = 2) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: d }) : '—';
  };

  const fmtTime = (v) => {
    if (!v) return '—';
    const d = new Date(v);
    return Number.isNaN(d.getTime())
      ? String(v)
      : d.toLocaleString('en-AU', { timeZone: 'Australia/Brisbane' });
  };

  const setStatus = (msg) => { status.textContent = msg || ''; };

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, { cache: 'no-store', ...options });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  }

  function renderRows(rows) {
    tbody.innerHTML = '';
    if (!rows.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    for (const r of rows) {
      const tr = document.createElement('tr');
      const pnl = Number(r.realized_pnl);
      tr.innerHTML = `
        <td>${fmtTime(r.close_time || r.open_time)}</td>
        <td><span class="pill">${r.source || '—'}</span></td>
        <td>${r.account_label || r.account || '—'}</td>
        <td>${r.symbol || '—'}</td>
        <td>${r.side || '—'}</td>
        <td>${fmtNum(r.qty, 6)}</td>
        <td>${fmtNum(r.entry_price, 6)}</td>
        <td>${fmtNum(r.exit_price, 6)}</td>
        <td>${fmtNum(r.notional_usd, 2)}</td>
        <td>${fmtNum(r.fees, 4)} ${r.fee_currency || ''}</td>
        <td class="num ${Number.isFinite(pnl) ? (pnl >= 0 ? 'pos' : 'neg') : ''}">${fmtNum(r.realized_pnl, 4)} ${r.realized_pnl_currency || ''}</td>
        <td>${r.status || '—'}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  function renderBalances(items) {
    const wrap = q('#tj-balances');
    wrap.innerHTML = '';
    for (const b of items || []) {
      const div = document.createElement('div');
      div.className = 'bal-card';
      div.innerHTML = `
        <div class="muted">${b.label || b.account || 'Account'}</div>
        <div style="font-size:1.1rem;font-weight:600">${fmtNum(b.balance, 2)} ${b.currency || ''}</div>
        ${b.nav != null ? `<div class="muted">NAV: ${fmtNum(b.nav, 2)} ${b.currency || ''}</div>` : ''}
      `;
      wrap.appendChild(div);
    }
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

      renderRows(Array.isArray(journal.items) ? journal.items : []);
      renderBalances(Array.isArray(balances.items) ? balances.items : []);
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
  filterInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(); });

  load();
  setInterval(load, 15000);
})();
