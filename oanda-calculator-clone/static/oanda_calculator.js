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

const OANDA_SPECS_HIDE_KEYS = new Set([
  'query',
  '_units',
  'source',
  'financing.longRate',
  'financing.shortRate',
  'financing.financingDaysOfWeek',
]);
const OANDA_SPECS_PRIMARY_KEYS = [
  'resolved_symbol',
  'type',
  'displayName',
  'pipLocation',
  'displayPrecision',
  'tradeUnitsPrecision',
  'minimumTradeSize',
  'maximumOrderUnits',
  'marginRate',
  'scan.spread',
  'scan.bid',
  'scan.ask',
];

function isNumericLike(v) {
  if (v === null || v === undefined) return false;
  if (typeof v === 'number') return Number.isFinite(v);
  if (typeof v !== 'string') return false;
  const s = v.trim();
  return s !== '' && /^-?\d+(\.\d+)?$/.test(s);
}

function compactNumber(n, decimals = 2) {
  const num = Number(n);
  if (!Number.isFinite(num)) return String(n ?? '—');
  const abs = Math.abs(num);
  if (abs >= 1e12) return `${(num / 1e12).toFixed(decimals).replace(/\.00$/, '')}T`;
  if (abs >= 1e9) return `${(num / 1e9).toFixed(decimals).replace(/\.00$/, '')}B`;
  if (abs >= 1e6) return `${(num / 1e6).toFixed(decimals).replace(/\.00$/, '')}M`;
  if (abs >= 1e3) return `${(num / 1e3).toFixed(decimals).replace(/\.00$/, '')}K`;
  return num.toFixed(decimals).replace(/\.00$/, '');
}

function formatPercentFromFraction(v, decimals = 2) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? '—');
  return `${(n * 100).toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '')}%`;
}

function setInstrumentStatus(text) {
  const el = document.getElementById('instrument_status');
  if (el) el.textContent = text || '';
}

async function resolveInstrument(force = false) {
  const input = document.getElementById('instrument');
  if (!input) return null;
  const raw = String(input.value || '').trim();
  if (!raw) {
    setInstrumentStatus('');
    return null;
  }

  input.value = raw.toUpperCase();
  if (!force && raw.includes('_')) {
    setInstrumentStatus('');
    return input.value;
  }

  const modeEl = document.getElementById('account_mode');
  const mode = modeEl ? String(modeEl.value || 'live') : 'live';
  setInstrumentStatus('Resolving...');
  try {
    const url = buildAppUrl(`/api/resolve-instrument?instrument=${encodeURIComponent(raw)}&account_mode=${encodeURIComponent(mode)}`);
    const resp = await fetch(url, { cache: 'no-store' });
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      setInstrumentStatus((data && data.detail) ? String(data.detail) : 'No match');
      return null;
    }
    const resolved = String((data && data.resolved) ? data.resolved : '').toUpperCase();
    if (resolved) {
      const before = input.value;
      input.value = resolved;
      setInstrumentStatus(before === resolved ? '' : `→ ${resolved}`);
      return resolved;
    }
    setInstrumentStatus('No match');
    return null;
  } catch {
    setInstrumentStatus('Resolve failed');
    return null;
  }
}

function formatSpecValue(key, value, unit) {
  if (unit === 'fraction') return formatPercentFromFraction(value, 2);
  if (unit === 'usd_value' || unit === 'usd_value_24h') return `$${compactNumber(value)}`;
  if (unit === 'contracts' || unit === 'base_units_24h') return compactNumber(value);
  if (unit === 'price') return String(value ?? '—');
  if (typeof value === 'object' && value !== null) return JSON.stringify(value);
  return String(value ?? '—');
}

function setEmbeddedSpecsStatus(text) {
  const el = document.getElementById('embedded_specs_status');
  if (el) el.textContent = text || '';
}

