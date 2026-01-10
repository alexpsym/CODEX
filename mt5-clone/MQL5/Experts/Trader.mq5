#property strict
#property description "Trader EA: (1) Trendline limit-order execution with on-chart arm/cancel buttons and per-bar re-anchoring; (2) EMA bounce/pullback market execution derived from PullbackEMA_ATR_RR signal logic. SL/TP are set by DISTANCE in MT5 POINTS, with optional AutoTP to guarantee NET RR after commissions."
#property version   "2.00"

#include <Trade/Trade.mqh>

CTrade trade;

// -------------------- Strategy selection --------------------
enum StrategyMode
{
   STRAT_TRENDLINE_LIMIT = 0,
   STRAT_EMA_BOUNCE      = 1
};

input group "Strategy"
input StrategyMode Strategy = STRAT_TRENDLINE_LIMIT;

// -------------------- Inputs (risk model originally aligned with PullbackEMA_ATR_RR) --------------------
input group "Risk (account currency)"
input double RiskAUD_Target           = 10.0;   // target risk per trade (position size is derived from this)
input double RiskAUD_Min              = 9.0;    // hard filter: do NOT trade if rounded risk < this
input double RiskAUD_Max              = 12.0;   // hard filter: do NOT trade if rounded risk > this
input bool   IncludeCommissionInRisk  = true;   // include commission in lot sizing + risk filters
input double CommissionPerLotPerSide  = 3.50;   // commission per side per 1.00 lot (account currency)
input int    RiskSlippageBufferPoints = 50;     // buffer added to stop distance for sizing (points)
input int    SlippagePoints           = 10;

// -------------------- Inputs (shared protection: DISTANCES, in MT5 POINTS) --------------------
input group "Stops & Targets (points)"
input int    SL_DistancePoints        = 200;    // Stop distance in MT5 points (example: 54 points = 5.4 pips on 5-digit FX)
input bool   AutoTP_NetRR_Enabled      = true;   // if true, EA ignores TP_DistancePoints and auto-sets TP so NET profit >= NetRR_Target * R (after commissions)
input double NetRR_Target              = 2.0;    // desired net R-multiple on winners (e.g., 2.0 means NET >= 2R after commissions)
input int    AutoTP_SafetyPoints       = 0;      // extra points added to computed TP (additional safety buffer)
input int    TP_DistancePoints         = 400;    // (fallback) Target distance in MT5 points when AutoTP_NetRR_Enabled=false

// Notes (for clarity)
// NOTE: 1 MT5 point = 1 TradingView tick.
// NOTE: On 5-digit FX / 3-digit JPY, 1 pip = 10 points (e.g., 5.4 pips = 54 points). On 4-digit/2-digit symbols, 1 pip is typically 1 point.

// -------------------- Inputs (Trendline strategy only) --------------------
input group "Trendline strategy (Trendline Limit)"
enum TL_Direction
{
   TL_BUY_LIMIT  = 0,
   TL_SELL_LIMIT = 1
};

input TL_Direction Direction           = TL_BUY_LIMIT;
input string       TrendlineObjectName = "";     // required for Strategy=Trendline unless you enable click-pick
input bool         EnablePickTrendlineByClick = false;  // default OFF: paste the trendline name into TrendlineObjectName
input int          PendingCancelAfterMinutes  = 60;     // cancel/disarm if limit order not filled after X minutes (age measured from first successful placement)

// -------------------- Inputs (EMA bounce strategy only) --------------------
input group "EMA bounce strategy (derived from PullbackEMA_ATR_RR)"
input bool   UseDualEMA       = true;     // true: trend uses Fast/Slow EMA relationship. false: trend uses TrendEMA only.
input int    FastEMAPeriod    = 9;        // used when UseDualEMA=true
input int    SlowEMAPeriod    = 20;       // used when UseDualEMA=true
input int    TrendEMAPeriod   = 20;       // used when UseDualEMA=false
input bool   Debug            = false;    // prints reason-coded messages for EMA bounce filters

// -------------------- Order housekeeping --------------------
input group "Orders"
input int    MagicNumber              = 91001;
input bool   EnforceOneTradeAtATime   = true;   // blocks if there's an open position on this symbol
input bool   AlsoBlockIfPendingExists = true;   // blocks if another EA pending order exists on this symbol (same magic) [trendline only]

