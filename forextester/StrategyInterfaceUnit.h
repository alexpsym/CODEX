//---------------------------------------------------------------------------
// Strategy interface ver 1.11 (c) Forex Tester Software 2013
//
// What's new:
//
// ver 1.11
//
//   1. Added new function OrderSwap - get order swap
//
// ver 1.10
//
//   1. Added new function ObjectGetText - read the object's description
//
// ver 1.9 
//   1. Added new function CloseOrderPartial - close part of position
//
// ver 1.8
//   1. Added new function iBarShift - returns index of a bar by its time
//   4. Added new function iHighest - returns index of a bar with the
//      highest value
//   5. Added new function iLowest - returns index of a bar with the
//      lowest value
//   6. New constants MODE_OPEN .. MODE_TIME
//   7. Added new external parameter type - ot_Color, represents color
//
// ver 1.7
//   1. Added new function TimeCurrent: TDateTime - current server time
//   2. Added new procedure SetIndicatorBuffStyle - allows to set indicator
//      line styles and colors
//
// ver 1.6
//   1. Constants of timeframes changed
//                   now      before
//      PERIOD_M1  = 1;       0
//      PERIOD_M5  = 5;       1
//      PERIOD_M15 = 15;      2
//      PERIOD_H1  = 60;      3
//      PERIOD_H4  = 60*4;    4
//      PERIOD_D1  = 60*24;   5
//      PERIOD_W1  = 60*24*7; 6
//
//   2. Timeframe should be defined with number of minutes in it
//
// ver 1.5
//   1. Added new object functions
//      ObjectCreate
//      ObjectDelete
//      ObjectExists
//      ObjectType
//      ObjectSet
//      ObjectGet
//      ObjectsDeleteAll
//      ObjectSetText
//   2. Added function to get interface verion - GetInterfaceVersion
//
// ver 1.4
//   1. Added new procedures Pause/Resume, that allow to pause testing from
//      strategy and continue
//
// ver 1.3
//   1. Added additional parameters Comment and MagicNumber to functions
//      SendInstantOrder and SendPendingOrder
//   2. Added new function OrderMagicNumber and OrderComment
//
// ver 1.2
//   1. Added new functions:
//      CreateIndicator, GetIndicatorValue
//
// ver 1.1
//   1. Added new functions to get account information:
//      AccountBalance, AccountEquity, AccountMargin, AccountFreeMargin,
//      AccountLeverage
//   2. Added debug function - Breakpoint
//
// ver 1.0
//
//---------------------------------------------------------------------------

#include <stdio.h>


#define EXPORT __declspec(dllexport)

#define PChar char*
#define TDateTime double
#define TColor unsigned long
#define PCurrencyInfo TCurrencyInfo*
#define PInterfaceProcRec TInterfaceProcRec*


typedef unsigned short    Word;
typedef unsigned long     LongWord;

enum TPenStyle {psSolid, psDash, psDot, psDashDot, psDashDotDot, psClear, psInsideFrame};

#define clRed       RGB(0xFF,0,0) 
#define clGreen     RGB(0,0xFF,0) 
#define clBlue      RGB(0,0,0xFF) 
#define clYellow    RGB(0xFF,0xFF,0)


//~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


#pragma pack(push, 1)

//-------------------------------------------
// currency information
//-------------------------------------------
struct TCurrencyInfo {
  PChar Symbol;               // currency name
  int Digits;                 // number of digits after '.'
  int spread;                 // spread in pips
  double Point;               // minimal currency point
  int lot;                    // 1 lot cost
  PChar curr;                 // lot currency
  double SwapLong;            // swap points long
  double SwapShort;           // swap points short
};

//-------------------------------------------
// trade position
//-------------------------------------------
struct TTradePosition {
  int ticket;                  // order handle
  TDateTime OpenTime;          // order open time
  TDateTime CloseTime;         // order close time
  int PosType;                 // position type (tp_Buy ... tp_Credit)
  double lot;                  // lot
  PChar Symbol;                // currency name
  double OpenPrice;            // open price
  double ClosePrice;           // close price
  double StopLoss;             // stop loss
  double TakeProfit;           // take profit
  double commission;           // comission
  double swap;                 // swap
  double profit;               // profit in USD
  int ProfitPips;              // profit in pips
  PChar comments;              // comments
  double margin;               // margin
};

// option type
enum TOptionType {
  ot_Longword  = 0,
  ot_Integer   = 1,
  ot_Double    = 2,
  ot_String    = 3,
  ot_Boolean   = 4,
  ot_EnumType  = 5,
  ot_TimeFrame = 6,
  ot_Currency  = 7,
  ot_LineStyle = 8,
  ot_Separator = 9,
  ot_Reserved0 = 10,
  ot_Color     = 11
};

// search mode
enum TSearchMode {
  MODE_TRADES  = 0,
  MODE_HISTORY = 1
};

// order select mode
enum TOrderSelectMode {
  SELECT_BY_POS     = 0,
  SELECT_BY_TICKET  = 1
};

// market info constants
enum TMarketInfo {
  MODE_BID   = 0,
  MODE_ASK   = 1
};

// trade position type
enum TTradePositionType {
  tp_Buy       = 0,
  tp_Sell      = 1,
  tp_BuyLimit  = 2,
  tp_SellLimit = 3,
  tp_BuyStop   = 4,
  tp_SellStop  = 5,
  tp_Balance   = 6,
  tp_Credit    = 7
};

// instant operation type
enum TInstantOrderType {
  op_Buy       = 0,
  op_Sell      = 1
};

// pending operation type
enum TPendingOrderType {
  op_BuyLimit  = 0,
  op_BuyStop   = 1,
  op_SellLimit = 2,
  op_SellStop  = 3
};

// object types
enum TObjectType {
  obj_AnyObject        = 0,
  obj_VLine            = 1,
  obj_HLine            = 2,
  obj_TrendLine        = 3,
  obj_Ray              = 4,
  obj_PolyLine         = 5,
  obj_FiboRetracement  = 6,
  obj_FiboTimeZones    = 7,
  obj_FiboArc          = 8,
  obj_FiboFan          = 9,
  obj_AndrewsPitchfork = 10,
  obj_Text             = 11,
  obj_TextLabel        = 12
};


// indicator window
enum TOutputWindow  {
  ow_ChartWindow    = 0,
  ow_SeparateWindow = 1
};
#pragma pack(pop)

// timeframes
#define PERIOD_M1  1
#define PERIOD_M5  5
#define PERIOD_M15 15
#define PERIOD_H1  60
#define PERIOD_H4  60*4
#define PERIOD_D1  60*24
#define PERIOD_W1  60*24*7

