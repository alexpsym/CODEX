#property strict
#property description "Trader EA: trendline/standard limits, EMA bounce, and token-gated one-shot standard market execution. SL/TP are set by DISTANCE in MT5 POINTS, with optional AutoTP NetRR."
#property version   "2.31"

#include <Trade/Trade.mqh>
CTrade trade;

#import "shell32.dll"
long ShellExecuteW(long hwnd, string operation, string file, string parameters, string directory, int show_cmd);
#import

// -------------------- Strategy selection --------------------
enum StrategyMode
{
   STRAT_TRENDLINE_LIMIT = 0,
   STRAT_EMA_BOUNCE      = 1,
   STRAT_STANDARD_LIMIT  = 2,
   STRAT_STANDARD_MARKET = 3
};

input group "Strategy"
input StrategyMode Strategy = STRAT_TRENDLINE_LIMIT;
input bool         OrdersEnabled = true; // master on/off switch (in Inputs)

// -------------------- Inputs (risk model) --------------------
input group "Risk (account currency)"
input double RiskAUD_Target           = 10.0;
input double RiskAUD_Min              = 9.0;
input double RiskAUD_Max              = 12.0;
input bool   IncludeCommissionInRisk  = true;
input double CommissionPerLotPerSide  = 3.50;
input int    RiskSlippageBufferPoints = 50;
input int    SlippagePoints           = 10;

// -------------------- Inputs (shared: DISTANCES, in MT5 POINTS) --------------------
input group "Stops & Targets (points)"
input int    SL_DistancePoints        = 200;
input bool   AutoTP_NetRR_Enabled      = true;
input double NetRR_Target              = 2.0;
input int    AutoTP_SafetyPoints       = 0;
input int    TP_DistancePoints         = 400;

// NOTE: 1 MT5 point = 1 TradingView tick.
// NOTE: On 5-digit FX / 3-digit JPY, 1 pip = 10 points (e.g., 5.4 pips = 54 points).

// -------------------- Inputs (Trendline strategy only) --------------------
input group "Trendline strategy (Trendline Limit)"
enum TL_Direction { TL_BUY_LIMIT=0, TL_SELL_LIMIT=1 };
input TL_Direction Direction           = TL_BUY_LIMIT;
input string       TrendlineObjectName = "";     // Auto-arm key: valid name => ON, empty/invalid => OFF
input int          PendingCancelAfterMinutes  = 60;

// -------------------- Inputs (Standard limit strategy only) --------------------
input group "Standard limit strategy"
enum StandardLimitDirection { STD_BUY_LIMIT=0, STD_SELL_LIMIT=1 };
input StandardLimitDirection StandardLimitSide = STD_BUY_LIMIT;
input double StandardLimitEntryPrice = 0.0;

// -------------------- Inputs (Standard one-shot market strategy only) --------------------
input group "Standard market strategy (one-shot)"
enum StandardMarketDirection { STD_MARKET_BUY=0, STD_MARKET_SELL=1 };
input StandardMarketDirection StandardMarketSide = STD_MARKET_BUY;
input string StandardMarketExecutionToken = "";

// -------------------- Inputs (EMA bounce strategy only) --------------------
input group "EMA bounce strategy (derived from Backtest)"
input bool   UseDualEMA       = true;
input int    FastEMAPeriod    = 9;
input int    SlowEMAPeriod    = 20;
input int    TrendEMAPeriod   = 20;
input bool   Debug            = false;

// -------------------- Orders housekeeping --------------------
input group "Orders"
input int    MagicNumber              = 91001;
input bool   EnforceOneTradeAtATime   = true;

// -------------------- Pepperstone spread export --------------------
input group "Pepperstone Spread Export"
input bool   EnablePepperstoneSpreadExport = true;
input int    PepperstoneSpreadExportIntervalSeconds = 300;
input string PepperstoneSpreadExportSymbols = "";
input string PepperstoneSpreadExportPath = "C:\\GPT\\CODEX-master\\mt5-clone\\pepperstone_spreads_latest.json";

// -------------------- Unified Market Watch spread + ATR feed --------------------
input group "Unified Market Watch"
input bool   EnableUnifiedMarketWatch = true;
input int    UnifiedATRLength = 14;
input int    UnifiedSymbolsPerTimer = 4;
input string UnifiedMarketWatchFileName = "MarketWatchUnifiedFeed.json";
input bool   LaunchUnifiedMarketWatchWindow = true;
input string UnifiedPythonExecutable = "C:\\Users\\User\\miniconda3\\python.exe";
input string UnifiedWindowScriptPath = "C:\\GPT\\CODEX-master\\mt5-clone\\atr_percent_window.py";

// -------------------- Internals --------------------
string   g_trendName    = "";
ulong    g_ticket       = 0;
datetime g_lastBarTime  = 0;
datetime g_armStartTime = 0;
datetime g_expireAt     = 0;
bool     g_wasInPosition = false;
datetime g_lastPepperstoneSpreadExport = 0;
int      g_lastPepperstoneSpreadExportSymbolCount = 0;
bool     g_marketExecutionHandled = false;
bool     g_standardLimitStructuralBlock = false;
bool     g_standardLimitAcceptanceMismatch = false;
bool     g_standardLimitPlacementConfirmed = false;
bool     g_standardLimitExpired = false;
int      g_standardLimitAttemptCount = 0;
datetime g_standardLimitNextAttemptAt = 0;
string   g_standardLimitLastReason = "";
bool     g_lastPendingFailureStructural = false;
bool     g_lastPendingAcceptanceMismatch = false;
const string STANDARD_MARKET_EXECUTE_BUTTON = "TraderExecuteStandardMarket";

void RefreshStandardMarketExecuteButton()
{
   ObjectDelete(0, STANDARD_MARKET_EXECUTE_BUTTON);
   if(Strategy != STRAT_STANDARD_MARKET) return;
   ObjectCreate(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJPROP_XDISTANCE, 12);
   ObjectSetInteger(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJPROP_YDISTANCE, 22);
   ObjectSetInteger(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJPROP_XSIZE, 260);
   ObjectSetInteger(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJPROP_YSIZE, 26);
   ObjectSetString(0, STANDARD_MARKET_EXECUTE_BUTTON, OBJPROP_TEXT,
                   "EXECUTE " + string(StandardMarketSide == STD_MARKET_BUY ? "BUY" : "SELL") + " " + _Symbol + " | acct " + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)));
}

const int STANDARD_LIMIT_MAX_ATTEMPTS = 6;
const int STANDARD_LIMIT_MAX_BACKOFF_SECONDS = 30;

// EMA handles
int hFast  = INVALID_HANDLE;
int hSlow  = INVALID_HANDLE;
int hTrend = INVALID_HANDLE;

string EA_COMMENT = "Trader";
string EA_VERSION = "2.20";

const int UNIFIED_FRAME_COUNT = 4;
ENUM_TIMEFRAMES g_unifiedPeriods[4] = {PERIOD_M1, PERIOD_M5, PERIOD_H1, PERIOD_D1};
string g_unifiedKeys[4] = {"m1", "m5", "h1", "d1"};
string g_unifiedSymbols[];
bool g_unifiedForex[];
int g_unifiedForexWorklist[];
int g_unifiedHandles[];
double g_unifiedAtrPercent[];
string g_unifiedFrameState[];
int g_unifiedFrameError[];
int g_unifiedRetryCount[];
datetime g_unifiedRetryAt[];
datetime g_unifiedClosedBar[];
bool g_unifiedSpreadValid[];
double g_unifiedSpreadPercent[];
double g_unifiedSpreadPoints[];
int g_unifiedCursor = 0;
string g_unifiedSignature = "";
datetime g_unifiedLastSuccessfulAtr = 0;
bool g_unifiedFeedDirty = false;
bool g_unifiedIsProducer = false;
string g_unifiedOwnerName = "";
double g_unifiedOwnerState = 0.0;
uint g_unifiedOwnerGeneration = 0;
ulong g_unifiedLastAtrProcessMs = 0;
ulong g_unifiedLastMaintenanceMs = 0;
ulong g_unifiedLastSpreadProcessMs = 0;
ulong g_unifiedLastHeartbeatMs = 0;
const ulong UNIFIED_FAST_INTERVAL_MS = 150;
const ulong UNIFIED_HEARTBEAT_INTERVAL_MS = 1000;

bool UnifiedIsCurrency(const string value)
{
   return value=="AUD" || value=="CAD" || value=="CHF" || value=="EUR" || value=="GBP" || value=="JPY" || value=="NZD" || value=="USD";
}

bool UnifiedIsForex(const string symbol)
{
   long calcMode=-1;
   return SymbolInfoInteger(symbol,SYMBOL_TRADE_CALC_MODE,calcMode) && (calcMode==SYMBOL_CALC_MODE_FOREX || calcMode==SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE);
}

int UnifiedIndex(const int symbolIndex,const int frameIndex) { return symbolIndex*UNIFIED_FRAME_COUNT+frameIndex; }

string UnifiedSignature()
{
   string value="";
   for(int i=0;i<SymbolsTotal(true);i++) value += SymbolName(i,true)+"|";
   return value;
}

void UnifiedReleaseHandles()
{
   for(int i=0;i<ArraySize(g_unifiedHandles);i++)
      if(g_unifiedHandles[i] != INVALID_HANDLE) { IndicatorRelease(g_unifiedHandles[i]); g_unifiedHandles[i]=INVALID_HANDLE; }
}

void UnifiedRebuildUniverse()
{
   UnifiedReleaseHandles();
   int count=SymbolsTotal(true);
   ArrayResize(g_unifiedSymbols,count); ArrayResize(g_unifiedForex,count);
   ArrayResize(g_unifiedSpreadValid,count); ArrayResize(g_unifiedSpreadPercent,count); ArrayResize(g_unifiedSpreadPoints,count); ArrayResize(g_unifiedForexWorklist,0);
   int slots=count*UNIFIED_FRAME_COUNT;
   ArrayResize(g_unifiedHandles,slots); ArrayResize(g_unifiedAtrPercent,slots); ArrayResize(g_unifiedFrameState,slots);
   ArrayResize(g_unifiedFrameError,slots); ArrayResize(g_unifiedRetryCount,slots); ArrayResize(g_unifiedRetryAt,slots); ArrayResize(g_unifiedClosedBar,slots);
   for(int i=0;i<count;i++)
   {
      g_unifiedSymbols[i]=SymbolName(i,true); g_unifiedForex[i]=UnifiedIsForex(g_unifiedSymbols[i]);
      g_unifiedSpreadValid[i]=false; g_unifiedSpreadPercent[i]=0.0; g_unifiedSpreadPoints[i]=0.0;
      if(g_unifiedForex[i]) { int work=ArraySize(g_unifiedForexWorklist); ArrayResize(g_unifiedForexWorklist,work+1); g_unifiedForexWorklist[work]=i; }
      for(int f=0;f<UNIFIED_FRAME_COUNT;f++)
      {
         int slot=UnifiedIndex(i,f); g_unifiedHandles[slot]=INVALID_HANDLE; g_unifiedAtrPercent[slot]=0.0;
         g_unifiedFrameState[slot]=g_unifiedForex[i]?"Loading":"N/A"; g_unifiedFrameError[slot]=0;
         g_unifiedRetryCount[slot]=0; g_unifiedRetryAt[slot]=0; g_unifiedClosedBar[slot]=0;
      }
   }
   g_unifiedCursor=0; g_unifiedSignature=UnifiedSignature(); g_unifiedFeedDirty=true;
}

