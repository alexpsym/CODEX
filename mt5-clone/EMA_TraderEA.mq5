//+------------------------------------------------------------------+
//|   AUD EMA Trader EA (Improved for beginners)                    |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Pepperstone EA"
#property version   "7.0"
#property strict

#include <Trade/Trade.mqh>
#include <Indicators/Trend.mqh>

//--- trading objects
CTrade  trade;
CiMA    maFast;
CiMA    maSlow;

//--- live trading options
input group "Live trading options";
input int    FastEMA     = 9;      // fast EMA period
input int    SlowEMA     = 20;     // slow EMA period
input bool   BounceFromFast = true; // true = bounce from FastEMA, false = from SlowEMA
input double NearPct     = 0.5;    // allowed distance from EMA (%)
input double RiskAUD     = 10.0;   // risk per trade in AUD
input int    StopPips    = 15;     // stop loss in pips
input int    TakePips    = 30;     // take profit if RRRatio==0
input double RRRatio     = 2.0;    // reward:risk ratio
input bool   AtrSL       = false;  // use ATR stop loss
input double AtrFactor   = 1.5;    // ATR multiplier
input uint   MagicNum    = 20240405; // unique EA ID

//--- backtest options
input group "Backtest options";
input bool   FixLotTest  = false;  // use fixed lot when testing
input double TestLot     = 0.01;   // fixed lot size for tests
input double TestRR      = 2.0;    // test reward:risk ratio

//--- misc options
input group "Misc options";
input bool   Hotkey      = true;   // press J to toggle the EA
input int    NyCloseBne  = 7;      // NY close / Asian open (Brisbane)
input int    ServerGMT   = 3;      // Pepperstone server GMT offset (+2 when DST off)
input group "";

//--- global variables
bool   eaEnabled      = true;     // is the EA currently active?
string currentSymbol;             // symbol we trade on
string lastStatus     = "";       // message shown on chart
bool   inRestricted   = false;    // are we in the restricted time?

//+------------------------------------------------------------------+
//| Helper to extract hour from datetime                             |
//+------------------------------------------------------------------+
int GetHour(datetime t)
  {
   MqlDateTime s;
   TimeToStruct(t, s);
   return(s.hour);
  }

