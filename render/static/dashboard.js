(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const status = document.getElementById('status');
  const alertsBtn = document.getElementById('alerts-backup-restore-btn');

  const forexList = document.getElementById('forex-scripts');
  const cryptoList = document.getElementById('crypto-scripts');
  const otherList = document.getElementById('other-scripts');

  // Open Orders (inline)
  const ooRefreshBtn = document.getElementById('oo-refresh-btn');
  const ooStatus = document.getElementById('oo-status');
  const ooTable = document.getElementById('open-orders-table');
  const ooTbody = ooTable?.querySelector('tbody');
  const ooEmpty = document.getElementById('open-orders-empty');
  const ooErrorsBox = document.getElementById('open-orders-errors');
  const ooErrorsList = ooErrorsBox?.querySelector('ul');

  const watchlistWidget = document.getElementById('watchlist-widget');
  const watchlistInput = document.getElementById('watchlist-input');
  const watchlistAddBtn = document.getElementById('watchlist-add-btn');
  const watchlistList = document.getElementById('watchlist-items');
  const watchlistEmpty = document.getElementById('watchlist-empty');
  const watchlistStatus = document.getElementById('watchlist-status');
  const watchlistCount = document.getElementById('watchlist-count');

  let scriptsInFlight = null;
  let ooInFlight = null;
  let watchlistItems = [];

  const setStatus = (msg, isErr = false) => {
    if (!status) return;
    status.textContent = msg;
    status.style.color = isErr ? '#fca5a5' : '#94a3b8';
  };

  const fetchJson = async (url, options = {}) => {
    const res = await fetch(url, options);
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${options.method || 'GET'} ${url} failed: ${res.status} ${body || res.statusText}`);
    }
    return res.json();
  };

  const normalizeSymbol = (value) => {
    const trimmed = String(value || '').trim().toUpperCase();
    if (!trimmed) return '';
    if (trimmed.length === 6 && /^[A-Z]+$/.test(trimmed)) {
      return `${trimmed.slice(0, 3)}_${trimmed.slice(3)}`;
    }
    return trimmed;
  };

  const normalizeWatchlist = (items) => {
    const unique = new Set();
    const normalized = [];
    items.forEach((item) => {
      const symbol = normalizeSymbol(item);
      if (!symbol || unique.has(symbol)) return;
      unique.add(symbol);
      normalized.push(symbol);
    });
    return normalized.slice(0, 50);
  };

  const setWatchlistStatus = (message) => {
    if (!watchlistStatus) return;
    watchlistStatus.textContent = message;
  };

  const renderWatchlist = () => {
    if (!watchlistList || !watchlistEmpty) return;

    watchlistList.innerHTML = '';
    watchlistEmpty.style.display = watchlistItems.length ? 'none' : 'block';
    if (watchlistCount) watchlistCount.textContent = String(watchlistItems.length);

    watchlistItems.forEach((symbol, index) => {
      const row = document.createElement('tr');

      const symbolCell = document.createElement('td');
      symbolCell.textContent = symbol;
      symbolCell.style.cursor = 'pointer';
      symbolCell.title = 'Click to copy';
      symbolCell.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(symbol);
          setWatchlistStatus(`Copied ${symbol}`);
        } catch (err) {
          console.error(err);
          setWatchlistStatus('Clipboard unavailable');
        }
      });

      const actionCell = document.createElement('td');
      actionCell.className = 'action-cell';

      const removeBtn = document.createElement('button');
      removeBtn.className = 'action-btn';
      removeBtn.type = 'button';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', () => {
        watchlistItems.splice(index, 1);
        saveWatchlist(watchlistItems);
        renderWatchlist();
      });

      actionCell.appendChild(removeBtn);
      row.appendChild(symbolCell);
      row.appendChild(actionCell);
      watchlistList.appendChild(row);
    });
  };

  const saveWatchlist = async (items) => {
    const normalized = normalizeWatchlist(items);
    watchlistItems = normalized;
    renderWatchlist();
    try {
      await fetchJson('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: normalized }),
      });
      setWatchlistStatus(`Saved ${normalized.length} item${normalized.length === 1 ? '' : 's'}`);
    } catch (err) {
      console.error(err);
      setWatchlistStatus('Failed to save watchlist');
    }
  };

  const loadWatchlist = async () => {
    if (!watchlistWidget) return;
    try {
      const data = await fetchJson('/api/watchlist');
      watchlistItems = normalizeWatchlist(Array.isArray(data?.items) ? data.items : []);
      renderWatchlist();
      if (watchlistItems.length) {
        setWatchlistStatus(`Loaded ${watchlistItems.length} item${watchlistItems.length === 1 ? '' : 's'}`);
      } else {
        setWatchlistStatus('');
      }
    } catch (err) {
      console.error(err);
      setWatchlistStatus('Failed to load watchlist');
    }
  };

  const downloadAlertsBackup = async () => {
    const res = await fetch('/api/alerts/backup', { headers: { 'Cache-Control': 'no-store' } });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'codex-alerts-backup.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const restoreAlertsBackup = async (file) => {
    if (!file) return;
    if (!confirm('Restore will REPLACE: alerts (Bybit + OANDA), watchlist, and pending webhooks. Continue?')) return;
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/alerts/restore', { method: 'POST', body: fd });
    const txt = await res.text();
    if (!res.ok) throw new Error(txt);
    await loadWatchlist();
    await refreshOpenOrders();
  };

  const openAlertsModal = () => {
    const overlay = document.createElement('div');
    overlay.style.position = 'fixed';
    overlay.style.inset = '0';
    overlay.style.background = 'rgba(0,0,0,0.6)';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.zIndex = '9999';

    const box = document.createElement('div');
    box.style.width = 'min(520px, calc(100% - 24px))';
    box.style.background = '#111827';
    box.style.border = '1px solid #1f2937';
    box.style.borderRadius = '12px';
    box.style.padding = '16px';
    box.style.color = '#e2e8f0';

    const title = document.createElement('div');
    title.textContent = 'Alerts + webhooks backup/restore';
    title.style.fontWeight = '900';
    title.style.marginBottom = '8px';

    const msg = document.createElement('div');
    msg.textContent = 'Backup includes alerts, watchlist, and pending webhooks. Restore replaces them.';
    msg.style.color = '#94a3b8';
    msg.style.marginBottom = '12px';

    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.gap = '8px';

    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'secondary';
    downloadBtn.textContent = 'Download backup';

    const restoreBtn = document.createElement('button');
    restoreBtn.className = 'secondary';
    restoreBtn.textContent = 'Restore from file';

    const closeBtn = document.createElement('button');
    closeBtn.className = 'secondary';
    closeBtn.textContent = 'Close';
    closeBtn.style.marginLeft = 'auto';

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'application/json,.json';
    fileInput.style.display = 'none';

    downloadBtn.addEventListener('click', async () => {
      downloadBtn.disabled = true;
      try {
        await downloadAlertsBackup();
      } finally {
        downloadBtn.disabled = false;
      }
    });

    restoreBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      restoreBtn.disabled = true;
      try {
        await restoreAlertsBackup(f);
        alert('Alerts restored.');
        overlay.remove();
      } catch (e) {
        alert(e.message || String(e));
      } finally {
        restoreBtn.disabled = false;
        fileInput.value = '';
      }
    });

    const close = () => overlay.remove();
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });

    row.appendChild(downloadBtn);
    row.appendChild(restoreBtn);
    row.appendChild(closeBtn);
    box.appendChild(title);
    box.appendChild(msg);
    box.appendChild(row);
    box.appendChild(fileInput);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  };

  alertsBtn?.addEventListener('click', () => openAlertsModal());

  watchlistAddBtn?.addEventListener('click', () => {
    if (!watchlistInput) return;
    const raw = watchlistInput.value || '';
    const tokens = raw.split(/[\s,]+/).filter(Boolean);
    if (!tokens.length) return;
    const merged = normalizeWatchlist([...watchlistItems, ...tokens]);
    if (merged.length === watchlistItems.length) {
      watchlistInput.value = '';
      return;
    }
    if (merged.length >= 50 && watchlistItems.length < merged.length) {
      setWatchlistStatus('Max 50 symbols');
    }
    saveWatchlist(merged);
    watchlistInput.value = '';
  });

  watchlistInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    watchlistAddBtn?.click();
  });

  // Normalize /scripts category values (prevents empty lists)
  const normCategory = (cat) => {
    const c = String(cat || '').trim().toLowerCase();
    if (c === 'forex' || c.includes('oanda') || c.includes('fx')) return 'Forex';
    if (c === 'crypto' || c.includes('bybit') || c.includes('coinspot')) return 'Crypto';
    return 'Other';
  };

  const makeScriptButton = (script, compact = false) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `script-btn${compact ? ' compact' : ''}`;

    const name = document.createElement('div');
    name.className = 'script-name';
    name.textContent = script.name;

    const pill = document.createElement('span');
    pill.className = `status-pill ${script.running ? 'running' : 'stopped'}`;
    pill.textContent = script.running ? 'Running' : 'Stopped';

    btn.appendChild(name);
    btn.appendChild(pill);

    btn.addEventListener('click', () => {
      const target = script.open_url || `/scripts/view/${encodeURIComponent(script.name)}`;
      window.location.href = target;
    });

    return btn;
  };

  const renderList = (container, scripts, compact = false) => {
    if (!container) return;
    container.innerHTML = '';
    scripts
      .slice()
      .sort((a, b) => String(a.name).localeCompare(String(b.name)))
      .forEach((s) => container.appendChild(makeScriptButton(s, compact)));
  };

  const refreshScripts = async () => {
    if (scriptsInFlight) return scriptsInFlight;

    scriptsInFlight = (async () => {
      try {
        setStatus('Loading scripts...');
        const scripts = await fetchJson('/scripts');
        const mapped = scripts.map((s) => ({ ...s, _cat: normCategory(s.category) }));

        renderList(forexList, mapped.filter((s) => s._cat === 'Forex'), false);
        renderList(cryptoList, mapped.filter((s) => s._cat === 'Crypto'), false);
        renderList(otherList, mapped.filter((s) => s._cat === 'Other'), true);

        setStatus(`Updated ${new Date().toLocaleTimeString()}`);
      } catch (e) {
        console.error(e);
        setStatus('Failed to load scripts.', true);
      } finally {
        scriptsInFlight = null;
      }
    })();

    return scriptsInFlight;
  };

  // -------- Open Orders / Positions (unchanged endpoints) --------
  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const fmtTime = (v) => {
    if (!v) return '—';
    const n = Number(v);
    if (!Number.isNaN(n)) {
      const ms = n < 1_000_000_000_000 ? n * 1000 : n;
      const d = new Date(ms);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    const d = new Date(v);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    return String(v);
  };

  const setOoPill = (text, tone) => {
    if (!ooStatus) return;
    ooStatus.textContent = text;
    ooStatus.classList.remove('running', 'stopped');
    if (tone === 'ok') ooStatus.classList.add('running');
    if (tone === 'bad') ooStatus.classList.add('stopped');
  };

  const renderOpenOrders = (items, errors) => {
    if (!ooTbody) return;

    ooTbody.innerHTML = '';

    if (ooErrorsBox) ooErrorsBox.style.display = errors?.length ? 'block' : 'none';
    if (ooErrorsList) {
      ooErrorsList.innerHTML = '';
      (errors || []).forEach((err) => {
        const li = document.createElement('li');
        li.textContent = err.message || String(err);
        ooErrorsList.appendChild(li);
      });
    }

    if (!items?.length) {
      if (ooEmpty) ooEmpty.style.display = 'block';
      return;
    }
    if (ooEmpty) ooEmpty.style.display = 'none';

    items.forEach((item) => {
      const tr = document.createElement('tr');

      const cols = [
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
        fmtTime(item.opened_at),
        item.id,
        item.status,
      ];

      cols.forEach((c) => {
        const td = document.createElement('td');
        td.textContent = fmt(c);
        tr.appendChild(td);
      });

      const actionTd = document.createElement('td');
      actionTd.className = 'action-cell';

      const t = String(item.type || '').toLowerCase();
      const isOrder = t === 'order';
      const isPosition = t === 'position' || t === 'trade';
      const isWebhook = t === 'webhook';

      if (isOrder || isPosition) {
        const label = isOrder ? 'Cancel' : 'Close';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'action-btn';
        btn.textContent = label;

        btn.addEventListener('click', async () => {
          btn.disabled = true;
          const old = btn.textContent;
          btn.textContent = '...';
          try {
            await fetchJson('/api/open-orders/close', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(item),
            });
            await refreshOpenOrders();
          } catch (e) {
            console.error(e);
            setOoPill(e?.message || 'Action failed', 'bad');
          } finally {
            btn.disabled = false;
            btn.textContent = old;
          }
        });

        actionTd.appendChild(btn);
      } else if (isWebhook) {
        const enabled = item.enabled !== false;

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'action-btn';
        toggleBtn.textContent = enabled ? 'Disable' : 'Enable';
        toggleBtn.addEventListener('click', async () => {
          toggleBtn.disabled = true;
          const old = toggleBtn.textContent;
          toggleBtn.textContent = '...';
          try {
            await fetchJson(`/api/pending-webhooks/${encodeURIComponent(item.id)}/enabled`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ enabled: !enabled }),
            });
            await refreshOpenOrders();
          } catch (e) {
            console.error(e);
            setOoPill(e?.message || 'Action failed', 'bad');
          } finally {
            toggleBtn.disabled = false;
            toggleBtn.textContent = old;
          }
        });
        actionTd.appendChild(toggleBtn);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'action-btn';
        removeBtn.textContent = 'Remove';
        removeBtn.style.marginLeft = '0.4rem';
        removeBtn.addEventListener('click', async () => {
          removeBtn.disabled = true;
          const old = removeBtn.textContent;
          removeBtn.textContent = '...';
          try {
            await fetchJson(`/api/pending-webhooks/${encodeURIComponent(item.id)}`, {
              method: 'DELETE',
            });
            await refreshOpenOrders();
          } catch (e) {
            console.error(e);
            setOoPill(e?.message || 'Action failed', 'bad');
          } finally {
            removeBtn.disabled = false;
            removeBtn.textContent = old;
          }
        });
        actionTd.appendChild(removeBtn);
      } else {
        actionTd.textContent = '—';
      }

      tr.appendChild(actionTd);
      ooTbody.appendChild(tr);
    });
  };

  const refreshOpenOrders = async () => {
    if (ooInFlight) return ooInFlight;

    ooInFlight = (async () => {
      try {
        setOoPill('Loading...', 'ok');
        const payload = await fetchJson('/api/open-orders');
        renderOpenOrders(payload.items || [], payload.errors || []);
        setOoPill((payload.errors || []).length ? 'Updated (issues)' : 'Updated', (payload.errors || []).length ? 'bad' : 'ok');
      } catch (e) {
        console.error(e);
        renderOpenOrders([], [{ message: e.message }]);
        setOoPill('Failed', 'bad');
      } finally {
        ooInFlight = null;
      }
    })();

    return ooInFlight;
  };

  refreshBtn?.addEventListener('click', () => { refreshScripts(); refreshOpenOrders(); });
  ooRefreshBtn?.addEventListener('click', () => refreshOpenOrders());

  document.getElementById('nav-back')?.addEventListener('click', () => window.history.back());
  document.getElementById('nav-forward')?.addEventListener('click', () => window.history.forward());
  document.getElementById('nav-home')?.addEventListener('click', () => { window.location.href = '/'; });

  setInterval(() => { refreshScripts(); refreshOpenOrders(); }, 5000);

  refreshScripts();
  refreshOpenOrders();
  loadWatchlist();
})();
