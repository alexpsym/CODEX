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
  const entryInput = document.getElementById('entry_price');
  if (!orderType || !entryField) {
    return;
  }
  const isMarket = orderType.value === 'market';
  entryField.style.display = isMarket ? 'none' : 'block';
  if (entryInput) {
    if (isMarket) {
      entryInput.removeAttribute('required');
    } else {
      entryInput.setAttribute('required', 'required');
    }
  }
}

function toggleRiskMode() {
  const modeInput = document.getElementById('risk_mode');
  const percentRow = document.getElementById('risk_percent_row');
  const amountRow = document.getElementById('risk_amount_row');
  const percentInput = document.getElementById('risk_pct');
  const amountInput = document.getElementById('risk_aud');
  if (!modeInput || !percentRow || !amountRow) {
    return;
  }
  const isPercent = modeInput.value === 'percent';
  percentRow.style.display = isPercent ? 'block' : 'none';
  amountRow.style.display = isPercent ? 'none' : 'block';
  if (percentInput) {
    if (isPercent) {
      percentInput.setAttribute('required', 'required');
    } else {
      percentInput.removeAttribute('required');
    }
  }
  if (amountInput) {
    if (isPercent) {
      amountInput.removeAttribute('required');
    } else {
      amountInput.setAttribute('required', 'required');
    }
  }
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
  const raw = (meta.dataset.orderType || 'market').toLowerCase();
  const isLimit = raw === 'limit';
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
  ['account_mode', 'side', 'order_type', 'risk_mode', 'track_pending'].forEach(bindButtonGroup);
  const orderType = document.getElementById('order_type');
  if (orderType) {
    orderType.addEventListener('change', toggleEntry);
    toggleEntry();
  }
  const riskMode = document.getElementById('risk_mode');
  if (riskMode) {
    riskMode.addEventListener('change', toggleRiskMode);
    toggleRiskMode();
  }
  updateExecuteButtons();
});
