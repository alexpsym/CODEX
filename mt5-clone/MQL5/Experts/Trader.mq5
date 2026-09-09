#property strict
#property description "Trader EA: trendline/standard limits, EMA bounce, and token-gated one-shot standard market execution. SL/TP are set by DISTANCE in MT5 POINTS, with optional AutoTP NetRR."
#property version   "2.38"
// Inactive Phase 1 preservation marker: #property version   "2.36"; EA_VERSION = "2.36"

#include <Trade/Trade.mqh>
CTrade trade;

#import "shell32.dll"
long ShellExecuteW(long hwnd, string operation, string file, string parameters, string directory, int show_cmd);
#import

#import "kernel32.dll"
uint GetFileAttributesW(string file_name);
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

input group "Desktop Trader Controls"
input bool   UseDesktopTraderControls       = true;
input bool   LaunchDesktopTraderWindow      = true;
input string PythonExecutable               = "C:\\Users\\User\\miniconda3\\python.exe";
input string TraderControlWindowScriptPath  = "C:\\GPT\\CODEX-master\\mt5-clone\\trader_control_window.py";
input int    TraderControlWindowRefreshMs   = 250;

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
input string       TrendlineObjectName = "";     // Named trendline to trade; the object remains unchanged after a cycle.
input long         TrendlineArmGeneration = 0;    // Set a new positive integer to arm/re-arm this unchanged named trendline once.
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
enum EmaBounceReference
{
   EMA_BOUNCE_FAST = 0, // Fast EMA
   EMA_BOUNCE_SLOW = 1  // Slow EMA
};
input EmaBounceReference BounceReferenceEMA = EMA_BOUNCE_SLOW; // Dual-EMA mode only; single-EMA mode always uses TrendEMAPeriod.
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
bool     g_lastPendingBrokerAttempted = false;
const string STANDARD_MARKET_EXECUTE_BUTTON = "TraderExecuteStandardMarket";
string   g_trendlineLifecycleStatus = "";
bool     g_trendlineTrackingFailed = false;
string   g_traderControlInstanceId = "";
bool     g_traderControlReady = false;
string   g_traderControlReason = "Desktop controls are initializing.";
// Keeps the lifecycle comment within common MT5 broker comment limits.
const long TRENDLINE_ARM_GENERATION_MAX = 999999999;
const int  TRADER_CONTROL_PROTOCOL_VERSION = 1;
const int  TRADER_CONTROL_COMMAND_MAX_AGE_SECONDS = 15;
const int  TRADER_CONTROL_STATUS_FRESH_SECONDS = 5;
const int  TRADER_CONTROL_SW_SHOWNORMAL = 1;
const uint TRADER_CONTROL_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF;
const uint TRADER_CONTROL_FILE_ATTRIBUTE_DIRECTORY = 0x00000010;

void RefreshStandardMarketExecuteButton()
{
   ObjectDelete(0, STANDARD_MARKET_EXECUTE_BUTTON);
   if(UseDesktopTraderControls) return;
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
string EA_VERSION = "2.38";

void Dbg(const string msg){ if(Debug) Print(EA_COMMENT, ": ", msg); }
bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,
                                       const double rawEntry,
                                       const bool allowReplace,
                                       const string orderComment,
                                       string &why);
bool IsTradePlacementAccepted(const uint retcode);
bool IsTransientPendingRetcode(const uint retcode);
bool IsDefinitePendingRejectionRetcode(const uint retcode);
bool StandardLimitShouldBeActive();
string TrendlineOrderComment(const long generation);

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

bool ComputeAutoTP_NetRR(double entry, bool isBuy, double vol, double riskRoundedAUD, double riskBufferedAUD,
                         double &tpOut, int &tpPointsOut, double &effNetRR, string &why)
{
   if(vol <= 0){ why="Invalid volume for AutoTP."; return false; }
   if(NetRR_Target <= 0){ why="NetRR_Target must be > 0."; return false; }

   double commissionRT = CommissionPerLotPerSide * 2.0 * vol;

   double finalAllInRisk = MathMax(riskRoundedAUD, riskBufferedAUD);
   if(!IncludeCommissionInRisk) finalAllInRisk += commissionRT;
   double rBase = MathMax(RiskAUD_Target, finalAllInRisk);

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
   double netAtTP = 0.0;
   int adjustmentAttempts = 0;
   const int maxAdjustmentAttempts = 10000;
   while(true)
   {
      if(!OrderCalcProfit(ot, _Symbol, vol, entry, tp, grossAtTP))
      { why="OrderCalcProfit failed while validating final TP."; return false; }
      netAtTP = grossAtTP - commissionRT;
      if(netAtTP + 0.0000001 >= requiredNetProfit) break;
      if(adjustmentAttempts++ >= maxAdjustmentAttempts)
      { why="Final normalized TP cannot satisfy the minimum Net R target."; return false; }
      pts++;
      tp = isBuy ? (entry + (double)pts * _Point) : (entry - (double)pts * _Point);
      tp = NormalizePrice(tp);
   }
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

bool ComputeVolumeFromRisk(double entry, double sl, double &outVol, double &outRiskRoundedAUD, double &outRiskBufferedAUD, string &why)
{
   double riskMin = RiskAUD_Min;
   double riskMax = MathMax(RiskAUD_Max, riskMin);
   double riskTarget = MathMax(RiskAUD_Target, riskMin);
   why = "";
   outRiskBufferedAUD = 0.0;

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
   outRiskBufferedAUD = riskTotal;

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
      outRiskBufferedAUD = riskWorst;
      if(riskWorst > riskMax)
      {
         why = "Worst-case buffered risk exceeds RiskAUD_Max.";
         return false;
      }
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
   double riskBuffered = 0.0;
   if(!BuildSLFromDistance(entry, isBuy, sl, why))
   {
      LogStandardMarketOutcome("invalid_stops", isBuy, entry, sl, tp, volume, riskRounded, tokenFingerprint, 0, "not_sent", 0, 0, why);
      return false;
   }
   if(!ComputeVolumeFromRisk(entry, sl, volume, riskRounded, riskBuffered, why))
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
      if(!ComputeAutoTP_NetRR(entry, isBuy, volume, riskRounded, riskBuffered, tp, autoTpPts, effectiveNetRR, why))
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

double SelectEmaBounceReference(const double fastValue,
                                const double slowValue,
                                const double trendValue)
{
   if(!UseDualEMA) return trendValue;
   return (BounceReferenceEMA == EMA_BOUNCE_FAST ? fastValue : slowValue);
}

bool ValidateEmaBouncePeriods()
{
   if(UseDualEMA)
   {
      if(FastEMAPeriod <= 0 || SlowEMAPeriod <= 0)
      {
         Print(EA_COMMENT, ": EMA Bounce initialization failed: FastEMAPeriod and SlowEMAPeriod must both be positive in dual-EMA mode.");
         return false;
      }
      return true;
   }
   if(TrendEMAPeriod <= 0)
   {
      Print(EA_COMMENT, ": EMA Bounce initialization failed: TrendEMAPeriod must be positive in single-EMA mode.");
      return false;
   }
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
      double reference1 = SelectEmaBounceReference(fast1, slow1, 0.0);

      up   = (c1 > reference1 && fast1 > slow1);
      down = (c1 < reference1 && fast1 < slow1);
   }
   else
   {
      double ema1=0;
      if(!GetBufferValue(hTrend, 0, 1, ema1)) return false;
      double reference1 = SelectEmaBounceReference(0.0, 0.0, ema1);
      up   = (c1 > reference1);
      down = (c1 < reference1);
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

   double vol=0, riskRounded=0, riskBuffered=0;
   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, riskBuffered, why)) return false;

   int autoTpPts=0;
   double effNetRR=0.0;

   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuy, vol, riskRounded, riskBuffered, tp, autoTpPts, effNetRR, why)) return false;
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

bool PlacePendingTrendlineGeneration(const long generation)
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
   return PlaceOrReplacePendingLimitAtEntry(isBuyLimit, entry, false,
                                            TrendlineOrderComment(generation), why);
}

bool PlaceOrReplacePendingTrendline()
{
   return PlacePendingTrendlineGeneration(TrendlineArmGeneration);
}

bool IsTradePlacementAccepted(const uint retcode)
{
   return (retcode == TRADE_RETCODE_DONE ||
           retcode == TRADE_RETCODE_DONE_PARTIAL ||
           retcode == TRADE_RETCODE_PLACED);
}

bool IsPendingLimitTicketMatching(const ulong ticket,
                                   const bool isBuyLimit,
                                   const double entry,
                                   const string requiredComment)
{
   if(ticket == 0 || !OrderSelect(ticket)) return false;
   if(OrderGetString(ORDER_SYMBOL) != _Symbol) return false;
   if((int)OrderGetInteger(ORDER_MAGIC) != MagicNumber) return false;
   long requiredType = isBuyLimit ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;
   if(OrderGetInteger(ORDER_TYPE) != requiredType) return false;
   if(OrderGetString(ORDER_COMMENT) != requiredComment) return false;
   double orderPrice = OrderGetDouble(ORDER_PRICE_OPEN);
   double tolerance = MathMax(_Point * 0.5, 1e-10);
   return (MathAbs(orderPrice - entry) <= tolerance);
}

