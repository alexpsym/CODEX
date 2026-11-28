import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "calculator.js"


def test_pepperstone_automatic_buffer_requires_no_user_input() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');const source=fs.readFileSync(process.argv[1],'utf8');
class B { constructor(v){this.dataset={v};this.listeners={};this.disabled=false;this.attrs={};this.classList={toggle(){},add(){},remove(){}};} addEventListener(e,f){this.listeners[e]=f;} click(){return this.listeners.click&&this.listeners.click();} setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} removeAttribute(k){delete this.attrs[k];} }
class E extends B { constructor(id){super('');this.id=id;this.value='';this.textContent='';this.style={};this._html='';this.buttons=[];this.checked=false;} set innerHTML(v){this._html=String(v||'');this.buttons=[...this._html.matchAll(/data-v="([^"]*)"/g)].map(m=>new B(m[1]));} get innerHTML(){return this._html;} querySelectorAll(s){return s==='button'?this.buttons:[];} }
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-pepperstone-risk-preflight','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));for(const [id,vals] of Object.entries({'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']}))el[id].buttons=vals.map(v=>new B(v));
Object.assign(el['calc-symbol'],{value:'USD_JPY'});el['calc-sl-ticks'].value='43';el['calc-rr'].value='2';el['calc-risk'].value='10';
const calls=[];const reply=data=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});const fetch=async(url,opts={})=>{const body=opts.body?JSON.parse(opts.body):null;calls.push({url,body});if(url==='/api/calculator/bootstrap')return reply({app_profile:'local',trendline_plans_available:false,webhook:{available:true}});if(url.startsWith('/api/calculator/instrument'))return reply({asset:'fx',broker:'pepperstone',symbol:'USD_JPY'});if(url==='/api/calculator/quote')return reply({asset:'fx',broker:'pepperstone',venue:'Pepperstone',resolved_venue:'Pepperstone',symbol:'USD_JPY',tick_size:'0.001',entry_price:'150',stop_price:'149.957',target_price:'150.086',quantity:'1',estimated_total_loss_aud:'10',estimated_total_loss:'10',pepperstone_mt5_export_status:{ready:true,code:null,reason:''},pepperstone_risk_buffer_preflight:{stop_points:43,preferred_maximum_buffer_points:50,minimum_automatic_buffer_points:10,conservative_max_buffer_points:14,planned_buffer_points:14,minimum_compatible_stop_points:30,compatible:true}});if(url.startsWith('/api/calculator/journal-summary')||url.startsWith('/api/instrument-specs'))return reply({status:'no_data'});return reply({status:'no_data'});};
const context={document:{getElementById:id=>el[id]},fetch,navigator:{clipboard:{writeText:async()=>{}}},setTimeout:(fn,ms)=>{if(ms===250)fn();return 1;},clearTimeout(){},setInterval:()=>1,clearInterval(){},console,Date,URL,URLSearchParams,AbortController};vm.runInNewContext(source,context);const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{await flush();await flush();el['asset-toggle'].buttons.find(b=>b.dataset.v==='fx').click();await flush();await flush();el['broker-toggle'].buttons.find(b=>b.dataset.v==='pepperstone').click();await flush();await flush();await el['calc-quote'].listeners.click();console.log(JSON.stringify({message:el['calc-quote-status'].textContent,visible:el['calc-pepperstone-set'].style.display!== 'none',enabled:!el['calc-pepperstone-set'].disabled,quotePayload:calls.find(c=>c.url==='/api/calculator/quote').body}));})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert "calc-pepperstone-risk-buffer" not in JS_PATH.read_text(encoding="utf-8")
    assert "risk_slippage_buffer_points" not in JS_PATH.read_text(encoding="utf-8")
    assert data["visible"] is True and data["enabled"] is True
    assert data["message"] == "USD/JPY \u00b7 Razor \u00b7 Preset ready"
    assert "risk_slippage_buffer_points" not in data["quotePayload"]


def test_pepperstone_target_mode_and_chart_ticks_invalidate_and_quote_without_auto_export() -> None:
    node = r"C:\Program Files\nodejs\node.exe"
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs'),vm=require('vm');const source=fs.readFileSync(process.argv[1],'utf8');
class B{constructor(v){this.dataset={v};this.listeners={};this.disabled=false;this.attrs={};this.classList={toggle(){},add(){},remove(){}};}addEventListener(e,f){this.listeners[e]=f;}click(){if(!this.disabled&&this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k];}removeAttribute(k){delete this.attrs[k];}}
class E extends B{constructor(id){super('');this.id=id;this.value='';this.textContent='';this.style={};this._html='';this.buttons=[];this.checked=false;this.dataset={};}set innerHTML(v){this._html=String(v||'');this.buttons=[...this._html.matchAll(/data-v="([^"]*)"/g)].map(m=>new B(m[1]));}get innerHTML(){return this._html;}appendChild(node){if(node.id)el[node.id]=node;return node;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
const ids=['calc-commission-chart-r','calc-results-disclosure','calc-stop-guidance','pepperstone-account-type-wrap','pepperstone-account-type-toggle','calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-pepperstone-risk-preflight','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-tp-ticks','calc-target-mode','calc-chart-tick-auto','calc-chart-tick-source','calc-chart-tick-size-override-enabled','calc-chart-tick-size','rr-wrap','tp-ticks-wrap','chart-tick-size-wrap','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const el=Object.fromEntries(ids.map(id=>[id,new E(id)]));for(const [id,vals] of Object.entries({'pepperstone-account-type-toggle':['standard','razor'],'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']}))el[id].buttons=vals.map(v=>new B(v));
el['calc-symbol'].value='GBPUSD';el['calc-sl-ticks'].value='43';el['calc-tp-ticks'].value='25';el['calc-rr'].value='2';el['calc-risk'].value='10';el['calc-target-mode'].value='rr';
let fixtureState='pending',fixtureEstimateAvailable=true,fixtureStep=1,fixtureMinimum=4,fixtureStaticMinimum=null,fixtureBlockedReason='',clockOffset=0,holdInstrument=false,delayed=null;const calls=[];const reply=data=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
let holdPreview=false, releasePreview; const hint={chart_r:'4.10',commission_r:'0.60'};
const fetch=async(url,opts={})=>{const body=opts.body?JSON.parse(opts.body):null;calls.push({url,body});if(url==='/api/calculator/commission-preview'){if(holdPreview)return new Promise(resolve=>{releasePreview=()=>resolve(reply({commission_chart_r:{chart_r:'99.00',commission_r:'9.00'}}));});return reply({commission_chart_r:hint});}if(url==='/api/calculator/bootstrap')return reply({app_profile:'local',trendline_plans_available:false,webhook:{available:true}});if(url.startsWith('/api/calculator/prewarm-account'))return reply({ready_for_quote:true,asset:'fx',account:'demo'});if(url.startsWith('/api/calculator/instrument?')){const params=new URL('http://local'+url).searchParams,broker=params.get('broker')||'oanda';const data={asset:'fx',broker,symbol:params.get('symbol').replace('_','').toUpperCase()==='USDJPY'?'USD_JPY':'GBP_USD',chart_tick_size_default:'0.00001',chart_tick_size_source:'Pepperstone GBPUSD TradingView chart (user screenshot measurement)',chart_tick_profile_id:'pepperstone-gbpusd-tradingview-screenshot-v1',pepperstone_stop_guidance:{state:params.get('estimate_razor_from_standard')==='true'&&!fixtureEstimateAvailable?'pending':fixtureState==='ready'&&params.get('order_type')==='limit'&&Number(params.get('entry_price'))<=0?'blocked':fixtureState,limit_entry_state:params.get('order_type')==='limit'&&Number(params.get('entry_price'))<=0?'invalid':'valid',automatic_buffer_floor_points:0,minimum_ticks:fixtureState==='pending'?fixtureStep:params.get('estimate_razor_from_standard')==='true'?2:fixtureMinimum,static_minimum_ticks:fixtureStaticMinimum??fixtureStep,buffer_policy:{preferred_maximum_buffer_points:50,distance_cap_numerator:1,distance_cap_denominator:3,request_deviation_points:10,points_per_chart_tick_numerator:1,points_per_chart_tick_denominator:1},maximum_ticks:fixtureState==='pending'?null:1200,step_ticks:fixtureStep,first_valid_tick:4,valid_for_seconds:fixtureState==='pending'?0:60,quote_age_seconds:fixtureState==='pending'?120:0,source:'Pepperstone terminal fallback exported demo account',maximum_source:'AUD minimum-lot risk-band cap',reason:fixtureState==='pending'?'MT5 quote stale; awaiting current export':fixtureBlockedReason|| (params.get('order_type')==='limit'&&Number(params.get('entry_price'))<=0?'Invalid limit entry; enter a positive price':params.get('side')+' '+params.get('pepperstone_account_type')+' MT5 spread/stops/freeze;LTT buffer separate')}};if(holdInstrument){holdInstrument=false;return new Promise(resolve=>{delayed=()=>resolve(reply({...data,pepperstone_stop_guidance:{...data.pepperstone_stop_guidance,minimum_ticks:999}}));});}return reply(data);}if(url==='/api/calculator/quote'){const ticks=body.target_mode==='ticks';return reply({commission_chart_r:!ticks&&body.pepperstone_account_type==='razor'?hint:null,pepperstone_razor_estimate:body.estimate_razor_from_standard?{available:true,label:'Estimated Razor from Standard',bid:'1.30005',ask:'1.30015',spread:'0.00010'}:undefined,asset:'fx',broker:'pepperstone',venue:'Pepperstone',resolved_venue:'Pepperstone',symbol:'GBP_USD',tick_size:'0.00001',chart_tick_size:body.chart_tick_size||'0.00001',chart_tick_size_source:'Pepperstone GBPUSD TradingView chart (user screenshot measurement)',chart_tick_profile_id:'pepperstone-gbpusd-tradingview-screenshot-v1',price_display_tick_size:'0.00001',entry_price:'1.30020',stop_price:ticks?'1.30063':'1.29977',target_price:ticks?'1.30045':'1.30106',target_distance:ticks?'0.00025':'0.00086',quantity:'2300',estimated_total_loss_aud:'10',estimated_total_loss:'10',position_size_estimate_note:'OANDA price/conversion estimate; Trader validates executable MT5 lots, margin, commission, and volume.',calculation_context_id:'ctx-'+calls.length,pepperstone_distance_conversion:{chart_tick_size:body.chart_tick_size||'0.00001',chart_tick_size_source:'Pepperstone GBPUSD TradingView chart (user screenshot measurement)',stop_loss_ticks:Number(body.stop_loss_ticks),stop_loss_price_distance:'0.00043',target_mode:body.target_mode,take_profit_ticks:body.take_profit_ticks?Number(body.take_profit_ticks):null,take_profit_price_distance:ticks?'0.00025':null},pepperstone_mt5_export_status:{ready:true,file_ready:true,broker_check_state:'pending',code:'PEPPERSTONE_MT5_POINT_METADATA_UNAVAILABLE',reason:'Broker point and risk-buffer checks are pending in Trader.'},pepperstone_risk_buffer_preflight:{state:'pending',compatible:null,explanation:'Broker point and risk-buffer checks are pending in Trader.'}});}if(url.startsWith('/api/calculator/journal-summary')||url.startsWith('/api/instrument-specs'))return reply({status:'no_data'});return reply({status:'no_data'});};
let timer=0;const timers=new Map();const setTimeout=(fn,ms)=>{const id=++timer;if(ms===250)fn();else timers.set(id,{fn,ms});return id;};const clearTimeout=id=>timers.delete(id);class Clock extends Date{static now(){return Date.now()+clockOffset;}}const context={document:{getElementById:id=>el[id],createElement:tag=>new E('')},fetch,navigator:{clipboard:{writeText:async()=>{}}},setTimeout,clearTimeout,setInterval:()=>99,clearInterval(){},console,Date:Clock,URL,URLSearchParams,AbortController};vm.runInNewContext(source,context);const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{await flush();await flush();await el['asset-toggle'].buttons.find(b=>b.dataset.v==='fx').click();await flush();await flush();await el['broker-toggle'].buttons.find(b=>b.dataset.v==='pepperstone').click();await flush();await flush();await el['account-toggle'].buttons.find(b=>b.dataset.v==='demo').click();await flush();await flush();await el['risk-toggle'].buttons.find(b=>b.dataset.v==='fixed_aud').click();
await flush();await el['pepperstone-account-type-toggle'].buttons.find(b=>b.dataset.v==='standard').click();await flush();await flush();
const guidanceChecks={};
guidanceChecks.partial=el['calc-stop-guidance'].textContent==='Pending \u2014 waiting for a current MT5 quote';
const panelCount=()=>calls.filter(c=>c.url.startsWith('/api/calculator/journal-summary')||c.url.startsWith('/api/instrument-specs')).length;
const panelsBefore=panelCount();el['calc-quote-status'].textContent='preserve existing status';el['calc-results'].innerHTML='preserve existing result';fixtureState='ready';
const fire=async(ms)=>{const found=[...timers].find(([id,t])=>t.ms===ms);if(!found)throw Error('Missing bounded timer '+ms);timers.delete(found[0]);await found[1].fn();await flush();await flush();};
await fire(10000);guidanceChecks.refresh=el['calc-stop-guidance'].textContent==='Valid'&&panelCount()===panelsBefore&&el['calc-quote-status'].textContent==='preserve existing status'&&el['calc-results'].innerHTML==='preserve existing result';
context.document.hidden=true;const hiddenBefore=calls.length;await fire(30000);guidanceChecks.hidden=calls.length===hiddenBefore;context.document.hidden=false;
holdInstrument=true;await fire(30000);const delayedRefresh=delayed;el['side-toggle'].buttons.find(b=>b.dataset.v==='sell').click();await flush();await flush();delayedRefresh();await flush();guidanceChecks.delayedRefresh=!el['calc-stop-guidance'].textContent.includes('999');el['side-toggle'].buttons.find(b=>b.dataset.v==='buy').click();await flush();await flush();
const beforeTyping=calls.filter(c=>c.url==='/api/calculator/quote').length;const lookupsBefore=calls.filter(c=>c.url.startsWith('/api/calculator/instrument?')).length;
for(const [value,verdict] of [['5','Valid'],['3','Invalid \u2014 minimum 4 ticks'],['4','Valid'],['1201','Invalid \u2014 maximum 1200 ticks'],['0','Invalid \u2014 enter a positive whole number of ticks'],['-1','Invalid \u2014 enter a positive whole number of ticks'],['1.5','Invalid \u2014 enter a positive whole number of ticks'],['','Invalid \u2014 enter a positive whole number of ticks']]){el['calc-sl-ticks'].value=value;el['calc-sl-ticks'].listeners.input();guidanceChecks[value]=el['calc-stop-guidance'].textContent===verdict&&el['calc-sl-ticks'].value===value;}
el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();guidanceChecks.singleLine=el['calc-stop-guidance'].textContent==='Valid';
guidanceChecks.noQuoteWhileTyping=calls.filter(c=>c.url==='/api/calculator/quote').length===beforeTyping&&calls.filter(c=>c.url.startsWith('/api/calculator/instrument?')).length===lookupsBefore;
fixtureStep=2;await fire(30000);el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();guidanceChecks.grid=el['calc-stop-guidance'].textContent==='Invalid \u2014 use steps of 2 ticks';fixtureStep=1;await fire(30000);
await el['pepperstone-account-type-toggle'].buttons.find(b=>b.dataset.v==='razor').click();await flush();await flush();el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();guidanceChecks.razor=el['calc-stop-guidance'].textContent==='Valid';await el['pepperstone-account-type-toggle'].buttons.find(b=>b.dataset.v==='standard').click();await flush();await flush();
holdInstrument=true;el['side-toggle'].buttons.find(b=>b.dataset.v==='sell').click();await flush();const oldReply=delayed;el['side-toggle'].buttons.find(b=>b.dataset.v==='buy').click();await flush();await flush();oldReply();await flush();guidanceChecks.staleIgnored=!el['calc-stop-guidance'].textContent.includes('999');
el['calc-symbol'].value='USDJPY';el['calc-symbol'].listeners.input();await flush();await flush();guidanceChecks.usdjpy=el['calc-stop-guidance'].textContent==='Valid';
el['order-toggle'].buttons.find(b=>b.dataset.v==='limit').click();await flush();await flush();guidanceChecks.invalidEntry=el['calc-stop-guidance'].textContent==='Invalid \u2014 limit entry is too close or on the wrong side';el['calc-limit'].value='149.990';el['calc-limit'].listeners.input();await flush();await flush();const params=new URL('http://local'+calls.filter(c=>c.url.startsWith('/api/calculator/instrument?')).at(-1).url).searchParams;guidanceChecks.context=params.get('entry_price')==='149.990'&&params.get('pepperstone_account_type')==='standard'&&params.get('risk_mode')==='fixed_aud';
fixtureMinimum=14;await fire(30000);el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();guidanceChecks.minimum14=el['calc-stop-guidance'].textContent==='Invalid \u2014 minimum 14 ticks';
clockOffset=61000;el['calc-sl-ticks'].listeners.input();guidanceChecks.expired=el['calc-stop-guidance'].textContent==='Pending \u2014 waiting for a current MT5 quote';el['calc-sl-ticks'].value='1201';el['calc-sl-ticks'].listeners.input();guidanceChecks.expiredMaximum=el['calc-stop-guidance'].textContent==='Pending \u2014 waiting for a current MT5 quote';clockOffset=0;
fixtureState='pending';fixtureStaticMinimum=6;await fire(30000);el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();guidanceChecks.staticMinimum=el['calc-stop-guidance'].textContent==='Invalid \u2014 minimum 6 ticks';fixtureStaticMinimum=null;fixtureState='ready';fixtureMinimum=4;await fire(10000);
fixtureState='blocked';fixtureBlockedReason='Limit entry must be on valid side';await fire(30000);el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();guidanceChecks.blocked=el['calc-stop-guidance'].textContent==='Invalid \u2014 entry is blocked';clockOffset=61000;el['calc-sl-ticks'].listeners.input();guidanceChecks.expiredBlocked=el['calc-stop-guidance'].textContent==='Pending \u2014 waiting for a current MT5 quote';clockOffset=0;
fixtureBlockedReason='Entry price is not on both MT5 point and trade tick grids.';await fire(30000);clockOffset=61000;el['calc-sl-ticks'].listeners.input();guidanceChecks.staticBlocked=el['calc-stop-guidance'].textContent==='Invalid \u2014 entry price is off-grid';clockOffset=0;fixtureState='ready';fixtureBlockedReason='';await fire(30000);
guidanceChecks.checkboxAbsent=!source.includes('calc-estimate-razor-from-standard')&&!source.includes('Estimate Razor from Standard MT5');
const selectPricing=async value=>{await el['pepperstone-account-type-toggle'].buttons.find(b=>b.dataset.v===value).click();await flush();await flush();};
const requestsBeforeEstimate=calls.filter(c=>c.url==='/api/calculator/quote').length;
await selectPricing('razor');
guidanceChecks.estimateEnabled=el['calc-stop-guidance'].textContent==='Valid'&&new URL('http://local'+calls.filter(c=>c.url.startsWith('/api/calculator/instrument?')).at(-1).url).searchParams.get('estimate_razor_from_standard')==='true';
el['calc-sl-ticks'].value='1';el['calc-sl-ticks'].listeners.input();guidanceChecks.estimateMinimum=el['calc-stop-guidance'].textContent==='Invalid \u2014 minimum 2 ticks';
el['calc-sl-ticks'].value='5';el['calc-sl-ticks'].listeners.input();fixtureEstimateAvailable=false;await fire(30000);guidanceChecks.estimatePending=el['calc-stop-guidance'].textContent==='Pending \u2014 waiting for a current MT5 quote';fixtureEstimateAvailable=true;await fire(10000);
holdInstrument=true;await selectPricing('standard');const priorEstimateReply=delayed;await selectPricing('razor');priorEstimateReply();await flush();guidanceChecks.estimateStaleIgnored=!el['calc-stop-guidance'].textContent.includes('999');
guidanceChecks.estimateNoQuote=calls.filter(c=>c.url==='/api/calculator/quote').length===requestsBeforeEstimate;
await selectPricing('standard');
guidanceChecks.standardRestored=new URL('http://local'+calls.filter(c=>c.url.startsWith('/api/calculator/instrument?')).at(-1).url).searchParams.get('estimate_razor_from_standard')==='false';
el['calc-sl-ticks'].value='3';el['calc-sl-ticks'].listeners.input();guidanceChecks.standardBound=el['calc-stop-guidance'].textContent==='Invalid \u2014 minimum 4 ticks';
el['order-toggle'].buttons.find(b=>b.dataset.v==='market').click();el['calc-symbol'].value='GBPUSD';el['calc-symbol'].listeners.input();await flush();await flush();el['calc-sl-ticks'].value='43';el['calc-sl-ticks'].listeners.input();
await el['calc-quote'].listeners.click();await flush();await flush();const netPayload=calls.filter(c=>c.url==='/api/calculator/quote')[0].body;
guidanceChecks.estimateUnchecked=netPayload.estimate_razor_from_standard===false;
await selectPricing('razor');guidanceChecks.estimateInvalidates=el['calc-results'].innerHTML===''&&el['calc-pepperstone-set'].style.display==='none';
el['calc-target-mode'].value='ticks';el['calc-target-mode'].listeners.change();const visibleAfterModeChange=el['calc-pepperstone-set'].style.display!== 'none';el['calc-tp-ticks'].listeners.input();el['calc-rr'].value='99';el['calc-rr'].listeners.input();
await el['calc-quote'].listeners.click();await flush();await flush();const manualPayload=calls.filter(c=>c.url==='/api/calculator/quote')[1].body;guidanceChecks.estimateQuote=manualPayload.estimate_razor_from_standard===true&&el['calc-results'].innerHTML.includes('Estimated Razor from Standard');const visibleBefore=el['calc-pepperstone-set'].style.display!== 'none';const enabledBefore=!el['calc-pepperstone-set'].disabled;const quoteStatus=el['calc-quote-status'].textContent;const preflightMessage=el['calc-pepperstone-risk-preflight'].textContent;const resultHtml=el['calc-results'].innerHTML;
el['calc-sl-ticks'].value='44';el['calc-sl-ticks'].listeners.input();const visibleAfter=el['calc-pepperstone-set'].style.display!== 'none';
const hintLine=el['calc-commission-chart-r'];
const expectedHint='Estimated chart target 4.10R \u00b7 Includes +0.60R for commission';
const rrField=el['calc-rr'];
el['calc-target-mode'].value='rr';el['calc-target-mode'].listeners.change();
rrField.value='3.5';rrField.listeners.input();
const quoteCount=()=>calls.filter(c=>c.url==='/api/calculator/quote').length;
const beforeTab=quoteCount();
rrField.listeners.blur({type:'blur',target:rrField});await fire(0);
if(hintLine.hidden||hintLine.textContent!==expectedHint||quoteCount()!==beforeTab||!el['calc-pepperstone-set'].disabled)throw Error('Tab must show only read-only commission preview');
holdPreview=true;rrField.listeners.blur();const queued=[...timers].find(([,t])=>t.ms===0);timers.delete(queued[0]);const inFlight=queued[1].fn();await flush();
await selectPricing('standard');releasePreview();await inFlight;
if(!hintLine.hidden||hintLine.textContent)throw Error('Delayed Razor preview survived Standard');
holdPreview=false;await selectPricing('razor');rrField.listeners.blur();await fire(0);
await el['calc-quote'].listeners.click();await flush();
if(hintLine.hidden||hintLine.textContent!==expectedHint||el['calc-results-disclosure'].open||quoteCount()!==beforeTab+1)throw Error('Calculate must preserve hint outside collapsed results');
el['calc-target-mode'].value='ticks';el['calc-target-mode'].listeners.change();
if(!hintLine.hidden||hintLine.textContent)throw Error('Manual TP must hide commission hint');
console.log(JSON.stringify({guidanceChecks,netPayload,manualPayload,visibleAfterModeChange,visibleBefore,enabledBefore,visibleAfter,quoteStatus,preflightMessage,resultHtml,autoStep:el['calc-chart-tick-auto'].value,autoSource:el['calc-chart-tick-source'].textContent,exportCalls:calls.filter(c=>c.url==='/api/calculator/pepperstone-set').length,submitCalls:calls.filter(c=>c.url==='/api/calculator/submit').length}));})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert all(data["guidanceChecks"].values()), data["guidanceChecks"]
    assert data["netPayload"]["target_mode"] == "rr"
    assert data["netPayload"]["risk_reward"] == "2"
    assert "take_profit_ticks" not in data["netPayload"]
    assert data["manualPayload"]["target_mode"] == "ticks"
    assert data["manualPayload"]["take_profit_ticks"] == "25"
    assert "chart_tick_size" not in data["manualPayload"]
    assert "risk_reward" not in data["manualPayload"]
    assert data["visibleAfterModeChange"] is False
    assert data["visibleBefore"] is True and data["enabledBefore"] is True and data["visibleAfter"] is False
    assert "2300" in data["resultHtml"] and "0.00043 price" in data["resultHtml"] and "0.00025 price" in data["resultHtml"]
    assert data["autoStep"] == "0.00001" and "Pepperstone GBPUSD TradingView chart" in data["autoSource"]
    assert "broker point count pending in Trader" in data["resultHtml"]
    assert data["quoteStatus"] == "GBP/USD \u00b7 Razor \u00b7 Preset ready"
    assert data["preflightMessage"] == ""
    assert data["exportCalls"] == 0 and data["submitCalls"] == 0


def _run_usdjpy_auto_route_harness(mode: str) -> dict:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8'),mode=process.argv[2];
class Button {
  constructor(v){this.dataset={v};this.listeners={};this.disabled=false;this.attrs={};this.active=false;this.classList={toggle:(name,on)=>{if(name==='active')this.active=!!on;},add:()=>{},remove:()=>{}};}
  addEventListener(e,f){this.listeners[e]=f;} click(){if(!this.disabled&&this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} removeAttribute(k){delete this.attrs[k];}
}
class Element extends Button {
  constructor(id){super('');this.id=id;this.value='';this.style={};this.textContent='';this._html='';this.buttons=[];this.checked=false;}
  set innerHTML(v){this._html=String(v||'');this.buttons=[...this._html.matchAll(/data-v="([^"]*)"/g)].map(m=>new Button(m[1]));}
  get innerHTML(){return this._html;} querySelectorAll(s){return s==='button'?this.buttons:[];}
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const el=Object.fromEntries(ids.map(id=>[id,new Element(id)]));
const groups={'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']};
for(const [id,values] of Object.entries(groups))el[id].buttons=values.map(v=>new Button(v));
el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';
const calls=[];let quotePayload=null,submitPayload=null,intervalClears=0,invalidQuote=false;const checks={};
const response=data=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
const quote={asset:'fx',broker:'oanda',venue:'OANDA',resolved_venue:'OANDA',symbol:'USD_JPY',tick_size:'0.001',entry_price:'150.000',stop_price:'149.990',target_price:'150.020',target_distance:'0.020',quantity:'1000',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20',calculation_context_id:'ctx-usdjpy',quote_created_at_ms:123,quote_valid_for_submit:true};
const fetch=async(url,opts={})=>{
  const body=opts.body?JSON.parse(opts.body):null;calls.push({url,method:opts.method||'GET',body});
  if(url==='/api/calculator/bootstrap')return response({app_profile:'render',trendline_plans_available:false,webhook:{available:true}});
  if(url.startsWith('/api/calculator/prewarm-account'))return response({ready_for_quote:true,asset:'crypto'});
  if(url.startsWith('/api/calculator/instrument?'))return response({asset:'fx',broker:'oanda',symbol:'USD_JPY'});
  if(url==='/api/calculator/quote'){quotePayload=body;if(Number(body.risk_value)<=0)return {...response({detail:'Risk must be positive'}),ok:false,status:400};return response({...quote,quote_valid_for_submit:!invalidQuote});}
  if(url==='/api/calculator/submit'){submitPayload=body;return response({ok:true,broker:'oanda',result:{order:{orderId:'test-order'}}});}
  if(url.startsWith('/api/calculator/journal-summary'))return response({status:'no_data'});
  if(url.startsWith('/api/instrument-specs'))return response({source:'oanda',resolved_symbol:'USD_JPY'});
  if(url.startsWith('/api/calculator/prewarm'))throw new Error('FX resolution must not prewarm Bybit quote data');
  return response({status:'no_data'});
};
let timerId=0;const timers=new Map();
const setTimeout=(fn,ms)=>{const id=++timerId;if(ms===250)fn();else timers.set(id,fn);return id;};
const clearTimeout=id=>timers.delete(id);const setInterval=()=>99;const clearInterval=()=>{intervalClears++;};
const context={document:{getElementById:id=>el[id]},fetch,navigator:{clipboard:{writeText:async()=>{}}},setTimeout,clearTimeout,setInterval,clearInterval,console,Date,URL,URLSearchParams,AbortController};
vm.runInNewContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();await flush();
  el['calc-symbol'].value='USDJPY';
  if(mode==='resolved') { el['calc-symbol'].listeners.input(); await flush(); await flush(); await flush(); }
  await el['calc-quote'].listeners.click();await flush();await flush();
  if(mode==='classification') {
    checks.quotePayload={...quotePayload};checks.unsetChoice=el['timeframe-toggle'].buttons.some(b=>b.dataset.v==='');
    await el['calc-submit'].listeners.click();await flush();checks.submitPayload={...submitPayload};
    el['calc-risk'].value='0';el['calc-risk'].listeners.input();await el['calc-quote'].listeners.click();await flush();
    checks.invalidRiskDisabled=el['calc-submit'].disabled;checks.invalidRiskError=el['calc-error'].textContent;await el['calc-submit'].listeners.click();
    el['calc-risk'].value='1';el['calc-risk'].listeners.input();invalidQuote=true;await el['calc-quote'].listeners.click();await flush();checks.invalidQuoteDisabled=el['calc-submit'].disabled;await el['calc-submit'].listeners.click();
    invalidQuote=false;await el['webhook-toggle'].buttons.find(b=>b.dataset.v==='yes').click();await el['calc-quote'].listeners.click();await flush();checks.webhookDisabled=el['calc-submit'].disabled;await el['calc-submit'].listeners.click();
    checks.submits=calls.filter(c=>c.url==='/api/calculator/submit').length;
  }
  if(mode==='race') { await el['calc-submit'].listeners.click();await flush(); }
  const active=(id,v)=>!!el[id].buttons.find(b=>b.dataset.v===v)?.active;
  console.log(JSON.stringify({checks,calls,quotePayload,submitPayload,canonical:el['calc-canonical-symbol'].textContent,assetFx:active('asset-toggle','fx'),brokerOanda:active('broker-toggle','oanda'),riskVisible:el['risk-toggle-wrap'].style.display!=='none',brokerVisible:el['broker-toggle-wrap'].style.display!=='none',intervalClears,submitEnabled:!el['calc-submit'].disabled}));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run(
        [node, "-e", harness, str(JS_PATH), mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_usdjpy_default_crypto_input_auto_selects_fx_and_posts_oanda_quote() -> None:
    data = _run_usdjpy_auto_route_harness("resolved")
    assert data["assetFx"] and data["brokerOanda"]
    assert data["riskVisible"] and data["brokerVisible"]
    assert data["canonical"].endswith("USD_JPY")
    assert data["quotePayload"]["asset"] == "fx"
    assert data["quotePayload"]["broker"] == "oanda"
    assert data["quotePayload"]["symbol"] == "USD_JPY"
    urls = [call["url"] for call in data["calls"]]
    assert any("/api/calculator/instrument?asset=crypto&broker=bybit" in url for url in urls)
    assert any("/api/calculator/journal-summary?asset=fx&symbol=USD_JPY" in url for url in urls)
    assert any("/api/instrument-specs?query=USD_JPY&prefer=oanda" in url for url in urls)
    assert not any(call["url"] == "/api/calculator/prewarm" for call in data["calls"])
    assert data["intervalClears"] >= 1


def test_usdjpy_quote_response_reconciles_fx_identity_after_resolution_race() -> None:
    data = _run_usdjpy_auto_route_harness("race")
    assert data["quotePayload"]["asset"] == "crypto"
    assert data["quotePayload"]["broker"] == "bybit"
    assert data["quotePayload"]["symbol"] == "USDJPY"
    assert data["assetFx"] and data["brokerOanda"]
    assert data["canonical"].endswith("USD_JPY")
    assert data["submitPayload"]["asset"] == "fx"
    assert data["submitPayload"]["broker"] == "oanda"
    assert data["submitPayload"]["symbol"] == "USD_JPY"
    urls = [call["url"] for call in data["calls"]]
    assert any("/api/calculator/journal-summary?asset=fx&symbol=USD_JPY" in url for url in urls)
    assert any("/api/instrument-specs?query=USD_JPY&prefer=oanda" in url for url in urls)


def test_risk_toggle_posts_fixed_aud_payload_and_preserves_fx_selection() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');

class MockElement {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.buttons = [];
    this.classList = {
      toggle: () => {},
      add: () => {},
      remove: () => {},
    };
  }
  addEventListener(evt, cb) {
    this.listeners[evt] = cb;
  }
  querySelectorAll(sel) {
    if (sel === 'button') return this.buttons;
    return [];
  }
}

class MockButton {
  constructor(v) {
    this.dataset = { v };
    this.listeners = {};
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} };
    this.disabled = false;
    this.title = '';
    this._attrs = {};
  }
  setAttribute(k,v){ this._attrs[k]=String(v); this[k] = v; }
  getAttribute(k){ return this._attrs[k]; }
  removeAttribute(k){ delete this._attrs[k]; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  click() { if (this.listeners.click) this.listeners.click(); }
}

const ids = [
  'calc-error', 'calc-error-debug', 'calc-success', 'calc-results', 'calc-request-summary',
  'calc-canonical-symbol', 'calc-journal-summary', 'calc-instrument-specs', 'risk-toggle-wrap',
  'calc-webhook-panel', 'calc-webhook-url', 'calc-webhook-json', 'calc-webhook-copy', 'calc-webhook-copy-url', 'risk-toggle', 'calc-risk-label',
  'limit-wrap', 'account-toggle', 'asset-toggle', 'side-toggle', 'order-toggle', 'webhook-toggle',
  'test-toggle', 'timeframe-toggle', 'calc-symbol', 'calc-limit', 'calc-sl-ticks', 'calc-rr',
  'calc-risk', 'calc-quote', 'calc-submit', 'calc-quote-status'
];
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]));

function makeToggleButtons(values) {
  return values.map((v) => new MockButton(v));
}

elements['risk-toggle'].buttons = makeToggleButtons(['fixed_aud', 'percent']);
elements['asset-toggle'].buttons = makeToggleButtons(['crypto', 'fx']);
elements['account-toggle'].buttons = makeToggleButtons(['live', 'demo']);
elements['side-toggle'].buttons = makeToggleButtons(['buy', 'sell']);
elements['order-toggle'].buttons = makeToggleButtons(['market', 'limit']);
elements['webhook-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['test-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['timeframe-toggle'].buttons = [];

elements['calc-risk'].value = '10';
elements['calc-symbol'].value = 'NZDUSD';
elements['calc-sl-ticks'].value = '35';
elements['calc-rr'].value = '2';

let lastQuotePayload = null;

global.fetch = async (url, opts = {}) => {
  if (url.includes('/api/calculator/quote')) {
    lastQuotePayload = JSON.parse(opts.body || '{}');
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: { get: () => 'application/json' },
      text: async () => JSON.stringify({
        broker: 'oanda', symbol: 'NZD_USD', tick_size: '0.00001', entry_price: '0.6102', stop_price: '0.60985',
        target_price: '0.6109', target_distance: '0.00070', quantity: '1000', estimated_fees_or_spread: '1',
        estimated_total_loss: '10', estimated_reward: '20', display_currency: 'AUD'
      }),
    };
  }
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: () => 'application/json' },
    text: async () => JSON.stringify({ status: 'no_data', canonical_symbol: 'NZDUSD' }),
  };
};