// -------------------- Chart UI --------------------
input group "UI"
input bool   ShowButtons              = true;
input int    UIButtonCorner           = 2;      // 0=left-top, 1=right-top, 2=left-bottom, 3=right-bottom
input int    UIButtonX                = 10;
input int    UIButtonY                = 40;

// -------------------- Internals --------------------
string   g_trendName    = "";
bool     g_armed        = false;
ulong    g_ticket       = 0;      // trendline pending ticket (will change each bar because we recreate)
datetime g_lastBarTime  = 0;
datetime g_armStartTime = 0;      // when the EA first successfully placed the pending order for this arming session
datetime g_expireAt     = 0;      // server-side pending order expiration timestamp

// EMA handles (ema bounce strategy)
int hFast  = INVALID_HANDLE;
int hSlow  = INVALID_HANDLE;
int hTrend = INVALID_HANDLE;

string BTN_PLACE  = "TR_EA_BTN_PLACE";
string BTN_CANCEL = "TR_EA_BTN_CANCEL";
string LBL_STATUS = "TR_EA_STATUS";

string EA_COMMENT = "Trader";

// -------------------- Helpers --------------------
void Dbg(const string msg)
{
   if(Debug) Print(EA_COMMENT, ": ", msg);
}

bool IsNewBar()
{
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == 0) return false;
   if(t != g_lastBarTime)
   {
      g_lastBarTime = t;
      return true;
   }
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

   // round DOWN to step (risk must not exceed max due to rounding up)
   double steps = MathFloor(vol / step);
   double v = steps * step;

   if(v < vmin) v = vmin;
   if(v > vmax) v = vmax;

   int digits = (int)MathRound(-MathLog10(step));
   if(digits < 0) digits = 2;

   return NormalizeDouble(v, digits);
}

// For display only (pip/point note)
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
   // line_id=0 is fine for trendline
   return ObjectGetValueByTime(0, name, t, 0);
}

bool BuildSLFromDistance(double entry, bool isBuy, double &slOut, string &why)
{
   if(SL_DistancePoints <= 0)
   {
      why = "SL_DistancePoints must be > 0.";
      return false;
   }

   double slDist = (double)SL_DistancePoints * _Point;

   if(isBuy) slOut = entry - slDist;
   else      slOut = entry + slDist;

   slOut = NormalizePrice(slOut);

   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0)
   {
      if(MathAbs(entry - slOut) < stopsLevel * _Point)
      {
         why = "SL too close to entry for broker stops-level.";
         return false;
      }
   }

   why = "";
   return true;
}

bool BuildTPManualFromDistance(double entry, bool isBuy, double &tpOut, string &why)
{
   if(TP_DistancePoints <= 0)
   {
      why = "TP_DistancePoints must be > 0 (or enable AutoTP_NetRR_Enabled).";
      return false;
   }

   double tpDist = (double)TP_DistancePoints * _Point;

   if(isBuy) tpOut = entry + tpDist;
   else      tpOut = entry - tpDist;

   tpOut = NormalizePrice(tpOut);

   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0)
   {
      if(MathAbs(entry - tpOut) < stopsLevel * _Point)
      {
         why = "TP too close to entry for broker stops-level.";
         return false;
      }
   }

   why = "";
   return true;
}

