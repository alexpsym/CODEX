import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import ivcore
from datetime import datetime, timezone


def test_update_scaled_iv():
    val = ivcore.update_scaled_iv("1h")
    assert isinstance(val, float)
    assert val > 0


def sample_options():
    expiry = datetime.now(timezone.utc)
    return [
        {"expiry": expiry, "type": "C", "delta": 0.3, "markIv": 0.5, "volume": 10, "openInterest": 5},
        {"expiry": expiry, "type": "P", "delta": -0.3, "markIv": 0.4, "volume": 8, "openInterest": 7},
    ]


def test_compute_skew():
    group = sample_options()
    skew = ivcore.compute_skew(group)
    assert skew == (0.5 - 0.4) * 100


def test_volume_and_oi():
    group = sample_options()
    assert ivcore.compute_volumes(group) == (10, 8)
    assert ivcore.compute_open_interest(group) == (5, 7)
