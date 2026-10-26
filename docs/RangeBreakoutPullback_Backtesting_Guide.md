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
4. Choose a project instrument for `Currency` and a matching `Timeframe`; FT6 requires both. Set **Setup mode** to `0` for the unchanged original range logic or `1` for Impulse Pullback Continuation. In impulse mode, a qualifying body/range candle seeds a parent leg anchored at the latest confirmed `Minor swing strength` pivot; later directional extremes extend that same leg and reset any still-unqualified PB1. Detection continues while a strategy position is open, but entry remains blocked: a candidate may survive until the position closes, while a PB1 resumption that occurs while busy is consumed and is never queued for delayed entry. Expansion retained before its origin pivot confirms is promoted only after confirmation by replaying already-closed bars chronologically. The replay preserves the endpoint and first PB1 known at each bar; a resumption that already passed, including one on the promotion bar, is consumed rather than retrospectively entered or re-armed. A historically qualified PB1 with no resumption through promotion remains armed from its actual qualification bar for a genuinely future signal. **Impulse maximum body ATR (0 disabled)** remains a normal seed-candle filter only; an over-cap candle cannot seed independently and remains provisional until a later normal same-direction seed or later same-direction endpoint advancement supplies objective multi-bar evidence. An opposing, doji or non-advancing bar cannot unlock it. While the parent PB1 is unresolved, contained same-direction expansions remain part of that parent rather than replacing it with a child leg. The first pullback arms only after its opposing-close, ATR-retracement, selected depth, duration and invalidation gates pass. After PB1 is consumed by entry, busy/passed resumption, invalidation or expiry, another correction cannot re-arm it. A same-direction opportunity requires a newly confirmed corrective origin after consumption and a new structural extreme. **Impulse M15/H4 trend filter (0 disabled)** remains a separate optional entry filter: when set to `1`, entry requires closed-candle EMA50 above/below EMA200 on M15 and closed-candle EMA10 above/below EMA30 on H4 in the trade direction. It is unavailable until 200 completed M15 and 30 completed H4 candles exist; the H4 value is updated only after its 4-hour candle closes. `Depth mode` is `0 Any`, `1 Shallow`, `2 Deep`, `3 Custom`; confirmation is `0 Aggressive`, `1 Balanced`, `2 Conservative`; stop mode is `0 Adaptive ATR`, `1 Fixed ATR`.
5. For the simplest deterministic setup, create the FT6 project with **Timezone: GMT+0** and **Daylight Saving Time: No DST**, then set `Server UTC offset hours` to `0`. The strategy converts the UTC project time itself to America/New_York for the Friday/Sunday blackout; no changing offset is needed.

### M5 first-touch ATR experiments (presets 5 and 6)

`5 M5 first-touch ATR` copies **every existing setting** from `4 M5 Baseline` and changes only **Impulse entry mode (0 confirmation,1 first-touch ATR)** to `1`. `6 M5 first-touch ATR buys-only` copies every effective setting from preset 5 and changes only **Trade direction** from `0` (both) to `1` (buys only). Presets 1-4 explicitly use confirmation mode `0`; Custom retains its saved selector and all other saved inputs. Resolution remains once on the first tick of each run. These are predefined experiments, with no optimisation or claim of better profitability. Use EURUSD and inspect a short **standard-test** segment before comparing the two existing M5 yearly periods. No historical test is launched by installation.

Impulse recognition, body/range rules, confirmed origins, oversized/provisional evidence, parent ownership and independent-leg requirements remain in force. Only an eligible parent can activate a level. With the parent's causally captured **frozen setupATR**, the planned long level is the known endpoint high minus `0.50 * setupATR`; the short level is the known endpoint low plus `0.50 * setupATR`. The existing instrument `Point()` supplies the executable tick grid: long levels round down and short levels round up, requiring at least the configured retracement. There is no price/date fitted to an example and no eventual pullback extreme in this calculation.

