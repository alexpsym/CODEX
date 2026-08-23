#property strict
#property description "Feed last-closed-candle ATR percentages for selected Market Watch forex symbols to a separate FILE_COMMON JSON file and optionally launch the separate desktop window. Display-only; no trading."
#property version   "1.00"

#import "shell32.dll"
long ShellExecuteW(long hwnd, string operation, string file, string parameters, string directory, int show_cmd);
#import

#import "kernel32.dll"
uint GetFileAttributesW(string file_name);
#import

input group "ATR Feed"
input int    ATRLength          = 14;
input int    UpdateIntervalMs   = 500;
input int    SymbolsPerTimer    = 4;
input string ExportFileName     = "MarketWatchATRPercentFeed.json";

input group "Desktop Window"
input bool   LaunchDesktopWindow      = true;
input string PythonExecutable         = "C:\\Users\\User\\miniconda3\\python.exe";
input string DesktopWindowScriptPath  = "C:\\GPT\\CODEX-master\\mt5-clone\\atr_percent_window.py";
input int    DesktopWindowRefreshMs   = 500;
input int    DesktopWindowDecimals    = 5;
input int    DesktopWindowTopN        = 10;
input string DesktopWindowRankFrame   = "1m";

const string DEFAULT_EXPORT_FILE = "MarketWatchATRPercentFeed.json";
const string NORMAL_DESKTOP_SCRIPT_PATH = "C:\\GPT\\CODEX-master\\mt5-clone\\atr_percent_window.py";
const int    SW_SHOWNORMAL = 1;
const uint   INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF;
const uint   FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
const int    FRAME_COUNT = 6;

ENUM_TIMEFRAMES FRAME_PERIODS[6] =
{
   PERIOD_M1, PERIOD_M5, PERIOD_H1, PERIOD_D1, PERIOD_W1, PERIOD_MN1
};
string FRAME_KEYS[6] =
{
   "m1", "m5", "h1", "d1", "w1", "mn1"
};

string   g_symbols[];
bool     g_is_forex[];
string   g_symbol_status[];
string   g_symbol_reason[];
int      g_handles[];
datetime g_last_closed_bars[];
double   g_atr_percent[];
bool     g_value_valid[];
bool     g_value_stale[];
string   g_frame_status[];
int      g_next_symbol = 0;
string   g_market_watch_signature = "";
datetime g_last_successful_refresh = 0;

int SafeATRLength()
{
   if(ATRLength < 2)
      return 2;
   if(ATRLength > 100)
      return 100;
   return ATRLength;
}

int SafeUpdateInterval()
{
   if(UpdateIntervalMs < 100)
      return 100;
   if(UpdateIntervalMs > 60000)
      return 60000;
   return UpdateIntervalMs;
}

int SafeSymbolsPerTimer()
{
   if(SymbolsPerTimer < 1)
      return 1;
   if(SymbolsPerTimer > 50)
      return 50;
   return SymbolsPerTimer;
}

int SafeDesktopWindowRefreshMs()
{
   if(DesktopWindowRefreshMs < 100)
      return 100;
   if(DesktopWindowRefreshMs > 5000)
      return 5000;
   return DesktopWindowRefreshMs;
}

int SafeDesktopWindowDecimals()
{
   if(DesktopWindowDecimals < 0)
      return 0;
   if(DesktopWindowDecimals > 8)
      return 8;
   return DesktopWindowDecimals;
}

int SafeDesktopWindowTopN()
{
   if(DesktopWindowTopN < 1)
      return 1;
   if(DesktopWindowTopN > 100)
      return 100;
   return DesktopWindowTopN;
}

string SafeRankFrame()
{
   string value = DesktopWindowRankFrame;
   StringTrimLeft(value);
   StringTrimRight(value);
   if(value == "1m" || value == "5m" || value == "1h" || value == "1D" || value == "1W" || value == "1Mo")
      return value;
   return "1m";
}

string SafeExportFileName()
{
   string name = ExportFileName;
   StringTrimLeft(name);
   StringTrimRight(name);
   if(name == "")
      return DEFAULT_EXPORT_FILE;
   return name;
}

