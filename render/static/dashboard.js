(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const status = document.getElementById('status');
  const scriptsGrid = document.getElementById('scripts-grid');

  const ooRefreshBtn = document.getElementById('oo-refresh-btn');
  const ooStatus = document.getElementById('oo-status');
  const ooTable = document.getElementById('open-orders-table');
  const ooTbody = ooTable?.querySelector('tbody');
  const ooEmpty = document.getElementById('open-orders-empty');
  const ooErrorsBox = document.getElementById('open-orders-errors');
  const ooErrorsList = ooErrorsBox?.querySelector('ul');

  const rtStatus = document.getElementById('recent-trades-status');
  const rtBody = document.querySelector('#recent-trades-table tbody');
  const rtEmpty = document.getElementById('recent-trades-empty');

  let scriptsInFlight = null;
  let ooInFlight = null;
  let rtInFlight = null;

  let scriptsTimer = null;
  let ooTimer = null;
  let rtTimer = null;

  const POLL_MS = {
    scripts: 15_000,
    openOrders: 10_000,
    recentTrades: 15_000,
    // Slow polling while tab is hidden to reduce background load.
    hiddenMultiplier: 3,
  };

  let hasOpenOrdersData = false;
  let hasRecentTradesData = false;

  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const fmtNum = (v, dp = 4) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(dp) : '—';
  };
  const fmtTime = (v) => {
    if (!v) return '—';
    const n = Number(v);
    if (!Number.isNaN(n)) {
      const ms = n < 1_000_000_000_000 ? n * 1000 : n;
      const d = new Date(ms);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    const d = new Date(v);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    return String(v);
  };
  const fmtDuration = (secs) => {
    const n = Number(secs);
    if (!Number.isFinite(n) || n < 0) return '—';
    const s = Math.floor(n % 60);
    const m = Math.floor((n / 60) % 60);
    const h = Math.floor((n / 3600) % 24);
    const d = Math.floor(n / 86400);
    if (d > 0) return `${d}d ${h}h ${m}m ${s}s`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  const setStatus = (msg, isErr = false) => {
    if (!status) return;
    status.textContent = msg;
    status.style.color = isErr ? '#fca5a5' : '#94a3b8';
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

  const buildFetchError = (url, method, status, statusText, bodyText, bodyJson) => {
    const detailErrors = bodyJson?.detail?.errors || bodyJson?.errors;
    const flattened = formatSourceErrors(detailErrors);
    if (flattened.length) {
      return new Error(flattened.join(' | '));
    }
    const body = (bodyText || '').trim();
    return new Error(`${method || 'GET'} ${url} failed: ${status} ${body || statusText}`);
  };

  const fetchJson = async (url, options = {}) => {
    const res = await fetch(url, options);
    let bodyText = '';
    let bodyJson = null;
    try {
      bodyText = await res.text();
      bodyJson = bodyText ? JSON.parse(bodyText) : null;
    } catch (_err) {
      bodyJson = null;
    }
    if (!res.ok) {
      throw buildFetchError(url, options.method || 'GET', res.status, res.statusText, bodyText, bodyJson);
    }
    if (bodyJson !== null) return bodyJson;
    return bodyText ? JSON.parse(bodyText) : {};
  };

  const makeScriptButton = (script) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'script-btn';

    const name = document.createElement('div');
    name.className = 'script-name';
    name.textContent = script.label || script.name;

    const dot = document.createElement('span');
    dot.className = `status-dot ${script.running ? 'running' : 'stopped'}`;

    btn.appendChild(name);
    btn.appendChild(dot);

    btn.addEventListener('click', () => {
      const target = script.open_url || '/';
      window.location.href = target;
    });

    return btn;
  };

  const refreshScripts = async () => {
    if (scriptsInFlight) return scriptsInFlight;
    scriptsInFlight = (async () => {
      try {
        setStatus('Loading scripts...');
        const scripts = await fetchJson('/scripts');
        if (scriptsGrid) {
          scriptsGrid.innerHTML = '';
          scripts.forEach((s) => scriptsGrid.appendChild(makeScriptButton(s)));
        }
        setStatus(`Updated ${new Date().toLocaleTimeString()}`);
      } catch (e) {
        console.error(e);
        setStatus('Failed to load scripts.', true);
      } finally {
        scriptsInFlight = null;
      }
    })();
    return scriptsInFlight;
  };

  const renderOpenOrders = (items, errors) => {
    if (!ooTbody) return;
    ooTbody.innerHTML = '';
    hasOpenOrdersData = Boolean(items?.length);

    if (ooErrorsBox) ooErrorsBox.style.display = errors?.length ? 'block' : 'none';
    if (ooErrorsList) {
      ooErrorsList.innerHTML = '';
      (errors || []).forEach((err) => {
        const li = document.createElement('li');
        li.textContent = formatSourceErrors([err])[0] || err.message || String(err);
        ooErrorsList.appendChild(li);
      });
    }

    if (!items?.length) {
      if (ooEmpty) ooEmpty.style.display = 'block';
      return;
    }
    if (ooEmpty) ooEmpty.style.display = 'none';

    items.forEach((item, idx) => {
      const tr = document.createElement('tr');
      const children = Array.isArray(item.children) ? item.children : [];

      const expTd = document.createElement('td');
      if (children.length) {
        const exp = document.createElement('button');
        exp.type = 'button';
        exp.className = 'action-btn';
        exp.textContent = '▸';
        exp.style.minWidth = '30px';
        exp.addEventListener('click', () => {
          const open = exp.textContent === '▾';
          exp.textContent = open ? '▸' : '▾';
          document.querySelectorAll(`tr[data-parent="${idx}"]`).forEach((row) => {
            row.style.display = open ? 'none' : '';
          });
        });
        expTd.appendChild(exp);
      } else {
        expTd.textContent = '—';
      }
      tr.appendChild(expTd);

      [item.broker, item.account, item.category, item.instrument, item.type, item.side, item.size, item.entry_price || item.order_price, item.current_price, item.stop_loss, item.take_profit, item.leverage, fmtTime(item.opened_at), item.status].forEach((c) => {
        const td = document.createElement('td');
        td.textContent = fmt(c);
        tr.appendChild(td);
      });

      const actionTd = document.createElement('td');
      actionTd.className = 'action-cell';
      actionTd.textContent = '—';
      tr.appendChild(actionTd);
      ooTbody.appendChild(tr);

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
          fmtTime(child.opened_at),
          child.status,
        ].forEach((c) => {
          const td = document.createElement('td');
          td.textContent = fmt(c);
          cRow.appendChild(td);
        });

        const actionTd = document.createElement('td');
        actionTd.className = 'action-cell';
        actionTd.textContent = '—';
        cRow.appendChild(actionTd);

        ooTbody.appendChild(cRow);
      });
    });
  };

  const refreshOpenOrders = async () => {
    if (ooInFlight) return ooInFlight;
    ooInFlight = (async () => {
      try {
        if (ooStatus) ooStatus.textContent = 'Loading...';
        const payload = await fetchJson('/api/open-orders');
        renderOpenOrders(payload.items || [], payload.errors || []);
        const stale = Boolean(payload.stale);
        const errCount = Array.isArray(payload.errors) ? payload.errors.length : 0;
        if (ooStatus) {
          if (stale) {
            ooStatus.textContent = `Stale${errCount ? ` (${errCount} errors)` : ''}`;
          } else {
            ooStatus.textContent = `Updated${errCount ? ` (${errCount} errors)` : ''}`;
          }
        }
      } catch (e) {
        console.error(e);
        if (!hasOpenOrdersData) {
          renderOpenOrders([], [{ message: e.message }]);
        } else if (ooErrorsBox) {
          ooErrorsBox.style.display = 'block';
          if (ooErrorsList) {
            ooErrorsList.innerHTML = '';
            const li = document.createElement('li');
            li.textContent = e.message || 'Open orders refresh failed';
            ooErrorsList.appendChild(li);
          }
        }
        if (ooStatus) ooStatus.textContent = 'Stale (refresh failed)';
      } finally {
        ooInFlight = null;
      }
    })();
    return ooInFlight;
  };

  const refreshRecentTrades = async () => {
    if (!rtBody) return Promise.resolve();
    if (rtInFlight) return rtInFlight;
    rtInFlight = (async () => {
      try {
        if (rtStatus) rtStatus.textContent = 'Loading...';
        const payload = await fetchJson('/api/recent-trades?limit=20');
        const items = payload.items || [];
        rtBody.innerHTML = '';
        items.forEach((item) => {
        const tr = document.createElement('tr');

        const resultPct = Number(item.result_pct);
        const pnlCls = Number.isFinite(resultPct)
          ? (resultPct > 0 ? 'pos' : (resultPct < 0 ? 'neg' : ''))
          : '';
        const outcome = String(item.outcome || '—');
        const outcomeCls =
          outcome === 'Win' ? 'win' :
          outcome === 'Loss' ? 'loss' : 'be';

        const cells = [
          item.account,
          item.symbol,
          item.side,
          fmtTime(item.opened_at),
          fmtTime(item.closed_at),
          fmtNum(item.stop_loss, 6),
          fmtNum(item.take_profit, 6),
          fmtNum(item.fees, 2),
        ];

        cells.forEach((c) => {
          const td = document.createElement('td');
          td.textContent = fmt(c);
          tr.appendChild(td);
        });

        const outcomeTd = document.createElement('td');
        outcomeTd.innerHTML = `<span class="rt-pill ${outcomeCls}">${outcome}</span>`;
        tr.appendChild(outcomeTd);

        const resultTd = document.createElement('td');
        resultTd.className = `num ${pnlCls}`;
        resultTd.textContent = Number.isFinite(resultPct) ? `${resultPct.toFixed(2)}%` : '—';
        tr.appendChild(resultTd);

        const durTd = document.createElement('td');
        durTd.textContent = fmtDuration(item.duration_seconds);
        tr.appendChild(durTd);

          rtBody.appendChild(tr);
        });
        hasRecentTradesData = Boolean(items.length);
        if (rtEmpty) rtEmpty.style.display = items.length ? 'none' : 'block';
        if (rtStatus) rtStatus.textContent = 'Updated';
      } catch (e) {
        console.error(e);
        if (!hasRecentTradesData && rtEmpty) rtEmpty.style.display = 'block';
        if (rtStatus) rtStatus.textContent = 'Stale (refresh failed)';
      } finally {
        rtInFlight = null;
      }
    })();
    return rtInFlight;
  };

  const restartPolling = () => {
    [scriptsTimer, ooTimer, rtTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
    const multiplier = document.visibilityState === 'hidden' ? POLL_MS.hiddenMultiplier : 1;
    scriptsTimer = setInterval(() => { refreshScripts(); }, POLL_MS.scripts * multiplier);
    ooTimer = setInterval(() => { refreshOpenOrders(); }, POLL_MS.openOrders * multiplier);
    rtTimer = setInterval(() => { refreshRecentTrades(); }, POLL_MS.recentTrades * multiplier);
  };

  refreshBtn?.addEventListener('click', () => { refreshScripts(); refreshOpenOrders(); refreshRecentTrades(); });
  ooRefreshBtn?.addEventListener('click', () => refreshOpenOrders());

  refreshScripts();
  refreshOpenOrders();
  refreshRecentTrades();
  restartPolling();
  document.addEventListener('visibilitychange', restartPolling);
  window.addEventListener('beforeunload', () => {
    [scriptsTimer, ooTimer, rtTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
  });
})();
