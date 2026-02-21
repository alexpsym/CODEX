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
  ]);

  const fetchJson = async (url) => {
    const res = await fetch(url);
    const text = await res.text();
    if (!res.ok) throw new Error(text || `${res.status} ${res.statusText}`);
    try {
      return JSON.parse(text);
    } catch {
      throw new Error(text || 'Invalid JSON');
    }
  };

  const isNumericLike = (v) => {
    if (v === null || v === undefined) return false;
    if (typeof v === 'number') return Number.isFinite(v);
    if (typeof v !== 'string') return false;
    const s = v.trim();
    return s !== '' && /^-?\d+(\.\d+)?$/.test(s);
  };

  const formatTimestampValue = (v) => {
    if (!isNumericLike(v)) return null;
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    const ms = n < 1_000_000_000_000 ? n * 1000 : n;
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return null;
    return `${d.toLocaleString()} (${d.toISOString().replace('T', ' ').replace('Z', ' UTC')})`;
  };

  const compactNumber = (v, decimals = 2) => {
    if (!isNumericLike(v)) return String(v ?? '—');
    const n = Number(v);
    const abs = Math.abs(n);
    const units = [
      [1e12, 'T'],
      [1e9, 'B'],
      [1e6, 'M'],
      [1e3, 'K'],
    ];
    for (const [div, suffix] of units) {
      if (abs >= div) return `${(n / div).toFixed(decimals).replace(/\.00$/, '')}${suffix}`;
    }
    return n.toFixed(decimals).replace(/\.00$/, '');
  };

  const formatFieldValue = (key, value) => {
    if (/(time|timestamp)$/i.test(key)) {
      const ts = formatTimestampValue(value);
      if (ts) return ts;
    }
    if (/(^|\.)(volume24h|turnover24h|openInterest|openInterestValue)$/i.test(key)) {
      return compactNumber(value, 2);
    }
    if (typeof value === 'object' && value !== null) return JSON.stringify(value);
    return String(value ?? '—');
  };

  const render = (specs) => {
    if (!rows) return;
    rows.innerHTML = '';
    const entries = Object.entries(specs || {}).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    entries.forEach(([key, value]) => {
      if (HIDE_FIELDS.has(key)) return;
      const tr = document.createElement('tr');
      const td1 = document.createElement('td');
      const td2 = document.createElement('td');
      td1.textContent = key;
      td2.textContent = formatFieldValue(key, value);
      tr.appendChild(td1);
      tr.appendChild(td2);
      rows.appendChild(tr);
    });
  };

  const setErr = (message) => {
    if (!err) return;
    err.textContent = message || '';
  };

  const load = async () => {
    const q = String(qInput?.value || '').trim();
    if (!q) return;
    setErr('');
    try {
      const specs = await fetchJson(`/api/instrument-specs?query=${encodeURIComponent(q)}`);
      render(specs);
      if (dl) dl.href = `/api/instrument-specs.jpg?query=${encodeURIComponent(q)}`;
      history.replaceState(null, '', `/instrument-specs?q=${encodeURIComponent(q)}`);
    } catch (e) {
      render({});
      setErr(e.message || String(e));
    }
  };

  loadBtn?.addEventListener('click', load);
  qInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    load();
  });

  const initial = (qs.get('q') || '').trim();
  if (qInput) qInput.value = initial;
  if (dl) dl.href = `/api/instrument-specs.jpg?query=${encodeURIComponent(initial || '')}`;
  if (initial) load();
})();