// Computes TP so that NET profit (after commissions) is >= NetRR_Target * 1R
bool ComputeAutoTP_NetRR(double entry, bool isBuy, double vol, double riskRoundedAUD, double &tpOut, int &tpPointsOut, double &effNetRR, string &why)
{
   if(vol <= 0)
   {
      why = "Invalid volume for AutoTP.";
      return false;
   }
   if(NetRR_Target <= 0)
   {
      why = "NetRR_Target must be > 0.";
      return false;
   }

   double commissionRT = CommissionPerLotPerSide * 2.0 * vol;

   double rBase = riskRoundedAUD;
   if(!IncludeCommissionInRisk)
      rBase += commissionRT;

   double requiredNetProfit = NetRR_Target * rBase;
   double requiredGrossProfit = requiredNetProfit + commissionRT;

   ENUM_ORDER_TYPE ot = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   double testPrice = isBuy ? (entry + _Point) : (entry - _Point);
   double p1 = 0.0;
   if(!OrderCalcProfit(ot, _Symbol, vol, entry, testPrice, p1))
   {
      why = "OrderCalcProfit failed while estimating profit-per-point.";
      return false;
   }

   double profitPerPoint = MathAbs(p1);
   if(profitPerPoint <= 0)
   {
      why = "Profit-per-point is zero/invalid (symbol config).";
      return false;
   }

   int pts = (int)MathCeil(requiredGrossProfit / profitPerPoint);
   if(pts < 1) pts = 1;
   pts += AutoTP_SafetyPoints;

   double tp = isBuy ? (entry + (double)pts * _Point) : (entry - (double)pts * _Point);
   tp = NormalizePrice(tp);

   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0)
   {
      if(MathAbs(entry - tp) < stopsLevel * _Point)
      {
         int minPts = stopsLevel + AutoTP_SafetyPoints;
         if(minPts < pts) minPts = pts;
         pts = minPts;
         tp = isBuy ? (entry + (double)pts * _Point) : (entry - (double)pts * _Point);
         tp = NormalizePrice(tp);
      }
   }

   double grossAtTP = 0.0;
   if(!OrderCalcProfit(ot, _Symbol, vol, entry, tp, grossAtTP))
   {
      why = "OrderCalcProfit failed while validating final TP.";
      return false;
   }

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
   if(ask <= 0 || bid <= 0)
   {
      why = "Bid/Ask not available.";
      return false;
   }

   if(isBuyLimit && !(entry < ask))
   {
      why = "Buy Limit entry is not below current Ask (this would be a Buy Stop).";
      return false;
   }
   if(!isBuyLimit && !(entry > bid))
   {
      why = "Sell Limit entry is not above current Bid (this would be a Sell Stop).";
      return false;
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
   if(stopPoints <= 0)
   {
      why = "Stop distance is zero/invalid.";
      return false;
   }

   double lossPerLotSL = 0.0;
   if(!CalcRiskFor1Lot(stopPoints, lossPerLotSL))
   {
      why = "Failed to compute tick value based risk for 1 lot.";
      return false;
   }

   double commissionRTPerLot = 2.0 * CommissionPerLotPerSide;

   double riskPerLotSizing = lossPerLotSL;
   if(IncludeCommissionInRisk)
      riskPerLotSizing += commissionRTPerLot;

   if(riskPerLotSizing <= 0)
   {
      why = "Total risk per lot invalid.";
      return false;
   }

   double volRaw = riskTarget / riskPerLotSizing;
   double vol = NormalizeVolume(volRaw);
   if(vol <= 0)
   {
      why = "Computed volume rounds to 0 (below broker min lot).";
      return false;
   }

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

   if(riskTotal < riskMin)
   {
      why = "Rounded risk is below RiskAUD_Min filter.";
      return false;
   }
   if(riskTotal > riskMax)
   {
      why = "Rounded risk exceeds RiskAUD_Max filter.";
      return false;
   }

   double effectiveStopPoints = stopPoints + MathMax(0, RiskSlippageBufferPoints);
   if(effectiveStopPoints > stopPoints)
   {
      double lossPerLotWorst = 0.0;
      if(!CalcRiskFor1Lot(effectiveStopPoints, lossPerLotWorst))
      {
         why = "Failed to compute worst-case risk for 1 lot.";
         return false;
      }

      double riskWorst = lossPerLotWorst * vol;
      if(IncludeCommissionInRisk)
         riskWorst += commissionRTPerLot * vol;

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
         if(IncludeCommissionInRisk)
            riskWorst += commissionRTPerLot * vol;
      }

      outVol = vol;
      outRiskRoundedAUD = riskTotal;

      if(riskWorst > riskMax)
         why = "WARN: worst-case buffered risk exceeds RiskAUD_Max (min-risk guarantee enforced).";
   }

   return true;
}

bool InPosition()
{
   if(!EnforceOneTradeAtATime) return false;
   return PositionSelect(_Symbol);
}

bool PendingOrderExistsByMagic(ulong &ticketOut)
{
   ticketOut = 0;
   int total = OrdersTotal();
   for(int i=0; i<total; i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(!OrderSelect(ticket)) continue;
      string sym = OrderGetString(ORDER_SYMBOL);
      if(sym != _Symbol) continue;

      long magic = OrderGetInteger(ORDER_MAGIC);
      if((int)magic != MagicNumber) continue;

      long type = OrderGetInteger(ORDER_TYPE);
      if(type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_SELL_LIMIT)
      {
         long state = OrderGetInteger(ORDER_STATE);
         if(state == ORDER_STATE_PLACED || state == ORDER_STATE_PARTIAL)
         {
            ticketOut = (ulong)OrderGetInteger(ORDER_TICKET);
            return true;
         }
      }
   }
   return false;
}

