#property strict
#property description "Feed selected Market Watch symbol spread percentages to a FILE_COMMON JSON file and optionally launch the desktop pop-out. Display-only; no trading."
#property version   "1.10"

#import "shell32.dll"
long ShellExecuteW(long hwnd, string operation, string file, string parameters, string directory, int show_cmd);
#import

input group "Feed"
input int    UpdateIntervalMs = 100;
input string ExportFileName   = "MarketWatchSpreadPercentFeed.json";

input group "Desktop Window"
input bool   LaunchDesktopWindow      = true;
input string PythonExecutable         = "C:\\Users\\User\\miniconda3\\python.exe";
input string DesktopWindowScriptPath  = "C:\\GPT\\CODEX-master\\mt5-clone\\spread_percent_window.py";
input int    DesktopWindowRefreshMs   = 150;
input int    DesktopWindowDecimals    = 5;
input bool   DesktopWindowShowPoints  = false;

const string DEFAULT_EXPORT_FILE = "MarketWatchSpreadPercentFeed.json";
const int    SW_SHOWNORMAL = 1;

int SafeUpdateInterval()
{
   if(UpdateIntervalMs < 10)
      return 10;
   if(UpdateIntervalMs > 60000)
      return 60000;
   return UpdateIntervalMs;
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

int SafeDesktopWindowRefreshMs()
{
   if(DesktopWindowRefreshMs < 50)
      return 50;
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

bool LaunchWindow()
{
   if(!LaunchDesktopWindow)
      return true;

   if(!MQLInfoInteger(MQL_DLLS_ALLOWED))
   {
      Print("MarketWatchSpreadPercentFeed: desktop window auto-launch needs 'Allow DLL imports' enabled for this EA. The feed still runs; no trades are sent.");
      return false;
   }

   string python = Trimmed(PythonExecutable);
   string script = Trimmed(DesktopWindowScriptPath);
   if(python == "" || script == "")
   {
      Print("MarketWatchSpreadPercentFeed: desktop window launch skipped because PythonExecutable or DesktopWindowScriptPath is blank.");
      return false;
   }

   string params = QuoteArg(script)
                 + " --file " + QuoteArg(CommonFeedPath())
                 + " --refresh-ms " + IntegerToString(SafeDesktopWindowRefreshMs())
                 + " --decimals " + IntegerToString(SafeDesktopWindowDecimals());

   if(DesktopWindowShowPoints)
      params += " --show-points";

   ResetLastError();
   long result = ShellExecuteW(0, "open", python, params, DirectoryName(script), SW_SHOWNORMAL);
   if(result <= 32)
   {
      Print("MarketWatchSpreadPercentFeed: failed to launch desktop window. ShellExecuteW result=", result,
            ". PythonExecutable=", python, " Script=", script, " LastError=", GetLastError());
      return false;
   }

   Print("MarketWatchSpreadPercentFeed: launched desktop spread window.");
   return true;
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

string JsonNumber(const double value, const int digits)
{
   return DoubleToString(value, digits);
}

bool CalculateSpread(const MqlTick &tick, const double point, double &spread_percent, double &spread_points)
{
   if(tick.time <= 0 || tick.bid <= 0.0 || tick.ask <= 0.0 || point <= 0.0)
      return false;

   double spread_price = tick.ask - tick.bid;
   double midpoint = (tick.ask + tick.bid) / 2.0;
   if(midpoint <= 0.0)
      return false;

   spread_percent = (spread_price / midpoint) * 100.0;
   spread_points = spread_price / point;
   return true;
}

void WriteSymbolJson(const int handle, const string sym, const bool valid, const MqlTick &tick,
                     const double spread_percent, const double spread_points)
{
   FileWriteString(handle, "    {\r\n");
   FileWriteString(handle, "      \"symbol\": " + JsonString(sym) + ",\r\n");
   FileWriteString(handle, "      \"valid\": " + (valid ? "true" : "false") + ",\r\n");

   if(valid)
   {
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      FileWriteString(handle, "      \"bid\": " + JsonNumber(tick.bid, digits) + ",\r\n");
      FileWriteString(handle, "      \"ask\": " + JsonNumber(tick.ask, digits) + ",\r\n");
      FileWriteString(handle, "      \"spread_percent\": " + JsonNumber(spread_percent, 10) + ",\r\n");
      FileWriteString(handle, "      \"spread_points\": " + JsonNumber(spread_points, 2) + ",\r\n");
      FileWriteString(handle, "      \"tick_time\": " + JsonTime(tick.time) + "\r\n");
   }
   else
   {
      FileWriteString(handle, "      \"bid\": null,\r\n");
      FileWriteString(handle, "      \"ask\": null,\r\n");
      FileWriteString(handle, "      \"spread_percent\": null,\r\n");
      FileWriteString(handle, "      \"spread_points\": null,\r\n");
      FileWriteString(handle, "      \"tick_time\": null,\r\n");
      FileWriteString(handle, "      \"status\": \"N/A\"\r\n");
   }

   FileWriteString(handle, "    }");
}

bool ExportSpreadFeed()
{
   string file_name = SafeExportFileName();
   ResetLastError();
   int handle = FileOpen(file_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
   {
      Print("MarketWatchSpreadPercentFeed: failed to open common feed file ", file_name, ". Error=", GetLastError());
      return false;
   }

   int selected = SymbolsTotal(true);
   if(selected < 0)
      selected = 0;

   FileWriteString(handle, "{\r\n");
   FileWriteString(handle, "  \"name\": \"MarketWatchSpreadPercentFeed\",\r\n");
   FileWriteString(handle, "  \"version\": \"1.10\",\r\n");
   FileWriteString(handle, "  \"generated_at\": " + JsonString(TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS)) + ",\r\n");
   FileWriteString(handle, "  \"symbol_count\": " + IntegerToString(selected) + ",\r\n");
   FileWriteString(handle, "  \"symbols\": [\r\n");

   bool first_symbol = true;
   for(int i = 0; i < selected; i++)
   {
      string sym = SymbolName(i, true);
      if(sym == "")
         continue;

      MqlTick tick;
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      double spread_percent = 0.0;
      double spread_points = 0.0;
      bool valid = (SymbolInfoTick(sym, tick) && CalculateSpread(tick, point, spread_percent, spread_points));

      if(!first_symbol)
         FileWriteString(handle, ",\r\n");
      WriteSymbolJson(handle, sym, valid, tick, spread_percent, spread_points);
      first_symbol = false;
   }

   if(!first_symbol)
      FileWriteString(handle, "\r\n");
   FileWriteString(handle, "  ]\r\n");
   FileWriteString(handle, "}\r\n");
   FileClose(handle);
   return true;
}

int OnInit()
{
   EventSetMillisecondTimer(SafeUpdateInterval());
   ExportSpreadFeed();
   LaunchWindow();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   ExportSpreadFeed();
}

void OnTick()
{
   ExportSpreadFeed();
}