//+------------------------------------------------------------------+
//| Check if current time is in the restricted window                |
//+------------------------------------------------------------------+
bool TradingTimeRestricted()
  {
   datetime server = TimeTradeServer();
   // server GMT offset provided by user (e.g. 3 during US DST, 2 otherwise)
   int offset      = ServerGMT;
   // convert server time to Brisbane (UTC+10)
   datetime bneTime = server + (10 - offset) * 3600;
   int hourBNE      = GetHour(bneTime);

   int start = (NyCloseBne - 3 + 24) % 24;  // 3h before NY close
   int end   = (NyCloseBne + 3) % 24;       // 3h after

   if(start < end)
      return(hourBNE >= start && hourBNE < end);
   else
      return(hourBNE >= start || hourBNE < end);
  }

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   currentSymbol = _Symbol;

   // validate inputs so they are sensible
   if(FastEMA <= 0 || SlowEMA <= 0 || StopPips <= 0 || RRRatio <= 0 ||
      NearPct <= 0)
    {
      Print("Error: input values must be greater than zero.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   // create EMA indicators
   if(!maFast.Create(currentSymbol, PERIOD_CURRENT, FastEMA, 0, MODE_EMA, PRICE_CLOSE))
     {
      Print("Failed to create fast EMA. Error ", GetLastError());
      return(INIT_FAILED);
     }
   if(!maSlow.Create(currentSymbol, PERIOD_CURRENT, SlowEMA, 0, MODE_EMA, PRICE_CLOSE))
     {
      Print("Failed to create slow EMA. Error ", GetLastError());
      return(INIT_FAILED);
     }

  maFast.Refresh();
  maSlow.Refresh();
  // use MagicNum input for unique identifier
  trade.SetExpertMagicNumber(MagicNum);
  trade.SetTypeFilling(ORDER_FILLING_FOK);


  // create trade log file
  int handle = FileOpen("FX_EMA_TradeLog.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
  if(handle != INVALID_HANDLE)
    {
      FileWrite(handle,
               "DateServer",
               "TimeServer",
               "DateBNE",
               "TimeBNE",
               "Symbol",
               "Type",
               "Lots",
               "Price",
               "SL",
               "TP",
               "ATRUsed",
               "Result");
      FileClose(handle);
      // trade log created successfully
    }
  else
      Print("Failed to open trade log file.");

  inRestricted = TradingTimeRestricted();
  if(inRestricted)
     Print("EA started during restricted trading time. Trading paused.");
  else
     Print("EA started outside restricted trading time.");

  Print("=== EA INITIALIZED ===");
   Comment("EA ENABLED");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Calculate ATR stop loss in points                                |
//+------------------------------------------------------------------+
int CalculateATRPoints()
  {
   double atr[];
   if(CopyBuffer(iATR(currentSymbol, PERIOD_CURRENT, 14), 0, 0, 1, atr) > 0)
     {
      return(int)MathRound((atr[0] * AtrFactor) / _Point);
     }
   Print("ATR fetch failed—using fixed stop loss.");
   return(StopPips * 10); // convert pips to points
  }

//+------------------------------------------------------------------+
//| Calculate lot size                                               |
//+------------------------------------------------------------------+
double CalculateLotSize()
  {
   if(FixLotTest)
      return(TestLot);

   double tickValue = SymbolInfoDouble(currentSymbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(currentSymbol, SYMBOL_TRADE_TICK_SIZE);
   int    digits    = (int)SymbolInfoInteger(currentSymbol, SYMBOL_DIGITS);
   if(tickValue <= 0 || tickSize <= 0)
     {
      Print("Invalid tick data.");
      return(0.0);
     }

   double pipSize  = MathPow(10.0, -digits + 1);
   double pipValue = tickValue * pipSize / tickSize;
   double slPips   = AtrSL ? (double)CalculateATRPoints() / 10.0 : StopPips;
   double riskPerLot = slPips * pipValue;
   double rawLots = RiskAUD / riskPerLot;

   double minLot  = SymbolInfoDouble(currentSymbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(currentSymbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(currentSymbol, SYMBOL_VOLUME_STEP);

   double lots = MathFloor(rawLots / lotStep) * lotStep;
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;

   // calculate final risk after rounding
   double finalRisk = lots * riskPerLot;

   // calculate allowed risk range of ±1 AUD around the requested amount
   double lowerRisk = RiskAUD - 1.0;
   double upperRisk = RiskAUD + 1.0;

   // adjust lots if risk is outside the allowed range
   if(finalRisk > upperRisk)
     lots = MathFloor(upperRisk / riskPerLot / lotStep) * lotStep;
   else if(finalRisk < lowerRisk)
     lots = MathCeil(lowerRisk / riskPerLot / lotStep) * lotStep;

   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;

   finalRisk = lots * riskPerLot;
   if(finalRisk < lowerRisk || finalRisk > upperRisk)
     {
      Print("Risk ", finalRisk, " AUD outside limits; trade skipped.");
      return(0.0);
     }

   return(lots);
  }

//+------------------------------------------------------------------+
//| Log trade result                                                 |
//+------------------------------------------------------------------+
void LogTrade(string type, double lots, double price, double sl, double tp, string result)
  {
   string dateStrServer = TimeToString(TimeCurrent(), TIME_DATE);
   string timeStrServer = TimeToString(TimeCurrent(), TIME_SECONDS);

   datetime server   = TimeTradeServer();
   // use configured server GMT offset for consistency
   int offset        = ServerGMT;
   datetime bneTime  = server + (10 - offset) * 3600; // convert to Brisbane
   string dateStrBNE = TimeToString(bneTime, TIME_DATE);
   string timeStrBNE = TimeToString(bneTime, TIME_SECONDS);

  int handle = FileOpen("FX_EMA_TradeLog.csv", FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI);
  if(handle != INVALID_HANDLE)
    {
      FileSeek(handle, 0, SEEK_END);
      FileWrite(handle,
                dateStrServer,
                timeStrServer,
                dateStrBNE,
                timeStrBNE,
                currentSymbol,
                type,
                lots,
                price,
                sl,
                tp,
                AtrSL,
                result);
      FileClose(handle);
      // record added to trade log
    }
  else
      Print("Failed to log trade.");
  }

//+------------------------------------------------------------------+
//| Helper to open buy trade                                         |
//+------------------------------------------------------------------+
void CheckBuy(double ema)
  {
   double bid = SymbolInfoDouble(currentSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(currentSymbol, SYMBOL_ASK);
   double threshold = ema * (NearPct / 100.0);

   // ensure price is at or above the EMA and close enough
   if(ask < ema || ask > ema + threshold)
      return;

   double prevClose = iClose(currentSymbol, PERIOD_CURRENT, 1);
   double prevEMA   = maFast.Main(1);
   if(prevClose <= prevEMA)
      return; // previous candle not above EMA

   // price must have approached the EMA from above during this bar
   double curLow = iLow(currentSymbol, PERIOD_CURRENT, 0);
   if(curLow > ema)
      return;

   double slPoints = AtrSL ? CalculateATRPoints() : StopPips * 10;
   double rr       = FixLotTest ? TestRR : RRRatio;
   double tpPoints = slPoints * rr;

   double sl = ask - slPoints * _Point;
   double tp = ask + tpPoints * _Point;
   double lots = CalculateLotSize();
   if(lots <= 0) return;

   if(trade.Buy(lots, currentSymbol, ask, sl, tp, "BUY|EMA"))
     {
      lastStatus = "BUY opened";
      LogTrade("BUY", lots, ask, sl, tp, "SUCCESS");
     }
   else
     {
      int err = GetLastError();
      Print("Buy failed: ", err);
      lastStatus = "BUY failed";
      LogTrade("BUY", lots, ask, sl, tp, "ERROR " + (string)err);
     }
  }

//+------------------------------------------------------------------+
//| Helper to open sell trade                                        |
//+------------------------------------------------------------------+
void CheckSell(double ema)
  {
   double bid = SymbolInfoDouble(currentSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(currentSymbol, SYMBOL_ASK);
   double threshold = ema * (NearPct / 100.0);

   // ensure price is at or below the EMA and close enough
   if(bid > ema || bid < ema - threshold)
      return;

   double prevClose = iClose(currentSymbol, PERIOD_CURRENT, 1);
   double prevEMA   = maFast.Main(1);
   if(prevClose >= prevEMA)
      return; // previous candle not below EMA

   // price must have approached the EMA from below during this bar
   double curHigh = iHigh(currentSymbol, PERIOD_CURRENT, 0);
   if(curHigh < ema)
      return;

   double slPoints = AtrSL ? CalculateATRPoints() : StopPips * 10;
   double rr       = FixLotTest ? TestRR : RRRatio;
   double tpPoints = slPoints * rr;

   double sl = bid + slPoints * _Point;
   double tp = bid - tpPoints * _Point;
   double lots = CalculateLotSize();
   if(lots <= 0) return;

   if(trade.Sell(lots, currentSymbol, bid, sl, tp, "SELL|EMA"))
     {
      lastStatus = "SELL opened";
      LogTrade("SELL", lots, bid, sl, tp, "SUCCESS");
     }
   else
     {
      int err = GetLastError();
      Print("Sell failed: ", err);
      lastStatus = "SELL failed";
      LogTrade("SELL", lots, bid, sl, tp, "ERROR " + (string)err);
     }
  }

//+------------------------------------------------------------------+
//| Execute trade logic                                              |
//+------------------------------------------------------------------+
void ExecuteTrade()
  {
  if(!eaEnabled)
      return;
   bool nowRestricted = TradingTimeRestricted();
   if(nowRestricted != inRestricted)
     {
      inRestricted = nowRestricted;
      if(nowRestricted)
         Print("EA has entered the restricted trading time block at ",
               TimeToString(TimeTradeServer(), TIME_SECONDS));
      else
         Print("Restricted trading time finished at ",
               TimeToString(TimeTradeServer(), TIME_SECONDS),
               ". Trading can resume.");
     }
   if(nowRestricted)
     {
      lastStatus = "session pause";
      return;
     }
  if(PositionSelect(currentSymbol))
      return; // already have a position on this symbol
   if(Bars(currentSymbol, PERIOD_CURRENT) < MathMax(FastEMA, SlowEMA))
      return; // not enough bars to calculate EMAs

   maFast.Refresh();
   maSlow.Refresh();
   double fast = maFast.Main(0);
   double slow = maSlow.Main(0);
   if(fast <= 0 || fast == DBL_MAX || slow <= 0 || slow == DBL_MAX)
      return;
   double bounce = BounceFromFast ? fast : slow;
   if(fast > slow)
      CheckBuy(bounce);
   else if(fast < slow)
      CheckSell(bounce);
  }

//+------------------------------------------------------------------+
//| Tick handler                                                     |
//+------------------------------------------------------------------+
void OnTick()
  {
   ExecuteTrade();
   Comment((eaEnabled ? "EA ENABLED" : "EA DISABLED"), " | ", lastStatus);
  }

//+------------------------------------------------------------------+
//| Hotkey toggle                                                    |
//+------------------------------------------------------------------+
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
  {
   if(Hotkey && id == CHARTEVENT_KEYDOWN && lparam == 74) // J key
     {
      eaEnabled = !eaEnabled;
      lastStatus = eaEnabled ? "enabled" : "disabled";
      Print("EA ", (eaEnabled ? "ENABLED" : "DISABLED"));
     }
  }

