#property strict
#property description "Display selected Market Watch symbols with bid, ask, spread percent, spread points, and tick time."
#property version   "1.00"

enum SortModeOptions
{
   none = 0,
   symbol = 1,
   spread_percent_desc = 2,
   spread_percent_asc = 3
};

input group "Update"
input int             UpdateIntervalMs       = 100;

input group "Panel"
input int             FontSize               = 9;
input int             RowHeight              = 18;
input int             PanelX                 = 8;
input int             PanelY                 = 18;
input SortModeOptions SortMode               = none;
input bool            ShowBidAsk             = true;
input bool            ShowSpreadPoints       = true;
input int             DecimalPlacesPercent   = 4;

struct SpreadRow
{
   string   sym;
   bool     has_tick;
   double   bid;
   double   ask;
   double   spread_percent;
   double   spread_points;
   datetime tick_time;
   int      digits;
};

string g_prefix = "";
int    g_rendered_rows = 0;
int    g_timer_ms = 100;
int    g_digits_percent = 4;
int    g_font_size = 9;
int    g_row_height = 18;
long   g_previous_show_grid = 1;
bool   g_restore_show_grid = false;

const string FONT_NAME = "Consolas";
const int    PAD_X = 8;
const int    PAD_Y = 5;
const int    SYMBOL_CHARS = 16;
const int    PRICE_CHARS = 14;
const int    PERCENT_CHARS = 12;
const int    POINTS_CHARS = 14;
const int    TIME_CHARS = 10;

int SafeUpdateInterval()
{
   if(UpdateIntervalMs < 10)
      return 10;
   if(UpdateIntervalMs > 60000)
      return 60000;
   return UpdateIntervalMs;
}

int SafeFontSize()
{
   if(FontSize < 6)
      return 6;
   if(FontSize > 24)
      return 24;
   return FontSize;
}

int SafeRowHeight()
{
   int min_height = SafeFontSize() + 6;
   if(RowHeight < min_height)
      return min_height;
   if(RowHeight > 60)
      return 60;
   return RowHeight;
}

int SafePercentDigits()
{
   if(DecimalPlacesPercent < 0)
      return 0;
   if(DecimalPlacesPercent > 8)
      return 8;
   return DecimalPlacesPercent;
}

string RepeatSpaces(const int count)
{
   string out = "";
   for(int i = 0; i < count; i++)
      out += " ";
   return out;
}

string ClipText(const string text, const int width)
{
   if(width <= 0)
      return "";
   if(StringLen(text) <= width)
      return text;
   return StringSubstr(text, 0, width);
}

string PadRight(const string text, const int width)
{
   string clipped = ClipText(text, width);
   int spaces = width - StringLen(clipped);
   if(spaces <= 0)
      return clipped;
   return clipped + RepeatSpaces(spaces);
}

string PadLeft(const string text, const int width)
{
   string clipped = ClipText(text, width);
   int spaces = width - StringLen(clipped);
   if(spaces <= 0)
      return clipped;
   return RepeatSpaces(spaces) + clipped;
}

int TableChars()
{
   int chars = SYMBOL_CHARS + PERCENT_CHARS + TIME_CHARS;
   int columns = 3;

   if(ShowBidAsk)
   {
      chars += PRICE_CHARS * 2;
      columns += 2;
   }

   if(ShowSpreadPoints)
   {
      chars += POINTS_CHARS;
      columns += 1;
   }

   return chars + ((columns - 1) * 2);
}

int TableWidth()
{
   double char_width = (double)g_font_size * 0.72;
   return (int)MathCeil((double)TableChars() * char_width) + (PAD_X * 2) + 8;
}

string HeaderText()
{
   string sep = "  ";
   string text = PadRight("Symbol", SYMBOL_CHARS);

   if(ShowBidAsk)
   {
      text += sep + PadLeft("Bid", PRICE_CHARS);
      text += sep + PadLeft("Ask", PRICE_CHARS);
   }

   text += sep + PadLeft("Spread %", PERCENT_CHARS);

   if(ShowSpreadPoints)
      text += sep + PadLeft("Spread Points", POINTS_CHARS);

   text += sep + PadLeft("Tick Time", TIME_CHARS);
   return text;
}

