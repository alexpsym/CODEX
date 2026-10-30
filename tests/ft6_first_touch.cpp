// Filtered synthetic FT6 API seam. Exactly one named case per invocation.
#include <string>
#include <vector>
#include "../forex-tester-6/RangeBreakoutPullbackStrategy/RangeBreakoutPullbackStrategy.cpp"
struct Bar {double o,h,l,c,t;};
Bar series[512];
Bar contextSeries[320];int contextSize=0,contextReadCount=0,oldestContextOHLC=0;
std::vector<std::string> logs;
std::vector<std::string> markerLabels;double markerPrice=0;
double bidQuote,askQuote,now,grid=0.0001,orderQuote,orderSL,orderTP;
int attempts,orderType,selectedFrame,buyAttempts,sellAttempts;unsigned long historyBars=512;
bool busy,orderResult;int orderTicket;
bool contextBound=false,frameSuccess=true;int frameCalls=0,symbolReadsBeforeBind=0,contextReadsBeforeBind=0;std::string selectedSymbol;int selectedTimeframe=0,symbolResultMode=0;
struct HistoryRow{int type,ticket,magic;std::string symbol;double closeTime;};
std::vector<HistoryRow> tradeHistory;int selectedHistory=-1,historyScans=0,pauseCalls=0;bool failHistorySelect=false;
void Check(bool ok,const char *why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);for(auto &s:logs)fprintf(stderr,"%s\n",s.c_str());exit(1);}}
bool Near(double a,double b){return fabs(a-b)<1e-9;}
bool Logged(const char *s){for(auto &v:logs)if(v.find(s)!=std::string::npos)return true;return false;}
void __stdcall Ignore(void*,PChar){}
void __stdcall Register(void*,PChar,int,void*){}
void __stdcall OptionValue(void*,PChar,PChar){}
void __stdcall Capture(void*,PChar s){logs.push_back(s);}
PChar __stdcall Sym(void*){if(!contextBound){symbolReadsBeforeBind++;return (PChar)"";}if(symbolResultMode==1)return NULL;if(symbolResultMode==2)return (PChar)"";return (PChar)selectedSymbol.c_str();}
bool __stdcall Frame(void*,PChar symbol,int f){frameCalls++;if(!frameSuccess||!symbol||!*symbol){contextBound=false;selectedSymbol.clear();selectedTimeframe=0;return false;}selectedSymbol=symbol;selectedTimeframe=f;selectedFrame=f;contextBound=true;return true;}
bool NeedContext(){if(contextBound)return true;contextReadsBeforeBind++;return false;}
unsigned long __stdcall BarsCount(void*){return NeedContext()?historyBars:0;}
double __stdcall O(void*,int s){return NeedContext()?series[s].o:0;}
double __stdcall H(void*,int s){return NeedContext()?series[s].h:0;}
double __stdcall L(void*,int s){return NeedContext()?series[s].l:0;}
double __stdcall C(void*,int s){return NeedContext()?series[s].c:0;}
double __stdcall T(void*,int s){return NeedContext()?series[s].t:0;}
double __stdcall Now(void*){return now;}
double __stdcall BidQuote(void*){return NeedContext()?bidQuote:0;}
double __stdcall AskQuote(void*){return NeedContext()?askQuote:0;}
double __stdcall Grid(void*){return NeedContext()?grid:0;}
int __stdcall Orders(void*){return busy?1:0;}
bool __stdcall Select(void*,int,int,int){return busy;}
int __stdcall Magic(void*){return selectedHistory>=0?tradeHistory[selectedHistory].magic:MagicNumber;}
int __stdcall HistoryTotalMock(void*){return NeedContext()?(int)tradeHistory.size():-1;}
bool __stdcall SelectHistory(void*,int index,int flags,int pool){if(!NeedContext())return false;if(pool==MODE_HISTORY&&flags==SELECT_BY_POS){historyScans++;if(failHistorySelect||index<0||index>=(int)tradeHistory.size())return false;selectedHistory=index;return true;}selectedHistory=-1;return busy;}
int __stdcall HistoryTicket(void*){return selectedHistory>=0?tradeHistory[selectedHistory].ticket:orderTicket;}
int __stdcall HistoryType(void*){return selectedHistory>=0?tradeHistory[selectedHistory].type:tp_Buy;}
double __stdcall HistoryClose(void*){return selectedHistory>=0?tradeHistory[selectedHistory].closeTime:0;}
PChar __stdcall HistorySymbol(void*){return selectedHistory>=0?(PChar)tradeHistory[selectedHistory].symbol.c_str():(PChar)"EURUSD";}
void __stdcall PauseMock(void*,PChar){pauseCalls++;}
void CheckIndexedContext(PChar symbol,int frame,int index=1)
{
  Check(NeedContext()&&symbol&&strcmp(symbol,"EURUSD")==0&&frame==PERIOD_M15&&selectedTimeframe==PERIOD_M5,"indexed M15 reads preserve selected Currency/M5 context");
  Check(index>=1&&index<contextSize,"no index zero or out-of-range M15 read");contextReadCount++;
}
unsigned long __stdcall IBars(void*,PChar symbol,int frame){contextReadCount++;return contextSize;}
double __stdcall IO(void*,PChar symbol,int frame,int i){CheckIndexedContext(symbol,frame,i);oldestContextOHLC=i>oldestContextOHLC?i:oldestContextOHLC;return contextSeries[i].o;}
double __stdcall IH(void*,PChar symbol,int frame,int i){CheckIndexedContext(symbol,frame,i);return contextSeries[i].h;}
double __stdcall IL(void*,PChar symbol,int frame,int i){CheckIndexedContext(symbol,frame,i);return contextSeries[i].l;}
double __stdcall IC(void*,PChar symbol,int frame,int i){CheckIndexedContext(symbol,frame,i);return contextSeries[i].c;}
double __stdcall IT(void*,PChar symbol,int frame,int i){CheckIndexedContext(symbol,frame,i);return contextSeries[i].t;}
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
  IntrfProcsRec.Symbol=Sym;IntrfProcsRec.SetCurrencyAndTimeframe=Frame;IntrfProcsRec.pSetCurrencyAndTimeframe=(void*)1;IntrfProcsRec.Bars=BarsCount;
  IntrfProcsRec.Open=O;IntrfProcsRec.High=H;IntrfProcsRec.Low=L;IntrfProcsRec.Close=C;IntrfProcsRec.Time=T;
  IntrfProcsRec.TimeCurrent=Now;IntrfProcsRec.pTimeCurrent=(void*)1;IntrfProcsRec.Bid=BidQuote;IntrfProcsRec.Ask=AskQuote;IntrfProcsRec.Point=Grid;
  IntrfProcsRec.OrdersTotal=Orders;IntrfProcsRec.OrderSelect=SelectHistory;IntrfProcsRec.pOrderSelect=(void*)1;IntrfProcsRec.OrderMagicNumber=Magic;IntrfProcsRec.pOrderMagicNumber=(void*)1;
  IntrfProcsRec.OrderSymbol=HistorySymbol;IntrfProcsRec.pOrderSymbol=(void*)1;IntrfProcsRec.HistoryTotal=HistoryTotalMock;IntrfProcsRec.pHistoryTotal=(void*)1;
  IntrfProcsRec.OrderTicket=HistoryTicket;IntrfProcsRec.pOrderTicket=(void*)1;IntrfProcsRec.OrderType=HistoryType;IntrfProcsRec.pOrderType=(void*)1;
  IntrfProcsRec.OrderCloseTime=HistoryClose;IntrfProcsRec.pOrderCloseTime=(void*)1;IntrfProcsRec.Pause=PauseMock;IntrfProcsRec.pPause=(void*)1;
  IntrfProcsRec.iBars=IBars;IntrfProcsRec.piBars=(void*)1;
  IntrfProcsRec.iOpen=IO;IntrfProcsRec.piOpen=(void*)1;IntrfProcsRec.iHigh=IH;IntrfProcsRec.piHigh=(void*)1;
  IntrfProcsRec.iLow=IL;IntrfProcsRec.piLow=(void*)1;IntrfProcsRec.iClose=IC;IntrfProcsRec.piClose=(void*)1;
  IntrfProcsRec.iTime=IT;IntrfProcsRec.piTime=(void*)1;IntrfProcsRec.SendInstantOrder2=Send;
  IntrfProcsRec.ObjectExists=Exists;IntrfProcsRec.ObjectCreate=Create;IntrfProcsRec.ObjectSetText=Text;IntrfProcsRec.pObjectCreate=(void*)1;IntrfProcsRec.pObjectSetText=(void*)1;
}
void Fresh()
{
  ResetStrategy();SettingsPreset=5;ApplySettingsPreset();runSettingsResolved=true;EnableWeekendBlackout=false;
  contextBound=false;frameSuccess=true;symbolResultMode=0;frameCalls=0;symbolReadsBeforeBind=contextReadsBeforeBind=0;selectedSymbol.clear();selectedTimeframe=selectedFrame=0;Check(Frame(NULL,(PChar)"EURUSD",PERIOD_M5),"fixture explicitly selects its context");
  now=46000.5;for(int i=0;i<512;i++)series[i]={1.20,1.2002,1.1998,1.20,now-i*5.0/1440};
  lastTime=series[0].t;nbar=10;atrCount=100;rmaATR=0.00206;RegimeLookback=1;AddATRPercent(0.1);
  historyBars=512;tradeHistory.clear();selectedHistory=-1;historyScans=0;pauseCalls=0;failHistorySelect=false;MaximumCompletedStrategyTrades=100;bidQuote=1.21;askQuote=1.2102;busy=false;attempts=0;orderResult=true;orderTicket=42;
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
void AddClosed(int ticket,int type=tp_Buy,int magic=26083001,const char *symbol="EURUSD",double close=46000.5)
{tradeHistory.push_back(HistoryRow{type,ticket,magic,symbol,close});}
void TradeLimitStop()
{
  Fresh();SettingsPreset=6;ApplySettingsPreset();runSettingsResolved=true;MaximumCompletedStrategyTrades=100;
  for(int i=0;i<99;i++)AddClosed(1000+i,i%2?tp_Buy:tp_Sell);
  // An open hundredth position is absent from MODE_HISTORY and must not stop
  // the test or be force-closed by the strategy.
  busy=true;GetSingleTick();Check(pauseCalls==0&&tradeHistory.size()==99&&attempts==0,"99 closed plus one open does not stop or close early");
  busy=false;AddClosed(1099,tp_Sell);historyBars=128;series[0].t+=5.0/1440;GetSingleTick();
  Check(pauseCalls==1&&tradeLimitBlocked&&Logged("completed=100 limit=100; test paused"),"trade 100 close pauses before strategy tick processing");
  Check(attempts==0&&tradeHistory.size()==100,"trade 100 history retained and trade 101 not submitted");
  size_t messages=logs.size();int scans=historyScans;
  for(int i=0;i<4;i++){series[0].t+=5.0/1440;GetSingleTick();}
  Check(pauseCalls==1&&logs.size()==messages&&attempts==0,"manual resume callbacks remain blocked without repeated notification");
  Check(historyScans==scans,"unchanged stopped history is not rescanned");
  Fresh(); // eligible-entry control: the fixture can submit below the cap.
  Check(Enter(1,1,false,0)&&attempts==1,"eligible uncapped entry control submits normally");
  for(int mode=0;mode<4;mode++)
  {
    Fresh();MaximumCompletedStrategyTrades=1;AddClosed(2000+mode);runSettingsResolved=true;
    if(mode==0)SetupModeSetting=0;else if(mode==1)ImpulseEntryMode=0;else SetupModeSetting=1;
    touchActive=true;touchLevel=askQuote;touchActivationTick=0;dir=1;setupExtreme=2.0;
    series[0].t+=5.0/1440;GetSingleTick(); // new-bar dispatch
    GetSingleTick(); // intrabar dispatch / simulated manual resume
    Check(pauseCalls==1&&attempts==0&&tradeLimitBlocked,"range/confirmation/first-touch new-bar and intrabar boundaries block after threshold");
  }
}
void TradeLimitHistory()
{
  Fresh();MaximumCompletedStrategyTrades=5;
  AddClosed(1,tp_Buy);AddClosed(2,tp_Sell);AddClosed(3,tp_BuyLimit);AddClosed(4,tp_Credit);AddClosed(5,tp_Balance);
  AddClosed(6,tp_Buy,999,"EURUSD");AddClosed(7,tp_Sell,MagicNumber,"GBPUSD");AddClosed(8,tp_Buy,MagicNumber,"EURUSD",0);
  AddClosed(1,tp_Buy); // duplicate ticket is counted once
  AddClosed(9,tp_Sell);AddClosed(10,tp_Buy); // SL/TP/normal closure all use closed market history
  GetSingleTick();Check(pauseCalls==0&&cachedCompletedStrategyTrades==4,"filtered unique market closures counted, including sell and closed ticket");
  orderResult=false;Check(Enter(1,1,false,0)&&attempts==1,"failed entry attempt is submitted below the cap");
  Check(cachedCompletedStrategyTrades==4&&pauseCalls==0,"failed entry is not counted as a completed trade");
  AddClosed(11,tp_Sell);GetSingleTick();Check(pauseCalls==1&&cachedCompletedStrategyTrades==5,"newly closed market ticket reaches the configured limit");
  int scans=historyScans;GetSingleTick();GetSingleTick();Check(historyScans==scans,"no historical rescans on unchanged ticks");
  Fresh();MaximumCompletedStrategyTrades=3;AddClosed(1);AddClosed(2);AddClosed(3);AddClosed(4);GetSingleTick();
  Check(pauseCalls==1&&cachedCompletedStrategyTrades==4,"retained history already above limit pauses honestly");
  Fresh();MaximumCompletedStrategyTrades=1;AddClosed(1);IntrfProcsRec.pPause=NULL;GetSingleTick();
  Check(tradeLimitBlocked&&pauseCalls==0&&Logged("Pause API unavailable")&&attempts==0,"missing pause context reports failure and blocks entry");
  IntrfProcsRec.pPause=(void*)1;
  Fresh();MaximumCompletedStrategyTrades=1;IntrfProcsRec.pHistoryTotal=NULL;GetSingleTick();
  Check(tradeLimitBlocked&&pauseCalls==1&&Logged("required history API/context unavailable")&&attempts==0,"missing history context is reported, paused and entry-blocked");
  IntrfProcsRec.pHistoryTotal=(void*)1;
  Fresh();MaximumCompletedStrategyTrades=1;AddClosed(1);failHistorySelect=true;GetSingleTick();
  Check(tradeLimitBlocked&&pauseCalls==1&&Logged("history order selection failed")&&attempts==0,"history selection failure reports and pauses");
}
void TradeLimitControls()
{
  Fresh();Check(MaximumCompletedStrategyTrades==100,"default cap is 100");
  MaximumCompletedStrategyTrades=0;IntrfProcsRec.pHistoryTotal=NULL;GetSingleTick();
  Check(!tradeLimitBlocked&&pauseCalls==0,"zero bypasses cap/history API and stays unrestricted");
  IntrfProcsRec.pHistoryTotal=(void*)1;
  Fresh();MaximumCompletedStrategyTrades=7;AddClosed(10);GetSingleTick();
  Check(!tradeLimitBlocked&&cachedCompletedStrategyTrades==1,"positive custom limit counts without stopping early");
  AddClosed(11);ResetStrategy();SettingsPreset=6;ApplySettingsPreset();runSettingsResolved=true;GetSingleTick();
  Check(cachedCompletedStrategyTrades==2&&MaximumCompletedStrategyTrades==7,"reset reconciles retained history and presets preserve selected limit");
  Fresh();MaximumCompletedStrategyTrades=7;GetSingleTick();
  Check(cachedCompletedStrategyTrades==0&&!tradeLimitBlocked,"genuine empty history restart starts at zero");
}
double QPrice(int d,double p){return d==1?p:2.40-p;}
void QualifiedFresh(int d=1,bool completeHistory=true)
{
  Fresh();SettingsPreset=7;ApplySettingsPreset();EnableWeekendBlackout=false;TradeDirection=d==1?1:0;rmaATR=0.002;RegimeLookback=1;
  // Confirmed origin exists before the first normal seed, not an armed fixture.
  if(d==1){haveLatestSwingLow=true;latestSwingLow=1.20;latestSwingLowBar=9;latestSwingLowTime=ClosedTime(9);}
  else{haveLatestSwingHigh=true;latestSwingHigh=1.20;latestSwingHighBar=9;latestSwingHighTime=ClosedTime(9);}
  for(int i=3;i<=22;i++){series[i].h=d==1?1.204:1.201;series[i].l=d==1?1.199:1.196;}
  if(!completeHistory)historyBars=22; // The 20th pre-origin bar is absent.
  series[1]={QPrice(d,1.2005),QPrice(d,d==1?1.203:1.200),QPrice(d,d==1?1.200:1.203),QPrice(d,1.203),series[1].t};
  bidQuote=QPrice(d,1.203);askQuote=bidQuote+0.0002;
  Remember(nbar,QPrice(d,1.203),d,QPrice(d,1.2028));
  ProcessImpulseSetup(series[1].o,series[1].h,series[1].l,series[1].c,1.20);
  Check(stage==IMPULSE_PARENT&&!touchActive&&!impulseQualified,"normal seed starts pending, never arms weak rebound");
}
void QBar(int d,double o,double h,double l,double c,double adverse=-1)
{
  double prior=series[1].c;
  for(int i=127;i>0;i--)series[i]=series[i-1];
  series[0].t+=5.0/1440;series[1]={QPrice(d,o),QPrice(d,d==1?h:l),QPrice(d,d==1?l:h),QPrice(d,c),series[0].t-5.0/1440};
  nbar++;lastTime=series[0].t;now=series[0].t;tickSequence++;
  bidQuote=QPrice(d,c);askQuote=bidQuote+0.0002;
  if(adverse>=0)Remember(nbar,QPrice(d,h),d,QPrice(d,adverse));
  ProcessImpulseSetup(series[1].o,series[1].h,series[1].l,series[1].c,prior);
}
void QSurge(int d=1)
{
  QBar(d,1.207,1.208,1.2068,1.2072,1.2070);
  Check(impulseQualified&&touchActive&&qualifyingBar==nbar&&qualifyingTime==Time(1),"closed surge qualifies and records actual bar/time");
  Check(Near(touchLevel,QPrice(d,1.206))&&attempts==0,"prospective depth level arms without order");
}
void QualifiedImpulseGate()
{
  for(int d:{1,-1})
  {
    QualifiedFresh(d);double high=qualifiedRangeHigh,low=qualifiedRangeLow;
    Check(qualifiedRangeReady&&Near(high,d==1?1.204:1.201)&&Near(low,d==1?1.199:1.196),"exact full pre-origin window excludes origin and seed");
    QBar(d,1.2048,1.2055,1.2045,1.2053,1.2052);
    Check(!touchActive&&!impulseQualified,"breakout close alone cannot qualify insufficient displacement");
    QBar(d,1.207,1.208,1.204,1.2041,1.2078);
    Check(!impulseQualified,"large wick and accepted displacement inside range cannot qualify");
    // Exact strict boundary uses the identical frozen operands.
    double boundary=d==1?qualifiedRangeHigh+0.1*setupATR:qualifiedRangeLow-0.1*setupATR;
    QBar(d,1.204,1.208,1.204,QPrice(d,boundary),1.2078);
    Check(!impulseQualified,"close equal to breakout buffer boundary rejected strictly");
    series[0].h=2;series[0].l=0.1;series[0].c=QPrice(d,1.30);ProcessFirstTouchTick(true);
    Check(!impulseQualified&&attempts==0,"unfinished candle cannot qualify");
    QBar(d,1.208,1.210,1.2078,1.2095,1.2093);
    Check(impulseQualified&&touchActive&&Near(high,qualifiedRangeHigh)&&Near(low,qualifiedRangeLow),"later bars cannot move frozen comparison range; mirrored Custom short qualifies");
    QualifiedFresh(d,false); // No shorter-window substitution.
    QBar(d,1.207,1.208,1.2068,1.2072,1.2070);
    Check(!qualifiedRangeReady&&!touchActive&&!impulseQualified,"incomplete pre-origin history remains unavailable");
    QualifiedFresh(d);setupATR=0;qualifiedRangeReady=false;BeginQualification(nbar);
    Check(!qualifiedRangeReady&&!TryQualifyImpulse(nbar),"invalid frozen ATR cannot qualify");
  }
}
void QualifiedPullbackTouch()
{
  for(int d:{1,-1})
  {
    QualifiedFresh(d);QSurge(d);double initial=touchLevel,atr=setupATR;
    Quote(QPrice(d,1.2068),QPrice(d,1.2068)+0.0002);
    Check(touchActive&&attempts==0,"sub-threshold early dip preserves opportunity");
    QBar(d,1.211,1.212,1.2108,1.2115,1.2113);
    Check(touchActive&&Near(touchLevel,QPrice(d,1.209))&&Near(setupATR,atr)&&touchLevel!=initial,"same parent endpoint growth refreshes percentage level prospectively");
    Check(touchActivationTick==tickSequence&&attempts==0,"no refresh tick order");
    double level=touchLevel;
    Quote(d==1?level:level-0.0002,d==1?level+0.0002:level);
    Check(attempts==0&&touchActive,"non-executable Bid-only/Ask-only touch ignored");
    Touch(d,0.0003);
    Check(attempts==1&&stage==IMPULSE_CONSUMED&&Near(orderQuote,level-d*0.0003),"first threshold retracement uses actual executable overshoot quote");
    Touch(d);Check(attempts==1,"consumed parent cannot retry");
  }
  QualifiedFresh();QualifiedPullbackMinimumDepth=10;
  QBar(1,1.207,1.208,1.2075,1.2078,1.2076);
  Check(touchActive&&Near(touchLevel,1.207),"Custom mode 2 retains the larger ATR retracement floor");
  // Opening quote evaluates OLD level before new-bar endpoint replacement.
  QualifiedFresh();QSurge();double old=touchLevel;
  series[1].h=1.212;series[0].t+=5.0/1440;now=series[0].t;
  bidQuote=old-0.0002;askQuote=old;GetSingleTick();
  Check(attempts==1&&Near(orderQuote,old)&&Near(touchLevel,old),"qualified old-level-before-refresh ordering");
  for(std::string reason:{"passed","missing","busy","history","risk","direction","failure","ticket","arming"})
  {
    QualifiedFresh();
    if(reason=="passed"||reason=="missing")
    {
      QBar(1,1.207,1.208,1.205,1.2072,reason=="passed"?1.2055:-1);
      Check(attempts==0&&stage==IMPULSE_CONSUMED,"passed or ambiguous qualification endpoint never filled retrospectively");continue;
    }
    QSurge();
    if(reason=="busy")busy=true;
    if(reason=="history")historyBars=0;
    if(reason=="risk")atrCount=0;
    if(reason=="direction")TradeDirection=2;
    if(reason=="failure")orderResult=false;
    if(reason=="ticket")orderTicket=-1;
    if(reason=="arming")
    {
      bidQuote=touchLevel-0.0002;askQuote=touchLevel;ProcessFirstTouchTick(true);
      Check(attempts==0&&touchActive,"explicit arming tick cannot fill");
    }
    Touch(1);int count=attempts;Touch(1);
    Check(stage==IMPULSE_CONSUMED&&attempts==count,"blocked/failed first touch consumed without queue");
    Check(count==((reason=="failure"||reason=="ticket"||reason=="arming")?1:0),"qualified touch gates and both API success confirmations");
  }
  // Promotion walks pending eligibility chronologically, then rejects passed paths.
  for(int passed:{0,1})
  {
    QualifiedFresh();QSurge();ResetTouch();ResetQualification();stage=0;
    ProvisionalImpulseCandidate &candidate=CandidateFor(1);candidate.active=true;candidate.normalSeedSeen=true;
    candidate.firstBar=nbar-1;candidate.extremeBar=nbar;candidate.firstTime=ClosedTime(nbar-1);candidate.seedATR=0.002;candidate.firstExtreme=1.203;candidate.extreme=1.208;
    if(passed)touchPaths[1].minAskAfterHigh=1.205;
    Check(TryPromoteCandidate(candidate),"actual provisional transition recovers confirmed origin");
    Check(qualifyingBar==nbar&&attempts==0&&(passed?stage==IMPULSE_CONSUMED:touchActive),"promotion retains qualifying bar and conservative path ordering without backdated entry");
  }
  std::vector<std::string> prefix;
  for(int future:{0,1})
  {
    QualifiedFresh();QSurge();Quote(1.2068,1.2070);
    if(!future)prefix=logs;else Check(prefix==logs,"identical causal decision prefix under different future continuations");
    QBar(1,1.211,1.212+future*0.002,1.2108,1.2115,1.2113);
  }
}
void QualifiedParentLifecycle()
{
  QualifiedFresh();long consumed=consumedBar;
  nbar+=31;ProcessImpulseSetup(1.20,1.2001,1.1999,1.20,1.20);
  Check(stage==0&&consumedBar==consumed&&!qualifiedRangeReady,"weak pending expires without consuming real parent boundary");
  haveLatestSwingLow=true;latestSwingLowBar=nbar-1;latestSwingLowTime=ClosedTime(nbar-1);latestSwingLow=1.20;
  QBar(1,1.2005,1.203,1.200,1.203,1.2028);QSurge();Touch(1);
  Check(attempts==1&&stage==IMPULSE_CONSUMED,"later valid leg survives weak candidate expiry");
  double p;long b;TDateTime t;const char *why="";
  Check(!CanSeedImpulse(1,1.22,p,b,t,why),"consumed parent requires distinct confirmed origin");
  Touch(1);Check(attempts==1,"no consumed-parent revival");
  QualifiedFresh();long first=qualifiedFirstSeedBar;
  latestSwingLowBar=first+1;latestSwingLowTime=Time(1);nbar+=2;
  ProcessImpulseSetup(1.2005,1.203,1.20,1.203,1.20);
  Check(qualifiedFirstSeedBar==nbar&&impulseOriginBar==first+1&&consumedBar==-1,"new confirmed origin cleanly replaces incompatible pending candidate");
  QualifiedFresh(-1);TradeDirection=1;QSurge(-1);Touch(-1);
  Check(attempts==0&&stage==IMPULSE_CONSUMED&&Logged("structure-only"),"preset buys-only still observes and consumes prohibited short");
  haveLatestSwingLow=true;latestSwingLow=1.19;latestSwingLowBar=nbar+1;latestSwingLowTime=Time(1);nbar+=2;
  Check(CanSeedImpulse(1,1.195,p,b,t,why),"opposite structural cycle permits later allowed leg inside old boundary");
  QualifiedFresh();AbandonQualification("oversize fixture begins before seed");
  QBar(1,1.20,1.208,1.20,1.207,1.2072);
  Check(provisionalLong.active&&provisionalLong.oversizedSeen&&!touchActive,"over-cap candle cannot independently seed qualified mode");
  Check(!TryPromoteCandidate(provisionalLong),"oversized promotion still requires later objective evidence");
  QBar(1,1.207,1.209,1.207,1.208,1.2082);
  Check(!provisionalLong.active&&Logged("later same-direction endpoint advancement"),"later advancing evidence enables existing oversized recovery path");
  QualifiedFresh();QSurge();ResetStrategy();
  Check(!impulseQualified&&!qualifiedRangeReady&&qualifiedFirstSeedBar==-1&&qualifyingBar==-1&&qualifyingTime==0&&!touchActive,"reset clears all pending and qualification metadata");
  for(int bad=0;bad<7;bad++)
  {
    QualifiedFresh();
    if(bad==0)QualifiedImpulseRangeLookbackBars=0;
    if(bad==1)QualifiedImpulseMinimumDisplacementATR=0;
    if(bad==2)QualifiedImpulseBreakoutBufferATR=-0.1;
    if(bad==3)QualifiedPullbackMinimumDepth=0;
    if(bad==4)QualifiedPullbackMinimumDepth=50;
    if(bad==5)QualifiedImpulseMinimumDisplacementATR=HUGE_VAL;
    if(bad==6)QualifiedPullbackMinimumDepth=sqrt(-1.0);
    qualifiedConfigValid=ValidateQualifiedSettings();
    Check(!qualifiedConfigValid&&Logged("configuration error"),"invalid mode-2 controls clearly rejected");
    QBar(1,1.207,1.208,1.2068,1.2072,1.2070);Quote(1.205,1.2052);
    Check(!touchActive&&attempts==0,"invalid mode-2 configuration blocks entries");
    ImpulseEntryMode=1;Check(ValidateQualifiedSettings(),"invalid qualified controls do not affect legacy modes");
  }
  QualifiedFresh();QualifiedImpulseBreakoutBufferATR=-1;SettingsPreset=0;
  for(int i=0;i<runOptionCount;i++)
  {
    RunOption &option=runOptions[i];
    if(option.type==ot_Double)option.custom.number=*(double*)option.effective;
    else if(option.type==ot_Boolean)option.custom.boolean=*(bool*)option.effective;
    else option.custom.integer=*(int*)option.effective;
  }
  ResetStrategy();logs.clear();GetSingleTick();GetSingleTick();GetSingleTick();
  int errors=0;for(auto &line:logs)if(line.find("configuration error")!=std::string::npos)errors++;
  Check(errors==1&&!qualifiedConfigValid&&attempts==0,"late invalid Custom inputs resolve once with one error per run");
}
void ColdStartContext()
{
  auto Count=[&](const char *part){int count=0;for(auto &line:logs)if(line.find(part)!=std::string::npos)count++;return count;};
  auto ColdReset=[&](){ResetStrategy();SettingsPreset=7;runSettingsResolved=false;contextBound=false;frameSuccess=true;symbolResultMode=0;selectedSymbol.clear();selectedTimeframe=0;frameCalls=0;symbolReadsBeforeBind=contextReadsBeforeBind=0;logs.clear();historyBars=512;};
  ColdReset();PChar savedCurrency=Currency;Currency=NULL;GetSingleTick();GetSingleTick();
  Check(frameCalls==0&&Count("Currency is missing or empty")==1,"missing Currency blocks safely with one semantic diagnostic");Currency=savedCurrency;GetSingleTick();
  Check(startupContextValidated&&contextBound&&Count("outcome=success")==1,"valid Currency recovers safely on a later callback");
  ColdReset();frameSuccess=false;GetSingleTick();GetSingleTick();
  Check(frameCalls==2&&!contextBound&&contextReadsBeforeBind==0&&symbolReadsBeforeBind==0&&Count("SetCurrencyAndTimeframe returned false")==1,"failed binding leaves context unselected and avoids context reads without diagnostic spam");
  frameSuccess=true;GetSingleTick();Check(startupContextValidated&&contextBound&&selectedSymbol=="EURUSD"&&Count("outcome=success")==1,"successful later binding recovers from an API failure");
  ColdReset();symbolResultMode=1;GetSingleTick();
  Check(contextBound&&frameCalls==1&&contextReadsBeforeBind==0&&Count("Symbol is null or empty")==1,"null selected Symbol is safe and blocks the callback");GetSingleTick();Check(Count("Symbol is null or empty")==1,"repeated null Symbol failure is not logged on every callback");
  symbolResultMode=0;GetSingleTick();Check(startupContextValidated&&contextBound&&selectedSymbol=="EURUSD"&&selectedTimeframe==PERIOD_M5&&Count("outcome=success")==1,"later valid callback recovers and binds the requested symbol/timeframe");
  ColdReset();symbolResultMode=2;GetSingleTick();
  Check(contextBound&&frameCalls==1&&contextReadsBeforeBind==0&&Count("Symbol is null or empty")==1,"empty selected Symbol is safe and blocks the callback");GetSingleTick();Check(Count("Symbol is null or empty")==1,"repeated empty Symbol failure is not logged on every callback");
  symbolResultMode=0;GetSingleTick();Check(startupContextValidated&&Count("outcome=success")==1,"empty Symbol failure recovers on the next valid callback");

  // Build all readiness state through unresolved preset-7 production callbacks.
  // The mock keeps the complete 20-bar range, ATR(14), and 200-bar risk lookback.
  ColdReset();now=46000.5;for(int i=0;i<512;i++)series[i]={1.2000,1.2002,1.1998,1.2000,now-i*5.0/1440};
  bidQuote=1.2000;askQuote=1.2002;orderResult=true;orderTicket=42;attempts=0;EnableWeekendBlackout=true;
  auto CloseSyntheticBar=[&](double o,double h,double l,double c){
    Bar closed={o,h,l,c,series[0].t};for(int i=511;i>0;i--)series[i]=series[i-1];series[1]=closed;
    double t=closed.t+5.0/1440;series[0]={c,c+0.0001,c-0.0001,c,t};now=t;bidQuote=c;askQuote=c+0.0002;GetSingleTick();
  };
  for(int i=0;i<220;i++)CloseSyntheticBar(1.2000,1.2002,1.1998,1.2000);
  Check(runSettingsResolved&&SettingsPreset==7&&Timeframe==PERIOD_M5&&TradeDirection==1&&ImpulseEntryMode==2,"cold start resolves preset 7 once from untouched Custom settings");
  Check(nbar>=200&&atrCount>=ATRLength&&atrPctCount>=RegimeLookback,"normal callback history readiness and ATR/risk warmup use full configured lookbacks");
  Check(startupContextValidated&&startupHistoryReady&&firstProcessedClosedBar>=0&&Logged("RBP startup first-closed-bar")&&Logged("RBP progress processedBars=1 pending=no qualified=no touch=no"),"bound callbacks report readiness, processed-bar and progress state");
  CloseSyntheticBar(1.2000,1.2010,1.1999,1.2005);
  Check(haveLatestSwingLow&&impulseOriginBar<nbar&&activeImpulseSetup,"production callbacks derive a confirmed structural origin and parent seed");
  Check(qualifiedRangeReady&&!impulseQualified&&!touchActive&&stage==IMPULSE_PARENT,"seed callback freezes the full pre-origin range while displacement remains pending");
  CloseSyntheticBar(1.2005,1.2016,1.20115,1.2013);
  Check(impulseQualified&&touchActive&&stage==IMPULSE_PARENT,"completed endpoint close derives qualified breakout and prospective touch arming");
  Check(Logged("RBP qualified qualified")&&Logged("RBP qualified armed"),"qualification and prospective arming diagnostics are present");
  Quote(touchLevel-0.0002,touchLevel);Check(attempts==1&&stage==IMPULSE_CONSUMED&&Logged("confirmed entry"),"one executable synthetic quote produces exactly one entry through GetSingleTick");
  Quote(touchLevel-0.0002,touchLevel);Check(attempts==1,"subsequent callback cannot duplicate the consumed opportunity");
  Check(contextReadCount==0,"legacy mode 2 cold start performs no new M15 context reads");
  ResetStrategy();Check(!startupContextValidated&&!startupHistoryStateKnown&&!startupReadyProgressLogged&&firstProcessedClosedBar==-1&&lastProgressBar==0,"restart clears startup diagnostic and bar-progress state");
}
void StructureContextSeries(int d,TDateTime cutoff,double recent=1.207,double older=1.209)
{
  contextSize=260;contextReadCount=oldestContextOHLC=0;
  for(int i=0;i<contextSize;i++)
  {
    double h=Min(1.201,recent-0.0002),l=1.199;
    if(i==5)h=recent;if(i==9)h=older;if(i==13||i==14)h=1.212;if(i==18)h=1.215;
    contextSeries[i]={1.20,QPrice(d,d==1?h:l),QPrice(d,d==1?l:h),1.20,cutoff+(2-i)*15.0/1440};
  }
  contextSeries[0].t=cutoff+1;contextSeries[0].h=99; // never indexed
  contextSeries[1].h=contextSeries[2].h=99;contextSeries[1].l=contextSeries[2].l=0.01;
}
void StructurePending(int d=1,double barrier=1.209)
{
  Fresh();SettingsPreset=8;ApplySettingsPreset();EnableWeekendBlackout=false;
  nbar=40;rmaATR=0.002;now=series[0].t;StructureContextSeries(d,ClosedTime(38),barrier-0.002,barrier);
  series[3].c=1.20;series[2].c=QPrice(d,1.203);series[1].c=QPrice(d,1.207);
  series[2].h=QPrice(d,d==1?1.204:1.202);series[2].l=QPrice(d,d==1?1.202:1.204);
  series[1].h=QPrice(d,d==1?1.208:1.2068);series[1].l=QPrice(d,d==1?1.2068:1.208);
  StartImpulseAt(d,QPrice(d,1.204),39,ClosedTime(39),1.20,38,ClosedTime(38),rmaATR,ClosedTime(39),"synthetic causal origin");
}
void StructureContextGate()
{
  for(int d:{1,-1})
  {
    StructurePending(d);
    Check(structureContextReady&&Near(structureBarrier,QPrice(d,1.209)),"last two relevant pivots use max highs/min lows; plateaus and older third pivot excluded");
    Check(structureReferences[0].center==contextSeries[5].t&&structureReferences[1].center==contextSeries[9].t&&structureReferences[0].confirmation==contextSeries[3].t+15.0/1440,"complete right-side confirmation by origin-open cutoff");
    Check(oldestContextOHLC==202&&selectedTimeframe==PERIOD_M5,"scan uses exactly at most 200 eligible candles independent of ATR regime");
    ProcessFirstTouchBar(High(1),Low(1),Close(1));
    Check(!impulseQualified&&!touchActive&&Logged("has not broken frozen M15 structure"),"clean local breakout beneath older frozen resistance/support fails");
    double boundary=structureBarrier+d*QualifiedImpulseBreakoutBufferATR*setupATR;
    series[1].c=boundary;Check(!TryQualifyImpulse(nbar),"exact context close buffer is strict in both directions");
    double barrier=structureBarrier;StructureReference saved[2];memcpy(saved,structureReferences,sizeof(saved));
    contextSeries[5].h=10;contextSeries[9].l=0.01;int reads=contextReadCount;
    Check(CaptureStructureContext()&&reads==contextReadCount&&memcmp(saved,structureReferences,sizeof(saved))==0&&barrier==structureBarrier,"snapshot never substituted after subsequent data changes");
    QBar(d,1.211,1.212,1.2108,1.2115,1.2114);
    Check(impulseQualified&&touchActive&&qualifyingTime==Time(1),"long reversal and bearish continuation beyond older structure can qualify");

    StructurePending(d);ResetQualification();StructureContextSeries(d,ClosedTime(38));
    contextSeries[5].t=ClosedTime(38)-15.0/1440+1.0/86400; // end one second after cutoff; cannot round it in
    BeginQualification(39);
    Check(!structureContextReady||structureReferences[0].center!=contextSeries[5].t,"future close cannot pass by minute rounding");

    StructurePending(d);ResetQualification();StructureContextSeries(d,ClosedTime(38));
    contextSize=10;BeginQualification(39);
    Check(!structureContextReady&&!TryQualifyImpulse(40),"older pivot needs its full left neighborhood; no partial-history fallback");
    size_t count=logs.size();CaptureStructureContext();CaptureStructureContext();Check(logs.size()==count,"missing-context reason deduplicates independently of M5 readiness");
    TDateTime cutoff=structureCutoff;contextSize=260;Check(CaptureStructureContext()&&structureCutoff==cutoff,"missing history recovers only against original cutoff without demanding all 200");
    ResetQualification();contextSize=12;BeginQualification(39);Check(structureContextReady,"two relevant pivots suffice with fewer than 200 bars and without opposite pivots");

    ResetQualification();IntrfProcsRec.piHigh=NULL;BeginQualification(39);
    Check(!structureContextReady&&Logged("API unavailable")&&!TryQualifyImpulse(40),"missing indexed SDK capability blocks qualification clearly");
    IntrfProcsRec.piHigh=(void*)1;Check(CaptureStructureContext()&&structureCutoff==cutoff,"API recovery preserves origin cutoff");
    ResetQualification();contextSeries[6].c=HUGE_VAL;BeginQualification(39);Check(!structureContextReady,"invalid eligible OHLC blocks context");

    ResetQualification();StructureContextSeries(d,ClosedTime(38));
    // Only one pivot inside the eligible 200. Pivots beyond the bound cannot cure it.
    for(int i=3;i<203;i++){contextSeries[i].h=1.201;contextSeries[i].l=1.199;}
    contextSeries[5].h=d==1?1.207:1.201;contextSeries[5].l=d==1?1.199:1.193;
    contextSeries[210].h=1.22;contextSeries[210].l=1.18;BeginQualification(39);
    Check(!structureContextReady,"older out-of-scan structure cannot supply missing second pivot");
  }
  for(int mode:{0,1,2}){Fresh();ImpulseEntryMode=mode;int before=contextReadCount;StartImpulseAt(1,1.21,nbar,Time(1),1.20,nbar-2,Time(3),rmaATR,Time(1),"legacy");Check(before==contextReadCount,"modes 0-2 make no new M15 context reads");}
}
// Build completed history, then run production chronological qualification transitions.
void StructurePath(const std::vector<double> &closes,int d=1,double minimum=0.70,int maximum=12)
{
  Fresh();SettingsPreset=8;ApplySettingsPreset();EnableWeekendBlackout=false;
  StructureImpulseMinimumEfficiency=minimum;StructureImpulseMaximumBars=maximum;nbar=50;rmaATR=0.002;
  long origin=nbar-(long)closes.size()+1;StructureContextSeries(d,ClosedTime(origin),1.2006,1.2008);
  for(size_t i=0;i<closes.size();i++)
  {
    int shift=ClosedBarShift(origin+(long)i);double c=QPrice(d,closes[i]);
    series[shift]={c,c+0.0001,c-0.0001,c,series[shift].t};
  }
  long seed=origin+1;
  StartImpulseAt(d,d==1?ClosedHigh(seed):ClosedLow(seed),seed,ClosedTime(seed),1.20,origin,ClosedTime(origin),rmaATR,ClosedTime(seed),"quality path seed");
  // Preserve complete observed paths, with no historical retracement after endpoints.
  for(long bar=seed;bar<=nbar;bar++)Remember(bar,d==1?ClosedHigh(bar):ClosedLow(bar),d,d==1?ClosedHigh(bar):ClosedLow(bar));
  bidQuote=ClosedClose(nbar);askQuote=bidQuote+0.0002;
}
void StructureImpulseQuality()
{
  for(int d:{1,-1})
  {
    StructurePath({1.20,1.202,1.204,1.207},d);RecoverFirstTouchChronologically(qualifiedFirstSeedBar);
    Check(impulseQualified&&touchActive&&Near(structureEfficiency,1)&&structureDuration==3,"clean signed long/short close path qualifies via production transitions");
    double measured=structureEfficiency;long duration=structureDuration;QBar(d,1.209,1.21,1.2088,1.2095,1.2094);
    Check(impulseQualified&&structureEfficiency==measured&&structureDuration==duration,"qualification metrics latch while endpoints advance");
    StructurePath({1.20,1.205,1.201,1.207},d);RecoverFirstTouchChronologically(qualifiedFirstSeedBar);
    Check(!impulseQualified&&!touchActive&&structureEfficiency<0.70&&ImpulseDisplacement()>=0.006,"overlapping close path fails despite sufficient extreme displacement");
    // A valid initial directional seed precedes the negative signed close path.
    StructurePath({1.20,1.2001,1.198,1.197},d);Check(!TryQualifyImpulse(nbar)&&structureEfficiency<0,"signed numerator cannot turn wrong-direction movement into efficiency");
  }
  // Exact binary-representable efficiency boundary: net .125 / path .25 = .5.
  StructurePath({1.25,1.28125,1.21875,1.375},1,0.5);QualifiedImpulseMinimumDisplacementATR=50;RecoverFirstTouchChronologically(qualifiedFirstSeedBar);
  Check(impulseQualified&&structureEfficiency==0.5,"exact efficiency threshold is inclusive");
  StructurePath({1.25,1.28125,1.21875,1.375},1,0.500001);QualifiedImpulseMinimumDisplacementATR=50;RecoverFirstTouchChronologically(qualifiedFirstSeedBar);Check(!impulseQualified,"just below configured efficiency fails");
  std::vector<double> closes(13,1.20);for(int i=1;i<13;i++)closes[i]=1.20+i*0.00051;
  StructurePath(closes);RecoverFirstTouchChronologically(qualifiedFirstSeedBar);Check(impulseQualified&&structureDuration==12,"exact 12-bar duration qualifies");
  StructurePath(std::vector<double>(14,1.20));long boundary=consumedBar;RecoverFirstTouchChronologically(qualifiedFirstSeedBar);
  Check(!impulseQualified&&stage==0&&consumedBar==boundary&&!touchActive,"13-bar never-qualified replay expires without consumed-boundary lock");
  StructurePath({1.20,1.20,1.20});Check(!TryQualifyImpulse(nbar)&&structurePath==0,"zero close path rejected");
  StructurePath({1.20,1.203,1.207});series[2].c=0;Check(!TryQualifyImpulse(nbar)&&Logged("missing or invalid M5 close path"),"missing participating close is not replaced");
  StructurePath({1.20,1.203,1.207});series[2].c=HUGE_VAL;Check(!TryQualifyImpulse(nbar),"nonfinite path rejected");
  StructurePath({1.20,1.203,1.207});historyBars=2;Check(!TryQualifyImpulse(nbar),"unavailable path history rejected");
  StructurePath({1.20,1.202,1.2001}); // sufficient wick first, breakout close later but no early conjunction
  series[2].c=1.2001;series[2].h=1.208;setupExtreme=1.208;Check(!TryQualifyImpulse(nbar-1),"M15/local close and displacement must hold on actual same bar");
  series[1].c=1.207;Check(TryQualifyImpulse(nbar)&&qualifyingBar==nbar,"later conjunction records later qualifying bar, never earlier wick bar");
  StructurePath({1.20,1.203,1.207});StructureImpulseMaximumBars=1;ProcessFirstTouchBar(High(1),Low(1),Close(1));Check(stage==0&&consumedBar==-1,"live pending expiry releases candidate");
  haveLatestSwingLow=true;latestSwingLow=1.20;latestSwingLowBar=nbar-1;latestSwingLowTime=Time(2);StructureImpulseMaximumBars=12;
  ProcessImpulseSetup(1.2000,1.203,1.1999,1.2025,1.20);Check(activeImpulseSetup&&impulseOriginBar==nbar-1&&consumedBar==-1,"fresh causal leg can follow expired candidate");
  for(int bad=0;bad<7;bad++)
  {
    StructurePath({1.20,1.203,1.207});
    if(bad==0)StructureContextPivotStrength=0;if(bad==1)StructureContextPivotStrength=100;
    if(bad==2)StructureImpulseMinimumEfficiency=0;if(bad==3)StructureImpulseMinimumEfficiency=1.01;
    if(bad==4)StructureImpulseMinimumEfficiency=HUGE_VAL;if(bad==5)StructureImpulseMaximumBars=0;if(bad==6)StructureImpulseMaximumBars=31;
    qualifiedConfigValid=ValidateQualifiedSettings();Check(!qualifiedConfigValid&&!TryQualifyImpulse(nbar),"invalid mode-3 control blocks without clamping");
    for(int mode:{0,1,2}){ImpulseEntryMode=mode;Check(ValidateQualifiedSettings(),"legacy modes ignore new invalid controls");}
  }
  // Resolve invalid late Custom inputs exactly once.
  StructurePath({1.20,1.203,1.207});StructureImpulseMinimumEfficiency=0;SettingsPreset=0;
  for(int i=0;i<runOptionCount;i++){RunOption &v=runOptions[i];if(v.type==ot_Double)v.custom.number=*(double*)v.effective;else if(v.type==ot_Boolean)v.custom.boolean=*(bool*)v.effective;else v.custom.integer=*(int*)v.effective;}
  ResetStrategy();logs.clear();GetSingleTick();GetSingleTick();int errors=0;for(auto &line:logs)if(line.find("configuration error")!=std::string::npos)errors++;
  Check(errors==1&&!qualifiedConfigValid&&attempts==0,"one clear mode-3 configuration error per run");
}
void CloseStructureBar(int d,double o,double h,double l,double c)
{
  double t=series[0].t;Bar closed={QPrice(d,o),QPrice(d,d==1?h:l),QPrice(d,d==1?l:h),QPrice(d,c),t};
  // Observe the actual endpoint then closing quote in its live bar.
  Quote(d==1?closed.h:closed.l,(d==1?closed.h:closed.l)+0.0002);
  Quote(closed.c,closed.c+0.0002);
  for(int i=511;i>0;i--)series[i]=series[i-1];series[1]=closed;
  series[0]={closed.c,closed.c+0.0001,closed.c-0.0001,closed.c,t+5.0/1440};now=series[0].t;
  bidQuote=closed.c;askQuote=closed.c+0.0002;GetSingleTick();
}
void StructureCallbackSetup(int d,bool singleDirection=false)
{
  Fresh();ResetStrategy();SettingsPreset=singleDirection?0:8;contextBound=false;logs.clear();
  if(singleDirection)
    for(int i=0;i<runOptionCount;i++){RunOption &v=runOptions[i];if(v.type==ot_Double)v.custom.number=*(double*)v.effective;else if(v.type==ot_Boolean)v.custom.boolean=*(bool*)v.effective;else v.custom.integer=*(int*)v.effective;}
  if(singleDirection)
    for(int i=0;i<runOptionCount;i++)
    {
      RunOption &v=runOptions[i];if(v.effective==&ImpulseEntryMode)v.custom.integer=3;if(v.effective==&TradeDirection)v.custom.integer=1;
      if(v.effective==&RegimeLookback)v.custom.integer=200;if(v.effective==&StructureImpulseMinimumEfficiency)v.custom.number=0.70;
      if(v.effective==&StructureImpulseMaximumBars)v.custom.integer=12;if(v.effective==&StructureContextPivotStrength)v.custom.integer=2;
    }
  now=46000.5;for(int i=0;i<512;i++)series[i]={1.20,1.2002,1.1998,1.20,now-i*5.0/1440};
  bidQuote=1.20;askQuote=1.2002;attempts=buyAttempts=sellAttempts=0;busy=false;
  for(int i=0;i<220;i++)CloseStructureBar(d,1.20,1.2002,1.1998,1.20);
  Check(startupContextValidated&&startupHistoryReady&&atrPctCount>=200&&nbar>=200,"mode 3 builds real callback binding/history/risk warmup");
  StructureContextSeries(d,series[0].t-2*5.0/1440,1.2006,1.2008);
  // The third older pivot makes target structure irrelevant to the frozen last two gate.
  CloseStructureBar(d,1.20,1.2010,1.1999,1.2005);
  Check(activeImpulseSetup&&structureContextReady&&!impulseQualified&&!touchActive,"callbacks derive confirmed origin, M15 snapshot and pending parent");
  CloseStructureBar(d,1.2005,1.2016,1.20115,1.2013);
  Check(impulseQualified&&touchActive&&qualifyingBar==nbar&&structureEfficiency>=0.70&&attempts==0,"callbacks derive actual qualification and prospective arming without arm-tick order");
}
void StructureFirstTouch()
{
  for(int d:{1,-1})
  {
    std::vector<std::string> prefix;double entry=0;
    for(int future:{0,1})
    {
      StructureCallbackSetup(d);double level=touchLevel;TDateTime cutoff=structureCutoff;
      Touch(d);Check(attempts==1&&stage==IMPULSE_CONSUMED&&orderType==(d==1?op_Buy:op_Sell),"allowed long reversal/short continuation executes future first-touch quote");
      if(!future){prefix=logs;entry=orderQuote;}else Check(prefix==logs&&entry==orderQuote,"identical information through entry yields identical decisions with different future continuations");
      Touch(d);Check(attempts==1&&structureCutoff==cutoff,"one order per parent");
      CloseStructureBar(d,1.2013,1.202+future*0.001,1.2012,1.2015);
      ResetStrategy();Check(!structureContextReady&&structureCutoff==0&&structureBarrier==0&&structureDuration==0&&structurePath==0&&!impulseQualified&&!touchActive&&structureReferences[0].center==0&&lastStructureGateReason[0]==0,"reset clears every context/metric/readiness state");
    }
  }
  StructureCallbackSetup(-1,true);Touch(-1);
  Check(attempts==0&&stage==IMPULSE_CONSUMED&&consumedDirection==-1&&Logged("direction blocked at first touch"),"Custom buys-only still observes, qualifies and consumes opposite short");
  // Recovery sees only old M15 structure and the close path of each replayed bar.
  StructurePath({1.20,1.202,1.204,1.207,1.2075});long first=qualifiedFirstSeedBar;
  contextSeries[1].h=50;contextSeries[2].h=50; // later, unfinished/inside-impulse data
  RecoverFirstTouchChronologically(first);
  Check(impulseQualified&&qualifyingBar==nbar-1&&structureDuration==3&&Near(structureEfficiency,1)&&touchActive,"chronological recovery freezes actual earlier qualifying metrics without later close/context look-ahead");
  double oldBarrier=structureBarrier;Check(oldBarrier<2&&structureReferences[0].confirmation<=structureCutoff,"recovery references remain known before origin");
  StructurePath({1.20,1.205,1.201,1.207,1.23});first=qualifiedFirstSeedBar;
  RecoverFirstTouchChronologically(first);
  Check(impulseQualified&&qualifyingBar==nbar&&structureDuration==4,"later efficient endpoint cannot make earlier overlapping replay bar qualify");
}

// Illustrative prices only: synthetic production admission, no historical replay.
void InvalidOppositeAttempt(int d)
{
  Fresh();rmaATR=0.002;ImpulseMaximumBodyATR=1.5;
  ObserveProvisional(d,QPrice(d,1.086),2,1.76,2.21,false,"confirmed directional origin unavailable");
  nbar++;ObserveProvisionalContinuation(d,QPrice(d,1.087),false);nbar++;
  if(d==1){haveLatestSwingHigh=true;latestSwingHigh=1.08106;latestSwingHighBar=nbar-1;latestSwingHighTime=Time(2);}
  else{haveLatestSwingLow=true;latestSwingLow=QPrice(d,1.08106);latestSwingLowBar=nbar-1;latestSwingLowTime=Time(2);}
  ProcessImpulseSetup(QPrice(d,1.0853),QPrice(d,d==1?1.0855:1.08253),QPrice(d,d==1?1.08253:1.0855),QPrice(d,1.0828),QPrice(d,1.0853));
  auto &candidate=CandidateFor(d);
  Check(candidate.active&&candidate.firstBar==10&&Near(candidate.seedATR,0.002)&&Near(candidate.firstExtreme,QPrice(d,1.086))&&Near(candidate.extreme,QPrice(d,1.087))&&candidate.oversizedSeen&&candidate.continuationSeen&&!candidate.normalSeedSeen,"invalid opposite seed preserves independent candidate and all evidence");
  Check(!activeImpulseSetup&&attempts==0,"invalid opposite geometry cannot create a parent/order");
}

void SetOrigin(int d,double price,long bar)
{
  if(d==1){haveLatestSwingLow=true;latestSwingLow=price;latestSwingLowBar=bar;latestSwingLowTime=ClosedTime(bar);}
  else{haveLatestSwingHigh=true;latestSwingHigh=price;latestSwingHighBar=bar;latestSwingHighTime=ClosedTime(bar);}
}
void CandidateState(int d=1)
{
  Fresh();rmaATR=0.002;ImpulseMaximumBodyATR=1.5;
  ObserveProvisional(d,QPrice(d,1.205),2,1.76,2.21,false,"confirmed directional origin unavailable");
  SetOrigin(d,1.20,nbar-1);
}
int PromotionCount(){int count=0;for(auto &line:logs)if(line.find("RBP promotion ")==0)count++;return count;}
void CandidateGeometryPreservation()
{
  for(int d:{1,-1})InvalidOppositeAttempt(d);
  for(int d:{1,-1})for(int mode:{0,1,2,3})for(double extreme:{QPrice(d,1.199),1.20,HUGE_VAL,-HUGE_VAL,nan("")})
  {
    Fresh();ImpulseEntryMode=mode;SetOrigin(d,1.20,nbar-1);
    double p=0;long b=-1;TDateTime t=0;const char *reason="";
    Check(!CanSeedImpulse(d,extreme,p,b,t,reason)&&strcmp(reason,"invalid directional geometry")==0,"normal admission rejects negative/equal/nonfinite geometry in every impulse mode");
    Parent(d); // Existing live touch must survive a defensive boundary rejection.
    ObserveProvisional(-d,QPrice(-d,1.205),2,1.76,2.21,false,"pending");
    auto saved=CandidateFor(-d);double atr=setupATR,endpoint=setupExtreme,origin=impulseOrigin,level=touchLevel;long first=qualifiedFirstSeedBar;int oldStage=stage;bool armed=touchActive,qualified=impulseQualified;
    Check(!StartImpulseAt(d,extreme,nbar,Time(1),1.20,nbar-1,Time(2),rmaATR,Time(1),"invalid replacement"),"defensive boundary propagates failure");
    Check(!StartImpulse(d,extreme,1.20,nbar-1,Time(2),"invalid caller"),"wrapper propagates failure without arming or qualifying stale parent");
    Check(stage==oldStage&&setupATR==atr&&setupExtreme==endpoint&&impulseOrigin==origin&&touchActive==armed&&touchLevel==level&&qualifiedFirstSeedBar==first&&impulseQualified==qualified&&memcmp(&saved,&CandidateFor(-d),sizeof(saved))==0,"invalid boundary leaves parent, touch, qualification and independent candidate unchanged");
    Check(attempts==0,"invalid boundary never orders");
  }
  for(int d:{1,-1})for(double origin:{0.0,-1.0,HUGE_VAL,nan("")})
  {
    Fresh();Check(!StartImpulseAt(d,QPrice(d,1.205),nbar,Time(1),origin,1,Time(10),rmaATR,Time(1),"bad origin")&&!activeImpulseSetup,"nonpositive/nonfinite origin rejected without parent mutation");
  }
  Fresh();Check(!StartImpulseAt(0,1.205,nbar,Time(1),1.20,1,Time(10),rmaATR,Time(1),"bad direction")&&!activeImpulseSetup,"invalid direction rejected");
  for(int d:{1,-1})
  {
    // Exercise ProcessImpulseSetup's newer-origin preemption with an actual pending parent.
    QualifiedFresh(d);long first=qualifiedFirstSeedBar,originBar=impulseOriginBar;double endpoint=setupExtreme;
    nbar+=2;SetOrigin(-d,QPrice(d,1.199),nbar-1);
    ProcessImpulseSetup(QPrice(d,1.2028),QPrice(d,d==1?1.203:1.200),QPrice(d,d==1?1.200:1.203),QPrice(d,1.2003),QPrice(d,1.203));
    Check(activeImpulseSetup&&dir==d&&qualifiedFirstSeedBar==first&&impulseOriginBar==originBar&&setupExtreme==endpoint&&!impulseQualified&&attempts==0,"invalid newer opposite origin cannot preempt pending parent");
    CandidateState(d);auto &candidate=CandidateFor(d);candidate.normalSeedSeen=true;candidate.extreme=QPrice(d,1.199);
    Check(!TryPromoteCandidate(candidate)&&candidate.active&&!activeImpulseSetup&&qualifiedFirstSeedBar==-1,"invalid provisional geometry never writes qualification metadata or recovers stale parent");
    // A cumulative endpoint may be valid while the initial recovered endpoint is invalid.
    CandidateState(d);candidate.normalSeedSeen=true;candidate.extreme=QPrice(d,1.21);
    for(int i=1;i<4;i++){series[i].h=QPrice(d,d==1?1.199:1.198);series[i].l=QPrice(d,d==1?1.198:1.199);}
    Check(!TryPromoteCandidate(candidate)&&!activeImpulseSetup&&candidate.active&&Logged("invalid directional geometry at recovered initial endpoint"),"reconstructed endpoint admission rejection is propagated without partial promotion");
    CandidateState(d);nbar+=31;Check(!TryPromoteCandidate(CandidateFor(d))&&!CandidateFor(d).active,"real expiry retires candidate");
    CandidateState(d);SetOrigin(d,1.20,nbar);nbar++;
    Check(RetireCandidateBeforeCurrentSeed(d,true,nbar-1)==0,"origin at first seed stays compatible");
    SetOrigin(d,1.20,nbar);nbar++;
    Check(RetireCandidateBeforeCurrentSeed(d,true,nbar-1)==2&&!CandidateFor(d).active,"genuinely incompatible newer origin retires candidate");
  }
}
// A retained oversized seed, sub-minimum continuation, rejected opposite attempt,
// then newly confirmed valid origin reach the existing mode-3 qualification path.
void OversizedFuturePath(int d)
{
  Fresh();SettingsPreset=8;ApplySettingsPreset();EnableWeekendBlackout=false;nbar=40;rmaATR=0.002;RegimeLookback=1;
  StructureContextSeries(d,ClosedTime(38),1.2006,1.2008);
  for(int i=3;i<30;i++){series[i].h=1.2002;series[i].l=1.1998;series[i].c=1.20;}
  nbar=39; // First oversized seed observed before a causal origin is available.
  ObserveProvisional(d,QPrice(d,1.204),2,1.76,2.21,false,"confirmed directional origin unavailable");
  nbar=40;ObserveProvisionalContinuation(d,QPrice(d,1.208),false);
  series[3].c=1.20;series[2]={QPrice(d,1.2005),QPrice(d,d==1?1.204:1.200),QPrice(d,d==1?1.200:1.204),QPrice(d,1.203),series[2].t};
  series[1]={QPrice(d,1.207),QPrice(d,d==1?1.208:1.2068),QPrice(d,d==1?1.2068:1.208),QPrice(d,1.2075),series[1].t};
  Remember(39,QPrice(d,1.204),d,QPrice(d,1.2038));Remember(40,QPrice(d,1.208),d,QPrice(d,1.2078));
  SetOrigin(-d,QPrice(d,1.19),38);
  ProcessImpulseSetup(QPrice(d,1.206),QPrice(d,d==1?1.2062:1.203),QPrice(d,d==1?1.203:1.2062),QPrice(d,1.2035),QPrice(d,1.206));
  Check(CandidateFor(d).active&&CandidateFor(d).continuationSeen&&!CandidateFor(d).normalSeedSeen&&attempts==0,"invalid opposite attempt preserves oversized-only continuation evidence");
  SetOrigin(d,1.20,38);bidQuote=QPrice(d,1.2075);askQuote=bidQuote+0.0002;
  // Rejecting the incoming short must still evaluate the other candidate now eligible.
  ProcessImpulseSetup(QPrice(d,1.206),QPrice(d,d==1?1.2062:1.203),QPrice(d,d==1?1.203:1.2062),QPrice(d,1.2035),QPrice(d,1.206));
  Check(impulseQualified&&touchActive&&dir==d&&qualifiedFirstSeedBar==39&&Near(setupATR,0.002)&&structureEfficiency>=0.70&&attempts==0,"oversized-only continuation promotes and qualifies without normal seed or arming order");
  Check(!provisionalLong.active&&!provisionalShort.active&&Logged("RBP promotion promoted")&&Logged("later same-direction endpoint advancement"),"accepted parent retires candidates and exposes successful evidence path");
  Touch(d);Check(attempts==1&&stage==IMPULSE_CONSUMED,"only prospective future first touch executes");Touch(d);Check(attempts==1,"consumed parent never retries");
  double p=0;long b=-1;TDateTime t=0;const char *reason="";
  Check(!CanSeedImpulse(d,QPrice(d,1.22),p,b,t,reason)&&strstr(reason,"not newer than consumed"),"consumed fresh-origin requirement remains effective");
}
void PromotionGateDiagnostics()
{
  Fresh();int count=PromotionCount();TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count,"inactive candidates emit nothing");
  CandidateState();haveLatestSwingLow=false;Check(!TryPromoteCandidate(provisionalLong)&&Logged("reason=confirmed directional origin unavailable")&&Logged("originAvailable=no"),"missing origin retains actual reason");
  count=PromotionCount();nbar++;TryPromoteCandidate(provisionalLong);TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count,"identical attempts across bars deduplicate");
  SetOrigin(1,1.20,nbar);TryPromoteCandidate(provisionalLong);Check(Logged("confirmed origin is not before expansion")&&PromotionCount()==count+1,"changed origin and denial are visible");
  CandidateState();stage=IMPULSE_CONSUMED;consumedDirection=-1;consumedBar=9;consumedExtreme=1.19;
  TryPromoteCandidate(provisionalLong);Check(Logged("reason=confirmed origin is not newer than consumed parent")&&Logged("consumedDir=-1 consumedBar=9 consumedExtreme=1.19000"),"consumed origin block remains blocked and references consumption");
  count=PromotionCount();consumedExtreme=1.18;TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count+1,"changed consumed reference visible with same reason");
  CandidateState();stage=IMPULSE_CONSUMED;consumedDirection=1;consumedBar=8;consumedExtreme=1.21;
  TryPromoteCandidate(provisionalLong);Check(Logged("reason=same-direction expansion did not exceed consumed parent")&&provisionalLong.active&&!touchActive,"consumed extreme block retains legitimate failure");
  CandidateState();SetOrigin(1,1.206,9);TryPromoteCandidate(provisionalLong);Check(Logged("invalid directional geometry")&&Logged("signedDisplacement=-0.00100")&&provisionalLong.active,"invalid geometry reported with signed values");
  CandidateState();provisionalLong.firstBar=8;TryPromoteCandidate(provisionalLong);Check(Logged("latest confirmed origin after candidate first seed")&&provisionalLong.active,"incompatible origin waits until genuine current-seed retirement");
  CandidateState();stage=IMPULSE_PARENT;dir=-1;impulseOriginBar=7;impulseOrigin=1.21;qualifiedFirstSeedBar=8;setupExtreme=1.19;
  TryPromoteCandidate(provisionalLong);Check(Logged("active parent owns setup")&&Logged("ownerDir=-1 ownerStage=4 ownerOriginBar=7")&&provisionalLong.active,"owner retains candidate and identifies reference");
  count=PromotionCount();TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count,"owner denial deduplicates");setupExtreme=1.18;TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count+1,"changed owner reference visible");
  CandidateState();nbar+=31;TryPromoteCandidate(provisionalLong);Check(Logged("RBP promotion retired")&&Logged("origin recovery window expired")&&!provisionalLong.active&&provisionalLong.lastPromotion[0]==0,"expiry is actual retirement and clears diagnostic state");
  CandidateState();SettingsPreset=8;ApplySettingsPreset();nbar=30;provisionalLong.firstBar=20;SetOrigin(1,1.20,7);
  TryPromoteCandidate(provisionalLong);Check(Logged("RBP promotion retired")&&Logged("first seed beyond origin qualification deadline")&&!provisionalLong.active,"mode-3 deadline is retirement");
  CandidateState();TryPromoteCandidate(provisionalLong);Check(Logged("no later normal seed or same-direction endpoint advancement")&&Logged("normal=no oversized=yes continuation=no")&&provisionalLong.active,"oversized evidence wait separate from eligibility");
  count=PromotionCount();TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count,"evidence wait deduplicates");
  for(double atr:{0.0,-0.002,HUGE_VAL,nan("")}){CandidateState();provisionalLong.normalSeedSeen=true;provisionalLong.seedATR=atr;TryPromoteCandidate(provisionalLong);Check(Logged("RBP promotion rejected")&&Logged("invalid seed ATR")&&provisionalLong.active&&!activeImpulseSetup,"invalid seed ATR rejected with retained candidate");}
  CandidateState();provisionalLong.normalSeedSeen=true;provisionalLong.extreme=1.201;TryPromoteCandidate(provisionalLong);
  Check(Logged("insufficient cumulative displacement")&&Logged("signedDisplacement=0.00100 requiredDisplacement=0.00250")&&Logged("firstBar=10 firstTime=")&&Logged("seedATR=0.00200")&&Logged("originBar=9 originTime="),"displacement failure identifies candidate, times, origin and requirement");
  count=PromotionCount();provisionalLong.extreme=1.2011;TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count+1,"changed endpoint visible despite same failure");
  ShowDiagnostics=false;auto saved=provisionalLong;Check(!TryPromoteCandidate(provisionalLong)&&memcmp(&saved,&provisionalLong,sizeof(saved))==0&&!activeImpulseSetup&&attempts==0,"disabled diagnostics do not mutate eligibility or decisions");
  ShowDiagnostics=true;ResetProvisionalCandidates();ObserveProvisional(1,1.2011,1,1.1,1.3,false,"pending");count=PromotionCount();TryPromoteCandidate(provisionalLong);Check(PromotionCount()==count+1,"replacement resets event deduplication");
  ResetStrategy();Check(provisionalLong.lastPromotion[0]==0&&provisionalShort.lastPromotion[0]==0,"strategy reset clears promotion identities");
  for(int d:{1,-1})OversizedFuturePath(d);
}