void UnifiedScheduleRetry(const int slot,const string symbol,const int frameIndex,const int errorCode,const string reason)
{
   string previous=g_unifiedFrameState[slot]; int previousError=g_unifiedFrameError[slot];
   g_unifiedFrameError[slot]=errorCode; g_unifiedRetryCount[slot]++;
   int waitSeconds=(int)MathMin(60,MathPow(2,MathMin(g_unifiedRetryCount[slot],5)));
   g_unifiedRetryAt[slot]=TimeCurrent()+waitSeconds;
   g_unifiedFrameState[slot]=(g_unifiedAtrPercent[slot]>0.0?"Stale":(errorCode==0?"Loading":"Error "+IntegerToString(errorCode)));
   if(previous!=g_unifiedFrameState[slot] || previousError!=errorCode) g_unifiedFeedDirty=true;
   if(g_unifiedRetryCount[slot] <= 3)
      Print(EA_COMMENT, ": ATR ", symbol, " ", g_unifiedKeys[frameIndex], " failed error=", IntegerToString(errorCode), " reason=", reason, " retry_seconds=", IntegerToString(waitSeconds));
}

void UnifiedRefreshFrame(const int symbolIndex,const int frameIndex)
{
   if(!g_unifiedForex[symbolIndex]) return;
   int slot=UnifiedIndex(symbolIndex,frameIndex); string symbol=g_unifiedSymbols[symbolIndex];
   if(TimeCurrent() < g_unifiedRetryAt[slot]) return;
   long synchronized=0;
   if(!SymbolSelect(symbol,true) || !SeriesInfoInteger(symbol,g_unifiedPeriods[frameIndex],SERIES_SYNCHRONIZED,synchronized) || synchronized==0 || Bars(symbol,g_unifiedPeriods[frameIndex]) < MathMax(20,UnifiedATRLength+2))
   {
      UnifiedScheduleRetry(slot,symbol,frameIndex,0,"series not synchronized or insufficient bars"); return;
   }
   datetime closedBar=iTime(symbol,g_unifiedPeriods[frameIndex],1);
   if(closedBar<=0) { UnifiedScheduleRetry(slot,symbol,frameIndex,0,"closed candle unavailable"); return; }
   if(g_unifiedAtrPercent[slot]>0.0 && g_unifiedClosedBar[slot]==closedBar && g_unifiedFrameState[slot]=="Ready") return;
   if(g_unifiedHandles[slot] == INVALID_HANDLE)
   {
      ResetLastError(); g_unifiedHandles[slot]=iATR(symbol,g_unifiedPeriods[frameIndex],MathMax(2,MathMin(100,UnifiedATRLength)));
      if(g_unifiedHandles[slot] == INVALID_HANDLE)
      {
         int errorCode=GetLastError(); UnifiedScheduleRetry(slot,symbol,frameIndex,errorCode,"iATR handle creation failed"); return;
      }
   }
   double buffer[1]; ResetLastError();
   int copied=CopyBuffer(g_unifiedHandles[slot],0,1,1,buffer); double close=iClose(symbol,g_unifiedPeriods[frameIndex],1);
   if(copied != 1 || buffer[0] == EMPTY_VALUE || !MathIsValidNumber(buffer[0]) || buffer[0] <= 0.0 || close == EMPTY_VALUE || !MathIsValidNumber(close) || close <= 0.0)
   {
      int errorCode=GetLastError();
      if(errorCode!=0 && g_unifiedHandles[slot]!=INVALID_HANDLE) { IndicatorRelease(g_unifiedHandles[slot]); g_unifiedHandles[slot]=INVALID_HANDLE; }
      UnifiedScheduleRetry(slot,symbol,frameIndex,errorCode,"closed-candle ATR/close unavailable"); return;
   }
   double percent=(buffer[0]/close)*100.0;
   if(!MathIsValidNumber(percent) || percent<=0.0) { UnifiedScheduleRetry(slot,symbol,frameIndex,0,"invalid ATR percentage"); return; }
   bool changed=(g_unifiedAtrPercent[slot]!=percent || g_unifiedClosedBar[slot]!=closedBar || g_unifiedFrameState[slot]!="Ready" || g_unifiedFrameError[slot]!=0);
   g_unifiedAtrPercent[slot]=percent; g_unifiedClosedBar[slot]=closedBar; g_unifiedFrameState[slot]="Ready";
   g_unifiedLastSuccessfulAtr=TimeGMT();
   g_unifiedFrameError[slot]=0; g_unifiedRetryCount[slot]=0; g_unifiedRetryAt[slot]=0;
   if(changed) g_unifiedFeedDirty=true;
}

void UnifiedProcessBatch()
{
   int count=ArraySize(g_unifiedForexWorklist); if(count<=0) return;
   int batch=MathMin(count,MathMax(1,MathMin(50,UnifiedSymbolsPerTimer)));
   for(int n=0;n<batch;n++)
   {
      int index=g_unifiedForexWorklist[g_unifiedCursor%count];
      for(int f=0;f<UNIFIED_FRAME_COUNT;f++) UnifiedRefreshFrame(index,f);
      g_unifiedCursor=(g_unifiedCursor+1)%count;
   }
}

void UnifiedRefreshSpreads()
{
   for(int i=0;i<ArraySize(g_unifiedSymbols);i++)
   {
      MqlTick tick; double point=SymbolInfoDouble(g_unifiedSymbols[i],SYMBOL_POINT);
      bool valid=SymbolInfoTick(g_unifiedSymbols[i],tick) && tick.bid>0.0 && tick.ask>=tick.bid && point>0.0;
      double percent=0.0, points=0.0;
      if(valid) { double midpoint=(tick.ask+tick.bid)/2.0; valid=(midpoint>0.0); if(valid) { percent=((tick.ask-tick.bid)/midpoint)*100.0; points=(tick.ask-tick.bid)/point; } }
      if(valid!=g_unifiedSpreadValid[i] || (valid && (percent!=g_unifiedSpreadPercent[i] || points!=g_unifiedSpreadPoints[i])))
      { g_unifiedSpreadValid[i]=valid; g_unifiedSpreadPercent[i]=percent; g_unifiedSpreadPoints[i]=points; g_unifiedFeedDirty=true; }
   }
}