function renderEmbeddedSpecs(specs) {
  const rows = document.getElementById('embedded_specs_rows');
  if (!rows) return;
  rows.innerHTML = '';

  const obj = (specs && typeof specs === 'object') ? specs : {};
  const units = (obj._units && typeof obj._units === 'object') ? obj._units : {};
  const keys = [];
  for (const k of OANDA_SPECS_PRIMARY_KEYS) {
    if (obj[k] !== null && obj[k] !== undefined) keys.push(k);
  }
  const scanKeys = Object.keys(obj).filter((k) => k.startsWith('scan.')).sort((a, b) => a.localeCompare(b));
  for (const k of scanKeys) {
    if (!keys.includes(k)) keys.push(k);
  }
  const rest = Object.keys(obj)
    .filter((k) => !k.startsWith('scan.') && !OANDA_SPECS_HIDE_KEYS.has(k) && k !== '_units')
    .sort((a, b) => a.localeCompare(b));
  for (const k of rest) {
    const v = obj[k];
    if (typeof v === 'object' && v !== null) continue;
    if (!keys.includes(k)) keys.push(k);
  }

  for (const key of keys) {
    if (OANDA_SPECS_HIDE_KEYS.has(key) || key === '_units') continue;
    const value = obj[key];
    const tr = document.createElement('tr');
    const tdKey = document.createElement('td');
    tdKey.textContent = key;
    const tdVal = document.createElement('td');
    tdVal.textContent = formatSpecValue(key, value, units[key]);
    tr.appendChild(tdKey);
    tr.appendChild(tdVal);
    rows.appendChild(tr);
  }
}

async function loadEmbeddedSpecs() {
  const panel = document.getElementById('embedded_specs');
  const input = document.getElementById('instrument');
  if (!panel || !input) return;

  const resolved = await resolveInstrument(true);
  const q = String(resolved || input.value || '').trim().toUpperCase();
  if (!q) return;

  panel.classList.remove('hidden');
  setEmbeddedSpecsStatus('Loading...');
  try {
    const resp = await fetch(`/api/instrument-specs?query=${encodeURIComponent(q)}&prefer=oanda&include_scanner=1`, { cache: 'no-store' });
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      setEmbeddedSpecsStatus((data && data.detail) ? String(data.detail) : 'Lookup failed');
      renderEmbeddedSpecs({});
      return;
    }
    setEmbeddedSpecsStatus('');
    renderEmbeddedSpecs(data);
  } catch {
    setEmbeddedSpecsStatus('Lookup failed');
    renderEmbeddedSpecs({});
  }
}

async function enterNow() {
  if (enterNow.inFlight) {
    return;
  }
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
  const enterBtn = document.getElementById('execute_market_btn');
  enterNow.inFlight = true;
  if (enterBtn) {
    enterBtn.disabled = true;
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
    const data = await resp.json().catch(async () => ({ raw: await resp.text() }));
    if (resultBox) {
      resultBox.innerText = `HTTP ${resp.status}\n${JSON.stringify(data, null, 2)}`;
    }
  } catch (err) {
    if (resultBox) {
      resultBox.innerText = 'Error: ' + err;
    }
  } finally {
    enterNow.inFlight = false;
    if (enterBtn) {
      enterBtn.disabled = false;
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

  const instrumentEl = document.getElementById('instrument');
  if (instrumentEl) {
    instrumentEl.addEventListener('blur', () => resolveInstrument(false));
  }

  const specsBtn = document.getElementById('instrument_specs_btn');
  if (specsBtn) {
    specsBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      loadEmbeddedSpecs();
    });
  }

  const form = document.querySelector('form');
  let submitInFlight = false;
  if (form) {
    form.addEventListener('submit', async (e) => {
      if (submitInFlight) return;
      e.preventDefault();
      submitInFlight = true;
      try {
        await resolveInstrument(true);
      } finally {
        submitInFlight = false;
      }
      form.submit();
    });
  }
});
