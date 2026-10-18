# A4 trading journal: Phase 6A layout proof and Phase 6B coverage plan

This is a synthetic layout proof and read-only foundation, not the full journal export. Phase 5 was live accepted by the user on 18 October 2026; Phases 1-4 remain accepted. No earlier feature was reopened.

## Sole production capture

- Source: `C:\GPT\CODEX-master\journal\Trading Journal.xlsx`.
- SHA256: `1508275c67b1ebfda0355ebc241cc3b139377533f2da4baebc7d34cee4d00547`.
- Size: 823271 bytes; before/after mtime_ns: 1792202206281703000; size/mtime agreed, and the byte count agreed with the source size.
- Exactly one source byte read/capture attempt; zero writes. An earlier host-TEMP setup failure occurred before any source stat/read and was not a capture attempt. All subsequent analysis used inert captured bytes. No Excel, recalculation, startup, Resync, import, account/network/broker operation or producer invocation.
- The immutable in-memory capture retains the bytes/hash. The temporary copy and structural inventory remain outside Git under a task-created host TEMP directory. No real transaction values or workbook copies enter this plan or the PDF.
- Bounded OOXML scan: maximum 200 MB expanded ZIP content and one million serialized cells; fail explicitly rather than scan arbitrary styled dimensions. Missing, zero, False and formulas are distinct. A far-away styled blank is excluded from meaningful bounds.

## Actual sheet inventory and coverage decisions

All 16 sheets are visible and all are business content. There are no Excel named tables in this captured workbook: report families are cell-range tables. Bounds below include populated values/formulas, not formatting padding. No current sheet is excluded. No separate month-named sheets were present; month sections belong to the year reports.

| Order / sheet (exact name) | Meaningful bounds | Populated cells | Merges | Formula / missing-cache count | Planned orientation and pagination |
|---|---|---:|---:|---:|---|
| 1. `STATS1` | A1:D136 | 509 | 0 | 0 / 0 | Portrait metric tables; keep section labels with following rows; repeat account/unit headers |
| 2. `STATS2` | A1:E9 | 39 | 0 | 0 / 0 | Landscape comparisons; repeat label/account headers; append 7 cell-addressed comments |
| 3. `SYMBOLS` | A1:AV123 | 5266 | 28 | 0 / 0 | Landscape semantic field bands; repeat Symbol/Class and shared row ranges; retain merged recommendation groups |
| 4. `Trade Log` | A1:AX1562 | 38544 | 52 | 0 / 0 | Landscape six-band prototype; field/record matrix for classifications/admin/notes; repeat source row/trade/account/symbol; continue tall notes |
| 5. `P&L Calendar` | A1:J13 | 105 | 0 | 0 / 0 | Portrait month blocks or landscape if all source columns require it; repeat calendar/year headers; retain zero days |
| 6. ` SIM` | A1:M109 | 641 | 0 | 511 / 0 | Landscape logical table blocks; retain all cached formula results; two chart objects require dedicated faithful rendering |
| 7. `YEARLY REPORT` | A1:J109 | 982 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 8. `2018` | A1:I109 | 847 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 9. `2019` | A1:M109 | 1097 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 10. `2020` | A1:M109 | 1221 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 11. `2021` | A1:M109 | 1166 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 12. `2022` | A1:M109 | 1053 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 13. `2023` | A1:M109 | 982 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 14. `2024` | A1:M109 | 944 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 15. `2025` | A1:M109 | 944 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |
| 16. `2026` | A1:M109 | 572 | 0 | 0 / 0 | Portrait month/metric sections; landscape column bands when 9-13 columns exceed 10pt widths; repeat year/account/unit headers |

### Logical blocks, merged headers and objects

Row ranges below are structural candidate blocks separated by blank populated rows. They are discovery aids, not a claim of completed semantic segmentation: Phase 6B must also divide contiguous tables at their actual section/account/month headings. No sheet is treated as covered by the thin browser cache.