// object properties constants
#define OBJPROP_TIME1              0
#define OBJPROP_PRICE1             1
#define OBJPROP_TIME2              2
#define OBJPROP_PRICE2             3
#define OBJPROP_TIME3              4
#define OBJPROP_PRICE3             5
#define OBJPROP_COLOR              6
#define OBJPROP_STYLE              7
#define OBJPROP_WIDTH              8
#define OBJPROP_ELLIPSE           11
#define OBJPROP_FIBOCLOSEDENDS    12
#define OBJPROP_FIBOSHOWPRICE     13
#define OBJPROP_FIBOENDWIDTH      14
#define OBJPROP_FIBOLEVELS        15
#define OBJPROP_FIBOLEVELN        16
#define OBJPROP_LEVELCOLOR        17
#define OBJPROP_LEVELSTYLE        18
#define OBJPROP_LEVELWIDTH        19
#define OBJPROP_LEVELVALUE        20
#define OBJPROP_FONTSIZE          21
#define OBJPROP_HALIGNMENT        22
#define OBJPROP_VALIGNMENT        23
#define OBJPROP_FONTNAME          24
#define OBJPROP_XDISTANCE         25
#define OBJPROP_YDISTANCE         26
#define OBJPROP_TEXT              27
#define OBJPROP_NAME              28
#define OBJPROP_SCREENCOORDS      29
#define OBJPROP_SCRHALIGNMENT     30
#define OBJPROP_SCRVALIGNMENT     31

// text alignment
#define tlTop             0
#define tlCenter          1
#define tlBottom          2

#define taLeftJustify     0
#define taRightJustify    1
#define taCenter          2

#define MODE_OPEN    0
#define MODE_LOW     1
#define MODE_HIGH    2
#define MODE_CLOSE   3
#define MODE_VOLUME  4
#define MODE_TIME    5


//~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


// Get currency information
// Symbol - currency name
// info - pointer to TCurrencyInfo record
// result: true if success
bool GetCurrencyInfo(PChar Symbol, PCurrencyInfo& info);

// Register option
// OptionName - name of option
// OptionType - see TOptionType enumeration
// option - pointer to first byte
void RegOption(PChar OptionName, TOptionType OptionType, void* option);

// Add separator to options dialog
// text - separator caption
void AddSeparator(PChar text);

// Add option value (for drop down box options)
// OptionName - name of previously registered option
// value - new value in drop down box
void AddOptionValue(PChar OptionName, PChar value);

// Set option range (for integer, longword and double types)
// OptionName - name of previously registered option
// LowValue - lowest value in range
// HighValue - highest value in range
// note: in dialog box after entering option value it will be checked
//       to be in range LowValue <= value <= HighValue
void SetOptionRange(PChar OptionName, double LowValue, double HighValue);

// Set option digits (for double options) defines number of digits after point
// OptionName - name of option
// digits - number of digits after point
void SetOptionDigits(PChar OptionName, Word digits);

// Print text in "Journal" window in ForexTester
// s - text
void Print(PChar s);

// Set strategy short name
// name - short name
void StrategyShortName(PChar name);

// Set strategy description
// desc - description
void StrategyDescription(PChar desc);

// Set currency and timeframe (after that such functions will be enabled:
// Bid, Ask, Symbol, Point, Digits, Open, Close, High, Low, Volume, Time, Bars)
// Symbol - requested currency
// TimeFrame - requested timeframe
// result: true if success
bool SetCurrencyAndTimeframe(PChar Symbol, int TimeFrame);

// Get market information
// Symbol - requested currency
// _type - requested item (MODE_BID, MODE_ASK)
double MarketInfo(PChar Symbol, TMarketInfo _type);

// Send instant order
// Symbol - currency name
// OperationType - type of operation (op_Sell, op_Buy)
// LotSize - lot size
// StopLoss - stop loss
// TakeProfit - take profit
// Comment - text comment
// MagicNumber - special number that will mark this order
// OrderHandle - returned handle of order (-1 if fail)
// result: true if success OrderHandle = ticket, false if failed OrderHandle = -1
bool SendInstantOrder(PChar Symbol, TInstantOrderType OperationType,
  double LotSize, double StopLoss, double TakeProfit, PChar Comment,
  int MagicNumber, int& OrderHandle);

// Send pending order
// Symbol - currency name
// OperationType - type of operation (op_SellLimit, op_SellStop, op_BuyLimit, op_BuyStop)
// LotSize - lot size
// StopLoss - stop loss
// TakeProfit - take profit
// ExecutionPrice - price where order should be placed
// Comment - text comment
// MagicNumber - special number that will mark this order
// OrderHandle - returned handle of order (-1 if fail)
// result: true if success OrderHandle = ticket, false if failed OrderHandle = -1
bool SendPendingOrder(PChar Symbol, TPendingOrderType OperationType,
  double LotSize, double StopLoss, double TakeProfit, double ExecutionPrice,
  PChar Comment, int MagicNumber, int& OrderHandle);

// Modify order
// OrderHandle - handle of the order
// NewPrice - new order price (only for pending order)
// StopLoss - new stop loss
// TakeProfit - new take profit
// result: true if success
bool ModifyOrder(int OrderHandle, double NewPrice, double StopLoss, double TakeProfit);

// Delete order
// OrderHandle - handle of the order
// result: true if success
bool DeleteOrder(int OrderHandle);

// Close order
// OrderHandle - handle of the order
// result: true if success
bool CloseOrder(int OrderHandle);

// Get order information
// OrderHandle - handle of the order
// info - if order is found this structure will be filled with order info
// result: true if success info will contain order information
bool GetOrderInfo(int OrderHandle, TTradePosition& info);

// Get Bid price
double Bid();

// Get Ask price
double Ask();

// Currency name
PChar Symbol();

// Currency digits after point
int Digits();

// Minimal currency point
double Point();

// Select order
// index - order index or order handle (depending on flags)
// flags - selecting flags (SELECT_BY_POS, SELECT_BY_TICKET)
// pool - where to find order (MODE_TRADES, MODE_HISTORY) only matters
//        when flags = SELECT_BY_POS
// result: true if success
bool OrderSelect(int index, TOrderSelectMode flags = SELECT_BY_POS, TSearchMode pool = MODE_TRADES);

// Get profit in dollars of selected order
double OrderProfit();

// Get profit in pips of selected order
double OrderProfitPips();

// Check if order was closed
// OrderHandle - order handle
// result: true if order is closed, otherwise false
bool OrderClosed(int OrderHandle);

// Number of closed positions
int HistoryTotal();

// Number of opened positions
int OrdersTotal();

// Open time of selected order
TDateTime OrderOpenTime();

// Close time of selected order
TDateTime OrderCloseTime();

// Lot size of selected order
double OrderLots();

// Handle of selected order
int OrderTicket();

// Type of the selected order (tp_Buy, tp_Sell, tp_BuyLimit, tp_SellLimit,
// tp_BuyStop, tp_SellStop, tp_Balance, tp_Credit)
TTradePositionType OrderType();

// Stop loss of selected order
double OrderStopLoss();

// Take profit of selected order
double OrderTakeProfit();

// Open price of selected order
double OrderOpenPrice();

// Close price of selected order
double OrderClosePrice();

// Currency of selected order
PChar OrderSymbol();

// Get order MagicNumber
int OrderMagicNumber();

// Get order comment
PChar OrderComment();

// Get order swap
double OrderSwap();

// Get open value
// Symbol - requested currency
// TimeFrame - requested timeframe
// index - shift in bars from last bar
double iOpen(PChar Symbol, int TimeFrame, int index);

// Get close value
// Symbol - requested currency
// TimeFrame - requested timeframe
// index - shift in bars from last bar
double iClose(PChar Symbol, int TimeFrame, int index);