bool UnifiedExportFeed()
{
   if(!g_unifiedFeedDirty) return true;
   string lastSuccessful=(g_unifiedLastSuccessfulAtr>0?"\""+IsoTimeUTC(g_unifiedLastSuccessfulAtr)+"\"":"null");
   string json="{\r\n  \"name\":\"MarketWatchUnifiedFeed\",\r\n  \"generated_at\":\""+IsoTimeUTC(TimeGMT())+"\",\r\n  \"last_successful_refresh\":"+lastSuccessful+",\r\n  \"atr_length\":"+IntegerToString(UnifiedATRLength)+",\r\n  \"symbols\":[\r\n";
   for(int i=0;i<ArraySize(g_unifiedSymbols);i++)
   {
      string symbol=g_unifiedSymbols[i]; bool spreadOk=g_unifiedSpreadValid[i];
      if(i>0) json+=",\r\n";
      json+="    {\"symbol\":\""+JsonEscape(symbol)+"\",\"is_forex\":"+(g_unifiedForex[i]?"true":"false")+",\"status\":\""+(g_unifiedForex[i]?"Ready":"N/A")+"\",\"reason\":\""+(g_unifiedForex[i]?"":"ATR not applicable")+"\",\"spread_percent\":"+(spreadOk?DoubleToString(g_unifiedSpreadPercent[i],10):"null")+",\"spread_points\":"+(spreadOk?DoubleToString(g_unifiedSpreadPoints[i],2):"null");
      for(int f=0;f<UNIFIED_FRAME_COUNT;f++)
      {
         int slot=UnifiedIndex(i,f);
         json+=",\"atr_percent_"+g_unifiedKeys[f]+"\":"+(g_unifiedAtrPercent[slot]>0.0?DoubleToString(g_unifiedAtrPercent[slot],10):"null");
         json+=",\"state_"+g_unifiedKeys[f]+"\":\""+g_unifiedFrameState[slot]+"\",\"error_"+g_unifiedKeys[f]+"\":"+IntegerToString(g_unifiedFrameError[slot]);
      }
      json+="}";
   }
   json+="\r\n  ]\r\n}\r\n";
   if(!UnifiedTryAcquireProducer()) return false;
   string temporary=UnifiedMarketWatchFileName+"."+IntegerToString((long)ChartID())+".tmp";
   int handle=FileOpen(temporary,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(handle==INVALID_HANDLE) { Print(EA_COMMENT, ": unified feed open failed error=",IntegerToString(GetLastError())); return false; }
   FileWriteString(handle,json); FileClose(handle);
   if(!UnifiedTryAcquireProducer()) { FileDelete(temporary,FILE_COMMON); return false; }
   if(!FileMove(temporary,FILE_COMMON,UnifiedMarketWatchFileName,FILE_REWRITE)) { FileDelete(temporary,FILE_COMMON); return false; }
   g_unifiedFeedDirty=false; return true;
}

string UnifiedQuote(const string value) { string escaped=value; StringReplace(escaped,"\"","\\\""); return "\""+escaped+"\""; }

void UnifiedLaunchWindow()
{
   if(!LaunchUnifiedMarketWatchWindow) return;
   string feedPath=TerminalInfoString(TERMINAL_COMMONDATA_PATH)+"\\Files\\"+UnifiedMarketWatchFileName;
   string parameters=UnifiedQuote(UnifiedWindowScriptPath)+" --file "+UnifiedQuote(feedPath)+" --refresh-ms 500 --decimals 5 --top-n 10 --rank-timeframe 1m";
   long result=ShellExecuteW(0,"open",UnifiedPythonExecutable,parameters,"",1);
   if(result<=32) Print(EA_COMMENT, ": unified desktop window launch failed code=",IntegerToString((int)result));
}

void UnifiedConfigureOwnership()
{
   uint hash=2166136261;
   for(int i=0;i<StringLen(UnifiedMarketWatchFileName);i++) hash=(hash ^ (uint)StringGetCharacter(UnifiedMarketWatchFileName,i))*16777619;
   string suffix=IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"_"+IntegerToString((long)TerminalInfoInteger(TERMINAL_BUILD))+"_"+IntegerToString((long)hash);
   g_unifiedOwnerName="TraderUnifiedOwner_"+suffix;
}

bool UnifiedTryAcquireProducer()
{
   if(g_unifiedOwnerName=="") UnifiedConfigureOwnership();
   if(!GlobalVariableCheck(g_unifiedOwnerName)) GlobalVariableSet(g_unifiedOwnerName,0.0);
   double observed=GlobalVariableGet(g_unifiedOwnerName), now=(double)TimeLocal();
   bool current=(g_unifiedIsProducer && observed==g_unifiedOwnerState);
   bool stale=(MathFloor(observed)<now);
   if(!current && !stale) { g_unifiedIsProducer=false; return false; }
   g_unifiedOwnerGeneration++;
   double renewed=now+3.0+((double)(g_unifiedOwnerGeneration%100000)/100000.0);
   if(GlobalVariableSetOnCondition(g_unifiedOwnerName,renewed,observed))
   { g_unifiedOwnerState=renewed; g_unifiedIsProducer=true; return true; }
   g_unifiedIsProducer=false; return false;
}

void UnifiedReleaseProducerState()
{
   UnifiedReleaseHandles(); ArrayResize(g_unifiedSymbols,0); ArrayResize(g_unifiedForex,0); ArrayResize(g_unifiedForexWorklist,0); g_unifiedCursor=0; g_unifiedSignature=""; g_unifiedFeedDirty=false; g_unifiedLastAtrProcessMs=0; g_unifiedLastSpreadProcessMs=0; g_unifiedLastHeartbeatMs=0;
}

void UnifiedWriteHeartbeat()
{
   ulong now=GetTickCount64();
   if(now-g_unifiedLastHeartbeatMs<UNIFIED_HEARTBEAT_INTERVAL_MS || !UnifiedTryAcquireProducer()) return;
   int handle=FileOpen(UnifiedMarketWatchFileName+".heartbeat",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(handle!=INVALID_HANDLE) { FileWriteString(handle,IntegerToString((long)TimeGMT())); FileClose(handle); g_unifiedLastHeartbeatMs=now; }
}

void UnifiedReleaseOwnership()
{
   if(g_unifiedIsProducer && GlobalVariableCheck(g_unifiedOwnerName) && GlobalVariableGet(g_unifiedOwnerName)==g_unifiedOwnerState)
      GlobalVariableSetOnCondition(g_unifiedOwnerName,0.0,g_unifiedOwnerState);
   g_unifiedIsProducer=false;
}

void UnifiedMarketWatchInit() { if(!EnableUnifiedMarketWatch) return; UnifiedConfigureOwnership(); if(UnifiedTryAcquireProducer()) { UnifiedRebuildUniverse(); UnifiedRefreshSpreads(); UnifiedProcessBatch(); UnifiedExportFeed(); UnifiedWriteHeartbeat(); UnifiedLaunchWindow(); } }
void UnifiedMarketWatchTimer()
{
   if(!EnableUnifiedMarketWatch) return;
   if(!UnifiedTryAcquireProducer()) { UnifiedReleaseProducerState(); return; }
   if(g_unifiedSignature=="") UnifiedRebuildUniverse();
   ulong now=GetTickCount64();
   if(now-g_unifiedLastSpreadProcessMs>=UNIFIED_FAST_INTERVAL_MS) { UnifiedRefreshSpreads(); g_unifiedLastSpreadProcessMs=now; }
   UnifiedWriteHeartbeat();
   if(now-g_unifiedLastAtrProcessMs>=1000) { if(UnifiedSignature()!=g_unifiedSignature) UnifiedRebuildUniverse(); if(!UnifiedTryAcquireProducer()) { UnifiedReleaseProducerState(); return; } UnifiedProcessBatch(); g_unifiedLastAtrProcessMs=now; if(!UnifiedTryAcquireProducer()) { UnifiedReleaseProducerState(); return; } }
   UnifiedExportFeed();
}

void UnifiedMarketWatchTick() { if(!EnableUnifiedMarketWatch || !g_unifiedIsProducer) return; ulong now=GetTickCount64(); if(now-g_unifiedLastSpreadProcessMs<UNIFIED_FAST_INTERVAL_MS) return; if(!UnifiedTryAcquireProducer()) { UnifiedReleaseProducerState(); return; } UnifiedRefreshSpreads(); g_unifiedLastSpreadProcessMs=now; UnifiedExportFeed(); }

void Dbg(const string msg){ if(Debug) Print(EA_COMMENT, ": ", msg); }
bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,
                                       const double rawEntry,
                                       const bool allowReplace,
                                       string &why);
bool IsTradePlacementAccepted(const uint retcode);
bool StandardLimitShouldBeActive();

bool IsNewBar()
{
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == 0) return false;
   if(t != g_lastBarTime){ g_lastBarTime = t; return true; }
   return false;
}

double NormalizePrice(double p)
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   return NormalizeDouble(p, digits);
}

double NormalizeVolume(double vol)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;

   double steps = MathFloor(vol / step);
   double v = steps * step;
   if(v < vmin) v = vmin;
   if(v > vmax) v = vmax;

   int digits = (int)MathRound(-MathLog10(step));
   if(digits < 0) digits = 2;
   return NormalizeDouble(v, digits);
}

int PointsPerPip()
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(digits == 5 || digits == 3) return 10;
   return 1;
}

bool CalcRiskFor1Lot(double stopPoints, double &lossPerLotAUD)
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0 || tickSize <= 0 || _Point <= 0) return false;

   double valuePerPoint = tickValue * (_Point / tickSize);
   lossPerLotAUD = stopPoints * valuePerPoint;
   return (lossPerLotAUD > 0);
}

// -------------------- Trendline helpers --------------------
bool TrendlineExists(const string name)
{
   if(name == "") return false;
   if(ObjectFind(0, name) < 0) return false;
   long t = ObjectGetInteger(0, name, OBJPROP_TYPE);
   return (t == OBJ_TREND);
}

double GetTrendlinePriceAtTime(const string name, datetime t)
{
   return ObjectGetValueByTime(0, name, t, 0);
}

bool BuildSLFromDistance(double entry, bool isBuy, double &slOut, string &why)
{
   if(SL_DistancePoints <= 0){ why = "SL_DistancePoints must be > 0."; return false; }
   double slDist = (double)SL_DistancePoints * _Point;
   slOut = isBuy ? (entry - slDist) : (entry + slDist);
   slOut = NormalizePrice(slOut);

   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0 && MathAbs(entry - slOut) < stopsLevel * _Point)
   { why = "SL too close to entry for broker stops-level."; return false; }

   why = "";
   return true;
}

bool BuildTPManualFromDistance(double entry, bool isBuy, double &tpOut, string &why)
{
   if(TP_DistancePoints <= 0){ why = "TP_DistancePoints must be > 0 (or enable AutoTP)."; return false; }
   double tpDist = (double)TP_DistancePoints * _Point;
   tpOut = isBuy ? (entry + tpDist) : (entry - tpDist);
   tpOut = NormalizePrice(tpOut);

   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0 && MathAbs(entry - tpOut) < stopsLevel * _Point)
   { why = "TP too close to entry for broker stops-level."; return false; }

   why = "";
   return true;
}

bool ComputeAutoTP_NetRR(double entry, bool isBuy, double vol, double riskRoundedAUD,
                         double &tpOut, int &tpPointsOut, double &effNetRR, string &why)
{
   if(vol <= 0){ why="Invalid volume for AutoTP."; return false; }
   if(NetRR_Target <= 0){ why="NetRR_Target must be > 0."; return false; }

   double commissionRT = CommissionPerLotPerSide * 2.0 * vol;

   double rBase = riskRoundedAUD;
   if(!IncludeCommissionInRisk) rBase += commissionRT;

   double requiredNetProfit   = NetRR_Target * rBase;
   double requiredGrossProfit = requiredNetProfit + commissionRT;

   ENUM_ORDER_TYPE ot = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   double testPrice = isBuy ? (entry + _Point) : (entry - _Point);
   double p1 = 0.0;
   if(!OrderCalcProfit(ot, _Symbol, vol, entry, testPrice, p1))
   { why="OrderCalcProfit failed while estimating profit-per-point."; return false; }

   double profitPerPoint = MathAbs(p1);
   if(profitPerPoint <= 0){ why="Profit-per-point is zero/invalid."; return false; }

   int pts = (int)MathCeil(requiredGrossProfit / profitPerPoint);
   if(pts < 1) pts = 1;
   pts += AutoTP_SafetyPoints;

   double tp = isBuy ? (entry + (double)pts * _Point) : (entry - (double)pts * _Point);
   tp = NormalizePrice(tp);

   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0 && MathAbs(entry - tp) < stopsLevel * _Point)
   {
      pts = stopsLevel + AutoTP_SafetyPoints;
      tp  = isBuy ? (entry + (double)pts * _Point) : (entry - (double)pts * _Point);
      tp  = NormalizePrice(tp);
   }

   double grossAtTP = 0.0;
   if(!OrderCalcProfit(ot, _Symbol, vol, entry, tp, grossAtTP))
   { why="OrderCalcProfit failed while validating final TP."; return false; }

   double netAtTP = grossAtTP - commissionRT;
   effNetRR = (rBase > 0) ? (netAtTP / rBase) : 0.0;

   tpOut = tp;
   tpPointsOut = pts;
   why = "";
   return true;
}

bool IsLimitPriceValid(double entry, bool isBuyLimit, string &why)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0){ why="Bid/Ask not available."; return false; }

   int stopsLevel  = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freezeLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int minDistancePoints = MathMax(stopsLevel, freezeLevel);
   double minDistance = (double)minDistancePoints * _Point;

   if(isBuyLimit)
   {
      if(!(entry < ask)){ why="Buy Limit entry is not below current Ask."; return false; }
      if(minDistancePoints > 0 && (ask - entry) < minDistance)
      {
         why = "Buy Limit entry is too close to current Ask for broker stops/freeze distance.";
         return false;
      }
   }
   else
   {
      if(!(entry > bid)){ why="Sell Limit entry is not above current Bid."; return false; }
      if(minDistancePoints > 0 && (entry - bid) < minDistance)
      {
         why = "Sell Limit entry is too close to current Bid for broker stops/freeze distance.";
         return false;
      }
   }

   why = "";
   return true;
}