- `STATS1`: populated row blocks 1-136; 0 named tables. Merged ranges: none.
- `STATS2`: populated row blocks 1-9; 0 named tables. Merged ranges: none.
- `SYMBOLS`: populated row blocks 1-123; 0 named tables. Merged ranges: A1:A2, AV1:AV2, U1:V1, D1:D2, R1:R2, F1:F2, H1:H2, J1:J2, P1:P2, AS1:AS2, B1:B2, L1:L2, N1:N2, T1:T2, G1:G2, I1:I2, S1:S2, AU1:AU2, C1:C2, E1:E2, K1:K2, AJ1:AM1, M1:M2, O1:O2, Q1:Q2, AN1:AQ1, AR1:AR2, AT1:AT2.
- `Trade Log`: populated row blocks 1-2, 4-1562; 0 named tables. Merged ranges: B1:B3, C1:C3, AK1:AK3, AM1:AM3, O1:O3, AH1:AH3, V2:V3, X2:X3, L1:L3, AX1:AX3, P1:P3, W2:W3, A1:A3, AI1:AI3, AU1:AU3, R1:R3, AG1:AG3, K1:K3, AD1:AD3, H1:H3, T1:X1, AA2:AA3, AC2:AC3, AS1:AS3, E1:E3, D1:D3, F1:F3, AJ1:AJ3, Z2:Z3, S1:S3, AB2:AB3, Y1:AC1, T2:T3, J1:J3, M1:M3, Q1:Q3, AQ1:AQ3, I1:I3, AW1:AW3, Y2:Y3, AT1:AT3, AL1:AL3, AR1:AR3, AV1:AV3, AP1:AP3, AN1:AN3, AE1:AE3, N1:N3, AF1:AF3, G1:G3, AO1:AO3, U2:U3.
- `P&L Calendar`: populated row blocks 1-13; 0 named tables. Merged ranges: none.
- ` SIM`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `YEARLY REPORT`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2018`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2019`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2020`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2021`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2022`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2023`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2024`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2025`: populated row blocks 1-109; 0 named tables. Merged ranges: none.
- `2026`: populated row blocks 1-109; 0 named tables. Merged ranges: none.

- `SYMBOLS` has 28 merged header ranges; Trade Log has 52. The management groups T:X (Move to Break-Even) and Y:AC (Move to Profit) supply the nested Time/Duration/Trigger Price/Distance From Entry %/Distance From Exit % labels. Other Trade Log fields are vertically merged through row 3. Use flattened canonical labels with visible band titles; preserve grouping meaning.
- STATS2 contains seven comments plus a VML drawing relationship (comment presentation). Print the comment bodies in a cell-addressed notes appendix in Phase 6B; VML must be resolved explicitly, never silently dropped.
- ` SIM` contains two charts. All other sheets have zero charts. No sheet has images. Chart rendering/data/style extraction is not implemented; Phase 6B must render existing workbook chart definitions with cached data, without regenerating financial statistics, or report an honest unsupported-object error.

## Exclusions and fidelity limitations

- Only exact `_Target R Metadata` is explicitly eligible for technical exclusion (internal target-R provenance). It is absent from this capture. Hidden business sheets remain included by default; the regression exercises both hidden business and veryHidden technical sheets.
- 511 formulas, all in ` SIM`, have stored cached values; missing-cache count is zero across every sheet in this capture. Cache presence does not prove freshness; export must never recalculate or claim freshness beyond stored workbook content. A future missing cache is printed as `[stored result unavailable]`, counted, and prevents a claim of faithful complete export until a user-provided workbook has stored results.
- Dates are decoded using the workbook epoch and retain ISO date/time information; explicit timezone text remains text. Excel serial dates have no inherent timezone, and no timezone is invented.
- Numeric XML values use Decimal and retain precision. Basic percent and quoted-unit/currency displays are available without a universal two-decimal rule; zero and False/No survive. Complex sections, accounting/negative/zero hiding, scaling, conditional formats, localized codes, elapsed durations, hyperlinks, rich/phonetic text and embedded objects are not yet a faithful full-export display model. Formula expressions are provenance, never numeric substitutes.
- The snapshot stores meaningful cell values, formats, merges, table refs, visibility and object counts. It is not a workbook style/layout clone: row/column sizing, conditional styles, object anchors/series, comment bodies, hyperlinks and complete logical block semantics still need extraction for the full export. The workbook remains authoritative and must not be rebuilt from transactions.
- The following encountered number-format strings require Phase 6B display validation; they are conservatively reported even where basic units/percent are already supported:

