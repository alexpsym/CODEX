(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const statusBadge = document.getElementById('open-orders-status');
  const table = document.getElementById('open-orders-table');
  const tbody = table?.querySelector('tbody');
  const emptyState = document.getElementById('open-orders-empty');
  const errorsBox = document.getElementById('open-orders-errors');
  const errorsList = errorsBox?.querySelector('ul');
  const POLL_MS = 10_000;
  const HIDDEN_MULTIPLIER = 3;
  let refreshInFlight = null;
  let pollTimer = null;
  let hasData = false;

  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const formatTimestamp = (value) => {
    if (!value) return '—';
    const n = Number(value);
    if (!Number.isNaN(n)) {
      const ms = n < 1_000_000_000_000 ? n * 1000 : n;
      const d = new Date(ms);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
  };

  const setBadge = (message) => { if (statusBadge) statusBadge.textContent = message; };

  const fetchJson = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  };

  const render = (items, errors = []) => {
    if (!tbody) return;
    tbody.innerHTML = '';
    hasData = Boolean(items.length);
    if (errorsBox) errorsBox.style.display = errors.length ? 'block' : 'none';
    if (errorsList) {
      errorsList.innerHTML = '';
      errors.forEach((entry) => {
        const li = document.createElement('li');
        li.textContent = entry.message || 'Unknown error';
        errorsList.appendChild(li);
      });
    }

    if (!items.length) {
      if (emptyState) emptyState.style.display = 'block';
      return;
    }
    if (emptyState) emptyState.style.display = 'none';

    items.forEach((item, idx) => {
      const row = document.createElement('tr');
      const children = Array.isArray(item.children) ? item.children : [];
      const expTd = document.createElement('td');
      if (children.length) {
        const exp = document.createElement('button');
        exp.className = 'action-btn';
        exp.textContent = '▸';
        exp.onclick = () => {
          const open = exp.textContent === '▾';
          exp.textContent = open ? '▸' : '▾';
          document.querySelectorAll(`tr[data-parent="${idx}"]`).forEach((r) => r.style.display = open ? 'none' : '');
        };
        expTd.appendChild(exp);
      } else expTd.textContent = '—';
      row.appendChild(expTd);

      [item.broker, item.account, item.category, item.instrument, item.type, item.side, item.size, item.entry_price || item.order_price, item.current_price, item.stop_loss, item.take_profit, item.leverage, formatTimestamp(item.opened_at), item.status].forEach((v) => {
        const td = document.createElement('td');
        td.textContent = fmt(v);
        row.appendChild(td);
      });
      const actionTd = document.createElement('td'); actionTd.textContent='—'; row.appendChild(actionTd);
      tbody.appendChild(row);

      children.forEach((child) => {
        const cRow = document.createElement('tr');
        cRow.dataset.parent = String(idx);
        cRow.style.display = 'none';

        const cExp = document.createElement('td');
        cExp.textContent = '';
        cRow.appendChild(cExp);

        [
          child.broker,
          child.account,
          child.category,
          child.instrument,
          child.type,
          child.side,
          child.size,
          child.entry_price || child.order_price,
          child.current_price,
          child.stop_loss,
          child.take_profit,
          child.leverage,
          formatTimestamp(child.opened_at),
          child.status,
        ].forEach((v) => {
          const td = document.createElement('td');
          td.textContent = fmt(v);
          cRow.appendChild(td);
        });

        const actionTd = document.createElement('td');
        actionTd.textContent = '—';
        cRow.appendChild(actionTd);

        tbody.appendChild(cRow);
      });
    });
  };

  const refresh = async () => {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
      try {
        setBadge('Loading...');
        const payload = await fetchJson('/api/open-orders');
        render(payload.items || [], payload.errors || []);
        const stale = Boolean(payload.stale);
        const errCount = Array.isArray(payload.errors) ? payload.errors.length : 0;
        if (stale) {
          setBadge(`Stale${errCount ? ` (${errCount} errors)` : ''}`);
        } else {
          setBadge(`Updated${errCount ? ` (${errCount} errors)` : ''}`);
        }
      } catch (err) {
        if (!hasData) {
          render([], [{ message: err.message }]);
        } else if (errorsBox) {
          errorsBox.style.display = 'block';
          if (errorsList) {
            errorsList.innerHTML = '';
            const li = document.createElement('li');
            li.textContent = err.message || 'Refresh failed';
            errorsList.appendChild(li);
          }
        }
        setBadge('Stale (refresh failed)');
      } finally {
        refreshInFlight = null;
      }
    })();
    return refreshInFlight;
  };

  const restartPolling = () => {
    if (pollTimer) clearInterval(pollTimer);
    const multiplier = document.visibilityState === 'hidden' ? HIDDEN_MULTIPLIER : 1;
    pollTimer = setInterval(() => { refresh(); }, POLL_MS * multiplier);
  };

  refreshBtn?.addEventListener('click', refresh);
  refresh();
  restartPolling();
  document.addEventListener('visibilitychange', restartPolling);
  window.addEventListener('beforeunload', () => { if (pollTimer) clearInterval(pollTimer); });
})();
