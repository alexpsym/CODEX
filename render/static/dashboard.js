(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const status = document.getElementById('status');
  const scriptsGrid = document.getElementById('scripts-grid');
  const exitButtonSlot = document.getElementById('exit-button-slot');

  const workspaceTitle = document.getElementById('dashboard-workspace-title');
  const workspaceStatus = document.getElementById('dashboard-workspace-status');
  const workspaceEmpty = document.getElementById('dashboard-workspace-empty');
  const workspaceFrame = document.getElementById('dashboard-workspace-frame');

  const bounceTradersPanel = document.getElementById('running-bounce-traders-panel');
  const bounceTradersStatus = document.getElementById('running-bounce-traders-status');
  const bounceTradersBody = document.getElementById('running-bounce-traders-body');
  const bounceTradersEmpty = document.getElementById('running-bounce-traders-empty');
  const BOUNCE_TRADER_LOCAL_ENABLED = document.body?.dataset?.dashboardProfile === 'local';
  const BOUNCE_TRADER_BASE = '/apps/bybit_trigger_bounce_trader';
  const pineStatus = document.getElementById('pine-status');
  const pineFiles = document.getElementById('pine-files');
  const pineFallback = document.getElementById('pine-fallback');

  
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

  const MAIN_WORKSPACE_URL = '/merged/open-orders';
  const scriptTabWindows = new Map();

  let scriptsInFlight = null;
  let oandaInFlight = null;
  let bounceTradersInFlight = null;

  let scriptsTimer = null;
  let oandaTimer = null;
  let oandaSecondTimer = null;
  let workspaceResizeObserver = null;
  let workspaceMutationObserver = null;
  let workspaceResizeFrame = null;

  const POLL_MS = {
    scripts: 15_000,
    oandaInactivity: 30_000,
    hiddenMultiplier: 3,
  };

  let oandaState = null;
  let oandaExpanded = false;
  let scriptsState = [];

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
    const fetchOptions = { cache: 'no-store', ...options };
    const res = await fetch(url, fetchOptions);
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
      const detailMessage = typeof detail === 'object' && detail
        ? (detail.message || detail.error || '')
        : '';
      const message = typeof detail === 'string' && detail.trim()
        ? detail.trim()
        : String(bodyJson?.message || bodyJson?.error || detailMessage || '').trim()
          || `${options.method || 'GET'} ${url} failed: ${res.status} ${(bodyText || res.statusText || '').trim()}`;
      const error = new Error(message);
      error.payload = bodyJson;
      error.status = res.status;
      throw error;
    }
    if (bodyJson !== null) return bodyJson;
    return bodyText ? JSON.parse(bodyText) : {};
  };

  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  const setWorkspaceMeta = (title, message, isErr = false) => {
    if (workspaceTitle) workspaceTitle.textContent = title || 'Orders / Positions';
    if (workspaceStatus) {
      workspaceStatus.textContent = message || '';
      workspaceStatus.style.color = isErr ? '#fca5a5' : '#94a3b8';
    }
  };

  const showOrdersWorkspace = () => {
    if (workspaceEmpty) workspaceEmpty.hidden = true;
    if (workspaceFrame) workspaceFrame.hidden = false;
  };

  const ensureOrdersWorkspace = () => {
    if (!workspaceFrame) return;
    const currentSrc = String(workspaceFrame.getAttribute('src') || '');
    if (!currentSrc || currentSrc === 'about:blank') {
      const glue = MAIN_WORKSPACE_URL.includes('?') ? '&' : '?';
      workspaceFrame.src = `${MAIN_WORKSPACE_URL}${glue}_dashboard=1&_dash_ts=${Date.now()}`;
    }
    showOrdersWorkspace();
    setWorkspaceMeta('Orders / Positions', 'Open orders and positions are shown here.', false);
  };

  const cleanupWorkspaceHeightObservers = () => {
    if (workspaceResizeObserver) {
      workspaceResizeObserver.disconnect();
      workspaceResizeObserver = null;
    }
    if (workspaceMutationObserver) {
      workspaceMutationObserver.disconnect();
      workspaceMutationObserver = null;
    }
    if (workspaceResizeFrame) {
      const cancelFrame = typeof window.cancelAnimationFrame === 'function'
        ? window.cancelAnimationFrame.bind(window)
        : window.clearTimeout.bind(window);
      cancelFrame(workspaceResizeFrame);
      workspaceResizeFrame = null;
    }
  };

  const workspaceFrameDocument = () => {
    if (!workspaceFrame) return null;
    try {
      return workspaceFrame.contentDocument || workspaceFrame.contentWindow?.document || null;
    } catch (_err) {
      return null;
    }
  };

  const syncWorkspaceFrameHeight = () => {
    const doc = workspaceFrameDocument();
    if (!workspaceFrame || !doc) return;
    const body = doc.body;
    const html = doc.documentElement;
    if (!body || !html) return;
    const contentHeight = Math.max(
      body.scrollHeight || 0,
      body.offsetHeight || 0,
      html.scrollHeight || 0,
      html.offsetHeight || 0,
    );
    if (!contentHeight) return;
    const nextHeight = Math.min(Math.max(Math.ceil(contentHeight), 180), 8000);
    const currentHeight = Number.parseFloat(String(workspaceFrame.style.height || workspaceFrame.getAttribute('height') || '0'));
    if (!Number.isFinite(currentHeight) || Math.abs(currentHeight - nextHeight) > 2) {
      workspaceFrame.style.height = `${nextHeight}px`;
    }
  };

  const scheduleWorkspaceFrameHeightSync = () => {
    if (!workspaceFrame || workspaceResizeFrame) return;
    const requestFrame = typeof window.requestAnimationFrame === 'function'
      ? window.requestAnimationFrame.bind(window)
      : window.setTimeout.bind(window);
    workspaceResizeFrame = requestFrame(() => {
      workspaceResizeFrame = null;
      syncWorkspaceFrameHeight();
    });
  };

  const installWorkspaceHeightSync = () => {
    cleanupWorkspaceHeightObservers();
    const doc = workspaceFrameDocument();
    if (!workspaceFrame || !doc || !doc.body) return;
    scheduleWorkspaceFrameHeightSync();
    const frameWindow = doc.defaultView || workspaceFrame.contentWindow || window;
    const FrameResizeObserver = frameWindow.ResizeObserver || window.ResizeObserver;
    const FrameMutationObserver = frameWindow.MutationObserver || window.MutationObserver;
    const FrameNode = frameWindow.Node || window.Node;
    const canObserveNode = (node) => {
      if (!node) return false;
      if (FrameNode && node instanceof FrameNode) return true;
      return typeof node.nodeType === 'number';
    };
    const observeNode = (observer, node, options = null) => {
      if (!observer || !canObserveNode(node)) return;
      try {
        if (options) observer.observe(node, options);
        else observer.observe(node);
      } catch (_err) {
        // Cross-frame reloads can invalidate nodes between lookup and observe.
      }
    };
    try {
      if (typeof FrameResizeObserver === 'function') {
        workspaceResizeObserver = new FrameResizeObserver(scheduleWorkspaceFrameHeightSync);
        observeNode(workspaceResizeObserver, doc.body);
        observeNode(workspaceResizeObserver, doc.documentElement);
      }
      if (typeof FrameMutationObserver === 'function') {
        workspaceMutationObserver = new FrameMutationObserver(scheduleWorkspaceFrameHeightSync);
        observeNode(workspaceMutationObserver, doc.body, {
          attributes: true,
          childList: true,
          subtree: true,
          characterData: true,
        });
      }
    } catch (_err) {
      cleanupWorkspaceHeightObservers();
    }
    window.setTimeout(scheduleWorkspaceFrameHeightSync, 250);
  };

  const renderScripts = () => {
    if (!scriptsGrid) return;
    scriptsGrid.innerHTML = '';
    if (exitButtonSlot) {
      exitButtonSlot.innerHTML = '';
    }
    scriptsState.forEach((item) => scriptsGrid.appendChild(makeScriptButton(item)));
    if (exitButtonSlot) {
      exitButtonSlot.appendChild(makeExitButton());
    } else {
      scriptsGrid.appendChild(makeExitButton());
    }
  };

  const makeExitButton = () => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'script-btn local-exit-btn';
    btn.textContent = 'Exit';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      setStatus('Exiting Local Trading Tools...');
      try {
        await fetchJson('/api/local-exit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: window.location.href }),
        });
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err || 'Exit request failed.');
        setStatus(detail, true);
        btn.disabled = false;
      }
    });
    return btn;
  };

  const scriptTabKey = (script) => String(script?.name || script?.id || '').trim();

  const scriptTabName = (script) => {
    const key = scriptTabKey(script).replace(/[^a-z0-9_-]+/gi, '_') || 'script';
    return `trading_tools_${key}`;
  };

  const scriptTabIsOpen = (script) => {
    const key = scriptTabKey(script);
    const tab = key ? scriptTabWindows.get(key) : null;
    if (!tab) return false;
    if (tab.closed) {
      scriptTabWindows.delete(key);
      return false;
    }
    return true;
  };

  const scriptOpenUrl = (script) => {
    const targetBase = String(script?.open_url || '').trim();
    if (!targetBase) return '';
    const glue = targetBase.includes('?') ? '&' : '?';
    return `${targetBase}${glue}_tab_ts=${Date.now()}`;
  };

  const openScriptTab = (script) => {
    const name = String(script?.name || '').trim();
    const key = scriptTabKey(script);
    const existing = key ? scriptTabWindows.get(key) : null;
    if (existing && !existing.closed) {
      existing.focus?.();
      setStatus(`${script.label || name} tab focused.`);
      renderScripts();
      return;
    }
    const target = scriptOpenUrl(script);
    if (!name || !target || !key) {
      setStatus('Script URL unavailable.', true);
      return;
    }
    const tab = window.open(target, scriptTabName(script));
    if (!tab) {
      setStatus(`Browser blocked the ${script.label || name} tab.`, true);
      return;
    }
    scriptTabWindows.set(key, tab);
    tab.focus?.();
    setStatus(`Opened ${script.label || name} in a new tab.`);
    renderScripts();
  };

  const scriptDotState = (script, processRunning, processStarting, processTitle) => {
    const lowerName = String(script?.name || '').trim().toLowerCase();
    if (lowerName === 'fxweekend') {
      const reportedHealth = String(script?.health_state || '').trim().toLowerCase();
      let healthState = ['green', 'amber', 'red', 'disabled'].includes(reportedHealth)
        ? reportedHealth
        : '';
      if (!healthState) {
        if (script?.enabled === false) {
          healthState = 'disabled';
        } else if (processStarting) {
          healthState = 'amber';
        } else if (
          processRunning
          && script?.heartbeat_fresh !== false
          && script?.operational === true
        ) {
          healthState = 'green';
        } else {
          healthState = 'red';
        }
      }

      const reason = String(script?.health_reason || script?.status_detail || '').trim();
      const titles = {
        green: 'Enabled process running with a fresh heartbeat',
        amber: processStarting
          ? 'FX Weekend is starting'
          : 'Process healthy; the latest cutoff or market outcome needs attention',
        red: !processRunning
          ? 'FX Weekend process stopped'
          : (script?.heartbeat_fresh === false ? 'FX Weekend heartbeat is stale' : 'FX Weekend execution failed'),
        disabled: 'FX Weekend is disabled',
      };
      const dotStates = {
        green: 'running',
        amber: 'starting',
        red: 'stopped',
        disabled: 'disabled',
      };
      return {
        dotState: dotStates[healthState],
        dotTitle: reason || titles[healthState],
        active: healthState === 'green' || (healthState === 'amber' && processRunning),
        healthState,
      };
    }
    if (lowerName === 'monitor') {
      const stopReason = String(script.last_start_error || script.last_exit_reason || '').trim();
      let dotState = processRunning ? 'running' : (processStarting ? 'starting' : 'stopped');
      let title = processRunning ? 'Alerts running' : (processStarting ? 'Alerts starting' : 'Alerts stopped');
      if (!processRunning && stopReason) {
        title = `Alerts stopped: ${stopReason}`;
      }
      if (typeof script.status_detail === 'string' && script.status_detail.trim()) {
        title = script.status_detail.trim();
      }
      return { dotState, dotTitle: title, active: processRunning };
    }
    if (lowerName === 'open-orders') {
      return { dotState: 'running', dotTitle: 'Shown on dashboard', active: true };
    }
    const tabOpen = scriptTabIsOpen(script);
    if (tabOpen) {
      return { dotState: 'running', dotTitle: 'Open in a tab', active: true };
    }
    return { dotState: 'stopped', dotTitle: 'Not open in a tab', active: false };
  };

  const makeScriptButton = (script) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'script-btn';
    btn.dataset.scriptName = String(script.name || '');

    const processRunning = script.running === true;
    const processStarting = script.starting === true;
    const processTitle = processRunning ? 'Process running' : (processStarting ? 'Process starting' : 'Process stopped');
    const { dotState, dotTitle, active, healthState } = scriptDotState(
      script,
      processRunning,
      processStarting,
      processTitle,
    );

    const name = document.createElement('div');
    name.className = 'script-name';
    const label = script.label || script.name;
    name.textContent = healthState === 'disabled' ? `${label} (Disabled)` : label;

    const dot = document.createElement('span');
    dot.className = `status-dot ${dotState}`;
    dot.title = dotTitle;
    dot.setAttribute('aria-label', dotTitle);
    if (active) {
      btn.classList.add('active-script');
    }
    btn.title = `${script.label || script.name} (${dotTitle})`;

    btn.appendChild(name);
    btn.appendChild(dot);

    btn.addEventListener('click', () => openScriptTab(script));
    btn.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      openScriptTab(script);
    });
    btn.addEventListener('auxclick', (event) => {
      if (event.button === 1) {
        event.preventDefault();
        openScriptTab(script);
      }
    });

    return btn;
  };

  const refreshScripts = async () => {
    if (scriptsInFlight) return scriptsInFlight;
    scriptsInFlight = (async () => {
      try {
        setStatus('Loading scripts...');
        const scripts = await fetchJson('/scripts');
        scriptsState = Array.isArray(scripts) ? scripts : [];
        renderScripts();
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
    if (statusValue === 'maintenance') {
      oandaHeadline.textContent = 'OANDA maintenance';
      oandaDetail.textContent = oandaExpanded ? 'Account inactivity status temporarily unavailable.' : '';
      if (oandaCountdown) oandaCountdown.textContent = 'Maintenance';
      return;
    }
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
    if (!oandaHeadline) return null;
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

  const setBounceTradersStatus = (message, isError = false) => {
    if (!bounceTradersStatus) return;
    bounceTradersStatus.textContent = message || '';
    bounceTradersStatus.style.color = isError ? '#fca5a5' : '#94a3b8';
  };

  const renderBounceTraders = (sessions) => {
    if (!bounceTradersBody) return;
    bounceTradersBody.innerHTML = '';
    const rows = Array.isArray(sessions) ? sessions : [];
    rows.forEach((session) => {
      const tr = document.createElement('tr');
      ['id', 'broker', 'instrument', 'side', 'strategy', 'account', 'started_at'].forEach((key) => {
        const td = document.createElement('td');
        td.textContent = String(session?.[key] ?? (key === 'broker' ? session?.market : '') ?? '');
        tr.appendChild(td);
      });
      const actionTd = document.createElement('td');
      const stopButton = document.createElement('button');
      stopButton.type = 'button';
      stopButton.className = 'action-btn danger';
      stopButton.textContent = 'Stop';
      stopButton.addEventListener('click', () => stopBounceTrader(session?.id, stopButton));
      actionTd.appendChild(stopButton);
      tr.appendChild(actionTd);
      bounceTradersBody.appendChild(tr);
    });
    if (bounceTradersEmpty) bounceTradersEmpty.hidden = rows.length > 0;
  };

  const refreshBounceTraders = async () => {
    if (!BOUNCE_TRADER_LOCAL_ENABLED || !bounceTradersPanel || !bounceTradersBody) return;
    const script = scriptsState.find((item) => item?.name === 'bybit_trigger_bounce_trader');
    if (script?.running !== true) {
      renderBounceTraders([]);
      setBounceTradersStatus('Bounce Trader is not running.');
      return;
    }
    if (bounceTradersInFlight) return bounceTradersInFlight;
    bounceTradersInFlight = (async () => {
      setBounceTradersStatus('Loading running bounce traders...');
      try {
        const payload = await fetchJson(BOUNCE_TRADER_BASE + '/status');
        renderBounceTraders(payload?.sessions);
        setBounceTradersStatus(Array.isArray(payload?.sessions) && payload.sessions.length ? '' : 'No active bounce trader sessions.');
      } catch (err) {
        console.error(err);
        renderBounceTraders([]);
        setBounceTradersStatus(err?.message || 'Failed to load running bounce traders.', true);
      } finally {
        bounceTradersInFlight = null;
      }
    })();
    return bounceTradersInFlight;
  };

  const stopBounceTrader = async (sessionId, button) => {
    const id = String(sessionId || '');
    if (!id || !button || button.disabled) return;
    button.disabled = true;
    setBounceTradersStatus('Stopping bounce trader...');
    try {
      await fetchJson(BOUNCE_TRADER_BASE + '/sessions/' + encodeURIComponent(id) + '/stop', { method: 'POST' });
      await refreshBounceTraders();
    } catch (err) {
      console.error(err);
      button.disabled = false;
      setBounceTradersStatus(err?.message || 'Failed to stop bounce trader.', true);
    }
  };

  const setPineStatus = (message, isErr = false) => {
    if (!pineStatus) return;
    pineStatus.textContent = message || '';
    pineStatus.style.color = isErr ? '#fca5a5' : '#94a3b8';
  };

  const copyPineScript = async (name) => {
    if (!name) return;
    if (pineFallback) {
      pineFallback.hidden = true;
      pineFallback.value = '';
    }
    setPineStatus('Loading script...');
    try {
      const payload = await fetchJson(`/api/pine/file?name=${encodeURIComponent(name)}`);
      const code = String(payload?.code || '');
      try {
        await navigator.clipboard.writeText(code);
        setPineStatus(`Copied pinescripts/${name}`);
      } catch (_err) {
        if (pineFallback) {
          pineFallback.value = code;
          pineFallback.hidden = false;
          pineFallback.focus();
          pineFallback.select();
        }
        setPineStatus('Clipboard unavailable. Use manual copy below.');
      }
    } catch (err) {
      console.error(err);
      setPineStatus(err?.message || 'Failed to copy Pine script.', true);
    }
  };

  const renderPineFiles = (files) => {
    if (!pineFiles) return;
    const list = Array.isArray(files) ? files : [];
    if (!list.length) {
      pineFiles.innerHTML = '<div class="pine-row"><span class="pine-file">No Pine scripts found.</span></div>';
      return;
    }
    pineFiles.innerHTML = list.map((file) => (
      `<div class="pine-row"><span class="pine-file">pinescripts/${esc(file)}</span><button type="button" class="action-btn" data-pine-name="${esc(file)}">Copy</button></div>`
    )).join('');
    pineFiles.querySelectorAll('button[data-pine-name]').forEach((button) => {
      button.addEventListener('click', () => copyPineScript(button.dataset.pineName || ''));
    });
  };

  const refreshPineScripts = async () => {
    if (!pineFiles) return;
    try {
      const payload = await fetchJson('/api/pine/files');
      const files = Array.isArray(payload?.files) ? payload.files : [];
      renderPineFiles(files);
      setPineStatus(`${files.length} script${files.length === 1 ? '' : 's'} in pinescripts`);
    } catch (err) {
      console.error(err);
      renderPineFiles([]);
      setPineStatus(err?.message || 'Failed to load Pine scripts.', true);
    }
  };

  const restartPolling = () => {
    [scriptsTimer, oandaTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
    if (oandaSecondTimer) clearInterval(oandaSecondTimer);
    const multiplier = document.visibilityState === 'hidden' ? POLL_MS.hiddenMultiplier : 1;
    scriptsTimer = setInterval(() => { refreshScripts(); }, POLL_MS.scripts * multiplier);
    if (oandaHeadline) {
      oandaTimer = setInterval(() => { refreshOandaInactivity(); }, POLL_MS.oandaInactivity * multiplier);
      oandaSecondTimer = setInterval(() => { tickOandaCountdown(); }, 1000);
    }
  };

  refreshBtn?.addEventListener('click', () => {
    refreshScripts();
    if (oandaHeadline) refreshOandaInactivity();
  });
  oandaToggleBtn?.addEventListener('click', () => {
    oandaExpanded = !oandaExpanded;
    syncOandaDetailsVisibility();
  });
  workspaceFrame?.addEventListener('load', installWorkspaceHeightSync);
  window.addEventListener('resize', scheduleWorkspaceFrameHeightSync);

  if (workspaceFrame) {
    ensureOrdersWorkspace();
    installWorkspaceHeightSync();
  }
  refreshScripts();
  refreshPineScripts();
  if (oandaHeadline) {
    refreshOandaInactivity();
    syncOandaDetailsVisibility();
  }
  restartPolling();
  document.addEventListener('visibilitychange', restartPolling);
  window.addEventListener('beforeunload', () => {
    [scriptsTimer, oandaTimer, oandaSecondTimer].forEach((id) => {
      if (id) clearInterval(id);
    });
    cleanupWorkspaceHeightObservers();
  });
})();
