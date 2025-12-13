#!/usr/bin/env python3
"""
Download YouTube links stored in the Brave bookmarks toolbar's MUSIC folder,
or download specific URLs that you paste at runtime.

Features
- Platform-aware discovery of Brave profiles with interactive selection.
- Validates custom bookmark paths and logs startup errors.
- Restricts traversal to the MUSIC folder on the bookmarks bar.
- Deduplicates YouTube links before downloading and checks for yt-dlp early.
- Provides per-download success/failure feedback.
"""

import json
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set


# ─── BOOKMARK DISCOVERY ─────────────────────────────────────────────────────────

def _platform_default_base() -> Optional[Path]:
    """Return the platform-specific base directory for Brave profiles."""
    home = Path.home()

    if sys.platform.startswith("darwin"):
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("linux"):
        return home / ".config" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("win"):
        return home / "AppData" / "Local" / "BraveSoftware" / "Brave-Browser" / "User Data"

    return None


def _discover_bookmark_files() -> Iterable[Path]:
    """Return available Brave bookmark files, prioritizing the default profile."""
    base = _platform_default_base()
    if not base or not base.exists():
        return []

    bookmark_files = []
    default_bookmarks = base / "Default" / "Bookmarks"
    if default_bookmarks.exists():
        bookmark_files.append(default_bookmarks)

    for child in sorted(base.iterdir()):
        if child.is_dir() and child.name != "Default":
            candidate = child / "Bookmarks"
            if candidate.exists():
                bookmark_files.append(candidate)

    return bookmark_files


def _validate_bookmark_path(raw_path: str, fallback: Path) -> Optional[Path]:
    """Validate user input and convert directories to bookmark files when possible."""
    bookmark_path = Path(raw_path) if raw_path else fallback

    if bookmark_path.is_dir():
        candidate = bookmark_path / "Bookmarks"
        if candidate.exists():
            bookmark_path = candidate
        else:
            print("The provided path is a directory. Please provide the full path to the 'Bookmarks' file.")
            return None

    if not bookmark_path.exists():
        print("Could not find the file. Check the path and try again.")
        return None

    if not bookmark_path.is_file():
        print("The provided path is not a file. Please try again.")
        return None

    return bookmark_path


def ask_bookmark_path() -> Optional[Path]:
    """Ask for Brave's bookmarks file path or use a platform-aware default."""
    detected = list(_discover_bookmark_files())
    fallback = _platform_default_base()
    fallback = fallback / "Default" / "Bookmarks" if fallback else Path.home()

    if detected:
        print("Detected Brave bookmark files:")
        for idx, path in enumerate(detected, start=1):
            print(f"  {idx}. {path}")
        print("Enter the number of the profile to use, or provide a custom path.")
        print(f"Press Enter to use the default selection: {detected[0]}")

        while True:
            choice = input("> ").strip()

            if choice.isdigit():
                selection = int(choice)
                if 1 <= selection <= len(detected):
                    return detected[selection - 1]
                print("Invalid selection. Please choose a listed number.")
                continue

            validated = _validate_bookmark_path(choice, detected[0])
            if validated:
                return validated
    else:
        print("Enter the full path to your Brave 'Bookmarks' file.")
        print(f"Press Enter to use the default: {fallback}")

        while True:
            choice = input("> ").strip()
            validated = _validate_bookmark_path(choice, fallback)
            if validated:
                return validated

    return None


def find_music_folder(bookmark_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the MUSIC folder node from the bookmarks toolbar if present."""
    bar = bookmark_data.get("roots", {}).get("bookmark_bar", {})
    for child in bar.get("children", []):
        if child.get("type") == "folder" and child.get("name") == "MUSIC":
            return child
    return None


def collect_youtube_urls(node: Any, results: Set[str]) -> None:
    """Search the bookmark data for YouTube video links inside the MUSIC folder."""
    if isinstance(node, dict):
        if node.get("type") == "url":
            url = node.get("url", "")
            if "youtube.com/watch" in url:
                results.add(url)
        for value in node.values():
            collect_youtube_urls(value, results)
    elif isinstance(node, list):
        for item in node:
            collect_youtube_urls(item, results)


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
    """Return user-pasted URLs when provided, otherwise an empty set."""

    print("Paste the YouTube URLs to download (space/comma separated).")
    print("Press Enter without typing anything to use Brave bookmarks instead.")

    raw = input("> ").strip()
    if not raw:
        return set()

    cleaned = raw.replace(",", " ").replace("\n", " ")
    return {token for token in cleaned.split() if token}


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

    if pasted_urls:
        print("Using provided URLs; Brave bookmarks will not be read.")
        download_links(sorted(pasted_urls))
        return

    bookmark_file = ask_bookmark_path()

    if not bookmark_file:
        print("No bookmark file selected.")
        return

    with open(bookmark_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    music_folder = find_music_folder(data)
    if not music_folder:
        print("No 'MUSIC' folder found on the bookmarks toolbar.")
        return

    youtube_links: Set[str] = set()
    collect_youtube_urls(music_folder, youtube_links)

    if not youtube_links:
        print("No YouTube bookmarks found inside the 'MUSIC' folder.")
        return

    download_links(sorted(youtube_links))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001
        log_path = _log_startup_error(exc)
        print(f"An error occurred. Details have been logged to: {log_path}")
        sys.exit(1)