bool DeleteTicketIfExists(ulong t)
{
   if(t == 0) return true;
   if(!OrderSelect((ulong)t)) return true;
   return trade.OrderDelete((ulong)t);
}

datetime ComputeExpireAt()
{
   if(PendingCancelAfterMinutes <= 0) return 0;
   datetime now = TimeCurrent();
   datetime exp = now + (datetime)(PendingCancelAfterMinutes * 60);
   if(exp <= now) exp = now + 60;
   return exp;
}

bool CheckAndHandlePendingExpiry()
{
   if(!g_armed) return false;
   if(PendingCancelAfterMinutes <= 0) return false;
   if(g_armStartTime <= 0) return false;

   long ageSec = (long)(TimeCurrent() - g_armStartTime);
   if(ageSec < (long)PendingCancelAfterMinutes * 60L) return false;

   if(g_ticket != 0)
      DeleteTicketIfExists(g_ticket);

   ulong other = 0;
   if(PendingOrderExistsByMagic(other))
      DeleteTicketIfExists(other);

   g_ticket = 0;
   g_armed = false;
   g_armStartTime = 0;
   g_expireAt = 0;

   // UI update happens via UpdateStatus()
   return true;
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

// Signal (copied/adapted from PullbackEMA_ATR_RR):
// - Determine trend on CLOSED candle [1]
// - Enter on NEW bar if candle [1] is counter-trend (bearish in uptrend; bullish in downtrend)
bool GetEmaBounceSignal(ENUM_ORDER_TYPE &outType)
{
   double c1 = iClose(_Symbol, _Period, 1);
   double o1 = iOpen(_Symbol,  _Period, 1);
   if(c1 == 0 || o1 == 0) return false;

   bool candleBear = (c1 < o1);
   bool candleBull = (c1 > o1);

   bool up = false, down = false;

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

   if(up && candleBear)
   {
      outType = ORDER_TYPE_BUY;
      return true;
   }
   if(down && candleBull)
   {
      outType = ORDER_TYPE_SELL;
      return true;
   }

   return false;
}

// -------------------- UI helpers --------------------
void UpdateStatus(const string msg)
{
   if(!ShowButtons) return;
   if(ObjectFind(0, LBL_STATUS) < 0) return;
   ObjectSetString(0, LBL_STATUS, OBJPROP_TEXT, msg);
   ChartRedraw();
}

void EnsureUI()
{
   if(!ShowButtons) return;

   if(ObjectFind(0, BTN_PLACE) < 0)
   {
      ObjectCreate(0, BTN_PLACE, OBJ_BUTTON, 0, 0, 0);
      ObjectSetInteger(0, BTN_PLACE, OBJPROP_CORNER, UIButtonCorner);
      ObjectSetInteger(0, BTN_PLACE, OBJPROP_XDISTANCE, UIButtonX);
      ObjectSetInteger(0, BTN_PLACE, OBJPROP_YDISTANCE, UIButtonY);
      ObjectSetInteger(0, BTN_PLACE, OBJPROP_XSIZE, 140);
      ObjectSetInteger(0, BTN_PLACE, OBJPROP_YSIZE, 24);
      ObjectSetString(0, BTN_PLACE, OBJPROP_TEXT, "PLACE / ARM");
   }

   if(ObjectFind(0, BTN_CANCEL) < 0)
   {
      ObjectCreate(0, BTN_CANCEL, OBJ_BUTTON, 0, 0, 0);
      ObjectSetInteger(0, BTN_CANCEL, OBJPROP_CORNER, UIButtonCorner);
      ObjectSetInteger(0, BTN_CANCEL, OBJPROP_XDISTANCE, UIButtonX);
      ObjectSetInteger(0, BTN_CANCEL, OBJPROP_YDISTANCE, UIButtonY + 30);
      ObjectSetInteger(0, BTN_CANCEL, OBJPROP_XSIZE, 140);
      ObjectSetInteger(0, BTN_CANCEL, OBJPROP_YSIZE, 24);
      ObjectSetString(0, BTN_CANCEL, OBJPROP_TEXT, "CANCEL");
   }

   if(ObjectFind(0, LBL_STATUS) < 0)
   {
      ObjectCreate(0, LBL_STATUS, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, LBL_STATUS, OBJPROP_CORNER, UIButtonCorner);
      ObjectSetInteger(0, LBL_STATUS, OBJPROP_XDISTANCE, UIButtonX);
      ObjectSetInteger(0, LBL_STATUS, OBJPROP_YDISTANCE, UIButtonY + 62);
      ObjectSetInteger(0, LBL_STATUS, OBJPROP_FONTSIZE, 9);
      ObjectSetString(0, LBL_STATUS, OBJPROP_TEXT, "");
   }
}

void RemoveUI()
{
   ObjectDelete(0, BTN_PLACE);
   ObjectDelete(0, BTN_CANCEL);
   ObjectDelete(0, LBL_STATUS);
}

// -------------------- Trendline execution --------------------
bool PlaceOrReplacePendingTrendline()
{
   if(!TrendlineExists(g_trendName))
   {
      UpdateStatus("No valid trendline selected.");
      return false;
   }

   bool isBuyLimit = (Direction == TL_BUY_LIMIT);

   datetime barTime = iTime(_Symbol, _Period, 0);
   double entry = GetTrendlinePriceAtTime(g_trendName, barTime);
   if(entry <= 0.0)
   {
      UpdateStatus("Trendline price unavailable (enable Ray Right?).");
      return false;
   }
   entry = NormalizePrice(entry);

   string why="";
   double sl=0, tp=0;

   if(!BuildSLFromDistance(entry, isBuyLimit, sl, why))
   {
      UpdateStatus("SL invalid: " + why);
      return false;
   }

   if(!IsLimitPriceValid(entry, isBuyLimit, why))
   {
      UpdateStatus("Limit invalid: " + why);
      return false;
   }

   double vol=0, riskRounded=0;
   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, why))
   {
      UpdateStatus("Risk sizing blocked: " + why);
      return false;
   }
   string riskWarn = why;

   int autoTpPts = 0;
   double effNetRR = 0.0;

   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuyLimit, vol, riskRounded, tp, autoTpPts, effNetRR, why))
      {
         UpdateStatus("Auto-TP blocked: " + why);
         return false;
      }
   }
   else
   {
      if(!BuildTPManualFromDistance(entry, isBuyLimit, tp, why))
      {
         UpdateStatus("TP invalid: " + why);
         return false;
      }
   }

   if(AlsoBlockIfPendingExists)
   {
      ulong other=0;
      if(PendingOrderExistsByMagic(other))
      {
         if(g_ticket == 0 || other != g_ticket)
         {
            UpdateStatus("Blocked: existing pending order (same magic).");
            return false;
         }
      }
   }

   if(g_ticket != 0)
      DeleteTicketIfExists(g_ticket);

   bool ok=false;

   ENUM_ORDER_TYPE_TIME tt = ORDER_TIME_GTC;
   datetime exp = 0;
   if(PendingCancelAfterMinutes > 0)
   {
      if(g_expireAt <= 0) g_expireAt = ComputeExpireAt();
      tt = ORDER_TIME_SPECIFIED;
      exp = g_expireAt;
   }

   if(isBuyLimit)
      ok = trade.BuyLimit(vol, entry, _Symbol, sl, tp, tt, exp, EA_COMMENT);
   else
      ok = trade.SellLimit(vol, entry, _Symbol, sl, tp, tt, exp, EA_COMMENT);

   ulong newTicket = (ulong)trade.ResultOrder();
   int ret = (int)trade.ResultRetcode();

   if(!ok || newTicket == 0)
   {
      UpdateStatus("Order failed. Retcode=" + IntegerToString(ret));
      g_ticket = 0;
      return false;
   }

   g_ticket = newTicket;

   int ppp = PointsPerPip();
   double slPips = (ppp > 0) ? ((double)SL_DistancePoints / (double)ppp) : 0.0;
   int tpPtsDisplay = AutoTP_NetRR_Enabled ? autoTpPts : TP_DistancePoints;
   double tpPips = (ppp > 0) ? ((double)tpPtsDisplay / (double)ppp) : 0.0;

   string side = isBuyLimit ? "BUY LIMIT" : "SELL LIMIT";
   UpdateStatus(
      "ARMED (Trendline) " + side +
      " | TL=" + g_trendName +
      " | Entry=" + DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) +
      " | SL=" + IntegerToString(SL_DistancePoints) + "pt (" + DoubleToString(slPips, 1) + "pip)" +
      " | TP=" + IntegerToString(tpPtsDisplay) + "pt (" + DoubleToString(tpPips, 1) + "pip)" +
      (AutoTP_NetRR_Enabled ? (" | NetRR~" + DoubleToString(effNetRR, 2)) : "") +
      " | Vol=" + DoubleToString(vol, 2) +
      " | Risk~" + DoubleToString(riskRounded, 2) +
      (riskWarn == "" ? "" : " | " + riskWarn)
   );

   return true;
}

