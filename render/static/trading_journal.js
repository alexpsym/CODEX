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

  function metricChips(metrics) {
    if (!metrics || typeof metrics !== 'object') return '—';
    return Object.entries(metrics)
      .filter(([, v]) => `${v || ''}`.trim())
      .slice(0, 10)
      .map(([k, v]) => `<span class="pill" style="margin-right:4px;">${k}: ${v}</span>`)
      .join(' ');
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
      const pnl = Number(r.net_profit ?? r.realized_pnl);
      tr.innerHTML = `
        <td>${fmtTime(r.close_time || r.open_time)}</td>
        <td>${r.account_label || r.account || '—'}</td>
        <td>${r.symbol || '—'}</td>
        <td>${r.side || '—'}</td>
        <td>${r.setup || '—'}</td>
        <td>${fmtNum(r.qty, 6)}</td>
        <td>${fmtNum(r.entry_price, 6)}</td>
        <td>${fmtNum(r.exit_price, 6)}</td>
        <td>${fmtNum(r.commission ?? r.fees, 4)}</td>
        <td class="num ${Number.isFinite(pnl) ? (pnl >= 0 ? 'pos' : 'neg') : ''}">${fmtNum(pnl, 4)}</td>
        <td>${r.breakeven || '—'}</td>
        <td>${r.status || '—'}</td>
      `;
      tbody.appendChild(tr);

      const detail = document.createElement('tr');
      detail.style.display = 'none';
      detail.innerHTML = `
        <td colspan="12" style="white-space:normal;">
          <div class="muted" style="margin-bottom:6px;"><strong>Comments:</strong> ${r.notes || ''} ${r.pre_trade_comments || ''} ${r.entry_comments || ''} ${r.trade_management || ''} ${r.exit_comments || ''}</div>
          <div style="margin-bottom:6px;"><strong>SL/TP:</strong> ${fmtNum(r.stop_loss, 6)} / ${fmtNum(r.take_profit, 6)} &nbsp; <strong>Swap:</strong> ${fmtNum(r.swap, 4)} &nbsp; <strong>High/Low:</strong> ${fmtNum(r.highest_price, 6)} / ${fmtNum(r.lowest_price, 6)}</div>
          <div><strong>Tags:</strong> ${metricChips(r.metrics)}</div>
        </td>
      `;
      tbody.appendChild(detail);
      tr.addEventListener('click', () => {
        detail.style.display = detail.style.display === 'none' ? '' : 'none';
      });
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

  document.querySelectorAll('.tj-chip').forEach((btn) => {
    btn.addEventListener('click', () => {
      filterInput.value = btn.getAttribute('data-q') || '';
      load();
    });
  });

  load();
  setInterval(load, 15000);
})();