An active level may refresh only after the existing closed-bar parent logic accepts a newer directional endpoint, before any first touch; its ATR basis stays frozen. The replacement activates prospectively on that tick. The opening quote is checked against the previously active level **before** the just-closed bar can replace it, after the normal closed-bar risk/structure update. An arming/refresh tick never submits an entry. If its quote is already at/past the new level, the opportunity is consumed with `touch already passed`.

A later tick triggers a long only when executable **Ask <= planned level**, or a short when executable **Bid >= planned level**. A Bid-only buy touch and Ask-only sell touch cannot create an order. FT6's existing instant-order API submits at the currently available quote: a gap is filled at that quote, never a manufactured planned-price fill. The submission log gives planned price, actual quote and server time to the second. Confirmation requires the API success result **and** a nonnegative returned ticket; failure retires the opportunity without retry. This is an instant-order model, not a limit-order queue; platform execution and configured spread still govern actual results.

PB1 starts causally on the first observed executable adverse tick relative to the known endpoint, without waiting for opposing closes. In mode 1 the **Minimum opposing closes** and **Confirmation** selectors are ignored for impulse entry; their saved values and legacy meanings are unchanged. Preset 5 keeps the Shallow **50% maximum depth**, 30-bar expiry, invalidation, weekend blackout, history/risk readiness, direction/position restrictions and optional Custom EMA filter. Tick depth uses Bid, consistently with FT6 bar prices. A first touch blocked by these gates is consumed, never queued. Depth, invalidation and expiry can also retire the parent before a touch. Consumed parents require the same distinct new structural leg as before; a second correction or later second touch cannot revive them.

Before recognition, compact observed tick paths distinguish adverse prices **after** the accepted endpoint from the seed's earlier prices. Equal endpoint revisits do not erase an earlier touch. The last 64 completed bar summaries cover the preset's 30-bar recovery window. Provisional/confirmed-origin promotion replays chronologically, checking each prior endpoint's level before accepting a later endpoint. Any already-passed first touch is consumed. Where a complete observed tick path is unavailable (including startup, older Custom recovery windows or incomplete historical coverage), OHLC cannot prove endpoint/touch ordering or historical Ask. Recovery conservatively skips ambiguous opportunities, logs `touch already passed`, and never invents an intrabar sequence or a retrospective fill. This can skip a setup that a full earlier tick record would have established as fresh. Legacy confirmation replay is unchanged.

Adaptive ATR stops, actual-entry bracket rounding, fixed preset size **0.01 lots**, entry-time structural classification, frozen **2R with-trend/neutral / 3R counter-trend** targets and existing trade management remain in place. Diagnostics retain their established prefixes and add `entryMode=first-touch-ATR touchATR=0.50` with the selected preset ID (`5` or `6`). Arm/refresh records include parent/endpoint, frozen ATR, planned price, activation time/tick and rounding policy. Supported `RBP_DIAG_..._TOUCH` text marks the planned price on the chart, alongside the existing impulse/PB1/class markers. Specific skip/consume/order-failure reasons are emitted once per event, with no per-tick logging.

Expected initialization for preset 5:

```text
RBP preset=M5 first-touch ATR timeframe=5 setup=1 confirmation=0 direction=0 body=1.00-1.50ATR depth=0-50% depthMode=1 stop=adaptive targets=2.0/3.0R EMAtrend=off scope=0 entryMode=first-touch-ATR touchATR=0.50 presetId=5 maxCompletedTrades=100
```

The synthetic seam `tests/ft6_first_touch.cpp` accepts exactly one named case; it has no default suite. The existing lifecycle seam requires `preset_lifecycle`. Exact selected commands and build/deployment evidence are recorded in `summary/CODEX_SUMMARY_latest.txt`. These checks establish causal mechanics and preservation, not profitability or live chart acceptance.

Expected initialization for preset 6:

