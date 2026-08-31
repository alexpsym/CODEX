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
4. Choose a project instrument for `Currency` and a matching `Timeframe`; FT6 requires both. Set **Setup mode** to `0` for the unchanged original range logic or `1` for Impulse Pullback Continuation. In impulse mode, a qualifying body/range candle seeds a parent leg anchored at the latest confirmed `Minor swing strength` pivot; later directional extremes extend that same leg and reset any still-unqualified PB1. The first pullback arms only after its opposing-close, ATR-retracement, selected depth, duration and invalidation gates pass. **Impulse maximum body ATR (0 disabled)** remains a seed-candle filter only; it never caps the cumulative parent-leg displacement. After PB1 is consumed by entry, invalidation or expiry, another correction cannot re-arm it. A same-direction opportunity requires a newly confirmed corrective origin after consumption and a new structural extreme. **Impulse M15/H4 trend filter (0 disabled)** remains a separate optional entry filter: when set to `1`, entry requires closed-candle EMA50 above/below EMA200 on M15 and closed-candle EMA10 above/below EMA30 on H4 in the trade direction. It is unavailable until 200 completed M15 and 30 completed H4 candles exist; the H4 value is updated only after its 4-hour candle closes. `Depth mode` is `0 Any`, `1 Shallow`, `2 Deep`, `3 Custom`; confirmation is `0 Aggressive`, `1 Balanced`, `2 Conservative`; stop mode is `0 Adaptive ATR`, `1 Fixed ATR`.
5. For the simplest deterministic setup, create the FT6 project with **Timezone: GMT+0** and **Daylight Saving Time: No DST**, then set `Server UTC offset hours` to `0`. The strategy converts the UTC project time itself to America/New_York for the Friday/Sunday blackout; no changing offset is needed.

### Dry run and review

### FT6 presets and parameter order

The first strategy setting is **Settings preset (0 Custom, 1 Baseline, 2 Trend-filter test)**. `0 Custom` leaves every detailed setting exactly as entered. A non-Custom preset uses its effective settings internally; Forex Tester may continue to display the previously entered detailed values.

`1 Baseline` uses Impulse setup, Aggressive confirmation, 1.0 minimum / 1.5 maximum impulse seed-body ATR, 1.25 minimum impulse seed-range ATR, 0.50 minimum impulse retracement ATR, Shallow depth 0–50%, Deep 50–100%, Custom 0–100%, maximum impulse pullback bars 30, minimum opposing closes 2, Adaptive ATR stop, weekend blackout on, server UTC offset 0, diagnostics on, 0.01 lots, and the existing M15/H4 trend filter off. In the existing FT6 depth mapping, `1` means Shallow (`0` remains Any), so the preset uses the Shallow mapping without changing its established parameter meaning. The preset does not overwrite the two structural target settings.

`2 Trend-filter test` is exactly Baseline with the existing **Impulse M15/H4 trend filter (0 disabled)** enabled. Its EMA calculation, readiness and rejection behaviour are unchanged. **Trade direction (0 both,1 buys only,2 sells only)** controls which setups the strategy may arm; `0` is the default. **Trend filter scope (0 both,1 buys only,2 sells only)** then selects which allowed directions need EMA consensus. The scope is ignored when the trend filter is disabled; a bypassed direction has no EMA-readiness or alignment requirement.

The remaining properties are grouped in this order: General execution/setup; Confirmation; Range-mode settings; Impulse-detection settings; Pullback and depth settings; Stop, risk and structural R targets; Time and weekend settings; Diagnostics. **With-trend / neutral target R (minimum 2.0)** defaults to `2.0`; **Counter-trend target R (minimum 3.0)** defaults to `3.0`. Values below those floors are clamped. For impulse entries, confirmed higher-high/higher-low M15 structure is Uptrend, confirmed lower-high/lower-low structure is Downtrend, and mixed, equal or insufficient pivots are Neutral. A long in Uptrend or short in Downtrend uses the with-trend target; the opposite pair uses the counter-trend target; Neutral uses the normal target. The structure snapshot, risk and target are frozen at entry. Range mode continues to use the normal target and is otherwise unchanged. The strategy Journal logs one compact initialization line with the resolved preset and effective core settings.

1. Create or open a testing project for the instrument. Choose the historical period/data and configure the project’s spread, commission, deposit and leverage assumptions before starting.
2. In **Strategies**, turn **Strategy execution** on and make sure the Range Breakout-Pullback strategy is enabled. Click **Start** for visual playback; the chart, Orders/Account History and strategy trades update as the test advances.
3. For an automated run, use **Strategies → Quick Test**. Select the period and project settings, confirm the strategy parameters, then start the test. This is the appropriate fast dry-run workflow; no broker account is used.
4. Review individual entries/exits in the account history/orders area, price action on the chart, and the project’s statistics/performance panels. Use the history context menu’s export option where available to save trades/results. Change the gear/settings values or project execution assumptions and run **Quick Test** again to compare a new run.

FT6 calls the DLL on ticks, but the port advances its setup state only after a new bar reveals the prior completed bar. Instant orders therefore fill on the first available tick after the confirmation bar, rather than TradingView's broker-emulator close. The same New York DST rule limitation applies: the current US DST dates are modelled for 2007 onward; verify earlier dates manually.

Enable **Show blackout status** and **Show diagnostics** when investigating a missed setup. FT6 records compact Journal messages only when the gate/state changes. Impulse records identify the confirmed-swing origin, seed, parent endpoint, cumulative displacement, PB1 start and measurements, reset/qualification/consumption reason, resumption mode, the exact four confirmed pivots used for the real-time structure snapshot, trade classification, frozen risk and selected R target. Pivots affect structure only after their right-side bars close; later pivots never revise an earlier trade classification. For a qualified PB1, four small supported text markers show `I-start`, `I-end`, `PB1` and the entry class (`WT`, `CT` or `N`); no per-bar labels are created, and normal FT6 trade markers remain intact. Across platforms, the main skip categories remain blackout, position busy, range/impulse not qualified, extension/pullback/retracement/depth failure, resumption failure, expired setup, or ATR risk not ready.

## Which one is easiest?

TradingView is quickest when the strategy is unchanged: its Pine source is already on the user’s normal chart and changing inputs/rerunning is immediate. MT5 is the most practical native-testing option after the first compile: its Strategy Tester, visual mode, reports and parameter repeats are tightly integrated, but the `.mq5` must be copied/compiled after updates. Forex Tester 6 takes the most maintenance when source changes because it requires rebuilding and replacing a 32-bit DLL, but its Strategy execution, visual playback and Quick Test workflow are convenient once installed. Use the platform whose data, spread and execution assumptions you want to study; parity of rules does not make their P&L identical.
