//+------------------------------------------------------------------+
//|                                                    ScannerEA.mq5 |
//|   Shows a live correlation matrix in a scrollable HTML table       |
//+------------------------------------------------------------------+
#property copyright "2024"
#property version   "1.00"
#property strict
#property script_show_inputs

#include <Arrays/ArrayString.mqh>

#import "shell32.dll"
int ShellExecuteW(int hwnd,string lpOperation,string lpFile,string lpParameters,string lpDirectory,int nShowCmd);
#import
#import "user32.dll"
int MessageBoxW(int hWnd,string text,string caption,int type);
#import

// use built in alignment constants like ALIGN_LEFT and ALIGN_CENTER

// basic color definitions
#define clrDarkSlateGray C'47,79,79'
#define clrGray          C'128,128,128'
#define clrLime          C'0,255,0'
#define clrTomato        C'255,99,71'
#define clrSilver        C'192,192,192'

// Removed dialog window and label grid from chart display
CArrayString symbols;                // list of symbols to display
input int   RefreshSeconds = 60;     // update interval
input bool  ShowPopupWindow = true;  // show popup window

// Restrict selectable timeframes to commonly used periods
enum CorrelPeriod
  {
   TF_M5   = PERIOD_M5,
   TF_M15  = PERIOD_M15,
   TF_M30  = PERIOD_M30,
   TF_H1   = PERIOD_H1,
   TF_H4   = PERIOD_H4,
   TF_D1   = PERIOD_D1,
   TF_W1   = PERIOD_W1,
   TF_MN1  = PERIOD_MN1
  };

// names and values for available timeframes
string TFNames[] = {"M5","M15","M30","H1","H4","D1","W1","MN1"};
int    TFValues[] = {PERIOD_M5,PERIOD_M15,PERIOD_M30,PERIOD_H1,PERIOD_H4,PERIOD_D1,PERIOD_W1,PERIOD_MN1};

// locate index of a timeframe value in the TFValues array
int FindTFIndex(int tf)
  {
   for(int i=0;i<ArraySize(TFValues);i++)
      if(TFValues[i]==tf)
         return i;
   return 0;
  }


// width of each cell in the ASCII table
#define CELL_WIDTH 8

int rows, cols;                      // matrix dimensions
datetime nextUpdate = 0;    // time for the next matrix update
bool     pageOpened = false;         // track if browser already opened

//+------------------------------------------------------------------+
int OnInit()
  {
   GetWatchlistSymbols(symbols);
   rows = symbols.Total();
   cols = rows;
   if(rows==0)
     {
      Print("No symbols in Market Watch.");
      return(INIT_FAILED);
     }

   // old chart-based dialog removed
   nextUpdate = TimeCurrent()+RefreshSeconds;
   if(ShowPopupWindow)
      ShowPopup();
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // nothing to clean up since dialog is removed
  }

//+------------------------------------------------------------------+
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
  {
   // no dialog events to handle
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(TimeCurrent()>=nextUpdate)
     {
      if(ShowPopupWindow)
         ShowPopup();
      nextUpdate = TimeCurrent()+RefreshSeconds;
     }
  }


//+------------------------------------------------------------------+
int GetWatchlistSymbols(CArrayString &list)
  {
   int total=SymbolsTotal(true);
   for(int i=0;i<total;i++)
      list.Add(SymbolName(i,true));
   return total;
  }

//+------------------------------------------------------------------+
double CalculateCorrelation(const string a,const string b,ENUM_TIMEFRAMES tf)
  {
   datetime end=TimeCurrent();
   const int bars=30;
   datetime start=end-PeriodSeconds(tf)*bars;
   MqlRates ra[], rb[];
   int na=CopyRates(a,tf,start,end,ra);
   int nb=CopyRates(b,tf,start,end,rb);
   int n=MathMin(na,nb);
   if(n<2) return 0.0;
   int use=MathMin(n,bars);
   double sx=0,sy=0,sx2=0,sy2=0,sxy=0;
   for(int i=0;i<use;i++)
     {
      double x=ra[i].close;
      double y=rb[i].close;
      sx+=x; sy+=y; sx2+=x*x; sy2+=y*y; sxy+=x*y;
     }
   double num=use*sxy-sx*sy;
  double den=MathSqrt((use*sx2-sx*sx)*(use*sy2-sy*sy));
  return(den!=0.0)?num/den:0.0;
 }

//+------------------------------------------------------------------+
//| Repeat a substring multiple times                                 |
//+------------------------------------------------------------------+
string Repeat(string s,int n)
  {
   string r="";
   for(int i=0;i<n;i++)
      r+=s;
   return r;
  }

