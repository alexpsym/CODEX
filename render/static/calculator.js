(function () {
  const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1mo'];
  const state = {
    account: 'live',
    asset: 'crypto',
    side: 'buy',
    order_type: 'market',
    risk_mode: 'percent',
    timeframe: '15m',
    quote: null,
    resolvedSymbol: '',
  };

  const $ = (id) => document.getElementById(id);
  const errorEl = $('calc-error');
  const okEl = $('calc-success');
  const resultEl = $('calc-results');
  const canonicalEl = $('calc-canonical-symbol');
  const journalEl = $('calc-journal-summary');
  const riskToggleWrap = $('risk-toggle-wrap');

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

  function renderJournalStats(payload) {
    const s = payload.stats || {};
    const rows = [
      ['Canonical symbol', payload.canonical_symbol],
      ['Total trades', s.total_trades], ['Wins', s.wins], ['Losses', s.losses], ['Break-even', s.break_even],
      ['Long trades', s.long_trades], ['Short trades', s.short_trades],
      ['Long wins / losses', `${s.long_wins ?? '-'} / ${s.long_losses ?? '-'}`],
      ['Short wins / losses', `${s.short_wins ?? '-'} / ${s.short_losses ?? '-'}`],
      ['Win rate', s.win_rate],
      ['Avg stop distance', s.avg_stop_distance],
      ['Avg target distance', s.avg_target_distance],
      ['Avg trade duration', s.avg_trade_duration],
      ['Last trade', s.last_trade_timestamp || '-'],
    ];
    const summaryCards = rows.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${v ?? '-'}</div></div>`).join('');
    const tradeRows = Array.isArray(payload.trades) ? payload.trades : [];
    const details = tradeRows.length
      ? `<details class="card" style="grid-column:1/-1"><summary>Journal trade details (${tradeRows.length})</summary>${tradeRows.map((t, idx) => `
          <div class="card" style="margin-top:8px">
            <div><strong>#${idx + 1}</strong> ${t.symbol ?? '-'}</div>
            <div class="muted">Side: ${t.side ?? '-'} • Open: ${t.open_time ?? '-'} • Close: ${t.close_time ?? '-'}</div>
            <div class="muted">Entry: ${t.entry_price ?? '-'} • Exit: ${t.exit_price ?? '-'} • PnL: ${t.net_profit ?? t.realized_pnl ?? '-'}</div>
            <div class="muted">SL: ${t.stop_loss ?? '-'} • TP: ${t.take_profit ?? '-'} • Timeframe: ${t.timeframe ?? '-'}</div>
          </div>`).join('')}</details>`
      : '<div class="card" style="grid-column:1/-1"><div class="muted">No detailed journal rows found.</div></div>';
    journalEl.dataset.state = 'ready';
    journalEl.innerHTML = summaryCards + details;
  }

  function renderQuote(q) {
    const rows = [
      ['Resolved broker', q.broker], ['Resolved symbol', q.symbol], ['Tick size', q.tick_size],
      ['Entry price', q.entry_price], ['Stop price', q.stop_price], ['Target price', q.target_price],
      ['Qty / units', q.quantity], ['Notional', q.notional], ['Estimated fees / spread', q.estimated_fees_or_spread_aud],
      ['Estimated total loss in AUD', q.estimated_total_loss_aud], ['Estimated reward in AUD', q.estimated_reward_aud], ['R:R', q.rr],
    ];
    resultEl.innerHTML = rows.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div>${v ?? '-'}</div></div>`).join('');
  }

  async function request(url, opts = {}) {
    const res = await fetch(url, opts);
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || payload.error || 'Request failed');
    return payload;
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

  function setToggle(id, key, onChange) {
    const root = $(id);
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        root.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state[key] = btn.dataset.v;
        state.quote = null;
        if (key === 'order_type') $('limit-wrap').style.display = state.order_type === 'limit' ? '' : 'none';
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

  $('calc-symbol').addEventListener('input', debounceSymbolResolve);

  $('calc-quote').addEventListener('click', async () => {
    clearMessages();
    try {
      const payload = {
        ...state,
        symbol: $('calc-symbol').value,
        entry_price: $('calc-limit').value,
        stop_loss_ticks: $('calc-sl-ticks').value,
        take_profit_ticks: $('calc-tp-ticks').value,
        risk_value: $('calc-risk').value,
      };
      const quote = await post('/api/calculator/quote', payload);
      state.quote = quote;
      renderQuote(quote);
    } catch (e) {
      resultEl.innerHTML = '';
      errorEl.textContent = String(e.message || e);
    }
  });

  $('calc-submit').addEventListener('click', async () => {
    clearMessages();
    try {
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
  setTimeframeButtons();
  updateRiskUiForAsset();
  setJournalState('idle', 'Type a symbol to load journal summary.');
})();
