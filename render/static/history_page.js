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



  const getFilenameFromContentDisposition = (headerValue, fallback) => {
    const safeFallback = String(fallback || 'history_export').replace(/[\/]+/g, '_').trim() || 'history_export';
    const raw = String(headerValue || '');
    if (!raw) return safeFallback;

    let filename = '';
    const utf8Match = raw.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match && utf8Match[1]) {
      try {
        filename = decodeURIComponent(utf8Match[1].trim());
      } catch {
        filename = utf8Match[1].trim();
      }
    }

    if (!filename) {
      const plainMatch = raw.match(/filename="?([^";]+)"?/i);
      if (plainMatch && plainMatch[1]) filename = plainMatch[1].trim();
    }

    filename = String(filename || '').replace(/[\/]+/g, '_').trim();
    return filename || safeFallback;
  };

  const buildFallbackExportFilename = (broker, payload, jobId) => {
    const safeJobId = String(jobId || 'export').replace(/[^a-zA-Z0-9_-]+/g, '_') || 'export';
    const safeAccount = String(payload?.account || 'account').replace(/[^a-zA-Z0-9_-]+/g, '_') || 'account';
    if (broker === 'coinspot') return `coinspot_history_${safeJobId}.zip`;
    if (broker === 'oanda') return `oanda_history_${safeAccount}_${safeJobId}.csv`;
    return `bybit_history_${safeAccount}_${safeJobId}.csv`;
  };

  const downloadExportFile = async (url, fallbackFilename) => {
    const res = await fetch(url, { method: 'GET', cache: 'no-store', credentials: 'same-origin' });
    if (!res.ok) {
      const body = await res.text();
      const detail = sanitizeStatusMessage(body || `${res.status} ${res.statusText}`) || `${res.status} ${res.statusText}`;
      throw new Error(`Download failed (HTTP ${res.status}): ${detail}`);
    }

    const blob = await res.blob();
    if (!blob || blob.size === 0) throw new Error('Downloaded export was empty.');

    const filename = getFilenameFromContentDisposition(res.headers.get('content-disposition'), fallbackFilename);
    const objectUrl = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.style.display = 'none';
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }
    return filename;
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
              const detail = backfillRes?.error || backfillRes?.sync?.message || backfillRes?.detail || 'OANDA export backfill failed.';
              const target = backfillRes?.oanda_export_target_workbook ? ` (${backfillRes.oanda_export_target_workbook})` : '';
              throw new Error(`Export completed, but Trading Journal backfill failed: ${detail}${target}`);
            }
            setStatus(`Export complete. Backfilled ${backfillRes.oanda_export_trades_seen || 0} OANDA ${String(payload.account || '').toUpperCase()} trades into Trading Journal.`);
            const syncRes = await fetchJson('/api/trading-journal/sync', { method: 'POST' });
            if (!syncRes || syncRes.ok === false) {
              throw new Error(syncRes?.message || 'Trading Journal sync reported failure after OANDA backfill.');
            }
          }
          const dl = st.download_url;
          if (!dl) {
            throw new Error('Export completed but no download URL was returned.');
          }

          setStatus('Export complete. Downloading file...');
          try {
            const filename = await downloadExportFile(dl, buildFallbackExportFilename(broker, payload, jobId));
            setResult(`Downloaded ${filename}.`);
          } catch (downloadErr) {
            setStatus(downloadErr?.message || String(downloadErr), true);
            if (resultEl) {
              resultEl.textContent = 'Automatic download failed. Manual download: ';
              const manual = document.createElement('a');
              manual.href = dl;
              manual.download = '';
              manual.textContent = 'Download export file';
              resultEl.appendChild(manual);
            }
            throw downloadErr;
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
