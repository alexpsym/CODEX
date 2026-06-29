#property strict
#property description "Feed selected Market Watch symbol spread percentages to a FILE_COMMON JSON file. Display-only; no trading."
#property version   "1.00"

input group "Feed"
input int    UpdateIntervalMs = 100;
input string ExportFileName   = "MarketWatchSpreadPercentFeed.json";

const string DEFAULT_EXPORT_FILE = "MarketWatchSpreadPercentFeed.json";

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
   FileWriteString(handle, "  \"version\": \"1.00\",\r\n");
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
