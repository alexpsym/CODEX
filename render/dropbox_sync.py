"""Dropbox upload/download helpers for Render state backups."""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx


DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DROPBOX_UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"
DROPBOX_DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"


@dataclass
class DropboxToken:
    access_token: str
    expires_at: float


_TOKEN_CACHE: Optional[DropboxToken] = None


def _clean_env(name: str) -> str:
    return (os.getenv(name) or "").strip().strip('"').strip("'")


def _fetch_dropbox_access_token() -> DropboxToken:
    app_key = _clean_env("DROPBOX_APP_KEY")
    app_secret = _clean_env("DROPBOX_APP_SECRET")
    refresh_token = _clean_env("DROPBOX_REFRESH_TOKEN")
    if not app_key or not app_secret or not refresh_token:
        raise ValueError("Dropbox refresh token credentials are missing.")

    auth = base64.b64encode(f"{app_key}:{app_secret}".encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {auth}"}
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    response = httpx.post(DROPBOX_TOKEN_URL, data=payload, headers=headers, timeout=10)
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise ValueError("Dropbox token response missing access_token.")
    expires_in = float(data.get("expires_in") or 14400)
    return DropboxToken(access_token=token, expires_at=time.time() + expires_in - 60)


def get_dropbox_access_token() -> str:
    env_token = _clean_env("DROPBOX_ACCESS_TOKEN")
    if env_token:
        return env_token
    global _TOKEN_CACHE
    if _TOKEN_CACHE and _TOKEN_CACHE.expires_at > time.time():
        return _TOKEN_CACHE.access_token
    _TOKEN_CACHE = _fetch_dropbox_access_token()
    return _TOKEN_CACHE.access_token


def upload_bytes(path: str, payload: bytes) -> dict:
    if not path.startswith("/"):
        raise ValueError("Dropbox path must be absolute (start with /).")
    token = get_dropbox_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps(
            {"path": path, "mode": "overwrite", "mute": True}
        ),
        "Content-Type": "application/octet-stream",
    }
    response = httpx.post(
        DROPBOX_UPLOAD_URL, headers=headers, content=payload, timeout=20
    )
    response.raise_for_status()
    return response.json()


def download_bytes(path: str) -> bytes:
    if not path.startswith("/"):
        raise ValueError("Dropbox path must be absolute (start with /).")
    token = get_dropbox_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path}),
    }
    response = httpx.post(DROPBOX_DOWNLOAD_URL, headers=headers, timeout=20)
    if response.status_code == 409:
        raise FileNotFoundError("Dropbox path not found.")
    response.raise_for_status()
    return response.content
