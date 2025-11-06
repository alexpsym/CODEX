//+------------------------------------------------------------------+
//|                                                MT5Scanner.mq5    |
//|   Simple MT5 Scanner for Spread, Swap and Correlation Matrix    |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

#include <Arrays/ArrayString.mqh>
#import "shell32.dll"
   int ShellExecuteW(int hwnd, string lpOperation, string lpFile, string lpParameters, string lpDirectory, int nShowCmd);
#import
#import "user32.dll"
   int MessageBoxW(int hWnd, string lpText, string lpCaption, int uType);
#import

// Script inputs provide flexibility to end users.
input string FilePrefix        = "FX_Data_";   // Prefix for generated CSV files
input bool   ShowDebugMessages = true;         // Print progress information
input bool   EnableNotifications = true;         // Show pop-ups and MetaTrader alerts
input int    ScanIntervalMinutes = 30;         // Interval between scans

// Relative path (inside MQL5/Files) where reports will be saved
string g_outputFolder = "";
string g_fileTimestamp = "";                   // Timestamp used in file names

// Timeframes for analysis. Metrics for these periods are computed
// from 1-minute candles for maximum precision.
ENUM_TIMEFRAMES timeframes[] =
{
    PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_M30,
    PERIOD_H1, PERIOD_H4, PERIOD_D1, PERIOD_W1, PERIOD_MN1
};

//+------------------------------------------------------------------+
//| Replace characters not allowed in Windows file or folder names    |
//+------------------------------------------------------------------+
void SanitizeForWindows(string &text)
{
    const string invalid = "\\/:*?\"<>|";
    for(int i = 0; i < StringLen(invalid); i++)
        StringReplace(text, StringSubstr(invalid, i, 1), "-");
    StringReplace(text, ":", "-");
    StringReplace(text, ".", "-");
    StringReplace(text, " ", "_");
}

//+------------------------------------------------------------------+
//| Get timestamp string for filenames                                |
//+------------------------------------------------------------------+
string GetTimestampString()
{
    string stamp = TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES);
    SanitizeForWindows(stamp);
    return stamp;
}

//+------------------------------------------------------------------+
//| Wait for a file to be closed so it can be removed                 |
//+------------------------------------------------------------------+
bool WaitForFileClose(const string path)
{
    for(int i=0;i<10 && !IsStopped();i++)
    {
        int h=FileOpen(path, FILE_READ|FILE_WRITE|FILE_BIN);
        if(h>=0)
        {
            FileClose(h);
            return true;
        }
        Sleep(1000);
    }
    return false;
}

//+------------------------------------------------------------------+
//| Delete CSV files from previous scans                              |
//+------------------------------------------------------------------+
void DeleteOldCsvFiles(const string folderPath)
{
    string name;
    long search=FileFindFirst(folderPath+"*"+FilePrefix+"*.csv", name);
    if(search==INVALID_HANDLE)
        return;

    if(EnableNotifications)
        SendNotification("Previous scan CSV files will be deleted. Please close them if they are open.");
    do
    {
        string full=folderPath+name;
        if(WaitForFileClose(full))
            FileDelete(full);
    }
    while(FileFindNext(search,name));
    FileFindClose(search);
}

//+------------------------------------------------------------------+
//| Script entry point                                               |
//+------------------------------------------------------------------+
void OnStart()
{
    if(ShowDebugMessages)
        Print("Starting FX Scanner Script v1.37 (Spread, Swap and Correlation Matrix)");

    // Files are always stored relative to the terminal's MQL5\Files folder.
    // Do not use an absolute path here, otherwise FileOpen() will fail.
    g_outputFolder = "";  // empty means the root of MQL5\Files

    string displayPath = TerminalInfoString(TERMINAL_DATA_PATH) +
                         "\\MQL5\\Files\\" + g_outputFolder;

    // Delete any old CSV files before starting the first scan
    DeleteOldCsvFiles(g_outputFolder);

    while(!IsStopped())
    {
        g_fileTimestamp = GetTimestampString();
        if(ShowDebugMessages)
            Print("Saving reports to ", displayPath, " with timestamp prefix ", g_fileTimestamp);

        PerformScan(g_outputFolder, g_fileTimestamp);

        if(IsStopped())
            break;

        if(ShowDebugMessages)
            Print("Waiting ", ScanIntervalMinutes, " minutes for next scan...");
        Sleep((uint)ScanIntervalMinutes * 60 * 1000);
    }
}

