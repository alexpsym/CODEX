#property strict
#property version   "1.00"
#property description "Closed-bar port of the TradingView Range Breakout-Pullback Strategy."

#include <Trade/Trade.mqh>

enum DirectionMode { LongsAndShorts=0, LongsOnly=1, ShortsOnly=2 };
enum DepthMode { AnyValidDepth=0, Shallow=1, Deep=2, CustomRange=3 };
enum ConfirmationMode { Aggressive=0, Balanced=1, Conservative=2 };
enum StopMode { AdaptiveATR=0, FixedATRMultiple=1 };
enum ServerTimeMode { PepperstoneNYClose=0, FixedUTCOffset=1 };
enum SetupMode { RangeBreakoutPullback=0, ImpulsePullbackContinuation=1 };

input group "1. Trade directions"
input DirectionMode InpTradeDirection=LongsAndShorts;
input double InpVolumeLots=0.01;
input ulong InpMagicNumber=26083001;
input int InpDeviationPoints=10;

input group "2. Setup mode"
input SetupMode InpSetupMode=RangeBreakoutPullback;

input group "3. Original horizontal range detection"
input int InpPivotStrength=3;
input int InpMinimumReactions=2;
input double InpClusterToleranceATR=0.35;
input double InpMinimumRangeHeightATR=2.0;
input int InpMinimumRangeBars=10;
input int InpMaximumRangeBars=250;

input group "4. Original range breakout and extension"
input double InpBreakoutBufferATR=0.10;
input double InpMinimumBreakoutBodyATR=0.25;
input double InpMinimumExtensionATR=0.75;
input int InpMaximumExtensionBars=20;

input group "5. Impulse pullback continuation"
input double InpImpulseMinimumBodyATR=1.0;
input double InpImpulseMinimumRangeATR=1.25;
input double InpImpulseMinimumRetracementATR=0.50;
input double InpImpulseMaximumDepth=75.0;
input int InpImpulseMaximumPullbackBars=30;

input group "6. Shared corrective pullback"
input int InpMinimumOpposingCloses=2;
input double InpMinimumRetracementATR=0.50;
input DepthMode InpPullbackDepthMode=AnyValidDepth;
input double InpShallowMinimumDepth=0.0;
input double InpShallowMaximumDepth=50.0;
input double InpDeepMinimumDepth=50.0;
input double InpDeepMaximumDepth=100.0;
input double InpCustomMinimumDepth=0.0;
input double InpCustomMaximumDepth=100.0;
input double InpInvalidationToleranceATR=0.25;
input int InpMaximumPullbackBars=30;

input group "7. Shared price-action resumption"
input ConfirmationMode InpConfirmationMode=Balanced;
input int InpMinorSwingStrength=2;

input group "8. ATR stop and profit target"
input StopMode InpStopMode=AdaptiveATR;
input int InpATRLength=14;
input double InpFixedATRMultiplier=1.5;
input int InpRegimeLookback=200;
input double InpLowRegimePercentile=33.0;
input double InpHighRegimePercentile=67.0;
input double InpLowVolatilityMultiplier=1.0;
input double InpNormalVolatilityMultiplier=1.5;
input double InpHighVolatilityMultiplier=2.0;
input double InpRiskRewardMultiple=2.0;

input group "9. FX Weekend blackout"
input bool InpEnableWeekendBlackout=true;
input ServerTimeMode InpServerTimeMode=PepperstoneNYClose;
input int InpTesterServerUTCOffsetHours=0; // Used only with FixedUTCOffset.

input group "10. Visual and diagnostics"
input bool InpShowBlackoutStatus=true;
input bool InpShowDiagnostics=false;
input bool InpShowBlackoutZones=true;

CTrade trade;
int atrHandle=INVALID_HANDLE;
datetime lastBarTime=0;
long barNumber=0;

double resistanceCentre=0.0,supportCentre=0.0;
int resistanceReactions=0,supportReactions=0;
long resistanceFirstBar=-1,resistanceLastBar=-1,supportFirstBar=-1,supportLastBar=-1;

int stage=0,direction=0; // 0 search, 1 breakout, 2 extension, 3 qualified pullback
double frozenResistance=0.0,frozenSupport=0.0,setupATR=0.0,setupExtreme=0.0;
double frozenExtensionExtreme=0.0,pullbackExtreme=0.0,pullbackMinorSwing=0.0;
bool haveFrozenExtension=false,havePullback=false,haveMinorSwing=false,pullbackStarted=false;
long breakoutBar=-1,extensionBar=-1,pullbackConfirmedBar=-1;
int opposingCloseCount=0;
bool activeImpulseSetup=false;
bool blackoutWasActive=false;
datetime blackoutStartTime=0;