// Get high value
// Symbol - requested currency
// TimeFrame - requested timeframe
// index - shift in bars from last bar
double iHigh(PChar Symbol, int TimeFrame, int index);

// Get low value
// Symbol - requested currency
// TimeFrame - requested timeframe
// index - shift in bars from last bar
double iLow(PChar Symbol, int TimeFrame, int index);

// Get volume
// Symbol - requested currency
// TimeFrame - requested timeframe
// index - shift in bars from last bar
double iVolume(PChar Symbol, int TimeFrame, int index);

// Get time of bar
// Symbol - requested currency
// TimeFrame - requested timeframe
// index - shift in bars from last bar
TDateTime iTime(PChar Symbol, int TimeFrame, int index);

// Get number of bars
// Symbol - requested currency
// TimeFrame - requested timeframe
int iBars(PChar Symbol, int TimeFrame);

// Get open value in default currency and timeframe
// index - shift in bars from last bar
double Open(int index);

// Get close value in default currency and timeframe
// index - shift in bars from last bar
double Close(int index);

// Get high value in default currency and timeframe
// index - shift in bars from last bar
double High(int index);

// Get low value in default currency and timeframe
// index - shift in bars from last bar
double Low(int index);

// Get volume in default currency and timeframe
// index - shift in bars from last bar
double Volume(int index);

// Get time of bar in default currency and timeframe
// index - shift in bars from last bar
TDateTime Time(int index);

// Get number of bars in default currency and timeframe
int Bars();

// Get account balance
double AccountBalance();

// Get account equity
double AccountEquity();

// Get account margin
double AccountMargin();

// Get account free margin
double AccountFreeMargin();

// Get account leverage
int AccountLeverage();

// Get account profit
double AccountProfit();

// Breakpoint
// Stop strategy execution and show debug window with text
// number - breakpoint number
// text - text to show
void Breakpoint(int number, PChar text);

// Pause - set pause mode
// text - text to show in message box, if text = '' then no message will appear
void Pause(PChar text = "");

// continue testing
void Resume();

// Create indicator and obtaind id
// Symbol - currency name
// TimeFrame - desired timeframe
// IndicatorName - case sensitive indicator name that you can see in progam
// parameters - indicator parameters separated with ';'
// result: id that will be used in GetIndicatorValue
int CreateIndicator(PChar Symbol, int TimeFrame, PChar IndicatorName, PChar parameters);

// Get indicator value
// IndicatorHandle - id obtained with CreateIndicator
// index - inder in array of values
// BufferIndex - index of buffer
// result: indicator value
double GetIndicatorValue(int IndicatorHandle, int index, int BufferIndex);

// Get interface version
void GetInterfaceVersion(int& MajorValue, int& MinorValue);

// Create object
// name - object name (must be unique)
// ObjType - type of object (see TObjectType)
// window - in what window to show object (ignored)
// time1, price1 .. time3, price3 - time and price coordinates of object
// function returns true if successful
bool ObjectCreate(PChar name, TObjectType ObjType, int window,
  TDateTime time1, double price1, TDateTime time2 = 0, double price2 = 0,
  TDateTime time3 = 0, double price3 = 0);

// Delete object by name
// name - name of the object
void ObjectDelete(PChar name);

// Check if object already exists
// name  - name of the object
bool ObjectExists(PChar name);

// Get object type
// name - name of the object
TObjectType ObjectType(PChar name);

// Set object property
// name - name of the object
// index - property index
// value - new value
// function returns true if successful
bool ObjectSet(PChar name, int index, double value);

// Get object property
// name - name of the object
// index - property index
double ObjectGet(PChar name, int index);

// Delete all objects
// window - window where to delete
// ObjType - type of objects
void ObjectsDeleteAll(int window = 0, TObjectType ObjType = obj_AnyObject);

// set text/description for object
// name - name of the object
// text - text to set
// FontSize - font size
// FontName - font name
// Color - font color
// function returns true if successful
bool ObjectSetText(PChar name, PChar text, int FontSize = 12,
  PChar FontName = "Arial", TColor Color = clRed);

// get text/description of the object
// name - name of the object
// function returns text or empty string if failed
PChar ObjectGetText(PChar name);

// get current server time
TDateTime  TimeCurrent();

// set indicator's buffer style
// IndicatorHandle - indicator's handle received with CreateIndicator
// BuffIndex - index of indicator's buffer where you want to change style
// _style - line style
// width - line width
// clr - line color
void SetIndicatorBuffStyle(int IndicatorHandle, int BuffIndex, TPenStyle _style,
  int width, TColor color);

// get bar index by its time
// Symbol - requested currency
// TimeFrame - requested timeframe
// time - time of the bar
// Exact - if this parameter is true then time should be exactly the same
//         otherwise will be returned index of the bar where time is
//         between time[index] and time[index + 1]
// function returns index of the bar if successful, and -1 if failed
int iBarShift(PChar Symbol, int TimeFrame, TDateTime time, bool Exact);

// get highest value in array
// Symbol - requested currency
// TimeFrame - requested timeframe
// _type - type of value (see constants MODE_OPEN .. MODE_TIME)
// count - number of bars to search
// index - first index to start searching
// function returns index of the bar if successful, and -1 if failed
int iHighest(PChar Symbol, int TimeFrame, int _type, int count, int index);

// get lowest value in array
// Symbol - requested currency
// TimeFrame - requested timeframe
// _type - type of value (see constants MODE_OPEN .. MODE_TIME)
// count - number of bars to search
// index - first index to start searching
// function returns index of the bar if successful, and -1 if failed
int iLowest(PChar Symbol, int TimeFrame, int _type, int count, int index);

// close part of position
// OrderHandle - handle of the order
// LotSize - part of position to close
// function returns "true" if successful
bool CloseOrderPartial(int OrderHandle, double LotSize);


int Sell(double LotSize, double StopLoss, double TakeProfit);
int Buy(double LotSize, double StopLoss, double TakeProfit);
PChar StrTime(TDateTime DateTime);
int GetStopLossPoints(int OrderHandle);
int GetTakeProfitPoints(int OrderHandle);
void SetStopLossPoints(int OrderHandle, int NewStopLoss);
void SetTakeProfitPoints(int OrderHandle, int NewTakeProfit);


bool _SendInstantOrder(PChar Symbol, TInstantOrderType OperationType,
  double price, double LotSize, double StopLoss, double TakeProfit, PChar Comment,
  int MagicNumber, int& OrderHandle);

bool _CloseOrder(int OrderHandle, double price);

//~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

// indicator interface procedures
typedef bool (__stdcall *TGetCurrencyInfoFunc) (void* ptr, PChar Symbol, PCurrencyInfo& info);
typedef void (__stdcall *TSetPropertyProc) (void* ptr, int PropertyID, void* value);
typedef void (__stdcall *TRegOptionProc) (void* ptr, PChar OptionName, int OptionType, void* OptionPtr);
typedef void (__stdcall *TAddOptionValueProc) (void* ptr, PChar OptionName, PChar value);
typedef void (__stdcall *TSetOptionRange) (void* ptr, PChar OptionName, double LowValue, double HighValue);  

