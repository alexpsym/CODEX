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
    '_units',
  ]);

  const FIELD_LABELS = {
    resolved_symbol: 'resolved_symbol',
    category: 'category',
    lastPrice: 'lastPrice (price)',
    fundingRate: 'fundingRate (%)',
    nextFundingTime: 'nextFundingTime (Brisbane time)',
    launchTime: 'launchTime (Brisbane time)',
    openInterestValue: 'openInterestValue (USD)',
    turnover24h: 'turnover24h (USD)',
    avg7dTurnoverUsd: 'avg7dVolume (USD)',
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

    if (/^(turnover24h|openInterestValue|avg7dTurnoverUsd|volume24h)$/i.test(key)) {
      return `$${compactNumber(value)}`;
    }

    if (typeof value === 'object' && value !== null) return JSON.stringify(value);
    return String(value ?? '—');
  }

  function setErr(message) {
    if (!err) return;
    err.textContent = message || '';
  }


  function isLikelyFxPair(q) {
    const s = String(q || '').trim().toUpperCase();
    return /^[A-Z]{6}$/.test(s) || /^[A-Z]{3}_[A-Z]{3}$/.test(s);
  }

  function renderSpecsTable(specs) {
    if (!rows) return;
    rows.innerHTML = '';
    const flattenedEntries = Object.entries(specs || {}).sort((a, b) => String(a[0]).localeCompare(String(b[0])));

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
  }

  async function load() {
    const q = String(qInput?.value || '').trim();
    if (!q) return;
    try {
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
