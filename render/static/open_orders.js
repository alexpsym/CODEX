(() => {
    const refreshBtn = document.getElementById('refresh-btn');
    const statusBadge = document.getElementById('open-orders-status');
    const table = document.getElementById('open-orders-table');
    const tbody = table?.querySelector('tbody');
    const emptyState = document.getElementById('open-orders-empty');
    const errorsBox = document.getElementById('open-orders-errors');
    const errorsList = errorsBox?.querySelector('ul');

    let openOrdersCache = [];
    let inFlight = null;

    const setBadge = (message, tone = 'default') => {
        if (!statusBadge) return;
        statusBadge.textContent = message;
        statusBadge.classList.remove('badge-error', 'badge-ok');
        if (tone === 'error') statusBadge.classList.add('badge-error');
        if (tone === 'ok') statusBadge.classList.add('badge-ok');
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

    const formatValue = (value) => {
        if (value === null || value === undefined || value === '') return '—';
        return value;
    };

    const toNumber = (value) => {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : null;
    };

    const formatDistance = (value, entryValue) => {
        const numeric = toNumber(value);
        const entry = toNumber(entryValue);
        if (numeric === null || entry === null) return value;
        return Math.abs(numeric - entry);
    };

    const formatTimestamp = (value) => {
        if (!value) return '—';
        const numeric = Number(value);
        if (!Number.isNaN(numeric) && String(value).trim() !== '') {
            const timestamp = numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
            const date = new Date(timestamp);
            if (!Number.isNaN(date.getTime())) return date.toLocaleString();
        }
        const date = new Date(value);
        if (!Number.isNaN(date.getTime())) return date.toLocaleString();
        return value;
    };

    const closeOpenItem = async (item, button, label) => {
        if (!button) return;
        button.disabled = true;
        const original = button.textContent;
        button.textContent = '...';
        try {
            await fetchJson('/api/open-orders/close', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(item),
            });
            await refresh();
            setBadge(`${label} request sent`, 'ok');
        } catch (err) {
            console.error(err);
            setBadge(`Failed to ${label.toLowerCase()}.`, 'error');
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    };

    const setPendingWebhookEnabled = async (item, button, enabled) => {
        if (!button) return;
        button.disabled = true;
        const original = button.textContent;
        button.textContent = '...';
        try {
            await fetchJson(`/api/pending-webhooks/${encodeURIComponent(item.id)}/enabled`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
            await refresh();
            setBadge(`Webhook ${enabled ? 'enabled' : 'disabled'}`, 'ok');
        } catch (err) {
            console.error(err);
            setBadge('Failed to update webhook.', 'error');
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    };

    const deletePendingWebhook = async (item, button) => {
        if (!button) return;
        button.disabled = true;
        const original = button.textContent;
        button.textContent = '...';
        try {
            await fetchJson(`/api/pending-webhooks/${encodeURIComponent(item.id)}`, {
                method: 'DELETE',
            });
            await refresh();
            setBadge('Webhook removed', 'ok');
        } catch (err) {
            console.error(err);
            setBadge('Failed to remove webhook.', 'error');
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    };

    const render = (items, errors = []) => {
        if (!tbody) return;
        tbody.innerHTML = '';

        if (errorsBox) errorsBox.style.display = errors.length ? 'block' : 'none';
        if (errorsList) {
            errorsList.innerHTML = '';
            errors.forEach((entry) => {
                const li = document.createElement('li');
                const parts = [];
                if (entry.broker) parts.push(entry.broker);
                if (entry.account) parts.push(entry.account);
                if (entry.category) parts.push(entry.category);
                const prefix = parts.length ? `${parts.join(' / ')}: ` : '';
                li.textContent = `${prefix}${entry.message || 'Unknown error'}`;
                errorsList.appendChild(li);
            });
        }

        if (!items.length) {
            if (emptyState) {
                emptyState.textContent = errors.length
                    ? 'No open orders or trades (some sources unavailable).'
                    : 'No open orders or trades.';
                emptyState.style.display = 'block';
            }
            return;
        }

        if (emptyState) emptyState.style.display = 'none';

        items.forEach((item) => {
            const row = document.createElement('tr');
            const t = String(item.type || '').trim().toLowerCase();
            const isWebhook = t === 'webhook';
            const status = String(item.status || '').trim().toUpperCase();
            const entryPrice = item.entry_price || item.order_price;
            const stopLoss =
                isWebhook && status === 'WAITING' ? formatDistance(item.stop_loss, entryPrice) : item.stop_loss;
            const takeProfit =
                isWebhook && status === 'WAITING'
                    ? formatDistance(item.take_profit, entryPrice)
                    : item.take_profit;
            const cells = [
                item.broker,
                item.account,
                item.category,
                item.instrument,
                item.type,
                item.side,
                item.size,
                entryPrice,
                item.current_price,
                stopLoss,
                takeProfit,
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

            const actionCell = document.createElement('td');
            actionCell.className = 'action-cell';

            const isOrder = t === 'order';
            const isPosition = t === 'position' || t === 'trade';

            if (isOrder || isPosition) {
                const label = isOrder ? 'Cancel' : 'Close';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'action-btn';
                button.textContent = label;
                button.addEventListener('click', () => closeOpenItem(item, button, label));
                actionCell.appendChild(button);
            } else if (isWebhook) {
                const enabled = item.enabled !== false;
                const toggleButton = document.createElement('button');
                toggleButton.type = 'button';
                toggleButton.className = 'action-btn';
                toggleButton.textContent = enabled ? 'Disable' : 'Enable';
                toggleButton.addEventListener('click', () =>
                    setPendingWebhookEnabled(item, toggleButton, !enabled),
                );
                actionCell.appendChild(toggleButton);

                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'action-btn';
                removeButton.textContent = 'Remove';
                removeButton.style.marginLeft = '0.4rem';
                removeButton.addEventListener('click', () => deletePendingWebhook(item, removeButton));
                actionCell.appendChild(removeButton);
            } else {
                actionCell.textContent = '—';
            }

            row.appendChild(actionCell);
            tbody.appendChild(row);
        });
    };

    const refresh = async () => {
        if (inFlight) return inFlight;
        inFlight = (async () => {
            try {
                setBadge('Loading...');
                if (errorsBox) errorsBox.style.display = 'none';
                const payload = await fetchJson('/api/open-orders');
                openOrdersCache = payload.items || [];
                const errors = payload.errors || [];
                render(openOrdersCache, errors);
                const updated = formatTimestamp(payload.updated_at);
                setBadge(
                    errors.length ? `Updated ${updated} • ${errors.length} source issue(s)` : `Updated ${updated}`,
                    errors.length ? 'error' : 'ok',
                );
            } catch (err) {
                console.error(err);
                render(openOrdersCache, [{ message: err.message }]);
                setBadge('Failed to load open orders.', 'error');
            } finally {
                inFlight = null;
            }
        })();
        return inFlight;
    };

    refreshBtn?.addEventListener('click', () => refresh());

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    const homeBtn = document.getElementById('nav-home');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());
    homeBtn?.addEventListener('click', () => (window.location.href = '/'));

    setInterval(() => refresh(), 5000);

    refresh();
})();