string Trimmed(const string value)
{
   string text = value;
   StringTrimLeft(text);
   StringTrimRight(text);
   return text;
}

string QuoteArg(const string value)
{
   string escaped = value;
   StringReplace(escaped, "\"", "\\\"");
   return "\"" + escaped + "\"";
}

string DirectoryName(const string path)
{
   string normalized = path;
   StringReplace(normalized, "/", "\\");
   int last = -1;
   for(int i = StringLen(normalized) - 1; i >= 0; i--)
   {
      if(StringGetCharacter(normalized, i) == '\\')
      {
         last = i;
         break;
      }
   }
   if(last <= 0)
      return "";
   return StringSubstr(normalized, 0, last);
}

string CommonFeedPath()
{
   string common = TerminalInfoString(TERMINAL_COMMONDATA_PATH);
   string file_name = SafeExportFileName();
   StringReplace(file_name, "/", "\\");
   if(common == "")
      return file_name;
   return common + "\\Files\\" + file_name;
}

bool ConfiguredLaunchFileExists(const string path)
{
   ResetLastError();
   uint attributes = GetFileAttributesW(path);
   return (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0);
}

bool LaunchWindow()
{
   if(!LaunchDesktopWindow)
      return true;
   if(!MQLInfoInteger(MQL_DLLS_ALLOWED))
   {
      Print("MarketWatchATRPercentFeed: desktop window auto-launch needs 'Allow DLL imports' enabled for this EA. The ATR feed still runs; no trades are sent.");
      return false;
   }

   string python = Trimmed(PythonExecutable);
   string script = Trimmed(DesktopWindowScriptPath);
   if(python == "")
   {
      Print("MarketWatchATRPercentFeed: configured PythonExecutable is blank. Set it to an existing python.exe. 'Allow DLL imports' must remain enabled for auto-launch.");
      return false;
   }
   if(script == "")
   {
      Print("MarketWatchATRPercentFeed: configured DesktopWindowScriptPath is blank. Normal installation path is ", NORMAL_DESKTOP_SCRIPT_PATH,
            ". Update this EA input after moving the repository. 'Allow DLL imports' must remain enabled for auto-launch.");
      return false;
   }
   if(!ConfiguredLaunchFileExists(python))
   {
      Print("MarketWatchATRPercentFeed: configured PythonExecutable does not exist or is not a file: ", python,
            ". Correct the EA input; no desktop launch was attempted.");
      return false;
   }
   if(!ConfiguredLaunchFileExists(script))
   {
      Print("MarketWatchATRPercentFeed: configured DesktopWindowScriptPath does not exist or is not a file: ", script,
            ". Normal installation path is ", NORMAL_DESKTOP_SCRIPT_PATH,
            ". Update this EA input after moving the repository; no desktop launch was attempted.");
      return false;
   }

   string params = QuoteArg(script)
                 + " --file " + QuoteArg(CommonFeedPath())
                 + " --refresh-ms " + IntegerToString(SafeDesktopWindowRefreshMs())
                 + " --decimals " + IntegerToString(SafeDesktopWindowDecimals())
                 + " --top-n " + IntegerToString(SafeDesktopWindowTopN())
                 + " --rank-timeframe " + SafeRankFrame();

   ResetLastError();
   long result = ShellExecuteW(0, "open", python, params, DirectoryName(script), SW_SHOWNORMAL);
   if(result <= 32)
   {
      Print("MarketWatchATRPercentFeed: failed to launch desktop ATR window. ShellExecuteW result=", result,
            ". PythonExecutable=", python, " Script=", script, " LastError=", GetLastError());
      return false;
   }
   Print("MarketWatchATRPercentFeed: ShellExecuteW accepted the separate desktop ATR window launch request. PythonExecutable=", python,
         " Script=", script, ". Process startup is not yet confirmed; if no window appears, inspect the configured paths and enable 'Allow DLL imports'.");
   return true;
}

int Slot(const int symbol_index, const int frame_index)
{
   return symbol_index * FRAME_COUNT + frame_index;
}

