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

    const setRunningState = (state) => {
        const running = state === 'running';
        const starting = state === 'starting';
        statusEl.textContent = running ? 'Running' : (starting ? 'Starting...' : 'Stopped');
        startBtn.disabled = running || starting;
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
            const starting = Boolean(script && script.starting);
            const state = running ? 'running' : (starting ? 'starting' : 'stopped');
            setRunningState(state);
            if (running) {
                scheduleAppLoad();
            }
            return script || { running: false, starting: false };
        } catch (err) {
            console.error(err);
            statusEl.textContent = 'Unable to load status.';
        }
        return { running: false, starting: false };
    };

    const waitForStartResolution = async () => {
        while (true) {
            const script = await refreshStatus();
            const running = Boolean(script?.running);
            const starting = Boolean(script?.starting);
            if (running) return true;
            if (!starting) {
                const reason = script?.last_start_error || script?.last_exit_reason;
                if (reason) {
                    appendLogs([`Startup failed: ${reason}`]);
                }
                return false;
            }
            await new Promise((resolve) => setTimeout(resolve, 500));
        }
    };

    const startScript = async (isAuto = false) => {
        if (autoStartInFlight && isAuto) {
            return;
        }
        autoStartInFlight = isAuto;
        setRunningState('starting');
        appendLogs(['Starting script...']);
        try {
            const payload = await fetchJson(`/scripts/${buildScriptPath(scriptName)}/start`, { method: 'POST' });
            if (payload?.redirect) {
                window.location.href = payload.redirect;
                return;
            }
            if (!pollTimer) {
                pollTimer = setInterval(pollLogs, 2000);
            }
            await pollLogs();
            await waitForStartResolution();
        } catch (err) {
            console.error(err);
            alert(err.message || 'Failed to start script');
            setRunningState('stopped');
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

    const setupCustomAlerts = (monitor, card) => {
        if (!card) return;
        const isBybit = monitor === 'bybit';
        const unitOptions = isBybit
            ? [
                { value: 'pct', label: 'Percent (%)' },
                { value: 'abs', label: 'Absolute move' },
            ]
            : [
                { value: 'pips', label: 'Pips' },
                { value: 'pct', label: 'Percent (%)' },
            ];

        const section = document.createElement('div');
        section.style.marginTop = '1.5rem';

        const header = document.createElement('div');
        header.className = 'settings-header';
        const headerText = document.createElement('div');
        const headerTitle = document.createElement('strong');
        headerTitle.textContent = 'Custom alerts';
        const headerMeta = document.createElement('p');
        headerMeta.className = 'meta';
        headerMeta.textContent = 'Create per-symbol price or move alerts that stay active.';
        headerText.append(headerTitle, headerMeta);
        const statusBadge = document.createElement('span');
        statusBadge.className = 'badge';
        statusBadge.textContent = '\u00a0';
        header.append(headerText, statusBadge);
        section.appendChild(header);

        const formGrid = document.createElement('div');
        formGrid.className = 'settings-grid';

        const makeLabel = (text, input) => {
            const label = document.createElement('label');
            label.append(text);
            label.appendChild(input);
            return label;
        };

        const symbolInput = document.createElement('input');
        symbolInput.type = 'text';
        symbolInput.placeholder = isBybit ? 'BTCUSDT' : 'EUR_USD';

        const kindSelect = document.createElement('select');
        ['price', 'move'].forEach((kind) => {
            const option = document.createElement('option');
            option.value = kind;
            option.textContent = kind === 'price' ? 'Price alert' : 'Move alert';
            kindSelect.appendChild(option);
        });

        const priceDirectionSelect = document.createElement('select');
        ['above', 'below'].forEach((direction) => {
            const option = document.createElement('option');
            option.value = direction;
            option.textContent = direction === 'above' ? 'Above' : 'Below';
            priceDirectionSelect.appendChild(option);
        });

        const targetPriceInput = document.createElement('input');
        targetPriceInput.type = 'number';
        targetPriceInput.min = '0';
        targetPriceInput.step = '0.0001';

        const messageInput = document.createElement('input');
        messageInput.type = 'text';
        messageInput.placeholder = 'Optional Telegram note (fixed price alerts only)';
        messageInput.maxLength = 500;

        const moveDirectionSelect = document.createElement('select');
        ['up', 'down', 'either'].forEach((direction) => {
            const option = document.createElement('option');
            option.value = direction;
            option.textContent = direction === 'either' ? 'Either' : direction[0].toUpperCase() + direction.slice(1);
            moveDirectionSelect.appendChild(option);
        });

        const thresholdInput = document.createElement('input');
        thresholdInput.type = 'number';
        thresholdInput.min = '0';
        thresholdInput.step = isBybit ? '0.01' : '0.1';

        const unitSelect = document.createElement('select');
        unitOptions.forEach((unit) => {
            const option = document.createElement('option');
            option.value = unit.value;
            option.textContent = unit.label;
            unitSelect.appendChild(option);
        });

        const windowSelect = document.createElement('select');
        [
            { label: '1', seconds: 60 },
            { label: '5', seconds: 300 },
            { label: '15', seconds: 900 },
            { label: '30', seconds: 1800 },
            { label: 'hour', seconds: 3600 },
            { label: 'day', seconds: 86400 },
            { label: 'week', seconds: 604800 },
            { label: 'month', seconds: 2592000 },
        ].forEach((opt) => {
            const option = document.createElement('option');
            option.value = String(opt.seconds);
            option.textContent = opt.label;
            windowSelect.appendChild(option);
        });

        const cooldownInput = document.createElement('input');
        cooldownInput.type = 'number';
        cooldownInput.min = '0';
        cooldownInput.step = '1';

        const enabledInput = document.createElement('input');
        enabledInput.type = 'checkbox';
        enabledInput.checked = true;

        const symbolLabel = makeLabel('Symbol', symbolInput);
        const kindLabel = makeLabel('Alert type', kindSelect);
        const priceDirectionLabel = makeLabel('Price direction', priceDirectionSelect);
        const targetPriceLabel = makeLabel('Target price', targetPriceInput);
        const messageLabel = makeLabel('Telegram message', messageInput);
        const moveDirectionLabel = makeLabel('Move direction', moveDirectionSelect);
        const thresholdLabel = makeLabel('Move threshold', thresholdInput);
        const unitLabel = makeLabel('Move unit', unitSelect);
        const windowLabel = makeLabel('Window', windowSelect);
        const cooldownLabel = makeLabel('Cooldown (seconds)', cooldownInput);
        const enabledLabel = makeLabel('Enabled', enabledInput);

        const priceFields = [priceDirectionLabel, targetPriceLabel, messageLabel];
        const moveFields = [moveDirectionLabel, thresholdLabel, unitLabel, windowLabel];

        const updateKindVisibility = () => {
            const isPrice = kindSelect.value === 'price';
            priceFields.forEach((field) => {
                field.style.display = isPrice ? 'flex' : 'none';
            });
            moveFields.forEach((field) => {
                field.style.display = isPrice ? 'none' : 'flex';
            });
        };

        kindSelect.addEventListener('change', updateKindVisibility);

        [
            symbolLabel,
            kindLabel,
            priceDirectionLabel,
            targetPriceLabel,
            messageLabel,
            moveDirectionLabel,
            thresholdLabel,
            unitLabel,
            windowLabel,
            cooldownLabel,
            enabledLabel,
        ].forEach((label) => formGrid.appendChild(label));
        section.appendChild(formGrid);

        const controls = document.createElement('div');
        controls.className = 'controls';
        const saveBtn = document.createElement('button');
        saveBtn.textContent = 'Save alert';
        const clearBtn = document.createElement('button');
        clearBtn.className = 'secondary';
        clearBtn.textContent = 'Clear';
        controls.append(saveBtn, clearBtn);
        section.appendChild(controls);

        const listContainer = document.createElement('div');
        listContainer.style.marginTop = '1rem';
        section.appendChild(listContainer);

        card.appendChild(section);
        updateKindVisibility();

        let editingId = null;

        const resetForm = () => {
            editingId = null;
            symbolInput.value = '';
            kindSelect.value = 'price';
            priceDirectionSelect.value = 'above';
            targetPriceInput.value = '';
            messageInput.value = '';
            moveDirectionSelect.value = 'either';
            thresholdInput.value = '';
            unitSelect.value = unitOptions[0].value;
            windowSelect.value = '60';
            cooldownInput.value = '';
            enabledInput.checked = true;
            saveBtn.textContent = 'Save alert';
            updateKindVisibility();
        };

        const renderAlerts = (alerts) => {
            listContainer.innerHTML = '';
            if (!alerts.length) {
                const empty = document.createElement('p');
                empty.className = 'meta';
                empty.textContent = 'No custom alerts yet.';
                listContainer.appendChild(empty);
                return;
            }
            alerts.forEach((alert) => {
                const row = document.createElement('div');
                row.style.display = 'flex';
                row.style.flexWrap = 'wrap';
                row.style.alignItems = 'center';
                row.style.justifyContent = 'space-between';
                row.style.gap = '0.75rem';
                row.style.padding = '0.75rem 0';
                row.style.borderBottom = '1px solid #1f2937';

                const summary = document.createElement('div');
                const label = document.createElement('strong');
                label.textContent = alert.symbol || 'Alert';
                const detail = document.createElement('div');
                detail.className = 'meta';
                if (alert.kind === 'price') {
                    detail.textContent = `Price ${alert.direction} ${alert.target_price}`;
                    if (alert.message) {
                        detail.textContent += ` · Note: ${alert.message}`;
                    }
                } else {
                    detail.textContent = `Move ${alert.direction} ${alert.threshold} ${alert.unit} in ${formatWindow(alert.window_seconds)}`;
                }
                const cooldownText = alert.cooldown_seconds
                    ? `Cooldown ${alert.cooldown_seconds}s`
                    : 'No cooldown';
                const statusText = alert.enabled ? 'Enabled' : 'Disabled';
                const meta = document.createElement('div');
                meta.className = 'meta';
                meta.textContent = `${statusText} · ${cooldownText}`;
                summary.append(label, detail, meta);

                const actions = document.createElement('div');
                actions.style.display = 'flex';
                actions.style.gap = '0.5rem';
                actions.style.flexWrap = 'wrap';

                const toggleBtn = document.createElement('button');
                toggleBtn.className = 'secondary';
                toggleBtn.textContent = alert.enabled ? 'Disable' : 'Enable';
                toggleBtn.addEventListener('click', async () => {
                    toggleBtn.disabled = true;
                    try {
                        await fetchJson(`/api/${monitor}-monitor/custom-alerts/${alert.id}/enabled`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ enabled: !alert.enabled }),
                        });
                        await loadAlerts();
                    } catch (err) {
                        console.error(err);
                        setSettingsBadge(statusBadge, 'Toggle failed', true);
                        alert(err.message || 'Unable to toggle alert');
                    } finally {
                        toggleBtn.disabled = false;
                    }
                });

                const editBtn = document.createElement('button');
                editBtn.textContent = 'Edit';
                editBtn.addEventListener('click', () => {
                    editingId = alert.id;
                    symbolInput.value = alert.symbol || '';
                    kindSelect.value = alert.kind || 'price';
                    if (alert.kind === 'price') {
                        priceDirectionSelect.value = alert.direction || 'above';
                        targetPriceInput.value = alert.target_price ?? '';
                        messageInput.value = alert.message || '';
                    } else {
                        moveDirectionSelect.value = alert.direction || 'either';
                        thresholdInput.value = alert.threshold ?? '';
                        unitSelect.value = alert.unit || unitOptions[0].value;
                        windowSelect.value = String(alert.window_seconds ?? 60);
                        messageInput.value = '';
                    }
                    cooldownInput.value = alert.cooldown_seconds ?? '';
                    enabledInput.checked = Boolean(alert.enabled);
                    saveBtn.textContent = 'Update alert';
                    updateKindVisibility();
                });

                const deleteBtn = document.createElement('button');
                deleteBtn.className = 'secondary';
                deleteBtn.textContent = 'Delete';
                deleteBtn.addEventListener('click', async () => {
                    if (!confirm('Delete this alert?')) return;
                    deleteBtn.disabled = true;
                    try {
                        await fetchJson(`/api/${monitor}-monitor/custom-alerts/${alert.id}`, {
                            method: 'DELETE',
                        });
                        await loadAlerts();
                    } catch (err) {
                        console.error(err);
                        setSettingsBadge(statusBadge, 'Delete failed', true);
                        alert(err.message || 'Unable to delete alert');
                    } finally {
                        deleteBtn.disabled = false;
                    }
                });

                actions.append(toggleBtn, editBtn, deleteBtn);
                row.append(summary, actions);
                listContainer.appendChild(row);
            });
        };

        const loadAlerts = async () => {
            setSettingsBadge(statusBadge, 'Loading...');
            try {
                const data = await fetchJson(`/api/${monitor}-monitor/custom-alerts`, {
                    headers: { 'Cache-Control': 'no-store' },
                });
                renderAlerts(data.alerts || []);
                setSettingsBadge(statusBadge, 'Ready');
            } catch (err) {
                console.error(err);
                setSettingsBadge(statusBadge, 'Load failed', true);
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

        const parseRequiredNumber = (input, label) => {
            const value = Number(input.value);
            if (!Number.isFinite(value)) {
                throw new Error(`${label} must be a number`);
            }
            return value;
        };

        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true;
            clearBtn.disabled = true;
            try {
                const symbol = symbolInput.value.trim().toUpperCase();
                if (!symbol) {
                    throw new Error('Symbol is required');
                }
                const kind = kindSelect.value;
                const payload = {
                    id: editingId || undefined,
                    symbol,
                    kind,
                    enabled: enabledInput.checked,
                    cooldown_seconds: cooldownInput.value ? parseRequiredNumber(cooldownInput, 'Cooldown seconds') : 0,
                };
                if (kind === 'price') {
                    const target = parseRequiredNumber(targetPriceInput, 'Target price');
                    if (target <= 0) {
                        throw new Error('Target price must be greater than zero');
                    }
                    payload.direction = priceDirectionSelect.value;
                    payload.target_price = target;
                    const customMessage = messageInput.value.trim();
                    if (customMessage) {
                        payload.message = customMessage;
                    }
                } else {
                    const threshold = parseRequiredNumber(thresholdInput, 'Move threshold');
                    if (threshold <= 0) {
                        throw new Error('Move threshold must be greater than zero');
                    }
                    const windowSeconds = Number(windowSelect.value);
                    if (!Number.isFinite(windowSeconds) || windowSeconds <= 0) {
                        throw new Error('Window must be selected');
                    }
                    payload.direction = moveDirectionSelect.value;
                    payload.threshold = threshold;
                    payload.unit = unitSelect.value;
                    payload.window_seconds = windowSeconds;
                }
                setSettingsBadge(statusBadge, 'Saving...');
                await fetchJson(`/api/${monitor}-monitor/custom-alerts`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                resetForm();
                await loadAlerts();
                setSettingsBadge(statusBadge, 'Saved');
            } catch (err) {
                console.error(err);
                setSettingsBadge(statusBadge, 'Save failed', true);
                alert(err.message || 'Unable to save alert');
            } finally {
                saveBtn.disabled = false;
                clearBtn.disabled = false;
            }
        });

        clearBtn.addEventListener('click', () => {
            resetForm();
        });

        loadAlerts();
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
        oandaSettingsCard.style.display = 'block';
        try {
            const resp = await fetch('/api/oanda-monitor/settings');
            if (!resp.ok) {
                throw new Error(`Failed to load settings (${resp.status})`);
            }
            const data = await resp.json();
            if (oandaWaitInput) oandaWaitInput.value = data.wait_seconds ?? '';
            if (oandaThresholdInput) oandaThresholdInput.value = data.percent_threshold ?? '';
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
        await refreshStatus();
        pollLogs();
        pollTimer = setInterval(pollLogs, 2000);
        loadBybitSettings();
        loadOandaSettings();
        if (isBybitMonitor) {
            setupCustomAlerts('bybit', settingsCard);
        }
        if (isOandaMonitor) {
            setupCustomAlerts('oanda', oandaSettingsCard);
        }
    };

    init();
})();
