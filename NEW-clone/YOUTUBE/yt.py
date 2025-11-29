import json
import subprocess
import shutil
from pathlib import Path

def ask_bookmark_path():
    """Ask for Brave's bookmarks file path or use a default location."""
    default = Path.home() / r"AppData\Local\BraveSoftware\Brave-Browser\User Data\Default\Bookmarks"
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