typedef void (__stdcall *TSetOptionDigitsProc) (void* ptr, PChar OptionName, Word digits);
typedef void (__stdcall *TPrintProc) (void* ptr, PChar s);
typedef double (__stdcall *TGetDoubleFunc) (void* ptr);
typedef PChar (__stdcall *TGetPCharFunc) (void* ptr);
typedef int (__stdcall *TGetIntegerFunc) (void* ptr);

typedef TDateTime (__stdcall *TGetDateTimeFunc) (void* ptr);
typedef double (__stdcall *TGet_iOHLCVFunc) (void* ptr, PChar Symbol, int TimeFrame, int index);
typedef TDateTime (__stdcall *TGet_iTimeFunc) (void* ptr, PChar Symbol, int TimeFrame, int index);
typedef unsigned long int (__stdcall *TGet_iBarsFunc) (void* ptr, PChar Symbol, int TimeFrame);
typedef double (__stdcall *TGetOHLCVFunc) (void* ptr, int index);

typedef TDateTime (__stdcall *TGetTimeFunc) (void* ptr, int index);
typedef unsigned long int (__stdcall *TGetBarsFunc) (void* ptr);

typedef bool (__stdcall *TSendInstantOrderFunc) (void* ptr, PChar Symbol, int OperationType,
  double LotSize, double StopLoss, double TakeProfit, int& OrderHandle);

typedef bool (__stdcall *TSendInstantOrder2Func) (void* ptr, PChar Symbol, int OperationType,
  double LotSize, double StopLoss, double TakeProfit, PChar comment, int MagicNumber, int& OrderHandle);

typedef bool (__stdcall *T_SendInstantOrderFunc) (void* ptr, PChar Symbol, int OperationType,
  double price, double LotSize, double StopLoss, double TakeProfit, PChar comment, int MagicNumber, int& OrderHandle);

typedef bool (__stdcall *TSendPendingOrderFunc) (void* ptr, PChar Symbol, int OperationType,
  double LotSize, double StopLoss, double TakeProfit, double ExecutionPrice, int& OrderHandle);

typedef bool (__stdcall *TSendPendingOrder2Func) (void* ptr, PChar Symbol, int OperationType,
  double LotSize, double StopLoss, double TakeProfit, double ExecutionPrice, PChar comment, int MagicNumber, int& OrderHandle);

typedef bool (__stdcall *TModifyOrderFunc) (void* ptr, int OrderHandle, double NewPrice, double StopLoss, double TakeProfit);
typedef bool (__stdcall *TDeleteOrderFunc) (void* ptr, int OrderHandle);
typedef bool (__stdcall *TCloseOrderFunc) (void* ptr, int OrderHandle);
typedef bool (__stdcall *T_CloseOrderFunc) (void* ptr, int OrderHandle, double peice);
typedef bool (__stdcall *TGetOrderInfoFunc) (void* ptr, int OrderHandle, TTradePosition& info);

typedef bool (__stdcall *TSetCurrencyAndTimeframeFunc) (void* ptr, PChar Symbol, int TimeFrame);
typedef bool (__stdcall *TOrderSelectFunc) (void* ptr, int index, int flags, int pool);
typedef bool (__stdcall *TOrderClosedFunc) (void* ptr, int OrderHandle);
typedef double (__stdcall *TMarketInfoFunc) (void* ptr, PChar Symbol, int _type);
typedef void (__stdcall *TStrategyShortNameProc) (void* ptr, PChar name);

typedef void (__stdcall *TStrategyDescriptionProc) (void* ptr, PChar desc);
typedef void (__stdcall *TBreakpointProc) (void* ptr, int number, PChar text);
typedef int (__stdcall *TCreateIndicatorFunc) (void* ptr, PChar CurrencyName, int Timeframe,
  PChar IndicatorName, PChar parameters);

typedef double (__stdcall *TGetIndicatorValueFunc) (void* ptr, int IndicatorHandle, int index, int BufferIndex);
typedef void (__stdcall *TPauseProc) (void* ptr, PChar text);
typedef void (__stdcall *TResumeProc) (void* ptr);
typedef void (__stdcall *TGetInterfaceVersionProc) (void* ptr, int& MajorValue, int& MinorValue);

typedef bool (__stdcall *TObjectCreateFunc) (void* ptr, PChar name, int ObjType, int window,
  TDateTime time1, double price1, TDateTime time2, double price2, TDateTime time3, double price3);

typedef void (__stdcall *TObjectDeleteProc) (void* ptr, PChar name);
typedef bool (__stdcall *TObjectExistsFunc) (void* ptr, PChar name);
typedef int (__stdcall *TObjectTypeFunc) (void* ptr, PChar name);
typedef bool (__stdcall *TObjectSetFunc) (void* ptr, PChar name, int index, double value);
typedef double (__stdcall *TObjectGetFunc) (void* ptr, PChar name, int index);  

typedef void (__stdcall *TObjectsDeleteAllProc) (void* ptr, int window, int ObjType);
typedef bool (__stdcall *TObjectSetTextFunc) (void* ptr, PChar name, PChar text, int FontSize,
  PChar FontName, int Color);

typedef void (__stdcall *TSetIndicatorBuffStyleProc) (void* ptr, int IndicatorHandle, 
  int BuffIndex, int _style, int width, int color);

typedef int (__stdcall *TIBarShiftFunc) (void* ptr, PChar Symbol, int TimeFrame, TDateTime time, bool Exact);  
typedef int (__stdcall *TIHighestFunc) (void* ptr, PChar Symbol, int TimeFrame, int _type, int count, int index);
typedef int (__stdcall *TILowestFunc) (void* ptr, PChar Symbol, int TimeFrame, int _type, int count, int index);

typedef bool (__stdcall *TCloseOrderPartialFunc) (void* ptr, int OrderHandle, double LotSize);

typedef PChar (__stdcall *TObjectGetTextFunc) (void* ptr, PChar name);


// pointers to interface procedures

#pragma pack(push)
struct TInterfaceProcRec {
  LongWord dwSize;

  TRegOptionProc RegOption;
  void* pRegOption;
  
  TAddOptionValueProc AddOptionValue;
  void* pAddOptionValue;   
  
  TSetOptionRange SetOptionRange;
  void* pSetOptionRange;

  TSetOptionDigitsProc SetOptionDigits;
  void* pSetOptionDigits;

  TPrintProc Print;
  void* pPrint;

  TStrategyShortNameProc StrategyShortName;
  void* pStrategyShortName;

  TStrategyDescriptionProc StrategyDescription;
  void* pStrategyDescription;

  TGetCurrencyInfoFunc GetCurrencyInfo;
  void* pGetCurrencyInfo;

  TSetCurrencyAndTimeframeFunc SetCurrencyAndTimeframe;
  void* pSetCurrencyAndTimeframe;
  
  TMarketInfoFunc MarketInfo;
  void* pMarketInfo;

  TGetDoubleFunc Bid;
  void* pBid;

  TGetDoubleFunc Ask;
  void* pAsk;

  TGetPCharFunc Symbol;
  void* pSymbol;

  TGetIntegerFunc Digits;
  void* pDigits;

  TGetDoubleFunc Point;
  void* pPoint;

  TSendInstantOrderFunc SendInstantOrder;
  void* pSendInstantOrder;

  TSendPendingOrderFunc SendPendingOrder;
  void* pSendPendingOrder;

  TModifyOrderFunc ModifyOrder;
  void* pModifyOrder;

