(() => {
    const logBox = document.getElementById('log-box');
    const saveBtn = document.getElementById('save-log-btn');
    const refreshBtn = document.getElementById('refresh-btn');
    const lineCount = document.getElementById('line-count');
    const scriptName =
        (window.RENDER_LOG_VIEW && window.RENDER_LOG_VIEW.scriptName) ||
        (document.body && document.body.dataset && document.body.dataset.scriptName) ||
        '';
    const settingsCard = document.getElementById('bybit-settings');
    const waitInput = document.getElementById('bybit-wait-seconds');
    const thresholdInput = document.getElementById('bybit-threshold');
    const saveSettingsBtn = document.getElementById('bybit-save-settings');
    const reloadSettingsBtn = document.getElementById('bybit-reload-settings');
    const settingsStatus = document.getElementById('bybit-settings-status');

    const isBybitMonitor = scriptName.replace(/-/g, '_') === 'bybit_monitor';

    let cachedLines = [];
    let refreshTimer = null;
    let cursor = 0;
    const resultTabs = new Map();

    const resultExportPattern = /Exporting data to HTML:\s*(.+)$/i;

    const buildScriptPath = (name) => encodeURIComponent(name).replace(/%2F/g, '/');

    const setLineCount = () => {
        const count = cachedLines.length;
        lineCount.textContent = `${count} ${count === 1 ? 'line' : 'lines'}`;
    };

    const fetchLogs = async ({ reset = false } = {}) => {
        try {
            const path = buildScriptPath(scriptName);
            const url = new URL(`/api/logs/${path}`, window.location.origin);
            url.searchParams.set('cursor', reset ? 0 : cursor);

            const response = await fetch(url.toString());
            if (!response.ok) {
                throw new Error(`Failed to load logs (${response.status})`);
            }

            const payload = await response.json();
            const newLines = Array.isArray(payload?.lines) ? payload.lines : [];
            if (reset) {
                cachedLines = newLines;
            } else {
                cachedLines.push(...newLines);
            }

            if (newLines.length) {
                handleResultExports(newLines);
            }

            cursor = typeof payload.cursor === 'number' ? payload.cursor : cachedLines.length;

            logBox.textContent = cachedLines.length
                ? cachedLines.join('\n')
                : 'No logs yet. Start the script to see output.';
            setLineCount();
        } catch (err) {
            console.error(err);
            logBox.textContent = 'Unable to load logs. Please retry or check the server.';
        }
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

    const downloadLog = () => {
        const header = [
            'Render Master Control Log Export',
            `Script: ${scriptName}`,
            `Exported: ${new Date().toISOString()}`,
            `Total lines: ${cachedLines.length}`,
            '----------------------------------------',
        ];
        const body = cachedLines.length ? cachedLines : ['No log output was available at export time.'];
        const content = [...header, ...body].join('\n');
        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        link.download = `${scriptName || 'render-script'}-log-${timestamp}.txt`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    };

    saveBtn?.addEventListener('click', downloadLog);
    refreshBtn?.addEventListener('click', () => fetchLogs({ reset: true }));

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
            setSettingsBadge('Ready');
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
        }
    };

    saveSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        saveSettings();
    });

    reloadSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        loadSettings();
    });

    refreshTimer = setInterval(fetchLogs, 3000);
    fetchLogs({ reset: true });
    loadSettings();

    window.addEventListener('beforeunload', () => {
        if (refreshTimer) {
            clearInterval(refreshTimer);
        }
    });
})();
