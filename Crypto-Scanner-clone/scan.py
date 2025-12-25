"""Utilities for running a Bybit volume scan and exporting results."""

import os
import logging
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import webbrowser
import asyncio

from typing import Callable

import pandas as pd
from xlsxwriter.utility import xl_col_to_name

import core
from scan_utils import wait_for_file_close

def setup_logging() -> logging.Logger:
    """Configure and return the main scanner logger."""
    logger = logging.getLogger("volume_logger")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler("logs/scanlog.txt")
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    return logger


def clean_existing_excels(logger: logging.Logger | None = None) -> None:
    """Delete existing Excel files in the working directory."""
    if logger is None:
        logger = logging.getLogger("volume_logger")
    for file in os.listdir():
        if file.endswith(".xlsx"):
            wait_for_file_close(file, logger)
            try:
                os.remove(file)
            except OSError:
                logger.warning("Failed to delete %s", file)

def export_to_excel(
    df: pd.DataFrame,
    symbol_order: list,
    logger: logging.Logger,
    filename: str = "Crypto_Volume.xlsx",
    header: str = "% Distance Below or Above 20 Bar Moving Average Volume Indicator",
    *,
    apply_conditional_formatting: bool = True,
    writer: pd.ExcelWriter | None = None,
    sheet_name: str = "Sheet1",
    format_as_funding: bool = False,
) -> None:
    # pylint: disable=too-many-locals,too-many-arguments,too-many-branches,too-many-statements
    """Write ``df`` to ``filename`` with formatting."""
    if "Symbol" in df.columns and not df.empty:
        df["__sort_order"] = df["Symbol"].map({s: i for i, s in enumerate(symbol_order)})
        df = df.sort_values("__sort_order").drop(columns=["__sort_order"])
    elif "Symbol" not in df.columns:
        logger.warning("'Symbol' column missing. Skipping sorting for sheet '%s'", sheet_name)

    if "Funding Rate" in df.columns and "24h USD Volume" in df.columns:
        cols = df.columns.tolist()
        fr_idx = cols.index("Funding Rate")
        vol_idx = cols.index("24h USD Volume")
        if fr_idx < vol_idx:
            cols[fr_idx], cols[vol_idx] = cols[vol_idx], cols[fr_idx]
            df = df[cols]

    manage_writer = writer is None
    if manage_writer:
        logger.info("Exporting data to Excel: %s", filename)
        wait_for_file_close(filename, logger)
        writer = pd.ExcelWriter(filename, engine="xlsxwriter")
    else:
        logger.info("Exporting sheet: %s", sheet_name)

    df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1)
    worksheet = writer.sheets[sheet_name]
    header_format = writer.book.add_format({"bold": True})
    worksheet.write("A1", header, header_format)
    worksheet.freeze_panes(2, 0)

    red_format = writer.book.add_format({
        "bg_color": "#FFC7CE",
        "font_color": "#9C0006",
    })
    green_format = writer.book.add_format({
        "bg_color": "#C6EFCE",
        "font_color": "#006100",
    })
    currency_format = writer.book.add_format({"num_format": "$#,##0.00"})
    percent_format = writer.book.add_format({"num_format": '0.00"%"'})
    funding_format = writer.book.add_format({"num_format": '0.0000000%'})

    if "24h USD Volume" in df.columns:
        col_idx = df.columns.get_loc("24h USD Volume")
        worksheet.set_column(col_idx, col_idx, None, currency_format)

    percent_columns: list[str] = []
    if not format_as_funding:
        percent_columns = [
            name
            for name in [
                "5M",
                "15M",
                "30M",
                "1H",
                "4H",
                "5M Percentile",
                "15M Percentile",
                "30M Percentile",
                "1H Percentile",
                "4H Percentile",
                "1D",
                "1W",
                "1M",
                "Open Interest Change",
            ]
            if name in df.columns
        ]
        for name in percent_columns:
            col = df.columns.get_loc(name)
            worksheet.set_column(col, col, None, percent_format)

    funding_cols: list[str]
    numeric_cols = df.select_dtypes("number").columns
    if format_as_funding:
        funding_cols = [c for c in numeric_cols if c != "24h USD Volume"]
    else:
        funding_cols = [c for c in df.columns if "Funding Rate" in c]
    for name in funding_cols:
        idx = df.columns.get_loc(name)
        worksheet.set_column(idx, idx, None, funding_format)

    if apply_conditional_formatting:
        if format_as_funding:
            columns_to_format = [
                c for c in numeric_cols if c != "24h USD Volume"
            ]
        else:
            columns_to_format = [
                name
                for name in [
                    "5M",
                    "15M",
                    "30M",
                    "1H",
                    "4H",
                    "1D",
                    "1W",
                    "1M",
                    "Open Interest Change",
                ]
                if name in df.columns
            ]
            columns_to_format.extend([c for c in df.columns if "Funding Rate" in c])
        for name in columns_to_format:
            col = df.columns.get_loc(name)
            col_letter = xl_col_to_name(col)
            cell_range = f"{col_letter}3:{col_letter}1048576"
            worksheet.conditional_format(cell_range, {
                "type": "cell",
                "criteria": ">",
                "value": 0,
                "format": green_format
            })
            worksheet.conditional_format(cell_range, {
                "type": "cell",
                "criteria": "<",
                "value": 0,
                "format": red_format
            })
    if manage_writer:
        writer.close()


