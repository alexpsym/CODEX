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
  const watchlistCount = document.getElementById('watchlist-count');
  const watchlistInput = document.getElementById('watchlist-input');
  const watchlistAddBtn = document.getElementById('watchlist-add-btn');
  const watchlistClearBtn = document.getElementById('watchlist-clear-btn');
  const watchlistStatus = document.getElementById('watchlist-status');
  const watchlistItems = document.getElementById('watchlist-items');
  const watchlistEmpty = document.getElementById('watchlist-empty');
  const oandaHeadline = document.getElementById('oanda-inactivity-headline');
  const oandaDetail = document.getElementById('oanda-inactivity-detail');
  const oandaLastTrade = document.getElementById('oanda-inactivity-last-trade');
  const oandaCountdown = document.getElementById('oanda-inactivity-countdown');
  const oandaOpenTrades = document.getElementById('oanda-inactivity-open-trades');
  const oandaThreshold = document.getElementById('oanda-inactivity-threshold');
  const oandaFeeDate = document.getElementById('oanda-inactivity-fee-date');
  const oandaMonthlyFee = document.getElementById('oanda-inactivity-monthly-fee');
  const oandaDetailsWrap = document.getElementById('oanda-inactivity-details');
  const oandaErrorDetail = document.getElementById('oanda-inactivity-error-detail');
  const oandaToggleBtn = document.getElementById('oanda-inactivity-toggle');

  let scriptsInFlight = null;
  let ooInFlight = null;
  let rtInFlight = null;
  let oandaInFlight = null;
  let watchlistInFlight = null;

  let scriptsTimer = null;
  let ooTimer = null;
  let rtTimer = null;
  let oandaTimer = null;
  let oandaSecondTimer = null;

  const POLL_MS = {
    scripts: 15_000,
    openOrders: 60_000,
    recentTrades: 60_000,
    oandaInactivity: 30_000,
    // Slow polling while tab is hidden to reduce background load.
    hiddenMultiplier: 3,
  };

  let hasOpenOrdersData = false;
  let hasRecentTradesData = false;
  let oandaState = null;
  let oandaExpanded = false;
  let watchlistState = [];

  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const fmtNum = (v, dp = 4) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(dp) : '—';
  };
  const fmtNullableNum = (v, dp = 4) => {
    if (v === null || v === undefined || v === '') return '—';
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
  const fmtCountdown = (secs) => {
    const n = Number(secs);
    if (!Number.isFinite(n) || n < 0) return '—';
    const days = Math.floor(n / 86400);
    const hours = Math.floor((n % 86400) / 3600);
    const mins = Math.floor((n % 3600) / 60);
    const rem = Math.floor(n % 60);
    if (days > 0) return `${days}d ${hours}h ${mins}m ${rem}s`;
    if (hours > 0) return `${hours}h ${mins}m ${rem}s`;
    return `${mins}m ${rem}s`;
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

  const isActionableRow = (row) => {
    if (!row || typeof row !== 'object') return false;
    if (row.parent_id || row.parent_order_id) return false;
    const status = String(row.status || '').toLowerCase();
    if (status.includes('bounce waiting')) return false;
    const type = String(row.type || '').toLowerCase();
    if (type === 'order') return true;
    return type === 'position' || type === 'trade';
  };

  const actionLabelFor = (row) => {
    const type = String(row?.type || '').toLowerCase();
    if (type === 'order') return 'Cancel';
    if (type === 'position' || type === 'trade') return 'Close';
    return null;
  };

  const closeOpenOrder = async (row, button) => {
    if (!row || !button) return;
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = 'Working...';
    try {
      await fetchJson('/api/open-orders/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(row),
      });
      await refreshOpenOrders();
      if (ooStatus) ooStatus.textContent = 'Updated';
    } catch (err) {
      console.error(err);
      if (ooStatus) ooStatus.textContent = err?.message || 'Action failed';
      button.disabled = false;
      button.textContent = previous;
    }
  };

  const renderActionCell = (row, actionTd, { allowAction = true } = {}) => {
    actionTd.className = 'action-cell';
    const label = actionLabelFor(row);
    if (!allowAction || !label || !isActionableRow(row)) {
      actionTd.textContent = '—';
      return;
    }
    const required = ['broker', 'account', 'category', 'instrument', 'id', 'type'];
    const missing = required.some((key) => !String(row[key] ?? '').trim());
    if (missing) {
      actionTd.textContent = '—';
      return;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'action-btn';
    btn.textContent = label;
    btn.addEventListener('click', () => closeOpenOrder(row, btn));
    actionTd.appendChild(btn);
  };

  const makeScriptButton = (script) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'script-btn';

    const name = document.createElement('div');
    name.className = 'script-name';
    name.textContent = script.label || script.name;

    const dot = document.createElement('span');
    const dotState = script.running ? 'running' : (script.starting ? 'starting' : 'stopped');
    dot.className = `status-dot ${dotState}`;

    btn.appendChild(name);
    btn.appendChild(dot);

    btn.addEventListener('click', () => {
      const target = script.open_url || '/';
      window.open(target, '_blank', 'noopener');
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
      renderActionCell(item, actionTd, { allowAction: true });
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
        renderActionCell(child, actionTd, { allowAction: false });
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
        const isMonthlyAudRow = item.row_type === 'monthly_aud_reval';

        const rawResultPct = item.result_pct;
        const hasResultPct =
          rawResultPct !== null &&
          rawResultPct !== undefined &&
          rawResultPct !== '' &&
          Number.isFinite(Number(rawResultPct));
        const resultPct = hasResultPct ? Number(rawResultPct) : null;

        const rawResultCash = item.result_cash;
        const hasResultCash =
          rawResultCash !== null &&
          rawResultCash !== undefined &&
          rawResultCash !== '' &&
          Number.isFinite(Number(rawResultCash));
        const resultCash = hasResultCash ? Number(rawResultCash) : null;
        const cashCls = hasResultCash
          ? (resultCash > 0 ? 'pos' : (resultCash < 0 ? 'neg' : ''))
          : '';
        const pctCls = hasResultPct
          ? (resultPct > 0 ? 'pos' : (resultPct < 0 ? 'neg' : ''))
          : '';
        const outcome = String(item.outcome || '—');
        const outcomeCls =
          outcome === 'Win' ? 'win' :
          outcome === 'Loss' ? 'loss' : 'be';

        const cells = [
          item.account,
          item.symbol,
          isMonthlyAudRow ? '' : item.side,
          fmtTime(item.opened_at),
          fmtTime(item.closed_at),
          fmtNullableNum(item.entry_price, 6),
          fmtNullableNum(item.exit_price, 6),
          isMonthlyAudRow ? '' : fmtNullableNum(item.stop_loss, 6),
          isMonthlyAudRow ? '' : fmtNullableNum(item.take_profit, 6),
          isMonthlyAudRow ? '' : fmtNum(item.fees, 2),
        ];

        cells.forEach((c) => {
          const td = document.createElement('td');
          td.textContent = isMonthlyAudRow && c === '' ? '' : fmt(c);
          tr.appendChild(td);
        });

        const outcomeTd = document.createElement('td');
        if (isMonthlyAudRow) {
          outcomeTd.textContent = '';
        } else {
          outcomeTd.innerHTML = `<span class="rt-pill ${outcomeCls}">${outcome}</span>`;
        }
        tr.appendChild(outcomeTd);

        const cashTd = document.createElement('td');
        cashTd.className = `num ${cashCls}`;
        if (!hasResultCash) {
          cashTd.textContent = '—';
        } else {
          const cashText = fmtNum(resultCash, 2);
          const ccy = String(item.result_currency || '').trim();
          cashTd.textContent = ccy ? `${cashText} ${ccy}` : cashText;
        }
        tr.appendChild(cashTd);

        const resultTd = document.createElement('td');
        resultTd.className = `num ${pctCls}`;
        if (isMonthlyAudRow) {
          resultTd.textContent = '';
        } else if (!hasResultPct) {
          resultTd.textContent = '—';
        } else {
          const dp = Math.abs(resultPct) > 0 && Math.abs(resultPct) < 0.01 ? 4 : 2;
          resultTd.textContent = `${resultPct.toFixed(dp)}%`;
        }
        tr.appendChild(resultTd);

        const durTd = document.createElement('td');
        durTd.textContent = isMonthlyAudRow ? '' : fmtDuration(item.duration_seconds);
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


  const renderOandaInactivity = (payload) => {
    oandaState = payload && typeof payload === 'object' ? { ...payload } : null;
    if (oandaLastTrade) oandaLastTrade.textContent = fmtTime(payload?.last_live_fill_at);
    if (oandaOpenTrades) {
      const ot = payload?.open_trade_count;
      const op = payload?.open_position_count;
      oandaOpenTrades.textContent = (ot === null || ot === undefined) ? '—' : `${ot} trades / ${op ?? '—'} positions`;
    }
    if (oandaThreshold) oandaThreshold.textContent = fmtTime(payload?.inactivity_threshold_at);
    if (oandaFeeDate) oandaFeeDate.textContent = fmtTime(payload?.earliest_fee_date);
    if (oandaMonthlyFee) oandaMonthlyFee.textContent = `Up to AUD ${payload?.monthly_fee_aud ?? 10}`;
    if (oandaErrorDetail) {
      const err = String(payload?.error || '').trim();
      oandaErrorDetail.textContent = err ? `Backend detail: ${err}` : '';
    }
    tickOandaCountdown();
  };

  const tickOandaCountdown = () => {
    const payload = oandaState || {};
    const statusValue = String(payload.status || '').toLowerCase();
    if (!oandaHeadline || !oandaDetail) return;
    if (!payload.ok || statusValue === 'unavailable') {
      oandaHeadline.textContent = 'Status unavailable';
      oandaDetail.textContent = oandaExpanded ? 'Unable to confirm inactivity state.' : '';
      if (oandaCountdown) oandaCountdown.textContent = 'Unavailable';
      return;
    }
    if (payload.has_open_positions || statusValue === 'paused_open_position') {
      oandaHeadline.textContent = 'Protected while an OANDA trade is open';
      oandaDetail.textContent = oandaExpanded ? 'Inactivity fee does not apply while open positions/trades exist.' : '';
      if (oandaCountdown) oandaCountdown.textContent = 'Protected';
      return;
    }
    if (statusValue === 'fee_eligible') {
      oandaHeadline.textContent = 'Fee eligibility reached';
      oandaDetail.textContent = oandaExpanded ? 'Threshold has passed. Charge timing follows the third-last weekday monthly rule.' : '';
      if (oandaCountdown) oandaCountdown.textContent = 'Fee eligible';
      return;
    }
    const secs = Number(payload.seconds_until_threshold);
    if (Number.isFinite(secs)) {
      const pretty = fmtCountdown(secs);
      oandaHeadline.textContent = '';
      oandaDetail.textContent = oandaExpanded ? `Earliest fee date: ${fmtTime(payload.earliest_fee_date)}` : '';
      if (oandaCountdown) oandaCountdown.textContent = pretty;
      payload.seconds_until_threshold = Math.max(0, Math.floor(secs - 1));
      return;
    }
    oandaHeadline.textContent = 'Status unavailable';
    oandaDetail.textContent = oandaExpanded ? 'Unable to compute inactivity countdown.' : '';
    if (oandaCountdown) oandaCountdown.textContent = 'Unavailable';
  };

  const syncOandaDetailsVisibility = () => {
    if (oandaDetailsWrap) oandaDetailsWrap.hidden = !oandaExpanded;
    if (oandaToggleBtn) {
      oandaToggleBtn.textContent = oandaExpanded ? '▴' : '▾';
      oandaToggleBtn.setAttribute('aria-expanded', oandaExpanded ? 'true' : 'false');
      oandaToggleBtn.title = oandaExpanded ? 'Hide details' : 'Show details';
    }
    if (oandaErrorDetail && !oandaExpanded) oandaErrorDetail.textContent = '';
    if (oandaState && oandaExpanded && oandaErrorDetail) {
      const err = String(oandaState.error || '').trim();
      oandaErrorDetail.textContent = err ? `Backend detail: ${err}` : '';
    }
  };

  const refreshOandaInactivity = async () => {
    if (oandaInFlight) return oandaInFlight;
    oandaInFlight = (async () => {
      try {
        const payload = await fetchJson('/api/oanda-inactivity-status');
        renderOandaInactivity(payload);
      } catch (e) {
        console.error(e);
        renderOandaInactivity({
          ok: false,
          status: 'unavailable',
          error: e.message || 'Request failed',
          updated_at: new Date().toISOString(),
          monthly_fee_aud: 10,
        });
      } finally {
        oandaInFlight = null;
      }
    })();
    return oandaInFlight;
  };

  const setWatchlistStatus = (msg, isErr = false) => {
    if (!watchlistStatus) return;
    watchlistStatus.textContent = msg || '';
    watchlistStatus.style.color = isErr ? '#fca5a5' : '#94a3b8';
  };

  const renderWatchlist = (items) => {
    if (!watchlistItems) return;
    watchlistItems.innerHTML = '';
    const list = Array.isArray(items) ? items : [];
    list.forEach((symbol) => {
      const tr = document.createElement('tr');
      const symTd = document.createElement('td');
      symTd.textContent = symbol;
      tr.appendChild(symTd);

      const actionTd = document.createElement('td');
      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'action-btn';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', () => removeWatchlistItem(symbol));
      actionTd.appendChild(removeBtn);
      tr.appendChild(actionTd);
      watchlistItems.appendChild(tr);
    });
    if (watchlistCount) watchlistCount.textContent = String(list.length);
    if (watchlistEmpty) watchlistEmpty.style.display = list.length ? 'none' : 'block';
    if (watchlistClearBtn) {
      watchlistClearBtn.disabled = !list.length || Boolean(watchlistInFlight);
    }
  };

  const normalizeWatchlistInput = (text) => {
    return String(text || '')
      .split(',')
      .map((entry) => String(entry || '').trim().toUpperCase())
      .filter(Boolean);
  };

  const FX_CODES = new Set(['AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'HKD', 'JPY', 'NZD', 'SGD', 'TRY', 'USD', 'ZAR', 'XAU', 'XAG']);
  const isLikelyFxPair = (value) => {
    const token = String(value || '').trim().toUpperCase();
    if (/^[A-Z]{3}_[A-Z]{3}$/.test(token)) {
      const [base, quote] = token.split('_');
      return FX_CODES.has(base) && FX_CODES.has(quote);
    }
    if (/^[A-Z]{6}$/.test(token)) {
      const base = token.slice(0, 3);
      const quote = token.slice(3);
      return FX_CODES.has(base) && FX_CODES.has(quote);
    }
    return false;
  };

  const resolveBybitSymbol = async (symbol) => {
    const token = String(symbol || '').trim().toUpperCase();
    if (!token || isLikelyFxPair(token)) return token;
    try {
      const payload = await fetchJson(`/api/resolve-symbol?symbol=${encodeURIComponent(token)}&prefer=bybit&scope=linear`);
      return String(payload?.resolved_symbol || token).trim().toUpperCase();
    } catch {
      return token;
    }
  };

  const saveWatchlist = async (items, successMessage = '') => {
    if (watchlistInFlight) return watchlistInFlight;
    const payloadItems = Array.isArray(items) ? items : [];
    if (watchlistClearBtn) watchlistClearBtn.disabled = true;
    watchlistInFlight = (async () => {
      try {
        const payload = await fetchJson('/api/watchlist', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: payloadItems }),
        });
        watchlistState = Array.isArray(payload?.items) ? payload.items : payloadItems;
        renderWatchlist(watchlistState);
        if (successMessage) setWatchlistStatus(successMessage, false);
      } catch (err) {
        console.error(err);
        renderWatchlist(watchlistState);
        setWatchlistStatus(err?.message || 'Watchlist update failed.', true);
      } finally {
        watchlistInFlight = null;
        if (watchlistClearBtn) watchlistClearBtn.disabled = !watchlistState.length;
      }
    })();
    return watchlistInFlight;
  };

  const refreshWatchlist = async () => {
    try {
      const payload = await fetchJson('/api/watchlist');
      watchlistState = Array.isArray(payload?.items) ? payload.items : [];
      renderWatchlist(watchlistState);
      setWatchlistStatus('', false);
    } catch (err) {
      console.error(err);
      setWatchlistStatus(err?.message || 'Failed to load watchlist.', true);
    }
  };

  const addWatchlistItems = async () => {
    const rawAdditions = normalizeWatchlistInput(watchlistInput?.value);
    const additions = [];
    for (const symbol of rawAdditions) {
      additions.push(await resolveBybitSymbol(symbol));
    }
    if (!additions.length) {
      setWatchlistStatus('Enter at least one symbol.', true);
      return;
    }
    const next = Array.from(new Set([...watchlistState, ...additions]));
    await saveWatchlist(next, `Saved ${next.length} item${next.length === 1 ? '' : 's'}.`);
    if (watchlistInput) watchlistInput.value = '';
  };

  const removeWatchlistItem = async (symbol) => {
    const target = String(symbol || '').trim().toUpperCase();
    if (!target) return;
    const next = watchlistState.filter((item) => String(item || '').toUpperCase() !== target);
    await saveWatchlist(next, `Removed ${target}.`);
  };

  const clearWatchlist = async () => {
    if (!watchlistState.length || watchlistInFlight) return;
    await saveWatchlist([], 'Watchlist cleared.');
  };

  const restartPolling = () => {
    [scriptsTimer, ooTimer, rtTimer, oandaTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
    if (oandaSecondTimer) clearInterval(oandaSecondTimer);
    const multiplier = document.visibilityState === 'hidden' ? POLL_MS.hiddenMultiplier : 1;
    scriptsTimer = setInterval(() => { refreshScripts(); }, POLL_MS.scripts * multiplier);
    if (document.visibilityState !== 'hidden') {
      ooTimer = setInterval(() => { refreshOpenOrders(); }, POLL_MS.openOrders);
    }
    rtTimer = setInterval(() => { refreshRecentTrades(); }, POLL_MS.recentTrades * multiplier);
    oandaTimer = setInterval(() => { refreshOandaInactivity(); }, POLL_MS.oandaInactivity * multiplier);
    oandaSecondTimer = setInterval(() => { tickOandaCountdown(); }, 1000);
  };

  refreshBtn?.addEventListener('click', () => { refreshScripts(); refreshOpenOrders(); refreshRecentTrades(); refreshOandaInactivity(); });
  ooRefreshBtn?.addEventListener('click', () => refreshOpenOrders());
  watchlistAddBtn?.addEventListener('click', () => addWatchlistItems());
  watchlistInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      addWatchlistItems();
    }
  });
  watchlistClearBtn?.addEventListener('click', () => clearWatchlist());
  oandaToggleBtn?.addEventListener('click', () => {
    oandaExpanded = !oandaExpanded;
    syncOandaDetailsVisibility();
  });

  refreshScripts();
  refreshOpenOrders();
  refreshRecentTrades();
  refreshWatchlist();
  refreshOandaInactivity();
  syncOandaDetailsVisibility();
  restartPolling();
  document.addEventListener('visibilitychange', restartPolling);
  window.addEventListener('beforeunload', () => {
    [scriptsTimer, ooTimer, rtTimer, oandaTimer, oandaSecondTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
  });
})();
