(() => {
  const qs = new URLSearchParams(window.location.search);
  const qInput = document.getElementById('q');
  const loadBtn = document.getElementById('load');
  const dl = document.getElementById('download');
  const rows = document.getElementById('rows');
  const err = document.getElementById('err');

  const HIDE_FIELDS = new Set([
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

  const FIELD_LABELS = {
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

  function isNumericLike(v) {
    if (v === null || v === undefined) return false;
    if (typeof v === 'number') return Number.isFinite(v);
    if (typeof v !== 'string') return false;
    const s = v.trim();
    return s !== '' && /^-?\d+(\.\d+)?$/.test(s);
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

  function formatPercentFromFraction(v, decimals = 4) {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v ?? '—');
    return `${(n * 100).toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '')}%`;
  }

  function formatValue(key, value) {
    if (key === 'launchTime' || key === 'nextFundingTime' || /(time|timestamp)$/i.test(key)) {
      const t = formatTimestampBrisbane(value);
      if (t) return t;
    }

    if (key === 'fundingRate' || key.endsWith('.fundingRate')) {
      return formatPercentFromFraction(value);
    }

    if (key === 'fundingRate' || key.endsWith('.fundingRate') || key.startsWith('range.')) {
      return formatPercentFromFraction(value, 2);
    }

    if (/^(volume24hUsd|turnover24h|openInterestValue|avg7dTurnoverUsd)$/i.test(key)) {
      return `$${compactNumber(value)}`;
    }

    if (typeof value === 'object' && value !== null) return JSON.stringify(value);
    return String(value ?? '—');
  }

  function setErr(message) {
    if (!err) return;
    err.textContent = message || '';
  }


  const FX_CODES = new Set(['AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'HKD', 'JPY', 'NZD', 'SGD', 'TRY', 'USD', 'ZAR', 'XAU', 'XAG']);
  function isLikelyFxPair(q) {
    const s = String(q || '').trim().toUpperCase();
    if (/^[A-Z]{3}_[A-Z]{3}$/.test(s)) {
      const [base, quote] = s.split('_');
      return FX_CODES.has(base) && FX_CODES.has(quote);
    }
    if (/^[A-Z]{6}$/.test(s)) {
      const base = s.slice(0, 3);
      const quote = s.slice(3);
      return FX_CODES.has(base) && FX_CODES.has(quote);
    }
    return false;
  }

  const DISPLAY_ORDER=['resolved_symbol','category','lastPrice','fundingRate','nextFundingTime','launchTime','openInterestValue','volume24hUsd','turnover24h','avg7dTurnoverUsd','range.1m','range.5m','range.15m','range.30m','range.1h','range.4h','range.1d','range.1w','range.1mo'];

  async function resolveBybitSymbol(q) {
    const value = String(q || '').trim();
    if (!value || isLikelyFxPair(value)) return value;
    const resp = await fetch(`/api/resolve-symbol?symbol=${encodeURIComponent(value)}&prefer=bybit&scope=all`, {
      cache: 'no-store',
    });
    if (!resp.ok) return value;
    const data = await resp.json().catch(() => null);
    const resolved = String(data?.resolved_symbol || '').trim();
    return resolved || value;
  }

  

  function renderSpecsTable(specs) {
    if (!rows) return;
    rows.innerHTML = '';
    const btcRef = specs && typeof specs._btc_reference === 'object' ? specs._btc_reference : null;
    const keys = Object.keys(specs || {}).filter((k) => !HIDE_FIELDS.has(k));
    const flattenedEntries = [...DISPLAY_ORDER.filter((k) => keys.includes(k)).map((k) => [k, specs[k]]), ...keys.filter((k) => !DISPLAY_ORDER.includes(k)).sort().map((k) => [k, specs[k]])];

    for (const [key, value] of flattenedEntries) {
      if (HIDE_FIELDS.has(key)) continue;

      const tr = document.createElement('tr');
      const tdKey = document.createElement('td');
      const tdVal = document.createElement('td');
      tdKey.textContent = FIELD_LABELS[key] || key;
      tdVal.textContent = formatValue(key, value);
      tr.appendChild(tdKey);
      tr.appendChild(tdVal);
      rows.appendChild(tr);
      if (btcRef && btcRef[key] !== undefined) {
        const btr = document.createElement('tr');
        btr.className = 'btc-reference-row';
        const bKey = document.createElement('td');
        const bVal = document.createElement('td');
        bKey.textContent = `BTC ${FIELD_LABELS[key] || key}`;
        bVal.textContent = formatValue(key, btcRef[key]);
        btr.appendChild(bKey);
        btr.appendChild(bVal);
        rows.appendChild(btr);
      }
    }
  }

  async function loadSpecs(q) {
    const prefer = isLikelyFxPair(q) ? "&prefer=oanda" : "";
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 12000);
    setErr('Loading…');

    let res;
    try {
      res = await fetch(`/api/instrument-specs?query=${encodeURIComponent(q)}${prefer}`, {
        signal: controller.signal,
      });
    } catch (e) {
      if (e?.name === 'AbortError') {
        setErr('Lookup timed out');
      } else {
        setErr(e?.message || 'Lookup failed');
      }
      renderSpecsTable({});
      return;
    } finally {
      clearTimeout(timeoutId);
    }

    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }

    if (!res.ok) {
      setErr((data && data.detail) || 'Lookup failed');
      renderSpecsTable({});
      return;
    }

    setErr('');
    renderSpecsTable(data);
    const warnings = Array.isArray(data?._spec_warnings) ? data._spec_warnings : [];
    if (warnings.length) setErr(`Some instrument specs could not be loaded: ${warnings.map((w) => `${w.field || 'spec'} ${w.symbol || ''}`).join(', ')}`);
  }

  async function load() {
    const raw = String(qInput?.value || '').trim();
    if (!raw) return;
    try {
      const q = await resolveBybitSymbol(raw);
      if (qInput && q && q !== raw) qInput.value = q;
      await loadSpecs(q);
      const prefer = isLikelyFxPair(q) ? '&prefer=oanda' : '';
      if (dl) dl.href = `/api/instrument-specs.jpg?query=${encodeURIComponent(q)}${prefer}`;
      history.replaceState(null, '', `/instrument-specs?q=${encodeURIComponent(q)}`);
    } catch (e) {
      setErr(e?.message || String(e));
      renderSpecsTable({});
    }
  }

  loadBtn?.addEventListener('click', load);
  qInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    load();
  });

  const initial = (qs.get('q') || '').trim();
  if (qInput) qInput.value = initial;
  const initialPrefer = isLikelyFxPair(initial || '') ? '&prefer=oanda' : '';
  if (dl) dl.href = `/api/instrument-specs.jpg?query=${encodeURIComponent(initial || '')}${initialPrefer}`;
  if (initial) load();
})();