_OPENED_PATHS: set[str] = set()


def open_in_edge(file_path: str, logger: logging.Logger) -> None:
    """Open ``file_path`` in Microsoft Edge only once per session."""
    if file_path in _OPENED_PATHS:
        logger.info("Edge already open for %s", file_path)
        return

    _OPENED_PATHS.add(file_path)

    if platform.system() == "Windows":
        try:
            subprocess.Popen(  # pylint: disable=consider-using-with
                ["cmd", "/c", "start", "msedge", file_path]
            )
        except OSError as exc:  # pragma: no cover - platform specific
            logger.warning("Failed to open Edge: %s", exc)
    else:  # pragma: no cover - platform specific
        webbrowser.open(file_path)


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
def export_to_html(
    df: pd.DataFrame,
    symbol_order: list,
    logger: logging.Logger,
    filename: str,
    header: str,
    *,
    include_sort_buttons: bool = False,
    refresh_seconds: int = 60,
    format_as_funding: bool = False,
) -> None:
    """Write ``df`` to ``filename`` with a dark theme and auto-refresh."""
    if "Symbol" in df.columns and not df.empty:
        df["__sort_order"] = df["Symbol"].map({s: i for i, s in enumerate(symbol_order)})
        df = df.sort_values("__sort_order").drop(columns=["__sort_order"])
    elif "Symbol" not in df.columns:
        logger.warning("'Symbol' column missing. Skipping sorting for file '%s'", filename)

    os.makedirs("html", exist_ok=True)
    path = os.path.join("html", filename)
    logger.info("Exporting data to HTML: %s", path)

    def style_cell(val: float) -> str:
        try:
            num = float(val)
        except (TypeError, ValueError):
            return ""
        return (
            "background-color:#C6EFCE;color:#006100"
            if num >= 0
            else "background-color:#FFC7CE;color:#9C0006"
        )

    numeric_cols = df.select_dtypes("number").columns
    highlight_cols = [
        c
        for c in numeric_cols
        if "Percentile" not in c and c != "24h USD Volume"
    ]
    styled = df.style.map(style_cell, subset=highlight_cols)

    format_dict: dict[str, Callable] = {}
    if "24h USD Volume" in numeric_cols:
        format_dict["24h USD Volume"] = lambda x: f"${x:,.0f}"
    for col in numeric_cols:
        if format_as_funding:
            format_dict[col] = lambda x: f"{x:.7f}%"
        elif "Percentile" in col:
            format_dict[col] = lambda x: f"{x * 100:.2f}%"
        elif col != "24h USD Volume":
            format_dict[col] = lambda x: f"{x:.2f}%"

    styled = styled.format(format_dict)
    html_table = styled.to_html(index=False, table_uuid="data-table")
    html_table = html_table.replace('id="T_data-table"', 'id="data-table"')

    nav = ""
    if include_sort_buttons:
        timeframes = ["5M", "15M", "30M", "1H", "4H", "1D", "1W", "1M"]
        buttons = [
            f"<button onclick=\"sortBy('{tf}')\">{tf}</button>"
            for tf in timeframes
            if tf in df.columns
        ]
        if "24h USD Volume" in df.columns:
            buttons.append(
                "<button onclick=\"sortBy('24h USD Volume')\">24h Vol</button>"
            )
        sort_buttons = "".join(buttons)
        nav = (
            "<div style='display:flex;justify-content:flex-start;"
            "align-items:center;gap:4px;margin-bottom:8px'>"
            f"{sort_buttons}</div>"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "<html><head><meta charset='utf-8'>"
            f"<meta http-equiv='refresh' content='{refresh_seconds}'>"
            "<style>"
            "body{background:#121212;color:#fff;font-family:Arial,Helvetica,sans-serif;}"
            "table{background:#1e1e1e;color:#fff;border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #333;padding:4px;text-align:right;}"
            "th{background:#333;}"
            "td:first-child,th:first-child{text-align:left;}"
            "button{background:#333;color:#fff;border:1px solid #555;padding:4px 8px;"
            "margin-right:4px;cursor:pointer;}"
            "button:hover{background:#444;}"
            "</style>"
            f"<title>{header}</title></head><body>"
        )
        if nav:
            f.write(nav)
        f.write(f"<h1 style='margin-top:8px'>{header}</h1>")
        f.write(html_table)
        if include_sort_buttons:
            f.write(
                "<script>"
                "const sortDirections = {};"
                "function sortBy(col){"
                "  const table=document.getElementById('data-table');"
                "  const headers=Array.from(table.rows[0].cells).map(c=>c.textContent.trim());"
                "  const idx=headers.indexOf(col);"
                "  if(idx===-1)return;"
                "  const dir=sortDirections[col]||'desc';"
                "  const rows=Array.from(table.tBodies[0].rows);"
                "  rows.sort((a,b)=>{"
                "    const aVal=parseFloat(a.cells[idx].textContent.replace(/[%,$]/g,'')"
                ".replace(/,/g,''))||0;"
                "    const bVal=parseFloat(b.cells[idx].textContent.replace(/[%,$]/g,'')"
                ".replace(/,/g,''))||0;"
                "    return dir==='desc'?bVal-aVal:aVal-bVal;"
                "  });"
                "  rows.forEach(r=>table.tBodies[0].appendChild(r));"
                "  sortDirections[col]=dir==='desc'?'asc':'desc';"
                "}"
                "</script>"
            )
        f.write("</body></html>")
    open_in_edge(os.path.abspath(path), logger)

