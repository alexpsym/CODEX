(() => {
    const VALID_MONITORS = new Set(['bybit', 'oanda']);

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

    const normalizeMonitor = (value) => (VALID_MONITORS.has(value) ? value : 'bybit');

    const setupCustomAlerts = ({ container, getMonitor }) => {
        if (!container) return { loadAlerts: async () => {}, resetForMonitor: () => {} };

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
        statusBadge.textContent = ' ';
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

        const symbolInput = document.createElement('input');
        symbolInput.type = 'text';

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
        targetPriceInput.type = 'number'; targetPriceInput.step = 'any'; targetPriceInput.min = '0'; targetPriceInput.placeholder = 'Target price';
        const thresholdInput = document.createElement('input');
        thresholdInput.type = 'number'; thresholdInput.step = 'any'; thresholdInput.min = '0'; thresholdInput.placeholder = 'Move threshold';
        const unitSelect = document.createElement('select');
        const windowSelect = document.createElement('select');
        [
            { value: '60', label: '1 minute' }, { value: '300', label: '5 minutes' }, { value: '900', label: '15 minutes' },
            { value: '3600', label: '1 hour' }, { value: '14400', label: '4 hours' }, { value: '86400', label: '24 hours' },
        ].forEach((item) => { const o=document.createElement('option'); o.value=item.value; o.textContent=item.label; windowSelect.appendChild(o); });

        const cooldownInput = document.createElement('input'); cooldownInput.type='number'; cooldownInput.min='0'; cooldownInput.step='1'; cooldownInput.value='0';
        const messageInput = document.createElement('input'); messageInput.type='text'; messageInput.placeholder='Optional custom message';
        const enabledInput = document.createElement('input'); enabledInput.type='checkbox'; enabledInput.checked=true;

        formGrid.append(makeLabel('Symbol', symbolInput), makeLabel('Type', kindSelect), makeLabel('Price direction', priceDirectionSelect), makeLabel('Move direction', moveDirectionSelect), makeLabel('Target price', targetPriceInput), makeLabel('Move threshold', thresholdInput), makeLabel('Unit', unitSelect), makeLabel('Window', windowSelect), makeLabel('Cooldown (seconds)', cooldownInput), makeLabel('Custom message', messageInput), makeLabel('Enabled', enabledInput));
        section.appendChild(formGrid);

        const actions=document.createElement('div'); actions.className='row';
        const saveBtn=document.createElement('button'); saveBtn.type='button'; saveBtn.textContent='Save alert';
        const clearBtn=document.createElement('button'); clearBtn.type='button'; clearBtn.textContent='Reset';
        actions.append(saveBtn, clearBtn); section.appendChild(actions);
        const table=document.createElement('table'); table.style.width='100%'; table.style.borderCollapse='collapse'; table.style.marginTop='8px'; section.appendChild(table);
        container.appendChild(section);

        let editingId = null;
        let alertLoadSeq = 0;

        const updateUnitOptions = () => {
            const monitor = getMonitor();
            const isBybit = monitor === 'bybit';
            symbolInput.placeholder = isBybit ? 'BTC or BTCUSDT' : 'EUR_USD';
            const options = isBybit ? [{ value: 'pct', label: 'Percent (%)' }, { value: 'abs', label: 'Absolute move' }] : [{ value: 'pips', label: 'Pips' }, { value: 'pct', label: 'Percent (%)' }];
            const prev = unitSelect.value;
            unitSelect.innerHTML = '';
            options.forEach((item) => { const o = document.createElement('option'); o.value = item.value; o.textContent = item.label; unitSelect.appendChild(o); });
            unitSelect.value = options.some((item) => item.value === prev) ? prev : options[0].value;
        };

        const toggleFields = () => {
            const isPrice = kindSelect.value === 'price';
            priceDirectionSelect.disabled = !isPrice; targetPriceInput.disabled = !isPrice;
            moveDirectionSelect.disabled = isPrice; thresholdInput.disabled = isPrice; unitSelect.disabled = isPrice; windowSelect.disabled = isPrice;
        };

        const resetForm = () => { editingId=null; symbolInput.value=''; kindSelect.value='price'; priceDirectionSelect.value='above'; moveDirectionSelect.value='up'; targetPriceInput.value=''; thresholdInput.value=''; unitSelect.selectedIndex=0; windowSelect.value='900'; cooldownInput.value='0'; messageInput.value=''; enabledInput.checked=true; saveBtn.textContent='Save alert'; toggleFields(); };
        const parseRequiredNumber = (input, name) => { const value = Number(input.value); if (!Number.isFinite(value)) throw new Error(`${name} must be numeric`); return value; };
        const rowText = (customAlert) => customAlert.kind === 'price' ? `${customAlert.symbol} ${customAlert.direction} ${customAlert.target_price}` : `${customAlert.symbol} ${customAlert.direction} ${customAlert.threshold} ${customAlert.unit} in ${customAlert.window_seconds}s`;

        const resolveBybitSymbol = async (raw) => {
            const symbol = String(raw || '').trim().toUpperCase();
            if (!symbol || getMonitor() !== 'bybit') return symbol;
            const resp = await fetch(`/api/resolve-symbol?symbol=${encodeURIComponent(symbol)}&prefer=bybit&scope=linear`, { cache: 'no-store' });
            if (!resp.ok) throw new Error(`Unable to resolve symbol: ${symbol}`);
            const data = await resp.json().catch(() => null);
            const resolved = String(data?.resolved_symbol || '').trim().toUpperCase();
            if (!resolved) throw new Error(`Unable to resolve symbol: ${symbol}`);
            return resolved;
        };

        const loadAlerts = async () => {
            const monitor = getMonitor();
            const req = ++alertLoadSeq;
            try {
                setSettingsBadge(statusBadge, 'Loading...');
                const payload = await fetchJson(`/api/${monitor}-alerts/custom-alerts`);
                if (req !== alertLoadSeq || monitor !== getMonitor()) return;
                const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
                table.innerHTML = '';
                const tbody = document.createElement('tbody');
                alerts.forEach((alertItem) => {
                    const tr = document.createElement('tr'); tr.style.borderTop = '1px solid #334155';
                    const tdMain = document.createElement('td'); tdMain.style.padding='8px'; tdMain.textContent=rowText(alertItem);
                    const tdActions = document.createElement('td'); tdActions.style.padding='8px'; tdActions.style.whiteSpace='nowrap';
                    const enabledBtn = document.createElement('button'); enabledBtn.type='button'; enabledBtn.textContent=alertItem.enabled ? 'Disable' : 'Enable';
                    enabledBtn.addEventListener('click', async () => { enabledBtn.disabled=true; try { await fetchJson(`/api/${monitor}-alerts/custom-alerts/${encodeURIComponent(alertItem.id)}/enabled`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ enabled: !alertItem.enabled }) }); await loadAlerts(); } catch (err) { console.error(err); setSettingsBadge(statusBadge, 'Toggle failed', true); window.alert(err.message || 'Unable to update alert'); } finally { enabledBtn.disabled=false; } });
                    const editBtn = document.createElement('button'); editBtn.type='button'; editBtn.textContent='Edit';
                    editBtn.addEventListener('click', () => { editingId=alertItem.id; symbolInput.value=alertItem.symbol||''; kindSelect.value=alertItem.kind||'price'; priceDirectionSelect.value=alertItem.direction||'above'; moveDirectionSelect.value=alertItem.direction||'up'; targetPriceInput.value=alertItem.target_price??''; thresholdInput.value=alertItem.threshold??''; unitSelect.value=alertItem.unit||unitSelect.options[0].value; windowSelect.value=String(alertItem.window_seconds||900); cooldownInput.value=String(alertItem.cooldown_seconds||0); messageInput.value=alertItem.message||''; enabledInput.checked=Boolean(alertItem.enabled); saveBtn.textContent='Update alert'; toggleFields(); });
                    const deleteBtn = document.createElement('button'); deleteBtn.type='button'; deleteBtn.textContent='Delete';
                    deleteBtn.addEventListener('click', async () => { if (!window.confirm('Delete this custom alert?')) return; deleteBtn.disabled=true; try { await fetchJson(`/api/${monitor}-alerts/custom-alerts/${encodeURIComponent(alertItem.id)}`, { method:'DELETE' }); if (editingId===alertItem.id) resetForm(); await loadAlerts(); } catch (err) { console.error(err); setSettingsBadge(statusBadge, 'Delete failed', true); window.alert(err.message || 'Unable to delete alert'); } finally { deleteBtn.disabled=false; } });
                    tdActions.append(enabledBtn, editBtn, deleteBtn); tr.append(tdMain, tdActions); tbody.appendChild(tr);
                });
                table.appendChild(tbody);
                setSettingsBadge(statusBadge, 'Loaded');
            } catch (err) {
                console.error(err); setSettingsBadge(statusBadge, 'Alerts load failed', true); window.alert(err.message || 'Unable to load custom alerts');
            }
        };

        kindSelect.addEventListener('change', toggleFields);
        clearBtn.addEventListener('click', resetForm);
        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true; clearBtn.disabled = true;
            try {
                let symbol = symbolInput.value.trim().toUpperCase(); if (!symbol) throw new Error('Symbol is required');
                symbol = await resolveBybitSymbol(symbol); symbolInput.value = symbol;
                const kind = kindSelect.value; const payload = { id: editingId || undefined, symbol, kind, enabled: enabledInput.checked, cooldown_seconds: cooldownInput.value ? parseRequiredNumber(cooldownInput, 'Cooldown seconds') : 0 };
                if (kind === 'price') { const target = parseRequiredNumber(targetPriceInput, 'Target price'); if (target <= 0) throw new Error('Target price must be greater than zero'); payload.direction = priceDirectionSelect.value; payload.target_price = target; const customMessage = messageInput.value.trim(); if (customMessage) payload.message = customMessage; }
                else { const threshold = parseRequiredNumber(thresholdInput, 'Move threshold'); if (threshold <= 0) throw new Error('Move threshold must be greater than zero'); const windowSeconds = Number(windowSelect.value); if (!Number.isFinite(windowSeconds) || windowSeconds <= 0) throw new Error('Window must be selected'); payload.direction = moveDirectionSelect.value; payload.threshold = threshold; payload.unit = unitSelect.value; payload.window_seconds = windowSeconds; }
                const monitor = getMonitor();
                setSettingsBadge(statusBadge, 'Saving...');
                await fetchJson(`/api/${monitor}-alerts/custom-alerts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                resetForm(); await loadAlerts(); setSettingsBadge(statusBadge, 'Saved');
            } catch (err) { console.error(err); setSettingsBadge(statusBadge, 'Save failed', true); window.alert(err.message || 'Unable to save alert'); }
            finally { saveBtn.disabled = false; clearBtn.disabled = false; }
        });

        const resetForMonitor = () => { alertLoadSeq += 1; table.innerHTML = ''; setSettingsBadge(statusBadge, 'Loading...'); updateUnitOptions(); resetForm(); };
        updateUnitOptions(); resetForm();
        return { loadAlerts, resetForMonitor };
    };

    const createMonitorController = () => {
        const monitorTargetEl = document.getElementById('monitor-target');
        const statusEl = document.getElementById('monitor-status');
        const healthEl = document.getElementById('monitor-health');
        const waitInput = document.getElementById('monitor-wait-seconds');
        const thresholdInput = document.getElementById('monitor-threshold');
        const saveSettingsBtn = document.getElementById('monitor-save-settings');
        const reloadSettingsBtn = document.getElementById('monitor-reload-settings');
        const testAlertBtn = document.getElementById('monitor-test-alert');
        const settingsStatus = document.getElementById('monitor-settings-status');
        const customAlertsContainer = document.getElementById('monitor-custom-alerts');

        const getMonitor = () => normalizeMonitor(monitorTargetEl?.value);
        if (monitorTargetEl) monitorTargetEl.value = getMonitor();
        let statusSeq = 0;
        let settingsSeq = 0;

        const customAlerts = setupCustomAlerts({ container: customAlertsContainer, getMonitor });
        const setRunningState = (state) => { const running = state === 'running'; const unavailable = state === 'unavailable'; if (statusEl) { statusEl.textContent = running ? 'Running' : (unavailable ? 'Status unavailable' : 'Stopped'); statusEl.style.background = running ? '#14532d' : (unavailable ? '#7f1d1d' : '#1f2937'); } };
        const updateHealth = (payload) => { if (!healthEl) return; const phase = String(payload?.phase || 'unknown'); const heartbeat = String(payload?.last_heartbeat_at || 'n/a'); const heartbeatFresh = payload?.heartbeat_fresh === true ? 'yes' : (payload?.heartbeat_fresh === false ? 'no' : 'unknown'); const pidAlive = payload?.pid_alive === true ? 'yes' : (payload?.pid_alive === false ? 'no' : 'unknown'); const reason = payload?.reason ? ` | Reason: ${payload.reason}` : ''; const error = payload?.error ? ` | Error: ${payload.error}` : ''; healthEl.textContent = `Phase: ${phase} | Heartbeat: ${heartbeat} | Fresh: ${heartbeatFresh} | PID alive: ${pidAlive}${reason}${error}`; };

        const refreshStatus = async () => { const monitor = getMonitor(); const req = ++statusSeq; try { const payload = await fetchJson(`/api/${monitor}-alerts/status`); if (req !== statusSeq || monitor !== getMonitor()) return; const uiStatus = String(payload?.ui_status || '').toLowerCase(); setRunningState(uiStatus === 'running' ? 'running' : (uiStatus === 'unavailable' ? 'unavailable' : 'stopped')); updateHealth(payload || {}); } catch (err) { if (req !== statusSeq || monitor !== getMonitor()) return; console.error(err); setRunningState('unavailable'); updateHealth({ reason: 'request_failed', error: err?.message || String(err || 'Unknown error') }); } };
        const loadSettings = async () => { const monitor = getMonitor(); const req = ++settingsSeq; try { const data = await fetchJson(`/api/${monitor}-alerts/settings`); if (req !== settingsSeq || monitor !== getMonitor()) return; if (waitInput) waitInput.value = data.wait_seconds ?? ''; if (thresholdInput) thresholdInput.value = data.percent_threshold ?? ''; setSettingsBadge(settingsStatus, data.push_ready ? 'Ready' : 'Telegram not configured', !data.push_ready); } catch (err) { if (req !== settingsSeq || monitor !== getMonitor()) return; console.error(err); setSettingsBadge(settingsStatus, 'Load failed', true); window.alert(err.message || 'Unable to load settings'); } };
        const saveSettings = async () => { const monitor = getMonitor(); const body = { wait_seconds: Number(waitInput?.value || 0), percent_threshold: Number(thresholdInput?.value || 0) }; saveSettingsBtn.disabled = true; reloadSettingsBtn.disabled = true; testAlertBtn.disabled = true; setSettingsBadge(settingsStatus, 'Saving...'); try { const data = await fetchJson(`/api/${monitor}-alerts/settings`, { method:'POST', headers:{ 'Content-Type':'application/json' }, body:JSON.stringify(body) }); if (waitInput) waitInput.value = data.wait_seconds ?? ''; if (thresholdInput) thresholdInput.value = data.percent_threshold ?? ''; setSettingsBadge(settingsStatus, 'Saved'); } catch (err) { console.error(err); setSettingsBadge(settingsStatus, 'Save failed', true); window.alert(err.message || 'Unable to save settings'); } finally { saveSettingsBtn.disabled = false; reloadSettingsBtn.disabled = false; testAlertBtn.disabled = false; } };
        const sendTestAlert = async () => { const monitor = getMonitor(); testAlertBtn.disabled = true; setSettingsBadge(settingsStatus, 'Sending test...'); try { const resp = await fetch(`/api/${monitor}-alerts/push-test`, { method:'POST' }); const bodyText = await resp.text(); let data = null; if (bodyText) { try { data = JSON.parse(bodyText); } catch (_err) { data = { detail: bodyText }; } } const detail = data?.detail || bodyText || `HTTP ${resp.status}`; if (data?.sent) { setSettingsBadge(settingsStatus, 'Test sent'); return; } if (data?.configured === false) { setSettingsBadge(settingsStatus, `Telegram not configured: ${detail}`, true); return; } setSettingsBadge(settingsStatus, `Test failed: ${detail}`, true); if (!resp.ok) throw new Error(detail || `Test failed (${resp.status})`); } catch (err) { console.error(err); if (!String(settingsStatus?.textContent || '').startsWith('Test failed') && !String(settingsStatus?.textContent || '').startsWith('Telegram not configured')) { setSettingsBadge(settingsStatus, `Test failed: ${err.message || String(err)}`, true); } } finally { testAlertBtn.disabled = false; } };

        const onMonitorChange = () => {
            if (monitorTargetEl) monitorTargetEl.value = getMonitor();
            statusSeq += 1; settingsSeq += 1;
            setRunningState('unavailable');
            updateHealth({ phase: 'checking', reason: 'monitor_changed' });
            setSettingsBadge(settingsStatus, 'Loading...');
            customAlerts.resetForMonitor();
            refreshStatus(); loadSettings(); customAlerts.loadAlerts();
        };

        monitorTargetEl?.addEventListener('change', onMonitorChange);
        saveSettingsBtn?.addEventListener('click', (event) => { event.preventDefault(); saveSettings(); });
        reloadSettingsBtn?.addEventListener('click', (event) => { event.preventDefault(); loadSettings(); });
        testAlertBtn?.addEventListener('click', (event) => { event.preventDefault(); sendTestAlert(); });

        return { refreshStatus, loadSettings, loadAlerts: customAlerts.loadAlerts, onMonitorChange };
    };

    const init = async () => {
        const controller = createMonitorController();
        controller.onMonitorChange();
        setInterval(() => { controller.refreshStatus(); }, 2000);
    };

    init().catch((err) => {
        console.error('Merged alerts page initialization failed', err);
        const statusEl = document.getElementById('monitor-status');
        const healthEl = document.getElementById('monitor-health');
        const settingsStatus = document.getElementById('monitor-settings-status');
        if (statusEl) statusEl.textContent = 'Page init failed';
        if (healthEl) healthEl.textContent = `Init error: ${err?.message || String(err)}`;
        setSettingsBadge(settingsStatus, 'Init failed', true);
    });
})();
