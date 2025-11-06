//+------------------------------------------------------------------+
//|   Auto Trendline Trader + Position Sizer EA (Improved v4.2)     |
//+------------------------------------------------------------------+
#property copyright "2024"
#property version   "4.2"
#property strict

#include <Trade\Trade.mqh>
#include <Object.mqh>

// log file for executed trades
string gLogFile = "AutoTrendlineTradeLog.txt";

CTrade trade;

// ENUMS
enum ENUM_RISK_MODE   { RISK_FIXED_PERCENT = 0, RISK_FIXED_AUD = 1 };
enum ENUM_TARGET_MODE { TARGET_FIXED_PERCENT = 0, TARGET_FIXED_AUD = 1 };
enum ENUM_PS_ASSET_CLASS
  {
   PS_ASSET_FX = 0,
   PS_ASSET_COMMODITY,
   PS_ASSET_INDEX,
   PS_ASSET_CRYPTO,
   PS_ASSET_SHARE,
   PS_ASSET_OTHER
  };

// INPUTS
input string          TrendlineName   = "EntryLine";         // Trendline object name
input string          CancelLineName  = "ExitLine";          // Optional exit/cancel line name
input ENUM_ORDER_TYPE TradeType       = ORDER_TYPE_BUY;       // BUY or SELL
input ENUM_RISK_MODE  RiskMode        = RISK_FIXED_PERCENT;   // Risk mode
input double          FixedRiskAUD    = 50.0;                 // Fixed risk amount
input double          RiskPercent     = 1.0;                  // Percent risk per trade
input double          StopLossPoints  = 30.0;                 // Stop loss in points
input bool            UseATRStop      = false;                // Use ATR-based stop loss
input int             ATRPeriod       = 14;                   // ATR period
input double          ATRMultiplier   = 1.5;                  // ATR multiplier
input ENUM_TARGET_MODE TargetMode     = TARGET_FIXED_PERCENT; // TP mode
input double          TargetFixedAUD  = 20.0;                 // Fixed AUD TP or percent
input double          CommissionPerLot= 7.0;                  // Commission per lot (AUD)
input int             TimeOffsetHours = 0;                    // Hours to add to terminal time
input int             BlockStartHour  = 5;                    // Block trading from this hour
input int             BlockEndHour    = 9;                    // Block trading until this hour
input int             ServerHoursBehind = 7;                  // MT5 server hours behind local

int  gBlockStartSrv = 0;
int  gBlockEndSrv   = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   // basic input validation
   if(RiskPercent <= 0 || RiskPercent > 100)
     {
      Print("Error: RiskPercent must be between 0 and 100.");
      return(INIT_PARAMETERS_INCORRECT);
     }
  if(StopLossPoints <= 0 && !UseATRStop)
    {
     Print("Error: StopLossPoints must be greater than zero.");
     return(INIT_PARAMETERS_INCORRECT);
    }
  if(BlockStartHour<0 || BlockStartHour>23 || BlockEndHour<0 || BlockEndHour>23)
    {
     Print("Error: Block hours must be between 0 and 23.");
     return(INIT_PARAMETERS_INCORRECT);
    }
  if(TimeOffsetHours<-24 || TimeOffsetHours>24)
    {
     Print("Error: TimeOffsetHours must be between -24 and 24.");
     return(INIT_PARAMETERS_INCORRECT);
    }
  if(ServerHoursBehind<-23 || ServerHoursBehind>23)
    {
     Print("Error: ServerHoursBehind must be between -23 and 23.");
     return(INIT_PARAMETERS_INCORRECT);
    }

  gBlockStartSrv = NormalizeHour(BlockStartHour - ServerHoursBehind);
  gBlockEndSrv   = NormalizeHour(BlockEndHour   - ServerHoursBehind);
  PrintFormat("Block hours %02d-%02d local -> %02d-%02d server",BlockStartHour,BlockEndHour,gBlockStartSrv,gBlockEndSrv);

  // prepare trade log file
  int logHandle = FileOpen(gLogFile, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI);
  if(logHandle != INVALID_HANDLE)
    {
     if(FileSize(logHandle)==0)
        FileWriteString(logHandle, "Date,Time,Symbol,Type,EntryPrice,ExecPrice,SlippagePts,StopLoss,TakeProfit,Volume,Result\r\n");
     FileClose(logHandle);
    }
  else
     Print("Failed to open log file for writing.");

  Print("EA initialized. Waiting for trendline '" + TrendlineName + "'.");
  return(INIT_SUCCEEDED);
 }