- `STATS1`: `#,##0.########\ "USDT"`; `#,##0.00\ "AUD"`; `0`; `0.00%`; `0.000"R"`.
- `STATS2`: `#,##0.########\ "USDT"`; `#,##0.00\ "AUD"`; `0.00%`.
- `SYMBOLS`: `0.00"R"`; `0.00%`; `0.000"R"`; `0.000%`; `0;\-0;;@`.
- `Trade Log`: `#,##0.00`; `#,##0.0000000000`; `#,##0.0000\ "USDT"`; `#,##0.000\ "AUD"`; `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `#,##0.00\ "USDT"`; `0.00`; `0.00%`; `0.000`; `0.000%`; `0.0000`; `0.0000%`; `0.00000`; `0.00000%`; `0.000000`; `@`.
- ` SIM`: `0%`; `0.0%`; `0.00`; `0.00%`; `0.00\ \R`; `0.0\ \R`; `_("$"* #,##0_);_("$"* \(#,##0\);_("$"* "-"??_);_(@_)`; `_(* #,##0.0000_);_(* \(#,##0.0000\);_(* "-"??_);_(@_)`.
- `YEARLY REPORT`: `#,##0.000\ "AUD"`; `#,##0.00\ "AUD"`; `0`; `0.00"R"`; `0.00%`; `0.000"R"`; `0.000%`; `0.0000"R"`; `0.0000%`.
- `2018`: `#,##0.000\ "AUD"`; `#,##0.00\ "AUD"`; `0`; `0.00"R"`; `0.00%`; `0.000"R"`; `0.000%`; `0.0000%`.
- `2019`: `#,##0.000\ "AUD"`; `#,##0.00\ "AUD"`; `0`; `0.00"R"`; `0.00%`; `0.000"R"`; `0.000%`; `0.0000"R"`.
- `2020`: `#,##0.0000\ "USDT"`; `#,##0.000\ "AUD"`; `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `#,##0.00\ "USDT"`; `0`; `0.00"R"`; `0.00%`; `0.000"R"`; `0.000%`; `0.0000%`.
- `2021`: `#,##0.0000\ "USDT"`; `#,##0.000\ "USDT"`; `#,##0.00\ "USDT"`; `0`; `0.00"R"`; `0.00%`; `0.000%`.
- `2022`: `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `0`; `0.00"R"`; `0.00%`; `0.000"R"`; `0.000%`.
- `2023`: `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `#,##0.00\ "USDT"`; `0`; `0.00"R"`; `0.00%`; `0.000%`.
- `2024`: `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `#,##0.00\ "USDT"`; `0`; `0.00"R"`; `0.00%`; `0.000%`.
- `2025`: `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `#,##0.00\ "USDT"`; `0`; `0.00"R"`; `0.00%`; `0.000%`.
- `2026`: `#,##0.000\ "USDT"`; `#,##0.00\ "AUD"`; `#,##0.00\ "USDT"`; `0`; `0.00"R"`; `0.00%`; `0.000%`; `0.0000%`.

## Trade Log field-to-band mapping

The capture has exactly 50 current fields, matching the producer TRADE_LOG_HEADERS. Stop/target recommendation columns exist in alternate producer schemas but are absent from this workbook; discover them if a later snapshot contains them. Do not silently truncate to the prototype schema.

Every band repeats Source row, Trade Number, Account and Symbol. Source row is a print reference, not an invented Trade Number. The second synthetic record deliberately has no Trade Number. Bands 1-5 keep the identical two-row range/order. Band 6 transposes fields into rows and the same two records into columns; every continuation repeats complete record identities in its headers.

1. **Identity / execution:** Open Time; Close Time; Side; Qty; Trade Duration (DD:HH:MM:SS).
2. **Prices / risk:** Entry Price; Exit Price; Stop Loss Price; Stop Loss Distance; Target Price; Target Distance.
3. **Results:** Commission; Net P/L; Profit %; R-Multiple; Balance After.
4. **Move to Break Even:** Move to Break Even Time; Move to Break Even Duration; Move to Break Even Trigger Price; Move to Break Even Distance From Entry %; Move to Break Even Distance From Exit %.
5. **Move to Profit:** Move to Profit Time; Move to Profit Duration; Move to Profit Trigger Price; Move to Profit Distance From Entry %; Move to Profit Distance From Exit %.
6. **Classifications / administration / notes:** Test; Pattern; EMA; VWAP; ATHS/ATLS; Order; Round Number; Spiked Out; Close Stopout; Near Perfect Entry; Near Win; Early Close; Setup; Timeframe; Breakeven; Cashflow Amount; Cashflow New Balance; Currency; Row Type; Row ID; Notes.

