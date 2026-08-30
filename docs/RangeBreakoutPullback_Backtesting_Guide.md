# Range Breakout-Pullback Strategy — Backtesting Guide

## Part A — MetaTrader 5 Strategy Tester

The intended dry-run environment is **MT5 Strategy Tester**. It uses historical data and does not place live trades, so you do not need to enable automated trading on a live account to backtest this EA.

### Install and compile the EA

1. This EA has already been deployed to the Pepperstone terminal data folder: `C:\Users\User\AppData\Roaming\MetaQuotes\Terminal\73B7A2420D6397DFF9014A20F1201F97\MQL5\Experts`. The canonical repository source remains `mt5-clone/MQL5/Experts/RangeBreakoutPullbackStrategy.mq5`.
2. To update it later, compile the repository source and replace only its `.mq5` and `.ex5` files in that Experts folder.
3. Open MetaEditor from MT5 with **Tools → MetaQuotes Language Editor**, or run `C:\Program Files\Pepperstone MetaTrader 5\MetaEditor64.exe`.
4. In MetaEditor’s Navigator, open **Experts**, double-click the file, then press **F7** (Compile). Confirm the Toolbox/Errors pane reports zero errors and that an `.ex5` is created beside the source.
5. Return to MT5. In Navigator, right-click **Expert Advisors** and choose **Refresh**. If it does not appear, restart MT5.

For chart inspection only, drag the EA from **Navigator → Expert Advisors** onto a chart, select **Allow Algo Trading** in its properties if prompted, and keep the platform’s global **Algo Trading** button off unless you deliberately want it to trade that chart. Attaching an EA to a chart is not needed for a Strategy Tester dry run.

### Run a dry backtest

1. Open **View → Strategy Tester** (or `Ctrl+R`).
2. Select `RangeBreakoutPullbackStrategy` as the Expert, then select the symbol and timeframe that match the TradingView test.
3. Set the date range with **Use date**, and choose an appropriate historical period. Ensure the broker has downloaded the relevant history first.
4. For the closest practical fill simulation, choose **Every tick based on real ticks** when available. The strategy itself makes structural decisions only after a completed bar; the tick mode affects market-order, stop and target fills.
5. In the tester’s account/testing settings, set deposit, leverage, deposit currency and any commission/advanced-account settings relevant to the test. For a normal broker symbol, Strategy Tester obtains the historical floating spread; it does not provide a simple arbitrary spread override.
6. Click **Inputs**. Leave `InpSetupMode` at **RangeBreakoutPullback** to reproduce the original strict horizontal-range strategy. Select **ImpulsePullbackContinuation** to test the new optional strong impulse → pullback → resumption framework. Its inputs control minimum impulse body/range in ATR, minimum pullback ATR retracement, maximum depth and duration. `InpImpulseMaximumBodyATR` applies only in this impulse mode: `0` disables it, while a positive value caps the impulse candle body (use `1.5` for the next EURUSD M15 experiment). `InpTradeDirection` replaces the two TradingView long/short checkboxes; `InpVolumeLots` is the fixed tester order size.
7. Leave `InpServerTimeMode` at **PepperstoneNYClose** for Pepperstone data. It derives New York wall time directly because Pepperstone server time stays seven hours ahead of New York in both DST regimes. Use **FixedUTCOffset** only for another broker/test environment, then set `InpTesterServerUTCOffsetHours`.
8. Press **Start**. Enable **Visual mode** before starting if you want replay on a chart; use the speed control to pause around entries and exits.

`InpShowBlackoutStatus` displays a compact chart status during the weekend block. `InpShowDiagnostics` adds the current mode, setup stage, opposing-close count and the current or most recent reset reason. It distinguishes retracement, minimum-depth, maximum-depth, expiry and invalidation gates. `InpShowBlackoutZones` adds one subtle historical rectangle per weekend interval in Strategy Tester visual mode; the active interval is extended as playback progresses. Only objects named by this EA's `RBP_FX_BLACKOUT_` prefix are created.

After a run, use the Strategy Tester tabs:

- **Graph** for balance/equity;
- **Results** for individual orders/deals and entry/exit prices;
- **Backtest** (or the report/statistics area in the current terminal build) for final statistics;
- the visual chart for the price action, trade markers and bracket exits.

Use the report context menu or **Save as Report** / **Save as Detailed Report** when your terminal build offers it. Change Inputs, symbol, timeframe, date range or execution assumptions, then press **Start** again to repeat a test.

### TradingView parity and fill model

The EA confirms pivots only after their right-side bars have closed, evaluates every setup state on the completed bar, prevents pyramiding, and freezes the ATR-derived bracket at the signal. TradingView’s `process_orders_on_close=true` can fill at the confirmation bar close. MT5 receives the signal at the following bar’s first tick and uses bid/ask prices, so the entry fill can differ even though the signal bar is the same. Different data, spreads, commissions and intrabar stop/target paths can also change P&L.