// helper: calculate ATR stop loss points
int CalcATRPoints()
  {
   double atr[];
   if(CopyBuffer(iATR(_Symbol,_Period,ATRPeriod),0,0,1,atr)>0)
      return((int)MathRound(atr[0]*ATRMultiplier/_Point));
   Print("Warning: ATR value invalid. Using default StopLossPoints.");
   return((int)StopLossPoints);
  }

// determine if a symbol is a Pepperstone commodity
bool IsPepperstoneCommodity(string symbol)
  {
   string sym = symbol;
   StringToUpper(sym);
   if(StringFind(sym,"XAU")>=0 || StringFind(sym,"XAG")>=0 ||
      StringFind(sym,"XPT")>=0 || StringFind(sym,"XPD")>=0 ||
      StringFind(sym,"BRENT")>=0 || StringFind(sym,"WTI")>=0 ||
      StringFind(sym,"OIL")>=0 || StringFind(sym,"NGAS")>=0 ||
      StringFind(sym,"COFFEE")>=0 || StringFind(sym,"COCOA")>=0 ||
      StringFind(sym,"COTTON")>=0 || StringFind(sym,"SUGAR")>=0)
         return true;
   return false;
  }

// categorize Pepperstone symbols into major asset classes
ENUM_PS_ASSET_CLASS GetPepperstoneAssetClass(string symbol)
  {
   string sym = symbol;
   StringToUpper(sym);

   if(IsPepperstoneCommodity(sym))
      return PS_ASSET_COMMODITY;

   if(StringFind(sym,"US500")>=0 || StringFind(sym,"NAS")>=0 ||
      StringFind(sym,"UK")>=0   || StringFind(sym,"GER")>=0 ||
      StringFind(sym,"JP")>=0   || StringFind(sym,"HK")>=0)
      return PS_ASSET_INDEX;

   if(StringFind(sym,"BTC")>=0 || StringFind(sym,"ETH")>=0 ||
      StringFind(sym,"LTC")>=0 || StringFind(sym,"XRP")>=0)
      return PS_ASSET_CRYPTO;

   bool isFxPair = (StringLen(sym)==6 || StringLen(sym)==7);
   if(isFxPair)
      return PS_ASSET_FX;

   return PS_ASSET_SHARE;
  }

// calculate lot size using PositionSizeFX rules
double CalcLotSize(double riskAmount,double slPoints)
  {
   string symbol=_Symbol;
   int    digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double pipSize=MathPow(10.0,-digits+1);
   double pointSize=SymbolInfoDouble(symbol,SYMBOL_POINT);
   double tickVal=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double pipValue=tickVal*pipSize/tickSize;
   double stopLossPips=slPoints*pointSize/pipSize;

   double commissionPerLot=CommissionPerLot;
   double volumeStepLocal=0.01;
   ENUM_PS_ASSET_CLASS asset=GetPepperstoneAssetClass(symbol);
   if(asset!=PS_ASSET_FX && commissionPerLot==7.0)
      commissionPerLot=0.0;

   double lotSizeRaw=riskAmount/(stopLossPips*pipValue+commissionPerLot);
   double lotSize=MathCeil(lotSizeRaw/volumeStepLocal)*volumeStepLocal;
   int lotPrec=(int)MathRound(MathLog10(1.0/volumeStepLocal));
  lotSize=NormalizeDouble(lotSize,lotPrec);
  return(lotSize);
  }

// log executed trade details including slippage
void LogTrade(string type,double entryPrice,double execPrice,double stopPrice,double tpPrice,double volume,uint result)
  {
   double slippagePts=MathAbs(execPrice-entryPrice)/_Point;
   int handle=FileOpen(gLogFile,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle!=INVALID_HANDLE)
     {
      FileSeek(handle,0,SEEK_END);
      string dt=TimeToString(TimeCurrent(),TIME_DATE);
      string tm=TimeToString(TimeCurrent(),TIME_SECONDS);
      string line=StringFormat("%s,%s,%s,%.5f,%.5f,%.2f,%.5f,%.5f,%.2f,%u\r\n",dt,tm,type,entryPrice,execPrice,slippagePts,stopPrice,tpPrice,volume,result);
      FileWriteString(handle,line);
      FileClose(handle);
     }
  else
     Print("Failed to write log file: ",GetLastError());
  }