// -------------------- EMA bounce execution --------------------
bool PlaceMarketEmaBounce()
{
   ENUM_ORDER_TYPE sigType;
   if(!GetEmaBounceSignal(sigType)) return false;

   bool isBuy = (sigType == ORDER_TYPE_BUY);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0)
   {
      UpdateStatus("No Bid/Ask.");
      return false;
   }

   double entry = isBuy ? ask : bid;
   entry = NormalizePrice(entry);

   string why="";
   double sl=0, tp=0;

   if(!BuildSLFromDistance(entry, isBuy, sl, why))
   {
      UpdateStatus("SL invalid: " + why);
      return false;
   }

   double vol=0, riskRounded=0;
   if(!ComputeVolumeFromRisk(entry, sl, vol, riskRounded, why))
   {
      UpdateStatus("Risk sizing blocked: " + why);
      return false;
   }
   string riskWarn = why;

   int autoTpPts = 0;
   double effNetRR = 0.0;

   if(AutoTP_NetRR_Enabled)
   {
      if(!ComputeAutoTP_NetRR(entry, isBuy, vol, riskRounded, tp, autoTpPts, effNetRR, why))
      {
         UpdateStatus("Auto-TP blocked: " + why);
         return false;
      }
   }
   else
   {
      if(!BuildTPManualFromDistance(entry, isBuy, tp, why))
      {
         UpdateStatus("TP invalid: " + why);
         return false;
      }
   }

   bool ok = false;
   // Market execution (price=0). Sizing uses observed Bid/Ask above; minor slippage is controlled by SlippagePoints.
   if(isBuy)
      ok = trade.Buy(vol, _Symbol, 0.0, sl, tp, EA_COMMENT);
   else
      ok = trade.Sell(vol, _Symbol, 0.0, sl, tp, EA_COMMENT);

   int ret = (int)trade.ResultRetcode();
   if(!ok)
   {
      UpdateStatus("Order failed. Retcode=" + IntegerToString(ret));
      return false;
   }

   int ppp = PointsPerPip();
   double slPips = (ppp > 0) ? ((double)SL_DistancePoints / (double)ppp) : 0.0;
   int tpPtsDisplay = AutoTP_NetRR_Enabled ? autoTpPts : TP_DistancePoints;
   double tpPips = (ppp > 0) ? ((double)tpPtsDisplay / (double)ppp) : 0.0;

   UpdateStatus(
      "ORDER SENT (EMA Bounce) " + string(isBuy ? "BUY" : "SELL") +
      " | SL=" + IntegerToString(SL_DistancePoints) + "pt (" + DoubleToString(slPips, 1) + "pip)" +
      " | TP=" + IntegerToString(tpPtsDisplay) + "pt (" + DoubleToString(tpPips, 1) + "pip)" +
      (AutoTP_NetRR_Enabled ? (" | NetRR~" + DoubleToString(effNetRR, 2)) : "") +
      " | Vol=" + DoubleToString(vol, 2) +
      " | Risk~" + DoubleToString(riskRounded, 2) +
      (riskWarn == "" ? "" : " | " + riskWarn)
   );

   return true;
}