//+------------------------------------------------------------------+
//| Main scanning function                                           |
//+------------------------------------------------------------------+
void PerformScan(const string folderPath, const string timestamp)
{
    CArrayString symbols;
    int symbolCount = GetWatchlistSymbols(symbols);
    if(symbolCount == 0)
    {
        if(ShowDebugMessages) Print("No symbols in Market Watch to scan");
        return;
    }

    // Open all required CSV files
    string prefix = timestamp + "-" + FilePrefix;
    int swapHandle = FileOpen(folderPath + prefix + "Swap.csv", FILE_WRITE | FILE_ANSI | FILE_CSV | FILE_TXT);

    int corrHandles[];
    ArrayResize(corrHandles, ArraySize(timeframes));
    for(int j = 0; j < ArraySize(timeframes); j++)
    {
        string tfStr   = EnumToString(timeframes[j]);
        string fn      = folderPath + prefix + tfStr + "Correlation.csv";
        corrHandles[j] = FileOpen(fn, FILE_WRITE | FILE_ANSI | FILE_CSV | FILE_TXT);
        if(corrHandles[j] < 0)
        {
            Print("Failed to open output file: ", fn);
            if(swapHandle >= 0) FileClose(swapHandle);
            for(int k=0;k<j;k++) if(corrHandles[k] >= 0) FileClose(corrHandles[k]);
            return;
        }
    }

    // Write headers
    FileWriteString(swapHandle,  "Symbol,SwapLong,SwapShort\r\n");
    for(int j = 0; j < ArraySize(timeframes); j++)
    {
        FileWriteString(corrHandles[j], "Symbol,");
        for(int s = 0; s < symbols.Total(); s++)
            FileWriteString(corrHandles[j], symbols.At(s) + ",");
        FileWriteString(corrHandles[j], "\r\n");
    }

    // Process each symbol
    for(int i = 0; i < symbols.Total(); i++)
    {
        string symbolRow = symbols.At(i);
        int progress  = (int)MathRound((double)(i+1) / symbols.Total() * 100);
        if(ShowDebugMessages)
            PrintFormat("[%3d%%] Processing symbol (%d/%d): %s", progress, i+1, symbols.Total(), symbolRow);

        // Ensure the symbol is subscribed so CopyRates can load history
        if(!SymbolSelect(symbolRow, true))
        {
            Print("Failed to select symbol ", symbolRow);
            continue;
        }

        // --- Swap information ---
        double swapLong, swapShort;
        if(!SymbolInfoDouble(symbolRow, SYMBOL_SWAP_LONG, swapLong) ||
           !SymbolInfoDouble(symbolRow, SYMBOL_SWAP_SHORT, swapShort))
        {
            Print("Failed to read swap for ", symbolRow);
            swapLong = 0.0;
            swapShort = 0.0;
        }
        FileWriteString(swapHandle, StringFormat("%s,%.4f,%.4f\r\n", symbolRow,
                                                NormalizeDouble(swapLong,4),
                                                NormalizeDouble(swapShort,4)));

        // --- Correlation matrix rows ---
        for(int j = 0; j < ArraySize(timeframes); j++)
        {
            FileWriteString(corrHandles[j], symbolRow + ",");
            for(int k = 0; k < symbols.Total(); k++)
            {
                string symbolCol = symbols.At(k);
                double correlation = (i == k) ? 1.0 : NormalizeDouble(
                    CalculateBarCorrelation(symbolRow, symbolCol, timeframes[j]), 4);
                FileWriteString(corrHandles[j], StringFormat("%.4f,", correlation));
            }
            FileWriteString(corrHandles[j], "\r\n");
        }
    }

    // Close files
    FileClose(swapHandle);
    for(int j = 0; j < ArraySize(timeframes); j++)
        FileClose(corrHandles[j]);

    // Spread report uses a separate file
    WriteSpreadReport(symbols, folderPath, timestamp);

    if(ShowDebugMessages)
    {
        Print("All files saved.");
        Print("Scan complete. Total symbols processed: ", symbols.Total());
    }

    //--- Open folder containing CSV files
    string fullFolder = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + folderPath;
    int openRes = ShellExecuteW(0, "open", fullFolder, NULL, NULL, 1);
    if(openRes <= 32)
        Print("Note: output written to ", fullFolder, " but folder could not be opened.");

    //--- Optional Windows push notification when finished
    // The final pop-up box has been removed to avoid interrupting the user.
    // If notifications are still desired for other events, they remain
    // controlled by the EnableNotifications input setting above.
    // if(EnableNotifications)
    //     MessageBoxW(0, "FX Scanner complete!", "FXScanner", 0);
}

