"""Shared Bybit credential resolution helper.

This helper keeps legacy BYBIT_API_KEY/BYBIT_API_SECRET support while
preferencing the Render-provided KEY1/SECRET1 (live) and KEY2/SECRET2
(demo/testnet) pairs.

Important:
- Bybit "Demo Trading" on mainnet uses a dedicated domain: https://api-demo.bybit.com
- Bybit Testnet uses: https://api-testnet.bybit.com

These are *not* interchangeable. Demo API keys must be used against the
demo domain.
"""
from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Sequence, Tuple


_DEMO_CREDENTIAL_SOURCES: Sequence[Tuple[str, str, str]] = (
    ("KEY2", "BYBIT_API_KEY2", "BYBIT_API_SECRET2"),
    ("DEMO_EXPLICIT", "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"),
    ("LEGACY", "BYBIT_API_KEY", "BYBIT_API_SECRET"),
)


class BybitCredentialConflictError(ValueError):
    """Raised when distinct complete credential pairs target one Bybit mode."""

    def __init__(self, *, mode: str, sources: Sequence[Tuple[str, str, str, str]]) -> None:
        self.mode = str(mode or "demo")
        self.sources = tuple(source[0] for source in sources)
        self.fingerprints = {source[0]: source[3] for source in sources}
        env_pairs = ", ".join(
            f"{label} ({key_env}/{secret_env})"
            for label, key_env, secret_env, _fingerprint in sources
        )
        super().__init__(
            f"Conflicting complete Bybit {self.mode} credential pairs detected: {env_pairs}. "
            "Reconcile those variables so every complete pair is identical, or leave only "
            "one complete pair configured. KEY2 is the preferred demo source."
        )


def _credential_fingerprint(key: str, secret: str) -> str:
    """Return a short one-way pair identifier without exposing credential characters."""

    if not key or not secret:
        return "unavailable"
    digest = hashlib.sha256(f"{key}\0{secret}".encode("utf-8")).hexdigest()
    return digest[:12]


def _configured_pairs(
    sources: Sequence[Tuple[str, str, str]],
) -> List[Tuple[str, str, str, str, str, str]]:
    configured: List[Tuple[str, str, str, str, str, str]] = []
    for label, key_env, secret_env in sources:
        key = str(os.getenv(key_env) or "").strip()
        secret = str(os.getenv(secret_env) or "").strip()
        if key or secret:
            configured.append(
                (label, key_env, secret_env, key, secret, _credential_fingerprint(key, secret))
            )
    return configured


def _resolve_atomic_pair(
    *,
    mode: str,
    sources: Sequence[Tuple[str, str, str]],
    reject_conflicts: bool,
) -> Tuple[str, str, str]:
    configured = _configured_pairs(sources)
    complete = [entry for entry in configured if entry[3] and entry[4]]
    distinct_fingerprints = {entry[5] for entry in complete}
    if reject_conflicts and len(distinct_fingerprints) > 1:
        raise BybitCredentialConflictError(
            mode=mode,
            sources=[(entry[0], entry[1], entry[2], entry[5]) for entry in complete],
        )
    if complete:
        label, _key_env, _secret_env, key, secret, _fingerprint = complete[0]
        return key, secret, label
    if configured:
        label, _key_env, _secret_env, key, secret, _fingerprint = configured[0]
        return key, secret, label
    return "", "", "NONE"


def _coerce_base_url(env_mode: str, candidate: str) -> str:
    """Return the canonical official host for the selected Bybit environment.

    ``candidate`` is deliberately not propagated.  Besides preventing a mode
    mismatch, canonicalization prevents credentials from being sent to a
    lookalike host such as ``api-demo.bybit.com.example.invalid``.
    """

    del candidate
    if env_mode == "demo":
        return "https://api-demo.bybit.com"
    if env_mode in {"testnet", "paper"}:
        return "https://api-testnet.bybit.com"
    return "https://api.bybit.com"


def resolve_bybit_credentials() -> Tuple[str, str, str, str, str]:
    """Return (mode, key, secret, base_url, key_source).

    The default mode is ``live``.

    - ``BYBIT_ENV=live`` -> KEY1/SECRET1 + https://api.bybit.com
    - ``BYBIT_ENV=demo`` -> KEY2/SECRET2 + https://api-demo.bybit.com
    - ``BYBIT_ENV=testnet`` (or paper) -> KEY2/SECRET2 + https://api-testnet.bybit.com

    Legacy ``BYBIT_API_KEY``/``BYBIT_API_SECRET`` values are still honored as a
    fallback so existing env files keep working.
    """

    mode = os.getenv("BYBIT_ENV", "live").strip().lower() or "live"
    return resolve_bybit_credentials_for(mode)