def submit_symbol_futures(symbols: list[str], executor: ThreadPoolExecutor,
                           logger: logging.Logger, func) -> dict:
    """Return a mapping of futures to their corresponding symbol."""
    return {
        executor.submit(func, symbol, logger): symbol
        for symbol in symbols
    }


def scan_and_collect_results(symbols: list[str],
                             logger: logging.Logger,
                             func=core.process_symbol) -> tuple[list, list]:
    """Process all symbols concurrently and collect successes and failures."""
    rows: list[dict] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = submit_symbol_futures(symbols, executor, logger, func)
        for future in as_completed(futures):
            symbol = futures[future]
            result = future.result()
            if result:
                rows.append(result)
            else:
                failed.append(symbol)
    return rows, failed


def run_funding_rate_scan(
    all_symbols: list[tuple],
    logger: logging.Logger,
) -> pd.DataFrame:
    """Collect the latest funding rate for each symbol."""

    logger.info("Scanning funding rates...")
    rows, _ = scan_and_collect_results(
        [s for s, _ in all_symbols],
        logger,
        core.process_symbol_funding,
    )
    df = pd.DataFrame(rows)
    export_to_html(
        df,
        [s for s, _ in all_symbols],
        logger,
        filename="funding_rates.html",
        header="Latest Funding Rates",
        refresh_seconds=60,
        include_sort_buttons=True,
        format_as_funding=True,
    )
    return df