//+------------------------------------------------------------------+
//| Retrieve symbols in Market Watch                                 |
//+------------------------------------------------------------------+
int GetWatchlistSymbols(CArrayString &symbols)
{
    int total = SymbolsTotal(true);
    for(int i = 0; i < total; i++)
        symbols.Add(SymbolName(i, true));
    return total;
}

//+------------------------------------------------------------------+
//| Calculate correlation using M1 bars between two symbols          |
//+------------------------------------------------------------------+
double CalculateBarCorrelation(const string symbolA, const string symbolB, const ENUM_TIMEFRAMES tf)
{
    datetime endTime   = TimeCurrent();
    // Use multiple bars for a more stable correlation. Using around
    // thirty bars generally balances accuracy with responsiveness to
    // recent changes.
    const int barsPerTf = 30;              // desired number of bars
    datetime startTime = endTime - PeriodSeconds(tf) * barsPerTf;
    MqlRates ratesA[], ratesB[];
    // Request data using the timeframe directly to avoid missing data
    int countA = CopyRates(symbolA, tf, startTime, endTime, ratesA);
    int countB = CopyRates(symbolB, tf, startTime, endTime, ratesB);
    int minCount = MathMin(countA, countB);

    // Work with whatever number of bars are available up to barsPerTf
    int barsToUse = MathMin(minCount, barsPerTf);
    if(barsToUse < 2)
        return 0.0;

    double sumX=0, sumY=0, sumX2=0, sumY2=0, sumXY=0;
    for(int i = 0; i < barsToUse; i++)
    {
        double x = ratesA[i].close;
        double y = ratesB[i].close;
        sumX  += x;
        sumY  += y;
        sumX2 += x * x;
        sumY2 += y * y;
        sumXY += x * y;
    }

    double num = barsToUse * sumXY - sumX * sumY;
    double den = MathSqrt((barsToUse * sumX2 - sumX * sumX) * (barsToUse * sumY2 - sumY * sumY));
    return (den != 0.0) ? num / den : 0.0;
}

