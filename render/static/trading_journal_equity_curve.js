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
  const VERIFIED_BALANCE_PROVENANCE = new Set([
    'authoritative_after_minus_trade_result',
    'cashflow_anchor_plus_trade_results',
    'prior_verified_trade_balance_plus_results',
  ]);
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
  const equityReturnPct = (row) => {
    const provenance = String(row?.analysis_balance_before_trade_source || '').trim();
    if (!VERIFIED_BALANCE_PROVENANCE.has(provenance)) return null;
    const netProfit = asFinite(row?.net_profit);
    const tradeResult = netProfit !== null ? netProfit : asFinite(row?.realized_pnl);
    if (tradeResult === null) return null;
    const before = asFinite(row?.analysis_balance_before_trade);
    if (before === null || before <= 0) return null;
    return (tradeResult / before) * 100;
  };

  function normalizeEquityPoints(rows, selectedAccount, _balances = []) {
    const account = canonicalAccount(selectedAccount);
    const deduped = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row || typeof row !== 'object') return;
      if (canonicalAccount(row) !== account) return;
      if (isTrue(row.is_test_trade)) return;
      const type = rowType(row);
      if (type === 'monthly_aud_reval' || (!isTrade(row) && !isValidCashflowAnchor(row))) return;
      const timestamp = canonicalTimestamp(row);
      const returnPct = isTrade(row) ? equityReturnPct(row) : null;
      if (timestamp === null || (isTrade(row) && returnPct === null)) return;
      const stableIdentity = String(row.id || row.row_id || row.stable_row_id || '').trim();
      const cashflowBalance = isValidCashflowAnchor(row)
        ? asFinite(row.cashflow_new_balance)
        : null;
      const key = stableIdentity || (
        `${account}|${type}|${timestamp}|${isTrade(row) ? returnPct : cashflowBalance}`
      );
      deduped.set(key, {
        timestamp,
        eventType: type,
        returnPct,
        identity: key,
      });
    });
    const ordered = Array.from(deduped.values()).sort((a, b) => (
      a.timestamp - b.timestamp || a.identity.localeCompare(b.identity)
    ));
    const firstTrade = ordered.find((event) => event.eventType === 'trade');
    if (!firstTrade) return [];

    let currentIndex = 100;
    const points = [{
      timestamp: firstTrade.timestamp,
      value: currentIndex,
      eventType: 'baseline',
      identity: `baseline:${firstTrade.identity}`,
    }];
    ordered.forEach((event) => {
      if (event.timestamp < firstTrade.timestamp) return;
      if (event.eventType === 'trade') {
        currentIndex *= 1 + (event.returnPct / 100);
      }
      points.push({
        timestamp: event.timestamp,
        value: currentIndex,
        eventType: event.eventType,
        identity: event.identity,
      });
    });
    return points;
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

  const formatPercentage = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${number.toLocaleString('en-AU', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}%`;
  };
  const formatDate = (timestamp) => new Date(timestamp).toLocaleDateString('en-AU', {
    day: '2-digit', month: 'short', year: '2-digit', timeZone: 'Australia/Brisbane',
  });

  const formatDateTime = (timestamp) => new Date(timestamp).toLocaleString('en-AU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Australia/Brisbane',
  });

  const formatHoverPercentage = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${number.toLocaleString('en-AU', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 6,
    })}%`;
  };

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

  function buildChartGeometry(points, cssWidth, cssHeight, widestYLabel = 0) {
    if (!Array.isArray(points) || !points.length) return null;
    const width = Math.max(1, Number(cssWidth) || 1);
    const height = Math.max(1, Number(cssHeight) || 1);
    const minTime = Math.min(...points.map((point) => Number(point.timestamp)));
    const maxTime = Math.max(...points.map((point) => Number(point.timestamp)));
    const rawMin = Math.min(...points.map((point) => Number(point.value)));
    const rawMax = Math.max(...points.map((point) => Number(point.value)));
    if (![minTime, maxTime, rawMin, rawMax].every(Number.isFinite)) return null;
    const padding = Math.max(
      (rawMax - rawMin) * 0.08,
      Math.abs(rawMax || 1) * 0.005,
      1e-9,
    );
    const minValue = rawMin - padding;
    const maxValue = rawMax + padding;
    const margin = {
      left: Math.min(
        Math.max(64, Math.ceil(Number(widestYLabel) || 0) + 18),
        Math.max(64, width - 90),
      ),
      right: Math.min(28, Math.max(12, Math.floor(width * 0.06))),
      top: 24,
      bottom: 54,
    };
    return {
      cssWidth: width,
      cssHeight: height,
      margin,
      plotWidth: Math.max(1, width - margin.left - margin.right),
      plotHeight: Math.max(1, height - margin.top - margin.bottom),
      minTime,
      maxTime,
      timeSpan: Math.max(1, maxTime - minTime),
      minValue,
      maxValue,
      valueSpan: Math.max(1e-9, maxValue - minValue),
    };
  }

  const xForTimestamp = (geometry, timestamp) => (
    geometry.margin.left
    + ((Number(timestamp) - geometry.minTime) / geometry.timeSpan) * geometry.plotWidth
  );

  const yForValue = (geometry, value) => (
    geometry.margin.top
    + ((geometry.maxValue - Number(value)) / geometry.valueSpan) * geometry.plotHeight
  );

  const timestampForX = (geometry, x) => {
    const plotX = clamp(Number(x), geometry.margin.left, geometry.margin.left + geometry.plotWidth);
    return geometry.minTime + ((plotX - geometry.margin.left) / geometry.plotWidth) * geometry.timeSpan;
  };

  const valueForY = (geometry, y) => {
    const plotY = clamp(Number(y), geometry.margin.top, geometry.margin.top + geometry.plotHeight);
    return geometry.maxValue - ((plotY - geometry.margin.top) / geometry.plotHeight) * geometry.valueSpan;
  };

  function nearestActualPoint(points, timestamp) {
    const candidates = (Array.isArray(points) ? points : []).filter((point) => (
      point && point.eventType !== 'baseline' && Number.isFinite(Number(point.timestamp))
    ));
    if (!candidates.length) return null;
    let low = 0;
    let high = candidates.length - 1;
    const target = Number(timestamp);
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (Number(candidates[middle].timestamp) < target) low = middle + 1;
      else high = middle;
    }
    const right = candidates[low];
    const left = low > 0 ? candidates[low - 1] : null;
    if (!left) return right;
    return Math.abs(Number(left.timestamp) - target) <= Math.abs(Number(right.timestamp) - target)
      ? left
      : right;
  }

  function sizeCanvas(canvas, cssWidth, cssHeight, dpr) {
    if (!canvas) return;
    canvas.style.width = '100%';
    canvas.style.height = `${cssHeight}px`;
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
  }

  function clearCanvas(canvas) {
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    if (typeof context.setTransform === 'function') context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);
  }

  function drawChart(canvas, points) {
    if (!canvas || !points.length) return null;
    // Keep the canvas out of CSS intrinsic sizing. An old pixel width here
    // makes the one-column mobile grid retain the previous desktop width.
    const rect = canvas.getBoundingClientRect();
    const cssWidth = Math.max(1, Math.floor(rect.width || canvas.clientWidth || 900));
    const cssHeight = Math.max(360, Math.floor(rect.height || canvas.clientHeight || 440));
    const dpr = Math.max(1, Number(window.devicePixelRatio) || 1);
    sizeCanvas(canvas, cssWidth, cssHeight, dpr);
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, cssWidth, cssHeight);

    let geometry = buildChartGeometry(points, cssWidth, cssHeight);
    if (!geometry) return null;
    context.font = '12px system-ui, sans-serif';
    const yTickLabels = Array.from({ length: 5 }, (_unused, index) => {
      const ratio = index / 4;
      return formatPercentage(geometry.maxValue - ratio * geometry.valueSpan);
    });
    const widestYLabel = Math.max(
      ...yTickLabels.map((label) => (
        typeof context.measureText === 'function'
          ? context.measureText(label).width
          : label.length * 7
      )),
      0,
    );
    geometry = buildChartGeometry(points, cssWidth, cssHeight, widestYLabel);
    const { margin, plotWidth, plotHeight } = geometry;
    const x = (value) => xForTimestamp(geometry, value);
    const y = (value) => yForValue(geometry, value);

    context.lineWidth = 1;
    context.strokeStyle = '#334155';
    context.fillStyle = '#94a3b8';
    context.textBaseline = 'middle';
    const yTicks = 5;
    for (let index = 0; index < yTicks; index += 1) {
      const ratio = index / (yTicks - 1);
      const value = geometry.maxValue - ratio * geometry.valueSpan;
      const yy = margin.top + ratio * plotHeight;
      context.beginPath();
      context.moveTo(margin.left, yy);
      context.lineTo(cssWidth - margin.right, yy);
      context.stroke();
      context.textAlign = 'right';
      context.fillText(formatPercentage(value), margin.left - 10, yy);
    }

    const xTicks = Math.min(6, Math.max(2, points.length));
    context.textBaseline = 'top';
    for (let index = 0; index < xTicks; index += 1) {
      const ratio = index / (xTicks - 1);
      const timestamp = geometry.minTime + ratio * geometry.timeSpan;
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
      const yy = y(point.value);
      if (index === 0) context.moveTo(xx, yy);
      else context.lineTo(xx, yy);
    });
    context.stroke();
    const latest = points[points.length - 1];
    context.fillStyle = '#bfdbfe';
    context.strokeStyle = '#2563eb';
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x(latest.timestamp), y(latest.value), 5, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    return { ...geometry, dpr };
  }

  function drawHoverOverlay(canvas, geometry, points, pointerX, pointerY) {
    if (!canvas || !geometry) return null;
    const { margin, plotWidth, plotHeight, cssWidth, cssHeight, dpr } = geometry;
    sizeCanvas(canvas, cssWidth, cssHeight, dpr);
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, cssWidth, cssHeight);
    const x = Number(pointerX);
    const y = Number(pointerY);
    const inside = (
      Number.isFinite(x)
      && Number.isFinite(y)
      && x >= margin.left
      && x <= margin.left + plotWidth
      && y >= margin.top
      && y <= margin.top + plotHeight
    );
    if (!inside) return null;

    const cursorTimestamp = timestampForX(geometry, x);
    const cursorValue = valueForY(geometry, y);
    const nearest = nearestActualPoint(points, cursorTimestamp);
    const nearestX = nearest ? xForTimestamp(geometry, nearest.timestamp) : null;
    const nearestY = nearest ? yForValue(geometry, nearest.value) : null;

    context.strokeStyle = 'rgba(148, 163, 184, 0.82)';
    context.lineWidth = 1;
    context.setLineDash?.([4, 4]);
    context.beginPath();
    context.moveTo(x, margin.top);
    context.lineTo(x, margin.top + plotHeight);
    context.moveTo(margin.left, y);
    context.lineTo(margin.left + plotWidth, y);
    context.stroke();
    context.setLineDash?.([]);

    if (nearest) {
      context.fillStyle = '#f8fafc';
      context.strokeStyle = '#2563eb';
      context.lineWidth = 2;
      context.beginPath();
      context.arc(nearestX, nearestY, 5, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    }

    context.font = '12px system-ui, sans-serif';
    context.textBaseline = 'middle';
    const measure = (text) => (
      typeof context.measureText === 'function' ? context.measureText(text).width : text.length * 7
    );
    const drawBadge = (text, left, top, width) => {
      context.fillStyle = '#334155';
      context.fillRect(left, top, width, 24);
      context.fillStyle = '#f8fafc';
      context.textAlign = 'center';
      context.fillText(text, left + width / 2, top + 12, Math.max(1, width - 10));
    };

    const xText = `${formatDateTime(cursorTimestamp)} Brisbane`;
    const availableCanvasWidth = Math.max(1, cssWidth - 8);
    const xBadgeWidth = Math.min(availableCanvasWidth, Math.ceil(measure(xText)) + 14);
    const xBadgeLeft = clamp(
      x - xBadgeWidth / 2,
      4,
      cssWidth - 4 - xBadgeWidth,
    );
    drawBadge(xText, xBadgeLeft, margin.top + plotHeight + 5, xBadgeWidth);

    const yText = formatHoverPercentage(cursorValue);
    const yBadgeWidth = Math.min(plotWidth, Math.ceil(measure(yText)) + 14);
    const yBadgeLeft = margin.left + plotWidth - yBadgeWidth;
    const yBadgeTop = clamp(y - 12, margin.top, margin.top + plotHeight - 24);
    drawBadge(yText, yBadgeLeft, yBadgeTop, yBadgeWidth);

    if (nearest) {
      const tooltipLines = [
        'Nearest actual point',
        `${formatDateTime(nearest.timestamp)} Brisbane`,
        `Equity index: ${formatHoverPercentage(nearest.value)}`,
      ];
      const tooltipWidth = Math.min(
        availableCanvasWidth,
        Math.max(...tooltipLines.map(measure)) + 18,
      );
      const tooltipHeight = 64;
      const tooltipLeft = clamp(
        x + 12,
        4,
        cssWidth - 4 - tooltipWidth,
      );
      const tooltipTop = clamp(
        y - tooltipHeight - 12,
        margin.top,
        margin.top + plotHeight - tooltipHeight,
      );
      context.fillStyle = 'rgba(15, 23, 42, 0.96)';
      context.fillRect(tooltipLeft, tooltipTop, tooltipWidth, tooltipHeight);
      context.fillStyle = '#e2e8f0';
      context.textAlign = 'left';
      const tooltipTextWidth = Math.max(1, tooltipWidth - 18);
      context.fillText(tooltipLines[0], tooltipLeft + 9, tooltipTop + 14, tooltipTextWidth);
      context.fillText(tooltipLines[1], tooltipLeft + 9, tooltipTop + 32, tooltipTextWidth);
      context.fillText(tooltipLines[2], tooltipLeft + 9, tooltipTop + 50, tooltipTextWidth);
    }

    return { cursorTimestamp, cursorValue, nearest };
  }

  const api = {
    ACCOUNT_CHOICES,
    canonicalAccount,
    normalizeEquityPoints,
    preferredOrFirstAccountWithData,
    equityReturnPct,
    formatPercentage,
    formatDateTime,
    formatHoverPercentage,
    buildChartGeometry,
    xForTimestamp,
    yForValue,
    timestampForX,
    valueForY,
    nearestActualPoint,
    drawChart,
    drawHoverOverlay,
  };
  window.TradingJournalEquityCurve = api;

  const accountSelect = document.getElementById('journal-equity-account');
  const refreshButton = document.getElementById('journal-equity-refresh-btn');
  const canvas = document.getElementById('journal-equity-canvas');
  const overlayCanvas = document.getElementById('journal-equity-overlay-canvas');
  const hoverLive = document.getElementById('journal-equity-hover-live');
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
  let activePoints = [];
  let activeGeometry = null;
  const REFRESH_URL = '/api/trading-journal/equity/refresh';
  const REFRESH_STATUS_URL = '/api/trading-journal/equity/refresh/status';
  const REFRESH_TIMEOUT_MS = 10 * 60 * 1000;
  const REFRESH_POLL_MS = 1250;
  const EQUITY_DATA_CHANNEL_NAME = 'trading-journal-equity-data';
  let equityDataChannel = null;

  const setChartState = (message, error = false) => {
    stateElement.textContent = message || '';
    stateElement.classList.toggle('error', error);
    stateElement.style.display = message ? 'flex' : 'none';
  };
  const clearChart = () => {
    clearCanvas(canvas);
    clearCanvas(overlayCanvas);
    activePoints = [];
    activeGeometry = null;
    if (hoverLive) hoverLive.textContent = '';
  };

  const clearHover = () => {
    clearCanvas(overlayCanvas);
    if (hoverLive) hoverLive.textContent = '';
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
    const provenCount = Number(equityCoverage?.[choice.value]);
    if (!Number.isFinite(provenCount) || provenCount !== points.length) {
      summary.innerHTML = `<strong>${choice.label}</strong><span>${points.length} unverified point${points.length === 1 ? '' : 's'}</span>`;
      setChartState(
        `Current equity data for ${choice.label} could not be verified. Refresh the equity curve.`,
        true,
      );
      clearChart();
      return;
    }
    if (!points.length) {
      summary.innerHTML = `<strong>${choice.label}</strong><span>0 points</span>`;
      setChartState(`No equity data is available for ${choice.label}.`);
      clearChart();
      return;
    }
    const latest = points[points.length - 1];
    summary.innerHTML = `<strong>${choice.label}</strong><span>Current equity index: ${formatPercentage(latest.value)}</span><span>${points.length} point${points.length === 1 ? '' : 's'}</span>`;
    setChartState('');
    activePoints = points;
    activeGeometry = drawChart(canvas, points);
    clearHover();
  };

  const pointerPosition = (event) => {
    const source = event?.touches?.[0] || event?.changedTouches?.[0] || event;
    const rect = overlayCanvas?.getBoundingClientRect?.();
    if (!source || !rect) return null;
    const widthRatio = rect.width ? activeGeometry.cssWidth / rect.width : 1;
    const heightRatio = rect.height ? activeGeometry.cssHeight / rect.height : 1;
    return {
      x: (Number(source.clientX) - rect.left) * widthRatio,
      y: (Number(source.clientY) - rect.top) * heightRatio,
    };
  };

  const updateHover = (event) => {
    if (!overlayCanvas || !activeGeometry || !activePoints.length) return;
    const currentDpr = Math.max(1, Number(window.devicePixelRatio) || 1);
    if (currentDpr !== activeGeometry.dpr) render();
    const position = pointerPosition(event);
    if (!position || !activeGeometry) {
      clearHover();
      return;
    }
    const hover = drawHoverOverlay(
      overlayCanvas,
      activeGeometry,
      activePoints,
      position.x,
      position.y,
    );
    if (hoverLive) {
      hoverLive.textContent = hover
        ? (
          `Cursor ${formatDateTime(hover.cursorTimestamp)} Brisbane, `
          + `${formatHoverPercentage(hover.cursorValue)}. `
          + (hover.nearest
            ? `Nearest actual point ${formatDateTime(hover.nearest.timestamp)} Brisbane, ${formatHoverPercentage(hover.nearest.value)}.`
            : 'No actual point is available.')
        )
        : '';
    }
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
  try {
    if (typeof window.BroadcastChannel === 'function') {
      equityDataChannel = new window.BroadcastChannel(EQUITY_DATA_CHANNEL_NAME);
      equityDataChannel.addEventListener(
        'message',
        () => load({ forceRefresh: true }),
      );
    }
  } catch {}
  window.addEventListener(
    'trading-journal:data-changed',
    () => load({ forceRefresh: true }),
  );
  window.addEventListener('resize', () => {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(render, 120);
  });
  if (overlayCanvas) {
    overlayCanvas.addEventListener('pointerdown', updateHover);
    overlayCanvas.addEventListener('pointermove', updateHover);
    overlayCanvas.addEventListener('pointerleave', clearHover);
    overlayCanvas.addEventListener('pointercancel', clearHover);
    overlayCanvas.addEventListener('mousemove', updateHover);
    overlayCanvas.addEventListener('mouseleave', clearHover);
    overlayCanvas.addEventListener('touchstart', updateHover, { passive: true });
    overlayCanvas.addEventListener('touchmove', updateHover, { passive: true });
    overlayCanvas.addEventListener('touchend', clearHover);
    overlayCanvas.addEventListener('touchcancel', clearHover);
  }
  window.addEventListener('beforeunload', () => {
    try { equityDataChannel?.close(); } catch {}
  });
  load();
})();