//+------------------------------------------------------------------+
//| Pad string with spaces                                           |
//+------------------------------------------------------------------+
string Pad(string s,int width)
  {
   while(StringLen(s)<width)
      s+=" ";
   return s;
  }

// Pad string on the left with spaces
string PadLeft(string s,int width)
  {
   while(StringLen(s)<width)
      s=" "+s;
   return s;
  }

//+------------------------------------------------------------------+
//| Build a simple ASCII table of correlations                         |
//+------------------------------------------------------------------+
string HorizontalLine()
  {
   string line="+";
   line+=Repeat("-",CELL_WIDTH)+"+";
   for(int c=0;c<cols;c++)
      line+=Repeat("-",CELL_WIDTH)+"+";
   return line;
  }

string BuildMatrixText(ENUM_TIMEFRAMES tf)
  {
   string txt=HorizontalLine()+"\n";
   txt+="|"+Pad("",CELL_WIDTH)+"|";
   for(int c=0;c<cols;c++)
      txt+=Pad(symbols.At(c),CELL_WIDTH)+"|";
   txt+="\n"+HorizontalLine()+"\n";
   for(int r=0;r<rows;r++)
     {
      txt+="|"+Pad(symbols.At(r),CELL_WIDTH)+"|";
      for(int c=0;c<cols;c++)
        txt+=PadLeft(StringFormat("%0.2f",(r==c)?1.0:CalculateCorrelation(symbols.At(r),symbols.At(c),tf)),CELL_WIDTH)+"|";
      txt+="\n"+HorizontalLine()+"\n";
     }
   return txt;
  }

//+------------------------------------------------------------------+
//| Build a correlation table for a single timeframe                  |
//+------------------------------------------------------------------+
string BuildMatrixTable(ENUM_TIMEFRAMES tf)
  {
   string html="<table><thead>";
   html+=StringFormat("<tr class='tf-row'><th colspan='%d'>",cols+1);
   for(int i=0;i<ArraySize(TFValues);i++)
     {
      html+=StringFormat("<button onclick=\"showTF('%s')\">%s</button>",TFNames[i],TFNames[i]);
      if(i<ArraySize(TFValues)-1) html+="&nbsp;";
     }
   html+="</th></tr>";
   html+="<tr class='head-row'><th></th>";
   for(int c=0;c<cols;c++)
      html+=StringFormat("<th>%s</th>",symbols.At(c));
   html+="</tr></thead><tbody>";
   for(int r=0;r<rows;r++)
     {
      html+=StringFormat("<tr><th class='sym'>%s</th>",symbols.At(r));
      for(int c=0;c<cols;c++)
        {
         double val=(r==c)?1.0:CalculateCorrelation(symbols.At(r),symbols.At(c),tf);
         string col=(val>=0)?"green":"red";
         html+=StringFormat("<td style='color:%s'>%0.2f</td>",col,val);
        }
      html+="</tr>";
     }
   html+="</tbody></table>";
   return html;
  }

//+------------------------------------------------------------------+
//| Build the HTML page with all timeframe tables                     |
//+------------------------------------------------------------------+
string BuildMatrixHtml(int defaultIndex)
  {
   string html="<html><head><meta charset='UTF-8'>";
   html+=StringFormat("<meta http-equiv='refresh' content='%d'>",RefreshSeconds);
   html+="<style>";
   html+="body{font-family:monospace;background:black;color:white;margin:0;}";
  // Removed extra top scroll bar to free up space
  html+="div.table-container{overflow:auto;height:100vh;}";
  html+="table{border-collapse:collapse;}";
  html+="th,td{border:1px solid white;padding:4px;text-align:right;color:white;}";
  html+="th:first-child{text-align:left;}";
  html+="button.ps-action{display:block;margin-top:4px;width:100%;}";
  html+="th:first-child,td:first-child{position:sticky;left:0;background:black;z-index:2;}";
  // Shift timeframe and header rows up since there is no top scroll bar
  html+="tr.tf-row th{position:sticky;top:0;text-align:left;background:black;z-index:2;}";
  html+="tr.head-row th{position:sticky;top:2em;background:black;z-index:1;}";
   html+="</style>";
   html+="<script>function showTF(tf){var tfs=['";
   for(int i=0;i<ArraySize(TFNames);i++)
     {
      if(i>0) html+="','";
      html+=TFNames[i];
     }
   html+="'];for(var i=0;i<tfs.length;i++){var e=document.getElementById('tf_'+tfs[i]);if(e) e.style.display=(tfs[i]==tf)?'block':'none';}location.hash=tf;}window.onload=function(){var h=location.hash.substring(1);if(h=='')h='"+TFNames[defaultIndex]+"';showTF(h);};</script>";
   html+="</head><body><div id='table-container' class='table-container'>";
   for(int i=0;i<ArraySize(TFValues);i++)
     {
      html+=StringFormat("<div id='tf_%s' style='display:none;'>",TFNames[i]);
      html+=BuildMatrixTable((ENUM_TIMEFRAMES)TFValues[i]);
      html+="</div>";
     }
   html+="</div></body></html>";
   return html;
  }

