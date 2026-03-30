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
let audusdFetchInFlight = false;
let isUpdatingRiskControls = false;

async function ensureAudUsdRate(force = false) {
  const row = document.getElementById('audusd_rate_row');
  const exchange = document.getElementById('execution_exchange');
  const rateInput = document.getElementById('audusd_rate');
  const status = document.getElementById('audusd_fetch_status');
  const setStatus = (message) => {
    if (status) {
      status.textContent = message || '';
    }
  };
  if (!exchange || !rateInput) {
    if (row) {
      row.style.display = 'none';
    }
    setStatus('');
    return;
  }
  const isCoinspot = String(exchange.value || '').toLowerCase() === 'coinspot';
  if (row) {
    row.style.display = isCoinspot ? 'block' : 'none';
  }
  if (!isCoinspot) {
    setStatus('');
    return;
  }
  const current = parseFloat(rateInput.value || '0');
  if (!force && current > 0) {
    setStatus('');
    return;
  }
  if (audusdFetchInFlight) {
    return;
  }
  audusdFetchInFlight = true;
  try {
    const resp = await fetch(buildAppUrl('/api/oanda/audusd'), { cache: 'no-store' });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data && data.error ? data.error : 'AUD/USD fetch failed');
    }
    const rate = parseFloat(String(data.rate || '0'));
    if (rate > 0) {
      rateInput.value = rate.toFixed(6);
      setStatus('');
    }
  } catch (err) {
    console.warn('AUD/USD rate fetch failed', err);
    setStatus('AUD/USD auto-fetch failed. Enter rate manually.');
  } finally {
    audusdFetchInFlight = false;
  }
}

const SYMBOL_SUFFIXES = ['USDT', 'USDC', 'USD', 'AUD', 'BTC', 'ETH'];
const SPECS_PRIMARY_KEYS = [
  'resolved_symbol',
  'category',
  'lastPrice',
  'fundingRate',
  'nextFundingTime',
  'openInterest',
  'openInterestValue',
  'volume24h',
  'turnover24h',
  'avg7dTurnoverUsd',
  'launchTime',
];
const SPECS_HIDE_FIELDS = new Set([
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
  '_units',
]);
const SPECS_FIELD_LABELS = {
  resolved_symbol: 'Symbol',
  category: 'Market',
  lastPrice: 'Last price',
  fundingRate: 'Funding rate',
  nextFundingTime: 'Next funding',
  launchTime: 'Launch time',
  openInterest: 'Open interest',
  openInterestValue: 'Open interest (USD)',
  volume24h: '24h volume',
  turnover24h: '24h turnover (USD)',
  avg7dTurnoverUsd: '7d avg turnover (USD)',
};

const SPECS_GROUPS = [
  { title: 'Contract', keys: ['resolved_symbol', 'category', 'launchTime'] },
  { title: 'Price', keys: ['lastPrice'] },
  { title: 'Funding', keys: ['fundingRate', 'nextFundingTime'] },
  { title: 'Open interest', keys: ['openInterest', 'openInterestValue'] },
  { title: 'Volume', keys: ['volume24h', 'turnover24h', 'avg7dTurnoverUsd'] },
  { title: 'Scanner', predicate: (k) => k.startsWith('scan.') },
  { title: 'Other', predicate: (_k) => true },
];

function normSymbol(value) {
  return String(value || '').replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
}

function looksLikeFullSymbol(value) {
  const s = normSymbol(value);
  if (s.length < 6) {
    return false;
  }
  return SYMBOL_SUFFIXES.some((x) => s.endsWith(x));
}

function setSymbolStatus(text) {
  const el = document.getElementById('symbol_status');
  if (el) {
    el.textContent = text || '';
  }
}

function isNumericLike(v) {
  if (v === null || v === undefined) {
    return false;
  }
  if (typeof v === 'number') {
    return Number.isFinite(v);
  }
  if (typeof v !== 'string') {
    return false;
  }
  const s = v.trim();
  return s !== '' && /^-?\d+(\.\d+)?$/.test(s);
}

