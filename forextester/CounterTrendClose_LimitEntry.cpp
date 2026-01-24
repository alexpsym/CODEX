// CounterTrendClose_LimitEntry.cpp
// Forex Tester 6 Strategy DLL (C++)
//
// Strategy rules:
// - Trend filter (last N COMPLETE candle bodies):
//     Majority bullish -> ONLY LONGS
//     Majority bearish -> ONLY SHORTS
//     Tie -> no trades
//
// - Entry rule STILL APPLIES:
//     LONG only if last 2 COMPLETE bodies bearish
//     SHORT only if last 2 COMPLETE bodies bullish
//
// - Entry (ALWAYS LIMIT):
//     On new bar, place LIMIT at Close[1] +/- offset
//
// - SL = ATR(N) on bar[1] * ATR_Multiplier   <-- ADJUSTABLE ATR PERIOD
// - TP >= 2R
//
// - NO trading 04:00-09:00 Brisbane time
//     Brisbane = UTC+10 (no DST) -> UTC window 18:00-23:00
//     Inside window: liquidate open trades + cancel pendings
//
// - NFP BLACKOUT (PATTERN-BASED):
//     Never hold a trade NfpBlackoutMinutes before/during NFP.
//     If inside blackout: liquidate open trades + cancel pendings + block new entries.
//     NFP pattern: first Friday of month, 08:30 ET (12:30 UTC during US DST, 13:30 UTC otherwise).
//     Small exception list for known shifted releases.
//
// - DAILY LOSS LIMIT:
//     Only allow max MaxLossesPerDay losses per Brisbane trading day.
//     If losses for TODAY reach MaxLossesPerDay -> block any NEW entries until next day.
//     Loss is attributed to the day the trade was OPENED (not closed).
//
//     Implementation uses ticket-disappearance detection + OrderSelect(ticket, MODE_HISTORY).
//
// - Pending expiry:
//     LIMIT must fill within next PendingExpiryBars candles, otherwise auto-cancel
//
// - Session entry filter (optional):
//     Only allow NEW entries within +/- 1 hour of London/Frankfurt/New York opens
//     Uses FIXED UTC open hours (you set winter/summer yourself)
//
// NOTE:
// FT6 headers use char* (non-const) in many APIs, so string literals must be mutable char[].

#include <windows.h>
#include <math.h>
#include <string.h>
#include <stdarg.h>
#include <stdlib.h>

#include "StrategyInterfaceUnit.h"
#include "TechnicalFunctions.h"

//----------------------------------------------------
// Inputs (FT6 Strategy Options)
//----------------------------------------------------
PChar  Currency  = NULL;
int    Timeframe = PERIOD_M1;

double Lots                = 0.01;
int    MagicNumber         = 26012026;

int    TrendLookbackBars   = 10;

double EntryOffsetPips     = 0.1;
double MinBodyPips         = 0.0;

// ATR period adjustable (default 2 keeps old behavior)
int    ATR_Period          = 2;

double ATR_Multiplier      = 1.0;
double Min_R_Multiple      = 2.0;

int    PendingExpiryBars   = 5;

bool   CancelOldPending    = true;
bool   OnlyOneTradeAtATime = true;

// Brisbane no-trade: 04:00-09:00 Brisbane
// Brisbane = UTC+10 -> UTC is 18:00-23:00
int NoTradeStartUTC_Hour = 18;
int NoTradeEndUTC_Hour   = 23;

// Session entry filter
bool UseSessionEntryFilter = true;
int  SessionWindowMinutes  = 60;

// Fixed UTC opens (set these in FT6 options to match season if needed)
int LondonOpenUTC_Hour     = 8;
int FrankfurtOpenUTC_Hour  = 7;
int NewYorkOpenUTC_Hour    = 13;

// NFP blackout settings
bool UseNfpBlackout        = true;
int  NfpBlackoutMinutes    = 10;   // adjustable in settings

// DAILY LOSS LIMIT settings
bool UseDailyLossLimit     = true;
int  MaxLossesPerDay       = 2;    // adjustable in settings

// DEBUG
bool DebugLogs = true;

//----------------------------------------------------
// Mutable strings required by FT API (char*)
//----------------------------------------------------
static char STRAT_NAME[] = "CounterTrendClose_LimitEntry";

static char STRAT_DESC[] =
"Trend filter last N bodies + last2 pullback rule + LIMIT entries. "
"ATR(N) SL, TP >= 2R. Brisbane no-trade (UTC 18-23) liquidates + cancels. "
"NFP blackout (minutes adjustable) liquidates + cancels + blocks new entries. "
"Daily loss limit (losses/day adjustable) blocks new entries after limit reached. "
"Pending expiry N bars. Session filter +/- 1h around LDN/FF/NY fixed UTC opens. "
"DebugLogs prints block reasons.";

