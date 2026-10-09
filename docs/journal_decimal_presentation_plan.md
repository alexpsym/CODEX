# Job 7A — journal decimal presentation policy and feasibility

Date: 8 October 2026
Repository baseline: `c9238885fe90b09d03108a817053d1280af0ff49` (`master`, matched fresh `origin/master` at inspection).
Status: feasibility only. No production code or workbook was changed.

## Decision requested for Job 7B

**Recommended choice:** preserve every numeric cell, formula expression/cache, and calculation input. Apply true signed upward rounding to existing text-display surfaces only. Keep numeric/formula cells numeric and retain/adapt their number formats for readable precision, while documenting that Excel will still use its ordinary format rounding there. This is the only option that preserves the explicit workbook-value/formula requirements without adding rows, columns, sheets, VBA, or another display surface. It does **not** satisfy a strict “round upward everywhere” requirement for numeric/formula cells.

A strict upward display for those cells requires relaxing at least one requirement: replace numeric/formula cells with text, alter formulas to calculate rounded values, or add a separate display surface. Text removes numeric sorting/arithmetic behavior; formula changes alter expressions/results and may affect dependents/cached results; a companion display surface changes the workbook layout. None is authorized by the current preservation contract. Please confirm whether the recommended preservation-first split is acceptable before Job 7B. If strict upward rounding on every visible numeric/formula cell is mandatory, specify which of those tradeoffs may be made.

## Proposed signed precision policy

All rules operate on the value in its **displayed unit**, using `Decimal` built from a decimal string representation rather than constructing from a binary float. For finite nonzero magnitude `a`:

- If `a >= 1`, use 2 decimal places.
- If `0 < a < 1`, use `max(2, 1 - a.adjusted())` decimal places.
- Quantize the magnitude to that decimal place with `ROUND_UP`, then reapply the input sign. `ROUND_UP` means away from zero, so negative values become more negative when any discarded digit is nonzero.
- Do not increment exact representable values. Keep at least two decimal places; retain any additional zero required by the selected precision. Thus `4.6` displays `4.60`, while an exact `4.68` remains `4.68`.
- Choose decimal places from the original magnitude, once. If rounding carries across a boundary, keep that precision: for example, `0.9999` becomes `1.00`, and `0.09999` becomes `0.100`.
- Missing values stay blank (or retain an existing intentional explanatory string); non-finite values are not converted to zero or displayed as `NaN`/infinity. Handle them as unavailable/blank at the presentation boundary.
- Do not impose the current 12-place adaptive-format cap on Decimal-generated strings. For numeric Excel cells, do not promise recoverable digits beyond Excel's stored numeric precision; Excel documents 15 digits of numeric precision. Avoid increasing the number-format registry once per value or creating unbounded per-cell formats.

| Displayed input and unit | Places | Proposed display | Notes |
| --- | ---: | --- | --- |
| `0.00038475834753%` (percentage points) | 5 | `0.00039%` | Convert an Excel fraction to points once, if that is the source representation. |
| `4.689345%` | 2 | `4.69%` | Magnitude away from zero. |
| `4.680001%` | 2 | `4.69%` | Distinguishes upward from nearest rounding. |
| `-4.689345%` | 2 | `-4.69%` | Preserve sign; magnitude grows. |
| `-0.00038475834753%` | 5 | `-0.00039%` | Never round a negative loss toward zero. |
| `0.000000344535%` | 8 | `0.00000035%` | No 12-place ceiling in text formatting. |
| `4.68%`, `0%` | 2 | `4.68%`, `0.00%` | Exact values get no extra increment; retain minimum display precision. |
| `-0.25R` | 2 | `-0.25R` | R is an ordinary displayed unit; do not scale by 100. |

### Unit and category rules