string MarketWatchSignature()
{
   int selected = SymbolsTotal(true);
   string signature = IntegerToString(selected);
   for(int i = 0; i < selected; i++)
      signature += "|" + SymbolName(i, true);
   return signature;
}

void ReleaseHandles()
{
   for(int i = 0; i < ArraySize(g_handles); i++)
   {
      if(g_handles[i] != INVALID_HANDLE)
      {
         IndicatorRelease(g_handles[i]);
         g_handles[i] = INVALID_HANDLE;
      }
   }
}

bool IsForexSymbol(const string symbol, string &reason)
{
   long calc_mode = -1;
   if(!SymbolInfoInteger(symbol, SYMBOL_TRADE_CALC_MODE, calc_mode))
   {
      reason = "Forex classification metadata unavailable";
      return false;
   }
   if(calc_mode == SYMBOL_CALC_MODE_FOREX || calc_mode == SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE)
   {
      reason = "";
      return true;
   }
   reason = "Selected Market Watch instrument is not Forex";
   return false;
}

void RebuildUniverse()
{
   ReleaseHandles();
   int selected = SymbolsTotal(true);
   if(selected < 0)
      selected = 0;

   ArrayResize(g_symbols, selected);
   ArrayResize(g_is_forex, selected);
   ArrayResize(g_symbol_status, selected);
   ArrayResize(g_symbol_reason, selected);
   ArrayResize(g_handles, selected * FRAME_COUNT);
   ArrayResize(g_last_closed_bars, selected * FRAME_COUNT);
   ArrayResize(g_atr_percent, selected * FRAME_COUNT);
   ArrayResize(g_value_valid, selected * FRAME_COUNT);
   ArrayResize(g_value_stale, selected * FRAME_COUNT);
   ArrayResize(g_frame_status, selected * FRAME_COUNT);

   for(int index = 0; index < selected; index++)
   {
      string symbol = SymbolName(index, true);
      g_symbols[index] = symbol;
      string reason = "";
      g_is_forex[index] = (symbol != "" && IsForexSymbol(symbol, reason));
      g_symbol_reason[index] = reason;
      g_symbol_status[index] = g_is_forex[index] ? "Loading" : "Excluded";

      for(int frame = 0; frame < FRAME_COUNT; frame++)
      {
         int slot = Slot(index, frame);
         g_handles[slot] = INVALID_HANDLE;
         g_last_closed_bars[slot] = 0;
         g_atr_percent[slot] = 0.0;
         g_value_valid[slot] = false;
         g_value_stale[slot] = false;
         g_frame_status[slot] = g_is_forex[index] ? "Loading" : "N/A";
      }
   }
   g_next_symbol = 0;
   g_market_watch_signature = MarketWatchSignature();
}

void MarkUnavailableFrame(const int slot, const string state)
{
   if(g_value_valid[slot])
   {
      g_value_stale[slot] = true;
      g_frame_status[slot] = "Stale";
   }
   else
   {
      g_value_stale[slot] = false;
      g_frame_status[slot] = state;
   }
}

