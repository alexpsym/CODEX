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