| Category | Policy input unit | Display handling |
| --- | --- | --- |
| Profit/result percentages, stop/target distances, drawdown and percentage-point gaps | Percentage points | Convert stored Excel fractions by multiplying by 100 exactly once. Values already represented as points remain unscaled. Gaps use `pp`, not `%`, where the existing label says percentage points. |
| R multiples / target-R labels | R | No percent scaling. Do not change `_target_r_display_decimal()` or `_finalize_target_r_recommendation_decimal()`; the former participates in recommendation decisions, despite its display-oriented name. |
| Ordinary decimal measurements | Their existing displayed units | No scaling. |
| Non-balance currency amounts (commission, Net P/L and other currency-valued metrics) | Currency units | Apply the same precision rule to the numeric amount, preserving the existing currency code/symbol and sign convention. This may show tiny nonzero charges/results instead of zero. |
| Prices and quantities | Existing price/quantity units | Do not silently exclude them. The same outward precision rule would apply at the presentation boundary, but it can show a price or quantity above its exact stored amount and need not align with a market tick or lot step. Job 7B must not alter the stored price/quantity. |
| Currency/account balances, including Trade Log `Balance After` and STATS 2 account `Balance` | Existing balance presentation | Explicitly excluded: leave their values and currency/account formats unchanged. |
| Counts, IDs, dates, timestamps, and duration encodings | Existing typed representation | No decimal policy; retain current integer, identifier, date, and duration behavior. |

## Producer/consumer map from the inspected implementation

| Surface | Producers / display consumers | Current numeric/string contract and refresh path |
| --- | --- | --- |
| Trade Log | `_trade_log_row_values`; `_apply_trade_log_adaptive_formats`; `_apply_trade_log_row_number_formats` | `Profit %` is written as `result_pct / 100` (Excel fraction); validated stop/target distances are fractions under fixed `0.00%`; R-Multiple, Qty, prices, commission, Net P/L, and Balance After remain numeric. Full generation and schema repair use adaptive formats; incremental append and preservation resync use `_apply_trade_log_row_number_formats`. Balance formatting is a separate account-currency path. Formatting a cell does not directionally round its stored value. |
| STATS 1 | `_stats1_sheet`; `_format_inline_metric_value`; `_apply_stats1_semantic_formatting`; `_repair_stats1_formatting`; data-only `update_master_journal_workbook_data_only()` writers (`write_metric`, `write_market_metric`, dashboard metric-cell helpers) | Aggregate percentage-point values are divided by 100 once for numeric percentage cells; numeric R cells remain R. Source-linked extrema may instead be strings with symbol/date/trade identity. The semantic policy uses fixed `0.00%` and `0.000"R"`; source-linked text currently uses fixed `.8f` percent / `.3f` R. Preservation mode treats STATS 1 presentation as user-authored; value updates must not normalize manually authored fonts/styles. |
| STATS 2 | `_stats2_sheet`; `_write_stats2_net_pl_percentages`; `_repair_stats2_account_balance_formatting` | Account `Balance` is explicitly a balance exclusion with currency-dependent format. `Risk of Ruin` and `Net P/L Percentage` are separate numeric percentage fractions. Preserve all account rows and current risk calculations; do not route balances through a generic decimal helper. |
| SYMBOLS | `_populate_symbols_metrics_preserving_layout`; `_normalize_symbols_performance_formatting`; `_apply_instrument_averages_profit_loss_formatting`; data-only refresh metric writers | `Net P/L %` and `Avg P/L %` use percentage formatting; `Net R Multiple` uses fixed R formatting; stop/target distance columns are distinct. The preservation writer updates metric cells in place and retains table structure/user styling. Any change to rendered values must leave observations, calculations, row identities, hyperlinks, and presentation structure untouched. |
| P&L Calendar | `_write_pnl_calendar_one_line_layout`; `_format_pnl_calendar_month_cell` | The cell is generated text (`pct_points:.2f%` plus trade count), not a numeric percentage cell. A Decimal formatter can change the visible percentage while leaving aggregation and count untouched. |
| YEARLY REPORT and year-named monthly reports | `_report_cell_value`; `_write_report_sheet`; `_update_report_sheet_preserving_layout`; `_apply_report_semantic_formatting` | Numeric percentage points become Excel fractions with fixed `0.00%`; numeric R gets `0.000"R"`; extrema with safe source identity can be text via `_format_inline_metric_value`. Preservation refresh updates existing cells and keeps report layout/styles. Do not replace layout repair or source-preservation rules for this job. |
| Recommendation text and charts | `_format_stop_pct_value`, `_format_stop_gap_value`, `_format_target_r_value`; `_recommendation_chart_observations`, `_recommendation_svg`, `_recommendation_chart_html` | Stop recommendation/explanation strings use fixed 2 decimals; gaps use fixed `pp`; target labels use fixed 1/2 decimals. SVG axes/marker geometry use raw observations and recommendation values; tick labels use `.3f`, recommendation labels and tooltips use `.8g`. Only rendered labels may change; do not round values before recommendation selection or geometry. `_target_r_display_decimal` is calculation-sensitive and out of scope for display-only rounding. |
| Formula and cache preservation | Trade Log formula-cache snapshot/transplant/verification helpers; preservation update path | Leave formula text, input values, cached formula results, and existing dependency checks unchanged. Do not wrap formulas with `ROUNDUP`, convert formula outputs to text, or write rounded display values back to source cells. |

