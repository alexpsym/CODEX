(() => {
  const brokerSel = document.getElementById('history-broker');
  const accountWrap = document.getElementById('history-account-wrap');
  const accountSel = document.getElementById('history-account');
  const periodWrap = document.getElementById('history-periods');
  const exportBtn = document.getElementById('history-export');
  const statusEl = document.getElementById('history-status');
  const resultEl = document.getElementById('history-result');

  const PERIOD_DEFAULT = { kind: 'days', value: '30' };
  const PERIOD_COMPLETE = { kind: 'complete', value: '1' };
  let selectedPeriod = { ...PERIOD_DEFAULT };
  let forceCompleteMode = false;

  const API_MAP = {
    bybit: {
      start: '/api/bybit-history/export',
      status: (id) => `/api/bybit-history/export/${encodeURIComponent(id)}`,
    },
    oanda: {
      start: '/api/oanda-history/export',
      status: (id) => `/api/oanda-history/export/${encodeURIComponent(id)}`,
    },
    coinspot: {
      start: '/api/coinspot-history/export',
      status: (id) => `/api/coinspot-history/export/${encodeURIComponent(id)}`,
    },
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const sanitizeStatusMessage = (value) => {
    const msg = String(value || '').trim();
    if (!msg) return '';
    const containsHtml = /<[^>]+>/.test(msg) || /<!doctype html/i.test(msg);
    const scrubbed = containsHtml ? 'Upstream service returned an HTML error page. Check credentials and API base URL.' : msg;
    const compact = scrubbed.replace(/\s+/g, ' ').trim();
    return compact.length > 280 ? `${compact.slice(0, 277)}...` : compact;
  };

  const setStatus = (msg, isErr = false) => {
    if (!statusEl) return;
    statusEl.textContent = sanitizeStatusMessage(msg);
    statusEl.style.color = isErr ? '#fca5a5' : '#93c5fd';
  };

  const setResult = (msg) => {
    if (!resultEl) return;
    resultEl.textContent = msg || '';
  };

  const setActivePeriod = (btn) => {
    if (!btn || btn.style.display === 'none') return;
    periodWrap?.querySelectorAll('.period-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    selectedPeriod = {
      kind: btn.dataset.kind || 'days',
      value: btn.dataset.value || '30',
    };
  };

  const syncBrokerUi = () => {
    const broker = (brokerSel?.value || 'bybit').toLowerCase();
    const account = (accountSel?.value || 'demo').toLowerCase();
    const showAccount = broker === 'bybit' || broker === 'oanda';
    if (accountWrap) accountWrap.style.display = showAccount ? '' : 'none';

    forceCompleteMode = broker === 'bybit' && account === 'demo';
    const periodButtons = Array.from(periodWrap?.querySelectorAll('.period-btn') || []);
    const completeBtn = periodButtons.find((btn) => btn.dataset.kind === 'complete');
    periodButtons.forEach((btn) => {
      if (!forceCompleteMode) {
        btn.style.display = '';
        return;
      }
      btn.style.display = btn.dataset.kind === 'complete' ? '' : 'none';
      btn.classList.remove('active');
    });
    if (forceCompleteMode && completeBtn) {
      selectedPeriod = { ...PERIOD_COMPLETE };
      setActivePeriod(completeBtn);
    } else if (!periodButtons.some((btn) => btn.classList.contains('active') && btn.style.display !== 'none')) {
      const fallback = periodButtons.find((btn) => btn.style.display !== 'none') || periodButtons[0];
      if (fallback) setActivePeriod(fallback);
    }
  };

  const buildPayload = () => {
    const broker = (brokerSel?.value || 'bybit').toLowerCase();
    const payload = {};
    if (broker === 'bybit' || broker === 'oanda') {
      payload.account = (accountSel?.value || 'demo').toLowerCase();
    }

    if (broker === 'bybit' && payload.account === 'demo') {
      payload.complete = true;
      return { broker, payload };
    }

    if (selectedPeriod.kind === 'complete') {
      payload.complete = true;
    } else if (selectedPeriod.kind === 'period') {
      payload.period = selectedPeriod.value;
    } else {
      payload.days = Number(selectedPeriod.value || 30);
    }

    return { broker, payload };
  };

  const fetchJson = async (url, options = {}) => {
    const res = await fetch(url, options);
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      throw new Error(sanitizeStatusMessage(data?.detail || `${res.status} ${res.statusText}`));
    }
    return data;
  };

  const runExport = async () => {
    const { broker, payload } = buildPayload();
    const api = API_MAP[broker];
    if (!api) throw new Error(`Unsupported broker: ${broker}`);

    setResult('');
    setStatus('Starting export...');

    exportBtn && (exportBtn.disabled = true);
    try {
      const started = await fetchJson(api.start, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const jobId = started.job_id;
      if (!jobId) throw new Error('Export did not return a job id.');

      setStatus(`Job queued (${jobId.slice(0, 8)}...)`);
      for (let i = 0; i < 180; i += 1) {
        await sleep(1000);
        const st = await fetchJson(api.status(jobId));
        const state = String(st.status || '').toLowerCase();
        if (state === 'done') {
          setStatus('Export complete.');
          if (broker === 'oanda') {
            const backfillRes = await fetchJson(`/api/oanda-history/export/${encodeURIComponent(jobId)}/backfill-journal`, {
              method: 'POST',
            });
            if (!backfillRes || backfillRes.ok === false) {
              throw new Error(backfillRes?.detail || backfillRes?.sync?.message || 'OANDA export backfill failed.');
            }
            setStatus(`Export complete. Backfilled ${backfillRes.oanda_export_trades_seen || 0} OANDA ${String(payload.account || '').toUpperCase()} trades into Trading Journal.`);
            await fetchJson('/api/trading-journal/sync', { method: 'POST' });
          }
          const dl = st.download_url;
          if (dl) {
            setResult('Download started.');
            window.open(dl, '_blank', 'noopener');
          }
          return;
        }
        if (state === 'error') {
          throw new Error(st.error || 'Export failed.');
        }
        setStatus(`Processing... (${state || 'running'})`);
      }
      throw new Error('Export timed out.');
    } finally {
      exportBtn && (exportBtn.disabled = false);
    }
  };

  periodWrap?.querySelectorAll('.period-btn').forEach((btn) => {
    btn.addEventListener('click', () => setActivePeriod(btn));
  });

  brokerSel?.addEventListener('change', syncBrokerUi);
  accountSel?.addEventListener('change', syncBrokerUi);
  exportBtn?.addEventListener('click', async () => {
    try {
      await runExport();
    } catch (err) {
      console.error(err);
      setStatus(err?.message || String(err), true);
    }
  });

  const qs = new URLSearchParams(window.location.search);
  const broker = (qs.get('broker') || '').toLowerCase();
  if (broker && API_MAP[broker]) brokerSel.value = broker;

  const initial = periodWrap?.querySelector('.period-btn.active') || periodWrap?.querySelector('.period-btn');
  if (initial) setActivePeriod(initial);
  syncBrokerUi();
})();
