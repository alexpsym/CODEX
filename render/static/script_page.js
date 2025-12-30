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

    const buildScriptPath = (name) => encodeURIComponent(name).replace(/%2F/g, '/');
    const appUrl = `/apps/${buildScriptPath(scriptName)}`;
    const isBybitMonitor = scriptName.replace(/-/g, '_') === 'bybit_monitor';

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

    const setSettingsBadge = (text, isError = false) => {
        if (!settingsStatus) return;
        settingsStatus.textContent = text;
        settingsStatus.style.background = isError ? '#7f1d1d' : '#1f2937';
        settingsStatus.style.color = isError ? '#fecdd3' : '#cbd5e1';
    };

    const loadSettings = async () => {
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
                setSettingsBadge('Ready');
            } else {
                setSettingsBadge('Telegram not configured', true);
            }
        } catch (err) {
            console.error(err);
            setSettingsBadge('Load failed', true);
        }
    };

    const saveSettings = async () => {
        if (!isBybitMonitor || !settingsCard) return;
        const body = {
            wait_seconds: Number(waitInput?.value || 0),
            percent_threshold: Number(thresholdInput?.value || 0),
        };

        if (saveSettingsBtn) saveSettingsBtn.disabled = true;
        if (reloadSettingsBtn) reloadSettingsBtn.disabled = true;
        if (testAlertBtn) testAlertBtn.disabled = true;
        setSettingsBadge('Saving...');

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
            setSettingsBadge('Saved');
        } catch (err) {
            console.error(err);
            setSettingsBadge('Save failed', true);
            alert(err.message || 'Unable to save settings');
        } finally {
            if (saveSettingsBtn) saveSettingsBtn.disabled = false;
            if (reloadSettingsBtn) reloadSettingsBtn.disabled = false;
            if (testAlertBtn) testAlertBtn.disabled = false;
        }
    };

    const sendTestAlert = async () => {
        if (!isBybitMonitor || !settingsCard) return;
        if (testAlertBtn) testAlertBtn.disabled = true;
        setSettingsBadge('Sending test...');
        try {
            const resp = await fetch('/api/bybit-monitor/push-test', { method: 'POST' });
            const payloadText = await resp.text();
            if (!resp.ok) {
                throw new Error(payloadText || `Test failed (${resp.status})`);
            }
            const data = payloadText ? JSON.parse(payloadText) : {};
            if (data?.sent) {
                setSettingsBadge('Test sent');
            } else if (data?.configured === false) {
                setSettingsBadge('Telegram not configured', true);
            } else {
                setSettingsBadge('Test completed');
            }
        } catch (err) {
            console.error(err);
            setSettingsBadge('Test failed', true);
            alert(err.message || 'Unable to send test alert');
        } finally {
            if (testAlertBtn) testAlertBtn.disabled = false;
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
        saveSettings();
    });
    reloadSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        loadSettings();
    });
    testAlertBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        sendTestAlert();
    });

    const init = async () => {
        const running = await refreshStatus();
        if (!running && hasUi) {
            await startScript(true);
        }
        pollLogs();
        pollTimer = setInterval(pollLogs, 2000);
        loadSettings();
    };

    init();
})();