void RefreshForexSymbol(const int index)
{
   int valid_count = 0;
   bool any_stale = false;
   bool any_error = false;

   for(int frame = 0; frame < FRAME_COUNT; frame++)
   {
      int slot = Slot(index, frame);
      int handle = g_handles[slot];
      if(handle == INVALID_HANDLE)
      {
         handle = iATR(g_symbols[index], FRAME_PERIODS[frame], SafeATRLength());
         g_handles[slot] = handle;
         if(handle == INVALID_HANDLE)
         {
            MarkUnavailableFrame(slot, "Error");
            any_error = true;
            continue;
         }
      }

      datetime closed_bar = iTime(g_symbols[index], FRAME_PERIODS[frame], 1);
      if(closed_bar <= 0 || BarsCalculated(handle) < SafeATRLength() + 2)
      {
         MarkUnavailableFrame(slot, "Loading");
      }
      else if(g_value_valid[slot] && !g_value_stale[slot] && g_last_closed_bars[slot] == closed_bar)
      {
         // The cached reading already belongs to the latest fully closed bar.
      }
      else
      {
         double atr_buffer[1];
         ResetLastError();
         int copied = CopyBuffer(handle, 0, 1, 1, atr_buffer);
         double closed_price = iClose(g_symbols[index], FRAME_PERIODS[frame], 1);
         if(copied != 1)
         {
            MarkUnavailableFrame(slot, "Error");
            any_error = true;
         }
         else if(atr_buffer[0] == EMPTY_VALUE || !MathIsValidNumber(atr_buffer[0]) || atr_buffer[0] <= 0.0 ||
                 closed_price == EMPTY_VALUE || !MathIsValidNumber(closed_price) || closed_price <= 0.0)
         {
            MarkUnavailableFrame(slot, "Error");
            any_error = true;
         }
         else
         {
            double percent = (atr_buffer[0] / closed_price) * 100.0;
            if(!MathIsValidNumber(percent) || percent <= 0.0)
            {
               MarkUnavailableFrame(slot, "Error");
            }
            else
            {
               g_atr_percent[slot] = percent;
               g_value_valid[slot] = true;
               g_value_stale[slot] = false;
               g_frame_status[slot] = "Ready";
               g_last_closed_bars[slot] = closed_bar;
               g_last_successful_refresh = TimeCurrent();
            }
         }
      }

      if(g_value_valid[slot])
         valid_count++;
      if(g_value_stale[slot])
         any_stale = true;
      if(g_frame_status[slot] == "Error")
         any_error = true;
   }

   if(any_stale)
   {
      g_symbol_status[index] = "Stale";
      g_symbol_reason[index] = "One or more cached ATR readings could not be refreshed";
   }
   else if(any_error)
   {
      g_symbol_status[index] = "Error";
      g_symbol_reason[index] = "One or more ATR timeframes could not be refreshed";
   }
   else if(valid_count == FRAME_COUNT)
   {
      g_symbol_status[index] = "Ready";
      g_symbol_reason[index] = "";
   }
   else
   {
      g_symbol_status[index] = "Loading";
      g_symbol_reason[index] = "MT5 is downloading or building closed-candle ATR history";
   }
}

void ProcessNextBatch()
{
   int count = ArraySize(g_symbols);
   if(count <= 0)
      return;
   int processed = 0;
   int visited = 0;
   int batch = SafeSymbolsPerTimer();
   while(processed < batch && visited < count)
   {
      if(g_next_symbol >= count)
         g_next_symbol = 0;
      int index = g_next_symbol;
      g_next_symbol++;
      visited++;
      if(!g_is_forex[index])
         continue;
      RefreshForexSymbol(index);
      processed++;
   }
}

string JsonEscape(const string value)
{
   string escaped = value;
   StringReplace(escaped, "\\", "\\\\");
   StringReplace(escaped, "\"", "\\\"");
   StringReplace(escaped, "\r", "\\r");
   StringReplace(escaped, "\n", "\\n");
   StringReplace(escaped, "\t", "\\t");
   return escaped;
}

string JsonString(const string value)
{
   return "\"" + JsonEscape(value) + "\"";
}

string JsonTime(const datetime value)
{
   if(value <= 0)
      return "null";
   return JsonString(TimeToString(value, TIME_DATE | TIME_SECONDS));
}

string JsonATRValue(const int slot)
{
   if(!g_value_valid[slot] || !MathIsValidNumber(g_atr_percent[slot]) || g_atr_percent[slot] <= 0.0)
      return "null";
   return DoubleToString(g_atr_percent[slot], 10);
}