global.document = { getElementById: (id) => elements[id] };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};

// load script
// eslint-disable-next-line no-eval
eval(source);

const riskFixed = elements['risk-toggle'].buttons.find((b) => b.dataset.v === 'fixed_aud');
const assetCrypto = elements['asset-toggle'].buttons.find((b) => b.dataset.v === 'crypto');
const assetFx = elements['asset-toggle'].buttons.find((b) => b.dataset.v === 'fx');

riskFixed.click();
assetCrypto.click();
assetFx.click();
riskFixed.click();

if (elements['calc-quote'].listeners.click) {
  elements['calc-quote'].listeners.click();
}

setTimeout(() => {
  console.log(JSON.stringify({ payload: lastQuotePayload, summary: elements['calc-request-summary'].textContent }));
}, 0);
'''
    result = subprocess.run(
        [node, "-e", harness, str(JS_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout.strip().splitlines()[-1])
    payload = data["payload"]
    assert payload["risk_mode"] == "fixed_aud"
    assert payload["broker"] == "oanda"
    assert str(payload["risk_value"]) == "10"
    assert "risk_mode=fixed_aud" in data["summary"]
    assert "risk_value=10" in data["summary"]
    assert "webhook=no" in data["summary"]
    assert "test=no" in data["summary"]
    assert "timeframe=15m" in data["summary"]
    assert "pending_webhook_id=" in data["summary"]


def test_fx_broker_toggle_is_hidden_for_crypto_and_supports_pepperstone_market_set() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
class E {
  constructor(id) { this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; }
  addEventListener(e,cb){ this.listeners[e]=cb; }
  querySelectorAll(sel){ return sel==='button' ? this.buttons : []; }
}
class B {
  constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; this.title=''; }
  addEventListener(e,cb){ this.listeners[e]=cb; }
  click(){ if(this.listeners.click) this.listeners.click(); }
  setAttribute(k,v){ this._attrs[k]=String(v); }
  getAttribute(k){ return this._attrs[k]; }
  removeAttribute(k){ delete this._attrs[k]; }
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','broker-toggle-wrap','calc-pepperstone-risk-preflight','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','broker-toggle','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-pepperstone-set','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map((id)=>[id,new E(id)]));
const mk=(vals)=>vals.map((v)=>new B(v));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);
el['asset-toggle'].buttons=mk(['crypto','fx']);
el['broker-toggle'].buttons=mk(['oanda','pepperstone']);
el['account-toggle'].buttons=mk(['live','demo']);
el['side-toggle'].buttons=mk(['buy','sell']);
el['order-toggle'].buttons=mk(['market','limit']);
el['webhook-toggle'].buttons=mk(['no','yes']);
el['test-toggle'].buttons=mk(['no','yes']);
el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='EUR_USD';
el['calc-limit'].value='1.1002';
el['calc-sl-ticks'].value='35';
el['calc-rr'].value='2';
el['calc-risk'].value='10';
  let quotePayload=null;
  let setPayload=null;
  const requests=[];
  const downloads=[];
  global.fetch=async (url,opts={})=>{
    requests.push(url);
  if(url.includes('/api/calculator/bootstrap')) return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({app_profile:'local',calculator_js_sha256_12:'abc123def456',render_calculator_base_url_configured:true,webhook:{available:true}})};
  if(url.includes('/api/calculator/instrument?'))return {ok:true,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'fx',broker:'pepperstone',symbol:'EUR_USD'})};
  if(url.includes('/api/calculator/quote')){ quotePayload=JSON.parse(opts.body||'{}'); return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'fx',broker:'pepperstone',venue:'Pepperstone',resolved_venue:'Pepperstone',symbol:'EUR_USD',tick_size:'0.00001',entry_price:'1.1002',stop_price:'1.09985',target_price:'1.10125',target_distance:'0.00105',quantity:'1000',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_total_loss_aud:'10',estimated_reward:'20',pepperstone_mt5_export_status:{file_ready:true,broker_check_state:'pending',reason:'Broker point, grid, and risk checks run in Trader.'},pepperstone_risk_buffer_preflight:{state:'pending',stop_points:35,preferred_maximum_buffer_points:50,minimum_automatic_buffer_points:10,conservative_max_buffer_points:11,planned_buffer_points:11,minimum_compatible_stop_points:30,compatible:null,explanation:'Broker checks are pending in Trader.'}})}; }
  if(url.includes('/api/calculator/pepperstone-set')){ setPayload=JSON.parse(opts.body||'{}'); return {ok:true,status:200,statusText:'OK',headers:{get:(k)=>k==='content-disposition'?'attachment; filename="Pepperstone_Trader_EUR_USD_BUY_MARKET_x.set"':'text/plain'},text:async()=> 'Strategy=3\nStandardMarketSide=0\nStandardMarketExecutionToken=mkt_test\n'}; }
  return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};
};
global.document={getElementById:(id)=>el[id],createElement:(tag)=>({tag,style:{},click(){downloads.push({tag,filename:this.download});}})};
global.navigator={clipboard:{writeText:async()=>{}}};
global.setTimeout=(fn,ms)=>{ if(ms===250)fn(); return 1; };
global.clearTimeout=()=>{};
global.setInterval=()=>1;
global.clearInterval=()=>{};
eval(source);
(async()=>{
  await Promise.resolve(); await Promise.resolve();
  const hiddenOnCrypto = el['broker-toggle-wrap'].style.display === 'none';
  el['asset-toggle'].buttons.find((b)=>b.dataset.v==='fx').click();
  const visibleOnFx = el['broker-toggle-wrap'].style.display === '';
  el['broker-toggle'].buttons.find((b)=>b.dataset.v==='pepperstone').click();
  await el['calc-quote'].listeners.click();
  const setReady = el['calc-pepperstone-set'].style.display === '' && el['calc-pepperstone-set'].disabled === false;
  if (!setReady) throw new Error('Pepperstone .set button was not ready after a compatible quote.');
  const submitAfterQuote = el['calc-submit'].style.display;
  const preflightAfterQuote = el['calc-results'].innerHTML;
  const automaticSetRequests = requests.filter((url)=>url.includes('/api/calculator/pepperstone-set')).length;
  const automaticOrderRequests = requests.filter((url)=>url.includes('/submit')).length;
  const automaticDownloads = downloads.length;
  await el['calc-pepperstone-set'].listeners.click();
  const explicitSetRequests = requests.filter((url)=>url.includes('/api/calculator/pepperstone-set')).length;
  const downloadsAfterExplicitClick = downloads.length;
  el['calc-risk'].value='11';
  el['calc-risk'].listeners.input();
  const invalidatedAfterEdit = el['calc-pepperstone-set'].style.display === 'none' && el['calc-pepperstone-set'].disabled === true;
  console.log(JSON.stringify({hiddenOnCrypto,visibleOnFx,broker:quotePayload?.broker,setReady,setPayload,automaticSetRequests,automaticOrderRequests,automaticDownloads,explicitSetRequests,downloadsAfterExplicitClick,invalidatedAfterEdit,setButton:el['calc-pepperstone-set'].style.display,submitAfterQuote,submitDisabledAfterEdit:el['calc-submit'].disabled,preflightAfterQuote,summary:el['calc-request-summary'].innerHTML}));
})();
'''
    run = subprocess.run([node, "-e", harness, str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    out = run.stdout
    data = json.loads(out.strip().splitlines()[-1])
    assert data["hiddenOnCrypto"] is True
    assert data["visibleOnFx"] is True
    assert data["broker"] == "pepperstone"
    assert data["setReady"] is True
    assert data["automaticSetRequests"] == 0
    assert data["automaticOrderRequests"] == 0
    assert data["automaticDownloads"] == 0
    assert data["explicitSetRequests"] == 1
    assert data["downloadsAfterExplicitClick"] == 1
    assert data["invalidatedAfterEdit"] is True
    assert "<dt>broker</dt><dd>pepperstone</dd>" in data["summary"]
    assert data["setPayload"]["order_type"] == "market"
    assert "entry_price" not in data["setPayload"]
    assert data["setButton"] == "none"
    assert data["submitAfterQuote"] == "none"
    assert data["submitDisabledAfterEdit"] is True
    assert "File ready; final broker checks" in data["preflightAfterQuote"]
    assert "<dt>broker</dt><dd>pepperstone</dd>" in data["summary"]
    assert "<dt>venue</dt><dd>Pepperstone</dd>" in data["summary"]


def test_submit_visibility_tracks_quote_validity_and_webhook_mode() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');

class MockElement {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.buttons = [];
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} };
  }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  querySelectorAll(sel) { return sel === 'button' ? this.buttons : []; }
}
class MockButton {
  constructor(v) { this.dataset = { v }; this.listeners = {}; this.classList = { toggle: () => {}, add: () => {}, remove: () => {} }; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  click() { if (this.listeners.click) this.listeners.click(); }
}

const ids = [
  'calc-error', 'calc-error-debug', 'calc-success', 'calc-results', 'calc-request-summary',
  'calc-canonical-symbol', 'calc-journal-summary', 'calc-instrument-specs', 'risk-toggle-wrap',
  'calc-webhook-panel', 'calc-webhook-url', 'calc-webhook-json', 'calc-webhook-copy', 'calc-webhook-copy-url',
  'risk-toggle', 'calc-risk-label', 'limit-wrap', 'account-toggle', 'asset-toggle', 'side-toggle',
  'order-toggle', 'webhook-toggle', 'test-toggle', 'timeframe-toggle', 'calc-symbol', 'calc-limit',
  'calc-sl-ticks', 'calc-rr', 'calc-risk', 'calc-quote', 'calc-submit', 'calc-quote-status', 'calc-webhook-status'
];
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]));

