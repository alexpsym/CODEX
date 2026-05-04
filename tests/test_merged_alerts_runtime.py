import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGED_ALERTS = ROOT / 'render' / 'static' / 'merged_alerts.js'


def test_merged_alerts_js_syntax() -> None:
    node = shutil.which('node')
    if not node:
        return
    result = subprocess.run([node, '--check', str(MERGED_ALERTS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_merged_alerts_source_has_no_expiry_form_artifacts() -> None:
    src = MERGED_ALERTS.read_text(encoding='utf-8')
    assert "form.querySelector('[data-alert-expiry]')" not in src
    assert 'expirySelect?.value' not in src
    assert 'expiry_choice' not in src


def test_merged_alerts_runtime_init_and_telegram_paths() -> None:
    node = shutil.which('node')
    if not node:
        return
    harness = r"""
const fs = require('fs');
const vm = require('vm');

class Element {
  constructor(id='') { this.id=id; this.children=[]; this.style={}; this.listeners={}; this.disabled=false; this.value=''; this.textContent=''; this.innerHTML=''; this.checked=false; this.options=[]; this.className=''; }
  append(...nodes){ this.children.push(...nodes); }
  appendChild(node){ this.children.push(node); return node; }
  addEventListener(type, cb){ this.listeners[type]=cb; }
  dispatch(type){ if(this.listeners[type]) this.listeners[type]({preventDefault(){}}); }
}
const elements = {};
const get = (id) => elements[id] || (elements[id]=new Element(id));
['monitor-target','monitor-status','monitor-health','monitor-wait-seconds','monitor-threshold','monitor-save-settings','monitor-reload-settings','monitor-test-alert','monitor-settings-status','monitor-custom-alerts'].forEach(get);
elements['monitor-target'].value='bybit';

const document = { createElement(){ return new Element(); }, getElementById(id){ return get(id); } };
const fetchCalls=[];
let pushMode='not_configured';
const fetch = async (url, options={}) => {
  fetchCalls.push([url, options.method||'GET']);
  if (url.includes('/status')) return { ok:true, text: async()=>JSON.stringify({ui_status:'running', phase:'waiting', heartbeat_fresh:true, pid_alive:true}), json: async()=>({ui_status:'running', phase:'waiting', heartbeat_fresh:true, pid_alive:true}) };
  if (url.includes('/settings')) return { ok:true, text: async()=>JSON.stringify({wait_seconds:5, percent_threshold:1.2, push_ready:false}), json: async()=>({wait_seconds:5, percent_threshold:1.2, push_ready:false}) };
  if (url.includes('/custom-alerts')) return { ok:false, status:503, statusText:'Service Unavailable', text: async()=> 'custom alerts offline', json: async()=>({}) };
  if (url.includes('/push-test')) {
    if (pushMode==='not_configured') return { ok:true, status:200, text: async()=>JSON.stringify({configured:false, sent:false, detail:'token missing'}) };
    if (pushMode==='sent') return { ok:true, status:200, text: async()=>JSON.stringify({configured:true, sent:true, detail:'sent'}) };
    return { ok:false, status:500, text: async()=>JSON.stringify({configured:true, sent:false, detail:'telegram api error'}) };
  }
  return { ok:true, text: async()=> '{}', json: async()=>({}) };
};

const ctx = { document, fetch, window:{ alert(){}, confirm(){return true;} }, console, setInterval(){ return 0; } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('render/static/merged_alerts.js','utf8'), ctx);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  await sleep(0);
  if (elements['monitor-status'].textContent === 'Checking…' || elements['monitor-status'].textContent === 'Checking...') throw new Error('status did not update');
  const testBtn = elements['monitor-test-alert'];
  testBtn.listeners['click']({preventDefault(){}});
  await sleep(0);
  if (!elements['monitor-settings-status'].textContent.includes('Telegram not configured')) throw new Error('missing not configured state');
  pushMode='sent';
  testBtn.listeners['click']({preventDefault(){}});
  await sleep(0);
  if (!elements['monitor-settings-status'].textContent.includes('Test sent')) throw new Error('missing sent state');
  pushMode='failed';
  testBtn.listeners['click']({preventDefault(){}});
  await sleep(0);
  if (!elements['monitor-settings-status'].textContent.includes('Test failed')) throw new Error('missing failed state');
  const called = fetchCalls.some(([u,m]) => u.includes('/api/bybit-alerts/push-test') && m==='POST');
  if (!called) throw new Error('push-test endpoint not called');
})().catch((err) => { console.error(err); process.exit(1); });
"""
    result = subprocess.run([node, '-e', harness], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