//+------------------------------------------------------------------+
//| Build HTML table for spread percentages                           |
//+------------------------------------------------------------------+
string BuildSpreadHtml()
  {
   int total=symbols.Total();
   string syms[];
   double spreads[];
   ArrayResize(syms,total);
   ArrayResize(spreads,total);
   for(int i=0;i<total;i++)
     {
      syms[i]=symbols.At(i);
      double bid,ask,point;
      if(!SymbolInfoDouble(syms[i],SYMBOL_BID,bid) ||
         !SymbolInfoDouble(syms[i],SYMBOL_ASK,ask) ||
         !SymbolInfoDouble(syms[i],SYMBOL_POINT,point))
        {
         spreads[i]=0.0;
         continue;
        }
      double raw=ask-bid;
      double sprd=(raw>0)?raw:point;
      spreads[i]=(sprd/bid)*100.0;
     }

   for(int i=0;i<total-1;i++)
      for(int j=0;j<total-1-i;j++)
         if(spreads[j]>spreads[j+1])
           {
            double td=spreads[j]; spreads[j]=spreads[j+1]; spreads[j+1]=td;
            string ts=syms[j];    syms[j]=syms[j+1];    syms[j+1]=ts;
           }

   string html="<html><head><meta charset='UTF-8'>";
   html+=StringFormat("<meta http-equiv='refresh' content='%d'>",RefreshSeconds);
   html+="<style>";
   html+="body{font-family:monospace;background:black;color:white;}";
   html+="div.table-container{overflow-x:auto;}";
   html+="table{border-collapse:collapse;}";
   html+="th,td{border:1px solid white;padding:4px;text-align:right;color:white;}";
   html+="th:first-child{text-align:left;}";
   html+="</style></head><body><div class='table-container'><table>";
   html+="<tr><th>Symbol</th><th>Spread %</th></tr>";
   for(int i=0;i<total;i++)
     html+=StringFormat("<tr><td>%s</td><td>%0.10f</td></tr>",syms[i],spreads[i]);
   html+="</table></div></body></html>";
   return html;
  }

//+------------------------------------------------------------------+
//| Build HTML table for swap information                             |
//+------------------------------------------------------------------+
string BuildSwapHtml()
  {
   int total=symbols.Total();
   string html="<html><head><meta charset='UTF-8'>";
   html+=StringFormat("<meta http-equiv='refresh' content='%d'>",RefreshSeconds);
   html+="<style>";
   html+="body{font-family:monospace;background:black;color:white;}";
   html+="div.table-container{overflow-x:auto;}";
   html+="table{border-collapse:collapse;}";
   html+="th,td{border:1px solid white;padding:4px;text-align:right;color:white;}";
   html+="th:first-child{text-align:left;}";
   html+="</style></head><body><div class='table-container'><table>";
   html+="<tr><th>Symbol</th><th>Swap Long</th><th>Swap Short</th></tr>";
   for(int i=0;i<total;i++)
     {
      string sym=symbols.At(i);
      double swapLong,swapShort;
      if(!SymbolInfoDouble(sym,SYMBOL_SWAP_LONG,swapLong) ||
         !SymbolInfoDouble(sym,SYMBOL_SWAP_SHORT,swapShort))
        { swapLong=0.0; swapShort=0.0; }

      string colLong  = (swapLong<0)?"red":"green";
      string colShort = (swapShort<0)?"red":"green";
      html+=StringFormat("<tr><td>%s</td><td style='color:%s'>%0.2f</td>"
                        "<td style='color:%s'>%0.2f</td></tr>",
                        sym,colLong,swapLong,colShort,swapShort);
    }
   html+="</table></div></body></html>";
  return html;
 }