int main(int argc,char **argv)
{
  if(argc!=2){fprintf(stderr,"One named case required\n");return 2;}Hooks();InitStrategy();ReplaceStr(Currency,(PChar)"EURUSD");
  std::string name=argv[1];
  if(name=="candidate_geometry_preservation")CandidateGeometryPreservation();
  else if(name=="promotion_gate_diagnostics")PromotionGateDiagnostics();
  else if(name=="structure_context_gate")StructureContextGate();
  else if(name=="structure_impulse_quality")StructureImpulseQuality();
  else if(name=="structure_first_touch")StructureFirstTouch();
  else if(name=="cold_start_context")ColdStartContext();
  else if(name=="qualified_impulse_gate")QualifiedImpulseGate();
  else if(name=="qualified_pullback_touch")QualifiedPullbackTouch();
  else if(name=="qualified_parent_lifecycle")QualifiedParentLifecycle();
  else if(name=="touch_intrabar")Intrabar();else if(name=="executable_quotes_gap_rounding")QuotesGap();
  else if(name=="observed_tick_activation")ObservedActivation();
  else if(name=="activation_and_passed")ActivationPassed();else if(name=="recovered_touch")Recover();
  else if(name=="endpoint_refresh_ordering")RefreshOrdering();else if(name=="touch_gates")Gates();
  else if(name=="order_failure")OrderFailure();else if(name=="frozen_brackets")Brackets();
  else if(name=="legacy_confirmation_entries")LegacyEntries();else if(name=="parent_ownership")Ownership();
  else if(name=="direction_filtered_lifecycle")DirectionFilteredLifecycle();
  else if(name=="trade_limit_stop")TradeLimitStop();else if(name=="trade_limit_history")TradeLimitHistory();else if(name=="trade_limit_controls")TradeLimitControls();
  else{fprintf(stderr,"Unknown case: %s\n",argv[1]);return 2;}
  DoneStrategy();printf("PASS: %s\n",argv[1]);
}