static char OPT_CCY[]  = "Currency";
static char OPT_TF[]   = "Timeframe";

static char OPT_LOTS[]  = "Lots";
static char OPT_MAGIC[] = "MagicNumber";

static char OPT_TRENDN[]   = "TrendLookbackBars";
static char OPT_OFFPIPS[]  = "EntryOffsetPips";
static char OPT_MINBOD[]   = "MinBodyPips";

static char OPT_ATRPER[]   = "ATR_Period";
static char OPT_ATRMULT[]  = "ATR_Multiplier";
static char OPT_MINR[]     = "Min_R_Multiple";

static char OPT_PENDEXP[]  = "PendingExpiryBars";
static char OPT_CANCELOLD[]= "CancelOldPending";
static char OPT_ONEONLY[]  = "OnlyOneTradeAtATime";

static char OPT_NOTSUTC[]  = "NoTradeStartUTC_Hour";
static char OPT_NOTENDU[]  = "NoTradeEndUTC_Hour";

static char OPT_SESSFILT[] = "UseSessionEntryFilter";
static char OPT_SESSWIN[]  = "SessionWindowMinutes";

static char OPT_LDNUTC[]   = "LondonOpenUTC_Hour";
static char OPT_FFUTC[]    = "FrankfurtOpenUTC_Hour";
static char OPT_NYUTC[]    = "NewYorkOpenUTC_Hour";

static char OPT_NFPUSE[]   = "UseNfpBlackout";
static char OPT_NFPMIN[]   = "NfpBlackoutMinutes";

static char OPT_DLOSSUSE[] = "UseDailyLossLimit";
static char OPT_DLOSSMAX[] = "MaxLossesPerDay";

static char OPT_DEBUG[]    = "DebugLogs";

static char COMM_BUY[]  = "TrendBull+2Bear -> BuyLimit";
static char COMM_SELL[] = "TrendBear+2Bull -> SellLimit";

//----------------------------------------------------
// Internals
//----------------------------------------------------
static TDateTime g_lastBarTime = 0.0;

//----------------------------------------------------
// Print helper (FT Print() takes ONE char* arg)
//----------------------------------------------------
static void PrintF(const char* fmt, ...)
{
    static char buf[512];
    va_list args;
    va_start(args, fmt);

#ifdef _MSC_VER
    _vsnprintf_s(buf, sizeof(buf), _TRUNCATE, fmt, args);
#else
    vsnprintf(buf, sizeof(buf), fmt, args);
#endif

    va_end(args);
    Print(buf);
}

static void LogBlock(const char* reason)
{
    if (!DebugLogs) return;
    PrintF("BLOCK: %s", reason);
}

//----------------------------------------------------
// Utility helpers
//----------------------------------------------------
static double PipSize()
{
    int d = Digits();
    double pt = Point();
    if (d == 3 || d == 5) return 10.0 * pt;
    return pt;
}

static double RoundToDigits(double price, int digits)
{
    double factor = pow(10.0, digits);
    return floor(price * factor + 0.5) / factor;
}

static int SecondsOfDay(TDateTime t)
{
    double dayFrac = t - floor(t);
    if (dayFrac < 0) dayFrac = 0;

    int sec = (int)(dayFrac * 86400.0 + 0.5);
    if (sec >= 86400) sec = 86399;
    return sec;
}

static bool IsNewBar()
{
    TDateTime t0 = Time(0);
    if (t0 == g_lastBarTime) return false;
    g_lastBarTime = t0;
    return true;
}

static bool InNoTradeWindowUTC()
{
    int sec = SecondsOfDay(Time(0));
    int h = sec / 3600;
    return (h >= NoTradeStartUTC_Hour && h < NoTradeEndUTC_Hour);
}

static bool InOpenWindowUTC(int openHourUTC, int windowMinutes)
{
    int nowSec  = SecondsOfDay(Time(0));
    int openSec = openHourUTC * 3600;
    int winSec  = windowMinutes * 60;

    int diff = abs(nowSec - openSec);
    if (diff > 43200) diff = 86400 - diff; // midnight wrap
    return (diff <= winSec);
}

static bool InAnySessionEntryWindowUTC()
{
    if (InOpenWindowUTC(LondonOpenUTC_Hour, SessionWindowMinutes)) return true;
    if (InOpenWindowUTC(FrankfurtOpenUTC_Hour, SessionWindowMinutes)) return true;
    if (InOpenWindowUTC(NewYorkOpenUTC_Hour, SessionWindowMinutes)) return true;
    return false;
}

//----------------------------------------------------
// Order helpers (open + pending)
//----------------------------------------------------
static bool HasOurOpenPosition(char* ccy)
{
    int total = OrdersTotal();
    for (int i = total - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if (strcmp(OrderSymbol(), ccy) != 0) continue;
        if (OrderMagicNumber() != MagicNumber) continue;

        TTradePositionType t = OrderType();
        if (t == tp_Buy || t == tp_Sell) return true;
    }
    return false;
}

