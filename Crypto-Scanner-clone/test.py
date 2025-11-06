"""
Unit test suite for Bybit volume anomaly scanner.
Tests include symbol sorting, kline deduplication, volume spike/dip detection,
and Excel export behavior. Supports pytest + pylint 10/10 compliance.
"""

import logging
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone, timedelta
import pandas as pd
import core
import scan
from volume_math import calculate_volume_change
import correlation_math
import percentile_math

def test_get_tradeable_symbols_sorted_by_volume():
    """Test symbol sorting by 24h volume descending order."""
    mock_response = {
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "turnover24h": "500000000"},
                {"symbol": "ETHUSDT", "turnover24h": "300000000"},
                {"symbol": "DOGEUSDT", "turnover24h": "100000000"}
            ]
        }
    }
    with patch("core.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_response
        result = core.get_tradeable_symbols_sorted_by_volume()
        assert result == [
            ("BTCUSDT", 500000000.0),
            ("ETHUSDT", 300000000.0),
            ("DOGEUSDT", 100000000.0)
        ]

def test_fetch_recent_klines_exact_count():
    """Test fetch returns the exact number of klines requested."""
    mock_klines = [
        [str(1717382400000 + i * 60000), "", "", "", "", "1"]
        for i in range(10080)
    ]
    mock_response = {"result": {"list": mock_klines}}
    with patch("core.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_response
        result = core.fetch_recent_klines("BTCUSDT", total=10080)
        assert isinstance(result, list)
        assert len(result) == 10080



def test_fetch_recent_klines_insufficient():
    """Test deduplication logic by simulating repeated stale API responses."""
    def build_chunk():
        return [[str(1717382400000), "", "", "", "", "1"] for _ in range(100)]

    mock_response = {"result": {"list": build_chunk()}}
    responses = [mock_response, mock_response, mock_response]

    def side_effect(*_, **__):
        if responses:
            return MagicMock(status_code=200, json=lambda: responses.pop(0))
        return MagicMock(status_code=200, json=lambda: {"result": {"list": []}})

    with patch("core.requests.get", side_effect=side_effect):
        result = core.fetch_recent_klines("BTCUSDT", total=10080)
        assert result == []

def test_clean_existing_excels(tmp_path):
    """Test Excel file cleanup removes files as expected."""
    dummy_file = tmp_path / "file.xlsx"
    dummy_file.write_text("data")

    with patch("scan.os.listdir", return_value=["file.xlsx"]), \
         patch("scan.os.remove") as mock_remove, \
         patch("scan.wait_for_file_close") as mock_wait:
        scan.clean_existing_excels()
        mock_wait.assert_called_once()
        mock_remove.assert_called_once_with("file.xlsx")

def test_setup_logging():
    """Test logger is created with correct config."""
    logger = scan.setup_logging()
    assert isinstance(logger, logging.Logger)
    assert logger.name == "volume_logger"
    assert logger.level == logging.INFO

def test_process_symbol_with_mocked_logger():
    """Ensure process_symbol runs with valid klines and mocked logger."""
    mock_klines = [[str(i), "", "", "", "", "2"] for i in range(10080)]
    with patch("core.fetch_recent_klines", return_value=mock_klines):
        result = core.process_symbol("BTCUSDT", MagicMock())
        assert isinstance(result, dict)
        assert result["Symbol"] == "BTCUSDT"
        expected = {
            "Symbol",
            "5M",
            "5M Percentile",
            "15M",
            "15M Percentile",
            "30M",
            "30M Percentile",
            "1H",
            "1H Percentile",
            "4H",
            "4H Percentile",
        }
        assert set(result.keys()) == expected


def test_calculate_price_correlation_perfect():
    """Correlation should be 1.0 for identical series."""
    klines = [[str(i), "", "", "", str(i), "1"] for i in range(10)]
    result = correlation_math.calculate_price_correlation(klines, klines, 5)
    assert round(result, 6) == 1.0


def test_process_symbol_correlation_with_mocked_logger():
    """Ensure correlation processing returns expected keys."""
    mock_klines = [[str(i), "", "", "", str(i), "2"] for i in range(10080)]
    with patch("core.fetch_recent_klines", return_value=mock_klines), \
         patch("core.get_open_interest_change", return_value=5.0):
        result = core.process_symbol_correlation("ETHUSDT", mock_klines, MagicMock())
        assert isinstance(result, dict)
        assert result["Symbol"] == "ETHUSDT"
        assert "5M" in result
        assert set(result) == {"Symbol", "5M", "15M", "30M", "1H", "4H"}


def test_process_symbol_open_interest_with_mocked_logger():
    """Ensure open interest metrics include all timeframes."""
    data = [{"openInterest": str(100 + i), "timestamp": str(i)} for i in range(50)]
    with patch("core.get_open_interest_history", return_value=data):
        result = core.process_symbol_open_interest("XRPUSDT", MagicMock())
        expected = {
            "Symbol",
            "5M",
            "5M Percentile",
            "15M",
            "15M Percentile",
            "30M",
            "30M Percentile",
            "1H",
            "1H Percentile",
            "4H",
            "4H Percentile",
            "1D",
            "1D Percentile",
            "1W",
            "1W Percentile",
            "1M",
            "1M Percentile",
        }
        assert set(result.keys()) == expected


def test_get_open_interest_changes_calls_expected_params():
    """Verify week and month calculations use daily data."""
    with patch("core.get_open_interest_change", return_value=5.0) as mock_oi:
        result = core.get_open_interest_changes("BTCUSDT")
        assert set(result) == {"5M", "15M", "30M", "1H", "4H", "1D", "1W", "1M"}
        assert mock_oi.call_count == 8
        mock_oi.assert_any_call("BTCUSDT", "1d", 7)
        mock_oi.assert_any_call("BTCUSDT", "1d", 30)


def test_process_symbol_funding_with_mocked_logger():
    """Ensure funding rate metric returns correct mapping."""
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    def ts(minutes):
        return int((now - timedelta(minutes=minutes)).timestamp() * 1000)

    history = [
        {"fundingRate": "0.0008", "fundingRateTimestamp": str(ts(5))},
        {"fundingRate": "0.0007", "fundingRateTimestamp": str(ts(15))},
        {"fundingRate": "0.0006", "fundingRateTimestamp": str(ts(30))},
        {"fundingRate": "0.0005", "fundingRateTimestamp": str(ts(60))},
        {"fundingRate": "0.0004", "fundingRateTimestamp": str(ts(240))},
        {"fundingRate": "0.0003", "fundingRateTimestamp": str(ts(1440))},
        {"fundingRate": "0.0002", "fundingRateTimestamp": str(ts(10080))},
        {"fundingRate": "0.0001", "fundingRateTimestamp": str(ts(43200))},
    ]
    with patch("core.get_funding_rate", return_value=(0.001, now_ms)), \
         patch("core.get_funding_rate_history", return_value=history):
        result = core.process_symbol_funding("XRPUSDT", MagicMock())
    assert result == {
        "Symbol": "XRPUSDT",
        "Funding Rate": 0.001,
        "5M": 0.0008,
        "15M": 0.0007,
        "30M": 0.0006,
        "1H": 0.0005,
        "4H": 0.0004,
        "1D": 0.0003,
        "1W": 0.0002,
        "1M": 0.0001,
    }

def test_get_funding_rate_success_timestamp():
    """Ensure timestamp reflects when the rate was fetched."""
    ts = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000)
    mock_data = {
        "result": {"list": [{"fundingRate": "0.0001"}]},
        "time": str(ts)
    }
    with patch("core.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_data
        rate, ts_returned = core.get_funding_rate("BTCUSDT")
        assert rate == 0.0001
        assert ts_returned == ts

def test_get_open_interest_change():
    """Calculate correct open interest percentage change."""
    mock_data = {
        "result": {
            "list": [
                {"openInterest": "100"},
                {"openInterest": "110"}
            ]
        }
    }
    with patch("core.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_data
        change = core.get_open_interest_change("BTCUSDT")
        assert round(change, 4) == 10.0


def test_get_open_interest_change_sorts_by_timestamp():
    """Ensure change calculation sorts rows by timestamp."""
    mock_data = {
        "result": {
            "list": [
                {"openInterest": "110", "timestamp": "2"},
                {"openInterest": "100", "timestamp": "1"},
            ]
        }
    }
    with patch("core.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_data
        change = core.get_open_interest_change("BTCUSDT")
        assert round(change, 4) == 10.0

# -------------------------------
# Tests for volume_math.calculate_volume_change
# -------------------------------

def test_calculate_volume_change_5m():
    """Detects a volume spike in 5m block size (5 klines)."""
    baseline = [[str(i), "", "", "", "", "1"] for i in range(5)]
    spike = [[str(i + 10000), "", "", "", "", "10"] for i in range(5)]
    klines = (baseline * 20) + spike
    result = calculate_volume_change(klines, 5)
    assert result > 800

def test_calculate_volume_change_30m():
    """Detects a volume spike in 30m block size (30 klines)."""
    baseline = [[str(i), "", "", "", "", "3"] for i in range(30)]
    spike = [[str(i + 50000), "", "", "", "", "15"] for i in range(30)]
    klines = (baseline * 20) + spike
    result = calculate_volume_change(klines, 30)
    assert result > 300

def test_calculate_volume_change_exactly_21_blocks():
    """Returns 0.0 when all blocks have identical volume."""
    one_block = [[str(i), "", "", "", "", "5"] for i in range(15)]
    klines = one_block * 21
    result = calculate_volume_change(klines, 15)
    assert isinstance(result, float)
    assert result == 0.0

def test_calculate_volume_change_latest_spike():
    """Detects a large positive volume spike in the latest block."""
    baseline = [[str(i), "", "", "", "", "2"] for i in range(15)]
    spike = [[str(i + 3000), "", "", "", "", "20"] for i in range(15)]
    klines = (baseline * 20) + spike
    result = calculate_volume_change(klines, 15)
    assert result > 800

def test_calculate_volume_change_latest_drop():
    """Detects a large negative volume drop in the latest block."""
    baseline = [[str(i), "", "", "", "", "10"] for i in range(15)]
    drop = [[str(i + 3000), "", "", "", "", "1"] for i in range(15)]
    klines = (baseline * 20) + drop
    result = calculate_volume_change(klines, 15)
    assert result < -85

def test_calculate_volume_change_insufficient_blocks():
    """Returns 0.0 if fewer than 21 blocks are available."""
    block = [[str(i), "", "", "", "", "3"] for i in range(15)]
    klines = block * 19
    result = calculate_volume_change(klines, 15)
    assert result == 0.0

def test_calculate_volume_change_invalid_data():
    """Returns 0.0 when volume data is malformed."""
    bad_klines = [["abc", "", "", "", "", "X"] for _ in range(315)]
    result = calculate_volume_change(bad_klines, 15)
    assert result == 0.0

def test_calculate_volume_change_4h():
    """Detects a volume shift in 4h block size (240 klines)."""
    baseline = [[str(i), "", "", "", "", "5"] for i in range(240)]
    spike = [[str(i + 100000), "", "", "", "", "30"] for i in range(240)]
    klines = (baseline * 20) + spike
    result = calculate_volume_change(klines, 240)
    assert result > 400








def test_export_to_excel_swaps_column_order():
    """24h volume column should precede funding rate in exported sheet."""
    df = pd.DataFrame([
        {
            "Symbol": "BTCUSDT",
            "Funding Rate": 0.001,
            "24h USD Volume": 1000,
            "5M": 1,
        }
    ])
    logger = MagicMock()
    captured = {}
    with patch("scan.pd.ExcelWriter") as mock_writer, \
         patch("scan.wait_for_file_close"), \
         patch("pandas.DataFrame.to_excel", autospec=True) as mock_to_excel:
        writer = MagicMock()
        writer.book.add_format.return_value = MagicMock()
        writer.sheets = {"Sheet1": MagicMock()}
        mock_writer.return_value.__enter__.return_value = writer

        def capture(self, *args, **kwargs):  # pylint: disable=unused-argument
            captured["cols"] = list(self.columns)

        mock_to_excel.side_effect = capture

        scan.export_to_excel(df, ["BTCUSDT"], logger)
        cols = captured.get("cols")
        assert cols.index("24h USD Volume") < cols.index("Funding Rate")





def test_run_correlation_scan():
    """Correlation scan returns dataframe without exporting HTML."""
    mock_klines = [[str(i), "", "", "", str(i), "1"] for i in range(10080)]
    all_syms = [("BTCUSDT", 1), ("ETHUSDT", 2)]
    logger = MagicMock()
    with patch("core.fetch_recent_klines", return_value=mock_klines):
        df = scan.run_correlation_matrix_scan(all_syms, logger)
        assert set(df["Symbol"]) == {"BTCUSDT", "ETHUSDT"}


def test_export_correlation_matrix_html():
    """HTML export delegates to export_to_html."""
    df = pd.DataFrame({"Symbol": ["BTCUSDT"], "5M": [0]})
    logger = MagicMock()
    with patch("scan.export_to_html") as mock_export:
        scan.export_correlation_matrix_html(df, logger, refresh_seconds=60)
        mock_export.assert_called_once()


def test_export_correlation_matrices_opens_html():
    """Excel export also triggers HTML export."""
    df = pd.DataFrame({"Symbol": ["BTCUSDT"], "5M": [0]})
    logger = MagicMock()
    with patch("scan.export_to_excel") as mock_excel, patch(
        "scan.export_correlation_matrix_html"
    ) as mock_html:
        scan.export_correlation_matrices(df, logger)
        mock_excel.assert_called_once()
        mock_html.assert_called_once_with(df, logger)


def test_percentile_rank_basic():
    """Percentile rank computes relative position of a value."""
    values = [1.0, 2.0, 3.0, 4.0]
    pct = percentile_math.percentile_rank(values, 3.0)
    assert round(pct, 2) == 0.7


def test_export_to_excel_skips_conditional_formatting():
    """No conditional formatting applied when flag is False."""
    df = pd.DataFrame([
        {"Symbol": "BTCUSDT", "5M": 1.0}
    ])
    logger = MagicMock()
    with patch("scan.pd.ExcelWriter") as mock_writer, \
         patch("scan.wait_for_file_close"), \
         patch("pandas.DataFrame.to_excel", autospec=True):
        writer = MagicMock()
        writer.book.add_format.return_value = MagicMock()
        worksheet = MagicMock()
        writer.sheets = {"Sheet1": worksheet}
        mock_writer.return_value.__enter__.return_value = writer

        scan.export_to_excel(df, ["BTCUSDT"], logger,
                             filename="x.xlsx", header="hdr",
                             apply_conditional_formatting=False)
        worksheet.conditional_format.assert_not_called()


def test_export_to_excel_does_not_merge_cells():
    """Header is written directly and no cells are merged."""
    df = pd.DataFrame([
        {"Symbol": "BTCUSDT", "5M": 1.0, "15M": 0.5}
    ])
    logger = MagicMock()
    with patch("scan.pd.ExcelWriter") as mock_writer, \
         patch("scan.wait_for_file_close"), \
         patch("pandas.DataFrame.to_excel", autospec=True):
        writer = MagicMock()
        fmt = MagicMock()
        writer.book.add_format.return_value = fmt
        worksheet = MagicMock()
        writer.sheets = {"Sheet1": worksheet}
        mock_writer.return_value = writer

        scan.export_to_excel(df, ["BTCUSDT"], logger,
                             filename="x.xlsx", header="hdr")
        worksheet.merge_range.assert_not_called()
        worksheet.write.assert_any_call("A1", "hdr", fmt)


def test_export_to_excel_formats_percentile_columns():
    """Percentile columns receive percentage formatting."""
    df = pd.DataFrame([
        {"Symbol": "BTCUSDT", "5M": 1.0, "5M Percentile": 0.5}
    ])
    logger = MagicMock()
    with patch("scan.pd.ExcelWriter") as mock_writer, \
         patch("scan.wait_for_file_close"), \
         patch("pandas.DataFrame.to_excel", autospec=True):
        writer = MagicMock()
        fmt = MagicMock()
        writer.book.add_format.return_value = fmt
        worksheet = MagicMock()
        writer.sheets = {"Sheet1": worksheet}
        mock_writer.return_value = writer

        scan.export_to_excel(df, ["BTCUSDT"], logger)
        idx = df.columns.get_loc("5M Percentile")
        worksheet.set_column.assert_any_call(idx, idx, None, fmt)


def test_export_to_html_includes_volume_button(tmp_path):
    """HTML export adds a sort button for 24h volume when column exists."""
    df = pd.DataFrame([
        {"Symbol": "BTCUSDT", "24h USD Volume": 1000, "5M": 1.0}
    ])
    logger = MagicMock()
    out_file = tmp_path / "out.html"
    class DummyStyler:
        """Minimal Styler used to bypass jinja2 dependency."""

        def __init__(self, frame):
            self.frame = frame

        def map(self, *_, **__):
            """Return self without applying styles."""
            return self

        def format(self, *_, **__):
            """Return self without formatting."""
            return self

        def to_html(self, index=False, table_uuid=None):  # pylint: disable=unused-argument
            """Return HTML for the underlying DataFrame."""
            return self.frame.to_html(index=index)

    with patch("scan.open_in_edge"), \
         patch("scan.os.path.join", return_value=out_file), \
         patch("scan.os.makedirs"), \
         patch("pandas.DataFrame.style", new_callable=PropertyMock) as mock_style:
        mock_style.return_value = DummyStyler(df)
        scan.export_to_html(
            df,
            ["BTCUSDT"],
            logger,
            filename="out.html",
            header="hdr",
            include_sort_buttons=True,
        )
    content = out_file.read_text(encoding="utf-8")
    assert "sortBy('24h USD Volume')" in content
