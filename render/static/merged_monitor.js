(() => {
    const buildScriptPath = (name) => encodeURIComponent(name).replace(/%2F/g, '/');

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        if (!response.ok) {
            const body = await response.text();
            const detail = body || response.statusText;
            throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}: ${detail}`);
        }
        return response.json();
    };

    const setSettingsBadge = (target, text, isError = false) => {
        if (!target) return;
        target.textContent = text;
        target.style.background = isError ? '#7f1d1d' : '#1f2937';
        target.style.color = isError ? '#fecdd3' : '#cbd5e1';
    };

    const setupCustomAlerts = (monitor, container) => {
        if (!container) return;
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
        section.style.marginTop = '1rem';

        const header = document.createElement('div');
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        header.style.alignItems = 'center';
        header.style.marginBottom = '8px';
        const headerTitle = document.createElement('strong');
        headerTitle.textContent = 'Custom alerts';
        const statusBadge = document.createElement('span');
        statusBadge.className = 'badge';
        statusBadge.textContent = '\u00a0';
        header.append(headerTitle, statusBadge);
        section.appendChild(header);

        const formGrid = document.createElement('div');
        formGrid.className = 'settings-grid';

        const makeLabel = (text, input) => {
            const label = document.createElement('label');
            label.append(text);
            label.appendChild(input);
            return label;
        };

        const resolveBybitSymbol = async (raw) => {
            const symbol = String(raw || '').trim().toUpperCase();
            if (!symbol || monitor !== 'bybit') {
                return symbol;
            }
            const resp = await fetch(`/api/resolve-symbol?symbol=${encodeURIComponent(symbol)}&prefer=bybit&scope=linear`, {
                cache: 'no-store',
            });
            if (!resp.ok) {
                throw new Error(`Unable to resolve symbol: ${symbol}`);
            }
            const data = await resp.json().catch(() => null);
            const resolved = String(data?.resolved_symbol || '').trim().toUpperCase();
            if (!resolved) {
                throw new Error(`Unable to resolve symbol: ${symbol}`);
            }
            return resolved;
        };

        const symbolInput = document.createElement('input');
        symbolInput.type = 'text';
        symbolInput.placeholder = isBybit ? 'BTC or BTCUSDT' : 'EUR_USD';

        const kindSelect = document.createElement('select');
        [
            { value: 'price', label: 'Price target' },
            { value: 'move', label: 'Price move' },
        ].forEach((item) => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            kindSelect.appendChild(option);
        });

        const priceDirectionSelect = document.createElement('select');
        [
            { value: 'above', label: 'Above' },
            { value: 'below', label: 'Below' },
        ].forEach((item) => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            priceDirectionSelect.appendChild(option);
        });

        const moveDirectionSelect = document.createElement('select');
        [
            { value: 'up', label: 'Move up' },
            { value: 'down', label: 'Move down' },
            { value: 'either', label: 'Either direction' },
        ].forEach((item) => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            moveDirectionSelect.appendChild(option);
        });

        const targetPriceInput = document.createElement('input');
        targetPriceInput.type = 'number';
        targetPriceInput.step = 'any';
        targetPriceInput.min = '0';
        targetPriceInput.placeholder = 'Target price';

        const thresholdInput = document.createElement('input');
        thresholdInput.type = 'number';
        thresholdInput.step = 'any';
        thresholdInput.min = '0';
        thresholdInput.placeholder = 'Move threshold';

        const unitSelect = document.createElement('select');
        unitOptions.forEach((item) => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            unitSelect.appendChild(option);
        });

        const windowSelect = document.createElement('select');
        [
            { value: '60', label: '1 minute' },
            { value: '300', label: '5 minutes' },
            { value: '900', label: '15 minutes' },
            { value: '3600', label: '1 hour' },
            { value: '14400', label: '4 hours' },
            { value: '86400', label: '24 hours' },
        ].forEach((item) => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            windowSelect.appendChild(option);
        });

        const cooldownInput = document.createElement('input');
        cooldownInput.type = 'number';
        cooldownInput.min = '0';
        cooldownInput.step = '1';
        cooldownInput.value = '0';

        const messageInput = document.createElement('input');
        messageInput.type = 'text';
        messageInput.placeholder = 'Optional custom message';

        const enabledInput = document.createElement('input');
        enabledInput.type = 'checkbox';
        enabledInput.checked = true;

        formGrid.append(
            makeLabel('Symbol', symbolInput),
            makeLabel('Type', kindSelect),
            makeLabel('Price direction', priceDirectionSelect),
            makeLabel('Move direction', moveDirectionSelect),
            makeLabel('Target price', targetPriceInput),
            makeLabel('Move threshold', thresholdInput),
            makeLabel('Unit', unitSelect),
            makeLabel('Window', windowSelect),
            makeLabel('Cooldown (seconds)', cooldownInput),
            makeLabel('Custom message', messageInput),
            makeLabel('Enabled', enabledInput),
        );
        section.appendChild(formGrid);

        const actions = document.createElement('div');
        actions.className = 'row';
        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.textContent = 'Save alert';
        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.textContent = 'Reset';
        actions.append(saveBtn, clearBtn);
        section.appendChild(actions);

        const table = document.createElement('table');
        table.style.width = '100%';
        table.style.borderCollapse = 'collapse';
        table.style.marginTop = '8px';
        section.appendChild(table);

        container.appendChild(section);

        let editingId = null;

        const toggleFields = () => {
            const kind = kindSelect.value;
            const isPrice = kind === 'price';
            priceDirectionSelect.disabled = !isPrice;
            targetPriceInput.disabled = !isPrice;
            moveDirectionSelect.disabled = isPrice;
            thresholdInput.disabled = isPrice;
            unitSelect.disabled = isPrice;
            windowSelect.disabled = isPrice;
        };

        const resetForm = () => {
            editingId = null;
            symbolInput.value = '';
            kindSelect.value = 'price';
            priceDirectionSelect.value = 'above';
            moveDirectionSelect.value = 'up';
            targetPriceInput.value = '';
            thresholdInput.value = '';
            unitSelect.selectedIndex = 0;
            windowSelect.value = '900';
            cooldownInput.value = '0';
            messageInput.value = '';
            enabledInput.checked = true;
            saveBtn.textContent = 'Save alert';
            toggleFields();
        };

        const parseRequiredNumber = (input, name) => {
            const value = Number(input.value);
            if (!Number.isFinite(value)) {
                throw new Error(`${name} must be numeric`);
            }
            return value;
        };

        const rowText = (alert) => {
            if (alert.kind === 'price') {
                return `${alert.symbol} ${alert.direction} ${alert.target_price}`;
            }
            return `${alert.symbol} ${alert.direction} ${alert.threshold} ${alert.unit} in ${alert.window_seconds}s`;
        };

        const loadAlerts = async () => {
            try {
                const payload = await fetchJson(`/api/${monitor}-monitor/custom-alerts`);
                const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
                table.innerHTML = '';
                const tbody = document.createElement('tbody');
                alerts.forEach((alert) => {
                    const tr = document.createElement('tr');
                    tr.style.borderTop = '1px solid #334155';
                    const tdMain = document.createElement('td');
                    tdMain.style.padding = '8px';
                    tdMain.textContent = rowText(alert);
                    const tdActions = document.createElement('td');
                    tdActions.style.padding = '8px';
                    tdActions.style.whiteSpace = 'nowrap';

                    const enabledBtn = document.createElement('button');
                    enabledBtn.type = 'button';
                    enabledBtn.textContent = alert.enabled ? 'Disable' : 'Enable';
                    enabledBtn.addEventListener('click', async () => {
                        enabledBtn.disabled = true;
                        try {
                            await fetchJson(`/api/${monitor}-monitor/custom-alerts/${encodeURIComponent(alert.id)}/enabled`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ enabled: !alert.enabled }),
                            });
                            await loadAlerts();
                        } catch (err) {
                            console.error(err);
                            alert(err.message || 'Unable to update alert');
                        } finally {
                            enabledBtn.disabled = false;
                        }
                    });

                    const editBtn = document.createElement('button');
                    editBtn.type = 'button';
                    editBtn.textContent = 'Edit';
                    editBtn.addEventListener('click', () => {
                        editingId = alert.id;
                        symbolInput.value = alert.symbol || '';
                        kindSelect.value = alert.kind || 'price';
                        priceDirectionSelect.value = alert.direction || 'above';
                        moveDirectionSelect.value = alert.direction || 'up';
                        targetPriceInput.value = alert.target_price ?? '';
                        thresholdInput.value = alert.threshold ?? '';
                        unitSelect.value = alert.unit || unitOptions[0].value;
                        windowSelect.value = String(alert.window_seconds || 900);
                        cooldownInput.value = String(alert.cooldown_seconds || 0);
                        messageInput.value = alert.message || '';
                        enabledInput.checked = Boolean(alert.enabled);
                        saveBtn.textContent = 'Update alert';
                        toggleFields();
                    });

                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.textContent = 'Delete';
                    deleteBtn.addEventListener('click', async () => {
                        if (!window.confirm('Delete this custom alert?')) return;
                        deleteBtn.disabled = true;
                        try {
                            await fetchJson(`/api/${monitor}-monitor/custom-alerts/${encodeURIComponent(alert.id)}`, { method: 'DELETE' });
                            if (editingId === alert.id) {
                                resetForm();
                            }
                            await loadAlerts();
                        } catch (err) {
                            console.error(err);
                            alert(err.message || 'Unable to delete alert');
                        } finally {
                            deleteBtn.disabled = false;
                        }
                    });

                    tdActions.append(enabledBtn, editBtn, deleteBtn);
                    tr.append(tdMain, tdActions);
                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);
            } catch (err) {
                console.error(err);
                setSettingsBadge(statusBadge, 'Alerts load failed', true);
            }
        };

        kindSelect.addEventListener('change', toggleFields);

        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true;
            clearBtn.disabled = true;
            try {
                let symbol = symbolInput.value.trim().toUpperCase();
                if (!symbol) {
                    throw new Error('Symbol is required');
                }
                symbol = await resolveBybitSymbol(symbol);
                symbolInput.value = symbol;
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

        clearBtn.addEventListener('click', resetForm);

        resetForm();
        loadAlerts();
    };

    const createMonitorController = ({
        monitor,
        scriptName,
        statusId,
        startId,
        stopId,
        logId,
        waitId,
        thresholdId,
        saveId,
        reloadId,
        testId,
        settingsStatusId,
        customAlertsId,
    }) => {
        const statusEl = document.getElementById(statusId);
        const startBtn = document.getElementById(startId);
        const stopBtn = document.getElementById(stopId);
        const logBox = document.getElementById(logId);
        const waitInput = document.getElementById(waitId);
        const thresholdInput = document.getElementById(thresholdId);
        const saveSettingsBtn = document.getElementById(saveId);
        const reloadSettingsBtn = document.getElementById(reloadId);
        const testAlertBtn = document.getElementById(testId);
        const settingsStatus = document.getElementById(settingsStatusId);
        const customAlertsContainer = document.getElementById(customAlertsId);

        let logCursor = 0;
        let startInFlight = false;

        const appendLogs = (lines) => {
            if (!Array.isArray(lines) || !lines.length || !logBox) return;
            const text = lines.join('\n') + '\n';
            if (logBox.textContent === 'Waiting for output...') {
                logBox.textContent = '';
            }
            logBox.textContent += text;
            logBox.scrollTop = logBox.scrollHeight;
        };

        const setRunningState = (state) => {
            const running = state === 'running';
            const starting = state === 'starting';
            if (statusEl) {
                statusEl.textContent = running ? 'Running' : (starting ? 'Starting...' : 'Stopped');
                statusEl.style.background = running ? '#14532d' : (starting ? '#1e3a8a' : '#1f2937');
            }
            if (startBtn) startBtn.disabled = running || starting;
            if (stopBtn) stopBtn.disabled = !running;
        };

        const refreshStatus = async () => {
            try {
                const script = await fetchJson(`/api/scripts/${buildScriptPath(scriptName)}`);
                const running = Boolean(script && script.running);
                const starting = Boolean(script && script.starting);
                setRunningState(running ? 'running' : (starting ? 'starting' : 'stopped'));
                return script || { running: false, starting: false };
            } catch (err) {
                console.error(err);
                if (statusEl) statusEl.textContent = 'Status unavailable';
            }
            return { running: false, starting: false };
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

        const prepareLogTailForNewStart = async () => {
            try {
                const snapshot = await fetchJson(`/api/logs/${buildScriptPath(scriptName)}?cursor=0`);
                const total = Number(snapshot?.total);
                logCursor = Number.isFinite(total) ? total : Number(snapshot?.cursor ?? logCursor);
            } catch (err) {
                console.error(err);
            }
        };

        const waitForStartResolution = async () => {
            while (true) {
                const script = await refreshStatus();
                const running = Boolean(script?.running);
                const starting = Boolean(script?.starting);
                if (running) return true;
                if (!starting) {
                    const reason = script?.last_start_error || script?.last_exit_reason || 'Startup failed.';
                    appendLogs([`Startup failed: ${reason}`]);
                    return false;
                }
                await new Promise((resolve) => setTimeout(resolve, 500));
            }
        };

        const startScript = async () => {
            if (startInFlight) return;
            startInFlight = true;
            setRunningState('starting');
            try {
                await prepareLogTailForNewStart();
                await fetchJson(`/scripts/${buildScriptPath(scriptName)}/start`, { method: 'POST' });
                await pollLogs();
                await waitForStartResolution();
            } catch (err) {
                console.error(err);
                alert(err.message || `Failed to start ${scriptName}`);
                setRunningState('stopped');
            } finally {
                startInFlight = false;
            }
        };

        const stopScript = async () => {
            if (stopBtn) stopBtn.disabled = true;
            try {
                await fetchJson(`/scripts/${buildScriptPath(scriptName)}/stop`, { method: 'POST' });
                await refreshStatus();
            } catch (err) {
                console.error(err);
                alert(err.message || `Failed to stop ${scriptName}`);
            } finally {
                if (stopBtn) stopBtn.disabled = false;
            }
        };

        const loadSettings = async () => {
            try {
                const data = await fetchJson(`/api/${monitor}-monitor/settings`);
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

        const saveSettings = async () => {
            const body = {
                wait_seconds: Number(waitInput?.value || 0),
                percent_threshold: Number(thresholdInput?.value || 0),
            };
            if (saveSettingsBtn) saveSettingsBtn.disabled = true;
            if (reloadSettingsBtn) reloadSettingsBtn.disabled = true;
            if (testAlertBtn) testAlertBtn.disabled = true;
            setSettingsBadge(settingsStatus, 'Saving...');
            try {
                const data = await fetchJson(`/api/${monitor}-monitor/settings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
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

        const sendTestAlert = async () => {
            if (testAlertBtn) testAlertBtn.disabled = true;
            setSettingsBadge(settingsStatus, 'Sending test...');
            try {
                const resp = await fetch(`/api/${monitor}-monitor/push-test`, { method: 'POST' });
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

        setupCustomAlerts(monitor, customAlertsContainer);

        return { refreshStatus, pollLogs, loadSettings };
    };

    const controllers = [
        createMonitorController({
            monitor: 'bybit',
            scriptName: 'bybit_monitor',
            statusId: 'bybit-status',
            startId: 'bybit-start-btn',
            stopId: 'bybit-stop-btn',
            logId: 'bybit-log-box',
            waitId: 'bybit-wait-seconds',
            thresholdId: 'bybit-threshold',
            saveId: 'bybit-save-settings',
            reloadId: 'bybit-reload-settings',
            testId: 'bybit-test-alert',
            settingsStatusId: 'bybit-settings-status',
            customAlertsId: 'bybit-custom-alerts',
        }),
        createMonitorController({
            monitor: 'oanda',
            scriptName: 'oanda_monitor',
            statusId: 'oanda-status',
            startId: 'oanda-start-btn',
            stopId: 'oanda-stop-btn',
            logId: 'oanda-log-box',
            waitId: 'oanda-wait-seconds',
            thresholdId: 'oanda-threshold',
            saveId: 'oanda-save-settings',
            reloadId: 'oanda-reload-settings',
            testId: 'oanda-test-alert',
            settingsStatusId: 'oanda-settings-status',
            customAlertsId: 'oanda-custom-alerts',
        }),
    ];

    const init = async () => {
        for (const controller of controllers) {
            await controller.refreshStatus();
            await controller.pollLogs();
            await controller.loadSettings();
        }
        setInterval(() => {
            controllers.forEach((controller) => {
                controller.refreshStatus();
                controller.pollLogs();
            });
        }, 2000);
    };

    init();
})();