bool ComputeVolumeFromRisk(double entry, double sl, double &outVol, double &outRiskRoundedAUD, string &why)
{
   double riskMin = RiskAUD_Min;
   double riskMax = MathMax(RiskAUD_Max, riskMin);
   double riskTarget = MathMax(RiskAUD_Target, riskMin);
   why = "";

   double stopPoints = MathAbs(entry - sl) / _Point;
   if(stopPoints <= 0){ why="Stop distance is zero/invalid."; return false; }

   double lossPerLotSL = 0.0;
   if(!CalcRiskFor1Lot(stopPoints, lossPerLotSL))
   { why="Failed to compute tick value based risk for 1 lot."; return false; }

   double commissionRTPerLot = 2.0 * CommissionPerLotPerSide;

   double riskPerLotSizing = lossPerLotSL;
   if(IncludeCommissionInRisk) riskPerLotSizing += commissionRTPerLot;
   if(riskPerLotSizing <= 0){ why="Total risk per lot invalid."; return false; }

   double volRaw = riskTarget / riskPerLotSizing;
   double vol = NormalizeVolume(volRaw);
   if(vol <= 0){ why="Computed volume rounds to 0 (below broker min lot)."; return false; }

   double riskSL = lossPerLotSL * vol;
   double riskCommission = commissionRTPerLot * vol;
   double riskTotal = IncludeCommissionInRisk ? (riskSL + riskCommission) : riskSL;

   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   int digits = (int)MathRound(-MathLog10(step));
   if(digits < 0) digits = 2;

   if(riskTotal > riskMax)
   {
      while(riskTotal > riskMax && vol - step >= vmin)
      {
         vol = NormalizeDouble(vol - step, digits);
         riskSL = lossPerLotSL * vol;
         riskCommission = commissionRTPerLot * vol;
         riskTotal = IncludeCommissionInRisk ? (riskSL + riskCommission) : riskSL;
      }
   }
   else if(riskTotal < riskMin)
   {
      while(riskTotal < riskMin && vol + step <= vmax)
      {
         vol = NormalizeDouble(vol + step, digits);
         riskSL = lossPerLotSL * vol;
         riskCommission = commissionRTPerLot * vol;
         riskTotal = IncludeCommissionInRisk ? (riskSL + riskCommission) : riskSL;
      }
   }

   outVol = vol;
   outRiskRoundedAUD = riskTotal;

   if(riskTotal < riskMin){ why="Rounded risk is below RiskAUD_Min filter."; return false; }
   if(riskTotal > riskMax){ why="Rounded risk exceeds RiskAUD_Max filter."; return false; }

   // worst-case sizing buffer (optional)
   double effectiveStopPoints = stopPoints + MathMax(0, RiskSlippageBufferPoints);
   if(effectiveStopPoints > stopPoints)
   {
      double lossPerLotWorst = 0.0;
      if(!CalcRiskFor1Lot(effectiveStopPoints, lossPerLotWorst))
      { why="Failed to compute worst-case risk for 1 lot."; return false; }

      double riskWorst = lossPerLotWorst * vol;
      if(IncludeCommissionInRisk) riskWorst += commissionRTPerLot * vol;

      while(riskWorst > riskMax && vol - step >= vmin)
      {
         double vNext = NormalizeDouble(vol - step, digits);
         double riskNext = IncludeCommissionInRisk
            ? (lossPerLotSL * vNext + commissionRTPerLot * vNext)
            : (lossPerLotSL * vNext);
         if(riskNext < riskMin) break;

         vol = vNext;
         riskSL = lossPerLotSL * vol;
         riskCommission = commissionRTPerLot * vol;
         riskTotal = IncludeCommissionInRisk ? (riskSL + riskCommission) : riskSL;

         riskWorst = lossPerLotWorst * vol;
         if(IncludeCommissionInRisk) riskWorst += commissionRTPerLot * vol;
      }

      outVol = vol;
      outRiskRoundedAUD = riskTotal;
      if(riskWorst > riskMax) why = "WARN: worst-case buffered risk exceeds RiskAUD_Max.";
   }

   return true;
}

bool InPosition()
{
   if(!EnforceOneTradeAtATime) return false;
   return PositionSelect(_Symbol);
}

string ShortStableFingerprint(const string value)
{
   long h1 = 5381;
   long h2 = 52711;
   int len = StringLen(value);
   for(int i = 0; i < len; i++)
   {
      long ch = (long)StringGetCharacter(value, i);
      h1 = (h1 * 33 + ch) % 2147483647;
      h2 = (h2 * 131 + ch) % 2147483629;
   }
   return StringFormat("%08X%08X", (uint)h1, (uint)h2);
}

string StandardMarketTokenFingerprint()
{
   return ShortStableFingerprint(StandardMarketExecutionToken);
}

string StandardMarketGlobalKey()
{
   long login = (long)AccountInfoInteger(ACCOUNT_LOGIN);
   string identity = (string)login + "|" + _Symbol + "|" +
                     IntegerToString(MagicNumber) + "|" + StandardMarketExecutionToken;
   string fingerprint = ShortStableFingerprint(identity);
   string key = "TraderMkt." + (string)login + "." +
                IntegerToString(MagicNumber) + "." + fingerprint;
   if(StringLen(key) > 63)
      key = "TraderMkt." + fingerprint;
   return key;
}

bool ValidateTradingReadiness(const bool isBuy, string &why)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   { why = "Terminal AutoTrading is disabled."; return false; }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   { why = "EA trading is disabled; enable Allow Algo Trading for this Expert."; return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   { why = "Trading is not allowed for this account."; return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
   { why = "Expert trading is not allowed for this account."; return false; }

   long tradeMode = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if(tradeMode == SYMBOL_TRADE_MODE_DISABLED)
   { why = "Trading is disabled for this symbol."; return false; }
   if(tradeMode == SYMBOL_TRADE_MODE_CLOSEONLY)
   { why = "Symbol is close-only; a new order is not allowed."; return false; }
   if(isBuy && tradeMode == SYMBOL_TRADE_MODE_SHORTONLY)
   { why = "Symbol is short-only; Buy is not allowed."; return false; }
   if(!isBuy && tradeMode == SYMBOL_TRADE_MODE_LONGONLY)
   { why = "Symbol is long-only; Sell is not allowed."; return false; }

   if(!SymbolSelect(_Symbol, true))
   { why = "Chart symbol could not be selected in Market Watch."; return false; }
   why = "";
   return true;
}

bool ValidateVolumeForBroker(const double volume, string &why)
{
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vmin <= 0.0 || vmax <= 0.0 || step <= 0.0)
   { why = "Broker volume minimum/maximum/step is unavailable."; return false; }
   if(volume < vmin - 1e-9)
   { why = "Calculated volume is below the broker minimum."; return false; }
   if(volume > vmax + 1e-9)
   { why = "Calculated volume exceeds the broker maximum."; return false; }
   double stepCount = volume / step;
   if(MathAbs(stepCount - MathRound(stepCount)) > 1e-7)
   { why = "Calculated volume is not aligned to the broker volume step."; return false; }
   why = "";
   return true;
}

bool ValidateMarketStopsAtLiveQuote(const bool isBuy,
                                    const double bid,
                                    const double ask,
                                    const double sl,
                                    const double tp,
                                    string &why)
{
   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freezeLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int requiredPoints = MathMax(stopsLevel, freezeLevel);
   double requiredDistance = (double)requiredPoints * _Point;
   double closeSideAnchor = isBuy ? bid : ask;

   if(isBuy)
   {
      if(!(sl < closeSideAnchor && tp > closeSideAnchor))
      { why = "Buy SL/TP are on an invalid side of the live Bid."; return false; }
      if(requiredPoints > 0 && ((closeSideAnchor - sl) < requiredDistance || (tp - closeSideAnchor) < requiredDistance))
      { why = "Buy SL/TP violate the broker stop/freeze distance at the live Bid."; return false; }
   }
   else
   {
      if(!(sl > closeSideAnchor && tp < closeSideAnchor))
      { why = "Sell SL/TP are on an invalid side of the live Ask."; return false; }
      if(requiredPoints > 0 && ((sl - closeSideAnchor) < requiredDistance || (closeSideAnchor - tp) < requiredDistance))
      { why = "Sell SL/TP violate the broker stop/freeze distance at the live Ask."; return false; }
   }
   why = "";
   return true;
}

void LogStandardMarketOutcome(const string outcome,
                              const bool isBuy,
                              const double entry,
                              const double sl,
                              const double tp,
                              const double volume,
                              const double risk,
                              const string tokenFingerprint,
                              const uint retcode,
                              const string retcodeDescription,
                              const ulong orderTicket,
                              const ulong dealTicket,
                              const string reason)
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   Print(EA_COMMENT,
         ": mode=standard_market outcome=", outcome,
         " symbol=", _Symbol,
         " side=", (isBuy ? "buy" : "sell"),
         " entry=", DoubleToString(entry, digits),
         " sl=", DoubleToString(sl, digits),
         " tp=", DoubleToString(tp, digits),
         " volume=", DoubleToString(volume, 8),
         " risk=", DoubleToString(risk, 2),
         " token_fp=", tokenFingerprint,
         " retcode=", (string)retcode,
         " retcode_description=", retcodeDescription,
         " order=", (string)orderTicket,
         " deal=", (string)dealTicket,
         " reason=", reason);
}

bool AcquireStandardMarketGateLock(int &lockHandle, string &why)
{
   lockHandle = INVALID_HANDLE;
   string lockName = "TraderMarketExecutionGate.lck";
   for(int attempt = 0; attempt < 20; attempt++)
   {
      ResetLastError();
      lockHandle = FileOpen(lockName, FILE_READ | FILE_WRITE | FILE_BIN | FILE_COMMON);
      if(lockHandle != INVALID_HANDLE)
      {
         why = "";
         return true;
      }
      Sleep(25);
   }
   why = "Could not acquire the exclusive terminal-common execution gate lock. error=" + IntegerToString(GetLastError());
   return false;
}

bool HasBlockingPendingOrderForMarket(ulong &ticketOut)
{
   ticketOut = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0 || !OrderSelect(ticket)) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((int)OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      ticketOut = ticket;
      return true;
   }
   return false;
}

bool ConsumeStandardMarketToken(string &why, bool &alreadyConsumed)
{
   alreadyConsumed = false;
   string key = StandardMarketGlobalKey();
   if(StringLen(key) > 63)
   { why = "Internal token-gate key exceeds the MT5 63-character limit."; return false; }

   int lockHandle = INVALID_HANDLE;
   if(!AcquireStandardMarketGateLock(lockHandle, why)) return false;

   if(GlobalVariableCheck(key))
   {
      double currentValue = GlobalVariableGet(key);
      if(currentValue != 0.0)
      {
         alreadyConsumed = true;
         why = "Execution token was already consumed; download a newly generated market .set for an explicit retry.";
         FileClose(lockHandle);
         return false;
      }
   }

   double marker = (double)TimeLocal();
   if(marker <= 0.0) marker = 1.0;
   ResetLastError();
   bool marked = GlobalVariableCheck(key)
      ? GlobalVariableSetOnCondition(key, marker, 0.0)
      : (GlobalVariableSet(key, marker) != 0);
   if(!marked)
   {
      if(GlobalVariableCheck(key) && GlobalVariableGet(key) != 0.0)
      {
         alreadyConsumed = true;
         why = "Execution token was already consumed; download a newly generated market .set for an explicit retry.";
      }
      else
      {
         why = "Could not atomically consume the terminal-global execution token. error=" + IntegerToString(GetLastError());
      }
      FileClose(lockHandle);
      return false;
   }
   GlobalVariablesFlush();
   FileClose(lockHandle);
   why = "";
   return true;
}

