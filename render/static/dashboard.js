(() => {
    const grid = document.getElementById('grid');
    const status = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh-btn');

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

    const openLogTab = (name) => {
        const url = `/log-view/${encodeURIComponent(name)}`;
        window.open(url, '_blank', 'noopener');
    };

    const modify = async (name, action, button) => {
        if (button) button.disabled = true;
        try {
            await fetchJson(`/scripts/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
            await refresh();
            if (action === 'start') {
                openLogTab(name);
            }
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

            const logControls = document.createElement('div');
            logControls.className = 'actions';
            const openLogBtn = document.createElement('button');
            openLogBtn.className = 'refresh';
            openLogBtn.textContent = 'Open Logs';
            openLogBtn.onclick = () => openLogTab(script.name);
            logControls.appendChild(openLogBtn);
            card.appendChild(logControls);

            grid.appendChild(card);
        });
    };

    const renderCategories = (scripts) => {
        const categories = Array.from(new Set(scripts.map((s) => s.category || 'Other'))).sort();
        grid.innerHTML = '';
        categories.forEach((category) => {
            const card = document.createElement('div');
            card.className = 'card';

            const header = document.createElement('div');
            header.className = 'row';
            const title = document.createElement('div');
            title.innerHTML = `<strong>${category}</strong><div class="path">${scripts.filter((s) => s.category === category).length} scripts</div>`;
            header.appendChild(title);
            card.appendChild(header);

            const actions = document.createElement('div');
            actions.className = 'actions';
            const openBtn = document.createElement('button');
            openBtn.className = 'start';
            openBtn.textContent = 'Open';
            openBtn.onclick = () => {
                selectedCategory = category;
                renderScripts(scriptsCache.filter((s) => s.category === category));
                const label = category === 'Other' ? 'scripts' : `${category} scripts`;
                setStatus(`${label}: ${scriptsCache.filter((s) => s.category === category).length}`);
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
                    renderScripts(scriptsCache.filter((s) => s.category === selectedCategory));
                    const filtered = scriptsCache.filter((s) => s.category === selectedCategory);
                    const label = filtered.length === 1 ? 'script' : 'scripts';
                    setStatus(`${selectedCategory}: ${filtered.length} ${label}`);
                } else {
                    renderCategories(scriptsCache);
                    setStatus('Select a category to manage scripts');
                }
            } catch (err) {
                console.error(err);
                grid.innerHTML = '';
                setStatus('Failed to load scripts. See console for details.', true);
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
