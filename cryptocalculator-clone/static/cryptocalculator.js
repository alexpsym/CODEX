const appRoot = document.body ? (document.body.dataset.appRoot || '') : '';

function buildAppUrl(path) {
  if (!path) {
    return appRoot || '';
  }
  if (appRoot) {
    return appRoot + (path.startsWith('/') ? '' : '/') + path;
  }
  return path.startsWith('/') ? path : '/' + path;
}

function getJsonData(datasetKey) {
  const holder = document.getElementById('js_data');
  if (!holder) {
    return {};
  }
  const raw = holder.dataset[datasetKey];
  if (!raw) {
    return {};
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    console.warn('Failed to parse JSON data for ' + datasetKey, err);
    return {};
  }
}

const priceModeNotes = getJsonData('priceModeNotes');
const optionsMinQtyMap = getJsonData('optionsMinQtyMap');
let optionsMinQtyTimer = null;

function copyText(text, statusId) {
  const status = document.getElementById(statusId);
  const done = () => {
    if (status) {
      status.innerText = 'Copied!';
      setTimeout(() => {
        status.innerText = '';
      }, 2000);
    }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => {});
  } else {
    const temp = document.createElement('textarea');
    temp.value = text;
    document.body.appendChild(temp);
    temp.select();
    try {
      document.execCommand('copy');
      done();
    } finally {
      document.body.removeChild(temp);
    }
  }
}

function copyFromElement(elementId, statusId) {
  const el = document.getElementById(elementId);
  if (!el) {
    return;
  }
  copyText(el.innerText, statusId);
}

function exportResult() {
  const payload = document.getElementById('export_json');
  if (!payload || !payload.innerText.trim()) {
    alert('Calculate a trade first to export the result.');
    return;
  }
  const blob = new Blob([payload.innerText], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  link.href = url;
  link.download = 'crypto-trade-' + timestamp + '.json';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function setButtonGroupValue(inputId, value, dispatchChange = true) {
  const input = document.getElementById(inputId);
  if (!input) {
    return;
  }
  input.value = value;
  const group = document.querySelector('[data-input="' + inputId + '"]');
  if (group) {
    group.querySelectorAll('button[data-value]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.value === value);
    });
  }
  if (dispatchChange) {
    input.dispatchEvent(new Event('change'));
  }
}

function bindButtonGroup(inputId) {
  const group = document.querySelector('[data-input="' + inputId + '"]');
  if (!group) {
    return;
  }
  group.querySelectorAll('button[data-value]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      setButtonGroupValue(inputId, btn.dataset.value);
    });
  });
  const input = document.getElementById(inputId);
  if (input) {
    setButtonGroupValue(inputId, input.value, false);
  }
}

function toggleEntry() {
  const orderType = document.getElementById('order_type');
  const entryField = document.getElementById('entry_price_row');
  const cancelField = document.getElementById('limit_cancel_row');
  if (!orderType || !entryField) {
    return;
  }
  const isLimit = orderType.value !== 'market';
  entryField.style.display = isLimit ? 'block' : 'none';
  if (cancelField) {
    cancelField.style.display = isLimit ? 'block' : 'none';
  }
}

function toggleOptionsEntry() {
  const orderType = document.getElementById('options_order_type');
  const entryField = document.getElementById('options_limit_price_row');
  if (!orderType || !entryField) {
    return;
  }
  entryField.style.display = orderType.value === 'limit' ? 'block' : 'none';
}

function updatePriceMode() {
  const priceSource = document.getElementById('price_source');
  const note = document.getElementById('price_mode_note');
  if (!priceSource || !note) {
    return;
  }
  note.innerText = priceModeNotes[priceSource.value] || '';
}

function renderOptionsMinQty(base) {
  const note = document.getElementById('options_min_qty_note');
  if (!note) {
    return;
  }
  const quote = 'USDT';
  const baseKey = base ? base.toUpperCase() : '';
  const minQty = optionsMinQtyMap[baseKey];
  const labelBase = baseKey ? baseKey + quote : quote;
  if (typeof minQty === 'number') {
    note.innerText = 'Min qty (' + labelBase + ' options): ' + minQty;
  } else {
    note.innerText = 'Min qty (' + labelBase + ' options): unavailable';
  }
}

function scheduleOptionsMinQty() {
  if (optionsMinQtyTimer) {
    clearTimeout(optionsMinQtyTimer);
  }
  optionsMinQtyTimer = setTimeout(updateOptionsMinQty, 200);
}

function updateOptionsMinQty() {
  const baseInput = document.getElementById('options_base');
  const base = baseInput ? baseInput.value : '';
  renderOptionsMinQty(base);
}