bool FindMatchingPendingLimit(const bool isBuyLimit,
                               const double entry,
                               const string requiredComment,
                               ulong &ticketOut)
{
   ticketOut = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(IsPendingLimitTicketMatching(ticket, isBuyLimit, entry, requiredComment))
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

struct TrendlineLifecycleRecord
{
   bool     exists;
   long     generation;
   long     blockedGeneration;
   bool     working;
   bool     replacing;
   ulong    ticket;
   datetime expiration;
   long     orderType;
   string   orderComment;
};

// The fingerprint scopes both the terminal-global cache and the indefinite
// FILE_COMMON record to this exact account/server, symbol, magic, strategy and line.
string TrendlineLifecycleFingerprint()
{
   long login = (long)AccountInfoInteger(ACCOUNT_LOGIN);
   string identity = AccountInfoString(ACCOUNT_SERVER) + "|" + (string)login + "|" +
                     _Symbol + "|" + IntegerToString(MagicNumber) + "|trendline|" +
                     g_trendName;
   return ShortStableFingerprint(identity);
}

string TrendlineLifecycleGlobalKey(){ return "TraderTL." + TrendlineLifecycleFingerprint(); }
string TrendlineLifecycleStateFile(){ return "TraderTL." + TrendlineLifecycleFingerprint() + ".state"; }

string TrendlineOrderComment(const long generation)
{
   // 28 characters at the supported generation maximum: safe for common MT5 comment limits.
   return "TL" + TrendlineLifecycleFingerprint() + ":" + (string)generation;
}

bool AcquireTrendlineLifecycleLock(int &lockHandle, string &why)
{
   lockHandle = INVALID_HANDLE;
   const string lockName = "TraderTrendlineLifecycleGate.lck";
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
   why = "Could not acquire the exclusive terminal-common trendline lifecycle gate lock. error=" + IntegerToString(GetLastError());
   return false;
}

bool TrendlineArmGenerationIsValid()
{
   return (TrendlineArmGeneration > 0 && TrendlineArmGeneration <= TRENDLINE_ARM_GENERATION_MAX);
}

void ClearTrendlineLifecycleRecord(TrendlineLifecycleRecord &record)
{
   record.exists = false;
   record.generation = 0;
   record.blockedGeneration = 0;
   record.working = false;
   record.replacing = false;
   record.ticket = 0;
   record.expiration = 0;
   record.orderType = -1;
   record.orderComment = "";
}

long TrendlineHighestHandled(const TrendlineLifecycleRecord &record)
{
   return MathMax(record.generation, record.blockedGeneration);
}

bool TrendlineStateIsWorking(const double state)
{
   return (MathAbs(state - ((double)MathFloor(state) + 0.5)) < 0.000001);
}

bool SameTrendlineLifecycleRecord(const TrendlineLifecycleRecord &left,
                                  const TrendlineLifecycleRecord &right)
{
   return (left.exists == right.exists && left.generation == right.generation &&
           left.blockedGeneration == right.blockedGeneration && left.working == right.working &&
           left.replacing == right.replacing && left.ticket == right.ticket &&
           left.expiration == right.expiration && left.orderType == right.orderType &&
           left.orderComment == right.orderComment);
}

bool IsUnsignedDecimalText(const string value)
{
   int length = StringLen(value);
   if(length <= 0 || length > 19) return false;
   if(length == 19 && StringCompare(value, "9223372036854775807") > 0) return false;
   for(int i = 0; i < length; i++)
   {
      ushort ch = StringGetCharacter(value, i);
      if(ch < 48 || ch > 57) return false;
   }
   return true;
}

bool ParseNonnegativeLongStrict(const string value, long &parsed)
{
   parsed = 0;
   if(!IsUnsignedDecimalText(value)) return false;
   parsed = StringToInteger(value);
   return parsed >= 0;
}

bool ReadTrendlineLifecycleRecord(TrendlineLifecycleRecord &record, string &why)
{
   ClearTrendlineLifecycleRecord(record);
   string fileName = TrendlineLifecycleStateFile();
   if(!FileIsExist(fileName, FILE_COMMON)){ why = ""; return true; }
   int handle = FileOpen(fileName, FILE_READ | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   { why = "Could not open durable trendline lifecycle state. error=" + IntegerToString(GetLastError()); return false; }
   int fileSize = (int)FileSize(handle);
   string line = (fileSize > 0 ? FileReadString(handle, fileSize) : "");
   bool atEnd = FileIsEnding(handle);
   FileClose(handle);
   if(!atEnd || StringFind(line, "\r") >= 0 || StringFind(line, "\n") >= 0)
   { why = "Durable trendline lifecycle state contains trailing or extra content."; return false; }
   string fields[];
   int fieldCount = StringSplit(line, '|', fields);
   if(fieldCount > 0 && fields[0] == "TraderTLV2")
   { why = "Legacy V2 trendline lifecycle state has no immutable order type; it is left disarmed."; return false; }
   if(fieldCount != 10 || fields[0] != "TraderTLV3" || fields[1] != TrendlineLifecycleFingerprint())
   { why = "Durable trendline lifecycle state is corrupt or belongs to another lifecycle."; return false; }
   if((fields[4] != "0" && fields[4] != "1") || (fields[5] != "0" && fields[5] != "1"))
   { why = "Durable trendline lifecycle Boolean fields must be exactly 0 or 1."; return false; }
   long generation=0, blocked=0, ticketValue=0, expirationValue=0, orderType=0;
   if(!ParseNonnegativeLongStrict(fields[2], generation) ||
      !ParseNonnegativeLongStrict(fields[3], blocked) ||
      !ParseNonnegativeLongStrict(fields[6], ticketValue) ||
      !ParseNonnegativeLongStrict(fields[7], expirationValue) ||
      !ParseNonnegativeLongStrict(fields[8], orderType))
   { why = "Durable trendline lifecycle numeric fields are malformed or overflowed."; return false; }
   record.generation = generation;
   record.blockedGeneration = blocked;
   record.working = (fields[4] == "1");
   record.replacing = (fields[5] == "1");
   record.ticket = (ulong)ticketValue;
   record.expiration = (datetime)expirationValue;
   record.orderType = orderType;
   record.orderComment = fields[9];
   if(record.generation <= 0 || record.blockedGeneration < record.generation ||
      record.blockedGeneration > TRENDLINE_ARM_GENERATION_MAX ||
      record.orderComment != TrendlineOrderComment(record.generation) ||
      (record.orderType != ORDER_TYPE_BUY_LIMIT && record.orderType != ORDER_TYPE_SELL_LIMIT) ||
      (record.replacing && (!record.working || record.ticket == 0)))
   { why = "Durable trendline lifecycle state is contradictory; the line is left disarmed for safety."; return false; }
   record.exists = true;
   why = "";
   return true;
}

bool WriteTrendlineLifecycleRecord(const TrendlineLifecycleRecord &record, string &why)
{
   string fileName = TrendlineLifecycleStateFile();
   ResetLastError();
   int handle = FileOpen(fileName, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   { why = "Could not write durable trendline lifecycle state. error=" + IntegerToString(GetLastError()); return false; }
   string line = "TraderTLV3|" + TrendlineLifecycleFingerprint() + "|" +
                 (string)record.generation + "|" + (string)record.blockedGeneration + "|" +
                 (record.working ? "1" : "0") + "|" + (record.replacing ? "1" : "0") + "|" +
                 (string)record.ticket + "|" + (string)record.expiration + "|" +
                 (string)record.orderType + "|" + record.orderComment;
   ResetLastError();
   uint written = FileWriteString(handle, line);
   int writeError = GetLastError();
   ResetLastError();
   FileFlush(handle);
   int flushError = GetLastError();
   FileClose(handle);
   if(written != (uint)StringLen(line) || writeError != 0 || flushError != 0)
   { why = "Durable trendline lifecycle write was short or failed."; return false; }
   TrendlineLifecycleRecord verified;
   if(!ReadTrendlineLifecycleRecord(verified, why) || !SameTrendlineLifecycleRecord(verified, record))
   { if(why == "") why = "Durable trendline lifecycle verification mismatch."; return false; }
   why = "";
   return true;
}

void LogTrendlineLifecycle(const string state, const string source, const string detail)
{
   if(g_trendlineLifecycleStatus == state) return;
   g_trendlineLifecycleStatus = state;
   Print(EA_COMMENT, ": mode=trendline state=", state,
         " source=", source,
         " line=", g_trendName,
         " generation=", (string)TrendlineArmGeneration,
         " ", detail);
}

bool PersistTrendlineLifecycleRecord(const TrendlineLifecycleRecord &expected,
                                     const TrendlineLifecycleRecord &replacement,
                                     string &why)
{
   int lockHandle = INVALID_HANDLE;
   if(!AcquireTrendlineLifecycleLock(lockHandle, why)) return false;
   TrendlineLifecycleRecord current;
   string readWhy = "";
   bool readable = ReadTrendlineLifecycleRecord(current, readWhy);
   bool stored = readable && SameTrendlineLifecycleRecord(current, expected) &&
                 WriteTrendlineLifecycleRecord(replacement, why);
   if(stored)
   {
      GlobalVariableSet(TrendlineLifecycleGlobalKey(),
                        replacement.working ? (double)replacement.generation + 0.5
                                            : (double)TrendlineHighestHandled(replacement));
      GlobalVariablesFlush();
   }
   FileClose(lockHandle);
   if(!stored)
   {
      if(!readable) why = readWhy;
      else if(why == "") why = "The durable trendline lifecycle changed concurrently; no order will be sent.";
      return false;
   }
   why = "";
   return true;
}

bool LoadTrendlineLifecycleRecord(TrendlineLifecycleRecord &record, string &why)
{
   if(!ReadTrendlineLifecycleRecord(record, why)) return false;
   if(record.exists) return true; // Durable state always takes precedence over the expiring cache.
   string key = TrendlineLifecycleGlobalKey();
   if(!GlobalVariableCheck(key)){ why = ""; return true; }
   double legacyState = GlobalVariableGet(key);
   if(legacyState < 0.0 || legacyState > (double)TRENDLINE_ARM_GENERATION_MAX + 0.5)
   { why = "Expired-cache migration found invalid trendline state; the line is left disarmed for safety."; return false; }
   ClearTrendlineLifecycleRecord(record);
   // V2/cache-only records lack immutable order type and are not safe to migrate.
   why = "Legacy trendline lifecycle state has no immutable order type; it is left disarmed for safety.";
   return false;
}

bool IsTrendlinePendingIdentity(const ulong ticket, const TrendlineLifecycleRecord &record)
{
   if(ticket == 0 || record.orderComment == "" || !OrderSelect(ticket)) return false;
   if(OrderGetString(ORDER_SYMBOL) != _Symbol || (int)OrderGetInteger(ORDER_MAGIC) != MagicNumber) return false;
   long type = OrderGetInteger(ORDER_TYPE);
   if(type != record.orderType) return false;
   if(OrderGetString(ORDER_COMMENT) != record.orderComment) return false;
   return true;
}

bool IsExactTrendlinePending(const ulong ticket, const TrendlineLifecycleRecord &record)
{
   return (record.ticket > 0 && ticket == record.ticket && IsTrendlinePendingIdentity(ticket, record));
}

int CountTrendlinePendingIdentity(const TrendlineLifecycleRecord &record, ulong &ticketOut)
{
   ticketOut = 0;
   int matches = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(!IsTrendlinePendingIdentity(ticket, record)) continue;
      ticketOut = ticket;
      matches++;
   }
   return matches;
}

bool FindExactTrendlinePosition(const TrendlineLifecycleRecord &record, ulong &ticketOut)
{
   ticketOut = 0;
   if(record.ticket == 0 || !HistoryOrderSelect(record.ticket)) return false;
   long positionId = HistoryOrderGetInteger(record.ticket, ORDER_POSITION_ID);
   if(positionId <= 0) return false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) != _Symbol) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetInteger(POSITION_IDENTIFIER) != positionId) continue;
      ticketOut = (ulong)PositionGetInteger(POSITION_TICKET);
      return ticketOut > 0;
   }
   return false;
}

