#include <windows.h>
#include <oleauto.h>
#include <math.h>
#include <float.h>
#include <string.h>
#include <stdio.h>
#include "StrategyInterfaceUnit.h"

// Forex Tester 6 desktop C++ strategy.  The API calls this on every tick; all
// structure is advanced on closed bars; optional impulse first-touch execution is tick based.

PChar Currency=NULL;
int SettingsPreset=0,Timeframe=PERIOD_M15, TradeDirection=0, SetupModeSetting=0, PivotStrength=3, MinimumReactions=2;
double VolumeLots=0.01, ClusterToleranceATR=0.35, MinimumRangeHeightATR=2.0;
int MinimumRangeBars=10, MaximumRangeBars=250;
double BreakoutBufferATR=0.10, MinimumBreakoutBodyATR=0.25, MinimumExtensionATR=0.75;
int MaximumExtensionBars=20, MinimumOpposingCloses=2;
int ImpulseEntryMode=0; // 0 confirmation; 1 first-touch ATR; 2 qualified; 3 structure
const int StructureContextTimeframe=PERIOD_M15,StructureContextScan=200;
int StructureContextPivotStrength=2,StructureImpulseMaximumBars=12;
double StructureImpulseMinimumEfficiency=0.70;
struct StructureReference {TDateTime center,confirmation;double price;};
StructureReference structureReferences[2]={0};
bool structureContextReady=false;
TDateTime structureCutoff=0;
double structureBarrier=0,structureEfficiency=0,structurePath=0;
long structureDuration=0;
char lastStructureContextReason[128]="",lastStructureGateReason[128]="";
int QualifiedImpulseRangeLookbackBars=20;
double QualifiedImpulseMinimumDisplacementATR=3.0,QualifiedImpulseBreakoutBufferATR=0.10,QualifiedPullbackMinimumDepth=25.0;
bool qualifiedConfigValid=true,qualifiedRangeReady=false,impulseQualified=false;
double qualifiedRangeHigh=0,qualifiedRangeLow=0;
long qualifiedFirstSeedBar=-1,qualifyingBar=-1;
TDateTime qualifyingTime=0;
double ImpulseMinimumBodyATR=1.0,ImpulseMaximumBodyATR=0.0,ImpulseMinimumRangeATR=1.25,ImpulseMinimumRetracementATR=0.50,ImpulseMaximumDepth=75.0; int ImpulseMaximumPullbackBars=30;
double MinimumRetracementATR=0.50;
int PullbackDepthMode=0;
double ShallowMinimumDepth=0,ShallowMaximumDepth=50,DeepMinimumDepth=50,DeepMaximumDepth=100,CustomMinimumDepth=0,CustomMaximumDepth=100;
double InvalidationToleranceATR=0.25; int MaximumPullbackBars=30;
int Confirmation=1, MinorSwingStrength=2, StopModeSetting=0, ATRLength=14;
int MaximumCompletedStrategyTrades=100;
double FixedATRMultiplier=1.5; int RegimeLookback=200;
double LowRegimePercentile=33,HighRegimePercentile=67,LowVolatilityMultiplier=1,NormalVolatilityMultiplier=1.5,HighVolatilityMultiplier=2;
double WithTrendNeutralTargetR=2.0,CounterTrendTargetR=3.0;
bool EnableWeekendBlackout=true,ShowBlackoutStatus=true,ShowDiagnostics=false; int ImpulseM15H4TrendFilter=0,TrendFilterScope=0,ServerUTCOffsetHours=0, MagicNumber=26083001;

// FT6 writes dialog/project values through the registered pointers. Keep those
// values separate from the globals consumed by trading so presets never replace
// the user's Custom settings. All detailed options are int, double or bool.
struct RunOption
{
  TOptionType type;
  void *effective;
  union { int integer; double number; bool boolean; } custom;
};
RunOption runOptions[64]; int runOptionCount=0;
bool runSettingsResolved=false;
bool tradeLimitBlocked=false,tradeLimitNotificationSent=false,tradeHistoryCacheValid=false;
int cachedCompletedStrategyTrades=0,cachedHistoryTotal=-1;
bool startupContextValidated=false,startupHistoryStateKnown=false,startupHistoryReady=false,startupReadyProgressLogged=false;
int startupTimeframe=-1;char startupCurrency[128]="",lastStartupEvent[512]="";
long firstProcessedClosedBar=-1,lastProgressBar=0;
void PauseForTradeLimit(const char *reason);
void RegRunOption(PChar name,TOptionType type,void *effective)
{
  // Includes the three appended structure controls; refuse an overflow explicitly.
  if(runOptionCount>=(int)(sizeof(runOptions)/sizeof(runOptions[0])))
  {Print((PChar)"RBP option registration capacity exceeded");return;}
  RunOption &option=runOptions[runOptionCount++]; option.type=type; option.effective=effective;
  if(type==ot_Double){option.custom.number=*(double*)effective;RegOption(name,type,&option.custom.number);}
  else if(type==ot_Boolean){option.custom.boolean=*(bool*)effective;RegOption(name,type,&option.custom.boolean);}
  else {option.custom.integer=*(int*)effective;RegOption(name,type,&option.custom.integer);}
}
void LoadCustomSettings()
{
  for(int i=0;i<runOptionCount;i++)
  {
    RunOption &option=runOptions[i];
    if(option.type==ot_Double) *(double*)option.effective=option.custom.number;
    else if(option.type==ot_Boolean) *(bool*)option.effective=option.custom.boolean;
    else *(int*)option.effective=option.custom.integer;
  }
}

long nbar=0; TDateTime lastTime=0; double rmaATR=0; int atrCount=0;
double *atrPct=NULL; int atrPctCapacity=0,atrPctCount=0,atrPctNext=0;
double resistance=0,support=0; int rCount=0,sCount=0; long rFirst=-1,rLast=-1,sFirst=-1,sLast=-1;
int stage=0,dir=0,opposing=0; long breakout=-1,extension=-1,pullbackConfirmed=-1;
double frozenR=0,frozenS=0,setupATR=0,setupExtreme=0,frozenExtension=0,pullbackExtreme=0,minorSwing=0;
bool pullbackStarted=false,hasFrozenExtension=false,hasMinorSwing=false,activeImpulseSetup=false;
enum ImpulseStage { IMPULSE_PARENT=4, IMPULSE_PULLBACK=5, IMPULSE_ARMED=6, IMPULSE_CONSUMED=7 };
double impulseOrigin=0,pullbackStartPrice=0,consumedExtreme=0,frozenRiskDistance=0,frozenTargetR=0,frozenTargetPrice=0;
long impulseOriginBar=-1,impulseEndpointBar=-1,pullbackStartBar=-1,consumedBar=-1;
TDateTime impulseOriginTime=0,impulseSeedTime=0,impulseEndpointTime=0,pullbackStartTime=0;
int consumedDirection=0,frozenStructuralTrend=0;
struct ProvisionalImpulseCandidate
{
  bool active,observedWhileBusy,normalSeedSeen,oversizedSeen,continuationSeen,oversizedWaitLogged;
  int direction;
  double seedATR,firstExtreme,extreme;
  long firstBar,extremeBar;
  TDateTime firstTime,extremeTime;
  char lastPromotion[1400]; // Semantic event key; cleared with the candidate lifecycle.
};
ProvisionalImpulseCandidate provisionalLong={false,false,false,false,false,false,1,0,0,0,-1,-1,0,0};
ProvisionalImpulseCandidate provisionalShort={false,false,false,false,false,false,-1,0,0,0,-1,-1,0,0};
long lastContainedDecisionBar=-1;
double previousSwingHigh=0,latestSwingHigh=0,previousSwingLow=0,latestSwingLow=0;
long previousSwingHighBar=-1,latestSwingHighBar=-1,previousSwingLowBar=-1,latestSwingLowBar=-1;
TDateTime previousSwingHighTime=0,latestSwingHighTime=0,previousSwingLowTime=0,latestSwingLowTime=0;
bool havePreviousSwingHigh=false,haveLatestSwingHigh=false,havePreviousSwingLow=false,haveLatestSwingLow=false;
char lastDiagnostic[512]="";
bool touchActive=false;
double touchLevel=0;
TDateTime touchActivationTime=0;
unsigned long long tickSequence=0,touchActivationTick=0;
// Only the just-completed bar's observed tick path is needed for a prospective
// activation. OHLC alone cannot order an endpoint and a retracement in that bar.
struct TouchBarPath
{
  TDateTime time;
  bool complete;
  double highBid,lowBid,minAskAfterHigh,maxBidAfterLow,minAsk;
};
TouchBarPath liveTouchPath={0},closedTouchPath={0};
// Covers the preset's 30-bar recovery window. Missing/partial older paths are
// handled conservatively; no synthetic intrabar order is inferred from OHLC.
TouchBarPath touchPaths[64]={0};int touchPathNext=0;
bool StructureMode(){return SetupModeSetting==1&&ImpulseEntryMode==3;}
bool QualifiedMode(){return SetupModeSetting==1&&(ImpulseEntryMode==2||ImpulseEntryMode==3);}
bool FirstTouchMode(){return SetupModeSetting==1&&(ImpulseEntryMode>=1&&ImpulseEntryMode<=3);}
void ResetQualification(){qualifiedRangeReady=impulseQualified=false;qualifiedRangeHigh=qualifiedRangeLow=0;qualifiedFirstSeedBar=qualifyingBar=-1;qualifyingTime=0;structureContextReady=false;structureCutoff=0;structureBarrier=structureEfficiency=structurePath=0;structureDuration=0;memset(structureReferences,0,sizeof(structureReferences));lastStructureContextReason[0]=lastStructureGateReason[0]=0;}
void ResetTouch(){touchActive=false;touchLevel=0;touchActivationTime=0;touchActivationTick=0;}

