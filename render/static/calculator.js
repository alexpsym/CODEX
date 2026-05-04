(function () {
  const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1mo'];
  const state = {
    account: 'live',
    asset: 'crypto',
    side: 'buy',
    order_type: 'market',
    risk_mode: 'percent',
    fx_risk_mode: 'percent',
    timeframe: '15m',
    webhook_mode: 'no',
    test_mode: 'no',
    quote: null,
    resolvedSymbol: '',
    pendingWebhookId: '',
    pendingWebhookDeleteUrl: '',
    quoteStatus: 'idle',
    hasCalculatedOnce: false,
    quoteRequestSeq: 0,
    quoteController: null,
    webhookCapability: null,
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
  const webhookPanel = $('calc-webhook-panel');
  const webhookUrlEl = $('calc-webhook-url');
  const webhookJsonEl = $('calc-webhook-json');
  const webhookCopyBtn = $('calc-webhook-copy');
  const webhookCopyUrlBtn = $('calc-webhook-copy-url');
  const submitBtn = $('calc-submit');
  const quoteStatusEl = $('calc-quote-status');
  const webhookStatusEl = $('calc-webhook-status');

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
    journalEl.dataset.state = kind;
    journalEl.innerHTML = `<div class="muted">${text}</div>`;
  }

  function setSpecsState(kind, text) {
    specsEl.dataset.state = kind;
    const msg = String(text || '').trim();
    specsEl.innerHTML = msg ? `<div class="muted">${msg}</div>` : '';
  }

  function setQuoteStatus(text) {
    if (quoteStatusEl) quoteStatusEl.textContent = text || '';
  }
  function webhookUnavailableMessage() {
    return 'Set RENDER_CALCULATOR_BASE_URL to the Render service URL to generate Render-owned TradingView webhook alerts from the local calculator. Webhook=No calculation remains available.';
  }

  function setSubmitState({ visible, enabled, reason = '', stateName = '' }) {
    submitBtn.style.display = visible ? '' : 'none';
    submitBtn.disabled = !(enabled && state.quote && state.quoteStatus === 'ready' && state.webhook_mode !== 'yes');
    submitBtn.title = reason || '';
    if (stateName) submitBtn.dataset.state = stateName;
  }

  function invalidateQuote({ clearResults = true, status = 'stale', reason = '' } = {}) {
    state.quote = null;
    state.quoteStatus = status;
    const visible = status === 'idle' ? false : state.hasCalculatedOnce;
    setSubmitState({ visible, enabled: false, reason, stateName: status });
    if (status === 'calculating') setQuoteStatus('Calculating position…');
    else if (status === 'stale') setQuoteStatus('Quote changed. Recalculate before submitting.');
    else if (status === 'error') setQuoteStatus('Quote failed. Recalculate before submitting.');
    else if (status === 'idle') setQuoteStatus('');
    if (clearResults) resultEl.innerHTML = status === 'calculating' ? '<div class="card"><div class="muted">Calculating position…</div></div>' : '';
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
      const err = new Error(detail.message || detail.error || `${method} ${url} failed: ${status}`);
      err.detail = detail;
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
    const formatDebugValue = (v) => (v && typeof v === "object" ? `<pre>${JSON.stringify(v, null, 2)}</pre>` : (v ?? "-"));
    const rows = Object.entries(debug)
      .map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${formatDebugValue(v)}</div></div>`)
      .join('');
    errorDebugEl.innerHTML = rows;
  }

  function renderRequestSummary(payload) {
    requestSummaryEl.style.whiteSpace = 'pre-wrap';
    requestSummaryEl.textContent = [
      `Submitted payload:`,
      `asset=${payload.asset}`,
      `account=${payload.account}`,
      `symbol=${payload.symbol}`,
      `webhook=${payload.webhook}`,
      `test=${payload.test}`,
      `timeframe=${state.timeframe || payload.timeframe || ''}`,
      `risk_mode=${payload.risk_mode}`,
      `risk_value=${payload.risk_value}`,
      `stop_loss_ticks=${payload.stop_loss_ticks}`,
      `order_type=${payload.order_type}`,
      `side=${payload.side}`,
      `pending_webhook_id=${payload.pending_webhook_id || ''}`,
      `previous_pending_webhook_id=${payload.previous_pending_webhook_id || ''}`,
    ].join('\n');
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
    syncToggleState('side-toggle', 'side');
    syncToggleState('order-toggle', 'order_type');
    syncToggleState('risk-toggle', 'risk_mode');
    syncToggleState('webhook-toggle', 'webhook_mode');
    syncToggleState('test-toggle', 'test_mode');
  }

  function setToggle(id, key, onChange) {
    const root = $(id);
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
      const diag = `Build: ${bootstrap?.calculator_js_sha256_12 || 'unknown'} | Profile: ${bootstrap?.app_profile || 'unknown'} | Render target: ${bootstrap?.render_calculator_base_url_configured ? 'configured' : 'missing'}`;
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
        if (webhookStatusEl) webhookStatusEl.textContent = `${diag} | ${warn}`;
      } else {
        yesBtn.disabled = false;
        if (typeof yesBtn.removeAttribute === 'function') yesBtn.removeAttribute('aria-disabled');
        else yesBtn.ariaDisabled = '';
        yesBtn.title = '';
        if (webhookStatusEl) webhookStatusEl.textContent = diag;
      }
    } catch (_err) {
      state.webhookCapability = null;
      if (webhookStatusEl) webhookStatusEl.textContent = 'Webhook availability could not be verified; server will validate on calculate.';
    }
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
    invalidateQuote();
    canonicalEl.textContent = '';
    if (!symbol) {
      setJournalState('idle', 'Type a symbol to load journal summary.');
      setSpecsState('idle', 'Enter a symbol to load instrument specs.');
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
    if (resolveController) resolveController.abort();
    if (journalController) journalController.abort();
    state.quoteController = new AbortController();
    const quoteTimeoutMs = 25000;
    const timeoutId = setTimeout(() => state.quoteController && state.quoteController.abort(), quoteTimeoutMs);
    state.quoteRequestSeq += 1;
    const seq = state.quoteRequestSeq;
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
      const payload = {
        ...state,
        symbol: $('calc-symbol').value,
        entry_price: $('calc-limit').value,
        stop_loss_ticks: $('calc-sl-ticks').value,
        risk_reward: $('calc-rr').value,
        risk_value: $('calc-risk').value,
        webhook: state.webhook_mode,
        test: state.test_mode,
        pending_webhook_id: state.webhook_mode === 'yes' ? (state.pendingWebhookId || undefined) : undefined,
        previous_pending_webhook_id: state.webhook_mode === 'yes' ? undefined : (state.pendingWebhookId || undefined),
      };
      renderRequestSummary(payload);
      const quote = await post('/api/calculator/quote', payload, { signal: state.quoteController.signal });
      if (seq !== state.quoteRequestSeq) return;
      state.quote = quote;
      renderQuote(quote);
      state.quoteStatus = 'ready';
      setQuoteStatus('Quote ready.');
      setSubmitState({ visible: state.webhook_mode !== 'yes', enabled: true, reason: '', stateName: 'ready' });
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
        errorEl.textContent = 'Quote timed out after 25s. Slow dependency: unknown unless server returned timings. The browser aborted before the server returned diagnostics.';
        return;
      }
      invalidateQuote({ status: 'error', reason: 'Quote failed. Recalculate before submitting.' });
      toggleWebhookPanel(false);
      state.pendingWebhookId = '';
      state.pendingWebhookDeleteUrl = '';
      errorEl.textContent = String(e.message || e);
      renderErrorDebug(e.detail || null);
    } finally {
      clearTimeout(timeoutId);
      if (seq === state.quoteRequestSeq) {
        quoteBtn.disabled = false;
        quoteBtn.textContent = defaultLabel;
      }
    }
  });

  $('calc-submit').addEventListener('click', async () => {
    clearMessages();
    try {
      if (submitBtn.disabled) throw new Error('Calculate a fresh quote before submitting.');
      if (state.webhook_mode === 'yes') throw new Error('Webhook mode is enabled. Use the generated TradingView JSON instead of Submit Order.');
      if (state.quoteStatus !== 'ready' || !state.quote) throw new Error('Calculate first.');
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
        test: state.test_mode,
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
  setToggle('test-toggle', 'test_mode');
  setTimeframeButtons();
  updateRiskUiForAsset();
  syncAllToggleStates();
  const webhookYesBtn = $('webhook-toggle').querySelectorAll('button')[1];
  if (webhookYesBtn) {
    webhookYesBtn.disabled = true;
    if (typeof webhookYesBtn.setAttribute === 'function') webhookYesBtn.setAttribute('aria-disabled', 'true');
    else webhookYesBtn.ariaDisabled = 'true';
    webhookYesBtn.title = 'Checking webhook availability…';
  }
  loadBootstrapCapability().finally(syncAllToggleStates);
  setSubmitState({ visible: false, enabled: false, reason: '', stateName: 'idle' });
  toggleWebhookPanel(false);
  setJournalState('idle', 'Type a symbol to load journal summary.');
  setSpecsState('idle', 'Enter a symbol to load instrument specs.');
})();
