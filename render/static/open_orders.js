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
  const pendingVisibilityChecks = new Map();
  const VERIFY_SCHEDULE_MS = [300, 700, 1500, 3000, 3000];
  const VISIBILITY_SCHEDULE_MS = [0, 350, 800, 1600, 3000, 5000];
  const CHANNEL_NAME = 'trading-tools-open-orders';
  const STORAGE_EVENT_KEY = 'trading-tools-open-orders-event';
  let refreshInFlight = null; let refreshQueued = false; let hasData = false; let knownVersion = null; let versionPollTimer = null; const POLL_MS = 2500;
  const stateChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(CHANNEL_NAME) : null;
  stateChannel?.unref?.();
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
  const fetchJson=async(url)=>{const response=await fetch(url,{cache:'no-store'}); let bodyText=''; let bodyJson=null; try{bodyText=await response.text(); bodyJson=bodyText?JSON.parse(bodyText):null;}catch(_){bodyJson=null;} if(!response.ok) throw buildFetchError(url,response.status,response.statusText,bodyText,bodyJson); if(bodyJson!==null) return bodyJson; return bodyText?JSON.parse(bodyText):{};};
  const resolveAccountLabel=(row)=>{const parts=[]; const account=String(row?.account||'').trim(); const accountId=String(row?.account_id||'').trim(); const suffix=String(row?.account_label_suffix||'').trim(); if(account) parts.push(account); if(accountId) parts.push(accountId); if(suffix) parts.push(suffix); return parts.join(' · ')||'—';};
  const isActionableRow=(row)=>{if(!row||typeof row!=='object') return false; if(row.parent_id||row.parent_order_id) return false; const status=String(row.status||'').toLowerCase(); if(status.includes('bounce waiting')) return false; const type=String(row.type||'').toLowerCase(); return type==='order'||type==='position'||type==='trade'||type==='webhook';};
  const actionLabelFor=(row)=>{const type=String(row?.type||'').toLowerCase(); if(type==='order'||type==='webhook') return 'Cancel'; if(type==='position'||type==='trade') return 'Close'; return null;};
  const broadcastStateChange=(event)=>{
    const safeEvent={...event,type:'state-changed',eventId:String(event?.eventId||`${Date.now()}-${Math.random().toString(16).slice(2)}`),submittedAt:Number(event?.submittedAt||Date.now())};
    try{stateChannel?.postMessage(safeEvent);}catch(_){}
    try{if(typeof localStorage!=='undefined') localStorage.setItem(STORAGE_EVENT_KEY,JSON.stringify(safeEvent));}catch(_){}
  };

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
      broadcastStateChange({action,broker:row.broker,account:row.account,category:row.category,symbol:row.instrument,order_id:row.id});
      render((window.__openOrdersItems || []).filter((it)=>rowKey(it)!==key), window.__openOrdersErrors || [], window.__openOrdersWarnings || []);
      verifyManualAction(row, key);
    } catch (err) {
      pendingManualActions.delete(key); btn.disabled = false; btn.textContent = prev; setBadge(safeDiagnostic(err?.message||err,'Action failed'));
    }
  };
  const renderActionCell=(row,cell,{allowAction=true}={})=>{const label=actionLabelFor(row); if(!allowAction||!label||!isActionableRow(row)){cell.textContent='—';return;} const required=['broker','account','category','instrument','id','type']; const missing=required.some((k)=>!String(row[k]??'').trim()); if(missing){cell.textContent='—';return;} const key=rowKey(row); const state=pendingManualActions.get(key); if(state&&['submitting','accepted_verifying'].includes(state.verifyStatus)){cell.textContent='Verifying...'; return;} const btn=document.createElement('button'); btn.type='button'; btn.className='action-btn'; btn.textContent=label; btn.addEventListener('click',()=>postClose(row,btn)); cell.appendChild(btn);};
  const render=(items,errors=[],warnings=[])=>{window.__openOrdersItems=Array.isArray(items)?items:[]; window.__openOrdersErrors=Array.isArray(errors)?errors:[]; window.__openOrdersWarnings=Array.isArray(warnings)?warnings:[]; if(!tbody) return; tbody.innerHTML=''; hasData=Boolean(items.length); const formattedErrors=formatSourceErrors(errors); const formattedWarnings=formatSourceErrors(warnings); if(errorsBox) errorsBox.style.display=formattedErrors.length?'block':'none'; if(errorsList){errorsList.innerHTML=''; formattedErrors.forEach((text)=>{const li=document.createElement('li'); li.textContent=text; errorsList.appendChild(li);});} if(warningsBox) warningsBox.style.display=formattedWarnings.length?'block':'none'; if(warningsList){warningsList.innerHTML=''; formattedWarnings.forEach((text)=>{const li=document.createElement('li'); li.textContent=text; warningsList.appendChild(li);});} if(!items.length){if(emptyState) emptyState.style.display='block'; return;} if(emptyState) emptyState.style.display='none';
    items.forEach((item)=>{ if (pendingManualActions.has(rowKey(item))) return; const row=document.createElement('tr'); if(item.source_stale){row.className='stale-row';row.title=safeDiagnostic(item.stale_reason||'Last-known-good broker state');} [item.venue||item.broker,resolveAccountLabel(item),item.asset,item.category,item.side,item.order_type,item.instrument,item.stop_loss_ticks,item.target_mode_display,item.take_profit_ticks,item.risk_mode_display,item.risk_value_display,item.risk_reward,item.timeframe,item.is_test_trade,item.setup,item.pattern,item.ema,item.vwap,item.aths_atls,item.round_number,item.webhook_mode,item.webhook_status,item.planned_entry_price,item.planned_stop_price,item.planned_target_price,item.type,item.size,item.entry_price||item.order_price,item.current_price,item.stop_loss,item.take_profit,item.leverage,formatTimestamp(item.opened_at),item.status].forEach((v)=>{const td=document.createElement('td'); td.textContent=fmt(v); row.appendChild(td);}); const actionTd=document.createElement('td'); renderActionCell(item,actionTd,{allowAction:true}); row.appendChild(actionTd); tbody.appendChild(row); });
  };
  const refresh=async(reason='manual')=>{
    if(refreshInFlight){refreshQueued=true;return refreshInFlight.then(()=>{if(refreshQueued){refreshQueued=false;return refresh(reason);}});}
    refreshInFlight=(async()=>{
      try{
        setBadge(reason==='broker-visibility'?'Submitted; waiting for broker visibility':'Loading...');
        const payload=await fetchJson('/api/open-orders?force=1');
        render(payload.items||[],payload.errors||[],payload.warnings||[]);
        const renderedVersion=Number(payload?.version);
        if(Number.isFinite(renderedVersion)) knownVersion=renderedVersion;
        const errCount=Array.isArray(payload.errors)?payload.errors.length:0;
        const warningCount=Array.isArray(payload.warnings)?payload.warnings.length:0;
        const stale=Boolean(payload.stale)||errCount>0;
        const details=[errCount?`${errCount} ${errCount===1?'error':'errors'}`:'',warningCount?`${warningCount} ${warningCount===1?'warning':'warnings'}`:''].filter(Boolean).join(', ');
        setBadge(`${stale?'Stale':'Updated'}${details?` (${details})`:''}`);
      }catch(err){
        const reasonText=safeDiagnostic(err?.message||err,'request failed without diagnostic details').replace(/:+\s*$/,'').trim()||'request failed without diagnostic details';
        setBadge(`Stale (refresh failed: ${reasonText})`);
      }finally{refreshInFlight=null;}
    })();
    return refreshInFlight;
  };
  const eventValue=(event,...keys)=>{for(const key of keys){const value=String(event?.[key]??'').trim();if(value)return value;}return '';};
  const itemMatchesSubmission=(item,event)=>{
    const orderId=eventValue(event,'order_id','orderId');
    const orderLinkId=eventValue(event,'order_link_id','orderLinkId');
    const contextId=eventValue(event,'calculation_context_id','calculationContextId','context_id','contextId');
    if(orderId&&eventValue(item,'id','order_id')===orderId)return true;
    if(orderLinkId&&eventValue(item,'order_link_id','orderLinkId')===orderLinkId)return true;
    if(contextId&&eventValue(item,'calculation_context_id','context_id')===contextId)return true;
    const broker=eventValue(event,'broker').toLowerCase();const account=eventValue(event,'account').toLowerCase();const category=eventValue(event,'category').toLowerCase();const symbol=eventValue(event,'symbol','instrument').toUpperCase();
    if(!symbol)return false;
    if(broker&&eventValue(item,'broker','venue').toLowerCase()!==broker)return false;
    if(account&&eventValue(item,'account').toLowerCase()!==account)return false;
    if(category&&eventValue(item,'category').toLowerCase()!==category)return false;
    if(eventValue(item,'instrument').toUpperCase()!==symbol)return false;
    const openedRaw=item?.opened_at;const openedNumber=Number(openedRaw);const opened=Number.isFinite(openedNumber)&&String(openedRaw??'').trim()!==''?(openedNumber<1_000_000_000_000?openedNumber*1000:openedNumber):Date.parse(String(openedRaw||''));const submitted=Number(event?.submittedAt||0);
    return Number.isFinite(opened)&&submitted>0&&opened>=(submitted-5*60*1000);
  };
  const runVisibilityCheck=async(key)=>{
    const state=pendingVisibilityChecks.get(key);if(!state||state.running)return;
    state.running=true;
    while(state.nextAttempt<VISIBILITY_SCHEDULE_MS.length){
      if(document.hidden){state.running=false;return;}
      const delay=VISIBILITY_SCHEDULE_MS[state.nextAttempt];state.nextAttempt+=1;
      if(delay)await new Promise((resolve)=>setTimeout(resolve,delay));
      if(document.hidden){state.running=false;return;}
      await refresh('broker-visibility');
      const visible=(window.__openOrdersItems||[]).some((item)=>itemMatchesSubmission(item,state.event));
      if(visible){pendingVisibilityChecks.delete(key);setBadge('Submitted; broker state is now visible.');return;}
      if(state.nextAttempt<VISIBILITY_SCHEDULE_MS.length)setBadge('Submitted; waiting for broker visibility');
    }
    pendingVisibilityChecks.delete(key);
    setBadge('Submitted, but broker visibility was not confirmed yet. Use Refresh; if it persists, check the source warning.');
  };
  const handleStateChange=(event)=>{
    if(!event||event.type!=='state-changed')return;
    if(String(event.action||'').toLowerCase()!=='submit'){if(document.hidden){refreshQueued=true;return;}refresh('broadcast').catch(()=>{});return;}
    const key=String(event.eventId||`${event.submittedAt||Date.now()}|${event.order_id||''}|${event.symbol||''}`);
    if(!pendingVisibilityChecks.has(key))pendingVisibilityChecks.set(key,{event:{...event},nextAttempt:0,running:false});
    setBadge('Submitted; waiting for broker visibility');
    runVisibilityCheck(key).catch((err)=>setBadge(`Submitted; broker visibility check failed: ${safeDiagnostic(err?.message||err,'refresh required')}`));
  };
  const pollVersion=async()=>{if(document.hidden)return;try{const payload=await fetchJson('/api/open-orders/version');const nextVersion=Number(payload?.version);if(!Number.isFinite(nextVersion))return;if(knownVersion===null||nextVersion!==knownVersion){await refresh('version');}}catch(_){}};
  const startVersionPolling=()=>{if(versionPollTimer) return; versionPollTimer=setInterval(pollVersion,POLL_MS);};
  const stopVersionPolling=()=>{if(!versionPollTimer) return; clearInterval(versionPollTimer); versionPollTimer=null;};
  if(typeof window!=='undefined'&&window.__OPEN_ORDERS_TESTING__){window.__openOrdersTestHooks={refresh,pollVersion,handleStateChange,runVisibilityCheck,itemMatchesSubmission,getState:()=>({knownVersion,refreshQueued,refreshInFlight:Boolean(refreshInFlight),pendingVisibilityChecks:pendingVisibilityChecks.size,hasData})};}
  stateChannel?.addEventListener('message',(event)=>handleStateChange(event?.data));
  if(typeof window?.addEventListener==='function')window.addEventListener('storage',(event)=>{if(event.key!==STORAGE_EVENT_KEY||!event.newValue)return;try{handleStateChange(JSON.parse(event.newValue));}catch(_){}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stopVersionPolling();return;}startVersionPolling();pollVersion();for(const key of pendingVisibilityChecks.keys())runVisibilityCheck(key).catch(()=>{});if(refreshQueued&&!refreshInFlight){refreshQueued=false;refresh('visible').catch(()=>{});}});
  refreshBtn?.addEventListener('click',()=>refresh('manual')); refresh('initial').then(pollVersion); if(!document.hidden) startVersionPolling();
})();