function formatTimestampBrisbane(value) {
  if (!isNumericLike(value)) {
    return null;
  }
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return null;
  }
  const ms = n < 1e12 ? n * 1000 : n;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) {
    return null;
  }
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
}

function compactNumber(n, decimals = 2) {
  const num = Number(n);
  if (!Number.isFinite(num)) {
    return String(n ?? '—');
  }
  const abs = Math.abs(num);
  if (abs >= 1e12) {
    return `${(num / 1e12).toFixed(decimals).replace(/\.00$/, '')}T`;
  }
  if (abs >= 1e9) {
    return `${(num / 1e9).toFixed(decimals).replace(/\.00$/, '')}B`;
  }
  if (abs >= 1e6) {
    return `${(num / 1e6).toFixed(decimals).replace(/\.00$/, '')}M`;
  }
  if (abs >= 1e3) {
    return `${(num / 1e3).toFixed(decimals).replace(/\.00$/, '')}K`;
  }
  return num.toFixed(decimals).replace(/\.00$/, '');
}

function formatNumber(n, minDecimals = 0, maxDecimals = 2) {
  const num = Number(n);
  if (!Number.isFinite(num)) {
    return String(n ?? '—');
  }
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: minDecimals,
    maximumFractionDigits: maxDecimals,
  }).format(num);
}

function formatPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return String(value ?? '—');
  }
  const abs = Math.abs(n);
  if (abs >= 1) return formatNumber(n, 2, 2);
  if (abs >= 0.01) return formatNumber(n, 4, 4);
  if (abs >= 0.0001) return formatNumber(n, 6, 6);
  return formatNumber(n, 8, 8);
}

function titleCaseWords(text) {
  return String(text || '')
    .replace(/[_\-.]+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => {
      const lower = w.toLowerCase();
      if (lower === 'usd') return 'USD';
      if (lower === 'oi') return 'OI';
      if (lower === 'tp') return 'TP';
      if (lower === 'sl') return 'SL';
      if (lower === 'atr') return 'ATR';
      if (lower === 'ema') return 'EMA';
      if (lower === 'vwap') return 'VWAP';
      if (lower === 'pnl') return 'PnL';
      return w.charAt(0).toUpperCase() + w.slice(1);
    })
    .join(' ');
}

function prettifyScanKey(key) {
  const parts = String(key || '').split('.');
  if (parts.length < 2 || parts[0] !== 'scan') return null;
  const metric = parts[1] || '';
  const tfRaw = parts.slice(2).join('.') || '';
  const metricLabelMap = {
    fundingRate: 'Funding rate',
    openInterest: 'Open interest',
    openInterestValue: 'Open interest (USD)',
    volume: 'Volume',
    turnover: 'Turnover (USD)',
  };
  const metricLabel = metricLabelMap[metric] || titleCaseWords(metric);
  const tf = tfRaw
    .replace(/(\d+)([MHDW])/g, (_m, n, u) => `${n}${String(u).toLowerCase()}`)
    .replace(/_/g, ' ')
    .trim();
  return tf ? `${metricLabel} (${tf})` : metricLabel;
}

function formatPercentFromFraction(v, decimals = 4) {
  const n = Number(v);
  if (!Number.isFinite(n)) {
    return String(v ?? '—');
  }
  return `${(n * 100).toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '')}%`;
}