static bool HasOurPending(char* ccy)
{
    int total = OrdersTotal();
    for (int i = total - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if (strcmp(OrderSymbol(), ccy) != 0) continue;
        if (OrderMagicNumber() != MagicNumber) continue;

        TTradePositionType t = OrderType();
        if (t == tp_BuyLimit || t == tp_SellLimit || t == tp_BuyStop || t == tp_SellStop)
            return true;
    }
    return false;
}

static void CancelOurPendings(char* ccy)
{
    int total = OrdersTotal();
    for (int i = total - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if (strcmp(OrderSymbol(), ccy) != 0) continue;
        if (OrderMagicNumber() != MagicNumber) continue;

        TTradePositionType t = OrderType();
        if (t != tp_BuyLimit && t != tp_SellLimit && t != tp_BuyStop && t != tp_SellStop)
            continue;

        DeleteOrder(OrderTicket());
    }
}

static void CloseOurMarketPositions(char* ccy)
{
    int total = OrdersTotal();
    for (int i = total - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if (strcmp(OrderSymbol(), ccy) != 0) continue;
        if (OrderMagicNumber() != MagicNumber) continue;

        TTradePositionType t = OrderType();
        if (t != tp_Buy && t != tp_Sell) continue;

        CloseOrder(OrderTicket());
    }
}

static void LiquidateNow(char* ccy, const char* why)
{
    if (DebugLogs) PrintF("%s: liquidating + canceling", why);
    CloseOurMarketPositions(ccy);
    CancelOurPendings(ccy);
}

static void CancelStaleLimitPendings(char* ccy, int maxBars)
{
    int tfSec = Timeframe * 60;
    if (tfSec <= 0) return;

    int expirySec = maxBars * tfSec;
    TDateTime now = Time(0);

    int total = OrdersTotal();
    for (int i = total - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if (strcmp(OrderSymbol(), ccy) != 0) continue;
        if (OrderMagicNumber() != MagicNumber) continue;

        TTradePositionType t = OrderType();
        if (t != tp_BuyLimit && t != tp_SellLimit) continue;

        int ageSec = (int)((now - OrderOpenTime()) * 86400.0 + 0.5);
        if (ageSec >= expirySec)
            DeleteOrder(OrderTicket());
    }
}

//----------------------------------------------------
// ATR + Trend
//----------------------------------------------------
static double TrueRange(int index)
{
    double hi = High(index);
    double lo = Low(index);
    double prevClose = Close(index + 1);

    double a = hi - lo;
    double b = fabs(hi - prevClose);
    double c = fabs(lo - prevClose);

    double tr = a;
    if (b > tr) tr = b;
    if (c > tr) tr = c;
    return tr;
}

// ATR(N) on bar[1]
static double ATR_OnBar1(int period)
{
    if (period < 1) period = 1;
    if (Bars() < (period + 3)) return 0.0; // TrueRange(i) reads Close(i+1)

    double sum = 0.0;
    for (int i = 1; i <= period; i++)
        sum += TrueRange(i);

    return sum / (double)period;
}

static int GetTrendDir_LastN(int n)
{
    int bull = 0, bear = 0;
    if (Bars() < (n + 3)) return 0;

    for (int i = 1; i <= n; i++)
    {
        double o = Open(i);
        double c = Close(i);
        if (c > o) bull++;
        else if (c < o) bear++;
    }

    if (bull > bear) return 1;
    if (bear > bull) return -1;
    return 0;
}

//----------------------------------------------------
// NFP Blackout (pattern-based + tiny exception list)
//----------------------------------------------------

// days since 1970-01-01
static int DaysFromCivil(int y, unsigned m, unsigned d)
{
    y -= m <= 2;
    const int era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = (unsigned)(y - era * 400);
    const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + (int)doe - 719468;
}

// days since 1970-01-01 -> y/m/d
static void CivilFromDays(int z, int& y, int& m, int& d)
{
    z += 719468;
    const int era = (z >= 0 ? z : z - 146096) / 146097;
    const unsigned doe = (unsigned)(z - era * 146097);
    const unsigned yoe = (doe - doe/1460 + doe/36524 - doe/146096) / 365;
    y = (int)(yoe) + era * 400;
    const unsigned doy = doe - (365*yoe + yoe/4 - yoe/100);
    const unsigned mp = (5*doy + 2) / 153;
    d = (int)(doy - (153*mp + 2)/5 + 1);
    m = (int)(mp + (mp < 10 ? 3 : -9));
    y += (m <= 2);
}

