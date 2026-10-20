// Focused lifecycle regression: compile as a 32-bit Release console executable
// with the installed FT6 C++ include path and oleaut32.lib. No history or trades.
#include <map>
#include <string>
#include <vector>
#include "../forex-tester-6/RangeBreakoutPullbackStrategy/RangeBreakoutPullbackStrategy.cpp"

struct Registered { int type; void *value; };
std::map<std::string,Registered> options;
std::vector<std::string> messages;
int selectedTimeframe=0;
void Check(bool condition,const char *reason)
{
  if(!condition){fprintf(stderr,"FAIL: %s\n",reason);exit(1);}
}
void __stdcall Register(void*,PChar name,int type,void *value)
{
  if(type!=ot_Separator) options[name]=Registered{type,value};
}
void __stdcall IgnoreText(void*,PChar) {}
void __stdcall IgnoreOptionValue(void*,PChar,PChar) {}
void __stdcall Capture(void*,PChar text){messages.push_back(text);}
PChar __stdcall TestSymbol(void*){return (PChar)"EURUSD";}
bool __stdcall SelectTimeframe(void*,PChar,int timeframe){selectedTimeframe=timeframe;return true;}
unsigned long __stdcall NoHistory(void*){return 0;}
void *Option(const char *prefix)
{
  for(auto &entry:options) if(entry.first.find(prefix)==0) return entry.second.value;
  Check(false,"registered option missing");return NULL;
}
std::vector<double> Values(bool custom)
{
  std::vector<double> result;
  for(int i=0;i<runOptionCount;i++)
  {
    RunOption &option=runOptions[i];
    if(option.type==ot_Double) result.push_back(custom?option.custom.number:*(double*)option.effective);
    else if(option.type==ot_Boolean) result.push_back(custom?option.custom.boolean:*(bool*)option.effective);
    else result.push_back(custom?option.custom.integer:*(int*)option.effective);
  }
  return result;
}
void TickOnce(const char *prefix,size_t expectedMessages)
{
  GetSingleTick();
  Check(messages.size()==expectedMessages,"exactly one diagnostic per run");
  Check(messages.back().find(prefix)==0,"effective preset diagnostic prefix");
  GetSingleTick();GetSingleTick();
  Check(messages.size()==expectedMessages,"subsequent ticks must not resolve/log again");
  Check(nbar==0,"no historical bars processed");
}
int main()
{
  IntrfProcsRec.RegOption=Register;
  IntrfProcsRec.StrategyShortName=IgnoreText;
  IntrfProcsRec.StrategyDescription=IgnoreText;
  IntrfProcsRec.AddOptionValue=IgnoreOptionValue;
  IntrfProcsRec.Print=Capture;
  IntrfProcsRec.Symbol=TestSymbol;
  IntrfProcsRec.SetCurrencyAndTimeframe=SelectTimeframe;
  IntrfProcsRec.Bars=NoHistory;
  InitStrategy();
  Check(messages.empty(),"DLL registration must not emit a stale diagnostic");
  Check(runOptionCount==51&&options.size()==53,"all detailed options registered separately");
  ReplaceStr(Currency,(PChar)"EURUSD");

  // Simulate FT6 supplying current project/dialog values AFTER ResetStrategy.
  ResetStrategy();
  for(int i=0;i<runOptionCount;i++)
  {
    RunOption &option=runOptions[i];
    if(option.type==ot_Double) option.custom.number+=0.25;
    else if(option.type==ot_Boolean) option.custom.boolean=!option.custom.boolean;
    else option.custom.integer+=1;
  }
  *(int*)Option("Setup mode")=0;
  *(int*)Option("Confirmation (")=2;
  *(int*)Option("Depth mode")=3;
  *(double*)Option("Custom minimum depth")=20;
  *(double*)Option("Custom maximum depth")=70;
  *(double*)Option("Impulse minimum body ATR")=0.75;
  *(double*)Option("Impulse maximum body ATR")=2.25;
  *(double*)Option("With-trend / neutral target R")=4;
  *(double*)Option("Counter-trend target R")=5;
  auto custom=Values(true);
  TickOnce("RBP preset=Custom setup=0 confirmation=2 direction=1 body=0.75-2.25ATR",1);
  Check(Values(false)==custom,"Custom must use every detailed value unchanged");
  Check(messages.back().find("depth=20-70% depthMode=3")!=std::string::npos,"Custom diagnostic uses selected depth settings");
  Check(selectedTimeframe==*(int*)Option("Timeframe"),"Custom timeframe used on first tick");

  ResetStrategy();
  Check(messages.size()==1,"reset must defer until options are current");
  *(int*)Option("Settings preset")=1;
  TickOnce("RBP preset=Baseline setup=1 confirmation=0 direction=0 body=1.00-1.50ATR",2);
  Check(PullbackDepthMode==1&&ShallowMinimumDepth==0&&ShallowMaximumDepth==50,"Baseline shallow depth");
  Check(StopModeSetting==0&&WithTrendNeutralTargetR==2&&CounterTrendTargetR==3,"Baseline adaptive stop and exact targets");
  Check(MinimumOpposingCloses==2&&TradeDirection==0&&ImpulseM15H4TrendFilter==0,"Baseline opposing closes and EMA off");
  Check(ImpulseMinimumRangeATR==1.25&&ImpulseMinimumRetracementATR==0.50&&ImpulseMaximumPullbackBars==30,"Baseline impulse defaults");
  Check(selectedTimeframe==PERIOD_M15,"preset timeframe resolved before API use");
  Check(Values(true)==custom,"Baseline must preserve all registered Custom values");
  auto baseline=Values(false);

  ResetStrategy();
  *(int*)Option("Settings preset")=2;
  TickOnce("RBP preset=Trend-filter test setup=1 confirmation=0 direction=0 body=1.00-1.50ATR",3);
  Check(TradeDirection==0&&ImpulseM15H4TrendFilter==1&&messages.back().find("EMAtrend=on")!=std::string::npos,"Trend-filter diagnostic enabled");
  ImpulseM15H4TrendFilter=0;
  Check(Values(false)==baseline,"Trend-filter preset differs ONLY by EMA enable");
  Check(Values(true)==custom,"Trend-filter preset preserves Custom values");

  ResetStrategy();
  *(int*)Option("Settings preset")=3;
  TickOnce("RBP preset=Baseline buys-only setup=1 confirmation=0 direction=1 body=1.00-1.50ATR depth=0-50% depthMode=1 stop=adaptive targets=2.0/3.0R EMAtrend=off scope=0",4);
  Check(TradeDirection==1,"buys-only effective direction");
  TradeDirection=0;
  Check(Values(false)==baseline,"buys-only preset differs ONLY by trade direction");
  TradeDirection=1;
  Check(Values(true)==custom,"buys-only preset preserves all Custom values");

  ResetStrategy();
  *(int*)Option("Settings preset")=0;
  TickOnce("RBP preset=Custom setup=0 confirmation=2 direction=1 body=0.75-2.25ATR",5);
  Check(Values(false)==custom,"return to Custom restores every detailed value");

  // Changed values during a run take effect on the next run, once.
  *(double*)Option("With-trend / neutral target R")=0.5;
  *(double*)Option("Counter-trend target R")=1;
  GetSingleTick();
  Check(WithTrendNeutralTargetR==4&&CounterTrendTargetR==5&&messages.size()==5,"no repeated preset resolution on ticks");
  ResetStrategy();
  TickOnce("RBP preset=Custom",6);
  Check(WithTrendNeutralTargetR==2&&CounterTrendTargetR==3,"existing Custom target floors retained");
  Check(*(double*)Option("With-trend / neutral target R")==0.5&&*(double*)Option("Counter-trend target R")==1,"clamping does not overwrite Custom inputs");
  ResetStrategy();ResetStrategy();
  TickOnce("RBP preset=Custom",7);
  DoneStrategy();
  puts("PASS: preset lifecycle, late options, one diagnostic/run, all Custom values, targets, EMA and buys-only isolation");
}
