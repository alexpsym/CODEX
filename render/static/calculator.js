(function () {
  const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1mo'];
  const state = {
    account: 'live',
    asset: 'crypto',
    side: 'buy',
    order_type: 'market',
    risk_mode: 'percent',
    timeframe: '15m',
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
  const specsEl = $('calc-instrument-specs');
  const riskToggleWrap = $('risk-toggle-wrap');
  const webhookPanel = $('calc-webhook-panel');
  const webhookJsonEl = $('calc-webhook-json');
  const webhookCopyBtn = $('calc-webhook-copy');
  const JOURNAL_COLUMNS = [
    'Open time', 'Close time', 'Account', 'Symbol', 'Side', 'Timeframe', 'Qty', 'Entry', 'Exit',
    'Stop', 'Target', 'Fees', 'P/L', 'Result %', 'R', 'Balance after', 'Duration', 'Breakeven', 'Chart',
  ];

  let symbolTimer = null;
  let resolveController = null;
  let journalController = null;
  const SPECS_HIDDEN_FIELDS = new Set([
    'contractType',
    'fundingHistory.fundingRate',
    'fundingHistory.fundingRateTimestamp',
    'indexPrice',
    'leverageFilter',
    'lotSizeFilter',
    'markPrice',
    'priceFilter',
    'query',
    'baseCoin',
    'quoteCoin',
    'source',
    'status',
    'scannerVolume24h',
    'openInterest',
    '_units',
  ]);
  const SPECS_FIELD_LABELS = {
    resolved_symbol: 'resolved_symbol',
    category: 'category',
    lastPrice: 'lastPrice (price)',
    fundingRate: 'fundingRate (%)',
    nextFundingTime: 'nextFundingTime (Brisbane time)',
    launchTime: 'launchTime (Brisbane time)',
    openInterestValue: 'openInterestValue (USD)',
    turnover24h: 'turnover24h (USD)',
    avg7dTurnoverUsd: 'avg7dVolume (USD)',
  };

  function clearMessages() {
    errorEl.textContent = '';
    okEl.textContent = '';
  }

  function setJournalState(kind, text) {
    journalEl.dataset.state = kind;
    journalEl.innerHTML = `<div class="muted">${text}</div>`;
  }

  function setSpecsState(kind, text) {
    specsEl.dataset.state = kind;
    const msg = String(text || '').trim();
    specsEl.innerHTML = msg ? `<div class="muted">${msg}</div>` : '';
  }

  function renderSpecs(specs) {
    const isNumericLike = (v) => {
      if (v === null || v === undefined) return false;
      if (typeof v === 'number') return Number.isFinite(v);
      if (typeof v !== 'string') return false;
      const s = v.trim();
      return s !== '' && /^-?\d+(\.\d+)?$/.test(s);
    };
    const compactNumber = (n, decimals = 2) => {
      const num = Number(n);
      if (!Number.isFinite(num)) return String(n ?? '—');
      const abs = Math.abs(num);
      if (abs >= 1e12) return `${(num / 1e12).toFixed(decimals).replace(/\.00$/, '')}T`;
      if (abs >= 1e9) return `${(num / 1e9).toFixed(decimals).replace(/\.00$/, '')}B`;
      if (abs >= 1e6) return `${(num / 1e6).toFixed(decimals).replace(/\.00$/, '')}M`;
      if (abs >= 1e3) return `${(num / 1e3).toFixed(decimals).replace(/\.00$/, '')}K`;
      return num.toFixed(decimals).replace(/\.00$/, '');
    };
    const formatPercentFromFraction = (v, decimals = 4) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return String(v ?? '—');
      return `${(n * 100).toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '')}%`;
    };
    const formatTimestampBrisbane = (value) => {
      if (!isNumericLike(value)) return null;
      const n = Number(value);
      if (!Number.isFinite(n)) return null;
      const ms = n < 1e12 ? n * 1000 : n;
      const d = new Date(ms);
      if (Number.isNaN(d.getTime())) return null;
      return new Intl.DateTimeFormat('en-AU', {
        timeZone: 'Australia/Brisbane',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(d) + ' (Brisbane)';
    };
    const formatSpecsValue = (key, value) => {
      if (key === 'launchTime' || key === 'nextFundingTime' || /(time|timestamp)$/i.test(key)) {
        const ts = formatTimestampBrisbane(value);
        if (ts) return ts;
      }
      if (key === 'fundingRate' || key.endsWith('.fundingRate')) return formatPercentFromFraction(value);
      if (/^(turnover24h|openInterestValue|avg7dTurnoverUsd|volume24h)$/i.test(key)) return `$${compactNumber(value)}`;
      if (typeof value === 'object' && value !== null) return JSON.stringify(value);
      return String(value ?? '—');
    };
    const entries = Object.entries(specs || {})
      .filter(([k]) => !SPECS_HIDDEN_FIELDS.has(k))
      .sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    if (!entries.length) {
      setSpecsState('empty', '');
      return;
    }
    const rows = entries.map(([k, v]) => `
      <tr>
        <td>${SPECS_FIELD_LABELS[k] || k}</td>
        <td>${formatSpecsValue(k, v)}</td>
      </tr>
    `).join('');
    specsEl.dataset.state = 'ready';
    specsEl.innerHTML = `<div class="card"><table class="specs-table">${rows}</table></div>`;
  }

  const fmtPct = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return `${n.toFixed(2)}%`;
  };
  const fmtNum = (value, dp = 2) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return n.toLocaleString(undefined, { maximumFractionDigits: dp });
  };
  const tickDecimals = (tickSize) => {
    const raw = String(tickSize ?? '').trim();
    if (!raw) return 2;
    if (/e-/i.test(raw)) {
      const parts = raw.toLowerCase().split('e-');
      const exp = Number(parts[1]);
      return Number.isFinite(exp) ? Math.max(0, Math.min(10, exp)) : 2;
    }
    const dot = raw.indexOf('.');
    if (dot < 0) return 0;
    return Math.max(0, Math.min(10, raw.length - dot - 1));
  };
  const fmtPriceLike = (value, tickSize) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return n.toFixed(tickDecimals(tickSize));
  };
  const fmtR = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return n.toFixed(2);
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
      ? `<details class="card">
          <summary>Journal trade details (${tradeRows.length})</summary>
          <div class="journal-details">
            <table class="journal-detail-table">
              <thead><tr>${JOURNAL_COLUMNS.map((h) => `<th style="text-align:left">${h}</th>`).join('')}</tr></thead>
              <tbody>${tradeRows.map((r) => renderJournalRow(r)).join('')}</tbody>
            </table>
          </div>
        </details>`
      : '<div class="card"><div class="muted">No detailed journal rows found.</div></div>';
    journalEl.dataset.state = 'ready';
    journalEl.innerHTML = summaryCards + details;
  }

  function renderJournalRow(r) {
    const pnl = Number(r.realized_pnl ?? r.net_profit);
    const resultPct = Number(r.result_pct ?? r.profit_pct);
    const rMultiple = Number(r.r_multiple);
    const bal = Number(r.balance_after_trade);
    const ccy = r.balance_currency || r.currency || '';
    const pnlCls = Number.isFinite(pnl) ? (pnl > 0 ? 'color:#86efac' : (pnl < 0 ? 'color:#fca5a5' : '')) : '';
    const pctCls = Number.isFinite(resultPct) ? (resultPct > 0 ? 'color:#86efac' : (resultPct < 0 ? 'color:#fca5a5' : '')) : '';
    const rCls = Number.isFinite(rMultiple) ? (rMultiple > 0 ? 'color:#86efac' : (rMultiple < 0 ? 'color:#fca5a5' : '')) : '';
    const chart = r.id ? `<a href="/trade-chart/${encodeURIComponent(r.id)}" target="_blank" rel="noopener">Chart</a>` : '';
    return `<tr>
      <td>${fmtBrisbaneTime(r.open_time)}</td>
      <td>${fmtBrisbaneTime(r.close_time || r.open_time)}</td>
      <td>${r.account_label || r.account || '-'}</td>
      <td>${r.symbol || '-'}</td>
      <td>${r.side || '-'}</td>
      <td>${r.timeframe || (r.metrics?.timeframe) || '-'}</td>
      <td>${fmtNum(r.qty, 8)}</td>
      <td>${fmtNum(r.entry_price, 6)}</td>
      <td>${fmtNum(r.exit_price, 6)}</td>
      <td>${fmtNum(r.stop_loss, 6)}</td>
      <td>${fmtNum(r.take_profit, 6)}</td>
      <td>${fmtNum(r.commission ?? r.fees, 4)} ${r.commission_currency || r.fee_currency || ''}</td>
      <td style="${pnlCls}">${fmtNum(pnl, 4)} ${r.realized_pnl_currency || r.currency || ''}</td>
      <td style="${pctCls}">${Number.isFinite(resultPct) ? `${fmtNum(resultPct, 4)}%` : '-'}</td>
      <td style="${rCls}">${Number.isFinite(rMultiple) ? `${fmtNum(rMultiple, 3)}R` : '-'}</td>
      <td>${Number.isFinite(bal) ? `${fmtNum(bal, 2)} ${ccy}` : '-'}</td>
      <td>${fmtDuration(r.trade_duration_seconds)}</td>
      <td>${r.breakeven || '-'}</td>
      <td>${chart}</td>
    </tr>`;
  }

  function renderQuote(q) {
    const currency = q.display_currency || 'AUD';
    const fee = Number(q.estimated_fees_or_spread);
    const loss = Number(q.estimated_total_loss);
    const reward = Number(q.estimated_reward);
    const tickSize = q.tick_size;
    const rows = [
      ['Resolved broker', q.broker], ['Resolved symbol', q.symbol],
      ['Entry price', fmtPriceLike(q.entry_price, tickSize)], ['Stop price', fmtPriceLike(q.stop_price, tickSize)], ['Target price', fmtPriceLike(q.target_price, tickSize)],
      ['TP distance', fmtPriceLike(q.target_distance, tickSize)], ['Qty / units', q.quantity],
      ['Estimated fees / spread', `${Number.isFinite(fee) ? fee.toFixed(2) : '-'} ${currency}`],
      ['Estimated total loss', `${Number.isFinite(loss) ? loss.toFixed(2) : '-'} ${currency}`],
      ['Estimated reward', `${Number.isFinite(reward) ? reward.toFixed(2) : '-'} ${currency}`],
      ['Requested net R', fmtR(q.requested_rr_net)], ['Effective net R', fmtR(q.effective_rr_net)],
      ['Fee buffer (R)', fmtR(q.fee_buffer_r)],
    ];
    if (Array.isArray(q.warnings) && q.warnings.length) {
      rows.push(['Warnings', q.warnings.map((w) => String(w || '').replace(/\s+/g, ' ').trim()).join(' | ')]);
    }
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

  function syncToggleState(id, key) {
    const root = $(id);
    if (!root) return;
    root.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === state[key]));
  }

  function syncAllToggleStates() {
    syncToggleState('account-toggle', 'account');
    syncToggleState('asset-toggle', 'asset');
    syncToggleState('side-toggle', 'side');
    syncToggleState('order-toggle', 'order_type');
    syncToggleState('risk-toggle', 'risk_mode');
    syncToggleState('webhook-toggle', 'webhook_mode');
  }

  function setToggle(id, key, onChange) {
    const root = $(id);
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        state[key] = btn.dataset.v;
        syncToggleState(id, key);
        state.quote = null;
        if (key === 'order_type') $('limit-wrap').style.display = state.order_type === 'limit' ? '' : 'none';
        if (key === 'webhook_mode' && state.webhook_mode !== 'yes') {
          toggleWebhookPanel(false);
          cleanupPendingWebhook();
        }
        if (typeof onChange === 'function') onChange();
        syncAllToggleStates();
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
      setSpecsState('idle', 'Type a symbol to load instrument specs.');
      return;
    }
    if (resolveController) resolveController.abort();
    resolveController = new AbortController();
    try {
      const instrument = await request(`/api/calculator/instrument?asset=${encodeURIComponent(state.asset)}&account=${encodeURIComponent(state.account)}&symbol=${encodeURIComponent(symbol)}`, { signal: resolveController.signal });
      state.resolvedSymbol = instrument.symbol;
      canonicalEl.textContent = `Canonical symbol: ${instrument.symbol}`;
      setSpecsState('loading', 'Loading instrument specs...');
      const prefer = state.asset === 'fx' ? '&prefer=oanda' : '';
      try {
        const specs = await request(`/api/instrument-specs?query=${encodeURIComponent(instrument.symbol)}${prefer}`, { signal: resolveController.signal });
        renderSpecs(specs);
      } catch (_specErr) {
        setSpecsState('error', `Unable to load instrument specs for ${instrument.symbol}.`);
      }
      setJournalState('loading', 'Loading journal summary...');
      if (journalController) journalController.abort();
      journalController = new AbortController();
      try {
        const j = await request(`/api/calculator/journal-summary?asset=${encodeURIComponent(state.asset)}&symbol=${encodeURIComponent(symbol)}`, { signal: journalController.signal });
        if (j.status === 'no_data') {
          setJournalState('no_data', `No journal data for ${j.canonical_symbol}.`);
        } else {
          renderJournalStats(j);
        }
      } catch (_journalErr) {
        setJournalState('error', `Unable to load journal summary for ${instrument.symbol}.`);
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      state.resolvedSymbol = '';
      setJournalState('unresolved', `Unresolved symbol: ${symbol}`);
      setSpecsState('unresolved', `Unresolved symbol: ${symbol}`);
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
        risk_reward: $('calc-rr').value,
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
  setToggle('webhook-toggle', 'webhook_mode');
  setTimeframeButtons();
  updateRiskUiForAsset();
  syncAllToggleStates();
  toggleWebhookPanel(false);
  setJournalState('idle', 'Type a symbol to load journal summary.');
  setSpecsState('idle', 'Type a symbol to load instrument specs.');
})();
