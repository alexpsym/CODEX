(() => {
  const openBtn = document.getElementById('open-journal-btn');
  const importBtn = document.getElementById('import-journal-btn');
  const fileInput = document.getElementById('journal-file-input');
  const cryptoMonthlyBtn = document.getElementById('crypto-monthly-pnl-btn');
  const status = document.getElementById('journal-actions-status');

  const setStatus = (msg, err = false) => {
    if (!status) return;
    status.textContent = msg || '';
    status.style.color = err ? '#fca5a5' : '#94a3b8';
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
    try {
      const form = new FormData(); form.append('file', file);
      const res = await fetch('/api/trading-journal/import-file', { method: 'POST', body: form });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload.ok !== true) throw new Error(payload.detail || payload.message || 'Import failed.');
      setStatus(payload.message || 'Import complete.');
    } catch (err) { setStatus(err?.message || String(err), true); }
    finally { importBtn.disabled = false; fileInput.value = ''; }
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
