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
  const localRemovalStates = new Map();
  const localRemovalDescription = 'Removes local tracking only. Manage MT5 orders in MT5.';
  const pendingVisibilityChecks = new Map();
  const VERIFY_SCHEDULE_MS = [300, 700, 1500, 3000, 3000];
  // This bounds only a snapshot transport request, never acceptance of broker work.
  const GET_TIMEOUT_MS = window.__OPEN_ORDERS_TESTING__ ? (window.__OPEN_ORDERS_GET_TIMEOUT_MS__ || 15000) : 15000;
  let versionInFlight = null;
  let knownRevision = null;
  let hasSnapshot = false;
  let refreshNeedsRecovery = false;
  const seenSubmitEvents = new Set();
  const CHANNEL_NAME = 'trading-tools-open-orders';
  const STORAGE_EVENT_KEY = 'trading-tools-open-orders-event';
  let refreshInFlight = null; let refreshQueued = false; let hasData = false; let knownVersion = null; let versionPollTimer = null; const POLL_MS = 2500;
  const stateChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(CHANNEL_NAME) : null;
  stateChannel?.unref?.();
  const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
  const setBadge = (message) => { if (statusBadge&&statusBadge.textContent!==message) statusBadge.textContent = message; };
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
    const context=[broker,account,sanitizeSourceText(entry.account_id||''),category,sourceType,settlementCoin?`settleCoin=${settlementCoin}`:'',endpoint].filter(Boolean).join(' ');
    return context?`${context}: ${message}`:message;
  }).filter(Boolean):[];
  const diagnosticSignatures=new WeakMap();
  const renderDiagnostics=(box,list,entries,label)=>{
    if(!box)return;
    const formatted=formatSourceErrors(entries);
    const signature=JSON.stringify(formatted);
    if(diagnosticSignatures.get(box)===signature)return;
    diagnosticSignatures.set(box,signature);
    box.style.display=formatted.length?'block':'none';
    const groups=new Map();
    entries.forEach(raw=>{
      const entry=raw&&typeof raw==='object'?raw:{message:raw};
      const broker=sanitizeSourceText(entry.broker||'Source'),account=sanitizeSourceText(entry.account||'');
      const message=sanitizeSourceText(entry.message||entry.error||'');
      // Maintenance is identified by the broker response, never by status 503 alone.
      const maintenance=broker.toLowerCase()==='oanda'&&/system under maintenance/i.test(message);
      const cause=maintenance?'reports maintenance':safeDiagnostic(entry.retMsg||message||entry.error_type,'request failed');
      const key=JSON.stringify([broker,account,entry.http_status||'',cause]);
      if(!groups.has(key))groups.set(key,{title:[broker,account,cause].filter(Boolean).join(' '),sources:new Set()});
      groups.get(key).sources.add(entry.source_key||JSON.stringify([entry.account_id,entry.category,entry.source_type,entry.settlement_coin,entry.endpoint]));
    });
    const summary=box.querySelector('summary');
    const text=[...groups.values()].slice(0,2).map(group=>group.title+' \u2014 '+group.sources.size+' affected source'+(group.sources.size===1?'':'s')).join('; ')+(groups.size>2?'; '+(groups.size-2)+' other causes (see details)':'');
    if(summary&&summary.textContent!==text)summary.textContent=text||label;
    // The details element itself survives every publication, preserving its open state.
    if(list){list.innerHTML='';formatted.forEach(text=>{const li=document.createElement('li');li.textContent=text;list.appendChild(li);});}
  };
  const buildFetchError=(url,status,statusText,bodyText,bodyJson)=>{const detailErrors=bodyJson?.detail?.errors||bodyJson?.errors; const flattened=formatSourceErrors(detailErrors); if(flattened.length) return new Error(flattened.join(' | ')); const endpoint=safeEndpoint(url)||'unknown endpoint'; const httpStatus=safeDiagnostic(status,'unknown'); const parsedBody=bodyJson&&typeof bodyJson==='object'?JSON.stringify(bodyJson):bodyText; const body=safeDiagnostic(parsedBody); const reason=body||safeDiagnostic(statusText,'upstream returned no diagnostic details'); return new Error(`GET ${endpoint} failed: HTTP ${httpStatus} ${reason}`.trim());};
  const fetchJson=async(url)=>{
    const controller=new AbortController(); let timer;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('GET '+url+': deadline exceeded ('+GET_TIMEOUT_MS/1000+'s); broker visibility unknown'));},GET_TIMEOUT_MS);});
    try{
      return await Promise.race([(async()=>{
        const response=await fetch(url,{cache:'no-store',signal:controller.signal});
        const bodyText=await response.text(); let bodyJson=null;
        try{bodyJson=bodyText?JSON.parse(bodyText):null;}catch(_){}
        if(!response.ok)throw buildFetchError(url,response.status,response.statusText,bodyText,bodyJson);
        return bodyJson!==null?bodyJson:(bodyText?JSON.parse(bodyText):{});
      })(),deadline]);
    }finally{clearTimeout(timer);}
  };
  const resolveAccountLabel=(row)=>{const parts=[]; const account=String(row?.account||'').trim(); const accountId=String(row?.account_id||'').trim(); const suffix=String(row?.account_label_suffix||'').trim(); if(account) parts.push(account); if(accountId) parts.push(accountId); if(suffix) parts.push(suffix); return parts.join(' · ')||'—';};
  const isActionableRow=(row)=>{if(!row||typeof row!=='object'||row.local_tracking||String(row.broker||'').toLowerCase()==='pepperstone') return false; if(row.parent_id||row.parent_order_id) return false; const status=String(row.status||'').toLowerCase(); if(status.includes('bounce waiting')) return false; const type=String(row.type||'').toLowerCase(); return type==='order'||type==='position'||type==='trade'||type==='webhook';};
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
  const postLocalRemoval=async(row,btn)=>{
    const key=rowKey(row);if(localRemovalStates.get(key)?.pending)return;
    localRemovalStates.set(key,{pending:true});btn.disabled=true;btn.textContent='Removing...';
    try{
      const response=await fetch('/api/open-orders/local-tracking/remove',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id:row.id,account:row.account,calculation_context_id:row.calculation_context_id})});
      const text=await response.text(),data=text?JSON.parse(text):{};
      if(!response.ok)throw new Error(parseApiError(data,response));
      if(data.ok!==true||data.removed!==true||data.id!==row.id)throw new Error('Local removal was not confirmed. Reload and retry.');
      localRemovalStates.delete(key);
      render((window.__openOrdersItems||[]).filter(item=>rowKey(item)!==key),window.__openOrdersErrors||[],window.__openOrdersWarnings||[]);
      setBadge('Removed from LTT. '+localRemovalDescription);
      broadcastStateChange({action:'local-tracking-remove',broker:row.broker,account:row.account,calculation_context_id:row.calculation_context_id});
    }catch(err){
      const message='Remove from LTT failed: '+safeDiagnostic(err?.message||err,'Request failed; retry.');
      localRemovalStates.set(key,{pending:false,error:message});
      render(window.__openOrdersItems||[],window.__openOrdersErrors||[],window.__openOrdersWarnings||[]);
      setBadge(message);
    }
  };
  const renderActionCell=(row,cell,{allowAction=true}={})=>{
    const local=row.local_tracking===true&&String(row.broker||'').toLowerCase()==='pepperstone'
      &&['demo','live'].includes(row.account)&&row.calculation_context_id
      &&row.id===`pepperstone-local:${row.account}:${row.calculation_context_id}`
      &&['limit','market'].includes(String(row.order_type||'').toLowerCase());
    if(allowAction&&local){
      const state=localRemovalStates.get(rowKey(row)),btn=document.createElement('button');
      btn.type='button';btn.className='action-btn';btn.textContent=state?.pending?'Removing...':'Remove from LTT';btn.disabled=Boolean(state?.pending);
      btn.title=localRemovalDescription;btn.setAttribute('aria-label','Remove from LTT. '+localRemovalDescription);
      btn.addEventListener('click',()=>postLocalRemoval(row,btn));cell.appendChild(btn);
      if(state?.error){const error=document.createElement('span');error.setAttribute('role','alert');error.textContent=state.error;cell.appendChild(error);}
      return;
    }
    const label=actionLabelFor(row); if(!allowAction||!label||!isActionableRow(row)){cell.textContent='—';return;} const required=['broker','account','category','instrument','id','type']; const missing=required.some((k)=>!String(row[k]??'').trim()); if(missing){cell.textContent='—';return;} const key=rowKey(row); const state=pendingManualActions.get(key); if(state&&['submitting','accepted_verifying'].includes(state.verifyStatus)){cell.textContent='Verifying...'; return;} const btn=document.createElement('button'); btn.type='button'; btn.className='action-btn'; btn.textContent=label; btn.addEventListener('click',()=>postClose(row,btn)); cell.appendChild(btn);
  };
  const classificationOptions = {
    timeframe: ['','1m','5m','15m','30m','1h','4h','1d','1w','1mo'],
    setup: ['','Pullback','Breakout','News Scalp'], pattern: ['','range','channel'],
    ema: ['','9','20','Yes','No'], vwap: ['','Yes','No'],
    aths_atls: ['','All-Time High','All-Time Low'], round_number: ['','Yes','No'],
    is_test_trade: ['','Yes','No'], webhook_classification: ['','Yes','No']
  };
  const classificationLabels = {timeframe:'Timeframe',setup:'Setup',pattern:'Pattern',ema:'EMA',vwap:'VWAP',
    aths_atls:'ATH / ATL',round_number:'Round Number',is_test_trade:'Test',webhook_classification:'Webhook classification'};
  // Explicit zero-based header positions: the new annotation follows Mode/Status.
  const classificationColumns = {13:'timeframe',14:'is_test_trade',15:'setup',16:'pattern',17:'ema',18:'vwap',19:'aths_atls',20:'round_number',23:'webhook_classification'};
  const timeframeLabels = {'1m':'1MIN','5m':'5MIN','15m':'15MIN','30m':'30MIN','1h':'1H','4h':'4H','1d':'1D','1w':'1W','1mo':'1MO'};
  const classificationRows = new Map();
  const classificationKey = item => rowKey(item)+'|'+(item.price_edit?.target||'')+'|'+(item.classification_target||'unlinked');
  const priceColumns={29:'entry',31:'stop_loss',32:'take_profit'};
  const priceLabels={entry:'Entry / Order',stop_loss:'Stop Loss',take_profit:'Take Profit'};
  const priceStorage='ltt-open-order-amendments-v1';
  let priceOperations={};
  try{priceOperations=JSON.parse(window.localStorage?.getItem(priceStorage)||'{}');}catch(_){}
  const persistPriceOperations=()=>{if(window.localStorage)window.localStorage.setItem(priceStorage,JSON.stringify(priceOperations));};
  const priceBusy=entry=>[...entry.prices.values()].some(state=>state.draft?.operation);
  const priceApply=(entry,item)=>{
    if(!item||Number(item._price_read_started||0)<Number(entry.item._price_read_started||0))return;
    for(const key of ['price_edit','_price_read_started','_price_broker','entry_price','order_price','stop_loss','take_profit']){
      if(Object.prototype.hasOwnProperty.call(item,key))entry.item[key]=item[key];
    }
  };
  const updatePriceCell=(entry,state)=>{
    const d=state.draft;if(!d)return;
    const p=entry.item.price_edit;
    if(!d.operation&&!d.conflict&&(!entry.present||p?.target!==d.target||
      (state.field==='entry'?['entry','stop_loss','take_profit']:['entry',state.field]).some(field=>String(p?.levels?.[field])!==String(d.base[field])))){
      d.conflict=true;d.message='Price or entry changed. Reload this cell.';
    }
    const busy=Boolean(d.operation),available=entry.present&&p?.fields?.includes(state.field);
    state.input.disabled=busy;state.units.disabled=busy;
    state.save.disabled=busy||priceBusy(entry)||!available||d.conflict||!d.dirty;
    state.cancel.disabled=busy;state.reload.hidden=!d.conflict;state.reload.disabled=busy||!entry.present;
    state.feedback.textContent=d.message||'';
  };
  const renderPriceCell=(entry,state)=>{
    if(state.draft){updatePriceCell(entry,state);return;}
    state.td.innerHTML='';
    if(entry.item.local_tracking){
      const item=entry.item;
      const planned=state.field==='entry'?item.planned_entry_price:state.field==='stop_loss'?item.planned_stop_price:item.planned_target_price;
      const text=document.createElement('span');
      text.textContent=state.field==='entry'&&String(item.order_type).toLowerCase()==='market'?'Unknown (awaiting MT5 fill)':`${fmt(planned)} (planned)`;
      state.td.appendChild(text);return;
    }
    const p=entry.item.price_edit,value=p?.levels?.[state.field]??(state.field==='entry'?(entry.item.entry_price||entry.item.order_price):entry.item[state.field]);
    if(state.saved&&state.savedValue!==value)state.saved=false;
    const text=document.createElement('span');text.textContent=String(fmt(value));state.td.appendChild(text);
    if(p?.fields?.includes(state.field)){
      const edit=button('\u270e',()=>openPriceCell(entry,state));edit.className='price-edit';edit.setAttribute('aria-label','Edit '+priceLabels[state.field]);state.td.appendChild(edit);
    }else state.td.title=p?.reason||(state.field==='entry'?'Filled entry is read-only':'Price editing unavailable');
    if(state.saved){const feedback=document.createElement('span');feedback.className='classification-feedback';feedback.setAttribute('role','status');feedback.textContent='Saved';state.td.appendChild(feedback);}
  };
  const finishPriceOperation=(entry,state,data)=>{
    const d=state.draft;if(!d?.operation)return;
    if(data.status==='confirmed'){
      priceApply(entry,data.item);delete priceOperations[d.operation];persistPriceOperations();
      state.draft=null;state.saved=true;state.savedValue=entry.item.price_edit?.levels?.[state.field];renderPriceCell(entry,state);
    }else if(data.status==='rejected'){
      delete priceOperations[d.operation];persistPriceOperations();d.operation=null;d.message=data.message||'Broker rejected amendment';
    }else d.message=data.message||'Awaiting broker evidence; do not resubmit';
    for(const other of entry.prices.values()){if(other.draft)updatePriceCell(entry,other);else renderPriceCell(entry,other);}
  };
  const reconcilePriceOperations=async()=>{
    for(const entry of classificationRows.values())for(const state of entry.prices.values()){
      const d=state.draft;if(!d?.operation||d.checking||d.sending)continue;
      d.checking=true;
      try{
        const response=await fetch('/api/open-orders/amend-status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_id:d.operation})});
        const data=await response.json();if(response.ok)finishPriceOperation(entry,state,data);
      }catch(_){d.message='Outcome unknown; read-only reconciliation pending';updatePriceCell(entry,state);}
      finally{d.checking=false;}
    }
  };
  const savePriceCell=async(entry,state)=>{
    const d=state.draft;if(!d||state.save.disabled)return;
    const id=window.crypto?.randomUUID?.()||('amend_'+Date.now()+'_'+Math.random().toString(36).slice(2));
    const body={operation_id:id,target:d.target,field:state.field,mode:state.units.value,value:state.input.value,base:d.base};
    d.operation=id;d.sending=true;d.message='Saving...';
    priceOperations[id]=body;
    try{persistPriceOperations();}catch(_){delete priceOperations[id];d.operation=null;d.sending=false;d.message='Cannot preserve operation reference; no request sent';updatePriceCell(entry,state);return;}
    for(const other of entry.prices.values())updatePriceCell(entry,other);
    try{
      const response=await fetch('/api/open-orders/amend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const data=await response.json();
      if(!response.ok){
        if(response.status>=500)throw new Error('Outcome unknown');
        delete priceOperations[id];persistPriceOperations();d.operation=null;d.message=parseApiError(data,response);if(response.status===409)d.conflict=true;
      }else finishPriceOperation(entry,state,data);
    }catch(_){d.message='Outcome unknown; operation retained for read-only reconciliation. Do not resubmit';}
    finally{d.sending=false;for(const other of entry.prices.values())updatePriceCell(entry,other);}
  };
  const openPriceCell=(entry,state,resume=null)=>{
    if(state.draft)return;
    const p=entry.item.price_edit;if(!resume&&(!entry.present||!p?.fields?.includes(state.field)))return;
    const d=state.draft={target:resume?.target||p.target,base:{unit:p.unit,...(resume?.base||p.levels)},dirty:false,conflict:false,operation:resume?.operation_id||null,message:resume?'Outcome unresolved; read-only reconciliation pending':''};
    state.saved=false;state.td.innerHTML='';
    const units=document.createElement('select');state.units=units;units.setAttribute('aria-label',priceLabels[state.field]+' units');
    for(const value of ['price','ticks']){if(value==='ticks'&&(state.field==='entry'||!p.unit))continue;const option=document.createElement('option');option.value=value;option.textContent=value==='price'?'Price':'Ticks';units.appendChild(option);}
    units.value=resume?.mode||'price';if(state.field!=='entry'&&p.unit)state.td.appendChild(units);
    const input=document.createElement('input');state.input=input;input.type='text';input.inputMode='decimal';input.setAttribute('aria-label',priceLabels[state.field]+' value; entry '+d.base.entry+(p.unit?'; tick '+p.unit:''));input.value=resume?.value||d.base[state.field];state.td.appendChild(input);
    const dirty=()=>{d.dirty=units.value!=='price'||input.value!==d.base[state.field];d.message='';updatePriceCell(entry,state);};
    input.addEventListener('input',dirty);units.addEventListener('change',()=>{input.value=units.value==='ticks'?(p.ticks?.[state.field]||''):d.base[state.field];dirty();});
    const cancel=()=>{if(d.operation)return;state.draft=null;renderPriceCell(entry,state);};
    state.save=button('Save',()=>savePriceCell(entry,state));state.cancel=button('Cancel',cancel);
    state.reload=button('Reload',async()=>{if(d.operation)return;await refresh('progress');if(!entry.present||refreshNeedsRecovery)return;state.draft=null;openPriceCell(entry,state);});
    for(const el of [state.save,state.cancel,state.reload])state.td.appendChild(el);
    state.feedback=document.createElement('span');state.feedback.className='classification-feedback';state.feedback.setAttribute('role','status');state.td.appendChild(state.feedback);
    state.td.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();cancel();}if(event.key==='Enter'){event.preventDefault();savePriceCell(entry,state);}});
    updatePriceCell(entry,state);if(!resume){input.focus();input.select();}
  };
  const choiceValue = (field,value) => {
    const text=value===true?'Yes':value===false?'No':String(value??'').trim();
    if(!text||text.toLowerCase()==='none'||text==='\u2014')return '';
    if(field==='timeframe'){
      const key=text.toLowerCase();
      return ({'1min':'1m','5min':'5m','15min':'15m','30min':'30m','daily':'1d','weekly':'1w','monthly':'1mo'})[key]||key;
    }
    if(field==='pattern')return text.toLowerCase();
    if(field==='is_test_trade')return ({'0':'No','1':'Yes','false':'No','true':'Yes','no':'No','yes':'Yes'})[text.toLowerCase()]||text;
    return text;
  };
  const fieldValue = (item,field) => choiceValue(field,Object.prototype.hasOwnProperty.call(item.classification_values||{},field)?item.classification_values[field]:item[field]);
  const displayChoice = (field,value) => !value?'\u2014':field==='timeframe'?(timeframeLabels[value]||value):field==='pattern'?value[0].toUpperCase()+value.slice(1):value;
  const button = (label, action) => {const el=document.createElement('button');el.type='button';el.className='action-btn';el.textContent=label;el.addEventListener('click',action);return el;};
  const updateCellAvailability = (entry,state) => {
    const draft=state.draft;if(!draft)return;
    const available=entry.present&&Boolean(entry.item.classification_target);
    if(!available)draft.message='Entry is no longer available. Draft retained; Cancel to discard.';
    state.select.disabled=!available||entry.saving===state.field;
    state.save.disabled=Boolean(entry.saving)||!available||draft.conflict||!draft.dirty;
    state.cancel.disabled=entry.saving===state.field;
    state.reload.disabled=entry.saving===state.field||!available;
    state.reload.hidden=!draft.conflict;
    state.feedback.textContent=draft.message||'';
  };
  const synchronizeDrafts = entry => {
    for(const state of entry.cells.values()){
      const draft=state.draft;if(!draft)continue;
      const version=Number(entry.item.classification_version||0);
      if(!draft.conflict&&version!==draft.version){
        if(fieldValue(entry.item,state.field)===draft.base)draft.version=version;
        else{draft.conflict=true;draft.message='This classification changed. Reload this cell before saving; draft retained.';}
      }
      updateCellAvailability(entry,state);
    }
  };
  const renderClassificationCell = (entry,state) => {
    if(state.draft){updateCellAvailability(entry,state);return;}
    if(!state.valueButton){
      state.td.innerHTML='';
      state.valueButton=button('',()=>openClassificationCell(entry,state));state.valueButton.className='classification-value';
      state.valueButton.setAttribute('aria-label','Edit '+classificationLabels[state.field]);state.td.appendChild(state.valueButton);
      state.feedback=document.createElement('span');state.feedback.className='classification-feedback';state.feedback.setAttribute('role','status');state.td.appendChild(state.feedback);
    }
    state.valueButton.textContent=displayChoice(state.field,fieldValue(entry.item,state.field));
    state.valueButton.disabled=!entry.present||!entry.item.classification_target;
    state.valueButton.title=entry.item.classification_unavailable||'Edit '+classificationLabels[state.field];
    state.feedback.textContent=state.savedUntil>Date.now()?'Saved':'';
  };
  const cancelClassificationCell = (entry,state) => {
    if(entry.saving===state.field)return;
    state.draft=null;state.valueButton=null;state.savedUntil=0;renderClassificationCell(entry,state);state.valueButton.focus?.();
    if(!entry.present)render(window.__openOrdersItems||[],window.__openOrdersErrors||[],window.__openOrdersWarnings||[]);
  };
  const saveClassificationCell = async(entry,state) => {
    const draft=state.draft;
    if(!draft||entry.saving||!entry.present||!entry.item.classification_target||draft.conflict||!draft.dirty)return;
    const selected=state.select.value;
    const value=state.field==='is_test_trade'?(selected===''?null:selected==='Yes'):selected;
    entry.saving=state.field;draft.message='Saving...';synchronizeDrafts(entry);
    try{
      const response=await fetch('/api/open-orders/classification',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:entry.item.classification_target,version:draft.version,changes:{[state.field]:value}})});
      const data=await response.json();if(!response.ok){if(response.status===409)draft.conflict=true;throw new Error(parseApiError(data,response));}
      // A delayed earlier publication/acknowledgment cannot replace newer metadata.
      if(Number(data.version)>=Number(entry.item.classification_version||0))entry.item={...entry.item,...data.item,classification_values:data.values,classification_version:data.version};
      state.draft=null;state.valueButton=null;state.savedUntil=Date.now()+2500;
      const updated=(window.__openOrdersItems||[]).map(item=>classificationKey(item)===entry.key?entry.item:item);
      render(updated,window.__openOrdersErrors||[],window.__openOrdersWarnings||[]);
    }catch(error){draft.message=safeDiagnostic(error.message||error);}
    finally{entry.saving=null;synchronizeDrafts(entry);}
  };
  const openClassificationCell = (entry,state) => {
    if(state.draft||!entry.present||!entry.item.classification_target)return;
    const base=fieldValue(entry.item,state.field);
    state.draft={base,version:Number(entry.item.classification_version||0),conflict:false,dirty:false,message:''};state.savedUntil=0;
    state.td.innerHTML='';state.valueButton=null;
    const select=document.createElement('select');state.select=select;select.dataset.field=state.field;
    select.setAttribute('aria-label',classificationLabels[state.field]);
    for(const value of classificationOptions[state.field]){const option=document.createElement('option');option.value=value;option.textContent=value?displayChoice(state.field,value):'None';select.appendChild(option);}
    select.value=classificationOptions[state.field].includes(base)?base:'';
    select.addEventListener('change',()=>{state.draft.dirty=select.value!==state.draft.base;updateCellAvailability(entry,state);});state.td.appendChild(select);
    state.save=button('Save',()=>saveClassificationCell(entry,state));state.cancel=button('Cancel',()=>cancelClassificationCell(entry,state));
    state.reload=button('Reload',async()=>{
      if(entry.saving===state.field||!entry.present)return;
      await refresh('progress');
      if(!entry.present)return;
      if(refreshNeedsRecovery){state.draft.message='Could not reload this cell. Draft retained.';updateCellAvailability(entry,state);return;}
      state.draft=null;openClassificationCell(entry,state);
    });
    state.td.appendChild(state.save);state.td.appendChild(state.cancel);state.td.appendChild(state.reload);
    state.feedback=document.createElement('span');state.feedback.className='classification-feedback';state.feedback.setAttribute('role','status');state.td.appendChild(state.feedback);
    updateCellAvailability(entry,state);select.focus?.();
  };
  const rowValues = item => [item.venue||item.broker,resolveAccountLabel(item),item.asset,item.category,item.side,item.order_type,item.instrument,item.stop_loss_ticks,item.target_mode_display,item.take_profit_ticks,item.risk_mode_display,item.risk_value_display,item.risk_reward,item.timeframe,item.is_test_trade,item.setup,item.pattern,item.ema,item.vwap,item.aths_atls,item.round_number,item.webhook_mode,item.webhook_status,item.webhook_classification,item.planned_entry_price,item.planned_stop_price,item.planned_target_price,item.type,item.quantity_is_estimate?`${item.size??''} (est.)`:item.size,item.local_tracking?'':(item.entry_price||item.order_price),item.current_price,item.stop_loss,item.take_profit,item.leverage,item.type==='Position'?item.funding_paid:null,item.type==='Position'?item.funding_received:null,item.type==='Position'?item.net_funding:null,formatTimestamp(item.opened_at),item.status];
  const render=(items,errors=[],warnings=[],coverage=window.__openOrdersCoverage||{})=>{
    window.__openOrdersItems=Array.isArray(items)?items:[];window.__openOrdersErrors=Array.isArray(errors)?errors:[];window.__openOrdersWarnings=Array.isArray(warnings)?warnings:[];
    if(!tbody)return;hasData=Boolean(items.length);renderDiagnostics(errorsBox,errorsList,errors,'Source errors');renderDiagnostics(warningsBox,warningsList,warnings,'Source warnings');
    for(const entry of classificationRows.values())entry.present=false;
    const desired=[];
    for(let item of items){
      if(pendingManualActions.has(rowKey(item)))continue;
      const key=classificationKey(item);let entry=classificationRows.get(key);
      if(!entry){const tr=document.createElement('tr');entry={key,tr,item,present:true,cells:new Map(),prices:new Map(),saving:null};classificationRows.set(key,entry);
        rowValues(item).forEach((_,i)=>{const td=document.createElement('td');tr.appendChild(td);const field=classificationColumns[i];if(field){
          td.className='classification-cell';td.dataset.field=field;const state={field,td,draft:null};entry.cells.set(field,state);
          td.addEventListener('keydown',event=>{if(event.key==='Escape'&&state.draft&&entry.saving!==field){event.preventDefault();cancelClassificationCell(entry,state);}});
        }else if(priceColumns[i]){const priceField=priceColumns[i];td.className='classification-cell price-cell';const state={field:priceField,td,draft:null};entry.prices.set(priceField,state);}});
        entry.action=document.createElement('td');tr.appendChild(entry.action);
      }
      if(Number(item.classification_version||0)<Number(entry.item.classification_version||0))item={...item,classification_values:entry.item.classification_values,classification_version:entry.item.classification_version};
      if(Number(item._price_read_started||0)<Number(entry.item._price_read_started||0)){
        for(const k of ['price_edit','_price_read_started','_price_broker','entry_price','order_price','stop_loss','take_profit'])item[k]=entry.item[k];
      }
      entry.item=item;entry.present=true;
      entry.action.innerHTML='';renderActionCell(item,entry.action,{allowAction:true});
      const tr=entry.tr;tr.className=item.source_stale?'stale-row':'';
      tr.title=item.source_stale?safeDiagnostic(item.stale_reason||'Last-known-good broker state'):item.source_last_success_at?'Last successful source read: '+formatTimestamp(item.source_last_success_at):'';
      synchronizeDrafts(entry);
      rowValues(item).forEach((value,i)=>{const field=classificationColumns[i];if(field)renderClassificationCell(entry,entry.cells.get(field));else if(priceColumns[i]){
        const state=entry.prices.get(priceColumns[i]);const resume=Object.values(priceOperations).find(op=>op.target===item.price_edit?.target&&op.field===state.field);
        if(resume&&!state.draft)openPriceCell(entry,state,resume);renderPriceCell(entry,state);
      }else{const text=String(fmt(value));if(tr.children[i].textContent!==text)tr.children[i].textContent=text;}});
      desired.push(tr);
    }
    for(const [key,entry] of classificationRows){if(entry.present)continue;if([...entry.cells.values(),...entry.prices.values()].some(state=>state.draft)){
      entry.action.innerHTML='';synchronizeDrafts(entry);for(const state of entry.cells.values())renderClassificationCell(entry,state);desired.push(entry.tr);
      for(const state of entry.prices.values())renderPriceCell(entry,state);
    }else{entry.tr.remove();classificationRows.delete(key);}}
    // Preserve the mounted dropdowns and focus through ordinary publications.
    desired.forEach((tr,i)=>{if(tbody.children[i]!==tr)tbody.insertBefore(tr,tbody.children[i]||null);});
    if(emptyState){emptyState.style.display=items.length?'none':'block';emptyState.textContent=coverage.empty_confirmed===true?'No open orders or positions.':coverage.pending?'Loading broker data':'Broker data incomplete';}
  };
  const confirmVisibility=()=>{
    for(const [key,state] of pendingVisibilityChecks){
      const visible=(window.__openOrdersItems||[]).some((item)=>!item.source_stale&&!item.source_pending&&
        Number(item.collection_version??knownVersion)===knownVersion&&itemMatchesSubmission(item,state.event));
      if(visible){pendingVisibilityChecks.delete(key);setBadge('Submitted; broker state is now visible.');}
    }
  };
  const refresh=async(reason='manual')=>{
    if(refreshInFlight){
      if(['broadcast','submit'].includes(reason))refreshQueued=true;
      return refreshInFlight.then(()=>{if(refreshQueued){refreshQueued=false;return refresh('progress');}});
    }
    refreshInFlight=(async()=>{
      try{
        if(!hasSnapshot)setBadge('Loading broker data');
        const payload=await fetchJson(reason==='manual'?'/api/open-orders?force=1':'/api/open-orders');
        if(payload.refresh_superseded)return;
        const renderedVersion=Number(payload.version);
        if(knownVersion!==null&&Number.isFinite(renderedVersion)&&renderedVersion<knownVersion)return;
        if(Number.isFinite(renderedVersion))knownVersion=renderedVersion;
        knownRevision=Number(payload.progress_revision||0);
        hasSnapshot=true;refreshNeedsRecovery=false;
        window.__openOrdersCoverage={pending:Boolean(payload.pending),empty_confirmed:payload.empty_confirmed===true,core_complete:payload.core_complete===true};
        render(payload.items||[],payload.errors||[],payload.warnings||[],window.__openOrdersCoverage);
        const errors=(payload.errors||[]).length,warnings=(payload.warnings||[]).length;
        const pending=(payload.sources||[]).filter((source)=>source.state==='pending').length;
        const status=payload.pending?(hasData?'Updating broker data':'Loading broker data'):payload.core_complete?'Updated':'Broker data incomplete';
        const details=[pending?pending+' sources pending':'',errors?errors+' errors':'',warnings?warnings+' warnings':''].filter(Boolean).join(', ');
        setBadge(status+(details?' ('+details+')':''));
        confirmVisibility();
      }catch(err){
        refreshNeedsRecovery=true;
        if(emptyState&&!hasData){emptyState.style.display='block';emptyState.textContent='Broker data incomplete';}
        setBadge('Broker data incomplete (snapshot request failed: '+safeDiagnostic(err?.message||err,'request failed')+')');
      }finally{refreshInFlight=null;}
    })();
    return refreshInFlight;
  };
  const eventValue=(event,...keys)=>{for(const key of keys){const value=String(event?.[key]??'').trim();if(value)return value;}return '';};
  const itemMatchesSubmission=(item,event)=>{
    const broker=eventValue(event,'broker').toLowerCase();const account=eventValue(event,'account').toLowerCase();const category=eventValue(event,'category').toLowerCase();const symbol=eventValue(event,'symbol','instrument').toUpperCase();
    if(broker&&eventValue(item,'broker','venue').toLowerCase()!==broker)return false;
    if(account&&eventValue(item,'account').toLowerCase()!==account)return false;
    if(category&&eventValue(item,'category').toLowerCase()!==category)return false;
    const orderId=eventValue(event,'order_id','orderId');
    const orderLinkId=eventValue(event,'order_link_id','orderLinkId');
    const contextId=eventValue(event,'calculation_context_id','calculationContextId','context_id','contextId');
    if(orderId&&eventValue(item,'id','order_id')===orderId)return true;
    if(orderLinkId&&eventValue(item,'order_link_id','orderLinkId')===orderLinkId)return true;
    if(contextId&&eventValue(item,'calculation_context_id','context_id')===contextId)return true;
    if(orderId||orderLinkId||contextId)return false;
    if(!symbol)return false;
    if(eventValue(item,'instrument').toUpperCase()!==symbol)return false;
    const openedRaw=item?.opened_at;const openedNumber=Number(openedRaw);const opened=Number.isFinite(openedNumber)&&String(openedRaw??'').trim()!==''?(openedNumber<1_000_000_000_000?openedNumber*1000:openedNumber):Date.parse(String(openedRaw||''));const submitted=Number(event?.submittedAt||0);
    return Number.isFinite(opened)&&submitted>0&&opened>=(submitted-5*60*1000);
  };
  const runVisibilityCheck=async(key)=>{
    const state=pendingVisibilityChecks.get(key);if(!state||state.running||document.hidden)return;
    state.running=true;
    try{await refresh('submit');confirmVisibility();}finally{state.running=false;}
  };
  const handleStateChange=(event)=>{
    if(!event||event.type!=='state-changed')return;
    if(String(event.action||'').toLowerCase()!=='submit'){
      if(document.hidden){refreshQueued=true;return;}
      refresh('broadcast').catch(()=>{});return;
    }
    const key=String(event.eventId||[event.submittedAt||Date.now(),eventValue(event,'order_id','orderId'),
      eventValue(event,'calculation_context_id','calculationContextId'),eventValue(event,'symbol','instrument')].join('|'));
    if(seenSubmitEvents.has(key))return;
    seenSubmitEvents.add(key);if(seenSubmitEvents.size>512)seenSubmitEvents.delete(seenSubmitEvents.values().next().value);
    // Acceptance is evidence-driven, with no elapsed or attempt cutoff.
    if(pendingVisibilityChecks.size>=512)pendingVisibilityChecks.delete(pendingVisibilityChecks.keys().next().value);
    pendingVisibilityChecks.set(key,{event:{...event},running:false});
    setBadge('Submitted; waiting for broker visibility');
    runVisibilityCheck(key).catch(()=>{});
  };
  const pollVersion=async()=>{
    if(document.hidden)return;
    if(versionInFlight)return versionInFlight;
    versionInFlight=(async()=>{try{
      await reconcilePriceOperations();
      const payload=await fetchJson('/api/open-orders/version');const nextVersion=Number(payload?.version);
      const revision=Number(payload.progress_revision||0);
      if(Number.isFinite(nextVersion)&&(refreshNeedsRecovery||knownVersion===null||nextVersion!==knownVersion||revision!==knownRevision))await refresh('progress');
    }catch(_){}finally{versionInFlight=null;}})();
    return versionInFlight;
  };
  const startVersionPolling=()=>{if(versionPollTimer) return; versionPollTimer=setInterval(pollVersion,POLL_MS);};
  const stopVersionPolling=()=>{if(!versionPollTimer) return; clearInterval(versionPollTimer); versionPollTimer=null;};
  if(typeof window!=='undefined'&&window.__OPEN_ORDERS_TESTING__){window.__openOrdersTestHooks={refresh,pollVersion,handleStateChange,runVisibilityCheck,itemMatchesSubmission,getState:()=>({knownVersion,refreshQueued,refreshInFlight:Boolean(refreshInFlight),pendingVisibilityChecks:pendingVisibilityChecks.size,hasData})};}
  stateChannel?.addEventListener('message',(event)=>handleStateChange(event?.data));
  if(typeof window?.addEventListener==='function')window.addEventListener('storage',(event)=>{if(event.key!==STORAGE_EVENT_KEY||!event.newValue)return;try{handleStateChange(JSON.parse(event.newValue));}catch(_){}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stopVersionPolling();return;}startVersionPolling();pollVersion();for(const key of pendingVisibilityChecks.keys())runVisibilityCheck(key).catch(()=>{});if(refreshQueued&&!refreshInFlight){refreshQueued=false;refresh('visible').catch(()=>{});}});
  refreshBtn?.addEventListener('click',()=>refresh('manual')); if(emptyState)emptyState.textContent='Loading broker data'; refresh('initial').then(pollVersion); if(!document.hidden) startVersionPolling();
})();
