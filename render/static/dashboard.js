(() => {
    const grid = document.getElementById('grid');
    const status = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh-btn');

    const CATEGORIES = ['Excel', 'Forex', 'Crypto', 'Other'];

    let scriptsCache = [];
    let selectedCategory = null;
    let refreshInFlight = null;
    let bybitSettingsEditing = false;

    const setStatus = (message, isError = false) => {
        status.textContent = message;
        status.style.color = isError ? '#fca5a5' : '#94a3b8';
    };

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        if (!response.ok) {
            const body = await response.text();
            const detail = body || response.statusText;
            throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}: ${detail}`);
        }
        return response.json();
    };

    const statusPill = (script) => {
        const pill = document.createElement('span');
        pill.className = 'pill ' + (script.running ? 'running' : 'stopped');
        pill.textContent = script.running ? 'Running' : 'Stopped';
        return pill;
    };

    const buildScriptPath = (name) => encodeURIComponent(name).replace(/%2F/g, '/');

    const modify = async (script, action, button) => {
        const name = script.name;

        if (button) button.disabled = true;
        try {
            const response = await fetch(`/scripts/${buildScriptPath(name)}/${action}`, { method: 'POST' });
            let payload = null;
            try {
                payload = await response.json();
            } catch (err) {
                console.warn('Non-JSON response from script action', err);
            }

            if (!response.ok) {
                const detail = payload?.detail || response.statusText;
                throw new Error(detail);
            }

            const targetUrl = payload?.open_url || script.open_url || `/logs/view/${buildScriptPath(name)}`;

            // If the backend tells us to use another page (e.g., payslip upload), honor it.
            if (payload?.redirect) {
                window.location.href = payload.redirect;
                return;
            }

            if (action === 'start') {
                window.location.href = targetUrl;
                return;
            }

            await refresh();
        } catch (err) {
            console.error(err);
            alert(`Failed to ${action} ${name}: ${err.message}`);
        } finally {
            if (button) button.disabled = false;
        }
    };

    const renderScriptsForCategory = (category) => {
        selectedCategory = category;
        refreshBtn.textContent = 'Back to categories';

        const scripts = scriptsCache.filter((s) => s.category === category);
        const label = scripts.length === 1 ? 'script' : 'scripts';
        setStatus(`${category}: ${scripts.length} ${label}`);

        grid.innerHTML = '';

        scripts.forEach((script) => {
            const card = document.createElement('div');
            card.className = 'card';

            const header = document.createElement('div');
            header.className = 'row';
            const title = document.createElement('div');
            title.innerHTML = `<strong>${script.name}</strong><div class="path">${script.path}</div>`;
            header.appendChild(title);
            header.appendChild(statusPill(script));
            card.appendChild(header);

            const actions = document.createElement('div');
            actions.className = 'actions';

            let persistBybitSettings = null;
            let setSettingsStatus = null;
            let loadSettings = null;

            if (script.name === 'payslip_audit') {
                const uploadBtn = document.createElement('button');
                uploadBtn.className = 'start';
                uploadBtn.textContent = 'Open Upload Page';
                uploadBtn.onclick = () => {
                    window.open('/payslip-audit', '_blank');
                };
                actions.appendChild(uploadBtn);
            } else {
                const startBtn = document.createElement('button');
                startBtn.className = 'start';
                startBtn.textContent = 'Start';
                startBtn.onclick = async () => {
                    if (persistBybitSettings) {
                        const ok = await persistBybitSettings();
                        if (!ok) {
                            alert('Please fix Bybit monitor settings before starting.');
                            return;
                        }
                    }
                    await modify(script, 'start', startBtn);
                };
                const stopBtn = document.createElement('button');
                stopBtn.className = 'stop';
                stopBtn.textContent = 'Stop';
                stopBtn.onclick = () => modify(script, 'stop', stopBtn);
                actions.appendChild(startBtn);
                actions.appendChild(stopBtn);
            }

            card.appendChild(actions);

            const showSettings = script.name.replace(/-/g, '_') === 'bybit_monitor';

            if (showSettings) {
                const settingsCard = document.createElement('div');
                settingsCard.className = 'settings-card';

                const settingsHeader = document.createElement('div');
                settingsHeader.className = 'row settings-header';

                const settingsTitle = document.createElement('div');
                settingsTitle.innerHTML = '<strong>Bybit monitor settings</strong><div class="path">Adjust before starting</div>';

                const settingsBadge = document.createElement('span');
                settingsBadge.className = 'badge';
                settingsBadge.textContent = 'Loading...';

                settingsHeader.appendChild(settingsTitle);
                settingsHeader.appendChild(settingsBadge);
                settingsCard.appendChild(settingsHeader);

                const settingsGrid = document.createElement('div');
                settingsGrid.className = 'settings-grid';

                const waitLabel = document.createElement('label');
                waitLabel.textContent = 'Wait between scans (seconds)';
                const waitInput = document.createElement('input');
                waitInput.type = 'number';
                waitInput.min = '1';
                waitInput.step = '1';
                waitLabel.appendChild(waitInput);

                const thresholdLabel = document.createElement('label');
                thresholdLabel.textContent = 'Alert threshold (% change)';
                const thresholdInput = document.createElement('input');
                thresholdInput.type = 'number';
                thresholdInput.min = '0.1';
                thresholdInput.step = '0.1';
                thresholdLabel.appendChild(thresholdInput);

                settingsGrid.appendChild(waitLabel);
                settingsGrid.appendChild(thresholdLabel);
                settingsCard.appendChild(settingsGrid);

                const settingsActions = document.createElement('div');
                settingsActions.className = 'actions';

                const saveBtn = document.createElement('button');
                saveBtn.textContent = 'Save settings';
                saveBtn.className = 'start';

                const resetBtn = document.createElement('button');
                resetBtn.textContent = 'Reset';
                resetBtn.className = 'secondary';

                settingsActions.appendChild(saveBtn);
                settingsActions.appendChild(resetBtn);
                settingsCard.appendChild(settingsActions);

                setSettingsStatus = (text, isError = false) => {
                    settingsBadge.textContent = text;
                    settingsBadge.className = 'badge ' + (isError ? 'badge-error' : '');
                };

                const markEditing = () => {
                    bybitSettingsEditing = true;
                    setSettingsStatus('Editing');
                };

                loadSettings = async () => {
                    try {
                        const data = await fetchJson('/api/bybit-monitor/settings');
                        if (!bybitSettingsEditing) {
                            waitInput.value = data.wait_seconds ?? '';
                            thresholdInput.value = data.percent_threshold ?? '';
                        }
                        bybitSettingsEditing = false;
                        setSettingsStatus('Ready');
                    } catch (err) {
                        console.error(err);
                        setSettingsStatus('Load failed', true);
                    }
                };

                const saveSettings = async (opts = {}) => {
                    const body = {
                        wait_seconds: Number(waitInput.value || 0),
                        percent_threshold: Number(thresholdInput.value || 0),
                    };

                    saveBtn.disabled = true;
                    resetBtn.disabled = true;
                    setSettingsStatus('Saving...');

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
                        waitInput.value = data.wait_seconds ?? '';
                        thresholdInput.value = data.percent_threshold ?? '';
                        bybitSettingsEditing = false;
                        setSettingsStatus('Saved');
                        return true;
                    } catch (err) {
                        console.error(err);
                        setSettingsStatus('Save failed', true);
                        if (!opts.silent) alert(err.message || 'Unable to save settings');
                        return false;
                    } finally {
                        saveBtn.disabled = false;
                        resetBtn.disabled = false;
                    }
                };

                saveBtn.onclick = (event) => {
                    event.preventDefault();
                    saveSettings();
                };

                resetBtn.onclick = (event) => {
                    event.preventDefault();
                    bybitSettingsEditing = false;
                    loadSettings();
                };

                waitInput.addEventListener('input', markEditing);
                thresholdInput.addEventListener('input', markEditing);

                persistBybitSettings = () => saveSettings({ silent: true });

                card.appendChild(settingsCard);
                loadSettings();
            }

            const lastOutput = document.createElement('div');
            lastOutput.className = 'path';
            const secondsAgo = script.last_output_at
                ? Math.max(0, Math.floor(Date.now() / 1000 - script.last_output_at))
                : null;
            lastOutput.textContent = secondsAgo === null
                ? 'last output: no log lines yet'
                : `last output: ${secondsAgo}s ago`;
            card.appendChild(lastOutput);

            grid.appendChild(card);
        });
    };

    const renderCategories = () => {
        selectedCategory = null;
        refreshBtn.textContent = 'Refresh';
        setStatus('Select a category to manage scripts');

        grid.innerHTML = '';
        CATEGORIES.forEach((category) => {
            const matching = scriptsCache.filter((s) => s.category === category);

            const card = document.createElement('div');
            card.className = 'card';

            const header = document.createElement('div');
            header.className = 'row';
            const title = document.createElement('div');
            title.innerHTML = `<strong>${category}</strong><div class="path">${matching.length} scripts</div>`;
            header.appendChild(title);
            card.appendChild(header);

            const actions = document.createElement('div');
            actions.className = 'actions';
            const openBtn = document.createElement('button');
            openBtn.className = 'start';
            openBtn.textContent = 'View scripts';
            openBtn.onclick = () => renderScriptsForCategory(category);
            actions.appendChild(openBtn);
            card.appendChild(actions);

            grid.appendChild(card);
        });
    };

    // Backwards compatibility for any cached script bundle that still references the older
    // renderCategoryCards global helper. By assigning it here, we avoid ReferenceError crashes
    // that prevent the dashboard from initializing.
    const renderCategoryCards = () => renderCategories();
    // Expose both helpers on window so any inline handlers from a cached page still work.
    window.renderCategoryCards = renderCategoryCards;
    window.renderScriptsForCategory = (category) => renderScriptsForCategory(category);

    const refresh = async () => {
        if (refreshInFlight) return refreshInFlight;
        if (bybitSettingsEditing) {
            setStatus('Editing Bybit monitor settings (auto-refresh paused)');
            return Promise.resolve();
        }

        setStatus('Loading scripts...');
        refreshInFlight = (async () => {
            try {
                scriptsCache = await fetchJson('/scripts');
                if (selectedCategory) {
                    renderScriptsForCategory(selectedCategory);
                } else {
                    renderCategories();
                }
            } catch (err) {
                console.error(err);
                setStatus('Failed to load scripts.', true);
                if (!grid.children.length) {
                    renderCategories();
                }
            } finally {
                refreshInFlight = null;
            }
        })();

        return refreshInFlight;
    };

    refreshBtn?.addEventListener('click', () => {
        if (selectedCategory) {
            renderCategories();
        } else {
            refresh();
        }
    });

    setInterval(() => {
        refresh();
    }, 5000);

    renderCategoryCards(scriptsCache);
    refresh();
})();