// weekday: 0=Sun..6=Sat
static int WeekdayFromDaysSince1970(int daysSince1970)
{
    // 1970-01-01 was Thursday => weekday=4 if 0=Sun
    int wd = (daysSince1970 + 4) % 7;
    if (wd < 0) wd += 7;
    return wd;
}

// nth Sunday in month
static int NthSunday(int y, int mo, int nth)
{
    int d0 = DaysFromCivil(y, (unsigned)mo, 1);
    int wd0 = WeekdayFromDaysSince1970(d0); // 0=Sun
    int firstSunday = 1 + ((7 - wd0) % 7);
    return firstSunday + (nth - 1) * 7;
}

// last Sunday in month
static int LastSunday(int y, int mo)
{
    static const int mdaysNorm[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
    int md = mdaysNorm[mo-1];
    bool leap = ( (y%4==0 && y%100!=0) || (y%400==0) );
    if (mo==2 && leap) md = 29;

    int dLast = DaysFromCivil(y,(unsigned)mo,(unsigned)md);
    int wdLast = WeekdayFromDaysSince1970(dLast);
    int offset = wdLast; // steps back to Sunday
    return md - offset;
}

// US DST rules (date-level sufficient for NFP 08:30 ET):
// - since 2007: starts 2nd Sunday March, ends 1st Sunday Nov
// - 2000-2006: starts 1st Sunday April, ends last Sunday Oct
static bool IsUSDST_Date(int y, int mo, int d)
{
    int startMo, startDay, endMo, endDay;

    if (y >= 2007)
    {
        startMo = 3;  startDay = NthSunday(y, 3, 2);
        endMo   = 11; endDay   = NthSunday(y, 11, 1);
    }
    else
    {
        startMo = 4;  startDay = NthSunday(y, 4, 1);
        endMo   = 10; endDay   = LastSunday(y, 10);
    }

    if (mo < startMo || mo > endMo) return false;
    if (mo > startMo && mo < endMo) return true;
    if (mo == startMo) return (d >= startDay);
    if (mo == endMo)   return (d < endDay);
    return false;
}

// first Friday of month
static int FirstFridayDay(int y, int mo)
{
    int d0 = DaysFromCivil(y, (unsigned)mo, 1);
    int wd0 = WeekdayFromDaysSince1970(d0); // 0=Sun..6=Sat
    // Friday = 5
    int delta = (5 - wd0 + 7) % 7;
    return 1 + delta;
}

// minimal exception list (shifted NFP releases)
struct SimpleDate { int y, mo, d; };
static const SimpleDate NFP_EXCEPTIONS[] =
{
    {2010, 1, 8},
    {2016, 1, 8},
    {2021, 1, 8},
};

static bool IsNfpException(int y, int mo, int& outDay)
{
    int n = (int)(sizeof(NFP_EXCEPTIONS) / sizeof(NFP_EXCEPTIONS[0]));
    for (int i = 0; i < n; i++)
    {
        if (NFP_EXCEPTIONS[i].y == y && NFP_EXCEPTIONS[i].mo == mo)
        {
            outDay = NFP_EXCEPTIONS[i].d;
            return true;
        }
    }
    return false;
}

static TDateTime MakeUTC_FromYMDHM(int y, int mo, int d, int hh, int mm)
{
    // TDateTime day 0 is 1899-12-30; 1970-01-01 is 25569
    const double TD_1970 = 25569.0;
    int daysSince1970 = DaysFromCivil(y, (unsigned)mo, (unsigned)d);
    double frac = ((double)(hh * 60 + mm)) / 1440.0;
    return TD_1970 + (double)daysSince1970 + frac;
}

static TDateTime NFP_UTC_ForMonth(int y, int mo)
{
    int day = FirstFridayDay(y, mo);
    (void)IsNfpException(y, mo, day); // override if exception exists

    bool isDst = IsUSDST_Date(y, mo, day);

    // 08:30 ET => 13:30 UTC (EST) OR 12:30 UTC (EDT)
    int hhUTC = isDst ? 12 : 13;
    int mmUTC = 30;

    return MakeUTC_FromYMDHM(y, mo, day, hhUTC, mmUTC);
}

// Returns true if within +/- minutes of NFP event time
static bool InNfpBlackoutUTC(int minutes)
{
    if (minutes <= 0) return false;

    TDateTime now = Time(0);

    // derive y/mo from "now" using day count
    int nowDaysSince1970 = (int)floor(now - 25569.0);
    int y, mo, d;
    CivilFromDays(nowDaysSince1970, y, mo, d);

    // check prev, current, next month (covers month edges)
    int yPrev = y, moPrev = mo - 1;
    if (moPrev < 1) { moPrev = 12; yPrev--; }

    int yNext = y, moNext = mo + 1;
    if (moNext > 12) { moNext = 1; yNext++; }

    TDateTime tA = NFP_UTC_ForMonth(yPrev, moPrev);
    TDateTime tB = NFP_UTC_ForMonth(y, mo);
    TDateTime tC = NFP_UTC_ForMonth(yNext, moNext);

    double winDays = (double)minutes / 1440.0;

    if (fabs(now - tA) <= winDays) return true;
    if (fabs(now - tB) <= winDays) return true;
    if (fabs(now - tC) <= winDays) return true;

    return false;
}

//----------------------------------------------------
// DAILY LOSS LIMIT (losses attributed to OPEN DAY in Brisbane)
//----------------------------------------------------
static int BrisbaneDayIdFromUTC(TDateTime utc)
{
    // Brisbane = UTC+10 (no DST)
    TDateTime bris = utc + (10.0 / 24.0);
    return (int)floor(bris);
}

struct TicketOpenDay
{
    int ticket;
    int openDayId;  // Brisbane day id
};

#define MAX_TRACKED_TICKETS 256
static TicketOpenDay g_ticketDays[MAX_TRACKED_TICKETS];
static int g_ticketDaysCount = 0;

// previous-bar snapshot of *open* trade tickets
static int g_prevOpenTickets[MAX_TRACKED_TICKETS];
static int g_prevOpenTicketsCount = 0;

// per-day loss counter (small ring)
struct DayLossCount
{
    int dayId;   // Brisbane day id
    int losses;
};
#define MAX_DAY_BUCKETS 32
static DayLossCount g_dayLoss[MAX_DAY_BUCKETS];
static int g_dayLossCount = 0;

static int SafeTicketDayCount()
{
    if (g_ticketDaysCount < 0) return 0;
    if (g_ticketDaysCount > MAX_TRACKED_TICKETS) return MAX_TRACKED_TICKETS;
    return g_ticketDaysCount;
}

static int SafePrevTicketsCount()
{
    if (g_prevOpenTicketsCount < 0) return 0;
    if (g_prevOpenTicketsCount > MAX_TRACKED_TICKETS) return MAX_TRACKED_TICKETS;
    return g_prevOpenTicketsCount;
}

static int SafeDayLossCount()
{
    if (g_dayLossCount < 0) return 0;
    if (g_dayLossCount > MAX_DAY_BUCKETS) return MAX_DAY_BUCKETS;
    return g_dayLossCount;
}

static int FindTicketIndex(int ticket)
{
    int n = SafeTicketDayCount();
    for (int i = 0; i < n; i++)
        if (g_ticketDays[i].ticket == ticket) return i;
    return -1;
}

static void ShiftTicketDaysLeftBy1()
{
    for (int i = 1; i < MAX_TRACKED_TICKETS; i++)
        g_ticketDays[i - 1] = g_ticketDays[i];
}

static void TrackTicketOpenDayIfMissing(int ticket, TDateTime openTimeUTC)
{
    if (ticket <= 0) return;
    if (FindTicketIndex(ticket) >= 0) return;

    if (g_ticketDaysCount >= MAX_TRACKED_TICKETS)
    {
        ShiftTicketDaysLeftBy1();
        g_ticketDaysCount = MAX_TRACKED_TICKETS - 1;
    }

    if (g_ticketDaysCount < 0) g_ticketDaysCount = 0;
    if (g_ticketDaysCount > MAX_TRACKED_TICKETS) g_ticketDaysCount = MAX_TRACKED_TICKETS;

    if (g_ticketDaysCount < MAX_TRACKED_TICKETS)
    {
        g_ticketDays[g_ticketDaysCount].ticket = ticket;
        g_ticketDays[g_ticketDaysCount].openDayId = BrisbaneDayIdFromUTC(openTimeUTC);
        g_ticketDaysCount++;
    }
}

static void ShiftDayLossLeftBy1()
{
    for (int i = 1; i < MAX_DAY_BUCKETS; i++)
        g_dayLoss[i - 1] = g_dayLoss[i];
}

static int GetLossBucketIndex(int dayId)
{
    int n = SafeDayLossCount();
    for (int i = 0; i < n; i++)
        if (g_dayLoss[i].dayId == dayId) return i;
    return -1;
}

static int GetLossesForDay(int dayId)
{
    int idx = GetLossBucketIndex(dayId);
    if (idx < 0) return 0;
    if (idx >= MAX_DAY_BUCKETS) return 0;
    return g_dayLoss[idx].losses;
}

static void IncrementLossForDay(int dayId)
{
    int idx = GetLossBucketIndex(dayId);
    if (idx < 0)
    {
        if (g_dayLossCount >= MAX_DAY_BUCKETS)
        {
            ShiftDayLossLeftBy1();
            g_dayLossCount = MAX_DAY_BUCKETS - 1;
        }

        if (g_dayLossCount < 0) g_dayLossCount = 0;
        if (g_dayLossCount > MAX_DAY_BUCKETS) g_dayLossCount = MAX_DAY_BUCKETS;

        if (g_dayLossCount < MAX_DAY_BUCKETS)
        {
            g_dayLoss[g_dayLossCount].dayId = dayId;
            g_dayLoss[g_dayLossCount].losses = 0;
            idx = g_dayLossCount;
            g_dayLossCount++;
        }
    }

    if (idx >= 0 && idx < MAX_DAY_BUCKETS)
        g_dayLoss[idx].losses++;
}

static bool TicketInArray(int ticket, int* arr, int n)
{
    if (n < 0) n = 0;
    if (n > MAX_TRACKED_TICKETS) n = MAX_TRACKED_TICKETS;

    for (int i = 0; i < n; i++)
        if (arr[i] == ticket) return true;
    return false;
}

static double SafeNetProfit()
{
    return OrderProfit();
}

// Build current list of open MARKET position tickets; also ensure openDay is tracked
static void BuildCurrentOpenTradeTickets(char* ccy, int* outArr, int& outCount)
{
    outCount = 0;
    int total = OrdersTotal();
    for (int i = total - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if (strcmp(OrderSymbol(), ccy) != 0) continue;
        if (OrderMagicNumber() != MagicNumber) continue;

        TTradePositionType t = OrderType();
        if (t != tp_Buy && t != tp_Sell) continue;

        int ticket = OrderTicket();
        if (ticket <= 0) continue;

        TrackTicketOpenDayIfMissing(ticket, OrderOpenTime());

        if (outCount < MAX_TRACKED_TICKETS)
        {
            outArr[outCount] = ticket;
            outCount++;
        }
    }
}

// When a ticket disappears from MODE_TRADES, try select it in history and count loss (against open day)
static void ProcessClosedTickets_AsLosses(int* curTickets, int curCount)
{
    int prevN = SafePrevTicketsCount();
    if (curCount < 0) curCount = 0;
    if (curCount > MAX_TRACKED_TICKETS) curCount = MAX_TRACKED_TICKETS;

    for (int i = 0; i < prevN; i++)
    {
        int ticket = g_prevOpenTickets[i];
        if (TicketInArray(ticket, curTickets, curCount))
            continue; // still open

        int tIdx = FindTicketIndex(ticket);
        if (tIdx < 0)
            continue; // no open-day record -> ignore

        int openDayId = g_ticketDays[tIdx].openDayId;

        // Try select closed order from history by ticket
        if (OrderSelect(ticket, SELECT_BY_TICKET, MODE_HISTORY))
        {
            TTradePositionType t = OrderType();
            if (t == tp_Buy || t == tp_Sell)
            {
                double net = SafeNetProfit();
                if (net < 0.0)
                {
                    IncrementLossForDay(openDayId);
                    if (DebugLogs)
                        PrintF("LOSS COUNTED: ticket=%d net=%.2f openDayId=%d lossesNow=%d",
                               ticket, net, openDayId, GetLossesForDay(openDayId));
                }
                else
                {
                    if (DebugLogs)
                        PrintF("CLOSE (not loss): ticket=%d net=%.2f openDayId=%d",
                               ticket, net, openDayId);
                }
            }
        }
        else
        {
            if (DebugLogs)
                PrintF("WARN: Could not OrderSelect(ticket=%d, MODE_HISTORY) to count loss", ticket);
        }
    }
}

static bool DailyLossLimitHitToday()
{
    if (MaxLossesPerDay < 0) return false;
    int todayId = BrisbaneDayIdFromUTC(Time(0));
    int lossesToday = GetLossesForDay(todayId);
    return (lossesToday >= MaxLossesPerDay);
}

//----------------------------------------------------
// REQUIRED FT6 Exported Functions (MUST be exact names)
//----------------------------------------------------
extern "C" __declspec(dllexport) void __stdcall InitStrategy()
{
    StrategyShortName(STRAT_NAME);
    StrategyDescription(STRAT_DESC);

    RegOption(OPT_CCY,  ot_Currency,  &Currency);
    RegOption(OPT_TF,   ot_TimeFrame, &Timeframe);

    RegOption(OPT_LOTS,  ot_Double,  &Lots);
    RegOption(OPT_MAGIC, ot_Integer, &MagicNumber);

    RegOption(OPT_TRENDN,  ot_Integer, &TrendLookbackBars);
    RegOption(OPT_OFFPIPS, ot_Double,  &EntryOffsetPips);
    RegOption(OPT_MINBOD,  ot_Double,  &MinBodyPips);

    RegOption(OPT_ATRPER,   ot_Integer, &ATR_Period);
    RegOption(OPT_ATRMULT,  ot_Double,  &ATR_Multiplier);
    RegOption(OPT_MINR,     ot_Double,  &Min_R_Multiple);

    RegOption(OPT_PENDEXP,   ot_Integer, &PendingExpiryBars);
    RegOption(OPT_CANCELOLD, ot_Boolean, &CancelOldPending);
    RegOption(OPT_ONEONLY,   ot_Boolean, &OnlyOneTradeAtATime);

    RegOption(OPT_NOTSUTC, ot_Integer, &NoTradeStartUTC_Hour);
    RegOption(OPT_NOTENDU, ot_Integer, &NoTradeEndUTC_Hour);

    RegOption(OPT_SESSFILT, ot_Boolean, &UseSessionEntryFilter);
    RegOption(OPT_SESSWIN,  ot_Integer, &SessionWindowMinutes);

    RegOption(OPT_LDNUTC, ot_Integer, &LondonOpenUTC_Hour);
    RegOption(OPT_FFUTC,  ot_Integer, &FrankfurtOpenUTC_Hour);
    RegOption(OPT_NYUTC,  ot_Integer, &NewYorkOpenUTC_Hour);

    RegOption(OPT_NFPUSE, ot_Boolean, &UseNfpBlackout);
    RegOption(OPT_NFPMIN, ot_Integer, &NfpBlackoutMinutes);

    RegOption(OPT_DLOSSUSE, ot_Boolean, &UseDailyLossLimit);
    RegOption(OPT_DLOSSMAX, ot_Integer, &MaxLossesPerDay);

    RegOption(OPT_DEBUG, ot_Boolean, &DebugLogs);

    g_lastBarTime = 0.0;

    g_ticketDaysCount = 0;
    g_prevOpenTicketsCount = 0;
    g_dayLossCount = 0;
}

extern "C" __declspec(dllexport) void __stdcall DoneStrategy()
{
    if (Currency) free(Currency);
}

extern "C" __declspec(dllexport) void __stdcall ResetStrategy()
{
    g_lastBarTime = 0.0;

    g_ticketDaysCount = 0;
    g_prevOpenTicketsCount = 0;
    g_dayLossCount = 0;
}

extern "C" __declspec(dllexport) void __stdcall GetSingleTick()
{
    // --- Currency resolve ---
    // If user didn't set Currency in FT6 options (blank), default to chart Symbol()
    char* ccy = Currency;

    if (ccy == NULL || ccy[0] == '\0')
    {
        ccy = Symbol();
        if (DebugLogs) PrintF("INFO: Currency input blank -> using ChartSymbol=%s", ccy);
    }

    SetCurrencyAndTimeframe(ccy, Timeframe);

    // Only run on new bars
    if (!IsNewBar())
        return;

    // ---- DAILY LOSS LIMIT TRACKING ----
    int curOpenTickets[MAX_TRACKED_TICKETS];
    int curOpenCount = 0;

    BuildCurrentOpenTradeTickets(ccy, curOpenTickets, curOpenCount);
    ProcessClosedTickets_AsLosses(curOpenTickets, curOpenCount);

    // update snapshot for next bar
    if (curOpenCount < 0) curOpenCount = 0;
    if (curOpenCount > MAX_TRACKED_TICKETS) curOpenCount = MAX_TRACKED_TICKETS;

    memcpy(g_prevOpenTickets, curOpenTickets, sizeof(int) * curOpenCount);
    g_prevOpenTicketsCount = curOpenCount;

    if (DebugLogs)
    {
        int inSess = UseSessionEntryFilter ? (InAnySessionEntryWindowUTC() ? 1 : 0) : 1;
        int inNT   = InNoTradeWindowUTC() ? 1 : 0;
        int inNFP  = (UseNfpBlackout && InNfpBlackoutUTC(NfpBlackoutMinutes)) ? 1 : 0;

        int todayId = BrisbaneDayIdFromUTC(Time(0));
        int lossesToday = GetLossesForDay(todayId);

        PrintF("STATE: %s TF=%d Bars=%d SessOK=%d NoTrade=%d NFPBlackout=%d LossesToday=%d/%d ExpBars=%d TrendN=%d ATR_Period=%d",
               ccy, Timeframe, Bars(), inSess, inNT, inNFP, lossesToday, MaxLossesPerDay,
               PendingExpiryBars, TrendLookbackBars, ATR_Period);
    }

    // Always expire stale LIMITs first
    CancelStaleLimitPendings(ccy, PendingExpiryBars);

    // NFP blackout enforcement: liquidate + cancel + block new entries
    if (UseNfpBlackout && InNfpBlackoutUTC(NfpBlackoutMinutes))
    {
        LiquidateNow(ccy, "NFP BLACKOUT");
        return;
    }

    // Brisbane no-trade window enforcement
    if (InNoTradeWindowUTC())
    {
        LiquidateNow(ccy, "NO-TRADE WINDOW");
        return;
    }

    // Daily loss limit enforcement (NEW ENTRIES ONLY)
    if (UseDailyLossLimit)
    {
        if (DailyLossLimitHitToday())
        {
            LogBlock("DailyLossLimit: max losses reached for today (Brisbane day, losses counted by OPEN day)");
            return;
        }
    }

    if (Bars() < 50)
    {
        LogBlock("Bars < 50");
        return;
    }

    // Session entry window enforcement (NEW entries only)
    if (UseSessionEntryFilter && !InAnySessionEntryWindowUTC())
    {
        LogBlock("Outside session entry window (London/Frankfurt/NY +/- window)");
        return;
    }

    // Only one position/pending at a time
    if (OnlyOneTradeAtATime)
    {
        if (HasOurOpenPosition(ccy)) { LogBlock("OnlyOneTradeAtATime: already has open position"); return; }
        if (HasOurPending(ccy))      { LogBlock("OnlyOneTradeAtATime: already has pending order"); return; }
    }

    // Trend filter
    int trendDir = GetTrendDir_LastN(TrendLookbackBars);
    if (trendDir == 0)
    {
        LogBlock("Trend tie/none (bull == bear in lookback)");
        return;
    }

    // Entry rule (last 2 COMPLETE bodies)
    double o1 = Open(1), c1 = Close(1);
    double o2 = Open(2), c2 = Close(2);

    bool bar1Bear = (c1 < o1);
    bool bar1Bull = (c1 > o1);
    bool bar2Bear = (c2 < o2);
    bool bar2Bull = (c2 > o2);

    // Optional body-size filter
    if (MinBodyPips > 0.0)
    {
        double pip = PipSize();
        double body1 = fabs(c1 - o1) / pip;
        double body2 = fabs(c2 - o2) / pip;
        if (body1 < MinBodyPips || body2 < MinBodyPips)
        {
            LogBlock("MinBodyPips filter failed");
            return;
        }
    }

    bool wantBuy  = (trendDir == 1  && bar1Bear && bar2Bear);
    bool wantSell = (trendDir == -1 && bar1Bull && bar2Bull);

    if (!wantBuy && !wantSell)
    {
        if (DebugLogs)
        {
            PrintF("BLOCK: EntryRuleFail (trendDir=%d bar1[%s] bar2[%s])",
                   trendDir,
                   (bar1Bear ? "BEAR" : (bar1Bull ? "BULL" : "DOJI")),
                   (bar2Bear ? "BEAR" : (bar2Bull ? "BULL" : "DOJI")));
        }
        return;
    }

    // Cancel old pendings (optional)
    if (CancelOldPending)
        CancelOurPendings(ccy);

    // SL/TP from ATR(N) on bar[1]
    int atrPeriod = ATR_Period;
    if (atrPeriod < 1) atrPeriod = 1;

    double atrN = ATR_OnBar1(atrPeriod);
    if (atrN <= 0.0) { LogBlock("ATR <= 0 (not enough bars or bad ATR_Period)"); return; }

    double slDist = atrN * ATR_Multiplier;
    if (slDist <= 0.0) { LogBlock("SL distance <= 0"); return; }

    double pip = PipSize();
    double offset = EntryOffsetPips * pip;

    double entryRaw = c1;
    int digits = Digits();

    int orderHandle = -1;

    // BUY LIMIT
    if (wantBuy)
    {
        double entry = RoundToDigits(entryRaw - offset, digits);
        double sl    = RoundToDigits(entry - slDist, digits);

        double R  = fabs(entry - sl);
        double tp = RoundToDigits(entry + (Min_R_Multiple * R), digits);

        bool ok = SendPendingOrder(ccy, op_BuyLimit, Lots, sl, tp, entry,
                                   COMM_BUY, MagicNumber, orderHandle);

        if (DebugLogs)
            PrintF(ok ? "ORDER OK: BUY_LIMIT handle=%d entry=%.5f sl=%.5f tp=%.5f"
                      : "ORDER FAIL: BuyLimit entry=%.5f sl=%.5f tp=%.5f",
                   orderHandle, entry, sl, tp);

        return;
    }

    // SELL LIMIT
    if (wantSell)
    {
        double entry = RoundToDigits(entryRaw + offset, digits);
        double sl    = RoundToDigits(entry + slDist, digits);

        double R  = fabs(sl - entry);
        double tp = RoundToDigits(entry - (Min_R_Multiple * R), digits);

        bool ok = SendPendingOrder(ccy, op_SellLimit, Lots, sl, tp, entry,
                                   COMM_SELL, MagicNumber, orderHandle);

        if (DebugLogs)
            PrintF(ok ? "ORDER OK: SELL_LIMIT handle=%d entry=%.5f sl=%.5f tp=%.5f"
                      : "ORDER FAIL: SellLimit entry=%.5f sl=%.5f tp=%.5f",
                   orderHandle, entry, sl, tp);

        return;
    }
}