void ResetTrendlinePlacementMetadata()
{
   g_ticket = 0;
   g_armStartTime = 0;
   g_expireAt = 0;
}

bool PrepareWorkingTrendlineTerms(const TrendlineLifecycleRecord &record,
                                  double &entry, double &sl, double &tp, double &vol, string &why)
{
   bool isBuyLimit = (record.orderType == ORDER_TYPE_BUY_LIMIT);
   entry = NormalizePrice(GetTrendlinePriceAtTime(g_trendName, iTime(_Symbol, _Period, 0)));
   if(entry <= 0.0 || !ValidateTradingReadiness(isBuyLimit, why) || !IsLimitPriceValid(entry, isBuyLimit, why)) return false;
   sl=0.0; tp=0.0; vol=0.0;
   double riskRounded=0.0, riskBuffered=0.0;
   if(!BuildSLFromDistance(entry, isBuyLimit, sl, why) ||
      !ComputeVolumeFromRisk(entry, sl, vol, riskRounded, riskBuffered, why) ||
      !ValidateVolumeForBroker(vol, why)) return false;
   int autoTpPts=0; double effNetRR=0.0;
   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuyLimit, vol, riskRounded, riskBuffered, tp, autoTpPts, effNetRR, why)) return false;
   }
   else if(!BuildTPManualFromDistance(entry, isBuyLimit, tp, why)) return false;
   why = "";
   return true;
}

bool PricesMateriallyDiffer(const double left, const double right)
{
   return MathAbs(left - right) > MathMax(_Point * 0.5, 1e-10);
}

bool TrendlinePendingMatchesPreparedTerms(const ulong ticket,
                                          const TrendlineLifecycleRecord &record,
                                          const double entry, const double sl, const double tp,
                                          const double volume)
{
   if(!IsTrendlinePendingIdentity(ticket, record)) return false;
   double volumeTolerance = MathMax(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP) * 0.5, 1e-10);
   return (!PricesMateriallyDiffer(OrderGetDouble(ORDER_PRICE_OPEN), entry) &&
           !PricesMateriallyDiffer(OrderGetDouble(ORDER_SL), sl) &&
           !PricesMateriallyDiffer(OrderGetDouble(ORDER_TP), tp) &&
           MathAbs(OrderGetDouble(ORDER_VOLUME_INITIAL) - volume) <= volumeTolerance &&
           (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION) == record.expiration);
}

bool ModifyWorkingTrendlinePending(const ulong ticket, const TrendlineLifecycleRecord &record,
                                   const double entry, const double sl, const double tp,
                                   const double volume, bool &volumeChanged, string &why)
{
   volumeChanged = false;
   if(!IsExactTrendlinePending(ticket, record))
   { why = "Exact lifecycle ticket is not observable before maintenance."; return false; }
   double currentVolume = OrderGetDouble(ORDER_VOLUME_INITIAL);
   double volumeTolerance = MathMax(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP) * 0.5, 1e-10);
   volumeChanged = (MathAbs(currentVolume - volume) > volumeTolerance);
   ENUM_ORDER_TYPE_TIME typeTime = record.expiration > 0 ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC;
   bool valuesChanged = PricesMateriallyDiffer(OrderGetDouble(ORDER_PRICE_OPEN), entry) ||
                        PricesMateriallyDiffer(OrderGetDouble(ORDER_SL), sl) ||
                        PricesMateriallyDiffer(OrderGetDouble(ORDER_TP), tp) ||
                        ((datetime)OrderGetInteger(ORDER_TIME_EXPIRATION) != record.expiration);
   if(volumeChanged || !valuesChanged) { why = ""; return true; }
   bool modified = trade.OrderModify(ticket, entry, sl, tp, typeTime, record.expiration, 0.0);
   uint retcode = trade.ResultRetcode();
   if(!modified || (retcode != TRADE_RETCODE_DONE && retcode != TRADE_RETCODE_NO_CHANGES))
   { why = "Broker rejected in-place trendline maintenance. retcode=" + (string)trade.ResultRetcode(); return false; }
   if(!IsExactTrendlinePending(ticket, record) ||
      !TrendlinePendingMatchesPreparedTerms(ticket, record, entry, sl, tp, volume))
   { why = "Modified trendline order is not exactly observable; no replacement will be sent."; return false; }
   why = "";
   return true;
}

bool DeleteExactTrendlinePending(const TrendlineLifecycleRecord &record, string &why)
{
   if(record.ticket == 0 || !IsExactTrendlinePending(record.ticket, record))
   { why = "Exact lifecycle pending ticket is not observable for deletion."; return false; }
   bool deleted = trade.OrderDelete(record.ticket);
   if(!deleted || trade.ResultRetcode() != TRADE_RETCODE_DONE)
   { why = "Broker rejected exact lifecycle cancellation. retcode=" + (string)trade.ResultRetcode(); return false; }
   if(OrderSelect(record.ticket))
   { why = "Exact lifecycle deletion was not confirmed absent."; return false; }
   why = "";
   return true;
}

enum ExactReplacementResult
{
   EXACT_REPLACEMENT_ACCEPTED = 0,
   EXACT_REPLACEMENT_CONSUMED = 1,
   EXACT_REPLACEMENT_UNRESOLVED = 2
};

ExactReplacementResult SubmitExactTrendlineReplacement(const TrendlineLifecycleRecord &record,
                                                        const double entry, const double sl,
                                                        const double tp, const double volume,
                                                        ulong &newTicket, string &why)
{
   newTicket = 0;
   ENUM_ORDER_TYPE_TIME typeTime = record.expiration > 0 ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC;
   bool sendOk = (record.orderType == ORDER_TYPE_BUY_LIMIT)
      ? trade.BuyLimit(volume, entry, _Symbol, sl, tp, typeTime, record.expiration, record.orderComment)
      : trade.SellLimit(volume, entry, _Symbol, sl, tp, typeTime, record.expiration, record.orderComment);
   uint retcode = trade.ResultRetcode();
   ulong resultTicket = (ulong)trade.ResultOrder();
   if(sendOk && IsTradePlacementAccepted(retcode) && resultTicket > 0)
   {
      if(TrendlinePendingMatchesPreparedTerms(resultTicket, record, entry, sl, tp, volume))
      {
         newTicket = resultTicket;
         why = "";
         return EXACT_REPLACEMENT_ACCEPTED;
      }
      why = "Replacement acceptance is ambiguous because the exact result ticket is not observable with all prepared terms.";
      return EXACT_REPLACEMENT_UNRESOLVED;
   }
   ulong observed = 0;
   int visibleMatches = CountTrendlinePendingIdentity(record, observed);
   if(IsDefinitePendingRejectionRetcode(retcode) &&
      resultTicket == 0 && visibleMatches == 0)
   {
      why = "Replacement was definitely rejected and no exact lifecycle order exists.";
      return EXACT_REPLACEMENT_CONSUMED;
   }
   why = "Replacement outcome is ambiguous; manual broker reconciliation is required.";
   return EXACT_REPLACEMENT_UNRESOLVED;
}

bool DeleteObservedExactReplacement(const ulong ticket,
                                    const TrendlineLifecycleRecord &record,
                                    const double entry, const double sl, const double tp,
                                    const double volume, string &why)
{
   if(ticket == 0 || !TrendlinePendingMatchesPreparedTerms(ticket, record, entry, sl, tp, volume))
   { why = "New replacement ticket cannot be exactly verified for cleanup."; return false; }
   bool deleted = trade.OrderDelete(ticket);
   if(!deleted || trade.ResultRetcode() != TRADE_RETCODE_DONE || OrderSelect(ticket))
   { why = "New exact replacement cleanup or absence confirmation failed."; return false; }
   why = "";
   return true;
}