```text
RBP preset=M5 first-touch ATR buys-only timeframe=5 setup=1 confirmation=0 direction=1 body=1.00-1.50ATR depth=0-50% depthMode=1 stop=adaptive targets=2.0/3.0R EMAtrend=off scope=0 entryMode=first-touch-ATR touchATR=0.50 presetId=6 maxCompletedTrades=100
```

### M5 qualified first pullback experiment (preset 7)

Select numeric **7 M5 qualified first pullback buys-only** for the new EURUSD M5 experiment. It inherits preset 6's seed/provisional and oversized-candle evidence rules, frozen setup ATR, buys-only permission, adaptive stops, 0.01 lots, 2R/3R structural targets, EMA filter off, 50% maximum depth, 30-bar expiry and other safeguards. Presets 0?6 retain their existing behavior; entry modes `0 confirmation` and `1 first-touch ATR` remain available. Mode `2 qualified-first-pullback` is also selectable in Custom, including mirrored shorts. The four new saved Custom inputs default to and are explicitly applied by preset 7 as follows:

- **Qualified impulse range lookback bars** = `20`.
- **Qualified impulse minimum displacement ATR** = `3.0`.
- **Qualified impulse breakout buffer ATR** = `0.10`.
- **Qualified pullback minimum depth %** = `25.0`.

These are fixed starting hypotheses, not optimised thresholds or exact user-supplied values. For the causally confirmed origin, freeze the highest high and lowest low of exactly N completed strategy bars immediately **before** the origin. Exclude the origin and all later bars; an incomplete window or invalid frozen ATR cannot qualify. A completed long candle must close **strictly above** `frozenRangeHigh + buffer * setupATR`, while accepted origin-to-endpoint displacement `D >= minimumDisplacementATR * setupATR`. Custom shorts require a close strictly below `frozenRangeLow - buffer * setupATR` with the mirrored displacement. Both conditions must hold before a level can activate; the actual qualifying bar/time is retained. Unfinished candles and future pivots never establish qualification.

Pending parents keep advancing without an active level. They expire after the existing recovery window (30 bars in preset 7), measured from the first seed, or are abandoned on invalidation/replaced by a normal seed with a newer incompatible confirmed origin. Never-qualified abandonment does not advance the real consumed-parent boundary. Existing oversized evidence, distinct-origin and consumed-parent protections still apply. Structure remains observed in both directions despite buys-only permission.

For mode 2 only, with the accepted closed-bar endpoint and frozen setup ATR:

```text
D = endpointHigh - origin                    (long)
D = origin - endpointLow                     (short)
required = max(ImpulseMinimumRetracementATR * setupATR,
               QualifiedPullbackMinimumDepth / 100 * D)
long planned = floor((endpointHigh - required) / Point()) * Point()
short planned = ceil((endpointLow + required) / Point()) * Point()
```

Preset 7 retains `ImpulseMinimumRetracementATR = 0.50`. A smaller dip leaves the opportunity available. A later accepted endpoint can refresh its level prospectively; ATR remains frozen. The prior level receives the opening quote before replacement. No arming/refresh tick fills; already-passed or ambiguous endpoint/touch paths are consumed conservatively, including chronological provisional recovery. Live eligibility is never backdated. A long requires executable Ask at/below the level, a short Bid at/above it, and the actual available quote supplies the fill after overshoot. A blocked/failed first touch is consumed without queue or retry; both API success and a valid returned ticket are required. Existing depth, invalidation, expiry, weekend, history/risk, direction/position, optional Custom EMA and order gates remain active. Missing tick/spread chronology can skip a setup; OHLC does not establish the required intrabar order.

Mode-2 inputs require lookback >= 1, finite positive displacement, finite nonnegative buffer, and finite minimum depth strictly between zero and the effective maximum depth. An invalid configuration emits one error per run and blocks entries without clamping; these controls do not affect legacy entry modes. Preset resolution remains once per run after late FT6 inputs, and returning to Custom restores all saved values. The separate completed-trade limit still defaults to **100** and is never overwritten by any preset, including 7. Exactly 100 trades does not establish reliability.

