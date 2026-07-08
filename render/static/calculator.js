(function () {
  const TIMEFRAMES = [['1m','1MIN'], ['5m','5MIN'], ['15m','15MIN'], ['30m','30MIN'], ['1h','1H'], ['4h','4H'], ['1d','DAILY'], ['1w','WEEKLY'], ['1mo','MONTHLY']];
  const state = {
    account: 'live',
    asset: 'crypto',
    broker: 'oanda',
    side: 'buy',
    order_type: 'market',
    risk_mode: 'percent',
    fx_risk_mode: 'percent',
    timeframe: '15m',
    webhook_mode: 'no',
    test_mode: 'no',
    setup: '',
    pattern: '',
    ema: '',
    aths_atls: '',
    round_number: '',
    quote: null,
    resolvedSymbol: '',
    pendingWebhookId: '',
    pendingWebhookDeleteUrl: '',
    quoteStatus: 'idle',
    hasCalculatedOnce: false,
    quoteRequestSeq: 0,
    quoteController: null,
    webhookCapability: null,
    quotePrewarmStatus: null,
    quotePrewarmPromise: null,
    quotePrewarmContext: null,
    pepperstoneSetPayload: null,
  };

  const $ = (id) => document.getElementById(id);
  const errorEl = $('calc-error');
  const errorDebugEl = $('calc-error-debug');
  const okEl = $('calc-success');
  const resultEl = $('calc-results');
  const requestSummaryEl = $('calc-request-summary');
  const canonicalEl = $('calc-canonical-symbol');
  const journalEl = $('calc-journal-summary');
  const specsEl = $('calc-instrument-specs');
  const riskToggleWrap = $('risk-toggle-wrap');
  const brokerToggleWrap = $('broker-toggle-wrap');
  const webhookPanel = $('calc-webhook-panel');
  const webhookUrlEl = $('calc-webhook-url');
  const webhookJsonEl = $('calc-webhook-json');
  const webhookCopyBtn = $('calc-webhook-copy');
  const webhookCopyUrlBtn = $('calc-webhook-copy-url');
  const submitBtn = $('calc-submit');
  const pepperstoneSetBtn = $('calc-pepperstone-set');
  const quoteStatusEl = $('calc-quote-status');
  const webhookStatusEl = $('calc-webhook-status');
  const SUBMIT_LABEL = 'Submit Order';
  const SUBMITTING_LABEL = 'Submitting...';
  let submitInFlight = false;
  let submitStateResetTimer = null;

  let symbolTimer = null;
  let walletPrewarmInterval = null;
  let resolveController = null;
  let resolveInFlight = null;
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
    'volume24h',
    '_units',
    '_btc_reference',
    '_spec_warnings',
  ]);
  const SPECS_FIELD_LABELS = {
    resolved_symbol: 'resolved_symbol',
    category: 'category',
    lastPrice: 'lastPrice (price)',
    fundingRate: 'fundingRate (%)',
    nextFundingTime: 'nextFundingTime (Brisbane time)',
    launchTime: 'launchTime (Brisbane time)',
    openInterestValue: 'openInterestValue (USD)',
    volume24hUsd: 'volume24h (USD)',
    turnover24h: 'volume24h (USD)',
    avg7dTurnoverUsd: 'avg7dVolume (USD)',
    'range.1m': 'range 1m (%)',
    'range.5m': 'range 5m (%)',
    'range.15m': 'range 15m (%)',
    'range.30m': 'range 30m (%)',
    'range.1h': 'range 1h (%)',
    'range.4h': 'range 4h (%)',
    'range.1d': 'range daily (%)',
    'range.1w': 'range weekly (%)',
    'range.1mo': 'range monthly (%)',
  };

  function clearMessages() {
    errorEl.textContent = '';
    okEl.textContent = '';
    errorDebugEl.innerHTML = '';
  }

  function setJournalState(kind, text) {
    if (!journalEl) return;
    journalEl.dataset.state = kind;
    journalEl.innerHTML = `<div class="muted">${text}</div>`;
  }

  function setSpecsState(kind, text) {
    if (!specsEl) return;
    specsEl.dataset.state = kind;
    const msg = String(text || '').trim();
    specsEl.innerHTML = msg ? `<div class="muted">${msg}</div>` : '';
  }

  function setQuoteStatus(text) {
    if (quoteStatusEl) quoteStatusEl.textContent = text || '';
  }
  function setPrewarmStatus(status) {
    state.quotePrewarmStatus = status || null;
    state.quotePrewarmContext = status
      ? { account: String(status.account || state.account || '').toLowerCase(), asset: String(status.asset || state.asset || '').toLowerCase(), symbol: String(status.symbol || '').toUpperCase() }
      : null;
    if (!status) return;
    const missing = new Set((status.missing_required || []).map((x) => String(x || '').toLowerCase()));
    const walletErr = String(status.wallet_error || '').trim();
    const tickerErr = String(status.ticker_error || '').trim();
    if (status.ready_for_quote) {
      setQuoteStatus('Quote data ready');
    } else if (missing.has('wallet') && missing.has('ticker')) {
      const detail = [walletErr, tickerErr].filter(Boolean).join('; ');
      setQuoteStatus(detail ? `Quote prewarm unavailable for wallet/ticker; Calculate will retry live. (${detail})` : 'Quote prewarm unavailable for wallet/ticker; Calculate will retry live.');
    } else if (missing.has('ticker')) {
      setQuoteStatus(tickerErr ? `Ticker prewarm unavailable; Calculate will retry live. (${tickerErr})` : 'Ticker prewarm unavailable; Calculate will retry live.');
    } else if (missing.has('wallet')) {
      setQuoteStatus(walletErr ? `Wallet prewarm unavailable; Calculate will retry live. (${walletErr})` : 'Wallet prewarm unavailable; Calculate will retry live.');
    } else {
      setQuoteStatus('Quote data prewarm incomplete; Calculate will retry live.');
    }
  }

  function expiredKeyActionableMessage(accountHint) {
    const acct = String(accountHint || state.account || '').toLowerCase();
    if (acct === 'demo') return 'Bybit Demo API key expired. Replace BYBIT_API_KEY2/BYBIT_API_SECRET2, then restart Local Trading Tools.';
    return 'Bybit Live API key expired. Replace BYBIT_API_KEY1/BYBIT_API_SECRET1, then restart Local Trading Tools.';
  }

  function extractExpiredBybitKeyMessage(detail) {
    const dbg = (detail && detail.debug) || {};
    const code = String((detail && detail.code) || (dbg && dbg.code) || '').toUpperCase();
    const blob = JSON.stringify(detail || {}).toLowerCase();
    if (code === 'BYBIT_API_KEY_EXPIRED' || blob.includes('retcode=33004') || blob.includes('"retcode":33004') || blob.includes('api key has expired')) {
      return expiredKeyActionableMessage((detail && detail.account) || dbg.account || state.account);
    }
    return '';
  }

  function refreshPrewarmSchedule() {
    if (walletPrewarmInterval) {
      clearInterval(walletPrewarmInterval);
      walletPrewarmInterval = null;
    }
    if (state.asset === 'crypto') {
      prewarmAccountDependencies();
      if (typeof setInterval === 'function') {
        walletPrewarmInterval = setInterval(() => { prewarmAccountDependencies(); }, 20000);
        if (walletPrewarmInterval && typeof walletPrewarmInterval.unref === 'function') walletPrewarmInterval.unref();
      }
    }
  }
  function webhookUnavailableMessage() {
    return 'Set RENDER_CALCULATOR_BASE_URL to the Render service URL to generate Render-owned TradingView webhook alerts from the local calculator. Webhook=No calculation remains available.';
  }

  function setSubmitState({ visible, enabled, reason = '', stateName = '' }) {
    submitBtn.style.display = visible ? '' : 'none';
    submitBtn.disabled = !(enabled && state.quote && state.quoteStatus === 'ready' && state.webhook_mode !== 'yes' && state.quote.quote_valid_for_submit !== false);
    submitBtn.title = reason || '';
    if (stateName) submitBtn.dataset.state = stateName;
  }

  function isPepperstoneFx() {
    return state.asset === 'fx' && state.broker === 'pepperstone';
  }

  function setPepperstoneSetButton({ visible, enabled = true, reason = '' } = {}) {
    if (!pepperstoneSetBtn) return;
    pepperstoneSetBtn.style.display = visible ? '' : 'none';
    pepperstoneSetBtn.disabled = !enabled;
    pepperstoneSetBtn.title = reason || '';
  }

  function clearPepperstoneSetDownload() {
    state.pepperstoneSetPayload = null;
    setPepperstoneSetButton({ visible: false, enabled: false });
  }

  function clearSubmitStateResetTimer() {
    if (!submitStateResetTimer) return;
    clearTimeout(submitStateResetTimer);
    submitStateResetTimer = null;
  }

  function markSubmitClicked() {
    clearSubmitStateResetTimer();
    submitBtn.dataset.submitVisualState = 'submitting';
    submitBtn.textContent = SUBMITTING_LABEL;
  }

  function clearSubmitClicked() {
    clearSubmitStateResetTimer();
    delete submitBtn.dataset.submitVisualState;
    submitBtn.textContent = SUBMIT_LABEL;
  }

  function invalidateQuote({ clearResults = true, status = 'stale', reason = '' } = {}) {
    clearSubmitClicked();
    clearPepperstoneSetDownload();
    state.quote = null;
    state.quoteStatus = status;
    const visible = status === 'idle' ? false : state.hasCalculatedOnce;
    setSubmitState({ visible, enabled: false, reason, stateName: status });
    if (status === 'calculating') setQuoteStatus('Calculating position…');
    else if (status === 'stale') setQuoteStatus('Quote changed. Recalculate before submitting.');
    else if (status === 'error') setQuoteStatus('Quote failed. Recalculate before submitting.');
    else if (status === 'idle') setQuoteStatus('');
    if (clearResults) resultEl.innerHTML = status === 'calculating' ? '<div class="card"><div class="muted">Calculating position…</div></div>' : '';
    state.quotePrewarmStatus = null;
    state.quotePrewarmContext = null;
  }

  function renderSpecs(specs) {
    if (!specsEl) return;
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
      if (key === 'fundingRate' || key.endsWith('.fundingRate') || key.startsWith('range.')) return formatPercentFromFraction(value, 2);
      if (/^(volume24hUsd|turnover24h|openInterestValue|avg7dTurnoverUsd)$/i.test(key)) return `$${compactNumber(value)}`;
      if (typeof value === 'object' && value !== null) return JSON.stringify(value);
      return String(value ?? '—');
    };
    const ORDER=['resolved_symbol','category','lastPrice','fundingRate','nextFundingTime','launchTime','openInterestValue','volume24hUsd','turnover24h','avg7dTurnoverUsd','range.1m','range.5m','range.15m','range.30m','range.1h','range.4h','range.1d','range.1w','range.1mo'];
    const btcRef = (specs && typeof specs._btc_reference==='object')?specs._btc_reference:null;
    const keys = Object.keys(specs||{}).filter((k)=>!SPECS_HIDDEN_FIELDS.has(k));
    const entries=[...ORDER.filter((k)=>keys.includes(k)).map((k)=>[k,specs[k]]), ...keys.filter((k)=>!ORDER.includes(k)).sort().map((k)=>[k,specs[k]])];
    if (!entries.length) {
      setSpecsState('empty', '');
      return;
    }
    const rows=[];
    for (const [k,v] of entries){ const label=SPECS_FIELD_LABELS[k]||k; rows.push(`<tr><td>${label}</td><td>${formatSpecsValue(k,v)}</td></tr>`); if (btcRef && btcRef[k]!==undefined){ rows.push(`<tr class="btc-reference-row"><td>BTC ${label}</td><td>${formatSpecsValue(k,btcRef[k])}</td></tr>`);} }
    const warnings=Array.isArray(specs?._spec_warnings)?specs._spec_warnings:[];
    const warnHtml=warnings.length?`<div class="muted">Some instrument specs could not be loaded: ${warnings.map((w)=>`${w.field||'spec'} ${w.symbol||''}`).join(', ')}</div>`:'';
    specsEl.dataset.state = 'ready';
    specsEl.innerHTML = `<div class="card"><table class="specs-table">${rows.join('')}</table>${warnHtml}</div>`;
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
  function safeTimeout(fn, ms) {
    let sync = true;
    const id = setTimeout(() => { if (!sync) fn(); }, ms);
    sync = false;
    return id;
  }

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
    if (!journalEl) return;
    const s = payload.stats || {};
    const tradeRows = Array.isArray(payload.trades) ? payload.trades : [];
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
      ['Detailed rows available', tradeRows.length],
    ];
    const summaryCards = rows.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${v ?? '-'}</div></div>`).join('');
    journalEl.dataset.state = 'ready';
    journalEl.innerHTML = summaryCards;
  }

  function renderQuote(q) {
    const currency = q.display_currency || q.account_currency || 'AUD';
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
      ['Account currency', q.account_currency || currency],
      ['Submitted risk mode', q.submitted_risk_mode ?? '-'],
      ['Submitted risk value', q.submitted_risk_value ?? '-'],
      ['Submitted stop ticks', q.submitted_stop_loss_ticks ?? '-'],
      ['Risk input (AUD)', q.risk_input_aud ?? '-'],
      ['Risk amount (home)', q.risk_amount_home ?? '-'],
      ['Margin rate', q.margin_rate ?? '-'],
      ['Position value factor', q.position_value_factor ?? '-'],
      ['Estimated position value (home)', q.estimated_position_value_home ?? '-'],
      ['Estimated initial margin (home)', q.estimated_initial_margin_home ?? '-'],
      ['Margin available (home)', q.margin_available_home ?? '-'],
      ['Entry price used', q.entry_price_used ?? '-'],
      ['Spread (quote)', q.spread_quote ?? '-'],
      ['Loss per unit (home)', q.loss_per_unit_home ?? '-'],
      ['Units raw', q.units_raw ?? '-'],
      ['Units final', q.units_final ?? '-'],
      ['Requested net R', fmtR(q.requested_rr_net)], ['Effective net R', fmtR(q.effective_rr_net)],
      ['Fee buffer (R)', fmtR(q.fee_buffer_r)],
    ];
    if (q.take_profit_adjusted && q.take_profit_adjustment) {
      const adj = q.take_profit_adjustment;
      rows.push(['TP auto-adjusted', 'Yes']);
      rows.push(['TP original', fmtPriceLike(adj.original_take_profit, tickSize)]);
      rows.push(['TP adjusted', fmtPriceLike(adj.adjusted_take_profit, tickSize)]);
      rows.push(['TP reason', adj.reason || '-']);
      rows.push(['TP last price anchor', fmtPriceLike(adj.last_price, tickSize)]);
    }
    if (Array.isArray(q.warnings) && q.warnings.length) {
      rows.push(['Warnings', q.warnings.map((w) => String(w || '').replace(/\s+/g, ' ').trim()).join(' | ')]);
    }
    rows.push(['Quote latency (ms)', q.quote_latency_ms ?? '-']);
    if (q.upstream_timings_ms && typeof q.upstream_timings_ms === 'object') {
      Object.entries(q.upstream_timings_ms).forEach(([k, v]) => rows.push([`Timing: ${k}`, (v && typeof v === "object") ? JSON.stringify(v, null, 2) : v]));
    }
    resultEl.innerHTML = rows.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${v ?? '-'}</div></div>`).join('');
  }

  const buildFetchError = (url, method, status, statusText, bodyText, bodyJson) => {
    const detail = bodyJson?.detail;
    if (typeof detail === 'string' && detail.trim()) return new Error(detail.trim());
    if (detail && typeof detail === 'object') {
      const err = new Error(detail.message || detail.error || detail.code || `${method} ${url} failed: ${status}`);
      err.detail = { ...detail, code: detail.code, message: detail.message || detail.error, debug: detail.debug };
      err.debug = detail.debug;
      return err;
    }
    if (bodyJson && typeof bodyJson === 'object' && (bodyJson.message || bodyJson.code || bodyJson.debug)) {
      const err = new Error(bodyJson.message || bodyJson.code || `${method} ${url} failed: ${status}`);
      err.detail = { code: bodyJson.code, message: bodyJson.message, debug: bodyJson.debug };
      err.debug = bodyJson.debug;
      return err;
    }
    const body = (bodyText || '').trim();
    return new Error(`${method || 'GET'} ${url} failed: ${status} ${body || statusText}`);
  };

  function renderErrorDebug(detail) {
    const debug = detail?.debug;
    if (!debug || typeof debug !== 'object') {
      errorDebugEl.innerHTML = '';
      return;
    }
    const escapeHtml = (value) => String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
    const formatDebugValue = (v) => {
      if (v && typeof v === "object") {
        try { return `<pre>${escapeHtml(JSON.stringify(v, null, 2))}</pre>`; } catch (_err) { return '<pre>[unserializable debug value]</pre>'; }
      }
      return escapeHtml(v ?? '-');
    };
    const rows = Object.entries(debug)
      .map(([k, v]) => `<div class="card"><div class="muted">${escapeHtml(k)}</div><div>${formatDebugValue(v)}</div></div>`)
      .join('');
    errorDebugEl.innerHTML = rows;
  }

  function renderRequestSummary(payload) {
    const lines = [
      `Submitted payload:`,
      `asset=${payload.asset}`,
      `broker=${payload.broker || state.broker || ''}`,
      `account=${payload.account}`,
      `symbol=${payload.symbol}`,
      `webhook=${payload.webhook}`,
      `test=${payload.test}`,
      `timeframe=${state.timeframe || payload.timeframe || ''}`,
      `setup=${state.setup || payload.setup || ''}`,
      `pattern=${state.pattern || payload.pattern || ''}`,
      `ema=${state.ema || payload.ema || ''}`,
      `aths_atls=${state.aths_atls || payload.aths_atls || ''}`,
      `round_number=${state.round_number || payload.round_number || ''}`,
      `risk_mode=${payload.risk_mode}`,
      `risk_value=${payload.risk_value}`,
      `stop_loss_ticks=${payload.stop_loss_ticks}`,
      `order_type=${payload.order_type}`,
      `side=${payload.side}`,
      `pending_webhook_id=${payload.pending_webhook_id || ''}`,
      `previous_pending_webhook_id=${payload.previous_pending_webhook_id || ''}`,
    ];
    [
      'entry_price',
      'stop_loss_price',
      'take_profit_price',
      'quantity',
      'planned_entry_price',
      'planned_stop_price',
      'planned_target_price',
      'level_anchor_mode',
      'calculation_context_id',
      'quote_created_at_ms',
    ].forEach((k) => {
      if (payload[k] !== undefined && payload[k] !== null && payload[k] !== '') lines.push(`${k}=${payload[k]}`);
    });
    requestSummaryEl.style.whiteSpace = 'pre-wrap';
    requestSummaryEl.textContent = lines.join('\n');
  }

  function buildPepperstoneSetPayload(quote) {
    return {
      asset: 'fx',
      broker: 'pepperstone',
      account: state.account,
      symbol: quote?.symbol || state.resolvedSymbol || $('calc-symbol').value,
      side: state.side,
      order_type: state.order_type,
      entry_price: quote?.entry_price || $('calc-limit').value,
      stop_loss_ticks: $('calc-sl-ticks').value,
      risk_reward: $('calc-rr').value,
      risk_mode: state.risk_mode,
      risk_value: $('calc-risk').value,
      estimated_total_loss_aud: quote?.estimated_total_loss_aud || quote?.estimated_total_loss || '',
      estimated_total_loss: quote?.estimated_total_loss || '',
    };
  }

  function filenameFromDisposition(disposition, fallback) {
    const text = String(disposition || '');
    const match = text.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
    const raw = match ? (match[1] || match[2] || '') : '';
    try {
      return raw ? decodeURIComponent(raw) : fallback;
    } catch (_err) {
      return raw || fallback;
    }
  }

  function saveTextDownload(text, filename) {
    if (typeof Blob === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function' || typeof document.createElement !== 'function') return false;
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = 'none';
    if (document.body && typeof document.body.appendChild === 'function') document.body.appendChild(anchor);
    if (typeof anchor.click === 'function') anchor.click();
    if (anchor.parentNode && typeof anchor.parentNode.removeChild === 'function') anchor.parentNode.removeChild(anchor);
    URL.revokeObjectURL(url);
    return true;
  }

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

  async function post(url, body, opts = {}) {
    return request(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), ...opts });
  }

  function updateRiskUiForAsset() {
    const isFx = state.asset === 'fx';
    riskToggleWrap.style.display = isFx ? '' : 'none';
    if (isFx) {
      state.risk_mode = state.fx_risk_mode || state.risk_mode || 'percent';
    } else {
      if (state.risk_mode === 'fixed_aud' || state.risk_mode === 'percent') {
        state.fx_risk_mode = state.risk_mode;
      }
      state.risk_mode = 'percent';
    }
    $('calc-risk-label').textContent = isFx ? 'Risk value (AUD or %)' : 'Risk value (%)';
    if (!isFx) {
      $('risk-toggle').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === 'percent'));
    }
  }

  function updateBrokerUiForAsset() {
    const isFx = state.asset === 'fx';
    if (brokerToggleWrap) brokerToggleWrap.style.display = isFx ? '' : 'none';
    if (!isFx) state.broker = 'oanda';
    if (isPepperstoneFx() && state.webhook_mode === 'yes') {
      state.webhook_mode = 'no';
      toggleWebhookPanel(false);
      cleanupPendingWebhook();
    }
    syncToggleState('broker-toggle', 'broker');
    syncToggleState('webhook-toggle', 'webhook_mode');
  }

  function toggleWebhookPanel(show) {
    webhookPanel.style.display = show ? '' : 'none';
    if (!show) {
      webhookUrlEl.textContent = '';
      webhookJsonEl.textContent = '';
      webhookPanel.dataset.pendingId = '';
      webhookPanel.dataset.endpoint = '';
      state.pendingWebhookDeleteUrl = '';
    }
  }

  async function cleanupPendingWebhook() {
    if (!state.pendingWebhookId) return;
    const staleId = state.pendingWebhookId;
    try {
      const deleteUrl = state.pendingWebhookDeleteUrl || `/api/pending-webhooks/${encodeURIComponent(staleId)}`;
      await request(deleteUrl, { method: 'DELETE' });
    } catch (err) {
      if (webhookStatusEl) webhookStatusEl.textContent = `Pending webhook cleanup failed: ${err?.message || err}`;
    }
    state.pendingWebhookId = '';
    state.pendingWebhookDeleteUrl = '';
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
    syncToggleState('broker-toggle', 'broker');
    syncToggleState('side-toggle', 'side');
    syncToggleState('order-toggle', 'order_type');
    syncToggleState('risk-toggle', 'risk_mode');
    syncToggleState('webhook-toggle', 'webhook_mode');
    syncToggleState('test-toggle', 'test_mode');
  }

  function setToggle(id, key, onChange) {
    const root = $(id);
    if (!root) return;
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        const ariaDisabled = typeof btn.getAttribute === 'function' ? btn.getAttribute('aria-disabled') : btn.ariaDisabled;
        if (btn.disabled || ariaDisabled === 'true') return;
        state[key] = btn.dataset.v;
        if (key === 'risk_mode' && state.asset === 'fx') {
          state.fx_risk_mode = state.risk_mode;
        }
        syncToggleState(id, key);
        invalidateQuote({ status: state.hasCalculatedOnce ? 'stale' : 'idle', reason: 'Quote changed. Recalculate before submitting.' });
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

  async function loadBootstrapCapability() {
    try {
      const bootstrap = await request('/api/calculator/bootstrap', { cache: 'no-store' });
      state.webhookCapability = bootstrap?.webhook || null;
      const yesBtn = $('webhook-toggle').querySelectorAll('button')[1];
      if (state.webhookCapability && state.webhookCapability.available === false) {
        state.webhook_mode = 'no';
        yesBtn.disabled = true;
        if (typeof yesBtn.setAttribute === 'function') yesBtn.setAttribute('aria-disabled', 'true');
        else yesBtn.ariaDisabled = 'true';
        yesBtn.title = state.webhookCapability?.unavailable_message || webhookUnavailableMessage();
        let warn = state.webhookCapability?.unavailable_message || webhookUnavailableMessage();
        const msg = String(state.webhookCapability?.unavailable_message || '');
        if ((msg.includes('PUBLIC_WEBHOOK_BASE_URL') || (bootstrap?.app_profile === 'local' && !msg.includes('RENDER_CALCULATOR_BASE_URL')))) {
          warn = 'Stale local server code detected. Restart local master from the replaced CODEX-master folder.';
        }
        if (webhookStatusEl) webhookStatusEl.textContent = warn;
      } else {
        yesBtn.disabled = false;
        if (typeof yesBtn.removeAttribute === 'function') yesBtn.removeAttribute('aria-disabled');
        else yesBtn.ariaDisabled = '';
        yesBtn.title = '';
        if (webhookStatusEl) webhookStatusEl.textContent = '';
      }
    } catch (_err) {
      state.webhookCapability = null;
      if (webhookStatusEl) webhookStatusEl.textContent = 'Webhook availability could not be verified; server will validate on calculate.';
    }
  }

  function setTimeframeButtons() {
    const root = $('timeframe-toggle');
    root.innerHTML = TIMEFRAMES.map(([tf,label]) => `<button type="button" data-v="${tf}" class="${tf === state.timeframe ? 'active' : ''}">${label}</button>`).join('');
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        root.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state.timeframe = btn.dataset.v;
      });
    });
  }
  function setSetupButtons() {
    const root = $('setup-toggle'); if (!root) return;
    const opts = [['','None'],['Pullback','pullback'],['Breakout','breakout'],['News Scalp','news scalp']];
    root.innerHTML = opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${v===state.setup?'active':''}">${l}</button>`).join('');
    root.querySelectorAll('button').forEach((btn)=>btn.addEventListener('click',()=>{state.setup=btn.dataset.v||''; setSetupButtons(); invalidateQuote();}));
  }
  function setPatternButtons() {
    const root = $('pattern-toggle'); if (!root) return;
    const opts = [['','None'],['range','range'],['channel','channel']];
    root.innerHTML = opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${v===state.pattern?'active':''}">${l}</button>`).join('');
    root.querySelectorAll('button').forEach((btn)=>btn.addEventListener('click',()=>{state.pattern=btn.dataset.v||''; setPatternButtons(); invalidateQuote();}));
  }
  function setEmaButtons() {
    const root = $('ema-toggle'); if (!root) return;
    const opts = [['','None'],['9','9'],['20','20']];
    root.innerHTML = opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${v===state.ema?'active':''}">${l}</button>`).join('');
    root.querySelectorAll('button').forEach((btn)=>btn.addEventListener('click',()=>{state.ema=btn.dataset.v||''; setEmaButtons(); invalidateQuote();}));
  }
  function setAthsAtlsButtons() {
    const root = $('aths-atls-toggle'); if (!root) return;
    const opts = [['','None'],['All-Time High','All-Time High'],['All-Time Low','All-Time Low']];
    root.innerHTML = opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${v===state.aths_atls?'active':''}">${l}</button>`).join('');
    root.querySelectorAll('button').forEach((btn)=>btn.addEventListener('click',()=>{state.aths_atls=btn.dataset.v||''; setAthsAtlsButtons(); invalidateQuote();}));
  }
  function setRoundNumberButtons() {
    const root = $('round-number-toggle'); if (!root) return;
    const opts = [['','None'],['Yes','Yes'],['No','No']];
    root.innerHTML = opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${v===state.round_number?'active':''}">${l}</button>`).join('');
    root.querySelectorAll('button').forEach((btn)=>btn.addEventListener('click',()=>{state.round_number=btn.dataset.v||''; setRoundNumberButtons(); invalidateQuote();}));
  }

  async function resolveSymbolAndLoad() {
    const symbol = $('calc-symbol').value.trim();
    invalidateQuote();
    canonicalEl.textContent = '';
    if (!symbol) {
      return;
    }
    if (resolveController) resolveController.abort();
    resolveController = new AbortController();
    try {
      resolveInFlight = request(`/api/calculator/instrument?asset=${encodeURIComponent(state.asset)}&account=${encodeURIComponent(state.account)}&symbol=${encodeURIComponent(symbol)}`, { signal: resolveController.signal });
      const instrument = await resolveInFlight;
      state.resolvedSymbol = instrument.symbol;
      canonicalEl.textContent = `Canonical symbol: ${instrument.symbol}`;
      prewarmQuoteDependencies(instrument.symbol);
    } catch (e) {
      if (e.name === 'AbortError') {
        return;
      }
      state.resolvedSymbol = '';
      canonicalEl.textContent = '';
    } finally {
      resolveInFlight = null;
    }
  }


  async function prewarmQuoteDependencies(symbol) {
    const expectedContext = {
      account: String(state.account || '').toLowerCase(),
      asset: String(state.asset || '').toLowerCase(),
      symbol: String(symbol || '').toUpperCase(),
    };
    try {
      if (!symbol) return;
      state.quotePrewarmPromise = post('/api/calculator/prewarm', { asset: state.asset, account: state.account, symbol: expectedContext.symbol });
      const status = await state.quotePrewarmPromise;
      const currentContext = {
        account: String(state.account || '').toLowerCase(),
        asset: String(state.asset || '').toLowerCase(),
        symbol: String(state.resolvedSymbol || '').toUpperCase(),
      };
      if (currentContext.account !== expectedContext.account || currentContext.asset !== expectedContext.asset || currentContext.symbol !== expectedContext.symbol) return;
      setPrewarmStatus(status);
    } catch (_e) {}
    finally { state.quotePrewarmPromise = null; }
  }
  async function prewarmAccountDependencies() {
    const expectedContext = {
      account: String(state.account || '').toLowerCase(),
      asset: String(state.asset || '').toLowerCase(),
      symbol: '',
    };
    try {
      if (state.asset !== 'crypto') return;
      state.quotePrewarmPromise = post('/api/calculator/prewarm-account', { asset: state.asset, account: state.account });
      const status = await state.quotePrewarmPromise;
      const currentContext = {
        account: String(state.account || '').toLowerCase(),
        asset: String(state.asset || '').toLowerCase(),
        symbol: '',
      };
      if (currentContext.account !== expectedContext.account || currentContext.asset !== expectedContext.asset) return;
      setPrewarmStatus(status);
    } catch (_e) {}
    finally { state.quotePrewarmPromise = null; }
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
  webhookCopyUrlBtn.addEventListener('click', async () => {
    const text = webhookUrlEl.textContent || '';
    if (!text.trim()) {
      errorEl.textContent = 'No webhook URL to copy.';
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      okEl.textContent = 'Webhook URL copied.';
    } catch (err) {
      errorEl.textContent = `Copy failed: ${err?.message || err}`;
    }
  });

  $('calc-symbol').addEventListener('input', () => {
    invalidateQuote({ clearResults: false, status: state.hasCalculatedOnce ? 'stale' : 'idle', reason: 'Quote changed. Recalculate before submitting.' });
    debounceSymbolResolve();
  });

  ['calc-limit', 'calc-sl-ticks', 'calc-rr', 'calc-risk'].forEach((id) => {
    const el = $(id);
    ['input', 'change'].forEach((evt) => el.addEventListener(evt, () => invalidateQuote({ clearResults: false })));
  });

  $('calc-quote').addEventListener('click', async () => {
    clearMessages();
    toggleWebhookPanel(false);
    if (state.quoteController) state.quoteController.abort();
    if (journalController) journalController.abort();
    state.quoteController = new AbortController();
    const quoteSoftTimeoutMs = 5000;
    const quoteTimeoutMs = 15000;
    state.quoteRequestSeq += 1;
    const seq = state.quoteRequestSeq;
    const softTimeoutId = safeTimeout(() => Promise.resolve().then(() => {
      if (seq === state.quoteRequestSeq) setQuoteStatus('Still calculating… waiting for upstream quote dependencies.');
    }), quoteSoftTimeoutMs);
    const timeoutId = safeTimeout(() => Promise.resolve().then(() => state.quoteController && state.quoteController.abort()), quoteTimeoutMs);
    state.hasCalculatedOnce = true;
    const quoteBtn = $('calc-quote');
    const defaultLabel = quoteBtn.dataset.defaultLabel || quoteBtn.textContent || 'Calculate';
    quoteBtn.dataset.defaultLabel = defaultLabel;
    quoteBtn.disabled = true;
    quoteBtn.textContent = 'Calculating…';
    invalidateQuote({ status: 'calculating', reason: 'Calculating position…' });
    try {
      if (state.webhook_mode === 'yes' && state.webhookCapability && state.webhookCapability.available === false) {
        const detail = {
          message: state.webhookCapability.unavailable_message || webhookUnavailableMessage(),
          debug: {
            webhook: 'yes',
            webhook_origin_host: state.webhookCapability.webhook_origin_host,
            webhook_endpoint_url: state.webhookCapability.webhook_endpoint_url,
            public_webhook_base_url: state.webhookCapability.public_webhook_base_url,
            app_profile: state.webhookCapability.app_profile,
            app_instance_id: state.webhookCapability.app_instance_id,
            resolution: state.webhookCapability.resolution,
          },
        };
        invalidateQuote({ status: 'error', reason: 'Quote failed. Recalculate before submitting.' });
        errorEl.textContent = detail.message;
        renderErrorDebug(detail);
        toggleWebhookPanel(false);
        return;
      }
      if (isPepperstoneFx() && state.order_type !== 'limit') {
        invalidateQuote({ status: 'error', reason: 'Pepperstone .set export is limit-only.' });
        errorEl.textContent = 'Pepperstone .set generation is available for limit orders only. Market orders are blocked because Trader.mq5 has no safe one-shot manual market strategy.';
        return;
      }
      if (isPepperstoneFx() && state.webhook_mode === 'yes') {
        state.webhook_mode = 'no';
        syncToggleState('webhook-toggle', 'webhook_mode');
        toggleWebhookPanel(false);
        cleanupPendingWebhook();
      }
      if (state.asset === 'crypto' && !state.resolvedSymbol && resolveInFlight) {
        try { await Promise.race([resolveInFlight, new Promise((r) => setTimeout(r, 800))]); } catch (_e) {}
      }
      if (state.asset === 'crypto' && state.quotePrewarmPromise) setQuoteStatus('Preparing quote data…');
      const payload = {
        ...state,
        submitted_symbol: $('calc-symbol').value,
        symbol: state.resolvedSymbol || $('calc-symbol').value,
        broker: state.asset === 'fx' ? state.broker : undefined,
        entry_price: $('calc-limit').value,
        stop_loss_ticks: $('calc-sl-ticks').value,
        risk_reward: $('calc-rr').value,
        risk_value: $('calc-risk').value,
        webhook: state.webhook_mode,
        test: state.test_mode,
        setup: state.setup,
        pattern: state.pattern,
        ema: state.ema,
        aths_atls: state.aths_atls,
        round_number: state.round_number,
        pending_webhook_id: state.webhook_mode === 'yes' ? (state.pendingWebhookId || undefined) : undefined,
        previous_pending_webhook_id: state.webhook_mode === 'yes' ? undefined : (state.pendingWebhookId || undefined),
      };
      delete payload.quotePrewarmStatus;
      delete payload.quotePrewarmPromise;
      renderRequestSummary(payload);
      const quote = await post('/api/calculator/quote', payload, { signal: state.quoteController.signal });
      if (seq !== state.quoteRequestSeq) return;
      state.quote = quote;
      renderQuote(quote);
      state.quoteStatus = 'ready';
      setQuoteStatus('Quote ready.');
      if (isPepperstoneFx()) {
        state.pepperstoneSetPayload = buildPepperstoneSetPayload(quote);
        setPepperstoneSetButton({ visible: true, enabled: true, reason: 'Download Pepperstone MT5 Expert Set file.' });
        setSubmitState({ visible: false, enabled: false, reason: 'Pepperstone uses MT5 .set export.', stateName: 'ready' });
      } else {
        clearPepperstoneSetDownload();
        setSubmitState({ visible: state.webhook_mode !== 'yes', enabled: true, reason: '', stateName: 'ready' });
      }
      if (state.webhook_mode === 'yes' && quote.webhook_payload_json) {
        state.pendingWebhookId = quote.pending_webhook_id || state.pendingWebhookId;
        state.pendingWebhookDeleteUrl = quote.pending_webhook_delete_url || '';
        webhookUrlEl.textContent = quote.webhook_endpoint_url || quote.webhook_endpoint || '';
        webhookJsonEl.textContent = quote.webhook_payload_json;
        webhookPanel.dataset.pendingId = quote.pending_webhook_id || '';
        webhookPanel.dataset.endpoint = quote.webhook_endpoint_url || quote.webhook_endpoint || '';
        toggleWebhookPanel(true);
      } else {
        state.pendingWebhookId = '';
        state.pendingWebhookDeleteUrl = '';
      }
      if (state.webhook_mode === 'yes') {
        setSubmitState({ visible: false, enabled: false, reason: 'Webhook mode enabled.', stateName: 'ready' });
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        invalidateQuote({ status: 'error', reason: 'Quote failed. Recalculate before submitting.' });
        toggleWebhookPanel(false);
        state.pendingWebhookId = '';
        state.pendingWebhookDeleteUrl = '';
        setQuoteStatus('Quote failed. Recalculate before submitting.');
        errorEl.textContent = 'Quote timed out after 15s. Upstream dependencies did not complete within the hard cap.';
        return;
      }
      invalidateQuote({ status: 'error', reason: 'Quote failed. Recalculate before submitting.' });
      toggleWebhookPanel(false);
      state.pendingWebhookId = '';
      state.pendingWebhookDeleteUrl = '';
      const detailObj = e.detail || null;
      const actionable = extractExpiredBybitKeyMessage(detailObj);
      const backendMessage = detailObj?.message || detailObj?.code || '';
      setQuoteStatus(backendMessage ? `Quote failed: ${backendMessage}` : 'Quote failed. Recalculate before submitting.');
      errorEl.textContent = actionable || String(e.message || e);
      renderErrorDebug(detailObj);
    } finally {
      clearTimeout(softTimeoutId);
      clearTimeout(timeoutId);
      if (seq === state.quoteRequestSeq) {
        quoteBtn.disabled = false;
        quoteBtn.textContent = defaultLabel;
      }
    }
  });

  if (pepperstoneSetBtn) pepperstoneSetBtn.addEventListener('click', async () => {
    clearMessages();
    try {
      if (!state.pepperstoneSetPayload) throw new Error('Calculate a Pepperstone FX limit order first.');
      pepperstoneSetBtn.disabled = true;
      const res = await fetch('/api/calculator/pepperstone-set', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(state.pepperstoneSetPayload),
      });
      const bodyText = await res.text();
      let bodyJson = null;
      try { bodyJson = bodyText && /^[\[{]/.test(bodyText.trim()) ? JSON.parse(bodyText) : null; } catch (_err) { bodyJson = null; }
      if (!res.ok) throw buildFetchError('/api/calculator/pepperstone-set', 'POST', res.status, res.statusText, bodyText, bodyJson);
      const filename = filenameFromDisposition(res.headers.get('content-disposition'), 'Pepperstone_Trader.set');
      const downloaded = saveTextDownload(bodyText, filename);
      okEl.textContent = downloaded ? `Pepperstone .set downloaded: ${filename}` : `Pepperstone .set ready: ${filename}`;
    } catch (err) {
      errorEl.textContent = String(err?.message || err);
      renderErrorDebug(err.detail || (err.debug ? { debug: err.debug } : null));
    } finally {
      pepperstoneSetBtn.disabled = false;
    }
  });

  $('calc-submit').addEventListener('click', async () => {
    if (submitInFlight) return;
    clearMessages();
    let startedSubmit = false;
    try {
      if (submitBtn.disabled) throw new Error('Calculate a fresh quote before submitting.');
      if (state.webhook_mode === 'yes') throw new Error('Webhook mode is enabled. Use the generated TradingView JSON instead of Submit Order.');
      if (state.quoteStatus !== 'ready' || !state.quote) throw new Error('Calculate first.');
      if (!state.timeframe) throw new Error('Timeframe is required.');
      submitInFlight = true;
      startedSubmit = true;
      markSubmitClicked();
      submitBtn.disabled = true;
      if (!state.resolvedSymbol && resolveInFlight) {
        try { await Promise.race([resolveInFlight, new Promise((r) => setTimeout(r, 800))]); } catch (_e) {}
      }
      const payload = {
        asset: state.asset,
        broker: state.asset === 'fx' ? state.broker : undefined,
        account: state.account,
        symbol: state.quote.symbol,
        action: state.side,
        order_type: state.order_type,
        entry_price: state.quote.entry_price,
        stop_loss_price: state.quote.stop_price,
        take_profit_price: state.quote.target_price,
        quantity: state.quote.quantity,
        timeframe: state.timeframe,
        test: state.test_mode,
        setup: state.setup,
        pattern: state.pattern,
        ema: state.ema,
        aths_atls: state.aths_atls,
        round_number: state.round_number,
        planned_entry_price: state.quote.entry_price,
        planned_stop_price: state.quote.stop_price,
        planned_target_price: state.quote.target_price,
        level_anchor_mode: state.order_type === 'limit' ? 'planned_entry' : 'actual_fill',
        pending_webhook_id: state.quote.pending_webhook_id || '',
        calculation_context_id: state.quote.calculation_context_id || '',
        quote_created_at_ms: state.quote.quote_created_at_ms,
        risk_mode: state.risk_mode,
        risk_value: $('calc-risk').value,
        stop_loss_ticks: $('calc-sl-ticks').value,
        target_mode: state.quote?.target_mode || 'rr',
        risk_reward: $('calc-rr').value,
      };
      if (state.take_profit_ticks !== undefined && state.take_profit_ticks !== null && state.take_profit_ticks !== '') payload.take_profit_ticks = state.take_profit_ticks;
      renderRequestSummary(payload);
      const submitResp = await post('/api/calculator/submit', payload);
      if (!submitResp || submitResp.ok !== true) {
        throw buildFetchError('/api/calculator/submit', 'POST', 400, 'Bad Request', '', submitResp || {});
      }
      const adj = submitResp?.submit_level_adjustments || {};
      if (adj && adj.submit_take_profit_auto_adjusted) okEl.textContent = `Order submitted. TP adjusted from ${adj.original_take_profit_price} to ${adj.adjusted_take_profit_price} because LastPrice moved since quote.`;
      else okEl.textContent = 'Order submitted successfully.';
      const submitWarnings = [];
      if (submitResp?.journal_context_saved === false) submitWarnings.push('Order may have been submitted, but journal enrichment context was not safely saved. Sync Journal may not be able to fill SL/target/timeframe/test/risk fields.');
      if (submitResp?.context_save_error) submitWarnings.push(String(submitResp.context_save_error));
      if (Array.isArray(submitResp?.warnings)) submitWarnings.push(...submitResp.warnings.map((w)=>String(w||'')).filter(Boolean));
      if (submitWarnings.length) {
        okEl.textContent = 'Order submitted, but journal context warning requires attention.';
        errorEl.textContent = submitWarnings.join(' ');
      }
      submitBtn.dataset.submitVisualState = 'success';
      submitBtn.textContent = SUBMIT_LABEL;
      clearSubmitStateResetTimer();
      submitStateResetTimer = setTimeout(() => {
        if (state.quoteStatus === 'ready' && state.quote && !submitInFlight && !isPepperstoneFx()) {
          clearSubmitClicked();
          setSubmitState({ visible: true, enabled: true, reason: '', stateName: 'ready' });
        }
      }, 1200);
    } catch (e) {
      okEl.textContent = '';
      errorEl.textContent = String(e.message || e);
      renderErrorDebug(e.detail || (e.debug ? { debug: e.debug } : null));
      clearSubmitClicked();
      if (state.quoteStatus === 'ready' && state.quote && state.webhook_mode !== 'yes' && !isPepperstoneFx() && state.quote.quote_valid_for_submit !== false) {
        setSubmitState({ visible: true, enabled: true, reason: '', stateName: 'ready' });
      }
    } finally {
      if (startedSubmit) submitInFlight = false;
    }
  });

  setToggle('account-toggle', 'account', () => { refreshPrewarmSchedule(); resolveSymbolAndLoad(); });
  setToggle('asset-toggle', 'asset', () => { updateRiskUiForAsset(); updateBrokerUiForAsset(); refreshPrewarmSchedule(); resolveSymbolAndLoad(); });
  setToggle('broker-toggle', 'broker', () => { updateBrokerUiForAsset(); resolveSymbolAndLoad(); });
  setToggle('side-toggle', 'side');
  setToggle('order-toggle', 'order_type');
  setToggle('risk-toggle', 'risk_mode');
  setToggle('webhook-toggle', 'webhook_mode');
  setToggle('test-toggle', 'test_mode');
  setTimeframeButtons();
  setSetupButtons();
  setPatternButtons();
  setEmaButtons();
  setAthsAtlsButtons();
  setRoundNumberButtons();
  updateRiskUiForAsset();
  updateBrokerUiForAsset();
  syncAllToggleStates();
  const webhookYesBtn = $('webhook-toggle').querySelectorAll('button')[1];
  if (webhookYesBtn) {
    webhookYesBtn.disabled = true;
    if (typeof webhookYesBtn.setAttribute === 'function') webhookYesBtn.setAttribute('aria-disabled', 'true');
    else webhookYesBtn.ariaDisabled = 'true';
    webhookYesBtn.title = 'Checking webhook availability…';
  }
  loadBootstrapCapability().finally(syncAllToggleStates);
  refreshPrewarmSchedule();
  setSubmitState({ visible: false, enabled: false, reason: '', stateName: 'idle' });
  clearPepperstoneSetDownload();
  toggleWebhookPanel(false);
})();