  TDeleteOrderFunc DeleteOrder;
  void* pDeleteOrder;

  TCloseOrderFunc CloseOrder;
  void* pCloseOrder;

  TOrderClosedFunc OrderClosed;
  void* pOrderClosed;

  TGetOrderInfoFunc GetOrderInfo;
  void* pGetOrderInfo;

  TOrderSelectFunc OrderSelect;
  void* pOrderSelect;

  TGetDoubleFunc OrderProfit;
  void* pOrderProfit;

  TGetDoubleFunc OrderProfitPips;
  void* pOrderProfitPips;

  TGetIntegerFunc HistoryTotal;
  void* pHistoryTotal;

  TGetIntegerFunc OrdersTotal;
  void* pOrdersTotal;

  TGetDateTimeFunc OrderOpenTime;
  void* pOrderOpenTime;

  TGetDateTimeFunc OrderCloseTime;
  void* pOrderCloseTime;

  TGetDoubleFunc OrderLots;
  void* pOrderLots;

  TGetIntegerFunc OrderTicket;
  void* pOrderTicket;

  TGetIntegerFunc OrderType;
  void* pOrderType;

  TGetDoubleFunc OrderStopLoss;
  void* pOrderStopLoss;

  TGetDoubleFunc OrderTakeProfit;
  void* pOrderTakeProfit;

  TGetDoubleFunc OrderOpenPrice;
  void* pOrderOpenPrice;

  TGetDoubleFunc OrderClosePrice;
  void* pOrderClosePrice;

  TGetPCharFunc OrderSymbol;
  void* pOrderSymbol;

  TGet_iOHLCVFunc iOpen;
  void* piOpen;

  TGet_iOHLCVFunc iClose;
  void* piClose;

  TGet_iOHLCVFunc iHigh;
  void* piHigh;

  TGet_iOHLCVFunc iLow;
  void* piLow;

  TGet_iOHLCVFunc iVolume;
  void* piVolume;

  TGet_iTimeFunc iTime;
  void* piTime;

  TGet_iBarsFunc iBars;
  void* piBars;

  TGetOHLCVFunc Open;
  void* pOpen;

  TGetOHLCVFunc Close;
  void* pClose;

  TGetOHLCVFunc High;
  void* pHigh;

  TGetOHLCVFunc Low;
  void* pLow;

  TGetOHLCVFunc Volume;
  void* pVolume;

  TGetTimeFunc Time;
  void* pTime;

  TGetBarsFunc Bars;
  void* pBars;

  // extensions ver 1.1 (dwSize > 412)
  TGetDoubleFunc AccountBalance;
  void* pAccountBalance;

  TGetDoubleFunc AccountEquity;
  void* pAccountEquity;

  TGetDoubleFunc AccountMargin;
  void* pAccountMargin;

  TGetDoubleFunc AccountFreeMargin;
  void* pAccountFreeMargin;

  TGetIntegerFunc AccountLeverage;
  void* pAccountLeverage;

  TGetDoubleFunc AccountProfit;
  void* pAccountProfit;

  TBreakpointProc Breakpoint;
  void* pBreakpoint;

  // extensions ver 1.2 (dwSize > 468)
  TCreateIndicatorFunc CreateIndicator;
  void* pCreateIndicator;

  TGetIndicatorValueFunc GetIndicatorValue;
  void* pGetIndicatorValue;

  // extensions ver 1.3 (dwSize > 484)
  TSendInstantOrder2Func SendInstantOrder2;
  void* pSendInstantOrder2;

  TSendPendingOrder2Func SendPendingOrder2;
  void* pSendPendingOrder2;

  TGetPCharFunc OrderComment;
  void* pOrderComment;

  TGetIntegerFunc OrderMagicNumber;
  void* pOrderMagicNumber;

  // extensions ver 1.4 (dwSize > 516)
  TPauseProc Pause;
  void* pPause;

  TResumeProc Resume;
  void* pResume;

  // extensions ver 1.41 (dwSize > 532)
  T_SendInstantOrderFunc _SendInstantOrder;
  void* p_SendInstantOrder;

  T_CloseOrderFunc _CloseOrder;
  void* p_CloseOrder;

  // extensions ver 1.5 (dwSize > 548)
  TGetInterfaceVersionProc GetInterfaceVersion;
  void* pGetInterfaceVersion;

  TObjectCreateFunc ObjectCreate;
  void* pObjectCreate;

  TObjectDeleteProc ObjectDelete;
  void* pObjectDelete;

  TObjectExistsFunc ObjectExists;
  void* pObjectExists;

  TObjectTypeFunc ObjectType;
  void* pObjectType;

  TObjectSetFunc ObjectSet;
  void* pObjectSet;

  TObjectGetFunc ObjectGet;
  void* pObjectGet;

  TObjectsDeleteAllProc ObjectsDeleteAll;
  void* pObjectsDeleteAll;

  TObjectSetTextFunc ObjectSetText;
  void* pObjectSetText;

  // extentions ver 1.7
  TGetDateTimeFunc TimeCurrent;
  void* pTimeCurrent;

  TSetIndicatorBuffStyleProc SetIndicatorBuffStyle;
  void* pSetIndicatorBuffStyle;

  // extensions ver 1.8 (dwSize > 636)
  TIBarShiftFunc iBarShift;
  void* piBarShift;

  TIHighestFunc iHighest;
  void* piHighest;

  TILowestFunc iLowest;
  void* piLowest;

  // extensions ver 1.9 (dwSize > 660)
  TCloseOrderPartialFunc CloseOrderPartial;
  void* pCloseOrderPartial;

  // extensions ver 1.10 (dwSize > 668)
  TObjectGetTextFunc ObjectGetText;
  void* pObjectGetText;

  // extensions ver 1.11 (dwSize > 676)
  TGetDoubleFunc OrderSwap;
  void* pOrderSwap;
};
#pragma pack(pop)

EXPORT TInterfaceProcRec IntrfProcsRec;

//-----------------------------------------------------------------------------

BOOL APIENTRY DllMain(HANDLE hModule, DWORD dwReason, LPVOID lpReserved) 
{
  switch (dwReason) 
  {
    case DLL_PROCESS_ATTACH:
    {
      memset(&IntrfProcsRec, 0, sizeof(TInterfaceProcRec));
      IntrfProcsRec.dwSize = sizeof(TInterfaceProcRec);
      break;
    }

    case DLL_THREAD_ATTACH:
    break;

    case DLL_THREAD_DETACH:
    break;

    case DLL_PROCESS_DETACH:
    break;
  }

  return TRUE;
}

//~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

#define rec IntrfProcsRec

//-----Replace PChar string with new one--------------------------------------
EXPORT void __stdcall ReplaceStr(PChar& dest, PChar source)
{
  free((void *)dest);
  dest = (PChar)malloc(strlen((char*)source) + 1);
  strcpy(dest, source);
}

//-----Sell-------------------------------------------------------------------
int Sell(double LotSize, double StopLoss, double TakeProfit)
{
  double tp, sl;

  if (TakeProfit == 0) tp = 0; else tp = Bid() - TakeProfit*Point();
  if (StopLoss == 0) sl = 0; else sl = Bid() + StopLoss*Point();

  int result;
  SendInstantOrder(Symbol(), op_Sell, LotSize, sl, tp, "", 0, result);
  return result;
}