bool ExecuteStandardMarketOnce()
{
   if(g_marketExecutionHandled) return false;
   g_marketExecutionHandled = true;

   bool isBuy = (StandardMarketSide == STD_MARKET_BUY);
   string tokenFingerprint = StandardMarketTokenFingerprint();
   string why = "";
   if(!OrdersEnabled)
   {
      LogStandardMarketOutcome("orders_disabled", isBuy, 0, 0, 0, 0, 0, tokenFingerprint, 0, "not_sent", 0, 0,
                               "OrdersEnabled is false; no token was consumed and no order was sent.");
      return false;
   }
   if(StringLen(StandardMarketExecutionToken) < 16)
   {
      LogStandardMarketOutcome("invalid_token", isBuy, 0, 0, 0, 0, 0, tokenFingerprint, 0, "not_sent", 0, 0,
                               "StandardMarketExecutionToken is missing/too short; download a new market .set.");
      return false;
   }
   if(EnforceOneTradeAtATime && PositionSelect(_Symbol))
   {
      LogStandardMarketOutcome("blocked_one_trade_rule", isBuy, 0, 0, 0, 0, 0, tokenFingerprint, 0, "not_sent", 0, 0,
                               "An open position already exists for this symbol; no token was consumed.");
      return false;
   }
   ulong blockingPendingTicket = 0;
   if(EnforceOneTradeAtATime && HasBlockingPendingOrderForMarket(blockingPendingTicket))
   {
      LogStandardMarketOutcome("blocked_one_trade_rule", isBuy, 0, 0, 0, 0, 0, tokenFingerprint, 0, "not_sent", blockingPendingTicket, 0,
                               "This EA already has a pending order for the symbol/magic; no token was consumed.");
      return false;
   }
   if(!ValidateTradingReadiness(isBuy, why))
   {
      LogStandardMarketOutcome("blocked_trade_readiness", isBuy, 0, 0, 0, 0, 0, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }

   MqlTick liveTick;
   if(!SymbolInfoTick(_Symbol, liveTick) || liveTick.bid <= 0.0 || liveTick.ask <= 0.0)
   {
      LogStandardMarketOutcome("blocked_live_quote", isBuy, 0, 0, 0, 0, 0, tokenFingerprint, 0, "not_sent", 0, 0,
                               "Live Bid/Ask is unavailable; no token was consumed.");
      return false;
   }

   double entry = NormalizePrice(isBuy ? liveTick.ask : liveTick.bid);
   double sl = 0.0;
   double tp = 0.0;
   double volume = 0.0;
   double riskRounded = 0.0;
   if(!BuildSLFromDistance(entry, isBuy, sl, why))
   {
      LogStandardMarketOutcome("invalid_stops", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }
   if(!ComputeVolumeFromRisk(entry, sl, volume, riskRounded, why))
   {
      LogStandardMarketOutcome("invalid_risk", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }
   if(!ValidateVolumeForBroker(volume, why))
   {
      LogStandardMarketOutcome("invalid_volume", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }

   int autoTpPts = 0;
   double effectiveNetRR = 0.0;
   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuy, volume, riskRounded, tp, autoTpPts, effectiveNetRR, why))
      {
         LogStandardMarketOutcome("invalid_stops", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
         return false;
      }
   }
   else if(!BuildTPManualFromDistance(entry, isBuy, tp, why))
   {
      LogStandardMarketOutcome("invalid_stops", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }
   if(!ValidateMarketStopsAtLiveQuote(isBuy, liveTick.bid, liveTick.ask, sl, tp, why))
   {
      LogStandardMarketOutcome("invalid_stops", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }

   Print(EA_COMMENT,
         ": mode=standard_market preflight symbol=", _Symbol,
         " side=", (isBuy ? "buy" : "sell"),
         " live_bid=", DoubleToString(liveTick.bid, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " live_ask=", DoubleToString(liveTick.ask, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " entry=", DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " sl=", DoubleToString(sl, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " tp=", DoubleToString(tp, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " volume=", DoubleToString(volume, 8),
         " risk=", DoubleToString(riskRounded, 2),
         " token_fp=", tokenFingerprint);

   bool alreadyConsumed = false;
   if(!ConsumeStandardMarketToken(why, alreadyConsumed))
   {
      LogStandardMarketOutcome(alreadyConsumed ? "already_consumed_token" : "token_gate_error",
                               isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint,
                               0, "not_sent", 0, 0, why);
      return false;
   }

   bool sendOk = isBuy
      ? trade.Buy(volume, _Symbol, 0.0, sl, tp, EA_COMMENT + " standard market")
      : trade.Sell(volume, _Symbol, 0.0, sl, tp, EA_COMMENT + " standard market");
   uint retcode = trade.ResultRetcode();
   string retcodeDescription = trade.ResultRetcodeDescription();
   ulong orderTicket = (ulong)trade.ResultOrder();
   ulong dealTicket = (ulong)trade.ResultDeal();
   bool accepted = sendOk && IsTradePlacementAccepted(retcode) && (orderTicket > 0 || dealTicket > 0);
   if(accepted)
   {
      LogStandardMarketOutcome("accepted", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint,
                               retcode, retcodeDescription, orderTicket, dealTicket, "Broker accepted the one-shot market request.");
      return true;
   }

   LogStandardMarketOutcome("rejected", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint,
                            retcode, retcodeDescription, orderTicket, dealTicket,
                            "The token remains consumed for safety; download a newly generated market .set for an explicit retry.");
   return false;
}

void CancelAllPendingByMagic()
{
   int total = OrdersTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(!OrderSelect(ticket)) continue;

      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((int)OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;

      long type = OrderGetInteger(ORDER_TYPE);
      if(type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_SELL_LIMIT)
      {
         if(!trade.OrderDelete(ticket))
         {
            Print(EA_COMMENT, ": Failed to delete pending order #", ticket,
                  ". retcode=", trade.ResultRetcode(),
                  " (", trade.ResultRetcodeDescription(), ")");
         }
      }
   }

   g_ticket = 0;
   g_armStartTime = 0;
   g_expireAt = 0;
}

datetime ComputeExpireAt()
{
   if(PendingCancelAfterMinutes <= 0) return 0;
   datetime now = TimeCurrent();
   datetime exp = now + (datetime)(PendingCancelAfterMinutes * 60);
   if(exp <= now) exp = now + 60;
   return exp;
}

bool PendingAgeExpired()
{
   if(PendingCancelAfterMinutes <= 0) return false;
   if(g_armStartTime <= 0) return false;
   long ageSec = (long)(TimeCurrent() - g_armStartTime);
   return (ageSec >= (long)PendingCancelAfterMinutes * 60L);
}

// ---------- EMA bounce helpers ----------
bool GetBufferValue(const int handle, const int bufferIndex, const int shift, double &outVal)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, bufferIndex, shift, 1, buf) != 1) return false;
   outVal = buf[0];
   return true;
}

bool GetEmaBounceSignal(ENUM_ORDER_TYPE &outType)
{
   double c1 = iClose(_Symbol, _Period, 1);
   double o1 = iOpen(_Symbol,  _Period, 1);
   if(c1 == 0 || o1 == 0) return false;

   bool candleBear = (c1 < o1);
   bool candleBull = (c1 > o1);

   bool up=false, down=false;

   if(UseDualEMA)
   {
      double fast1=0, slow1=0;
      if(!GetBufferValue(hFast, 0, 1, fast1)) return false;
      if(!GetBufferValue(hSlow, 0, 1, slow1)) return false;

      up   = (c1 > slow1 && fast1 > slow1);
      down = (c1 < slow1 && fast1 < slow1);
   }
   else
   {
      double ema1=0;
      if(!GetBufferValue(hTrend, 0, 1, ema1)) return false;
      up   = (c1 > ema1);
      down = (c1 < ema1);
   }

   if(up && candleBear){ outType = ORDER_TYPE_BUY;  return true; }
   if(down && candleBull){ outType = ORDER_TYPE_SELL; return true; }

   return false;
}

bool PlaceMarketEmaBounce()
{
   ENUM_ORDER_TYPE sigType;
   if(!GetEmaBounceSignal(sigType)) return false;

   bool isBuy = (sigType == ORDER_TYPE_BUY);

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0) return false;

   double entry = NormalizePrice(isBuy ? ask : bid);

   string why="";
   double sl=0, tp=0;

   if(!BuildSLFromDistance(entry, isBuy, sl, why)) return false;

   double vol=0, riskRounded=0;
   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, why)) return false;

   int autoTpPts=0;
   double effNetRR=0.0;

   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuy, vol, riskRounded, tp, autoTpPts, effNetRR, why)) return false;
   }
   else
   {
      if(!BuildTPManualFromDistance(entry, isBuy, tp, why)) return false;
   }

   bool ok=false;
   if(isBuy) ok = trade.Buy(vol, _Symbol, 0.0, sl, tp, EA_COMMENT);
   else      ok = trade.Sell(vol, _Symbol, 0.0, sl, tp, EA_COMMENT);

   return ok;
}

bool PlaceOrReplacePendingTrendline()
{
   bool isBuyLimit = (Direction == TL_BUY_LIMIT);
   datetime barTime = iTime(_Symbol, _Period, 0);
   double entry = GetTrendlinePriceAtTime(g_trendName, barTime);
   if(entry <= 0.0)
   {
      Print(EA_COMMENT, ": Trendline entry is invalid/non-positive.");
      return false;
   }

   string why="";
   return PlaceOrReplacePendingLimitAtEntry(isBuyLimit, entry, true, why);
}

bool IsTradePlacementAccepted(const uint retcode)
{
   return (retcode == TRADE_RETCODE_DONE ||
           retcode == TRADE_RETCODE_DONE_PARTIAL ||
           retcode == TRADE_RETCODE_PLACED);
}

bool IsPendingLimitTicketMatching(const ulong ticket,
                                  const bool isBuyLimit,
                                  const double entry)
{
   if(ticket == 0 || !OrderSelect(ticket)) return false;
   if(OrderGetString(ORDER_SYMBOL) != _Symbol) return false;
   if((int)OrderGetInteger(ORDER_MAGIC) != MagicNumber) return false;
   long requiredType = isBuyLimit ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;
   if(OrderGetInteger(ORDER_TYPE) != requiredType) return false;
   double orderPrice = OrderGetDouble(ORDER_PRICE_OPEN);
   double tolerance = MathMax(_Point * 0.5, 1e-10);
   return (MathAbs(orderPrice - entry) <= tolerance);
}

bool FindMatchingPendingLimit(const bool isBuyLimit,
                              const double entry,
                              ulong &ticketOut)
{
   ticketOut = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(IsPendingLimitTicketMatching(ticket, isBuyLimit, entry))
      {
         ticketOut = ticket;
         return true;
      }
   }
   return false;
}

bool FindAnyPendingLimitForEA(ulong &ticketOut)
{
   ticketOut = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0 || !OrderSelect(ticket)) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((int)OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      long type = OrderGetInteger(ORDER_TYPE);
      if(type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_SELL_LIMIT)
      {
         ticketOut = ticket;
         return true;
      }
   }
   return false;
}

