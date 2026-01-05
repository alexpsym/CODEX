(() => {
    const scriptName = document.body.dataset.scriptName;
    const hasUi = document.body.dataset.hasUi === 'true';

    const statusEl = document.getElementById('script-status');
    const startBtn = document.getElementById('start-btn');
    const stopBtn = document.getElementById('stop-btn');
    const logBox = document.getElementById('log-box');
    const appPanel = document.getElementById('app-panel');
    const appFrame = document.getElementById('app-frame');
    const settingsCard = document.getElementById('bybit-settings');
    const waitInput = document.getElementById('bybit-wait-seconds');
    const thresholdInput = document.getElementById('bybit-threshold');
    const saveSettingsBtn = document.getElementById('bybit-save-settings');
    const reloadSettingsBtn = document.getElementById('bybit-reload-settings');
    const testAlertBtn = document.getElementById('bybit-test-alert');
    const settingsStatus = document.getElementById('bybit-settings-status');

    const oandaSettingsCard = document.getElementById('oanda-settings');
    const oandaWaitInput = document.getElementById('oanda-wait-seconds');
    const oandaThresholdInput = document.getElementById('oanda-threshold');
    const oandaAthAtlEnabled = document.getElementById('oanda-ath-atl-enabled');
    const oandaAthAtlMinBreak = document.getElementById('oanda-ath-atl-min-break');
    const oandaAthAtlCooldown = document.getElementById('oanda-ath-atl-cooldown');
    const oandaAthAtlGranularity = document.getElementById('oanda-ath-atl-granularity');
    const oandaAthAtlPrice = document.getElementById('oanda-ath-atl-price');
    const oandaAthAtlBackfillBatch = document.getElementById('oanda-ath-atl-backfill-batch');
    const oandaAthAtlBackfillPages = document.getElementById('oanda-ath-atl-backfill-pages');
    const oandaSaveSettingsBtn = document.getElementById('oanda-save-settings');
    const oandaReloadSettingsBtn = document.getElementById('oanda-reload-settings');
    const oandaTestAlertBtn = document.getElementById('oanda-test-alert');
    const oandaSettingsStatus = document.getElementById('oanda-settings-status');

    const buildScriptPath = (name) => encodeURIComponent(name).replace(/%2F/g, '/');
    const appUrl = `/apps/${buildScriptPath(scriptName)}`;
    const normalizedScriptName = scriptName.replace(/-/g, '_');
    const isBybitMonitor = normalizedScriptName === 'bybit_monitor';
    const isOandaMonitor = normalizedScriptName === 'oanda_monitor';

    let logCursor = 0;
    let pollTimer = null;
    let appLoadTimer = null;
    let appFrameLoaded = false;
    let autoStartInFlight = false;
    const resultTabs = new Map();
    const resultExportPattern = /Exporting data to HTML:\s*(.+)$/i;

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        if (!response.ok) {
            const body = await response.text();
            const detail = body || response.statusText;
            throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}: ${detail}`);
        }
        return response.json();
    };

    const appendLogs = (lines) => {
        if (!lines.length) return;
        handleResultExports(lines);
        const text = lines.join('\n') + '\n';
        if (logBox.textContent === 'Waiting for output...') {
            logBox.textContent = '';
        }
        logBox.textContent += text;
        logBox.scrollTop = logBox.scrollHeight;
    };

    const normalizeResultPath = (rawPath) =>
        rawPath.replace(/\\/g, '/').trim().replace(/^\/+/, '');

    const buildResultUrl = (resultPath) => {
        const normalized = normalizeResultPath(resultPath);
        const scriptPath = buildScriptPath(scriptName);
        const url = new URL(`/results/${scriptPath}/${normalized}`, window.location.origin);
        url.searchParams.set('ts', Date.now().toString());
        return url.toString();
    };

    const getTabKey = (resultPath) => normalizeResultPath(resultPath);

    const openOrRefreshTab = (resultPath) => {
        const tabKey = getTabKey(resultPath);
        if (!tabKey) return;
        const url = buildResultUrl(tabKey);
        const existingTab = resultTabs.get(tabKey);
        if (existingTab && !existingTab.closed) {
            existingTab.location.href = url;
            existingTab.focus();
            return;
        }
        const safeName = `result-${scriptName}-${tabKey}`.replace(/[^a-zA-Z0-9_-]/g, '_');
        const newTab = window.open(url, safeName);
        if (newTab) {
            resultTabs.set(tabKey, newTab);
        }
    };

    const handleResultExports = (lines) => {
        lines.forEach((line) => {
            const match = line.match(resultExportPattern);
            if (match && match[1]) {
                openOrRefreshTab(match[1]);
            }
        });
    };

    const pollLogs = async () => {
        try {
            const snapshot = await fetchJson(`/api/logs/${buildScriptPath(scriptName)}?cursor=${logCursor}`);
            logCursor = snapshot.cursor ?? logCursor;
            appendLogs(snapshot.lines || []);
        } catch (err) {
            console.error(err);
        }
    };

    const setRunningState = (running) => {
        statusEl.textContent = running ? 'Running' : 'Stopped';
        startBtn.disabled = running;
        stopBtn.disabled = !running;

        if (hasUi) {
            if (running) {
                appPanel.style.display = 'block';
            } else {
                appPanel.style.display = 'none';
                appFrame.src = '';
                appFrameLoaded = false;
            }
        }
    };

    const scheduleAppLoad = () => {
        if (!hasUi || appFrameLoaded || appLoadTimer) {
            return;
        }
        let attempts = 0;
        const maxAttempts = 20;
        const attempt = async () => {
            attempts += 1;
            try {
                const response = await fetch(appUrl, { cache: 'no-store' });
                if (response.ok) {
                    appFrame.src = `${appUrl}?ts=${Date.now()}`;
                    appFrameLoaded = true;
                    appLoadTimer = null;
                    return;
                }
            } catch (err) {
                console.warn('Waiting for app UI...', err);
            }
            if (attempts < maxAttempts) {
                appLoadTimer = setTimeout(attempt, 500);
            } else {
                appLoadTimer = null;
            }
        };
        appLoadTimer = setTimeout(attempt, 200);
    };

    const refreshStatus = async () => {
        try {
            const scripts = await fetchJson('/scripts');
            const script = scripts.find((item) => item.name === scriptName);
            const running = Boolean(script && script.running);
            setRunningState(running);
            if (running) {
                scheduleAppLoad();
            }
            return running;
        } catch (err) {
            console.error(err);
            statusEl.textContent = 'Unable to load status.';
        }
        return false;
    };

    const startScript = async (isAuto = false) => {
        if (autoStartInFlight && isAuto) {
            return;
        }
        autoStartInFlight = isAuto;
        startBtn.disabled = true;
        logBox.textContent = 'Starting script...\n';
        try {
            const payload = await fetchJson(`/scripts/${buildScriptPath(scriptName)}/start`, { method: 'POST' });
            if (payload?.redirect) {
                window.location.href = payload.redirect;
                return;
            }
            let running = await refreshStatus();
            let attempts = 0;
            while (!running && attempts < 10) {
                await new Promise((resolve) => setTimeout(resolve, 500));
                running = await refreshStatus();
                attempts += 1;
            }
            if (!pollTimer) {
                pollTimer = setInterval(pollLogs, 2000);
            }
        } catch (err) {
            console.error(err);
            alert(err.message || 'Failed to start script');
            startBtn.disabled = false;
        } finally {
            autoStartInFlight = false;
        }
    };

    const stopScript = async () => {
        stopBtn.disabled = true;
        try {
            await fetchJson(`/scripts/${buildScriptPath(scriptName)}/stop`, { method: 'POST' });
            await refreshStatus();
        } catch (err) {
            console.error(err);
            alert(err.message || 'Failed to stop script');
        } finally {
            stopBtn.disabled = false;
        }
    };

    const setSettingsBadge = (target, text, isError = false) => {
        if (!target) return;
        target.textContent = text;
        target.style.background = isError ? '#7f1d1d' : '#1f2937';
        target.style.color = isError ? '#fecdd3' : '#cbd5e1';
    };

    const loadBybitSettings = async () => {
        if (!isBybitMonitor || !settingsCard) return;
        try {
            const resp = await fetch('/api/bybit-monitor/settings');
            if (!resp.ok) {
                throw new Error(`Failed to load settings (${resp.status})`);
            }
            const data = await resp.json();
            if (waitInput) waitInput.value = data.wait_seconds ?? '';
            if (thresholdInput) thresholdInput.value = data.percent_threshold ?? '';
            settingsCard.style.display = 'block';
            if (data.push_ready) {
                setSettingsBadge(settingsStatus, 'Ready');
            } else {
                setSettingsBadge(settingsStatus, 'Telegram not configured', true);
            }
        } catch (err) {
            console.error(err);
            setSettingsBadge(settingsStatus, 'Load failed', true);
        }
    };

    const loadOandaSettings = async () => {
        if (!isOandaMonitor || !oandaSettingsCard) return;
        try {
            const resp = await fetch('/api/oanda-monitor/settings');
            if (!resp.ok) {
                throw new Error(`Failed to load settings (${resp.status})`);
            }
            const data = await resp.json();
            if (oandaWaitInput) oandaWaitInput.value = data.wait_seconds ?? '';
            if (oandaThresholdInput) oandaThresholdInput.value = data.percent_threshold ?? '';
            if (oandaAthAtlEnabled) oandaAthAtlEnabled.checked = Number(data.ath_atl_enabled ?? 0) === 1;
            if (oandaAthAtlMinBreak) oandaAthAtlMinBreak.value = data.ath_atl_min_break_pct ?? '';
            if (oandaAthAtlCooldown) oandaAthAtlCooldown.value = data.ath_atl_cooldown_seconds ?? '';
            if (oandaAthAtlGranularity) oandaAthAtlGranularity.value = data.ath_atl_granularity ?? '';
            if (oandaAthAtlPrice) oandaAthAtlPrice.value = (data.ath_atl_price ?? 'M').toUpperCase();
            if (oandaAthAtlBackfillBatch) oandaAthAtlBackfillBatch.value = data.ath_atl_backfill_batch ?? '';
            if (oandaAthAtlBackfillPages) oandaAthAtlBackfillPages.value = data.ath_atl_backfill_max_pages ?? '';
            oandaSettingsCard.style.display = 'block';
            if (data.push_ready) {
                setSettingsBadge(oandaSettingsStatus, 'Ready');
            } else {
                setSettingsBadge(oandaSettingsStatus, 'Telegram not configured', true);
            }
        } catch (err) {
            console.error(err);
            setSettingsBadge(oandaSettingsStatus, 'Load failed', true);
        }
    };

    const saveBybitSettings = async () => {
        if (!isBybitMonitor || !settingsCard) return;
        const body = {
            wait_seconds: Number(waitInput?.value || 0),
            percent_threshold: Number(thresholdInput?.value || 0),
        };

        if (saveSettingsBtn) saveSettingsBtn.disabled = true;
        if (reloadSettingsBtn) reloadSettingsBtn.disabled = true;
        if (testAlertBtn) testAlertBtn.disabled = true;
        setSettingsBadge(settingsStatus, 'Saving...');

        try {
            const resp = await fetch('/api/bybit-monitor/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            if (!resp.ok) {
                const detail = await resp.text();
                throw new Error(detail || `Save failed (${resp.status})`);
            }

            const data = await resp.json();
            if (waitInput) waitInput.value = data.wait_seconds ?? '';
            if (thresholdInput) thresholdInput.value = data.percent_threshold ?? '';
            setSettingsBadge(settingsStatus, 'Saved');
        } catch (err) {
            console.error(err);
            setSettingsBadge(settingsStatus, 'Save failed', true);
            alert(err.message || 'Unable to save settings');
        } finally {
            if (saveSettingsBtn) saveSettingsBtn.disabled = false;
            if (reloadSettingsBtn) reloadSettingsBtn.disabled = false;
            if (testAlertBtn) testAlertBtn.disabled = false;
        }
    };

    const saveOandaSettings = async () => {
        if (!isOandaMonitor || !oandaSettingsCard) return;
        const body = {
            wait_seconds: Number(oandaWaitInput?.value || 0),
            percent_threshold: Number(oandaThresholdInput?.value || 0),
            ath_atl_enabled: oandaAthAtlEnabled?.checked ? 1 : 0,
            ath_atl_min_break_pct: Number(oandaAthAtlMinBreak?.value || 0),
            ath_atl_cooldown_seconds: Number(oandaAthAtlCooldown?.value || 0),
            ath_atl_granularity: oandaAthAtlGranularity?.value || '',
            ath_atl_price: oandaAthAtlPrice?.value || '',
            ath_atl_backfill_batch: Number(oandaAthAtlBackfillBatch?.value || 0),
            ath_atl_backfill_max_pages: Number(oandaAthAtlBackfillPages?.value || 0),
        };

        if (oandaSaveSettingsBtn) oandaSaveSettingsBtn.disabled = true;
        if (oandaReloadSettingsBtn) oandaReloadSettingsBtn.disabled = true;
        if (oandaTestAlertBtn) oandaTestAlertBtn.disabled = true;
        setSettingsBadge(oandaSettingsStatus, 'Saving...');

        try {
            const resp = await fetch('/api/oanda-monitor/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            if (!resp.ok) {
                const detail = await resp.text();
                throw new Error(detail || `Save failed (${resp.status})`);
            }

            const data = await resp.json();
            if (oandaWaitInput) oandaWaitInput.value = data.wait_seconds ?? '';
            if (oandaThresholdInput) oandaThresholdInput.value = data.percent_threshold ?? '';
            if (oandaAthAtlEnabled) oandaAthAtlEnabled.checked = Number(data.ath_atl_enabled ?? 0) === 1;
            if (oandaAthAtlMinBreak) oandaAthAtlMinBreak.value = data.ath_atl_min_break_pct ?? '';
            if (oandaAthAtlCooldown) oandaAthAtlCooldown.value = data.ath_atl_cooldown_seconds ?? '';
            if (oandaAthAtlGranularity) oandaAthAtlGranularity.value = data.ath_atl_granularity ?? '';
            if (oandaAthAtlPrice) oandaAthAtlPrice.value = (data.ath_atl_price ?? 'M').toUpperCase();
            if (oandaAthAtlBackfillBatch) oandaAthAtlBackfillBatch.value = data.ath_atl_backfill_batch ?? '';
            if (oandaAthAtlBackfillPages) oandaAthAtlBackfillPages.value = data.ath_atl_backfill_max_pages ?? '';
            setSettingsBadge(oandaSettingsStatus, 'Saved');
        } catch (err) {
            console.error(err);
            setSettingsBadge(oandaSettingsStatus, 'Save failed', true);
            alert(err.message || 'Unable to save settings');
        } finally {
            if (oandaSaveSettingsBtn) oandaSaveSettingsBtn.disabled = false;
            if (oandaReloadSettingsBtn) oandaReloadSettingsBtn.disabled = false;
            if (oandaTestAlertBtn) oandaTestAlertBtn.disabled = false;
        }
    };

    const sendBybitTestAlert = async () => {
        if (!isBybitMonitor || !settingsCard) return;
        if (testAlertBtn) testAlertBtn.disabled = true;
        setSettingsBadge(settingsStatus, 'Sending test...');
        try {
            const resp = await fetch('/api/bybit-monitor/push-test', { method: 'POST' });
            const payloadText = await resp.text();
            if (!resp.ok) {
                throw new Error(payloadText || `Test failed (${resp.status})`);
            }
            const data = payloadText ? JSON.parse(payloadText) : {};
            if (data?.sent) {
                setSettingsBadge(settingsStatus, 'Test sent');
            } else if (data?.configured === false) {
                setSettingsBadge(settingsStatus, 'Telegram not configured', true);
            } else {
                setSettingsBadge(settingsStatus, 'Test completed');
            }
        } catch (err) {
            console.error(err);
            setSettingsBadge(settingsStatus, 'Test failed', true);
            alert(err.message || 'Unable to send test alert');
        } finally {
            if (testAlertBtn) testAlertBtn.disabled = false;
        }
    };

    const sendOandaTestAlert = async () => {
        if (!isOandaMonitor || !oandaSettingsCard) return;
        if (oandaTestAlertBtn) oandaTestAlertBtn.disabled = true;
        setSettingsBadge(oandaSettingsStatus, 'Sending test...');
        try {
            const resp = await fetch('/api/oanda-monitor/push-test', { method: 'POST' });
            const payloadText = await resp.text();
            if (!resp.ok) {
                throw new Error(payloadText || `Test failed (${resp.status})`);
            }
            const data = payloadText ? JSON.parse(payloadText) : {};
            if (data?.sent) {
                setSettingsBadge(oandaSettingsStatus, 'Test sent');
            } else if (data?.configured === false) {
                setSettingsBadge(oandaSettingsStatus, 'Telegram not configured', true);
            } else {
                setSettingsBadge(oandaSettingsStatus, 'Test completed');
            }
        } catch (err) {
            console.error(err);
            setSettingsBadge(oandaSettingsStatus, 'Test failed', true);
            alert(err.message || 'Unable to send test alert');
        } finally {
            if (oandaTestAlertBtn) oandaTestAlertBtn.disabled = false;
        }
    };

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());

    startBtn?.addEventListener('click', startScript);
    stopBtn?.addEventListener('click', stopScript);
    saveSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        saveBybitSettings();
    });
    reloadSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        loadBybitSettings();
    });
    testAlertBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        sendBybitTestAlert();
    });
    oandaSaveSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        saveOandaSettings();
    });
    oandaReloadSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        loadOandaSettings();
    });
    oandaTestAlertBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        sendOandaTestAlert();
    });

    const init = async () => {
        const running = await refreshStatus();
        if (!running && hasUi) {
            await startScript(true);
        }
        pollLogs();
        pollTimer = setInterval(pollLogs, 2000);
        loadBybitSettings();
        loadOandaSettings();
    };

    init();
})();
