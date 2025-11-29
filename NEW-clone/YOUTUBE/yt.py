import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

def _platform_default_base():
    """Return the platform-specific base directory for Brave profiles."""
    home = Path.home()

    if sys.platform.startswith("darwin"):
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("linux"):
        return home / ".config" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("win"):
        return home / "AppData" / "Local" / "BraveSoftware" / "Brave-Browser" / "User Data"

    return None


def _discover_bookmark_files():
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


def ask_bookmark_path():
    """Ask for Brave's bookmarks file path or use a platform-aware default."""
    detected = _discover_bookmark_files()
    fallback = _platform_default_base()
    if fallback:
        fallback = fallback / "Default" / "Bookmarks"
    else:
        fallback = Path.home()

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

def find_youtube_urls(node, results, seen):
    """Search the bookmark data for YouTube video links."""
    if isinstance(node, dict):
        if node.get("type") == "url":
            url = node.get("url", "")
            if "youtube.com/watch" in url and url not in seen:
                results.append(url)
                seen.add(url)
        for value in node.values():
            find_youtube_urls(value, results, seen)
    elif isinstance(node, list):
        for item in node:
            find_youtube_urls(item, results, seen)

def main():
    bookmark_file = ask_bookmark_path()

    with open(bookmark_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    youtube_links = []
    find_youtube_urls(data, youtube_links, set())

    if not youtube_links:
        print("No YouTube bookmarks found.")
        return

    if not shutil.which("yt-dlp"):
        print("Error: yt-dlp is not installed or not on your PATH.")
        return

    for url in youtube_links:
        print(f"Downloading: {url}")
        try:
            result = subprocess.run(
                ["yt-dlp", url], capture_output=True, text=True
            )
        except FileNotFoundError:
            print("Error: yt-dlp executable not found. Aborting remaining downloads.")
            return

        if result.returncode == 0:
            print(f"  Success: {url}")
        else:
            print(f"  Failed: {url} (exit code {result.returncode})")
            if result.stderr:
                print(result.stderr.strip())

if __name__ == "__main__":
    main()