ExactReplacementResult ReplaceExactTrendlinePendingForVolume(TrendlineLifecycleRecord &record,
                                                              const double entry, const double sl,
                                                              const double tp, const double volume,
                                                              string &why)
{
   if(record.generation <= 0 ||
      (record.orderType != ORDER_TYPE_BUY_LIMIT && record.orderType != ORDER_TYPE_SELL_LIMIT) ||
      record.orderComment != TrendlineOrderComment(record.generation))
   {
      why = "Replacement preflight rejected an invalid immutable lifecycle identity.";
      return EXACT_REPLACEMENT_UNRESOLVED;
   }
   TrendlineLifecycleRecord transition = record;
   transition.replacing = true;
   if(!PersistTrendlineLifecycleRecord(record, transition, why)) return EXACT_REPLACEMENT_UNRESOLVED;
   if(!IsExactTrendlinePending(transition.ticket, transition))
   { why = "Original exact lifecycle ticket changed before replacement deletion."; return EXACT_REPLACEMENT_UNRESOLVED; }
   if(!DeleteExactTrendlinePending(transition, why)) return EXACT_REPLACEMENT_UNRESOLVED;
   ulong newTicket = 0;
   ExactReplacementResult outcome = SubmitExactTrendlineReplacement(transition, entry, sl, tp, volume, newTicket, why);
   if(outcome == EXACT_REPLACEMENT_CONSUMED)
   {
      string consumedReason = why;
      TrendlineLifecycleRecord consumed = transition;
      consumed.working = false;
      consumed.replacing = false;
      consumed.ticket = 0;
      if(!PersistTrendlineLifecycleRecord(transition, consumed, why)) return EXACT_REPLACEMENT_UNRESOLVED;
      record = consumed;
      why = consumedReason;
      return EXACT_REPLACEMENT_CONSUMED;
   }
   if(outcome != EXACT_REPLACEMENT_ACCEPTED) return EXACT_REPLACEMENT_UNRESOLVED;
   TrendlineLifecycleRecord replaced = transition;
   replaced.replacing = false;
   replaced.ticket = newTicket;
   if(!PersistTrendlineLifecycleRecord(transition, replaced, why))
   {
      string cleanupWhy = "";
      if(DeleteObservedExactReplacement(newTicket, transition, entry, sl, tp, volume, cleanupWhy))
      {
         TrendlineLifecycleRecord consumed = transition;
         consumed.working = false;
         consumed.replacing = false;
         consumed.ticket = 0;
         PersistTrendlineLifecycleRecord(transition, consumed, cleanupWhy);
         why = "New exact replacement was removed after durable-ticket persistence failed; lifecycle remains fail-closed.";
      }
      else
         why = "New exact replacement persistence and cleanup were not both confirmed; manual broker reconciliation is required.";
      return EXACT_REPLACEMENT_UNRESOLVED;
   }
   record = replaced;
   why = "";
   return EXACT_REPLACEMENT_ACCEPTED;
}

void CancelExactTrendlineLifecyclePending(const string source)
{
   TrendlineLifecycleRecord record;
   string why = "";
   if(!LoadTrendlineLifecycleRecord(record, why) || !record.exists || !record.working) return;
   if(record.replacing)
   { LogTrendlineLifecycle("cancel_unresolved", source, "reason=Replacement transition requires manual broker reconciliation."); return; }
   if(record.ticket > 0)
   {
      if(!IsExactTrendlinePending(record.ticket, record))
      { LogTrendlineLifecycle("cancel_unresolved", source, "reason=Persisted exact ticket is not observable; no other ticket will be substituted."); return; }
   }
   else
   {
      ulong observedTicket = 0;
      int matches = CountTrendlinePendingIdentity(record, observedTicket);
      if(matches > 1)
      { LogTrendlineLifecycle("cancel_unresolved", source, "reason=Multiple lifecycle-identity orders are visible; cancellation is ambiguous."); return; }
      if(matches == 1)
      {
         TrendlineLifecycleRecord observed = record;
         observed.ticket = observedTicket;
         if(!OrderSelect(observedTicket))
         { LogTrendlineLifecycle("cancel_unresolved", source, "reason=Observed lifecycle ticket disappeared before persistence."); return; }
         observed.expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
         if(!PersistTrendlineLifecycleRecord(record, observed, why))
         { LogTrendlineLifecycle("cancel_unresolved", source, "reason=" + why); return; }
         record = observed;
      }
      else
      {
         TrendlineLifecycleRecord consumedReserved = record;
         consumedReserved.working = false;
         if(!PersistTrendlineLifecycleRecord(record, consumedReserved, why))
            LogTrendlineLifecycle("disarmed", source, "reason=" + why);
         else
            LogTrendlineLifecycle("consumed", source, "reason=Reserved lifecycle had no broker order to cancel.");
         return;
      }
   }
   if(!DeleteExactTrendlinePending(record, why))
   { LogTrendlineLifecycle("cancel_failed", source, "reason=" + why); return; }
   TrendlineLifecycleRecord consumed = record;
   consumed.working = false;
   consumed.replacing = false;
   if(!PersistTrendlineLifecycleRecord(record, consumed, why))
      LogTrendlineLifecycle("disarmed", source, "reason=" + why);
   else
      LogTrendlineLifecycle("consumed", source, "reason=Exact lifecycle pending order was cancelled.");
}

void MaintainTrendlineLifecycle(const string source, const bool allowNewArm=true)
{
   if(g_trendName == "" || !TrendlineExists(g_trendName)) return;

   TrendlineLifecycleRecord record;
   string why = "";
   if(!LoadTrendlineLifecycleRecord(record, why))
   {
      LogTrendlineLifecycle("disarmed", source, "reason=" + why);
      return;
   }

   if(record.exists && record.working)
   {
      if(record.replacing)
      {
         LogTrendlineLifecycle("tracking_failed", source,
                               "reason=An earlier exact delete-confirm-replace transition is unresolved; no automatic retry is allowed.");
         return;
      }
      if(allowNewArm && TrendlineArmGenerationIsValid() &&
         TrendlineArmGeneration > record.blockedGeneration)
      {
         TrendlineLifecycleRecord rejected = record;
         rejected.blockedGeneration = TrendlineArmGeneration;
         if(!PersistTrendlineLifecycleRecord(record, rejected, why))
         {
            LogTrendlineLifecycle("disarmed", source, "reason=" + why);
            return;
         }
         record = rejected;
         LogTrendlineLifecycle("active_rearm_rejected", source,
                               "reason=A higher generation entered during active work is consumed and will not queue.");
      }
      ulong pendingTicket = 0;
      bool hasPending = false;
      if(record.ticket > 0)
      {
         pendingTicket = record.ticket;
         hasPending = IsExactTrendlinePending(pendingTicket, record);
      }
      else
      {
         int identityMatches = CountTrendlinePendingIdentity(record, pendingTicket);
         if(identityMatches > 1)
         {
            LogTrendlineLifecycle("tracking_failed", source,
                                  "reason=Multiple orders match the lifecycle identity; manual broker reconciliation is required.");
            return;
         }
         if(identityMatches == 1)
         {
            TrendlineLifecycleRecord observed = record;
            observed.ticket = pendingTicket;
            if(!OrderSelect(pendingTicket))
            { LogTrendlineLifecycle("tracking_failed", source, "reason=Observed lifecycle ticket disappeared before persistence."); return; }
            observed.expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
            if(!PersistTrendlineLifecycleRecord(record, observed, why))
            { LogTrendlineLifecycle("disarmed", source, "reason=" + why); return; }
            record = observed;
            hasPending = true;
         }
      }
      if(hasPending)
      {
         bool volumeChanged = false;
         bool newBar = IsNewBar();
         double desiredEntry=0.0, desiredSL=0.0, desiredTP=0.0, desiredVolume=0.0;
         if(newBar && !g_trendlineTrackingFailed &&
            (!PrepareWorkingTrendlineTerms(record, desiredEntry, desiredSL, desiredTP, desiredVolume, why) ||
             !ModifyWorkingTrendlinePending(pendingTicket, record, desiredEntry, desiredSL, desiredTP,
                                            desiredVolume, volumeChanged, why)))
         {
            g_trendlineTrackingFailed = true;
            LogTrendlineLifecycle("tracking_failed", source, "reason=" + why + "; keeping the last confirmed pending order.");
            return;
         }
         if(newBar && !g_trendlineTrackingFailed && volumeChanged)
         {
            ExactReplacementResult replaceResult = ReplaceExactTrendlinePendingForVolume(
               record, desiredEntry, desiredSL, desiredTP, desiredVolume, why);
            if(replaceResult == EXACT_REPLACEMENT_CONSUMED)
            { LogTrendlineLifecycle("consumed", source, "reason=" + why + "; a later higher generation is required."); return; }
            if(replaceResult != EXACT_REPLACEMENT_ACCEPTED)
            {
               g_trendlineTrackingFailed = true;
               LogTrendlineLifecycle("tracking_failed", source, "reason=" + why + "; no replacement retry is allowed.");
               return;
            }
            pendingTicket = record.ticket;
         }
         LogTrendlineLifecycle("working", source, "kind=pending ticket=" + (string)pendingTicket);
         return;
      }
      ulong positionTicket = 0;
      if(FindExactTrendlinePosition(record, positionTicket))
      { LogTrendlineLifecycle("working", source, "kind=position ticket=" + (string)positionTicket); return; }
      TrendlineLifecycleRecord completed = record;
      completed.working = false;
      if(!PersistTrendlineLifecycleRecord(record, completed, why))
      { LogTrendlineLifecycle("disarmed", source, "reason=" + why); return; }
      record = completed;
      LogTrendlineLifecycle("consumed", source, "reason=The exact pending intent or resulting position is no longer active.");
   }

   // Desktop-control maintenance may track/complete existing work, but only an
   // explicitly consumed desktop command may enter the new-arm path below.
   if(!allowNewArm) return;

   if(!TrendlineArmGenerationIsValid())
   {
      LogTrendlineLifecycle("disarmed", source,
                            "reason=Set TrendlineArmGeneration to a new positive integer to arm this line.");
      return;
   }

   if(record.exists && TrendlineHighestHandled(record) >= TrendlineArmGeneration)
   {
      LogTrendlineLifecycle("consumed", source,
                            "reason=This arm generation was already used; increase TrendlineArmGeneration to re-arm.");
      return;
   }

   ulong unrelatedPending = 0;
   if(FindAnyPendingLimitForEA(unrelatedPending))
   {
      LogTrendlineLifecycle("blocked_unrelated", source,
                            "reason=Another same-symbol/same-magic pending order exists and is not this lifecycle.");
      return;
   }
   bool manualRearm = record.exists;
   LogTrendlineLifecycle("armed", source,
                         manualRearm
                         ? "event=manually_rearmed; this new generation permits one trade cycle."
                         : "event=armed; this generation permits one trade cycle.");

   // Reserve before calling the broker.  A restart, ambiguous broker response,
   // or persistence uncertainty therefore fails closed rather than duplicating.
   TrendlineLifecycleRecord armed = record;
   armed.exists = true;
   armed.generation = TrendlineArmGeneration;
   armed.blockedGeneration = TrendlineArmGeneration;
   armed.working = true;
   armed.replacing = false;
   armed.ticket = 0;
   armed.expiration = 0;
   armed.orderType = (Direction == TL_BUY_LIMIT ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT);
   armed.orderComment = TrendlineOrderComment(TrendlineArmGeneration);
   if(!PersistTrendlineLifecycleRecord(record, armed, why))
   {
      LogTrendlineLifecycle("disarmed", source, "reason=" + why);
      return;
   }

   ResetTrendlinePlacementMetadata();
   if(PlaceOrReplacePendingTrendline())
   {
      TrendlineLifecycleRecord reserved = armed;
      armed.ticket = g_ticket;
      if(OrderSelect(armed.ticket)) armed.expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
      if(!PersistTrendlineLifecycleRecord(reserved, armed, why))
      {
         LogTrendlineLifecycle("disarmed", source, "reason=Accepted order retained, but durable ticket persistence failed: " + why);
         return;
      }
      LogTrendlineLifecycle("working", source,
                            "reason=The one-shot pending-order intent was accepted.");
      return;
   }

   TrendlineLifecycleRecord consumed = armed;
   consumed.working = false;
   if(!PersistTrendlineLifecycleRecord(armed, consumed, why))
   {
      LogTrendlineLifecycle("disarmed", source, "reason=" + why);
      return;
   }
   LogTrendlineLifecycle("consumed", source,
                         "reason=The one-shot pending-order intent was rejected or could not be confirmed.");
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

bool IsDefinitePendingRejectionRetcode(const uint retcode)
{
   return (retcode == TRADE_RETCODE_REJECT ||
           retcode == TRADE_RETCODE_INVALID ||
           retcode == TRADE_RETCODE_INVALID_VOLUME ||
           retcode == TRADE_RETCODE_INVALID_PRICE ||
           retcode == TRADE_RETCODE_INVALID_STOPS ||
           retcode == TRADE_RETCODE_TRADE_DISABLED ||
           retcode == TRADE_RETCODE_NO_MONEY ||
           retcode == TRADE_RETCODE_INVALID_EXPIRATION ||
           retcode == TRADE_RETCODE_INVALID_FILL ||
           retcode == TRADE_RETCODE_ONLY_REAL ||
           retcode == TRADE_RETCODE_LIMIT_ORDERS ||
           retcode == TRADE_RETCODE_LIMIT_VOLUME ||
           retcode == TRADE_RETCODE_INVALID_ORDER ||
           retcode == TRADE_RETCODE_LIMIT_POSITIONS);
}

bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,
                                       const double rawEntry,
                                       const bool allowReplace,
                                       const string orderComment,
                                       string &why)
{
   g_lastPendingFailureStructural = false;
   g_lastPendingAcceptanceMismatch = false;
   g_lastPendingBrokerAttempted = false;
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
      if(IsPendingLimitTicketMatching(g_ticket, isBuyLimit, entry, orderComment)) return true;
      g_ticket = 0;
   }

   double sl=0.0, tp=0.0, vol=0.0, riskRounded=0.0, riskBuffered=0.0;

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

   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, riskBuffered, why))
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
      if(!ComputeAutoTP_NetRR(entry, isBuyLimit, vol, riskRounded, riskBuffered, tp, autoTpPts, effNetRR, why))
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
   g_lastPendingBrokerAttempted = true;
   if(isBuyLimit) sendOk = trade.BuyLimit(vol, entry, _Symbol, sl, tp, tt, exp, orderComment);
   else           sendOk = trade.SellLimit(vol, entry, _Symbol, sl, tp, tt, exp, orderComment);

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
   if(IsPendingLimitTicketMatching(orderTicket, isBuyLimit, entry, orderComment))
      observedTicket = orderTicket;
   else
      FindMatchingPendingLimit(isBuyLimit, entry, orderComment, observedTicket);
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
   bool placed = PlaceOrReplacePendingLimitAtEntry(isBuyLimit, StandardLimitEntryPrice, false, EA_COMMENT, why);
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
   if(FindMatchingPendingLimit(isBuyLimit, entry, EA_COMMENT, matchingTicket))
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