double Min(double a,double b){return a<b?a:b;} double Max(double a,double b){return a>b?a:b;}
double Down(double p){ double q=Point(); return floor(p/q+1e-9)*q; }
double Up(double p){ double q=Point(); return ceil(p/q-1e-9)*q; }
void ResetProvisionalCandidates();
void ResetSetup(){ResetTouch();ResetQualification();stage=0;dir=0;opposing=0;breakout=extension=pullbackConfirmed=-1;pullbackStarted=false;hasFrozenExtension=false;hasMinorSwing=false;activeImpulseSetup=false;pullbackStartBar=-1;lastContainedDecisionBar=-1;ResetProvisionalCandidates();}
double ActiveMinimumRetracement(){return activeImpulseSetup?ImpulseMinimumRetracementATR:MinimumRetracementATR;}
int ActiveMaximumPullbackBars(){return activeImpulseSetup?ImpulseMaximumPullbackBars:MaximumPullbackBars;}
bool DepthExceeded(double depth,bool useRange,double rangeMaximum){return activeImpulseSetup?depth>ImpulseMaximumDepth:(useRange&&depth>rangeMaximum);}
void EmitStatus(PChar text){if(strcmp(lastDiagnostic,text)!=0){Print(text);strncpy(lastDiagnostic,text,511);lastDiagnostic[511]=0;}}
void EmitDiagnostic(const char *text){if(ShowDiagnostics)EmitStatus((PChar)text);}
void EmitStartupEvent(const char *text){if(strcmp(lastStartupEvent,text)!=0){Print((PChar)text);strncpy(lastStartupEvent,text,sizeof(lastStartupEvent)-1);lastStartupEvent[sizeof(lastStartupEvent)-1]=0;}}
void StartupBindingFailure(const char *reason,const char *configured)
{
  char text[512];sprintf(text,"RBP startup binding configured=%s selected=unavailable timeframe=%d outcome=failure reason=%s",configured&&*configured?configured:"<missing>",Timeframe,reason);EmitStartupEvent(text);
  startupContextValidated=false;startupHistoryStateKnown=false;
}
bool BindRunContext()
{
  if(!Currency||!Currency[0]){StartupBindingFailure("Currency is missing or empty",Currency);return false;}
  if(!IntrfProcsRec.SetCurrencyAndTimeframe||!IntrfProcsRec.pSetCurrencyAndTimeframe){StartupBindingFailure("SetCurrencyAndTimeframe API unavailable",Currency);return false;}
  if(!SetCurrencyAndTimeframe(Currency,Timeframe)){StartupBindingFailure("SetCurrencyAndTimeframe returned false",Currency);return false;}
  PChar selected=Symbol();
  if(!selected||!selected[0]){StartupBindingFailure("selected Symbol is null or empty after successful binding",Currency);return false;}
  if(strcmp(Currency,selected)!=0)
  {
    char text[512];sprintf(text,"RBP startup binding configured=%s selected=%s timeframe=%d outcome=failure reason=selected context mismatch",Currency,selected,Timeframe);EmitStartupEvent(text);
    startupContextValidated=false;startupHistoryStateKnown=false;return false;
  }
  if(!startupContextValidated||startupTimeframe!=Timeframe||strcmp(startupCurrency,Currency)!=0)
  {
    char text[512];sprintf(text,"RBP startup binding configured=%s selected=%s timeframe=%d outcome=success",Currency,selected,Timeframe);EmitStartupEvent(text);
    strncpy(startupCurrency,Currency,sizeof(startupCurrency)-1);startupCurrency[sizeof(startupCurrency)-1]=0;startupTimeframe=Timeframe;
  }
  startupContextValidated=true;return true;
}
void StartupHistoryStatus(const unsigned long available,const int required,const bool ready)
{
  if(!startupHistoryStateKnown||startupHistoryReady!=ready)
  {
    char text[512];sprintf(text,"RBP startup history configured=%s selected=%s timeframe=%d binding=success bars=%lu required=%d readiness=%s",Currency?Currency:"<missing>",Currency?Currency:"<missing>",Timeframe,available,required,ready?"ready":"waiting");EmitStartupEvent(text);
    startupHistoryStateKnown=true;startupHistoryReady=ready;
  }
}
void StartupProgress()
{
  if(!startupReadyProgressLogged||nbar-lastProgressBar>=250)
  {
    char text[384];sprintf(text,"RBP progress processedBars=%ld pending=%s qualified=%s touch=%s stage=%d",nbar,(activeImpulseSetup&&stage!=IMPULSE_CONSUMED)?"yes":"no",impulseQualified?"yes":"no",touchActive?"yes":"no",stage);EmitStartupEvent(text);
    startupReadyProgressLogged=true;lastProgressBar=nbar;
  }
}
const char *DirectionName(const int d){return d==1?"long":"short";}
const char *TrendName(const int trend){return trend==1?"Uptrend":trend==-1?"Downtrend":"Neutral";}
ProvisionalImpulseCandidate &CandidateFor(const int d){return d==1?provisionalLong:provisionalShort;}
void ResetCandidate(ProvisionalImpulseCandidate &candidate)
{
  int direction=candidate.direction;memset(&candidate,0,sizeof(candidate));candidate.direction=direction;candidate.firstBar=candidate.extremeBar=-1;
}
void ResetProvisionalCandidates(){ResetCandidate(provisionalLong);ResetCandidate(provisionalShort);}
void FormatTime(const TDateTime value,char *buffer)
{
  SYSTEMTIME st;
  if(value!=0&&VariantTimeToSystemTime(value,&st)) sprintf(buffer,"%04d-%02d-%02d %02d:%02d",st.wYear,st.wMonth,st.wDay,st.wHour,st.wMinute);
  else strcpy(buffer,"n/a");
}
void FormatTickTime(const TDateTime value,char *buffer)
{
  SYSTEMTIME st;
  if(value!=0&&VariantTimeToSystemTime(value,&st))sprintf(buffer,"%04d-%02d-%02d %02d:%02d:%02d",st.wYear,st.wMonth,st.wDay,st.wHour,st.wMinute,st.wSecond);
  else strcpy(buffer,"n/a");
}
void DiagnosticMarker(const char *kind,const TDateTime time,const double price,const char *label,const TColor color)
{
  if(!ShowDiagnostics) return;
  char name[96];sprintf(name,"RBP_DIAG_%d_%ld_%s",MagicNumber,breakout,kind);
  if(ObjectExists((PChar)name)) ObjectDelete((PChar)name);
  if(ObjectCreate((PChar)name,obj_Text,0,time,price)) ObjectSetText((PChar)name,(PChar)label,7,(PChar)"Arial",color);
}
void ClampTargetSettings(){WithTrendNeutralTargetR=Max(2.0,WithTrendNeutralTargetR);CounterTrendTargetR=Max(3.0,CounterTrendTargetR);}
void ApplySettingsPreset()
{
  if(SettingsPreset<1||SettingsPreset>8) return;
  // Non-Custom presets start from the registered defaults, then apply their compact experiment values.
  Timeframe=PERIOD_M15; TradeDirection=0; VolumeLots=0.01; SetupModeSetting=0; PivotStrength=3; MinimumReactions=2;
  ClusterToleranceATR=0.35; MinimumRangeHeightATR=2.0; MinimumRangeBars=10; MaximumRangeBars=250;
  BreakoutBufferATR=0.10; MinimumBreakoutBodyATR=0.25; MinimumExtensionATR=0.75; MaximumExtensionBars=20; MinimumOpposingCloses=2;
  ImpulseEntryMode=0;
  ImpulseMinimumBodyATR=1.0; ImpulseMaximumBodyATR=0.0; ImpulseMinimumRangeATR=1.25; ImpulseMinimumRetracementATR=0.50; ImpulseMaximumDepth=75.0; ImpulseMaximumPullbackBars=30;
  MinimumRetracementATR=0.50; PullbackDepthMode=0; ShallowMinimumDepth=0; ShallowMaximumDepth=50; DeepMinimumDepth=50; DeepMaximumDepth=100; CustomMinimumDepth=0; CustomMaximumDepth=100; InvalidationToleranceATR=0.25; MaximumPullbackBars=30;
  Confirmation=1; MinorSwingStrength=2; StopModeSetting=0; ATRLength=14; FixedATRMultiplier=1.5; RegimeLookback=200; LowRegimePercentile=33; HighRegimePercentile=67; LowVolatilityMultiplier=1; NormalVolatilityMultiplier=1.5; HighVolatilityMultiplier=2;
  EnableWeekendBlackout=true; ShowBlackoutStatus=true; ShowDiagnostics=false; ImpulseM15H4TrendFilter=0; TrendFilterScope=0; ServerUTCOffsetHours=0; MagicNumber=26083001;
  SetupModeSetting=1; Confirmation=0; ImpulseMaximumBodyATR=1.5; PullbackDepthMode=1; ShowDiagnostics=true;
  WithTrendNeutralTargetR=2.0; CounterTrendTargetR=3.0;
  if(SettingsPreset==2) ImpulseM15H4TrendFilter=1;
  if(SettingsPreset==3) TradeDirection=1;
  if(SettingsPreset>=4) Timeframe=PERIOD_M5;
  if(SettingsPreset==5||SettingsPreset==6) ImpulseEntryMode=1;
  if(SettingsPreset==6||SettingsPreset==7) TradeDirection=1;
  if(SettingsPreset>=7){ImpulseEntryMode=2;QualifiedImpulseRangeLookbackBars=20;QualifiedImpulseMinimumDisplacementATR=3.0;QualifiedImpulseBreakoutBufferATR=0.10;QualifiedPullbackMinimumDepth=25.0;}
  if(SettingsPreset==8){TradeDirection=0;ImpulseEntryMode=3;StructureContextPivotStrength=2;StructureImpulseMinimumEfficiency=0.70;StructureImpulseMaximumBars=12;}
}
void EmitPresetDiagnostic()
{
  char text[1024]; const char *name=SettingsPreset==1?"Baseline":SettingsPreset==2?"Trend-filter test":SettingsPreset==3?"Baseline buys-only":SettingsPreset==4?"M5 Baseline":SettingsPreset==5?"M5 first-touch ATR":SettingsPreset==6?"M5 first-touch ATR buys-only":SettingsPreset==7?"M5 qualified first pullback buys-only":SettingsPreset==8?"M5 structure-break first pullback both":"Custom";
  double lo=PullbackDepthMode==1?ShallowMinimumDepth:PullbackDepthMode==2?DeepMinimumDepth:PullbackDepthMode==3?CustomMinimumDepth:0;
  double hi=PullbackDepthMode==1?ShallowMaximumDepth:PullbackDepthMode==2?DeepMaximumDepth:PullbackDepthMode==3?CustomMaximumDepth:100;
  _snprintf_s(text,sizeof(text),_TRUNCATE,"RBP preset=%s timeframe=%d setup=%d confirmation=%d direction=%d body=%.2f-%.2fATR depth=%.0f-%.0f%% depthMode=%d stop=%s targets=%.1f/%.1fR EMAtrend=%s scope=%d",name,Timeframe,SetupModeSetting,Confirmation,TradeDirection,ImpulseMinimumBodyATR,ImpulseMaximumBodyATR,Min(lo,hi),Max(lo,hi),PullbackDepthMode,StopModeSetting==0?"adaptive":"fixed",WithTrendNeutralTargetR,CounterTrendTargetR,ImpulseM15H4TrendFilter!=0?"on":"off",TrendFilterScope);
  _snprintf_s(text+strlen(text),sizeof(text)-strlen(text),_TRUNCATE," entryMode=%s touchATR=%.2f presetId=%d maxCompletedTrades=%d",ImpulseEntryMode==3?"structure-first-pullback":ImpulseEntryMode==2?"qualified-first-pullback":ImpulseEntryMode==1?"first-touch-ATR":"confirmation",ImpulseMinimumRetracementATR,SettingsPreset,MaximumCompletedStrategyTrades);
  if(QualifiedMode())_snprintf_s(text+strlen(text),sizeof(text)-strlen(text),_TRUNCATE," qualifiedLookback=%d qualifiedDisplacementATR=%.2f qualifiedBufferATR=%.2f qualifiedDepthPct=%.2f",QualifiedImpulseRangeLookbackBars,QualifiedImpulseMinimumDisplacementATR,QualifiedImpulseBreakoutBufferATR,QualifiedPullbackMinimumDepth);
  if(StructureMode())_snprintf_s(text+strlen(text),sizeof(text)-strlen(text),_TRUNCATE," contextTF=15 contextStrength=%d contextScan=200 minEfficiency=%.2f impulseMaxBars=%d",StructureContextPivotStrength,StructureImpulseMinimumEfficiency,StructureImpulseMaximumBars);
  Print((PChar)text);
}
bool ValidateQualifiedSettings();
void ResolveRunSettings()
{
  if(runSettingsResolved) return;
  LoadCustomSettings(); ApplySettingsPreset(); ClampTargetSettings();
  qualifiedConfigValid=ValidateQualifiedSettings();
  runSettingsResolved=true;
  if(MaximumCompletedStrategyTrades<0)PauseForTradeLimit("Maximum completed strategy trades must be 0 or greater");
  EmitPresetDiagnostic();
}
bool StrategyTradeHistoryReady()
{
  return IntrfProcsRec.HistoryTotal&&IntrfProcsRec.pHistoryTotal&&
    IntrfProcsRec.OrderSelect&&IntrfProcsRec.pOrderSelect&&
    IntrfProcsRec.OrderType&&IntrfProcsRec.pOrderType&&
    IntrfProcsRec.OrderTicket&&IntrfProcsRec.pOrderTicket&&
    IntrfProcsRec.OrderCloseTime&&IntrfProcsRec.pOrderCloseTime&&
    IntrfProcsRec.OrderSymbol&&IntrfProcsRec.pOrderSymbol&&
    IntrfProcsRec.OrderMagicNumber&&IntrfProcsRec.pOrderMagicNumber;
}
void PauseForTradeLimit(const char *reason)
{
  tradeLimitBlocked=true;
  if(tradeLimitNotificationSent)return;
  char text[256];
  if(reason)sprintf(text,"RBP auto-stop failure: %s; entries blocked",reason);
  else sprintf(text,"RBP auto-stop: completed=%d limit=%d; test paused",cachedCompletedStrategyTrades,MaximumCompletedStrategyTrades);
  if(IntrfProcsRec.Pause&&IntrfProcsRec.pPause)
  {
    Pause((PChar)"");
    EmitStatus((PChar)text);
  }
  else
  {
    if(reason)sprintf(text,"RBP auto-stop failure: %s; Pause API unavailable, entries blocked",reason);
    else sprintf(text,"RBP auto-stop failure: completed=%d limit=%d; Pause API unavailable, entries blocked",cachedCompletedStrategyTrades,MaximumCompletedStrategyTrades);
    EmitStatus((PChar)text);
  }
  tradeLimitNotificationSent=true;
}
int CompareTradeTickets(const void *left,const void *right)
{
  int a=*(const int*)left,b=*(const int*)right;
  return a<b?-1:a>b?1:0;
}
bool ReconcileCompletedStrategyTrades()
{
  if(MaximumCompletedStrategyTrades==0)return true;
  if(MaximumCompletedStrategyTrades<0){PauseForTradeLimit("invalid negative trade limit");return false;}
  if(!StrategyTradeHistoryReady()){PauseForTradeLimit("required history API/context unavailable");return false;}
  int total=HistoryTotal();
  if(total<0){PauseForTradeLimit("HistoryTotal returned an invalid count");return false;}
  if(tradeHistoryCacheValid&&total==cachedHistoryTotal)return true;
  if(total>0)
  {
    if((unsigned long)total>0x3fffffffUL){PauseForTradeLimit("history count exceeds safe reconciliation size");return false;}
    int *tickets=(int*)malloc(sizeof(int)*(size_t)total);
    if(!tickets){PauseForTradeLimit("unable to allocate history reconciliation buffer");return false;}
    int collected=0;
    for(int i=0;i<total;i++)
    {
      if(!OrderSelect(i,SELECT_BY_POS,MODE_HISTORY)){free(tickets);PauseForTradeLimit("history order selection failed");return false;}
      int type=(int)OrderType(),ticket=OrderTicket();
      if((type!=tp_Buy&&type!=tp_Sell)||ticket<0||OrderCloseTime()<=0||!OrderSymbol()||
        strcmp(OrderSymbol(),Currency)!=0||OrderMagicNumber()!=MagicNumber)continue;
      tickets[collected++]=ticket;
    }
    qsort(tickets,(size_t)collected,sizeof(int),CompareTradeTickets);
    int count=0;for(int i=0;i<collected;i++)if(i==0||tickets[i]!=tickets[i-1])count++;
    free(tickets);cachedCompletedStrategyTrades=count;
  }
  else cachedCompletedStrategyTrades=0;
  // History selection and live-order selection share one SDK cursor. Leave it
  // on the open-order pool before any later position-management call.
  if(IntrfProcsRec.OrdersTotal&&IntrfProcsRec.pOrdersTotal)
    for(int i=0;i<OrdersTotal();i++)OrderSelect(i,SELECT_BY_POS,MODE_TRADES);
  cachedHistoryTotal=total;tradeHistoryCacheValid=true;
  return true;
}
bool CheckTradeLimit()
{
  if(tradeLimitBlocked)return false;
  if(MaximumCompletedStrategyTrades==0)return true;
  if(!ReconcileCompletedStrategyTrades())return false;
  if(cachedCompletedStrategyTrades>=MaximumCompletedStrategyTrades)
  {PauseForTradeLimit(NULL);return false;}
  return true;
}
bool StrategyPositionOpen();
bool RiskReady(double &mult);
void ArmFirstTouch(const char *action);
void RecoverFirstTouchChronologically(const long firstBar);
void ProcessFirstTouchTick(const bool historyReady);
PChar CurrentDiagnostic()
{
  if(StrategyPositionOpen()) return (PChar)"RBP: position busy / no pyramiding";
  if(stage==0)
  {
    if(SetupModeSetting==0) return (PChar)"RBP: range/breakout not qualified";
    double o=Open(1),h=High(1),l=Low(1),c=Close(1),body=fabs(c-o);
    bool directional=c!=o;
    if(ImpulseMaximumBodyATR>0&&directional&&body>=rmaATR*ImpulseMinimumBodyATR&&h-l>=rmaATR*ImpulseMinimumRangeATR&&body>rmaATR*ImpulseMaximumBodyATR) return (PChar)"RBP: impulse body exceeds maximum ATR";
    return (PChar)"RBP: impulse body/range not qualified";
  }
  if(stage==1) return (PChar)"RBP: extension pending";
  if(stage==IMPULSE_PARENT) return (PChar)"RBP: tracking parent impulse";
  if(stage==IMPULSE_PULLBACK) return (PChar)"RBP: tracking first pullback";
  if(stage==IMPULSE_ARMED) return (PChar)"RBP: first pullback armed / resumption pending";
  if(stage==IMPULSE_CONSUMED) return (PChar)"RBP: impulse consumed / new structural leg required";
  if(stage==2)
  {
    if(!pullbackStarted) return (PChar)"RBP: pullback not started";
    if(opposing<MinimumOpposingCloses) return (PChar)"RBP: insufficient opposing closes";
    double ext=dir==1?frozenExtension-frozenR:frozenS-frozenExtension,ret=dir==1?frozenExtension-pullbackExtreme:pullbackExtreme-frozenExtension,depth=ext>0?ret/ext*100:0;
    if(ret<setupATR*ActiveMinimumRetracement()) return (PChar)"RBP: ATR retracement not reached";
    if(!activeImpulseSetup&&PullbackDepthMode!=0)
    {
      double lo=PullbackDepthMode==1?Min(ShallowMinimumDepth,ShallowMaximumDepth):PullbackDepthMode==2?Min(DeepMinimumDepth,DeepMaximumDepth):Min(CustomMinimumDepth,CustomMaximumDepth);
      if(depth<lo) return (PChar)"RBP: pullback below configured minimum depth";
    }
    return (PChar)"RBP: pullback qualifying";
  }
  if(stage==3){double mult=0;return RiskReady(mult)?(PChar)"RBP: resumption confirmation not satisfied":(PChar)"RBP: ATR/risk not ready";}
  return (PChar)"RBP: entry requested";
}
void Status(bool blocked){if(ShowBlackoutStatus&&blocked){EmitStatus((PChar)"RBP: FX weekend blackout");return;}if(ShowDiagnostics)EmitStatus(CurrentDiagnostic());}
void ClearClusters(){rCount=sCount=0;rFirst=rLast=sFirst=sLast=-1;resistance=support=0;}
bool StrategyPositionOpen(){for(int i=0;i<OrdersTotal();i++) if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES) && OrderMagicNumber()==MagicNumber && strcmp(OrderSymbol(),Currency)==0) return true; return false;}
void Flatten(){for(int i=OrdersTotal()-1;i>=0;i--) if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES) && OrderMagicNumber()==MagicNumber && strcmp(OrderSymbol(),Currency)==0) CloseOrder(OrderTicket());}
bool IsPivotHigh(int strength,double &p){int c=strength+1; p=High(c); for(int i=1;i<=strength;i++) if(p<High(c-i)||p<High(c+i)) return false; return true;}
bool IsPivotLow(int strength,double &p){int c=strength+1; p=Low(c); for(int i=1;i<=strength;i++) if(p>Low(c-i)||p>Low(c+i)) return false; return true;}
void UpdateStructuralPivots()
{
  int strength=MinorSwingStrength<1?1:MinorSwingStrength,center=strength+1; double p; long pivotBar=nbar-strength;
  if(IsPivotHigh(strength,p)&&pivotBar!=latestSwingHighBar)
  {
    if(haveLatestSwingHigh){previousSwingHigh=latestSwingHigh;previousSwingHighBar=latestSwingHighBar;previousSwingHighTime=latestSwingHighTime;havePreviousSwingHigh=true;}
    latestSwingHigh=p;latestSwingHighBar=pivotBar;latestSwingHighTime=Time(center);haveLatestSwingHigh=true;
  }
  if(IsPivotLow(strength,p)&&pivotBar!=latestSwingLowBar)
  {
    if(haveLatestSwingLow){previousSwingLow=latestSwingLow;previousSwingLowBar=latestSwingLowBar;previousSwingLowTime=latestSwingLowTime;havePreviousSwingLow=true;}
    latestSwingLow=p;latestSwingLowBar=pivotBar;latestSwingLowTime=Time(center);haveLatestSwingLow=true;
  }
}
int StructuralTrend()
{
  if(!havePreviousSwingHigh||!haveLatestSwingHigh||!havePreviousSwingLow||!haveLatestSwingLow) return 0;
  if(latestSwingHigh>previousSwingHigh&&latestSwingLow>previousSwingLow) return 1;
  if(latestSwingHigh<previousSwingHigh&&latestSwingLow<previousSwingLow) return -1;
  return 0;
}
bool LatestImpulseOrigin(const int d,double &price,long &bar,TDateTime &time)
{
  if(d==1&&haveLatestSwingLow){price=latestSwingLow;bar=latestSwingLowBar;time=latestSwingLowTime;return true;}
  if(d==-1&&haveLatestSwingHigh){price=latestSwingHigh;bar=latestSwingHighBar;time=latestSwingHighTime;return true;}
  return false;
}
// Admission integrity is independent of seed size, qualification and execution policy.
bool ValidImpulseGeometry(const int direction,const double origin,const double extreme)
{
  double displacement=direction*(extreme-origin);
  return (direction==1||direction==-1)&&_finite(origin)&&origin>0&&_finite(extreme)&&extreme>0&&_finite(displacement)&&displacement>0;
}
void LogPromotion(ProvisionalImpulseCandidate &candidate,const char *disposition,const char *reason,
  const bool originAvailable,const double origin,const long originBar,const TDateTime originTime)
{
  if(!candidate.active||!ShowDiagnostics)return;
  double displacement=originAvailable?candidate.direction*(candidate.extreme-origin):0;
  double required=candidate.seedATR*Max(ImpulseMinimumBodyATR,ImpulseMinimumRangeATR);
  char key[1400];
  // Current quote/bar is deliberately absent: identical eligibility on later quotes
  // is one event. Changed candidate, origin, owner or consumption is a new event.
  _snprintf_s(key,sizeof(key),_TRUNCATE,"%s|%s|%d|%ld|%.17g|%.17g|%ld|%.17g|%.17g|%.17g|%d%d%d|%d|%.17g|%ld|%.17g|%d|%d|%ld|%.17g|%ld|%.17g|%d|%ld|%.17g|%.17g",
    disposition,reason,candidate.direction,candidate.firstBar,candidate.firstTime,candidate.firstExtreme,candidate.extremeBar,candidate.extremeTime,candidate.extreme,candidate.seedATR,
    candidate.normalSeedSeen,candidate.oversizedSeen,candidate.continuationSeen,originAvailable,origin,originBar,originTime,
    stage,dir,impulseOriginBar,impulseOrigin,qualifiedFirstSeedBar,setupExtreme,consumedDirection,consumedBar,consumedExtreme,required);
  if(strcmp(key,candidate.lastPromotion)==0)return;
  strcpy_s(candidate.lastPromotion,key);
  char currentText[32],firstText[32],originText[32],text[1600];
  FormatTime(lastTime,currentText);FormatTime(candidate.firstTime,firstText);FormatTime(originTime,originText);
  _snprintf_s(text,sizeof(text),_TRUNCATE,"RBP promotion %s dir=%s reason=%s currentBar=%ld currentTime=%s firstBar=%ld firstTime=%s firstExtreme=%.5f extremeBar=%ld extreme=%.5f seedATR=%.5f normal=%s oversized=%s continuation=%s originAvailable=%s originBar=%ld originTime=%s origin=%.5f signedDisplacement=%.5f requiredDisplacement=%.5f ownerDir=%d ownerStage=%d ownerOriginBar=%ld ownerOrigin=%.5f ownerFirstBar=%ld ownerExtreme=%.5f consumedDir=%d consumedBar=%ld consumedExtreme=%.5f",
    disposition,DirectionName(candidate.direction),reason,nbar,currentText,candidate.firstBar,firstText,candidate.firstExtreme,candidate.extremeBar,candidate.extreme,candidate.seedATR,
    candidate.normalSeedSeen?"yes":"no",candidate.oversizedSeen?"yes":"no",candidate.continuationSeen?"yes":"no",originAvailable?"yes":"no",originBar,originText,origin,displacement,required,
    dir,stage,impulseOriginBar,impulseOrigin,qualifiedFirstSeedBar,setupExtreme,consumedDirection,consumedBar,consumedExtreme);
  EmitDiagnostic(text);
}
void LogPromotionCurrent(ProvisionalImpulseCandidate &candidate,const char *disposition,const char *reason)
{
  double origin=0;long bar=-1;TDateTime time=0;bool available=LatestImpulseOrigin(candidate.direction,origin,bar,time);
  LogPromotion(candidate,disposition,reason,available,origin,bar,time);
}
void SelectedDepthLimits(double &minimumDepth,double &maximumDepth,bool &hasMaximum)
{
  minimumDepth=0;maximumDepth=0;hasMaximum=PullbackDepthMode!=0;
  if(PullbackDepthMode==1){minimumDepth=Min(ShallowMinimumDepth,ShallowMaximumDepth);maximumDepth=Max(ShallowMinimumDepth,ShallowMaximumDepth);}
  if(PullbackDepthMode==2){minimumDepth=Min(DeepMinimumDepth,DeepMaximumDepth);maximumDepth=Max(DeepMinimumDepth,DeepMaximumDepth);}
  if(PullbackDepthMode==3){minimumDepth=Min(CustomMinimumDepth,CustomMaximumDepth);maximumDepth=Max(CustomMinimumDepth,CustomMaximumDepth);}
}
bool ImpulseDepthExceeded(const double depth)
{
  double minimumDepth=0,maximumDepth=0;bool hasMaximum=false;SelectedDepthLimits(minimumDepth,maximumDepth,hasMaximum);
  if(depth>ImpulseMaximumDepth) return true;
  return hasMaximum&&depth>maximumDepth;
}
bool ImpulseDepthQualified(const double depth)
{
  double minimumDepth=0,maximumDepth=0;bool hasMaximum=false;SelectedDepthLimits(minimumDepth,maximumDepth,hasMaximum);
  return depth>0&&depth>=minimumDepth&&!ImpulseDepthExceeded(depth);
}
bool ValidateQualifiedSettings()
{
  if(!QualifiedMode())return true;
  double lo=0,hi=0;bool limited=false;SelectedDepthLimits(lo,hi,limited);
  double maximum=limited?Min(ImpulseMaximumDepth,hi):ImpulseMaximumDepth;
  bool valid=QualifiedImpulseRangeLookbackBars>=1&&_finite(QualifiedImpulseMinimumDisplacementATR)&&QualifiedImpulseMinimumDisplacementATR>0&&
    _finite(QualifiedImpulseBreakoutBufferATR)&&QualifiedImpulseBreakoutBufferATR>=0&&_finite(QualifiedPullbackMinimumDepth)&&
    _finite(maximum)&&QualifiedPullbackMinimumDepth>0&&QualifiedPullbackMinimumDepth<maximum;
  if(!valid)Print((PChar)"RBP qualified configuration error: finite lookback >=1, displacement >0, buffer >=0 and 0 < minimum depth < effective maximum required; entries blocked");
  if(!valid)return false;
  if(StructureMode())
  {
    valid=StructureContextPivotStrength>=1&&StructureContextPivotStrength<=(StructureContextScan-1)/2&&
      _finite(StructureImpulseMinimumEfficiency)&&StructureImpulseMinimumEfficiency>0&&StructureImpulseMinimumEfficiency<=1&&
      StructureImpulseMaximumBars>=1&&StructureImpulseMaximumBars<=ImpulseMaximumPullbackBars;
    if(!valid)Print((PChar)"RBP structure configuration error: strength 1..99, finite 0 < efficiency <= 1, and 1 <= maximum bars <= impulse recovery bound required; entries blocked");
  }
  return valid;
}
double ImpulseDisplacement(){return dir==1?setupExtreme-impulseOrigin:impulseOrigin-setupExtreme;}
double PullbackRetracement(){return dir==1?setupExtreme-pullbackExtreme:pullbackExtreme-setupExtreme;}
double PullbackDepth(){double displacement=ImpulseDisplacement();return displacement>0?PullbackRetracement()/displacement*100.0:0;}
bool ImpulseInvalidated(const double close){return dir==1?close<impulseOrigin-setupATR*InvalidationToleranceATR:close>impulseOrigin+setupATR*InvalidationToleranceATR;}
int SeedClassification(const int d,const double o,const double h,const double l,const double c,double &bodyATR,double &rangeATR)
{
  double body=d==1?c-o:o-c;bodyATR=rmaATR>0?body/rmaATR:0;rangeATR=rmaATR>0?(h-l)/rmaATR:0;
  if(bodyATR<ImpulseMinimumBodyATR||rangeATR<ImpulseMinimumRangeATR) return 0;
  return ImpulseMaximumBodyATR>0&&bodyATR>ImpulseMaximumBodyATR?2:1;
}
bool CanSeedImpulse(const int seedDirection,const double seedExtreme,double &origin,long &originBar,TDateTime &originTime,const char *&reason)
{
  if(!LatestImpulseOrigin(seedDirection,origin,originBar,originTime)){reason="confirmed directional origin unavailable";return false;}
  if(!ValidImpulseGeometry(seedDirection,origin,seedExtreme)){reason="invalid directional geometry";return false;}
  if(originBar>=nbar){reason="confirmed origin is not before expansion";return false;}
  if(stage!=IMPULSE_CONSUMED){reason="confirmed origin and directional expansion eligible";return true;}
  if(originBar<=consumedBar){reason="confirmed origin is not newer than consumed parent";return false;}
  if(!StructureMode()&&seedDirection==consumedDirection&&!(seedDirection==1?seedExtreme>consumedExtreme:seedExtreme<consumedExtreme))
  {reason="same-direction expansion did not exceed consumed parent";return false;}
  reason=StructureMode()?"fresh confirmed origin permits new structure impulse; prior extreme not required":"new post-consumption structural origin and expansion eligible";return true;
}
void LogSeedDecision(const int seedDirection,const char *decision,const char *reason,const double bodyATR,const double rangeATR,const bool busy)
{
  char text[512];sprintf(text,"RBP seed %s dir=%s reason=%s body=%.2fATR range=%.2fATR busy=%s execution=%s",decision,DirectionName(seedDirection),reason,bodyATR,rangeATR,busy?"yes":"no",((seedDirection==1&&TradeDirection==2)||(seedDirection==-1&&TradeDirection==1))?"structure-only":"direction-permitted");EmitDiagnostic(text);
}
void BeginQualification(const long firstSeed);
bool QualificationExpired(const long bar);
void AbandonQualification(const char *reason);
bool TryQualifyImpulse(const long bar);
bool StartImpulseAt(const int seedDirection,const double seedExtreme,const long endpointBar,const TDateTime endpointTime,const double origin,const long originBar,const TDateTime originTime,const double atr,const TDateTime seedTime,const char *eligibilityReason)
{
  // Validate before touching ANY parent, qualification, touch or candidate state.
  if(!ValidImpulseGeometry(seedDirection,origin,seedExtreme))
  {
    char text[384];_snprintf_s(text,sizeof(text),_TRUNCATE,"RBP parent rejected dir=%d reason=invalid directional geometry originBar=%ld origin=%.5f endpointBar=%ld extreme=%.5f signedDisplacement=%.5f",seedDirection,originBar,origin,endpointBar,seedExtreme,seedDirection*(seedExtreme-origin));EmitDiagnostic(text);return false;
  }
  ResetTouch();ResetQualification();
  dir=seedDirection;stage=IMPULSE_PARENT;activeImpulseSetup=true;setupATR=atr;impulseOrigin=origin;impulseOriginBar=originBar;impulseOriginTime=originTime;
  setupExtreme=seedExtreme;impulseEndpointBar=endpointBar;impulseEndpointTime=endpointTime;impulseSeedTime=seedTime;breakout=extension=endpointBar;
  pullbackStarted=false;pullbackStartBar=-1;opposing=0;hasMinorSwing=false;hasFrozenExtension=false;
  char originText[32],seedText[32],endpointText[32],text[768];FormatTime(impulseOriginTime,originText);FormatTime(impulseSeedTime,seedText);FormatTime(impulseEndpointTime,endpointText);
  _snprintf_s(text,sizeof(text),_TRUNCATE,"RBP parent accepted dir=%s reason=%s origin=%s@%.5f seed=%s endpoint=%s@%.5f displacement=%.5f/%.2fATR execution=%s originBar=%ld consumedDir=%d consumedBar=%ld consumedExtreme=%.5f",DirectionName(dir),eligibilityReason,originText,impulseOrigin,seedText,endpointText,setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0,((dir==1&&TradeDirection==2)||(dir==-1&&TradeDirection==1))?"structure-only":"direction-permitted",impulseOriginBar,consumedDirection,consumedBar,consumedExtreme);
  EmitDiagnostic(text);
  ResetProvisionalCandidates();
  if(QualifiedMode())BeginQualification(endpointBar);
  return true;
}
bool StartImpulse(const int seedDirection,const double seedExtreme,const double origin,const long originBar,const TDateTime originTime,const char *eligibilityReason)
{
  if(!StartImpulseAt(seedDirection,seedExtreme,nbar,Time(1),origin,originBar,originTime,rmaATR,Time(1),eligibilityReason))return false;
  if(StructureMode()&&QualificationExpired(nbar)){AbandonQualification("origin qualification deadline exceeded at seed");return true;}
  if(QualifiedMode())TryQualifyImpulse(nbar);
  if(FirstTouchMode()) ArmFirstTouch("armed");
  return true;
}
void ObserveProvisional(const int seedDirection,const double seedExtreme,const int classification,const double bodyATR,const double rangeATR,const bool busy,const char *reason)
{
  ProvisionalImpulseCandidate &candidate=CandidateFor(seedDirection);
  if(!candidate.active)
  {
    candidate.active=true;candidate.direction=seedDirection;candidate.observedWhileBusy=busy;candidate.normalSeedSeen=classification==1;candidate.oversizedSeen=classification==2;
    candidate.seedATR=rmaATR;candidate.firstExtreme=candidate.extreme=seedExtreme;candidate.firstBar=candidate.extremeBar=nbar;candidate.firstTime=candidate.extremeTime=Time(1);
    LogSeedDecision(seedDirection,"retained provisionally",reason,bodyATR,rangeATR,busy);
    if(classification==2)LogSeedDecision(seedDirection,"body classification","body cap prevents normal seed; cumulative parent recovery retained",bodyATR,rangeATR,busy);
    if(busy){char text[256];sprintf(text,"RBP candidate observed while position open dir=%s bar=%ld; detection continues without entry",DirectionName(seedDirection),nbar);EmitDiagnostic(text);}
    return;
  }
  bool firstLaterNormal=candidate.oversizedSeen&&!candidate.normalSeedSeen&&classification==1;
  candidate.observedWhileBusy=candidate.observedWhileBusy||busy;candidate.normalSeedSeen=candidate.normalSeedSeen||classification==1;candidate.oversizedSeen=candidate.oversizedSeen||classification==2;
  if(firstLaterNormal){char text[384];sprintf(text,"RBP oversized recovery evidence dir=%s type=later normal same-direction seed bar=%ld body=%.2fATR range=%.2fATR",DirectionName(seedDirection),nbar,bodyATR,rangeATR);EmitDiagnostic(text);}
  if(seedDirection==1?seedExtreme>candidate.extreme:seedExtreme<candidate.extreme){candidate.continuationSeen=candidate.continuationSeen||nbar>candidate.firstBar;candidate.extreme=seedExtreme;candidate.extremeBar=nbar;candidate.extremeTime=Time(1);}
}
void ObserveProvisionalContinuation(const int direction,const double extreme,const bool busy)
{
  ProvisionalImpulseCandidate &candidate=CandidateFor(direction);if(!candidate.active)return;
  candidate.observedWhileBusy=candidate.observedWhileBusy||busy;
  if(nbar>candidate.firstBar&&(direction==1?extreme>candidate.extreme:extreme<candidate.extreme))
  {
    candidate.continuationSeen=true;candidate.extreme=extreme;candidate.extremeBar=nbar;candidate.extremeTime=Time(1);
    if(candidate.oversizedSeen){char text[384];sprintf(text,"RBP oversized recovery evidence dir=%s type=later same-direction endpoint advancement bar=%ld endpoint=%.5f firstExtreme=%.5f",DirectionName(direction),nbar,extreme,candidate.firstExtreme);EmitDiagnostic(text);}
  }
}
int ClosedBarShift(const long bar){return (int)(nbar-bar+1);}
double ClosedHigh(const long bar){return High(ClosedBarShift(bar));}
double ClosedLow(const long bar){return Low(ClosedBarShift(bar));}
double ClosedClose(const long bar){return Close(ClosedBarShift(bar));}
TDateTime ClosedTime(const long bar){return Time(ClosedBarShift(bar));}
void ConsumeImpulse(const char *reason);
void LogQualification(const char *event,const char *reason);
bool RecoveredMinorPivot(const int direction,const long confirmationBar,double &price)
{
  int strength=MinorSwingStrength<1?1:MinorSwingStrength;long center=confirmationBar-strength;
  if(center<pullbackStartBar)return false;
  price=direction==1?ClosedHigh(center):ClosedLow(center);
  for(int i=1;i<=strength;i++)
  {
    if(direction==1&&(price<ClosedHigh(center-i)||price<ClosedHigh(center+i)))return false;
    if(direction==-1&&(price>ClosedLow(center-i)||price>ClosedLow(center+i)))return false;
  }
  return true;
}
bool RecoveredResumption(const int direction,const long bar)
{
  double c=ClosedClose(bar),o=Open(ClosedBarShift(bar)),previousClose=ClosedClose(bar-1);
  if(Confirmation==0)return direction==1?c>previousClose:c<previousClose;
  if(Confirmation==1)return direction==1?c>o&&c>ClosedHigh(bar-1):c<o&&c<ClosedLow(bar-1);
  return hasMinorSwing&&(direction==1?c>minorSwing:c<minorSwing);
}
void LogRecoveredEndpoint(const long bar)
{
  char endpointText[32],text[384];FormatTime(impulseEndpointTime,endpointText);
  sprintf(text,"RBP replay endpoint dir=%s bar=%ld time=%s endpoint=%.5f displacement=%.5f/%.2fATR",DirectionName(dir),bar,endpointText,setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0);EmitDiagnostic(text);
}
void RecoverProvisionalChronologically(const long replayStartBar)
{
  long qualificationBar=-1;TDateTime qualificationTime=0;
  for(long bar=replayStartBar;bar<=nbar;bar++)
  {
    double h=ClosedHigh(bar),l=ClosedLow(bar),c=ClosedClose(bar),previousClose=ClosedClose(bar-1);
    if(stage==IMPULSE_PARENT||stage==IMPULSE_PULLBACK)
    {
      if(ImpulseInvalidated(c)){ConsumeImpulse("recovered parent invalidated during chronological replay");return;}
      bool newExtreme=dir==1?h>setupExtreme:l<setupExtreme;
      if(newExtreme)
      {
        setupExtreme=dir==1?h:l;impulseEndpointBar=bar;impulseEndpointTime=ClosedTime(bar);extension=bar;LogRecoveredEndpoint(bar);
        if(stage==IMPULSE_PULLBACK)
        {
          char text[384];sprintf(text,"RBP recovered PB1 reset dir=%s bar=%ld reason=new same-direction extreme before qualification",DirectionName(dir),bar);EmitDiagnostic(text);
          stage=IMPULSE_PARENT;pullbackStarted=false;pullbackStartBar=-1;pullbackStartTime=0;opposing=0;pullbackExtreme=0;pullbackConfirmed=-1;hasMinorSwing=false;
        }
        continue;
      }
      bool opposingClose=dir==1?c<previousClose:c>previousClose;
      if(stage==IMPULSE_PARENT&&opposingClose)
      {
        stage=IMPULSE_PULLBACK;pullbackStarted=true;pullbackStartBar=bar;pullbackStartTime=ClosedTime(bar);pullbackExtreme=dir==1?l:h;pullbackStartPrice=pullbackExtreme;opposing=1;hasMinorSwing=false;
        frozenExtension=setupExtreme;hasFrozenExtension=true;extension=bar;
        char startText[32],text[512];FormatTime(pullbackStartTime,startText);
        sprintf(text,"RBP recovered PB1 start dir=%s bar=%ld time=%s@%.5f endpoint=%.5f",DirectionName(dir),bar,startText,pullbackStartPrice,setupExtreme);EmitDiagnostic(text);
      }
      else if(stage==IMPULSE_PULLBACK)
      {
        pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);if(opposingClose)opposing++;
      }
      if(stage!=IMPULSE_PULLBACK)continue;
      double recoveredPivot=0;if(RecoveredMinorPivot(dir,bar,recoveredPivot)){minorSwing=recoveredPivot;hasMinorSwing=true;}
      double retracement=PullbackRetracement(),depth=PullbackDepth();
      if(bar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("recovered first pullback expired during chronological replay");return;}
      if(ImpulseDepthExceeded(depth)){ConsumeImpulse("recovered first pullback exceeded maximum depth during chronological replay");return;}
      if(opposing>=MinimumOpposingCloses&&retracement>=setupATR*ActiveMinimumRetracement()&&ImpulseDepthQualified(depth))
      {
        stage=IMPULSE_ARMED;qualificationBar=pullbackConfirmed=bar;qualificationTime=ClosedTime(bar);
        char qualificationText[32],text[512];FormatTime(qualificationTime,qualificationText);
        sprintf(text,"RBP historical PB1 qualified dir=%s bar=%ld time=%s opposing=%d retracement=%.2fATR depth=%.1f%%",DirectionName(dir),qualificationBar,qualificationText,opposing,setupATR>0?retracement/setupATR:0,depth);EmitDiagnostic(text);
        DiagnosticMarker("START",impulseOriginTime,impulseOrigin,"I-start",clBlue);DiagnosticMarker("END",impulseEndpointTime,setupExtreme,"I-end",clBlue);DiagnosticMarker("PB1",pullbackStartTime,pullbackStartPrice,"PB1",clYellow);
      }
      continue;
    }
    if(stage!=IMPULSE_ARMED)return;
    pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);
    double recoveredPivot=0;if(RecoveredMinorPivot(dir,bar,recoveredPivot)){minorSwing=recoveredPivot;hasMinorSwing=true;}
    double depth=PullbackDepth();
    if(ImpulseInvalidated(c)){ConsumeImpulse("recovered parent invalidated after historical PB1 qualification");return;}
    if(bar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("recovered first pullback expired after historical qualification");return;}
    if(ImpulseDepthExceeded(depth)){ConsumeImpulse("recovered first pullback exceeded maximum depth after historical qualification");return;}
    if(bar>qualificationBar&&RecoveredResumption(dir,bar))
    {
      char signalText[32],text[512];FormatTime(ClosedTime(bar),signalText);
      if(bar<nbar)
      {
        sprintf(text,"RBP historical resumption found dir=%s mode=%s bar=%ld time=%s decision=consumed reason=PB1 resumption occurred before origin promotion; retrospective entry forbidden",DirectionName(dir),Confirmation==0?"aggressive":Confirmation==1?"balanced":"conservative",bar,signalText);EmitDiagnostic(text);
        ConsumeImpulse("PB1 resumption occurred before origin promotion; retrospective entry forbidden");return;
      }
      bool busy=StrategyPositionOpen();
      sprintf(text,"RBP current-bar promotion resumption dir=%s mode=%s bar=%ld time=%s busy=%s decision=consumed-not-armed",DirectionName(dir),Confirmation==0?"aggressive":Confirmation==1?"balanced":"conservative",bar,signalText,busy?"yes":"no");EmitDiagnostic(text);
      ConsumeImpulse(busy?"position busy at current-bar promotion resumption; opportunity unavailable and not queued":"PB1 resumption occurred on origin promotion bar; conservative retrospective entry forbidden");return;
    }
  }
  char text[512];
  if(stage==IMPULSE_ARMED)
  {
    char qualificationText[32];FormatTime(qualificationTime,qualificationText);
    sprintf(text,"RBP replay complete dir=%s historicalQualification=%s bar=%ld resumption=none-through-promotion decision=armed-for-future-only",DirectionName(dir),qualificationText,qualificationBar);EmitDiagnostic(text);
  }
  else
  {
    sprintf(text,"RBP replay complete dir=%s stage=%s resumption=not-applicable endpointBar=%ld PB1=%s",DirectionName(dir),stage==IMPULSE_PARENT?"parent":"pullback",impulseEndpointBar,pullbackStarted?"unqualified":"not-started");EmitDiagnostic(text);
  }
}
bool TryPromoteCandidate(ProvisionalImpulseCandidate &candidate)
{
  if(!candidate.active)return false;
  if(stage==IMPULSE_PARENT||stage==IMPULSE_PULLBACK||stage==IMPULSE_ARMED)
  {LogPromotionCurrent(candidate,"pending","active parent owns setup; retain/wait");return false;}
  if(nbar-candidate.firstBar>ImpulseMaximumPullbackBars)
  {
    LogPromotionCurrent(candidate,"retired","origin recovery window expired");ResetCandidate(candidate);return false;
  }
  double origin=0;long originBar=-1;TDateTime originTime=0;const char *reason="";
  if(!CanSeedImpulse(candidate.direction,candidate.extreme,origin,originBar,originTime,reason))
  {LogPromotionCurrent(candidate,"pending",reason);return false;}
  if(originBar>candidate.firstBar)
  {LogPromotionCurrent(candidate,"pending","latest confirmed origin after candidate first seed; retain/wait for current seed retirement");return false;}
  if(StructureMode()&&candidate.firstBar-originBar>StructureImpulseMaximumBars)
  {
    LogPromotionCurrent(candidate,"retired","provisional first seed beyond origin qualification deadline");ResetCandidate(candidate);return false;
  }
  if(candidate.oversizedSeen&&!candidate.normalSeedSeen&&!candidate.continuationSeen)
  {LogPromotionCurrent(candidate,"pending","no later normal seed or same-direction endpoint advancement; retain/wait");return false;}
  double displacement=candidate.direction*(candidate.extreme-origin);
  double requiredATR=Max(ImpulseMinimumBodyATR,ImpulseMinimumRangeATR);
  if(!_finite(candidate.seedATR)||candidate.seedATR<=0)
  {LogPromotionCurrent(candidate,"rejected","invalid seed ATR; retain/wait");return false;}
  if(displacement<candidate.seedATR*requiredATR)
  {LogPromotionCurrent(candidate,"pending","insufficient cumulative displacement; retain/wait");return false;}
  bool observedBusy=candidate.observedWhileBusy,oversized=candidate.oversizedSeen,laterNormal=candidate.normalSeedSeen;TDateTime seedTime=candidate.firstTime;long firstBar=candidate.firstBar;
  double initialEndpoint=candidate.direction==1?ClosedHigh(originBar):ClosedLow(originBar);long initialEndpointBar=originBar;
  for(long bar=originBar+1;bar<=firstBar;bar++)
  {
    double value=candidate.direction==1?ClosedHigh(bar):ClosedLow(bar);
    if(candidate.direction==1?value>initialEndpoint:value<initialEndpoint){initialEndpoint=value;initialEndpointBar=bar;}
  }
  char originText[32],text[512];FormatTime(originTime,originText);
  sprintf(text,"RBP confirmed origin available dir=%s reason=%s origin=%s@%.5f provisionalFirstBar=%ld cumulative=%.5f/%.2fATR oversizedIncluded=%s evidence=%s",DirectionName(candidate.direction),reason,originText,origin,firstBar,displacement,displacement/candidate.seedATR,oversized?"yes":"no",oversized?(laterNormal?"later normal same-direction seed":"later same-direction endpoint advancement"):"normal provisional seed");EmitDiagnostic(text);
  if(!ValidImpulseGeometry(candidate.direction,origin,initialEndpoint))
  {LogPromotionCurrent(candidate,"rejected","invalid directional geometry at recovered initial endpoint; retain/wait");return false;}
  LogPromotionCurrent(candidate,"promoted",reason);
  if(!StartImpulseAt(candidate.direction,initialEndpoint,initialEndpointBar,ClosedTime(initialEndpointBar),origin,originBar,originTime,candidate.seedATR,seedTime,reason))return false;
  if(QualifiedMode())qualifiedFirstSeedBar=firstBar;
  if(FirstTouchMode()) RecoverFirstTouchChronologically(firstBar);
  else RecoverProvisionalChronologically(firstBar+1);
  sprintf(text,"RBP provisional promoted dir=%s observedWhileBusy=%s replayStartBar=%ld currentBar=%ld finalStage=%d retrospectiveEntry=no",DirectionName(dir),observedBusy?"yes":"no",firstBar+1,nbar,stage);EmitDiagnostic(text);
  return true;
}
void ClearUnqualifiedPullback()
{
  stage=IMPULSE_PARENT;pullbackStarted=false;pullbackStartBar=-1;pullbackStartTime=0;opposing=0;pullbackExtreme=0;pullbackConfirmed=-1;hasMinorSwing=false;
}
void ConsumeImpulse(const char *reason)
{
  touchActive=false;
  if(QualifiedMode())LogQualification("consume",reason);
  bool hadPullback=pullbackStarted;double retracement=hadPullback?PullbackRetracement():0,depth=hadPullback?PullbackDepth():0;int closeCount=opposing;
  consumedDirection=dir;consumedExtreme=setupExtreme;consumedBar=nbar;stage=IMPULSE_CONSUMED;pullbackStarted=false;activeImpulseSetup=true;
  char endpointText[32],startText[32],text[512];FormatTime(impulseEndpointTime,endpointText);FormatTime(pullbackStartTime,startText);
  sprintf(text,"RBP impulse consumed dir=%s reason=%s endpoint=%s@%.5f displacement=%.5f/%.2fATR PB1=%s opposing=%d retracement=%.2fATR depth=%.1f%%",DirectionName(dir),reason,endpointText,setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0,hadPullback?startText:"n/a",closeCount,setupATR>0?retracement/setupATR:0,depth);
  EmitDiagnostic(text);
}
void StartFirstPullback(const double h,const double l)
{
  stage=IMPULSE_PULLBACK;pullbackStarted=true;pullbackStartBar=nbar;pullbackStartTime=Time(1);pullbackExtreme=dir==1?l:h;pullbackStartPrice=pullbackExtreme;opposing=1;hasMinorSwing=false;
  frozenExtension=setupExtreme;hasFrozenExtension=true;extension=nbar;
  char startText[32],text[512];FormatTime(pullbackStartTime,startText);
  sprintf(text,"RBP PB1 start dir=%s time=%s@%.5f endpoint=%.5f opposing=%d retracement=%.2fATR depth=%.1f%%",DirectionName(dir),startText,pullbackStartPrice,setupExtreme,opposing,setupATR>0?PullbackRetracement()/setupATR:0,PullbackDepth());
  EmitDiagnostic(text);
}
void LogPullbackReset()
{
  char endpointText[32],text[512];FormatTime(impulseEndpointTime,endpointText);
  sprintf(text,"RBP PB1 reset dir=%s reason=new impulse extreme endpoint=%s@%.5f displacement=%.5f/%.2fATR",DirectionName(dir),endpointText,setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0);
  EmitDiagnostic(text);
}
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
bool EnsureATRHistory()
{
  int needed=RegimeLookback>0?RegimeLookback:1;
  if(atrPctCapacity>=needed) return true;
  double *replacement=(double*)malloc(sizeof(double)*needed); if(!replacement) return false;
  for(int i=0;i<atrPctCount;i++) replacement[i]=atrPct[(atrPctNext-atrPctCount+i+atrPctCapacity)%atrPctCapacity];
  free(atrPct); atrPct=replacement; atrPctCapacity=needed; atrPctNext=atrPctCount%atrPctCapacity;
  return true;
}
void AddATRPercent(const double value)
{
  if(!EnsureATRHistory()) return;
  atrPct[atrPctNext]=value; atrPctNext=(atrPctNext+1)%atrPctCapacity;
  if(atrPctCount<atrPctCapacity) atrPctCount++;
}
double RecentATRPercent(const int offset)
{
  return atrPct[(atrPctNext-1-offset+atrPctCapacity)%atrPctCapacity];
}
void AddATR()
{
  double tr=Max(High(1)-Low(1),Max(fabs(High(1)-Close(2)),fabs(Low(1)-Close(2))));
  if(atrCount==0) rmaATR=tr; else if(atrCount<ATRLength) rmaATR=(rmaATR*atrCount+tr)/(atrCount+1); else rmaATR=(rmaATR*(ATRLength-1)+tr)/ATRLength;
  atrCount++;
  // Pine ta.atr/RMA is not valid until its ATRLength seed is complete.
  if(atrCount>=ATRLength && Close(1)!=0) AddATRPercent(rmaATR/Close(1)*100.0);
}
bool RiskReady(double &mult)
{
  if(atrCount<ATRLength || rmaATR<=0) return false; if(StopModeSetting==1){mult=FixedATRMultiplier;return true;} if(!EnsureATRHistory() || atrPctCount<RegimeLookback) return false;
  double cur=RecentATRPercent(0);int count=0;for(int i=0;i<RegimeLookback;i++)if(RecentATRPercent(i)<=cur)count++;double rank=100.0*count/RegimeLookback;
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
bool Enter(int d,double mult,const bool impulseTrade,const int structuralTrend,const bool checkedTouch=false)
{
  if(tradeLimitBlocked||!CheckTradeLimit())return false;
  ClampTargetSettings(); int relation=structuralTrend==0?0:((d==1&&structuralTrend==1)||(d==-1&&structuralTrend==-1)?1:-1);
  double targetR=impulseTrade&&relation==-1?CounterTrendTargetR:WithTrendNeutralTargetR;
  targetR=relation==-1?Max(3.0,targetR):Max(2.0,targetR);
  double riskTicks=Max(1,ceil(rmaATR*mult/Point()));double risk=riskTicks*Point(), target=Max(1,floor(riskTicks*targetR+0.5))*Point();double entry=d==1?Ask():Bid();double sl=d==1?Down(entry-risk):Up(entry+risk);double tp=d==1?Up(entry+target):Down(entry-target);int ticket=-1;
  bool success=true;
  if(checkedTouch)
  {
    char when[32],text[384];FormatTickTime(TimeCurrent(),when);
    sprintf(text,"RBP touch order attempt dir=%s planned=%.5f quote=%.5f time=%s lots=%.2f",DirectionName(d),touchLevel,entry,when,VolumeLots);EmitDiagnostic(text);
    bool result=SendInstantOrder(Currency,d==1?op_Buy:op_Sell,VolumeLots,sl,tp,(PChar)"RangeBreakoutPullback",MagicNumber,ticket);
    success=result&&ticket>=0;
    sprintf(text,"RBP touch order %s result=%s ticket=%d dir=%s quote=%.5f time=%s",success?"confirmed entry":"failure",result?"true":"false",ticket,DirectionName(d),entry,when);EmitDiagnostic(text);
    if(!success)return false;
  }
  frozenStructuralTrend=structuralTrend;frozenRiskDistance=risk;frozenTargetR=targetR;frozenTargetPrice=tp;
  if(impulseTrade)
  {
    char text[256];sprintf(text,"RBP entry frozen dir=%s structure=%s risk=%.5f target=%.1fR targetPrice=%.5f",DirectionName(d),TrendName(structuralTrend),frozenRiskDistance,frozenTargetR,frozenTargetPrice);EmitDiagnostic(text);
  }
  if(!checkedTouch) SendInstantOrder(Currency,d==1?op_Buy:op_Sell,VolumeLots,sl,tp,(PChar)"RangeBreakoutPullback",MagicNumber,ticket);
  return success;
}
bool ImpulseTrendConsensus(const int entryDirection,bool &ready)
{
  ready=false;
  int bars=iBars(Currency,PERIOD_M15);
  if(bars<201) return false;
  double ema50=0,ema200=0,ema10=0,ema30=0;
  int m15Count=0,h4Count=0;
  // Oldest-to-newest closed M15 bars seed recursive EMAs from their first close.
  for(int index=bars-1;index>=1;index--)
  {
    double close=iClose(Currency,PERIOD_M15,index);
    if(close==0) continue;
    m15Count++;
    if(m15Count==1){ema50=ema200=close;}else{ema50+=(close-ema50)*2.0/51.0;ema200+=(close-ema200)*2.0/201.0;}
    // Normalize the server-time slot before testing 00:00/04:00/08:00 boundaries.
    double normalized=iTime(Currency,PERIOD_M15,index)-ServerUTCOffsetHours/24.0;
    long minute=(long)floor(normalized*1440.0+0.5);
    int minuteOfDay=(int)(minute%1440); if(minuteOfDay<0) minuteOfDay+=1440;
    // The M15 bar ending at xx:00 is the prior xx:45 bar; only that completed close feeds H4.
    if(minuteOfDay%240==225)
    {
      h4Count++;
      if(h4Count==1){ema10=ema30=close;}else{ema10+=(close-ema10)*2.0/11.0;ema30+=(close-ema30)*2.0/31.0;}
    }
  }
  if(m15Count<200||h4Count<30) return false;
  ready=true;
  return entryDirection==1 ? ema50>ema200&&ema10>ema30 : ema50<ema200&&ema10<ema30;
}
double RequiredTouchRetracement()
{
  double ret=ImpulseMinimumRetracementATR*setupATR;
  return QualifiedMode()?Max(ret,QualifiedPullbackMinimumDepth/100.0*ImpulseDisplacement()):ret;
}
void LogStructure(const char *event,const char *reason)
{
  if(!StructureMode()||!ShowDiagnostics)return;
  char text[512],cutoff[32],origin[32],endpoint[32],qualified[32];
  FormatTime(structureCutoff,cutoff);FormatTime(impulseOriginTime,origin);FormatTime(impulseEndpointTime,endpoint);FormatTime(qualifyingTime,qualified);
  // Separate bounded records keep the reference audit trail below fixed buffers.
  _snprintf_s(text,sizeof(text),_TRUNCATE,"RBP structure %s dir=%s reason=%s cutoff=%s ready=%s barrier=%.5f origin=%s@%.5f endpoint=%s@%.5f qualifyingTime=%s efficiency=%.6f N=%ld path=%.6f",event,DirectionName(dir),reason,cutoff,structureContextReady?"yes":"no",structureBarrier,origin,impulseOrigin,endpoint,setupExtreme,qualified,structureEfficiency,structureDuration,structurePath);Print((PChar)text);
  if(structureContextReady)
    for(int i=0;i<2;i++)
    {
      char center[32],confirmation[32];FormatTime(structureReferences[i].center,center);FormatTime(structureReferences[i].confirmation,confirmation);
      _snprintf_s(text,sizeof(text),_TRUNCATE,"RBP structure reference %s dir=%s cutoff=%s pivot=%d center=%s confirmation=%s price=%.5f barrier=%.5f",event,DirectionName(dir),cutoff,i+1,center,confirmation,structureReferences[i].price,structureBarrier);Print((PChar)text);
    }
}
bool StructureContextPending(const char *reason)
{
  if(strcmp(lastStructureContextReason,reason)!=0)
  {strcpy_s(lastStructureContextReason,reason);LogStructure("context-pending",reason);}
  return false;
}
bool CaptureStructureContext()
{
  if(!StructureMode())return true;
  if(structureContextReady)return true;
  if(!qualifiedConfigValid)return false;
  if(!Currency||!*Currency||!_finite(structureCutoff)||structureCutoff<=0)
    return StructureContextPending("invalid frozen origin cutoff or Currency");
  if(!IntrfProcsRec.iBars||!IntrfProcsRec.piBars||!IntrfProcsRec.iTime||!IntrfProcsRec.piTime||
     !IntrfProcsRec.iOpen||!IntrfProcsRec.piOpen||!IntrfProcsRec.iHigh||!IntrfProcsRec.piHigh||
     !IntrfProcsRec.iLow||!IntrfProcsRec.piLow||!IntrfProcsRec.iClose||!IntrfProcsRec.piClose||
     !IntrfProcsRec.TimeCurrent||!IntrfProcsRec.pTimeCurrent)
    return StructureContextPending("indexed M15 OHLC/time API unavailable");
  struct ContextBar {double high,low;TDateTime time,end;};
  ContextBar history[StructureContextScan];int count=0;
  int available=iBars(Currency,StructureContextTimeframe);
  TDateTime clock=TimeCurrent(),previous=0;
  if(available<2||!_finite(clock)||clock<=0)return StructureContextPending("completed M15 history unavailable");
  for(int index=1;index<available&&count<StructureContextScan;index++)
  {
    TDateTime time=iTime(Currency,StructureContextTimeframe,index),end=time+15.0/1440.0;
    if(!_finite(time)||time<=0||(index>1&&time>=previous))return StructureContextPending("invalid M15 time chronology");
    previous=time;
    // Direct comparison, with no rounding/tolerance that could admit a future close.
    if(end>structureCutoff||end>clock)continue;
    double o=iOpen(Currency,StructureContextTimeframe,index),h=iHigh(Currency,StructureContextTimeframe,index),
      l=iLow(Currency,StructureContextTimeframe,index),c=iClose(Currency,StructureContextTimeframe,index);
    if(!_finite(o)||!_finite(h)||!_finite(l)||!_finite(c)||l<=0||h<l||o<l||o>h||c<l||c>h)
      return StructureContextPending("invalid eligible M15 OHLC");
    history[count++]={h,l,time,end};
  }
  int strength=StructureContextPivotStrength,found=0;StructureReference references[2]={0};
  for(int center=strength;center+strength<count&&found<2;center++)
  {
    double price=dir==1?history[center].high:history[center].low;bool pivot=true;
    for(int side=1;side<=strength;side++)
      if(dir==1?(price<=history[center-side].high||price<=history[center+side].high):
                (price>=history[center-side].low||price>=history[center+side].low)){pivot=false;break;}
    if(pivot)references[found++]={history[center].time,history[center-strength].end,price};
  }
  if(found<2)return StructureContextPending("two confirmed relevant M15 pivots unavailable in bounded history");
  memcpy(structureReferences,references,sizeof(references));
  structureBarrier=dir==1?Max(references[0].price,references[1].price):Min(references[0].price,references[1].price);
  structureContextReady=true;lastStructureContextReason[0]=0;LogStructure("context-frozen","two relevant pivots known by origin open");return true;
}
bool QualificationExpired(const long bar)
{
  return bar-qualifiedFirstSeedBar>ImpulseMaximumPullbackBars||
    (StructureMode()&&bar-impulseOriginBar>StructureImpulseMaximumBars);
}
bool MeasureStructureQuality(const long bar,const char *&reason)
{
  structureDuration=bar-impulseOriginBar;structurePath=structureEfficiency=0;
  if(structureDuration<1||structureDuration>StructureImpulseMaximumBars){reason="origin-to-close duration outside qualification bound";return false;}
  int first=ClosedBarShift(impulseOriginBar),last=ClosedBarShift(bar);
  if(last<1||first<last||(unsigned long)first>=Bars()){reason="M5 close path history unavailable";return false;}
  double origin=ClosedClose(impulseOriginBar),prior=origin;TDateTime priorTime=ClosedTime(impulseOriginBar);
  if(!_finite(origin)||origin<=0||!_finite(priorTime)||priorTime<=0){reason="invalid M5 origin close/time";return false;}
  for(long j=impulseOriginBar+1;j<=bar;j++)
  {
    double close=ClosedClose(j);TDateTime time=ClosedTime(j);
    if(!_finite(close)||close<=0||!_finite(time)||time<=priorTime){reason="missing or invalid M5 close path";return false;}
    structurePath+=fabs(close-prior);prior=close;priorTime=time;
  }
  if(!_finite(structurePath)||structurePath<=0){reason="zero or invalid M5 close path";return false;}
  structureEfficiency=dir*(prior-origin)/structurePath;
  if(!_finite(structureEfficiency)||structureEfficiency<StructureImpulseMinimumEfficiency){reason="directional efficiency below minimum";return false;}
  return true;
}
bool StructureGatePending(const char *reason)
{
  if(strcmp(lastStructureGateReason,reason)!=0)
  {strcpy_s(lastStructureGateReason,reason);LogStructure("pending",reason);}
  return false;
}
void LogQualification(const char *event,const char *reason)
{
  LogStructure(event,reason);
  char text[512],when[32];FormatTime(qualifyingTime,when);
  _snprintf_s(text,sizeof(text),_TRUNCATE,"RBP qualified %s dir=%s reason=%s range=%.5f/%.5f originBar=%ld origin=%.5f endpointBar=%ld endpoint=%.5f displacement=%.5f/%.2fATR required=%.5f qualifyingBar=%ld qualifyingTime=%s",event,DirectionName(dir),reason,qualifiedRangeLow,qualifiedRangeHigh,impulseOriginBar,impulseOrigin,impulseEndpointBar,setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0,RequiredTouchRetracement(),qualifyingBar,when);EmitDiagnostic(text);
}
void BeginQualification(const long firstSeed)
{
  qualifiedFirstSeedBar=firstSeed;
  if(StructureMode()){structureCutoff=impulseOriginTime;CaptureStructureContext();}
  int originShift=ClosedBarShift(impulseOriginBar);
  // Full pre-origin window only; origin and every later candle are excluded.
  if(!qualifiedConfigValid||!_finite(setupATR)||setupATR<=0||originShift<1||
     QualifiedImpulseRangeLookbackBars<1||(unsigned long)originShift>=Bars()||
     (unsigned long)QualifiedImpulseRangeLookbackBars>=Bars()-(unsigned long)originShift)
  {LogQualification("pending","full pre-origin window or valid ATR unavailable");return;}
  for(int i=1;i<=QualifiedImpulseRangeLookbackBars;i++)
  {
    int shift=originShift+i;double h=High(shift),l=Low(shift);
    if(!_finite(h)||!_finite(l)||h<=0||l<=0||h<l||Time(shift)<=0){LogQualification("pending","invalid pre-origin history");return;}
    if(i==1){qualifiedRangeHigh=h;qualifiedRangeLow=l;}else{qualifiedRangeHigh=Max(qualifiedRangeHigh,h);qualifiedRangeLow=Min(qualifiedRangeLow,l);}
  }
  qualifiedRangeReady=true;LogQualification("pending","frozen full pre-origin range; awaiting completed close and displacement");
}
bool TryQualifyImpulse(const long bar)
{
  if(impulseQualified)return true;
  if(!qualifiedConfigValid||!qualifiedRangeReady||bar<qualifiedFirstSeedBar||bar-qualifiedFirstSeedBar>ImpulseMaximumPullbackBars)return false;
  double c=ClosedClose(bar),buffer=QualifiedImpulseBreakoutBufferATR*setupATR;
  if(StructureMode())
  {
    const char *reason="";
    bool quality=MeasureStructureQuality(bar,reason);
    if(!CaptureStructureContext())return false;
    if(!quality)return StructureGatePending(reason);
    if(!(dir==1?c>qualifiedRangeHigh+buffer:c<qualifiedRangeLow-buffer))return StructureGatePending("completed close has not broken frozen M5 range");
    if(ImpulseDisplacement()<QualifiedImpulseMinimumDisplacementATR*setupATR)return StructureGatePending("accepted extreme displacement below minimum ATR");
    if(!(dir==1?c>structureBarrier+buffer:c<structureBarrier-buffer))return StructureGatePending("completed close has not broken frozen M15 structure");
  }
  if(!_finite(c)||!(dir==1?c>qualifiedRangeHigh+buffer:c<qualifiedRangeLow-buffer)||
     ImpulseDisplacement()<QualifiedImpulseMinimumDisplacementATR*setupATR)return false;
  impulseQualified=true;qualifyingBar=bar;qualifyingTime=ClosedTime(bar);
  LogQualification("qualified","completed close breakout and accepted displacement; live eligibility never backdated");return true;
}
void AbandonQualification(const char *reason)
{
  LogQualification(StructureMode()?"abandon":"skip",reason);ResetTouch();ResetQualification();ResetProvisionalCandidates();
  // Weak, never-qualified candidates do not create a consumed-parent boundary.
  stage=consumedDirection!=0?IMPULSE_CONSUMED:0;activeImpulseSetup=stage==IMPULSE_CONSUMED;pullbackStarted=false;
}
double PlannedFirstTouch()
{
  // Outward rounding requires at least the configured ATR retracement.
  double raw=setupExtreme-dir*RequiredTouchRetracement();
  return dir==1?Down(raw):Up(raw);
}
bool TouchReached(const double quote,const double level){return dir==1?quote<=level:quote>=level;}
void ObserveTouchPath()
{
  TDateTime barTime=Time(0);double bid=Bid(),ask=Ask();
  if(liveTouchPath.time!=barTime)
  {
    closedTouchPath=liveTouchPath;
    if(closedTouchPath.time!=0){touchPaths[touchPathNext]=closedTouchPath;touchPathNext=(touchPathNext+1)%64;}
    liveTouchPath.time=barTime;liveTouchPath.complete=closedTouchPath.time!=0;
    liveTouchPath.highBid=liveTouchPath.lowBid=bid;
    liveTouchPath.minAskAfterHigh=liveTouchPath.minAsk=ask;liveTouchPath.maxBidAfterLow=bid;
    return;
  }
  liveTouchPath.minAsk=Min(liveTouchPath.minAsk,ask);
  if(bid>liveTouchPath.highBid){liveTouchPath.highBid=bid;liveTouchPath.minAskAfterHigh=ask;}
  else liveTouchPath.minAskAfterHigh=Min(liveTouchPath.minAskAfterHigh,ask);
  if(bid<liveTouchPath.lowBid){liveTouchPath.lowBid=bid;liveTouchPath.maxBidAfterLow=bid;}
  else liveTouchPath.maxBidAfterLow=Max(liveTouchPath.maxBidAfterLow,bid);
}
const TouchBarPath *ObservedTouchBar(const TDateTime time)
{
  for(int i=0;i<64;i++)if(touchPaths[i].complete&&touchPaths[i].time==time)return &touchPaths[i];
  return NULL;
}
bool EndpointTouchPassed()
{
  if(QualifiedMode()&&impulseQualified)
  {
    for(long bar=impulseEndpointBar+1;bar<=qualifyingBar;bar++)
    {
      const TouchBarPath *prior=ObservedTouchBar(ClosedTime(bar));
      if(TouchReached(dir==1?(prior?prior->minAsk:ClosedLow(bar)):ClosedHigh(bar),PlannedFirstTouch()))return true;
    }
  }
  const TouchBarPath *path=ObservedTouchBar(impulseEndpointTime);
  if(path&&fabs((dir==1?path->highBid:path->lowBid)-setupExtreme)<Point()*0.5)
    return TouchReached(dir==1?path->minAskAfterHigh:path->maxBidAfterLow,PlannedFirstTouch());
  // A partial/missing path cannot establish the ordering within the endpoint bar.
  int shift=ClosedBarShift(impulseEndpointBar);
  return TouchReached(dir==1?Low(shift):High(shift),PlannedFirstTouch());
}
void ArmFirstTouch(const char *action)
{
  if(QualifiedMode()&&(!qualifiedConfigValid||!impulseQualified))return;
  if(QualifiedMode())LogQualification(action,"prospective level activation");
  touchLevel=PlannedFirstTouch();touchActivationTime=TimeCurrent();touchActivationTick=tickSequence;
  char when[32],endpoint[32],text[512],label[80];FormatTickTime(touchActivationTime,when);FormatTime(impulseEndpointTime,endpoint);
  sprintf(text,"RBP touch %s dir=%s parentBar=%ld origin=%.5f endpoint=%s@%.5f frozenATR=%.5f planned=%.5f activation=%s tick=%llu rounding=long-down/short-up Point=%.8f",action,DirectionName(dir),breakout,impulseOrigin,endpoint,setupExtreme,setupATR,touchLevel,when,tickSequence,Point());EmitDiagnostic(text);
  sprintf(label,"Touch %s%s %.5f",QualifiedMode()?"QFP ":"",DirectionName(dir),touchLevel);
  DiagnosticMarker("TOUCH",touchActivationTime,touchLevel,label,clYellow);
  DiagnosticMarker("START",impulseOriginTime,impulseOrigin,"I-start",clBlue);DiagnosticMarker("END",impulseEndpointTime,setupExtreme,"I-end",clBlue);
  double quote=dir==1?Ask():Bid();
  if(TouchReached(quote,touchLevel)){ConsumeImpulse("touch already passed at activation; no arming-tick fill");return;}
  if(EndpointTouchPassed()){ConsumeImpulse("touch already passed after endpoint before activation (missing tick order is conservative)");return;}
  touchActive=true;
}
void StartTouchPullback(const double bid,const long bar,const TDateTime when)
{
  if(pullbackStarted)return;
  stage=IMPULSE_PULLBACK;pullbackStarted=true;pullbackStartBar=bar;pullbackStartTime=when;
  pullbackExtreme=pullbackStartPrice=bid;opposing=0;frozenExtension=setupExtreme;hasFrozenExtension=true;
  char text[256];sprintf(text,"RBP PB1 start dir=%s mode=first-touch-ATR bar=%ld quote=%.5f reason=first executable adverse tick; opposing/resumption ignored",DirectionName(dir),bar,bid);EmitDiagnostic(text);
  DiagnosticMarker("PB1",when,bid,"PB1",clYellow);
}
void ProcessFirstTouchTick(const bool historyReady)
{
  if(!FirstTouchMode()||!touchActive||tickSequence<=touchActivationTick||(QualifiedMode()&&(!qualifiedConfigValid||!impulseQualified)))return;
  double bid=Bid(),ask=Ask(),quote=dir==1?ask:bid;
  if((dir==1?quote<setupExtreme:quote>setupExtreme))StartTouchPullback(bid,nbar,TimeCurrent());
  if(pullbackStarted)pullbackExtreme=dir==1?Min(pullbackExtreme,bid):Max(pullbackExtreme,bid);
  if(ImpulseInvalidated(bid)){ConsumeImpulse("first-touch invalidation beyond origin tolerance");return;}
  if(pullbackStarted&&nbar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("first-touch pullback expired");return;}
  if(pullbackStarted&&ImpulseDepthExceeded(PullbackDepth())){ConsumeImpulse("first-touch maximum pullback depth exceeded");return;}
  if(!TouchReached(quote,touchLevel))return;
  // Retire before any callback/order. Every gate is evaluated on the touch tick.
  touchActive=false;
  if(FXBlocked(TimeCurrent())){ConsumeImpulse("weekend blackout at first touch; not queued");return;}
  if(!historyReady){ConsumeImpulse("history not ready at first touch; not queued");return;}
  if((dir==1&&TradeDirection==2)||(dir==-1&&TradeDirection==1)){ConsumeImpulse("direction blocked at first touch; structure-only opportunity consumed, not queued");return;}
  if(StrategyPositionOpen()){ConsumeImpulse("position busy at first touch; not queued");return;}
  if(!ImpulseDepthQualified(PullbackDepth())){ConsumeImpulse("configured depth not qualified at first touch; not queued");return;}
  double mult=0;if(!RiskReady(mult)||mult<=0){ConsumeImpulse("ATR/risk not ready at first touch; not queued");return;}
  bool filter=ImpulseM15H4TrendFilter!=0&&(TrendFilterScope==0||(TrendFilterScope==1&&dir==1)||(TrendFilterScope==2&&dir==-1));
  bool ready=true;if(filter&&!ImpulseTrendConsensus(dir,ready)){ConsumeImpulse(ready?"EMA trend filter not aligned at first touch":"EMA trend filter not ready at first touch");return;}
  int trend=StructuralTrend(),relation=trend==0?0:(trend==dir?1:-1);
  char text[512];sprintf(text,"RBP resumption=first-touch-ATR dir=%s structure=%s class=%s pivots H1=%.5f H2=%.5f L1=%.5f L2=%.5f",DirectionName(dir),TrendName(trend),relation==1?"with-trend":relation==-1?"counter-trend":"neutral",previousSwingHigh,latestSwingHigh,previousSwingLow,latestSwingLow);EmitDiagnostic(text);
  bool entered=Enter(dir,mult,true,trend,true);
  if(entered)DiagnosticMarker("ENTRY",TimeCurrent(),quote,relation==1?"WT":relation==-1?"CT":"N",relation==-1?clRed:relation==1?clGreen:clYellow);
  ConsumeImpulse(entered?"entered first pullback at first touch":"first-touch order failure; opportunity retired without retry");
}
void RecoverFirstTouchChronologically(const long firstBar)
{
  // A level never existed live during these bars. Check the PREVIOUS endpoint's
  // level before accepting a newer endpoint, so a recovery cannot erase PB1.
  if(QualifiedMode())
  {
    if(QualificationExpired(firstBar)){AbandonQualification("pending recovery origin qualification deadline exceeded");return;}
    TryQualifyImpulse(firstBar);
  }
  if((!QualifiedMode()||impulseQualified)&&EndpointTouchPassed()){ConsumeImpulse("touch already passed after recovered seed endpoint; no retrospective entry");return;}
  long replayStart=QualifiedMode()?firstBar+1:impulseEndpointBar+1;
  for(long bar=replayStart;bar<=nbar;bar++)
  {
    double level=PlannedFirstTouch(),h=ClosedHigh(bar),l=ClosedLow(bar),c=ClosedClose(bar);
    if(QualifiedMode()&&!impulseQualified)
    {
      if(QualificationExpired(bar)||ImpulseInvalidated(c)){AbandonQualification("pending recovery expired or invalidated");return;}
      if(dir==1?h>setupExtreme:l<setupExtreme){setupExtreme=dir==1?h:l;impulseEndpointBar=bar;impulseEndpointTime=ClosedTime(bar);extension=bar;}
      if(TryQualifyImpulse(bar)&&EndpointTouchPassed()){ConsumeImpulse("touch already passed at historical qualification; no retrospective entry");return;}
      continue;
    }
    if(ImpulseInvalidated(c)){ConsumeImpulse("first-touch recovered parent invalidated");return;}
    const TouchBarPath *path=ObservedTouchBar(ClosedTime(bar));
    double adverse=dir==1?(path?path->minAsk:l):h;
    if(TouchReached(adverse,level))
    {ConsumeImpulse("touch already passed during chronological recovery (OHLC order/spread conservative); no retrospective entry");return;}
    if(dir==1?h>setupExtreme:l<setupExtreme)
    {
      setupExtreme=dir==1?h:l;impulseEndpointBar=bar;impulseEndpointTime=ClosedTime(bar);extension=bar;ClearUnqualifiedPullback();LogRecoveredEndpoint(bar);
      if(EndpointTouchPassed())
      {ConsumeImpulse("touch already passed or endpoint/touch order unknown during recovery refresh");return;}
    }
    else if(dir==1?l<setupExtreme:h>setupExtreme)
    {
      StartTouchPullback(dir==1?l:h,bar,ClosedTime(bar));
      pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);
      if(bar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("first-touch recovered pullback expired");return;}
      if(ImpulseDepthExceeded(PullbackDepth())){ConsumeImpulse("first-touch recovered maximum depth exceeded");return;}
    }
  }
  ArmFirstTouch("armed after chronological recovery");
}
void ProcessFirstTouchBar(const double h,const double l,const double c)
{
  if(QualifiedMode()&&!impulseQualified)
  {
    if(QualificationExpired(nbar)||ImpulseInvalidated(c)){AbandonQualification("pending expired or invalidated");return;}
    if(dir==1?h>setupExtreme:l<setupExtreme){setupExtreme=dir==1?h:l;impulseEndpointBar=nbar;impulseEndpointTime=Time(1);extension=nbar;}
    if(TryQualifyImpulse(nbar))ArmFirstTouch("armed");
    return;
  }
  if(!touchActive)return;
  if(ImpulseInvalidated(c)){ConsumeImpulse("first-touch closed-bar invalidation");return;}
  if(pullbackStarted&&nbar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("first-touch pullback expired");return;}
  if(dir==1?h>setupExtreme:l<setupExtreme)
  {
    setupExtreme=dir==1?h:l;impulseEndpointBar=nbar;impulseEndpointTime=Time(1);extension=nbar;
    ClearUnqualifiedPullback();ArmFirstTouch("refreshed");
  }
}
int RetireCandidateBeforeCurrentSeed(const int direction,const bool originAvailable,const long originBar)
{
  ProvisionalImpulseCandidate &candidate=CandidateFor(direction);if(!candidate.active)return 0;
  if(nbar-candidate.firstBar>ImpulseMaximumPullbackBars)
  {
    LogPromotionCurrent(candidate,"retired","origin recovery window expired");
    char text[384];sprintf(text,"RBP candidate retired dir=%s reason=recovery window expired oldFirstBar=%ld currentBar=%ld",DirectionName(direction),candidate.firstBar,nbar);EmitDiagnostic(text);ResetCandidate(candidate);return 1;
  }
  if(originAvailable&&originBar>candidate.firstBar)
  {
    LogPromotionCurrent(candidate,"retired","newer confirmed origin structurally incompatible");
    char text[384];sprintf(text,"RBP candidate retired dir=%s reason=newer confirmed origin structurally incompatible oldFirstBar=%ld newOriginBar=%ld currentBar=%ld",DirectionName(direction),candidate.firstBar,originBar,nbar);EmitDiagnostic(text);ResetCandidate(candidate);return 2;
  }
  return 0;
}
void ProcessImpulseSetup(const double o,const double h,const double l,const double c,const double previousClose)
{
  bool busy=StrategyPositionOpen();int candleDirection=c>o?1:c<o?-1:0;double bodyATR=0,rangeATR=0;
  int seedClass=candleDirection==0?0:SeedClassification(candleDirection,o,h,l,c,bodyATR,rangeATR);
  if(QualifiedMode()&&activeImpulseSetup&&!impulseQualified&&stage!=IMPULSE_CONSUMED)
  {
    double origin=0;long bar=-1;TDateTime time=0;
    bool newer=candleDirection!=0&&seedClass==1&&LatestImpulseOrigin(candleDirection,origin,bar,time)&&ValidImpulseGeometry(candleDirection,origin,candleDirection==1?h:l)&&bar<nbar&&bar>qualifiedFirstSeedBar;
    if(QualificationExpired(nbar)||ImpulseInvalidated(c)||newer)
      AbandonQualification(newer?"new confirmed origin replaces incompatible pending parent":"pending expired or invalidated before current seed");
  }
  if((stage==IMPULSE_PARENT||stage==IMPULSE_PULLBACK||stage==IMPULSE_ARMED)&&seedClass!=0&&lastContainedDecisionBar!=nbar)
  {
    char text[512];bool sameDirection=candleDirection==dir;
    sprintf(text,"RBP parent/child decision parent=%s candidate=%s decision=%s body=%.2fATR range=%.2fATR reason=%s",DirectionName(dir),DirectionName(candleDirection),sameDirection?"retain parent":"reject replacement",bodyATR,rangeATR,sameDirection?"same-direction expansion is contained while parent PB1 is unresolved":"opposite expansion cannot replace unresolved parent");EmitDiagnostic(text);lastContainedDecisionBar=nbar;
  }
  if(stage==0||stage==IMPULSE_CONSUMED)
  {
    // TradeDirection restricts orders, not observation. Opposite structural
    // cycles must retire normally so a consumed boundary cannot lock one side.
    if(candleDirection!=0&&seedClass!=0)
    {
      double seedExtreme=candleDirection==1?h:l,origin=0;long originBar=-1;TDateTime originTime=0;const char *reason="";
      double latestOrigin=0;long latestOriginBar=-1;TDateTime latestOriginTime=0;bool originAvailable=LatestImpulseOrigin(candleDirection,latestOrigin,latestOriginBar,latestOriginTime);
      int retirement=RetireCandidateBeforeCurrentSeed(candleDirection,originAvailable,latestOriginBar);
      bool canSeed=CanSeedImpulse(candleDirection,seedExtreme,origin,originBar,originTime,reason);
      if(seedClass==1&&canSeed&&!CandidateFor(candleDirection).active)
      {
        LogSeedDecision(candleDirection,"accepted",reason,bodyATR,rangeATR,busy);
        if(retirement!=0){char text[384];sprintf(text,"RBP current normal seed preserved after retirement dir=%s currentBar=%ld decision=fresh parent accepted",DirectionName(candleDirection),nbar);EmitDiagnostic(text);}
        if(busy){char text[256];sprintf(text,"RBP candidate observed while position open dir=%s bar=%ld; parent tracking active, entry remains blocked",DirectionName(candleDirection),nbar);EmitDiagnostic(text);}
        if(StartImpulse(candleDirection,seedExtreme,origin,originBar,originTime,reason))return;
      }
      ObserveProvisional(candleDirection,seedExtreme,seedClass,bodyATR,rangeATR,busy,canSeed?"normal seed retained for cumulative parent selection":reason);
      if(retirement!=0&&seedClass==2)
      {
        ProvisionalImpulseCandidate &restarted=CandidateFor(candleDirection);char text[512];
        sprintf(text,"RBP current oversized seed restarted provisionally after retirement dir=%s currentBar=%ld firstBar=%ld normalSeedSeen=%s continuationSeen=%s decision=not a normal seed",DirectionName(candleDirection),nbar,restarted.firstBar,restarted.normalSeedSeen?"yes":"no",restarted.continuationSeen?"yes":"no");EmitDiagnostic(text);
      }
    }
    else if(candleDirection!=0&&(bodyATR>=ImpulseMinimumBodyATR||rangeATR>=ImpulseMinimumRangeATR))
    {
      LogSeedDecision(candleDirection,"rejected",bodyATR<ImpulseMinimumBodyATR?"body below minimum ATR":"range below minimum ATR",bodyATR,rangeATR,busy);
    }
    if(candleDirection!=0&&seedClass==0)ObserveProvisionalContinuation(candleDirection,candleDirection==1?h:l,busy);
    if(TryPromoteCandidate(candleDirection==-1?provisionalShort:provisionalLong))
    {
      if(seedClass!=0&&stage==IMPULSE_CONSUMED)EmitDiagnostic("RBP current seed disposition after compatible replay=not reprocessed reason=no distinct newer confirmed origin; duplicate processing forbidden");return;
    }
    if(TryPromoteCandidate(candleDirection==-1?provisionalLong:provisionalShort))
    {
      if(seedClass!=0&&stage==IMPULSE_CONSUMED)EmitDiagnostic("RBP current seed disposition after compatible replay=not reprocessed reason=other-direction replay consumed; duplicate processing forbidden");return;
    }
    return;
  }
  if(FirstTouchMode()){ProcessFirstTouchBar(h,l,c);return;}
  if(stage==IMPULSE_PARENT||stage==IMPULSE_PULLBACK)
  {
    if(ImpulseInvalidated(c)){ConsumeImpulse("invalidation beyond origin tolerance");return;}
    bool newExtreme=dir==1?h>setupExtreme:l<setupExtreme;
    if(newExtreme)
    {
      setupExtreme=dir==1?h:l;impulseEndpointBar=nbar;impulseEndpointTime=Time(1);extension=nbar;
      if(seedClass!=0&&candleDirection==dir)
      {
        char text[384];sprintf(text,"RBP parent extended dir=%s endpoint=%.5f displacement=%.5f/%.2fATR reason=same-direction seed strengthens unresolved parent",DirectionName(dir),setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0);EmitDiagnostic(text);
      }
      if(stage==IMPULSE_PULLBACK){LogPullbackReset();ClearUnqualifiedPullback();}
      return;
    }
    bool opposingClose=dir==1?c<previousClose:c>previousClose;
    if(stage==IMPULSE_PARENT)
    {
      if(opposingClose) StartFirstPullback(h,l);
    }
    else
    {
      pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);
      if(opposingClose) opposing++;
    }
    if(stage!=IMPULSE_PULLBACK) return;
    double retracement=PullbackRetracement(),depth=PullbackDepth();
    if(nbar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("first pullback expired");return;}
    if(ImpulseDepthExceeded(depth)){ConsumeImpulse("maximum pullback depth exceeded");return;}
    if(opposing>=MinimumOpposingCloses&&retracement>=setupATR*ActiveMinimumRetracement()&&ImpulseDepthQualified(depth))
    {
      stage=IMPULSE_ARMED;pullbackConfirmed=nbar;
      char originText[32],endpointText[32],startText[32],text[512];FormatTime(impulseOriginTime,originText);FormatTime(impulseEndpointTime,endpointText);FormatTime(pullbackStartTime,startText);
      sprintf(text,"RBP PB1 qualified dir=%s origin=%s@%.5f endpoint=%s@%.5f displacement=%.5f/%.2fATR start=%s opposing=%d retracement=%.2fATR depth=%.1f%%",DirectionName(dir),originText,impulseOrigin,endpointText,setupExtreme,ImpulseDisplacement(),setupATR>0?ImpulseDisplacement()/setupATR:0,startText,opposing,setupATR>0?retracement/setupATR:0,depth);
      EmitDiagnostic(text);
      DiagnosticMarker("START",impulseOriginTime,impulseOrigin,"I-start",clBlue);DiagnosticMarker("END",impulseEndpointTime,setupExtreme,"I-end",clBlue);DiagnosticMarker("PB1",pullbackStartTime,pullbackStartPrice,"PB1",clYellow);
    }
    else if(opposingClose)
    {
      const char *reason=opposing<MinimumOpposingCloses?"insufficient opposing closes":retracement<setupATR*ActiveMinimumRetracement()?"minimum ATR retracement not reached":"configured minimum depth not reached";
      char text[384];sprintf(text,"RBP PB1 pending dir=%s reason=%s opposing=%d retracement=%.2fATR depth=%.1f%%",DirectionName(dir),reason,opposing,setupATR>0?retracement/setupATR:0,depth);EmitDiagnostic(text);
    }
    return;
  }
  if(stage!=IMPULSE_ARMED) return;
  pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);
  if(dir==1&&haveLatestSwingHigh&&latestSwingHighBar>=pullbackStartBar){minorSwing=latestSwingHigh;hasMinorSwing=true;}
  if(dir==-1&&haveLatestSwingLow&&latestSwingLowBar>=pullbackStartBar){minorSwing=latestSwingLow;hasMinorSwing=true;}
  double depth=PullbackDepth();
  if(ImpulseInvalidated(c)){ConsumeImpulse("invalidation beyond origin tolerance");return;}
  if(nbar-pullbackStartBar>ActiveMaximumPullbackBars()){ConsumeImpulse("first pullback expired");return;}
  if(ImpulseDepthExceeded(depth)){ConsumeImpulse("maximum pullback depth exceeded");return;}
  bool resumed=Confirmation==0?(dir==1?c>previousClose:c<previousClose):Confirmation==1?(dir==1?c>o&&c>High(2):c<o&&c<Low(2)):(hasMinorSwing&&(dir==1?c>minorSwing:c<minorSwing));
  double mult=0;bool riskReady=RiskReady(mult);
  if(nbar<=pullbackConfirmed||!resumed) return;
  if((dir==1&&TradeDirection==2)||(dir==-1&&TradeDirection==1)){ConsumeImpulse("direction blocked at PB1 resumption; structure-only opportunity consumed, not queued");return;}
  if(busy){ConsumeImpulse("position busy at PB1 resumption; opportunity unavailable and not queued");return;}
  if(!riskReady||mult<=0) return;
  int entryDirection=dir,structuralTrend=StructuralTrend();bool trendReady=true,trendAligned=true;
  bool filterThisDirection=ImpulseM15H4TrendFilter!=0&&(TrendFilterScope==0||(TrendFilterScope==1&&entryDirection==1)||(TrendFilterScope==2&&entryDirection==-1));
  if(filterThisDirection)trendAligned=ImpulseTrendConsensus(entryDirection,trendReady);
  if(filterThisDirection&&(!trendReady||!trendAligned)){ConsumeImpulse(!trendReady?"EMA trend filter not ready":"EMA trend filter not aligned");return;}
  ClampTargetSettings();int relation=structuralTrend==0?0:((entryDirection==1&&structuralTrend==1)||(entryDirection==-1&&structuralTrend==-1)?1:-1);double selectedTargetR=relation==-1?CounterTrendTargetR:WithTrendNeutralTargetR;
  char previousHighText[32],latestHighText[32],previousLowText[32],latestLowText[32],text[512];
  FormatTime(previousSwingHighTime,previousHighText);FormatTime(latestSwingHighTime,latestHighText);FormatTime(previousSwingLowTime,previousLowText);FormatTime(latestSwingLowTime,latestLowText);
  sprintf(text,"RBP resumption=%s dir=%s structure=%s class=%s target=%.1fR pivots H1=%s@%.5f H2=%s@%.5f L1=%s@%.5f L2=%s@%.5f",Confirmation==0?"aggressive":Confirmation==1?"balanced":"conservative",DirectionName(entryDirection),TrendName(structuralTrend),relation==1?"with-trend":relation==-1?"counter-trend":"neutral",selectedTargetR,previousHighText,previousSwingHigh,latestHighText,latestSwingHigh,previousLowText,previousSwingLow,latestLowText,latestSwingLow);
  EmitDiagnostic(text);DiagnosticMarker("ENTRY",Time(1),c,relation==1?"WT":relation==-1?"CT":"N",relation==-1?clRed:relation==1?clGreen:clYellow);Enter(entryDirection,mult,true,structuralTrend);ConsumeImpulse("entered first pullback");
}
void ProcessBar()
{
  nbar++; AddATR();
  if(atrCount>=ATRLength)UpdateStructuralPivots();
  // The opening quote belongs to the OLD active level. Evaluate it after the
  // closed-bar risk/structure update, before that bar can refresh the endpoint.
  ProcessFirstTouchTick(true);
  if(atrCount<ATRLength)return;
  if(FXBlocked(Time(0)))
  {
    Flatten();
    if(FirstTouchMode()&&activeImpulseSetup){if(stage!=IMPULSE_CONSUMED){if(QualifiedMode()&&!impulseQualified)AbandonQualification("weekend blackout during pending qualification");else ConsumeImpulse("weekend blackout; first-touch parent retired");}ResetProvisionalCandidates();}
    else ResetSetup();
    ClearClusters();Status(true);return;
  }
  AddClusters(); double o=Open(1),h=High(1),l=Low(1),c=Close(1),pc=Close(2);
  if(stage==0&&SetupModeSetting==0&&!StrategyPositionOpen()&&RangeReady()){bool lng=TradeDirection!=2&&(MinimumBreakoutBodyATR==0||c-o>=rmaATR*MinimumBreakoutBodyATR)&&c>resistance+rmaATR*BreakoutBufferATR;bool sht=TradeDirection!=1&&(MinimumBreakoutBodyATR==0||o-c>=rmaATR*MinimumBreakoutBodyATR)&&c<support-rmaATR*BreakoutBufferATR;if(lng||sht){dir=lng?1:-1;stage=1;frozenR=resistance;frozenS=support;setupATR=rmaATR;setupExtreme=dir==1?h:l;breakout=nbar;ClearClusters();}}
  if(stage==1&&nbar>breakout){bool bad=dir==1?c<frozenR-setupATR*InvalidationToleranceATR:c>frozenS+setupATR*InvalidationToleranceATR;if(bad||nbar-breakout>MaximumExtensionBars){EmitStatus(bad?(PChar)"RBP: setup invalidated":(PChar)"RBP: extension expired");ResetSetup();return;}if(dir==1){setupExtreme=Max(setupExtreme,h);if(setupExtreme>=frozenR+setupATR*MinimumExtensionATR){stage=2;extension=nbar;}}else{setupExtreme=Min(setupExtreme,l);if(setupExtreme<=frozenS-setupATR*MinimumExtensionATR){stage=2;extension=nbar;}}}
  if(stage==2&&nbar>extension){bool bad=dir==1?c<frozenR-setupATR*InvalidationToleranceATR:c>frozenS+setupATR*InvalidationToleranceATR;if(!pullbackStarted){bool newx=dir==1?h>setupExtreme:l<setupExtreme;setupExtreme=dir==1?Max(setupExtreme,h):Min(setupExtreme,l);bool opp=dir==1?c<pc:c>pc;if(opp){pullbackStarted=true;hasFrozenExtension=true;frozenExtension=setupExtreme;pullbackExtreme=dir==1?l:h;opposing=1;extension=nbar;}else if(newx)extension=nbar;}else{pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);if(dir==1?c<pc:c>pc)opposing++;}if(bad||nbar-extension>ActiveMaximumPullbackBars()){EmitStatus(bad?(PChar)"RBP: setup invalidated":(PChar)"RBP: pullback expired");ResetSetup();return;}if(pullbackStarted){double ext=dir==1?frozenExtension-frozenR:frozenS-frozenExtension,ret=dir==1?frozenExtension-pullbackExtreme:pullbackExtreme-frozenExtension,depth=ext>0?ret/ext*100:0,lo=0,hi=0;bool maxDepth=PullbackDepthMode!=0;if(PullbackDepthMode==1){lo=Min(ShallowMinimumDepth,ShallowMaximumDepth);hi=Max(ShallowMinimumDepth,ShallowMaximumDepth);}if(PullbackDepthMode==2){lo=Min(DeepMinimumDepth,DeepMaximumDepth);hi=Max(DeepMinimumDepth,DeepMaximumDepth);}if(PullbackDepthMode==3){lo=Min(CustomMinimumDepth,CustomMaximumDepth);hi=Max(CustomMinimumDepth,CustomMaximumDepth);}if(DepthExceeded(depth,maxDepth,hi)){EmitStatus((PChar)"RBP: maximum pullback depth exceeded");ResetSetup();return;}if(opposing>=MinimumOpposingCloses&&ret>=setupATR*ActiveMinimumRetracement()&&depth>0&&(activeImpulseSetup||!maxDepth||depth>=lo)){stage=3;pullbackConfirmed=nbar;}}}
  if(stage==3){pullbackExtreme=dir==1?Min(pullbackExtreme,l):Max(pullbackExtreme,h);double p;if(dir==1&&IsPivotHigh(MinorSwingStrength,p)&&nbar-MinorSwingStrength>=extension){minorSwing=p;hasMinorSwing=true;}if(dir==-1&&IsPivotLow(MinorSwingStrength,p)&&nbar-MinorSwingStrength>=extension){minorSwing=p;hasMinorSwing=true;}bool bad=dir==1?c<frozenR-setupATR*InvalidationToleranceATR:c>frozenS+setupATR*InvalidationToleranceATR;double ext=dir==1?frozenExtension-frozenR:frozenS-frozenExtension,depth=ext>0?(dir==1?frozenExtension-pullbackExtreme:pullbackExtreme-frozenExtension)/ext*100:0,hi=0;bool maxDepth=PullbackDepthMode!=0;if(PullbackDepthMode==1)hi=Max(ShallowMinimumDepth,ShallowMaximumDepth);if(PullbackDepthMode==2)hi=Max(DeepMinimumDepth,DeepMaximumDepth);if(PullbackDepthMode==3)hi=Max(CustomMinimumDepth,CustomMaximumDepth);if(bad||nbar-extension>ActiveMaximumPullbackBars()||DepthExceeded(depth,maxDepth,hi)){EmitStatus(bad?(PChar)"RBP: setup invalidated":nbar-extension>ActiveMaximumPullbackBars()?(PChar)"RBP: pullback expired":(PChar)"RBP: maximum pullback depth exceeded");ResetSetup();return;}bool resume=Confirmation==0?(dir==1?c>pc:c<pc):Confirmation==1?(dir==1?c>o&&c>High(2):c<o&&c<Low(2)):(hasMinorSwing&&(dir==1?c>minorSwing:c<minorSwing));double mult=0;bool riskReady=RiskReady(mult);if(nbar>pullbackConfirmed&&resume&&!StrategyPositionOpen()&&riskReady&&mult>0){int d=dir;EmitStatus((PChar)"RBP: entry requested");ResetSetup();Enter(d,mult,false,0);}}
  if(SetupModeSetting==1) ProcessImpulseSetup(o,h,l,c,pc);
  Status(false);
}
EXPORT void __stdcall InitStrategy()
{
 runOptionCount=0; runSettingsResolved=false;
 StrategyShortName((PChar)"Range Breakout-Pullback"); StrategyDescription((PChar)"Closed-bar TradingView range breakout-pullback port");
 RegOption((PChar)"Settings preset (0 Custom, 1 Baseline, 2 Trend-filter test, 3 Baseline buys-only, 4 M5 Baseline, 5 M5 first-touch ATR, 6 M5 first-touch ATR buys-only, 7 M5 qualified first pullback buys-only)",ot_Integer,&SettingsPreset);
 AddOptionValue((PChar)"Settings preset (0 Custom, 1 Baseline, 2 Trend-filter test, 3 Baseline buys-only, 4 M5 Baseline, 5 M5 first-touch ATR, 6 M5 first-touch ATR buys-only, 7 M5 qualified first pullback buys-only)",(PChar)"8 = M5 structure-break first pullback both");
 AddSeparator((PChar)"General execution/setup");
 RegOption((PChar)"Currency",ot_Currency,&Currency);RegRunOption((PChar)"Timeframe",ot_TimeFrame,&Timeframe);RegRunOption((PChar)"Trade direction (0 both,1 buys only,2 sells only)",ot_Integer,&TradeDirection);AddOptionValue((PChar)"Trade direction (0 both,1 buys only,2 sells only)",(PChar)"0 = Both directions");AddOptionValue((PChar)"Trade direction (0 both,1 buys only,2 sells only)",(PChar)"1 = Buys only");AddOptionValue((PChar)"Trade direction (0 both,1 buys only,2 sells only)",(PChar)"2 = Sells only");RegRunOption((PChar)"Maximum completed strategy trades (0 unlimited)",ot_Integer,&MaximumCompletedStrategyTrades);RegRunOption((PChar)"Setup mode (0 range, 1 impulse)",ot_Integer,&SetupModeSetting);RegRunOption((PChar)"Volume lots",ot_Double,&VolumeLots);RegRunOption((PChar)"Magic number",ot_Integer,&MagicNumber);
 AddSeparator((PChar)"Confirmation");
 RegRunOption((PChar)"Confirmation (0 aggressive,1 balanced,2 conservative)",ot_Integer,&Confirmation);RegRunOption((PChar)"Minor swing strength",ot_Integer,&MinorSwingStrength);
 AddSeparator((PChar)"Range-mode settings");
 RegRunOption((PChar)"Pivot strength",ot_Integer,&PivotStrength);RegRunOption((PChar)"Minimum reactions",ot_Integer,&MinimumReactions);RegRunOption((PChar)"Cluster tolerance ATR",ot_Double,&ClusterToleranceATR);RegRunOption((PChar)"Minimum range height ATR",ot_Double,&MinimumRangeHeightATR);RegRunOption((PChar)"Minimum range bars",ot_Integer,&MinimumRangeBars);RegRunOption((PChar)"Maximum range bars",ot_Integer,&MaximumRangeBars);RegRunOption((PChar)"Breakout buffer ATR",ot_Double,&BreakoutBufferATR);RegRunOption((PChar)"Minimum breakout body ATR",ot_Double,&MinimumBreakoutBodyATR);RegRunOption((PChar)"Minimum extension ATR",ot_Double,&MinimumExtensionATR);RegRunOption((PChar)"Maximum extension bars",ot_Integer,&MaximumExtensionBars);
 AddSeparator((PChar)"Impulse-detection settings");
 RegRunOption((PChar)"Impulse entry mode (0 confirmation,1 first-touch ATR,2 qualified-first-pullback)",ot_Integer,&ImpulseEntryMode);
 AddOptionValue((PChar)"Impulse entry mode (0 confirmation,1 first-touch ATR,2 qualified-first-pullback)",(PChar)"3 = structure-first-pullback");
 RegRunOption((PChar)"Qualified impulse range lookback bars",ot_Integer,&QualifiedImpulseRangeLookbackBars);
 RegRunOption((PChar)"Qualified impulse minimum displacement ATR",ot_Double,&QualifiedImpulseMinimumDisplacementATR);
 RegRunOption((PChar)"Qualified impulse breakout buffer ATR",ot_Double,&QualifiedImpulseBreakoutBufferATR);
 RegRunOption((PChar)"Qualified pullback minimum depth %",ot_Double,&QualifiedPullbackMinimumDepth);
 RegRunOption((PChar)"Impulse minimum body ATR",ot_Double,&ImpulseMinimumBodyATR);RegRunOption((PChar)"Impulse maximum body ATR (0 disabled)",ot_Double,&ImpulseMaximumBodyATR);RegRunOption((PChar)"Impulse minimum range ATR",ot_Double,&ImpulseMinimumRangeATR);RegRunOption((PChar)"Impulse minimum retracement ATR",ot_Double,&ImpulseMinimumRetracementATR);RegRunOption((PChar)"Impulse maximum depth %",ot_Double,&ImpulseMaximumDepth);RegRunOption((PChar)"Impulse maximum pullback bars",ot_Integer,&ImpulseMaximumPullbackBars);RegRunOption((PChar)"Impulse M15/H4 trend filter (0 disabled)",ot_Integer,&ImpulseM15H4TrendFilter);RegRunOption((PChar)"Trend filter scope (0 both,1 buys only,2 sells only)",ot_Integer,&TrendFilterScope);AddOptionValue((PChar)"Trend filter scope (0 both,1 buys only,2 sells only)",(PChar)"0 = Both directions");AddOptionValue((PChar)"Trend filter scope (0 both,1 buys only,2 sells only)",(PChar)"1 = Buys only");AddOptionValue((PChar)"Trend filter scope (0 both,1 buys only,2 sells only)",(PChar)"2 = Sells only");
 AddSeparator((PChar)"Pullback and depth settings");
 RegRunOption((PChar)"Minimum opposing closes",ot_Integer,&MinimumOpposingCloses);RegRunOption((PChar)"Minimum retracement ATR",ot_Double,&MinimumRetracementATR);RegRunOption((PChar)"Depth mode (0 any,1 shallow,2 deep,3 custom)",ot_Integer,&PullbackDepthMode);RegRunOption((PChar)"Shallow minimum depth",ot_Double,&ShallowMinimumDepth);RegRunOption((PChar)"Shallow maximum depth",ot_Double,&ShallowMaximumDepth);RegRunOption((PChar)"Deep minimum depth",ot_Double,&DeepMinimumDepth);RegRunOption((PChar)"Deep maximum depth",ot_Double,&DeepMaximumDepth);RegRunOption((PChar)"Custom minimum depth",ot_Double,&CustomMinimumDepth);RegRunOption((PChar)"Custom maximum depth",ot_Double,&CustomMaximumDepth);RegRunOption((PChar)"Invalidation tolerance ATR",ot_Double,&InvalidationToleranceATR);RegRunOption((PChar)"Maximum pullback bars",ot_Integer,&MaximumPullbackBars);
 AddSeparator((PChar)"Stop, risk and structural R targets");
 RegRunOption((PChar)"Stop mode (0 adaptive,1 fixed)",ot_Integer,&StopModeSetting);RegRunOption((PChar)"ATR length",ot_Integer,&ATRLength);RegRunOption((PChar)"Fixed ATR multiplier",ot_Double,&FixedATRMultiplier);RegRunOption((PChar)"ATR percentile lookback",ot_Integer,&RegimeLookback);RegRunOption((PChar)"Low regime percentile",ot_Double,&LowRegimePercentile);RegRunOption((PChar)"High regime percentile",ot_Double,&HighRegimePercentile);RegRunOption((PChar)"Low multiplier",ot_Double,&LowVolatilityMultiplier);RegRunOption((PChar)"Normal multiplier",ot_Double,&NormalVolatilityMultiplier);RegRunOption((PChar)"High multiplier",ot_Double,&HighVolatilityMultiplier);RegRunOption((PChar)"With-trend / neutral target R (minimum 2.0)",ot_Double,&WithTrendNeutralTargetR);RegRunOption((PChar)"Counter-trend target R (minimum 3.0)",ot_Double,&CounterTrendTargetR);
 AddSeparator((PChar)"Time and weekend settings");
 RegRunOption((PChar)"Enable weekend blackout",ot_Boolean,&EnableWeekendBlackout);RegRunOption((PChar)"Server UTC offset hours",ot_Integer,&ServerUTCOffsetHours);
 AddSeparator((PChar)"Diagnostics");
 RegRunOption((PChar)"Show blackout status",ot_Boolean,&ShowBlackoutStatus);RegRunOption((PChar)"Show diagnostics",ot_Boolean,&ShowDiagnostics);
 // Append new controls so all existing registration identities/order stay stable.
 RegRunOption((PChar)"Structure context pivot strength",ot_Integer,&StructureContextPivotStrength);
 RegRunOption((PChar)"Structure impulse minimum efficiency",ot_Double,&StructureImpulseMinimumEfficiency);
 RegRunOption((PChar)"Structure impulse maximum bars",ot_Integer,&StructureImpulseMaximumBars);
}
EXPORT void __stdcall DoneStrategy(){free(Currency);free(atrPct);atrPct=NULL;atrPctCapacity=atrPctCount=atrPctNext=0;} EXPORT void __stdcall ResetStrategy(){qualifiedConfigValid=true;runSettingsResolved=false;tradeLimitBlocked=false;tradeLimitNotificationSent=false;tradeHistoryCacheValid=false;cachedCompletedStrategyTrades=0;cachedHistoryTotal=-1;startupContextValidated=false;startupHistoryStateKnown=false;startupHistoryReady=false;startupReadyProgressLogged=false;startupTimeframe=-1;startupCurrency[0]=lastStartupEvent[0]=0;firstProcessedClosedBar=-1;lastProgressBar=0;tickSequence=0;touchPathNext=0;memset(touchPaths,0,sizeof(touchPaths));memset(&liveTouchPath,0,sizeof(liveTouchPath));memset(&closedTouchPath,0,sizeof(closedTouchPath));lastTime=0;nbar=0;rmaATR=0;atrCount=atrPctCount=atrPctNext=0;lastDiagnostic[0]=0;previousSwingHigh=latestSwingHigh=previousSwingLow=latestSwingLow=0;previousSwingHighBar=latestSwingHighBar=previousSwingLowBar=latestSwingLowBar=-1;previousSwingHighTime=latestSwingHighTime=previousSwingLowTime=latestSwingLowTime=0;havePreviousSwingHigh=haveLatestSwingHigh=havePreviousSwingLow=haveLatestSwingLow=false;consumedDirection=0;consumedExtreme=0;consumedBar=-1;ResetSetup();ClearClusters();}
EXPORT void __stdcall GetSingleTick()
{
  ResolveRunSettings();if(!BindRunContext())return;tickSequence++;
  if(!CheckTradeLimit())return;
  unsigned long bars=Bars();
  if(FirstTouchMode()&&bars>0)ObserveTouchPath();
  int required=Max(ATRLength,RegimeLookback)+Max(PivotStrength,MinorSwingStrength)+5;
  bool historyReady=bars>=(unsigned long)required;StartupHistoryStatus(bars,required,historyReady);
  if(!historyReady){ProcessFirstTouchTick(false);if(!CheckTradeLimit())return;return;}
  TDateTime t=Time(0);
  if(t!=lastTime){lastTime=t;ProcessBar();if(firstProcessedClosedBar<0){firstProcessedClosedBar=nbar;char when[32],text[384];FormatTime(Time(1),when);sprintf(text,"RBP startup first-closed-bar processedBars=%ld time=%s availableBars=%lu requiredBars=%d",nbar,when,bars,required);EmitStartupEvent(text);}StartupProgress();}
  else ProcessFirstTouchTick(true);
  if(!CheckTradeLimit())return;
}