## Concrete preview and reusable interfaces

- `docs/previews/journal_a4_layout_preview.pdf`: final ten-page SYNTHETIC artifact, generated once after tests. Page 1 portrait summary; page 2 landscape grouped comparison; pages 3-7 bands 1-5; pages 8-9 band 6 and continued notes; page 10 portrait year/month report.
- Exact A4 210 x 297 mm / 297 x 210 mm, 36pt margins; title 16pt, table/header/body/continuation 10pt, footer 9pt. Embedded TrueType fonts, no TeX, no tight cropping, headless Matplotlib PdfPages. Page objects own their figures; cleanup clears those owned figures without touching global pyplot state.
- `capture_workbook(source, temporary_directory) -> Capture`: single read with hash and before/after checks; preserves captured evidence before rejecting concurrent changes; caller must never retry production capture within this phase.
- `read_print_snapshot(capture) -> list[SheetSnapshot]`: source-separated, bounded inert OOXML inventory and values; no workbook save. `display_value(PrintCell)` separates stored results from display text.
- `build_layout_preview(rows=None) -> list[PrintPage]`: reusable measured wrapping and pagination; page/band and source row/column addressed PlacedText records retain original strings, actual placed text, font sizes and renderer-measured bounds. Rendering rejects overflow rather than clipping or shrinking. Tall cells retain every text fragment and explicit continued identity.
- `write_preview_pdf(pages, output, inspection_directory=None)` writes exact physical A4 pages. Optional inspections generate only four PNGs from those same page objects; this is not independent PDF rasterization. `close_pages(pages)` releases owned page content.
- Legacy no-argument HTML behavior and paths are preserved and were not executed. New explicit CLI (no production reads):

```powershell
& 'C:\Users\User\miniconda3\python.exe' -u tools/build_trading_journal_report.py --print-proof docs/previews/journal_a4_layout_preview.pdf
```

### Verification and visual status

Exactly two unique test nodes and two pytest processes. First: both failed (notes exceeded page limit; test cache injection assumed non-self-closing XML). Corrected spacing/width without shrinking fonts and inserted fixture caches by XML parsing. Sole retry ran only those failed selectors: both passed (3.48 pytest seconds / 4.47 outer seconds). No additional test process or old-phase tests. Each host process used fresh TEMP basetemp and a 180-second outer bound.

- `tests/test_trading_journal_report.py::test_a4_trade_log_bands_cover_fields_and_preserve_readable_page_bounds`
- `tests/test_trading_journal_report.py::test_print_snapshot_preserves_cached_values_and_never_writes_source`

The first compares all placed field strings and values to an independent 50-field expectation, checks aligned repeated records, zero/No/signs/units/tiny prices, full whitespace-sensitive long notes, continuation, actual text overlap/bounds/fonts and saved-PDF MediaBoxes. The second uses a temporary ZIP/workbook fixture with stored zero/result/missing cache, hidden sheets, merged labels and XFD1000000 styled padding; asserts source bytes/hash/mtime unchanged and forbids workbook save.

Inspected exactly four 100dpi page-object PNGs once: pages 1, 3, 4 and 9. Portrait summary, related bands, tiny prices and continued long notes are readable; no clipping/overlap observed. The final note ending is visible. No independent saved-PDF rasterization was performed. User acceptance at Actual size remains pending.

## Phase 6B handoff (not implemented or authorized here)

1. After layout acceptance, complete workbook-driven logical blocks and pagination for every inventory row, faithful encountered units/formats, seven comments, both SIM charts and any new/hidden business sheets. Use actual source headers/merged groups instead of assuming this fixed preview fixture. Preserve every source value/manual edit and error honestly on unsupported fidelity.
2. Reuse tools/build_trading_journal_report.py. Build from one stable read-only capture off the event loop; return a complete PDF or an honest error. Never rewrite/recalculate the workbook or allocate folders/trade numbers.
3. Add one Journal-menu "Printable journal (PDF)" action using TRADING_JOURNAL_ACTIONS_TEMPLATE and existing /dashboard/trading-journal and /merged/trading-journal integration in render/master_service.py and render/static/trading_journal_actions.js. Those locations were read only this phase; no button/full export was added.
4. User reviews this PDF at 100% / Actual size: portrait summary, complete related bands, continued notes and landscape tables. No LTT run, Excel operation, trade, physical print or waiting period is required.
