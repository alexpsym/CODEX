#property strict
#property description "Trendline limit-order EA: press button to place a limit order exactly on a trendline, with manual SL/TP. Repositions each new candle by deleting+recreating pending order to preserve AUD risk sizing (commission-aware)."
#property version   "1.00"

#include <Trade/Trade.mqh>

CTrade trade;

// -------------------- Inputs (risk model copied from PullbackEMA_ATR_RR) --------------------
input double RiskAUD_Target           = 10.0;   // target AUD risk per trade (position size is derived from this)
input double RiskAUD_Min              = 9.0;    // hard filter: do NOT trade if rounded risk < this
input double RiskAUD_Max              = 12.0;   // hard filter: do NOT trade if rounded risk > this
input bool   IncludeCommissionInRisk  = true;   // include commission in lot sizing + risk filters
input double CommissionPerLotPerSide  = 3.50;   // commission per side per 1.00 lot (account currency)
input int    RiskSlippageBufferPoints = 50;     // buffer added to stop distance for sizing (points)
input int    SlippagePoints           = 10;

// -------------------- Inputs (trendline execution) --------------------
enum TL_Direction
{
   TL_BUY_LIMIT  = 0,
   TL_SELL_LIMIT = 1
};

input TL_Direction Direction          = TL_BUY_LIMIT;

// Manual protection (prices)
input double ManualSL_Price           = 0.0;    // REQUIRED: SL price
input double ManualTP_Price           = 0.0;    // REQUIRED: TP price

// Trendline selection
input string TrendlineObjectName      = "";     // if blank, EA can pick from clicks (EnablePickTrendlineByClick=true)
input bool   EnablePickTrendlineByClick = true; // click a trendline on chart to set it as active

// Order housekeeping
input int    MagicNumber              = 91001;
input bool   EnforceOneTradeAtATime   = true;   // blocks if there's an open position on this symbol
input bool   AlsoBlockIfPendingExists = true;   // blocks if another EA pending order exists on this symbol (same magic)

// Chart UI
input bool   ShowButtons              = true;
input int    UIButtonCorner           = 0;      // 0=left-top, 1=right-top, 2=left-bottom, 3=right-bottom
input int    UIButtonX                = 10;
input int    UIButtonY                = 20;

// -------------------- Internals --------------------
string  g_trendName      = "";
bool    g_armed          = false;
ulong   g_ticket         = 0;
datetime g_lastBarTime   = 0;

string BTN_PLACE  = "TL_EA_BTN_PLACE";
string BTN_CANCEL = "TL_EA_BTN_CANCEL";
string LBL_STATUS = "TL_EA_STATUS";

string EA_COMMENT = "Trendline_Limit_RiskAUD";

// -------------------- Helpers --------------------
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

   if(v < vmin) v = 0.0;
   if(v > vmax) v = vmax;

   int digits = (int)MathRound(-MathLog10(step));
   if(digits < 0) digits = 2;

   return NormalizeDouble(v, digits);
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

bool TrendlineExists(const string name)
{
   if(name == "") return false;
   if(ObjectFind(0, name) < 0) return false;
   long t = ObjectGetInteger(0, name, OBJPROP_TYPE);
   return (t == OBJ_TREND);
}

double GetTrendlinePriceAtTime(const string name, datetime t)
{
   // For trendline, line_id=0 is fine
   double v = ObjectGetValueByTime(0, name, t, 0); // returns price for specified time :contentReference[oaicite:3]{index=3}
   return v;
}

bool ValidateSLTP(double entry, double sl, double tp, bool isBuyLimit, string &why)
{
   if(sl <= 0.0 || tp <= 0.0)
   {
      why = "ManualSL_Price and ManualTP_Price must both be set (>0).";
      return false;
   }

   if(isBuyLimit)
   {
      if(!(sl < entry && entry < tp))
      {
         why = "For BUY LIMIT, must be SL < Entry < TP.";
         return false;
      }
   }
   else
   {
      if(!(tp < entry && entry < sl))
      {
         why = "For SELL LIMIT, must be TP < Entry < SL.";
         return false;
      }
   }

   // Stops level guard (broker minimum distance, in points)
   int stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0)
   {
      if(MathAbs(entry - sl) < stopsLevel * _Point)
      {
         why = "SL too close to entry for broker stops-level.";
         return false;
      }
      if(MathAbs(entry - tp) < stopsLevel * _Point)
      {
         why = "TP too close to entry for broker stops-level.";
         return false;
      }
   }

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

   // BuyLimit must be below current market; SellLimit must be above current market :contentReference[oaicite:4]{index=4}
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
   double stopPoints = MathAbs(entry - sl) / _Point;
   if(stopPoints <= 0)
   {
      why = "Stop distance is zero/invalid.";
      return false;
   }

   double effectiveStopPoints = stopPoints + MathMax(0, RiskSlippageBufferPoints);

   double lossPerLotSL = 0.0;
   if(!CalcRiskFor1Lot(effectiveStopPoints, lossPerLotSL))
   {
      why = "Failed to compute tick value based risk for 1 lot.";
      return false;
   }

   double commissionRTPerLot = 2.0 * CommissionPerLotPerSide;

   double totalRiskPerLot = lossPerLotSL;
   if(IncludeCommissionInRisk)
      totalRiskPerLot += commissionRTPerLot;

   if(totalRiskPerLot <= 0)
   {
      why = "Total risk per lot invalid.";
      return false;
   }

   double volRaw = RiskAUD_Target / totalRiskPerLot;
   double vol = NormalizeVolume(volRaw);
   if(vol <= 0)
   {
      why = "Computed volume rounds to 0 (below broker min lot).";
      return false;
   }

   // recompute rounded risk with rounded volume
   double riskSL = lossPerLotSL * vol;
   double riskCommission = commissionRTPerLot * vol;
   double riskTotal = IncludeCommissionInRisk ? (riskSL + riskCommission) : riskSL;

   outVol = vol;
   outRiskRoundedAUD = riskTotal;

   if(riskTotal < RiskAUD_Min || riskTotal > RiskAUD_Max)
   {
      why = "Rounded risk is outside RiskAUD_Min/Max filters.";
      return false;
   }

   why = "";
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

   // If ticket not found, treat as already gone
   if(!OrderSelect((ulong)t)) return true;

   return trade.OrderDelete((ulong)t);
}

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

   // PLACE button
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

   // CANCEL button
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

   // Status label
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