The New York DST implementation uses the current US DST rule (second Sunday in March to first Sunday in November) for 2007 onward. For older historical data, verify the blackout manually because the statutory US dates differed before 2007.

## Part B — Forex Tester 6

The native FT6 strategy is a 32-bit Windows DLL. The canonical source is `forex-tester-6\RangeBreakoutPullbackStrategy\RangeBreakoutPullbackStrategy.cpp`; it was built against the installed FT6 C++ API and deployed as `C:\ForexTester6\Strategies\RangeBreakoutPullbackStrategy.dll`.

### Build and add the strategy

1. The supplied FT6 C++ example specifies an empty **32-bit DLL** project in Microsoft Visual C++. Add the strategy `.cpp` and `.def` files, add `C:\ForexTester6\Examples\Strategies\C++` to the include path, and use `RangeBreakoutPullbackStrategy.def` as the Linker **Module Definition File**. Link `oleaut32.lib`.
2. Close Forex Tester 6 before replacing `C:\ForexTester6\Strategies\RangeBreakoutPullbackStrategy.dll`; Windows keeps a loaded strategy DLL locked. Copy the rebuilt DLL, then restart Forex Tester 6.
3. Open the **Strategies** tab and select **List of Strategies**. `Range Breakout-Pullback` should appear. Enable the strategy using its switch, then use its gear/settings control.
4. Choose a project instrument for `Currency` and a matching `Timeframe`; FT6 requires both. Set **Setup mode** to `0` for the original range logic or `1` for Impulse Pullback Continuation. The impulse settings define its minimum ATR body/range, optional maximum body cap, pullback retracement, maximum depth and maximum duration. **Impulse maximum body ATR (0 disabled)** is active only in mode `1`; enter `1.5` for the next EURUSD M15 experiment. `Depth mode` is `0 Any`, `1 Shallow`, `2 Deep`, `3 Custom`; confirmation is `0 Aggressive`, `1 Balanced`, `2 Conservative`; stop mode is `0 Adaptive ATR`, `1 Fixed ATR`.
5. For the simplest deterministic setup, create the FT6 project with **Timezone: GMT+0** and **Daylight Saving Time: No DST**, then set `Server UTC offset hours` to `0`. The strategy converts the UTC project time itself to America/New_York for the Friday/Sunday blackout; no changing offset is needed.

### Dry run and review

1. Create or open a testing project for the instrument. Choose the historical period/data and configure the project’s spread, commission, deposit and leverage assumptions before starting.
2. In **Strategies**, turn **Strategy execution** on and make sure the Range Breakout-Pullback strategy is enabled. Click **Start** for visual playback; the chart, Orders/Account History and strategy trades update as the test advances.
3. For an automated run, use **Strategies → Quick Test**. Select the period and project settings, confirm the strategy parameters, then start the test. This is the appropriate fast dry-run workflow; no broker account is used.
4. Review individual entries/exits in the account history/orders area, price action on the chart, and the project’s statistics/performance panels. Use the history context menu’s export option where available to save trades/results. Change the gear/settings values or project execution assumptions and run **Quick Test** again to compare a new run.

FT6 calls the DLL on ticks, but the port advances its setup state only after a new bar reveals the prior completed bar. Instant orders therefore fill on the first available tick after the confirmation bar, rather than TradingView's broker-emulator close. The same New York DST rule limitation applies: the current US DST dates are modelled for 2007 onward; verify earlier dates manually.

Enable **Show blackout status** and **Show diagnostics** when investigating a missed setup. FT6 records compact Journal messages only when the gate/state changes, including blackout start/end, setup progression, specific reset reasons and entry request; it does not print on every completed bar. Its supplied strategy API does not offer a clean historical chart-background shading equivalent, so this intentionally avoids creating many chart objects. Pine's Data Window gate code separates FX, busy, range, impulse, extension, pullback start, opposing closes, ATR retracement, minimum depth, confirmation, ATR risk and entry; its reset value separately identifies invalidation, extension expiry, pullback expiry and maximum depth. Across platforms, the main skip categories are blackout, position busy, range/impulse not qualified, extension/pullback/retracement/depth failure, resumption failure, expired setup, or ATR risk not ready.

## Which one is easiest?

TradingView is quickest when the strategy is unchanged: its Pine source is already on the user’s normal chart and changing inputs/rerunning is immediate. MT5 is the most practical native-testing option after the first compile: its Strategy Tester, visual mode, reports and parameter repeats are tightly integrated, but the `.mq5` must be copied/compiled after updates. Forex Tester 6 takes the most maintenance when source changes because it requires rebuilding and replacing a 32-bit DLL, but its Strategy execution, visual playback and Quick Test workflow are convenient once installed. Use the platform whose data, spread and execution assumptions you want to study; parity of rules does not make their P&L identical.
