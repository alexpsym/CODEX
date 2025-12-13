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
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Set


# ─── DOWNLOADS ──────────────────────────────────────────────────────────────────

def _ffmpeg_installed() -> Optional[str]:
    """Return the ffmpeg executable path when available for audio extraction."""

    return shutil.which("ffmpeg")


def _print_ffmpeg_help(log: Callable[[str], None]) -> None:
    """Provide platform-specific guidance for installing ffmpeg binaries."""

    log("Error: ffmpeg is required to convert downloads to mp3.")
    log(
        "Install a system ffmpeg binary (pip packages alone are not enough). "
        "Examples:"
    )
    log("  Windows: `choco install ffmpeg` or download from https://www.gyan.dev/ffmpeg/builds/")
    log("  macOS:   `brew install ffmpeg`")
    log("  Linux:   `sudo apt-get install ffmpeg` or use your distro's package manager")


def _parse_urls(raw: str) -> Set[str]:
    """Parse whitespace/comma separated URLs from the raw text entry."""

    cleaned = raw.replace(",", " ").replace("\n", " ")
    return {token for token in cleaned.split() if token}


def download_links(urls: Iterable[str], log: Callable[[str], None] = print) -> None:
    """Download each URL with yt-dlp, reporting per-link success/failure."""
    if not shutil.which("yt-dlp"):
        log("Error: yt-dlp is not installed or not on your PATH.")
        return

    ffmpeg_path = _ffmpeg_installed()
    if not ffmpeg_path:
        _print_ffmpeg_help(log)
        return

    log(f"Using ffmpeg at: {ffmpeg_path}")

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
        log(f"Downloading: {url}")
        for args in (common_args, fallback_args):
            try:
                result = subprocess.run(["yt-dlp", *args, url])
            except FileNotFoundError:
                log("yt-dlp executable not found. Aborting remaining downloads.")
                return

            if result.returncode == 0:
                log(f"Downloaded successfully: {url}")
                break

            log(
                "Download failed with this client selection. "
                "Retrying with an alternate player client..."
            )
        else:
            log(f"Download failed (exit code {result.returncode}): {url}")


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


def _cli_prompt_and_download() -> None:
    """Fallback console prompt when Tkinter is unavailable."""

    print("Tkinter GUI unavailable; running in console mode.")
    raw = input("Enter one or more YouTube URLs (comma or space separated): ").strip()
    urls = _parse_urls(raw)

    if not urls:
        print("No URLs provided. Exiting.")
        return

    download_links(sorted(urls))


# ─── GUI ──────────────────────────────────────────────────────────────────────

def _build_gui_and_run() -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ModuleNotFoundError as exc:  # noqa: PERF203
        raise RuntimeError(
            "Tkinter is not available in this Python installation. "
            "Reinstall Python with Tk support or use the CLI fallback."
        ) from exc

    root = tk.Tk()
    root.title("YouTube Downloader")
    root.geometry("620x380")
    root.resizable(False, False)

    url_label = tk.Label(root, text="Paste the YouTube URL(s):")
    url_label.pack(anchor="w", padx=12, pady=(12, 4))

    url_var = tk.StringVar()
    url_entry = tk.Entry(root, textvariable=url_var, width=70)
    url_entry.pack(fill="x", padx=12)
    url_entry.focus_set()

    status_var = tk.StringVar(value="Ready")
    status_label = tk.Label(root, textvariable=status_var, anchor="w")
    status_label.pack(fill="x", padx=12, pady=(6, 0))

    log_path = Path(__file__).resolve().parent / "yt_error_log.txt"

    def open_log_file() -> None:
        try:
            log_path.touch(exist_ok=True)
            webbrowser.open_new_tab(log_path.resolve().as_uri())
        except BaseException as exc:  # noqa: BLE001
            append_output(f"Unable to open log file: {exc}")

    output = tk.Text(root, height=12, state="disabled", wrap="word")
    output.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    download_thread: Optional[threading.Thread] = None

    def append_output(message: str) -> None:
        def _append() -> None:
            output.configure(state="normal")
            output.insert("end", message + "\n")
            output.see("end")
            output.configure(state="disabled")

        root.after(0, _append)

    def update_status(message: str) -> None:
        root.after(0, lambda: status_var.set(message))

    def log(message: str) -> None:
        print(message)
        append_output(message)

    def run_downloads(urls: Set[str]) -> None:
        update_status("Downloading...")
        try:
            download_links(sorted(urls), log=log)
            update_status("Finished downloads.")
        except BaseException as exc:  # noqa: BLE001
            append_output(f"Error: {exc}")
            update_status("An error occurred. Check the log above.")
        finally:
            root.after(0, lambda: download_button.configure(state="normal"))

    def on_download_clicked() -> None:
        nonlocal download_thread

        urls = _parse_urls(url_var.get())
        if not urls:
            messagebox.showerror("No URLs", "Please enter at least one YouTube URL.")
            return

        if download_thread and download_thread.is_alive():
            messagebox.showinfo("Download in progress", "Please wait for the current download to finish.")
            return

        download_button.configure(state="disabled")
        append_output("Starting download...")
        download_thread = threading.Thread(target=run_downloads, args=(urls,), daemon=True)
        download_thread.start()

    controls = tk.Frame(root)
    controls.pack(fill="x", padx=12, pady=(0, 8))

    download_button = tk.Button(controls, text="Download", command=on_download_clicked)
    download_button.pack(side="left")

    open_log_button = tk.Button(controls, text="Open Error Log", command=open_log_file)
    open_log_button.pack(side="left", padx=(8, 0))

    root.mainloop()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        _build_gui_and_run()
    except RuntimeError as exc:
        log_path = _log_startup_error(exc)
        print(
            "Tkinter is missing in this Python environment. "
            "Switching to CLI mode...",
            file=sys.stderr,
        )
        print(f"Details logged to: {log_path}", file=sys.stderr)
        _cli_prompt_and_download()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001
        log_path = _log_startup_error(exc)
        print(f"An error occurred. Details have been logged to: {log_path}")
        sys.exit(1)
