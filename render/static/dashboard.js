(() => {
    const refreshBtn = document.getElementById('refresh-btn');
    const status = document.getElementById('status');

    const forexList = document.getElementById('forex-scripts');
    const cryptoList = document.getElementById('crypto-scripts');
    const otherList = document.getElementById('other-scripts');

    const forexCount = document.getElementById('forex-count');
    const cryptoCount = document.getElementById('crypto-count');
    const otherCount = document.getElementById('other-count');

    let scriptsCache = [];
    let refreshInFlight = null;

    const setStatus = (message, isError = false) => {
        if (!status) return;
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

    const renderCount = (el, scripts) => {
        if (!el) return;
        const running = scripts.filter((s) => s.running).length;
        el.textContent = `${running}/${scripts.length} running`;
        el.classList.remove('running', 'stopped');
        el.classList.add(running ? 'running' : 'stopped');
    };

    const makeScriptButton = (script, compact = false) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `script-btn${script.running ? ' running' : ''}${compact ? ' compact' : ''}`;

        const name = document.createElement('div');
        name.className = 'script-name';
        name.textContent = script.name;

        const pill = document.createElement('span');
        pill.className = `status-pill ${script.running ? 'running' : 'stopped'}`;
        pill.textContent = script.running ? 'Running' : 'Stopped';

        btn.appendChild(name);
        btn.appendChild(pill);

        btn.addEventListener('click', () => {
            window.location.href = `/scripts/view/${encodeURIComponent(script.name)}`;
        });

        return btn;
    };

    const renderList = (container, scripts, compact = false) => {
        if (!container) return;
        container.innerHTML = '';

        if (!scripts.length) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No scripts found.';
            container.appendChild(empty);
            return;
        }

        scripts
            .slice()
            .sort((a, b) => String(a.name).localeCompare(String(b.name)))
            .forEach((script) => {
                container.appendChild(makeScriptButton(script, compact));
            });
    };

    const renderHome = () => {
        const forex = scriptsCache.filter((s) => s.category === 'Forex');
        const crypto = scriptsCache.filter((s) => s.category === 'Crypto');
        const other = scriptsCache.filter((s) => s.category === 'Other');

        renderCount(forexCount, forex);
        renderCount(cryptoCount, crypto);
        renderCount(otherCount, other);

        renderList(forexList, forex, false);
        renderList(cryptoList, crypto, false);
        renderList(otherList, other, true);
    };

    const refresh = async () => {
        if (refreshInFlight) return refreshInFlight;

        setStatus('Loading scripts...');
        refreshBtn && (refreshBtn.textContent = 'Refresh');

        refreshInFlight = (async () => {
            try {
                scriptsCache = await fetchJson('/scripts');
                renderHome();
                setStatus(`Updated ${new Date().toLocaleTimeString()}`);
            } catch (err) {
                console.error(err);
                renderHome();
                setStatus('Failed to load scripts.', true);
            } finally {
                refreshInFlight = null;
            }
        })();

        return refreshInFlight;
    };

    refreshBtn?.addEventListener('click', () => refresh());

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());

    setInterval(() => refresh(), 5000);

    renderHome();
    refresh();
})();