The map covers the inspected generator and preservation/update paths; it is not a claim that every legacy/manual workbook layout has been exhaustively tested.

## Feasibility and preservation findings

Excel custom number formats control how a numeric value is rendered. Microsoft documents that digit placeholders round a value to the number of decimal placeholders, while the ROUNDUP function is a separate value/formula operation. Custom formats therefore cannot request decimal `ROUND_UP`/away-from-zero semantics. Python Decimal provides `ROUND_UP` (away from zero) and `adjusted()` for the proposed precision calculation. Excel number-format metadata inspected through openpyxl is not evidence of Excel's rendered output.

Changing only a cell's number format leaves its number/formula intact, but does not guarantee the requested direction. Changing a literal numeric cell to text preserves a visible string but removes numeric behavior. Replacing a formula with a rounded constant loses the formula; wrapping it with `ROUNDUP` changes the formula result and can affect dependents and caches. Those approaches conflict with the explicit requirement to preserve underlying values/calculations/formulas.

Literal one-off formats are not a safe workaround. The current helpers produce a format string based on decimal-place count (capped at 12), which can already create one style format per distinct precision, though identical patterns can be reused. Microsoft documents roughly 200–250 available custom number formats depending on Excel language version. An unbounded precision ladder can grow format metadata; per-value literal formats would be worse. Keep the existing style registry, row/column dimensions, formula-cache transplant, and user formatting out of any generic rebuild. Use Decimal text output for surfaces that are already text, with unit-specific adapters; do not describe openpyxl format metadata as an Excel rendering test.

## Job 7B proposal (smallest source/test scope)

- Files: `tools/master_journal_workbook.py` and `tests/test_master_journal_workbook.py` only, plus the required summary update.
- Add a pure Decimal display helper for finite values and explicit adapters for Excel percentage fractions versus percentage points, R, ordinary decimals, currency, and existing text surfaces. Keep missing/nonfinite handling explicit and preserve existing source suffixes/labels.
- Apply it to existing generated text producers first: inline extrema/source text, stop and target explanation strings, P&L Calendar text, and recommendation/chart axis/marker/tool-tip labels. Keep chart observation values and geometry raw.
- Add narrowly selected temporary-workbook tests for all sample values, sign, exact/carry cases, unit scaling, source suffix retention, and identical repeat refresh. Reuse the named adaptive-format, fraction-preservation, and inline-extrema cases only where the chosen Job 7B change affects them. No real workbook/resync test is proposed.
- Keep numeric/formula cell payloads and balance exclusions unchanged under the recommended option. Retain existing adaptive number formats solely for visibility; explicitly mark strict directional display for those cells as an accepted limitation, not as completed behavior.

## Evidence and limits

One temporary Python probe imported and called the actual `adaptive_percent_number_format()` and `adaptive_number_format()` helpers and calculated the proposed policy with `Decimal`/`ROUND_UP` for the supplied values and signed/exact controls. It printed `0.00039%`, `4.69%`, `0.00000035%`, `4.69%` for `4.680001%`, `-4.69%`, `-0.00039%`, `0.00%`, and `4.68%`. The existing helper format metadata was `0.0000%`, `0.00%`, `0.0000000%`, `0.00%`, `0.00%`, `0.0000%`, `0.00%`, and `0.00%`, respectively, when given the matching fraction inputs. This shows the format selector's decimal-place choices only; Excel was not launched and no rendered result was observed. `adaptive_number_format(0.00038475834753)` returned `0.0000`.

