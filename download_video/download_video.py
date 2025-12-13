import os
import sys
import threading
import tkinter as tk
from queue import Empty, Queue
from typing import Callable
from tkinter import messagebox
from urllib.parse import urljoin, urlparse

import logging
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def download_video(url: str) -> str:
    """Download the primary video from a webpage and return the filename."""

    response = requests.get(url, timeout=10)
    response.raise_for_status()

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
        raise ValueError("Could not find a video on the page.")

    video_url = urljoin(url, video_url)
    filename = os.path.basename(urlparse(video_url).path) or "video.mp4"

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

    logger.info("Downloaded video to %s", filename)
    return filename


def start_download(entry: tk.Entry, download_button: tk.Button, status_label: tk.Label) -> None:
    """Trigger the download process from the GUI."""

    url = entry.get().strip()
    if not url:
        messagebox.showerror("Missing URL", "Please enter a URL to download.")
        return

    download_button.config(state=tk.DISABLED)
    status_label.config(text="Downloading...")

    task_queue: Queue[tuple[Callable, tuple, dict]] = Queue()
    download_thread: threading.Thread | None = None

    def run_on_main(callback, *args, **kwargs) -> None:
        task_queue.put((callback, args, kwargs))

    def process_queue() -> None:
        while True:
            try:
                callback, args, kwargs = task_queue.get_nowait()
            except Empty:
                break
            callback(*args, **kwargs)

        if download_thread and (download_thread.is_alive() or not task_queue.empty()):
            status_label.after(50, process_queue)

    def show_error(message: str) -> None:
        messagebox.showerror("Download Failed", message)
        status_label.config(text="Download failed")

    def show_success(filename: str) -> None:
        messagebox.showinfo("Download Complete", f"Saved as: {filename}")
        status_label.config(text=f"Saved as: {filename}")

    def enable_button() -> None:
        download_button.config(state=tk.NORMAL)

    def run_download() -> None:
        try:
            filename = download_video(url)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to download video")
            run_on_main(show_error, str(exc))
        else:
            run_on_main(show_success, filename)
        finally:
            run_on_main(enable_button)

    download_thread = threading.Thread(target=run_download, daemon=True)
    download_thread.start()
    process_queue()


def launch_gui() -> None:
    """Create a simple GUI for entering the download URL."""

    root = tk.Tk()
    root.title("Video Downloader")

    tk.Label(root, text="Enter the webpage URL:").grid(row=0, column=0, padx=10, pady=(10, 2), sticky="w")

    url_entry = tk.Entry(root, width=60)
    url_entry.grid(row=1, column=0, padx=10, pady=5, sticky="we")
    url_entry.focus()

    download_button = tk.Button(root, text="Download")
    download_button.grid(row=1, column=1, padx=(0, 10), pady=5)

    status_label = tk.Label(root, text="Ready", anchor="w")
    status_label.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="we")

    download_button.config(command=lambda: start_download(url_entry, download_button, status_label))

    root.columnconfigure(0, weight=1)
    root.mainloop()


def main() -> None:
    """Launch the graphical interface for downloading videos."""

    launch_gui()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
