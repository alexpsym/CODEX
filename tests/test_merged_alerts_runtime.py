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
const fetchCalls=[]; const futureExpiry='2099-05-06T03:04:00Z'; let savedPayload=null;
const fetch=async(url,options={})=>{
  const method=options.method||'GET'; fetchCalls.push([url,method]);
  if(url.includes('/status')) return {ok:true,json:async()=>({ui_status:'running',phase:'waiting',heartbeat_fresh:true,pid_alive:true}),text:async()=>''};
  if(url.includes('/settings')) return {ok:true,json:async()=>({wait_seconds:5,percent_threshold:1.2,telegram_ready:false,email_ready:true}),text:async()=>''};
  if(url.includes('/api/resolve-symbol')) return {ok:true,json:async()=>({resolved_symbol:'BTCUSDT'}),text:async()=>''};
  if(url.includes('/custom-alerts')&&method==='GET') return {ok:true,json:async()=>({alerts:[{id:'future',symbol:'BTCUSDT',kind:'price',direction:'above',target_price:2,enabled:true,expires_at:futureExpiry},{id:'past',symbol:'ETHUSDT',kind:'move',direction:'up',threshold:1,unit:'pct',window_seconds:60,enabled:true,expires_at:'2020-01-01T00:00:00Z',expired:true}]}),text:async()=>''};
  if(url.includes('/custom-alerts')&&method==='POST'){savedPayload=JSON.parse(options.body);return {ok:true,json:async()=>({ok:true}),text:async()=>'{"ok":true}'};}
  if(url.includes('/notification-test')) return {ok:true,status:200,text:async()=>JSON.stringify({channels:{telegram:{configured:false,sent:false},email:{configured:true,sent:true}}})};
  return {ok:true,json:async()=>({}),text:async()=>''};
};
const ctx={document,fetch,window:{alert(){},confirm(){return true;}},console,setInterval(){return 0;},Date};
vm.createContext(ctx); vm.runInContext(fs.readFileSync('render/static/merged_alerts.js','utf8'),ctx);
const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));
const allNodes=(root)=>[root,...(root.children||[]).filter((child)=>typeof child==='object').flatMap(allNodes)];
(async()=>{
  await sleep(0);
  const section=elements['monitor-custom-alerts'].children[0]; if(!section) throw new Error('custom alerts controls not rendered');
  const formGrid=section.children[1];
  const expiryLabel=formGrid.children.find((label)=>label.children?.[0]==='Expiry');
  const expiryDateLabel=formGrid.children.find((label)=>label.children?.[0]==='Expiry date / time');
  if(!expiryLabel||!expiryDateLabel) throw new Error('expiry controls missing');
  const expiryMode=expiryLabel.children[1]; const expiryDate=expiryDateLabel.children[1];
  if(expiryMode.value!=='none'||!expiryDate.disabled) throw new Error('No expiry is not the default');
  const edit=allNodes(section).find((node)=>node.textContent==='Edit'); if(!edit) throw new Error('edit control missing'); edit.dispatch('click');
  if(expiryMode.value!=='datetime'||!expiryDate.value) throw new Error('edit did not load expiry');
  const save=allNodes(section).find((node)=>node.textContent==='Update alert'); if(!save) throw new Error('update control missing'); save.dispatch('click'); await sleep(0); await sleep(0);
  if(!savedPayload?.expires_at||new Date(savedPayload.expires_at).getTime()!==new Date(futureExpiry).getTime()) throw new Error('expiry UTC round trip failed');
  if(!allNodes(section).some((node)=>String(node.textContent||'').includes('Expired'))) throw new Error('expired status not displayed');
  const testBtn=elements['monitor-test-alert']; testBtn.dispatch('click'); await sleep(0);
  const status=elements['monitor-settings-status'].textContent;
  if(!status.includes('Telegram: not configured')||!status.includes('Email: sent')) throw new Error('separate channel results missing: '+status);
  if(!fetchCalls.some(([url,method])=>url.includes('/api/bybit-alerts/notification-test')&&method==='POST')) throw new Error('notification-test endpoint not called');
})().catch((err)=>{console.error(err);process.exit(1);});
"""
    result = subprocess.run([node, "-e", harness], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