// -------------------- Arming / lifecycle --------------------
void DisarmAndCancel()
{
   g_armed = false;
   g_armStartTime = 0;
   g_expireAt = 0;

   if(g_ticket != 0)
      DeleteTicketIfExists(g_ticket);

   g_ticket = 0;
   UpdateStatus("DISARMED / cancelled");
}

void RefreshActiveTrendlineFromInput()
{
   if(TrendlineObjectName != "")
      g_trendName = TrendlineObjectName;
}

void Arm()
{
   if(InPosition())
   {
      UpdateStatus("Blocked: already in position.");
      return;
   }

   // For trendline, start the aging timer from the initial arming placement.
   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      RefreshActiveTrendlineFromInput();
      if(!TrendlineExists(g_trendName))
      {
         UpdateStatus("Trendline not found: set TrendlineObjectName.");
         return;
      }

      g_armStartTime = TimeCurrent();
      g_expireAt = ComputeExpireAt();
   }

   g_armed = true;

   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      if(!PlaceOrReplacePendingTrendline())
      {
         g_armed = false;
         g_ticket = 0;
         g_armStartTime = 0;
         g_expireAt = 0;
      }
   }
   else
   {
      UpdateStatus("ARMED (EMA Bounce) | Waiting for new-bar signal");
   }
}

