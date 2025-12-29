(() => {
    const grid = document.getElementById('grid');
    const status = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh-btn');
    const openOrdersTable = document.getElementById('open-orders-table');
    const openOrdersBody = openOrdersTable?.querySelector('tbody');
    const openOrdersStatus = document.getElementById('open-orders-status');
    const openOrdersEmpty = document.getElementById('open-orders-empty');
    const openOrdersErrors = document.getElementById('open-orders-errors');
    const openOrdersErrorsList = openOrdersErrors?.querySelector('ul');

    const CATEGORIES = ['Excel', 'Forex', 'Crypto', 'Other'];

    let scriptsCache = [];
    let openOrdersCache = [];
    let refreshInFlight = null;
    let openOrdersInFlight = null;
    let bybitSettingsEditing = false;

    const setStatus = (message, isError = false) => {
        status.textContent = message;
        status.style.color = isError ? '#fca5a5' : '#94a3b8';
    };

    const setOrdersStatus = (message, tone = 'default') => {
        if (!openOrdersStatus) return;
        openOrdersStatus.textContent = message;
        openOrdersStatus.classList.remove('badge-error', 'badge-ok');
        if (tone === 'error') {
            openOrdersStatus.classList.add('badge-error');
        } else if (tone === 'ok') {
            openOrdersStatus.classList.add('badge-ok');
        }
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

    const formatValue = (value) => {
        if (value === null || value === undefined || value === '') return '—';
        return value;
    };

    const formatTimestamp = (value) => {
        if (!value) return '—';
        const numeric = Number(value);
        if (!Number.isNaN(numeric) && String(value).trim() !== '') {
            const timestamp = numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
            const date = new Date(timestamp);
            if (!Number.isNaN(date.getTime())) {
                return date.toLocaleString();
            }
        }
        const date = new Date(value);
        if (!Number.isNaN(date.getTime())) {
            return date.toLocaleString();
        }
        return value;
    };

    const renderOpenOrders = (items, errorCount = 0, errors = []) => {
        if (!openOrdersBody || !openOrdersTable) return;
        openOrdersBody.innerHTML = '';
        if (openOrdersErrors) {
            openOrdersErrors.style.display = errorCount ? 'block' : 'none';
        }
        if (openOrdersErrorsList) {
            openOrdersErrorsList.innerHTML = '';
            errors.forEach((entry) => {
                const item = document.createElement('li');
                const parts = [];
                if (entry.broker) parts.push(entry.broker);
                if (entry.account) parts.push(entry.account);
                if (entry.category) parts.push(entry.category);
                const prefix = parts.length ? `${parts.join(' / ')}: ` : '';
                item.textContent = `${prefix}${entry.message || 'Unknown error'}`;
                openOrdersErrorsList.appendChild(item);
            });
        }
        if (!items.length) {
            if (openOrdersEmpty) {
                openOrdersEmpty.textContent = errorCount
                    ? 'No open orders or trades (some sources unavailable).'
                    : 'No open orders or trades.';
            }
            openOrdersEmpty?.setAttribute('style', 'display:block;');
            return;
        }
        openOrdersEmpty?.setAttribute('style', 'display:none;');
        items.forEach((item) => {
            const row = document.createElement('tr');
            const cells = [
                item.broker,
                item.account,
                item.category,
                item.instrument,
                item.type,
                item.side,
                item.size,
                item.entry_price || item.order_price,
                item.current_price,
                item.stop_loss,
                item.take_profit,
                item.leverage,
                formatTimestamp(item.opened_at),
                item.id,
                item.status,
            ];
            cells.forEach((cell) => {
                const td = document.createElement('td');
                td.textContent = formatValue(cell);
                row.appendChild(td);
            });
            openOrdersBody.appendChild(row);
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

    const refreshOpenOrders = async () => {
        if (!openOrdersTable || openOrdersInFlight) return openOrdersInFlight;
        openOrdersInFlight = (async () => {
            try {
                const payload = await fetchJson('/api/open-orders');
                openOrdersCache = payload.items || [];
                const errorCount = (payload.errors || []).length;
                renderOpenOrders(openOrdersCache, errorCount, payload.errors || []);
                const updated = formatTimestamp(payload.updated_at);
                if (errorCount) {
                    setOrdersStatus(`Updated ${updated} • ${errorCount} source issue(s)`, 'error');
                } else {
                    setOrdersStatus(`Updated ${updated}`, 'ok');
                }
            } catch (err) {
                console.error(err);
                renderOpenOrders(openOrdersCache, 1, [{ message: err.message }]);
                setOrdersStatus('Failed to load open orders.', 'error');
            } finally {
                openOrdersInFlight = null;
            }
        })();

        return openOrdersInFlight;
    };

    refreshBtn?.addEventListener('click', () => {
        refresh();
        refreshOpenOrders();
    });

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());

    setInterval(() => {
        refresh();
        refreshOpenOrders();
    }, 5000);

    renderCategories();
    refresh();
    refreshOpenOrders();
})();