function makeToggleButtons(values) { return values.map((v) => new MockButton(v)); }
elements['risk-toggle'].buttons = makeToggleButtons(['fixed_aud', 'percent']);
elements['asset-toggle'].buttons = makeToggleButtons(['crypto', 'fx']);
elements['account-toggle'].buttons = makeToggleButtons(['live', 'demo']);
elements['side-toggle'].buttons = makeToggleButtons(['buy', 'sell']);
elements['order-toggle'].buttons = makeToggleButtons(['market', 'limit']);
elements['webhook-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['test-toggle'].buttons = makeToggleButtons(['no', 'yes']);
elements['timeframe-toggle'].buttons = [];
elements['calc-symbol'].value = 'BTCUSDT';
elements['calc-limit'].value = '0';
elements['calc-sl-ticks'].value = '10';
elements['calc-rr'].value = '2';
elements['calc-risk'].value = '1';

let nextQuoteShouldFail = false;
global.fetch = async (url, opts = {}) => {
  if (url.includes('/api/calculator/quote')) {
    if (nextQuoteShouldFail) {
      nextQuoteShouldFail = false;
      return { ok: false, status: 500, statusText: 'bad', headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ detail: 'boom' }) };
    }
    return {
      ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' },
      text: async () => JSON.stringify({
        broker: 'bybit', symbol: 'BTCUSDT', tick_size: '0.10', entry_price: '60000', stop_price: '59900',
        target_price: '60200', target_distance: '200', quantity: '1', estimated_fees_or_spread: '1',
        estimated_total_loss: '10', estimated_reward: '20', display_currency: 'AUD',
        webhook_payload_json: '{"a":1}', pending_webhook_id: 'pid-1', webhook_endpoint_url: 'https://example.com'
      }),
    };
  }
  return { ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ status: 'no_data' }) };
};
global.document = { getElementById: (id) => elements[id] };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};
eval(source);

const submit = elements['calc-submit'];
const quoteClick = elements['calc-quote'].listeners.click;
const webhookYes = elements['webhook-toggle'].buttons.find((b) => b.dataset.v === 'yes');
const webhookNo = elements['webhook-toggle'].buttons.find((b) => b.dataset.v === 'no');

(async () => {
  const states = [];
  states.push(submit.style.display === 'none');
  let p1 = quoteClick();
  states.push(submit.style.display === '' && submit.disabled === true);
  await p1;
  states.push(submit.style.display === '' && submit.disabled === false);
  states.push(elements['calc-quote'].disabled === false && elements['calc-quote'].textContent !== 'Calculating…');
  elements['calc-risk'].value = '2';
  elements['calc-risk'].listeners.input();
  states.push(submit.style.display === '' && submit.disabled === true);
  p1 = quoteClick();
  states.push(submit.style.display === '' && submit.disabled === true);
  await p1;
  states.push(submit.style.display === '' && submit.disabled === false);
  elements['calc-sl-ticks'].value = '20';
  elements['calc-sl-ticks'].listeners.change();
  states.push(submit.style.display === '' && submit.disabled === true);
  nextQuoteShouldFail = true;
  await quoteClick();
  states.push(submit.disabled === true);
  states.push(elements['calc-quote'].disabled === false && elements['calc-quote'].textContent !== 'Calculating…');
  webhookYes.click();
  await quoteClick();
  states.push(submit.style.display === 'none');
  webhookNo.click();
  await quoteClick();
  states.push(submit.style.display === '');
  console.log(JSON.stringify(states));
})();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    states = json.loads(result.stdout.strip().splitlines()[-1])
    assert states == [True, True, True, True, True, True, True, True, True, True, True, True]


def test_webhook_copy_button_no_reference_error() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
class MockElement {
  constructor(id) { this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
  querySelectorAll(sel) { return sel === 'button' ? this.buttons : []; }
}
class MockButton {
  constructor(v) { this.dataset = { v }; this.listeners = {}; this.classList = { toggle: () => {}, add: () => {}, remove: () => {} }; }
  addEventListener(evt, cb) { this.listeners[evt] = cb; }
}
const ids = ['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status'];
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]));
for (const id of ['risk-toggle','asset-toggle','account-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle']) {
  elements[id].buttons = [new MockButton('no'), new MockButton('yes')];
}
elements['calc-webhook-json'].textContent = '{"a":1}';
global.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ status: 'no_data' }) });
global.document = { getElementById: (id) => elements[id] };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};
eval(source);
(async () => {
  await elements['calc-webhook-copy'].listeners.click();
  console.log('ok');
})();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_webhook_disabled_from_bootstrap_blocks_yes_mode() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs'); const source = fs.readFileSync(process.argv[1], 'utf8');
class E { constructor(id){ this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; } addEventListener(e,cb){this.listeners[e]=cb;} querySelectorAll(sel){return sel==='button'?this.buttons:[];} }
class B { constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; } addEventListener(e,cb){this.listeners[e]=cb;} click(){ if(this.listeners.click) this.listeners.click(); } setAttribute(k,v){this._attrs[k]=String(v);} getAttribute(k){return this._attrs[k];} removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']); el['asset-toggle'].buttons=mk(['crypto','fx']); el['account-toggle'].buttons=mk(['live','demo']); el['side-toggle'].buttons=mk(['buy','sell']); el['order-toggle'].buttons=mk(['market','limit']); el['webhook-toggle'].buttons=mk(['no','yes']); el['test-toggle'].buttons=mk(['no','yes']); el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT'; el['calc-sl-ticks'].value='10'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let quotePayloads=[];
global.fetch=async (url,opts={})=>{ if(url.includes('/api/calculator/bootstrap')) return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({webhook:{available:false,unavailable_message:'blocked',webhook_origin_host:'127.0.0.1'}})}; if(url.includes('/api/calculator/quote')){ quotePayloads.push(JSON.parse(opts.body||'{}')); return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'1',entry_price:'100',stop_price:'90',target_price:'120',target_distance:'20',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})}; } return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]}; global.navigator={clipboard:{writeText:async()=>{}}}; global.setTimeout=(fn)=>{fn();return 1;}; global.clearTimeout=()=>{};
eval(source);
(async()=>{ await Promise.resolve(); const yes=el['webhook-toggle'].buttons[1]; yes.click(); await el['calc-quote'].listeners.click(); console.log(JSON.stringify({yesDisabled:yes.disabled,payloadWebhook:quotePayloads[0]?.webhook,panel:el['calc-webhook-panel'].style.display,status:el['calc-webhook-status'].textContent})); })();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["yesDisabled"] is True
    assert data["payloadWebhook"] == "no"
    assert data["panel"] == "none"
    assert "blocked" in data["status"].lower()
    assert "build:" not in data["status"].lower()


def test_webhook_enabled_from_bootstrap_allows_yes_mode() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs'); const source = fs.readFileSync(process.argv[1], 'utf8');
class E { constructor(id){ this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; } addEventListener(e,cb){this.listeners[e]=cb;} querySelectorAll(sel){return sel==='button'?this.buttons:[];} }
class B { constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; } addEventListener(e,cb){this.listeners[e]=cb;} click(){ if(this.listeners.click) this.listeners.click(); } setAttribute(k,v){this._attrs[k]=String(v);} getAttribute(k){return this._attrs[k];} removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']); el['asset-toggle'].buttons=mk(['crypto','fx']); el['account-toggle'].buttons=mk(['live','demo']); el['side-toggle'].buttons=mk(['buy','sell']); el['order-toggle'].buttons=mk(['market','limit']); el['webhook-toggle'].buttons=mk(['no','yes']); el['test-toggle'].buttons=mk(['no','yes']); el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT'; el['calc-sl-ticks'].value='10'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let quotePayload;
global.fetch=async (url,opts={})=>{ if(url.includes('/api/calculator/bootstrap')) return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({app_profile:'local',calculator_js_sha256_12:'abc123def456',render_calculator_base_url_configured:true,webhook:{available:true}})}; if(url.includes('/api/calculator/quote')){ quotePayload=JSON.parse(opts.body||'{}'); return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'1',entry_price:'100',stop_price:'90',target_price:'120',target_distance:'20',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20',webhook_payload_json:'{\"a\":1}',pending_webhook_id:'pid-1',webhook_endpoint_url:'https://example.test/api/calculator/webhook'})}; } return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]}; global.navigator={clipboard:{writeText:async()=>{}}}; global.setTimeout=(fn)=>{fn();return 1;}; global.clearTimeout=()=>{};
eval(source);
 (async()=>{ await Promise.resolve(); await Promise.resolve(); el['webhook-toggle'].buttons[1].disabled=false; el['webhook-toggle'].buttons[1].removeAttribute('aria-disabled'); el['webhook-toggle'].buttons[1].click(); await el['calc-quote'].listeners.click(); console.log(JSON.stringify({webhook:quotePayload?.webhook,panel:el['calc-webhook-panel'].style.display,url:el['calc-webhook-url'].textContent,json:el['calc-webhook-json'].textContent,status:el['calc-webhook-status'].textContent})); })();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["webhook"] == "yes"
    assert data["panel"] == ""
    assert "https://example.test" in data["url"]
    assert data["status"] == ""


def test_webhook_stale_runtime_warning_on_old_public_webhook_message() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}setAttribute(k,v){this._attrs[k]=v;}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));const mk=(v)=>v.map(x=>new B(x));el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);el['timeframe-toggle'].buttons=[];global.fetch=async (url)=>url.includes('/api/calculator/bootstrap')?{ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({app_profile:'local',calculator_js_sha256_12:'deadbeef1234',render_calculator_base_url_configured:false,webhook:{available:false,unavailable_message:'TradingView webhook is unavailable on localhost unless you use Render or set PUBLIC_WEBHOOK_BASE_URL to a public same-instance tunnel URL.'}})}:{ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);(async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();console.log(JSON.stringify({status:el['calc-webhook-status'].textContent,yesDisabled:el['webhook-toggle'].buttons[1].disabled}));})();'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["yesDisabled"] is True
    assert "Stale local server code detected" in data["status"]


def test_abort_error_message_fallback_present() -> None:
    script = JS_PATH.read_text(encoding="utf-8")
    assert "Quote timed out after 15s" in script

def test_render_quote_shows_tp_auto_adjustment() -> None:
    node = shutil.which('node')
    assert node
    harness = r'''
const fs = require('fs'); const source = fs.readFileSync(process.argv[1], 'utf8');
class E{constructor(){this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,cb){this.listeners[e]=cb;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,cb){this.listeners[e]=cb;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E()]));
const mk=(vals)=>vals.map(v=>new B(v)); el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTCUSDT';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url,opts={})=> url.includes('/api/calculator/quote')?{ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'0.1',entry_price:'95',stop_price:'94',target_price:'100.1',target_distance:'5.1',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20',take_profit_adjusted:true,take_profit_adjustment:{original_take_profit:'96',adjusted_take_profit:'100.1',reason:'bybit_last_price_trigger_side',last_price:'100'},warnings:['Take profit was auto-adjusted to satisfy Bybit LastPrice trigger rules.']})}:{ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({})};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click(); console.log(el['calc-results'].innerHTML);})();
'''
    out = subprocess.run([node, '-e', harness, str(JS_PATH)], check=True, capture_output=True, text=True).stdout
    assert 'TP auto-adjusted' in out

def test_submit_payload_summary_includes_submit_levels() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));const mk=(v)=>v.map(x=>new B(x));el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTCUSDT';el['calc-risk'].value='1';el['calc-sl-ticks'].value='1999';el['calc-rr'].value='1';
let submitted=null;
let broadcasts=[];let storageEvents=[];
global.BroadcastChannel=class{constructor(name){this.name=name;}postMessage(msg){broadcasts.push({name:this.name,msg});}close(){}};
global.localStorage={setItem:(key,value)=>storageEvents.push({key,value:JSON.parse(value)})};
global.fetch=async (url,opts={})=>{if(url.includes('/quote'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'crypto',broker:'bybit',venue:'Bybit',resolved_venue:'Bybit',symbol:'BTCUSDT',entry_price:'79300',stop_price:'78784.5',target_price:'79669',quantity:'0.012',calculation_context_id:'ctx1',quote_created_at_ms:123})};if(url.includes('/submit')){submitted=JSON.parse(opts.body||'{}');return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ok:true,broker:'bybit',venue:'Bybit',resolved_venue:'Bybit'})};}return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await new Promise(setImmediate);await el['calc-quote'].listeners.click();const quoteError=el['calc-error'].textContent;await el['calc-submit'].listeners.click();console.log(JSON.stringify({quoteError,submitted,summary:el['calc-request-summary'].innerHTML,error:el['calc-error'].textContent,broadcasts,storageEvents}));})();
'''
    out = subprocess.run([node, '-e', harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data['submitted'] is not None, data
    assert data['submitted']['planned_entry_price'] == '79300'
    assert data['submitted']['stop_loss_price'] == '78784.5'
    assert data['submitted']['broker'] == 'bybit'
    assert '<dt>planned_entry_price</dt><dd>79300</dd>' in data['summary']
    assert '<dt>take_profit_price</dt><dd>79669</dd>' in data['summary']
    assert '<dt>broker</dt><dd>bybit</dd>' in data['summary']
    assert '<dt>venue</dt><dd>Bybit</dd>' in data['summary']
    assert data['broadcasts'][0]['name'] == 'trading-tools-open-orders'
    assert data['broadcasts'][0]['msg']['type'] == 'state-changed'
    assert data['broadcasts'][0]['msg']['action'] == 'submit'
    assert data['storageEvents'][0]['key'] == 'trading-tools-open-orders-event'


def test_crypto_calculate_attempts_quote_when_prewarm_wallet_unavailable() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='5';el['calc-rr'].value='2';el['calc-risk'].value='1';
let quoteCalls=0;
global.fetch=async (url,opts={})=>{if(url.includes('/instrument'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})};if(url.includes('/prewarm'))return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'crypto',account:'demo',symbol:'BTCUSDT',ready_for_quote:false,missing_required:['wallet'],wallet_error:'retCode=10003 retMsg=invalid key'})};if(url.includes('/quote')){quoteCalls++;return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'0.1',entry_price:'100',stop_price:'99',target_price:'102',target_distance:'2',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})};}return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-symbol'].listeners.input();await Promise.resolve();await Promise.resolve();await el['calc-quote'].listeners.click();console.log(JSON.stringify({quoteCalls,status:el['calc-quote-status'].textContent,err:el['calc-error'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["quoteCalls"] == 1
    assert "please wait for wallet/ticker prewarm" not in (data["status"] + data["err"])


def test_crypto_quote_failure_shows_backend_wallet_error_not_prewarm_gate() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC'; el['calc-sl-ticks'].value='5'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let quoteCalls=0;
global.fetch=async (url,opts={})=>{ if(url.includes('/instrument')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; if(url.includes('/prewarm')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({asset:'crypto',account:'demo',symbol:'BTCUSDT',ready_for_quote:false,missing_required:['wallet'],wallet_error:'retCode=10003'})}; if(url.includes('/quote')){quoteCalls++; return {ok:false,status:502,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'QUOTE_FAILED',message:'wallet down',debug:{dependency:'bybit_wallet_balance',path:'/v5/account/wallet-balance'}}})}; } return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-symbol'].listeners.input(); await Promise.resolve(); await el['calc-quote'].listeners.click(); console.log(JSON.stringify({quoteCalls,err:el['calc-error'].textContent,dbg:el['calc-error-debug'].textContent,status:el['calc-quote-status'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["quoteCalls"] == 1
    assert ("wallet down" in data["err"]) or ("bybit_wallet_balance" in (data["err"] + data["dbg"]))
    assert "please wait for wallet/ticker prewarm" not in (data["err"] + data["dbg"])


def test_stale_prewarm_response_is_ignored_after_symbol_or_account_change() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='5';el['calc-rr'].value='2';el['calc-risk'].value='1';
let quoteCalls=0; let prewarmResolver=null;
global.fetch=async (url,opts={})=>{if(url.includes('/instrument')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; if(url.includes('/prewarm')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:()=>new Promise((resolve)=>{prewarmResolver=resolve;})}; if(url.includes('/quote')){quoteCalls++; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'ETHUSDT',tick_size:'0.1',entry_price:'100',stop_price:'99',target_price:'102',target_distance:'2',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})};} return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{el['calc-symbol'].listeners.input();await Promise.resolve();el['account-toggle'].buttons.find((b)=>b.dataset.v==='live').click();if(prewarmResolver)prewarmResolver(JSON.stringify({asset:'crypto',account:'demo',symbol:'BTCUSDT',ready_for_quote:false,missing_required:['wallet'],wallet_error:'stale'}));await Promise.resolve();el['calc-symbol'].value='ETH';el['calc-symbol'].listeners.input();await Promise.resolve();await el['calc-quote'].listeners.click();console.log(JSON.stringify({quoteCalls,status:el['calc-quote-status'].textContent}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["quoteCalls"] == 1
    assert "stale" not in data["status"].lower()

def test_bybit_expired_demo_key_message_is_actionable() -> None:
    node = shutil.which("node"); assert node
    harness = r'''
const fs=require('fs'); const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(){this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle(){},add(){},remove(){}};}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle(){},add(){},remove(){}};}addEventListener(e,c){this.listeners[e]=c;}click(){this.listeners.click&&this.listeners.click();}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E()])); const mk=(v)=>v.map(x=>new B(x)); el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='5';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url)=> url.includes('/quote')?{ok:false,status:502,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'BYBIT_API_KEY_EXPIRED',account:'demo',message:'x',debug:{retCode:33004}}})}:{ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(fn)=>{fn();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click();console.log(el['calc-error'].textContent)})();'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True).stdout
    assert "Bybit Demo API key expired" in out


def test_bybit_expired_live_key_message_is_actionable() -> None:
    assert "BYBIT_API_KEY1/BYBIT_API_SECRET1" in JS_PATH.read_text(encoding='utf-8')


def test_instrument_specs_loading_does_not_remain_forever_after_failure() -> None:
    assert "Instrument specs unavailable for" in JS_PATH.read_text(encoding='utf-8')


