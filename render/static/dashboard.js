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

  let scriptsInFlight = null;
  let ooInFlight = null;

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
    if (!confirm('Restore will REPLACE alerts for BOTH Bybit and OANDA. Continue?')) return;
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/alerts/restore', { method: 'POST', body: fd });
    const txt = await res.text();
    if (!res.ok) throw new Error(txt);
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
    title.textContent = 'Alerts backup/restore (Bybit + OANDA)';
    title.style.fontWeight = '900';
    title.style.marginBottom = '8px';

    const msg = document.createElement('div');
    msg.textContent = 'Backup downloads one file. Restore uploads that same file.';
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
      window.location.href = `/scripts/view/${encodeURIComponent(script.name)}`;
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
            setOoPill('Action failed', 'bad');
          } finally {
            btn.disabled = false;
            btn.textContent = old;
          }
        });

        actionTd.appendChild(btn);
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
})();
