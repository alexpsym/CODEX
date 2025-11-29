#!/usr/bin/env python3
"""
Download YouTube links stored in the Brave bookmarks toolbar's MUSIC folder.

Features
- Platform-aware default Brave bookmarks path with an interactive override.
- Restricts traversal to the MUSIC folder on the bookmarks bar.
- Deduplicates YouTube links before downloading.
- Provides per-download success/failure feedback and handles missing yt-dlp.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set


# ─── BOOKMARK DISCOVERY ─────────────────────────────────────────────────────────

def default_bookmarks_path() -> Path:
    home = Path.home()
    if sys.platform.startswith("win"):
        return home / "AppData" / "Local" / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Bookmarks"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser" / "Default" / "Bookmarks"
    return home / ".config" / "BraveSoftware" / "Brave-Browser" / "Default" / "Bookmarks"


def ask_bookmark_path() -> Path:
    """Ask for Brave's bookmarks file path or use the detected default."""
    default = default_bookmarks_path()
    print("Enter the full path to your Brave 'Bookmarks' file.")
    print(f"Press Enter to use the default: {default}")

    while True:
        path = input("> ").strip()
        bookmark_path = Path(path) if path else default

        if bookmark_path.is_dir():
            candidate = bookmark_path / "Bookmarks"
            if candidate.exists():
                bookmark_path = candidate
            else:
                print("The provided path is a directory. Please provide the full path to the 'Bookmarks' file.")
                continue

        if not bookmark_path.exists():
            print("Could not find the file. Check the path and try again.")
            continue

        if not bookmark_path.is_file():
            print("The provided path is not a file. Please try again.")
            continue

        return bookmark_path


def find_music_folder(bookmark_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the MUSIC folder node from the bookmarks toolbar if present."""
    bar = bookmark_data.get("roots", {}).get("bookmark_bar", {})
    for child in bar.get("children", []):
        if child.get("type") == "folder" and child.get("name") == "MUSIC":
            return child
    return None


def collect_youtube_urls(node: Any, results: Set[str]) -> None:
    """Search the bookmark data for YouTube video links."""
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

def download_links(urls: Iterable[str]) -> None:
    """Download each URL with yt-dlp, reporting per-link success/failure."""
    for url in urls:
        print(f"Downloading: {url}")
        try:
            result = subprocess.run(["yt-dlp", url])
        except FileNotFoundError:
            print("yt-dlp is not installed or not found on PATH. Aborting.")
            return

        if result.returncode != 0:
            print(f"Download failed (exit code {result.returncode}): {url}")
        else:
            print(f"Downloaded successfully: {url}")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    bookmark_file = ask_bookmark_path()

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
    main()