bool IsNewBar()
{
   datetime t=iTime(_Symbol,_Period,0);
   if(t==0 || t==lastBarTime) return false;
   lastBarTime=t;
   return true;
}

double TickSize()
{
   double t=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   return t>0.0 ? t : _Point;
}
double RoundDownTick(double price) { return NormalizeDouble(MathFloor(price/TickSize()+1e-10)*TickSize(),_Digits); }
double RoundUpTick(double price)   { return NormalizeDouble(MathCeil(price/TickSize()-1e-10)*TickSize(),_Digits); }

bool IsLeap(const int year) { return (year%4==0 && (year%100!=0 || year%400==0)); }
int DaysInMonth(const int year,const int month)
{
   int days[]={31,28,31,30,31,30,31,31,30,31,30,31};
   return (month==2 && IsLeap(year)) ? 29 : days[month-1];
}
datetime NthSundayUTC(const int year,const int month,const int nth,const int utcHour)
{
   MqlDateTime d; d.year=year; d.mon=month; d.day=1; d.hour=0; d.min=0; d.sec=0;
   datetime first=StructToTime(d);
   MqlDateTime f; TimeToStruct(first,f);
   int firstSunday=1+((7-f.day_of_week)%7);
   d.day=firstSunday+(nth-1)*7; d.hour=utcHour;
   return StructToTime(d);
}
bool NewYorkDST(datetime utc)
{
   MqlDateTime d; TimeToStruct(utc,d);
   // US rules effective from 2007: 02:00 EST=07:00 UTC in March; 02:00 EDT=06:00 UTC in November.
   datetime start=NthSundayUTC(d.year,3,2,7);
   datetime end=NthSundayUTC(d.year,11,1,6);
   return (utc>=start && utc<end);
}
bool IsFXBlocked(datetime serverBarClose)
{
   if(!InpEnableWeekendBlackout) return false;
   // Pepperstone follows New York DST: server is always seven wall-clock hours ahead of New York.
   datetime ny=0;
   if(InpServerTimeMode==PepperstoneNYClose)
      ny=serverBarClose-7*3600;
   else
   {
      datetime utc=serverBarClose-(datetime)(InpTesterServerUTCOffsetHours*3600);
      ny=utc+(NewYorkDST(utc)?-4*3600:-5*3600);
   }
   MqlDateTime d; TimeToStruct(ny,d);
   int minutes=d.hour*60+d.min;
   return (d.day_of_week==5 && minutes>=900) || d.day_of_week==6 || (d.day_of_week==0 && minutes<1020);
}