string RowText(const SpreadRow &row)
{
   string sep = "  ";
   string text = PadRight(row.sym, SYMBOL_CHARS);

   if(ShowBidAsk)
   {
      string bid_text = row.has_tick ? DoubleToString(row.bid, row.digits) : "N/A";
      string ask_text = row.has_tick ? DoubleToString(row.ask, row.digits) : "N/A";
      text += sep + PadLeft(bid_text, PRICE_CHARS);
      text += sep + PadLeft(ask_text, PRICE_CHARS);
   }

   string percent_text = row.has_tick ? DoubleToString(row.spread_percent, g_digits_percent) : "N/A";
   text += sep + PadLeft(percent_text, PERCENT_CHARS);

   if(ShowSpreadPoints)
   {
      string points_text = row.has_tick ? DoubleToString(row.spread_points, 1) : "N/A";
      text += sep + PadLeft(points_text, POINTS_CHARS);
   }

   string time_text = row.has_tick ? TimeToString(row.tick_time, TIME_SECONDS) : "N/A";
   text += sep + PadLeft(time_text, TIME_CHARS);
   return text;
}

bool EnsureRectangle(const string name, const int x, const int y, const int width, const int height,
                     const color fill_color, const color border_color, const int z_order)
{
   if(ObjectFind(0, name) < 0)
   {
      if(!ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0))
         return false;
   }

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, width);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, height);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, fill_color);
   ObjectSetInteger(0, name, OBJPROP_COLOR, border_color);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, z_order);
   return true;
}

bool EnsureLabel(const string name, const int x, const int y, const string text,
                 const color text_color, const int z_order)
{
   if(ObjectFind(0, name) < 0)
   {
      if(!ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0))
         return false;
   }

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, g_font_size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, text_color);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, z_order);
   ObjectSetString(0, name, OBJPROP_FONT, FONT_NAME);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   return true;
}

void DeleteObjectIfExists(const string name)
{
   if(ObjectFind(0, name) >= 0)
      ObjectDelete(0, name);
}

void DeleteRenderedRowsFrom(const int first_row)
{
   for(int row = first_row; row < g_rendered_rows; row++)
   {
      DeleteObjectIfExists(g_prefix + "row_bg_" + IntegerToString(row));
      DeleteObjectIfExists(g_prefix + "row_text_" + IntegerToString(row));
   }
}

void DeleteAllObjects()
{
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, g_prefix) == 0)
         ObjectDelete(0, name);
   }
   g_rendered_rows = 0;
}

bool IsValidQuote(const MqlTick &tick, const double point, double &spread_percent, double &spread_points)
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

int LoadRows(SpreadRow &rows[])
{
   int selected = SymbolsTotal(true);
   if(selected < 0)
      selected = 0;

   ArrayResize(rows, selected);

   int actual = 0;
   for(int i = 0; i < selected; i++)
   {
      string sym = SymbolName(i, true);
      if(sym == "")
         continue;

      SpreadRow row;
      row.sym = sym;
      row.has_tick = false;
      row.bid = 0.0;
      row.ask = 0.0;
      row.spread_percent = 0.0;
      row.spread_points = 0.0;
      row.tick_time = 0;
      row.digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

      MqlTick tick;
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      double spread_percent = 0.0;
      double spread_points = 0.0;

      if(SymbolInfoTick(sym, tick) && IsValidQuote(tick, point, spread_percent, spread_points))
      {
         row.has_tick = true;
         row.bid = tick.bid;
         row.ask = tick.ask;
         row.spread_percent = spread_percent;
         row.spread_points = spread_points;
         row.tick_time = tick.time;
      }

      rows[actual] = row;
      actual++;
   }

   if(actual != selected)
      ArrayResize(rows, actual);

   return actual;
}

