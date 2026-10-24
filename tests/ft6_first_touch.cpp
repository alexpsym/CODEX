// Filtered synthetic FT6 API seam. Exactly one named case per invocation.
#include <string>
#include <vector>
#include "../forex-tester-6/RangeBreakoutPullbackStrategy/RangeBreakoutPullbackStrategy.cpp"
struct Bar {double o,h,l,c,t;};
Bar series[128];
std::vector<std::string> logs;
std::vector<std::string> markerLabels;double markerPrice=0;
double bidQuote,askQuote,now,grid=0.0001,orderQuote,orderSL,orderTP;
int attempts,orderType,selectedFrame,buyAttempts,sellAttempts;unsigned long historyBars=128;
bool busy,orderResult;int orderTicket;
void Check(bool ok,const char *why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);for(auto &s:logs)fprintf(stderr,"%s\n",s.c_str());exit(1);}}
bool Near(double a,double b){return fabs(a-b)<1e-9;}
bool Logged(const char *s){for(auto &v:logs)if(v.find(s)!=std::string::npos)return true;return false;}
void __stdcall Ignore(void*,PChar){}
void __stdcall Register(void*,PChar,int,void*){}
void __stdcall OptionValue(void*,PChar,PChar){}
void __stdcall Capture(void*,PChar s){logs.push_back(s);}
PChar __stdcall Sym(void*){return (PChar)"EURUSD";}
bool __stdcall Frame(void*,PChar,int f){selectedFrame=f;return true;}
unsigned long __stdcall BarsCount(void*){return historyBars;}
double __stdcall O(void*,int s){return series[s].o;}
double __stdcall H(void*,int s){return series[s].h;}
double __stdcall L(void*,int s){return series[s].l;}
double __stdcall C(void*,int s){return series[s].c;}
double __stdcall T(void*,int s){return series[s].t;}
double __stdcall Now(void*){return now;}
double __stdcall BidQuote(void*){return bidQuote;}
double __stdcall AskQuote(void*){return askQuote;}
double __stdcall Grid(void*){return grid;}
int __stdcall Orders(void*){return busy?1:0;}
bool __stdcall Select(void*,int,int,int){return busy;}
int __stdcall Magic(void*){return MagicNumber;}
unsigned long __stdcall IBars(void*,PChar,int){return 0;}
bool __stdcall Exists(void*,PChar){return false;}
bool __stdcall Create(void*,PChar name,int type,int,double,double price,double,double,double,double){Check(type==obj_Text,"supported text marker");if(strstr(name,"TOUCH"))markerPrice=price;return true;}
bool __stdcall Text(void*,PChar,PChar text,int,PChar,int){markerLabels.push_back(text);return true;}
bool __stdcall Send(void*,PChar,int type,double lots,double sl,double tp,PChar,int,int &ticket)
{
  attempts++;if(type==op_Buy)buyAttempts++;else sellAttempts++;orderType=type;orderQuote=type==op_Buy?askQuote:bidQuote;orderSL=sl;orderTP=tp;
  Check(Near(lots,0.01),"fixed lots");ticket=orderTicket;return orderResult;
}
void Hooks()
{
  IntrfProcsRec.RegOption=Register;IntrfProcsRec.AddOptionValue=OptionValue;
  IntrfProcsRec.StrategyShortName=Ignore;IntrfProcsRec.StrategyDescription=Ignore;IntrfProcsRec.Print=Capture;
  IntrfProcsRec.Symbol=Sym;IntrfProcsRec.SetCurrencyAndTimeframe=Frame;IntrfProcsRec.Bars=BarsCount;
  IntrfProcsRec.Open=O;IntrfProcsRec.High=H;IntrfProcsRec.Low=L;IntrfProcsRec.Close=C;IntrfProcsRec.Time=T;
  IntrfProcsRec.TimeCurrent=Now;IntrfProcsRec.pTimeCurrent=(void*)1;IntrfProcsRec.Bid=BidQuote;IntrfProcsRec.Ask=AskQuote;IntrfProcsRec.Point=Grid;
  IntrfProcsRec.OrdersTotal=Orders;IntrfProcsRec.OrderSelect=Select;IntrfProcsRec.OrderMagicNumber=Magic;IntrfProcsRec.pOrderMagicNumber=(void*)1;
  IntrfProcsRec.OrderSymbol=Sym;IntrfProcsRec.iBars=IBars;IntrfProcsRec.SendInstantOrder2=Send;
  IntrfProcsRec.ObjectExists=Exists;IntrfProcsRec.ObjectCreate=Create;IntrfProcsRec.ObjectSetText=Text;IntrfProcsRec.pObjectCreate=(void*)1;IntrfProcsRec.pObjectSetText=(void*)1;
}
void Fresh()
{
  ResetStrategy();SettingsPreset=5;ApplySettingsPreset();runSettingsResolved=true;EnableWeekendBlackout=false;
  now=46000.5;for(int i=0;i<128;i++)series[i]={1.20,1.2002,1.1998,1.20,now-i*5.0/1440};
  lastTime=series[0].t;nbar=10;atrCount=100;rmaATR=0.00206;RegimeLookback=1;AddATRPercent(0.1);
  historyBars=128;bidQuote=1.21;askQuote=1.2102;busy=false;attempts=0;orderResult=true;orderTicket=42;
  logs.clear();markerLabels.clear();markerPrice=0;tickSequence=1;buyAttempts=sellAttempts=0;
}
void Remember(long bar,double endpoint,int direction,double adverse)
{
  TouchBarPath &p=touchPaths[touchPathNext++%64];p.time=ClosedTime(bar);p.complete=true;
  p.highBid=direction==1?endpoint:adverse;p.lowBid=direction==1?adverse:endpoint;
  p.minAskAfterHigh=direction==1?adverse:endpoint+0.0002;p.maxBidAfterLow=direction==1?endpoint:adverse;
  p.minAsk=direction==1?adverse:endpoint+0.0002;
}
void Parent(int d,bool arm=true)
{
  double endpoint=d==1?1.21:1.19,origin=d==1?1.20:1.20;
  bidQuote=d==1?1.2098:1.1902;askQuote=bidQuote+0.0002;
  series[1].h=d==1?endpoint:endpoint+0.002;series[1].l=d==1?endpoint-0.002:endpoint;
  series[1].c=bidQuote;
  Remember(nbar,endpoint,d,d==1?1.2098:1.1902);
  StartImpulseAt(d,endpoint,nbar,Time(1),origin,1,Time(10),rmaATR,Time(1),"synthetic eligible confirmed origin");
  if(arm)ArmFirstTouch("armed");
}
void Quote(double b,double a){bidQuote=b;askQuote=a;now+=1.0/86400;GetSingleTick();}
void Touch(int d,double gap=0){double q=touchLevel-d*gap;Quote(d==1?q-0.0002:q,d==1?q:q+0.0002);}
void Intrabar()
{
  for(int d:{1,-1}){Fresh();Parent(d);Check(touchActive,"parent arms");double level=touchLevel;long bar=nbar;
    Check(Near(level,d==1?1.2089:1.1911),"outward tick grid and frozen ATR level");
    MinimumOpposingCloses=99;Confirmation=2;Touch(d);
    Check(attempts==1&&stage==IMPULSE_CONSUMED&&nbar==bar,"intrabar entry without confirmation waits");
    Check(opposing==0&&pullbackStartBar==bar,"causal adverse-tick PB1 without opposing closes");
    Check(Logged("confirmed entry")&&Logged("rounding=long-down/short-up"),"confirmed diagnostic and rounding policy");
    for(int i=0;i<3;i++)Touch(d);Check(attempts==1,"one opportunity no duplicate orders");}
}
void QuotesGap()
{
  Fresh();Parent(1);double level=touchLevel;
  Quote(level,level+0.0002);Check(attempts==0&&touchActive,"Bid-only buy touch is not executable");
  Touch(1,0.0003);Check(attempts==1&&Near(orderQuote,level-0.0003),"buy actual Ask gap fill");
  Check(Near(orderSL,Down(orderQuote-frozenRiskDistance))&&Near(orderTP,Up(orderQuote+frozenRiskDistance*2)),"brackets from available quote");
  Fresh();Parent(-1);level=touchLevel;Quote(level-0.0002,level);Check(attempts==0,"Ask-only sell touch ignored");
  Touch(-1,0.0003);Check(attempts==1&&Near(orderQuote,level+0.0003),"sell actual Bid gap fill");
}
void ActivationPassed()
{
  for(int d:{1,-1}){
    Fresh();Parent(d,false);double level=PlannedFirstTouch();
    bidQuote=d==1?level-0.0002:level;askQuote=bidQuote+0.0002;ArmFirstTouch("armed");
    Check(attempts==0&&stage==IMPULSE_CONSUMED&&Logged("touch already passed"),"no arming-tick fill");
    Quote(d==1?1.2098:1.1902,d==1?1.2100:1.1904);Touch(d);Check(attempts==0,"no second-touch retry");
    Fresh();Parent(d,false);Remember(nbar,setupExtreme,d,d==1?level-0.0001:level+0.0001);ArmFirstTouch("armed");
    // Replace the first (safe) record too; production has exactly one record/time.
    if(touchActive){touchPaths[0]=touchPaths[1];ArmFirstTouch("armed");}
    Check(stage==IMPULSE_CONSUMED&&attempts==0,"observed touch before recognition consumed");
    Fresh();Parent(d);tickSequence=touchActivationTick;bidQuote=d==1?touchLevel-0.0002:touchLevel;askQuote=bidQuote+0.0002;
    ProcessFirstTouchTick(true);Check(attempts==0&&touchActive,"activation tick guarded explicitly");
    Fresh();Parent(d,false);memset(touchPaths,0,sizeof(touchPaths));ArmFirstTouch("armed");
    Check(stage==IMPULSE_CONSUMED&&Logged("missing tick order"),"unknown endpoint-bar order conservatively skipped");
  }
}
void ObservedActivation()
{
  for(int d:{1,-1})for(int passed:{0,1}){
    Fresh();double base=series[0].t,step=5.0/1440,endpoint=d==1?1.21:1.19;
    series[0].t=base-2*step;bidQuote=1.20;askQuote=1.2002;ObserveTouchPath();
    series[0].t=base-step;ObserveTouchPath(); // complete observation of seed bar starts here
    // The pre-endpoint adverse price must not be treated as a later touch.
    bidQuote=endpoint;askQuote=endpoint+0.0002;ObserveTouchPath();
    double planned=d==1?1.2089:1.1911;
    if(passed){bidQuote=d==1?planned-0.0002:planned;askQuote=bidQuote+0.0002;ObserveTouchPath();}
    bidQuote=d==1?1.2098:1.1902;askQuote=bidQuote+0.0002;ObserveTouchPath();
    // Equal endpoint visits cannot erase an already observed first touch.
    bidQuote=endpoint;askQuote=endpoint+0.0002;ObserveTouchPath();
    series[0].t=base;ObserveTouchPath();
    StartImpulseAt(d,endpoint,nbar,series[1].t,1.20,1,series[10].t,rmaATR,series[1].t,"observed seed");
    ArmFirstTouch("armed");
    Check(attempts==0,"activation never submits an order");
    Check(passed?stage==IMPULSE_CONSUMED:touchActive,"observed endpoint chronology retained");
    Check(Near(markerPrice,planned)&&!markerLabels.empty()&&markerLabels[0].find("Touch ")==0,"planned price has supported chart marker");
    if(!passed){Touch(d);Check(attempts==1,"observed fresh parent enters later");}
  }
}
void Recover()
{
  for(int d:{1,-1})for(int oversized:{0,1}){
    Fresh();Parent(d,false);ResetTouch();stage=0;nbar=12;
    // Candidate began at bar 10, origin was not confirmed until now.
    for(int i=0;i<128;i++){series[i].h=d==1?1.21:1.1912;series[i].l=d==1?1.2088:1.19;series[i].c=d==1?1.2098:1.1902;}
    series[ClosedBarShift(10)].h=d==1?1.21:1.1902;series[ClosedBarShift(10)].l=d==1?1.2098:1.19;
    series[ClosedBarShift(10)].c=d==1?1.2098:1.1902;
    if(d==1){haveLatestSwingLow=true;latestSwingLow=1.20;latestSwingLowBar=9;latestSwingLowTime=ClosedTime(9);series[ClosedBarShift(9)].h=1.20;}
    else{haveLatestSwingHigh=true;latestSwingHigh=1.20;latestSwingHighBar=9;latestSwingHighTime=ClosedTime(9);series[ClosedBarShift(9)].l=1.20;}
    // Evidence and endpoint path are available before promotion, not invented later.
    memset(touchPaths,0,sizeof(touchPaths));touchPathNext=0;Remember(10,d==1?1.21:1.19,d,d==1?1.2098:1.1902);
    auto &candidate=CandidateFor(d);candidate.active=true;candidate.firstBar=10;candidate.extremeBar=10;candidate.firstTime=ClosedTime(10);
    candidate.seedATR=0.00206;candidate.firstExtreme=candidate.extreme=d==1?1.21:1.19;
    candidate.oversizedSeen=oversized!=0;candidate.normalSeedSeen=true;candidate.continuationSeen=oversized!=0;
    Check(TryPromoteCandidate(candidate),"confirmed pivot recovery promoted");
    Check(stage==IMPULSE_CONSUMED&&attempts==0&&Logged("touch already passed"),"recovered normal/oversized parent cannot retrospectively fill");
    Touch(d);Check(attempts==0,"recovered second touch cannot enter");
  }
  Fresh();Parent(1,false);RecoverFirstTouchChronologically(nbar);
  Check(touchActive&&attempts==0,"untouched recovered parent armed prospectively");Touch(1);Check(attempts==1,"future first touch after safe recovery");
}
void RefreshOrdering()
{
  // A new-bar opening quote touches the prior level before closed-bar refresh.
  Fresh();Parent(1);double old=touchLevel;series[1].h=1.212;series[0].t+=5.0/1440;
  bidQuote=old-0.0002;askQuote=old;now=series[0].t;GetSingleTick();
  Check(attempts==1&&Near(orderQuote,old)&&Near(touchLevel,old),"old level receives opening tick before refresh");
  // Accepted closed-bar endpoint activates a new level with the SAME ATR basis.
  Fresh();Parent(1);double atr=setupATR;series[1].h=1.212;Remember(nbar+1,1.212,1,1.2118);
  series[0].t+=5.0/1440;series[1].t=series[0].t-5.0/1440;touchPaths[1].time=series[1].t;
  bidQuote=1.212;askQuote=1.2122;now=series[0].t;GetSingleTick();
  Check(attempts==0&&touchActive&&Near(setupATR,atr)&&Near(touchLevel,1.2109),"prospective refresh frozen ATR");
  Check(touchActivationTick==tickSequence&&Logged("refreshed"),"new level activation tick recorded");Touch(1);Check(attempts==1,"new level trades on later intrabar tick");
  // A quote past the newly calculated level cannot create an arming fill.
  Fresh();Parent(1);series[1].h=1.212;Remember(nbar,1.212,1,1.2118);series[0].t+=5.0/1440;
  bidQuote=1.2101;askQuote=1.2103;now=series[0].t;GetSingleTick();
  Check(attempts==0&&stage==IMPULSE_CONSUMED&&Logged("touch already passed at activation"),"refresh cannot reuse the same opening tick");
}
void Gates()
{
  for(std::string reason:{"busy","blackout","risk","history","direction","depth","expiry","invalid","minimum-depth","trend"}){
    Fresh();Parent(1);
    if(reason=="busy")busy=true;
    if(reason=="blackout"){EnableWeekendBlackout=true;SYSTEMTIME st={0};st.wYear=2026;st.wMonth=10;st.wDay=24;st.wHour=12;SystemTimeToVariantTime(&st,&now);}
    if(reason=="risk")atrCount=0;
    if(reason=="history")historyBars=0;
    if(reason=="direction")TradeDirection=2;
    if(reason=="depth")ShallowMaximumDepth=5;
    if(reason=="expiry"){StartTouchPullback(bidQuote,nbar-31,now);}
    if(reason=="minimum-depth")ShallowMinimumDepth=20;
    if(reason=="trend")ImpulseM15H4TrendFilter=1;
    if(reason=="invalid"){bidQuote=1.19;askQuote=1.1902;GetSingleTick();}else Touch(1);
    Check(stage==IMPULSE_CONSUMED&&attempts==0,("blocked touch consumed: "+reason).c_str());
    const char *expected=reason=="busy"?"position busy":reason=="blackout"?"weekend blackout":reason=="risk"?"ATR/risk not ready":reason=="history"?"history not ready":reason=="direction"?"direction blocked":reason=="depth"?"maximum pullback depth":reason=="expiry"?"expired":reason=="invalid"?"invalidation":reason=="trend"?"EMA trend filter not ready":"configured depth not qualified";
    Check(Logged(expected),"specific consume reason");busy=false;EnableWeekendBlackout=false;atrCount=100;historyBars=128;TradeDirection=0;ImpulseM15H4TrendFilter=0;Touch(1);Check(attempts==0,"blocked opportunity never queued");
  }
}
void OrderFailure()
{
  for(int variant:{0,1,2}){Fresh();Parent(1);orderResult=variant==1;orderTicket=variant==2?42:-1;Touch(1);
    Check(attempts==1&&stage==IMPULSE_CONSUMED&&Logged("touch order failure")&&!Logged("confirmed entry")&&!Logged("entry frozen"),"return result AND ticket required");
    Touch(1);Check(attempts==1,"failed attempt never retried");}
}
void Brackets()
{
  for(int d:{1,-1})for(int trend:{-1,0,1}){Fresh();Parent(d);
    if(trend){havePreviousSwingHigh=haveLatestSwingHigh=havePreviousSwingLow=haveLatestSwingLow=true;previousSwingHigh=1.22;latestSwingHigh=1.22+trend*0.001;previousSwingLow=1.18;latestSwingLow=1.18+trend*0.001;}
    double risk=ceil(rmaATR*HighVolatilityMultiplier/grid)*grid,targetR=trend!=0&&trend!=d?3:2;
    Touch(d);Check(attempts==1&&Near(frozenRiskDistance,risk)&&Near(frozenTargetR,targetR),"adaptive risk and structural 2R/3R");
    Check(Near(orderSL,d==1?Down(orderQuote-risk):Up(orderQuote+risk))&&Near(orderTP,d==1?Up(orderQuote+risk*targetR):Down(orderQuote-risk*targetR)),"actual-entry bracket tick rounding");
    double tp=frozenTargetPrice;rmaATR*=2;latestSwingHigh+=0.1;WithTrendNeutralTargetR=9;Touch(d);
    Check(attempts==1&&Near(frozenRiskDistance,risk)&&Near(frozenTargetR,targetR)&&Near(frozenTargetPrice,tp),"risk target and classification frozen");}
}
void LegacyEntries()
{
  for(int d:{1,-1}){Fresh();ImpulseEntryMode=0;Parent(d,false);StartFirstPullback(d==1?1.21:1.1912,d==1?1.2088:1.19);
    opposing=2;stage=IMPULSE_ARMED;pullbackConfirmed=nbar-1;double c=d==1?1.2092:1.1908,pc=d==1?1.2090:1.1910;
    ProcessImpulseSetup(c,c+0.0001,c-0.0001,c,pc);Check(attempts==1&&stage==IMPULSE_CONSUMED,"legacy aggressive confirmation entry preserved");
    Fresh();ImpulseEntryMode=0;Parent(d,false);opposing=1;stage=IMPULSE_PULLBACK;pullbackStarted=true;pullbackStartBar=nbar;pullbackExtreme=d==1?1.2088:1.1912;
    ProcessImpulseSetup(c,c+0.0001,c-0.0001,c,c);Check(attempts==0&&stage==IMPULSE_PULLBACK,"legacy opposing-count wait preserved");
  }
  Fresh();Parent(1);SetupModeSetting=0;Touch(1);Check(attempts==0,"mode 1 tick path cannot affect range setup");
}
void Ownership()
{
  Fresh();Parent(1);double origin=impulseOrigin;ProcessImpulseSetup(1.2098,1.211,1.2098,1.2108,1.2098);
  Check(Near(impulseOrigin,origin)&&breakout==10,"contained child does not replace parent");
  ConsumeImpulse("test consumed");haveLatestSwingLow=true;latestSwingLow=1.20;latestSwingLowBar=9;
  double p;long b;TDateTime t;const char *why="";Check(!CanSeedImpulse(1,1.212,p,b,t,why),"consumed parent cannot re-arm from old origin");
  latestSwingLowBar=nbar+1;nbar+=2;Check(CanSeedImpulse(1,1.212,p,b,t,why),"distinct post-consumption leg remains eligible");
  Fresh();double body,range;Check(SeedClassification(1,1.20,1.205,1.199,1.204,body,range)==2,"oversized cap retained");
  ObserveProvisional(1,1.205,2,body,range,false,"test");haveLatestSwingLow=true;latestSwingLow=1.19;latestSwingLowBar=5;
  Check(!TryPromoteCandidate(provisionalLong)&&provisionalLong.active,"oversized seed cannot promote without later evidence");
}
// Chronological market input only: publish one live bar's ticks, then shift it
// into the closed series. GetSingleTick -> ProcessBar confirms pivots itself.
// Reflection exercises the exact same price chronology in both directions.
double MarketPrice(double x,int reflection){return 1.20+reflection*x*0.001;}
void MarketQuote(double x,int reflection)
{
  now=Max(now,series[0].t);double b=MarketPrice(x,reflection);
  series[0].h=Max(series[0].h,b);series[0].l=Min(series[0].l,b);series[0].c=b;
  Quote(b,b+0.0002);
}
void MarketBar(int reflection,double o,double h,double l,double c,double nextOpen=1e100)
{
  double t=series[0].t;
  // Keep the already observed opening/touch ticks in this bar's OHLC. A gap
  // opening is specified by the preceding bar, before its first tick arrives.
  MarketQuote(o,reflection);
  // In each directional expansion the adverse extreme precedes its endpoint.
  MarketQuote(c>=o?l:h,reflection);MarketQuote(c>=o?h:l,reflection);MarketQuote(c,reflection);
  for(int i=127;i>0;i--)series[i]=series[i-1];
  if(nextOpen==1e100)nextOpen=c;
  double opening=MarketPrice(nextOpen,reflection);
  series[0]={opening,opening,opening,opening,t+5.0/1440};
  MarketQuote(nextOpen,reflection);
}
void MarketDecision(int reflection,int direction,int mode,double endpoint)
{
  if(mode==1){double b=touchLevel-(direction==1?0.0002:0);MarketQuote((b-1.20)/(reflection*0.001),reflection);return;}
  // Two closed opposing corrections qualify PB1; a later close resumes it.
  double e=(endpoint-1.20)/(reflection*0.001),s=direction*reflection;
  auto bar=[&](double o,double h,double l,double c){
    if(s==1)MarketBar(reflection,e+o,e+h,e+l,e+c);
    else MarketBar(reflection,e-o,e-l,e-h,e-c);
  };
  bar(-0.2,0,-1.4,-1.2);bar(-1.2,-1.0,-1.7,-1.5);bar(-1.5,-0.9,-1.6,-1.0);
}
void DirectionFilteredLifecycle()
{
  for(int mode:{1,0})for(int reflection:{1,-1})for(int restricted:{1,0}){
    Fresh();ImpulseEntryMode=mode;TradeDirection=restricted?(reflection==1?1:2):0;
    // Custom slow ATR keeps these compact prices within the normal seed band.
    // No origins, candidates, parents or consumed state are injected.
    ATRLength=100;
    MarketBar(reflection,0,0.4,-0.2,0.2);
    MarketBar(reflection,0.2,0.3,-1,-0.2);long originBar=nbar;
    MarketBar(reflection,-0.2,0.5,-0.5,0.2);
    Check(!haveLatestSwingLow||reflection==-1||latestSwingLowBar!=originBar,"origin unavailable before right-side confirmation");
    MarketBar(reflection,0.2,0.6,0,0.4,7.2);
    Check((reflection==1?latestSwingLowBar:latestSwingHighBar)==originBar,"origin appears only on confirmation bar");
    MarketBar(reflection,7.2,10,7.1,9.8);
    Check(dir==reflection&&stage==IMPULSE_PARENT,"initial allowed parent detected through production bars");
    double oldEndpoint=setupExtreme;MarketDecision(reflection,reflection,mode,oldEndpoint);
    Check(stage==IMPULSE_CONSUMED&&attempts==1,"initial allowed parent consumes once");
    // Old origin and a later corrective origin cannot grant PB2, even with a
    // fresh normal seed. Its endpoint remains inside the consumed boundary.
    MarketBar(reflection,9.8,9.9,9.3,9.5);
    MarketBar(reflection,9.5,9.7,9,9.2,4);
    MarketBar(reflection,4,4.2,3.6,3.8);
    Check(stage==IMPULSE_CONSUMED&&attempts==1,"old-origin second correction stays consumed");
    MarketBar(reflection,3.8,4.3,3.7,4);
    MarketBar(reflection,4,4.4,3.9,4.2,1.4);
    Check((reflection==1?latestSwingLowBar:latestSwingHighBar)>consumedBar,"new corrective origin is confirmed after consumption");
    MarketBar(reflection,1.4,4.5,1.3,4);
    Check(stage==IMPULSE_CONSUMED&&attempts==1&&CandidateFor(reflection).active,"new confirmed origin alone cannot revive contained second pullback");
    MarketBar(reflection,4,4.3,3.7,3.9);
    MarketBar(reflection,3.9,4.2,3.6,3.8);
    Check(stage==IMPULSE_CONSUMED&&attempts==1,"newer pivot confirmation cannot unlock old boundary");
    size_t beforeOpposite=logs.size(),markersBefore=markerLabels.size();
    MarketBar(reflection,3.8,3.9,1,1.2);
    Check(dir==-reflection&&stage==IMPULSE_PARENT,"opposite structural cycle must be observed despite execution filter (old consumed boundary otherwise remains locked)");
    double oppositeEndpoint=setupExtreme;MarketDecision(reflection,-reflection,mode,oppositeEndpoint);
    Check(stage==IMPULSE_CONSUMED&&consumedDirection==-reflection&&!touchActive,"opposite structural cycle consumes at normal decision point");
    Check(attempts==(restricted?1:2),"opposite execution permission enforced in both entry modes");
    if(restricted){
      bool blocked=false;
      for(size_t i=beforeOpposite;i<logs.size();i++){
        Check(logs[i].find("entry frozen")==std::string::npos&&logs[i].find("confirmed entry")==std::string::npos&&logs[i].find("reason=entered")==std::string::npos,"structure-only cycle must never report entry");
        if(logs[i].find("direction blocked")!=std::string::npos)blocked=true;
      }
      Check(blocked,"specific direction-blocked transition diagnostic");
      for(size_t i=markersBefore;i<markerLabels.size();i++)Check(markerLabels[i]!="WT"&&markerLabels[i]!="CT"&&markerLabels[i]!="N","structure-only cycle has no entry marker");
    }
    int previousAttempts=attempts;
    // A distinct corrective origin forms AFTER the opposite opportunity was
    // consumed, and confirms before the next allowed expansion is submitted.
    MarketBar(reflection,0,0.2,-1,-0.2);long freshOrigin=nbar;
    MarketBar(reflection,-0.2,0.5,-0.5,0.2);
    MarketBar(reflection,0.2,0.6,0,0.4,0);
    Check(attempts==previousAttempts,"blocked opportunity has no delayed order");
    MarketBar(reflection,0,2.8,-0.1,2.6);
    Check(dir==reflection&&stage==IMPULSE_PARENT&&impulseOriginBar==freshOrigin,"fresh allowed cycle recovers using causally confirmed origin");
    long parentBar=breakout;double parentOrigin=impulseOrigin,frozenATR=setupATR;
    MarketBar(reflection,2.6,5.3,2.4,5.1);
    Check(breakout==parentBar&&Near(impulseOrigin,parentOrigin)&&Near(setupATR,frozenATR),"unresolved contained child preserves parent and frozen ATR");
    Check(reflection==1?setupExtreme<oldEndpoint:setupExtreme>oldEndpoint,"fresh endpoint is inside original consumed boundary");
    Check(attempts==previousAttempts,"no arming-tick or retrospective entry");
    MarketDecision(reflection,reflection,mode,setupExtreme);
    Check(stage==IMPULSE_CONSUMED&&attempts==previousAttempts+1,"fresh allowed cycle enters on future decision");
    if(restricted)Check(reflection==1?sellAttempts==0:buyAttempts==0,"zero prohibited submissions across complete lifecycle");
  }
  // A normal seed observed before its pivot confirms must replay the actual
  // later touch, consume it, and reject any recovered second touch.
  for(int reflection:{1,-1}){
    Fresh();TradeDirection=reflection==1?1:2;ATRLength=100;
    double opening=MarketPrice(-3,reflection);series[0]={opening,opening,opening,opening,series[0].t};
    MarketBar(reflection,-3,0,-3.1,-0.4);
    Check(CandidateFor(reflection).active&&stage==0,"unconfirmed origin retains provisional seed only");
    MarketBar(reflection,-0.4,-0.2,-1.4,-1.2);
    MarketBar(reflection,-1.2,-0.4,-1.3,-0.6);
    Check(stage==IMPULSE_CONSUMED&&attempts==0&&Logged("touch already passed"),"confirmed-origin promotion consumes recovered passed touch");
    MarketQuote(-1.4,reflection);MarketQuote(-0.5,reflection);MarketQuote(-1.4,reflection);
    Check(attempts==0,"recovered touch never queues a second touch");
    ResetStrategy();
    Check(stage==0&&consumedBar==-1&&!touchActive&&!provisionalLong.active&&!provisionalShort.active&&!haveLatestSwingLow&&!haveLatestSwingHigh,"restart clears evidence without manufacturing eligible structure");
  }
}
int main(int argc,char **argv)
{
  if(argc!=2){fprintf(stderr,"One named case required\n");return 2;}Hooks();InitStrategy();ReplaceStr(Currency,(PChar)"EURUSD");
  std::string name=argv[1];
  if(name=="touch_intrabar")Intrabar();else if(name=="executable_quotes_gap_rounding")QuotesGap();
  else if(name=="observed_tick_activation")ObservedActivation();
  else if(name=="activation_and_passed")ActivationPassed();else if(name=="recovered_touch")Recover();
  else if(name=="endpoint_refresh_ordering")RefreshOrdering();else if(name=="touch_gates")Gates();
  else if(name=="order_failure")OrderFailure();else if(name=="frozen_brackets")Brackets();
  else if(name=="legacy_confirmation_entries")LegacyEntries();else if(name=="parent_ownership")Ownership();
  else if(name=="direction_filtered_lifecycle")DirectionFilteredLifecycle();
  else{fprintf(stderr,"Unknown case: %s\n",argv[1]);return 2;}
  DoneStrategy();printf("PASS: %s\n",argv[1]);
}
