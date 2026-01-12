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

    const el = (tag, attrs = {}, children = []) => {
        const node = document.createElement(tag);
        Object.entries(attrs).forEach(([key, value]) => {
            if (key === 'class') {
                node.className = value;
            } else if (key === 'text') {
                node.textContent = value;
            } else if (key.startsWith('on') && typeof value === 'function') {
                node.addEventListener(key.slice(2), value);
            } else {
                node.setAttribute(key, String(value));
            }
        });
        children.forEach((child) => {
            node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        });
        return node;
    };

    const setupCustomAlerts = (monitor, parentCard) => {
        if (!parentCard) return;
        const apiBase =
            monitor === 'oanda'
                ? '/api/oanda-monitor/custom-alerts'
                : '/api/bybit-monitor/custom-alerts';

        const section = el('div', { class: 'settings-section' }, [
            el('h3', { text: 'Custom alerts', style: 'margin-top:16px;' }),
        ]);

        const status = el('div', { class: 'meta', text: '' });
        const list = el('div', {
            id: `${monitor}-custom-alerts-list`,
            style: 'display:flex;flex-direction:column;gap:10px;margin-top:10px;',
        });

        const symbolInput = el('input', {
            placeholder: monitor === 'oanda' ? 'EUR_USD' : 'BTCUSDT',
            style: 'width:180px;',
        });
        const kindSelect = el('select', {}, [
            el('option', { value: 'price', text: 'price' }),
            el('option', { value: 'move', text: 'move' }),
        ]);
        const directionSelect = el('select');
        const unitSelect = el('select');
        const thresholdInput = el('input', { placeholder: 'threshold', style: 'width:120px;' });
        const windowSelect = el('select', { style: 'width:140px;' }, [
            el('option', { value: '60', text: '1' }),
            el('option', { value: '300', text: '5' }),
            el('option', { value: '900', text: '15' }),
            el('option', { value: '1800', text: '30' }),
            el('option', { value: '3600', text: 'hour' }),
            el('option', { value: '86400', text: 'day' }),
            el('option', { value: '604800', text: 'week' }),
            el('option', { value: '2592000', text: 'month' }),
        ]);
        const targetPriceInput = el('input', {
            placeholder: 'target_price',
            style: 'width:140px;',
        });
        const createBtn = el('button', { class: 'secondary', text: 'Add alert' });

        const formRow = el(
            'div',
            {
                style:
                    'display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:10px;',
            },
            [
                symbolInput,
                kindSelect,
                directionSelect,
                unitSelect,
                thresholdInput,
                windowSelect,
                targetPriceInput,
                createBtn,
            ],
        );

        section.appendChild(formRow);
        section.appendChild(status);
        section.appendChild(list);
        parentCard.appendChild(section);

        const renderForm = () => {
            const kind = kindSelect.value;
            directionSelect.innerHTML = '';
            unitSelect.innerHTML = '';

            if (kind === 'price') {
                directionSelect.appendChild(el('option', { value: 'above', text: 'above' }));
                directionSelect.appendChild(el('option', { value: 'below', text: 'below' }));
                unitSelect.style.display = 'none';
                thresholdInput.style.display = 'none';
                windowSelect.style.display = 'none';
                targetPriceInput.style.display = '';
            } else {
                directionSelect.appendChild(el('option', { value: 'either', text: 'either' }));
                directionSelect.appendChild(el('option', { value: 'up', text: 'up' }));
                directionSelect.appendChild(el('option', { value: 'down', text: 'down' }));

                if (monitor === 'oanda') {
                    unitSelect.appendChild(el('option', { value: 'pips', text: 'pips' }));
                    unitSelect.appendChild(el('option', { value: 'pct', text: '%' }));
                } else {
                    unitSelect.appendChild(el('option', { value: 'pct', text: '%' }));
                    unitSelect.appendChild(el('option', { value: 'abs', text: 'abs' }));
                }

                unitSelect.style.display = '';
                thresholdInput.style.display = '';
                windowSelect.style.display = '';
                targetPriceInput.style.display = 'none';
            }
        };

        const formatWindow = (seconds) => {
            const s = Number(seconds);
            const map = {
                60: '1m',
                300: '5m',
                900: '15m',
                1800: '30m',
                3600: 'hour',
                86400: 'day',
                604800: 'week',
                2592000: 'month',
            };
            return map[s] || `${s}s`;
        };

        const renderList = (alerts) => {
            list.innerHTML = '';
            if (!alerts.length) {
                list.appendChild(el('div', { class: 'meta', text: 'No custom alerts yet.' }));
                return;
            }
            alerts.forEach((alert) => {
                const left = el('div', { style: 'flex:1;min-width:320px;' });
                let description = `${alert.symbol} | ${alert.kind}`;
                if (alert.kind === 'price') {
                    description += ` ${alert.direction} ${alert.target_price}`;
                }
                if (alert.kind === 'move') {
                    const unitSuffix = alert.unit === 'pct' ? '%' : alert.unit || '';
                    description += ` ${alert.direction} ${alert.threshold}${unitSuffix} in ${formatWindow(alert.window_seconds)}`;
                }
                left.appendChild(el('div', { text: description, style: 'font-weight:600;' }));
                left.appendChild(el('div', { class: 'meta', text: `id: ${alert.id}` }));

                const toggle = el('input', {
                    type: 'checkbox',
                    ...(alert.enabled ? { checked: '' } : {}),
                    onchange: async () => {
                        await fetch(`${apiBase}/${encodeURIComponent(alert.id)}/enabled`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ enabled: toggle.checked }),
                        });
                        await loadAlerts();
                    },
                });

                const deleteBtn = el('button', {
                    class: 'secondary',
                    text: 'Delete',
                    onclick: async () => {
                        await fetch(`${apiBase}/${encodeURIComponent(alert.id)}`, {
                            method: 'DELETE',
                        });
                        await loadAlerts();
                    },
                });

                const row = el(
                    'div',
                    {
                        style:
                            'display:flex;gap:12px;align-items:center;justify-content:space-between;' +
                            'padding:10px;border:1px solid rgba(255,255,255,0.08);border-radius:10px;',
                    },
                    [
                        left,
                        el('div', { style: 'display:flex;gap:10px;align-items:center;' }, [
                            el(
                                'label',
                                { style: 'display:flex;gap:6px;align-items:center;' },
                                [toggle, el('span', { text: 'On' })],
                            ),
                            deleteBtn,
                        ]),
                    ],
                );

                list.appendChild(row);
            });
        };

        const loadAlerts = async () => {
            status.textContent = 'Loading...';
            const resp = await fetch(apiBase);
            if (!resp.ok) {
                throw new Error(`GET ${apiBase} failed (${resp.status})`);
            }
            const data = await resp.json();
            const alerts = Array.isArray(data?.alerts) ? data.alerts : [];
            renderList(alerts);
            status.textContent = '';
        };

        const createAlert = async () => {
            const symbol = (symbolInput.value || '').trim().toUpperCase();
            if (!symbol) {
                status.textContent = 'symbol required';
                return;
            }
            const kind = kindSelect.value;
            const payload = { symbol, kind, enabled: true };
            if (kind === 'price') {
                payload.direction = directionSelect.value;
                payload.target_price = Number(targetPriceInput.value);
            } else {
                payload.direction = directionSelect.value;
                payload.unit = unitSelect.value;
                payload.threshold = Number(thresholdInput.value);
                payload.window_seconds = Number(windowSelect.value);
            }
            status.textContent = 'Saving...';
            const resp = await fetch(apiBase, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                throw new Error(`POST ${apiBase} failed (${resp.status})`);
            }
            status.textContent = '';
            await loadAlerts();
        };

        kindSelect.addEventListener('change', renderForm);
        createBtn.addEventListener('click', (event) => {
            event.preventDefault();
            createAlert().catch((err) => {
                status.textContent = String(err);
            });
        });

        renderForm();
        loadAlerts().catch((err) => {
            status.textContent = String(err);
        });
    };

    const loadBybitSettings = async () => {
        if (!isBybitMonitor || !settingsCard) return;
        settingsCard.style.display = 'block';
        try {
            const resp = await fetch('/api/bybit-monitor/settings');
            if (!resp.ok) {
                throw new Error(`Failed to load settings (${resp.status})`);
            }
            const data = await resp.json();
            if (waitInput) waitInput.value = data.wait_seconds ?? '';
            if (thresholdInput) thresholdInput.value = data.percent_threshold ?? '';
            setSettingsBadge(settingsStatus, 'Ready');
        } catch (err) {
            console.error(err);
            setSettingsBadge(settingsStatus, 'Load failed', true);
        }
    };

    const loadOandaSettings = async () => {
        if (!isOandaMonitor || !oandaSettingsCard) return;
        oandaSettingsCard.style.display = 'block';
        try {
            const resp = await fetch('/api/oanda-monitor/settings');
            if (!resp.ok) {
                throw new Error(`Failed to load settings (${resp.status})`);
            }
            const data = await resp.json();
            if (oandaWaitInput) oandaWaitInput.value = data.wait_seconds ?? '';
            if (oandaThresholdInput) oandaThresholdInput.value = data.percent_threshold ?? '';
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
    if (isBybitMonitor && settingsCard) {
        setupCustomAlerts('bybit', settingsCard);
    }
    if (isOandaMonitor && oandaSettingsCard) {
        setupCustomAlerts('oanda', oandaSettingsCard);
    }

    window.addEventListener('beforeunload', () => {
        if (refreshTimer) {
            clearInterval(refreshTimer);
        }
    });
})();
