(function () {
  const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1mo'];
  const state = {
    account: 'live',
    asset: 'crypto',
    side: 'buy',
    order_type: 'market',
    risk_mode: 'percent',
    timeframe: '15m',
    target_mode: 'rr',
    webhook_mode: 'no',
    quote: null,
    resolvedSymbol: '',
    pendingWebhookId: '',
  };

  const $ = (id) => document.getElementById(id);
  const errorEl = $('calc-error');
  const okEl = $('calc-success');
  const resultEl = $('calc-results');
  const canonicalEl = $('calc-canonical-symbol');
  const journalEl = $('calc-journal-summary');
  const riskToggleWrap = $('risk-toggle-wrap');
  const tpTicksWrap = $('tp-ticks-wrap');
  const rrWrap = $('rr-wrap');
  const webhookPanel = $('calc-webhook-panel');
  const webhookJsonEl = $('calc-webhook-json');
  const webhookCopyBtn = $('calc-webhook-copy');

  let symbolTimer = null;
  let resolveController = null;
  let journalController = null;

  function clearMessages() {
    errorEl.textContent = '';
    okEl.textContent = '';
  }

  function setJournalState(kind, text) {
    journalEl.dataset.state = kind;
    journalEl.innerHTML = `<div class="muted">${text}</div>`;
  }

  const fmtPct = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    const dp = n >= 10 ? 1 : 2;
    return `${n.toFixed(dp)}%`;
  };

  const fmtDuration = (secs) => {
    const n = Number(secs);
    if (!Number.isFinite(n) || n < 0) return '-';
    const s = Math.floor(n % 60);
    const m = Math.floor((n / 60) % 60);
    const h = Math.floor((n / 3600) % 24);
    const d = Math.floor(n / 86400);
    if (d > 0) return `${d}d ${h}h ${m}m ${s}s`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  const fmtBrisbaneTime = (value) => {
    if (!value) return '-';
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return String(value);
    return dt.toLocaleString('en-AU', {
      timeZone: 'Australia/Brisbane',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  };

  function renderJournalStats(payload) {
    const s = payload.stats || {};
    const rows = [
      ['Canonical symbol', payload.canonical_symbol],
      ['Total trades', s.total_trades], ['Wins', s.wins], ['Losses', s.losses], ['Break-even', s.break_even],
      ['Long trades', s.long_trades], ['Short trades', s.short_trades],
      ['Long wins / losses', `${s.long_wins ?? '-'} / ${s.long_losses ?? '-'}`],
      ['Short wins / losses', `${s.short_wins ?? '-'} / ${s.short_losses ?? '-'}`],
      ['Win rate', s.win_rate],
      ['Avg stop distance', fmtPct(s.avg_stop_distance)],
      ['Avg target distance', fmtPct(s.avg_target_distance)],
      ['Avg trade duration', fmtDuration(s.avg_trade_duration)],
      ['Last trade', fmtBrisbaneTime(s.last_trade_timestamp)],
    ];
    const summaryCards = rows.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${v ?? '-'}</div></div>`).join('');
    const tradeRows = Array.isArray(payload.trades) ? payload.trades : [];
    const details = tradeRows.length
      ? `<details class="card" style="grid-column:1/-1"><summary>Journal trade details (${tradeRows.length})</summary>${tradeRows.map((t, idx) => `
          <div class="card" style="margin-top:8px">
            <div><strong>#${idx + 1}</strong> ${t.symbol ?? '-'}</div>
            ${Object.entries(t).map(([k, v]) => `<div class="muted">${k}: ${typeof v === 'object' ? JSON.stringify(v) : (v ?? '-')}</div>`).join('')}
          </div>`).join('')}</details>`
      : '<div class="card" style="grid-column:1/-1"><div class="muted">No detailed journal rows found.</div></div>';
    journalEl.dataset.state = 'ready';
    journalEl.innerHTML = summaryCards + details;
  }

  function renderQuote(q) {
    const rows = [
      ['Resolved broker', q.broker], ['Resolved symbol', q.symbol], ['Tick size', q.tick_size],
      ['Entry price', q.entry_price], ['Stop price', q.stop_price], ['Target price', q.target_price],
      ['TP distance', q.target_distance ?? '-'], ['Qty / units', q.quantity], ['Notional', q.notional],
      ['Estimated fees / spread', q.estimated_fees_or_spread_aud], ['Estimated total loss in AUD', q.estimated_total_loss_aud],
      ['Estimated reward in AUD', q.estimated_reward_aud], ['R:R', q.rr],
      ['Requested net R', q.requested_rr_net ?? '-'], ['Effective net R', q.effective_rr_net ?? '-'],
      ['Fee buffer (R)', q.fee_buffer_r ?? '-'],
    ];
    resultEl.innerHTML = rows.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${v ?? '-'}</div></div>`).join('');
  }

  const buildFetchError = (url, method, status, statusText, bodyText, bodyJson) => {
    const detail = bodyJson?.detail;
    if (typeof detail === 'string' && detail.trim()) return new Error(detail.trim());
    if (detail && typeof detail === 'object') return new Error(detail.message || detail.error || `${method} ${url} failed: ${status}`);
    const body = (bodyText || '').trim();
    return new Error(`${method || 'GET'} ${url} failed: ${status} ${body || statusText}`);
  };

  async function request(url, opts = {}) {
    const res = await fetch(url, opts);
    const contentType = (res.headers.get('content-type') || '').toLowerCase();
    let bodyText = '';
    let bodyJson = null;
    try {
      bodyText = await res.text();
      const looksJson = contentType.includes('application/json') || /^[\[{]/.test((bodyText || '').trim());
      if (looksJson && bodyText) bodyJson = JSON.parse(bodyText);
    } catch (_err) {
      bodyJson = null;
    }
    if (!res.ok) {
      throw buildFetchError(url, opts.method || 'GET', res.status, res.statusText, bodyText, bodyJson);
    }
    if (bodyJson !== null) return bodyJson;
    return bodyText ? { message: bodyText } : {};
  }

  async function post(url, body) {
    return request(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  }

  function updateRiskUiForAsset() {
    const isFx = state.asset === 'fx';
    riskToggleWrap.style.display = isFx ? '' : 'none';
    if (!isFx) state.risk_mode = 'percent';
    $('calc-risk-label').textContent = isFx ? 'Risk value (AUD or %)' : 'Risk value (%)';
    if (!isFx) {
      $('risk-toggle').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === 'percent'));
    }
  }

  function updateTargetModeUi() {
    const useRr = state.target_mode === 'rr';
    rrWrap.style.display = useRr ? '' : 'none';
    tpTicksWrap.style.display = useRr ? 'none' : '';
  }

  function toggleWebhookPanel(show) {
    webhookPanel.style.display = show ? '' : 'none';
    if (!show) {
      webhookJsonEl.textContent = '';
      webhookPanel.dataset.pendingId = '';
      webhookPanel.dataset.endpoint = '';
    }
  }

  async function cleanupPendingWebhook() {
    if (!state.pendingWebhookId) return;
    const staleId = state.pendingWebhookId;
    try {
      await request(`/api/pending-webhooks/${encodeURIComponent(staleId)}`, { method: 'DELETE' });
    } catch (_err) {
      // best-effort cleanup
    }
    state.pendingWebhookId = '';
    if (state.quote && state.quote.pending_webhook_id === staleId) {
      delete state.quote.pending_webhook_id;
      delete state.quote.webhook_payload_json;
      delete state.quote.webhook_endpoint;
    }
  }

  function setToggle(id, key, onChange) {
    const root = $(id);
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        root.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state[key] = btn.dataset.v;
        state.quote = null;
        if (key === 'order_type') $('limit-wrap').style.display = state.order_type === 'limit' ? '' : 'none';
        if (key === 'target_mode') updateTargetModeUi();
        if (key === 'webhook_mode' && state.webhook_mode !== 'yes') {
          toggleWebhookPanel(false);
          cleanupPendingWebhook();
        }
        if (typeof onChange === 'function') onChange();
      });
    });
  }

  function setTimeframeButtons() {
    const root = $('timeframe-toggle');
    root.innerHTML = TIMEFRAMES.map((tf) => `<button type="button" data-v="${tf}" class="${tf === state.timeframe ? 'active' : ''}">${tf}</button>`).join('');
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        root.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state.timeframe = btn.dataset.v;
      });
    });
  }

  async function resolveSymbolAndLoad() {
    const symbol = $('calc-symbol').value.trim();
    state.quote = null;
    resultEl.innerHTML = '';
    canonicalEl.textContent = '';
    if (!symbol) {
      setJournalState('idle', 'Type a symbol to load journal summary.');
      return;
    }
    if (resolveController) resolveController.abort();
    resolveController = new AbortController();
    try {
      const instrument = await request(`/api/calculator/instrument?asset=${encodeURIComponent(state.asset)}&account=${encodeURIComponent(state.account)}&symbol=${encodeURIComponent(symbol)}`, { signal: resolveController.signal });
      state.resolvedSymbol = instrument.symbol;
      canonicalEl.textContent = `Canonical symbol: ${instrument.symbol}`;
      setJournalState('loading', 'Loading journal summary...');
      if (journalController) journalController.abort();
      journalController = new AbortController();
      const j = await request(`/api/calculator/journal-summary?asset=${encodeURIComponent(state.asset)}&symbol=${encodeURIComponent(symbol)}`, { signal: journalController.signal });
      if (j.status === 'no_data') {
        setJournalState('no_data', `No journal data for ${j.canonical_symbol}.`);
      } else {
        renderJournalStats(j);
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      state.resolvedSymbol = '';
      setJournalState('unresolved', `Unresolved symbol: ${symbol}`);
      canonicalEl.textContent = '';
    }
  }

  function debounceSymbolResolve() {
    if (symbolTimer) clearTimeout(symbolTimer);
    symbolTimer = setTimeout(resolveSymbolAndLoad, 250);
  }

  webhookCopyBtn.addEventListener('click', async () => {
    const text = webhookJsonEl.textContent || '';
    if (!text.trim()) {
      errorEl.textContent = 'No webhook JSON to copy.';
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      okEl.textContent = 'Webhook JSON copied.';
    } catch (err) {
      errorEl.textContent = `Copy failed: ${err?.message || err}`;
    }
  });

  $('calc-symbol').addEventListener('input', debounceSymbolResolve);

  $('calc-quote').addEventListener('click', async () => {
    clearMessages();
    toggleWebhookPanel(false);
    try {
      const payload = {
        ...state,
        symbol: $('calc-symbol').value,
        entry_price: $('calc-limit').value,
        stop_loss_ticks: $('calc-sl-ticks').value,
        take_profit_ticks: state.target_mode === 'ticks' ? $('calc-tp-ticks').value : undefined,
        risk_reward: state.target_mode === 'rr' ? $('calc-rr').value : undefined,
        risk_value: $('calc-risk').value,
        webhook: state.webhook_mode,
        pending_webhook_id: state.webhook_mode === 'yes' ? (state.pendingWebhookId || undefined) : undefined,
        previous_pending_webhook_id: state.webhook_mode === 'yes' ? undefined : (state.pendingWebhookId || undefined),
      };
      const quote = await post('/api/calculator/quote', payload);
      state.quote = quote;
      renderQuote(quote);
      if (state.webhook_mode === 'yes' && quote.webhook_payload_json) {
        state.pendingWebhookId = quote.pending_webhook_id || state.pendingWebhookId;
        webhookJsonEl.textContent = quote.webhook_payload_json;
        webhookPanel.dataset.pendingId = quote.pending_webhook_id || '';
        webhookPanel.dataset.endpoint = quote.webhook_endpoint || '';
        toggleWebhookPanel(true);
      } else {
        state.pendingWebhookId = '';
      }
    } catch (e) {
      state.quote = null;
      resultEl.innerHTML = '';
      toggleWebhookPanel(false);
      errorEl.textContent = String(e.message || e);
    }
  });

  $('calc-submit').addEventListener('click', async () => {
    clearMessages();
    try {
      if (state.webhook_mode === 'yes') throw new Error('Webhook mode is enabled. Use the generated TradingView JSON instead of Submit Order.');
      if (!state.quote) throw new Error('Calculate first.');
      if (!state.timeframe) throw new Error('Timeframe is required.');
      const payload = {
        asset: state.asset,
        account: state.account,
        symbol: state.quote.symbol,
        action: state.side,
        order_type: state.order_type,
        entry_price: state.quote.entry_price,
        stop_loss_price: state.quote.stop_price,
        take_profit_price: state.quote.target_price,
        quantity: state.quote.quantity,
        timeframe: state.timeframe,
      };
      await post('/api/calculator/submit', payload);
      okEl.textContent = 'Order submitted successfully.';
    } catch (e) {
      errorEl.textContent = String(e.message || e);
    }
  });

  setToggle('account-toggle', 'account', resolveSymbolAndLoad);
  setToggle('asset-toggle', 'asset', () => { updateRiskUiForAsset(); resolveSymbolAndLoad(); });
  setToggle('side-toggle', 'side');
  setToggle('order-toggle', 'order_type');
  setToggle('risk-toggle', 'risk_mode');
  setToggle('target-toggle', 'target_mode');
  setToggle('webhook-toggle', 'webhook_mode');
  setTimeframeButtons();
  updateRiskUiForAsset();
  updateTargetModeUi();
  toggleWebhookPanel(false);
  setJournalState('idle', 'Type a symbol to load journal summary.');
})();