bool PlaceOrReplacePending(const bool firstTime)
{
   if(!TrendlineExists(g_trendName))
   {
      UpdateStatus("No valid trendline selected.");
      return false;
   }

   bool isBuyLimit = (Direction == TL_BUY_LIMIT);

   // Price at the OPEN time of the current bar; updates once per new candle
   datetime barTime = iTime(_Symbol, _Period, 0);
   double entry = GetTrendlinePriceAtTime(g_trendName, barTime);
   if(entry <= 0.0)
   {
      UpdateStatus("Trendline price unavailable (enable Ray Right?).");
      return false;
   }

   entry = NormalizePrice(entry);
   double sl = NormalizePrice(ManualSL_Price);
   double tp = NormalizePrice(ManualTP_Price);

   string why="";
   if(!ValidateSLTP(entry, sl, tp, isBuyLimit, why))
   {
      UpdateStatus("SL/TP invalid: " + why);
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

   // If required, block if another pending exists (same magic)
   if(AlsoBlockIfPendingExists)
   {
      ulong other=0;
      if(PendingOrderExistsByMagic(other))
      {
         // If it's our own ticket, fine; otherwise block
         if(g_ticket == 0 || other != g_ticket)
         {
            UpdateStatus("Blocked: existing pending order (same magic).");
            return false;
         }
      }
   }

   // Delete old ticket if present (volume cannot be modified on pending order -> recreate) :contentReference[oaicite:5]{index=5}
   if(g_ticket != 0)
      DeleteTicketIfExists(g_ticket);

   bool ok=false;
   if(isBuyLimit)
      ok = trade.BuyLimit(vol, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, EA_COMMENT);  // :contentReference[oaicite:6]{index=6}
   else
      ok = trade.SellLimit(vol, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, EA_COMMENT); // :contentReference[oaicite:7]{index=7}

   // BuyLimit/SellLimit "true" != guaranteed server accept; check ResultOrder/Retcode :contentReference[oaicite:8]{index=8}
   ulong newTicket = (ulong)trade.ResultOrder();
   int ret = (int)trade.ResultRetcode();

   if(!ok || newTicket == 0)
   {
      UpdateStatus("Order failed. Retcode=" + IntegerToString(ret));
      g_ticket = 0;
      return false;
   }

   g_ticket = newTicket;

   string side = isBuyLimit ? "BUY LIMIT" : "SELL LIMIT";
   UpdateStatus(
      (g_armed ? "ARMED " : "") +
      side +
      " | TL=" + g_trendName +
      " | Entry=" + DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) +
      " | Vol=" + DoubleToString(vol, 2) +
      " | Risk~" + DoubleToString(riskRounded, 2)
   );

   return true;
}

void Arm()
{
   if(InPosition())
   {
      UpdateStatus("Blocked: already in position.");
      return;
   }

   g_armed = true;
   PlaceOrReplacePending(true);
}

void DisarmAndCancel()
{
   g_armed = false;

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

void MaybeStopIfFilled()
{
   // If position exists, assume pending filled; stop managing
   if(PositionSelect(_Symbol))
   {
      g_armed = false;
      g_ticket = 0;
      UpdateStatus("FILLED -> management stopped");
   }
}

// -------------------- MT5 lifecycle --------------------
int OnInit()
{
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetExpertMagicNumber(MagicNumber);

   RefreshActiveTrendlineFromInput();
   EnsureUI();

   // Initialize bar time
   g_lastBarTime = iTime(_Symbol, _Period, 0);

   UpdateStatus("Ready. Trendline=" + (g_trendName=="" ? "<none>" : g_trendName));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   RemoveUI();
}

void OnTick()
{
   MaybeStopIfFilled();

   if(!g_armed) return;

   if(IsNewBar())
   {
      // Reposition order on each new candle (delete + recreate to keep risk sizing consistent) :contentReference[oaicite:9]{index=9}
      PlaceOrReplacePending(false);
   }
}

// Buttons + optional trendline pick
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      // Button clicks
      if(sparam == BTN_PLACE)
      {
         RefreshActiveTrendlineFromInput();
         Arm();
         return;
      }
      if(sparam == BTN_CANCEL)
      {
         DisarmAndCancel();
         return;
      }

      // Trendline pick by click
      if(EnablePickTrendlineByClick)
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
