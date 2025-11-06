#!/usr/bin/env python3
"""
master_download_videos.py

Interactive tool to:
 1) Extract album URLs from gallery pages (with optional infinite scrolling)
    and then download *one* video per unique base-name (preferring mp4) from each album, or
 2) Download *one* video per unique base-name (preferring mp4) from one or more specified album URLs.

Features:
 • Groups multiple formats of the same clip and downloads exactly one.
 • Preference order: mp4 → avi → mov → mkv → mpg → mpeg → flv → webm → ogv.
 • Never saves more than one copy of the same filename.
 • Multiple album URLs accepted in option 2.

Dependencies:
    pip install requests beautifulsoup4 selenium
Requires ChromeDriver on your PATH for Selenium mode.
"""

import os
import re
import sys
import time
import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException


# ─── CONFIG & LOGGING ──────────────────────────────────────────────────────────

script_dir       = os.path.dirname(os.path.abspath(__file__))
videos_dir       = os.path.join(script_dir, 'videos')
os.makedirs(videos_dir, exist_ok=True)

downloaded_names = set()  # track saved base-filenames

# Preferred video extensions in priority order
PREFERRED = ['.mp4', '.avi', '.mov', '.mkv', '.mpg', '.mpeg', '.flv', '.webm', '.ogv']

# Logging setup
log_file = os.path.join(script_dir, 'master_download_videos.log')
logger   = logging.getLogger()
logger.setLevel(logging.INFO)
fmt       = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                              datefmt='%Y-%m-%d %H:%M:%S')

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)

fh = logging.FileHandler(log_file)
fh.setLevel(logging.ERROR)
fh.setFormatter(fmt)
logger.addHandler(fh)


# ─── SELENIUM SCROLLING ─────────────────────────────────────────────────────────

def fetch_page_source(url, scroll_pause=2.0, max_scrolls=50):
    opts = Options()
    opts.add_argument('--headless')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')
    try:
        driver = webdriver.Chrome(options=opts)
    except WebDriverException as e:
        logger.error(f"ChromeDriver start failed: {e}")
        sys.exit(1)

    driver.get(url)
    time.sleep(scroll_pause)
    last_h = driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h

    html = driver.page_source
    driver.quit()
    return html


# ─── ALBUM URL EXTRACTION ──────────────────────────────────────────────────────

def extract_album_urls(page_url, use_selenium=False):
    """
    Finds album-page URLs by:
      • any <a> wrapping an <img>
      • any <a> whose parent/sibling has an <img>
      • any <a> whose href contains “album”
    """
    found = set()
    try:
        if use_selenium:
            logger.info("Loading with Selenium + scrolling…")
            html = fetch_page_source(page_url)
        else:
            resp = requests.get(page_url, timeout=15)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        logger.exception(f"Failed to fetch {page_url}")
        return found

    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        full = urljoin(page_url, href)
        low  = href.lower()

        if a.find('img'):
            found.add(full); continue
        if a.parent and a.parent.find('img'):
            found.add(full); continue
        if 'album' in low:
            found.add(full)
    return found


# ─── VIDEO URL EXTRACTION & FILTERING ──────────────────────────────────────────

def get_all_video_urls(page_url):
    """
    Collects every candidate video URL on the page:
     • <video src="…">
     • <video><source src="…">
     • <a href="…ext">
    """
    urls = set()
    try:
        resp = requests.get(page_url, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        logger.exception(f"Cannot fetch page: {page_url}")
        return urls

    soup = BeautifulSoup(html, 'html.parser')
    exts = tuple(PREFERRED)

    # 1) <video src="…ext">
    for vid in soup.find_all('video', src=True):
        src = vid['src']
        if src.lower().endswith(exts):
            urls.add(urljoin(page_url, src))

    # 2) <video><source src="…ext">
    for src_tag in soup.select('video source[src]'):
        src = src_tag['src']
        if src.lower().endswith(exts):
            urls.add(urljoin(page_url, src))

    # 3) <a href="…ext">
    for a in soup.find_all('a', href=True):
        href = a['href'].split('?', 1)[0]
        if href.lower().endswith(exts):
            urls.add(urljoin(page_url, a['href']))

    return urls

def select_preferred(urls):
    """
    Group URLs by basename (no extension), then pick one per group
    according to PREFERRED order.
    """
    groups = {}
    for u in urls:
        base = os.path.splitext(os.path.basename(urlparse(u).path))[0]
        groups.setdefault(base, []).append(u)

    selected = []
    for base, lst in groups.items():
        # Determine preference index for each URL
        def pref_index(url):
            ext = os.path.splitext(urlparse(url).path)[1].lower()
            return next((i for i, e in enumerate(PREFERRED) if e == ext), len(PREFERRED))

        # Sort by that index and pick the first
        chosen = sorted(lst, key=pref_index)[0]
        selected.append(chosen)
    return selected


# ─── DOWNLOAD HELPERS ──────────────────────────────────────────────────────────

def sanitize_fname(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', name)

def download_video(url, outdir):
    """
    Downloads the URL, skipping if its basename already exists.
    """
    fname = os.path.basename(urlparse(url).path)
    fname = sanitize_fname(fname)
    path = os.path.join(outdir, fname)

    if fname in downloaded_names or os.path.exists(path):
        logger.info(f"Skipping duplicate: {fname}")
        return

    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()
    except Exception:
        logger.exception(f"Failed to download: {url}")
        return

    downloaded_names.add(fname)
    try:
        with open(path, 'wb') as f:
            for chunk in r.iter_content(64_000):
                f.write(chunk)
        logger.info(f"Saved: {path}")
    except Exception:
        logger.exception(f"Failed saving: {path}")

def download_videos_from_page(page_url, outdir):
    all_urls = get_all_video_urls(page_url)
    sel      = select_preferred(all_urls)
    logger.info(f"  Found {len(all_urls)} candidates, selecting {len(sel)} to download")
    for vid in sel:
        download_video(vid, outdir)


# ─── INTERACTIVE MENU ──────────────────────────────────────────────────────────

def prompt_menu():
    print("\n*** Master Video Downloader ***")
    print("1) Download Albums (discover & download)")
    print("2) Download Single Album(s)")
    return input("Choose 1 or 2: ").strip()

def prompt_urls(prompt):
    print(prompt)
    print("Enter one URL per line; blank line to finish:")
    L = []
    while True:
        ln = input().strip()
        if not ln:
            break
        L.append(ln)
    return L

def main():
    mode = None
    while mode not in ('1','2'):
        mode = prompt_menu()

    os.makedirs(videos_dir, exist_ok=True)

    if mode == '1':
        use_s = input("Use Selenium scrolling? (y/N): ").strip().lower() == 'y'
        galleries = prompt_urls("Enter GALLERY page URLs:")
        albums    = set()
        for g in galleries:
            logger.info(f"Scanning for albums on {g}")
            albums.update(extract_album_urls(g, use_selenium=use_s))
        if not albums:
            logger.warning("No albums found; exiting.")
        else:
            print(f"\nDownloading from {len(albums)} album(s)…")
            for alb in sorted(albums):
                logger.info(f"Album: {alb}")
                download_videos_from_page(alb, videos_dir)

    else:
        albums = prompt_urls("Enter ALBUM page URLs:")
        print(f"\nDownloading from {len(albums)} album(s)…")
        for alb in albums:
            logger.info(f"Album: {alb}")
            download_videos_from_page(alb, videos_dir)

    print(f"\nDone! Videos saved in:\n  {videos_dir}")
    input("Press ENTER to exit.")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logger.exception("Unexpected error")
        input("Press ENTER to exit.")
        sys.exit(1)
