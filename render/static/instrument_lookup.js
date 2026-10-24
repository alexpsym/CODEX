(() => {
  const qs = new URLSearchParams(window.location.search);
  const qInput = document.getElementById('q');
  const loadBtn = document.getElementById('load');
  let rows = document.getElementById('rows');
  const err = document.getElementById('err');
  const assetToggle = document.getElementById('asset-toggle');
  let journalStatus = document.getElementById('journal-status');
  let journalMetrics = document.getElementById('journal-metrics');
  let tradeHead = document.getElementById('trade-head');
  let tradeBody = document.getElementById('trade-body');
  let recentTradesSection = document.getElementById('recent-trades-section');
  let tradeScrollTop = document.getElementById('trade-scroll-top');
  let tradeScrollSpacer = document.getElementById('trade-scroll-spacer');
  let tradeTableWrap = document.getElementById('trade-table-wrap');
  let tradeScrollResizeObserver = null;
  let tradeScrollResizeFallbackBound = false;
  let tradeScrollSyncing = false;
  let loadGeneration = 0;
  let exportSnapshot = null;
  let exportJob = null;
  let pngButton = null;
  let pngStatus = null;

  const state = { asset: 'crypto' };
  const FX_CODES = new Set(['AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'HKD', 'JPY', 'NZD', 'SGD', 'TRY', 'USD', 'ZAR', 'XAU', 'XAG']);
  const HIDE_SPEC_FIELDS = new Set([
    'category', 'status', 'baseCoin', 'quoteCoin',
    'fundingHistory.fundingRate', 'fundingHistory.fundingRateTimestamp',
    'indexPrice', 'leverageFilter', 'lotSizeFilter', 'markPrice', 'priceFilter',
    'openInterest', 'query', 'source', 'scannerVolume24h', '_units', '_btc_reference', '_spec_warnings', '_source_notice',
    'tickSize', 'minPrice', 'maxPrice', 'qtyStep', 'minOrderQty', 'maxOrderQty', 'maxMktOrderQty',
    'minNotionalValue', 'minLeverage', 'maxLeverage', 'leverageStep', 'pipLocation', 'displayPrecision',
    'tradeUnitsPrecision', 'minimumTradeSize', 'maximumOrderUnits', 'maximumPositionSize', 'marginRate',
  ]);

  const SPEC_LABELS = {
    resolved_symbol: 'Symbol',
    category: 'Category',
    type: 'Type',
    displayName: 'Display name',
    contractType: 'Contract',
    status: 'Status',
    baseCoin: 'Base',
    quoteCoin: 'Quote',
    lastPrice: 'Last price',
    fundingRate: 'Funding rate',
    nextFundingTime: 'Next funding',
    launchTime: 'Launch time',
    openInterestValue: 'Open interest value (USD)',
    volume24hUsd: '24h turnover',
    turnover24h: '24h turnover',
    avg7dTurnoverUsd: '7d avg turnover',
    tickSize: 'Tick size',
    minPrice: 'Min price',
    maxPrice: 'Max price',
    qtyStep: 'Qty step',
    minOrderQty: 'Min order qty',
    maxOrderQty: 'Max order qty',
    maxMktOrderQty: 'Max market order qty',
    minNotionalValue: 'Min notional',
    minLeverage: 'Min leverage',
    maxLeverage: 'Max leverage',
    leverageStep: 'Leverage step',
    pipLocation: 'Pip location',
    displayPrecision: 'Price precision',
    tradeUnitsPrecision: 'Unit precision',
    minimumTradeSize: 'Minimum trade size',
    maximumOrderUnits: 'Maximum order units',
    maximumPositionSize: 'Maximum position size',
    marginRate: 'Margin rate',
    'financing.longRate': 'Long financing',
    'financing.shortRate': 'Short financing',
    'financing.financingDaysOfWeek': 'Financing days',
    'range.1m': 'Range 1m',
    'range.5m': 'Range 5m',
    'range.15m': 'Range 15m',
    'range.30m': 'Range 30m',
    'range.1h': 'Range 1h',
    'range.4h': 'Range 4h',
    'range.1d': 'Daily range',
    'range.1w': 'Weekly range',
    'range.1mo': 'Monthly range',
  };

  const SPEC_SECTIONS = [
    {
      title: 'Instrument',
      note: 'Identity',
      keys: ['resolved_symbol', 'displayName', 'type', 'contractType'],
    },
    {
      title: 'Market Snapshot',
      note: 'Live context',
      keys: ['lastPrice', 'fundingRate', 'nextFundingTime', 'launchTime', 'openInterestValue', 'volume24hUsd', 'turnover24h', 'avg7dTurnoverUsd'],
    },
    {
      title: 'Movement Range',
      note: 'Latest candle: (high-low)/open; may be forming. Equal rounded values can be normal.',
      keys: ['range.1m', 'range.5m', 'range.15m', 'range.30m', 'range.1h', 'range.4h', 'range.1d', 'range.1w', 'range.1mo'],
    },
    {
      title: 'Financing',
      note: 'Carry costs',
      keys: ['financing.longRate', 'financing.shortRate', 'financing.financingDaysOfWeek'],
    },
  ];

  const TRADE_COLUMNS = [
    { key: 'trade_number', label: '#', kind: 'text' },
    { key: 'close_time', label: 'Close Time', kind: 'date' },
    { key: 'account_label', label: 'Account', kind: 'text' },
    { key: 'side', label: 'Side', kind: 'text' },
    { key: 'qty', label: 'Qty', kind: 'number' },
    { key: 'entry_price', label: 'Entry', kind: 'price' },
    { key: 'exit_price', label: 'Exit', kind: 'price' },
    { key: 'stop_loss', label: 'Stop', kind: 'price' },
    { key: 'take_profit', label: 'Target', kind: 'price' },
    { key: 'net_profit', label: 'Net P/L', kind: 'money' },
    { key: 'result_pct', label: 'Result %', kind: 'pct' },
    { key: 'r_multiple', label: 'R', kind: 'r' },
    { key: 'balance_after_trade', label: 'Balance After', kind: 'money' },
    { key: 'setup', label: 'Setup', kind: 'text' },
    { key: 'timeframe', label: 'TF', kind: 'text' },
    { key: 'notes', label: 'Notes', kind: 'text' },
  ];

  function ensureRuntimeStyles() {
    if (document.getElementById('instrument-lookup-runtime-css')) return;
    const style = document.createElement('style');
    style.id = 'instrument-lookup-runtime-css';
    style.textContent = `
      .panel { background:#f8fafc !important; border:1px solid #cfd8e3 !important; border-radius:8px !important; color:#0f172a !important; overflow:hidden !important; padding:0 !important; }
      .panel-head { display:flex; gap:0.75rem; align-items:flex-start; justify-content:space-between; padding:0.85rem 1rem; background:#eaf2f8; border-bottom:1px solid #cfd8e3; }
      .spec-export-actions { display:flex; flex-wrap:wrap; gap:0.4rem; align-items:center; min-width:0; max-width:100%; }
      #spec-png-status { overflow-wrap:anywhere; color:#64748b; font-size:0.82rem; }
      #download-spec-png:disabled { opacity:0.55; cursor:default; }
      .panel h2 { margin:0 !important; font-size:1rem !important; color:#0f172a !important; }
      .panel-note { color:#64748b; font-size:0.82rem; margin-top:0.2rem; }
      .panel-body { padding:0.85rem; }
      .spec-section, .journal-section { border:1px solid #cfd8e3; border-radius:6px; overflow:hidden; background:#ffffff; margin-bottom:0.75rem; }
      .section-title { display:flex; align-items:center; justify-content:space-between; gap:0.75rem; padding:0.55rem 0.7rem; background:#eaf2f8; border-bottom:1px solid #cfd8e3; font-weight:800; color:#0f172a; }
      .section-subtitle { color:#64748b; font-size:0.78rem; font-weight:700; }
      .spec-row, .metric-row { display:grid; grid-template-columns:minmax(130px, 0.9fr) minmax(0, 1.1fr); min-height:42px; border-bottom:1px solid #e5e7eb; }
      .spec-row:last-child, .metric-row:last-child { border-bottom:0; }
      .spec-label, .metric-label { padding:0.55rem 0.7rem; font-weight:750; color:#1f2937; background:#fbfdff; border-right:1px solid #e5e7eb; overflow-wrap:anywhere; }
      .spec-value, .metric-value { padding:0.55rem 0.7rem; font-weight:760; color:#0f172a; overflow-wrap:anywhere; min-width:0; }
      .btc-reference-row { display:block; margin-top:0.2rem; color:#64748b; font-size:0.8rem; font-weight:700; }
      .journal-overview { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:0.65rem; margin-bottom:0.8rem; }
      .stat-card { border:1px solid #cfd8e3; border-radius:6px; background:#ffffff; padding:0.65rem 0.7rem; min-width:0; border-left:4px solid #94a3b8; }
      .stat-card.positive { border-left-color:#22c55e; background:#dcfce7; color:#166534; }
      .stat-card.negative { border-left-color:#ef4444; background:#fee2e2; color:#991b1b; }
      .stat-card.neutral { border-left-color:#3b82f6; }
      .stat-label { color:#64748b; font-size:0.76rem; font-weight:800; text-transform:uppercase; letter-spacing:0; }
      .stat-value { margin-top:0.25rem; font-size:1.25rem; font-weight:900; overflow-wrap:anywhere; }
      .journal-columns { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0.75rem; align-items:start; }
      .wide-section { grid-column:1 / -1; }
      .metric-row.three { grid-template-columns:minmax(120px, 0.9fr) minmax(0, 1fr) minmax(0, 1fr); }
      .metric-head { background:#eaf2f8; font-weight:850; color:#0f172a; }
      .metric-value.positive, td.positive { background:#dcfce7; color:#166534; }
      .metric-value.negative, td.negative { background:#fee2e2; color:#991b1b; }
      .empty-state { padding:0.85rem; color:#64748b; background:#ffffff; border:1px dashed #cfd8e3; border-radius:6px; }
      .trade-block { margin-top:1rem; }
      .recent-trades-panel { margin-top:1rem; }
      .recent-trades-panel .section-title { margin:0; }
      .trade-scroll-top { overflow-x:auto; overflow-y:hidden; height:16px; margin:0.85rem 0.85rem 0.55rem; border:1px solid #cfd8e3; border-radius:6px; background:#ffffff; }
      .trade-scroll-spacer { height:1px; min-width:100%; }
      .table-wrap { overflow-x:auto; border-radius:6px; border:1px solid #cfd8e3; background:#ffffff; }
      .recent-trades-panel .table-wrap { margin:0 0.85rem 0.85rem; }
      #trade-table { min-width:960px !important; }
      #trade-table th, #trade-table td { color:#0f172a; border-bottom:1px solid #e5e7eb; border-right:1px solid #edf2f7; }
      #trade-table th { background:#eaf2f8; font-weight:850; }
      td.numeric, th.numeric { text-align:right; font-variant-numeric:tabular-nums; }
      td.notes { max-width:240px; white-space:normal; }
      #journal-status { color:#64748b !important; margin:0; font-weight:750; text-align:right; }
      @media (max-width:980px) { .journal-columns { grid-template-columns:1fr; } .panel-head { flex-direction:column; } #journal-status { text-align:left; } }
    `;
    document.head.appendChild(style);
  }

  function recentTradesMarkup() {
    return [
      '<section class="panel trade-block recent-trades-panel" id="recent-trades-section">',
      '<div class="trade-scroll-top" id="trade-scroll-top" aria-hidden="true"><div class="trade-scroll-spacer" id="trade-scroll-spacer"></div></div>',
      '<div class="section-title">Recent Trades <span class="section-subtitle">Latest first</span></div>',
      '<div class="table-wrap" id="trade-table-wrap"><table id="trade-table"><thead id="trade-head"></thead><tbody id="trade-body"></tbody></table></div>',
      '</section>',
    ].join('');
  }

  function refreshTradeElements() {
    recentTradesSection = document.getElementById('recent-trades-section');
    tradeScrollTop = document.getElementById('trade-scroll-top');
    tradeScrollSpacer = document.getElementById('trade-scroll-spacer');
    tradeTableWrap = document.getElementById('trade-table-wrap');
    tradeHead = document.getElementById('trade-head');
    tradeBody = document.getElementById('trade-body');
  }

  function ensureRecentTradesSection(journalPanel) {
    refreshTradeElements();
    if (recentTradesSection) return;
    if (!journalPanel || typeof document.createElement !== 'function') return;
    const holder = document.createElement('div');
    holder.innerHTML = recentTradesMarkup();
    const section = holder.firstElementChild;
    if (!section) return;
    const layout = typeof document.querySelector === 'function' ? document.querySelector('.layout') : null;
    if (layout?.parentNode && typeof layout.parentNode.insertBefore === 'function') {
      layout.parentNode.insertBefore(section, layout.nextSibling);
    } else if (journalPanel.parentNode && typeof journalPanel.parentNode.appendChild === 'function') {
      journalPanel.parentNode.appendChild(section);
    }
    refreshTradeElements();
  }

  function syncTradeScroll(source) {
    if (!tradeScrollTop || !tradeTableWrap || tradeScrollSyncing) return;
    tradeScrollSyncing = true;
    if (source === 'top') {
      tradeTableWrap.scrollLeft = tradeScrollTop.scrollLeft;
    } else {
      tradeScrollTop.scrollLeft = tradeTableWrap.scrollLeft;
    }
    tradeScrollSyncing = false;
  }

  function updateTradeTopScrollbar() {
    refreshTradeElements();
    if (!tradeScrollTop || !tradeScrollSpacer || !tradeTableWrap) return;
    const table = document.getElementById('trade-table');
    const scrollWidth = Math.max(
      Number(table?.scrollWidth || 0),
      Number(tradeTableWrap.scrollWidth || 0),
      Number(tradeTableWrap.clientWidth || 0),
    );
    tradeScrollSpacer.style.width = `${Math.max(scrollWidth, 1)}px`;
    syncTradeScroll('table');
  }

  function scheduleTradeTopScrollbarUpdate() {
    const raf = window.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    raf(updateTradeTopScrollbar);
  }

  function bindTradeScrollSync() {
    refreshTradeElements();
    if (!tradeScrollTop || !tradeTableWrap) return;
    if (!tradeScrollTop.dataset.syncBound) {
      tradeScrollTop.addEventListener('scroll', () => syncTradeScroll('top'));
      tradeScrollTop.dataset.syncBound = '1';
    }
    if (!tradeTableWrap.dataset.syncBound) {
      tradeTableWrap.addEventListener('scroll', () => syncTradeScroll('table'));
      tradeTableWrap.dataset.syncBound = '1';
    }
    if (!tradeScrollResizeObserver && typeof window.ResizeObserver === 'function') {
      tradeScrollResizeObserver = new window.ResizeObserver(updateTradeTopScrollbar);
      tradeScrollResizeObserver.observe(tradeTableWrap);
      const table = document.getElementById('trade-table');
      if (table) tradeScrollResizeObserver.observe(table);
    } else if (!tradeScrollResizeObserver && !tradeScrollResizeFallbackBound && typeof window.addEventListener === 'function') {
      window.addEventListener('resize', scheduleTradeTopScrollbarUpdate);
      tradeScrollResizeFallbackBound = true;
    }
    updateTradeTopScrollbar();
  }

  function upgradeLegacyMarkup() {
    ensureRuntimeStyles();
    const specsPanel = rows?.closest('section.panel');
    if (specsPanel && rows.tagName === 'TBODY') {
      specsPanel.innerHTML = [
        '<div class="panel-head"><div><h2>Instrument Specs</h2><div class="panel-note">Live market context and movement ranges.</div></div></div>',
        '<div class="panel-body"><div id="rows"></div></div>',
      ].join('');
      rows = document.getElementById('rows');
    }
    ensurePngControl(specsPanel);
    const journalPanel = journalMetrics?.closest('section.panel');
    if (journalPanel && !journalPanel.querySelector('.panel-head')) {
      journalPanel.innerHTML = [
        '<div class="panel-head"><div><h2>Journal Stats</h2><div class="panel-note">Filtered to this instrument, excluding test trades.</div></div><div id="journal-status"></div></div>',
        '<div class="panel-body"><div id="journal-metrics"></div></div>',
      ].join('');
      journalStatus = document.getElementById('journal-status');
      journalMetrics = document.getElementById('journal-metrics');
    }
    ensureRecentTradesSection(journalPanel);
    bindTradeScrollSync();
  }

  function ensurePngControl(panel) {
    if (!panel) return;
    pngButton = document.getElementById('download-spec-png');
    if (!pngButton) {
      const head = panel.querySelector('.panel-head');
      if (!head) return;
      const actions = document.createElement('div');
      actions.className = 'spec-export-actions';
      actions.innerHTML = '<button id="download-spec-png" type="button" disabled>Download PNG</button><span id="spec-png-status" role="status" aria-live="polite"></span>';
      head.appendChild(actions);
      pngButton = document.getElementById('download-spec-png');
    }
    pngStatus = document.getElementById('spec-png-status');
    pngButton?.addEventListener('click', downloadSpecsPng);
    updatePngControl();
  }

  function updatePngControl(message = '') {
    if (pngButton) pngButton.disabled = !exportSnapshot || !!exportJob;
    if (pngStatus) pngStatus.textContent = message;
  }

  function invalidatePng() {
    exportSnapshot = null;
    if (exportJob) {
      exportJob.canvas.width = exportJob.canvas.height = 0;
      exportJob.canvas.remove();
      exportJob = null;
    }
    updatePngControl();
  }

  function specWarnings(specs) {
    const warnings = Array.isArray(specs?._spec_warnings) ? specs._spec_warnings : [];
    return warnings.length ? `Some instrument specs could not be loaded: ${warnings.map((w) => `${w.field || 'spec'} ${w.symbol || ''}`.trim()).join(', ')}` : '';
  }

  function snapshotStamp(date) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Australia/Brisbane', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(date).map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}_${parts.hour}${parts.minute}${parts.second}`;
  }

  // Fixed readable columns; height grows from measured wrapped text, independent of viewport.
  function drawSpecsTable(canvas, snapshot) {
    const width = 1100, margin = 28, labelWidth = 350, padding = 14, lineHeight = 28;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas unavailable');
    ctx.font = '20px sans-serif';
    function wrap(text, maxWidth) {
      const lines = [];
      for (const paragraph of String(text).split('\n')) {
        let line = '';
        for (const word of paragraph.split(/\s+/)) {
          if (line && ctx.measureText(`${line} ${word}`).width <= maxWidth) {
            line += ` ${word}`;
            continue;
          }
          if (line) lines.push(line);
          line = '';
          for (const char of word) {
            if (line && ctx.measureText(line + char).width > maxWidth) {
              lines.push(line);
              line = '';
            }
            line += char;
          }
        }
        lines.push(line);
      }
      return lines;
    }
    const blocks = [];
    function block(label, value, heading = false) {
      const left = wrap(label, (value === null ? width - 2 * margin : labelWidth) - 2 * padding);
      const right = value === null ? [] : wrap(value, width - 2 * margin - labelWidth - 2 * padding);
      blocks.push({ left, right, heading, height: Math.max(left.length, right.length) * lineHeight + 2 * padding });
    }
    block(`Instrument Specs: ${snapshot.symbol}`, null, true);
    block(`Lookup snapshot: ${snapshot.stamp.replace('_', ' ')} (Brisbane)`, null);
    if (snapshot.source) block(snapshot.source, null);
    if (snapshot.warning) block(snapshot.warning, null);
    snapshot.sections.forEach((section) => {
      block(`${section.title}\n${section.note}`, null, true);
      section.rows.forEach((row) => block(row.label, row.value + (row.reference ? `\nBTC reference: ${row.reference}` : '')));
    });
    canvas.width = width;
    canvas.height = margin * 2 + blocks.reduce((total, b) => total + b.height, 0);
    ctx.font = '20px sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, canvas.height);
    let y = margin;
    blocks.forEach((b) => {
      ctx.fillStyle = b.heading ? '#eaf2f8' : '#ffffff';
      ctx.fillRect(margin, y, width - 2 * margin, b.height);
      ctx.strokeStyle = '#cfd8e3';
      ctx.strokeRect(margin, y, width - 2 * margin, b.height);
      ctx.fillStyle = '#0f172a';
      b.left.forEach((line, i) => ctx.fillText(line, margin + padding, y + padding + i * lineHeight));
      b.right.forEach((line, i) => ctx.fillText(line, margin + labelWidth + padding, y + padding + i * lineHeight));
      if (b.right.length) ctx.strokeRect(margin, y, labelWidth, b.height);
      y += b.height;
    });
  }

  async function downloadSpecsPng() {
    if (!exportSnapshot || exportJob) return;
    const snapshot = exportSnapshot;
    const job = { canvas: document.createElement('canvas') };
    exportJob = job;
    updatePngControl('Creating PNG...');
    let url = null;
    let anchor = null;
    try {
      drawSpecsTable(job.canvas, snapshot);
      const blob = await new Promise((resolve, reject) => {
        job.canvas.toBlob((value) => value ? resolve(value) : reject(new Error('PNG encoding failed')), 'image/png');
      });
      if (exportSnapshot !== snapshot || exportJob !== job) return;
      url = URL.createObjectURL(blob);
      anchor = document.createElement('a');
      anchor.href = url;
      const safeSymbol = snapshot.symbol.replace(/[^a-zA-Z0-9_-]/g, '_');
      anchor.download = `instrument_specs_${safeSymbol}_${snapshot.stamp}.png`;
      document.body.appendChild(anchor);
      anchor.click();
      // Allow the browser to consume the dispatched download before releasing its URL.
      const dispatchedUrl = url;
      setTimeout(() => URL.revokeObjectURL(dispatchedUrl), 1000);
      url = null;
      updatePngControl('PNG downloaded.');
    } catch (_error) {
      if (exportSnapshot === snapshot && exportJob === job) updatePngControl('PNG download failed. Please try again.');
    } finally {
      if (url) URL.revokeObjectURL(url);
      anchor?.remove();
      job.canvas.width = job.canvas.height = 0;
      job.canvas.remove();
      if (exportJob === job) {
        exportJob = null;
        if (pngButton) pngButton.disabled = !exportSnapshot;
      }
    }
  }

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

  function hasValue(value) {
    if (value === null || value === undefined || value === '') return false;
    if (Array.isArray(value)) return value.length > 0;
    return true;
  }

  function numeric(value) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string') return null;
    const cleaned = value.trim().replace(/[$,%\s]/g, '').replace(/,/g, '');
    if (!cleaned) return null;
    const number = Number(cleaned);
    return Number.isFinite(number) ? number : null;
  }

  function isNumericLike(value) {
    if (value === null || value === undefined) return false;
    if (typeof value === 'number') return Number.isFinite(value);
    if (typeof value !== 'string') return false;
    return /^-?\d+(\.\d+)?$/.test(value.trim());
  }

  function isLikelyFxPair(value) {
    const text = String(value || '').trim().toUpperCase();
    const normalized = text.replace(/[^A-Z]/g, '');
    if (!/^[A-Z]{6}$/.test(normalized)) return false;
    return FX_CODES.has(normalized.slice(0, 3)) && FX_CODES.has(normalized.slice(3));
  }

  function setAsset(asset) {
    state.asset = asset === 'fx' ? 'fx' : 'crypto';
    assetToggle?.querySelectorAll('button[data-asset]').forEach((button) => {
      button.classList.toggle('active', button.dataset.asset === state.asset);
    });
  }

  function formatTimestampBrisbane(value) {
    if (!hasValue(value)) return null;
    let date = null;
    if (isNumericLike(value)) {
      const n = Number(value);
      const ms = n < 1e12 ? n * 1000 : n;
      date = new Date(ms);
    } else {
      date = new Date(value);
    }
    if (!date || Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat('en-AU', {
      timeZone: 'Australia/Brisbane',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(date) + ' (Brisbane)';
  }

  function compactNumber(value, decimals = 2) {
    const number = numeric(value);
    if (number === null) return String(value ?? '-');
    const abs = Math.abs(number);
    const clean = (n) => n.toFixed(decimals).replace(/\.00$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
    if (abs >= 1e12) return `${clean(number / 1e12)}T`;
    if (abs >= 1e9) return `${clean(number / 1e9)}B`;
    if (abs >= 1e6) return `${clean(number / 1e6)}M`;
    if (abs >= 1e3) return `${clean(number / 1e3)}K`;
    return clean(number);
  }

  function formatNumber(value, decimals = 2) {
    const number = numeric(value);
    if (number === null) return String(value ?? '-');
    return number.toLocaleString(undefined, { maximumFractionDigits: decimals });
  }

  function formatPercentFromFraction(value, decimals = 2, trimTrailingZeros = true) {
    const number = numeric(value);
    if (number === null) return String(value ?? '-');
    const formatted = (number * 100).toFixed(decimals);
    return `${trimTrailingZeros ? formatted.replace(/0+$/, '').replace(/\.$/, '') : formatted}%`;
  }

  function formatPercentPoints(value, decimals = 2) {
    if (typeof value === 'string' && value.trim().endsWith('%')) return value.trim();
    const number = numeric(value);
    if (number === null) return '-';
    return `${number.toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '')}%`;
  }

  function formatDuration(value) {
    const seconds = numeric(value);
    if (seconds === null || seconds < 0) return '-';
    const total = Math.floor(seconds);
    const s = total % 60;
    const m = Math.floor(total / 60) % 60;
    const h = Math.floor(total / 3600) % 24;
    const d = Math.floor(total / 86400);
    if (d > 0) return `${d}d ${h}h ${m}m ${s}s`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function formatMoney(value, currency) {
    const number = numeric(value);
    if (number === null) return '-';
    const code = String(currency || '').trim().toUpperCase();
    const digits = Math.abs(number) >= 1000 ? 2 : 4;
    const text = number.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: digits });
    if (code === 'AUD' || code === 'USD') return `$${text} ${code}`;
    return code ? `${text} ${code}` : text;
  }

  function formatObjectValue(value) {
    if (Array.isArray(value)) {
      return value.map((item) => {
        if (!item || typeof item !== 'object') return String(item ?? '');
        const day = item.dayOfWeek || item.day || item.weekday || '';
        const charged = item.daysCharged || item.days || '';
        return [day, charged ? `${charged} days` : ''].filter(Boolean).join(': ');
      }).filter(Boolean).join(', ') || '-';
    }
    if (value && typeof value === 'object') {
      return Object.entries(value)
        .filter(([, item]) => hasValue(item) && typeof item !== 'object')
        .map(([key, item]) => `${key}: ${item}`)
        .join(', ') || '-';
    }
    return String(value ?? '-');
  }

  function formatSpecValue(key, value) {
    if (key === 'launchTime' || key === 'nextFundingTime' || /(time|timestamp)$/i.test(key)) {
      const formatted = formatTimestampBrisbane(value);
      if (formatted) return formatted;
    }
    if (key === 'fundingRate' || key === 'marginRate' || key.endsWith('.longRate') || key.endsWith('.shortRate')) {
      return formatPercentFromFraction(value, 4);
    }
    if (key.startsWith('range.')) return formatPercentFromFraction(value, 2, false);
    if (/^(volume24hUsd|turnover24h|openInterestValue|avg7dTurnoverUsd)$/i.test(key)) return `$${compactNumber(value)}`;
    if (/^(minLeverage|maxLeverage)$/.test(key)) {
      const number = numeric(value);
      return number === null ? String(value ?? '-') : `${formatNumber(number, 2)}x`;
    }
    if (key === 'leverageStep') {
      const number = numeric(value);
      return number === null ? String(value ?? '-') : `${formatNumber(number, 4)}x`;
    }
    if (typeof value === 'object' && value !== null) return formatObjectValue(value);
    if (isNumericLike(value)) return formatNumber(value, 10);
    return String(value ?? '-');
  }

  function formatJournalValue(value, kind, currency) {
    if (!hasValue(value)) return '-';
    if (kind === 'pct') return formatPercentPoints(value);
    if (kind === 'pctFraction') return formatPercentFromFraction(value);
    if (kind === 'duration') return formatDuration(value);
    if (kind === 'money') return formatMoney(value, currency);
    if (kind === 'r') {
      const number = numeric(value);
      return number === null ? String(value ?? '-') : `${number.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')}R`;
    }
    if (kind === 'count') {
      if (value && typeof value === 'object' && numeric(value.trade_count) !== null) {
        return Number(value.trade_count).toLocaleString(undefined, { maximumFractionDigits: 0 });
      }
      const number = numeric(value);
      return number === null ? String(value ?? '-') : number.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    if (kind === 'date') return formatTimestampBrisbane(value) || String(value ?? '-');
    if (kind === 'price' || kind === 'number') return formatNumber(value, 10);
    if (typeof value === 'object') return formatObjectValue(value);
    return String(value);
  }

  function toneClass(value, positiveIsGood = true) {
    const number = numeric(value);
    if (number === null || number === 0) return '';
    if (number > 0) return positiveIsGood ? 'positive' : 'negative';
    return positiveIsGood ? 'negative' : 'positive';
  }

  function firstValue(...values) {
    for (const value of values) {
      if (hasValue(value)) return value;
    }
    return null;
  }

  function streakCount(value) {
    if (value && typeof value === 'object') return firstValue(value.trade_count, value.count, value.trades);
    return value;
  }

  function inferCurrency(payload) {
    const trades = Array.isArray(payload?.trades) ? payload.trades : [];
    for (const trade of trades) {
      const value = trade?.currency || trade?.account_currency || trade?.result_currency || trade?.quote_currency;
      if (value) return String(value).toUpperCase();
    }
    return state.asset === 'fx' ? 'AUD' : 'USDT';
  }

  function renderMarketDataSource(specs) {
    if (!rows?.parentNode) return '';
    let indicator = document.getElementById('instrument-data-source');
    if (!indicator) {
      indicator = document.createElement('div');
      indicator.id = 'instrument-data-source';
      indicator.className = 'panel-note';
      indicator.setAttribute('role', 'status');
      indicator.style.marginBottom = '0.65rem';
      rows.parentNode.insertBefore(indicator, rows);
    }
    let label = '';
    if (specs?.source === 'bybit') {
      label = 'Data source: Bybit';
    } else if (specs?.source === 'binance_usdm') {
      label = specs?._source_notice
        ? `Data source: Binance USD-M fallback — ${String(specs._source_notice)}`
        : 'Data source: Binance USD-M';
    } else if (specs?.source === 'oanda') {
      label = 'Data source: OANDA';
    }
    indicator.textContent = label;
    indicator.hidden = !label;
    return label;
  }

  function renderSpecs(specs) {
    if (!rows) return;
    const source = specs && typeof specs === 'object' ? specs : {};
    const sourceLabel = renderMarketDataSource(source);
    const sections = [];
    const btcRef = source._btc_reference && typeof source._btc_reference === 'object' ? source._btc_reference : null;
    const usableKeys = Object.keys(source).filter((key) => {
      if (HIDE_SPEC_FIELDS.has(key) || !hasValue(source[key])) return false;
      const value = source[key];
      return typeof value !== 'object' || key === 'financing.financingDaysOfWeek';
    });
    const used = new Set();

    const sectionHtml = SPEC_SECTIONS.map((section) => {
      const exportRows = [];
      const body = section.keys
        .filter((key) => usableKeys.includes(key))
        .map((key) => {
          used.add(key);
          return renderSpecRow(key, source[key], btcRef, exportRows);
        })
        .join('');
      if (!body) return '';
      sections.push({ title: section.title, note: section.note, rows: exportRows });
      return `<div class="spec-section"><div class="section-title">${escapeHtml(section.title)}<span class="section-subtitle">${escapeHtml(section.note)}</span></div>${body}</div>`;
    }).join('');

    const extraRows = [];
    const extras = usableKeys
      .filter((key) => !used.has(key))
      .sort()
      .map((key) => renderSpecRow(key, source[key], btcRef, extraRows))
      .join('');
    const extraHtml = extras
      ? `<div class="spec-section"><div class="section-title">Other Specs<span class="section-subtitle">Additional broker fields</span></div>${extras}</div>`
      : '';

    if (extraRows.length) sections.push({ title: 'Other Specs', note: 'Additional broker fields', rows: extraRows });
    rows.innerHTML = sectionHtml || extraHtml
      ? sectionHtml + extraHtml
      : '<div class="empty-state">No instrument specs loaded yet.</div>';
    return { sections, source: sourceLabel, warning: specWarnings(source) };
  }

  function renderSpecRow(key, value, btcRef, exportRows) {
    const formatted = formatSpecValue(key, value);
    const reference = btcRef && hasValue(btcRef[key]) ? formatSpecValue(key, btcRef[key]) : '';
    exportRows.push({ label: SPEC_LABELS[key] || key, value: formatted, reference });
    const btcLine = btcRef && hasValue(btcRef[key])
      ? `<span class="btc-reference-row">BTC reference: ${escapeHtml(reference)}</span>`
      : '';
    return [
      '<div class="spec-row">',
      `<div class="spec-label">${escapeHtml(SPEC_LABELS[key] || key)}</div>`,
      `<div class="spec-value">${escapeHtml(formatted)}${btcLine}</div>`,
      '</div>',
    ].join('');
  }

  function renderOverviewCards(payload, currency) {
    const s = payload.stats || {};
    const m = payload.metrics || {};
    const cards = [
      { label: 'Trades', value: firstValue(s.total_trades, m.trades), kind: 'count', tone: 'neutral' },
      { label: 'Wins', value: firstValue(s.wins, m.wins), kind: 'count', tone: 'positive' },
      { label: 'Losses', value: firstValue(s.losses, m.losses), kind: 'count', tone: 'negative' },
      { label: 'Win Rate', value: firstValue(s.win_rate, m.win_rate_pct), kind: 'pct', tone: 'neutral' },
      { label: 'Net P/L', value: m.net_profit_total, kind: 'money', tone: toneClass(m.net_profit_total) || 'neutral' },
    ];
    return `<div class="journal-overview">${cards.map((card) => (
      `<div class="stat-card ${escapeHtml(card.tone || 'neutral')}"><div class="stat-label">${escapeHtml(card.label)}</div><div class="stat-value">${escapeHtml(formatJournalValue(card.value, card.kind, currency))}</div></div>`
    )).join('')}</div>`;
  }

  function renderMetricSection(title, rowsIn, currency, options = {}) {
    const body = rowsIn
      .filter((row) => row && row.length >= 3)
      .map(([label, value, kind, tone]) => {
        const autoTone = options.autoTone !== false;
        const cls = tone || (autoTone && (kind === 'money' || kind === 'pct' || kind === 'r') ? toneClass(value) : '');
        return [
          '<div class="metric-row">',
          `<div class="metric-label">${escapeHtml(label)}</div>`,
          `<div class="metric-value ${escapeHtml(cls)}">${escapeHtml(formatJournalValue(value, kind, currency))}</div>`,
          '</div>',
        ].join('');
      })
      .join('');
    if (!body) return '';
    const wide = options.wide ? ' wide-section' : '';
    const subtitle = options.note ? `<span class="section-subtitle">${escapeHtml(options.note)}</span>` : '';
    return `<div class="journal-section${wide}"><div class="section-title">${escapeHtml(title)}${subtitle}</div>${body}</div>`;
  }

  function renderSplitSection(title, leftLabel, rightLabel, rowsIn, currency) {
    const body = rowsIn.map(([label, left, right, kind, leftTone, rightTone]) => (
      `<div class="metric-row three"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value ${escapeHtml(leftTone || '')}">${escapeHtml(formatJournalValue(left, kind, currency))}</div><div class="metric-value ${escapeHtml(rightTone || '')}">${escapeHtml(formatJournalValue(right, kind, currency))}</div></div>`
    )).join('');
    return [
      '<div class="journal-section">',
      `<div class="section-title">${escapeHtml(title)}</div>`,
      '<div class="metric-row three metric-head">',
      '<div class="metric-label">Metric</div>',
      `<div class="metric-value">${escapeHtml(leftLabel)}</div>`,
      `<div class="metric-value">${escapeHtml(rightLabel)}</div>`,
      '</div>',
      body,
      '</div>',
    ].join('');
  }

  function latestPeriodRows(periodReports) {
    const years = periodReports && typeof periodReports === 'object' && periodReports.years && typeof periodReports.years === 'object'
      ? periodReports.years
      : {};
    const yearKey = Object.keys(years).sort((a, b) => Number(b) - Number(a))[0];
    if (!yearKey) return null;
    const report = years[yearKey] || {};
    const byMarket = report.groups && report.groups.by_market && typeof report.groups.by_market === 'object'
      ? report.groups.by_market
      : {};
    const totals = byMarket.overall || report.totals || {};
    return {
      title: `${yearKey} Snapshot`,
      rows: [
        ['Trades', totals.trades, 'count'],
        ['Wins', totals.wins, 'count', 'positive'],
        ['Losses', totals.losses, 'count', 'negative'],
        ['Win rate', totals.win_rate_pct, 'pct'],
        ['Net P/L', totals.net_profit_total, 'money'],
        ['Avg result %', totals.avg_result_pct, 'pct'],
      ],
    };
  }

  function renderJournal(payload) {
    if (!journalMetrics || !tradeHead || !tradeBody) return;
    const canonical = payload?.canonical_symbol || '';
    const status = payload?.status || '';
    if (status === 'loading') {
      if (journalStatus) journalStatus.textContent = 'Loading journal...';
      journalMetrics.innerHTML = '<div class="empty-state">Loading journal stats.</div>';
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      updateTradeTopScrollbar();
      return;
    }
    if (status === 'no_data') {
      if (journalStatus) journalStatus.textContent = canonical ? `No journal rows for ${canonical}.` : 'No journal rows.';
      journalMetrics.innerHTML = '<div class="empty-state">No matching non-test trades were found in the journal.</div>';
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      updateTradeTopScrollbar();
      return;
    }
    if (status !== 'ok') {
      if (journalStatus) journalStatus.textContent = 'Journal lookup did not return data.';
      journalMetrics.innerHTML = '<div class="empty-state">Journal stats are unavailable for this lookup.</div>';
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      updateTradeTopScrollbar();
      return;
    }

    const trades = Array.isArray(payload.trades) ? payload.trades : [];
    const s = payload.stats || {};
    const m = payload.metrics || {};
    const currency = inferCurrency(payload);
    if (journalStatus) {
      journalStatus.textContent = `${canonical || 'Symbol'} - ${trades.length} journal row${trades.length === 1 ? '' : 's'}`;
    }

    const period = latestPeriodRows(payload.period_reports);
    journalMetrics.innerHTML = [
      renderOverviewCards(payload, currency),
      '<div class="journal-columns">',
      renderSplitSection('Direction', 'Long', 'Short', [
        ['Trades', firstValue(s.long_trades, m.long_trades), firstValue(s.short_trades, m.short_trades), 'count'],
        ['Wins', firstValue(s.long_wins, m.long_wins), firstValue(s.short_wins, m.short_wins), 'count', 'positive', 'positive'],
        ['Losses', firstValue(s.long_losses, m.long_losses), firstValue(s.short_losses, m.short_losses), 'count', 'negative', 'negative'],
      ], currency),
      renderMetricSection('Performance', [
        ['Net P/L', m.net_profit_total, 'money'],
        ['Gross gain', m.gross_gain, 'money', 'positive'],
        ['Gross loss', m.gross_loss, 'money', 'negative'],
        ['Avg result %', m.avg_result_pct, 'pct'],
        ['Net R multiple', m.net_r_multiple, 'r'],
        ['Avg R multiple', m.avg_r_multiple, 'r'],
      ], currency),
      renderMetricSection('Distances', [
        ['Avg stop %', firstValue(s.avg_stop_distance, m.avg_stop_pct), 'pct'],
        ['Min stop %', m.min_stop_pct, 'pct'],
        ['Max stop %', m.max_stop_pct, 'pct'],
        ['Avg target %', firstValue(s.avg_target_distance, m.avg_target_pct), 'pct'],
        ['Min target %', m.min_target_pct, 'pct'],
        ['Max target %', m.max_target_pct, 'pct'],
      ], currency, { autoTone: false }),
      renderSplitSection('Winners / Losers', 'Winners', 'Losers', [
        ['Avg result %', m.avg_result_pct_winners, m.avg_result_pct_losers, 'pct', 'positive', 'negative'],
        ['Avg R', m.avg_r_multiple_winners, m.avg_r_multiple_losers, 'r', 'positive', 'negative'],
        ['Avg stop %', m.avg_stop_pct_winners, m.avg_stop_pct_losers, 'pct'],
        ['Avg target %', m.avg_target_pct_winners, m.avg_target_pct_losers, 'pct'],
      ], currency),
      renderMetricSection('Timing & Drawdown', [
        ['Last trade', s.last_trade_timestamp, 'date'],
        ['Avg duration', firstValue(s.avg_trade_duration, m.avg_duration_seconds), 'duration'],
        ['Shortest duration', firstValue(m.shortest_duration_seconds, m.min_trade_duration_seconds, m.min_duration_seconds), 'duration'],
        ['Longest duration', firstValue(m.longest_duration_seconds, m.max_trade_duration_seconds, m.max_duration_seconds), 'duration'],
        ['Best win streak', firstValue(m.longest_winning_streak_count, streakCount(m.longest_winning_streak), m.winning_streak), 'count', 'positive'],
        ['Worst losing streak', firstValue(m.longest_losing_streak_count, streakCount(m.longest_losing_streak), m.losing_streak), 'count', 'negative'],
      ], currency),
      period ? renderMetricSection(period.title, period.rows, currency, { wide: true, note: 'This instrument only' }) : '',
      '</div>',
    ].join('');
    renderTradeTable(trades, currency);
  }

  function renderTradeTable(trades, currency) {
    if (!tradeHead || !tradeBody) return;
    if (!trades.length) {
      tradeHead.innerHTML = '';
      tradeBody.innerHTML = '';
      updateTradeTopScrollbar();
      return;
    }
    const columns = TRADE_COLUMNS.filter((column) => {
      if (['close_time', 'side', 'net_profit', 'result_pct'].includes(column.key)) return true;
      return trades.some((trade) => hasValue(trade?.[column.key]));
    });
    tradeHead.innerHTML = `<tr>${columns.map((column) => `<th class="${column.kind === 'number' || column.kind === 'money' || column.kind === 'pct' || column.kind === 'r' || column.kind === 'price' ? 'numeric' : ''}">${escapeHtml(column.label)}</th>`).join('')}</tr>`;
    tradeBody.innerHTML = trades.map((trade) => (
      `<tr>${columns.map((column) => {
        const value = trade?.[column.key];
        const cls = [
          column.kind === 'number' || column.kind === 'money' || column.kind === 'pct' || column.kind === 'r' || column.kind === 'price' ? 'numeric' : '',
          column.key === 'notes' ? 'notes' : '',
          column.key === 'net_profit' || column.key === 'result_pct' || column.key === 'r_multiple' ? toneClass(value) : '',
        ].filter(Boolean).join(' ');
        return `<td class="${escapeHtml(cls)}">${escapeHtml(formatJournalValue(value, column.kind, currency))}</td>`;
      }).join('')}</tr>`
    )).join('');
    bindTradeScrollSync();
    updateTradeTopScrollbar();
    scheduleTradeTopScrollbarUpdate();
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

  async function load() {
    const raw = String(qInput?.value || '').trim();
    if (!raw) return;
    const generation = ++loadGeneration;
    invalidatePng();
    setErr('Loading...');
    renderSpecs({});
    renderJournal({ status: 'loading', trades: [] });
    const detectedAsset = isLikelyFxPair(raw) ? 'fx' : 'crypto';
    setAsset(detectedAsset);
    const prefer = detectedAsset === 'fx' ? '&prefer=oanda' : '&prefer=bybit';
    const [specsResult] = await Promise.allSettled([
      fetchJson(`/api/instrument-specs?query=${encodeURIComponent(raw)}${prefer}`),
    ]);
    if (generation !== loadGeneration) return;

    const errors = [];
    let specs = null;
    let resolved = raw;
    if (specsResult.status === 'fulfilled') {
      specs = specsResult.value || {};
      resolved = String(specs.resolved_symbol || raw).trim() || raw;
      if (qInput && resolved !== raw) qInput.value = resolved;
      const model = renderSpecs(specs);
      if (model?.sections.length && specs.resolved_symbol) {
        exportSnapshot = { ...model, symbol: resolved, stamp: snapshotStamp(new Date()) };
        updatePngControl();
      }
    } else {
      renderSpecs({});
      errors.push(`Instrument specs failed: ${specsResult.reason?.message || String(specsResult.reason)}`);
    }

    const [journalResult] = await Promise.allSettled([
      fetchJson(`/api/calculator/journal-summary?asset=${encodeURIComponent(detectedAsset)}&symbol=${encodeURIComponent(resolved)}`),
    ]);
    if (generation !== loadGeneration) return;
    if (journalResult.status === 'fulfilled') {
      renderJournal(journalResult.value || { status: 'error', trades: [] });
    } else {
      renderJournal({ status: 'error', trades: [] });
      errors.push(`Journal stats failed: ${journalResult.reason?.message || String(journalResult.reason)}`);
    }

    history.replaceState(null, '', `/instrument-lookup?q=${encodeURIComponent(resolved)}`);
    const warningText = specWarnings(specs);
    setErr([...errors, warningText].filter(Boolean).join(' | '));
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

  setAsset('crypto');
  upgradeLegacyMarkup();
  const initial = (qs.get('q') || '').trim();
  if (qInput) qInput.value = initial;
  if (initial) load();
  else {
    renderSpecs({});
    renderJournal({ status: 'empty', trades: [] });
  }
})();