Expected initialization with the default independent limit:

```text
RBP preset=M5 qualified first pullback buys-only timeframe=5 setup=1 confirmation=0 direction=1 body=1.00-1.50ATR depth=0-50% depthMode=1 stop=adaptive targets=2.0/3.0R EMAtrend=off scope=0 entryMode=qualified-first-pullback touchATR=0.50 presetId=7 maxCompletedTrades=100 qualifiedLookback=20 qualifiedDisplacementATR=3.00 qualifiedBufferATR=0.10 qualifiedDepthPct=25.00
```

Event diagnostics retain `RBP` prefixes and report pending/qualified/arm/refresh/skip/consume reasons, frozen range, origin, endpoint, displacement/ATR, required retracement and qualifying time. The existing planned TOUCH text marker identifies mode 2 as `Touch QFP`; no new chart objects or per-tick logging are introduced.

The reviewed August?September 2023 sample and September screenshots are **development data**. The unchanged 100-trade sample was approximately -13.76R; it does not prove this replacement profitable. Screenshot geometry was not reconstructed as OHLC/ticks or replayed, and the approximate yellow-line area is not a required fill or known future bottom. Synthetic checks establish causal mechanics, not screenshot matches, chart acceptance or profitability. Freeze these rules before any later profitability check, and use fresh data for that check. The user performs chart acceptance and the next standard 100-trade test; no historical backtest, replay or optimisation accompanies this implementation.

### Automatic stop after completed trades

`Maximum completed strategy trades (0 unlimited)` is an independent run control with a default of `100`. Presets, including preset 7, leave the selected value intact. Set it to `0` for an unrestricted run. Negative values are rejected and block entries.

FT6 pauses automatically when the number of completed market tickets in the current project history reaches or exceeds the limit. The count includes buys and sells matching the effective `Currency` and `Magic number`; each closed ticket counts once. Deposits/credits, pending orders, open positions, failed submissions and other symbols or magic numbers do not count. SL/TP and other normal exits count after the platform records the closed trade. Retained history is recounted after strategy reset, so reinitializing does not grant another 100 trades. Trade 100 closes through its ordinary exit and the paused project retains its chart, account history and statistics.

To export the run, use FT6's normal report/statistics area or Results context menu and select **Save as Report** or **Save as Detailed Report** when available. Leave the test running; FT6 pauses after the matching completed-trade limit.

### Dry run and review

### FT6 presets and parameter order

The first strategy setting is **Settings preset (0 Custom, 1 Baseline, 2 Trend-filter test, 3 Baseline buys-only, 4 M5 Baseline, 5 M5 first-touch ATR, 6 M5 first-touch ATR buys-only, 7 M5 qualified first pullback buys-only)**. To select the new experiment, open the Range Breakout-Pullback strategy settings, set **Settings preset** to numeric `7`, click **Apply**, then close the settings. Restart a standard test so the preset resolves on its first tick. `0 Custom` uses every detailed setting as entered, except the existing 2R/3R target-floor clamping. Presets are resolved once at the first tick of each new standard test or Quick Test, after FT6 supplies the current option values, including after a restart. A non-Custom preset uses separate effective settings internally; the detailed Custom values remain saved and are restored when you select `0 Custom` for a later run. Forex Tester may continue to display those Custom values while a preset is selected.

`1 Baseline` uses Impulse setup, Aggressive confirmation, 1.0 minimum / 1.5 maximum impulse seed-body ATR, 1.25 minimum impulse seed-range ATR, 0.50 minimum impulse retracement ATR, Shallow depth 0–50%, Deep 50–100%, Custom 0–100%, maximum impulse pullback bars 30, minimum opposing closes 2, Adaptive ATR stop, weekend blackout on, server UTC offset 0, diagnostics on, 0.01 lots, and the existing M15/H4 trend filter off. In the existing FT6 depth mapping, `1` means Shallow (`0` remains Any), so the preset uses the Shallow mapping without changing its established parameter meaning. The effective structural targets are exactly 2R with-trend/neutral and 3R counter-trend; the saved Custom target values are preserved.