// ---------- Separate desktop Trader control ----------
struct DesktopTraderCommand
{
   int      protocolVersion;
   string   instanceId;
   string   commandId;
   string   action;
   datetime createdAt;
};

struct DesktopTraderResult
{
   string commandId;
   string action;
   string outcome;
   string reason;
   uint   retcode;
   ulong  ticket;
};

int SafeTraderControlRefreshMs()
{
   if(TraderControlWindowRefreshMs < 100) return 100;
   if(TraderControlWindowRefreshMs > 5000) return 5000;
   return TraderControlWindowRefreshMs;
}

string TraderControlNamespace()
{
   return "TraderControl.v1." + g_traderControlInstanceId;
}

string TraderControlStatusFile(){ return TraderControlNamespace() + ".status.json"; }
string TraderControlCommandFile(){ return TraderControlNamespace() + ".command.json"; }
string TraderControlResultFile(){ return TraderControlNamespace() + ".result.json"; }
string TraderControlGateFile(){ return TraderControlNamespace() + ".command-gate.lck"; }
string TraderControlActiveTrendlineFile(){ return TraderControlNamespace() + ".active-trendline.txt"; }
string TraderControlConsumedFile(const string commandId)
{
   return TraderControlNamespace() + ".consumed." + commandId + ".json";
}

string TraderControlInstanceId()
{
   string identity = AccountInfoString(ACCOUNT_SERVER) + "|" +
                     (string)AccountInfoInteger(ACCOUNT_LOGIN) + "|" +
                     (string)ChartID() + "|" + _Symbol + "|" +
                     IntegerToString(MagicNumber);
   return ShortStableFingerprint(identity);
}

string TraderControlCommonFilesPath()
{
   string common = TerminalInfoString(TERMINAL_COMMONDATA_PATH);
   if(common == "") return "";
   return common + "\\Files";
}

string TraderControlQuoteArg(const string value)
{
   string escaped = value;
   StringReplace(escaped, "\"", "\\\"");
   return "\"" + escaped + "\"";
}

string TraderControlDirectoryName(const string path)
{
   string normalized = path;
   StringReplace(normalized, "/", "\\");
   for(int i = StringLen(normalized) - 1; i >= 0; i--)
      if(StringGetCharacter(normalized, i) == 92)
         return StringSubstr(normalized, 0, i);
   return "";
}

bool TraderControlConfiguredFileExists(const string path)
{
   ResetLastError();
   uint attributes = GetFileAttributesW(path);
   return (attributes != TRADER_CONTROL_INVALID_FILE_ATTRIBUTES &&
           (attributes & TRADER_CONTROL_FILE_ATTRIBUTE_DIRECTORY) == 0);
}