void WriteSymbolJson(const int file_handle, const int index)
{
   FileWriteString(file_handle, "    {\r\n");
   FileWriteString(file_handle, "      \"symbol\": " + JsonString(g_symbols[index]) + ",\r\n");
   FileWriteString(file_handle, "      \"is_forex\": " + (g_is_forex[index] ? "true" : "false") + ",\r\n");
   FileWriteString(file_handle, "      \"status\": " + JsonString(g_symbol_status[index]) + ",\r\n");
   FileWriteString(file_handle, "      \"reason\": " + JsonString(g_symbol_reason[index]) + ",\r\n");
   for(int frame = 0; frame < FRAME_COUNT; frame++)
   {
      int slot = Slot(index, frame);
      string key = FRAME_KEYS[frame];
      FileWriteString(file_handle, "      \"atr_percent_" + key + "\": " + JsonATRValue(slot) + ",\r\n");
      FileWriteString(file_handle, "      \"bar_time_" + key + "\": " + JsonTime(g_last_closed_bars[slot]) + ",\r\n");
      FileWriteString(file_handle, "      \"state_" + key + "\": " + JsonString(g_frame_status[slot]));
      if(frame < FRAME_COUNT - 1)
         FileWriteString(file_handle, ",\r\n");
      else
         FileWriteString(file_handle, "\r\n");
   }
   FileWriteString(file_handle, "    }");
}

bool ExportATRFeed()
{
   string file_name = SafeExportFileName();
   ResetLastError();
   int file_handle = FileOpen(file_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(file_handle == INVALID_HANDLE)
   {
      Print("MarketWatchATRPercentFeed: failed to open separate common ATR feed file ", file_name, ". Error=", GetLastError());
      return false;
   }

   int symbol_count = ArraySize(g_symbols);
   int forex_count = 0;
   for(int index = 0; index < symbol_count; index++)
      if(g_is_forex[index])
         forex_count++;

   FileWriteString(file_handle, "{\r\n");
   FileWriteString(file_handle, "  \"name\": \"MarketWatchATRPercentFeed\",\r\n");
   FileWriteString(file_handle, "  \"version\": \"1.00\",\r\n");
   FileWriteString(file_handle, "  \"generated_at\": " + JsonString(TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS)) + ",\r\n");
   FileWriteString(file_handle, "  \"generated_at_epoch_ms\": " + IntegerToString((long)TimeCurrent() * 1000) + ",\r\n");
   FileWriteString(file_handle, "  \"last_successful_refresh\": " + JsonTime(g_last_successful_refresh) + ",\r\n");
   FileWriteString(file_handle, "  \"last_successful_refresh_epoch_ms\": " + (g_last_successful_refresh > 0 ? IntegerToString((long)g_last_successful_refresh * 1000) : "null") + ",\r\n");
   FileWriteString(file_handle, "  \"basis\": \"Wilder iATR / same timeframe last closed candle close * 100\",\r\n");
   FileWriteString(file_handle, "  \"atr_length\": " + IntegerToString(SafeATRLength()) + ",\r\n");
   FileWriteString(file_handle, "  \"rank_timeframe\": " + JsonString(SafeRankFrame()) + ",\r\n");
   FileWriteString(file_handle, "  \"top_n\": " + IntegerToString(SafeDesktopWindowTopN()) + ",\r\n");
   FileWriteString(file_handle, "  \"symbol_count\": " + IntegerToString(symbol_count) + ",\r\n");
   FileWriteString(file_handle, "  \"forex_count\": " + IntegerToString(forex_count) + ",\r\n");
   FileWriteString(file_handle, "  \"symbols\": [\r\n");
   for(int index = 0; index < symbol_count; index++)
   {
      if(index > 0)
         FileWriteString(file_handle, ",\r\n");
      WriteSymbolJson(file_handle, index);
   }
   if(symbol_count > 0)
      FileWriteString(file_handle, "\r\n");
   FileWriteString(file_handle, "  ]\r\n");
   FileWriteString(file_handle, "}\r\n");
   FileClose(file_handle);
   return true;
}

int OnInit()
{
   ResetLastError();
   if(!EventSetMillisecondTimer(SafeUpdateInterval()))
   {
      Print("MarketWatchATRPercentFeed: failed to start the bounded millisecond timer. Error=", GetLastError());
      return INIT_FAILED;
   }
   RebuildUniverse();
   ProcessNextBatch();
   ExportATRFeed();
   LaunchWindow();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ReleaseHandles();
}

void OnTimer()
{
   string signature = MarketWatchSignature();
   if(signature != g_market_watch_signature)
      RebuildUniverse();
   ProcessNextBatch();
   ExportATRFeed();
}

void OnTick()
{
   // Work is intentionally timer-batched so a busy chart cannot multiply ATR refreshes.
}
