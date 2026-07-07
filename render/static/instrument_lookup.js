(() => {
  const qs = new URLSearchParams(window.location.search);
  const qInput = document.getElementById('q');
  const loadBtn = document.getElementById('load');
  const downloadLink = document.getElementById('download');
  const rows = document.getElementById('rows');
  const err = document.getElementById('err');
  const assetToggle = document.getElementById('asset-toggle');
  const journalStatus = document.getElementById('journal-status');
  const journalMetrics = document.getElementById('journal-metrics');
  const tradeHead = document.getElementById('trade-head');
  const tradeBody = document.getElementById('trade-body');

  const state = { asset: 'crypto' };
  const FX_CODES = new Set(['AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'HKD', 'JPY', 'NZD', 'SGD', 'TRY', 'USD', 'ZAR', 'XAU', 'XAG']);
  const HIDE_SPEC_FIELDS = new Set([
    'contractType', 'fundingHistory.fundingRate', 'fundingHistory.fundingRateTimestamp',
    'indexPrice', 'leverageFilter', 'lotSizeFilter', 'markPrice', 'priceFilter',
    'query', 'baseCoin', 'quoteCoin', 'source', 'status', 'scannerVolume24h',
    'openInterest', 'volume24h', '_units', '_btc_reference', '_spec_warnings',
  ]);
  const SPEC_LABELS = {
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
  const SPEC_ORDER = ['resolved_symbol', 'category', 'lastPrice', 'fundingRate', 'nextFundingTime', 'launchTime', 'openInterestValue', 'volume24hUsd', 'turnover24h', 'avg7dTurnoverUsd', 'range.1m', 'range.5m', 'range.15m', 'range.30m', 'range.1h', 'range.4h', 'range.1d', 'range.1w', 'range.1mo'];
  const TRADE_ORDER = ['trade_number', 'id', 'row_type', 'account', 'account_label', 'asset_class', 'symbol', 'side', 'open_time', 'close_time', 'qty', 'entry_price', 'exit_price', 'stop_loss', 'take_profit', 'net_profit', 'result_pct', 'r_multiple', 'balance_after_trade', 'analysis_balance_after_trade', 'commission', 'setup', 'timeframe', 'pattern', 'notes'];

  function setErr(message) {
    if (err) err.textContent = message || '';
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function isNumericLike(value) {
    if (value === null || value === undefined) return false;
    if (typeof value === 'number') return Number.isFinite(value);
    if (typeof value !== 'string') return false;
    const text = value.trim();
    return text !== '' && /^-?\d+(\.\d+)?$/.test(text);
  }

  function isLikelyFxPair(value) {
    const text = String(value || '').trim().toUpperCase();
    if (/^[A-Z]{3}_[A-Z]{3}$/.test(text)) {
      const [base, quote] = text.split('_');
      return FX_CODES.has(base) && FX_CODES.has(quote);
    }
    if (/^[A-Z]{6}$/.test(text)) {
      return FX_CODES.has(text.slice(0, 3)) && FX_CODES.has(text.slice(3));
    }
    return false;
  }

  function setAsset(asset) {
    state.asset = asset === 'fx' ? 'fx' : 'crypto';
    assetToggle?.querySelectorAll('button[data-asset]').forEach((button) => {
      button.classList.toggle('active', button.dataset.asset === state.asset);
    });
  }

  function formatTimestampBrisbane(value) {
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
  }

  function compactNumber(value, decimals = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value ?? '-');
    const abs = Math.abs(number);
    if (abs >= 1e12) return `${(number / 1e12).toFixed(decimals).replace(/\.00$/, '')}T`;
    if (abs >= 1e9) return `${(number / 1e9).toFixed(decimals).replace(/\.00$/, '')}B`;
    if (abs >= 1e6) return `${(number / 1e6).toFixed(decimals).replace(/\.00$/, '')}M`;
    if (abs >= 1e3) return `${(number / 1e3).toFixed(decimals).replace(/\.00$/, '')}K`;
    return number.toFixed(decimals).replace(/\.00$/, '');
  }

  function formatPercentFromFraction(value, decimals = 4) {
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value ?? '-');
    return `${(number * 100).toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '')}%`;
  }

  function formatSpecValue(key, value) {
    if (key === 'launchTime' || key === 'nextFundingTime' || /(time|timestamp)$/i.test(key)) {
      const formatted = formatTimestampBrisbane(value);
      if (formatted) return formatted;
    }
    if (key === 'fundingRate' || key.endsWith('.fundingRate')) return formatPercentFromFraction(value);
    if (key.startsWith('range.')) return formatPercentFromFraction(value, 2);
    if (/^(volume24hUsd|turnover24h|openInterestValue|avg7dTurnoverUsd)$/i.test(key)) return `$${compactNumber(value)}`;
    if (typeof value === 'object' && value !== null) return JSON.stringify(value);
    return String(value ?? '-');
  }

  function formatAnyValue(value) {
    if (value === null || value === undefined || value === '') return '-';
    if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 10 }) : '-';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (_err) { payload = null; }
    if (!response.ok) {
      const detail = payload?.detail || payload?.error || payload?.message || text || response.statusText;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return payload || {};
  }

  async function resolveBybitSymbol(value) {
    if (!value || isLikelyFxPair(value)) return value;
    try {
      const payload = await fetchJson(`/api/resolve-symbol?symbol=${encodeURIComponent(value)}&prefer=bybit&scope=all`);
      return String(payload?.resolved_symbol || value).trim() || value;
    } catch (_err) {
      return value;
    }
  }

  function renderSpecs(specs) {
    if (!rows) return;
    const btcRef = specs && typeof specs._btc_reference === 'object' ? specs._btc_reference : null;
    const keys = Object.keys(specs || {}).filter((key) => !HIDE_SPEC_FIELDS.has(key));
    const entries = [
      ...SPEC_ORDER.filter((key) => keys.includes(key)).map((key) => [key, specs[key]]),
      ...keys.filter((key) => !SPEC_ORDER.includes(key)).sort().map((key) => [key, specs[key]]),
    ];
    rows.innerHTML = entries.map(([key, value]) => {
      const label = SPEC_LABELS[key] || key;
      const main = `<tr><td>${escapeHtml(label)}</td><td>${escapeHtml(formatSpecValue(key, value))}</td></tr>`;
      const ref = btcRef && btcRef[key] !== undefined
        ? `<tr><td>${escapeHtml(`BTC ${label}`)}</td><td>${escapeHtml(formatSpecValue(key, btcRef[key]))}</td></tr>`
        : '';
      return main + ref;
    }).join('');
  }

  function flattenMetrics(value, prefix = '') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    const out = [];
    for (const [key, item] of Object.entries(value)) {
      const label = prefix ? `${prefix}.${key}` : key;
      if (item && typeof item === 'object' && !Array.isArray(item)) out.push(...flattenMetrics(item, label));
      else if (Array.isArray(item)) out.push([label, item.length ? JSON.stringify(item) : '[]']);
      else out.push([label, item]);
    }
    return out;
  }

  function renderJournal(payload) {
    if (!journalMetrics || !tradeHead || !tradeBody) return;
    const canonical = payload?.canonical_symbol || '';
    if (payload?.status === 'no_data') {
      journalStatus.textContent = canonical ? `No journal rows for ${canonical}.` : 'No journal rows.';
      journalMetrics.innerHTML = '';
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      return;
    }
    if (payload?.status !== 'ok') {
      journalStatus.textContent = 'Journal lookup did not return data.';
      journalMetrics.innerHTML = '';
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      return;
    }
    const trades = Array.isArray(payload.trades) ? payload.trades : [];
    journalStatus.textContent = `${canonical || 'Symbol'}: ${trades.length} journal row${trades.length === 1 ? '' : 's'}`;
    const metricPairs = [
      ...flattenMetrics(payload.stats || {}, 'summary'),
      ...flattenMetrics(payload.metrics || {}, 'metrics'),
      ...flattenMetrics(payload.period_reports || {}, 'period'),
    ];
    journalMetrics.innerHTML = metricPairs.map(([key, value]) => (
      `<div class="metric"><div class="metric-key">${escapeHtml(key)}</div><div class="metric-value">${escapeHtml(formatAnyValue(value))}</div></div>`
    )).join('');
    const tradeKeys = [...TRADE_ORDER, ...Array.from(new Set(trades.flatMap((trade) => Object.keys(trade || {})))).filter((key) => !TRADE_ORDER.includes(key)).sort()];
    if (!trades.length) {
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      return;
    }
    tradeHead.innerHTML = `<tr>${tradeKeys.map((key) => `<th>${escapeHtml(key)}</th>`).join('')}</tr>`;
    tradeBody.innerHTML = trades.map((trade) => (
      `<tr>${tradeKeys.map((key) => `<td>${escapeHtml(formatAnyValue(trade?.[key]))}</td>`).join('')}</tr>`
    )).join('');
  }

  async function load() {
    const raw = String(qInput?.value || '').trim();
    if (!raw) return;
    setErr('Loading...');
    renderSpecs({});
    renderJournal({ status: 'loading', trades: [] });
    const detectedAsset = isLikelyFxPair(raw) ? 'fx' : state.asset;
    setAsset(detectedAsset);
    const resolved = detectedAsset === 'crypto' ? await resolveBybitSymbol(raw) : raw;
    if (qInput && resolved && resolved !== raw) qInput.value = resolved;
    const prefer = detectedAsset === 'fx' ? '&prefer=oanda' : '';
    try {
      const [specs, journal] = await Promise.all([
        fetchJson(`/api/instrument-specs?query=${encodeURIComponent(resolved)}${prefer}`),
        fetchJson(`/api/calculator/journal-summary?asset=${encodeURIComponent(detectedAsset)}&symbol=${encodeURIComponent(resolved)}`),
      ]);
      renderSpecs(specs);
      renderJournal(journal);
      if (downloadLink) downloadLink.href = `/api/instrument-specs.jpg?query=${encodeURIComponent(resolved)}${prefer}`;
      history.replaceState(null, '', `/instrument-lookup?q=${encodeURIComponent(resolved)}&asset=${encodeURIComponent(detectedAsset)}`);
      const warnings = Array.isArray(specs?._spec_warnings) ? specs._spec_warnings : [];
      setErr(warnings.length ? `Some instrument specs could not be loaded: ${warnings.map((w) => `${w.field || 'spec'} ${w.symbol || ''}`).join(', ')}` : '');
    } catch (error) {
      setErr(error?.message || String(error));
      renderSpecs({});
      renderJournal({ status: 'error', trades: [] });
    }
  }

  assetToggle?.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-asset]');
    if (!button) return;
    setAsset(button.dataset.asset);
  });
  loadBtn?.addEventListener('click', load);
  qInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    load();
  });

  setAsset((qs.get('asset') || '').toLowerCase() === 'fx' ? 'fx' : 'crypto');
  const initial = (qs.get('q') || '').trim();
  if (qInput) qInput.value = initial;
  if (initial) load();
})();
