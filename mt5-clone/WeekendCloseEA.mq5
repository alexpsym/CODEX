//+------------------------------------------------------------------+
//|   Weekend Close EA                                               |
//|   Closes all positions before the weekend                        |
//+------------------------------------------------------------------+
#property copyright "2024"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- trade object
CTrade trade;

//+------------------------------------------------------------------+
//| Helper: get day of week (0=Sunday..6=Saturday)                    |
//+------------------------------------------------------------------+
int GetDayOfWeek(datetime t)
  {
   MqlDateTime tm;
   TimeToStruct(t,tm);
   return tm.day_of_week;
  }

input int    TimerSeconds   = 60; // interval for timer checks
input string LogFileName    = "WeekendCloseLog.csv"; // log file

//+------------------------------------------------------------------+
//| Helper: calculate second Sunday of March                         |
//+------------------------------------------------------------------+
datetime SecondSundayMarch(int year)
  {
   MqlDateTime dt={0};
   dt.year=year;
   dt.mon=3;
   dt.day=1;
   datetime d=StructToTime(dt);
   while(GetDayOfWeek(d)!=0)
      d+=86400;
   d+=7*86400; // second Sunday
   return d;
  }

//+------------------------------------------------------------------+
//| Helper: calculate first Sunday of November                       |
//+------------------------------------------------------------------+
datetime FirstSundayNovember(int year)
  {
   MqlDateTime dt={0};
   dt.year=year;
   dt.mon=11;
   dt.day=1;
   datetime d=StructToTime(dt);
   while(GetDayOfWeek(d)!=0)
      d+=86400;
   return d;
  }

//+------------------------------------------------------------------+
//| Check if given time is during US DST                             |
//+------------------------------------------------------------------+
bool USDST(datetime t)
  {
   MqlDateTime tm; TimeToStruct(t,tm);
   datetime start=SecondSundayMarch(tm.year);
   datetime end=FirstSundayNovember(tm.year);
   return(t>=start && t<end);
  }

//+------------------------------------------------------------------+
//| Close all open positions                                         |
//+------------------------------------------------------------------+
void CloseAll()
  {
   int total=PositionsTotal();
   for(int i=total-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i);
      string symbol=PositionGetString(POSITION_SYMBOL);
      double volume=PositionGetDouble(POSITION_VOLUME);
      double price=PositionGetDouble(POSITION_PRICE_CURRENT);

      if(trade.PositionClose(ticket))
        {
         string dateStr=TimeToString(TimeLocal(),TIME_DATE|TIME_MINUTES);
         int fh=FileOpen(LogFileName,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI);
         if(fh!=INVALID_HANDLE)
           {
            if(FileSize(fh)==0)
               FileWrite(fh,"Date","Ticket","Symbol","Volume","Price");
            FileSeek(fh,0,SEEK_END);
            FileWrite(fh,dateStr,ticket,symbol,DoubleToString(volume,2),DoubleToString(price,_Digits));
            FileClose(fh);
           }
         Print("Closed ",symbol," ticket ",ticket);
        }
      else
         Print("Failed to close ",symbol," ticket ",ticket,": ",GetLastError());
     }
  }

//+------------------------------------------------------------------+
//| Check time and close if needed                                   |
//+------------------------------------------------------------------+
void CheckWeekendClose()
  {
   datetime now=TimeLocal();
   MqlDateTime tm; TimeToStruct(now,tm);
   if(GetDayOfWeek(now)==6) // Saturday
     {
      bool dst=USDST(now);
      int cutoff=dst?5:6;
      if(tm.hour>=cutoff)
         CloseAll();
     }
  }

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(TimerSeconds);
   CheckWeekendClose();
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
//| Timer event handler                                               |
//+------------------------------------------------------------------+
void OnTimer()
  {
   CheckWeekendClose();
  }

//+------------------------------------------------------------------+
