#!/usr/bin/env python3
"""
Download pasted YouTube URLs as mp3 files.

Features
- Validates pasted URLs and checks for yt-dlp early.
- Provides per-download success/failure feedback.
- Logs startup errors next to the script for troubleshooting.
"""

import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Set


# ─── DOWNLOADS ──────────────────────────────────────────────────────────────────

def _ffmpeg_installed() -> Optional[str]:
    """Return the ffmpeg executable path when available for audio extraction."""

    return shutil.which("ffmpeg")


def _print_ffmpeg_help() -> None:
    """Provide platform-specific guidance for installing ffmpeg binaries."""

    print("Error: ffmpeg is required to convert downloads to mp3.")
    print(
        "Install a system ffmpeg binary (pip packages alone are not enough). "
        "Examples:"
    )
    print("  Windows: `choco install ffmpeg` or download from https://www.gyan.dev/ffmpeg/builds/")
    print("  macOS:   `brew install ffmpeg`")
    print("  Linux:   `sudo apt-get install ffmpeg` or use your distro's package manager")


def _prompt_manual_urls() -> Set[str]:
    """Prompt the user to paste YouTube URLs and return the parsed set."""

    while True:
        print("Paste the YouTube URLs to download (space/comma separated).")
        print("Press Enter after pasting to start the downloads.")

        raw = input("> ").strip()
        cleaned = raw.replace(",", " ").replace("\n", " ")
        parsed = {token for token in cleaned.split() if token}

        if parsed:
            return parsed

        print("No URLs detected. Please paste at least one YouTube link.\n")


def download_links(urls: Iterable[str]) -> None:
    """Download each URL with yt-dlp, reporting per-link success/failure."""
    if not shutil.which("yt-dlp"):
        print("Error: yt-dlp is not installed or not on your PATH.")
        return

    ffmpeg_path = _ffmpeg_installed()
    if not ffmpeg_path:
        _print_ffmpeg_help()
        return

    print(f"Using ffmpeg at: {ffmpeg_path}")

    common_args = [
        "-f",
        "bestaudio/bv*+ba/b",
        "--extractor-args",
        "youtube:player_client=web",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
    ]
    fallback_args = [
        "-f",
        "bestaudio/bv*+ba/b",
        "--extractor-args",
        "youtube:player_client=android,player_skip=webpage",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
    ]

    for url in urls:
        print(f"Downloading: {url}")
        for args in (common_args, fallback_args):
            try:
                result = subprocess.run(["yt-dlp", *args, url])
            except FileNotFoundError:
                print("yt-dlp executable not found. Aborting remaining downloads.")
                return

            if result.returncode == 0:
                print(f"Downloaded successfully: {url}")
                break

            print(
                "Download failed with this client selection. "
                "Retrying with an alternate player client..."
            )
        else:
            print(f"Download failed (exit code {result.returncode}): {url}")


# ─── LOGGING ───────────────────────────────────────────────────────────────────

def _log_startup_error(exc: BaseException) -> Path:
    """Write startup errors to a log file next to this script."""
    log_path = Path(__file__).resolve().parent / "yt_error_log.txt"
    log_entry = (
        f"\n---\n{datetime.now().isoformat()} - Unhandled exception during startup\n"
        f"{traceback.format_exc()}"
    )
    log_path.write_text(log_path.read_text() + log_entry if log_path.exists() else log_entry)
    return log_path


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    pasted_urls = _prompt_manual_urls()

    download_links(sorted(pasted_urls))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001
        log_path = _log_startup_error(exc)
        print(f"An error occurred. Details have been logged to: {log_path}")
        sys.exit(1)
