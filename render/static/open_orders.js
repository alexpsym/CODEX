(() => {
  const refreshBtn = document.getElementById('refresh-btn');
  const statusBadge = document.getElementById('open-orders-status');
  const table = document.getElementById('open-orders-table');
  const tbody = table?.querySelector('tbody');
  const emptyState = document.getElementById('open-orders-empty');
  const errorsBox = document.getElementById('open-orders-errors');
  const errorsList = errorsBox?.querySelector('ul');
  const attemptsTable = document.getElementById('webhook-attempts-table');
  const attemptsBody = attemptsTable?.querySelector('tbody');
  const pendingInput = document.getElementById('pending-webhook-id-input');
  const diagCard = document.getElementById('webhook-diagnostic-card');
  const pendingManualActions = new Map();
  const VERIFY_SCHEDULE_MS = [300, 700, 1500, 3000, 3000];
  const webhookStatusLabel = (status) => {
    const key = String(status || '').toUpperCase();
    if (key === 'WAITING') return 'Waiting for TradingView POST';
    if (key === 'TRIGGERING') return 'Triggering';
    if (key === 'BYBIT_REJECTED') return 'Bybit rejected';
    if (key === 'FAILED_BEFORE_SUBMIT') return 'Failed before submit';
    if (key === 'ORDER_CREATED_TPSL_FAILED') return 'Order created, TP/SL failed';
    if (key === 'PENDING_NOT_FOUND') return 'Pending webhook not found on this instance';
    return status || '—';
  };
  let refreshInFlight = null; let hasData = false; let knownVersion = null; let versionPollTimer = null; const POLL_MS = 2500;
  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const setBadge = (message) => { if (statusBadge) statusBadge.textContent = message; };
  const rowKey = (row) => [row?.broker,row?.account,row?.account_id,row?.category,row?.instrument,row?.type,row?.id,row?.position_idx,row?.order_link_id].map((v)=>String(v ?? '').trim().toLowerCase()).join('|');
  const parseApiError = (data, response) => {
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail === 'object') return detail.message || detail.error || JSON.stringify(detail);
    return `${response.status} ${response.statusText}`;
  };
  const formatTimestamp=(value)=>{ if(!value) return '—'; const n=Number(value); if(!Number.isNaN(n)){const ms=n<1_000_000_000_000?n*1000:n; const d=new Date(ms); if(!Number.isNaN(d.getTime())) return d.toLocaleString();} const d=new Date(value); return Number.isNaN(d.getTime())?String(value):d.toLocaleString();};
  const formatSourceErrors=(errors=[])=>Array.isArray(errors)?errors.map((entry)=>{if(!entry||typeof entry!=='object') return null; const broker=String(entry.broker||'Source').trim(); const account=String(entry.account||'').trim(); const category=String(entry.category||'').trim(); const endpoint=String(entry.endpoint||entry.path||entry.endpoint_type||'').trim(); const params=entry.params&&typeof entry.params==='object'?JSON.stringify(entry.params):String(entry.params||'').trim(); const retCode=String(entry.retCode??entry.ret_code??'').trim(); const retMsg=String(entry.retMsg??entry.ret_msg??'').trim(); let message=String(entry.message||entry.error||'').trim(); if(!message){message=[endpoint,params,retCode?`retCode=${retCode}`:'',retMsg?`retMsg=${retMsg}`:''].filter(Boolean).join(' ');} if(!message) return null; return [broker,account,category].filter(Boolean).join(' ')+`: ${message}`;}).filter(Boolean):[];
  const buildFetchError=(url,status,statusText,bodyText,bodyJson)=>{const detailErrors=bodyJson?.detail?.errors||bodyJson?.errors; const flattened=formatSourceErrors(detailErrors); if(flattened.length) return new Error(flattened.join(' | ')); const body=(bodyText||'').trim(); return new Error(`GET ${url} failed: ${status} ${body||statusText}`)};
  const fetchJson=async(url)=>{const response=await fetch(url); let bodyText=''; let bodyJson=null; try{bodyText=await response.text(); bodyJson=bodyText?JSON.parse(bodyText):null;}catch(_){bodyJson=null;} if(!response.ok) throw buildFetchError(url,response.status,response.statusText,bodyText,bodyJson); if(bodyJson!==null) return bodyJson; return bodyText?JSON.parse(bodyText):{};};
  const pendingIdFromQuery = () => new URLSearchParams(window.location.search).get('pending_webhook_id') || '';
  const renderDiagnostic=(payload, err='')=>{ if(!diagCard) return; if(err){diagCard.className='diag-card'; diagCard.textContent=`Diagnostic failed: ${err}`; return;} const a=payload?.attempts?.[0]||{}; diagCard.className='diag-card'; diagCard.innerHTML=`<strong>Webhook Diagnostic</strong><div>Status: ${fmt(payload?.status)}</div><div>Pending ID: ${fmt(payload?.pending_webhook_id)}</div><div>Host: ${fmt(payload?.current_instance?.host)}</div><div>Origin host: ${fmt(a.payload_origin_host)}</div><div>Request URL: ${fmt(a.request_url)}</div><div>retCode/retMsg: ${fmt(payload?.bybit_ret_code)} / ${fmt(payload?.bybit_ret_msg)}</div><div>orderId/orderLinkId: ${fmt(payload?.orderId)} / ${fmt(payload?.orderLinkId)}</div>`; };
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
        if (data.verified) { state.verifyStatus='verified'; state.message = `${state.action === 'close' ? 'Close':'Cancel'} verified.`; setBadge(state.message); refresh().catch((err)=>setBadge(`Verified; refresh failed: ${err?.message||err}`)); return; }
        if (data.still_open && i === VERIFY_SCHEDULE_MS.length - 1) { pendingManualActions.delete(key); setBadge(`${state.action === 'close' ? 'Close':'Cancel'} submitted but still appears open after verification.`); refresh().catch(()=>{}); return; }
      } catch (err) {
        pendingManualActions.delete(key); setBadge(`Verification failed; refresh required: ${err?.message || err}`); refresh().catch(()=>{}); return;
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
      render((window.__openOrdersItems || []).filter((it)=>rowKey(it)!==key), window.__openOrdersErrors || []);
      verifyManualAction(row, key);
    } catch (err) {
      pendingManualActions.delete(key); btn.disabled = false; btn.textContent = prev; setBadge(err?.message || 'Action failed');
    }
  };
  const renderActionCell=(row,cell,{allowAction=true}={})=>{const label=actionLabelFor(row); if(!allowAction||!label||!isActionableRow(row)){cell.textContent='—';return;} const required=['broker','account','category','instrument','id','type']; const missing=required.some((k)=>!String(row[k]??'').trim()); if(missing){cell.textContent='—';return;} const key=rowKey(row); const state=pendingManualActions.get(key); if(state&&['submitting','accepted_verifying'].includes(state.verifyStatus)){cell.textContent='Verifying...'; return;} const btn=document.createElement('button'); btn.type='button'; btn.className='action-btn'; btn.textContent=label; btn.addEventListener('click',()=>postClose(row,btn)); cell.appendChild(btn);};
  const render=(items,errors=[])=>{window.__openOrdersItems=Array.isArray(items)?items:[]; window.__openOrdersErrors=Array.isArray(errors)?errors:[]; if(!tbody) return; tbody.innerHTML=''; hasData=Boolean(items.length); const formattedErrors=formatSourceErrors(errors); if(errorsBox) errorsBox.style.display=formattedErrors.length?'block':'none'; if(errorsList){errorsList.innerHTML=''; formattedErrors.forEach((text)=>{const li=document.createElement('li'); li.textContent=text; errorsList.appendChild(li);});} if(!items.length){if(emptyState) emptyState.style.display='block'; return;} if(emptyState) emptyState.style.display='none';
    items.forEach((item)=>{ if (pendingManualActions.has(rowKey(item))) return; const row=document.createElement('tr'); ['broker',resolveAccountLabel(item),item.category,item.instrument,item.timeframe,item.is_test_trade,item.type,item.side,item.size,item.entry_price||item.order_price,item.current_price,item.stop_loss,item.take_profit,item.leverage,formatTimestamp(item.opened_at),item.status].forEach((v,idx)=>{if(idx===0){const td=document.createElement('td'); td.textContent='—'; row.appendChild(td);} const td=document.createElement('td'); td.textContent=fmt(v); row.appendChild(td);}); const actionTd=document.createElement('td'); renderActionCell(item,actionTd,{allowAction:true}); row.appendChild(actionTd); tbody.appendChild(row); });
  };
  const renderWebhookAttempts=(items=[],fetchError='')=>{ if(!attemptsBody) return; attemptsBody.innerHTML=''; if(fetchError){const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=11; td.className='muted'; td.textContent=`Failed to load webhook attempts: ${fetchError}`; tr.appendChild(td); attemptsBody.appendChild(tr); return;} (Array.isArray(items)?items:[]).forEach((item)=>{const tr=document.createElement('tr'); [formatTimestamp(item.updated_at||item.received_at),item.symbol,item.action,item.account,item.status,item.bybit_ret_code,item.bybit_ret_msg,item.request_id,item.pending_webhook_id,item.error||item.last_error,item.request_host||item.payload_origin_host].forEach((v)=>{const td=document.createElement('td'); td.textContent=fmt(v); tr.appendChild(td);}); attemptsBody.appendChild(tr);});};
  const refresh=async()=>{ if(refreshInFlight) return refreshInFlight; refreshInFlight=(async()=>{ try{setBadge('Loading...'); const payload=await fetchJson('/api/open-orders?force=1'); render(payload.items||[],payload.errors||[]); try{const attempts=await fetchJson('/api/calculator/webhook-attempts?limit=20'); renderWebhookAttempts(attempts.items||[]);}catch(attemptErr){renderWebhookAttempts([],attemptErr?.message||String(attemptErr));} const pid=(pendingInput?.value||pendingIdFromQuery()).trim(); if(pid){try{const d=await fetchJson(`/api/calculator/webhook-diagnostic/${encodeURIComponent(pid)}`); renderDiagnostic(d);}catch(e){renderDiagnostic(null,e?.message||String(e));}} else {renderDiagnostic({status:'IDLE'});} const stale=Boolean(payload.stale); const errCount=Array.isArray(payload.errors)?payload.errors.length:0; setBadge(stale?`Stale${errCount?` (${errCount} errors)`:''}`:`Updated${errCount?` (${errCount} errors)`:''}`);}catch(err){setBadge('Stale (refresh failed)'); renderDiagnostic(null, err?.message||String(err));} finally{refreshInFlight=null;}})(); return refreshInFlight; };
  const pollVersion=async()=>{if(document.hidden) return; try{const payload=await fetchJson('/api/open-orders/version'); const nextVersion=Number(payload?.version); if(!Number.isFinite(nextVersion)) return; if(knownVersion===null){knownVersion=nextVersion; return;} if(nextVersion!==knownVersion){knownVersion=nextVersion; await refresh();}}catch(_){}};
  const startVersionPolling=()=>{if(versionPollTimer) return; versionPollTimer=setInterval(pollVersion,POLL_MS);};
  const stopVersionPolling=()=>{if(!versionPollTimer) return; clearInterval(versionPollTimer); versionPollTimer=null;};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stopVersionPolling(); return;} startVersionPolling(); pollVersion();});
  if (pendingInput) pendingInput.value = pendingIdFromQuery();
  pendingInput?.addEventListener('change',refresh);
  refreshBtn?.addEventListener('click',refresh); refresh().then(pollVersion); if(!document.hidden) startVersionPolling();
})();