`2 Trend-filter test` is exactly Baseline with the existing **Impulse M15/H4 trend filter (0 disabled)** enabled. Its EMA calculation, readiness and rejection behaviour are unchanged. **Trade direction (0 both,1 buys only,2 sells only)** controls order permission; `0` is the default. Impulse structure is tracked in both directions even when execution is restricted. A prohibited-side parent follows the same confirmed-origin, candidate/promotion, PB1 and consumption rules, but its first touch or legacy confirmation resumption consumes the opportunity without an order, entry marker or delayed retry. This permits a genuinely new allowed-direction cycle inside an older consumed boundary after the opposite structural cycle; it does not revive the old parent or its second pullback. Transition diagnostics identify `execution=structure-only` versus `execution=direction-permitted`; all other order gates still apply. Range-mode direction handling is unchanged. **Trend filter scope (0 both,1 buys only,2 sells only)** then selects which allowed directions need EMA consensus. The scope is ignored when the trend filter is disabled; a bypassed direction has no EMA-readiness or alignment requirement.

`3 Baseline buys-only` is the isolated Baseline buys-only experiment: every effective value is identical to preset 1 except **Trade direction = 1 (buys only)**. It retains impulse/aggressive setup, body 1.00-1.50 ATR, shallow 0-50% depth (mode 1), adaptive stop, 2R/3R structural targets, EMA trend filter off and scope 0. Presets 1 and 2 both retain trade direction 0 (both). Saved detailed Custom values remain preserved.

`4 M5 Baseline` is the M5 variant of Baseline; `5 M5 first-touch ATR` differs from preset 4 only by first-touch ATR entry mode. `6 M5 first-touch ATR buys-only` differs from preset 5 only by **Trade direction = 1 (buys only)**. When preset 5 or 6 is selected, the Journal diagnostic is authoritative for effective settings because detailed rows can continue to show saved Custom values.

`4 M5 Baseline` matches preset 1 exactly except its effective strategy timeframe is M5 (`Timeframe=5`). Trade direction stays 0 (both) and EMA filtering stays off. ATR thresholds, bar counts, confirmation, stops and 2R/3R targets are unchanged; no parameters are optimised. Bar-count settings retain their numeric values: 30 M5 bars represent 150 minutes.

The chart timeframe controls the visible chart; the effective strategy timeframe selects the series used by strategy history reads. Presets 1-3 use M15, presets 4-7 use M5, and Custom uses its saved Timeframe value. Impulse detection, pullback processing, ATR, pivots and structural trend classification all use this active strategy series. The optional M15/H4 EMA filter remains a separate higher-timeframe filter with unchanged calculations. Every initialization diagnostic includes the effective timeframe in minutes (`timeframe=5` for presets 4-7, `timeframe=15` for presets 1-3, and the actual effective value for Custom). Preset 4 begins `RBP preset=M5 Baseline timeframe=5 setup=1 confirmation=0 direction=0` and retains `body=1.00-1.50ATR depth=0-50% depthMode=1 stop=adaptive targets=2.0/3.0R EMAtrend=off scope=0`.