//+------------------------------------------------------------------+
//| Build a combined HTML page showing spread and swap tables side by |
//| side. This keeps the existing table styles but places them in a   |
//| single page using a flex container so they appear next to each    |
//| other.                                                            |
//+------------------------------------------------------------------+
string BuildSpreadSwapHtml()
  {
   int total = symbols.Total();
   string syms[];
   double spreads[];
   ArrayResize(syms,total);
   ArrayResize(spreads,total);

   // Collect spread information
   for(int i=0;i<total;i++)
     {
      syms[i]=symbols.At(i);
      double bid,ask,point;
      if(!SymbolInfoDouble(syms[i],SYMBOL_BID,bid) ||
         !SymbolInfoDouble(syms[i],SYMBOL_ASK,ask) ||
         !SymbolInfoDouble(syms[i],SYMBOL_POINT,point))
        {
         spreads[i]=0.0;
         continue;
        }
      double raw=ask-bid;
      double sprd=(raw>0)?raw:point;
      spreads[i]=(sprd/bid)*100.0;
     }

   // Sort spreads ascending to match previous behaviour
   for(int i=0;i<total-1;i++)
      for(int j=0;j<total-1-i;j++)
         if(spreads[j]>spreads[j+1])
           {
            double td=spreads[j]; spreads[j]=spreads[j+1]; spreads[j+1]=td;
            string ts=syms[j];    syms[j]=syms[j+1];    syms[j+1]=ts;
           }

   // Build the HTML page
   string html="<html><head><meta charset='UTF-8'>";
   html+=StringFormat("<script>var refreshTimer=setTimeout(function(){location.reload();},%d000);function stopRefresh(){clearTimeout(refreshTimer);}</script>",RefreshSeconds);
   html+="<style>";
   html+="body{font-family:monospace;background:black;color:white;margin:0;}";
  html+="div.wrapper{display:flex;gap:20px;flex-wrap:wrap;}";
  html+="div.table-container{overflow-x:auto;}";
  html+="table{border-collapse:collapse;}";
  html+="th,td{border:1px solid white;padding:4px;text-align:right;color:white;}";
  html+="th:first-child{text-align:left;}";
  html+="button.ps-action{display:block;margin-top:4px;width:100%;}";
  html+="</style></head><body><div class='wrapper'>";

   // Spread table
   html+="<div class='table-container'><table>";
   html+="<tr><th>Symbol</th><th>Spread %</th></tr>";
   for(int i=0;i<total;i++)
      html+=StringFormat("<tr><td>%s</td><td>%0.10f</td></tr>",syms[i],spreads[i]);
   html+="</table></div>";

   // Swap table
   html+="<div class='table-container'><table>";
   html+="<tr><th>Symbol</th><th>Swap Long</th><th>Swap Short</th></tr>";
   for(int i=0;i<total;i++)
     {
      string sym=symbols.At(i);
      double swapLong,swapShort;
      if(!SymbolInfoDouble(sym,SYMBOL_SWAP_LONG,swapLong) ||
         !SymbolInfoDouble(sym,SYMBOL_SWAP_SHORT,swapShort))
        { swapLong=0.0; swapShort=0.0; }

      string colLong  = (swapLong<0)?"red":"green";
      string colShort = (swapShort<0)?"red":"green";
      html+=StringFormat("<tr><td>%s</td><td style='color:%s'>%0.2f</td><td style='color:%s'>%0.2f</td></tr>",
                        sym,colLong,swapLong,colShort,swapShort);
     }
   html+="</table></div>";
   html+="</div></body></html>";
   return html;
  }

//+------------------------------------------------------------------+
//| Generate an HTML table and open it in the default browser         |
//+------------------------------------------------------------------+
void ShowPopup()
  {
   // generate updated HTML content
   string combined_html = BuildSpreadSwapHtml();

   int defaultIndex = FindTFIndex(PERIOD_M5);

   string matrix_html = BuildMatrixHtml(defaultIndex);
   string matrixFile="CorrelationMatrix.html";
   int h = FileOpen(matrixFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h >= 0)
     { FileWriteString(h, matrix_html); FileClose(h); }

   string combinedFile="SpreadSwap.html";
   h = FileOpen(combinedFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h >= 0)
     { FileWriteString(h, combined_html); FileClose(h); }

   if(!pageOpened)
     {
      string base=TerminalInfoString(TERMINAL_DATA_PATH)+"\\MQL5\\Files\\";
      string fullMatrix=base+matrixFile;
      string fullCombined=base+combinedFile;

      string params=StringFormat("\"%s\" \"%s\"",
                                 fullMatrix,fullCombined);
      int res=ShellExecuteW(0,"open","msedge.exe",params,NULL,1);
      if(res>32)
         pageOpened=true;
      else
        MessageBoxW(0,BuildMatrixText(PERIOD_M5),"Correlation Matrix",0);
     }
  }

//+------------------------------------------------------------------+
