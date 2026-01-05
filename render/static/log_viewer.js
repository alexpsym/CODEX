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
    const oandaSettingsStatus = document.getElementById('oanda-settings-status');

    const normalizedScriptName = scriptName.replace(/-/g, '_');
    const isBybitMonitor = normalizedScriptName === 'bybit_monitor';
    const isOandaMonitor = normalizedScriptName === 'oanda_monitor';

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
            setSettingsBadge(settingsStatus, 'Ready');
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
            setSettingsBadge(oandaSettingsStatus, 'Ready');
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
        }
    };

    saveSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        saveBybitSettings();
    });

    reloadSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        loadBybitSettings();
    });
    oandaSaveSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        saveOandaSettings();
    });
    oandaReloadSettingsBtn?.addEventListener('click', (event) => {
        event.preventDefault();
        loadOandaSettings();
    });

    refreshTimer = setInterval(fetchLogs, 3000);
    fetchLogs({ reset: true });
    loadBybitSettings();
    loadOandaSettings();

    window.addEventListener('beforeunload', () => {
        if (refreshTimer) {
            clearInterval(refreshTimer);
        }
    });
})();