bool WriteVerifiedCommonText(const string fileName, const string contents, string &why)
{
   ResetLastError();
   int handle = FileOpen(fileName, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
   { why = "Could not open FILE_COMMON record " + fileName + ". error=" + IntegerToString(GetLastError()); return false; }
   ResetLastError();
   uint written = FileWriteString(handle, contents);
   int writeError = GetLastError();
   FileFlush(handle);
   int flushError = GetLastError();
   FileClose(handle);
   if(written != (uint)StringLen(contents) || writeError != 0 || flushError != 0)
   { why = "FILE_COMMON write/flush failed for " + fileName + "."; return false; }

   handle = FileOpen(fileName, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
   { why = "Could not reopen FILE_COMMON record " + fileName + " for verification."; return false; }
   int size = (int)FileSize(handle);
   string verified = (size > 0 ? FileReadString(handle, size) : "");
   FileClose(handle);
   if(verified != contents)
   { why = "FILE_COMMON verification mismatch for " + fileName + "."; return false; }
   why = "";
   return true;
}

bool WriteDesktopTraderStatus()
{
   if(!UseDesktopTraderControls || g_traderControlInstanceId == "") return true;
   datetime now = TimeGMT();
   string payload = "{";
   payload += "\"account_login\":" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + ",";
   payload += "\"account_server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\",";
   payload += "\"chart_id\":" + (string)ChartID() + ",";
   payload += "\"connected\":" + (TerminalInfoInteger(TERMINAL_CONNECTED) ? "true" : "false") + ",";
   payload += "\"control_ready\":" + (g_traderControlReady ? "true" : "false") + ",";
   payload += "\"ea_version\":\"" + EA_VERSION + "\",";
   payload += "\"fresh_for_seconds\":" + IntegerToString(TRADER_CONTROL_STATUS_FRESH_SECONDS) + ",";
   payload += "\"instance_id\":\"" + g_traderControlInstanceId + "\",";
   payload += "\"magic_number\":" + IntegerToString(MagicNumber) + ",";
   payload += "\"orders_enabled\":" + (OrdersEnabled ? "true" : "false") + ",";
   payload += "\"protocol_version\":" + IntegerToString(TRADER_CONTROL_PROTOCOL_VERSION) + ",";
   payload += "\"reason\":\"" + JsonEscape(g_traderControlReason) + "\",";
   payload += "\"symbol\":\"" + JsonEscape(_Symbol) + "\",";
   payload += "\"updated_at\":" + (string)now + "}";
   string why = "";
   if(WriteVerifiedCommonText(TraderControlStatusFile(), payload, why)) return true;
   Print(EA_COMMENT, ": desktop control status write failed: ", why);
   return false;
}

bool LaunchDesktopTraderControls(string &why)
{
   if(!LaunchDesktopTraderWindow)
   { why = "Automatic launch disabled; waiting for a manually started matching Trader Controls window."; return true; }
   if(!MQLInfoInteger(MQL_DLLS_ALLOWED))
   { why = "Desktop Trader Controls require Allow DLL imports for automatic launch; commands are disabled."; return false; }

   string python = TrimText(PythonExecutable);
   string script = TrimText(TraderControlWindowScriptPath);
   string common = TraderControlCommonFilesPath();
   if(python == "" || !TraderControlConfiguredFileExists(python))
   { why = "Configured PythonExecutable is missing or invalid: " + python; return false; }
   if(script == "" || !TraderControlConfiguredFileExists(script))
   { why = "Configured TraderControlWindowScriptPath is missing or invalid: " + script; return false; }
   if(common == "")
   { why = "TERMINAL_COMMONDATA_PATH is unavailable; desktop commands are disabled."; return false; }

   string params = TraderControlQuoteArg(script) +
                   " --common-dir " + TraderControlQuoteArg(common) +
                   " --instance-id " + TraderControlQuoteArg(g_traderControlInstanceId) +
                   " --account-login " + (string)AccountInfoInteger(ACCOUNT_LOGIN) +
                   " --account-server " + TraderControlQuoteArg(AccountInfoString(ACCOUNT_SERVER)) +
                   " --chart-id " + (string)ChartID() +
                   " --symbol " + TraderControlQuoteArg(_Symbol) +
                   " --magic " + IntegerToString(MagicNumber) +
                   " --ea-version " + TraderControlQuoteArg(EA_VERSION) +
                   " --refresh-ms " + IntegerToString(SafeTraderControlRefreshMs());
   ResetLastError();
   long result = ShellExecuteW(0, "open", python, params,
                               TraderControlDirectoryName(script), TRADER_CONTROL_SW_SHOWNORMAL);
   if(result <= 32)
   {
      why = "ShellExecuteW rejected the Trader Controls launch. result=" + (string)result +
            " error=" + IntegerToString(GetLastError());
      return false;
   }
   why = "Trader Controls launch accepted; waiting for fresh instance-scoped commands.";
   return true;
}

bool ReadDesktopJsonString(const string payload, const string key, string &value)
{
   string token = "\"" + key + "\":\"";
   int at = StringFind(payload, token);
   if(at < 0 || StringFind(payload, token, at + StringLen(token)) >= 0) return false;
   int start = at + StringLen(token);
   int stop = StringFind(payload, "\"", start);
   if(stop < start) return false;
   value = StringSubstr(payload, start, stop - start);
   return StringFind(value, "\\") < 0;
}

bool ReadDesktopJsonLong(const string payload, const string key, long &value)
{
   string token = "\"" + key + "\":";
   int at = StringFind(payload, token);
   if(at < 0 || StringFind(payload, token, at + StringLen(token)) >= 0) return false;
   int start = at + StringLen(token);
   int stop = start;
   while(stop < StringLen(payload))
   {
      ushort ch = StringGetCharacter(payload, stop);
      if(ch < 48 || ch > 57) break;
      stop++;
   }
   string number = StringSubstr(payload, start, stop - start);
   return ParseNonnegativeLongStrict(number, value);
}

bool IsDesktopCommandIdValid(const string commandId)
{
   if(StringLen(commandId) != 36) return false;
   for(int i = 0; i < 36; i++)
   {
      ushort ch = StringGetCharacter(commandId, i);
      if(i == 8 || i == 13 || i == 18 || i == 23)
      { if(ch != 45) return false; continue; }
      if(!((ch >= 48 && ch <= 57) || (ch >= 97 && ch <= 102))) return false;
   }
   ushort variant = StringGetCharacter(commandId, 19);
   return (StringGetCharacter(commandId, 14) == 52 &&
           (variant == 56 || variant == 57 || variant == 97 || variant == 98));
}

bool IsDesktopActionAllowed(const string action)
{
   return (action == "market" || action == "limit" ||
           action == "trendline" || action == "ema_bounce");
}

bool ReadDesktopTraderCommand(DesktopTraderCommand &command, string &why)
{
   int handle = FileOpen(TraderControlCommandFile(),
                         FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(handle == INVALID_HANDLE){ why = ""; return false; }
   int size = (int)FileSize(handle);
   string payload = (size > 0 ? FileReadString(handle, size) : "");
   FileClose(handle);

   long protocol=0, created=0;
   if(!ReadDesktopJsonString(payload, "action", command.action) ||
      !ReadDesktopJsonString(payload, "command_id", command.commandId) ||
      !ReadDesktopJsonLong(payload, "created_at", created) ||
      !ReadDesktopJsonString(payload, "instance_id", command.instanceId) ||
      !ReadDesktopJsonLong(payload, "protocol_version", protocol))
   { why = "Malformed desktop command JSON."; return false; }
   string canonical = "{\"action\":\"" + command.action + "\",\"command_id\":\"" +
                      command.commandId + "\",\"created_at\":" + (string)created +
                      ",\"instance_id\":\"" + command.instanceId +
                      "\",\"protocol_version\":" + (string)protocol + "}";
   if(payload != canonical)
   { why = "Desktop command is non-canonical or contains unsupported fields."; return false; }
   command.protocolVersion = (int)protocol;
   command.createdAt = (datetime)created;
   if(command.protocolVersion != TRADER_CONTROL_PROTOCOL_VERSION)
   { why = "Desktop command protocol version mismatch."; return false; }
   if(command.instanceId != g_traderControlInstanceId)
   { why = "Desktop command instance identity mismatch."; return false; }
   if(!IsDesktopCommandIdValid(command.commandId))
   { why = "Desktop command ID is not a valid UUIDv4."; return false; }
   if(!IsDesktopActionAllowed(command.action))
   { why = "Desktop command action is unknown."; return false; }
   datetime now = TimeGMT();
   if(command.createdAt > now + 5 || command.createdAt < now - TRADER_CONTROL_COMMAND_MAX_AGE_SECONDS)
   { why = "Desktop command is stale or has an invalid future timestamp."; return false; }
   why = "";
   return true;
}

bool AcquireDesktopCommandGate(int &handle, string &why)
{
   handle = INVALID_HANDLE;
   for(int attempt = 0; attempt < 20; attempt++)
   {
      ResetLastError();
      handle = FileOpen(TraderControlGateFile(), FILE_READ | FILE_WRITE | FILE_BIN | FILE_COMMON);
      if(handle != INVALID_HANDLE){ why = ""; return true; }
      Sleep(25);
   }
   why = "Could not acquire the instance-scoped desktop command gate.";
   return false;
}

bool ConsumeDesktopTraderCommand(const DesktopTraderCommand &command,
                                 bool &alreadyConsumed, string &why)
{
   alreadyConsumed = false;
   int gate = INVALID_HANDLE;
   if(!AcquireDesktopCommandGate(gate, why)) return false;
   string marker = TraderControlConsumedFile(command.commandId);
   if(FileIsExist(marker, FILE_COMMON))
   {
      alreadyConsumed = true;
      why = "Command ID was already consumed; replay rejected.";
      FileClose(gate);
      return false;
   }
   string payload = "{\"command_id\":\"" + command.commandId +
                    "\",\"consumed_at\":" + (string)TimeGMT() +
                    ",\"instance_id\":\"" + g_traderControlInstanceId +
                    "\",\"protocol_version\":" + IntegerToString(TRADER_CONTROL_PROTOCOL_VERSION) + "}";
   bool stored = WriteVerifiedCommonText(marker, payload, why);
   FileClose(gate);
   return stored;
}

bool WriteDesktopTraderResult(const DesktopTraderResult &result)
{
   string payload = "{";
   payload += "\"action\":\"" + result.action + "\",";
   payload += "\"command_id\":\"" + result.commandId + "\",";
   payload += "\"instance_id\":\"" + g_traderControlInstanceId + "\",";
   payload += "\"outcome\":\"" + result.outcome + "\",";
   payload += "\"protocol_version\":" + IntegerToString(TRADER_CONTROL_PROTOCOL_VERSION) + ",";
   payload += "\"reason\":\"" + JsonEscape(result.reason) + "\",";
   payload += "\"retcode\":" + (string)result.retcode + ",";
   payload += "\"ticket\":" + (string)result.ticket + ",";
   payload += "\"updated_at\":" + (string)TimeGMT() + "}";
   string why = "";
   if(WriteVerifiedCommonText(TraderControlResultFile(), payload, why)) return true;
   Print(EA_COMMENT, ": desktop result write failed: ", why);
   return false;
}

void SetDesktopResult(DesktopTraderResult &result, const string outcome,
                      const string reason, const uint retcode=0, const ulong ticket=0)
{
   result.outcome = outcome;
   result.reason = reason;
   result.retcode = retcode;
   result.ticket = ticket;
}

bool DesktopOneTradeBlock(string &why, ulong &ticket)
{
   ticket = 0;
   if(!EnforceOneTradeAtATime) return false;
   if(PositionSelect(_Symbol))
   { ticket = (ulong)PositionGetInteger(POSITION_TICKET); why = "An open position already exists for this symbol."; return true; }
   if(HasBlockingPendingOrderForMarket(ticket))
   { why = "A same-symbol/same-magic pending order already exists."; return true; }
   return false;
}

void ExecuteDesktopMarket(const string commandId, DesktopTraderResult &result)
{
   bool isBuy = (StandardMarketSide == STD_MARKET_BUY);
   string why = "";
   ulong blocking = 0;
   if(DesktopOneTradeBlock(why, blocking))
   { SetDesktopResult(result, "blocked", why, 0, blocking); return; }
   if(!ValidateTradingReadiness(isBuy, why))
   { SetDesktopResult(result, "blocked", why); return; }
   MqlTick liveTick;
   if(!SymbolInfoTick(_Symbol, liveTick) || liveTick.bid <= 0.0 || liveTick.ask <= 0.0)
   { SetDesktopResult(result, "blocked", "Fresh live Bid/Ask is unavailable."); return; }
   double entry = NormalizePrice(isBuy ? liveTick.ask : liveTick.bid);
   double sl=0.0, tp=0.0, volume=0.0, riskRounded=0.0, riskBuffered=0.0;
   if(!BuildSLFromDistance(entry, isBuy, sl, why) ||
      !ComputeVolumeFromRisk(entry, sl, volume, riskRounded, riskBuffered, why) ||
      !ValidateVolumeForBroker(volume, why))
   { SetDesktopResult(result, "blocked", why); return; }
   int autoTpPts=0; double effectiveNetRR=0.0;
   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuy, volume, riskRounded, riskBuffered,
                             tp, autoTpPts, effectiveNetRR, why))
      { SetDesktopResult(result, "blocked", why); return; }
   }
   else if(!BuildTPManualFromDistance(entry, isBuy, tp, why))
   { SetDesktopResult(result, "blocked", why); return; }
   if(!ValidateMarketStopsAtLiveQuote(isBuy, liveTick.bid, liveTick.ask, sl, tp, why))
   { SetDesktopResult(result, "blocked", why); return; }

   string comment = "TDM:" + ShortStableFingerprint(commandId);
   bool sendOk = isBuy
      ? trade.Buy(volume, _Symbol, 0.0, sl, tp, comment)
      : trade.Sell(volume, _Symbol, 0.0, sl, tp, comment);
   uint retcode = trade.ResultRetcode();
   ulong order = (ulong)trade.ResultOrder();
   ulong deal = (ulong)trade.ResultDeal();
   ulong ticket = order > 0 ? order : deal;
   if(sendOk && IsTradePlacementAccepted(retcode) && ticket > 0)
   { SetDesktopResult(result, "accepted", "Broker accepted the one-shot desktop market request.", retcode, ticket); return; }
   if(IsDefinitePendingRejectionRetcode(retcode) && ticket == 0)
      SetDesktopResult(result, "rejected", trade.ResultRetcodeDescription(), retcode, 0);
   else
      SetDesktopResult(result, "uncertain", "Market request outcome is not conclusively accepted or rejected; no retry will occur. " + trade.ResultRetcodeDescription(), retcode, ticket);
}

