(() => {
    const grid = document.getElementById('grid');
    const status = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh-btn');

    const CATEGORIES = ['Excel', 'Forex', 'Crypto', 'Other'];

    let scriptsCache = [];
    let selectedCategory = null;
    let refreshInFlight = null;

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

    const modify = async (name, action, button) => {
        if (button) button.disabled = true;
        try {
            const response = await fetch(`/scripts/${buildScriptPath(name)}/${action}`, { method: 'POST' });
            const payload = await response.json();

            if (!response.ok) {
                const detail = payload?.detail || response.statusText;
                throw new Error(detail);
            }

            // If the backend tells us to use another page (e.g., payslip upload), honor it.
            if (payload.redirect) {
                window.open(payload.redirect, '_blank');
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
                startBtn.onclick = () => modify(script.name, 'start', startBtn);
                const stopBtn = document.createElement('button');
                stopBtn.className = 'stop';
                stopBtn.textContent = 'Stop';
                stopBtn.onclick = () => modify(script.name, 'stop', stopBtn);
                actions.appendChild(startBtn);
                actions.appendChild(stopBtn);
            }

            card.appendChild(actions);

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
            openBtn.textContent = 'Open';
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
