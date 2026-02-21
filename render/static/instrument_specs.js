(() => {
  const qs = new URLSearchParams(window.location.search);
  const qInput = document.getElementById('q');
  const loadBtn = document.getElementById('load');
  const dl = document.getElementById('download');
  const rows = document.getElementById('rows');
  const err = document.getElementById('err');

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

  const render = (specs) => {
    if (!rows) return;
    rows.innerHTML = '';
    Object.keys(specs || {})
      .sort((a, b) => String(a).localeCompare(String(b)))
      .forEach((k) => {
        const tr = document.createElement('tr');
        const td1 = document.createElement('td');
        const td2 = document.createElement('td');
        td1.textContent = k;
        const v = specs[k];
        td2.textContent = typeof v === 'object' ? JSON.stringify(v) : String(v);
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