The remaining properties are grouped in this order: General execution/setup; Confirmation; Range-mode settings; Impulse-detection settings; Pullback and depth settings; Stop, risk and structural R targets; Time and weekend settings; Diagnostics. **With-trend / neutral target R (minimum 2.0)** defaults to `2.0`; **Counter-trend target R (minimum 3.0)** defaults to `3.0`. Values below those floors are clamped. For impulse entries, confirmed higher-high/higher-low structure on the active strategy timeframe is Uptrend, confirmed lower-high/lower-low structure is Downtrend, and mixed, equal or insufficient pivots are Neutral. A long in Uptrend or short in Downtrend uses the with-trend target; the opposite pair uses the counter-trend target; Neutral uses the normal target. The structure snapshot, risk and target are frozen at entry. Range mode continues to use the normal target and is otherwise unchanged. Each run emits exactly one compact initialization diagnostic with the resolved preset and effective core settings, before any history-readiness or trading checks. Standard testing exposes it in the Journal. Baseline begins `RBP preset=Baseline timeframe=15 setup=1 confirmation=0 direction=0 body=1.00-1.50ATR`; Trend-filter test reports `direction=0` and `EMAtrend=on`. Baseline buys-only begins `RBP preset=Baseline buys-only timeframe=15 setup=1 confirmation=0 direction=1 body=1.00-1.50ATR` and reports `depth=0-50% depthMode=1 stop=adaptive targets=2.0/3.0R EMAtrend=off scope=0`. Every preset diagnostic includes the effective `direction=<value>`.

1. Create or open a testing project for the instrument. Choose the historical period/data and configure the project’s spread, commission, deposit and leverage assumptions before starting.
2. In **Strategies**, turn **Strategy execution** on and make sure the Range Breakout-Pullback strategy is enabled. Click **Start** for visual playback; the chart, Orders/Account History and strategy trades update as the test advances.
3. For an automated run, use **Strategies → Quick Test**. Select the period and project settings, confirm the strategy parameters, then start the test. Quick Test provides only its own summary; no broker account is used. It does not populate the standard project Journal, Account History or detailed export.
4. Use **standard testing** (start/restart the project test) for the Journal, individual entries/exits in Account History, chart review and detailed export. Use the history context menu's export option where available. After changing settings and clicking Apply, start/restart a standard test to inspect its new initialization diagnostic and trades, or run **Quick Test** again to compare only its summary.

FT6 calls the DLL on ticks. In confirmation mode, the port advances its setup state only after a new bar reveals the prior completed bar. Instant orders therefore fill on the first available tick after the confirmation bar, rather than TradingView's broker-emulator close. The same New York DST rule limitation applies: the current US DST dates are modelled for 2007 onward; verify earlier dates manually.

Enable **Show blackout status** and **Show diagnostics** when investigating a missed setup. FT6 records compact Journal messages only when the gate/state changes. Impulse records identify seed measurements and rejection/retention reasons, open-position observation, confirmed-origin availability, provisional promotion, parent-versus-contained-child decisions, parent origin/endpoint/displacement, PB1 start and measurements, reset/qualification/consumption reason, busy-signal consumption, independent-leg eligibility, resumption mode, the exact four confirmed pivots used for the real-time structure snapshot, trade classification, frozen risk and selected R target. Pivots affect structure only after their right-side bars close; later pivots never revise or backdate an earlier entry decision. For a qualified PB1, four small supported text markers show `I-start`, `I-end`, `PB1` and the entry class (`WT`, `CT` or `N`); no per-bar labels are created, and normal FT6 trade markers remain intact. Across platforms, the main skip categories remain blackout, position busy, range/impulse not qualified, extension/pullback/retracement/depth failure, resumption failure, expired setup, or ATR risk not ready.

## Which one is easiest?

TradingView is quickest when the strategy is unchanged: its Pine source is already on the user’s normal chart and changing inputs/rerunning is immediate. MT5 is the most practical native-testing option after the first compile: its Strategy Tester, visual mode, reports and parameter repeats are tightly integrated, but the `.mq5` must be copied/compiled after updates. Forex Tester 6 takes the most maintenance when source changes because it requires rebuilding and replacing a 32-bit DLL, but its Strategy execution, visual playback and Quick Test workflow are convenient once installed. Use the platform whose data, spread and execution assumptions you want to study; parity of rules does not make their P&L identical.