def run_open_interest_scan(
    all_symbols: list[tuple],
    logger: logging.Logger,
) -> pd.DataFrame:
    """Collect open interest change metrics for each symbol."""

    logger.info("Scanning open interest changes...")
    rows, _ = scan_and_collect_results(
        [s for s, _ in all_symbols],
        logger,
        core.process_symbol_open_interest,
    )
    df = pd.DataFrame(rows)
    export_to_html(
        df,
        [s for s, _ in all_symbols],
        logger,
        filename="open_interest.html",
        header="% Change in Open Interest",
        refresh_seconds=60,
        include_sort_buttons=True,
    )
    return df


def run_volume_scan(
    all_symbols: list[tuple],
    logger: logging.Logger,
    klines_cache: dict | None = None,
) -> pd.DataFrame:
    """Collect volume metrics for each symbol."""

    logger.info("Scanning volume metrics...")

    rows, failed = scan_and_collect_results(
        [s for s, _ in all_symbols],
        logger,
        lambda s, log: core.process_symbol(s, log, klines_cache),
    )

    volume_map = dict(all_symbols)
    for row in rows:
        row["24h USD Volume"] = volume_map.get(row["Symbol"], 0)
    df = pd.DataFrame(rows)
    export_to_html(
        df,
        [s for s, _ in all_symbols],
        logger,
        filename="volume.html",
        header="% Distance Below or Above 20 Bar Moving Average Volume Indicator",
        refresh_seconds=900,
        include_sort_buttons=True,
    )

    if failed:
        logger.warning("%d symbols failed: %s", len(failed), ", ".join(failed))

    return df