//-----Buy-------------------------------------------------------------------
int Buy(double LotSize, double StopLoss, double TakeProfit)
{
  double tp, sl;

  if (TakeProfit == 0) tp = 0; else tp = Ask() + TakeProfit*Point();
  if (StopLoss == 0) sl = 0; else sl = Ask() - StopLoss*Point();

  int result;
  SendInstantOrder(Symbol(), op_Buy, LotSize, sl, tp, "", 0, result);
  return result;
}

/*
//-----Convert time to string-------------------------------------------------
function StrTime(DateTime: TDateTime): string;
{
  DateTimeToString(result, 'yyyy.mm.dd hh:nn', DateTime);
}
*/

//-----Get currency information-----------------------------------------------
bool GetCurrencyInfo(PChar Symbol, PCurrencyInfo& info)
{
  return rec.GetCurrencyInfo(rec.pGetCurrencyInfo, Symbol, info);
}

//-----Register option--------------------------------------------------------
void RegOption(PChar OptionName, TOptionType OptionType, void* option)
{
  rec.RegOption(rec.pRegOption, OptionName, OptionType, option);
}

//-----Add separator----------------------------------------------------------
void AddSeparator(PChar text)
{
  rec.RegOption(rec.pRegOption, text, ot_Separator, NULL);
}

//-----Add option value (for drop down box options)---------------------------
void AddOptionValue(PChar OptionName, PChar value)
{
  rec.AddOptionValue(rec.pAddOptionValue, OptionName, value);
}

//-----Set option range (for integer, longword and double types)--------------
void SetOptionRange(PChar OptionName, double LowValue, double HighValue)
{
  rec.SetOptionRange(rec.pSetOptionRange, OptionName, LowValue, HighValue);
}

//-----Set option digits------------------------------------------------------
void SetOptionDigits(PChar OptionName, Word digits)
{
  rec.SetOptionDigits(rec.pSetOptionDigits, OptionName, digits);
}

//-----Print text in "Journal" window in ForexTester--------------------------
void Print(PChar s)
{
  rec.Print(rec.pPrint, s);
}

//-----Set strategy short name------------------------------------------------
void StrategyShortName(PChar name)
{
  rec.StrategyShortName(rec.pStrategyShortName, name);
}

//-----Set strategy description-----------------------------------------------
void StrategyDescription(PChar desc)
{
  rec.StrategyDescription(rec.pStrategyDescription, desc);
}

//-----Get Bid price----------------------------------------------------------
double Bid()
{
  return rec.Bid(rec.pBid);
}

//-----Get Ask price----------------------------------------------------------
double Ask()
{
  return rec.Ask(rec.pAsk);
}

//-----Currency name----------------------------------------------------------
PChar Symbol()
{
  return rec.Symbol(rec.pSymbol);
}

//-----Currency digits after point--------------------------------------------
int Digits()
{
  return rec.Digits(rec.pDigits);
}

//-----Minimal currency point-------------------------------------------------
double Point()
{
  return rec.Point(rec.pPoint);
}

//-----Get open value---------------------------------------------------------
double iOpen(PChar Symbol, int TimeFrame, int index)
{
  return rec.iOpen(rec.piOpen, Symbol, TimeFrame, index);
}

//-----Get close value--------------------------------------------------------
double iClose(PChar Symbol, int TimeFrame, int index)
{
  return rec.iClose(rec.piClose, Symbol, TimeFrame, index);
}

//-----Get high value---------------------------------------------------------
double iHigh(PChar Symbol, int TimeFrame, int index)
{
  return rec.iHigh(rec.piHigh, Symbol, TimeFrame, index);
}

//-----Get low value----------------------------------------------------------
double iLow(PChar Symbol, int TimeFrame, int index)
{
  return rec.iLow(rec.piLow, Symbol, TimeFrame, index);
}

//-----Get volume-------------------------------------------------------------
double iVolume(PChar Symbol, int TimeFrame, int index)
{
  return rec.iVolume(rec.piVolume, Symbol, TimeFrame, index);
}


//-----Get time of bar--------------------------------------------------------
TDateTime iTime(PChar Symbol, int TimeFrame, int index)
{
  return rec.iTime(rec.piTime, Symbol, TimeFrame, index);
}

//-----Get number of bars-----------------------------------------------------
int iBars(PChar Symbol, int TimeFrame)
{
  return rec.iBars(rec.piBars, Symbol, TimeFrame);
}

//-----Get open value in default currency and timeframe-----------------------
double Open(int index)
{
  return rec.Open(rec.pOpen, index);
}

//-----Get close value in default currency and timeframe----------------------
double Close(int index)
{
  return rec.Close(rec.pClose, index);
}

//-----Get high value in default currency and timeframe-----------------------
double High(int index)
{
  return rec.High(rec.pHigh, index);
}

//-----Get low value in default currency and timeframe------------------------
double Low(int index)
{
  return rec.Low(rec.pLow, index);
}

//-----Get volume in default currency and timeframe---------------------------
double Volume(int index)
{
  return rec.Volume(rec.pVolume, index);
}

//-----Get time of bar in default currency and timeframe----------------------
TDateTime Time(int index)
{
  return rec.Time(rec.pTime, index);
}

//-----Get number of bars in default currency and timeframe-------------------
int Bars()
{
  return rec.Bars(rec.pBars);
}

//-----Set currency and timeframe---------------------------------------------
bool SetCurrencyAndTimeframe(PChar Symbol, int TimeFrame)
{
  return rec.SetCurrencyAndTimeframe(rec.pSetCurrencyAndTimeframe, Symbol, TimeFrame);
}

//-----Get market information-------------------------------------------------
double MarketInfo(PChar Symbol, TMarketInfo _type)
{
  return rec.MarketInfo(rec.pMarketInfo, Symbol, _type);
}

//-----Send instant order-----------------------------------------------------
bool SendInstantOrder(PChar Symbol, TInstantOrderType OperationType, double LotSize, 
  double StopLoss, double TakeProfit, PChar Comment, int MagicNumber, int& OrderHandle)
{
  return rec.SendInstantOrder2(rec.pSendInstantOrder2, Symbol, OperationType,
    LotSize, StopLoss, TakeProfit, Comment, MagicNumber, OrderHandle);
}

//-----Send pending order-----------------------------------------------------
bool SendPendingOrder(PChar Symbol, TPendingOrderType OperationType, double LotSize, 
  double StopLoss, double TakeProfit, double ExecutionPrice, 
  PChar Comment, int MagicNumber, int& OrderHandle)
{
  return rec.SendPendingOrder2(rec.pSendPendingOrder2, Symbol, OperationType,
    LotSize, StopLoss, TakeProfit, ExecutionPrice, Comment, MagicNumber, OrderHandle);
}

//-----Modify order-----------------------------------------------------------
bool ModifyOrder(int OrderHandle, double NewPrice, double StopLoss, double TakeProfit)
{
  return rec.ModifyOrder(rec.pModifyOrder, OrderHandle, NewPrice, StopLoss, TakeProfit);
}

//-----Delete order-----------------------------------------------------------
bool DeleteOrder(int OrderHandle)
{
  return rec.DeleteOrder(rec.pDeleteOrder, OrderHandle);
}

