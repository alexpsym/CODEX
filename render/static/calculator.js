(function () {
  const state = { account: 'live', asset: 'crypto', side: 'buy', order_type: 'market', risk_mode: 'fixed_aud', quote: null };
  const $ = (id) => document.getElementById(id);
  const errorEl = $('calc-error');
  const okEl = $('calc-success');
  const resultEl = $('calc-results');

  function setToggle(id, key) {
    const root = $(id);
    root.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        root.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state[key] = btn.dataset.v;
        if (key === 'order_type') $('limit-wrap').style.display = state.order_type === 'limit' ? '' : 'none';
      });
    });
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

  async function post(url, body) {
    const res = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || payload.error || 'Request failed');
    return payload;
  }

  $('calc-quote').addEventListener('click', async () => {
    errorEl.textContent = ''; okEl.textContent = '';
    try {
      const payload = {
        // NOTE: spread operator intentionally uses `...state` (not `.state`).
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
    errorEl.textContent = ''; okEl.textContent = '';
    try {
      if (!state.quote) throw new Error('Calculate first.');
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
        timeframe: $('calc-timeframe').value,
      };
      await post('/api/calculator/submit', payload);
      okEl.textContent = 'Order submitted successfully.';
    } catch (e) {
      errorEl.textContent = String(e.message || e);
    }
  });

  setToggle('account-toggle', 'account');
  setToggle('asset-toggle', 'asset');
  setToggle('side-toggle', 'side');
  setToggle('order-toggle', 'order_type');
  setToggle('risk-toggle', 'risk_mode');
})();
