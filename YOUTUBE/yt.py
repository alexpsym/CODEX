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
from urllib.parse import parse_qs, urlparse
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
    parsed: Set[str] = set()

    for token in cleaned.split():
        normalized = _normalize_url(token)
        if normalized:
            parsed.add(normalized)

    return parsed


def _normalize_url(raw_url: str) -> str:
    """Normalize common YouTube share URLs into canonical watch links."""

    url = raw_url.strip()
    if not url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        return url

    netloc = parsed.netloc.lower().replace("www.", "")

    if netloc == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        return url

    if "youtube.com" in netloc:
        query = parse_qs(parsed.query)
        video_id = query.get("v", [""])[0]
        if video_id:
            suffix = ""
            playlist = query.get("list", [""])[0]
            if playlist:
                suffix = f"&list={playlist}"
                index = query.get("index", [""])[0]
                if index:
                    suffix += f"&index={index}"
            return f"https://www.youtube.com/watch?v={video_id}{suffix}"

    return url


def _cookies_args(cookies_path: Optional[str], log: Callable[[str], None]) -> list[str]:
    """Return yt-dlp cookie arguments when a file is available."""

    if not cookies_path:
        log(
            "No cookies configured. If YouTube requests sign-in, set YTDLP_COOKIES_B64 "
            "(base64 cookies.txt) or upload cookies via the downloader UI."
        )
        return []

    path = Path(cookies_path)
    if not path.exists():
        log(f"Cookies file not found at: {path}. Proceeding without cookies.")
        return []

    log(f"Using cookies file at: {path}")
    return ["--cookies", str(path)]


def _parse_progress_line(line: str) -> Optional[dict]:
    """Parse a progress template line into structured fields.

    Expected format: ``progress:<percent>|<downloaded>|<total>|<speed>|<eta>``
    """

    if not line.startswith("progress:"):
        return None

    payload = line.split("progress:", 1)[1]
    parts = payload.split("|")
    if len(parts) != 5:
        return None

    percent_raw, downloaded_raw, total_raw, speed_raw, eta_raw = parts
    try:
        percent = float(percent_raw.strip().rstrip("%"))
    except ValueError:
        percent = None

    def _int_or_none(value: str) -> Optional[int]:
        try:
            return int(value)
        except ValueError:
            return None

    return {
        "percent": percent,
        "downloaded_bytes": _int_or_none(downloaded_raw.strip()),
        "total_bytes": _int_or_none(total_raw.strip()),
        "speed": speed_raw.strip() or None,
        "eta": eta_raw.strip() or None,
    }


def _run_yt_dlp(args: list[str], url: str, log: Callable[[str], None], progress_cb: Optional[Callable[[dict], None]], output_root: Path) -> tuple[int, list[Path]]:
    """Run yt-dlp once, streaming stdout lines to ``log`` and parsing progress."""

    file_candidates: list[Path] = []

    try:
        proc = subprocess.Popen(
            [*args, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        log("yt-dlp executable not found. Aborting remaining downloads.")
        return 127, file_candidates

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        progress = _parse_progress_line(line)
        if progress and progress_cb:
            progress_cb(progress)
            continue

        log(line)
        candidate = line.strip()
        if candidate:
            try:
                resolved = Path(candidate).resolve()
            except Exception:  # noqa: BLE001 - defensive parsing only
                continue

            if resolved.suffix and output_root in resolved.parents and resolved not in file_candidates:
                file_candidates.append(resolved)

    proc.wait()
    return proc.returncode, file_candidates


def download_links(
    urls: Iterable[str],
    log: Callable[[str], None] = print,
    cookies_path: Optional[str] = None,
    output_dir: Optional[Path] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> list[Path]:
    """Download each URL with yt-dlp, reporting per-link success/failure."""
    if not shutil.which("yt-dlp"):
        log("Error: yt-dlp is not installed or not on your PATH.")
        return []

    output_root = (output_dir or Path.cwd() / "youtube_downloads").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = _ffmpeg_installed()
    if not ffmpeg_path:
        _print_ffmpeg_help(log)
        return []

    log(f"Using ffmpeg at: {ffmpeg_path}")

    try:
        version_output = subprocess.run(
            ["yt-dlp", "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        version_line = version_output.stdout.strip()
        if version_line:
            log(f"yt-dlp version: {version_line}")
    except Exception as exc:  # noqa: BLE001 - logging only
        log(f"Unable to determine yt-dlp version: {exc}")

    base_args = [
        "yt-dlp",
        "--newline",
        "--progress-template",
        "progress:%(progress._percent_str)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "-f",
        "bestaudio/best",
        "--extractor-args",
        "youtube:player_client=android,ios,web,player_skip=configs",
        "--force-ipv4",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--paths",
        str(output_root),
        "-o",
        "%(title).200B.%(ext)s",
        "--print",
        "after_move:filepath",
    ]

    base_args.extend(_cookies_args(cookies_path, log))

    fallback_args = base_args.copy()
    extractor_args_index = fallback_args.index("--extractor-args") + 1
    fallback_args[extractor_args_index] = "youtube:player_client=all,player_skip=configs"

    downloaded: list[Path] = []

    for raw_url in urls:
        url = _normalize_url(raw_url)
        log(f"Downloading: {url}")

        for args in (base_args, fallback_args):
            return_code, file_candidates = _run_yt_dlp(
                args=args,
                url=url,
                log=log,
                progress_cb=progress_cb,
                output_root=output_root,
            )

            if return_code == 0:
                log(f"Downloaded successfully: {url}")
                if file_candidates:
                    downloaded.append(file_candidates[-1])
                    log(f"Saved to: {file_candidates[-1]}")
                break

            log(
                "Download failed with this client selection. "
                "Retrying with an alternate player client..."
            )
        else:
            log(f"Download failed (exit code {return_code}): {url}")

    return downloaded


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
