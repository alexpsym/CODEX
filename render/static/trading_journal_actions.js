(() => {
  const openBtn = document.getElementById('open-journal-btn');
  const importBtn = document.getElementById('import-journal-btn');
  const resyncBtn = document.getElementById('journal-resync-btn');
  const fileInput = document.getElementById('journal-file-input');
  const dropZone = document.getElementById('journal-import-drop-zone');
  const cryptoMonthlyBtn = document.getElementById('crypto-monthly-pnl-btn');
  const bybitDemoBalanceAdjustmentBtn = document.getElementById('bybit-demo-balance-adjustment-btn');
  const accountModeSelect = document.getElementById('journal-account-mode');
  const status = document.getElementById('journal-actions-status');
  const BYBIT_AMBIGUITY_MSG = 'Select Demo or Live in Bybit CSV account, then import this file again.';
  const IMPORT_WATCHDOG_MS = 15000;
  const formatElapsed = (ms) => {
    const total = Math.max(0, Math.floor(ms / 1000));
    const minutes = String(Math.floor(total / 60)).padStart(2, '0');
    const seconds = String(total % 60).padStart(2, '0');
    return `${minutes}:${seconds}`;
  };
  const pendingRetry = { kind: '', run: null };
  let retryInFlight = false;
  const makeFallbackButton = () => ({ id: '', textContent: '', style: { display: 'none' }, disabled: false, addEventListener: () => {} });
  const resumeBtn = typeof document?.createElement === 'function' ? document.createElement('button') : makeFallbackButton();
  const cancelBtn = typeof document?.createElement === 'function' ? document.createElement('button') : makeFallbackButton();
  resumeBtn.id = 'journal-retry-resume-btn'; resumeBtn.textContent = 'Resume after closing Excel'; resumeBtn.style.display = 'none';
  cancelBtn.id = 'journal-retry-cancel-btn'; cancelBtn.textContent = 'Cancel'; cancelBtn.style.display = 'none';
  const hasRetryControls = Boolean(status && typeof status.after === 'function');
  if (status && typeof status.after !== 'function') status.after = () => {};
  if (status) status.after(resumeBtn, cancelBtn);

  const setStatus = (msg, err = false) => {
    if (!status) return;
    status.textContent = msg || '';
    status.style.color = err ? '#fca5a5' : '#94a3b8';
  };
  const isExplicitAccountMode = (value) => value === 'demo' || value === 'live';
  const isExcelLockPayload = (payload) => {
    if (payload?.code === 'EXCEL_WORKBOOK_OPEN') return true;
    const errs = Array.isArray(payload?.errors) ? payload.errors.map((e) => String(e)) : [];
    return errs.includes('workbook_locked') || errs.includes('excel_open');
  };
  const clearPendingRetry = () => { pendingRetry.kind = ''; pendingRetry.run = null; retryInFlight = false; resumeBtn.disabled = false; resumeBtn.style.display = 'none'; cancelBtn.style.display = 'none'; if (openBtn) openBtn.disabled = false; if (importBtn) importBtn.disabled = false; if (resyncBtn) resyncBtn.disabled = false; if (cryptoMonthlyBtn) cryptoMonthlyBtn.disabled = false; if (bybitDemoBalanceAdjustmentBtn) bybitDemoBalanceAdjustmentBtn.disabled = false; };
  const setPendingRetry = (kind, fn) => { pendingRetry.kind = kind; pendingRetry.run = fn; resumeBtn.style.display = ''; cancelBtn.style.display = ''; if (openBtn) openBtn.disabled = true; if (importBtn) importBtn.disabled = true; if (resyncBtn) resyncBtn.disabled = true; };
  resumeBtn.addEventListener('click', async () => {
    if (!pendingRetry.run || retryInFlight) return;
    retryInFlight = true;
    resumeBtn.disabled = true;
    try { await pendingRetry.run(); } finally { retryInFlight = false; resumeBtn.disabled = false; }
  });
  cancelBtn.addEventListener('click', () => { clearPendingRetry(); setStatus('Retry canceled.'); });

  const formatImportError = (payload, fallback) => {
    const base = String(payload?.detail || payload?.message || fallback || 'Import failed.').trim();
    const parts = [base];
    if (Array.isArray(payload?.errors) && payload.errors.length) {
      parts.push(`Errors: ${payload.errors.map((v) => String(v)).join(', ')}`);
    }
    if (Array.isArray(payload?.missing_row_ids) && payload.missing_row_ids.length) {
      parts.push(`Missing Row IDs: ${payload.missing_row_ids.map((v) => String(v)).join(', ')}`);
    }
    if (payload?.import_timings && typeof payload.import_timings === 'object') {
      const timingText = Object.entries(payload.import_timings).map(([k, v]) => `${k}=${v}s`).join(', ');
      if (timingText) parts.push(`Timings: ${timingText}`);
    }
    return parts.join('\n');
  };
  const isBybitCsvFileName = (name) => String(name || '').trim().toLowerCase().endsWith('.csv');
  const isLikelyBybitHistoryCsv = async (file) => {
    if (!file || !isBybitCsvFileName(file.name)) return false;
    const head = await file.slice(0, 16384).text();
    const normalized = String(head || '').toLowerCase();
    const markers = [
      'contracts', 'order no.', 'direction', 'order type', 'filled qty', 'filled price', 'order price',
      'filled type', 'trading fee rate', 'fees paid', 'transaction time', 'final balance',
    ];
    const hasTxId = normalized.includes('trasaction id') || normalized.includes('transaction id');
    return markers.every((m) => normalized.includes(m)) && hasTxId;
  };

  openBtn?.addEventListener('click', async () => {
    openBtn.disabled = true;
    setStatus('Opening Trading Journal...');
    try {
      const res = await fetch('/api/trading-journal/open-master-journal', { method: 'POST', headers: { Accept: 'application/json' } });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload.ok !== true) throw new Error(payload.detail || payload.message || 'Failed to open workbook.');
      setStatus(`Opened: ${payload.master_journal_path || 'Trading Journal.xlsx'}`);
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally { openBtn.disabled = false; }
  });

  importBtn?.addEventListener('click', () => fileInput?.click());
  const runImport = async (file, fixedAccountMode = null) => {
    if (!file) return;
    if (openBtn) openBtn.disabled = true; if (importBtn) importBtn.disabled = true; if (resyncBtn) resyncBtn.disabled = true;
    if (importBtn) importBtn.disabled = true;
    if (dropZone) dropZone.classList.remove('drag-over');
    const importStartedAt = Date.now();
    const updateImportTimer = () => setStatus(`Importing... elapsed ${formatElapsed(Date.now() - importStartedAt)}`);
    updateImportTimer();
    let watchdog = null;
    let elapsedTimer = null;
    let statusPoll = null;
    const pollImportStatus = async () => {
      try {
        const res = await fetch('/api/trading-journal/import/status', { headers: { Accept: 'application/json' }, cache: 'no-store' });
        const payload = await res.json().catch(() => ({}));
        if (payload?.running && payload?.stage) {
          setStatus(`Importing... ${payload.stage} elapsed ${formatElapsed((payload.elapsed_seconds || 0) * 1000)}`);
        }
      } catch (_err) {}
    };
    try {
      statusPoll = typeof window.setInterval === 'function' ? window.setInterval(pollImportStatus, 2000) : null;
      elapsedTimer = typeof window.setInterval === 'function' ? window.setInterval(updateImportTimer, 1000) : null;
      watchdog = window.setTimeout(() => {
        setStatus(`Import is still running longer than expected. Waiting for backend result... elapsed ${formatElapsed(Date.now() - importStartedAt)}`, true);
      }, IMPORT_WATCHDOG_MS);
      const explicitMode = String((fixedAccountMode ?? accountModeSelect?.value) || '').trim().toLowerCase();
      const bybitLikely = await isLikelyBybitHistoryCsv(file);
      if (bybitLikely && !isExplicitAccountMode(explicitMode)) {
        if (elapsedTimer && typeof window.clearInterval === 'function') { window.clearInterval(elapsedTimer); elapsedTimer = null; }
        if (statusPoll && typeof window.clearInterval === 'function') { window.clearInterval(statusPoll); statusPoll = null; }
        if (watchdog) { window.clearTimeout(watchdog); watchdog = null; }
        setStatus(BYBIT_AMBIGUITY_MSG, true);
        accountModeSelect?.focus?.();
        fileInput.value = '';
        return;
      }
      const form = new FormData(); form.append('file', file);
      if (isExplicitAccountMode(explicitMode)) form.append('account_mode', explicitMode);
      const res = await fetch('/api/trading-journal/import-file', { method: 'POST', body: form });
      const payload = await res.json().catch(() => ({}));
      if (payload?.requires_account_mode || (Array.isArray(payload?.errors) && payload.errors.includes('ambiguous_bybit_account'))) {
        if (elapsedTimer && typeof window.clearInterval === 'function') { window.clearInterval(elapsedTimer); elapsedTimer = null; }
        if (statusPoll && typeof window.clearInterval === 'function') { window.clearInterval(statusPoll); statusPoll = null; }
        if (watchdog) { window.clearTimeout(watchdog); watchdog = null; }
        setStatus(BYBIT_AMBIGUITY_MSG, true);
        accountModeSelect?.focus?.();
        return;
      }
      if (isExcelLockPayload(payload)) {
        if (elapsedTimer && typeof window.clearInterval === 'function') { window.clearInterval(elapsedTimer); elapsedTimer = null; }
        if (statusPoll && typeof window.clearInterval === 'function') { window.clearInterval(statusPoll); statusPoll = null; }
        if (watchdog) { window.clearTimeout(watchdog); watchdog = null; }
        setStatus(`Import failed: ${formatImportError(payload, 'Trading Journal.xlsx appears to be open/locked. Close Excel, then press Resume.')}`, true);
        setPendingRetry('import', () => runImport(file, explicitMode));
        return;
      }
      if (!res.ok || payload.ok !== true) {
        if (elapsedTimer && typeof window.clearInterval === 'function') { window.clearInterval(elapsedTimer); elapsedTimer = null; }
        if (statusPoll && typeof window.clearInterval === 'function') { window.clearInterval(statusPoll); statusPoll = null; }
        if (watchdog) { window.clearTimeout(watchdog); watchdog = null; }
        throw new Error(formatImportError(payload, 'Import failed.'));
      }
      if (elapsedTimer && typeof window.clearInterval === 'function') { window.clearInterval(elapsedTimer); elapsedTimer = null; }
        if (statusPoll && typeof window.clearInterval === 'function') { window.clearInterval(statusPoll); statusPoll = null; }
      if (watchdog) { window.clearTimeout(watchdog); watchdog = null; }
      const warnings = payload.warnings || [];
      const inferred = payload.pnl_inferred_count ?? 0;
      const unresolved = payload.pnl_unresolved_count ?? 0;
      setStatus(`${payload.message || 'Import complete.'}\nRows parsed: ${payload.rows_parsed ?? 0}\nRows upserted: ${payload.rows_upserted ?? 0}\nP/L inferred: ${inferred}\nP/L unresolved: ${unresolved}\nWorkbook: ${payload.master_journal_path || ''}\nMissing Row IDs: ${(payload.missing_row_ids || []).join(', ') || 'none'}${warnings.length ? `\nWarnings:\n- ${warnings.join('\n- ')}` : ''}`);
      clearPendingRetry();
    } catch (err) {
      setStatus(err?.message || String(err), true);
      clearPendingRetry();
    }
    finally {
      if (elapsedTimer && typeof window.clearInterval === 'function') window.clearInterval(elapsedTimer);
      if (statusPoll && typeof window.clearInterval === 'function') window.clearInterval(statusPoll);
      if (watchdog) window.clearTimeout(watchdog);
      if (openBtn) openBtn.disabled = Boolean(pendingRetry.run);
      if (importBtn) importBtn.disabled = Boolean(pendingRetry.run);
      if (resyncBtn) resyncBtn.disabled = Boolean(pendingRetry.run);
      if (cryptoMonthlyBtn) cryptoMonthlyBtn.disabled = Boolean(pendingRetry.run);
      if (bybitDemoBalanceAdjustmentBtn) bybitDemoBalanceAdjustmentBtn.disabled = Boolean(pendingRetry.run);
      if (!pendingRetry.run && fileInput) fileInput.value = '';
    }
  };
  const formatTimings = (value) => {
    if (!value || typeof value !== 'object') return '';
    const text = Object.entries(value).map(([k, v]) => `${k}=${v}s`).join(', ');
    return text ? `\nTimings: ${text}` : '';
  };

  const runResync = async () => {
    if (resyncBtn) resyncBtn.disabled = true;
    if (importBtn) importBtn.disabled = true;
    setStatus('Resyncing Trading Journal... elapsed 00:00');
    const started = Date.now();
    const elapsedTimer = typeof window.setInterval === 'function' ? window.setInterval(() => {
      setStatus(`Resyncing Trading Journal... elapsed ${formatElapsed(Date.now() - started)}`);
    }, 1000) : null;
    try {
      const res = await fetch('/api/trading-journal/resync', { method: 'POST', headers: { Accept: 'application/json' } });
      const payload = await res.json().catch(() => ({}));
      if (isExcelLockPayload(payload)) {
        setStatus(payload.message || 'Trading Journal.xlsx appears to be open in Excel. Close it, then press Resume.', true);
        setPendingRetry('resync', runResync);
        return;
      }
      if (!res.ok || payload.ok !== true) throw new Error(payload.message || payload.error || payload.detail || 'Trading Journal resync failed.');
      const diagnostics = payload.master_journal_diagnostics || payload.diagnostics || {};
      const stageTimings = diagnostics.workbook_sync_substage_timings || payload.resync_timings || {};
      setStatus(`Resync complete.\nWorkbook: ${payload.master_journal_path || ''}${formatTimings(stageTimings)}`);
      clearPendingRetry();
    } catch (err) {
      setStatus(err?.message || String(err), true);
      clearPendingRetry();
    } finally {
      if (elapsedTimer && typeof window.clearInterval === 'function') window.clearInterval(elapsedTimer);
      if (resyncBtn) resyncBtn.disabled = Boolean(pendingRetry.run);
      if (importBtn) importBtn.disabled = Boolean(pendingRetry.run);
    }
  };
  resyncBtn?.addEventListener('click', runResync);

  const isAcceptedImportFile = (file) => /\.(xlsx|xlsm|xls|csv)$/i.test(String(file?.name || ''));
  dropZone?.addEventListener('click', () => fileInput?.click());
  dropZone?.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropZone.classList.add('drag-over');
  });
  dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone?.addEventListener('drop', async (event) => {
    event.preventDefault();
    dropZone.classList.remove('drag-over');
    const file = event.dataTransfer?.files && event.dataTransfer.files[0];
    if (!file) return;
    if (!isAcceptedImportFile(file)) { setStatus('Unsupported file type. Drop .xlsx, .xlsm, .xls, or .csv.', true); return; }
    const capturedMode = String(accountModeSelect?.value || '').trim().toLowerCase();
    await runImport(file, capturedMode);
  });
  fileInput?.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    const capturedMode = String(accountModeSelect?.value || '').trim().toLowerCase();
    await runImport(file, capturedMode);
  });
  accountModeSelect?.addEventListener('change', () => {
    const explicitMode = String(accountModeSelect?.value || '').trim().toLowerCase();
    const text = String(status?.textContent || '');
    if (text.includes('Select Demo or Live in Bybit CSV account')) {
      setStatus(isExplicitAccountMode(explicitMode) ? 'Account mode selected. Re-import the file to continue.' : '');
    }
  });

  const runCryptoMonthly = async () => {
    if (cryptoMonthlyBtn) cryptoMonthlyBtn.disabled = true;
    setStatus('Checking crypto monthly P&L...');
    try {
      const res = await fetch('/api/trading-journal/crypto-monthly-pnl', { method: 'POST', headers: { Accept: 'application/json' } });
      const payload = await res.json().catch(() => ({}));
      if (isExcelLockPayload(payload)) {
        setStatus(payload.message || 'Trading Journal.xlsx appears to be open in Excel. Close it, then press Resume.', true);
        setPendingRetry('crypto', runCryptoMonthly);
        return;
      }
      if (!res.ok || payload.ok !== true) throw new Error(payload.error || payload.detail || payload.message || 'Crypto monthly P&L failed.');
      setStatus(`Target months: ${(payload.target_months || []).join(', ') || '—'}\nInserted months: ${(payload.inserted_months || []).join(', ') || '—'}\nSkipped existing months: ${(payload.skipped_existing_months || []).join(', ') || '—'}\nRows inserted: ${payload.rows_inserted || 0}\nWorkbook: ${payload.master_journal_path || ''}\n${payload.message || ''}`);
      clearPendingRetry();
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally { if (cryptoMonthlyBtn) cryptoMonthlyBtn.disabled = Boolean(pendingRetry.run); }
  };
  cryptoMonthlyBtn?.addEventListener('click', runCryptoMonthly);

  const runBybitAdjust = async (amount, reason) => {
    if (bybitDemoBalanceAdjustmentBtn) bybitDemoBalanceAdjustmentBtn.disabled = true;
    setStatus('Applying Bybit Demo balance adjustment...');
    try {
      const res = await fetch('/api/trading-journal/bybit-demo/balance-adjustment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ amount, reason }),
      });
      const payload = await res.json().catch(() => ({}));
      if (isExcelLockPayload(payload)) {
        if (!hasRetryControls) {
          const retry = window.confirm('Trading Journal.xlsx appears to be open in Excel. Close it, then click OK to retry.');
          if (retry) return await runBybitAdjust(amount, reason);
          setStatus('Adjustment cancelled. Close Excel before trying again.', true);
          return;
        }
        setStatus(payload.message || 'Trading Journal.xlsx appears to be open in Excel. Close it, then press Resume.', true);
        setPendingRetry('bybit_demo_adjustment', () => runBybitAdjust(amount, reason));
        return;
      }
      if (!res.ok || payload.ok !== true) {
        const errors = Array.isArray(payload?.errors) && payload.errors.length ? `\nErrors: ${payload.errors.join(', ')}` : '';
        throw new Error(String(payload?.detail || payload?.message || 'Bybit Demo balance adjustment failed.') + errors);
      }
      setStatus(`Success. Previous balance: ${payload.previous_balance} ${payload.currency || 'USDT'}\nAdjustment: ${payload.adjustment_amount} ${payload.currency || 'USDT'}\nNew balance: ${payload.new_balance} ${payload.currency || 'USDT'}\nRow ID: ${payload.row_id || ''}\nWorkbook: ${payload.master_journal_path || ''}`);
      clearPendingRetry();
    } catch (err) {
      setStatus(err?.message || String(err), true);
    } finally {
      if (bybitDemoBalanceAdjustmentBtn) bybitDemoBalanceAdjustmentBtn.disabled = Boolean(pendingRetry.run);
    }
  };
  bybitDemoBalanceAdjustmentBtn?.addEventListener('click', async () => {
    const raw = window.prompt('Enter Bybit Demo journal balance adjustment in USDT. Use negative to reduce balance. This is journal-only and does not change Bybit.');
    if (raw === null) return;
    const text = String(raw || '').trim();
    if (!text) { setStatus('Amount is required.', true); return; }
    const amount = Number(text);
    if (!Number.isFinite(amount) || amount === 0) { setStatus('Enter a finite non-zero number.', true); return; }
    const reasonRaw = window.prompt('Optional reason/note for this journal-only adjustment:', '');
    const reason = reasonRaw === null ? '' : String(reasonRaw || '').trim();
    await runBybitAdjust(amount, reason);
  });

})();;
