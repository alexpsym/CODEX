import asyncio
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None
pytestmark = pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx is not installed")
if HTTPX_AVAILABLE:
    SPEC = importlib.util.spec_from_file_location('render_master_service_specs', ROOT / 'render' / 'master_service.py')
    master_service = importlib.util.module_from_spec(SPEC)
    assert SPEC and SPEC.loader
    sys.modules[SPEC.name] = master_service
    SPEC.loader.exec_module(master_service)


def _fake_instrument(symbol, base='BTC'):
    return {
        '_category': 'linear',
        'symbol': symbol,
        'baseCoin': base,
        'quoteCoin': 'USDT',
        'launchTime': '1',
        'contractType': 'LinearPerpetual',
        'status': 'Trading',
        'priceFilter': {'tickSize': '0.10', 'minPrice': '0.10', 'maxPrice': '999999'},
        'lotSizeFilter': {'qtyStep': '0.001', 'minOrderQty': '0.001', 'maxOrderQty': '1000', 'maxMktOrderQty': '500', 'minNotionalValue': '5'},
        'leverageFilter': {'minLeverage': '1', 'maxLeverage': '50', 'leverageStep': '0.01'},
    }


def _fake_get_factory(fail_interval=None):
    async def fake_get(_base, path, params):
        if path.endswith('instruments-info'):
            sym = params['symbol']
            base = 'BTC' if sym.startswith('BTC') else 'ETH'
            item = _fake_instrument(sym, base)
            item['_category'] = params['category']
            return {'result': {'list': [item]}}
        if path.endswith('tickers'):
            sym = params['symbol']
            return {'result': {'list': [{'symbol': sym, 'lastPrice': '100', 'fundingRate': '0.01', 'nextFundingTime': '2', 'openInterestValue': '500', 'turnover24h': '1234'}]}}
        if path.endswith('kline'):
            if fail_interval and params.get('interval') == fail_interval:
                raise RuntimeError('kline fail')
            return {'result': {'list': [['10', '100', '110', '90', '101', '1', '1']]}}
        raise AssertionError(path)
    return fake_get


def test_btc_specs_include_ranges_and_no_btc_reference(monkeypatch):
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _x: {'base_url': 'https://x'})
    monkeypatch.setattr(master_service, '_bybit_lookup_symbol', lambda *_a, **_k: asyncio.sleep(0, result=_fake_instrument('BTCUSDT', 'BTC')))
    monkeypatch.setattr(master_service, '_bybit_get_async', _fake_get_factory())
    monkeypatch.setattr(master_service, '_bybit_avg_7d_turnover_usd_async', lambda *_a, **_k: asyncio.sleep(0, result=77.0))
    specs = asyncio.run(master_service._bybit_resolve_and_fetch_specs('BTCUSDT'))
    assert 'volume24h' not in specs
    assert specs['volume24hUsd'] == '1234'
    assert specs['contractType'] == 'LinearPerpetual'
    assert specs['tickSize'] == '0.10'
    assert specs['qtyStep'] == '0.001'
    assert specs['minNotionalValue'] == '5'
    assert specs['maxLeverage'] == '50'
    for key in ['range.1m','range.5m','range.15m','range.30m','range.1h','range.4h','range.1d','range.1w','range.1mo']:
      assert key in specs and abs(float(specs[key]) - 0.2) < 1e-9
      assert specs['_units'][key] == 'fraction'
    assert '_btc_reference' not in specs


def test_eth_specs_include_btc_reference(monkeypatch):
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _x: {'base_url': 'https://x'})
    monkeypatch.setattr(master_service, '_bybit_lookup_symbol', lambda *_a, **_k: asyncio.sleep(0, result=_fake_instrument('ETHUSDT', 'ETH')))
    monkeypatch.setattr(master_service, '_bybit_get_async', _fake_get_factory())
    monkeypatch.setattr(master_service, '_bybit_avg_7d_turnover_usd_async', lambda *_a, **_k: asyncio.sleep(0, result=77.0))
    specs = asyncio.run(master_service._bybit_resolve_and_fetch_specs('ETHUSDT'))
    assert 'volume24h' not in specs
    assert '_btc_reference' in specs
    assert specs['_btc_reference']['resolved_symbol'] == 'BTCUSDT'
    assert 'volume24hUsd' in specs['_btc_reference']
    assert 'range.1m' in specs['_btc_reference']


