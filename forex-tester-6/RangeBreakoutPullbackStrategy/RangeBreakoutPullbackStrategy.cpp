#include <windows.h>
#include <oleauto.h>
#include <math.h>
#include <string.h>
#include "StrategyInterfaceUnit.h"

// Forex Tester 6 desktop C++ strategy.  The API calls this on every tick; all
// decisions below are deliberately made once, after a bar has closed.

PChar Currency=NULL;
int Timeframe=PERIOD_M15, TradeDirection=0, PivotStrength=3, MinimumReactions=2;
double VolumeLots=0.01, ClusterToleranceATR=0.35, MinimumRangeHeightATR=2.0;
int MinimumRangeBars=10, MaximumRangeBars=250;
double BreakoutBufferATR=0.10, MinimumBreakoutBodyATR=0.25, MinimumExtensionATR=0.75;
int MaximumExtensionBars=20, MinimumOpposingCloses=2;
double MinimumRetracementATR=0.50;
int PullbackDepthMode=0;
double ShallowMinimumDepth=0,ShallowMaximumDepth=50,DeepMinimumDepth=50,DeepMaximumDepth=100,CustomMinimumDepth=0,CustomMaximumDepth=100;
double InvalidationToleranceATR=0.25; int MaximumPullbackBars=30;
int Confirmation=1, MinorSwingStrength=2, StopModeSetting=0, ATRLength=14;
double FixedATRMultiplier=1.5; int RegimeLookback=200;
double LowRegimePercentile=33,HighRegimePercentile=67,LowVolatilityMultiplier=1,NormalVolatilityMultiplier=1.5,HighVolatilityMultiplier=2,RiskRewardMultiple=2;
bool EnableWeekendBlackout=true; int ServerUTCOffsetHours=0, MagicNumber=26083001;

long nbar=0; TDateTime lastTime=0; double rmaATR=0; int atrCount=0;
double atrPct[1000]; int atrPctCount=0;
double resistance=0,support=0; int rCount=0,sCount=0; long rFirst=-1,rLast=-1,sFirst=-1,sLast=-1;
int stage=0,dir=0,opposing=0; long breakout=-1,extension=-1,pullbackConfirmed=-1;
double frozenR=0,frozenS=0,setupATR=0,setupExtreme=0,frozenExtension=0,pullbackExtreme=0,minorSwing=0;
bool pullbackStarted=false,hasFrozenExtension=false,hasMinorSwing=false;