void MaybeStopIfFilled()
{
   if(PositionSelect(_Symbol))
   {
      g_armed = false;
      g_ticket = 0;
      g_armStartTime = 0;
      g_expireAt = 0;
      UpdateStatus("FILLED -> management stopped");
   }
}

// -------------------- MT5 lifecycle --------------------
int OnInit()
{
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetExpertMagicNumber(MagicNumber);

   EnsureUI();

   // Prepare EMA handles only when needed.
   if(Strategy == STRAT_EMA_BOUNCE)
   {
      if(UseDualEMA)
      {
         hFast = iMA(_Symbol, _Period, FastEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
         hSlow = iMA(_Symbol, _Period, SlowEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
         if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE)
            return INIT_FAILED;
      }
      else
      {
         hTrend = iMA(_Symbol, _Period, TrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
         if(hTrend == INVALID_HANDLE)
            return INIT_FAILED;
      }
   }

   g_lastBarTime = iTime(_Symbol, _Period, 0);

   int ppp = PointsPerPip();
   string pipNote = (ppp == 10) ? "1 pip=10 points" : "1 pip=1 point";
   string strat = (Strategy == STRAT_TRENDLINE_LIMIT) ? "Trendline" : "EMA Bounce";

   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      RefreshActiveTrendlineFromInput();
      UpdateStatus("Ready (" + strat + "). Trendline=" + (g_trendName=="" ? "<none>" : g_trendName) + " | " + pipNote + " | 1 point=1 TradingView tick");
      if(PendingCancelAfterMinutes > 0)
         EventSetTimer(10);
   }
   else
   {
      UpdateStatus("Ready (" + strat + ") | " + pipNote + " | 1 point=1 TradingView tick");
   }

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   RemoveUI();

   if(hFast  != INVALID_HANDLE) IndicatorRelease(hFast);
   if(hSlow  != INVALID_HANDLE) IndicatorRelease(hSlow);
   if(hTrend != INVALID_HANDLE) IndicatorRelease(hTrend);
}

void OnTick()
{
   MaybeStopIfFilled();
   if(!g_armed) return;

   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      if(CheckAndHandlePendingExpiry())
      {
         UpdateStatus("EXPIRED -> cancelled after " + IntegerToString(PendingCancelAfterMinutes) + " min (no fill)");
         return;
      }

      if(IsNewBar())
      {
         PlaceOrReplacePendingTrendline();
      }
      return;
   }

   // EMA bounce: evaluate once per new bar
   if(!IsNewBar()) return;

   if(InPosition())
   {
      g_armed = false;
      return;
   }

   // If a signal occurs, place a market order and stop.
   if(PlaceMarketEmaBounce())
      g_armed = false;
}

void OnTimer()
{
   // Timer is used only for trendline pending expiry enforcement.
   MaybeStopIfFilled();
   if(Strategy == STRAT_TRENDLINE_LIMIT)
   {
      if(CheckAndHandlePendingExpiry())
         UpdateStatus("EXPIRED -> cancelled after " + IntegerToString(PendingCancelAfterMinutes) + " min (no fill)");
   }
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      if(sparam == BTN_PLACE)
      {
         Arm();
         return;
      }
      if(sparam == BTN_CANCEL)
      {
         DisarmAndCancel();
         return;
      }

      // Optional: click-pick trendline (OFF by default)
      if(Strategy == STRAT_TRENDLINE_LIMIT && EnablePickTrendlineByClick)
      {
         if(TrendlineExists(sparam))
         {
            g_trendName = sparam;
            UpdateStatus("Selected trendline: " + g_trendName);
            return;
         }
      }
   }
}
