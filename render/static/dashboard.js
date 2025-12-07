(() => {
    const grid = document.getElementById('grid');
    const status = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh-btn');

    const CATEGORIES = ['Excel', 'Forex', 'Crypto', 'Other'];

    let scriptsCache = [];
    let selectedCategory = null;

    const setStatus = (message, isError = false) => {
        status.textContent = message;
        status.style.color = isError ? '#fca5a5' : '#94a3b8';
    };

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}`);
        }
        return response.json();
    };

    const statusPill = (script) => {
        const pill = document.createElement('span');
        pill.className = 'pill ' + (script.running ? 'running' : 'stopped');
        pill.textContent = script.running ? 'Running' : 'Stopped';
        return pill;
    };

    const loadLogs = async (name, box) => {
        try {
            const lines = await fetchJson(`/logs/${encodeURIComponent(name)}`);
            box.textContent = lines.length ? lines.join('\n') : 'No logs yet.';
        } catch (err) {
            console.error(err);
            box.textContent = 'Failed to load logs.';
        }
    };

    const modify = async (name, action, button) => {
        if (button) button.disabled = true;
        try {
            await fetchJson(`/scripts/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
            await refresh();
        } catch (err) {
            console.error(err);
            alert(`Failed to ${action} ${name}: ${err.message}`);
        } finally {
            if (button) button.disabled = false;
        }
    };

    const renderScripts = (scripts) => {
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
            const startBtn = document.createElement('button');
            startBtn.className = 'start';
            startBtn.textContent = 'Start';
            startBtn.onclick = () => modify(script.name, 'start', startBtn);
            const stopBtn = document.createElement('button');
            stopBtn.className = 'stop';
            stopBtn.textContent = 'Stop';
            stopBtn.onclick = () => modify(script.name, 'stop', stopBtn);
            actions.appendChild(startBtn);
            actions.appendChild(stopBtn);
            card.appendChild(actions);

            const logBox = document.createElement('pre');
            logBox.textContent = 'Loading logs...';
            card.appendChild(logBox);

            grid.appendChild(card);
            loadLogs(script.name, logBox);
        });
    };

    const renderCategories = (scripts) => {
        grid.innerHTML = '';
        CATEGORIES.forEach((category) => {
            const matching = scripts.filter((s) => s.category === category);
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
            openBtn.textContent = 'Open';
            openBtn.onclick = () => {
                selectedCategory = category;
                refreshBtn.textContent = 'Back to categories';
                renderScripts(matching);
                const label = matching.length === 1 ? 'script' : 'scripts';
                setStatus(`${category}: ${matching.length} ${label}`);
            };
            actions.appendChild(openBtn);
            card.appendChild(actions);

            grid.appendChild(card);
        });
    };

    let refreshInFlight = null;

    const refresh = async () => {
        if (refreshInFlight) {
            return refreshInFlight;
        }

        setStatus('Loading scripts...');
        refreshInFlight = (async () => {
            try {
                scriptsCache = await fetchJson('/scripts');
                if (selectedCategory) {
                    const filtered = scriptsCache.filter((s) => s.category === selectedCategory);
                    renderScripts(filtered);
                    const label = filtered.length === 1 ? 'script' : 'scripts';
                    setStatus(`${selectedCategory}: ${filtered.length} ${label}`);
                } else {
                    renderCategories(scriptsCache);
                    refreshBtn.textContent = 'Refresh';
                    setStatus('Select a category to manage scripts');
                }
            } catch (err) {
                console.error(err);
                renderCategories(scriptsCache);
                setStatus('Failed to load scripts. Showing cached categories.', true);
            } finally {
                refreshInFlight = null;
            }
        })();

        return refreshInFlight;
    };

    refreshBtn?.addEventListener('click', () => {
        if (selectedCategory) {
            selectedCategory = null;
            renderCategories(scriptsCache);
            refreshBtn.textContent = 'Refresh';
            setStatus('Select a category to manage scripts');
        } else {
            refresh();
        }
    });

    setInterval(() => {
        refresh();
    }, 5000);

    refresh();
})();
