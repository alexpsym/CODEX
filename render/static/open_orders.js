(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const statusBadge = document.getElementById('open-orders-status');
  const table = document.getElementById('open-orders-table');
  const tbody = table?.querySelector('tbody');
  const emptyState = document.getElementById('open-orders-empty');
  const errorsBox = document.getElementById('open-orders-errors');
  const errorsList = errorsBox?.querySelector('ul');

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
        const cr = document.createElement('tr'); cr.dataset.parent = String(idx); cr.style.display='none';
        const td1 = document.createElement('td'); cr.appendChild(td1);
        const td2 = document.createElement('td'); td2.colSpan = 15; td2.textContent = `${child.kind}: ${fmt(child.price)}`; cr.appendChild(td2);
        tbody.appendChild(cr);
      });
    });
  };

  const refresh = async () => {
    try {
      setBadge('Loading...');
      const payload = await fetchJson('/api/open-orders');
      render(payload.items || [], payload.errors || []);
      setBadge('Updated');
    } catch (err) {
      render([], [{ message: err.message }]);
      setBadge('Failed');
    }
  };

  refreshBtn?.addEventListener('click', refresh);
  refresh();
})();
