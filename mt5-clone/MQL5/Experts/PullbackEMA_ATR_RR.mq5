#property strict
#property description "Pullback strategy: trade trend pullbacks on the close of a counter-trend candle. Trend via EMA(s). SL via ATR multiple. TP via RR. Money-risk sizing (AUD). Blackout window (AEST/AEDT -> server time)."
#property version   "1.00"

#include <Trade/Trade.mqh>

CTrade trade;

// ---------- Inputs ----------
input bool   UseDualEMA              = true;     // true: trend uses Fast/Slow EMA relationship. false: trend uses TrendEMA only.
input int    FastEMAPeriod           = 9;        // used when UseDualEMA=true
input int    SlowEMAPeriod           = 20;       // used when UseDualEMA=true
input int    TrendEMAPeriod          = 20;       // used when UseDualEMA=false
input int    ATRPeriod               = 14;
input double ATRMultiple             = 1.5;      // 0.5, 1.5, 2, 2.5, 3 (set in Inputs)
input double RiskAUD_Target          = 10.0;     // target AUD risk per trade (position size is derived from this)
input double RiskAUD_Min             = 9.0;      // hard filter: do NOT trade if rounded risk < this
input double RiskAUD_Max             = 12.0;     // hard filter: do NOT trade if rounded risk > this
input double RiskReward              = 2.0;      // must be >= 2.0 (set 2.0 or 3.0)
input int    SlippagePoints          = 10;
input bool   EnforceOneTradeAtATime  = true;     // true = only one open position per symbol
input bool   CloseDuringBlackout     = true;     // if true, force-close any open position during blackout
input bool   IncludeCommissionInRisk = true;     // include commission in lot sizing + risk filters
input bool   AdjustTPForCommission   = true;     // extend TP so net RR matches RiskReward
input double CommissionPerLotPerSide = 3.50;     // commission per side per 1.00 lot (account currency)
input int    RiskSlippageBufferPoints = 50;      // buffer added to stop distance for max-risk guard (points)
input bool   UseRolloverWindow       = true;     // avoid trading around rollover window
input bool   CloseBeforeRollover     = true;     // close open position before rollover window
input bool   Debug                   = true;     // prints reason-coded messages for filters
input int    RolloverStartHour       = 23;       // server time
input int    RolloverStartMinute     = 55;       // server time
input int    RolloverEndHour         = 0;        // server time
input int    RolloverEndMinute       = 10;       // server time

// Blackout window requirement:
// - No NEW trade, and no OPEN trade allowed during:
//   05:00–09:00 AEST  (local)  => 02:00–06:00 server (UTC+7 shown in your note)
//   06:00–10:00 AEDT  (local)  => 03:00–07:00 server (UTC+8 shown in your note)
//
// You toggle which one you want active via UseAEDT.
input bool   UseAEDT                 = false;    // false=AEST blackout (02-06 server). true=AEDT blackout (03-07 server)
input int    BlackoutStartHour_AEST  = 2;        // server time
input int    BlackoutEndHour_AEST    = 6;        // server time (end is exclusive)
input int    BlackoutStartHour_AEDT  = 3;        // server time
input int    BlackoutEndHour_AEDT    = 7;        // server time (end is exclusive)

// ---------- Internals ----------
int hFast = INVALID_HANDLE;
int hSlow = INVALID_HANDLE;
int hTrend = INVALID_HANDLE;
int hATR  = INVALID_HANDLE;

datetime lastBarTime = 0;

// ---------- Helpers ----------
void Dbg(string msg)
{
   if(Debug) Print("PBEMA_ATR_RR: ", msg);
}

bool IsNewBar()
{
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == 0) return false;

   if(t != lastBarTime)
   {
      lastBarTime = t;
      return true;
   }

   return false;
}

bool IsBlackout(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);

   int start = UseAEDT ? BlackoutStartHour_AEDT : BlackoutStartHour_AEST;
   int end   = UseAEDT ? BlackoutEndHour_AEDT   : BlackoutEndHour_AEST;

   return (dt.hour >= start && dt.hour < end);
}

bool IsTimeInWindow(datetime t, int startHour, int startMinute, int endHour, int endMinute)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);

   int current = dt.hour * 60 + dt.min;
   int start = startHour * 60 + startMinute;
   int end = endHour * 60 + endMinute;

   if(start == end) return false;
   if(start < end)
      return (current >= start && current < end);

   // window crosses midnight
   return (current >= start || current < end);
}

bool IsRolloverWindow(datetime t)
{
   if(!UseRolloverWindow) return false;
   return IsTimeInWindow(t, RolloverStartHour, RolloverStartMinute, RolloverEndHour, RolloverEndMinute);
}

bool GetBufferValue(int handle, int bufferIndex, int shift, double &outVal)
{
   double buf[];
   ArraySetAsSeries(buf, true);

   if(CopyBuffer(handle, bufferIndex, shift, 1, buf) != 1) return false;

   outVal = buf[0];
   return true;
}