//-----Close order------------------------------------------------------------
bool CloseOrder(int OrderHandle)
{
  return rec.CloseOrder(rec.pCloseOrder, OrderHandle);
}

//-----Get order information--------------------------------------------------
bool GetOrderInfo(int OrderHandle, TTradePosition& info)
{
  return rec.GetOrderInfo(rec.pGetOrderInfo, OrderHandle, info);
}

//-----Select order-----------------------------------------------------------
bool OrderSelect(int index, TOrderSelectMode flags, TSearchMode pool)
{
  return rec.OrderSelect(rec.pOrderSelect, index, flags, pool);
}

//-----Get profit in dollars of selected order--------------------------------
double OrderProfit()
{
  return rec.OrderProfit(rec.pOrderProfit);
}

//-----Get profit in pips of selected order-----------------------------------
double OrderProfitPips()
{
  return rec.OrderProfitPips(rec.pOrderProfitPips);
}

//-----Check if order was closed----------------------------------------------
bool OrderClosed(int OrderHandle)
{
  return rec.OrderClosed(rec.pOrderClosed, OrderHandle);
}

//-----Number of closed positions---------------------------------------------
int HistoryTotal()
{
  return rec.HistoryTotal(rec.pHistoryTotal);
}

//-----Number of opened positions---------------------------------------------
int OrdersTotal()
{
  return rec.OrdersTotal(rec.pOrdersTotal);
}

//-----Open time of selected order--------------------------------------------
TDateTime OrderOpenTime()
{
  return rec.OrderOpenTime(rec.pOrderOpenTime);
}

//-----Close time of selected order-------------------------------------------
TDateTime OrderCloseTime()
{
  return rec.OrderCloseTime(rec.pOrderCloseTime);
}

//-----Lot size of selected order---------------------------------------------
double OrderLots()
{
  return rec.OrderLots(rec.pOrderLots);
}

//-----Handle of selected order-----------------------------------------------
int OrderTicket()
{
  return rec.OrderTicket(rec.pOrderTicket);
}

//-----Type of the selected order---------------------------------------------
TTradePositionType OrderType()
{
  return TTradePositionType(rec.OrderType(rec.pOrderType));
}

//-----Stop loss of selected order--------------------------------------------
double OrderStopLoss()
{
  return rec.OrderStopLoss(rec.pOrderStopLoss);
}

//-----Take profit of selected order------------------------------------------
double OrderTakeProfit()
{
  return rec.OrderTakeProfit(rec.pOrderTakeProfit);
}

//-----Open price of selected order-------------------------------------------
double OrderOpenPrice()
{
  return rec.OrderOpenPrice(rec.pOrderOpenPrice);
}

//-----Close price of selected order------------------------------------------
double OrderClosePrice()
{
  return rec.OrderClosePrice(rec.pOrderClosePrice);
}

//-----Currency of selected order---------------------------------------------
PChar OrderSymbol()
{
  return rec.OrderSymbol(rec.pOrderSymbol);
}

//-----Get stop loss in points------------------------------------------------
int GetStopLossPoints(int OrderHandle)
{
  int result = 0;

  if (OrderSelect(OrderHandle, SELECT_BY_TICKET, MODE_TRADES))
  {
    if (OrderStopLoss() == 0) return result;

    PCurrencyInfo pInfo;
    if (!GetCurrencyInfo(OrderSymbol(), pInfo)) return result;

    result = int((OrderOpenPrice()-OrderStopLoss())/pInfo->Point);
    if (OrderType() == tp_Buy) result = -result;
  }

  return result;
}

//-----Get take profit points-------------------------------------------------
int GetTakeProfitPoints(int OrderHandle)
{
  int result = 0;

  if (OrderSelect(OrderHandle, SELECT_BY_TICKET, MODE_TRADES))
  {
    if (OrderTakeProfit() == 0) return result;
    
    PCurrencyInfo pInfo;
    if (!GetCurrencyInfo(OrderSymbol(), pInfo)) return result;

    result = int((OrderOpenPrice()-OrderTakeProfit())/pInfo->Point);
    if (OrderType() == tp_Sell) result = -result;
  }

  return result;
}


//-----Set stop loss in points------------------------------------------------
void SetStopLossPoints(int OrderHandle, int NewStopLoss)
{
  if (OrderSelect(OrderHandle, SELECT_BY_TICKET, MODE_TRADES))
  {
    PCurrencyInfo pInfo;
    if (!GetCurrencyInfo(OrderSymbol(), pInfo)) return;

    switch (OrderType())
    {
      case tp_Buy:  
      ModifyOrder(OrderHandle, OrderOpenPrice(),
        OrderOpenPrice() - NewStopLoss*pInfo->Point, OrderTakeProfit());
      break;

      case tp_Sell: 
      ModifyOrder(OrderHandle, OrderOpenPrice(),
        OrderOpenPrice() + NewStopLoss*pInfo->Point, OrderTakeProfit());
      break;
    }
  }
}

//-----Set take profit in points----------------------------------------------
void SetTakeProfitPoints(int OrderHandle, int NewTakeProfit)
{
  if (OrderSelect(OrderHandle, SELECT_BY_TICKET, MODE_TRADES))
  {
    PCurrencyInfo pInfo;
    if (!GetCurrencyInfo(OrderSymbol(), pInfo)) return;

    switch (OrderType())
    {
      case tp_Buy:  
      ModifyOrder(OrderHandle, OrderOpenPrice(),
        OrderOpenPrice() + NewTakeProfit*pInfo->Point, OrderTakeProfit());
      break;

      case tp_Sell: 
      ModifyOrder(OrderHandle, OrderOpenPrice(),
        OrderOpenPrice() - NewTakeProfit*pInfo->Point, OrderTakeProfit());
      break;
    }
  }
}

//-----Get account balance----------------------------------------------------
double AccountBalance()
{
  if (rec.pAccountBalance == NULL) return 0;
  return rec.AccountBalance(rec.pAccountBalance);
}

//-----Get account equity-----------------------------------------------------
double AccountEquity()
{
  if (rec.pAccountEquity == NULL) return 0;
  return rec.AccountEquity(rec.pAccountEquity);
}

//-----Get account margin-----------------------------------------------------
double AccountMargin()
{
  if (rec.pAccountMargin == NULL) return 0;
  return rec.AccountMargin(rec.pAccountMargin);
}

//-----Get account free margin------------------------------------------------
double AccountFreeMargin()
{
  if (rec.pAccountFreeMargin == NULL) return 0;
  return rec.AccountFreeMargin(rec.pAccountFreeMargin);
}

//-----Get account leverage---------------------------------------------------
int AccountLeverage()
{
  if (rec.pAccountLeverage == NULL) return 0;
  return rec.AccountLeverage(rec.pAccountLeverage);
}

//-----Get account profit-----------------------------------------------------
double AccountProfit()
{
  if (rec.pAccountProfit == NULL) return 0;
  return rec.AccountProfit(rec.pAccountProfit);
}

//-----Breakpoint-------------------------------------------------------------
void Breakpoint(int number, PChar text)
{
  if (rec.pBreakpoint == NULL) return;
  rec.Breakpoint(rec.pBreakpoint, number, text);
}


