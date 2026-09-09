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
    async def fake_get(_base, path, params, **_kwargs):
        if path.endswith('instruments-info'):
            sym = params['symbol']
            base = 'BTC' if sym.startswith('BTC') else 'ETH'
            item = _fake_instrument(sym, base)
            item['_category'] = params['category']
            return {'retCode': 0, 'result': {'list': [item]}}
        if path.endswith('tickers'):
            sym = params['symbol']
            return {'retCode': 0, 'result': {'list': [{'symbol': sym, 'lastPrice': '100', 'fundingRate': '0.01', 'nextFundingTime': '2', 'openInterestValue': '500', 'turnover24h': '1234'}]}}
        if path.endswith('kline'):
            if fail_interval and params.get('interval') == fail_interval:
                raise RuntimeError('kline fail')
            return {'retCode': 0, 'result': {'list': [['10', '100', '110', '90', '101', '1', '1']]}}
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


def test_crypto_specs_use_bybit_first_for_generic_symbol_variants(monkeypatch):
    monkeypatch.setenv('BYBIT_PUBLIC_MARKET_BASE_URL', 'https://api.bytick.com')
    monkeypatch.setattr(master_service, '_BYBIT_INSTRUMENT_CACHE', {})
    monkeypatch.setattr(master_service, '_BYBIT_SYMBOL_LIST_CACHE', {})
    monkeypatch.setattr(master_service, '_BYBIT_NAME_ALIAS_CACHE', {'expires_at': 0.0, 'aliases': {}})
    requests = []
    enrichments = []

    def credentials_forbidden(_account):
        raise AssertionError('public Instrument Lookup must not resolve credentials')

    async def binance_forbidden(_query, **_kwargs):
        raise AssertionError('Binance must not be called when Bybit lists the pair')

    async def fake_get(base_url, path, params, **_kwargs):
        requests.append((base_url, path, dict(params)))
        assert base_url == 'https://api.bytick.com'
        category = params.get('category')
        if path == '/v5/market/instruments-info':
            selector = str(params.get('symbol') or '')
            base = str(params.get('baseCoin') or '')
            rows = []
            symbol = selector or (f'{base}USDT' if base else '')
            expected_category = 'linear' if symbol.startswith(('ZORA', 'BTC')) else 'spot'
            if symbol in {'ZORAUSDT', 'UAIUSDT', 'BTCUSDT'} and category == expected_category:
                rows = [_fake_instrument(symbol, symbol.removesuffix('USDT'))]
                if base:
                    usdc = _fake_instrument(f'{base}USDC', base)
                    usdc['quoteCoin'] = 'USDC'
                    rows.insert(0, usdc)
            return {'retCode': 0, 'retMsg': 'OK', 'result': {'list': rows}}
        if path == '/v5/market/tickers':
            symbol = str(params['symbol'])
            return {
                'retCode': 0,
                'retMsg': 'OK',
                'result': {'list': [{
                    'symbol': symbol,
                    'lastPrice': '1.25',
                    'bid1Price': '1.24',
                    'ask1Price': '1.26',
                    'turnover24h': '2500',
                }]},
            }
        raise AssertionError((path, params))

    async def fake_avg(base_url, symbol, category):
        enrichments.append(('turnover', base_url, symbol, category))
        return 1000.0

    async def fake_ranges(base_url, category, symbol):
        enrichments.append(('ranges', base_url, symbol, category))
        return {'range.1d': 0.1}, []

    monkeypatch.setattr(master_service, 'resolve_bybit_credentials_for', credentials_forbidden)
    monkeypatch.setattr(master_service, '_binance_resolve_and_fetch_specs', binance_forbidden)
    monkeypatch.setattr(master_service, '_bybit_get_async', fake_get)
    monkeypatch.setattr(master_service, '_bybit_avg_7d_turnover_usd_async', fake_avg)
    monkeypatch.setattr(master_service, '_bybit_fetch_range_specs_async', fake_ranges)

    variants = {
        'ZORAUSDT': ('ZORAUSDT', 'linear'),
        'ZORA/USDT': ('ZORAUSDT', 'linear'),
        'ZORA USDT': ('ZORAUSDT', 'linear'),
        'ZORA': ('ZORAUSDT', 'linear'),
        'UAIUSDT': ('UAIUSDT', 'spot'),
        'UAI/USDT': ('UAIUSDT', 'spot'),
        'UAI USDT': ('UAIUSDT', 'spot'),
        'UAI': ('UAIUSDT', 'spot'),
    }
    for query, (symbol, category) in variants.items():
        specs = asyncio.run(master_service._fetch_instrument_specs(query, prefer='bybit'))
        assert specs['source'] == 'bybit'
        assert specs['resolved_symbol'] == symbol
        assert specs['category'] == category

    assert requests
    assert {request[0] for request in requests} == {'https://api.bytick.com'}
    assert ('turnover', 'https://api.bytick.com', 'ZORAUSDT', 'linear') in enrichments
    assert ('ranges', 'https://api.bytick.com', 'UAIUSDT', 'spot') in enrichments