bool IsTransientPendingRetcode(const uint retcode)
{
   return (retcode == TRADE_RETCODE_REQUOTE ||
           retcode == TRADE_RETCODE_TIMEOUT ||
           retcode == TRADE_RETCODE_PRICE_CHANGED ||
           retcode == TRADE_RETCODE_PRICE_OFF ||
           retcode == TRADE_RETCODE_TOO_MANY_REQUESTS ||
           retcode == TRADE_RETCODE_LOCKED ||
           retcode == TRADE_RETCODE_CONNECTION ||
           retcode == TRADE_RETCODE_MARKET_CLOSED ||
           retcode == TRADE_RETCODE_SERVER_DISABLES_AT ||
           retcode == TRADE_RETCODE_CLIENT_DISABLES_AT);
}

bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,
                                       const double rawEntry,
                                       const bool allowReplace,
                                       string &why)
{
   g_lastPendingFailureStructural = false;
   g_lastPendingAcceptanceMismatch = false;
   if(rawEntry <= 0.0)
   {
      g_lastPendingFailureStructural = true;
      why = "Invalid manual entry price (must be > 0).";
      Print(EA_COMMENT, ": ", why, " rawEntry=", DoubleToString(rawEntry, 8));
      return false;
   }

   double entry = NormalizePrice(rawEntry);
   if(entry <= 0.0)
   {
      g_lastPendingFailureStructural = true;
      why = "Normalized entry price is invalid/non-positive.";
      Print(EA_COMMENT, ": ", why);
      return false;
   }

   if(!allowReplace && g_ticket > 0)
   {
      if(IsPendingLimitTicketMatching(g_ticket, isBuyLimit, entry)) return true;
      g_ticket = 0;
   }

   double sl=0.0, tp=0.0, vol=0.0, riskRounded=0.0;

   if(!ValidateTradingReadiness(isBuyLimit, why))
   {
      Print(EA_COMMENT, ": Pending limit trade-readiness preflight blocked. ", why);
      return false;
   }

   if(!IsLimitPriceValid(entry, isBuyLimit, why))
   {
      g_lastPendingFailureStructural = (why != "Bid/Ask not available.");
      Print(EA_COMMENT, ": Wrong-side/too-close limit price. ", why,
            " entry=", DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
      return false;
   }

   if(!BuildSLFromDistance(entry, isBuyLimit, sl, why))
   {
      g_lastPendingFailureStructural = true;
      Print(EA_COMMENT, ": Failed to build SL. ", why);
      return false;
   }

   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, why))
   {
      g_lastPendingFailureStructural = true;
      Print(EA_COMMENT, ": Risk sizing failure. ", why);
      return false;
   }
   if(!ValidateVolumeForBroker(vol, why))
   {
      g_lastPendingFailureStructural = true;
      Print(EA_COMMENT, ": Broker volume preflight failed. ", why);
      return false;
   }

   int autoTpPts=0;
   double effNetRR=0.0;
   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuyLimit, vol, riskRounded, tp, autoTpPts, effNetRR, why))
      {
         g_lastPendingFailureStructural = true;
         Print(EA_COMMENT, ": Failed to build AutoTP. ", why);
         return false;
      }
   }
   else
   {
      if(!BuildTPManualFromDistance(entry, isBuyLimit, tp, why))
      {
         g_lastPendingFailureStructural = true;
         Print(EA_COMMENT, ": Failed to build manual TP (possibly too close). ", why);
         return false;
      }
   }

   if(allowReplace) CancelAllPendingByMagic();

   ENUM_ORDER_TYPE_TIME tt = ORDER_TIME_GTC;
   datetime exp = 0;
   if(PendingCancelAfterMinutes > 0)
   {
      if(g_armStartTime <= 0) g_armStartTime = TimeCurrent();
      if(g_expireAt <= 0) g_expireAt = ComputeExpireAt();
      tt = ORDER_TIME_SPECIFIED;
      exp = g_expireAt;
   }

   MqlTick liveTick;
   bool hasLiveTick = SymbolInfoTick(_Symbol, liveTick);
   Print(EA_COMMENT,
         ": mode=standard_limit preflight symbol=", _Symbol,
         " side=", (isBuyLimit ? "buy" : "sell"),
         " entry=", DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " live_bid=", DoubleToString(hasLiveTick ? liveTick.bid : 0.0, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " live_ask=", DoubleToString(hasLiveTick ? liveTick.ask : 0.0, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " sl=", DoubleToString(sl, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " tp=", DoubleToString(tp, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " volume=", DoubleToString(vol, 8),
         " risk=", DoubleToString(riskRounded, 2),
         " expiration=", TimeToString(exp, TIME_DATE | TIME_SECONDS));

   bool sendOk=false;
   if(isBuyLimit) sendOk = trade.BuyLimit(vol, entry, _Symbol, sl, tp, tt, exp, EA_COMMENT);
   else           sendOk = trade.SellLimit(vol, entry, _Symbol, sl, tp, tt, exp, EA_COMMENT);

   uint retcode = trade.ResultRetcode();
   ulong orderTicket = (ulong)trade.ResultOrder();
   bool accepted = IsTradePlacementAccepted(retcode) && orderTicket > 0;
   Print(EA_COMMENT, ": mode=standard_limit broker_result retcode=", retcode,
         " description=", trade.ResultRetcodeDescription(),
         " order=", (string)orderTicket,
         " sendOk=", (sendOk ? "true" : "false"));
   if(!sendOk || !accepted)
   {
      g_lastPendingFailureStructural = !IsTransientPendingRetcode(retcode);
      why = g_lastPendingFailureStructural
         ? "Broker/server rejected pending placement with a non-retryable result."
         : "Transient broker/transport failure while placing the pending order.";
      Print(EA_COMMENT, ": Pending limit placement failed. retcode=", retcode,
            " (", trade.ResultRetcodeDescription(), ")",
            ", order=", (string)orderTicket,
            ", sendOk=", (sendOk ? "true" : "false"));
      return false;
   }

   ulong observedTicket = 0;
   if(IsPendingLimitTicketMatching(orderTicket, isBuyLimit, entry))
      observedTicket = orderTicket;
   else
      FindMatchingPendingLimit(isBuyLimit, entry, observedTicket);
   if(observedTicket == 0)
   {
      g_lastPendingAcceptanceMismatch = true;
      why = "Broker reported accepted order ticket " + (string)orderTicket +
            " but no matching pending order is observable; automatic retry is blocked to prevent a duplicate.";
      Print(EA_COMMENT, ": mode=standard_limit outcome=accepted_not_observable ", why);
      return false;
   }

   g_ticket = observedTicket;
   why = "";
   Print(EA_COMMENT, ": mode=standard_limit outcome=confirmed_pending order=", (string)g_ticket,
         " symbol=", _Symbol, " magic=", IntegerToString(MagicNumber));
   return true;
}

bool PlacePendingStandardLimit()
{
   bool isBuyLimit = (StandardLimitSide == STD_BUY_LIMIT);
   string why="";
   bool placed = PlaceOrReplacePendingLimitAtEntry(isBuyLimit, StandardLimitEntryPrice, false, why);
   g_standardLimitLastReason = why;
   return placed;
}

void ArmStandardLimitPlacementWindow()
{
   if(g_armStartTime <= 0) g_armStartTime = TimeCurrent();
   if(PendingCancelAfterMinutes > 0 && g_expireAt <= 0)
      g_expireAt = ComputeExpireAt();
}

int StandardLimitRetryDelaySeconds(const int completedAttempts)
{
   int delaySeconds = 2;
   for(int i = 1; i < completedAttempts; i++)
   {
      delaySeconds *= 2;
      if(delaySeconds >= STANDARD_LIMIT_MAX_BACKOFF_SECONDS)
         return STANDARD_LIMIT_MAX_BACKOFF_SECONDS;
   }
   return (int)MathMin(delaySeconds, STANDARD_LIMIT_MAX_BACKOFF_SECONDS);
}

void AdoptObservedStandardLimit(const ulong ticket, const string source)
{
   g_ticket = ticket;
   g_standardLimitPlacementConfirmed = true;
   if(OrderSelect(ticket))
   {
      datetime setupTime = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      datetime expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
      if(setupTime > 0) g_armStartTime = setupTime;
      if(expiration > 0) g_expireAt = expiration;
   }
   Print(EA_COMMENT, ": mode=standard_limit outcome=confirmed_existing source=", source,
         " order=", (string)ticket, " symbol=", _Symbol,
         " magic=", IntegerToString(MagicNumber));
}

void MaintainStandardLimit(const string source)
{
   if(!StandardLimitShouldBeActive()) return;
   if(g_standardLimitStructuralBlock ||
      g_standardLimitAcceptanceMismatch ||
      g_standardLimitExpired)
      return;

   if(InPosition())
   {
      CancelAllPendingByMagic();
      g_standardLimitPlacementConfirmed = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=blocked_one_trade_rule source=", source,
            " reason=An open position exists for this symbol.");
      return;
   }

   ArmStandardLimitPlacementWindow();
   if(PendingAgeExpired() || (g_expireAt > 0 && TimeCurrent() >= g_expireAt))
   {
      CancelAllPendingByMagic();
      g_standardLimitExpired = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=expired source=", source,
            " reason=The placement window expired; load a new .set to arm another attempt.");
      return;
   }

   bool isBuyLimit = (StandardLimitSide == STD_BUY_LIMIT);
   double entry = NormalizePrice(StandardLimitEntryPrice);
   ulong matchingTicket = 0;
   if(FindMatchingPendingLimit(isBuyLimit, entry, matchingTicket))
   {
      if(!g_standardLimitPlacementConfirmed || g_ticket != matchingTicket)
         AdoptObservedStandardLimit(matchingTicket, source);
      return;
   }

   if(g_standardLimitPlacementConfirmed || g_ticket > 0)
   {
      g_standardLimitAcceptanceMismatch = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=previously_confirmed_not_observable source=", source,
            " order=", (string)g_ticket,
            " reason=Automatic replacement is blocked to prevent a duplicate; inspect MT5 Orders/History and load a new .set if needed.");
      return;
   }

   datetime now = TimeCurrent();
   if(g_standardLimitNextAttemptAt > 0 && now < g_standardLimitNextAttemptAt) return;
   if(g_standardLimitAttemptCount >= STANDARD_LIMIT_MAX_ATTEMPTS)
   {
      g_standardLimitStructuralBlock = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=retry_exhausted source=", source,
            " attempts=", IntegerToString(g_standardLimitAttemptCount),
            " reason=", g_standardLimitLastReason,
            " action=Load a corrected/new .set after resolving trading readiness or transport.");
      return;
   }

   ulong otherPendingTicket = 0;
   if(FindAnyPendingLimitForEA(otherPendingTicket))
   {
      g_standardLimitStructuralBlock = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=blocked_nonmatching_pending source=", source,
            " order=", (string)otherPendingTicket,
            " reason=Another pending limit with this symbol/magic exists; automatic placement is blocked to prevent a duplicate.");
      return;
   }

   g_standardLimitAttemptCount++;
   MqlTick liveTick;
   bool hasLiveTick = SymbolInfoTick(_Symbol, liveTick);
   Print(EA_COMMENT,
         ": mode=standard_limit attempt source=", source,
         " attempt=", IntegerToString(g_standardLimitAttemptCount),
         "/", IntegerToString(STANDARD_LIMIT_MAX_ATTEMPTS),
         " symbol=", _Symbol,
         " side=", (isBuyLimit ? "buy" : "sell"),
         " entry=", DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " live_bid=", DoubleToString(hasLiveTick ? liveTick.bid : 0.0, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " live_ask=", DoubleToString(hasLiveTick ? liveTick.ask : 0.0, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));

   if(PlacePendingStandardLimit())
   {
      g_standardLimitPlacementConfirmed = true;
      g_standardLimitNextAttemptAt = 0;
      return;
   }
   if(g_lastPendingAcceptanceMismatch)
   {
      g_standardLimitAcceptanceMismatch = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=accepted_not_observable source=", source,
            " reason=", g_standardLimitLastReason);
      return;
   }
   if(g_lastPendingFailureStructural)
   {
      g_standardLimitStructuralBlock = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=structural_block source=", source,
            " reason=", g_standardLimitLastReason,
            " action=Correct the limit price/settings and load a new .set; this EA will not retry this invalid request.");
      return;
   }

   if(g_standardLimitAttemptCount >= STANDARD_LIMIT_MAX_ATTEMPTS)
   {
      g_standardLimitStructuralBlock = true;
      Print(EA_COMMENT, ": mode=standard_limit outcome=retry_exhausted source=", source,
            " attempts=", IntegerToString(g_standardLimitAttemptCount),
            " reason=", g_standardLimitLastReason,
            " action=Resolve the readiness/transport problem and load a new .set.");
      return;
   }

   int delaySeconds = StandardLimitRetryDelaySeconds(g_standardLimitAttemptCount);
   g_standardLimitNextAttemptAt = now + delaySeconds;
   Print(EA_COMMENT, ": mode=standard_limit outcome=retry_scheduled source=", source,
         " attempt=", IntegerToString(g_standardLimitAttemptCount),
         " next_attempt_seconds=", IntegerToString(delaySeconds),
         " reason=", g_standardLimitLastReason);
}

