import os
import sys
from urllib.parse import urljoin, urlparse

import logging
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    """Prompt for a webpage and download its main video file."""
    url = input("Enter the webpage URL: ").strip()
    if not url:
        logger.error("No URL provided.")
        return

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception:
        logger.exception("Failed to retrieve page")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    video_url = None

    video_tag = soup.find("video")
    if video_tag:
        video_url = video_tag.get("src")
        if not video_url:
            source_tag = video_tag.find("source")
            if source_tag:
                video_url = source_tag.get("src")

    if not video_url:
        meta_tag = soup.find("meta", property="og:video")
        if meta_tag:
            video_url = meta_tag.get("content")

    if not video_url:
        logger.error("Could not find a video on the page.")
        return

    video_url = urljoin(url, video_url)
    filename = os.path.basename(urlparse(video_url).path) or "video.mp4"

    try:
        with requests.get(video_url, stream=True) as vid_resp:
            vid_resp.raise_for_status()
            total = int(vid_resp.headers.get("content-length", 0))
            progress = tqdm(total=total, unit="B", unit_scale=True, desc="Downloading")
            with open(filename, "wb") as file:
                for chunk in vid_resp.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        progress.update(len(chunk))
            progress.close()
        logger.info(f"Downloaded video to {filename}")
    except Exception:
        logger.exception("Failed to download video")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