function updateTradeType() {
  const selector = document.getElementById('trade_type');
  const optionsSection = document.getElementById('options_section');
  const cryptoSection = document.getElementById('crypto_section');
  if (!selector || !optionsSection || !cryptoSection) {
    return;
  }
  const isOptions = selector.value === 'options';
  optionsSection.classList.toggle('hidden', !isOptions);
  cryptoSection.classList.toggle('hidden', isOptions);
  const cryptoRequired = ['symbol', 'stop_loss_ticks', 'risk_percent', 'rr_ratio'];
  cryptoRequired.forEach((fieldId) => {
    const el = document.getElementById(fieldId);
    if (!el) {
      return;
    }
    if (isOptions) {
      el.removeAttribute('required');
    } else {
      el.setAttribute('required', 'required');
    }
  });
}

async function enterNow() {
  const payloadEl = document.getElementById('alert_json');
  if (!payloadEl || !payloadEl.innerText.trim()) {
    alert('Calculate a trade first to enable immediate entry.');
    return;
  }
  const ok = confirm('Place a live market order immediately? This cannot be undone.');
  if (!ok) {
    return;
  }
  let payload = null;
  try {
    payload = JSON.parse(payloadEl.innerText);
  } catch (err) {
    alert('Could not parse the current payload. Recalculate and try again.');
    return;
  }
  const resultBox = document.getElementById('execute_result');
  if (resultBox) {
    resultBox.classList.remove('hidden');
    resultBox.innerText = 'Submitting market order...';
  }
  try {
    const resp = await fetch(buildAppUrl('/execute_now'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resultBox) {
      resultBox.innerText = JSON.stringify(data, null, 2);
    }
  } catch (err) {
    if (resultBox) {
      resultBox.innerText = 'Error: ' + err;
    }
  }
}

function updateExecuteButtons() {
  const meta = document.getElementById('calc_meta');
  if (!meta) {
    return;
  }

  const tradeType = (meta.dataset.tradeType || '').toLowerCase();
  const raw = tradeType === 'options'
    ? (meta.dataset.optionsOrderType || 'market')
    : (meta.dataset.orderType || 'market');

  const isLimit = (raw || 'market').toLowerCase() === 'limit';

  const marketBtn = document.getElementById('execute_market_btn');
  const limitBtn = document.getElementById('execute_limit_btn');
  const marketNote = document.getElementById('execute_market_note');
  const limitNote = document.getElementById('execute_limit_note');

  if (marketBtn) {
    marketBtn.style.display = isLimit ? 'none' : 'inline-block';
  }
  if (marketNote) {
    marketNote.style.display = isLimit ? 'none' : 'block';
  }
  if (limitBtn) {
    limitBtn.style.display = isLimit ? 'inline-block' : 'none';
  }
  if (limitNote) {
    limitNote.style.display = isLimit ? 'block' : 'none';
  }
}

function placeLimitOrder() {
  const payloadEl = document.getElementById('tv_payload');
  if (!payloadEl) {
    return;
  }

  const ok = confirm('Submit a LIVE LIMIT order now?');
  if (!ok) {
    return;
  }

  const resultBox = document.getElementById('execute_result');
  if (resultBox) {
    resultBox.textContent = 'Submitting limit order...\n';
    resultBox.classList.remove('hidden');
  }

  fetch(buildAppUrl('/execute_now'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payloadEl.textContent,
  })
    .then((r) => r.json().then((j) => ({ ok: r.ok, json: j })))
    .then(({ ok, json }) => {
      if (!resultBox) {
        return;
      }
      resultBox.textContent += (ok ? 'OK\n' : 'ERROR\n') + JSON.stringify(json, null, 2);
    })
    .catch((err) => {
      if (!resultBox) {
        return;
      }
      resultBox.textContent += 'ERROR\n' + String(err);
    });
}

document.addEventListener('DOMContentLoaded', () => {
  ['trade_type', 'account_mode', 'direction', 'order_type', 'options_order_type', 'options_type', 'options_side', 'price_source', 'execution_exchange', 'options_base', 'track_pending'].forEach(bindButtonGroup);
  const ot = document.getElementById('order_type');
  if (ot) {
    ot.addEventListener('change', toggleEntry);
    toggleEntry();
  }
  const oot = document.getElementById('options_order_type');
  if (oot) {
    oot.addEventListener('change', toggleOptionsEntry);
    toggleOptionsEntry();
  }
  const ps = document.getElementById('price_source');
  if (ps) {
    ps.addEventListener('change', updatePriceMode);
    updatePriceMode();
  }
  const tradeType = document.getElementById('trade_type');
  if (tradeType) {
    tradeType.addEventListener('change', updateTradeType);
  }
  updateTradeType();
  const optionsBaseInput = document.getElementById('options_base');
  if (optionsBaseInput) {
    optionsBaseInput.addEventListener('change', scheduleOptionsMinQty);
  }
  scheduleOptionsMinQty();
  try {
    updateExecuteButtons();
  } catch (err) {
    console.warn('Failed to update execute buttons', err);
  }
});