def resolve_bybit_credentials_for(mode: str) -> Tuple[str, str, str, str, str]:
    """Return credentials for an explicit mode without relying on ``BYBIT_ENV``.

    Demo pairs are atomic and consistently prefer KEY2, then the explicit demo
    names, then the legacy pair. Distinct complete demo pairs are rejected so a
    stale shadow pair can never be selected silently. Testnet deliberately does
    not consume the mainnet-demo-only ``BYBIT_DEMO_*`` pair.
    """

    normalized = (mode or "live").strip().lower()
    if normalized in {"demo", "testnet", "paper"}:
        if normalized in {"testnet", "paper"}:
            key, secret, key_source = _resolve_atomic_pair(
                mode="testnet",
                sources=(
                    ("KEY2", "BYBIT_API_KEY2", "BYBIT_API_SECRET2"),
                    ("LEGACY", "BYBIT_API_KEY", "BYBIT_API_SECRET"),
                ),
                reject_conflicts=False,
            )
            base_url = _coerce_base_url(
                "testnet",
                os.getenv("BYBIT_BASE_URL_TESTNET")
                or os.getenv("BYBIT_API_BASE_TESTNET")
                or "https://api-testnet.bybit.com",
            )
            env_label = "testnet"
        else:
            key, secret, key_source = _resolve_atomic_pair(
                mode="demo",
                sources=_DEMO_CREDENTIAL_SOURCES,
                reject_conflicts=True,
            )
            base_url = _coerce_base_url(
                "demo",
                os.getenv("BYBIT_BASE_URL_DEMO")
                or os.getenv("BYBIT_API_BASE_DEMO")
                or "https://api-demo.bybit.com",
            )
            env_label = "demo"
        return env_label, key, secret, base_url.rstrip("/"), key_source

    key, secret, key_source = _resolve_atomic_pair(
        mode="live",
        sources=(
            ("KEY1", "BYBIT_API_KEY1", "BYBIT_API_SECRET1"),
            ("LEGACY", "BYBIT_API_KEY", "BYBIT_API_SECRET"),
        ),
        reject_conflicts=False,
    )
    base_url = _coerce_base_url(
        "live",
        os.getenv("BYBIT_BASE_URL_LIVE")
        or os.getenv("BYBIT_BASE_URL")
        or os.getenv("BYBIT_API_BASE")
        or "https://api.bybit.com",
    )
    return "live", key, secret, base_url.rstrip("/"), key_source


def summarize_bybit_auth() -> Dict[str, str]:
    """Return a summary payload without exposing secrets."""

    mode, key, secret, base_url, key_source = resolve_bybit_credentials()
    return {
        "mode": mode,
        "base_url": base_url,
        "auth": "yes" if key and secret else "no",
        "key_source": key_source,
    }


def describe_bybit_credentials_for(mode: str) -> Dict[str, object]:
    normalized = (mode or "live").strip().lower()
    try:
        env_label, key, secret, base_url, key_source = resolve_bybit_credentials_for(normalized)
        conflict_message = ""
        conflict_sources: List[str] = []
    except BybitCredentialConflictError as exc:
        env_label = "demo"
        key = ""
        secret = ""
        base_url = _coerce_base_url(
            "demo",
            os.getenv("BYBIT_BASE_URL_DEMO")
            or os.getenv("BYBIT_API_BASE_DEMO")
            or "https://api-demo.bybit.com",
        ).rstrip("/")
        key_source = "CONFLICT"
        conflict_message = str(exc)
        conflict_sources = list(exc.sources)
    missing: List[str] = []
    if env_label == "demo":
        if not key:
            missing.append("BYBIT_DEMO_API_KEY|BYBIT_API_KEY2|BYBIT_API_KEY")
        if not secret:
            missing.append("BYBIT_DEMO_API_SECRET|BYBIT_API_SECRET2|BYBIT_API_SECRET")
    elif env_label == "live":
        if not key:
            missing.append("BYBIT_API_KEY1|BYBIT_API_KEY")
        if not secret:
            missing.append("BYBIT_API_SECRET1|BYBIT_API_SECRET")
    return {
        "mode": env_label,
        "base_url": base_url,
        "key_source": key_source,
        "credentials_available": bool(key and secret),
        "pair_complete": bool(key and secret),
        "credential_fingerprint": _credential_fingerprint(key, secret),
        "key_length": len(key),
        "missing_env_vars": missing,
        "credential_conflict": bool(conflict_message),
        "conflict_sources": conflict_sources,
        "message": conflict_message,
    }