int CompareRows(const SpreadRow &a, const SpreadRow &b)
{
   if(SortMode == symbol)
      return StringCompare(a.sym, b.sym);

   if(SortMode == spread_percent_desc || SortMode == spread_percent_asc)
   {
      if(a.has_tick && !b.has_tick)
         return -1;
      if(!a.has_tick && b.has_tick)
         return 1;
      if(!a.has_tick && !b.has_tick)
         return StringCompare(a.sym, b.sym);

      if(a.spread_percent < b.spread_percent)
         return (SortMode == spread_percent_desc) ? 1 : -1;
      if(a.spread_percent > b.spread_percent)
         return (SortMode == spread_percent_desc) ? -1 : 1;

      return StringCompare(a.sym, b.sym);
   }

   return 0;
}

void SortRows(SpreadRow &rows[], const int count)
{
   if(SortMode == none || count <= 1)
      return;

   for(int i = 1; i < count; i++)
   {
      SpreadRow key = rows[i];
      int j = i - 1;

      while(j >= 0 && CompareRows(key, rows[j]) < 0)
      {
         rows[j + 1] = rows[j];
         j--;
      }

      rows[j + 1] = key;
   }
}

void RenderEmptyTable(const int table_width)
{
   int y = PanelY + g_row_height + 1;
   EnsureRectangle(g_prefix + "row_bg_0", PanelX, y, table_width, g_row_height, clrWhite, clrGainsboro, 1);
   EnsureLabel(g_prefix + "row_text_0", PanelX + PAD_X, y + PAD_Y, "No selected Market Watch symbols.", clrDimGray, 2);
   DeleteRenderedRowsFrom(1);
   g_rendered_rows = 1;
}

void RenderRows(const SpreadRow &rows[], const int count, const int table_width)
{
   for(int i = 0; i < count; i++)
   {
      int y = PanelY + g_row_height + 1 + (i * g_row_height);
      color fill = (i % 2 == 0) ? clrWhite : clrAliceBlue;
      color text_color = rows[i].has_tick ? clrBlack : clrDimGray;

      EnsureRectangle(g_prefix + "row_bg_" + IntegerToString(i), PanelX, y, table_width, g_row_height, fill, clrGainsboro, 1);
      EnsureLabel(g_prefix + "row_text_" + IntegerToString(i), PanelX + PAD_X, y + PAD_Y, RowText(rows[i]), text_color, 2);
   }

   DeleteRenderedRowsFrom(count);
   g_rendered_rows = count;
}

void RenderPanel(const SpreadRow &rows[], const int count)
{
   int table_width = TableWidth();
   int display_rows = (count > 0) ? count : 1;
   int table_height = g_row_height + 1 + (display_rows * g_row_height);

   EnsureRectangle(g_prefix + "panel_bg", PanelX - 1, PanelY - 1, table_width + 2, table_height + 2, clrWhiteSmoke, clrSilver, 0);
   EnsureRectangle(g_prefix + "header_bg", PanelX, PanelY, table_width, g_row_height, clrLightSteelBlue, clrSteelBlue, 1);
   EnsureLabel(g_prefix + "header_text", PanelX + PAD_X, PanelY + PAD_Y, HeaderText(), clrBlack, 2);

   if(count <= 0)
      RenderEmptyTable(table_width);
   else
      RenderRows(rows, count, table_width);
}

void UpdatePanel()
{
   g_font_size = SafeFontSize();
   g_row_height = SafeRowHeight();
   g_digits_percent = SafePercentDigits();

   SpreadRow rows[];
   int count = LoadRows(rows);
   SortRows(rows, count);
   RenderPanel(rows, count);
   ChartRedraw(0);
}

int OnInit()
{
   g_prefix = "MWSpreadPercent_" + IntegerToString(ChartID()) + "_";
   g_timer_ms = SafeUpdateInterval();
   g_font_size = SafeFontSize();
   g_row_height = SafeRowHeight();
   g_digits_percent = SafePercentDigits();

   g_previous_show_grid = ChartGetInteger(0, CHART_SHOW_GRID);
   g_restore_show_grid = true;
   ChartSetInteger(0, CHART_SHOW_GRID, false);

   EventSetMillisecondTimer(g_timer_ms);
   UpdatePanel();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DeleteAllObjects();
   if(g_restore_show_grid)
      ChartSetInteger(0, CHART_SHOW_GRID, g_previous_show_grid != 0);
   ChartRedraw(0);
}

void OnTimer()
{
   UpdatePanel();
}

void OnTick()
{
   UpdatePanel();
}