def run_scan(
    all_symbols: list[tuple],
    logger: logging.Logger,
    klines_cache: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    """Fetch funding, open interest and volume metrics in sequence."""

    funding_df = run_funding_rate_scan(all_symbols, logger)
    oi_df = run_open_interest_scan(all_symbols, logger)
    volume_df = run_volume_scan(all_symbols, logger, klines_cache)

    return (
        volume_df,

        funding_df,
        oi_df,
        [s for s, _ in all_symbols],
    )

def run_correlation_matrix_scan(
    all_symbols: list[tuple],
    logger: logging.Logger,
    klines_cache: dict | None = None,
) -> pd.DataFrame:
    """Return correlation of each symbol versus BTCUSDT for all timeframes."""
    logger.info("Starting correlation scan against BTCUSDT...")

    if not all_symbols:
        logger.warning("No symbols retrieved. Skipping correlation export.")
        return pd.DataFrame()

    btc_klines = core.fetch_recent_klines("BTCUSDT", cache=klines_cache)
    if not btc_klines:
        logger.warning("BTCUSDT data unavailable. Skipping correlation export.")
        return pd.DataFrame()

    rows, failed = scan_and_collect_results(
        [s for s, _ in all_symbols],
        logger,
        lambda s, log: core.process_symbol_correlation(s, btc_klines, log, klines_cache),
    )

    if failed:
        logger.warning("%d symbols failed: %s", len(failed), ", ".join(failed))

    df = pd.DataFrame(rows)
    logger.info("Correlation data collected for %d symbols", len(df))

    return df






def export_all_data(
    volume_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    symbol_order: list[str],
    logger: logging.Logger,
    filename: str = "Scan.xlsx",
) -> None:
    """Write all metric DataFrames to an Excel file."""

    wait_for_file_close(filename, logger)
    with pd.ExcelWriter(filename, engine="xlsxwriter") as writer:
        export_to_excel(
            volume_df,
            symbol_order,
            logger,
            header="% Distance Below or Above 20 Bar Moving Average Volume Indicator",
            writer=writer,
            sheet_name="Volume",
        )
        export_to_excel(
            funding_df,
            symbol_order,
            logger,
            header="Latest Funding Rates",
            writer=writer,
            sheet_name="Funding Rates",
            format_as_funding=True,
        )
        export_to_excel(
            oi_df,
            symbol_order,
            logger,
            header="% Change in Open Interest",
            writer=writer,
            sheet_name="Open Interest",
        )
    logger.info("Export complete: %s", filename)


def export_correlation_matrices(
    df: pd.DataFrame,
    logger: logging.Logger,
    filename: str = "Correlation.xlsx",
) -> None:
    """Write correlation results to ``filename`` as a single sheet."""

    if df.empty:
        logger.warning("No correlation matrix data to export.")
        return

    export_to_excel(
        df,
        df.get("Symbol", []).tolist() if "Symbol" in df.columns else [],
        logger,
        filename=filename,
        header="% Correlation to BTCUSDT",
        sheet_name="Correlation",
    )
    export_correlation_matrix_html(df, logger)


def export_correlation_matrix_html(
    df: pd.DataFrame,
    logger: logging.Logger,
    filename: str = "correlation.html",
    *,
    refresh_seconds: int = 180,
) -> None:
    """Write correlation results to a single HTML file."""

    if df.empty:
        logger.warning("No correlation matrix data to export.")
        return

    export_to_html(
        df,
        df.get("Symbol", []).tolist() if "Symbol" in df.columns else [],
        logger,
        filename=filename,
        header="% Correlation to BTCUSDT",
        include_sort_buttons=True,
        refresh_seconds=refresh_seconds,
    )
def export_all_data_html(
    volume_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    symbol_order: list[str],
    logger: logging.Logger,
) -> None:
    """Write all metric DataFrames to individual HTML files."""  # pylint: disable=too-many-arguments,too-many-positional-arguments
    export_to_html(
        volume_df,
        symbol_order,
        logger,
        filename="volume.html",
        header="% Distance Below or Above 20 Bar Moving Average Volume Indicator",
        refresh_seconds=900,
        include_sort_buttons=True,
    )
    export_to_html(
        funding_df,
        symbol_order,
        logger,
        filename="funding_rates.html",
        header="Latest Funding Rates",
        refresh_seconds=60,
        include_sort_buttons=True,
        format_as_funding=True,
    )
    export_to_html(
        oi_df,
        symbol_order,
        logger,
        filename="open_interest.html",
        header="% Change in Open Interest",
        refresh_seconds=60,
        include_sort_buttons=True,
    )


def main() -> None:
    """Entry point for running the scanner from the command line."""
    logger = setup_logging()
    try:
        logger.info("Fetching USDT perpetual futures from Bybit...")
        all_symbols = core.get_tradeable_symbols_sorted_by_volume()
        logger.info("Total pairs found: %d", len(all_symbols))

        if not all_symbols:
            logger.warning("No symbols retrieved. Skipping export.")
            return

        clean_existing_excels(logger)

        symbols_only = [s for s, _ in all_symbols]
        logger.info("Fetching klines asynchronously for %d symbols", len(symbols_only))
        klines_cache = asyncio.run(core.fetch_all_recent_klines_async(symbols_only))

        volume_df, funding_df, oi_df, symbol_order = run_scan(all_symbols, logger, klines_cache)
        corr_df = run_correlation_matrix_scan(all_symbols, logger, klines_cache)

        export_correlation_matrices(corr_df, logger)

        export_all_data(
            volume_df,
            funding_df,
            oi_df,
            symbol_order,
            logger,
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.exception("Script failed: %s", exc)


if __name__ == "__main__":
    main()
