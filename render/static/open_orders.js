(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const statusBadge = document.getElementById('open-orders-status');
  const table = document.getElementById('open-orders-table');
  const tbody = table?.querySelector('tbody');
  const emptyState = document.getElementById('open-orders-empty');
  const errorsBox = document.getElementById('open-orders-errors');
  const errorsList = errorsBox?.querySelector('ul');
  let refreshInFlight = null;
  let hasData = false;
  let knownVersion = null;
  let versionPollTimer = null;
  const POLL_MS = 2500;

  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const setBadge = (message) => { if (statusBadge) statusBadge.textContent = message; };

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

  const formatSourceErrors = (errors = []) => {
    if (!Array.isArray(errors)) return [];
    return errors
      .map((entry) => {
        if (!entry || typeof entry !== 'object') return null;
        const broker = String(entry.broker || 'Source').trim();
        const account = String(entry.account || '').trim();
        const category = String(entry.category || '').trim();
        const message = String(entry.message || '').trim() || 'Unknown source error';
        return [broker, account, category].filter(Boolean).join(' ') + `: ${message}`;
      })
      .filter(Boolean);
  };

  const buildFetchError = (url, status, statusText, bodyText, bodyJson) => {
    const detailErrors = bodyJson?.detail?.errors || bodyJson?.errors;
    const flattened = formatSourceErrors(detailErrors);
    if (flattened.length) {
      return new Error(flattened.join(' | '));
    }
    const body = (bodyText || '').trim();
    return new Error(`GET ${url} failed: ${status} ${body || statusText}`);
  };

  const fetchJson = async (url) => {
    const response = await fetch(url);
    let bodyText = '';
    let bodyJson = null;
    try {
      bodyText = await response.text();
      bodyJson = bodyText ? JSON.parse(bodyText) : null;
    } catch (_err) {
      bodyJson = null;
    }
    if (!response.ok) throw buildFetchError(url, response.status, response.statusText, bodyText, bodyJson);
    if (bodyJson !== null) return bodyJson;
    return bodyText ? JSON.parse(bodyText) : {};
  };

  const resolveAccountLabel = (row) => {
    const parts = [];
    const account = String(row?.account || '').trim();
    const accountId = String(row?.account_id || '').trim();
    const suffix = String(row?.account_label_suffix || '').trim();
    if (account) parts.push(account);
    if (accountId) parts.push(accountId);
    if (suffix) parts.push(suffix);
    return parts.join(' · ') || '—';
  };

  const isActionableRow = (row) => {
    if (!row || typeof row !== 'object') return false;
    if (row.parent_id || row.parent_order_id) return false;
    const status = String(row.status || '').toLowerCase();
    if (status.includes('bounce waiting')) return false;
    const type = String(row.type || '').toLowerCase();
    return type === 'order' || type === 'position' || type === 'trade' || type === 'webhook';
  };

  const actionLabelFor = (row) => {
    const type = String(row?.type || '').toLowerCase();
    if (type === 'order' || type === 'webhook') return 'Cancel';
    if (type === 'position' || type === 'trade') return 'Close';
    return null;
  };

  const postClose = async (row, btn) => {
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = 'Working...';
    try {
      const response = await fetch('/api/open-orders/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(row),
      });
      const text = await response.text();
      const data = text ? JSON.parse(text) : {};
      if (!response.ok) {
        throw new Error(data?.detail || `${response.status} ${response.statusText}`);
      }
      setBadge('Action accepted. Refreshing...');
      await refresh();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = prev;
      setBadge(err?.message || 'Action failed');
    }
  };

  const renderActionCell = (row, cell, { allowAction = true } = {}) => {
    const label = actionLabelFor(row);
    if (!allowAction || !label || !isActionableRow(row)) {
      cell.textContent = '—';
      return;
    }
    const required = ['broker', 'account', 'category', 'instrument', 'id', 'type'];
    const missing = required.some((key) => !String(row[key] ?? '').trim());
    if (missing) {
      cell.textContent = '—';
      return;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'action-btn';
    btn.textContent = label;
    btn.addEventListener('click', () => postClose(row, btn));
    cell.appendChild(btn);
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
        li.textContent = formatSourceErrors([entry])[0] || entry.message || 'Unknown error';
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
          document.querySelectorAll(`tr[data-parent="${idx}"]`).forEach((r) => { r.style.display = open ? 'none' : ''; });
        };
        expTd.appendChild(exp);
      } else {
        expTd.textContent = '—';
      }
      row.appendChild(expTd);

      [item.broker, resolveAccountLabel(item), item.category, item.instrument, item.timeframe, item.is_test_trade, item.type, item.side, item.size, item.entry_price || item.order_price, item.current_price, item.stop_loss, item.take_profit, item.leverage, formatTimestamp(item.opened_at), item.status].forEach((v) => {
        const td = document.createElement('td');
        td.textContent = fmt(v);
        row.appendChild(td);
      });
      const actionTd = document.createElement('td');
      renderActionCell(item, actionTd, { allowAction: true });
      row.appendChild(actionTd);
      tbody.appendChild(row);

      children.forEach((child) => {
        const cRow = document.createElement('tr');
        cRow.dataset.parent = String(idx);
        cRow.style.display = 'none';

        const cExp = document.createElement('td');
        cExp.textContent = '';
        cRow.appendChild(cExp);

        [child.broker, resolveAccountLabel(child), child.category, child.instrument, child.timeframe, child.is_test_trade, child.type, child.side, child.size, child.entry_price || child.order_price, child.current_price, child.stop_loss, child.take_profit, child.leverage, formatTimestamp(child.opened_at), child.status].forEach((v) => {
          const td = document.createElement('td');
          td.textContent = fmt(v);
          cRow.appendChild(td);
        });

        const actionTd = document.createElement('td');
        renderActionCell(child, actionTd, { allowAction: false });
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
        const payload = await fetchJson('/api/open-orders?force=1');
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

  const pollVersion = async () => {
    if (document.hidden) return;
    try {
      const payload = await fetchJson('/api/open-orders/version');
      const nextVersion = Number(payload?.version);
      if (!Number.isFinite(nextVersion)) return;
      if (knownVersion === null) {
        knownVersion = nextVersion;
        return;
      }
      if (nextVersion !== knownVersion) {
        knownVersion = nextVersion;
        await refresh();
      }
    } catch (_err) {
      // Keep polling; refresh() already handles user-facing network errors.
    }
  };

  const startVersionPolling = () => {
    if (versionPollTimer) return;
    versionPollTimer = setInterval(pollVersion, POLL_MS);
  };

  const stopVersionPolling = () => {
    if (!versionPollTimer) return;
    clearInterval(versionPollTimer);
    versionPollTimer = null;
  };

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopVersionPolling();
      return;
    }
    startVersionPolling();
    pollVersion();
  });

  refreshBtn?.addEventListener('click', refresh);
  refresh().then(pollVersion);
  if (!document.hidden) startVersionPolling();
})();