function formatSpecValue(key, value, unit) {
  if (key === 'category' && value !== null && value !== undefined) {
    return titleCaseWords(value);
  }
  if (unit === 'timestamp_ms') {
    const t = formatTimestampBrisbane(value);
    if (t) return t;
  }
  if (unit === 'fraction') {
    return formatPercentFromFraction(value, 2);
  }
  if (unit === 'usd_value' || unit === 'usd_value_24h' || unit === 'usd_value_per_day_avg_7d') {
    return `$${compactNumber(value)}`;
  }
  if (unit === 'contracts' || unit === 'base_units_24h') {
    return compactNumber(value);
  }
  if (unit === 'ratio') {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(4) : String(value ?? '—');
  }
  if (unit === 'price' && isNumericLike(value)) {
    return formatPrice(value);
  }
  if (key === 'launchTime' || key === 'nextFundingTime' || /(time|timestamp)$/i.test(key)) {
    const t = formatTimestampBrisbane(value);
    if (t) {
      return t;
    }
  }
  if (key === 'fundingRate' || key.endsWith('.fundingRate')) {
    return formatPercentFromFraction(value);
  }
  if (/^(turnover24h|openInterestValue|avg7dTurnoverUsd)$/i.test(key)) {
    return `$${compactNumber(value)}`;
  }
  if (/^(volume24h|openInterest)$/i.test(key)) {
    return compactNumber(value);
  }
  if (typeof value === 'object' && value !== null) {
    return JSON.stringify(value);
  }
  return String(value ?? '—');
}

function renderEmbeddedSpecsSummary(obj, units) {
  const el = document.getElementById('embedded_specs_summary');
  if (!el) return;
  el.innerHTML = '';

  const cards = [
    { label: 'Symbol', key: 'resolved_symbol' },
    { label: 'Market', key: 'category' },
    { label: 'Last price', key: 'lastPrice' },
    { label: 'Funding', key: 'fundingRate', subKey: 'nextFundingTime', subLabel: 'Next' },
    { label: 'Open interest', key: 'openInterestValue', subKey: 'openInterest', subLabel: 'Contracts' },
    { label: '24h turnover', key: 'turnover24h', subKey: 'volume24h', subLabel: 'Vol' },
  ];

  for (const card of cards) {
    const wrap = document.createElement('div');
    wrap.className = 'specs-card';
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = card.label;
    const value = document.createElement('div');
    value.className = 'value';
    value.textContent = formatSpecValue(card.key, obj[card.key], units[card.key]);
    wrap.appendChild(label);
    wrap.appendChild(value);
    if (card.subKey) {
      const sub = document.createElement('div');
      sub.className = 'sub';
      sub.textContent = `${card.subLabel}: ${formatSpecValue(card.subKey, obj[card.subKey], units[card.subKey])}`;
      wrap.appendChild(sub);
    }
    el.appendChild(wrap);
  }
}

function renderEmbeddedSpecs(specs) {
  const rows = document.getElementById('embedded_specs_rows');
  if (!rows) {
    return;
  }
  rows.innerHTML = '';

  const obj = (specs && typeof specs === 'object') ? specs : {};
  const units = (obj._units && typeof obj._units === 'object') ? obj._units : {};
  const source = String(obj.source || '');

  renderEmbeddedSpecsSummary(obj, units);
  const keys = [];
  // 1) Primary keys
  for (const key of SPECS_PRIMARY_KEYS) {
    if (obj[key] !== null && obj[key] !== undefined) {
      keys.push(key);
    }
  }
  // 2) Scanner metrics
  const scanKeys = Object.keys(obj).filter((k) => k.startsWith('scan.'));
  scanKeys.sort((a, b) => a.localeCompare(b));
  for (const k of scanKeys) {
    if (!keys.includes(k)) keys.push(k);
  }
  // 3) Remaining scalar keys
  const rest = Object.keys(obj).filter((k) => !k.startsWith('scan.') && !SPECS_HIDE_FIELDS.has(k) && k !== '_units');
  rest.sort((a, b) => a.localeCompare(b));
  for (const k of rest) {
    const v = obj[k];
    if (typeof v === 'object' && v !== null) continue;
    if (!keys.includes(k)) keys.push(k);
  }

  if (source && source !== 'bybit') {
    setEmbeddedSpecsStatus(`Source: ${source} (forced to Bybit next load)`);
  }

  const remaining = new Set(keys.filter((k) => !SPECS_HIDE_FIELDS.has(k)));

  for (const group of SPECS_GROUPS) {
    const groupKeys = [];
    if (group.keys) {
      for (const k of group.keys) {
        if (remaining.has(k)) {
          groupKeys.push(k);
          remaining.delete(k);
        }
      }
    } else if (group.predicate) {
      const picked = Array.from(remaining).filter((k) => group.predicate(k));
      picked.sort((a, b) => a.localeCompare(b));
      for (const k of picked) {
        groupKeys.push(k);
        remaining.delete(k);
      }
    }
    if (!groupKeys.length) continue;

    const sectionTr = document.createElement('tr');
    sectionTr.className = 'specs-section-row';
    const sectionTd = document.createElement('td');
    sectionTd.colSpan = 2;
    sectionTd.textContent = group.title;
    sectionTr.appendChild(sectionTd);
    rows.appendChild(sectionTr);

    for (const key of groupKeys) {
      const value = obj[key];
      const tr = document.createElement('tr');
      const tdKey = document.createElement('td');
      tdKey.textContent = SPECS_FIELD_LABELS[key] || prettifyScanKey(key) || titleCaseWords(key);
      const tdVal = document.createElement('td');
      tdVal.textContent = formatSpecValue(key, value, units[key]);
      tr.appendChild(tdKey);
      tr.appendChild(tdVal);
      rows.appendChild(tr);
    }
  }
}

