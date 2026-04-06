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
            button.textContent = script.label || script.name;
            button.onclick = () => {
                const target = script.open_url || `/scripts/view/${buildScriptPath(script.name)}`;
                if (script.standalone || target === '/trading-journal') {
                    window.open(target, '_blank', 'noopener');
                    return;
                }
                window.location.href = target;
            };
            if (script.running) {
                button.classList.add('running');
            } else if (script.starting) {
                button.classList.add('starting');
            }

            const status = document.createElement('span');
            status.className = 'status-pill';
            status.textContent = script.running ? 'Running' : (script.starting ? 'Starting...' : 'Stopped');
            if (script.running) {
                status.classList.add('running');
            } else if (script.starting) {
                status.classList.add('starting');
            } else {
                status.classList.add('stopped');
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

    load();
    setInterval(load, 5000);
})();
