(() => {
    const grid = document.getElementById('grid');
    const category = document.body.dataset.category;

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

    const renderScripts = (scripts) => {
        grid.innerHTML = '';
        if (!scripts.length) {
            const empty = document.createElement('div');
            empty.className = 'meta';
            empty.textContent = 'No scripts found in this category.';
            grid.appendChild(empty);
            return;
        }

        scripts.forEach((script) => {
            const card = document.createElement('div');
            card.className = 'card';

            const button = document.createElement('button');
            button.className = 'script-btn';
            button.textContent = script.name;
            button.onclick = () => {
                if (script.name === 'cryptocalculator-clone') {
                    window.open(`/scripts/view/${buildScriptPath(script.name)}`, '_blank', 'noopener');
                    return;
                }
                window.location.href = `/scripts/view/${buildScriptPath(script.name)}`;
            };
            if (script.running) {
                button.classList.add('running');
            }

            const status = document.createElement('span');
            status.className = 'status-pill';
            status.textContent = script.running ? 'Running' : 'Stopped';
            if (script.running) {
                status.classList.add('running');
            }

            card.appendChild(button);
            card.appendChild(status);
            grid.appendChild(card);
        });
    };

    const load = async () => {
        try {
            const scripts = await fetchJson('/scripts');
            const filtered = scripts.filter((script) => script.category === category);
            renderScripts(filtered);
        } catch (err) {
            console.error(err);
            grid.innerHTML = '<div class="meta">Failed to load scripts.</div>';
        }
    };

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());

    load();
    setInterval(load, 5000);
})();