//+------------------------------------------------------------------+
//| Calculate bar-based correlation using M1 close returns            |
//+------------------------------------------------------------------+
double CalculateBarCorrelation(const string symbolA, const string symbolB, const int bars)
{
    MqlRates ratesA[], ratesB[];
    int countA   = CopyRates(symbolA, PERIOD_M1, 0, bars, ratesA);
    int countB   = CopyRates(symbolB, PERIOD_M1, 0, bars, ratesB);
    int minCount = MathMin(countA, countB);
    if(minCount < 2)
        return 0.0;

    int retCount = minCount - 1;
    if(retCount < 10)
        return 0.0;

    double returnsA[], returnsB[];
    ArrayResize(returnsA, retCount);
    ArrayResize(returnsB, retCount);
    for(int i = 1; i < minCount; i++)
    {
        double closePrevA = ratesA[i - 1].close;
        double closePrevB = ratesB[i - 1].close;
        returnsA[i - 1] = (ratesA[i].close - closePrevA) / closePrevA * 100.0;
        returnsB[i - 1] = (ratesB[i].close - closePrevB) / closePrevB * 100.0;
    }

    double sumX=0, sumY=0, sumX2=0, sumY2=0, sumXY=0;
    for(int i = 0; i < retCount; i++)
    {
        double x = returnsA[i];
        double y = returnsB[i];
        sumX  += x;
        sumY  += y;
        sumX2 += x * x;
        sumY2 += y * y;
        sumXY += x * y;
    }

    double num = retCount * sumXY - sumX * sumY;
    double den = MathSqrt((retCount * sumX2 - sumX * sumX) * (retCount * sumY2 - sumY * sumY));
    return (den != 0.0) ? num / den : 0.0;
}

//+------------------------------------------------------------------+
//| Get number of significant decimals                              |
//+------------------------------------------------------------------+
int GetSignificantDecimals(double val)
{
    string s = DoubleToString(val, 10);
    int dotPos = StringFind(s, ".");
    if(dotPos < 0) return 0;
    int len = StringLen(s) - dotPos - 1;
    while(len > 0 && StringGetCharacter(s, StringLen(s) - 1) == '0')
    {
        s   = StringSubstr(s, 0, StringLen(s) - 1);
        len--;
    }
    return len;
}

//+------------------------------------------------------------------+
//| Write Spread Report                                              |
//+------------------------------------------------------------------+
void WriteSpreadReport(const CArrayString &symbols, const string folderPath, const string timestamp)
{
    string syms[];
    double spreads[];
    int total = symbols.Total();
    ArrayResize(syms, total);
    ArrayResize(spreads, total);

    // Collect spread information
    for(int i = 0; i < total; i++)
    {
        syms[i] = symbols.At(i);
        double bid, ask, point;
        if(!SymbolInfoDouble(syms[i], SYMBOL_BID, bid) ||
           !SymbolInfoDouble(syms[i], SYMBOL_ASK, ask) ||
           !SymbolInfoDouble(syms[i], SYMBOL_POINT, point))
        {
            Print("Failed to read spread info for ", syms[i]);
            spreads[i] = 0.0;
            continue;
        }
        double rawSpread = ask - bid;
        double spread    = rawSpread > 0.0 ? rawSpread : point;  // avoid zero
        spreads[i]       = (spread / bid) * 100.0;                // percent of bid
    }

    // Sort spreads in ascending order
    int n = ArraySize(syms);
    for(int i = 0; i < n - 1; i++)
        for(int j = 0; j < n - i - 1; j++)
            if(spreads[j] > spreads[j + 1])
            {
                double tmpD = spreads[j]; spreads[j] = spreads[j + 1]; spreads[j + 1] = tmpD;
                string tmpS = syms[j];    syms[j] = syms[j + 1];       syms[j + 1] = tmpS;
            }

    // Write to CSV
    string prefix = timestamp + "-" + FilePrefix;
    int handle = FileOpen(folderPath + prefix + "Spread.csv", FILE_WRITE | FILE_ANSI | FILE_CSV | FILE_TXT);
    if(handle < 0)
    {
        Print("Failed opening file: ", folderPath + prefix + "Spread.csv");
        return;
    }
    FileWriteString(handle, "Symbol,SpreadPercent (%)\r\n");
    for(int i = 0; i < n; i++)
    {
        int decs = GetSignificantDecimals(spreads[i]);
        if(decs < 1) decs = 1;
        FileWriteString(handle, syms[i] + "," + DoubleToString(spreads[i], decs) + "\r\n");
    }
    FileClose(handle);
    Print("Spread report written to ", folderPath + prefix + "Spread.csv");
}

//+------------------------------------------------------------------+

// (Python helper script removed)

//+------------------------------------------------------------------+
