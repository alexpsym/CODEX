(() => {
  const openBtn = document.getElementById('open-journal-btn');
  const importBtn = document.getElementById('import-journal-btn');
  const fileInput = document.getElementById('journal-file-input');
  const cryptoMonthlyBtn = document.getElementById('crypto-monthly-pnl-btn');
  const accountModeSelect = document.getElementById('journal-account-mode');
  const status = document.getElementById('journal-actions-status');
  const BYBIT_AMBIGUITY_MSG = 'Select Demo or Live in Bybit CSV account, then import this file again.';
  const IMPORT_WATCHDOG_MS = 15000;

  const setStatus = (msg, err = false) => {
    if (!status) return;
    status.textContent = msg || '';
    status.style.color = err ? '#fca5a5' : '#94a3b8';
  };
  const isExplicitAccountMode = (value) => value === 'demo' || value === 'live';

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
      const payload = await res.json();
      if (!res.ok || payload.ok !== true) throw new Error(payload.detail || payload.message || 'Failed to open workbook.');
      setStatus(`Opened: ${payload.master_journal_path || 'Trading Journal.xlsx'}`);
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally { openBtn.disabled = false; }
  });

  importBtn?.addEventListener('click', () => fileInput?.click());
  fileInput?.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    importBtn.disabled = true;
    setStatus('Importing...');
    let watchdog = null;
    try {
      watchdog = window.setTimeout(() => {
        setStatus('Import is still running longer than expected. Waiting for backend result...', true);
      }, IMPORT_WATCHDOG_MS);
      const explicitMode = String(accountModeSelect?.value || '').trim().toLowerCase();
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
      if (!res.ok || payload.ok !== true) throw new Error(formatImportError(payload, 'Import failed.'));
      const warnings = payload.warnings || [];
      const inferred = payload.pnl_inferred_count ?? 0;
      const unresolved = payload.pnl_unresolved_count ?? 0;
      setStatus(`${payload.message || 'Import complete.'}\nRows parsed: ${payload.rows_parsed ?? 0}\nRows upserted: ${payload.rows_upserted ?? 0}\nP/L inferred: ${inferred}\nP/L unresolved: ${unresolved}\nWorkbook: ${payload.master_journal_path || ''}\nMissing Row IDs: ${(payload.missing_row_ids || []).join(', ') || 'none'}${warnings.length ? `\nWarnings:\n- ${warnings.join('\n- ')}` : ''}`);
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally {
      if (watchdog) window.clearTimeout(watchdog);
      importBtn.disabled = false; fileInput.value = '';
    }
  });
  accountModeSelect?.addEventListener('change', () => {
    const explicitMode = String(accountModeSelect?.value || '').trim().toLowerCase();
    const text = String(status?.textContent || '');
    if (text.includes('Select Demo or Live in Bybit CSV account')) {
      setStatus(isExplicitAccountMode(explicitMode) ? 'Account mode selected. Re-import the file to continue.' : '');
    }
  });

  cryptoMonthlyBtn?.addEventListener('click', async () => {
    cryptoMonthlyBtn.disabled = true;
    setStatus('Checking crypto monthly P&L...');
    try {
      const res = await fetch('/api/trading-journal/crypto-monthly-pnl', { method: 'POST', headers: { Accept: 'application/json' } });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload.ok !== true) throw new Error(payload.error || payload.detail || payload.message || 'Crypto monthly P&L failed.');
      setStatus(`Target months: ${(payload.target_months || []).join(', ') || '—'}\nInserted months: ${(payload.inserted_months || []).join(', ') || '—'}\nSkipped existing months: ${(payload.skipped_existing_months || []).join(', ') || '—'}\nRows inserted: ${payload.rows_inserted || 0}\nWorkbook: ${payload.master_journal_path || ''}\n${payload.message || ''}`);
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally { cryptoMonthlyBtn.disabled = false; }
  });
})();