double Min(double a,double b){return a<b?a:b;} double Max(double a,double b){return a>b?a:b;}
double Down(double p){ double q=Point(); return floor(p/q+1e-9)*q; }
double Up(double p){ double q=Point(); return ceil(p/q-1e-9)*q; }
void ResetSetup(){stage=0;dir=0;opposing=0;breakout=extension=pullbackConfirmed=-1;pullbackStarted=false;hasFrozenExtension=false;hasMinorSwing=false;}
void ClearClusters(){rCount=sCount=0;rFirst=rLast=sFirst=sLast=-1;resistance=support=0;}
bool StrategyPositionOpen(){for(int i=0;i<OrdersTotal();i++) if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES) && OrderMagicNumber()==MagicNumber && strcmp(OrderSymbol(),Currency)==0) return true; return false;}
void Flatten(){for(int i=OrdersTotal()-1;i>=0;i--) if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES) && OrderMagicNumber()==MagicNumber && strcmp(OrderSymbol(),Currency)==0) CloseOrder(OrderTicket());}
bool IsPivotHigh(int strength,double &p){int c=strength+1; p=High(c); for(int i=1;i<=strength;i++) if(p<High(c-i)||p<High(c+i)) return false; return true;}
bool IsPivotLow(int strength,double &p){int c=strength+1; p=Low(c); for(int i=1;i<=strength;i++) if(p>Low(c-i)||p>Low(c+i)) return false; return true;}
int DaysInMonth(int y,int m){int d[]={31,28,31,30,31,30,31,31,30,31,30,31}; return m==2 && y%4==0 && (y%100!=0||y%400==0)?29:d[m-1];}
bool IsNYDST(TDateTime serverTime)
{
  SYSTEMTIME st; VariantTimeToSystemTime(serverTime-ServerUTCOffsetHours/24.0,&st); int y=st.wYear;
  SYSTEMTIME first={0}; first.wYear=y;first.wMonth=3;first.wDay=1;
  FILETIME ft; SystemTimeToFileTime(&first,&ft); FileTimeToSystemTime(&ft,&first); // March 1 weekday
  int marchSunday=1+(7-first.wDayOfWeek)%7+7;
  SYSTEMTIME march={0};march.wYear=y;march.wMonth=3;march.wDay=marchSunday;march.wHour=7; double start;SystemTimeToVariantTime(&march,&start);
  SYSTEMTIME novfirst={0};novfirst.wYear=y;novfirst.wMonth=11;novfirst.wDay=1; FILETIME nft;SystemTimeToFileTime(&novfirst,&nft);FileTimeToSystemTime(&nft,&novfirst);
  int novSunday=1+(7-novfirst.wDayOfWeek)%7; SYSTEMTIME nov={0};nov.wYear=y;nov.wMonth=11;nov.wDay=novSunday;nov.wHour=6;double end;SystemTimeToVariantTime(&nov,&end);
  double utc=serverTime-ServerUTCOffsetHours/24.0; return utc>=start && utc<end;
}
bool FXBlocked(TDateTime serverTime)
{
  if(!EnableWeekendBlackout) return false; double utc=serverTime-ServerUTCOffsetHours/24.0; double ny=utc+(IsNYDST(serverTime)?-4.0:-5.0)/24.0; SYSTEMTIME st; VariantTimeToSystemTime(ny,&st); int minutes=st.wHour*60+st.wMinute;
  return (st.wDayOfWeek==5 && minutes>=900)||st.wDayOfWeek==6||(st.wDayOfWeek==0&&minutes<1020);
}
void AddATR()
{
  double tr=Max(High(1)-Low(1),Max(fabs(High(1)-Close(2)),fabs(Low(1)-Close(2))));
  if(atrCount==0) rmaATR=tr; else if(atrCount<ATRLength) rmaATR=(rmaATR*atrCount+tr)/(atrCount+1); else rmaATR=(rmaATR*(ATRLength-1)+tr)/ATRLength;
  atrCount++; if(Close(1)!=0 && atrPctCount<1000) atrPct[atrPctCount++]=rmaATR/Close(1)*100.0;
}
bool RiskReady(double &mult)
{
  if(atrCount<ATRLength || rmaATR<=0) return false; if(StopModeSetting==1){mult=FixedATRMultiplier;return true;} if(atrPctCount<RegimeLookback) return false;
  double cur=atrPct[atrPctCount-1];int count=0;for(int i=atrPctCount-RegimeLookback;i<atrPctCount;i++)if(atrPct[i]<=cur)count++;double rank=100.0*count/RegimeLookback;
  double lo=Min(LowRegimePercentile,HighRegimePercentile),hi=Max(LowRegimePercentile,HighRegimePercentile);mult=rank<=lo?LowVolatilityMultiplier:(rank>=hi?HighVolatilityMultiplier:NormalVolatilityMultiplier);return true;
}
void AddClusters()
{
  if(rLast>=0&&nbar-rLast>MaximumRangeBars){rCount=0;rFirst=rLast=-1;}if(sLast>=0&&nbar-sLast>MaximumRangeBars){sCount=0;sFirst=sLast=-1;}
  double p,tolerance=rmaATR*ClusterToleranceATR;long pb=nbar-PivotStrength;
  if(IsPivotHigh(PivotStrength,p)){if(rCount&&fabs(p-resistance)<=tolerance&&pb-rFirst<=MaximumRangeBars){resistance=(resistance*rCount+p)/(rCount+1);rCount++;rLast=pb;}else{resistance=p;rCount=1;rFirst=rLast=pb;}}
  if(IsPivotLow(PivotStrength,p)){if(sCount&&fabs(p-support)<=tolerance&&pb-sFirst<=MaximumRangeBars){support=(support*sCount+p)/(sCount+1);sCount++;sLast=pb;}else{support=p;sCount=1;sFirst=sLast=pb;}}
}
bool RangeReady(){if(stage||rCount<MinimumReactions||sCount<MinimumReactions)return false;long duration=Max(rLast,sLast)-Min(rFirst,sFirst);return resistance-support>=rmaATR*MinimumRangeHeightATR&&duration>=MinimumRangeBars&&duration<=MaximumRangeBars;}
void Enter(int d)
{
  double mult;if(!RiskReady(mult))return; double riskTicks=Max(1,ceil(rmaATR*mult/Point()));double risk=riskTicks*Point(), target=Max(1,floor(riskTicks*RiskRewardMultiple+0.5))*Point();double entry=d==1?Ask():Bid();double sl=d==1?Down(entry-risk):Up(entry+risk);double tp=d==1?Up(entry+target):Down(entry-target);int ticket=-1;
  SendInstantOrder(Currency,d==1?op_Buy:op_Sell,VolumeLots,sl,tp,(PChar)"RangeBreakoutPullback",MagicNumber,ticket);
}
void ProcessBar()
{
  nbar++; AddATR(); if(atrCount<ATRLength)return; if(FXBlocked(Time(0))){Flatten();ResetSetup();ClearClusters();return;} AddClusters(); double o=Open(1),h=High(1),l=Low(1),c=Close(1),pc=Close(2);
  if(stage==0&&!StrategyPositionOpen()&&RangeReady()){bool lng=TradeDirection!=2&&(MinimumBreakoutBodyATR==0||c-o>=rmaATR*MinimumBreakoutBodyATR)&&c>resistance+rmaATR*BreakoutBufferATR;bool sht=TradeDirection!=1&&(MinimumBreakoutBodyATR==0||o-c>=rmaATR*MinimumBreakoutBodyATR)&&c<support-rmaATR*BreakoutBufferATR;if(lng||sht){dir=lng?1:-1;stage=1;frozenR=resistance;frozenS=support;setupATR=rmaATR;setupExtreme=dir==1?h:l;breakout=nbar;ClearClusters();}}
  if(stage==1&&nbar>breakout){bool bad=dir==1?c<frozenR-setupATR*InvalidationToleranceATR:c>frozenS+setupATR*InvalidationToleranceATR;if(bad||nbar-breakout>MaximumExtensionBars){ResetSetup();return;}if(dir==1){setupExtreme=Max(setupExtreme,h);if(setupExtreme>=frozenR+setupATR*MinimumExtensionATR){stage=2;extension=nbar;}}else{setupExtreme=Min(setupExtreme,l);if(setupExtreme<=frozenS-setupATR*MinimumExtensionATR){stage=2;extension=nbar;}}}
  if(stage==2&&nbar>extension){bool bad=dir==1?c<frozenR-setupATR*InvalidationToleranceATR:c>frozenS+setupATR*InvalidationToleranceATR;if(!pullbackStarted){bool newx=dir==1?h>setupExtreme:l<setupExtreme;setupExtreme=dir==1?Max(setupExtreme,h):Min(setupExtreme,l);bool opp=dir==1?c<pc:c>pc;if(opp){pullbackStarted=true;hasFrozenExtension=true;frozenExtension=setupExtreme;pullbackExtreme=dir==1?l:h;opposing=1;extension=nbar;}else if(newx)extension=nbar;}else{pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);if(dir==1?c<pc:c>pc)opposing++;}if(bad||nbar-extension>MaximumPullbackBars){ResetSetup();return;}if(pullbackStarted){double ext=dir==1?frozenExtension-frozenR:frozenS-frozenExtension,ret=dir==1?frozenExtension-pullbackExtreme:pullbackExtreme-frozenExtension,depth=ext>0?ret/ext*100:0,lo=0,hi=0;bool maxDepth=PullbackDepthMode!=0;if(PullbackDepthMode==1){lo=Min(ShallowMinimumDepth,ShallowMaximumDepth);hi=Max(ShallowMinimumDepth,ShallowMaximumDepth);}if(PullbackDepthMode==2){lo=Min(DeepMinimumDepth,DeepMaximumDepth);hi=Max(DeepMinimumDepth,DeepMaximumDepth);}if(PullbackDepthMode==3){lo=Min(CustomMinimumDepth,CustomMaximumDepth);hi=Max(CustomMinimumDepth,CustomMaximumDepth);}if(maxDepth&&depth>hi){ResetSetup();return;}if(opposing>=MinimumOpposingCloses&&ret>=setupATR*MinimumRetracementATR&&depth>0&&(!maxDepth||depth>=lo)){stage=3;pullbackConfirmed=nbar;}}}
  if(stage==3){pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);double p;if(dir==1&&IsPivotHigh(MinorSwingStrength,p)&&nbar-MinorSwingStrength>=extension){minorSwing=p;hasMinorSwing=true;}if(dir==-1&&IsPivotLow(MinorSwingStrength,p)&&nbar-MinorSwingStrength>=extension){minorSwing=p;hasMinorSwing=true;}bool bad=dir==1?c<frozenR-setupATR*InvalidationToleranceATR:c>frozenS+setupATR*InvalidationToleranceATR;double ext=dir==1?frozenExtension-frozenR:frozenS-frozenExtension,depth=ext>0?(dir==1?frozenExtension-pullbackExtreme:pullbackExtreme-frozenExtension)/ext*100:0,hi=0;bool maxDepth=PullbackDepthMode!=0;if(PullbackDepthMode==1)hi=Max(ShallowMinimumDepth,ShallowMaximumDepth);if(PullbackDepthMode==2)hi=Max(DeepMinimumDepth,DeepMaximumDepth);if(PullbackDepthMode==3)hi=Max(CustomMinimumDepth,CustomMaximumDepth);if(bad||nbar-extension>MaximumPullbackBars||(maxDepth&&depth>hi)){ResetSetup();return;}bool resume=Confirmation==0?(dir==1?c>pc:c<pc):Confirmation==1?(dir==1?c>o&&c>High(2):c<o&&c<Low(2)):(hasMinorSwing&&(dir==1?c>minorSwing:c<minorSwing));if(nbar>pullbackConfirmed&&resume&&!StrategyPositionOpen()){int d=dir;ResetSetup();Enter(d);}}
}
EXPORT void __stdcall InitStrategy()
{
 StrategyShortName((PChar)"Range Breakout-Pullback"); StrategyDescription((PChar)"Closed-bar TradingView range breakout-pullback port");
 RegOption((PChar)"Currency",ot_Currency,&Currency);RegOption((PChar)"Timeframe",ot_TimeFrame,&Timeframe);Timeframe=PERIOD_M15;
 RegOption((PChar)"Trade direction (0 both, 1 long, 2 short)",ot_Integer,&TradeDirection);RegOption((PChar)"Volume lots",ot_Double,&VolumeLots);
 RegOption((PChar)"Pivot strength",ot_Integer,&PivotStrength);RegOption((PChar)"Minimum reactions",ot_Integer,&MinimumReactions);RegOption((PChar)"Cluster tolerance ATR",ot_Double,&ClusterToleranceATR);RegOption((PChar)"Minimum range height ATR",ot_Double,&MinimumRangeHeightATR);RegOption((PChar)"Minimum range bars",ot_Integer,&MinimumRangeBars);RegOption((PChar)"Maximum range bars",ot_Integer,&MaximumRangeBars);
 RegOption((PChar)"Breakout buffer ATR",ot_Double,&BreakoutBufferATR);RegOption((PChar)"Minimum breakout body ATR",ot_Double,&MinimumBreakoutBodyATR);RegOption((PChar)"Minimum extension ATR",ot_Double,&MinimumExtensionATR);RegOption((PChar)"Maximum extension bars",ot_Integer,&MaximumExtensionBars);
 RegOption((PChar)"Minimum opposing closes",ot_Integer,&MinimumOpposingCloses);RegOption((PChar)"Minimum retracement ATR",ot_Double,&MinimumRetracementATR);RegOption((PChar)"Depth mode (0 any,1 shallow,2 deep,3 custom)",ot_Integer,&PullbackDepthMode);RegOption((PChar)"Shallow minimum depth",ot_Double,&ShallowMinimumDepth);RegOption((PChar)"Shallow maximum depth",ot_Double,&ShallowMaximumDepth);RegOption((PChar)"Deep minimum depth",ot_Double,&DeepMinimumDepth);RegOption((PChar)"Deep maximum depth",ot_Double,&DeepMaximumDepth);RegOption((PChar)"Custom minimum depth",ot_Double,&CustomMinimumDepth);RegOption((PChar)"Custom maximum depth",ot_Double,&CustomMaximumDepth);RegOption((PChar)"Invalidation tolerance ATR",ot_Double,&InvalidationToleranceATR);RegOption((PChar)"Maximum pullback bars",ot_Integer,&MaximumPullbackBars);
 RegOption((PChar)"Confirmation (0 aggressive,1 balanced,2 conservative)",ot_Integer,&Confirmation);RegOption((PChar)"Minor swing strength",ot_Integer,&MinorSwingStrength);RegOption((PChar)"Stop mode (0 adaptive,1 fixed)",ot_Integer,&StopModeSetting);RegOption((PChar)"ATR length",ot_Integer,&ATRLength);RegOption((PChar)"Fixed ATR multiplier",ot_Double,&FixedATRMultiplier);RegOption((PChar)"ATR percentile lookback",ot_Integer,&RegimeLookback);RegOption((PChar)"Low regime percentile",ot_Double,&LowRegimePercentile);RegOption((PChar)"High regime percentile",ot_Double,&HighRegimePercentile);RegOption((PChar)"Low multiplier",ot_Double,&LowVolatilityMultiplier);RegOption((PChar)"Normal multiplier",ot_Double,&NormalVolatilityMultiplier);RegOption((PChar)"High multiplier",ot_Double,&HighVolatilityMultiplier);RegOption((PChar)"Profit target R",ot_Double,&RiskRewardMultiple);
 RegOption((PChar)"Enable weekend blackout",ot_Boolean,&EnableWeekendBlackout);RegOption((PChar)"Server UTC offset hours",ot_Integer,&ServerUTCOffsetHours);RegOption((PChar)"Magic number",ot_Integer,&MagicNumber);
}
EXPORT void __stdcall DoneStrategy(){free(Currency);} EXPORT void __stdcall ResetStrategy(){lastTime=0;nbar=0;rmaATR=0;atrCount=atrPctCount=0;ResetSetup();ClearClusters();}
EXPORT void __stdcall GetSingleTick(){if(!Currency||strcmp(Currency,Symbol())!=0)return;SetCurrencyAndTimeframe(Currency,Timeframe);if(Bars()<Max(ATRLength,RegimeLookback)+PivotStrength+5)return;TDateTime t=Time(0);if(t!=lastTime){lastTime=t;ProcessBar();}}
