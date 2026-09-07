import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERGED_ALERTS = ROOT / "render" / "static" / "merged_alerts.js"


def test_merged_alerts_runtime_expiry_default_round_trip_and_notification_channels() -> None:
    node = shutil.which("node")
    if not node:
        return
    harness = r"""
const fs = require('fs');
const vm = require('vm');
class Element {
  constructor(id='',tag='') { this.id=id; this.tag=tag.toUpperCase(); this.children=[]; this.style={}; this.listeners={}; this.disabled=false; this.value=''; this.textContent=''; this._innerHTML=''; this.checked=false; this.options=[]; this.className=''; this.selectedIndex=0; }
  set innerHTML(value){ this._innerHTML=value; if(value===''){this.children=[];this.options=[];} }
  get innerHTML(){ return this._innerHTML; }
  append(...nodes){ nodes.forEach((node)=>this.appendChild(node)); }
  appendChild(node){ this.children.push(node); if(this.tag==='SELECT'&&typeof node==='object'){this.options.push(node);if(!this.value)this.value=node.value;} return node; }
  addEventListener(type, cb){ this.listeners[type]=cb; }
  dispatch(type){ if(this.listeners[type]) this.listeners[type]({preventDefault(){}}); }
}
const elements={}; const get=(id)=>elements[id]||(elements[id]=new Element(id));
['monitor-target','monitor-status','monitor-health','monitor-wait-seconds','monitor-threshold','monitor-save-settings','monitor-reload-settings','monitor-test-alert','monitor-settings-status','monitor-custom-alerts'].forEach(get);
elements['monitor-target'].value='bybit';
const document={createElement(tag){return new Element('',tag);},getElementById(id){return get(id);}};
const NativeDate=Date; const fixedNow=new NativeDate(2024,0,31,10,30,0,0).getTime();
class FixedDate extends NativeDate { constructor(...args){ super(...(args.length?args:[fixedNow])); } static now(){ return fixedNow; } }
const fetchCalls=[]; const futureExpiry='2030-05-06T03:04:00Z'; const pastExpiry='2020-01-01T00:00:00Z'; const savedPayloads=[]; const savedRequests=[];
const fetch=async(url,options={})=>{
  const method=options.method||'GET'; fetchCalls.push([url,method]);
  if(url.includes('/status')) return {ok:true,json:async()=>({ui_status:'running',phase:'waiting',heartbeat_fresh:true,pid_alive:true}),text:async()=>''};
  if(url.includes('/settings')) return {ok:true,json:async()=>({wait_seconds:5,percent_threshold:1.2,telegram_ready:false,email_ready:true}),text:async()=>''};
  if(url.includes('/api/resolve-symbol')) { const input=decodeURIComponent((url.match(/[?&]symbol=([^&]+)/)||[])[1]||''); if(!url.includes('prefer=auto')) throw new Error('alerts resolver did not request automatic routing'); if(input==='USDJPY') return {ok:true,json:async()=>({resolved_symbol:'USD_JPY',source:'oanda'}),text:async()=>''}; return {ok:true,json:async()=>({resolved_symbol:'BTCUSDT',source:'bybit'}),text:async()=>''}; }
  if(url.includes('/custom-alerts')&&method==='GET') { if(url.includes('/api/oanda-alerts/')) return {ok:true,json:async()=>({alerts:savedRequests.filter((request)=>request.url==='/api/oanda-alerts/custom-alerts').map((request,index)=>({id:'oanda-'+index,...request.payload}))}),text:async()=>''}; return {ok:true,json:async()=>({alerts:[{id:'future',symbol:'BTCUSDT',kind:'price',direction:'above',target_price:2,enabled:true,expires_at:futureExpiry,active_period:'weekend_brisbane'},{id:'past',symbol:'ETHUSDT',kind:'move',direction:'up',threshold:1,unit:'pct',window_seconds:60,enabled:true,expires_at:pastExpiry,expired:true}]}),text:async()=>''}; }
  if(url.includes('/custom-alerts')&&method==='POST'){const payload=JSON.parse(options.body);savedPayloads.push(payload);savedRequests.push({url,payload});return {ok:true,json:async()=>({ok:true}),text:async()=>'{"ok":true}'};}
  if(url.includes('/notification-test')) return {ok:true,status:200,text:async()=>JSON.stringify({channels:{telegram:{configured:false,sent:false},email:{configured:true,sent:true}}})};
  return {ok:true,json:async()=>({}),text:async()=>''};
};
const ctx={document,fetch,window:{alert(){},confirm(){return true;}},console,setInterval(){return 0;},Date:FixedDate};
vm.createContext(ctx); vm.runInContext(fs.readFileSync('render/static/merged_alerts.js','utf8'),ctx);
const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));
const allNodes=(root)=>[root,...(root.children||[]).filter((child)=>typeof child==='object').flatMap(allNodes)];
(async()=>{
  await sleep(0);
  const section=elements['monitor-custom-alerts'].children[0]; if(!section) throw new Error('custom alerts controls not rendered');
  const formGrid=section.children[1];
  const control=(name)=>{const label=formGrid.children.find((item)=>item.children?.[0]===name);if(!label)throw new Error('missing '+name);return label.children[1];};
  const expiry=control('Expiry'); const activePeriod=control('Active period'); const symbol=control('Symbol'); const target=control('Target price'); const kind=control('Type'); const threshold=control('Move threshold'); const unit=control('Unit'); const windowSelect=control('Window'); const save=()=>allNodes(section).find((node)=>node.textContent==='Save alert'||node.textContent==='Update alert');
  const editButtons=()=>allNodes(section).filter((node)=>node.textContent==='Edit');
  const optionLabels=()=>expiry.options.map((option)=>option.textContent);
  const settle=async()=>{await sleep(0);await sleep(0);await sleep(0);};
  const expected=(minutes)=>new NativeDate(fixedNow+minutes*60000).toISOString();
  const expectedMonth=new NativeDate(2024,1,29,10,30,0,0).toISOString();
  if(JSON.stringify(optionLabels())!==JSON.stringify(['Lifetime','1 hour','4 hours','1 day','1 week','1 month'])) throw new Error('expiry preset choices missing');
  if(expiry.value!=='lifetime') throw new Error('Lifetime is not the default');
  if(JSON.stringify(activePeriod.options.map((option)=>option.textContent))!==JSON.stringify(['Anytime','Weekend only — Saturday 7:00 am to Monday 7:00 am, Brisbane time'])) throw new Error('active period choices missing');
  if(activePeriod.value!=='anytime') throw new Error('Anytime is not the active-period default');
  if(allNodes(section).some((node)=>node.type==='datetime-local')) throw new Error('manual datetime input remains');
  const saveNew=async(preset)=>{symbol.value='BTC';target.value='2';expiry.value=preset;activePeriod.value='anytime';save().dispatch('click');await settle();return savedPayloads.at(-1);};
  if(Object.hasOwn(await saveNew('lifetime'),'expires_at')) throw new Error('Lifetime did not omit expiry');
  if(savedPayloads.at(-1).active_period!=='anytime') throw new Error('Anytime did not save');
  const durationCases=[['1h',60],['4h',240],['1d',1440],['1w',10080]];
  for(const [preset,minutes] of durationCases){const payload=await saveNew(preset);if(payload.expires_at!==expected(minutes))throw new Error(preset+' expiry mismatch');}
  const monthPayload=await saveNew('1mo'); if(monthPayload.expires_at!==expectedMonth) throw new Error('calendar month clamping mismatch');

  editButtons()[0].dispatch('click');
  if(expiry.value!=='keep-current'||!expiry.options.at(-1).textContent.startsWith('Keep current expiry — ')) throw new Error('future current-expiry option missing');
  if(activePeriod.value!=='weekend_brisbane') throw new Error('weekend active period did not load');
  save().dispatch('click'); await settle(); if(savedPayloads.at(-1).expires_at!==futureExpiry||savedPayloads.at(-1).active_period!=='weekend_brisbane') throw new Error('future expiry/period was not preserved');
  editButtons()[1].dispatch('click'); if(expiry.value!=='keep-current'||activePeriod.value!=='anytime'||windowSelect.value!=='60') throw new Error('expired current expiry, active period, or movement window did not load');
  save().dispatch('click'); await settle(); if(savedPayloads.at(-1).expires_at!==pastExpiry||savedPayloads.at(-1).active_period!=='anytime'||savedPayloads.at(-1).window_seconds!==60) throw new Error('expired expiry/period/window was not preserved');
  editButtons()[1].dispatch('click'); expiry.value='1h'; save().dispatch('click'); await settle(); if(savedPayloads.at(-1).expires_at!==expected(60)) throw new Error('replacement preset did not restart expiry');
  editButtons()[0].dispatch('click'); expiry.value='lifetime'; save().dispatch('click'); await settle(); if(Object.hasOwn(savedPayloads.at(-1),'expires_at')) throw new Error('Lifetime did not clear current expiry');
  const saveMove=async(windowValue)=>{kind.value='move';kind.dispatch('change');symbol.value='BTC';threshold.value='1';unit.value='pct';windowSelect.value=String(windowValue);activePeriod.value='weekend_brisbane';save().dispatch('click');await settle();const payload=savedPayloads.at(-1);if(payload.window_seconds!==windowValue||payload.active_period!=='weekend_brisbane')throw new Error('movement window/active period save failed: '+windowValue);};
  await saveMove(60); await saveMove(300); await saveMove(14400);
  editButtons()[0].dispatch('click'); if(expiry.options.length!==7) throw new Error('temporary option missing before reset');
  allNodes(section).find((node)=>node.textContent==='Reset').dispatch('click'); if(expiry.options.length!==6||activePeriod.value!=='anytime') throw new Error('reset retained temporary expiry option or active period');
  elements['monitor-target'].value='oanda'; elements['monitor-target'].dispatch('change'); await settle(); if(expiry.options.length!==6||activePeriod.value!=='anytime') throw new Error('monitor switch retained temporary expiry option or active period');
  elements['monitor-target'].value='bybit'; elements['monitor-target'].dispatch('change'); await settle();
  allNodes(section).find((node)=>node.textContent==='Reset').dispatch('click');
  symbol.value='USDJPY'; target.value='155'; expiry.value='1w'; activePeriod.value='weekend_brisbane'; save().dispatch('click'); await settle();
  const usdJpyRequests=savedRequests.filter((request)=>request.payload.symbol==='USD_JPY');
  if(usdJpyRequests.length!==1||usdJpyRequests[0].url!=='/api/oanda-alerts/custom-alerts') throw new Error('USDJPY did not make exactly one OANDA alert save');
  if(savedRequests.some((request)=>request.url==='/api/bybit-alerts/custom-alerts'&&request.payload.symbol==='USD_JPY')) throw new Error('USDJPY attempted a Bybit alert save');
  if(fetchCalls.filter(([url])=>url.includes('/api/resolve-symbol')&&url.includes('symbol=USDJPY')).length!==1) throw new Error('USDJPY did not resolve exactly once');
  if(usdJpyRequests[0].payload.expires_at!==expected(10080)||usdJpyRequests[0].payload.active_period!=='weekend_brisbane'||usdJpyRequests[0].payload.enabled!==true) throw new Error('USDJPY routed save lost alert settings');
  if(elements['monitor-target'].value!=='oanda'||!fetchCalls.some(([url,method])=>url.includes('/api/oanda-alerts/custom-alerts')&&method==='GET')||!fetchCalls.some(([url])=>url.includes('/api/oanda-alerts/status'))||!fetchCalls.some(([url])=>url.includes('/api/oanda-alerts/settings'))||!allNodes(section).some((node)=>String(node.textContent||'').includes('USD_JPY'))) throw new Error('USDJPY routing did not switch to the OANDA view');
  elements['monitor-target'].value='bybit'; elements['monitor-target'].dispatch('change'); await settle();
  symbol.value='BTC'; target.value='2'; save().dispatch('click'); await settle();
  const btcRequest=savedRequests.at(-1); if(btcRequest.url!=='/api/bybit-alerts/custom-alerts'||btcRequest.payload.symbol!=='BTCUSDT') throw new Error('ordinary Bybit BTC routing changed');
  if(!allNodes(section).some((node)=>String(node.textContent||'').includes('Expired'))) throw new Error('expired status not displayed');
  if(!allNodes(section).some((node)=>String(node.textContent||'').includes('Active: Weekend only — Saturday 7:00 am to Monday 7:00 am, Brisbane time'))||!allNodes(section).some((node)=>String(node.textContent||'').includes('Active: Anytime'))) throw new Error('active period row labels missing');
  const testBtn=elements['monitor-test-alert']; testBtn.dispatch('click'); await sleep(0);
  const status=elements['monitor-settings-status'].textContent;
  if(!status.includes('Telegram: not configured')||!status.includes('Email: sent')) throw new Error('separate channel results missing: '+status);
  if(!fetchCalls.some(([url,method])=>url.includes('/api/bybit-alerts/notification-test')&&method==='POST')) throw new Error('notification-test endpoint not called');
})().catch((err)=>{console.error(err);process.exit(1);});
"""
    result = subprocess.run([node, "-e", harness], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