bool PositionOpen()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket!=0 && PositionGetString(POSITION_SYMBOL)==_Symbol && (ulong)PositionGetInteger(POSITION_MAGIC)==InpMagicNumber) return true;
   }
   return false;
}
void CloseStrategyPosition()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket!=0 && PositionGetString(POSITION_SYMBOL)==_Symbol && (ulong)PositionGetInteger(POSITION_MAGIC)==InpMagicNumber)
         trade.PositionClose(ticket);
   }
}
void ResetSetup()
{
   stage=0; direction=0; frozenResistance=0; frozenSupport=0; setupATR=0; setupExtreme=0;
   frozenExtensionExtreme=0; pullbackExtreme=0; pullbackMinorSwing=0; haveFrozenExtension=false;
   havePullback=false; haveMinorSwing=false; pullbackStarted=false; breakoutBar=-1; extensionBar=-1;
   pullbackConfirmedBar=-1; opposingCloseCount=0;
   activeImpulseSetup=false;
}
double ActiveMinimumRetracement() { return activeImpulseSetup ? InpImpulseMinimumRetracementATR : InpMinimumRetracementATR; }
int ActiveMaximumPullbackBars() { return activeImpulseSetup ? InpImpulseMaximumPullbackBars : InpMaximumPullbackBars; }
bool DepthExceeded(const double depth,const bool hasRangeMaximum,const double rangeMaximum) { return activeImpulseSetup ? depth>InpImpulseMaximumDepth : hasRangeMaximum && depth>rangeMaximum; }
string DiagnosticReason(const bool blocked)
{
   if(blocked) return "FX weekend blackout";
   if(PositionOpen()) return "position busy / no pyramiding";
   if(stage==0) return InpSetupMode==RangeBreakoutPullback ? "range not qualified or breakout body/buffer failed" : "impulse body/range not qualified";
   if(stage==1) return "extension pending/insufficient";
   if(stage==2 && !pullbackStarted) return "pullback not started";
   if(stage==2) return opposingCloseCount<InpMinimumOpposingCloses ? "insufficient opposing closes" : "ATR retracement/depth not qualified";
   if(stage==3) { bool ready=false; SelectedMultiplier(ready); return ready ? "resumption confirmation not satisfied" : "ATR/risk data not ready"; }
   return "entry requested";
}
void UpdateBlackoutZone(const bool blocked,const datetime t)
{
   if(!InpShowBlackoutZones) { blackoutWasActive=blocked; return; }
   if(blocked && !blackoutWasActive) blackoutStartTime=t;
   if(!blocked && blackoutWasActive && blackoutStartTime>0)
   {
      string name="RBP_FX_BLACKOUT_"+IntegerToString((long)blackoutStartTime);
      if(ObjectFind(0,name)<0)
      {
         double top=0,bottom=0;
         if(!ChartGetDouble(0,CHART_PRICE_MAX,0,top) || !ChartGetDouble(0,CHART_PRICE_MIN,0,bottom) || top<=bottom) { top=iHigh(_Symbol,_Period,1); bottom=iLow(_Symbol,_Period,1); }
         if(ObjectCreate(0,name,OBJ_RECTANGLE,0,blackoutStartTime,top,t,bottom))
         {
            ObjectSetInteger(0,name,OBJPROP_COLOR,ColorToARGB(clrGray,35));
            ObjectSetInteger(0,name,OBJPROP_FILL,true);
            ObjectSetInteger(0,name,OBJPROP_BACK,true);
            ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
         }
      }
      blackoutStartTime=0;
   }
   blackoutWasActive=blocked;
}
void UpdateVisualStatus(const bool blocked)
{
   if(!InpShowBlackoutStatus && !InpShowDiagnostics) return;
   string mode=InpSetupMode==RangeBreakoutPullback ? "Range" : "Impulse";
   string state=DiagnosticReason(blocked);
   Comment("Range Breakout-Pullback [",mode,"]\n",state,InpShowDiagnostics ? StringFormat("\nstage=%d direction=%d opposing=%d",stage,direction,opposingCloseCount) : "");
}
void ClearClusters()
{
   resistanceCentre=supportCentre=0; resistanceReactions=supportReactions=0;
   resistanceFirstBar=resistanceLastBar=supportFirstBar=supportLastBar=-1;
}
bool ATR(const int shift,double &value)
{
   double b[]; ArraySetAsSeries(b,true);
   if(CopyBuffer(atrHandle,0,shift,1,b)!=1 || b[0]<=0) return false;
   value=b[0]; return true;
}
bool PivotHigh(const int strength,double &value)
{
   int c=strength+1;
   value=iHigh(_Symbol,_Period,c); if(value==0) return false;
   for(int i=1;i<=strength;i++) if(value<iHigh(_Symbol,_Period,c-i) || value<iHigh(_Symbol,_Period,c+i)) return false;
   return true;
}
bool PivotLow(const int strength,double &value)
{
   int c=strength+1;
   value=iLow(_Symbol,_Period,c); if(value==0) return false;
   for(int i=1;i<=strength;i++) if(value>iLow(_Symbol,_Period,c-i) || value>iLow(_Symbol,_Period,c+i)) return false;
   return true;
}
bool PercentileRank(double &rank)
{
   if(InpRegimeLookback<1) return false;
   double a[]; ArraySetAsSeries(a,true);
   if(CopyBuffer(atrHandle,0,1,InpRegimeLookback,a)!=InpRegimeLookback) return false;
   int atOrBelow=0;
   for(int i=0;i<InpRegimeLookback;i++)
   {
      double close=iClose(_Symbol,_Period,1+i);
      if(close==0 || a[i]<=0) return false;
      double pct=a[i]/close*100.0;
      double current=a[0]/iClose(_Symbol,_Period,1)*100.0;
      if(pct<=current) atOrBelow++;
   }
   rank=(double)atOrBelow/InpRegimeLookback*100.0; return true;
}
double SelectedMultiplier(bool &ready)
{
   ready=true;
   if(InpStopMode==FixedATRMultiple) return InpFixedATRMultiplier;
   double rank=0; if(!PercentileRank(rank)) { ready=false; return 0; }
   double low=MathMin(InpLowRegimePercentile,InpHighRegimePercentile);
   double high=MathMax(InpLowRegimePercentile,InpHighRegimePercentile);
   return rank<=low ? InpLowVolatilityMultiplier : rank>=high ? InpHighVolatilityMultiplier : InpNormalVolatilityMultiplier;
}
void AddClusters(const double atr)
{
   if(resistanceLastBar>=0 && barNumber-resistanceLastBar>InpMaximumRangeBars)
      { resistanceCentre=0; resistanceReactions=0; resistanceFirstBar=resistanceLastBar=-1; }
   if(supportLastBar>=0 && barNumber-supportLastBar>InpMaximumRangeBars)
      { supportCentre=0; supportReactions=0; supportFirstBar=supportLastBar=-1; }
   double p=0, tolerance=atr*InpClusterToleranceATR;
   long pivotBar=barNumber-InpPivotStrength;
   if(PivotHigh(InpPivotStrength,p))
   {
      if(resistanceReactions>0 && MathAbs(p-resistanceCentre)<=tolerance && pivotBar-resistanceFirstBar<=InpMaximumRangeBars)
         { resistanceCentre=(resistanceCentre*resistanceReactions+p)/(resistanceReactions+1); resistanceReactions++; resistanceLastBar=pivotBar; }
      else { resistanceCentre=p; resistanceReactions=1; resistanceFirstBar=resistanceLastBar=pivotBar; }
   }
   if(PivotLow(InpPivotStrength,p))
   {
      if(supportReactions>0 && MathAbs(p-supportCentre)<=tolerance && pivotBar-supportFirstBar<=InpMaximumRangeBars)
         { supportCentre=(supportCentre*supportReactions+p)/(supportReactions+1); supportReactions++; supportLastBar=pivotBar; }
      else { supportCentre=p; supportReactions=1; supportFirstBar=supportLastBar=pivotBar; }
   }
}
bool RangeConfirmed(const double atr)
{
   if(stage!=0 || resistanceReactions<InpMinimumReactions || supportReactions<InpMinimumReactions) return false;
   long first=MathMin(resistanceFirstBar,supportFirstBar), last=MathMax(resistanceLastBar,supportLastBar);
   long duration=last-first;
   return resistanceCentre-supportCentre>=atr*InpMinimumRangeHeightATR && duration>=InpMinimumRangeBars && duration<=InpMaximumRangeBars;
}
void SendEntry(const int dir,const double atr,const double multiplier)
{
   double riskTicks=MathMax(1.0,MathCeil(atr*multiplier/TickSize()));
   double riskDistance=riskTicks*TickSize();
   double targetDistance=MathMax(1.0,MathRound(riskTicks*InpRiskRewardMultiple))*TickSize();
   MqlTick tick; if(!SymbolInfoTick(_Symbol,tick)) return;
   int stops=(int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double minDistance=stops*_Point;
   double entry=(dir==1 ? tick.ask : tick.bid);
   double sl=(dir==1 ? RoundDownTick(entry-riskDistance) : RoundUpTick(entry+riskDistance));
   double tp=(dir==1 ? RoundUpTick(entry+targetDistance) : RoundDownTick(entry-targetDistance));
   // A tester symbol can enforce a larger minimum bracket than the Pine symbol. Preserve R from the actual rounded stop.
   if(MathAbs(entry-sl)<minDistance)
   {
      sl=(dir==1 ? RoundDownTick(entry-minDistance) : RoundUpTick(entry+minDistance));
      double actualRisk=MathAbs(entry-sl);
      tp=(dir==1 ? RoundUpTick(entry+actualRisk*InpRiskRewardMultiple) : RoundDownTick(entry-actualRisk*InpRiskRewardMultiple));
   }
   if(dir==1) trade.Buy(InpVolumeLots,_Symbol,0.0,sl,tp,"RangeBreakoutPullback LONG");
   else trade.Sell(InpVolumeLots,_Symbol,0.0,sl,tp,"RangeBreakoutPullback SHORT");
}
void ProcessClosedBar()
{
   barNumber++;
   double atr=0; if(!ATR(1,atr)) return;
   datetime closeTime=iTime(_Symbol,_Period,0); // the next bar opens at the prior completed bar's close time.
   bool blocked=IsFXBlocked(closeTime);
   UpdateBlackoutZone(blocked,closeTime);
   if(blocked)
   {
      CloseStrategyPosition(); ResetSetup(); ClearClusters(); UpdateVisualStatus(true); return;
   }
   AddClusters(atr);
   double open=iOpen(_Symbol,_Period,1), high=iHigh(_Symbol,_Period,1), low=iLow(_Symbol,_Period,1), close=iClose(_Symbol,_Period,1), previousClose=iClose(_Symbol,_Period,2), previousHigh=iHigh(_Symbol,_Period,2), previousLow=iLow(_Symbol,_Period,2);
   if(stage==0 && InpSetupMode==RangeBreakoutPullback && !PositionOpen() && RangeConfirmed(atr))
   {
      bool longOK=InpTradeDirection!=ShortsOnly && (InpMinimumBreakoutBodyATR==0 || close-open>=atr*InpMinimumBreakoutBodyATR) && close>resistanceCentre+atr*InpBreakoutBufferATR;
      bool shortOK=InpTradeDirection!=LongsOnly && (InpMinimumBreakoutBodyATR==0 || open-close>=atr*InpMinimumBreakoutBodyATR) && close<supportCentre-atr*InpBreakoutBufferATR;
      if(longOK || shortOK)
      {
         direction=longOK?1:-1; stage=1; frozenResistance=resistanceCentre; frozenSupport=supportCentre; setupATR=atr;
         setupExtreme=direction==1?high:low; breakoutBar=barNumber; ClearClusters();
      }
   }
   if(stage==0 && InpSetupMode==ImpulsePullbackContinuation && !PositionOpen())
   {
      bool longImpulse=InpTradeDirection!=ShortsOnly && close>open && close-open>=atr*InpImpulseMinimumBodyATR && high-low>=atr*InpImpulseMinimumRangeATR;
      bool shortImpulse=InpTradeDirection!=LongsOnly && close<open && open-close>=atr*InpImpulseMinimumBodyATR && high-low>=atr*InpImpulseMinimumRangeATR;
      if(longImpulse || shortImpulse)
      {
         direction=longImpulse?1:-1; stage=2; activeImpulseSetup=true; frozenResistance=direction==1?open:0; frozenSupport=direction==-1?open:0; setupATR=atr; setupExtreme=direction==1?high:low; breakoutBar=extensionBar=barNumber; pullbackStarted=false; opposingCloseCount=0; haveMinorSwing=false;
      }
   }
   if(stage==1 && barNumber>breakoutBar)
   {
      bool invalid=direction==1 ? close<frozenResistance-setupATR*InpInvalidationToleranceATR : close>frozenSupport+setupATR*InpInvalidationToleranceATR;
      if(invalid || barNumber-breakoutBar>InpMaximumExtensionBars) { ResetSetup(); return; }
      if(direction==1) { setupExtreme=MathMax(setupExtreme,high); if(setupExtreme>=frozenResistance+setupATR*InpMinimumExtensionATR) { stage=2; extensionBar=barNumber; } }
      else { setupExtreme=MathMin(setupExtreme,low); if(setupExtreme<=frozenSupport-setupATR*InpMinimumExtensionATR) { stage=2; extensionBar=barNumber; } }
   }
   if(stage==2 && barNumber>extensionBar)
   {
      bool invalid=direction==1 ? close<frozenResistance-setupATR*InpInvalidationToleranceATR : close>frozenSupport+setupATR*InpInvalidationToleranceATR;
      if(!pullbackStarted)
      {
         bool newExtreme=direction==1 ? high>setupExtreme : low<setupExtreme;
         setupExtreme=direction==1 ? MathMax(setupExtreme,high) : MathMin(setupExtreme,low);
         bool opposing=direction==1 ? close<previousClose : close>previousClose;
         if(opposing) { pullbackStarted=true; haveFrozenExtension=true; frozenExtensionExtreme=setupExtreme; pullbackExtreme=direction==1?low:high; havePullback=true; opposingCloseCount=1; extensionBar=barNumber; }
         else if(newExtreme) extensionBar=barNumber;
      }
      else { pullbackExtreme=direction==1?MathMin(pullbackExtreme,low):MathMax(pullbackExtreme,high); if(direction==1 ? close<previousClose : close>previousClose) opposingCloseCount++; }
      if(invalid || barNumber-extensionBar>ActiveMaximumPullbackBars()) { ResetSetup(); return; }
      if(pullbackStarted)
      {
         double extension=direction==1 ? frozenExtensionExtreme-frozenResistance : frozenSupport-frozenExtensionExtreme;
         double retracement=direction==1 ? frozenExtensionExtreme-pullbackExtreme : pullbackExtreme-frozenExtensionExtreme;
         double depth=extension>0 ? retracement/extension*100.0 : 0.0;
         double lo=0,hi=0; bool hasMax=InpPullbackDepthMode!=AnyValidDepth;
         if(InpPullbackDepthMode==Shallow) { lo=MathMin(InpShallowMinimumDepth,InpShallowMaximumDepth); hi=MathMax(InpShallowMinimumDepth,InpShallowMaximumDepth); }
         else if(InpPullbackDepthMode==Deep) { lo=MathMin(InpDeepMinimumDepth,InpDeepMaximumDepth); hi=MathMax(InpDeepMinimumDepth,InpDeepMaximumDepth); }
         else if(InpPullbackDepthMode==CustomRange) { lo=MathMin(InpCustomMinimumDepth,InpCustomMaximumDepth); hi=MathMax(InpCustomMinimumDepth,InpCustomMaximumDepth); }
         if(DepthExceeded(depth,hasMax,hi)) { ResetSetup(); return; }
         if(opposingCloseCount>=InpMinimumOpposingCloses && retracement>=setupATR*ActiveMinimumRetracement() && depth>0 && (activeImpulseSetup || !hasMax || depth>=lo)) { stage=3; pullbackConfirmedBar=barNumber; }
      }
   }
   if(stage==3)
   {
      pullbackExtreme=direction==1?MathMin(pullbackExtreme,low):MathMax(pullbackExtreme,high);
      double pivot=0; if(direction==1 && PivotHigh(InpMinorSwingStrength,pivot) && barNumber-InpMinorSwingStrength>=extensionBar) { pullbackMinorSwing=pivot; haveMinorSwing=true; }
      if(direction==-1 && PivotLow(InpMinorSwingStrength,pivot) && barNumber-InpMinorSwingStrength>=extensionBar) { pullbackMinorSwing=pivot; haveMinorSwing=true; }
      bool invalid=direction==1 ? close<frozenResistance-setupATR*InpInvalidationToleranceATR : close>frozenSupport+setupATR*InpInvalidationToleranceATR;
      double extension=direction==1 ? frozenExtensionExtreme-frozenResistance : frozenSupport-frozenExtensionExtreme;
      double depth=extension>0 ? (direction==1?(frozenExtensionExtreme-pullbackExtreme):(pullbackExtreme-frozenExtensionExtreme))/extension*100.0 : 0;
      double maxDepth=0; bool hasMax=InpPullbackDepthMode!=AnyValidDepth;
      if(InpPullbackDepthMode==Shallow) maxDepth=MathMax(InpShallowMinimumDepth,InpShallowMaximumDepth);
      else if(InpPullbackDepthMode==Deep) maxDepth=MathMax(InpDeepMinimumDepth,InpDeepMaximumDepth);
      else if(InpPullbackDepthMode==CustomRange) maxDepth=MathMax(InpCustomMinimumDepth,InpCustomMaximumDepth);
      if(invalid || barNumber-extensionBar>ActiveMaximumPullbackBars() || DepthExceeded(depth,hasMax,maxDepth)) { ResetSetup(); return; }
      bool resume=InpConfirmationMode==Aggressive ? (direction==1?close>previousClose:close<previousClose) : InpConfirmationMode==Balanced ? (direction==1?close>open && close>previousHigh:close<open && close<previousLow) : (haveMinorSwing && (direction==1?close>pullbackMinorSwing:close<pullbackMinorSwing));
      bool riskReady=false; double multiplier=SelectedMultiplier(riskReady);
      if(barNumber>pullbackConfirmedBar && resume && !PositionOpen() && riskReady && multiplier>0) { int entryDirection=direction; ResetSetup(); SendEntry(entryDirection,atr,multiplier); }
   }
   UpdateVisualStatus(false);
}
int OnInit()
{
   if(InpPivotStrength<1 || InpMinimumReactions<2 || InpATRLength<1 || InpRegimeLookback<1 || InpVolumeLots<=0) return INIT_PARAMETERS_INCORRECT;
   atrHandle=iATR(_Symbol,_Period,InpATRLength);
   if(atrHandle==INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagicNumber); trade.SetDeviationInPoints(InpDeviationPoints);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { if(atrHandle!=INVALID_HANDLE) IndicatorRelease(atrHandle); }
void OnTick() { if(IsNewBar()) ProcessClosedBar(); }