// helper: normalize hour to 0..23
int NormalizeHour(int h)
  {
   while(h<0)  h+=24;
   while(h>=24) h-=24;
   return(h);
  }

// helper: check if current server time is within blocked hours
bool IsBlockedTrading()
  {
   datetime t=TimeCurrent();
   MqlDateTime tm; TimeToStruct(t,tm);
   if(gBlockStartSrv==gBlockEndSrv)
      return(false);
   if(gBlockStartSrv<gBlockEndSrv)
      return(tm.hour>=gBlockStartSrv && tm.hour<gBlockEndSrv);
   return(tm.hour>=gBlockStartSrv || tm.hour<gBlockEndSrv);
  }

// helper: close open position if present
void CloseBlockedPosition()
  {
   if(PositionSelect(_Symbol))
     {
      ulong ticket=PositionGetTicket(0);
      if(trade.PositionClose(ticket))
         Print("Position closed due to blocked hours");
      else
         Print("Failed to close position: ",trade.ResultRetcodeDescription());
     }
  }

// helper: close all positions and cancel pending orders
void CloseAllTrades()
  {
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i);
      if(trade.PositionClose(ticket))
         Print("Closed position ",ticket," due to cancel line");
     }

   for(int j=OrdersTotal()-1;j>=0;j--)
     {
      ulong ticket=OrderGetTicket(j);
      if(trade.OrderDelete(ticket))
         Print("Deleted order ",ticket," due to cancel line");
     }
  }

