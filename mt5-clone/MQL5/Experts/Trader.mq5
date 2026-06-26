#property strict
#property description "Trader EA: (1) Trendline limit-order execution auto-armed by TrendlineObjectName; (2) EMA bounce market execution derived from Backtest signal logic. SL/TP are set by DISTANCE in MT5 POINTS, with optional AutoTP NetRR."
#property version   "2.10"

#include <Trade/Trade.mqh>
CTrade trade;

// -------------------- Strategy selection --------------------
enum StrategyMode
{
   STRAT_TRENDLINE_LIMIT = 0,
   STRAT_EMA_BOUNCE      = 1,
   STRAT_STANDARD_LIMIT  = 2
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
input string PepperstoneSpreadExportPath = "C:\\Users\\User\\Documents\\GPT\\CODEX-master\\mt5-clone\\pepperstone_spreads_latest.json";

// -------------------- Internals --------------------
string   g_trendName    = "";
ulong    g_ticket       = 0;
datetime g_lastBarTime  = 0;
datetime g_armStartTime = 0;
datetime g_expireAt     = 0;
bool     g_wasInPosition = false;
datetime g_lastPepperstoneSpreadExport = 0;
int      g_lastPepperstoneSpreadExportSymbolCount = 0;

// EMA handles
int hFast  = INVALID_HANDLE;
int hSlow  = INVALID_HANDLE;
int hTrend = INVALID_HANDLE;

string EA_COMMENT = "Trader";
string EA_VERSION = "2.10";

void Dbg(const string msg){ if(Debug) Print(EA_COMMENT, ": ", msg); }
bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,
                                       const double rawEntry,
                                       const bool allowReplace,
                                       string &why);

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
   return (retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED);
}

bool PlaceOrReplacePendingLimitAtEntry(const bool isBuyLimit,
                                       const double rawEntry,
                                       const bool allowReplace,
                                       string &why)
{
   if(rawEntry <= 0.0)
   {
      why = "Invalid manual entry price (must be > 0).";
      Print(EA_COMMENT, ": ", why, " rawEntry=", DoubleToString(rawEntry, 8));
      return false;
   }

   double entry = NormalizePrice(rawEntry);
   if(entry <= 0.0)
   {
      why = "Normalized entry price is invalid/non-positive.";
      Print(EA_COMMENT, ": ", why);
      return false;
   }

   if(!allowReplace && g_ticket > 0) return true;

   double sl=0.0, tp=0.0, vol=0.0, riskRounded=0.0;

   if(!IsLimitPriceValid(entry, isBuyLimit, why))
   {
      Print(EA_COMMENT, ": Wrong-side/too-close limit price. ", why,
            " entry=", DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
      return false;
   }

   if(!BuildSLFromDistance(entry, isBuyLimit, sl, why))
   {
      Print(EA_COMMENT, ": Failed to build SL. ", why);
      return false;
   }

   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, why))
   {
      Print(EA_COMMENT, ": Risk sizing failure. ", why);
      return false;
   }

   int autoTpPts=0;
   double effNetRR=0.0;
   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuyLimit, vol, riskRounded, tp, autoTpPts, effNetRR, why))
      {
         Print(EA_COMMENT, ": Failed to build AutoTP. ", why);
         return false;
      }
   }
   else
   {
      if(!BuildTPManualFromDistance(entry, isBuyLimit, tp, why))
      {
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

   bool sendOk=false;
   if(isBuyLimit) sendOk = trade.BuyLimit(vol, entry, _Symbol, sl, tp, tt, exp, EA_COMMENT);
   else           sendOk = trade.SellLimit(vol, entry, _Symbol, sl, tp, tt, exp, EA_COMMENT);

   uint retcode = trade.ResultRetcode();
   ulong orderTicket = (ulong)trade.ResultOrder();
   bool accepted = IsTradePlacementAccepted(retcode) && orderTicket > 0;
   if(!sendOk || !accepted)
   {
      why = "Broker/server rejected pending placement.";
      Print(EA_COMMENT, ": Pending limit placement failed. retcode=", retcode,
            " (", trade.ResultRetcodeDescription(), ")",
            ", order=", (string)orderTicket,
            ", sendOk=", (sendOk ? "true" : "false"));
      return false;
   }

   g_ticket = orderTicket;
   return true;
}

bool PlacePendingStandardLimit()
{
   bool isBuyLimit = (StandardLimitSide == STD_BUY_LIMIT);
   string why="";
   return PlaceOrReplacePendingLimitAtEntry(isBuyLimit, StandardLimitEntryPrice, false, why);
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
         if(!PlacePendingStandardLimit())
            Print(EA_COMMENT, ": Failed to place standard limit order on init.");
      }
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
}

void OnTick()
{
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

      if(InPosition())
      {
         CancelAllPendingByMagic();
         return;
      }

      if(PendingAgeExpired())
      {
         CancelAllPendingByMagic();
         return;
      }

      return;
   }

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

   // Mirrors OnTick gating so cancel happens even with no ticks
   if(Strategy == STRAT_STANDARD_LIMIT)
   {
      if(!OrdersEnabled || !StandardLimitShouldBeActive())
      {
         CancelAllPendingByMagic();
         return;
      }

      if(InPosition())
      {
         CancelAllPendingByMagic();
         return;
      }

      if(PendingAgeExpired())
      {
         CancelAllPendingByMagic();
         return;
      }
      return;
   }

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