def test_crypto_specs_fall_back_to_labeled_binance_only_after_confirmed_bybit_absence(monkeypatch):
    monkeypatch.setattr(master_service, '_BYBIT_INSTRUMENT_CACHE', {})
    events = []

    async def confirmed_absence(_base_url, path, params, **_kwargs):
        assert path == '/v5/market/instruments-info'
        events.append(('bybit', params['category']))
        return {'retCode': 0, 'retMsg': 'OK', 'result': {'list': []}}

    async def fake_binance(query, **_kwargs):
        events.append(('binance', query))
        return {'source': 'binance_usdm', 'resolved_symbol': 'MISSINGUSDT'}

    monkeypatch.setattr(master_service, '_bybit_get_async', confirmed_absence)
    monkeypatch.setattr(master_service, '_binance_resolve_and_fetch_specs', fake_binance)
    specs = asyncio.run(master_service._fetch_instrument_specs('MISSINGUSDT', prefer='crypto'))

    assert events == [
        ('bybit', 'linear'),
        ('bybit', 'spot'),
        ('bybit', 'inverse'),
        ('binance', 'MISSINGUSDT'),
    ]
    assert specs['source'] == 'binance_usdm'
    assert specs['_source_notice'] == 'Bybit did not list this instrument; Binance USD-M was used as fallback.'


def test_bybit_lookup_failure_never_silently_falls_back_to_binance(monkeypatch):
    binance_calls = []

    async def fake_binance(query, **_kwargs):
        binance_calls.append(query)
        return {'source': 'binance_usdm', 'resolved_symbol': query}

    monkeypatch.setattr(master_service, '_binance_resolve_and_fetch_specs', fake_binance)
    monkeypatch.setattr(master_service, '_BYBIT_INSTRUMENT_CACHE', {})

    async def transport_failure(_base_url, _path, _params, **_kwargs):
        raise master_service.httpx.ConnectError('public endpoint unavailable')

    monkeypatch.setattr(master_service, '_bybit_get_async', transport_failure)
    with pytest.raises(master_service.HTTPException) as transport_error:
        asyncio.run(master_service._fetch_instrument_specs('FAILUSDT', prefer='bybit'))
    assert transport_error.value.status_code == 502
    assert 'Bybit public market specification request failed' in transport_error.value.detail
    assert binance_calls == []

    async def api_failure(_base_url, _path, _params, **_kwargs):
        return {'retCode': 10001, 'retMsg': 'invalid request', 'result': {'list': []}}

    monkeypatch.setattr(master_service, '_BYBIT_INSTRUMENT_CACHE', {})
    monkeypatch.setattr(master_service, '_bybit_get_async', api_failure)
    with pytest.raises(master_service.HTTPException) as api_error:
        asyncio.run(master_service._fetch_instrument_specs('FAILUSDT', prefer='perp'))
    assert api_error.value.status_code == 502
    assert 'retCode=10001' in api_error.value.detail
    assert binance_calls == []

    instrument = _fake_instrument('PARTIALUSDT', 'PARTIAL')

    async def resolved(_base_url, _query):
        return dict(instrument)

    async def optional_failure(_base_url, path, _params, **_kwargs):
        raise master_service.httpx.ReadTimeout(f'optional {path} unavailable')

    async def avg_failure(_base_url, _symbol, _category):
        raise RuntimeError('optional turnover unavailable')

    async def range_partial(_base_url, category, symbol):
        assert category == 'linear'
        return {}, [{'scope': 'range', 'symbol': symbol, 'field': 'range.1d', 'message': 'optional range unavailable'}]

    monkeypatch.setattr(master_service, '_BYBIT_INSTRUMENT_CACHE', {})
    monkeypatch.setattr(master_service, '_bybit_lookup_symbol', resolved)
    monkeypatch.setattr(master_service, '_bybit_get_async', optional_failure)
    monkeypatch.setattr(master_service, '_bybit_avg_7d_turnover_usd_async', avg_failure)
    monkeypatch.setattr(master_service, '_bybit_fetch_range_specs_async', range_partial)
    specs = asyncio.run(master_service._fetch_instrument_specs('PARTIALUSDT', prefer='perpetual'))

    assert specs['source'] == 'bybit'
    assert specs['resolved_symbol'] == 'PARTIALUSDT'
    assert specs['tickSize'] == '0.10'
    fields = {warning['field'] for warning in specs['_spec_warnings']}
    assert {'tickers', 'avg7dTurnoverUsd', 'range.1d', 'instrument'} <= fields
    assert binance_calls == []


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

    async def fake_get(path, params=None, **_kwargs):
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


def test_oanda_http_failure_is_not_reported_as_binance(monkeypatch):
    async def fail_oanda(_query):
        raise master_service.httpx.ConnectError('oanda unavailable')

    monkeypatch.setattr(master_service, '_oanda_resolve_and_fetch_specs', fail_oanda)
    with pytest.raises(master_service.HTTPException) as caught:
        asyncio.run(master_service._fetch_instrument_specs('EUR_USD', prefer='oanda'))

    assert caught.value.status_code == 502
    assert 'OANDA specification request failed for EUR_USD' in caught.value.detail
    assert 'Binance' not in caught.value.detail
