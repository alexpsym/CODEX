(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const status = document.getElementById('status');
  const scriptsGrid = document.getElementById('scripts-grid');

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
  let oandaInFlight = null;
  let watchlistInFlight = null;

  let scriptsTimer = null;
  let oandaTimer = null;
  let oandaSecondTimer = null;

  const POLL_MS = {
    scripts: 15_000,
    oandaInactivity: 30_000,
    hiddenMultiplier: 3,
  };

  let oandaState = null;
  let oandaExpanded = false;
  let watchlistState = [];

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
      const detail = bodyJson?.detail;
      const message = typeof detail === 'string' && detail.trim()
        ? detail.trim()
        : `${options.method || 'GET'} ${url} failed: ${res.status} ${(bodyText || res.statusText || '').trim()}`;
      throw new Error(message);
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
          scripts.forEach((item) => scriptsGrid.appendChild(makeScriptButton(item)));
        }
        setStatus(`Updated ${new Date().toLocaleTimeString()}`);
      } catch (err) {
        console.error(err);
        setStatus('Failed to load scripts.', true);
      } finally {
        scriptsInFlight = null;
      }
    })();
    return scriptsInFlight;
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
      } catch (err) {
        console.error(err);
        renderOandaInactivity({
          ok: false,
          status: 'unavailable',
          error: err.message || 'Request failed',
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
    if (watchlistClearBtn) watchlistClearBtn.disabled = !list.length || Boolean(watchlistInFlight);
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
    [scriptsTimer, oandaTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
    if (oandaSecondTimer) clearInterval(oandaSecondTimer);
    const multiplier = document.visibilityState === 'hidden' ? POLL_MS.hiddenMultiplier : 1;
    scriptsTimer = setInterval(() => { refreshScripts(); }, POLL_MS.scripts * multiplier);
    oandaTimer = setInterval(() => { refreshOandaInactivity(); }, POLL_MS.oandaInactivity * multiplier);
    oandaSecondTimer = setInterval(() => { tickOandaCountdown(); }, 1000);
  };

  refreshBtn?.addEventListener('click', () => {
    refreshScripts();
    refreshOandaInactivity();
  });
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
  refreshWatchlist();
  refreshOandaInactivity();
  syncOandaDetailsVisibility();
  restartPolling();
  document.addEventListener('visibilitychange', restartPolling);
  window.addEventListener('beforeunload', () => {
    [scriptsTimer, oandaTimer, oandaSecondTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
  });
})();