double NormalizeVolumeToStep(double vol, bool roundUp)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(step <= 0) step = 0.01;

   double steps = roundUp ? MathCeil(vol / step) : MathFloor(vol / step);
   double v = steps * step;

   if(v < vmin) v = 0.0;
   if(v > vmax) v = vmax;

   // normalize digits for display
   int digits = (int)MathRound(-MathLog10(step));
   if(digits < 0) digits = 2;

   return NormalizeDouble(v, digits);
}

bool CalcRiskFor1Lot(double stopPoints, double &lossPerLotAUD)
{
   // Generic point value:
   // tick_value = value of one tick_size move for 1 lot (usually in deposit currency in tester)
   // value_per_point = tick_value * (_Point / tick_size)
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0 || tickSize <= 0 || _Point <= 0)
   {
      Dbg(StringFormat("FAIL: tickValue=%g tickSize=%g point=%g", tickValue, tickSize, _Point));
      return false;
   }

   double valuePerPoint = tickValue * (_Point / tickSize);
   lossPerLotAUD = stopPoints * valuePerPoint;

   return (lossPerLotAUD > 0);
}

double ValuePerPointPerLot()
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0 || tickSize <= 0 || _Point <= 0) return 0.0;

   return tickValue * (_Point / tickSize);
}

double CalcRiskForVolume(double lossPerLotSL, double commissionRoundTurnPerLot, double vol)
{
   double riskSL = lossPerLotSL * vol;
   double riskCommission = commissionRoundTurnPerLot * vol;
   return IncludeCommissionInRisk ? (riskSL + riskCommission) : riskSL;
}

bool BuildOrderParams(ENUM_ORDER_TYPE type, double &price, double &sl, double &tp, double &vol, double &riskRoundedAUD)
{
   // RR guard
   double rr = RiskReward;
   if(rr < 2.0) rr = 2.0;

   // price
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0)
   {
      Dbg("FAIL: ask/bid <= 0");
      return false;
   }

   price = (type == ORDER_TYPE_BUY) ? ask : bid;

   // ATR from closed candle [1]
   double atr = 0.0;
   if(!GetBufferValue(hATR, 0, 1, atr))
   {
      Dbg("FAIL: ATR CopyBuffer");
      return false;
   }
   if(atr <= 0)
   {
      Dbg("FAIL: atr <= 0");
      return false;
   }

   double slDistPrice = atr * ATRMultiple;
   double stopPoints  = slDistPrice / _Point;
   if(stopPoints <= 0)
   {
      Dbg("FAIL: stopPoints <= 0");
      return false;
   }

   // broker stops-level check (in points)
   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopPoints = (stopsLevel > 0) ? (stopsLevel + 1) : 0.0;
   Dbg(StringFormat("INFO: atr=%g stopPoints=%g stopsLevel=%d", atr, stopPoints, stopsLevel));
   if(minStopPoints > 0.0 && stopPoints < minStopPoints)
   {
      Dbg("WARN: stopPoints < stopsLevel, widening to broker minimum");
      stopPoints = minStopPoints;
      slDistPrice = stopPoints * _Point;
   }

   // money loss per 1.00 lot if SL hit (size using actual stop distance)
   double lossPerLotSL = 0.0;
   if(!CalcRiskFor1Lot(stopPoints, lossPerLotSL))
   {
      Dbg("FAIL: tick value/size invalid for risk calc");
      return false;
   }

   // commission per lot (round-turn)
   double commissionRoundTurnPerLot = 2.0 * CommissionPerLotPerSide;
   double totalRiskPerLot = lossPerLotSL;
   if(IncludeCommissionInRisk)
      totalRiskPerLot += commissionRoundTurnPerLot;

   if(totalRiskPerLot <= 0)
   {
      Dbg("FAIL: totalRiskPerLot <= 0");
      return false;
   }

   // lot sizing by money risk
   double volRaw = RiskAUD_Target / totalRiskPerLot;
   double volDown = NormalizeVolumeToStep(volRaw, false);
   double volUp = NormalizeVolumeToStep(volRaw, true);

   double bestVol = 0.0;
   double bestRisk = 0.0;
   double bestDiff = 1e100;

   double candidates[2] = {volDown, volUp};
   for(int i = 0; i < 2; i++)
   {
      double volCandidate = candidates[i];
      if(volCandidate <= 0) continue;

      double riskCandidate = CalcRiskForVolume(lossPerLotSL, commissionRoundTurnPerLot, volCandidate);
      if(riskCandidate < RiskAUD_Min || riskCandidate > RiskAUD_Max) continue;

      double diff = MathAbs(RiskAUD_Target - riskCandidate);
      if(diff < bestDiff)
      {
         bestDiff = diff;
         bestVol = volCandidate;
         bestRisk = riskCandidate;
      }
   }

   vol = bestVol;
   if(vol <= 0)
   {
      Dbg(StringFormat("FAIL: rounded risk outside bounds (min=%g max=%g)", RiskAUD_Min, RiskAUD_Max));
      return false;
   }

   double riskTotal = bestRisk;
   riskRoundedAUD = riskTotal;

   // hard risk filters based on actual SL distance
   if(riskTotal < RiskAUD_Min || riskTotal > RiskAUD_Max)
   {
      Dbg("FAIL: risk outside bounds after rounding");
      return false;
   }

   // optional max-risk guard using buffer (does not affect sizing)
   if(RiskSlippageBufferPoints > 0)
   {
      double bufferedStopPoints = stopPoints + RiskSlippageBufferPoints;
      double lossPerLotBuffered = 0.0;
      if(!CalcRiskFor1Lot(bufferedStopPoints, lossPerLotBuffered))
      {
         Dbg("FAIL: buffered risk calc invalid");
         return false;
      }

      double riskBuffered = lossPerLotBuffered * vol;
      if(IncludeCommissionInRisk)
         riskBuffered += commissionRoundTurnPerLot * vol;

      if(riskBuffered > RiskAUD_Max)
      {
         Dbg("FAIL: buffered risk exceeds max");
         return false;
      }
   }

   if(type == ORDER_TYPE_BUY)
   {
      sl = price - slDistPrice;
      tp = price + slDistPrice * rr;
   }
   else
   {
      sl = price + slDistPrice;
      tp = price - slDistPrice * rr;
   }

   // enforce TP >= 2R (already true if rr>=2, but keep explicit)
   double tpDist = MathAbs(tp - price);
   if(tpDist < (2.0 * MathAbs(price - sl)))
   {
      Dbg("FAIL: TP < 2R guard");
      return false;
   }

   // Optionally adjust TP so RR is net of commission
   if(AdjustTPForCommission)
   {
      double valuePerPoint = ValuePerPointPerLot();
      if(valuePerPoint > 0.0)
      {
         double commissionRT = riskCommission;
         double extraPoints = commissionRT / (valuePerPoint * vol);
         double extraPrice = extraPoints * _Point;

         if(type == ORDER_TYPE_BUY)
            tp += extraPrice;
         else
            tp -= extraPrice;
      }
   }

   return true;
}

