#ifndef ATR2RSTRATEGY_H
#define ATR2RSTRATEGY_H

#ifdef _WIN32
#define DLL_EXPORT __declspec(dllexport)
#else
#define DLL_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

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

#ifdef __cplusplus
}
#endif

#endif // ATR2RSTRATEGY_H