//-----Create indicator and obtaind id----------------------------------------
int CreateIndicator(PChar Symbol, int TimeFrame, PChar IndicatorName, PChar parameters)
{
  if (IntrfProcsRec.pCreateIndicator == NULL) return 0;

  PChar IndicatorNameEx = (PChar)malloc(strlen(IndicatorName) + 4 + 1);
  strcpy(IndicatorNameEx, IndicatorName);
  strcat(IndicatorNameEx, ".dll");

  return rec.CreateIndicator(rec.pCreateIndicator, Symbol, TimeFrame,
    IndicatorNameEx, parameters);

  free(IndicatorNameEx);
}

//-----Get indicator value----------------------------------------------------
double GetIndicatorValue(int IndicatorHandle, int index, int BufferIndex)
{
  if (rec.pGetIndicatorValue == NULL) return 0;
  return rec.GetIndicatorValue(rec.pGetIndicatorValue, IndicatorHandle, index, BufferIndex);
}

//-----Get order MagicNumber--------------------------------------------------
int OrderMagicNumber()
{
  if (rec.pOrderMagicNumber == NULL) return 0;
  return rec.OrderMagicNumber(rec.pOrderMagicNumber);
}

//-----Get order comment------------------------------------------------------
PChar OrderComment()
{
  if (rec.pOrderComment == NULL) return "";
  return rec.OrderComment(rec.pOrderComment);
}

//-----Order swap-------------------------------------------------------------
double OrderSwap()
{
  if (rec.pOrderSwap == NULL) return 0;
  return rec.OrderSwap(rec.pOrderSwap);
}

//-----Pause------------------------------------------------------------------
void Pause(PChar text)
{
  if (rec.pPause == NULL) return;
  rec.Pause(rec.pPause, text);
}

//-----Resume-----------------------------------------------------------------
void Resume()
{
  if (rec.pResume) return;
  rec.Resume(rec.pResume);
}


//-----Send instant order with selected price---------------------------------
bool _SendInstantOrder(PChar Symbol, TInstantOrderType OperationType, double price, 
  double LotSize, double StopLoss, double TakeProfit, PChar Comment, int MagicNumber, int& OrderHandle)
{
  if (rec.p_SendInstantOrder == NULL)
  {
    OrderHandle = -1;
    return false;  
  }

  return rec._SendInstantOrder(rec.p_SendInstantOrder, Symbol, OperationType,
    price, LotSize, StopLoss, TakeProfit, Comment, MagicNumber, OrderHandle);
}

//-----Close order with requested price---------------------------------------
bool _CloseOrder(int OrderHandle, double price)
{
  if (rec.p_CloseOrder == NULL) return false;
  return rec._CloseOrder(rec.p_CloseOrder, OrderHandle, price);
}

//-----Create object----------------------------------------------------------
bool ObjectCreate(PChar name, TObjectType ObjType, int window, 
  TDateTime time1, double price1, TDateTime time2, double price2, 
  TDateTime time3, double price3)
{
  if (rec.pObjectCreate == NULL) return false;
  return rec.ObjectCreate(rec.pObjectCreate, name, ObjType,
    window, time1, price1, time2, price2, time3, price3);
}

//-----Delete object by name--------------------------------------------------
void ObjectDelete(PChar name)
{
  if (rec.pObjectDelete == NULL) return;
  rec.ObjectDelete(rec.pObjectDelete, name);
}

//-----Check if object already exists-----------------------------------------
bool ObjectExists(PChar name)
{
  if (rec.pObjectExists == NULL) return false;
  return rec.ObjectExists(rec.pObjectExists, name);
}

//-----Get object type--------------------------------------------------------
TObjectType ObjectType(PChar name)
{
  if (rec.pObjectType == NULL) return obj_Text;
  return TObjectType(rec.ObjectType(rec.pObjectType, name));
}

//-----Set object property----------------------------------------------------
bool ObjectSet(PChar name, int index, double value)
{
  if (rec.pObjectSet == NULL) return false;
  return rec.ObjectSet(rec.pObjectSet, name, index, value);
}

//-----Get object property----------------------------------------------------
double ObjectGet(PChar name, int index)
{
  if (rec.pObjectGet == NULL) return 0;
  return rec.ObjectGet(rec.pObjectGet, name, index);
}

//-----Delete all objects-----------------------------------------------------
void ObjectsDeleteAll(int window, TObjectType ObjType)
{
  if (rec.pObjectsDeleteAll == NULL) return;
  rec.ObjectsDeleteAll(rec.pObjectsDeleteAll, window, ObjType);
}

//-----Get interface version--------------------------------------------------
void GetInterfaceVersion(int& MajorValue, int& MinorValue)
{
  if (rec.pGetInterfaceVersion == NULL)
  {
    MajorValue = 1;
    MinorValue = 4;
    return;  
  }
  
  rec.GetInterfaceVersion(rec.pGetInterfaceVersion, MajorValue, MinorValue);
}

//-----Set text---------------------------------------------------------------
bool ObjectSetText(PChar name, PChar text, int FontSize, PChar FontName, TColor Color)
{
  if (rec.pObjectSetText == NULL) return false;
  return rec.ObjectSetText(rec.pObjectSetText, name, text, FontSize, FontName, Color);
}

//-----Get text---------------------------------------------------------------
PChar ObjectGetText(PChar name)
{
  if (rec.pObjectGetText == NULL) return NULL;
  return rec.ObjectGetText(rec.pObjectGetText, name);
}

//-----Get current time-------------------------------------------------------
TDateTime  TimeCurrent()
{
  if (rec.pTimeCurrent == NULL) return false;
  return rec.TimeCurrent(rec.pTimeCurrent);
}

//-----Set indicator's buffer style-------------------------------------------
void SetIndicatorBuffStyle(int IndicatorHandle, int BuffIndex, TPenStyle _style,
  int width, TColor color)
{
  if (rec.pSetIndicatorBuffStyle == NULL) return;
  rec.SetIndicatorBuffStyle(rec.pSetIndicatorBuffStyle, IndicatorHandle, BuffIndex, _style,
    width, color);
}

//-----Get bar shift by its time----------------------------------------------
int iBarShift(PChar Symbol, int TimeFrame, TDateTime time, bool Exact)
{
  if (rec.iBarShift == NULL) return -1;
  return rec.iBarShift(rec.piBarShift, Symbol, TimeFrame, time, Exact);
}

//-----Get highest value in array---------------------------------------------
int iHighest(PChar Symbol, int TimeFrame, int _type, int count, int index)
{
  if (rec.iHighest == NULL) return -1;
  return rec.iHighest(rec.piHighest, Symbol, TimeFrame, _type, count, index);
}

//-----Get lowest value in array----------------------------------------------
int iLowest(PChar Symbol, int TimeFrame, int _type, int count, int index)
{
  if (rec.iLowest == NULL) return -1;
  return rec.iLowest(rec.piLowest, Symbol, TimeFrame, _type, count, index);
}

//-----Close part of position-------------------------------------------------
bool CloseOrderPartial(int OrderHandle, double LotSize)
{
  if (rec.CloseOrderPartial == NULL) return false;
  return rec.CloseOrderPartial(rec.pCloseOrderPartial, OrderHandle, LotSize);
}


#undef rec