def test_journal_summary_default_prompt_replaced_after_resolved_symbol_failure() -> None:
    assert "Journal summary unavailable for" in JS_PATH.read_text(encoding='utf-8')


def test_expired_key_error_does_not_reintroduce_prewarm_gate() -> None:
    assert "please wait for wallet/ticker prewarm" not in JS_PATH.read_text(encoding='utf-8').lower()


def test_render_error_debug_escapes_html_values() -> None:
    script = JS_PATH.read_text(encoding="utf-8")
    assert "replace(/</g, '&lt;')" in script
    assert "replace(/>/g, '&gt;')" in script
    assert "escapeHtml(k)" in script


def test_render_error_debug_escapes_runtime_html_payload() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='1111';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url)=>{ if(url.includes('/quote')) return {ok:false,status:502,statusText:'bad',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED',message:'save failed',debug:{'<img src=x onerror=alert(1)>':{value:'<script>x</script>'}}}})}; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; };
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-quote'].listeners.click();const out=el['calc-error-debug'].innerHTML;console.log(JSON.stringify({html:out,hasRawImg:out.includes('<img'),hasRawScript:out.includes('<script'),hasEscapedImg:out.includes('&lt;img'),hasEscapedScript:out.includes('&lt;script&gt;')}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["hasRawImg"] is False
    assert data["hasRawScript"] is False
    assert data["hasEscapedImg"] is True
    assert data["hasEscapedScript"] is True


def test_quote_failure_overwrites_stale_prewarm_ready_status_and_renders_debug() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTC';el['calc-sl-ticks'].value='1111';el['calc-rr'].value='2';el['calc-risk'].value='1';
global.fetch=async (url,opts={})=>{if(url.includes('/instrument')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({symbol:'BTCUSDT'})}; if(url.includes('/prewarm')) return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ready_for_quote:true,asset:'crypto',account:'demo',symbol:'BTCUSDT'})}; if(url.includes('/quote')) return {ok:false,status:502,statusText:'bad',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:{code:'BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED',message:'save failed',debug:{nested:{reason:'disk locked'}}}})}; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};eval(source);
(async()=>{await el['calc-symbol'].listeners.input();await Promise.resolve();await Promise.resolve();await el['calc-quote'].listeners.click();console.log(JSON.stringify({status:el['calc-quote-status'].textContent,error:el['calc-error'].textContent,debug:el['calc-error-debug'].innerHTML}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert "Quote data ready" not in data["status"]
    assert "save failed" in data["error"] or "BYBIT_DEMO_CALC_CONTEXT_SAVE_FAILED" in data["error"]
    assert "disk locked" in data["debug"] or "nested" in data["debug"]

def test_submit_button_visual_states_and_duplicate_blocking() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
class E { constructor(id){ this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.dataset={}; this.style={}; this.listeners={}; this.buttons=[]; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this.title=''; } addEventListener(e,cb){this.listeners[e]=cb;} querySelectorAll(s){return s==='button'?this.buttons:[];} }
class B { constructor(v){ this.dataset={v}; this.listeners={}; this.classList={toggle:()=>{},add:()=>{},remove:()=>{}}; this.disabled=false; this._attrs={}; this.title=''; } addEventListener(e,cb){this.listeners[e]=cb;} click(){ if(this.listeners.click) this.listeners.click(); } setAttribute(k,v){this._attrs[k]=String(v); this[k]=v;} getAttribute(k){return this._attrs[k];} removeAttribute(k){delete this._attrs[k];} }
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));
const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']); el['asset-toggle'].buttons=mk(['crypto','fx']); el['account-toggle'].buttons=mk(['live','demo']); el['side-toggle'].buttons=mk(['buy','sell']); el['order-toggle'].buttons=mk(['market','limit']); el['webhook-toggle'].buttons=mk(['no','yes']); el['test-toggle'].buttons=mk(['no','yes']); el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT'; el['calc-sl-ticks'].value='10'; el['calc-rr'].value='2'; el['calc-risk'].value='1';
let mode='success'; let submitCalls=0; let resolveSubmit;
const submitPromise = () => new Promise((r)=>{ resolveSubmit = r; });
let gate = null;
let quoteSeq = 0;
global.fetch=async (url,opts={})=>{ if(url.includes('/quote')) { quoteSeq += 1; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',entry_price:String(79300 + quoteSeq),stop_price:String(78784.5 + quoteSeq),target_price:String(79669 + quoteSeq),quantity:'0.012',calculation_context_id:'ctx'+String(quoteSeq),quote_created_at_ms:123 + quoteSeq})}; } if(url.includes('/submit')){ submitCalls += 1; if(mode==='success'){ gate = submitPromise(); await gate; return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({ok:true})}; } return {ok:false,status:400,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:'fail'})}; } return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})}; };
global.document={getElementById:(id)=>el[id]}; global.navigator={clipboard:{writeText:async()=>{}}}; global.setTimeout=(f)=>{f();return 1;}; global.clearTimeout=()=>{};
eval(source);
(async()=>{
  await el['calc-quote'].listeners.click();
  const p1 = el['calc-submit'].listeners.click();
  const submittingState = el['calc-submit'].dataset.submitVisualState;
  const submittingText = el['calc-submit'].textContent;
  const p2 = el['calc-submit'].listeners.click();
  const p3 = el['calc-submit'].listeners.click();
  const submitCallsWhilePending = submitCalls;
  const pendingStateAfterDuplicates = el['calc-submit'].dataset.submitVisualState || '';
  const pendingTextAfterDuplicates = el['calc-submit'].textContent;
  const pendingErrorAfterDuplicates = el['calc-error'].textContent;
  el['calc-risk'].value = '2';
  el['calc-risk'].listeners.input();
  const staleClearsVisualState = !el['calc-submit'].dataset.submitVisualState && el['calc-submit'].textContent === 'Submit Order';
  await el['calc-quote'].listeners.click();
  await el['calc-submit'].listeners.click();
  const submitCallsAfterInvalidateRecalc = submitCalls;
  resolveSubmit();
  await p1; await p2; await p3;
  const successState = el['calc-submit'].dataset.submitVisualState || '';
  el['calc-risk'].listeners.input();
  const clearedAfterInvalidate = !el['calc-submit'].dataset.submitVisualState && el['calc-submit'].textContent === 'Submit Order';
  await el['calc-quote'].listeners.click();
  mode='fail';
  await el['calc-submit'].listeners.click();
  const failCleared = !el['calc-submit'].dataset.submitVisualState && el['calc-submit'].textContent === 'Submit Order' && !!el['calc-error'].textContent;
  el['calc-submit'].disabled = true;
  await el['calc-submit'].listeners.click();
  const disabledNoHighlight = !el['calc-submit'].dataset.submitVisualState;
  console.log(JSON.stringify({submittingState,submittingText,submitCallsWhilePending,pendingStateAfterDuplicates,pendingTextAfterDuplicates,pendingErrorAfterDuplicates,staleClearsVisualState,submitCallsAfterInvalidateRecalc,successState,submitCalls,clearedAfterInvalidate,failCleared,disabledNoHighlight}));
})();
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["submittingState"] == "submitting"
    assert data["submittingText"] == "Submitting..."
    assert data["submitCallsWhilePending"] == 1
    assert data["pendingStateAfterDuplicates"] == "submitting"
    assert data["pendingTextAfterDuplicates"] == "Submitting..."
    assert data["pendingErrorAfterDuplicates"] == ""
    assert data["staleClearsVisualState"] is True
    assert data["submitCallsAfterInvalidateRecalc"] == 1
    assert data["submitCalls"] == 2
    assert data["successState"] == "success"
    assert data["clearedAfterInvalidate"] is True
    assert data["failCleared"] is True
    assert data["disabledNoHighlight"] is True


def test_pattern_selector_buttons_invalidate_quote_and_payloads_include_pattern() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    assert "id=\"pattern-toggle\"" not in source  # UI container is server-rendered.
    assert "function setPatternButtons()" in source
    pattern_block = source.split("function setPatternButtons()", 1)[1].split("async function resolveSymbolAndLoad", 1)[0]
    assert "['','None']" in pattern_block
    assert "['range','range']" in pattern_block
    assert "['channel','channel']" in pattern_block
    assert "invalidateQuote()" in pattern_block
    assert source.count("pattern: state.pattern") >= 2
    assert "`pattern=${state.pattern || payload.pattern || ''}`" in source


def test_quality_criteria_buttons_invalidate_quote_and_are_in_both_payloads() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    for function_name, state_field, container_id in (
        ("setEmaButtons", "ema", "ema-toggle"),
        ("setVwapButtons", "vwap", "vwap-toggle"),
        ("setAthsAtlsButtons", "aths_atls", "aths-atls-toggle"),
        ("setRoundNumberButtons", "round_number", "round-number-toggle"),
    ):
        assert f"function {function_name}()" in source
        block = source.split(f"function {function_name}()", 1)[1].split("\n  function ", 1)[0]
        assert f"$('{container_id}')" in block
        assert "invalidateQuote()" in block
        assert source.count(f"{state_field}: state.{state_field}") >= 2
        assert f"`{state_field}=${{state.{state_field} || payload.{state_field} || ''}}`" in source
    assert "['All-Time High','All-Time High']" in source
    assert "['All-Time Low','All-Time Low']" in source


def test_vwap_toggle_posts_payload_and_invalidates_ready_quote() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class E{constructor(i){this.id=i;this.value='';this.textContent='';this._innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;}set innerHTML(v){this._innerHTML=String(v||'');this.buttons=[...this._innerHTML.matchAll(/data-v="([^"]*)"/g)].map((m)=>new B(m[1]));}get innerHTML(){return this._innerHTML;}addEventListener(e,c){this.listeners[e]=c;}querySelectorAll(s){return s==='button'?this.buttons:[];}}
class B{constructor(v){this.dataset={v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){if(this.listeners.click)this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','vwap-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)]));const mk=(v)=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['broker-toggle'].buttons=mk(['oanda','pepperstone']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);el['timeframe-toggle'].buttons=[];
el['calc-symbol'].value='BTCUSDT';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';
const payloads=[];
global.fetch=async (url,opts={})=>{if(url.includes('/api/calculator/quote')){payloads.push(JSON.parse(opts.body||'{}'));return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({broker:'bybit',symbol:'BTCUSDT',tick_size:'1',entry_price:'100',stop_price:'90',target_price:'120',target_distance:'20',quantity:'1',estimated_fees_or_spread:'1',estimated_total_loss:'10',estimated_reward:'20'})};}return {ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({status:'no_data'})};};
global.document={getElementById:(id)=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(f)=>{f();return 1;};global.clearTimeout=()=>{};global.setInterval=()=>1;global.clearInterval=()=>{};
eval(source);
(async()=>{el['vwap-toggle'].buttons.find((b)=>b.dataset.v==='Yes').click();await el['calc-quote'].listeners.click();const ready=el['calc-quote-status'].textContent;el['vwap-toggle'].buttons.find((b)=>b.dataset.v==='No').click();const stale=el['calc-quote-status'].textContent;await el['calc-quote'].listeners.click();console.log(JSON.stringify({payloads,ready,stale,summary:el['calc-request-summary'].innerHTML}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["payloads"][0]["vwap"] == "Yes"
    assert data["payloads"][1]["vwap"] == "No"
    assert data["ready"] == "Quote ready."
    assert "Quote changed" in data["stale"]
    assert "vwap=No" in data["summary"]


def test_local_trendline_plan_ui_saves_manual_anchors_and_manages_status() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS behavior test"
    harness = r'''
process.env.TZ='Australia/Brisbane'; const fs=require('fs');const source=fs.readFileSync(process.argv[1],'utf8');
class B{constructor(v,a){this.dataset=a?{tlAction:a,tlId:v}:{v};this.listeners={};this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this._attrs={};}addEventListener(e,c){this.listeners[e]=c;}click(){return this.listeners.click&&this.listeners.click();}setAttribute(k,v){this._attrs[k]=String(v);}getAttribute(k){return this._attrs[k];}removeAttribute(k){delete this._attrs[k];}}
class E{constructor(i){this.id=i;this.value='';this.textContent='';this._innerHTML='';this.dataset={};this.style={};this.listeners={};this.buttons=[];this.actions=[];this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};this.disabled=false;this.checked=false;}set innerHTML(v){this._innerHTML=String(v||'');this.buttons=[...this._innerHTML.matchAll(/data-v="([^"]*)"/g)].map(m=>new B(m[1]));this.actions=[...this._innerHTML.matchAll(/data-tl-action="([^"]*)" data-tl-id="([^"]*)"/g)].map(m=>new B(m[2],m[1]));}get innerHTML(){return this._innerHTML;}addEventListener(e,c){this.listeners[e]=c;}click(){return this.listeners.click&&this.listeners.click();}querySelectorAll(s){if(s==='button')return this.buttons;if(s==='[data-tl-action]')return this.actions;return [];}}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list'];
const el=Object.fromEntries(ids.map(i=>[i,new E(i)])); const mk=v=>v.map(x=>new B(x));
el['risk-toggle'].buttons=mk(['fixed_aud','percent']);el['asset-toggle'].buttons=mk(['crypto','fx']);el['broker-toggle'].buttons=mk(['oanda','pepperstone']);el['account-toggle'].buttons=mk(['live','demo']);el['side-toggle'].buttons=mk(['buy','sell']);el['order-toggle'].buttons=mk(['market','limit']);el['webhook-toggle'].buttons=mk(['no','yes']);el['test-toggle'].buttons=mk(['no','yes']);
el['calc-symbol'].value='BTCUSDT';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';el['calc-risk'].value='1';el['trendline-right-extension'].checked=true;el['trendline-trigger-mode'].value='touch';el['trendline-cross-direction'].value='upward';el['trendline-price-basis'].value='executable';el['trendline-tolerance-ticks'].value='2';el['trendline-expiry'].value='lifetime';
let plans=[];let calls=[];let confirms=0;let allowConfirm=false;global.confirm=()=>{confirms++;return allowConfirm;};
const reply=o=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(o)});
global.fetch=async(url,opts={})=>{calls.push([url,opts.method||'GET',opts.body]);if(url.includes('/api/calculator/bootstrap'))return reply({app_profile:'local',trendline_plans_available:true,webhook:{available:true}});if(url.includes('/api/calculator/instrument'))return reply({symbol:'BTCUSDT'});if(url==='/api/trendline-plans'&&(opts.method||'GET')==='GET')return reply({ok:true,plans,monitoring_active:false,execution_enabled:false});if(url==='/api/trendline-plans'&&opts.method==='POST'){const p=JSON.parse(opts.body);const plan={...p,plan_id:'plan123456',status:'draft',anchors:p.anchors};plans=[plan];return reply({ok:true,plan,monitoring_active:false,execution_enabled:false});}if(url.includes('/arm')){plans[0]={...plans[0],status:'armed'};return reply({ok:true,plan:plans[0]});}if(url.includes('/cancel')){plans[0]={...plans[0],status:'cancelled'};return reply({ok:true,plan:plans[0]});}if(url.includes('/plan123456')&&opts.method==='PATCH'){plans[0]={...plans[0],...JSON.parse(opts.body)};return reply({ok:true,plan:plans[0]});}if(url.includes('/api/calculator/submit')||url.includes('webhook')||url.includes('/api/calculator/quote'))throw new Error('unexpected calculator/broker request');return reply({status:'no_data'});};
global.document={getElementById:id=>el[id]};global.navigator={clipboard:{writeText:async()=>{}}};global.setTimeout=(fn)=>{fn();return 1;};global.clearTimeout=()=>{};global.setInterval=()=>1;global.clearInterval=()=>{};
eval(source);
(async()=>{await Promise.resolve();await Promise.resolve();el['calc-symbol'].listeners.input();await Promise.resolve();await Promise.resolve();await Promise.resolve();el['trendline-anchor-1-time'].value='2025-01-02T03:04';el['trendline-anchor-2-time'].value='2025-01-02T04:04';el['trendline-anchor-1-price'].value='100';el['trendline-anchor-2-price'].value='101';el['trendline-anchor-1-time'].listeners.input();el['trendline-anchor-2-time'].listeners.input();const utcPreview=el['trendline-anchor-1-utc'].textContent;el['trendline-trigger-mode'].listeners.change();const first=el['trendline-save'].click();const duplicate=el['trendline-save'].click();await first;await duplicate;const actions=()=>el['trendline-plan-list'].querySelectorAll('[data-tl-action]');const action=(name)=>{const button=actions().find(b=>b.dataset.tlAction===name);if(!button)throw new Error(`Expected rendered ${name} action; list=${el['trendline-plan-list'].innerHTML}`);return button;};await action('edit').click();el['trendline-tolerance-ticks'].value='3';await el['trendline-save'].click();await action('arm').click();allowConfirm=true;await action('arm').click();await action('cancel').click();console.log(JSON.stringify({panel:el['trendline-plans-panel'].style.display,utc1:utcPreview,postCalls:calls.filter(c=>c[0]==='/api/trendline-plans'&&c[1]==='POST').length,postPayload:JSON.parse(calls.find(c=>c[0]==='/api/trendline-plans'&&c[1]==='POST')[2]),patch:calls.some(c=>c[1]==='PATCH'),armCalls:calls.filter(c=>c[0].includes('/arm')).length,cancelCalls:calls.filter(c=>c[0].includes('/cancel')).length,confirms,status:plans[0].status,noUnexpected:!calls.some(c=>c[0].includes('/api/calculator/submit')||c[0].includes('/webhook')||c[0].includes('/api/calculator/quote'))}));})();
'''
    out = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["panel"] == ""
    assert "2025-01-01T17:04:00.000Z" in data["utc1"]
    assert data["postCalls"] == 1
    assert data["postPayload"]["instrument"] == "BTCUSDT"
    assert data["postPayload"]["cross_direction"] == "either"
    assert data["patch"] and data["armCalls"] == 1 and data["cancelCalls"] == 1
    assert data["confirms"] == 2 and data["status"] == "cancelled" and data["noUnexpected"]


def test_trendline_monitor_ui_failures_live_arm_and_limit_round_trip() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
class Button {
  constructor(v, action) { this.dataset=action?{tlAction:action,tlId:v}:{v}; this.listeners={}; this.disabled=false; this.attrs={}; this.classList={toggle(){},add(){},remove(){}}; }
  addEventListener(e,f){this.listeners[e]=f;}
  click(){if(!this.disabled && this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} removeAttribute(k){delete this.attrs[k];}
}
class Element extends Button {
  constructor(id){super('');this.id=id;this.value='';this.style={};this.textContent='';this.checked=false;this.buttons=[];this.actions=[];}
  set innerHTML(v){this.html=String(v);this.buttons=[...this.html.matchAll(/data-v="([^"]*)"/g)].map(m=>new Button(m[1]));this.actions=[...this.html.matchAll(/data-tl-action="([^"]*)" data-tl-id="([^"]*)"/g)].map(m=>new Button(m[2],m[1]));}
  get innerHTML(){return this.html||'';}
  querySelectorAll(s){return s==='button'?this.buttons:s==='[data-tl-action]'?this.actions:[];}
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function run(profile){
 const el=Object.fromEntries(ids.map(id=>[id,new Element(id)])), calls=[];
 const groups={'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']};
 for(const [id,values] of Object.entries(groups))el[id].buttons=values.map(v=>new Button(v));
 Object.assign(el['calc-symbol'],{value:'BTCUSDT'});el['calc-risk'].value='1';el['calc-sl-ticks'].value='10';el['calc-rr'].value='2';
 let plans=[],confirmed=false,running=false,fail=false,changeOnFailure=false;const prompts=[];
 const response=(data,status=200)=>({ok:status<400,status,statusText:'mock',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
 const fetch=async(url,opts={})=>{
  const method=opts.method||'GET';calls.push({url,method,body:opts.body?JSON.parse(opts.body):null});
  if(url==='/api/calculator/bootstrap')return response({app_profile:profile,trendline_plans_available:profile==='local',trendline_monitoring:{running:false},webhook:{available:true}});
  if(url.startsWith('/api/calculator/instrument?'))return response({symbol:'BTCUSDT',asset:'crypto',broker:'bybit'});
  if(url==='/api/trendline-plans/monitor/status')return response({ok:true,running});
  if(url.startsWith('/api/trendline-plans/monitor/')){
   if(fail){if(changeOnFailure)running=!running;return response({detail:'mock failure'},503);}
   running=url.endsWith('/start');plans.forEach(p=>p.monitoring_eligible=false);return response({ok:true,running});
  }
  if(url==='/api/trendline-plans' && method==='GET')return response({ok:true,plans,monitoring_active:running});
  if(url==='/api/trendline-plans' && method==='POST'){
   const p=JSON.parse(opts.body);plans=[{...p,plan_id:'plan-1',revision:1,monitor_generation:'session-1',status:'draft',order_intent:p.order_type,action:p.side,order_metadata:Object.fromEntries(['setup','pattern','ema','vwap','aths_atls','round_number'].map(k=>[k,p[k]||null]))}];return response({ok:true,plan:plans[0]});
  }
  if(url==='/api/trendline-plans/plan-1' && method==='PATCH'){
   plans[0]={...plans[0],...JSON.parse(opts.body)};return response({ok:true,plan:plans[0]});
  }
  if(url==='/api/trendline-plans/plan-1/arm'){plans[0].status='armed';plans[0].monitoring_eligible=true;running=true;if(fail)return response({detail:'lost arm response'},503);return response({ok:true,plan:plans[0],monitoring_active:true});}
  throw new Error('Unexpected request '+url);
 };
 // A VM does not inherit browser/Node web globals. Symbol resolution uses
 // AbortController before its first awaited fetch, just as in the browser.
 vm.runInNewContext(source,{document:{getElementById:id=>el[id]},fetch,confirm:text=>{prompts.push(text);return confirmed;},navigator:{clipboard:{writeText:async()=>{}}},setTimeout:fn=>{fn();return 1;},clearTimeout(){},setInterval:()=>1,clearInterval(){},console,Date,URL,URLSearchParams,AbortController});
 await flush();
 if(profile!=='local'){assert.equal(el['trendline-plans-panel'].style.display,'none');assert(!calls.some(c=>c.url.startsWith('/api/trendline-plans')));return;}
 assert.equal(el['trendline-plans-panel'].style.display,'');
 el['calc-symbol'].listeners.input();await flush();
 assert(el['calc-canonical-symbol'].textContent.includes('BTCUSDT'),'Canonical resolution must complete before saving');
 el['timeframe-toggle'].buttons.find(b=>b.dataset.v==='1m').click();
 el['test-toggle'].buttons.find(b=>b.dataset.v==='yes').click();
 el['order-toggle'].buttons.find(b=>b.dataset.v==='limit').click();
 el['calc-limit'].value='99.25';el['trendline-anchor-1-time'].value='2025-01-01T01:00';el['trendline-anchor-2-time'].value='2025-01-01T02:00';el['trendline-anchor-1-price'].value='100';el['trendline-anchor-2-price'].value='101';
 el['trendline-price-basis'].value='executable';
 const save=el['trendline-save'].click();el['trendline-save'].click();await save;
 assert.equal(calls.filter(c=>c.method==='POST' && c.url==='/api/trendline-plans').length,1,el['trendline-plan-status'].textContent);
 assert.strictEqual(plans[0].test_trade,true);assert.equal(plans[0].limit_entry_price,'99.25');assert.equal(plans[0].order_intent,'limit');
 const action=name=>{const b=el['trendline-plan-list'].actions.find(b=>b.dataset.tlAction===name);assert(b,'Missing action '+name);return b;};
 el['order-toggle'].buttons.find(b=>b.dataset.v==='market').click();el['calc-limit'].value='123';
 await action('edit').click();assert.equal(el['calc-limit'].value,'99.25');await el['trendline-save'].click();
 const patch=calls.find(c=>c.method==='PATCH');assert.equal(patch.body.limit_entry_price,'99.25');assert.equal(patch.body.order_type,'limit');
 await action('arm').click();assert(!calls.some(c=>c.url.endsWith('/arm')));
 confirmed=true;await action('arm').click();assert.deepEqual(calls.find(c=>c.url.endsWith('/arm')).body,{confirm_live_execution:true,revision:1,monitor_generation:'session-1'});
 assert(el['trendline-plan-list'].innerHTML.includes('Armed and monitoring'));
 assert.equal(el['trendline-monitor-start'].listeners.click,undefined);
 assert(!calls.some(c=>c.url.includes('/monitor/') && c.method==='POST'));
 await el['trendline-monitor-stop'].click();
 assert(el['trendline-plan-list'].innerHTML.includes('Paused'));
 fail=true;const pending=action('arm').click();action('arm').click();await pending;
 assert(el['trendline-plan-list'].innerHTML.includes('Armed and monitoring'));
 // Arm is the sole activation request. Stop refreshes the saved cards.
 assert.equal(calls.filter(c=>c.url.endsWith('/arm')).length,2);
 assert.equal(calls.filter(c=>c.url.endsWith('/monitor/start')).length,0);
 assert(!calls.some(c=>c.url.includes('/quote')||c.url.includes('/submit')||c.url.includes('webhook')));
 assert(prompts.some(text=>text.includes('Live')&&text.includes('actual broker order')&&text.includes('Test Yes')));
 fail=false;await el['trendline-monitor-stop'].click();plans[0].test_trade=false;await el['trendline-refresh'].click();
 const previous=calls.filter(c=>c.url.endsWith('/arm')).length;confirmed=false;await action('arm').click();
 assert.equal(calls.filter(c=>c.url.endsWith('/arm')).length,previous);
 confirmed=true;await action('arm').click();
 assert.equal(calls.filter(c=>c.url.endsWith('/arm')).length,previous+1);

}
(async()=>{await run('render');await run('local');console.log('ok');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_trendline_reconciliation_warning_is_visible() -> None:
    node = shutil.which("node")
    assert node
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
// Execute the production renderer unchanged, isolated from application startup.
const start=source.indexOf('  function renderTrendlineMonitor(status) {');
const end=source.indexOf('  async function trendlineMonitor(action)',start);
assert(start>=0 && end>start,'Production monitor renderer must exist');
const elements=Object.fromEntries(['trendline-monitor-status','trendline-monitor-start','trendline-monitor-stop'].map(id=>[id,{textContent:'',disabled:false}]));
let requests=0;
const context=vm.createContext({$:id=>elements[id],state:{trendlineMonitorPending:false},fetch:()=>{requests++;throw new Error('No request allowed');}});
vm.runInContext(source.slice(start,end),context);
for(const running of [false,true]) {
  for(const count of [0,1,3,null]) {
    const message=count===null?'Reconciliation status unknown: registry unavailable. Automatic retry remains disabled.':count?'Manual broker reconciliation required; automatic retry is disabled.':null;
    context.renderTrendlineMonitor({running,reconciliation_required:count,reconciliation_message:message});
    const text=elements['trendline-monitor-status'].textContent;
    assert(text.includes(running?'Monitoring running':'Monitoring stopped'));
    if(count===null){assert(text.includes(message));assert(!text.includes('0 plans'));}
    else if(count>0){assert(text.includes(`${count} ${count===1?'plan requires':'plans require'} manual broker reconciliation`));assert(text.includes(message));}
    else assert(!text.includes('reconciliation'));
    assert.equal(elements['trendline-monitor-start'].disabled,running);
    assert.equal(elements['trendline-monitor-stop'].disabled,!running);
  }
}
context.renderTrendlineMonitor({running:false,reconciliation_required:null});
assert(elements['trendline-monitor-status'].textContent.includes('Reconciliation status unknown. Automatic retry remains disabled.'));
assert.equal(requests,0);
console.log('ok');
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_calculator_submits_without_pre_entry_classification():
    data = _run_usdjpy_auto_route_harness("classification")["checks"]
    assert data["unsetChoice"]
    for payload in (data["quotePayload"], data["submitPayload"]):
        for field in ("timeframe", "setup", "pattern", "ema", "vwap", "aths_atls", "round_number"):
            assert payload[field] == ""
        assert payload["risk_value"] == "1" and payload["risk_mode"] == "percent"
    submitted = data["submitPayload"]
    assert submitted["broker"] == "oanda" and submitted["symbol"] == "USD_JPY"
    assert submitted["quantity"] == "1000" and submitted["stop_loss_price"] == "149.990"
    assert submitted["take_profit_price"] == "150.020" and submitted["order_type"] == "market"
    assert submitted["calculation_context_id"] == "ctx-usdjpy"
    assert data["submits"] == 1
    assert data["invalidRiskDisabled"] and "Risk must be positive" in data["invalidRiskError"]
    assert data["invalidQuoteDisabled"] and data["webhookDisabled"]


def _run_anchor_import_behavior(mode, timezone, saved_record=None, table_packets=None, image_options=None):
    import os
    node = r"C:\Program Files\nodejs\node.exe"
    harness = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
class Button {
  constructor(v, action) { this.dataset=action?{tlAction:action,tlId:v}:{v}; this.listeners={}; this.disabled=false; this.attrs={}; this.classList={toggle(){},add(){},remove(){}}; }
  addEventListener(e,f){this.listeners[e]=f;}
  click(){if(!this.disabled && this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} removeAttribute(k){delete this.attrs[k];}
}
class Element extends Button {
  constructor(id){super('');this.id=id;this.value='';this.style={};this.textContent='';this.checked=false;this.buttons=[];this.actions=[];this.children=[];}
  appendChild(node){this.children.push(node);return node;}
  dispatchEvent(event){return this.listeners[event.type]?.(event);}
  set innerHTML(v){this.html=String(v);this.buttons=[...this.html.matchAll(/data-v="([^"]*)"/g)].map(m=>new Button(m[1]));this.actions=[...this.html.matchAll(/data-tl-action="([^"]*)" data-tl-id="([^"]*)"/g)].map(m=>new Button(m[2],m[1]));}
  get innerHTML(){return this.html||'';}
  querySelectorAll(s){return s==='button'?this.buttons:s==='[data-tl-action]'?this.actions:[];}
}
const ids=['calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-import-text','trendline-import-preview','trendline-import-preview-btn','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const flush=()=>new Promise(resolve=>setImmediate(resolve));

async function run(){
 const el=Object.fromEntries(ids.filter(id=>process.argv[2]!=='deletion'||id!=='trendline-import-preview-btn').map(id=>[id,new Element(id)])),calls=[];
 const dynamic=[];
 const imageOptions=process.argv[5]?JSON.parse(process.argv[5]):null,imageReplies=[];
 const groups={'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']};
 for(const [id,values] of Object.entries(groups))el[id].buttons=values.map(v=>new Button(v));
 el['calc-symbol'].value='BTCUSDT';el['calc-risk'].value='2.5';el['calc-sl-ticks'].value='50';el['calc-rr'].value='3.25';
 let plans=[],deleteReply=null,deleteCalls=0,holdList=false,releaseList=null,confirmAnswer=false,prompts=0,dismissCalls=0,dismissReply=null;
 if(process.argv[2]==='deletion'){
  const base={broker:'bybit',account:'demo',instrument:'BTCUSDT',action:'buy',order_intent:'limit',limit_entry_price:'99.25',test_trade:true,anchors:[{timestamp_ms:1793368797123,price:'100.25'},{timestamp_ms:1793368801987,price:'101.5'}],right_extension:true,trigger_mode:'confirmed_cross',cross_direction:'upward',trigger_price_basis:'midpoint',tolerance_ticks:2,risk_mode:'percent',risk_value:'7.25',stop_loss_ticks:80,rr_target:'4.5',timeframe:'1m',expiry_at_ms:1893456000000,order_metadata:{setup:'Pullback',pattern:'channel',ema:'20',vwap:'No',aths_atls:null,round_number:'Yes'}};
  plans=[...['closed','simulated','confirm_finished'].map(kind=>({...base,plan_id:kind,status:'submitted',revision:7,can_delete:false,can_dismiss_calculator:true,calculator_finish_kind:kind})),{...base,plan_id:'draft-a',status:'draft',can_delete:true},{...base,plan_id:'cancelled-c',status:'cancelled',can_delete:true},{...base,plan_id:'other-b',status:'draft',can_delete:true},{...base,plan_id:'protected-p',status:'armed',can_delete:false},{...base,plan_id:'expired-e',status:'expired',can_delete:true},{...base,plan_id:'expired-protected',status:'expired',can_delete:false}];
 }

 const response=(data,status=200)=>({ok:status<400,status,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
 const fetch=async(url,opts={})=>{
  const method=opts.method||'GET';const body=url==='/api/calculator/anchor-image'?null:opts.body?JSON.parse(opts.body):null;calls.push({url,method,body});
  if(url==='/api/calculator/anchor-image') {
   if(imageOptions.http)return globalThis.fetch(imageOptions.http+url,opts);
   if(imageOptions.rpc){
    const index=calls.filter(c=>c.url==='/api/calculator/anchor-image').length-1;
    const path=require('path'),replyPath=path.join(imageOptions.rpc,'response-'+index+'.json');
    fs.writeFileSync(path.join(imageOptions.rpc,'request-'+index+'.png'),Buffer.from(opts.body));
    const deadline=Date.now()+90000;while(!fs.statSync(replyPath).size){if(Date.now()>deadline)throw Error('Offline OCR transport timed out');await new Promise(resolve=>setTimeout(resolve,5));}
    const answer=JSON.parse(fs.readFileSync(replyPath,'utf8'));return response(answer.body,answer.status);
   }
   return new Promise(resolve=>imageReplies.push({resolve,signal:opts.signal}));
  }
  if(url==='/api/calculator/bootstrap')return response({app_profile:'local',trendline_plans_available:true,trendline_monitoring:{running:false},webhook:{available:true}});
  if(url.startsWith('/api/calculator/instrument?'))return response(imageOptions?(imageOptions.expected.symbol.startsWith('BYBIT:')?{symbol:'XRPUSDT',asset:'crypto',broker:'bybit'}:{symbol:'NZD_USD',asset:'fx',broker:'pepperstone'}):{symbol:'BTCUSDT',asset:'crypto',broker:'bybit'});
  if(url==='/api/trendline-plans'&&method==='GET'){
   if(holdList){const old=JSON.parse(JSON.stringify(plans));return new Promise(resolve=>{releaseList=()=>resolve(response({plans:old}));});}
   return response({plans});
  }
  if(method==='POST'&&url.endsWith('/dismiss-calculator')){
   dismissCalls++;const id=url.split('/')[3];return new Promise(resolve=>{dismissReply=(success,matching=true)=>{const plan=plans.find(p=>p.plan_id===id);if(success)plan.calculator_dismissal={plan_id:id,plan_revision:body.revision};resolve(response(success?{ok:true,dismissed_plan_id:matching?id:'wrong',plan}:{detail:'synthetic storage failure'},success?200:500));};});
  }
  if(method==='DELETE'&&url.startsWith('/api/trendline-plans/')){
   deleteCalls++;const id=url.split('/').pop();return new Promise(resolve=>{deleteReply=(success)=>{if(success)plans=plans.filter(p=>p.plan_id!==id);resolve(response(success?{ok:true,deleted_plan_id:id}:{detail:'synthetic disk busy'},success?200:500));};});
  }

  if(url==='/api/trendline-plans'&&method==='POST'){const created={...body,plan_id:process.argv[2]==='deletion'?'replacement-draft':'anchor-draft',status:'draft',can_delete:true,order_intent:body.order_type,action:body.side,order_metadata:Object.fromEntries(['setup','pattern','ema','vwap','aths_atls','round_number'].map(key=>[key,body[key]||null]))};plans=process.argv[2]==='deletion'?[...plans,created]:[created];return response({plan:created});}
  if(url==='/api/trendline-plans/anchor-draft'&&method==='PATCH'){plans[0]={...plans[0],...body};return response({plan:plans[0]});}
  throw new Error('Unexpected mocked request '+url);
 };
 vm.runInNewContext(source,{document:{getElementById:id=>el[id]||dynamic.find(node=>node.id===id),createElement:tag=>{const node=new Element('');dynamic.push(node);return node;}},fetch,confirm:message=>{assert(message.includes('no remaining broker order'));prompts++;return confirmAnswer;},navigator:{clipboard:{writeText:async()=>{}}},setTimeout:(fn,ms)=>{if(!imageOptions || !ms || ms<=250)fn();return 1;},clearTimeout(){},setInterval:()=>1,clearInterval(){},console,Date,Intl,URL,URLSearchParams,AbortController});
 await flush();el['calc-symbol'].listeners.input();await flush();
 el['timeframe-toggle'].buttons.find(b=>b.dataset.v==='1m').click();
 el['order-toggle'].buttons.find(b=>b.dataset.v==='limit').click();el['calc-limit'].value='99.25';
 el['trendline-trigger-mode'].value='confirmed_cross';el['trendline-cross-direction'].value='upward';el['trendline-price-basis'].value='midpoint';el['trendline-tolerance-ticks'].value='2';el['trendline-right-extension'].checked=false;el['trendline-expiry'].value='1d';
 const packet=process.argv[4] ? JSON.parse(JSON.parse(process.argv[4])[0]) : {schema:'ltt-anchors/v1',symbol:'BYBIT:BTCUSDT.P',timeframe:'1',anchors:[{timestamp_ms:Date.parse('2026-10-30T13:59:57.123Z'),price:'100.00012345'},{timestamp_ms:Date.parse('2026-10-30T14:00:01.987Z'),price:'101.00098765'}]};
 const copy=x=>JSON.parse(JSON.stringify(x));
 const preview=text=>{el['trendline-import-text'].value=text;el['trendline-import-text'].listeners.input();};
 const fields=()=>['trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-2-time','trendline-anchor-2-price'].map(id=>el[id].value);
 const paste=text=>{
  let prevented=0, reads=0;
  el['trendline-import-text'].dispatchEvent({type:'paste',clipboardData:{getData:format=>{assert.equal(format,'text/plain');reads++;return text;}},preventDefault(){prevented++;}});
  assert.equal(prevented,1);assert.equal(reads,1,'Read the paste event once; no asynchronous clipboard access');
 };
 const edit=()=>el['trendline-plan-list'].actions.find(b=>b.dataset.tlAction==='edit').click();
 const expectedLocal=ms=>{const d=new Date(ms),p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3,'0')}`;};
 if(imageOptions){
  const isBybit=imageOptions.expected.symbol.startsWith('BYBIT:');
  el['asset-toggle'].buttons.find(b=>b.dataset.v===(isBybit?'crypto':'fx')).click();
  if(!isBybit)el['broker-toggle'].buttons.find(b=>b.dataset.v==='pepperstone').click();
  el['account-toggle'].buttons.find(b=>b.dataset.v==='demo').click();
  el['calc-symbol'].value=isBybit?'XRPUSDT':'NZDUSD';el['calc-symbol'].listeners.input();await flush();
  el['timeframe-toggle'].buttons.find(b=>b.dataset.v===(isBybit?'1m':'5m')).click();await flush();
  const expected=imageOptions.expected, imports=()=>calls.filter(c=>c.url==='/api/calculator/anchor-image').length;
  const baseline=calls.length, settings=()=>JSON.stringify([el['calc-risk'].value,el['calc-sl-ticks'].value,el['calc-rr'].value,el['calc-limit'].value,el['trendline-trigger-mode'].value,el['trendline-expiry'].value]);
  let originalSettings=settings();const timings=[];
  const file=()=>({type:'image/png',name:'chart.png',size:123,arrayBuffer:async()=>imageOptions.file?fs.readFileSync(imageOptions.file):new Uint8Array([137,80,78,71]).buffer});
  const imagePaste=(value=file())=>el['trendline-import-text'].dispatchEvent({type:'paste',clipboardData:{items:[{kind:'file',type:'image/png',getAsFile:()=>value}]},preventDefault(){}});
  const imageDrop=()=>el['trendline-import-text'].dispatchEvent({type:'drop',dataTransfer:{files:[file()]},preventDefault(){}});
  const assertAnchors=()=>{expected.anchors.forEach((a,i)=>{assert.equal(el[`trendline-anchor-${i+1}-time`].dataset.anchorEpoch,String(a.timestamp_ms),el['trendline-import-preview'].textContent);assert.equal(el[`trendline-anchor-${i+1}-price`].value,a.price);});assert.equal(el['trendline-right-extension'].checked,expected.right_extension);assert.equal(settings(),originalSettings);};
  if(imageOptions.rpc||imageOptions.http){
   for(let i=0;i<4;i++){const start=performance.now();await imagePaste();timings.push(performance.now()-start);assertAnchors();assert.equal(el['trendline-import-preview'].textContent,'Success');}
   await imageDrop();assertAnchors();assert.equal(imports(),5);
   const beforeText=imports();paste(JSON.stringify(expected));assertAnchors();assert.equal(imports(),beforeText,'Text paste does not request OCR');
   assert(calls.slice(baseline).every(c=>c.url==='/api/calculator/anchor-image'),'Only the local OCR endpoint is requested by image import');
   console.log('IMAGE_TIMINGS '+JSON.stringify(timings));return;
  }
  const reply=(index,value=expected,status=200)=>imageReplies[index].resolve(response(status===200?{packet:value}:{detail:value},status));
  let first=imagePaste();await flush();let second=imagePaste();await flush();assert.equal(imports(),2);assert(imageReplies[0].signal.aborted);reply(1);await second;assertAnchors();const stable=fields();reply(0,{...expected,anchors:expected.anchors.map(a=>({...a,price:'999'}))});await first;assert.deepEqual(fields(),stable,'Older OCR cannot overwrite newer paste');
  let pending=imagePaste();await flush();paste(JSON.stringify(expected));reply(2,{...expected,anchors:expected.anchors.map(a=>({...a,price:'888'}))});await pending;assertAnchors();
  pending=imagePaste();await flush();el['trendline-anchor-1-price'].value='123.456';el['trendline-anchor-1-price'].listeners.input();reply(3);await pending;assert.equal(el['trendline-anchor-1-price'].value,'123.456','Partial manual edit supersedes OCR');
  pending=imagePaste();await flush();el['trendline-reset'].click();reply(4);await pending;assert.deepEqual(fields(),['','','',''],'Reset supersedes OCR');
  pending=imagePaste();await flush();el['account-toggle'].buttons.find(b=>b.dataset.v==='live').click();reply(5);await pending;assert.deepEqual(fields(),['','','',''],'Context change supersedes OCR');
  el['account-toggle'].buttons.find(b=>b.dataset.v==='demo').click();await flush();
  originalSettings=settings();pending=imageDrop();await flush();reply(6);await pending;assertAnchors();
  const intact=fields();pending=imagePaste();await flush();reply(7,{...expected,symbol:'PEPPERSTONE:USDJPY'});await pending;assert.deepEqual(fields(),intact);assert.equal(el['trendline-import-preview'].textContent,'Fail \u2014 wrong symbol. Retry.');
  pending=imagePaste();await flush();reply(8,'A1 milliseconds is ambiguous; no anchors applied.',422);await pending;assert.deepEqual(fields(),intact);assert.equal(el['trendline-import-preview'].textContent,'Fail \u2014 invalid anchor time. Retry.');
  const beforeInvalid=imports();await imagePaste({...file(),type:'image/jpeg'});assert.equal(imports(),beforeInvalid);assert.deepEqual(fields(),intact);
  assert(!calls.slice(baseline).some(c=>c.method==='POST'&&c.url!=='/api/calculator/anchor-image'),'No save, quote, arm, monitoring or order POST');
  console.log('image stale/drop controls passed');return;
 }
 if(process.argv[2]==='deletion'){
  assert(!el['trendline-import-preview-btn'],'Removed Preview button is absent');
  const action=(id,name)=>el['trendline-plan-list'].actions.find(b=>b.dataset.tlId===id&&b.dataset.tlAction===name);
  assert(action('draft-a','delete'));assert(action('cancelled-c','delete'));assert(!action('protected-p','delete'));assert(action('draft-a','cancel'));
  assert(action('expired-e','delete'));assert(!action('expired-protected','delete'));
  await action('draft-a','edit').click();
  const noImportRequests=calls.length;preview(JSON.stringify(packet));assert.equal(el['trendline-import-preview'].textContent,'Success');const initial=fields();
  preview('');assert.equal(el['trendline-import-preview'].textContent,'');assert.deepEqual(fields(),initial);preview('{bad');assert(el['trendline-import-preview'].textContent.startsWith('Fail \u2014'));assert.deepEqual(fields(),initial);
  preview(JSON.stringify(packet));assert.equal(calls.length,noImportRequests);assert.deepEqual([el['trendline-anchor-1-price'].value,el['trendline-anchor-2-price'].value],packet.anchors.map(a=>a.price));
  const settings=()=>JSON.stringify({anchors:fields(),risk:el['calc-risk'].value,sl:el['calc-sl-ticks'].value,rr:el['calc-rr'].value,limit:el['calc-limit'].value,expiry:el['trendline-expiry'].value,mode:el['trendline-trigger-mode'].value,direction:el['trendline-cross-direction'].value,basis:el['trendline-price-basis'].value,tolerance:el['trendline-tolerance-ticks'].value,extension:el['trendline-right-extension'].checked});
  const editing=settings();const remove=action('draft-a','delete');const failing=remove.click();remove.click();assert.equal(deleteCalls,1);assert(action('draft-a','delete'),'Keep card until confirmation');deleteReply(false);await failing;assert(action('draft-a','delete'));assert(el['trendline-plan-status'].textContent.includes('Delete failed'));assert.equal(settings(),editing);assert.equal(el['trendline-save'].textContent,'Update draft');
  holdList=true;const stale=el['trendline-refresh'].click();assert(releaseList);holdList=false;
  const success=action('draft-a','delete').click();assert.equal(deleteCalls,2);assert(action('draft-a','delete'));deleteReply(true);await success;assert(!action('draft-a','delete'));releaseList();await stale;assert(!action('draft-a','delete'),'Old list cannot restore deleted card');assert.equal(settings(),editing);assert.equal(el['trendline-save'].textContent,'Save draft');assert(el['trendline-plan-status'].textContent.includes('creates a new draft'));
  assert.equal(calls.filter(c=>c.url==='/api/trendline-plans'&&c.method==='POST').length,0,'No automatic replacement Save');
  await el['trendline-save'].click();const created=calls.find(c=>c.url==='/api/trendline-plans'&&c.method==='POST').body;assert.equal(created.expiry_at_ms,1893456000000);assert.deepEqual(created.anchors,packet.anchors);assert.equal(created.risk_value,'7.25');assert.equal(Number(created.stop_loss_ticks),80);assert.equal(created.rr_target,'4.5');assert(!calls.some(c=>c.method==='PATCH'),'Deleted ID must never be patched');
  assert(action('other-b','edit'));assert(action('protected-p','cancel'));await action('other-b','edit').click();const otherSettings=settings();const beforeOther=calls.length;const other=action('cancelled-c','delete').click();deleteReply(true);await other;assert(!action('cancelled-c','delete'));assert(action('other-b','edit'));assert.equal(settings(),otherSettings);assert.equal(el['trendline-save'].textContent,'Update draft');assert(!calls.slice(beforeOther).some(c=>c.method==='POST'||c.method==='PATCH'));
  const expiredControl=action('expired-e','delete'),beforeExpired=deleteCalls;const expiredPending=expiredControl.click();expiredControl.click();assert.equal(deleteCalls,beforeExpired+1);assert(action('expired-e','delete'),'Expired card remains until acknowledgement');assert.equal(settings(),otherSettings);assert(!action('expired-protected','delete'));deleteReply(true);await expiredPending;assert(!action('expired-e','delete'));await el['trendline-refresh'].click();assert(!action('expired-e','delete'),'Expired deletion survives refresh');assert(action('other-b','edit'));assert.equal(settings(),otherSettings);assert.equal(el['trendline-save'].textContent,'Update draft');assert(!action('expired-protected','delete'));
  assert(el['trendline-plan-list'].innerHTML.includes('Closed'));assert(el['trendline-plan-list'].innerHTML.includes('Simulated'));assert(el['trendline-plan-list'].innerHTML.includes('Submitted: close unverified'));
  await action('confirm_finished','delete').click();assert.equal(prompts,1);assert.equal(dismissCalls,0);assert(action('confirm_finished','delete'));
  confirmAnswer=true;const uncertain=action('confirm_finished','delete').click();assert.equal(prompts,2);assert.equal(dismissCalls,1);assert.equal(calls.at(-1).body.confirm_finished,true);dismissReply(false);await uncertain;assert(action('confirm_finished','delete'),'Storage rejection retains card');
  const unverified=action('confirm_finished','delete').click();action('confirm_finished','delete').click();assert.equal(dismissCalls,2);assert.equal(prompts,3);dismissReply(true,false);await unverified;assert(action('confirm_finished','delete'),'Mismatched acknowledgement retains card');await el['trendline-refresh'].click();assert(!action('confirm_finished','delete'),'Fresh read confirms persisted dismissal');
  holdList=true;const oldFinished=el['trendline-refresh'].click();holdList=false;const closedControl=action('closed','delete'),closedPending=closedControl.click();closedControl.click();assert.equal(dismissCalls,3);assert.equal(prompts,3);assert(action('closed','delete'),'Confirmed close retained until server ack');dismissReply(true);await closedPending;releaseList();await oldFinished;assert(!action('closed','delete'),'Stale list cannot restore dismissed card');assert(action('simulated','delete'));assert.equal(settings(),otherSettings);assert.equal(el['trendline-save'].textContent,'Update draft');
  const simulation=action('simulated','delete').click();dismissReply(true);await simulation;assert(!action('simulated','delete'));assert(action('other-b','edit'));assert.equal(settings(),otherSettings);
  assert(!calls.some(c=>c.url.endsWith('/arm')||c.url.endsWith('/cancel')||c.url.includes('/monitor/')&&c.method==='POST'||c.url.includes('/quote')||c.url.includes('/submit')||c.url.includes('webhook')));
  console.log('permanent delete UI and automatic preview passed');return;
 }
 if(process.argv[2]==='extension'){
  const now=Date.now();packet.anchors[0].timestamp_ms=now-3600000+123;packet.anchors[1].timestamp_ms=now+3600000+987;
  const baseline=calls.length;
  const preserved=()=>{assert.equal(el['calc-risk'].value,'2.5');assert.equal(el['calc-sl-ticks'].value,'50');assert.equal(el['calc-rr'].value,'3.25');assert.equal(el['calc-limit'].value,'99.25');assert.equal(el['trendline-trigger-mode'].value,'confirmed_cross');assert.equal(el['trendline-cross-direction'].value,'upward');assert.equal(el['trendline-price-basis'].value,'midpoint');assert.equal(el['trendline-tolerance-ticks'].value,'2');};
  packet.right_extension=true;paste('LTT_ANCHORS_V1 '+JSON.stringify(packet));assert.equal(el['trendline-right-extension'].checked,true);assert.equal(calls.length,baseline);preserved();
  assert.equal(el['trendline-anchor-1-time'].value,expectedLocal(packet.anchors[0].timestamp_ms));assert.equal(el['trendline-anchor-2-time'].value,expectedLocal(packet.anchors[1].timestamp_ms));
  await el['trendline-save'].click();assert.deepEqual(plans[0].anchors,packet.anchors);await edit();preserved();
  const beforeOff=calls.length;packet.right_extension=false;paste(JSON.stringify(packet));assert.equal(el['trendline-right-extension'].checked,false);assert.equal(calls.length,beforeOff);preserved();
  await el['trendline-save'].click();await edit();assert.equal(plans[0].right_extension,false);assert.deepEqual(plans[0].anchors,packet.anchors);preserved();
  delete packet.right_extension;paste(JSON.stringify(packet));assert.equal(el['trendline-right-extension'].checked,false);
  el['trendline-right-extension'].checked=true;paste(JSON.stringify(packet));assert.equal(el['trendline-right-extension'].checked,true);
  const intact=fields(),reads=calls.length;
  for(const invalid of ['true',0,1,null,[],{}]){paste(JSON.stringify({...packet,right_extension:invalid}));assert.equal(el['trendline-import-preview'].textContent,'Fail \u2014 invalid extension. Retry.');assert.deepEqual(fields(),intact);assert.equal(el['trendline-right-extension'].checked,true);assert.equal(calls.length,reads);}
  return;
 }
 if(process.argv[2]==='roundtrip'){
  const before=calls.length;
  const writes={};for(const id of ['trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-2-time','trendline-anchor-2-price']){
   let value=el[id].value;writes[id]=0;Object.defineProperty(el[id],'value',{get:()=>value,set:v=>{writes[id]++;value=v;}});
  }
  paste(process.argv[4] ? JSON.parse(process.argv[4])[0] : 'Earlier plain log text\n[2026-10-31 00:00:00] [Info] LTT_ANCHORS_V1 '+JSON.stringify(packet)+'\nLater plain log text');
  assert(Object.values(writes).every(n=>n===1),'Exactly one assignment to each anchor on a successful paste');
  assert.equal(el['trendline-import-preview'].textContent,'Success');
  assert(process.argv[4] ? el['trendline-import-text'].value===JSON.parse(process.argv[4])[0] : el['trendline-import-text'].value.includes('Later plain log text'));
  const first=fields();const nextPacket=process.argv[4] ? JSON.parse(JSON.parse(process.argv[4])[1]) : {...packet,anchors:packet.anchors.map(a=>({...a,timestamp_ms:a.timestamp_ms,price:a.price+'0'}))};Object.assign(packet,nextPacket);
  paste(process.argv[4] ? JSON.parse(process.argv[4])[1] : JSON.stringify(packet));assert.notDeepEqual(fields(),first);assert(Object.values(writes).every(n=>n===2),'Second paste replaces the prior import without clearing first');
  assert.equal(el['trendline-import-text'].value,process.argv[4] ? JSON.parse(process.argv[4])[1] : JSON.stringify(packet));assert.equal(el['trendline-import-preview'].textContent,'Success');assert(el['trendline-anchor-1-utc'].textContent.includes(new Date(packet.anchors[0].timestamp_ms).toISOString()));assert(Object.values(writes).every(n=>n===2));assert.equal(calls.length,before);
  assert.equal(el['trendline-anchor-1-time'].value,expectedLocal(packet.anchors[0].timestamp_ms));assert.equal(el['trendline-anchor-2-time'].value,expectedLocal(packet.anchors[1].timestamp_ms));
  if(process.env.TZ==='Australia/Brisbane')assert(el['trendline-anchor-2-time'].value.startsWith(process.argv[4] ? '2026-10-31T00:00:02.987' : '2026-10-31T00:00:01.987'));
  const preserved=()=>{assert.equal(el['calc-risk'].value,'2.5');assert.equal(el['calc-sl-ticks'].value,'50');assert.equal(el['calc-rr'].value,'3.25');assert.equal(el['calc-limit'].value,'99.25');assert.equal(el['trendline-trigger-mode'].value,'confirmed_cross');assert.equal(el['trendline-cross-direction'].value,'upward');assert.equal(el['trendline-price-basis'].value,'midpoint');assert.equal(el['trendline-tolerance-ticks'].value,'2');assert.equal(el['trendline-right-extension'].checked,false);};preserved();
  await el['trendline-save'].click();assert.deepEqual(plans[0].anchors,packet.anchors);const initial=copy(plans[0]);await edit();preserved();assert.equal(el['trendline-anchor-1-time'].value,expectedLocal(packet.anchors[0].timestamp_ms));assert.equal(el['trendline-import-text'].value,'');assert.equal(el['trendline-import-preview'].textContent,'');
  const editRequests=calls.length;packet.anchors=packet.anchors.map(a=>({...a,price:a.price+'1'}));
  paste('[Info] LTT_ANCHORS_V1 '+JSON.stringify(packet));assert.equal(calls.length,editRequests);preserved();
  assert.equal(el['trendline-save'].textContent,'Update draft');assert.equal(el['trendline-import-preview'].textContent,'Success');
  await el['trendline-save'].click();assert.deepEqual(plans[0].anchors,packet.anchors);const patch=calls.find(c=>c.method==='PATCH').body;for(const k of ['risk_value','stop_loss_ticks','rr_target','limit_entry_price','trigger_mode','cross_direction','trigger_price_basis','tolerance_ticks','right_extension'])assert.deepEqual(patch[k],initial[k]);assert.equal(plans[0].plan_id,'anchor-draft');
  await freshContext(JSON.parse(process.argv[3]));
 }else{
  paste(JSON.stringify(packet));await el['trendline-save'].click();await edit();const original=fields();const before=calls.length;
  const invalid=['', '{', JSON.stringify(packet)+JSON.stringify(packet), 'LTT_ANCHORS_V1 '+JSON.stringify(packet)+' LTT_ANCHORS_V1 '+JSON.stringify(packet), 'x'.repeat(8193), 'Before LTT_ANCHORS_V1 '+JSON.stringify(packet)+' trailing '+JSON.stringify(packet)];
  for(const mutate of [p=>delete p.schema,p=>p.schema='bad',p=>p.anchors.pop(),p=>p.anchors.push(p.anchors[0]),p=>p.anchors[1].timestamp_ms=p.anchors[0].timestamp_ms,p=>p.anchors.reverse(),p=>p.anchors[0].timestamp_ms=1.5,p=>p.anchors[0].timestamp_ms=8640000000000001,p=>p.anchors[0].timestamp_ms='123',p=>p.anchors[0].price='0',p=>p.anchors[0].price='NaN',p=>p.anchors[0].price=100,p=>p.symbol='BINANCE:BTCUSDT.P',p=>p.symbol='BYBIT:ETHUSDT.P',p=>p.timeframe='5']){const bad=copy(packet);mutate(bad);invalid.push(JSON.stringify(bad));}
  for(const text of invalid){paste(text);assert(text?el['trendline-import-preview'].textContent.startsWith('Fail \u2014'):el['trendline-import-preview'].textContent==='');assert.deepEqual(fields(),original);assert.equal(calls.length,before);}
  const future=copy(packet);future.anchors[1].timestamp_ms=Date.now()+3600000+987;future.right_extension=true;
  paste('LTT_ANCHORS_V1 '+JSON.stringify(future));assert.equal(el['trendline-right-extension'].checked,true);
  assert(el['trendline-anchor-2-utc'].textContent.includes(new Date(future.anchors[1].timestamp_ms).toISOString()));
  delete future.right_extension;paste(JSON.stringify(future));assert.equal(el['trendline-right-extension'].checked,true,'Absent extension preserves current setting');
  future.right_extension=false;paste(JSON.stringify(future));assert.equal(el['trendline-right-extension'].checked,false);assert.equal(calls.length,before);
  // Manual edits and reset clear pending and applied provenance.
  preview(JSON.stringify(packet));el['trendline-anchor-1-price'].value='102';el['trendline-anchor-1-price'].listeners.input();assert.equal(el['trendline-import-text'].value,'');assert.equal(el['trendline-import-preview'].textContent,'');
  preview(JSON.stringify(packet));el['account-toggle'].buttons.find(b=>b.dataset.v==='demo').click();assert.equal(el['trendline-import-preview'].textContent,'');
  preview(JSON.stringify(packet));el['timeframe-toggle'].buttons.find(b=>b.dataset.v==='5m').click();preview(JSON.stringify(packet));assert(el['trendline-import-preview'].textContent.includes('timeframe'));
  el['timeframe-toggle'].buttons.find(b=>b.dataset.v==='1m').click();preview(JSON.stringify(packet));el['trendline-reset'].click();assert.deepEqual(fields(),['','','','']);assert.equal(el['trendline-import-text'].value,'');assert.equal(el['trendline-import-preview'].textContent,'');
  assert.equal(calls.filter(c=>c.method==='POST'&&c.url==='/api/trendline-plans').length,1);assert(!calls.some(c=>c.method==='PATCH'));
 }
 assert(!calls.some(c=>c.url.includes('/arm')||c.url.includes('/monitor/')&&c.method==='POST'||c.url.includes('/submit')||c.url.includes('/quote')||c.url.includes('webhook')));
 console.log('anchor import '+process.argv[2]+' passed');
}

async function freshContext(record){
 const el=Object.fromEntries(ids.map(id=>[id,new Element(id)])),calls=[],dynamic=[];
 const groups={'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']};
 for(const [id,values] of Object.entries(groups))el[id].buttons=values.map(v=>new Button(v));
 el['calc-symbol'].value='WRONGUSDT';el['calc-risk'].value='999';el['calc-sl-ticks'].value='888';el['calc-rr'].value='9';el['calc-limit'].value='777';
 let delayed;
 const reply=data=>({ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
 const fetch=async(url,opts={})=>{
  const method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):null;calls.push({url,method,body});
  if(url==='/api/calculator/bootstrap')return reply({app_profile:'local',trendline_plans_available:true,trendline_monitoring:{running:false},webhook:{available:true}});
  if(url==='/api/trendline-plans'&&method==='GET')return reply({plans:[record]});
  if(url.startsWith('/api/calculator/instrument?'))return new Promise(resolve=>{delayed=()=>resolve(reply({asset:'crypto',broker:'bybit',symbol:'WRONGUSDT'}));});
  if(url===`/api/trendline-plans/${record.plan_id}`&&method==='PATCH'){
   const mapping={broker:'broker',account:'account',instrument:'instrument',side:'action',order_type:'order_intent',test_trade:'test_trade',limit_entry_price:'limit_entry_price',risk_mode:'risk_mode',risk_value:'risk_value',rr_target:'rr_target',timeframe:'timeframe',right_extension:'right_extension',trigger_mode:'trigger_mode',cross_direction:'cross_direction',trigger_price_basis:'trigger_price_basis',tolerance_ticks:'tolerance_ticks'};
   for(const [client,stored] of Object.entries(mapping))assert.deepEqual(body[client],record[stored],client);
   assert.deepEqual(body.anchors,record.anchors);assert.equal(Number(body.stop_loss_ticks),record.stop_loss_ticks);
   for(const [key,value] of Object.entries(record.order_metadata))assert.deepEqual(body[key],value,key);
   assert.equal(body.asset,'fx');assert(!Object.hasOwn(body,'expiry_at_ms'),'Keep absolute expiry via existing update API');
   return reply({plan:record});
  }
  throw Error('Unexpected fresh context request '+url);
 };
 vm.runInNewContext(source,{document:{getElementById:id=>el[id]||dynamic.find(node=>node.id===id),createElement:tag=>{const node=new Element('');dynamic.push(node);return node;}},fetch,confirm:()=>{throw Error('No confirmation');},navigator:{clipboard:{writeText:async()=>{}}},setTimeout:fn=>{fn();return 1;},clearTimeout(){},setInterval:()=>1,clearInterval(){},console,Date,Intl,URL,URLSearchParams,AbortController});
 await flush();
 const saved=el['trendline-plan-list'].innerHTML;
 for(const text of ['Saved settings (server record)','EUR_USD','sell','15m','fixed_aud','17.25 AUD','91 / 3.5 R','downward','bid','UTC '+new Date(record.anchors[0].timestamp_ms).toISOString(),'Browser local','1.087654','Test mode: Yes','Saved &lt;swing&gt;','Not recorded'])assert(saved.includes(text),'Missing saved detail '+text);
 assert(!saved.includes('999'));assert(!saved.includes('WRONGUSDT'));assert(!saved.includes('Saved <swing>'));
 el['calc-symbol'].listeners.input();assert(delayed);const before=calls.length;
 await el['trendline-plan-list'].actions.find(b=>b.dataset.tlAction==='edit').click();
 assert.equal(calls.length,before,'Edit must not request quote/submit/resolve');delayed();await flush();
 assert.equal(el['calc-symbol'].value,record.instrument);assert(el['calc-canonical-symbol'].textContent.includes(record.instrument));
 for(const [id,value] of Object.entries({'calc-risk':'17.25','calc-sl-ticks':91,'calc-rr':'3.5','calc-limit':'1.087654'}))assert.equal(el[id].value,value,id);
 assert(el['calc-risk-label'].textContent.includes('AUD'));assert.equal(el['trendline-expiry'].value,'keep');
 assert.equal(el['trendline-anchor-1-price'].value,record.anchors[0].price);assert.equal(el['trendline-anchor-2-price'].value,record.anchors[1].price);
 assert(el['trendline-anchor-1-utc'].textContent.includes(new Date(record.anchors[0].timestamp_ms).toISOString()));
 for(const [id,value] of Object.entries({'account-toggle':'demo','asset-toggle':'fx','broker-toggle':'oanda','side-toggle':'sell','order-toggle':'limit','test-toggle':'yes','risk-toggle':'fixed_aud'}))assert(el[id].buttons.some(b=>b.dataset.v===value),id);
 assert(el['setup-toggle'].innerHTML.includes('Saved &lt;swing&gt;'));assert(el['ema-toggle'].innerHTML.includes('No (saved)'));
 await el['trendline-save'].click();assert.equal(calls.filter(c=>c.method==='PATCH').length,1);assert.equal(calls.slice(before).filter(c=>c.method==='POST').length,0,'Edit/Update must not add POSTs beyond existing startup prewarm');
 assert(el['trendline-plan-list'].innerHTML.includes(new Date(record.expiry_at_ms).toISOString()));
 assert(!calls.some(c=>c.url.includes('/arm')||c.url.includes('/monitor/')&&c.method==='POST'||c.url.includes('/quote')||c.url.includes('/submit')||c.url.includes('webhook')));
}
run().catch(e=>{console.error(e);process.exitCode=1;});

'''
    result = subprocess.run([node, '-e', "eval(require('fs').readFileSync(0,'utf8'))", str(JS_PATH), mode, json.dumps(saved_record), json.dumps(table_packets) if table_packets else '', json.dumps(image_options) if image_options else ''], input=harness, capture_output=True, text=True, timeout=30, env={**os.environ, 'TZ': timezone})
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    if image_options:
        print(result.stdout)

def test_tradingview_anchor_import_preserves_exact_draft_roundtrip(tmp_path):
    import runpy
    import sys
    sys.path.insert(0, str(ROOT))
    from render.trendline_plans import TrendlinePlanStore
    payload = runpy.run_path(str(ROOT / 'tests/test_trendline_plans.py'))['_saved_anchor_repair_payload']()
    path = tmp_path / 'server-returned-draft.json'
    created = TrendlinePlanStore(path).create(payload, now_ms=1793368802000)
    record = TrendlinePlanStore(path).get(created['plan_id'])
    for timezone in ('UTC', 'Australia/Brisbane'):
        _run_anchor_import_behavior('roundtrip', timezone, record)

def test_tradingview_anchor_pine_logs_packet_roundtrip(tmp_path):
    import re
    import runpy
    import sys
    sys.path.insert(0, str(ROOT))
    from render.trendline_plans import TrendlinePlanStore
    pine = (ROOT / 'pinescripts/ltt_anchor_export.pine').read_text(encoding='utf-8')
    expression = next(line.strip()[len('string packet = '):] for line in pine.splitlines() if line.strip().startswith('string packet = '))
    # Evaluate only the original bounded string-concatenation expression, not Pine/runtime behavior.
    def packet_text(t1, p1, t2, p2):
        values = {'syminfo.tickerid': 'BYBIT:BTCUSDT.P', 'timeframe.period': '1',
                  'str.tostring(time1, "0")': str(t1), 'str.tostring(time2, "0")': str(t2),
                  'str.tostring(price1, "0.################")': p1, 'str.tostring(price2, "0.################")': p2,
                  '(extendRight ? "true" : "false")': 'false'}
        parts = re.split(r' \+ (?=(?:[^\']*\'[^\']*\')*[^\']*$)', expression)
        packet = ''.join(part[1:-1] if part.startswith("'") and part.endswith("'") else values[part] for part in parts)
        return packet.replace(',"', ',\n"').replace('},{', '},\n{')
    first = packet_text(1793368797123, '100.00012345', 1793368801987, '101.00098765')
    moved = packet_text(1793368797123, '100.00012345', 1793368802987, '102.00098765001')
    assert json.loads(first)['anchors'][1] == {'timestamp_ms': 1793368801987, 'price': '101.00098765'}
    assert json.loads(moved)['anchors'][1] == {'timestamp_ms': 1793368802987, 'price': '102.00098765001'}
    assert not any(token in pine for token in ['table.', 'label.', 'plot(', 'Move tables to bottom', 'copyPacket', 'string coordinates'])
    assert 'line.new(time1, price1, time2, price2,' in pine
    assert 'if packet != emitted' in pine and 'LTT_ANCHORS_V1 " + packet)' in pine
    assert 'log.warning(reason)' in pine and 'line.delete(boundary)' in pine
    payload = runpy.run_path(str(ROOT / 'tests/test_trendline_plans.py'))['_saved_anchor_repair_payload']()
    store = TrendlinePlanStore(tmp_path / 'table-draft.json')
    record = store.create(payload, now_ms=1793368802000)
    _run_anchor_import_behavior('roundtrip', 'Australia/Brisbane', record, [first, moved])


def test_tradingview_anchor_import_rejects_invalid_and_stale_packets():
    _run_anchor_import_behavior('invalid', 'Australia/Brisbane')


def test_tradingview_future_anchor_extension_import_roundtrip():
    _run_anchor_import_behavior('extension', 'Australia/Brisbane')


def test_trendline_delete_controls_and_automatic_anchor_preview():
    _run_anchor_import_behavior('deletion', 'Australia/Brisbane')


def test_pepperstone_account_selector_behavior() -> None:
    node = r"C:\Program Files\nodejs\node.exe"
    assert node, "node is required for JS behavior test"
    harness = r'''
const fs=require('fs'),vm=require('vm');const source=fs.readFileSync(process.argv[1],'utf8');
class B{constructor(v){this.dataset={v};this.listeners={};this.disabled=false;this.attrs={};this.classList={toggle(){},add(){},remove(){}};}addEventListener(e,f){this.listeners[e]=f;}click(){if(!this.disabled&&this.listeners.click)return this.listeners.click({target:this,currentTarget:this});}setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k];}removeAttribute(k){delete this.attrs[k];}}
class E extends B{constructor(id){super('');this.id=id;this.value='';this.textContent='';this.style={};this._html='';this.buttons=[];this.checked=false;this.dataset={};}set innerHTML(v){this._html=String(v||'');this.buttons=[...this._html.matchAll(/data-v="([^"]*)"/g)].map(m=>new B(m[1]));}get innerHTML(){return this._html;}querySelectorAll(s){return s==='button'?this.buttons:[];}appendChild(child){(this.children||=[]).push(child);if(child.id)el[child.id]=child;return child;}}
const ids=['pepperstone-account-type-wrap','pepperstone-account-type-toggle','calc-error','calc-error-debug','calc-success','calc-results','calc-request-summary','calc-canonical-symbol','calc-journal-summary','calc-instrument-specs','risk-toggle-wrap','calc-pepperstone-risk-preflight','calc-webhook-panel','calc-webhook-url','calc-webhook-json','calc-webhook-copy','calc-webhook-copy-url','risk-toggle','calc-risk-label','limit-wrap','account-toggle','asset-toggle','side-toggle','order-toggle','webhook-toggle','test-toggle','timeframe-toggle','setup-toggle','pattern-toggle','ema-toggle','vwap-toggle','aths-atls-toggle','round-number-toggle','calc-symbol','calc-limit','calc-sl-ticks','calc-rr','calc-tp-ticks','calc-target-mode','calc-chart-tick-auto','calc-chart-tick-source','calc-chart-tick-size-override-enabled','calc-chart-tick-size','rr-wrap','tp-ticks-wrap','chart-tick-size-wrap','calc-risk','calc-quote','calc-submit','calc-quote-status','calc-webhook-status','calc-pepperstone-set','broker-toggle-wrap','broker-toggle','trendline-plans-panel','trendline-anchor-1-time','trendline-anchor-1-price','trendline-anchor-1-utc','trendline-anchor-2-time','trendline-anchor-2-price','trendline-anchor-2-utc','trendline-trigger-mode','trendline-cross-direction','trendline-price-basis','trendline-tolerance-ticks','trendline-right-extension','trendline-expiry','trendline-save','trendline-reset','trendline-refresh','trendline-plan-status','trendline-plan-list','trendline-monitor-start','trendline-monitor-stop','trendline-monitor-status'];
const el=Object.fromEntries(ids.map(id=>[id,new E(id)]));for(const [id,vals] of Object.entries({'pepperstone-account-type-toggle':['razor','standard'],'risk-toggle':['fixed_aud','percent'],'asset-toggle':['crypto','fx'],'broker-toggle':['oanda','pepperstone'],'account-toggle':['live','demo'],'side-toggle':['buy','sell'],'order-toggle':['market','limit'],'webhook-toggle':['no','yes'],'test-toggle':['no','yes']}))el[id].buttons=vals.map(v=>new B(v));
el['calc-symbol'].value='GBPUSD';el['calc-sl-ticks'].value='43';el['calc-tp-ticks'].value='25';el['calc-rr'].value='2';el['calc-risk'].value='10';el['calc-target-mode'].value='rr';
let delayed=null;const checks={};const calls=[];let lotMode='ready';const reply=data=>({ok:true,status:200,statusText:'OK',headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)});
const fetch=async(url,opts={})=>{const body=opts.body?JSON.parse(opts.body):null;calls.push({url,body});if(url==='/api/calculator/bootstrap')return reply({app_profile:'local',trendline_plans_available:false,webhook:{available:true}});if(url.startsWith('/api/calculator/prewarm-account'))return reply({ready_for_quote:true,asset:'fx',account:'demo'});if(url.startsWith('/api/calculator/instrument?')){const broker=new URL('http://local'+url).searchParams.get('broker')||'oanda';return reply({asset:'fx',broker,symbol:'GBP_USD',chart_tick_size_default:'0.00001',chart_tick_size_source:'Pepperstone GBPUSD TradingView chart (user screenshot measurement)',chart_tick_profile_id:'pepperstone-gbpusd-tradingview-screenshot-v1'});}if(url==='/api/calculator/quote'){if(delayed)await new Promise(r=>delayed=r);const ticks=body.target_mode==='ticks';return reply({pepperstone_lot_estimate:body.pepperstone_account_type==='standard'?{state:lotMode,reason:lotMode==='no_fit'?'No MT5 lot fits the exported AUD risk band.':lotMode==='pending'?'Missing MT5 volume step; Trader checks pending.':'MT5 lot-grid estimate; Trader validates live execution.',lots:'0.67',nominal_loss_aud:'10.05',buffered_loss_aud:'11.99',buffer_points:2,risk_min_aud:'10',risk_target_aud:'10',risk_max_aud:'12',volume_min:'0.01',volume_step:'0.01',volume_max:'100',source:'Pepperstone Trader spread export',mt5_symbol:'GBPUSD',account_mode:'demo',quote_age_seconds:1}:undefined,pepperstone_account_type:body.pepperstone_account_type,estimated_commission_aud:body.pepperstone_account_type==='standard'?'0':'1.2',estimated_spread_aud:'0.4',asset:'fx',broker:'pepperstone',venue:'Pepperstone',resolved_venue:'Pepperstone',symbol:'GBP_USD',tick_size:'0.00001',chart_tick_size:body.chart_tick_size||'0.00001',chart_tick_size_source:'Pepperstone GBPUSD TradingView chart (user screenshot measurement)',chart_tick_profile_id:'pepperstone-gbpusd-tradingview-screenshot-v1',price_display_tick_size:'0.00001',entry_price:'1.30020',stop_price:ticks?'1.30063':'1.29977',target_price:ticks?'1.30045':'1.30106',target_distance:ticks?'0.00025':'0.00086',quantity:body.pepperstone_account_type==='standard'?'67000':'2300',estimated_total_loss_aud:body.pepperstone_account_type==='standard'?'10.05':'10',estimated_total_loss:'10',position_size_estimate_note:'OANDA price/conversion estimate; Trader validates executable MT5 lots, margin, commission, and volume.',calculation_context_id:'ctx-'+calls.length,pepperstone_distance_conversion:{chart_tick_size:body.chart_tick_size||'0.00001',chart_tick_size_source:'Pepperstone GBPUSD TradingView chart (user screenshot measurement)',stop_loss_ticks:Number(body.stop_loss_ticks),stop_loss_price_distance:'0.00043',target_mode:body.target_mode,take_profit_ticks:body.take_profit_ticks?Number(body.take_profit_ticks):null,take_profit_price_distance:ticks?'0.00025':null},pepperstone_mt5_export_status:{ready:lotMode!=='no_fit',file_ready:lotMode!=='no_fit',broker_check_state:lotMode==='no_fit'?'incompatible':'pending',code:'PEPPERSTONE_MT5_POINT_METADATA_UNAVAILABLE',reason:'Broker point and risk-buffer checks are pending in Trader.'},pepperstone_risk_buffer_preflight:{state:'pending',compatible:null,explanation:'Broker point and risk-buffer checks are pending in Trader.'}});}if(url.startsWith('/api/calculator/journal-summary')||url.startsWith('/api/instrument-specs'))return reply({status:'no_data'});return reply({status:'no_data'});};
let timer=0;const timers=new Map();const setTimeout=(fn,ms)=>{const id=++timer;if(ms===250)fn();else timers.set(id,fn);return id;};const clearTimeout=id=>timers.delete(id);const context={document:{getElementById:id=>el[id],createElement:()=>new E('')},fetch,navigator:{clipboard:{writeText:async()=>{}}},setTimeout,clearTimeout,setInterval:()=>99,clearInterval(){},console,Date,URL,URLSearchParams,AbortController};vm.runInNewContext(source,context);const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{await flush();await flush();await el['asset-toggle'].buttons.find(b=>b.dataset.v==='fx').click();await flush();await flush();await el['broker-toggle'].buttons.find(b=>b.dataset.v==='pepperstone').click();await flush();await flush();await el['account-toggle'].buttons.find(b=>b.dataset.v==='demo').click();await flush();await flush();await el['risk-toggle'].buttons.find(b=>b.dataset.v==='fixed_aud').click();
await el['calc-quote'].listeners.click();await flush();await flush();const netPayload=calls.filter(c=>c.url==='/api/calculator/quote')[0].body;
el['calc-target-mode'].value='ticks';el['calc-target-mode'].listeners.change();const visibleAfterModeChange=el['calc-pepperstone-set'].style.display!== 'none';el['calc-tp-ticks'].listeners.input();el['calc-rr'].value='99';el['calc-rr'].listeners.input();
await el['calc-quote'].listeners.click();await flush();await flush();const manualPayload=calls.filter(c=>c.url==='/api/calculator/quote')[1].body;const visibleBefore=el['calc-pepperstone-set'].style.display!== 'none';const enabledBefore=!el['calc-pepperstone-set'].disabled;const quoteStatus=el['calc-quote-status'].textContent;const preflightMessage=el['calc-pepperstone-risk-preflight'].textContent;const resultHtml=el['calc-results'].innerHTML;
const choose=v=>el['pepperstone-account-type-toggle'].buttons.find(b=>b.dataset.v===v).click();
if(netPayload.pepperstone_account_type!=='razor')throw Error('default');
choose('standard');if(!el['calc-pepperstone-set'].disabled||el['calc-pepperstone-set'].style.display!=='none')throw Error('stale export');
await el['calc-quote'].listeners.click();await flush();checks.standardPayload=calls.filter(c=>c.url==='/api/calculator/quote').at(-1).body;checks.standardHtml=el['calc-results'].innerHTML;
lotMode='no_fit';await el['calc-quote'].listeners.click();await flush();checks.noFitHtml=el['calc-results'].innerHTML;checks.noFitBlocked=el['calc-pepperstone-set'].disabled;lotMode='pending';await el['calc-quote'].listeners.click();await flush();checks.pendingHtml=el['calc-results'].innerHTML;checks.pendingPortable=!el['calc-pepperstone-set'].disabled;lotMode='ready';
el['broker-toggle'].buttons.find(b=>b.dataset.v==='oanda').click();await flush();if(el['pepperstone-account-type-wrap'].style.display!=='none')throw Error('conditional');el['broker-toggle'].buttons.find(b=>b.dataset.v==='pepperstone').click();await flush();
await el['calc-quote'].listeners.click();checks.retained=calls.filter(c=>c.url==='/api/calculator/quote').at(-1).body.pepperstone_account_type;
delayed=true;const pending=el['calc-quote'].listeners.click();await flush();choose('razor');delayed();await pending;await flush();checks.raceBlocked=el['calc-pepperstone-set'].disabled&&el['calc-pepperstone-set'].style.display==='none';
el['calc-sl-ticks'].value='44';el['calc-sl-ticks'].listeners.input();const visibleAfter=el['calc-pepperstone-set'].style.display!== 'none';
console.log(JSON.stringify({checks,netPayload,manualPayload,visibleAfterModeChange,visibleBefore,enabledBefore,visibleAfter,quoteStatus,preflightMessage,resultHtml,autoStep:el['calc-chart-tick-auto'].value,autoSource:el['calc-chart-tick-source'].textContent,exportCalls:calls.filter(c=>c.url==='/api/calculator/pepperstone-set').length,submitCalls:calls.filter(c=>c.url==='/api/calculator/submit').length}));})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(JS_PATH)], check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["netPayload"]["target_mode"] == "rr"
    assert data["netPayload"]["risk_reward"] == "2"
    assert "take_profit_ticks" not in data["netPayload"]
    assert data["manualPayload"]["target_mode"] == "ticks"
    assert data["manualPayload"]["take_profit_ticks"] == "25"
    assert "chart_tick_size" not in data["manualPayload"]
    assert "risk_reward" not in data["manualPayload"]
    assert data["visibleAfterModeChange"] is False
    assert data["visibleBefore"] is True and data["enabledBefore"] is True and data["visibleAfter"] is False
    assert "2300" in data["resultHtml"] and "0.00043 price" in data["resultHtml"] and "0.00025 price" in data["resultHtml"]
    assert data["autoStep"] == "0.00001" and "Pepperstone GBPUSD TradingView chart" in data["autoSource"]
    assert "broker point count pending in Trader" in data["resultHtml"]
    assert data["quoteStatus"] == "GBP/USD \u00b7 Razor \u00b7 Preset ready"
    assert data["preflightMessage"] == ""
    assert data["exportCalls"] == 0 and data["submitCalls"] == 0


    assert data["checks"]["standardPayload"]["pepperstone_account_type"] == "standard"
    assert data["checks"]["retained"] == "standard"
    assert data["checks"]["raceBlocked"] is True
    assert "standard" in data["checks"]["standardHtml"] and "Estimated round-trip commission" in data["checks"]["standardHtml"]


    assert "Estimated MT5 lots" in data["checks"]["standardHtml"]
    assert "10 / 10 / 12" in data["checks"]["standardHtml"]
    assert "0.67" in data["checks"]["standardHtml"] and "10.05" in data["checks"]["standardHtml"]
    assert "Modeled buffered loss" in data["checks"]["standardHtml"] and "11.99" in data["checks"]["standardHtml"]
    assert "Pepperstone Trader spread export" in data["checks"]["standardHtml"]
    assert "No fitting lot" in data["checks"]["noFitHtml"] and data["checks"]["noFitBlocked"]
    assert "Advisory; MT5 lots pending" in data["checks"]["pendingHtml"] and data["checks"]["pendingPortable"]

def test_missed_touch_latest_evaluation_card_is_escaped_and_refreshable():
    node=shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    harness=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');const source=fs.readFileSync(process.argv[1],'utf8');
const a=source.indexOf('  function trendlineEvaluationDetails(plan) {'),b=source.indexOf('  function renderTrendlinePlans()',a);assert(a>=0&&b>a);
const ctx=vm.createContext({escapeHtml:s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')});vm.runInContext(source.slice(a,b),ctx);
assert.equal(ctx.trendlineEvaluationDetails({}), '');
const plan={latest_evaluation:{revision:2,stage:'candidate_quote',reason:'quote_future',inputs:{now_ms:100,timestamp_ms:101,bid:'1.15',ask:'1.1501',tick_size:'0.0001'}}};
let html=ctx.trendlineEvaluationDetails(plan);assert(html.includes('candidate_quote / quote_future'));assert(html.includes('timestamp_ms'));assert(html.includes('1.1501'));
plan.latest_evaluation={revision:2,stage:'final_evaluation',reason:'touch_not_reached',inputs:{timestamp_ms:102},evaluation:{reason:'touch_not_reached',projected_price:'1.16',classification:'below'}};
html=ctx.trendlineEvaluationDetails(plan);assert(html.includes('final_evaluation'));assert(html.includes('1.16'));
plan.latest_evaluation.reason='<img onerror=evil()>';html=ctx.trendlineEvaluationDetails(plan);assert(!html.includes('<img'));assert(html.includes('&lt;img'));
assert(source.includes('${trendlineEvaluationDetails(plan)}'));assert(source.includes('state.trendlinePlans=response.plans||[];'));console.log('ok');
"""
    result=subprocess.run([node,"-e",harness,str(JS_PATH)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=="ok"


def test_fresh_xrp_timing_diagnostic_wraps_and_shows_pending_phase():
    node=shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
    code=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert'),src=fs.readFileSync(process.argv[1],'utf8');
const a=src.indexOf('  function trendlineEvaluationDetails(plan) {'),b=src.indexOf('  function renderTrendlinePlans()',a);
const ctx=vm.createContext({escapeHtml:s=>String(s).replace(/</g,'&lt;').replace(/>/g,'&gt;')});vm.runInContext(src.slice(a,b),ctx);
const html=ctx.trendlineEvaluationDetails({latest_evaluation:{revision:2,stage:'candidate_quote',reason:'quote_future',pending_stage:'candidate_quote',inputs:{now_ms:1000,timestamp_ms:1797,comparison_now_ms:2145,clock_offset_ms:797}}});
assert(html.includes('overflow-wrap:anywhere'));assert(html.includes('white-space:normal'));assert(html.includes('quote_future'));assert(html.includes('checking candidate_quote'));assert(html.includes('comparison_now_ms'));
assert(!ctx.trendlineEvaluationDetails({latest_evaluation:{stage:'<img>',reason:'<script>',inputs:{}}}).includes('<img>'));console.log('ok');
"""
    p=subprocess.run([node,'-e',code,str(JS_PATH)],capture_output=True,text=True,timeout=15)
    assert p.returncode==0,p.stderr
    assert p.stdout.strip()=='ok'



def test_armed_risk_editor_preserves_drafts_and_acknowledgement():
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    harness = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');const src=fs.readFileSync(process.argv[1],'utf8');
let focused=null;
class E{constructor(){this.children=[];this.dataset={};this.listeners={};this.disabled=false;this.value='';this.textContent='';}appendChild(n){n.parent=this;this.children.push(n);return n;}remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}setAttribute(k,v){this[k]=v;}addEventListener(k,f){this.listeners[k]=f;}click(){if(!this.disabled)return this.listeners.click?.();}focus(){focused=this;}querySelectorAll(s){let all=this.children.flatMap(n=>[n,...n.querySelectorAll(s)]);return all.filter(n=>s==='[data-tl-plan]'?n.dataset.tlPlan!==undefined:s==='[data-tl-content]'?n.content:s==='[data-tl-action]'?n.dataset.tlAction!==undefined:false);}querySelector(s){return this.querySelectorAll(s)[0];}set innerHTML(s){this.html=s;this.children=[];if(this.isList){for(const m of s.matchAll(/<div class="card" data-tl-plan="([^"]*)"><div data-tl-content>([\s\S]*?)(?=<div class="card" data-tl-plan=|$)/g)){let card=this.appendChild(new E());card.dataset.tlPlan=m[1];let content=card.appendChild(new E());content.content=true;content.innerHTML=m[2];}}else{for(const m of s.matchAll(/data-tl-action="([^"]*)" data-tl-id="([^"]*)"/g)){let b=this.appendChild(new E());b.dataset={tlAction:m[1],tlId:m[2]};}}}get innerHTML(){return this.html;}}
const list=new E();list.isList=true;const state={trendlinePlansAvailable:true,trendlineListRequestSeq:0,trendlineMutationPending:false,trendlineMonitorPending:false,trendlineRiskEdit:null,trendlineEditId:'existing-draft'};
let plan={plan_id:'one',revision:2,monitor_generation:'g1',status:'armed',monitoring_eligible:true,broker:'bybit',account:'live',test_trade:true,instrument:'XRPUSDT',action:'buy',stop_loss_ticks:10,rr_target:'2',anchors:[],order_metadata:{}};const draft={...plan,plan_id:'draft',status:'draft'};state.trendlinePlans=[plan,draft];let server=plan,allow=true,notify=0,edited=null,patches=0,reads=0,handler=null,message='',prompts=[];
const clone=x=>JSON.parse(JSON.stringify(x));const ctx={state,document:{createElement:()=>new E()},$:id=>id==='trendline-plan-list'?list:null,escapeHtml:String,localDateTime:String,trendlineStatus:s=>message=s,notifyOpenOrdersStateChanged:()=>notify++,renderTrendlineMonitor(){},editTrendlinePlan:p=>edited=p.plan_id,confirm:s=>{prompts.push(s);return allow;},Date,Intl,BigInt,console,request:async(url,opts={})=>{if(opts.method==='PATCH'){patches++;assert.equal(url,'/api/trendline-plans/one/risk');return handler(JSON.parse(opts.body));}assert.equal(url,'/api/trendline-plans');reads++;return {plans:[clone(server),draft]};}};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('  function savedPlanDetails('),src.indexOf('  async function saveTrendlinePlan('))+src.slice(src.indexOf('  async function trendlineAction('),src.indexOf('  function initializeTrendlinePlans(')),ctx);
const render=()=>ctx.renderTrendlinePlans(),open=()=>ctx.trendlineAction('risk-edit','one'),edit=()=>state.trendlineRiskEdit;
const accept=body=>{assert.equal(body.confirm_live_execution,true);assert.equal(body.monitor_generation,'g1');server={...server,...body,revision:body.revision+1,risk_update:{from_revision:body.revision,saved_revision:body.revision+1}};return {ok:true,plan:clone(server)};};
(async()=>{render();await ctx.trendlineAction('edit','draft');assert.equal(edited,'draft');await open();assert.equal(edit().stop.value,'10');assert.equal(edit().rr.value,'2');const mounted=edit().stop,card=list.children[0];edit().stop.value='25';edit().rr.value='3.25';await ctx.refreshTrendlinePlans();assert.strictEqual(edit().stop,mounted);assert.strictEqual(list.children[0],card);assert.strictEqual(focused,mounted);edit().cancel.click();assert.equal(edit(),null);assert.equal(patches,0);assert.equal(state.trendlineEditId,'existing-draft');
await open();edit().stop.value='25';allow=false;await edit().save.click();assert.equal(patches,0);assert.match(prompts.at(-1),/Live.*actual broker/);allow=true;let release;handler=body=>new Promise(r=>release=()=>r(accept(body)));const saving=edit().save.click();await edit().save.click();assert.equal(patches,1);assert.equal(state.trendlinePlans[0].stop_loss_ticks,10);assert(edit().cancel.disabled);release();await saving;assert.equal(edit(),null);assert.equal(state.trendlinePlans[0].stop_loss_ticks,'25');assert.equal(notify,1);
await open();const attempted=edit();attempted.rr.value='bad';handler=()=>{throw Error('Invalid target');};const before=reads;await attempted.save.click();assert.equal(reads,before+1);assert.strictEqual(edit(),attempted);assert.equal(edit().rr.value,'bad');assert.match(edit().status.textContent,/Invalid target/);assert(!edit().save.disabled);
edit().rr.value='4';handler=body=>{accept(body);server.status='submitted';throw Error('Ambiguous response');};await edit().save.click();assert.equal(edit(),null);assert.equal(state.trendlinePlans[0].status,'submitted');assert.match(message,/saved.*submitted/);
server={...server,status:'armed',monitoring_eligible:true};state.trendlinePlans=[clone(server),draft];render();await open();edit().rr.value='5';handler=()=>{server={...server,status:'claimed',revision:server.revision+1};throw Error('Claim won');};await edit().save.click();assert(edit().save.disabled);assert.equal(edit().rr.value,'5');assert.match(edit().status.textContent,/no longer monitoring/);edit().cancel.click();assert.equal(state.trendlineEditId,'existing-draft');
server={...server,status:'armed',revision:server.revision+1};state.trendlinePlans=[clone(server),draft];render();await open();let delayed;const normal=ctx.request;ctx.request=(url,opts={})=>opts.method==='PATCH'?normal(url,opts):new Promise(r=>delayed=r);const stale=ctx.refreshTrendlinePlans();edit().rr.value='6';handler=accept;await edit().save.click();delayed({plans:[plan,draft]});await stale;assert.equal(state.trendlinePlans[0].rr_target,'6');assert.equal(state.trendlineEditId,'existing-draft');console.log('editor contracts passed');})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', harness, str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'editor contracts passed' in result.stdout


def test_calculator_streamlined_native_disclosures_and_payload(tmp_path):
    """Real isolated Edge layout/interaction; every broker/API response is synthetic."""
    import ast
    import base64
    import json
    import os
    import time
    import urllib.request
    import websocket
    service_source = (ROOT / 'render/master_service.py').read_text(encoding='utf-8-sig')
    tree = ast.parse(service_source)
    template = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                    and any(isinstance(t, ast.Name) and t.id == 'CALCULATOR_TEMPLATE' for t in n.targets)))
    assert 'bybit-expiry-status' not in template and 'Local trendline monitoring' not in template
    assert 'trendline-import-apply' not in template and 'placeholder="Paste JSON"' in template
    mock = r"""
window.__CALCULATOR_TESTING__=true;
window.fixtureCalls=[];window.fixturePlans=[];
window.historyBTC={worker:{healthy:true},schedules:[{symbol:'BTCUSDT',status_label:'Broker-confirmed cancellation',deadline_ms:1793368802000}]};
window.fetch=async(url,opts={})=>{
 const method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):{};fixtureCalls.push({url,method,body});let data={};
 if(url.includes('/bootstrap'))data={app_profile:'local',trendline_plans_available:true,trendline_monitoring:{running:false},webhook:{available:true}};
 else if(url.includes('/instrument?'))data={symbol:'BTCUSDT',asset:'crypto',broker:'bybit',tick_size:'0.1'};
 else if(url.includes('/order-expiry/status'))data=historyBTC;
 else if(url==='/api/calculator/quote')data={asset:'crypto',broker:'bybit',symbol:'BTCUSDT',entry_price:'100',stop_price:'95',target_price:'110',quantity:'2',tick_size:'0.1',estimated_total_loss:'10',estimated_reward:'20',calculation_context_id:'ctx-native-'+ 'W'.repeat(120),quote_created_at_ms:Date.now()};
 else if(url==='/api/calculator/submit')data={ok:true,broker:'bybit',result:{order:{orderId:'fixture-only'},order_expiry:{status_label:'Cancellation scheduled',deadline_ms:Date.now()+300000}}};
 else if(url==='/api/trendline-plans'&&method==='GET')data={plans:fixturePlans};
 else if(url==='/api/trendline-plans'&&method==='POST'){
  const plan={...body,plan_id:'native-draft-'+fixturePlans.length,status:'draft',can_delete:true,revision:1,order_intent:body.order_type,action:body.side,order_metadata:Object.fromEntries(['setup','pattern','ema','vwap','aths_atls','round_number'].map(k=>[k,body[k]||null]))};fixturePlans.push(plan);data={plan};
 }else if(url.startsWith('/api/trendline-plans/')&&method==='PATCH'){
  const id=url.split('/')[3];fixturePlans=fixturePlans.map(p=>p.plan_id===id?{...p,...body}:p);data={plan:fixturePlans.find(p=>p.plan_id===id)};
 }else if(url.startsWith('/api/trendline-plans/')&&method==='POST'){
  const id=url.split('/')[3],action=url.split('/')[4];fixturePlans=fixturePlans.map(p=>p.plan_id===id?{...p,status:action==='arm'?'armed':'cancelled',monitoring_eligible:action==='arm'}:p);data={plan:fixturePlans.find(p=>p.plan_id===id)};
 }else if(url.startsWith('/api/trendline-plans/')&&method==='DELETE'){
  const id=url.split('/')[3];fixturePlans=fixturePlans.filter(p=>p.plan_id!==id);data={ok:true,deleted_plan_id:id};
 }
 return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data),json:async()=>data};
};
"""
    html = template.replace('<script src="{{CALCULATOR_JS_URL}}"></script>',
                            '<script>' + mock + '</script><script src="' + JS_PATH.as_uri() + '"></script>')
    page = tmp_path / 'calculator.html'
    page.write_text(html, encoding='utf-8')
    profile = tmp_path / 'edge-profile'
    profile.mkdir()
    binary = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    assert Path(binary).is_file(), 'Recorded Edge required; no install/download'
    process = None
    ws = None
    with (tmp_path / 'edge.log').open('wb') as log:
        process = subprocess.Popen([binary, '--headless=new', '--disable-gpu', '--no-first-run',
                                    '--no-default-browser-check', '--disable-background-networking',
                                    '--disable-component-update', '--disable-sync', '--remote-debugging-port=0',
                                    '--user-data-dir=' + str(profile), 'about:blank'],
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 20
            active = profile / 'DevToolsActivePort'
            while not active.is_file():
                assert process.poll() is None, 'Private Edge exited before DevTools startup'
                assert time.monotonic() < deadline, 'Private Edge startup deadline'
                time.sleep(.05)
            port = int(active.read_text().splitlines()[0])
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as response:
                target = next(t for t in json.load(response) if t['type'] == 'page')
            ws = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=10, suppress_origin=True)
            sequence = 0
            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                ws.send(json.dumps(dict(id=sequence, method=method, params=params or {})))
                while True:
                    data = json.loads(ws.recv())
                    if data.get('id') == sequence:
                        assert 'error' not in data, data
                        return data.get('result', {})
            def evaluate(script):
                data = call('Runtime.evaluate', dict(expression=script, awaitPromise=True, returnByValue=True))
                assert 'exceptionDetails' not in data, data
                return data.get('result', {}).get('value')
            call('Page.navigate', dict(url=page.as_uri()))
            deadline = time.monotonic() + 15
            while not evaluate("!!window.__expiryTestHooks && !!document.querySelector('#trendline-save')"):
                assert time.monotonic() < deadline, 'Private page initialization deadline'
                time.sleep(.05)
            result = evaluate(r"""(async()=>{
 const $=id=>document.getElementById(id),wait=()=>new Promise(r=>setTimeout(r,80));
 const check=(v,m)=>{if(!v)throw Error(m)};
 const paste=text=>$('trendline-import-text').dispatchEvent(new ClipboardEvent('paste',{clipboardData:new DataTransfer(),bubbles:true,cancelable:true}));
 const textPaste=text=>{const data=new DataTransfer();data.setData('text/plain',text);$('trendline-import-text').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));};
 const packet={schema:'ltt-anchors/v1',symbol:'BYBIT:BTCUSDT.P',timeframe:'1',right_extension:true,anchors:[{timestamp_ms:1793368797123,price:'100.00012345'},{timestamp_ms:1793368801987,price:'101.00098765'}]};
 $('calc-symbol').value='BTCUSDT';$('calc-symbol').dispatchEvent(new Event('input'));await new Promise(r=>setTimeout(r,650));
 document.querySelector('#account-toggle [data-v="demo"]').click();document.querySelector('#timeframe-toggle [data-v="1m"]').click();document.querySelector('#order-toggle [data-v="limit"]').click();$('calc-limit').value='100';
 check(!$('calc-results-disclosure').open,'Quote initially collapsed');
 const count=fixtureCalls.length;textPaste('[2026-10-31T00:00:01] [Info] LTT Anchor Export 2A.3 | LTT_ANCHORS_V1 '+JSON.stringify(packet));
 check($('trendline-import-preview').textContent==='Success','Full Pine Logs paste');
 check($('trendline-anchor-1-price').value===packet.anchors[0].price,'Exact price');
 check($('trendline-anchor-1-time').dataset.anchorEpoch==='1793368797123','Exact milliseconds');check(fixtureCalls.length===count,'Paste sends zero requests');
 const prior=[$('trendline-anchor-1-time').value,$('trendline-anchor-1-price').value,$('trendline-anchor-2-time').value,$('trendline-anchor-2-price').value,$('trendline-right-extension').checked];
 textPaste(JSON.stringify({...packet,timeframe:'5',right_extension:false}));check($('trendline-import-preview').textContent==='Fail \u2014 wrong timeframe. Retry.','Short failure');
 check(JSON.stringify(prior)===JSON.stringify([$('trendline-anchor-1-time').value,$('trendline-anchor-1-price').value,$('trendline-anchor-2-time').value,$('trendline-anchor-2-price').value,$('trendline-right-extension').checked]),'Failure atomic');
 $('trendline-save').click();await wait();check(fixturePlans.length===1,'Save draft');
 let d=document.querySelector('[data-tl-settings]');check(d&&!d.open,'Saved settings collapsed');
 for(const action of ['arm','edit','cancel','delete'])check(document.querySelector('[data-tl-action="'+action+'"]')?.getClientRects().length,'Visible '+action);
 d.querySelector('summary').click();await wait();$('trendline-refresh').click();await wait();check(document.querySelector('[data-tl-settings]').open,'Refresh preserves open');
 document.querySelector('[data-tl-settings] summary').click();await wait();$('trendline-refresh').click();await wait();check(!document.querySelector('[data-tl-settings]').open,'Refresh preserves closed');
 document.querySelector('[data-tl-action="edit"]').click();textPaste(JSON.stringify(packet));document.querySelector('[data-tl-settings] summary').click();await wait();$('trendline-save').click();await wait();check(!document.querySelector('[data-tl-settings]').open,'Update collapses settings');
 document.querySelector('[data-tl-action="arm"]').click();await wait();check(fixturePlans[0].status==='armed','Arm fixture');document.querySelector('[data-tl-action="cancel"]').click();await wait();check(fixturePlans[0].status==='cancelled','Cancel fixture');
 document.querySelector('[data-tl-action="delete"]').click();await wait();check(fixturePlans.length===0,'Delete fixture');
 $('calc-quote').click();await wait();check(!$('calc-results-disclosure').open,'New Calculate collapsed');
 check($('calc-results').innerHTML.includes('Estimated'),'Complete quote table');$('calc-results-disclosure').querySelector('summary').click();await wait();check($('calc-results').getClientRects().length,'Quote opens');
 const expanded=$('calc-results-disclosure').getBoundingClientRect().height;$('calc-results-disclosure').querySelector('summary').click();await wait();check($('calc-results-disclosure').getBoundingClientRect().height<expanded,'Collapse frees space');
 $('calc-results-disclosure').querySelector('summary').click();$('calc-quote').click();await wait();check(!$('calc-results-disclosure').open,'Subsequent Calculate collapses again');
 check($('calc-submit').getClientRects().length,'Submit outside disclosure');
 $('bybit-expiry-mode').value='duration';$('bybit-expiry-value').value='5';$('bybit-expiry-unit').value='minutes';$('bybit-expiry-mode').dispatchEvent(new Event('change'));
 $('calc-quote').click();await wait();$('calc-submit').click();await wait();
 check($('calc-success').textContent.includes('Cancellation scheduled')&&$('calc-success').textContent.includes('Do not resubmit'),'Current expiry acknowledgement visible');check(!$('calc-error').textContent,'Accepted submission has no DOM-removal error');
 check([...document.querySelectorAll('.payload-pair')].some(p=>p.querySelector('dt').textContent==='side'&&p.querySelector('dd').textContent==='buy'),'Actual submitted side');
 check([...document.querySelectorAll('.payload-pair')].some(p=>p.querySelector('dt').textContent==='webhook'&&p.querySelector('dd').textContent==='no'),'Current webhook choice');
 check(!fixtureCalls.some(c=>c.url.includes('/order-expiry/status')),'Historical BTC status not requested');check(!document.body.innerText.includes('Broker-confirmed cancellation'),'No historical expiry output');
 check(fixtureCalls.filter(c=>c.url==='/api/calculator/submit').length===1,'One simulated submit');$('calc-submit').click();await wait();check(fixtureCalls.filter(c=>c.url==='/api/calculator/submit').length===1,'Duplicate guard');
 return {requests:fixtureCalls.length,success:$('calc-success').textContent,submitted:fixtureCalls.find(c=>c.url==='/api/calculator/submit').body};
})()""")
            widths = {}
            for width in (1440, 390):
                call('Emulation.setDeviceMetricsOverride', dict(width=width,height=1100,deviceScaleFactor=1,mobile=False))
                widths[width] = evaluate("(()=>{const e=document.querySelector('.payload-grid'),r=e.getBoundingClientRect();return {columns:getComputedStyle(e).gridTemplateColumns.split(' ').length,width:r.width,page:document.documentElement.scrollWidth,viewport:innerWidth,pairs:e.children.length,content:e.scrollWidth,box:e.clientWidth}})()")
                assert widths[width]['page'] <= width, widths[width]
                assert widths[width]['pairs'] >= 20
                assert widths[width]['content'] <= widths[width]['box'], widths[width]
                evaluate("document.getElementById('calc-request-summary').scrollIntoView()")
                png = call('Page.captureScreenshot', dict(format='png'))['data']
                (tmp_path / f'payload-{width}.png').write_bytes(base64.b64decode(png))
            assert widths[1440]['columns'] >= 3 and widths[390]['columns'] == 1, widths
            print('NATIVE_EDGE_FIXTURE', json.dumps(dict(result=result, widths=widths)))
        finally:
            if ws is not None:
                try:
                    ws.send(json.dumps(dict(id=999999, method='Browser.close')))
                finally:
                    ws.close()
            if process is not None:
                exit_code = process.wait(timeout=15)
                print('PRIVATE_EDGE_ACTUAL_EXIT', exit_code)
                assert exit_code == 0
