import json
import subprocess
from pathlib import Path

def ask_bookmark_path():
    """Ask for Brave's bookmarks file path or use a default location."""
    default = Path.home() / r"AppData\Local\BraveSoftware\Brave-Browser\User Data\Default\Bookmarks"
    print("Enter the full path to your Brave 'Bookmarks' file.")
    print(f"Press Enter to use the default: {default}")
    path = input("> ").strip()
    return Path(path) if path else default

def find_youtube_urls(node, results):
    """Search the bookmark data for YouTube video links."""
    if isinstance(node, dict):
        if node.get("type") == "url":
            url = node.get("url", "")
            if "youtube.com/watch" in url:
                results.append(url)
        for value in node.values():
            find_youtube_urls(value, results)
    elif isinstance(node, list):
        for item in node:
            find_youtube_urls(item, results)

def main():
    bookmark_file = ask_bookmark_path()

    if not bookmark_file.exists():
        print("Could not find the file. Check the path and try again.")
        return

    with open(bookmark_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    youtube_links = []
    find_youtube_urls(data, youtube_links)

    if not youtube_links:
        print("No YouTube bookmarks found.")
        return

    for url in youtube_links:
        print(f"Downloading: {url}")
        subprocess.run(["yt-dlp", url])

if __name__ == "__main__":
    main()
