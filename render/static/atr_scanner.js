(() => {
  'use strict';

  const get = (id) => document.getElementById(id);
  const controls = {
    rankTimeframe: get('scanner-rank-timeframe'),
    topN: get('scanner-top-n'),
    atrLength: get('scanner-atr-length'),
    minTurnover: get('scanner-min-turnover'),
    maxSpread: get('scanner-max-spread'),
    depthBand: get('scanner-depth-band'),
    minBidDepth: get('scanner-min-bid-depth'),
    minAskDepth: get('scanner-min-ask-depth'),
    exclusions: get('scanner-exclusions'),
  };
  const saveButton = get('scanner-save');
  const resetButton = get('scanner-reset');
  const refreshButton = get('scanner-refresh');
  const actionStatus = get('scanner-action-status');
  const autoStatus = get('scanner-auto-status');
  const progressBar = get('scanner-progress-bar');
  const progressText = get('scanner-progress-text');
  const basis = get('scanner-basis');
  const qualifiedBody = get('scanner-qualified-body');
  const excludedBody = get('scanner-excluded-body');
  const qualifiedEmpty = get('scanner-qualified-empty');
  const excludedEmpty = get('scanner-excluded-empty');
  const qualifiedTab = get('scanner-qualified-tab');
  const excludedTab = get('scanner-excluded-tab');
  const qualifiedPanel = get('scanner-qualified-panel');
  const excludedPanel = get('scanner-excluded-panel');

  const TIMEFRAMES = ['1m', '5m', '1h', '1D', '1W', '1Mo'];
  const ATR_EXCLUSION_LABELS = {
    insufficient_atr_history: 'Insufficient valid closed-candle ATR history',
    missing_invalid_market_data: 'Missing, invalid, crossed, or stale market data',
    transient_upstream_failure: 'Transient upstream failure',
  };
  let settings = null;
  let lastSnapshot = null;
  let refreshRequestInFlight = null;
  let statusRequestInFlight = null;
  let progressTimer = null;
  let autoTimer = null;

  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const finite = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const formatAtr = (value) => {
    const number = finite(value);
    return number === null ? 'N/A' : `${number.toFixed(5)}%`;
  };
  const formatPct = (value) => {
    const number = finite(value);
    return number === null ? 'N/A' : `${number.toFixed(4)}%`;
  };
  const formatMoney = (value) => {
    const number = finite(value);
    return number === null
      ? 'N/A'
      : number.toLocaleString(undefined, { maximumFractionDigits: 0 });
  };
  const formatTime = (value) => {
    if (!value) return 'N/A';
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString() : 'N/A';
  };

  const fetchJson = async (url, options = {}) => {
    const response = await fetch(url, options);
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = { detail: text }; }
    if (!response.ok) {
      const error = new Error(payload.detail || payload?.refresh_error?.message || `${response.status} ${response.statusText}`);
      error.payload = payload;
      error.status = response.status;
      throw error;
    }
    return payload;
  };

  const setActionStatus = (message, state = '') => {
    if (!actionStatus) return;
    actionStatus.textContent = message || '';
    actionStatus.className = `status${state ? ` ${state}` : ''}`;
  };

  const setControlsDisabled = (disabled) => {
    [saveButton, resetButton, refreshButton].forEach((button) => {
      if (button) button.disabled = Boolean(disabled);
    });
  };

  const applySettings = (value) => {
    settings = { ...(value || {}) };
    if (controls.rankTimeframe) controls.rankTimeframe.value = String(settings.rank_timeframe || '1m');
    if (controls.topN) controls.topN.value = String(settings.top_n ?? 10);
    if (controls.atrLength) controls.atrLength.value = String(settings.atr_length ?? 14);
    if (controls.minTurnover) controls.minTurnover.value = String(settings.min_turnover_usdt ?? 20000000);
    if (controls.maxSpread) controls.maxSpread.value = String(settings.max_spread_pct ?? 0.1);
    if (controls.depthBand) controls.depthBand.value = String(settings.depth_band_pct ?? 0.1);
    if (controls.minBidDepth) controls.minBidDepth.value = String(settings.min_bid_depth_usdt ?? 25000);
    if (controls.minAskDepth) controls.minAskDepth.value = String(settings.min_ask_depth_usdt ?? 25000);
    if (controls.exclusions) {
      const values = Array.isArray(settings.manual_exclusions) ? settings.manual_exclusions : [];
      controls.exclusions.value = values.join('\n');
    }
    if (autoStatus) autoStatus.textContent = `Automatic refresh: every ${settings.auto_refresh_seconds ?? 60} seconds`;
    scheduleAutomaticRefresh();
  };

  const collectSettings = () => ({
    rank_timeframe: String(controls.rankTimeframe?.value || ''),
    top_n: Number(controls.topN?.value),
    atr_length: Number(controls.atrLength?.value),
    min_turnover_usdt: Number(controls.minTurnover?.value),
    max_spread_pct: Number(controls.maxSpread?.value),
    depth_band_pct: Number(controls.depthBand?.value),
    min_bid_depth_usdt: Number(controls.minBidDepth?.value),
    min_ask_depth_usdt: Number(controls.minAskDepth?.value),
    manual_exclusions: String(controls.exclusions?.value || ''),
  });

  const rankRows = (rows, timeframe, topN) => {
    const selected = TIMEFRAMES.includes(timeframe) ? timeframe : '1m';
    return (Array.isArray(rows) ? rows : [])
      .filter((row) => finite(row?.atr_pct?.[selected]) !== null)
      .slice()
      .sort((left, right) => {
        const difference = finite(right?.atr_pct?.[selected]) - finite(left?.atr_pct?.[selected]);
        if (difference) return difference;
        const leftSymbol = String(left?.symbol || '');
        const rightSymbol = String(right?.symbol || '');
        return leftSymbol < rightSymbol ? -1 : (leftSymbol > rightSymbol ? 1 : 0);
      })
      .slice(0, Math.max(1, Number(topN) || 10))
      .map((row, index) => ({ ...row, rank: index + 1 }));
  };

  const rowDataState = (row, timeframe, snapshotStale = false) => {
    if (snapshotStale || row?.stale) return 'Stale';
    const selectedStatus = String(row?.atr_status?.[timeframe] || '').toLowerCase();
    if (selectedStatus === 'error') return 'Error';
    if (finite(row?.atr_pct?.[timeframe]) === null) return selectedStatus === 'unavailable' ? 'N/A' : 'Loading';
    const statuses = Object.values(row?.atr_status || {}).map((value) => String(value).toLowerCase());
    if (statuses.some((value) => value === 'error')) return 'Partial / error';
    if (statuses.some((value) => value === 'unavailable')) return 'Partial / N/A';
    return 'Fresh';
  };

  const excludedRowsFor = (snapshot, timeframe) => {
    const selected = TIMEFRAMES.includes(timeframe) ? timeframe : '1m';
    const baseRows = Array.isArray(snapshot?.base_excluded_rows)
      ? snapshot.base_excluded_rows
      : (Array.isArray(snapshot?.excluded_rows) ? snapshot.excluded_rows : []);
    const atrRows = (Array.isArray(snapshot?.qualified_rows) ? snapshot.qualified_rows : [])
      .filter((row) => finite(row?.atr_pct?.[selected]) === null)
      .map((row) => {
        const failed = String(row?.atr_status?.[selected] || '').toLowerCase() === 'error';
        const fallback = failed ? 'transient_upstream_failure' : 'insufficient_atr_history';
        const candidate = String(row?.atr_reason?.[selected] || fallback);
        const reason = Object.prototype.hasOwnProperty.call(ATR_EXCLUSION_LABELS, candidate) ? candidate : fallback;
        const label = ATR_EXCLUSION_LABELS[reason];
        return { ...row, liquidity_status: 'Excluded', reasons: [reason], reason_labels: [label] };
      });
    return [...baseRows, ...atrRows].sort((left, right) => {
      const leftSymbol = String(left?.symbol || '');
      const rightSymbol = String(right?.symbol || '');
      return leftSymbol < rightSymbol ? -1 : (leftSymbol > rightSymbol ? 1 : 0);
    });
  };

  const renderQualified = (snapshot) => {
    if (!qualifiedBody) return;
    const activeSettings = settings || snapshot?.settings || {};
    const rows = rankRows(
      snapshot?.qualified_rows,
      String(activeSettings.rank_timeframe || '1m'),
      Number(activeSettings.top_n || 10),
    );
    qualifiedBody.innerHTML = rows.map((row) => {
      const atrCells = TIMEFRAMES.map((timeframe) => `<td>${esc(formatAtr(row?.atr_pct?.[timeframe]))}</td>`).join('');
      const dataState = rowDataState(row, String(activeSettings.rank_timeframe || '1m'), Boolean(snapshot?.stale));
      return `<tr class="${dataState === 'Stale' ? 'stale' : ''}">
        <td>${row.rank}</td><td>${esc(row.symbol)}</td>${atrCells}
        <td>${esc(formatMoney(row.turnover24h_usdt))}</td><td>${esc(formatPct(row.spread_pct))}</td>
        <td>${esc(formatMoney(row.bid_depth_usdt))}</td><td>${esc(formatMoney(row.ask_depth_usdt))}</td>
        <td>${esc(row.liquidity_status || 'Qualified')}</td><td>${dataState}</td>
      </tr>`;
    }).join('');
    if (qualifiedEmpty) qualifiedEmpty.hidden = rows.length > 0;
  };

  const renderExcluded = (snapshot) => {
    if (!excludedBody) return;
    const activeSettings = settings || snapshot?.settings || {};
    const rows = excludedRowsFor(snapshot, String(activeSettings.rank_timeframe || '1m'));
    excludedBody.innerHTML = rows.map((row) => {
      const reasons = Array.isArray(row.reason_labels) ? row.reason_labels.join('; ') : 'Unknown';
      return `<tr><td class="left">${esc(row.symbol)}</td><td class="left reason-list">${esc(reasons)}</td>
        <td>${esc(formatMoney(row.turnover24h_usdt))}</td><td>${esc(formatPct(row.spread_pct))}</td>
        <td>${esc(formatMoney(row.bid_depth_usdt))}</td><td>${esc(formatMoney(row.ask_depth_usdt))}</td></tr>`;
    }).join('');
    if (excludedEmpty) excludedEmpty.hidden = rows.length > 0;
  };

  const renderBasis = (snapshot) => {
    if (!basis) return;
    const activeSettings = settings || snapshot?.settings || {};
    const selectedTimeframe = String(activeSettings.rank_timeframe || '1m');
    const excludedRows = excludedRowsFor(snapshot, selectedTimeframe);
    const counts = {};
    excludedRows.forEach((row) => (Array.isArray(row?.reasons) ? row.reasons : []).forEach((reason) => {
      counts[reason] = (counts[reason] || 0) + 1;
    }));
    const rankEligibleCount = rankRows(snapshot?.qualified_rows, selectedTimeframe, Number.MAX_SAFE_INTEGER).length;
    const reasonSummary = Object.entries(counts).map(([key, value]) => `${key}: ${value}`).join(', ') || 'none';
    basis.innerHTML = `
      <div><strong>Ranking</strong><br>Top ${esc(activeSettings.top_n ?? 10)} by ATR(${esc(activeSettings.atr_length ?? 14)}) ${esc(selectedTimeframe)}, last closed candle</div>
      <div><strong>Eligibility</strong><br>${esc(rankEligibleCount)} rank eligible / ${esc(snapshot?.liquidity_qualified_count ?? 0)} liquidity qualified</div>
      <div><strong>Thresholds</strong><br>Turnover ≥ ${esc(formatMoney(activeSettings.min_turnover_usdt))}; spread ≤ ${esc(formatPct(activeSettings.max_spread_pct))}; depth band ±${esc(formatPct(activeSettings.depth_band_pct))}</div>
      <div><strong>Depth</strong><br>Bid ≥ ${esc(formatMoney(activeSettings.min_bid_depth_usdt))}; ask ≥ ${esc(formatMoney(activeSettings.min_ask_depth_usdt))} USDT</div>
      <div><strong>Last successful update</strong><br>${esc(formatTime(snapshot?.updated_at))}</div>
      <div><strong>Excluded (${esc(excludedRows.length)})</strong><br>${esc(reasonSummary)}</div>`;
  };

  const renderProgress = (progress) => {
    const completed = Number(progress?.completed || 0);
    const total = Number(progress?.total || 0);
    const percentage = total > 0 ? Math.min(100, Math.max(0, completed / total * 100)) : (progress?.in_progress ? 8 : 0);
    if (progressBar) progressBar.style.width = `${percentage}%`;
    if (progressText) {
      const counter = total > 0 ? ` (${completed}/${total})` : '';
      progressText.textContent = `${progress?.detail || 'Idle'}${counter}`;
    }
    setControlsDisabled(Boolean(progress?.in_progress));
  };

  const renderSnapshot = (snapshot) => {
    if (!snapshot || typeof snapshot !== 'object') return;
    if (snapshot.settings && !settings) applySettings(snapshot.settings);
    lastSnapshot = snapshot;
    renderProgress(snapshot.progress || {});
    renderQualified(snapshot);
    renderExcluded(snapshot);
    renderBasis(snapshot);

    if (snapshot.stale) {
      const age = finite(snapshot.stale_age_seconds);
      setActionStatus(`Stale last-known-good result${age === null ? '' : ` (${Math.round(age)}s old)`}: ${snapshot?.refresh_error?.message || 'refresh failed'}`, 'stale');
    } else if (snapshot.state === 'partial') {
      setActionStatus('Refresh completed with scoped stale/error data. Inspect row states and excluded reasons.', 'stale');
    } else if (snapshot.ok) {
      setActionStatus(`Updated ${formatTime(snapshot.updated_at)} from public live Bybit V5 market data.`);
    } else if (snapshot.state === 'error') {
      setActionStatus(snapshot?.refresh_error?.message || 'Scanner refresh failed.', 'error');
    }
  };

  const pollStatus = async () => {
    if (statusRequestInFlight) return statusRequestInFlight;
    statusRequestInFlight = (async () => {
      try {
        const snapshot = await fetchJson('/api/atr-scanner/status');
        renderSnapshot(snapshot);
        if (snapshot?.progress?.in_progress) startProgressPolling();
        else stopProgressPolling();
        return snapshot;
      } catch (error) {
        const payload = error?.payload;
        if (payload && typeof payload === 'object') renderSnapshot(payload);
        setActionStatus(error?.message || 'Failed to load scanner status.', 'error');
        stopProgressPolling();
        return null;
      } finally {
        statusRequestInFlight = null;
      }
    })();
    return statusRequestInFlight;
  };

  const startProgressPolling = () => {
    if (progressTimer) return;
    progressTimer = setInterval(pollStatus, 2000);
  };
  const stopProgressPolling = () => {
    if (progressTimer) clearInterval(progressTimer);
    progressTimer = null;
  };

  const requestRefresh = async (manual = true) => {
    if (refreshRequestInFlight) return refreshRequestInFlight;
    refreshRequestInFlight = (async () => {
      setActionStatus(manual ? 'Manual refresh requested.' : 'Automatic refresh requested.');
      try {
        const response = await fetchJson('/api/atr-scanner/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ manual: Boolean(manual) }),
        });
        if (response.shared_in_flight) {
          setActionStatus('Joined the refresh already in progress.');
        }
        renderProgress(response.progress || { in_progress: Boolean(response.started), detail: 'Refresh queued.' });
        startProgressPolling();
        await pollStatus();
        return response;
      } catch (error) {
        setActionStatus(error?.message || 'Refresh request failed.', 'error');
        return null;
      } finally {
        refreshRequestInFlight = null;
      }
    })();
    return refreshRequestInFlight;
  };

  const saveSettings = async () => {
    setControlsDisabled(true);
    setActionStatus('Saving settings and rebuilding the scanner view.');
    try {
      const payload = await fetchJson('/api/atr-scanner/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(collectSettings()),
      });
      applySettings(payload.settings || {});
      startProgressPolling();
      await pollStatus();
    } catch (error) {
      setActionStatus(error?.message || 'Settings were rejected.', 'error');
    } finally {
      if (!lastSnapshot?.progress?.in_progress) setControlsDisabled(false);
    }
  };

  const resetSettings = async () => {
    setControlsDisabled(true);
    setActionStatus('Resetting scanner settings to defaults.');
    try {
      const payload = await fetchJson('/api/atr-scanner/settings/reset', { method: 'POST' });
      applySettings(payload.settings || {});
      startProgressPolling();
      await pollStatus();
    } catch (error) {
      setActionStatus(error?.message || 'Settings reset failed.', 'error');
    } finally {
      if (!lastSnapshot?.progress?.in_progress) setControlsDisabled(false);
    }
  };

  const switchTab = (showExcluded) => {
    if (qualifiedPanel) qualifiedPanel.hidden = showExcluded;
    if (excludedPanel) excludedPanel.hidden = !showExcluded;
    qualifiedTab?.setAttribute('aria-selected', showExcluded ? 'false' : 'true');
    excludedTab?.setAttribute('aria-selected', showExcluded ? 'true' : 'false');
  };

  const scheduleAutomaticRefresh = () => {
    if (autoTimer) clearInterval(autoTimer);
    const seconds = Math.max(30, Number(settings?.auto_refresh_seconds) || 60);
    autoTimer = setInterval(() => requestRefresh(false), seconds * 1000);
  };

  saveButton?.addEventListener('click', saveSettings);
  resetButton?.addEventListener('click', resetSettings);
  refreshButton?.addEventListener('click', () => requestRefresh(true));
  qualifiedTab?.addEventListener('click', () => switchTab(false));
  excludedTab?.addEventListener('click', () => switchTab(true));
  controls.rankTimeframe?.addEventListener('change', () => {
    if (settings) settings.rank_timeframe = controls.rankTimeframe.value;
    if (lastSnapshot) {
      renderQualified(lastSnapshot);
      renderExcluded(lastSnapshot);
      renderBasis(lastSnapshot);
    }
  });
  controls.topN?.addEventListener('change', () => {
    if (settings) settings.top_n = Number(controls.topN.value);
    if (lastSnapshot) {
      renderQualified(lastSnapshot);
      renderBasis(lastSnapshot);
    }
  });

  if (typeof window !== 'undefined') {
    window.__atrScannerTestHooks = { rankRows, formatAtr, finite, rowDataState, excludedRowsFor };
  }

  (async () => {
    try {
      const payload = await fetchJson('/api/atr-scanner/settings');
      applySettings(payload.settings || payload.defaults || {});
      const snapshot = await pollStatus();
      if (!snapshot || snapshot.state === 'not_started') await requestRefresh(false);
    } catch (error) {
      setActionStatus(error?.message || 'Scanner initialization failed.', 'error');
    }
  })();

  window.addEventListener('beforeunload', () => {
    stopProgressPolling();
    if (autoTimer) clearInterval(autoTimer);
  });
})();