No pytest processes/cases ran. No real workbook, journal data, runtime state, account, service, Excel, MT5, browser, or Dropbox content was accessed. The prior dashboard Exit job's live acceptance has now been reported successful by the user; this audit did not repeat it. Job 7 implementation remains pending the numeric/formula display tradeoff decision.

## References

- Python Decimal rounding modes and exact decimal arithmetic: https://docs.python.org/3/library/decimal.html#rounding-modes
- Excel custom number-format digit placeholders and rounding to placeholder count: https://support.microsoft.com/en-us/excel/review-guidelines-for-customizing-a-number-format
- Excel's separate ROUND, ROUNDUP and ROUNDDOWN functions: https://support.microsoft.com/en-us/excel/round-a-number
- Excel custom number-format availability and language-dependent count: https://support.microsoft.com/en-us/excel/get-started/available-number-formats-in-excel
- Excel 15-digit numeric precision: https://support.microsoft.com/en-us/excel/format-numbers-as-text

## Job 7B implementation status (2026-10-09)

The authorized text-only portion is implemented in `tools/master_journal_workbook.py`. Existing generated text now uses Decimal `ROUND_UP` away from zero with selected precision retained through carries, no fixed 12-place cap, blank handling for missing/non-finite inputs, and explicit unit adapters. The changed surfaces are source-linked inline percentage/R extrema, stop and stop-gap displays, target recommendation/explanation text, P&L Calendar percentage strings, recommendation chart labels/tooltips/observation tables, and identified non-balance mixed-currency commission text. Raw observations, recommendation calculations and R bucket keys, chart geometry, numeric/formula cell values and formats, and currency/account balances remain unchanged.

The refreshed preservation fixture now tests the supported Trade Log formula-cache transplant: row ID `t1`, `Commission`, formula `=ROUND(2.499,1)`, cached result `2.5`. It installs the cache after all workbook authoring and inspects each actual candidate before using it for the next refresh. No arbitrary STATS 2 cache preservation is claimed.

The exact preservation node passed in the final authorized run: `tests/test_master_journal_workbook.py::test_journal_decimal_text_refresh_preserves_numbers_formulas_balances_and_layout` (36.07 pytest seconds; 37.54 runner seconds). Both refreshes retained the formula and cached result, transplanted one cache with candidate verification true and no skipped entry for this cell, and preserved the generated source text. The three other selected nodes retain their earlier valid passes: `test_journal_decimal_text_rounds_up_with_units_and_preserves_sources`, `test_report_inline_extrema_use_numeric_fallback_and_preserve_existing_source_text`, and `test_recommendation_svg_high_density_preserves_every_marker_without_x_compression`.

Verification chronology: the initial four-node process passed three and failed the preservation fixture because canned aggregate statistics disagreed with the altered trade row (169.81 seconds). A one-node rerun was interrupted at 180 seconds without a test result. A reduced-fixture run then completed in 45.61 pytest seconds / 48 runner seconds but failed because a synthetic `STATS2!Z1` cache was outside the supported cache-transplant path and read back as `None`. The final fixture correction moved coverage to the supported Trade Log path; its build took 3.66 seconds and two refreshes took 15.78 and 11.28 seconds. Across Job 7B verification: 4 pytest processes, 7 case executions started across 4 distinct nodes; the preservation node passed only in the final run. The final streamed log is `.job7b_preservation_20261009_01/pytest.log`.

These are temporary synthetic-fixture checks only. No real workbook, Excel, journal resync or runtime data was accessed. Real workbook acceptance remains pending. Upward rounding for existing generated text is implemented; strict directional rounding of numeric/formula cells remains unresolved and is not waived. Job 7 as a whole is not complete.

## Job 7B preservation-assertion repair (2026-10-09)

The earlier passing preservation test compared numeric values only for Trade Log and compared only selected fields of the preservation contract; its initial full-generation statistics also differed from the preservation writer's row-derived output. This was an assertion-baseline gap, not evidence of a production defect.