function setEmbeddedSpecsStatus(text) {
  const el = document.getElementById('embedded_specs_status');
  if (el) {
    el.textContent = text || '';
  }
}

async function resolveSymbol(force = false) {
  const symbolEl = document.getElementById('symbol');
  const tradeTypeEl = document.getElementById('trade_type');
  if (!symbolEl) {
    return null;
  }
  const tradeType = (tradeTypeEl ? tradeTypeEl.value : '').toLowerCase();
  if (tradeType === 'options') {
    return null;
  }

  const raw = String(symbolEl.value || '').trim();
  const normalized = normSymbol(raw);
  if (!normalized) {
    setSymbolStatus('');
    return null;
  }
  symbolEl.value = normalized;
  if (!force && looksLikeFullSymbol(normalized)) {
    setSymbolStatus('');
    return normalized;
  }

  const priceSourceEl = document.getElementById('price_source');
  const priceSource = priceSourceEl ? String(priceSourceEl.value || '').trim() : '';
  setSymbolStatus('Resolving...');
  try {
    const url = buildAppUrl(`/api/resolve-symbol?symbol=${encodeURIComponent(normalized)}&price_source=${encodeURIComponent(priceSource)}`);
    const resp = await fetch(url, { cache: 'no-store' });
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      setSymbolStatus((data && data.detail) ? String(data.detail) : 'No match');
      return null;
    }
    const resolved = normSymbol(data && data.resolved_symbol);
    if (resolved) {
      symbolEl.value = resolved;
      setSymbolStatus(resolved === normalized ? '' : `→ ${resolved}`);
      return resolved;
    }
    setSymbolStatus('No match');
    return null;
  } catch {
    setSymbolStatus('Resolve failed');
    return null;
  }
}