// helper: check if a trendline was touched between two price samples
bool TrendlineTouched(string name,double prevAsk,double prevBid,double currAsk,double currBid)
  {
   if(ObjectFind(0,name)<0)
      return(false);
   datetime t1=(datetime)ObjectGetInteger(0,name,OBJPROP_TIME,0);
   datetime t2=(datetime)ObjectGetInteger(0,name,OBJPROP_TIME,1);
   if(t1==t2)
      return(false);
   double p1=ObjectGetDouble(0,name,OBJPROP_PRICE,0);
   double p2=ObjectGetDouble(0,name,OBJPROP_PRICE,1);

   datetime now=TimeCurrent();
   double price=p1+(p2-p1)*(double)(now-t1)/(double)(t2-t1);
   double low=MathMin(MathMin(prevAsk,prevBid),MathMin(currAsk,currBid));
   double high=MathMax(MathMax(prevAsk,prevBid),MathMax(currAsk,currBid));
   return(price>=low && price<=high);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
  static bool  detected = false;
  static bool  warnedAuto = false;
  static bool  deactivated = false;
  static bool  cancelDetected = false;
  static double lastAsk = 0.0, lastBid = 0.0;

   double currAsk = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double currBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double prevAsk = lastAsk;
   double prevBid = lastBid;
   lastAsk = currAsk;
   lastBid = currBid;

   if(IsBlockedTrading())
     {
      CloseBlockedPosition();
      return;
     }

   // handle cancel line
   if(CancelLineName!="" && TrendlineTouched(CancelLineName,prevAsk,prevBid,currAsk,currBid))
     {
      if(!cancelDetected)
        {
         Print("Cancel line touched. Closing trades and orders.");
         CloseAllTrades();
         cancelDetected=true;
        }
     }
   else
      cancelDetected=false;

   // Only one position at a time
   if(PositionSelect(_Symbol))
      return;

   // Check trendline presence
   if(ObjectFind(0, TrendlineName) < 0)
     {
      detected = false;
      deactivated = false;
      return;
     }

   if(deactivated)
      return;

   // Confirm detection once
     if(!detected)
       {
        detected = true;
        Print("Detected trendline '" + TrendlineName + "'. Monitoring for touches.");
        return;
       }

   // Retrieve endpoints safely
   ResetLastError();
   datetime t1 = (datetime)ObjectGetInteger(0, TrendlineName, OBJPROP_TIME, 0);
   if(_LastError!=0)
     {
      Print("Error reading trendline time 1: ", _LastError);
      return;
     }
   datetime t2 = (datetime)ObjectGetInteger(0, TrendlineName, OBJPROP_TIME, 1);
   if(_LastError!=0)
     {
      Print("Error reading trendline time 2: ", _LastError);
      return;
     }
   ResetLastError();
   double p1 = ObjectGetDouble(0, TrendlineName, OBJPROP_PRICE, 0);
   if(_LastError!=0)
     {
      Print("Error reading trendline price 1: ", _LastError);
      return;
     }
   double p2 = ObjectGetDouble(0, TrendlineName, OBJPROP_PRICE, 1);
   if(_LastError!=0)
     {
      Print("Error reading trendline price 2: ", _LastError);
      return;
     }

   // Calculate trendline price at current time
   datetime currTime = TimeCurrent();
   double priceOnLine = p1 + (p2 - p1) * (double)(currTime - t1) / (double)(t2 - t1);

   // Detect crossing with gap check using min/max
   bool doTrade=false;
   if(TradeType==ORDER_TYPE_BUY)
     {
      double minPrice=MathMin(prevAsk,currAsk);
      double maxPrice=MathMax(prevAsk,currAsk);
      if(priceOnLine>=minPrice && priceOnLine<=maxPrice)
         doTrade=true;
     }
   else
     {
      double minPrice=MathMin(prevBid,currBid);
      double maxPrice=MathMax(prevBid,currBid);
      if(priceOnLine>=minPrice && priceOnLine<=maxPrice)
         doTrade=true;
     }

   if(!doTrade)
      return;

   // Calculate SL, TP, and volume
   double entryPrice = (TradeType==ORDER_TYPE_BUY) ? currAsk : currBid;
   double slPoints = UseATRStop ? CalcATRPoints() : StopLossPoints;
   double stopPrice = (TradeType == ORDER_TYPE_BUY)
                       ? entryPrice - slPoints * _Point
                       : entryPrice + slPoints * _Point;

   double accountBal = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmt = (RiskMode == RISK_FIXED_PERCENT)
                    ? accountBal * RiskPercent / 100.0
                    : FixedRiskAUD;
   double tpAmt = (TargetMode == TARGET_FIXED_PERCENT)
                  ? accountBal * TargetFixedAUD / 100.0
                  : TargetFixedAUD;

   double volume = CalcLotSize(riskAmt, slPoints);
   double minVol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(volume < minVol) volume = minVol;
   if(volume > maxVol) volume = maxVol;

   // value of one price step for one lot in deposit currency
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);

   // convert desired profit (tpAmt) to price distance taking traded volume
   // into account. without dividing by volume the TP would be calculated as
   // if we traded exactly 1 lot and would give a much smaller real profit
   // when trading less.
   // add commission cost so that net profit after commission matches target
   double commissionCost = CommissionPerLot * volume;
   double grossTarget = tpAmt + commissionCost;
   double tpPoints = grossTarget / (tickValue * volume);
   double takeProfit = (TradeType == ORDER_TYPE_BUY)
                       ? entryPrice + tpPoints * _Point
                       : entryPrice - tpPoints * _Point;

   PrintFormat("Entry=%.5f SL=%.5f TP=%.5f Vol=%.2f", entryPrice, stopPrice, takeProfit, volume);

  bool result;
  if(TradeType == ORDER_TYPE_BUY)
     result = trade.Buy(volume, _Symbol, entryPrice, stopPrice, takeProfit);
  else
     result = trade.Sell(volume, _Symbol, entryPrice, stopPrice, takeProfit);

  if(!result)
    {
     uint code = trade.ResultRetcode();
     string msg = trade.ResultRetcodeDescription();
      if(code == 10027 && !warnedAuto)
        {
         warnedAuto = true;
         Print("AutoTrading disabled: please enable for execution.");
         Comment("AutoTrading disabled! Enable to trade.");
        }
      else
         Print(__FILE__, " trade failed code=", code, " desc=", msg);
      LogTrade((TradeType==ORDER_TYPE_BUY)?"BUY":"SELL",entryPrice,trade.ResultPrice(),stopPrice,takeProfit,volume,code);
    }
   else
     {
      ObjectSetInteger(0, TrendlineName, OBJPROP_COLOR, clrWhite); // mark line as inactive
      deactivated=true;             // prevent further executions
      detected=false;               // re-arm if a new line is drawn
      LogTrade((TradeType==ORDER_TYPE_BUY)?"BUY":"SELL",entryPrice,trade.ResultPrice(),stopPrice,takeProfit,volume,0);
     }
  }
//+------------------------------------------------------------------+
