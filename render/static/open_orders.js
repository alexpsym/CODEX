(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const statusBadge = document.getElementById('open-orders-status');
  const table = document.getElementById('open-orders-table');
  const tbody = table?.querySelector('tbody');
  const emptyState = document.getElementById('open-orders-empty');
  const errorsBox = document.getElementById('open-orders-errors');
  const errorsList = errorsBox?.querySelector('ul');
  const warningsBox = document.getElementById('open-orders-warnings');
  const warningsList = warningsBox?.querySelector('ul');
  const pendingManualActions = new Map();
  const VERIFY_SCHEDULE_MS = [300, 700, 1500, 3000, 3000];
  let refreshInFlight = null; let hasData = false; let knownVersion = null; let versionPollTimer = null; const POLL_MS = 2500;
  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const setBadge = (message) => { if (statusBadge) statusBadge.textContent = message; };
  const rowKey = (row) => [row?.broker,row?.account,row?.account_id,row?.category,row?.instrument,row?.type,row?.id,row?.position_idx,row?.order_link_id].map((v)=>String(v ?? '').trim().toLowerCase()).join('|');
  const parseApiError = (data, response) => {
    const detail = data?.detail;
    const fallback = `HTTP ${response.status} ${sanitizeSourceText(response.statusText) || 'request failed'}`;
    if (typeof detail === 'string' && detail.trim()) return safeDiagnostic(detail, fallback);
    if (detail && typeof detail === 'object') return safeDiagnostic(detail.message || detail.error || JSON.stringify(detail), fallback);
    return safeDiagnostic(`${response.status} ${response.statusText}`, fallback);
  };
  const formatTimestamp=(value)=>{ if(!value) return '—'; const n=Number(value); if(!Number.isNaN(n)){const ms=n<1_000_000_000_000?n*1000:n; const d=new Date(ms); if(!Number.isNaN(d.getTime())) return d.toLocaleString();} const d=new Date(value); return Number.isNaN(d.getTime())?String(value):d.toLocaleString();};
  const sanitizeSourceText=(value)=>String(value??'').replace(/https?:\/\/\S+/gi,'[redacted URL]').replace(/\b(api[_-]?key|api[_-]?secret|secret|signature|x-bapi-api-key|x-bapi-sign)\s*["']?\s*([=:])\s*(?:"[^"]*"|'[^']*'|[^"',\s;&}]+)/gi,'$1$2[redacted]').replace(/\s+/g,' ').trim();
  const safeEndpoint=(value)=>{const raw=String(value??'').trim(); if(!raw) return ''; return sanitizeSourceText(raw.replace(/^https?:\/\/[^/]+/i,'').split(/[?#]/,1)[0]||'unknown');};
  const safeDiagnostic=(value,fallback='')=>{const safe=sanitizeSourceText(value); return (safe||fallback).slice(0,300);};
  const formatSourceErrors=(errors=[])=>Array.isArray(errors)?errors.map((rawEntry)=>{
    const entry=rawEntry&&typeof rawEntry==='object'?rawEntry:{message:rawEntry};
    const broker=sanitizeSourceText(entry.broker||'Source');
    const account=sanitizeSourceText(entry.account||'');
    const category=sanitizeSourceText(entry.category||'');
    const sourceType=sanitizeSourceText(entry.source_type||entry.operation||entry.endpoint_type||'');
    const settlementCoin=sanitizeSourceText(entry.settlement_coin||entry.settleCoin||entry.settle_coin||'');
    const endpoint=safeEndpoint(entry.endpoint||entry.path||'');
    const errorType=sanitizeSourceText(entry.error_type||entry.errorType||'');
    const httpStatus=sanitizeSourceText(entry.http_status??entry.status??'');
    const retCode=sanitizeSourceText(entry.retCode??entry.ret_code??'');
    const retMsg=sanitizeSourceText(entry.retMsg??entry.ret_msg??'');
    let message=sanitizeSourceText(entry.message||entry.error||'').replace(/:+\s*$/,'').trim();
    const diagnostics=[
      httpStatus?`HTTP ${httpStatus}`:'',
      retCode?`retCode=${retCode}`:'',
      retMsg?`retMsg=${retMsg}`:'',
    ].filter(Boolean);
    if(!message) message=errorType?`${errorType} reported without diagnostic text`:'Source request failed without diagnostic details';
    if(diagnostics.length&&!diagnostics.every((part)=>message.includes(part))) message=`${message} (${diagnostics.join(', ')})`;
    const context=[broker,account,category,sourceType,settlementCoin?`settleCoin=${settlementCoin}`:'',endpoint].filter(Boolean).join(' ');
    return context?`${context}: ${message}`:message;
  }).filter(Boolean):[];
  const buildFetchError=(url,status,statusText,bodyText,bodyJson)=>{const detailErrors=bodyJson?.detail?.errors||bodyJson?.errors; const flattened=formatSourceErrors(detailErrors); if(flattened.length) return new Error(flattened.join(' | ')); const endpoint=safeEndpoint(url)||'unknown endpoint'; const httpStatus=safeDiagnostic(status,'unknown'); const parsedBody=bodyJson&&typeof bodyJson==='object'?JSON.stringify(bodyJson):bodyText; const body=safeDiagnostic(parsedBody); const reason=body||safeDiagnostic(statusText,'upstream returned no diagnostic details'); return new Error(`GET ${endpoint} failed: HTTP ${httpStatus} ${reason}`.trim());};
  const fetchJson=async(url)=>{const response=await fetch(url); let bodyText=''; let bodyJson=null; try{bodyText=await response.text(); bodyJson=bodyText?JSON.parse(bodyText):null;}catch(_){bodyJson=null;} if(!response.ok) throw buildFetchError(url,response.status,response.statusText,bodyText,bodyJson); if(bodyJson!==null) return bodyJson; return bodyText?JSON.parse(bodyText):{};};
  const resolveAccountLabel=(row)=>{const parts=[]; const account=String(row?.account||'').trim(); const accountId=String(row?.account_id||'').trim(); const suffix=String(row?.account_label_suffix||'').trim(); if(account) parts.push(account); if(accountId) parts.push(accountId); if(suffix) parts.push(suffix); return parts.join(' · ')||'—';};
  const isActionableRow=(row)=>{if(!row||typeof row!=='object') return false; if(row.parent_id||row.parent_order_id) return false; const status=String(row.status||'').toLowerCase(); if(status.includes('bounce waiting')) return false; const type=String(row.type||'').toLowerCase(); return type==='order'||type==='position'||type==='trade'||type==='webhook';};
  const actionLabelFor=(row)=>{const type=String(row?.type||'').toLowerCase(); if(type==='order'||type==='webhook') return 'Cancel'; if(type==='position'||type==='trade') return 'Close'; return null;};

  const verifyManualAction = async (row, key) => {
    const state = pendingManualActions.get(key); if (!state) return;
    for (let i=0;i<VERIFY_SCHEDULE_MS.length;i+=1){
      await new Promise((r)=>setTimeout(r, VERIFY_SCHEDULE_MS[i]));
      try {
        const payload = await fetch('/api/open-orders/verify-action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ...row, action: state.action, close_response: state.response })});
        const text = await payload.text(); const data = text ? JSON.parse(text) : {};
        if (!payload.ok) throw new Error(parseApiError(data, payload));
        if (data.verified) { state.verifyStatus='verified'; state.message = `${state.action === 'close' ? 'Close':'Cancel'} verified.`; setBadge(state.message); refresh().catch((err)=>setBadge(`Verified; refresh failed: ${safeDiagnostic(err?.message||err,'request failed without diagnostic details')}`)); return; }
        if (data.still_open && i === VERIFY_SCHEDULE_MS.length - 1) { pendingManualActions.delete(key); setBadge(`${state.action === 'close' ? 'Close':'Cancel'} submitted but still appears open after verification.`); refresh().catch(()=>{}); return; }
      } catch (err) {
        pendingManualActions.delete(key); setBadge(`Verification failed; refresh required: ${safeDiagnostic(err?.message||err,'request failed without diagnostic details')}`); refresh().catch(()=>{}); return;
      }
    }
  };

  const postClose = async (row, btn) => {
    btn.disabled = true; const prev = btn.textContent; btn.textContent = 'Submitting...';
    const key = rowKey(row); const action = actionLabelFor(row)?.toLowerCase() === 'cancel' ? 'cancel' : 'close';
    pendingManualActions.set(key, { action, submittedAt: Date.now(), broker: row.broker, account: row.account, category: row.category, instrument: row.instrument, id: row.id, verifyStatus: 'submitting', message: 'Submitting...', response: null });
    try {
      const response = await fetch('/api/open-orders/close', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(row) });
      const text = await response.text(); const data = text ? JSON.parse(text) : {};
      if (!response.ok) throw new Error(parseApiError(data, response));
      const state = pendingManualActions.get(key); if (state) { state.verifyStatus = 'accepted_verifying'; state.message = `${action === 'close' ? 'Close' : 'Cancel'} submitted. Verifying...`; state.response = data; }
      setBadge(`${action === 'close' ? 'Close' : 'Cancel'} submitted. Verifying...`);
      render((window.__openOrdersItems || []).filter((it)=>rowKey(it)!==key), window.__openOrdersErrors || [], window.__openOrdersWarnings || []);
      verifyManualAction(row, key);
    } catch (err) {
      pendingManualActions.delete(key); btn.disabled = false; btn.textContent = prev; setBadge(safeDiagnostic(err?.message||err,'Action failed'));
    }
  };
  const renderActionCell=(row,cell,{allowAction=true}={})=>{const label=actionLabelFor(row); if(!allowAction||!label||!isActionableRow(row)){cell.textContent='—';return;} const required=['broker','account','category','instrument','id','type']; const missing=required.some((k)=>!String(row[k]??'').trim()); if(missing){cell.textContent='—';return;} const key=rowKey(row); const state=pendingManualActions.get(key); if(state&&['submitting','accepted_verifying'].includes(state.verifyStatus)){cell.textContent='Verifying...'; return;} const btn=document.createElement('button'); btn.type='button'; btn.className='action-btn'; btn.textContent=label; btn.addEventListener('click',()=>postClose(row,btn)); cell.appendChild(btn);};
  const render=(items,errors=[],warnings=[])=>{window.__openOrdersItems=Array.isArray(items)?items:[]; window.__openOrdersErrors=Array.isArray(errors)?errors:[]; window.__openOrdersWarnings=Array.isArray(warnings)?warnings:[]; if(!tbody) return; tbody.innerHTML=''; hasData=Boolean(items.length); const formattedErrors=formatSourceErrors(errors); const formattedWarnings=formatSourceErrors(warnings); if(errorsBox) errorsBox.style.display=formattedErrors.length?'block':'none'; if(errorsList){errorsList.innerHTML=''; formattedErrors.forEach((text)=>{const li=document.createElement('li'); li.textContent=text; errorsList.appendChild(li);});} if(warningsBox) warningsBox.style.display=formattedWarnings.length?'block':'none'; if(warningsList){warningsList.innerHTML=''; formattedWarnings.forEach((text)=>{const li=document.createElement('li'); li.textContent=text; warningsList.appendChild(li);});} if(!items.length){if(emptyState) emptyState.style.display='block'; return;} if(emptyState) emptyState.style.display='none';
    items.forEach((item)=>{ if (pendingManualActions.has(rowKey(item))) return; const row=document.createElement('tr'); [item.broker,resolveAccountLabel(item),item.category,item.instrument,item.timeframe,item.is_test_trade,item.type,item.side,item.size,item.entry_price||item.order_price,item.current_price,item.stop_loss,item.take_profit,item.leverage,formatTimestamp(item.opened_at),item.status].forEach((v)=>{const td=document.createElement('td'); td.textContent=fmt(v); row.appendChild(td);}); const actionTd=document.createElement('td'); renderActionCell(item,actionTd,{allowAction:true}); row.appendChild(actionTd); tbody.appendChild(row); });
  };
  const refresh=async()=>{ if(refreshInFlight) return refreshInFlight; refreshInFlight=(async()=>{ try{setBadge('Loading...'); const payload=await fetchJson('/api/open-orders?force=1'); render(payload.items||[],payload.errors||[],payload.warnings||[]); const errCount=Array.isArray(payload.errors)?payload.errors.length:0; const warningCount=Array.isArray(payload.warnings)?payload.warnings.length:0; const stale=Boolean(payload.stale)||errCount>0; const details=[errCount?`${errCount} ${errCount===1?'error':'errors'}`:'',warningCount?`${warningCount} ${warningCount===1?'warning':'warnings'}`:''].filter(Boolean).join(', '); setBadge(`${stale?'Stale':'Updated'}${details?` (${details})`:''}`);}catch(err){const reason=safeDiagnostic(err?.message||err,'request failed without diagnostic details').replace(/:+\s*$/,'').trim()||'request failed without diagnostic details'; setBadge(`Stale (refresh failed: ${reason})`);} finally{refreshInFlight=null;}})(); return refreshInFlight; };
  const pollVersion=async()=>{if(document.hidden) return; try{const payload=await fetchJson('/api/open-orders/version'); const nextVersion=Number(payload?.version); if(!Number.isFinite(nextVersion)) return; if(knownVersion===null){knownVersion=nextVersion; return;} if(nextVersion!==knownVersion){knownVersion=nextVersion; await refresh();}}catch(_){}};
  const startVersionPolling=()=>{if(versionPollTimer) return; versionPollTimer=setInterval(pollVersion,POLL_MS);};
  const stopVersionPolling=()=>{if(!versionPollTimer) return; clearInterval(versionPollTimer); versionPollTimer=null;};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stopVersionPolling(); return;} startVersionPolling(); pollVersion();});
  refreshBtn?.addEventListener('click',refresh); refresh().then(pollVersion); if(!document.hidden) startVersionPolling();
})();
