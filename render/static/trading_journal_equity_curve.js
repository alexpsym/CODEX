(() => {
  const ACCOUNT_CHOICES = Object.freeze([
    { value: 'BINANCE', label: 'Binance', aliases: ['BINANCE'] },
    { value: 'BYBIT', label: 'Bybit', aliases: ['BYBIT', 'BYBIT LIVE'] },
    { value: 'OANDA DEMO', label: 'Oanda demo', aliases: ['OANDA DEMO'] },
    { value: 'OANDA LIVE', label: 'Oanda live', aliases: ['OANDA LIVE'] },
    { value: 'PEPPERSTONE DEMO', label: 'Pepperstone demo', aliases: ['PEPPERSTONE DEMO'] },
    { value: 'PEPPERSTONE LIVE', label: 'Pepperstone live', aliases: ['PEPPERSTONE LIVE'] },
  ]);
  const STORAGE_KEY = 'tradingJournal.equityAccount';
  const byAlias = new Map();
  ACCOUNT_CHOICES.forEach((choice) => {
    choice.aliases.forEach((alias) => byAlias.set(alias, choice.value));
  });

  const normalizeWords = (value) => String(value ?? '')
    .trim()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .toUpperCase();

  const canonicalAccount = (rowOrValue) => {
    const raw = typeof rowOrValue === 'object' && rowOrValue !== null
      ? (
        rowOrValue.account_label
        ?? rowOrValue.account
        ?? rowOrValue.label
        ?? rowOrValue.provider_account
      )
      : rowOrValue;
    return byAlias.get(normalizeWords(raw)) || '';
  };

  const asFinite = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const isTrue = (value) => ['true', 'yes', 'y', '1'].includes(String(value ?? '').trim().toLowerCase());
  const rowType = (row) => normalizeWords(row?.row_type || 'trade').toLowerCase().replace(/ /g, '_');
  const isTrade = (row) => rowType(row) === 'trade';
  const isValidCashflowAnchor = (row) => rowType(row) === 'cashflow' && asFinite(row?.cashflow_new_balance) !== null;
  const canonicalTimestamp = (row) => {
    const raw = row?.close_time || row?.open_time;
    const value = raw ? new Date(raw).getTime() : NaN;
    return Number.isFinite(value) ? value : null;
  };
  const rowBalance = (row) => {
    const analysis = asFinite(row?.analysis_balance_after_trade);
    if (analysis !== null) return analysis;
    const tradeBalance = asFinite(row?.balance_after_trade);
    if (tradeBalance !== null) return tradeBalance;
    return isValidCashflowAnchor(row) ? asFinite(row?.cashflow_new_balance) : null;
  };
  const rowCurrency = (row) => normalizeWords(
    row?.balance_after_trade_currency || row?.account_currency || row?.currency || ''
  );

  const accountCurrencyMap = (balances) => {
    const currencies = new Map();
    (Array.isArray(balances) ? balances : []).forEach((balance) => {
      if (!balance || typeof balance !== 'object') return;
      const account = canonicalAccount(balance);
      const currency = normalizeWords(
        balance.currency || balance.account_currency || ''
      );
      if (account && currency) currencies.set(account, currency);
    });
    return currencies;
  };

  function normalizeEquityPoints(rows, selectedAccount, balances = []) {
    const account = canonicalAccount(selectedAccount);
    const fallbackCurrency = accountCurrencyMap(balances).get(account) || '';
    const deduped = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row || typeof row !== 'object') return;
      if (canonicalAccount(row) !== account) return;
      if (isTrue(row.is_test_trade)) return;
      const type = rowType(row);
      if (type === 'monthly_aud_reval' || (!isTrade(row) && !isValidCashflowAnchor(row))) return;
      const timestamp = canonicalTimestamp(row);
      const balance = rowBalance(row);
      const currency = rowCurrency(row) || fallbackCurrency;
      if (timestamp === null || balance === null || !currency) return;
      const stableIdentity = String(row.id || row.row_id || row.stable_row_id || '').trim();
      const key = stableIdentity || `${account}|${timestamp}|${balance}|${currency}`;
      deduped.set(key, { timestamp, balance, currency, identity: key });
    });
    const ordered = Array.from(deduped.values()).sort((a, b) => (
      a.timestamp - b.timestamp || a.identity.localeCompare(b.identity)
    ));
    if (!ordered.length) return [];
    const latestCurrency = ordered[ordered.length - 1].currency;
    return ordered.filter((point) => point.currency === latestCurrency);
  }

  function preferredOrFirstAccountWithData(rows, preferred, balances = []) {
    const canonicalPreferred = canonicalAccount(preferred);
    if (
      canonicalPreferred
      && normalizeEquityPoints(rows, canonicalPreferred, balances).length
    ) {
      return canonicalPreferred;
    }
    const first = ACCOUNT_CHOICES.find(
      (choice) => normalizeEquityPoints(rows, choice.value, balances).length
    );
    return first?.value || canonicalPreferred || ACCOUNT_CHOICES[0].value;
  }

  const formatBalance = (value, currency) => {
    const maximumFractionDigits = ['JPY'].includes(currency) ? 0 : 2;
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${number.toLocaleString('en-AU', { maximumFractionDigits })} ${currency}`.trim();
  };
  const formatDate = (timestamp) => new Date(timestamp).toLocaleDateString('en-AU', {
    day: '2-digit', month: 'short', year: '2-digit', timeZone: 'Australia/Brisbane',
  });

  function drawChart(canvas, points) {
    if (!canvas || !points.length) return;
    // Keep the canvas out of CSS intrinsic sizing. An old pixel width here
    // makes the one-column mobile grid retain the previous desktop width.
    canvas.style.width = '100%';
    const rect = canvas.getBoundingClientRect();
    const cssWidth = Math.max(1, Math.floor(rect.width || canvas.clientWidth || 900));
    const cssHeight = Math.max(360, Math.floor(rect.height || canvas.clientHeight || 440));
    const dpr = Math.max(1, Number(window.devicePixelRatio) || 1);
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    canvas.style.height = `${cssHeight}px`;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, cssWidth, cssHeight);

    const minTime = points[0].timestamp;
    const maxTime = points[points.length - 1].timestamp;
    const rawMin = Math.min(...points.map((point) => point.balance));
    const rawMax = Math.max(...points.map((point) => point.balance));
    const padding = Math.max((rawMax - rawMin) * 0.08, Math.abs(rawMax || 1) * 0.005, 1e-9);
    const minBalance = rawMin - padding;
    const maxBalance = rawMax + padding;
    const timeSpan = Math.max(1, maxTime - minTime);
    const balanceSpan = Math.max(1e-9, maxBalance - minBalance);
    const currency = points[points.length - 1].currency;
    context.font = '12px system-ui, sans-serif';
    const yTickLabels = Array.from({ length: 5 }, (_unused, index) => {
      const ratio = index / 4;
      return formatBalance(maxBalance - ratio * balanceSpan, currency);
    });
    const widestYLabel = Math.max(
      ...yTickLabels.map((label) => (
        typeof context.measureText === 'function'
          ? context.measureText(label).width
          : label.length * 7
      )),
      0,
    );
    const margin = {
      left: Math.min(
        Math.max(64, Math.ceil(widestYLabel) + 18),
        Math.max(64, cssWidth - 90),
      ),
      right: Math.min(28, Math.max(12, Math.floor(cssWidth * 0.06))),
      top: 24,
      bottom: 54,
    };
    const plotWidth = Math.max(1, cssWidth - margin.left - margin.right);
    const plotHeight = Math.max(1, cssHeight - margin.top - margin.bottom);
    const x = (value) => margin.left + ((value - minTime) / timeSpan) * plotWidth;
    const y = (value) => margin.top + ((maxBalance - value) / balanceSpan) * plotHeight;

    context.lineWidth = 1;
    context.strokeStyle = '#334155';
    context.fillStyle = '#94a3b8';
    context.textBaseline = 'middle';
    const yTicks = 5;
    for (let index = 0; index < yTicks; index += 1) {
      const ratio = index / (yTicks - 1);
      const value = maxBalance - ratio * balanceSpan;
      const yy = margin.top + ratio * plotHeight;
      context.beginPath();
      context.moveTo(margin.left, yy);
      context.lineTo(cssWidth - margin.right, yy);
      context.stroke();
      context.textAlign = 'right';
      context.fillText(formatBalance(value, currency), margin.left - 10, yy);
    }

    const xTicks = Math.min(6, Math.max(2, points.length));
    context.textBaseline = 'top';
    for (let index = 0; index < xTicks; index += 1) {
      const ratio = index / (xTicks - 1);
      const timestamp = minTime + ratio * timeSpan;
      const xx = margin.left + ratio * plotWidth;
      context.beginPath();
      context.moveTo(xx, margin.top);
      context.lineTo(xx, cssHeight - margin.bottom);
      context.stroke();
      context.textAlign = index === 0 ? 'left' : index === xTicks - 1 ? 'right' : 'center';
      context.fillText(formatDate(timestamp), xx, cssHeight - margin.bottom + 12);
    }

    context.strokeStyle = '#60a5fa';
    context.lineWidth = 2.5;
    context.beginPath();
    points.forEach((point, index) => {
      const xx = x(point.timestamp);
      const yy = y(point.balance);
      if (index === 0) context.moveTo(xx, yy);
      else context.lineTo(xx, yy);
    });
    context.stroke();
    const latest = points[points.length - 1];
    context.fillStyle = '#bfdbfe';
    context.strokeStyle = '#2563eb';
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x(latest.timestamp), y(latest.balance), 5, 0, Math.PI * 2);
    context.fill();
    context.stroke();
  }

  const api = {
    ACCOUNT_CHOICES,
    canonicalAccount,
    normalizeEquityPoints,
    preferredOrFirstAccountWithData,
    rowBalance,
    drawChart,
  };
  window.TradingJournalEquityCurve = api;

  const accountSelect = document.getElementById('journal-equity-account');
  const refreshButton = document.getElementById('journal-equity-refresh-btn');
  const canvas = document.getElementById('journal-equity-canvas');
  const summary = document.getElementById('journal-equity-summary');
  const stateElement = document.getElementById('journal-equity-state');
  if (!accountSelect || !refreshButton || !canvas || !summary || !stateElement) return;

  let rows = [];
  let balances = [];
  let equityCoverage = {};
  let snapshotCurrent = false;
  let loading = false;
  let reloadQueued = false;
  let forceReloadQueued = false;
  let resizeTimer = null;
  let userSelected = false;
  const REFRESH_URL = '/api/trading-journal/equity/refresh';
  const REFRESH_STATUS_URL = '/api/trading-journal/equity/refresh/status';
  const REFRESH_TIMEOUT_MS = 10 * 60 * 1000;
  const REFRESH_POLL_MS = 1250;

  const setChartState = (message, error = false) => {
    stateElement.textContent = message || '';
    stateElement.classList.toggle('error', error);
    stateElement.style.display = message ? 'flex' : 'none';
  };
  const clearChart = () => {
    const context = canvas.getContext('2d');
    context?.clearRect(0, 0, canvas.width, canvas.height);
  };
  const selectedChoice = () => ACCOUNT_CHOICES.find((choice) => choice.value === accountSelect.value) || ACCOUNT_CHOICES[0];
  const render = () => {
    const choice = selectedChoice();
    if (!snapshotCurrent) {
      summary.innerHTML = '';
      clearChart();
      return;
    }
    const points = normalizeEquityPoints(rows, choice.value, balances);
    if (!points.length) {
      summary.innerHTML = `<strong>${choice.label}</strong><span>0 points</span>`;
      const provenCount = Number(equityCoverage?.[choice.value]);
      if (Number.isFinite(provenCount) && provenCount === 0) {
        setChartState(`No equity data is available for ${choice.label}.`);
      } else {
        setChartState(
          `Current equity data for ${choice.label} could not be verified. Refresh the equity curve.`,
          true,
        );
      }
      clearChart();
      return;
    }
    const latest = points[points.length - 1];
    summary.innerHTML = `<strong>${choice.label}</strong><span>Current equity: ${formatBalance(latest.balance, latest.currency)}</span><span>${points.length} point${points.length === 1 ? '' : 's'}</span>`;
    setChartState('');
    drawChart(canvas, points);
  };

  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const responsePayload = async (response) => response.json().catch(() => ({}));
  const failureMessage = (payload, fallback) => (
    payload?.error || payload?.warning || payload?.detail || payload?.message || fallback
  );

  const waitForRefreshCompletion = async (deadline) => {
    while (Date.now() < deadline) {
      const response = await fetch(REFRESH_STATUS_URL, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      const payload = await responsePayload(response);
      if (response.status === 202 || payload?.pending === true) {
        setChartState('Building current equity data from Trading Journal.xlsx…');
        await sleep(REFRESH_POLL_MS);
        continue;
      }
      if (!response.ok || payload?.ok !== true) {
        throw new Error(failureMessage(payload, 'Equity cache build failed.'));
      }
      return payload;
    }
    throw new Error('Equity cache build timed out. Try Refresh Equity Curve again.');
  };

  const requestEquityRefresh = async (deadline) => {
    const response = await fetch(REFRESH_URL, {
      method: 'POST',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    const payload = await responsePayload(response);
    if (response.status === 202 || payload?.pending === true) {
      await waitForRefreshCompletion(deadline);
      return;
    }
    if (!response.ok || payload?.ok !== true) {
      throw new Error(failureMessage(payload, 'Unable to start the equity cache refresh.'));
    }
  };

  const fetchCurrentSnapshot = async (deadline) => {
    while (Date.now() < deadline) {
      const response = await fetch('/api/trading-journal', {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      const payload = await responsePayload(response);
      if (response.status === 202 || payload?.pending === true) {
        setChartState('Building current equity data from Trading Journal.xlsx…');
        await waitForRefreshCompletion(deadline);
        continue;
      }
      if (
        !response.ok
        || payload?.ok !== true
        || payload?.snapshot_current !== true
        || payload?.snapshot_stale === true
      ) {
        throw new Error(failureMessage(payload, 'Current authoritative equity data is unavailable.'));
      }
      if (
        payload?.equity_cache?.verified !== true
        || !payload?.equity_cache?.point_counts
      ) {
        throw new Error('The current equity snapshot has no verified point coverage.');
      }
      return payload;
    }
    throw new Error('Equity data refresh timed out. Try Refresh Equity Curve again.');
  };

  const load = async ({ forceRefresh = false } = {}) => {
    if (loading) {
      reloadQueued = true;
      forceReloadQueued = forceReloadQueued || forceRefresh;
      return;
    }
    loading = true;
    refreshButton.disabled = true;
    snapshotCurrent = false;
    setChartState(
      forceRefresh
        ? 'Refreshing equity data from Trading Journal.xlsx…'
        : 'Loading authoritative Trading Journal data…',
    );
    const deadline = Date.now() + REFRESH_TIMEOUT_MS;
    try {
      if (forceRefresh) await requestEquityRefresh(deadline);
      const payload = await fetchCurrentSnapshot(deadline);
      rows = Array.isArray(payload.items) ? payload.items : [];
      balances = Array.isArray(payload.balances)
        ? payload.balances
        : Array.isArray(payload?.stats?.balances)
          ? payload.stats.balances
          : [];
      equityCoverage = { ...payload.equity_cache.point_counts };
      snapshotCurrent = true;
      if (!userSelected) {
        accountSelect.value = preferredOrFirstAccountWithData(
          rows, accountSelect.value, balances
        );
      }
      try { localStorage.setItem(STORAGE_KEY, accountSelect.value); } catch {}
      render();
    } catch (error) {
      rows = [];
      balances = [];
      equityCoverage = {};
      snapshotCurrent = false;
      summary.innerHTML = '';
      clearChart();
      setChartState(error?.message || String(error), true);
    } finally {
      loading = false;
      refreshButton.disabled = false;
    }
    if (reloadQueued) {
      const queuedForce = forceReloadQueued;
      reloadQueued = false;
      forceReloadQueued = false;
      return load({ forceRefresh: queuedForce });
    }
  };

  try {
    const saved = canonicalAccount(localStorage.getItem(STORAGE_KEY));
    if (saved && ACCOUNT_CHOICES.some((choice) => choice.value === saved)) {
      accountSelect.value = saved;
    }
  } catch {}
  accountSelect.addEventListener('change', () => {
    userSelected = true;
    try { localStorage.setItem(STORAGE_KEY, accountSelect.value); } catch {}
    render();
  });
  refreshButton.addEventListener('click', () => load({ forceRefresh: true }));
  window.addEventListener(
    'trading-journal:data-changed',
    () => load({ forceRefresh: true }),
  );
  window.addEventListener('resize', () => {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(render, 120);
  });
  load();
})();