async function loadEmbeddedSpecs() {
  const symbolEl = document.getElementById('symbol');
  const panel = document.getElementById('embedded_specs');
  if (!symbolEl || !panel) {
    return;
  }
  const resolved = await resolveSymbol(true);
  const q = normSymbol(resolved || symbolEl.value);
  if (!q) {
    return;
  }

  panel.style.display = 'block';
  setEmbeddedSpecsStatus('Loading...');
  try {
    // instrument specs endpoint is served by the main dashboard at site-root.
    // Do NOT prefix with appRoot (otherwise it becomes /<script>/api/instrument-specs -> 404).
    // Force Bybit so BTC* doesn't resolve to OANDA crypto CFD specs (financing fields).
    const resp = await fetch(`/api/instrument-specs?query=${encodeURIComponent(q)}&prefer=bybit&include_scanner=1`, { cache: 'no-store' });
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
  let shouldDispatchChange = dispatchChange;
  if (typeof dispatchChange === 'object' && dispatchChange !== null) {
    shouldDispatchChange = dispatchChange.silent !== true;
  }
  const input = document.getElementById(inputId);
  if (!input) {
    return;
  }
  const hasValueChanged = input.value !== value;
  if (hasValueChanged) {
    input.value = value;
  }
  const group = document.querySelector('[data-input="' + inputId + '"]');
  if (group) {
    group.querySelectorAll('button[data-value]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.value === value);
    });
  }
  if (shouldDispatchChange && hasValueChanged) {
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

function updateRiskControls() {
  if (isUpdatingRiskControls) {
    return;
  }
  isUpdatingRiskControls = true;
  try {
    const exchangeEl = document.getElementById('execution_exchange');
    const riskModeRow = document.getElementById('risk_mode_row');
    const riskModeEl = document.getElementById('risk_mode');
    const riskPercentRow = document.getElementById('risk_percent_row');
    const riskPercentInput = document.getElementById('risk_percent');
    const fixedRow = document.getElementById('fixed_risk_aud_row');
    const fixedInput = document.getElementById('fixed_risk_aud');

    const isCoinspot = !!exchangeEl && exchangeEl.value === 'coinspot';
    if (riskModeRow) {
      riskModeRow.classList.toggle('hidden', !isCoinspot);
    }
    if (riskModeEl && !isCoinspot) {
      setButtonGroupValue('risk_mode', 'percent', { silent: true });
    }

    const riskMode = (riskModeEl ? riskModeEl.value : 'percent').toLowerCase();
    const useFixed = isCoinspot && riskMode === 'fixed_aud';

    if (riskPercentRow) {
      riskPercentRow.classList.toggle('hidden', useFixed);
    }
    if (fixedRow) {
      fixedRow.classList.toggle('hidden', !useFixed);
    }

    if (riskPercentInput) {
      if (useFixed) {
        riskPercentInput.removeAttribute('required');
      } else {
        riskPercentInput.setAttribute('required', 'required');
      }
    }
    if (fixedInput) {
      if (useFixed) {
        fixedInput.setAttribute('required', 'required');
      } else {
        fixedInput.removeAttribute('required');
      }
    }
  } finally {
    isUpdatingRiskControls = false;
  }
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
  const qtyMode = document.getElementById('options_quantity_mode');
  if (qtyMode && qtyMode.value === 'auto') {
    note.innerText = 'Qty auto mode: lot-size constraints are resolved from live instruments metadata.';
    return;
  }
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

function updateOptionsFieldModes() {
  const expiryMode = document.getElementById('options_expiry_mode');
  const strikeMode = document.getElementById('options_strike_mode');
  const qtyMode = document.getElementById('options_quantity_mode');
  const expiryManual = document.getElementById('options_expiry_manual_row');
  const strikeManual = document.getElementById('options_strike_manual_row');
  const qtyInput = document.getElementById('options_quantity');
  if (expiryMode && expiryManual) {
    expiryManual.classList.toggle('hidden', expiryMode.value !== 'manual');
  }
  if (strikeMode && strikeManual) {
    strikeManual.classList.toggle('hidden', strikeMode.value !== 'manual');
  }
  if (qtyMode && qtyInput) {
    qtyInput.disabled = qtyMode.value !== 'manual';
  }
  updateOptionsMinQty();
}

function updateTradeType() {
  const selector = document.getElementById('trade_type');
  const optionsSection = document.getElementById('options_section');
  const cryptoSection = document.getElementById('crypto_section');
  const manual = document.getElementById('options_manual_fields');
  const orderTypeRow = document.getElementById('options_order_type_row');
  if (!selector || !optionsSection || !cryptoSection) {
    return;
  }
  const isOptions = selector.value === 'options';
  optionsSection.classList.toggle('hidden', !isOptions);
  cryptoSection.classList.toggle('hidden', isOptions);
  if (manual) {
    manual.classList.toggle('hidden', !isOptions);
  }
  if (orderTypeRow) {
    orderTypeRow.classList.toggle('hidden', !isOptions);
  }
  updateRiskControls();
  const cryptoRequired = ['symbol', 'stop_loss_ticks', 'rr_ratio'];
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
  ['trade_type', 'account_mode', 'direction', 'order_type', 'options_order_type', 'options_type', 'options_side', 'price_source', 'execution_exchange', 'options_base', 'track_pending', 'risk_mode', 'options_expiry_mode', 'options_strike_mode', 'options_quantity_mode'].forEach(bindButtonGroup);
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
  const ex = document.getElementById('execution_exchange');
  if (ex) {
    ex.addEventListener('change', () => {
      ensureAudUsdRate(true);
    });
  }
  ensureAudUsdRate(false);
  const executionExchange = document.getElementById('execution_exchange');
  if (executionExchange) {
    executionExchange.addEventListener('change', updateRiskControls);
  }
  const riskModeEl = document.getElementById('risk_mode');
  if (riskModeEl) {
    riskModeEl.addEventListener('change', updateRiskControls);
  }
  updateRiskControls();
  const tradeType = document.getElementById('trade_type');
  if (tradeType) {
    tradeType.addEventListener('change', updateTradeType);
  }
  updateTradeType();
  const optionsBaseInput = document.getElementById('options_base');
  if (optionsBaseInput) {
    optionsBaseInput.addEventListener('change', scheduleOptionsMinQty);
  }
  ['options_expiry_mode', 'options_strike_mode', 'options_quantity_mode'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('change', updateOptionsFieldModes);
    }
  });
  updateOptionsFieldModes();
  scheduleOptionsMinQty();
  try {
    updateExecuteButtons();
  } catch (err) {
    console.warn('Failed to update execute buttons', err);
  }

  const symbolEl = document.getElementById('symbol');
  if (symbolEl) {
    symbolEl.addEventListener('blur', () => {
      resolveSymbol(false);
    });
  }

  const specsBtn = document.getElementById('symbol_specs_btn');
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
      if (submitInFlight) {
        return;
      }
      const tradeTypeEl = document.getElementById('trade_type');
      const tradeType = (tradeTypeEl ? tradeTypeEl.value : '').toLowerCase();
      if (tradeType === 'options') {
        const expiryMode = document.getElementById('options_expiry_mode');
        const strikeMode = document.getElementById('options_strike_mode');
        const qtyMode = document.getElementById('options_quantity_mode');
        const side = document.getElementById('options_side');
        const expiry = document.getElementById('options_expiry');
        const strike = document.getElementById('options_strike');
        const qty = document.getElementById('options_quantity');
        if (side && side.value === 'Sell' && ((expiryMode && expiryMode.value === 'auto') || (strikeMode && strikeMode.value === 'auto') || (qtyMode && qtyMode.value === 'auto'))) {
          e.preventDefault();
          alert('Auto risk-based contract selection is only supported for Buy in single-leg options.');
          return;
        }
        if (expiryMode && expiryMode.value === 'manual' && (!expiry || !expiry.value.trim())) {
          e.preventDefault();
          alert('Manual expiry mode requires Expiry (D/M/YY).');
          return;
        }
        if (strikeMode && strikeMode.value === 'manual' && (!strike || !strike.value.trim())) {
          e.preventDefault();
          alert('Manual strike mode requires Strike.');
          return;
        }
        if (qtyMode && qtyMode.value === 'manual' && (!qty || !qty.value.trim())) {
          e.preventDefault();
          alert('Manual quantity mode requires Quantity.');
          return;
        }
        return;
      }
      e.preventDefault();
      submitInFlight = true;
      try {
        await resolveSymbol(true);
      } finally {
        submitInFlight = false;
      }
      form.submit();
    });
  }
});
