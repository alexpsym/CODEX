(() => {
  const openBtn = document.getElementById('open-journal-btn');
  const importBtn = document.getElementById('import-journal-btn');
  const fileInput = document.getElementById('journal-file-input');
  const cryptoMonthlyBtn = document.getElementById('crypto-monthly-pnl-btn');
  const bybitDemoBalanceAdjustmentBtn = document.getElementById('bybit-demo-balance-adjustment-btn');
  const accountModeSelect = document.getElementById('journal-account-mode');
  const status = document.getElementById('journal-actions-status');
  const BYBIT_AMBIGUITY_MSG = 'Select Demo or Live in Bybit CSV account, then import this file again.';
  const IMPORT_WATCHDOG_MS = 15000;
  const pendingRetry = { kind: '', run: null };
  let retryInFlight = false;
  const resumeBtn = document.createElement('button');
  const cancelBtn = document.createElement('button');
  resumeBtn.id = 'journal-retry-resume-btn'; resumeBtn.textContent = 'Resume after closing Excel'; resumeBtn.style.display = 'none';
  cancelBtn.id = 'journal-retry-cancel-btn'; cancelBtn.textContent = 'Cancel'; cancelBtn.style.display = 'none';
  if (status) status.after(resumeBtn, cancelBtn);

  const setStatus = (msg, err = false) => {
    if (!status) return;
    status.textContent = msg || '';
    status.style.color = err ? '#fca5a5' : '#94a3b8';
  };
  const isExplicitAccountMode = (value) => value === 'demo' || value === 'live';
  const isExcelLockPayload = (payload) => payload?.code === 'EXCEL_WORKBOOK_OPEN';
  const clearPendingRetry = () => { pendingRetry.kind = ''; pendingRetry.run = null; retryInFlight = false; resumeBtn.disabled = false; resumeBtn.style.display = 'none'; cancelBtn.style.display = 'none'; if (importBtn) importBtn.disabled = false; if (cryptoMonthlyBtn) cryptoMonthlyBtn.disabled = false; if (bybitDemoBalanceAdjustmentBtn) bybitDemoBalanceAdjustmentBtn.disabled = false; };
  const setPendingRetry = (kind, fn) => { pendingRetry.kind = kind; pendingRetry.run = fn; resumeBtn.style.display = ''; cancelBtn.style.display = ''; };
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
    if (importBtn) importBtn.disabled = true;
    setStatus('Importing...');
    let watchdog = null;
    try {
      watchdog = window.setTimeout(() => {
        setStatus('Import is still running longer than expected. Waiting for backend result...', true);
      }, IMPORT_WATCHDOG_MS);
      const explicitMode = String((fixedAccountMode ?? accountModeSelect?.value) || '').trim().toLowerCase();
      const bybitLikely = await isLikelyBybitHistoryCsv(file);
      if (bybitLikely && !isExplicitAccountMode(explicitMode)) {
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
        setStatus(BYBIT_AMBIGUITY_MSG, true);
        accountModeSelect?.focus?.();
        return;
      }
      if (isExcelLockPayload(payload)) {
        setStatus(payload.message || 'Trading Journal.xlsx appears to be open in Excel. Close it, then press Resume.', true);
        setPendingRetry('import', () => runImport(file, explicitMode));
        return;
      }
      if (!res.ok || payload.ok !== true) throw new Error(formatImportError(payload, 'Import failed.'));
      const warnings = payload.warnings || [];
      const inferred = payload.pnl_inferred_count ?? 0;
      const unresolved = payload.pnl_unresolved_count ?? 0;
      setStatus(`${payload.message || 'Import complete.'}\nRows parsed: ${payload.rows_parsed ?? 0}\nRows upserted: ${payload.rows_upserted ?? 0}\nP/L inferred: ${inferred}\nP/L unresolved: ${unresolved}\nWorkbook: ${payload.master_journal_path || ''}\nMissing Row IDs: ${(payload.missing_row_ids || []).join(', ') || 'none'}${warnings.length ? `\nWarnings:\n- ${warnings.join('\n- ')}` : ''}`);
      clearPendingRetry();
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally {
      if (watchdog) window.clearTimeout(watchdog);
      if (importBtn) importBtn.disabled = Boolean(pendingRetry.run); if (!pendingRetry.run && fileInput) fileInput.value = '';
    }
  };
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

  bybitDemoBalanceAdjustmentBtn?.addEventListener('click', async () => {
    const raw = window.prompt('Enter Bybit Demo journal balance adjustment in USDT. Use negative to reduce balance. This is journal-only and does not change Bybit.');
    if (raw === null) return;
    const text = String(raw || '').trim();
    if (!text) { setStatus('Amount is required.', true); return; }
    const amount = Number(text);
    if (!Number.isFinite(amount) || amount === 0) { setStatus('Enter a finite non-zero number.', true); return; }
    const reasonRaw = window.prompt('Optional reason/note for this journal-only adjustment:', '');
    const reason = reasonRaw === null ? '' : String(reasonRaw || '').trim();
    bybitDemoBalanceAdjustmentBtn.disabled = true;
    setStatus('Applying Bybit Demo balance adjustment...');
    const postAdjustmentOnce = async () => {
      const res = await fetch('/api/trading-journal/bybit-demo/balance-adjustment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ amount, reason }),
      });
      const payload = await res.json().catch(() => ({}));
      return { res, payload };
    };
    try {
      let { res, payload } = await postAdjustmentOnce();
      const errCodes = Array.isArray(payload?.errors) ? payload.errors.map((e) => String(e)) : [];
      const locked = res.status === 423 || errCodes.includes('workbook_locked') || errCodes.includes('excel_open');
      if (locked) {
        const retry = window.confirm('Trading Journal.xlsx appears to be open in Excel. Close it, save if needed, then click OK to retry. Cancel leaves the journal unchanged.');
        if (!retry) {
          setStatus('Adjustment cancelled. Close Excel before trying again.', true);
          return;
        }
        ({ res, payload } = await postAdjustmentOnce());
      }
      if (!res.ok || payload.ok !== true) {
        const errors = Array.isArray(payload?.errors) && payload.errors.length ? `
Errors: ${payload.errors.join(', ')}` : '';
        throw new Error(String(payload?.detail || payload?.message || 'Bybit Demo balance adjustment failed.') + errors);
      }
      setStatus(`Success. Previous balance: ${payload.previous_balance} ${payload.currency || 'USDT'}
Adjustment: ${payload.adjustment_amount} ${payload.currency || 'USDT'}
New balance: ${payload.new_balance} ${payload.currency || 'USDT'}
Row ID: ${payload.row_id || ''}
Workbook: ${payload.master_journal_path || ''}`);
    } catch (err) {
      setStatus(err?.message || String(err), true);
    } finally {
      bybitDemoBalanceAdjustmentBtn.disabled = false;
    }
  });
})();