def test_range_failure_exposes_warning(monkeypatch):
    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', lambda _x: {'base_url': 'https://x'})
    monkeypatch.setattr(master_service, '_bybit_lookup_symbol', lambda *_a, **_k: asyncio.sleep(0, result=_fake_instrument('BTCUSDT', 'BTC')))
    monkeypatch.setattr(master_service, '_bybit_get_async', _fake_get_factory(fail_interval='W'))
    monkeypatch.setattr(master_service, '_bybit_avg_7d_turnover_usd_async', lambda *_a, **_k: asyncio.sleep(0, result=77.0))
    specs = asyncio.run(master_service._bybit_resolve_and_fetch_specs('BTCUSDT'))
    warns = specs.get('_spec_warnings') or []
    assert any(w.get('field') == 'range.1w' for w in warns)


def test_zec_binance_specs_parse_reordered_filters_without_optional_filters(monkeypatch):
    zec = {
        'symbol': 'ZECUSDT',
        'pair': 'ZECUSDT',
        'contractType': 'PERPETUAL',
        'status': 'TRADING',
        'baseAsset': 'ZEC',
        'quoteAsset': 'USDT',
        'onboardDate': 1,
        'filters': [
            {'filterType': 'MIN_NOTIONAL', 'notional': '5'},
            {'filterType': 'MARKET_LOT_SIZE', 'maxQty': '1000'},
            {'filterType': 'PRICE_FILTER', 'maxPrice': '100000', 'tickSize': '0.01', 'minPrice': '0.01'},
            {'filterType': 'LOT_SIZE', 'maxQty': '5000', 'stepSize': '0.001', 'minQty': '0.001'},
            {'filterType': 'MAX_NUM_ORDERS', 'limit': 200},
        ],
    }

    async def fake_get(path, params=None):
        if path.endswith('exchangeInfo'):
            return {'symbols': [zec]}
        if path.endswith('ticker/24hr'):
            return {'symbol': 'ZECUSDT', 'lastPrice': '40', 'quoteVolume': '1234'}
        if path.endswith('premiumIndex'):
            return {'symbol': 'ZECUSDT', 'markPrice': '40', 'lastFundingRate': '0.0001', 'nextFundingTime': 2}
        if path.endswith('openInterest'):
            return {'symbol': 'ZECUSDT', 'openInterest': '10'}
        if path.endswith('klines'):
            if params['interval'] == '1d' and params['limit'] == 7:
                return [[1, '40', '42', '39', '41', '1', 2, '100']]
            return [[1, '40', '42', '39', '41']]
        raise AssertionError(path)

    monkeypatch.setattr(master_service, '_binance_futures_get_async', fake_get)
    monkeypatch.setattr(master_service, '_BINANCE_EXCHANGE_INFO_CACHE', {'ts': 0.0, 'symbols': []})
    monkeypatch.setattr(master_service, '_BINANCE_RANGE_CACHE', {})
    for query in ('ZECUSDT', 'ZEC/USDT', 'ZEC USDT'):
        specs = asyncio.run(master_service._fetch_instrument_specs(query, prefer='crypto'))
        assert specs['resolved_symbol'] == 'ZECUSDT'
        assert specs['source'] == 'binance_usdm'
        assert specs['tickSize'] == '0.01'
        assert specs['qtyStep'] == '0.001'
        assert specs['maxMktOrderQty'] == '1000'
        assert specs['minNotionalValue'] == '5'
        assert 'maxLeverage' not in specs


def test_binance_movement_ranges_keep_interval_specific_values_and_cache_keys(monkeypatch):
    intervals = [
        ('range.15m', '15m'),
        ('range.30m', '30m'),
        ('range.1h', '1h'),
        ('range.4h', '4h'),
        ('range.1d', '1d'),
    ]
    highs = {'15m': 101, '30m': 102, '1h': 103, '4h': 104, '1d': 105}
    requested = []

    async def fake_get(path, params=None):
        assert path == '/fapi/v1/klines'
        requested.append((params['symbol'], params['interval']))
        return [[1, '100', str(highs[params['interval']]), '100', '100']]

    monkeypatch.setattr(master_service, '_BINANCE_RANGE_INTERVALS', intervals)
    monkeypatch.setattr(master_service, '_BINANCE_RANGE_CACHE', {})
    monkeypatch.setattr(master_service, '_binance_futures_get_async', fake_get)
    ranges, warnings = asyncio.run(master_service._binance_fetch_range_specs_async('BTCUSDT'))

    assert warnings == []
    assert requested == [('BTCUSDT', interval) for _field, interval in intervals]
    assert ranges == {
        'range.15m': 0.01,
        'range.30m': 0.02,
        'range.1h': 0.03,
        'range.4h': 0.04,
        'range.1d': 0.05,
    }
    expected_keys = {
        master_service._binance_range_cache_key('BTCUSDT', interval)
        for _field, interval in intervals
    }
    assert set(master_service._BINANCE_RANGE_CACHE) == expected_keys
    assert master_service._binance_range_cache_key('BTCUSDT', '15m') != master_service._binance_range_cache_key('ETHUSDT', '15m')
