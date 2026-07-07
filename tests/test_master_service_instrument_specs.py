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