void ExecuteDesktopPendingAtEntry(const bool isBuyLimit, const double entry,
                                  const string comment, DesktopTraderResult &result)
{
   string why = "";
   ulong blocking = 0;
   if(DesktopOneTradeBlock(why, blocking))
   { SetDesktopResult(result, "blocked", why, 0, blocking); return; }
   ResetTrendlinePlacementMetadata();
   bool placed = PlaceOrReplacePendingLimitAtEntry(isBuyLimit, entry, false, comment, why);
   uint retcode = g_lastPendingBrokerAttempted ? trade.ResultRetcode() : 0;
   ulong ticket = g_lastPendingBrokerAttempted ? (ulong)trade.ResultOrder() : 0;
   if(placed && g_ticket > 0)
   { SetDesktopResult(result, "accepted", "Broker accepted and exposed the one-shot pending order.", retcode, g_ticket); return; }
   if(!g_lastPendingBrokerAttempted)
   { SetDesktopResult(result, "blocked", why); return; }
   if(g_lastPendingAcceptanceMismatch || ticket > 0 || !IsDefinitePendingRejectionRetcode(retcode))
   { SetDesktopResult(result, "uncertain", why + " No retry will occur.", retcode, ticket); return; }
   SetDesktopResult(result, "rejected", why, retcode, 0);
}

void ExecuteDesktopLimit(DesktopTraderResult &result)
{
   bool isBuyLimit = (StandardLimitSide == STD_BUY_LIMIT);
   ExecuteDesktopPendingAtEntry(isBuyLimit, StandardLimitEntryPrice,
                                "TDL:" + ShortStableFingerprint(result.commandId), result);
}

void ExecuteDesktopEmaBounce(DesktopTraderResult &result)
{
   double fast0=0.0, slow0=0.0, trend0=0.0;
   double reference=0.0;
   bool isBuyLimit=false;
   if(UseDualEMA)
   {
      if(!GetBufferValue(hFast, 0, 0, fast0) || !GetBufferValue(hSlow, 0, 0, slow0))
      { SetDesktopResult(result, "blocked", "Current Fast/Slow EMA values are unavailable."); return; }
      if(!PricesMateriallyDiffer(fast0, slow0))
      { SetDesktopResult(result, "blocked", "Fast and Slow EMA values are equal; direction is ambiguous."); return; }
      isBuyLimit = (fast0 > slow0);
      reference = SelectEmaBounceReference(fast0, slow0, 0.0);
   }
   else
   {
      double trend1=0.0;
      double close1=iClose(_Symbol, _Period, 1);
      if(close1 <= 0.0 || !GetBufferValue(hTrend, 0, 0, trend0) ||
         !GetBufferValue(hTrend, 0, 1, trend1))
      { SetDesktopResult(result, "blocked", "Trend EMA or latest completed candle is unavailable."); return; }
      if(!PricesMateriallyDiffer(close1, trend1))
      { SetDesktopResult(result, "blocked", "Latest completed close equals the Trend EMA; direction is ambiguous."); return; }
      isBuyLimit = (close1 > trend1);
      reference = SelectEmaBounceReference(0.0, 0.0, trend0);
   }
   ExecuteDesktopPendingAtEntry(isBuyLimit, NormalizePrice(reference),
                                "TDE:" + ShortStableFingerprint(result.commandId), result);
}

bool IsValidDesktopTrendline(const string name)
{
   if(!TrendlineExists(name)) return false;
   datetime barTime = iTime(_Symbol, _Period, 0);
   return (barTime > 0 && GetTrendlinePriceAtTime(name, barTime) > 0.0);
}

bool ResolveDesktopTrendline(string &name, string &why)
{
   int total = ObjectsTotal(0, -1, OBJ_TREND);
   int selectedCount = 0;
   string selected = "";
   for(int i = 0; i < total; i++)
   {
      string candidate = ObjectName(0, i, -1, OBJ_TREND);
      if(!IsValidDesktopTrendline(candidate)) continue;
      if((bool)ObjectGetInteger(0, candidate, OBJPROP_SELECTED))
      { selected = candidate; selectedCount++; }
   }
   if(selectedCount == 1){ name = selected; why = ""; return true; }
   if(selectedCount > 1)
   { why = "Multiple valid trendlines are selected; select exactly one."; return false; }
   if(IsValidDesktopTrendline(TrendlineObjectName))
   { name = TrendlineObjectName; why = ""; return true; }

   int validCount = 0;
   string only = "";
   for(int i = 0; i < total; i++)
   {
      string candidate = ObjectName(0, i, -1, OBJ_TREND);
      if(!IsValidDesktopTrendline(candidate)) continue;
      only = candidate;
      validCount++;
   }
   if(validCount == 1){ name = only; why = ""; return true; }
   why = validCount == 0 ? "No valid trendline exists on this chart."
                         : "Multiple valid trendlines exist; select exactly one.";
   return false;
}

bool SaveDesktopActiveTrendline(const string name, string &why)
{
   if(name == "" || StringFind(name, "\r") >= 0 || StringFind(name, "\n") >= 0)
   { why = "Resolved trendline name cannot be persisted safely."; return false; }
   return WriteVerifiedCommonText(TraderControlActiveTrendlineFile(), name, why);
}

