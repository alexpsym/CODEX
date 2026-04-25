(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const statusBadge = document.getElementById('open-orders-status');
  const table = document.getElementById('open-orders-table');
  const tbody = table?.querySelector('tbody');
  const emptyState = document.getElementById('open-orders-empty');
  const errorsBox = document.getElementById('open-orders-errors');
  const errorsList = errorsBox?.querySelector('ul');
  const attemptsTable = document.getElementById('webhook-attempts-table');
  const attemptsBody = attemptsTable?.querySelector('tbody');
  const webhookStatusLabel = (status) => {
    const key = String(status || '').toUpperCase();
    if (key === 'WAITING') return 'Waiting for TradingView POST';
    if (key === 'TRIGGERING') return 'Triggering';
    if (key === 'BYBIT_REJECTED') return 'Bybit rejected';
    if (key === 'FAILED_BEFORE_SUBMIT') return 'Failed before submit';
    if (key === 'ORDER_CREATED_TPSL_FAILED') return 'Order created, TP/SL failed';
    if (key === 'PENDING_NOT_FOUND') return 'Pending webhook not found on this instance';
    return status || '—';
  };
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

      const isWebhook = String(item.broker || '').toUpperCase() === 'WEBHOOK' || String(item.type || '').toLowerCase() === 'webhook';
      const rowValues = [
        item.broker,
        resolveAccountLabel(item),
        item.category,
        item.instrument,
        item.timeframe,
        item.is_test_trade,
        item.type,
        item.side,
        item.size,
        item.entry_price || item.order_price,
        item.current_price,
        item.stop_loss,
        item.take_profit,
        item.leverage,
        formatTimestamp(item.opened_at),
        isWebhook ? `Pending webhook — not a live broker order · ${webhookStatusLabel(item.status)}` : item.status,
      ];
      rowValues.forEach((v, valueIdx) => {
        const td = document.createElement('td');
        td.textContent = fmt(v);
        if (isWebhook && valueIdx === rowValues.length - 1) {
          td.title = [
            `request_id: ${fmt(item.request_id)}`,
            `last_error: ${fmt(item.last_error)}`,
            `bybit_ret_code: ${fmt(item.bybit_ret_code)}`,
            `bybit_ret_msg: ${fmt(item.bybit_ret_msg)}`,
          ].join('\n');
        }
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

  const renderWebhookAttempts = (items = [], fetchError = '') => {
    if (!attemptsBody) return;
    attemptsBody.innerHTML = '';
    if (fetchError) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 11;
      td.className = 'muted';
      td.textContent = `Failed to load webhook attempts: ${fetchError}`;
      tr.appendChild(td);
      attemptsBody.appendChild(tr);
      return;
    }
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 11;
      td.className = 'muted';
      td.textContent = 'No recent webhook attempts.';
      tr.appendChild(td);
      attemptsBody.appendChild(tr);
      return;
    }
    rows.forEach((item) => {
      const tr = document.createElement('tr');
      [
        formatTimestamp(item.updated_at || item.received_at),
        item.symbol,
        item.action,
        item.account,
        item.status,
        item.bybit_ret_code,
        item.bybit_ret_msg,
        item.request_id,
        item.pending_webhook_id,
        item.error || item.last_error,
        item.request_host || item.payload_origin_host,
      ].forEach((v) => {
        const td = document.createElement('td');
        td.textContent = fmt(v);
        tr.appendChild(td);
      });
      attemptsBody.appendChild(tr);
    });
  };

  const refresh = async () => {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
      try {
        setBadge('Loading...');
        const payload = await fetchJson('/api/open-orders?force=1');
        render(payload.items || [], payload.errors || []);
        try {
          const attempts = await fetchJson('/api/calculator/webhook-attempts?limit=20');
          renderWebhookAttempts(attempts.items || []);
        } catch (attemptErr) {
          renderWebhookAttempts([], attemptErr?.message || String(attemptErr));
        }
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
