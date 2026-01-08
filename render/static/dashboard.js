(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const status = document.getElementById('status');

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