void LoadDesktopActiveTrendline()
{
   g_trendName = "";
   int handle = FileOpen(TraderControlActiveTrendlineFile(),
                         FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(handle != INVALID_HANDLE)
   {
      int size = (int)FileSize(handle);
      g_trendName = size > 0 ? FileReadString(handle, size) : "";
      FileClose(handle);
   }
   if(g_trendName == "" && TrendlineObjectName != "")
      g_trendName = TrendlineObjectName;
}

void MaintainDesktopTrendlineLifecycle(const string source)
{
   if(g_trendName == "") return;
   if(!OrdersEnabled || !TrendlineExists(g_trendName))
   { CancelExactTrendlineLifecyclePending(source + " inactive"); return; }
   MaintainTrendlineLifecycle(source, false);
}

void ExecuteDesktopTrendline(DesktopTraderResult &result)
{
   string line = "";
   string why = "";
   if(!ResolveDesktopTrendline(line, why))
   { SetDesktopResult(result, "blocked", why); return; }
   if(!SaveDesktopActiveTrendline(line, why))
   { SetDesktopResult(result, "blocked", why); return; }
   g_trendName = line;

   TrendlineLifecycleRecord record;
   if(!LoadTrendlineLifecycleRecord(record, why))
   { SetDesktopResult(result, "blocked", why); return; }
   if(record.exists && record.working)
   {
      MaintainTrendlineLifecycle("desktop command preflight", false);
      if(!LoadTrendlineLifecycleRecord(record, why))
      { SetDesktopResult(result, "blocked", why); return; }
      if(record.working)
      { SetDesktopResult(result, "blocked", "This exact trendline already has an active or unresolved lifecycle.", 0, record.ticket); return; }
   }

   ulong unrelated = 0;
   if(FindAnyPendingLimitForEA(unrelated))
   { SetDesktopResult(result, "blocked", "Another same-symbol/same-magic pending order exists.", 0, unrelated); return; }
   if(EnforceOneTradeAtATime && PositionSelect(_Symbol))
   { SetDesktopResult(result, "blocked", "An open position already exists for this symbol.", 0, (ulong)PositionGetInteger(POSITION_TICKET)); return; }

   long generation = record.exists ? TrendlineHighestHandled(record) + 1 : 1;
   if(generation <= 0 || generation > TRENDLINE_ARM_GENERATION_MAX)
   { SetDesktopResult(result, "blocked", "No higher safe trendline lifecycle generation is available."); return; }
   TrendlineLifecycleRecord armed = record;
   armed.exists = true;
   armed.generation = generation;
   armed.blockedGeneration = generation;
   armed.working = true;
   armed.replacing = false;
   armed.ticket = 0;
   armed.expiration = 0;
   armed.orderType = (Direction == TL_BUY_LIMIT ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT);
   armed.orderComment = TrendlineOrderComment(generation);
   if(!PersistTrendlineLifecycleRecord(record, armed, why))
   { SetDesktopResult(result, "blocked", why); return; }

   ResetTrendlinePlacementMetadata();
   bool placed = PlacePendingTrendlineGeneration(generation);
   uint retcode = g_lastPendingBrokerAttempted ? trade.ResultRetcode() : 0;
   ulong resultTicket = g_lastPendingBrokerAttempted ? (ulong)trade.ResultOrder() : 0;
   if(placed && g_ticket > 0)
   {
      TrendlineLifecycleRecord working = armed;
      working.ticket = g_ticket;
      if(OrderSelect(g_ticket)) working.expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
      if(!PersistTrendlineLifecycleRecord(armed, working, why))
      { SetDesktopResult(result, "uncertain", "Order is observable but durable ticket persistence failed: " + why, retcode, g_ticket); return; }
      SetDesktopResult(result, "accepted", "The trendline generation was accepted once.", retcode, g_ticket);
      return;
   }
   if(g_lastPendingAcceptanceMismatch || resultTicket > 0)
   { SetDesktopResult(result, "uncertain", "Trendline request outcome is ambiguous; lifecycle remains reserved and no retry will occur.", retcode, resultTicket); return; }

   TrendlineLifecycleRecord consumed = armed;
   consumed.working = false;
   if(!PersistTrendlineLifecycleRecord(armed, consumed, why))
   { SetDesktopResult(result, "uncertain", "Placement failed and consumed-state persistence also failed: " + why, retcode, 0); return; }
   if(!g_lastPendingBrokerAttempted)
      SetDesktopResult(result, "blocked", "Trendline placement preflight failed; this generation is consumed.");
   else if(IsDefinitePendingRejectionRetcode(retcode))
      SetDesktopResult(result, "rejected", trade.ResultRetcodeDescription(), retcode, 0);
   else
      SetDesktopResult(result, "uncertain", "Trendline request was not accepted; generation is consumed and no retry will occur.", retcode, 0);
}

void HandleDesktopTraderCommand()
{
   if(!UseDesktopTraderControls || !g_traderControlReady) return;
   DesktopTraderCommand command;
   string why = "";
   if(!ReadDesktopTraderCommand(command, why))
   {
      if(why == "") return;
      static string lastRejected = "";
      string rejectionKey = command.commandId + "|" + why;
      if(rejectionKey == lastRejected) return;
      lastRejected = rejectionKey;
      DesktopTraderResult rejected;
      rejected.commandId = IsDesktopCommandIdValid(command.commandId) ? command.commandId : "invalid";
      rejected.action = IsDesktopActionAllowed(command.action) ? command.action : "unknown";
      SetDesktopResult(rejected, "blocked", why);
      WriteDesktopTraderResult(rejected);
      Print(EA_COMMENT, ": desktop command rejected before consumption: ", why);
      return;
   }
   static string lastObserved = "";
   if(command.commandId == lastObserved) return;
   lastObserved = command.commandId;

   DesktopTraderResult result;
   result.commandId = command.commandId;
   result.action = command.action;
   SetDesktopResult(result, "blocked", "Command was not dispatched.");
   bool alreadyConsumed = false;
   if(!ConsumeDesktopTraderCommand(command, alreadyConsumed, why))
   {
      SetDesktopResult(result, "blocked", alreadyConsumed ? "Command replay rejected." : why);
      WriteDesktopTraderResult(result);
      return;
   }
   // The durable per-command marker above is verified before any broker request.
   if(!OrdersEnabled)
      SetDesktopResult(result, "blocked", "OrdersEnabled is false; command consumed without submission.");
   else if(command.action == "market") ExecuteDesktopMarket(command.commandId, result);
   else if(command.action == "limit") ExecuteDesktopLimit(result);
   else if(command.action == "trendline") ExecuteDesktopTrendline(result);
   else if(command.action == "ema_bounce") ExecuteDesktopEmaBounce(result);
   else SetDesktopResult(result, "blocked", "Unknown desktop action.");
   WriteDesktopTraderResult(result);
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
   g_traderControlInstanceId = TraderControlInstanceId();

   g_lastBarTime = iTime(_Symbol, _Period, 0);

   // Always run a one-second timer so cancels happen even when market is quiet/no ticks.
   EventSetTimer(1);
   Print(EA_COMMENT, ": EA version ", EA_VERSION);
   Print(EA_COMMENT, ": EnablePepperstoneSpreadExport=", (EnablePepperstoneSpreadExport ? "true" : "false"));
   Print(EA_COMMENT, ": PepperstoneSpreadExportIntervalSeconds=", IntegerToString(PepperstoneSpreadExportIntervalSeconds));
   Print(EA_COMMENT, ": PepperstoneSpreadExportSymbols=", PepperstoneSpreadExportSymbols);
   Print(EA_COMMENT, ": PepperstoneSpreadExportPath requested=", PepperstoneSpreadExportPath);
   Print(EA_COMMENT, ": TERMINAL_DATA_PATH=", TerminalInfoString(TERMINAL_DATA_PATH));
   Print(EA_COMMENT, ": Expected fallback MQL5\\Files path=", TerminalInfoString(TERMINAL_DATA_PATH), "\\MQL5\\Files\\", FileNameOnly(PepperstoneSpreadExportPath));
   MaybeExportPepperstoneSpreads(true);
   RefreshTrendlineNameFromInputs();

   // Desktop mode exposes EMA Bounce regardless of the legacy Strategy selector.
   if(UseDesktopTraderControls || Strategy == STRAT_EMA_BOUNCE)
   {
      if(!ValidateEmaBouncePeriods()) return INIT_PARAMETERS_INCORRECT;
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

   if(UseDesktopTraderControls)
   {
      ObjectDelete(0, STANDARD_MARKET_EXECUTE_BUTTON);
      g_traderControlReady = false;
      g_traderControlReason = "Desktop controls are initializing; execution is disabled.";
      WriteDesktopTraderStatus();
      string launchReason = "";
      g_traderControlReady = LaunchDesktopTraderControls(launchReason);
      g_traderControlReason = launchReason;
      LoadDesktopActiveTrendline();
      MaintainDesktopTrendlineLifecycle("OnInit desktop maintenance");
      WriteDesktopTraderStatus();
      Print(EA_COMMENT, ": desktop_control ready=", (g_traderControlReady ? "true" : "false"),
            " instance=", g_traderControlInstanceId, " reason=", g_traderControlReason);
      return INIT_SUCCEEDED;
   }

    // A valid named trendline is deliberately not enough to submit an order.
    // The durable generation gate makes an explicit arm/re-arm one-shot.
    if(Strategy == STRAT_TRENDLINE_LIMIT)
    {
      if(!TrendlineShouldBeActive())
      {
         CancelExactTrendlineLifecyclePending("OnInit inactive");
      }
      else
      {
          MaintainTrendlineLifecycle("OnInit");
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
   if(UseDesktopTraderControls)
   {
      g_traderControlReady = false;
      g_traderControlReason = "EA instance disconnected.";
      WriteDesktopTraderStatus();
   }
   EventKillTimer();
   if(hFast  != INVALID_HANDLE) IndicatorRelease(hFast);
   if(hSlow  != INVALID_HANDLE) IndicatorRelease(hSlow);
   if(hTrend != INVALID_HANDLE) IndicatorRelease(hTrend);
   ObjectDelete(0, STANDARD_MARKET_EXECUTE_BUTTON);
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(!UseDesktopTraderControls && id == CHARTEVENT_OBJECT_CLICK &&
      sparam == STANDARD_MARKET_EXECUTE_BUTTON && Strategy == STRAT_STANDARD_MARKET)
      ExecuteStandardMarketOnce();
}

void OnTick()
{
   if(UseDesktopTraderControls)
   {
      MaintainDesktopTrendlineLifecycle("OnTick desktop maintenance");
      return;
   }

   // If orders are disabled at any time, make sure trendline pendings are gone.
   if(!OrdersEnabled)
   {
      if(Strategy == STRAT_TRENDLINE_LIMIT)
      {
         RefreshTrendlineNameFromInputs();
         CancelExactTrendlineLifecyclePending("OnTick orders_disabled");
      }
      else if(Strategy == STRAT_STANDARD_LIMIT) CancelAllPendingByMagic();
      return;
   }

   // Trendline: if name is cleared/invalid OR object was deleted -> cancel immediately
   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      RefreshTrendlineNameFromInputs();
      if(!TrendlineShouldBeActive())
      {
         CancelExactTrendlineLifecyclePending("OnTick inactive");
         return;
      }

       MaintainTrendlineLifecycle("OnTick");
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
   MaybeExportPepperstoneSpreads();

   if(UseDesktopTraderControls)
   {
      MaintainDesktopTrendlineLifecycle("OnTimer desktop maintenance");
      WriteDesktopTraderStatus();
      HandleDesktopTraderCommand();
      WriteDesktopTraderStatus();
      return;
   }

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
      CancelExactTrendlineLifecyclePending("OnTimer inactive");
      return;
   }

    MaintainTrendlineLifecycle("OnTimer");
}
