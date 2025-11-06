//+------------------------------------------------------------------+
//|   Position Size Calculator EA                                    |
//|   Displays the calculator in a browser window                    |
//+------------------------------------------------------------------+
#property copyright "2024"
#property version   "1.00"
#property strict

#include <Arrays/ArrayString.mqh>

#import "shell32.dll"
int ShellExecuteW(int hwnd,string lpOperation,string lpFile,string lpParameters,string lpDirectory,int nShowCmd);
#import
#import "user32.dll"
int MessageBoxW(int hWnd,string text,string caption,int type);
#import

CArrayString symbols;
input bool  ShowPopup      = true;

bool pageOpened=false;

int OnInit()
  {
   GetWatchlistSymbols(symbols);
   if(symbols.Total()==0)
     {
      Print("No symbols in Market Watch.");
      return(INIT_FAILED);
     }
   if(ShowPopup)
      ShowPopup();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
  }

int GetWatchlistSymbols(CArrayString &list)
  {
   int total=SymbolsTotal(true);
   for(int i=0;i<total;i++)
      list.Add(SymbolName(i,true));
   return total;
  }

string BuildPositionSizeHtml()
  {
   int total=symbols.Total();
   string opts="";
   for(int i=0;i<total;i++)
      opts+=StringFormat("<option value='%s'>%s</option>",symbols.At(i),symbols.At(i));

   double accBal=AccountInfoDouble(ACCOUNT_BALANCE);
   double freeMargin=AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   string symInfo="var symbolInfo={";
   for(int i=0;i<total;i++)
     {
      string s=symbols.At(i);
      double price=SymbolInfoDouble(s,SYMBOL_ASK);
      int digits=(int)SymbolInfoInteger(s,SYMBOL_DIGITS);
      double pt=SymbolInfoDouble(s,SYMBOL_POINT);
      double tv=SymbolInfoDouble(s,SYMBOL_TRADE_TICK_VALUE);
      double ts=SymbolInfoDouble(s,SYMBOL_TRADE_TICK_SIZE);
      double cs=SymbolInfoDouble(s,SYMBOL_TRADE_CONTRACT_SIZE);
      symInfo+=StringFormat("'%s':{p:%f,d:%d,pt:%f,tv:%f,ts:%f,cs:%f},",s,price,digits,pt,tv,ts,cs);
     }
   if(StringLen(symInfo)>13)
      StringSetCharacter(symInfo,StringLen(symInfo)-1,'}');
   else
      symInfo+="}";
   symInfo+=";";

   string html="<html><head><meta charset='UTF-8'>";
   // No auto-refresh
   html+="<style>";
   html+="body{font-family:monospace;background:black;color:white;margin:0;}";
   html+="div.wrapper{display:flex;gap:20px;flex-wrap:wrap;}";
   html+="div.table-container{overflow-x:auto;}";
   html+="table{border-collapse:collapse;}";
   html+="th,td{border:1px solid white;padding:4px;text-align:right;color:white;}";
   html+="th:first-child{text-align:left;}";
   html+="button.ps-action{display:block;margin-top:4px;width:100%;}";
   html+="</style></head><body><div class='wrapper'>";

   html+="<div class='table-container'><table id='ps_result'><tr><th colspan='2'>Last Calculation</th></tr></table>";
   html+="<button id='ps_download' class='ps-action' onclick='downloadFiles()' style='display:none;'>Download Files</button>";
   html+="</div>";

   html+="<div class='table-container'><table id='ps_form'>";
   html+="<tr><th colspan='2'>Position Size Calc</th></tr>";
   html+="<tr><td>Symbol</td><td><select id='ps_symbol'>"+opts+"</select></td></tr>";
   html+="<tr><td>Risk Mode</td><td><select id='risk_mode' onchange='updateVis()'><option value='pct'>Risk %</option><option value='aud'>Fixed AUD</option></select></td></tr>";
   html+="<tr id='tr_fixed_risk'><td>Fixed Risk AUD</td><td><input id='fixed_risk' type='number' value='100'/></td></tr>";
   html+="<tr id='tr_risk_pct'><td>Risk %</td><td><input id='risk_pct' type='number' value='1'/></td></tr>";
   html+="<tr><td>SL Unit</td><td><select id='sl_unit'><option value='pips'>Pips</option><option value='points'>Points</option></select></td></tr>";
   html+="<tr><td>SL Value</td><td><input id='sl_value' type='number' value='20'/></td></tr>";
   // Broker is fixed to Pepperstone
   html+="<tr><td>RR Ratio</td><td><input id='rr_ratio' type='number' value='2'/></td></tr>";
   html+="<tr><td>Side</td><td><select id='order_side'><option value='buy'>Buy</option><option value='sell'>Sell</option></select></td></tr>";
   html+="<tr id='tr_commission'><td>Commission/Lot</td><td><input id='commission' type='number' value='7'/></td></tr>";
   html+="<tr><td>Min Net Profit</td><td><input id='min_net' type='number' value='20'/></td></tr>";
   html+="<tr><td colspan='2' style='text-align:center;'><button onclick='calcPosition()'>Calculate</button></td></tr>";
   html+="</table></div>";

   html+="</div><script>"+symInfo+
   "function el(id){return document.getElementById(id);}"+
   "var accBal="+DoubleToString(accBal,2)+";var freeMarg="+DoubleToString(freeMargin,2)+";"+
   "function saveInputs(){var o={symbol:el('ps_symbol').value,risk_mode:el('risk_mode').value,fixed_risk:el('fixed_risk').value,risk_pct:el('risk_pct').value,sl_unit:el('sl_unit').value,sl_value:el('sl_value').value,rr_ratio:el('rr_ratio').value,order_side:el('order_side').value,commission:el('commission').value,min_net:el('min_net').value};localStorage.setItem('ps_inputs',JSON.stringify(o));}"+
   "function loadInputs(){var j=localStorage.getItem('ps_inputs');if(!j)return;try{var o=JSON.parse(j);for(var k in o){var e=el(k);if(e)e.value=o[k];}}catch(e){} }"+
   "function getLev(sym){sym=sym.toUpperCase();if(sym.includes('USD')&&(sym.includes('EUR')||sym.includes('GBP')||sym.includes('AUD')||sym.includes('NZD')||sym.includes('CAD')||sym.includes('CHF')||sym.includes('JPY')))return 30;if(sym.length==6||sym.length==7)return 20;if(sym.includes('XAU')||sym.includes('US500')||sym.includes('NAS')||sym.includes('UK')||sym.includes('GER'))return 20;if(sym.includes('XAG')||sym.includes('WTI')||sym.includes('BRENT'))return 10;if(sym.includes('BTC')||sym.includes('ETH')||sym.includes('LTC')||sym.includes('XRP'))return 2;return 5;}"+
   "function dl(name,text){var b=new Blob([text]);var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=name;a.click();URL.revokeObjectURL(a.href);}"+
   "var lastTxt='';"+
   "function updateVis(){var m=el('risk_mode').value;el('tr_fixed_risk').style.display=(m=='aud')?'table-row':'none';el('tr_risk_pct').style.display=(m=='pct')?'table-row':'none';}"+
   "function downloadFiles(){var t=localStorage.getItem('ps_last_txt');if(!t)return;var ts=new Date().toISOString().replace(/[:T]/g,'-').split('.')[0];dl('PositionSizeOutput-'+ts+'.txt',t);}"+
   "function calcPosition(){var s=el('ps_symbol').value;var inf=symbolInfo[s];var price=inf.p;var digits=inf.d;"+
   "var pipSize=Math.pow(10,-digits+1);var pipVal=inf.tv*pipSize/inf.ts;"+
   "var slUnit=el('sl_unit').value;var slVal=parseFloat(el('sl_value').value);"+
   "var slPips=(slUnit=='pips')?slVal:slVal*inf.pt/pipSize;var bro='pepper';"+
   "var volStep=0.01;var comm=parseFloat(el('commission').value);"+
   "var bal=accBal;"+
   "var riskMode=el('risk_mode').value;var riskAmt=(riskMode=='aud')?parseFloat(el('fixed_risk').value):bal*parseFloat(el('risk_pct').value)/100;"+
   "if(riskAmt<=0)return;var lotRaw=riskAmt/(slPips*pipVal+comm);"+
   "var lot=Math.ceil(lotRaw/volStep)*volStep;var lotPrec=Math.round(Math.log10(1/volStep));lot=parseFloat(lot.toFixed(lotPrec));"+
   "var commiss=lot*comm;var rr=parseFloat(el('rr_ratio').value);var tpP=slPips*rr;var netReward=tpP*pipVal*lot-commiss;"+
   "var minNet=parseFloat(el('min_net').value);var reqProfit=Math.max(riskAmt*rr,minNet);"+
   "while(netReward<reqProfit){tpP+=0.5;netReward=tpP*pipVal*lot-commiss;}"+
   "var side=el('order_side').value;var buy=side=='buy';"+
   "var slPrice=buy?price-slPips*pipSize:price+slPips*pipSize;var tpPrice=buy?price+tpP*pipSize:price-tpP*pipSize;"+
   "var netRisk=slPips*pipVal*lot+commiss;var slDisp=slVal+' '+(slUnit=='pips'?'pips':'points');"+
   "var tpVal=(slUnit=='pips')?tpP:tpP*(pipSize/inf.pt);var tpDisp=tpVal.toFixed(1)+' '+(slUnit=='pips'?'pips':'points');"+
   "var isFx=s.length>=6&&s.length<=7&&!/[0-9]/.test(s);"+
   "var out='=== Position Size Calculation ===\\n';out+='Symbol: '+s+'\\n';out+='Trade Side: '+(buy?'Buy':'Sell')+'\\n';"+
   "out+='Account Balance: AUD'+bal.toFixed(2)+'\\n';out+='Risk Amount: AUD'+riskAmt.toFixed(2)+'\\n';"+
   "out+='Lot Size: '+lot.toFixed(lotPrec)+'\\n';out+='Commission: AUD'+commiss.toFixed(2)+'\\n';"+
   "out+='Net Risk: AUD'+netRisk.toFixed(2)+'\\n';"+
   "out+='Stop Loss: '+slDisp+'\\n';"+
   "out+='Take Profit: '+tpDisp+' (RR=1:'+rr.toFixed(2)+')\\n';"+
   "out+='Expected Net Profit at TP: AUD'+netReward.toFixed(2)+'\\n';out+='Minimum Net Profit Target: AUD'+minNet.toFixed(2)+'\\n';"+
   "var lev=getLev(s);var contract=inf.cs;var notion=lot*contract*price;var margin=notion/lev;"+
   "out+='Margin Needed: '+margin.toFixed(2)+'\\n';"+
   "lastTxt=out;"+
   "localStorage.setItem('ps_last_txt',lastTxt);el('ps_download').style.display='block';"+
   "var r='<tr><th colspan=\"2\">Last Calculation</th></tr>';"+
   "r+='<tr><td>Lot Size</td><td>'+lot.toFixed(lotPrec)+'</td></tr>';"+
   "r+='<tr><td>Stop Loss</td><td>'+slDisp+'</td></tr>';"+
   "r+='<tr><td>Take Profit</td><td>'+tpDisp+'</td></tr>';"+
   "r+='<tr><td>Margin</td><td>'+margin.toFixed(2)+'</td></tr>';"+
   "r+='<tr><td>Net Risk</td><td>'+netRisk.toFixed(2)+'</td></tr>';"+
   "r+='<tr><td>Commission</td><td>'+commiss.toFixed(2)+'</td></tr>';"+
   "r+='<tr><td>Net Profit</td><td>'+netReward.toFixed(2)+'</td></tr>';"+
   "document.getElementById('ps_result').innerHTML=r;localStorage.setItem('ps_result',r);saveInputs();updateVis();}"+
   "window.onload=function(){loadInputs();updateVis();var r=localStorage.getItem('ps_result');if(r)el('ps_result').innerHTML=r;if(localStorage.getItem('ps_last_txt'))el('ps_download').style.display='block';var ins=document.querySelectorAll('#ps_form input,#ps_form select');for(var i=0;i<ins.length;i++){ins[i].addEventListener('input',function(){saveInputs();updateVis();});ins[i].addEventListener('change',function(){saveInputs();updateVis();});}window.addEventListener('beforeunload',saveInputs);};"+
   "</script></body></html>";
   return html;
  }

void ShowPopup()
  {
   string html=BuildPositionSizeHtml();
   string file="PositionSize.html";
   int h=FileOpen(file,FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h>=0){FileWriteString(h,html);FileClose(h);} 
   if(!pageOpened)
     {
      string base=TerminalInfoString(TERMINAL_DATA_PATH)+"\\MQL5\\Files\\";
      string full=base+file;
      int res=ShellExecuteW(0,"open","msedge.exe","\""+full+"\"",NULL,1);
      if(res>32)
         pageOpened=true;
      else
        MessageBoxW(0,"HTML saved at "+full,"Position Size",0);
     }
  }