The strengthened fixture performs one preparatory real preservation refresh with unchanged one-trade inputs and disabled asset publishing, inspects that returned candidate, and installs it as the baseline source. It then authors the existing STATS 1 style, an `A26` label-column hyperlink, and old generated text; saves those edits; installs the existing Trade Log formula cache last; and captures the baseline without another save. Each of the two measured real refreshes is inspected as a candidate before installation as the next source.

Both candidates are compared with the baseline's entire numeric dictionary across all represented sheets, all formula expressions, balances, calculation signature, chart parts/relationships, and the complete `_workbook_preservation_contract`, which includes full STATS 1 presentation, the manual hyperlink, comments, static labels, views, annotations, and structural properties. The test independently verifies the hyperlink exists in the baseline, expected changed text and source identity, and the Trade Log `t1` / `Commission` formula/cache (`=ROUND(2.499,1)` / `2.5`), one cache transplanted, candidate verification true, and no skipped cache for that cell. The first measured candidate is the second refresh's source; no cache is reseeded.

The strengthened exact node passed in one fresh process: `tests/test_master_journal_workbook.py::test_journal_decimal_text_refresh_preserves_numbers_formulas_balances_and_layout` (43.32 seconds test call, 47.56 pytest seconds, 49.37 runner seconds). Fixture timings: build 3.62 seconds, baseline preparation refresh 13.03 seconds, measured refreshes 11.93 and 12.74 seconds. Across Job 7B, five pytest processes and eight case executions were started across the same four nodes. The three other required nodes retain their earlier passes and were not rerun. The earlier unsupported `STATS2!Z1` cache failure remains fixture-only; no arbitrary STATS 2 cache preservation is claimed.

This is synthetic-fixture verification only. No real workbook or Excel was accessed; real workbook acceptance is pending. Text displays use the implemented upward policy. Directional upward rounding for numeric/formula cells remains unresolved and unwaived; Job 7 is not complete.

## Job 7C decision and implementation (2026-10-09)

The user superseded the earlier directional-rounding proposal with ordinary nearest presentation rounding. Earlier ROUND_UP examples and the Job 7B limitation above are historical. The active policy uses Decimal ROUND_HALF_UP for existing generated text; numeric and formula payloads remain exact and Excel number formats provide ordinary nearest display rounding. This leaves actual Excel rendering to live acceptance, rather than claiming openpyxl metadata proves it.

For each displayed unit, nonzero values use `max(2, 1 - abs(value).adjusted())` decimal places, selected before rounding and retained across carries. Percentage fractions are multiplied by 100 once for precision selection, while R, prices, quantities and currency are unscaled. Fixed formats are bounded at 12 places; smaller nonzero numeric values use a two-significant-digit scientific format. Generated text has no 12-place cap. Missing/nonfinite values stay blank under existing contracts; zero uses two places. Examples: `4.680001 -> 4.68`, `0.00038475834753% -> 0.00038%`, `0.000000344535% -> 0.00000034%`, and `0.6619274857% -> 0.66%`.

The generator-owned numeric surfaces now use adaptive formats in Trade Log profit/distance/R, quantity, price, commission and Net P/L cells; STATS 1 managed percentage/R/commission cells; STATS 2 non-balance percentages; SYMBOLS percentage/R metrics; and managed yearly/monthly report percentages/R/commissions. P&L Calendar and recommendation/inline/chart text reuse the changed Decimal renderer. Balance After and STATS 2 account Balance values and formats remain excluded. Counts, IDs, dates and duration encodings retain their typed formats. Numeric/formula values, calculation inputs, supported formula caches, chart raw data/geometry, recommendation selection and workbook structure are unchanged. A formula whose result is unavailable to the writer cannot select a value-dependent precision until refreshed with an available result; this remains an Excel-rendering limitation for live acceptance.

The service now checks the candidate workbook against its prepared recommendation bundle before final replacement, asset publication and GitHub sync. The prepared bundle supplies canonical final-workbook link names, so validation does not read stale published HTML. The existing final verification and verified post-replacement fingerprint remain in place. A rejected candidate leaves the previous workbook and assets untouched. This adds candidate validation but does not repeat chart preparation or recommendation calculation. Existing substage timings identify the workbook update as the 755-second stage in the reported live run; internal causes were not established from available diagnostics, so no performance change or live duration claim is made.