bool InPosition()
{
   if(!EnforceOneTradeAtATime) return false;
   return PositionSelect(_Symbol);
}

// Signal:
// - Determine trend on CLOSED candle [1]
// - Enter on NEW bar if candle [1] is counter-trend (bearish in uptrend; bullish in downtrend)
bool GetSignal(ENUM_ORDER_TYPE &outType)
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

void TryCloseForBlackout()
{
   if(!CloseDuringBlackout) return;
   if(!PositionSelect(_Symbol)) return;

   // If within blackout, close immediately on this tick.
   if(IsBlackout(TimeCurrent()))
   {
      trade.PositionClose(_Symbol);
   }
}

void TryCloseForRollover()
{
   if(!CloseBeforeRollover) return;
   if(!PositionSelect(_Symbol)) return;
   if(IsRolloverWindow(TimeCurrent()))
   {
      trade.PositionClose(_Symbol);
   }
}

// ---------- MT5 lifecycle ----------
int OnInit()
{
   Print("PBEMA_ATR_RR INIT BUILD ", __DATE__, " ", __TIME__, " Debug=", Debug);

   // Indicator handles
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

   hATR = iATR(_Symbol, _Period, ATRPeriod);
   if(hATR == INVALID_HANDLE) return INIT_FAILED;

   trade.SetDeviationInPoints(SlippagePoints);

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(hFast  != INVALID_HANDLE) IndicatorRelease(hFast);
   if(hSlow  != INVALID_HANDLE) IndicatorRelease(hSlow);
   if(hTrend != INVALID_HANDLE) IndicatorRelease(hTrend);
   if(hATR   != INVALID_HANDLE) IndicatorRelease(hATR);
}

void OnTick()
{
   static long ticks = 0;
   ticks++;
   if(Debug && (ticks % 5000) == 0)
      Comment("PBEMA_ATR_RR ticks=", ticks, " time=", TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS));

   // Enforce "no open trade during blackout"
   TryCloseForBlackout();
   TryCloseForRollover();

   if(!IsNewBar()) return;

   // No new trades during blackout
   if(IsBlackout(TimeCurrent())) return;
   if(IsRolloverWindow(TimeCurrent())) return;

   // One trade at a time (per symbol)
   if(InPosition()) return;

   // Signal
   ENUM_ORDER_TYPE sigType;
   if(!GetSignal(sigType)) return;

   // Build order params (includes all hard filters)
   double price=0, sl=0, tp=0, vol=0, riskRounded=0;
   if(!BuildOrderParams(sigType, price, sl, tp, vol, riskRounded)) return;

   // Send
   bool ok = false;
   if(sigType == ORDER_TYPE_BUY)
      ok = trade.Buy(vol, _Symbol, price, sl, tp, "PullbackEMA_ATR_RR");
   else
      ok = trade.Sell(vol, _Symbol, price, sl, tp, "PullbackEMA_ATR_RR");

   if(!ok)
   {
      // keep silent; Strategy Tester log will show errors if any
   }
}
