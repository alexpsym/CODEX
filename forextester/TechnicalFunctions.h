//---------------------------------------------------------------------------
// Support library, additional useful functions
//
// Ver 1.0
//---------------------------------------------------------------------------

#include <math.h>

enum TPriceType {pt_Close, pt_Open, pt_High, pt_Low, pt_HL2, pt_HLC3, pt_HLCC4};
enum TMAType {ma_SMA, ma_EMA, ma_WMA, ma_SSMA};


double GetPrice(int index, TPriceType PriceType);
void LRCChannelParams(int Offset, int period, TPriceType PriceType,
  double& StartValue, double& EndValue, double&Height, double& Top, double& Bottom);

// close all opened positions (except pending orders)
void CloseAllOpenPos();

// delete all pending orders
void DeleteAllPendingOrders();

// close all open positions and delete all pending orders
void CloseAndDeleteAll();

// Get number of open positions
int GetNumberOfOpenPositions();

// Get number of pending orders
int GetNumberOfPendingOrders();

// Get profit for open positions
double GetOpenPosProfit();

// Convert price type to string
PChar StrPriceType(TPriceType ptype);

// Convert moving average type to string
PChar StrMAType(TMAType matype);


//----------------------------------------------------------------------------
//implementation
//----------------------------------------------------------------------------

//-----Get price--------------------------------------------------------------
double GetPrice(int index, TPriceType PriceType)
{
  switch (PriceType)
  {
    case pt_Close: return Close(index);
    case pt_Open:  return Open(index);
    case pt_High:  return High(index);
    case pt_Low:   return Low(index);
    case pt_HL2:   return (High(index) + Low(index))/2;
    case pt_HLC3:  return (High(index) + Low(index) + Close(index))/3;
    case pt_HLCC4: return (High(index) + Low(index) + Close(index)*2)/4;
    default:       return 0;
  }
}

//-----Get channel params-----------------------------------------------------
void LRCChannelParams(int Offset, int period, TPriceType PriceType,
  double& StartValue, double& EndValue, double& Height, double& Top, double& Bottom)
{
  // Variable initialization
  double sum_x = 0;
  double sum_y = 0;
  double sum_xy = 0;
  double sum_x2 = 0;

  double y, z, max;
  int x;

  // Calculating sums for regression line
  
  int i = Offset;
  for (x=0; x < period; x++)
  {
    y = GetPrice(i, PriceType);
    sum_x += x;
    sum_y += y;
    sum_xy += x*y;
    sum_x2 += x*x;
    i++;
  }

  // Calculating regression line
  double b = (period*sum_xy - sum_x*sum_y)/(period*sum_x2 - sum_x*sum_x);
  double a = (sum_y - b*sum_x)/period;

  // Calculating channel height
  i = Offset;
  max = 0;
  for (x=0; x < period; x++)
  {
    y = a + b*x;
    z = fabs(GetPrice(i, PriceType) - y);
    if (z > max) max = z;
    i++;
  }

  // Returning channel values
  StartValue = a + b*period;
  EndValue = a;
  Height = max;
  Top = a + max;
  Bottom = a - max;
}

//-----Close all open positions-----------------------------------------------
void CloseAllOpenPos()
{
  int total = OrdersTotal();
  for (int i=total-1; i>=0; i--)
  {
    if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;

    switch (OrderType())
    {
      case tp_Buy: case tp_Sell:
        CloseOrder(OrderTicket());
        break;
    }
  }
}

//-----Delete all pending orders----------------------------------------------
void DeleteAllPendingOrders()
{
  int total = OrdersTotal();
  for (int i=total-1; i>=0; i--)
  {
    if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;

    switch (OrderType())
    {
      case tp_BuyStop: case tp_SellStop:
      case tp_BuyLimit: case tp_SellLimit:
        DeleteOrder(OrderTicket());
        break;
    }
  }
}

//-----Close all open positions and delete all orders-------------------------
void CloseAndDeleteAll()
{
  int total = OrdersTotal(); 
  for (int i=total; i>=0; i--)
  {
    if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;

    switch (OrderType())
    {
      case tp_Buy: case tp_Sell:
        CloseOrder(OrderTicket());
        break;

      case tp_BuyStop: case tp_SellStop:
      case tp_BuyLimit: case tp_SellLimit:
        DeleteOrder(OrderTicket());
        break;
    }
  }
}

//-----Get number of open positions-------------------------------------------
int GetNumberOfOpenPositions()
{
  int result = 0;

  int total = OrdersTotal(); 
  for (int i=total; i>=0; i--)
  {
    if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;

    switch (OrderType())
    {
      case tp_Buy: case tp_Sell:
        result++;
        break;
    }
  }

  return result;
}

//-----Get number of pending orders-------------------------------------------
int GetNumberOfPendingOrders()
{
  int result = 0;

  int total = OrdersTotal(); 
  for (int i=total; i>=0; i--)
  {
    if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;

    switch (OrderType())
    {
      case tp_BuyStop: case tp_SellStop:
      case tp_BuyLimit: case tp_SellLimit:
        result++;
        break;
    }
  }

  return result;
}

//-----Get profit for open positions------------------------------------------
double GetOpenPosProfit()
{
  return (AccountEquity() - AccountBalance());
}

//-----Convert price type to string-------------------------------------------
PChar StrPriceType(TPriceType ptype)
{
  switch (ptype)
  {
    case pt_Close: return "Close";
    case pt_Open:  return "Open";
    case pt_High:  return "High";
    case pt_Low:   return "Low";
    case pt_HL2:   return "(High + Low)/2";
    case pt_HLC3:  return "(High + Low + Close)/3";
    case pt_HLCC4: return "(High + Low + Close + Close)/4";
    default:       return "";
  }
}

//-----Convert moving average type to string----------------------------------
PChar StrMAType(TMAType matype)
{
  switch (matype)
  {
    case ma_SMA:  return "Simple (SMA)";
    case ma_EMA:  return "Exponential (EMA)";
    case ma_WMA:  return "Weighted (WMA)";
    case ma_SSMA: return "Smoothed (SSMA)";
    default:      return "";
  }
}

//-----Create "MA type" option-------------------------------------------------
void RegMATypeOption(int& option, PChar name)
{
  if (strcmp(name, "") == 0) name = "MA type";
  RegOption(name, ot_EnumType, &option);
  AddOptionValue(name, "Simple (SMA)");
  AddOptionValue(name, "Exponential (EMA)");
  AddOptionValue(name, "Weighted (WMA)");
  AddOptionValue(name, "Smoothed (SSMA)");
  option = 0;
}


//-----Create "Apply to price" option------------------------------------------
void RegApplyToPriceOption(int& option, PChar name)
{
  if (strcmp(name, "") == 0) name = "Apply to price";
  RegOption(name, ot_EnumType, &option);
  AddOptionValue(name, "Close");
  AddOptionValue(name, "Open");
  AddOptionValue(name, "High");
  AddOptionValue(name, "Low");
  AddOptionValue(name, "(High + Low)/2");
  AddOptionValue(name, "(High + Low + Close)/3");
  AddOptionValue(name, "(High + Low + Close + Close)/4");
  option = 0;
}

