#ifdef _WIN32
#define DLL_EXPORT __declspec(dllexport)
#else
#define DLL_EXPORT
#endif

extern "C" {

// Returns 1 for buy, -1 for sell, 0 for no trade
DLL_EXPORT int GetTradeSignal(int keycode,
                              double closePrev,
                              double openPrev,
                              double atrValue,
                              double bid,
                              double ask,
                              double atrMultiplier,
                              double* sl,
                              double* tp);

DLL_EXPORT double GetLots();

}

static const double Lots = 0.01;

DLL_EXPORT int GetTradeSignal(int keycode,
                              double closePrev,
                              double openPrev,
                              double atrValue,
                              double bid,
                              double ask,
                              double atrMultiplier,
                              double* sl,
                              double* tp)
{
    if(keycode != 49) // '1' key
        return 0;

    double stopPoints = atrValue * atrMultiplier;
    bool buy = closePrev > openPrev;

    if(buy)
    {
        *sl = bid - stopPoints;
        *tp = bid + stopPoints * 2.0;
        return 1; // buy signal
    }
    else
    {
        *sl = ask + stopPoints;
        *tp = ask - stopPoints * 2.0;
        return -1; // sell signal
    }
}

DLL_EXPORT double GetLots()
{
    return Lots;
}