void RefreshTrendlineNameFromInputs()
{
   g_trendName = TrendlineObjectName;
}

// Central gating logic:
// - OrdersEnabled must be true
// - For trendline strategy, TrendlineObjectName must reference an existing trendline object
bool TrendlineShouldBeActive()
{
   if(!OrdersEnabled) return false;
   if(Strategy != STRAT_TRENDLINE_LIMIT) return false;
   if(g_trendName == "") return false;
   if(!TrendlineExists(g_trendName)) return false;
   return true;
}

bool StandardLimitShouldBeActive()
{
   if(!OrdersEnabled) return false;
   if(Strategy != STRAT_STANDARD_LIMIT) return false;
   if(StandardLimitEntryPrice <= 0.0) return false;
   return true;
}

// ---------- Pepperstone spread export helpers ----------
string TrimText(string value)
{
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

string NormalizePepperstoneSpreadSymbol(const string rawSymbol)
{
   string symbol = TrimText(rawSymbol);
   StringToUpper(symbol);
   return symbol;
}

bool IsAlphaNumericChar(const ushort ch)
{
   if(ch >= 48 && ch <= 57) return true;
   if(ch >= 65 && ch <= 90) return true;
   if(ch >= 97 && ch <= 122) return true;
   return false;
}

bool IsPepperstoneSpreadSuffix(const string suffix)
{
   if(suffix == "") return false;
   ushort first = StringGetCharacter(suffix, 0);
   return !IsAlphaNumericChar(first);
}

string ExtractPepperstoneChartSuffix()
{
   string chartSymbol = TrimText(_Symbol);
   int baseLength = 6;
   if(StringLen(chartSymbol) <= baseLength) return "";
   string suffix = StringSubstr(chartSymbol, baseLength);
   if(!IsPepperstoneSpreadSuffix(suffix)) return "";
   return suffix;
}

bool TrySelectPepperstoneSpreadSymbol(const string requestedSymbol, const string candidate, string &resolvedSymbol)
{
   if(candidate == "") return false;
   if(!SymbolSelect(candidate, true)) return false;
   resolvedSymbol = candidate;
   Print(EA_COMMENT, ": Pepperstone spread export resolved ", requestedSymbol, " -> ", resolvedSymbol);
   return true;
}

string ResolvePepperstoneSpreadSymbol(const string rawSymbol)
{
   string exactSymbol = TrimText(rawSymbol);
   if(exactSymbol == "") return "";

   string requestedSymbol = exactSymbol;
   StringToUpper(requestedSymbol);

   string resolvedSymbol = "";

   if(TrySelectPepperstoneSpreadSymbol(requestedSymbol, exactSymbol, resolvedSymbol))
      return resolvedSymbol;
   if(exactSymbol != requestedSymbol && TrySelectPepperstoneSpreadSymbol(requestedSymbol, requestedSymbol, resolvedSymbol))
      return resolvedSymbol;

   int requestedLen = StringLen(requestedSymbol);
   if(requestedLen <= 0) return "";

   string chartSuffix = ExtractPepperstoneChartSuffix();
   if(chartSuffix != "")
   {
      string inferredSymbol = requestedSymbol + chartSuffix;
      if(inferredSymbol != exactSymbol && inferredSymbol != requestedSymbol)
      {
         if(TrySelectPepperstoneSpreadSymbol(requestedSymbol, inferredSymbol, resolvedSymbol))
            return resolvedSymbol;
      }
   }

   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
   {
      string candidate = SymbolName(i, false);
      if(StringLen(candidate) <= requestedLen) continue;
      if(StringSubstr(candidate, 0, requestedLen) != requestedSymbol) continue;
      string suffix = StringSubstr(candidate, requestedLen);
      if(!IsPepperstoneSpreadSuffix(suffix)) continue;
      if(TrySelectPepperstoneSpreadSymbol(requestedSymbol, candidate, resolvedSymbol))
         return resolvedSymbol;
   }

   return "";
}

string JsonEscape(const string value)
{
   string result = "";
   int len = StringLen(value);
   for(int i = 0; i < len; i++)
   {
      ushort ch = StringGetCharacter(value, i);
      if(ch == 34) result += "\\\"";
      else if(ch == 92) result += "\\\\";
      else if(ch == 10) result += "\\n";
      else if(ch == 13) result += "\\r";
      else if(ch == 9) result += "\\t";
      else result += StringSubstr(value, i, 1);
   }
   return result;
}

string IsoTimeUTC(datetime value)
{
   MqlDateTime dt;
   TimeToStruct(value, dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

string FileNameOnly(string path)
{
   string normalized = path;
   StringReplace(normalized, "/", "\\");
   int lastSlash = -1;
   int len = StringLen(normalized);
   for(int i = len - 1; i >= 0; i--)
   {
      if(StringGetCharacter(normalized, i) == 92)
      {
         lastSlash = i;
         break;
      }
   }
   if(lastSlash >= 0 && lastSlash < len - 1)
      return StringSubstr(normalized, lastSlash + 1);
   if(normalized == "")
      return "pepperstone_spreads_latest.json";
   return normalized;
}

bool TryGetPepperstoneBidAsk(const string symbol, double &bid, double &ask)
{
   bid = 0.0;
   ask = 0.0;

   MqlTick tick;
   if(SymbolInfoTick(symbol, tick))
   {
      if(tick.bid > 0.0 && tick.ask > 0.0)
      {
         bid = tick.bid;
         ask = tick.ask;
         return true;
      }
   }

   bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   return (bid > 0.0 && ask > 0.0);
}

void AppendPepperstoneSpreadJsonEntry(const string symbol, const string mt5Symbol, const string generated, string &entries, int &written)
{
   double bid = 0.0;
   double ask = 0.0;
   bool available = TryGetPepperstoneBidAsk(mt5Symbol, bid, ask);
   int symbolSpread = (int)SymbolInfoInteger(mt5Symbol, SYMBOL_SPREAD);

   if(written > 0) entries += ",\n";

   if(!available)
   {
      entries += StringFormat(
         "    {\"symbol\":\"%s\",\"mt5_symbol\":\"%s\",\"available\":false,\"symbol_spread\":%d,\"error\":\"bid/ask unavailable\",\"timestamp\":\"%s\"}",
         JsonEscape(symbol),
         JsonEscape(mt5Symbol),
         symbolSpread,
         generated
      );
      written++;
      return;
   }

   double midpoint = (ask + bid) / 2.0;
   double spreadPct = ((ask - bid) / midpoint) * 100.0;
   int digits = (int)SymbolInfoInteger(mt5Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(mt5Symbol, SYMBOL_POINT);
   double spreadPoints = point > 0.0 ? ((ask - bid) / point) : 0.0;

   entries += StringFormat(
      "    {\"symbol\":\"%s\",\"mt5_symbol\":\"%s\",\"available\":true,\"bid\":%s,\"ask\":%s,\"spread_pct\":%s,\"spread_points\":%s,\"symbol_spread\":%d,\"digits\":%d,\"point\":%s,\"timestamp\":\"%s\"}",
      JsonEscape(symbol),
      JsonEscape(mt5Symbol),
      DoubleToString(bid, digits),
      DoubleToString(ask, digits),
      DoubleToString(spreadPct, 10),
      DoubleToString(spreadPoints, 2),
      symbolSpread,
      digits,
      DoubleToString(point, 10),
      generated
   );
   written++;
}

string BuildPepperstoneSpreadJson(datetime generatedAt)
{
   string generated = IsoTimeUTC(generatedAt);
   string entries = "";
   int written = 0;
   int marketWatchCount = SymbolsTotal(true);
   Print(EA_COMMENT, ": Pepperstone Market Watch symbols found: ", IntegerToString(marketWatchCount));

   if(marketWatchCount > 0)
   {
      for(int i = 0; i < marketWatchCount; i++)
      {
         string mt5Symbol = SymbolName(i, true);
         mt5Symbol = TrimText(mt5Symbol);
         if(mt5Symbol == "") continue;
         SymbolSelect(mt5Symbol, true);
         AppendPepperstoneSpreadJsonEntry(mt5Symbol, mt5Symbol, generated, entries, written);
      }
      Print(EA_COMMENT, ": Pepperstone spread export wrote ", IntegerToString(written), " of ", IntegerToString(marketWatchCount), " Market Watch symbols");
   }
   else
   {
      string tokens[];
      int count = StringSplit(PepperstoneSpreadExportSymbols, ',', tokens);
      for(int i = 0; i < count; i++)
      {
         string symbol = NormalizePepperstoneSpreadSymbol(tokens[i]);
         if(symbol == "") continue;

         string mt5Symbol = ResolvePepperstoneSpreadSymbol(tokens[i]);
         if(mt5Symbol == "")
         {
            if(written > 0) entries += ",\n";
            entries += StringFormat(
               "    {\"symbol\":\"%s\",\"mt5_symbol\":\"\",\"available\":false,\"error\":\"no matching MT5 symbol was found\",\"timestamp\":\"%s\"}",
               JsonEscape(symbol),
               generated
            );
            written++;
            continue;
         }

         SymbolSelect(mt5Symbol, true);
         AppendPepperstoneSpreadJsonEntry(symbol, mt5Symbol, generated, entries, written);
      }
      Print(EA_COMMENT, ": Pepperstone spread export wrote ", IntegerToString(written), " configured fallback symbols");
   }

   g_lastPepperstoneSpreadExportSymbolCount = written;
   if(written == 0)
      Print(EA_COMMENT, ": Pepperstone spread export produced zero symbols. Check Market Watch availability.");

   string json = "{\n";
   json += "  \"version\": 1,\n";
   json += "  \"broker\": \"pepperstone\",\n";
   json += "  \"generated_at\": \"" + generated + "\",\n";
   json += "  \"symbol_count\": " + IntegerToString(written) + ",\n";
   json += "  \"account\": {\n";
   json += "    \"server\": \"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\",\n";
   json += "    \"company\": \"" + JsonEscape(AccountInfoString(ACCOUNT_COMPANY)) + "\",\n";
   json += "    \"login\": " + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) + "\n";
   json += "  },\n";
   json += "  \"symbols\": [\n" + entries + "\n  ]\n";
   json += "}\n";
   return json;
}

bool WritePepperstoneSpreadFile(const string requestedPath, const string contents, string &resolvedPath)
{
   ResetLastError();
   int handle = FileOpen(requestedPath, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileWriteString(handle, contents);
      FileClose(handle);
      resolvedPath = requestedPath;
      return true;
   }

   int absoluteError = GetLastError();
   ResetLastError();
   string fallbackName = FileNameOnly(requestedPath);
   handle = FileOpen(fallbackName, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      int fallbackError = GetLastError();
      Print(EA_COMMENT, ": Pepperstone spread export failed. requested=", requestedPath,
            " absolute_error=", absoluteError, " fallback_error=", fallbackError);
      return false;
   }

   FileWriteString(handle, contents);
   FileClose(handle);
   resolvedPath = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + fallbackName;
   Print(EA_COMMENT, ": MT5 blocked the requested absolute spread export path. Wrote fallback file instead: ", resolvedPath);
   return true;
}

void MaybeExportPepperstoneSpreads(const bool force=false)
{
   if(!EnablePepperstoneSpreadExport)
   {
      if(force)
         Print(EA_COMMENT, ": Pepperstone spread export is disabled.");
      return;
   }
   int interval = PepperstoneSpreadExportIntervalSeconds;
   if(interval < 1) interval = 1;
   datetime now = TimeGMT();
   if(!force && g_lastPepperstoneSpreadExport > 0 && (now - g_lastPepperstoneSpreadExport) < interval)
      return;

   g_lastPepperstoneSpreadExport = now;
   string payload = BuildPepperstoneSpreadJson(now);
   string writtenPath = "";
   if(WritePepperstoneSpreadFile(PepperstoneSpreadExportPath, payload, writtenPath))
   {
      if(g_lastPepperstoneSpreadExportSymbolCount <= 0)
         Print(EA_COMMENT, ": Pepperstone spread export succeeded but wrote zero symbols to ", writtenPath);
      else
         Print(EA_COMMENT, ": Pepperstone spread export wrote ", IntegerToString(g_lastPepperstoneSpreadExportSymbolCount), " symbols to ", writtenPath);
   }
   else
   {
      Print(EA_COMMENT, ": Pepperstone spread export failed. No file was written.");
   }
}

int OnInit()
{
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetExpertMagicNumber(MagicNumber);

   g_lastBarTime = iTime(_Symbol, _Period, 0);

   // Always run a timer so cancels happen even when market is quiet/no ticks.
   if(EnableUnifiedMarketWatch) EventSetMillisecondTimer(100); else EventSetTimer(1);
   Print(EA_COMMENT, ": EA version ", EA_VERSION);
   Print(EA_COMMENT, ": EnablePepperstoneSpreadExport=", (EnablePepperstoneSpreadExport ? "true" : "false"));
   Print(EA_COMMENT, ": PepperstoneSpreadExportIntervalSeconds=", IntegerToString(PepperstoneSpreadExportIntervalSeconds));
   Print(EA_COMMENT, ": PepperstoneSpreadExportSymbols=", PepperstoneSpreadExportSymbols);
   Print(EA_COMMENT, ": PepperstoneSpreadExportPath requested=", PepperstoneSpreadExportPath);
   Print(EA_COMMENT, ": TERMINAL_DATA_PATH=", TerminalInfoString(TERMINAL_DATA_PATH));
   Print(EA_COMMENT, ": Expected fallback MQL5\\Files path=", TerminalInfoString(TERMINAL_DATA_PATH), "\\MQL5\\Files\\", FileNameOnly(PepperstoneSpreadExportPath));
   MaybeExportPepperstoneSpreads(true);
   UnifiedMarketWatchInit();

   RefreshTrendlineNameFromInputs();

   // EMA handles only if strategy needs them
   if(Strategy == STRAT_EMA_BOUNCE)
   {
      if(UseDualEMA)
      {
         hFast = iMA(_Symbol, _Period, FastEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
         hSlow = iMA(_Symbol, _Period, SlowEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
         if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE) return INIT_FAILED;
      }
      else
      {
         hTrend = iMA(_Symbol, _Period, TrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
         if(hTrend == INVALID_HANDLE) return INIT_FAILED;
      }
   }

   // Immediate behavior on applying settings:
   // - If trendline is invalid/off -> cancel EA pending orders now
   // - If trendline is valid/on  -> place immediately now
   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      if(!TrendlineShouldBeActive())
      {
         CancelAllPendingByMagic();
      }
      else
      {
         // place immediately upon valid name submission
         PlaceOrReplacePendingTrendline();
      }
   }
   else if(Strategy == STRAT_STANDARD_LIMIT)
   {
      if(!StandardLimitShouldBeActive())
      {
         Print(EA_COMMENT, ": Standard limit strategy inactive/invalid on init. Cancelling pending orders.");
         CancelAllPendingByMagic();
      }
      else
      {
         MaintainStandardLimit("OnInit");
      }
   }
   else if(Strategy == STRAT_STANDARD_MARKET)
   {
      Print(EA_COMMENT, ": Standard Market loaded for ", _Symbol, ". Review account/side and click EXECUTE; no automatic order is sent.");
      RefreshStandardMarketExecuteButton();
   }
   else
   {
      // EMA strategy: nothing to place on init; it triggers on new-bar signal
      // If OrdersEnabled is false, do nothing (no pending orders expected here).
   }

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(hFast  != INVALID_HANDLE) IndicatorRelease(hFast);
   if(hSlow  != INVALID_HANDLE) IndicatorRelease(hSlow);
   if(hTrend != INVALID_HANDLE) IndicatorRelease(hTrend);
   UnifiedReleaseHandles();
   UnifiedReleaseOwnership();
   ObjectDelete(0, STANDARD_MARKET_EXECUTE_BUTTON);
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == STANDARD_MARKET_EXECUTE_BUTTON && Strategy == STRAT_STANDARD_MARKET)
      ExecuteStandardMarketOnce();
}

void OnTick()
{
   UnifiedMarketWatchTick();
   // If orders are disabled at any time, make sure trendline pendings are gone.
   if(!OrdersEnabled)
   {
      if(Strategy == STRAT_TRENDLINE_LIMIT || Strategy == STRAT_STANDARD_LIMIT) CancelAllPendingByMagic();
      return;
   }

   // Trendline: if name is cleared/invalid OR object was deleted -> cancel immediately
   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      RefreshTrendlineNameFromInputs();
      if(!TrendlineShouldBeActive())
      {
         CancelAllPendingByMagic();
         return;
      }

      bool nowInPos = InPosition();

      if(nowInPos)
      {
         // once filled, stop managing pendings
         g_wasInPosition = true;
         CancelAllPendingByMagic();
         return;
      }

      // position just closed -> immediately re-arm next pending
      if(g_wasInPosition)
      {
         g_wasInPosition = false;
         PlaceOrReplacePendingTrendline();
         return;
      }

      if(PendingAgeExpired())
      {
         CancelAllPendingByMagic();
         return;
      }

      if(IsNewBar())
      {
         PlaceOrReplacePendingTrendline();
      }
      return;
   }

   if(Strategy == STRAT_STANDARD_LIMIT)
   {
      if(!StandardLimitShouldBeActive())
      {
         Print(EA_COMMENT, ": Standard limit strategy inactive/invalid. Cancelling pending orders.");
         CancelAllPendingByMagic();
         return;
      }
      MaintainStandardLimit("OnTick");
      return;
   }

   // Standard manual market execution is deliberately OnInit-only. Reinitialization
   // is guarded by the persistent terminal-global token; ticks never submit it.
   if(Strategy == STRAT_STANDARD_MARKET) return;

   // EMA bounce strategy
   if(Strategy == STRAT_EMA_BOUNCE)
   {
      if(InPosition()) return;
      if(!IsNewBar()) return;
      if(OrdersEnabled) PlaceMarketEmaBounce();
   }
}

void OnTimer()
{
   ulong timerNow=GetTickCount64();
   bool secondElapsed=(timerNow-g_unifiedLastMaintenanceMs>=1000);
   if(secondElapsed) { MaybeExportPepperstoneSpreads(); g_unifiedLastMaintenanceMs=timerNow; }
   UnifiedMarketWatchTimer();

   if(!secondElapsed) return;

   // Mirrors OnTick gating so cancel happens even with no ticks
   if(Strategy == STRAT_STANDARD_LIMIT)
   {
      if(!OrdersEnabled || !StandardLimitShouldBeActive())
      {
         CancelAllPendingByMagic();
         return;
      }
      MaintainStandardLimit("OnTimer");
      return;
   }

   // The one-shot market strategy is never driven by the timer.
   if(Strategy == STRAT_STANDARD_MARKET) return;

   if(Strategy != STRAT_TRENDLINE_LIMIT) return;

   RefreshTrendlineNameFromInputs();

   if(!TrendlineShouldBeActive())
   {
      CancelAllPendingByMagic();
      return;
   }

   bool nowInPos = InPosition();

   if(nowInPos)
   {
      g_wasInPosition = true;
      CancelAllPendingByMagic();
      return;
   }

   if(g_wasInPosition)
   {
      g_wasInPosition = false;
      PlaceOrReplacePendingTrendline();
      return;
   }

   if(PendingAgeExpired())
   {
      CancelAllPendingByMagic();
      return;
   }
}
