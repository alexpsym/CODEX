"""Run Bybit scans at a fixed interval and save each result."""

import time
from datetime import datetime
import pandas as pd

import scan
import core


def run_periodic_scans() -> None:  # pylint: disable=too-many-branches,too-many-statements
    """Refresh each metric at its own interval."""
    logger = scan.setup_logging()
    logger.info("Continuous scan started. Press Ctrl+C to stop.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Scan_{timestamp}.xlsx"

    intervals = {
        "funding": 60,
        "oi": 5 * 60,
        "corr": 15 * 60,
        "volume": 20 * 60,
    }
    next_run = {key: 0 for key in intervals}

    volume_df = pd.DataFrame()
    funding_df = pd.DataFrame()
    oi_df = pd.DataFrame()
    corr_df: pd.DataFrame = pd.DataFrame()
    symbol_order: list[str] = []

    while True:
        now = time.time()
        try:
            if (
                now >= next_run["funding"]
                or now >= next_run["oi"]
                or now >= next_run["corr"]
                or now >= next_run["volume"]
            ):
                logger.info("Fetching USDT perpetual futures from Bybit...")
                all_symbols = core.get_tradeable_symbols_sorted_by_volume()
                logger.info("Total pairs found: %d", len(all_symbols))

                if not all_symbols:
                    logger.warning("No symbols retrieved. Skipping export.")
                else:
                    if now >= next_run["funding"]:
                        funding_df = scan.run_funding_rate_scan(all_symbols, logger)
                        symbol_order = [s for s, _ in all_symbols]
                        next_run["funding"] = now + intervals["funding"]

                    if now >= next_run["oi"]:
                        oi_df = scan.run_open_interest_scan(all_symbols, logger)
                        next_run["oi"] = now + intervals["oi"]

                    if now >= next_run["corr"]:
                        matrix_map = scan.run_correlation_matrix_scan(all_symbols, logger)
                        scan.export_correlation_matrices(matrix_map, logger)
                        next_run["corr"] = now + intervals["corr"]

                    if now >= next_run["volume"]:
                        volume_df = scan.run_volume_scan(all_symbols, logger)
                        next_run["volume"] = now + intervals["volume"]
                        scan.export_all_data(
                            volume_df,
                            funding_df,
                            oi_df,
                            symbol_order,
                            logger,
                            filename=filename,
                        )
                    if now >= next_run["volume"]:
                        next_run["volume"] = now + intervals["volume"]
                    if now >= next_run["funding"]:
                        next_run["funding"] = now + intervals["funding"]
                    if now >= next_run["oi"]:
                        next_run["oi"] = now + intervals["oi"]

            if now >= next_run["corr"]:
                logger.info("Updating correlation matrix")
                all_symbols = core.get_tradeable_symbols_sorted_by_volume()
                corr_df = scan.run_correlation_matrix_scan(all_symbols, logger)
                scan.export_correlation_matrices(
                    corr_df, logger
                )
                next_run["corr"] = now + intervals["corr"]


        except (RuntimeError, ValueError, TypeError) as exc:
            logger.exception("Script failed: %s", exc)

        time.sleep(60)


if __name__ == "__main__":
    run_periodic_scans()
