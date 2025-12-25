(() => {
    const grid = document.getElementById('grid');
    const status = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh-btn');

    const CATEGORIES = ['Excel', 'Forex', 'Crypto', 'Other'];

    let scriptsCache = [];
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

    const renderCategories = () => {
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
            openBtn.onclick = () => {
                window.location.href = `/category/${encodeURIComponent(category)}`;
            };
            actions.appendChild(openBtn);
            card.appendChild(actions);

            grid.appendChild(card);
        });
    };

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
                renderCategories();
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
        refresh();
    });

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());

    setInterval(() => {
        refresh();
    }, 5000);

    renderCategories();
    refresh();
})();
