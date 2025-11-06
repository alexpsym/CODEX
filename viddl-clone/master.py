#!/usr/bin/env python3
"""
master_download.py

Interactive tool to:
 1) Extract album URLs from gallery pages (with optional infinite scrolling)
    and then download *all* images from those albums, or
 2) Download all images from one or more specified album URLs.

Features:
 • Never saves more than one copy of the same image filename.
 • Skips any image under 50 KB **and** whose max(width, height) ≤ 150 px.
 • Multiple album URLs accepted in option 2.

Dependencies:
    pip install requests beautifulsoup4 selenium pillow
Requires ChromeDriver on your PATH for Selenium mode.
"""

import os, re, sys, io, time, logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

# ─── SETUP ──────────────────────────────────────────────────────────────────────

script_dir   = os.path.dirname(os.path.abspath(__file__))
images_dir   = os.path.join(script_dir, 'images')
os.makedirs(images_dir, exist_ok=True)

# Keep track of saved base-filenames
downloaded_names = set()

# Logging
log_file = os.path.join(script_dir, 'master_download.log')
logger   = logging.getLogger()
logger.setLevel(logging.INFO)
fmt       = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                              datefmt='%Y-%m-%d %H:%M:%S')
# Console
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)
# File (errors only)
fh = logging.FileHandler(log_file)
fh.setLevel(logging.ERROR)
fh.setFormatter(fmt)
logger.addHandler(fh)


# ─── SCROLLING FETCH ────────────────────────────────────────────────────────────

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

        # link wrapping <img>
        if a.find('img'):
            found.add(full); continue
        # parent of link has <img>
        if a.parent and a.parent.find('img'):
            found.add(full); continue
        # keyword “album”
        if 'album' in low:
            found.add(full)
    return found


# ─── IMAGE-DOWNLOAD HELPERS ────────────────────────────────────────────────────

def parse_srcset(srcset):
    candidates = []
    for part in srcset.split(','):
        urlp, *desc = part.strip().split()
        w = 0
        if desc and desc[0].endswith('w'):
            try: w = int(desc[0][:-1])
            except: pass
        candidates.append((w, urlp))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None

def get_best_image_url(img, base):
    # 1) linked <a>
    p = img.find_parent('a', href=True)
    if p:
        href = p['href']
        if re.search(r'\.(jpe?g|png|gif|bmp|webp)(\?|$)', href, re.I):
            return urljoin(base, href)
    # 2) srcset
    ss = img.get('srcset')
    if ss:
        best = parse_srcset(ss)
        if best:
            return urljoin(base, best)
    # 3) data- attributes
    for attr in ('data-src','data-original','data-full','data-large'):
        if img.get(attr):
            return urljoin(base, img[attr])
    # 4) fallback src
    src = img.get('src') or img.get('data-src')
    return urljoin(base, src) if src else None

def sanitize_fname(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', name)

def download_image(url, outdir):
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.content
    except Exception:
        logger.exception(f"Failed to download: {url}")
        return

    # thumbnail filter: skip if <50KB AND max dim ≤150px
    if len(data) < 50*1024:
        try:
            img = Image.open(io.BytesIO(data))
            w, h = img.size
            if max(w, h) <= 150:
                logger.info(f"Skipping thumbnail-like: {url}")
                return
        except Exception:
            logger.info(f"Skipping small file (<50KB): {url}")
            return

    # derive a base‐filename (with extension)
    base = os.path.basename(urlparse(url).path) or 'image'
    name = sanitize_fname(base)

    # SKIP if we've saved this name before or if it already exists on disk
    filepath = os.path.join(outdir, name)
    if name in downloaded_names or os.path.exists(filepath):
        logger.info(f"Skipping duplicate: {name}")
        return

    # mark as saved and write to disk
    downloaded_names.add(name)
    try:
        with open(filepath, 'wb') as f:
            f.write(data)
        logger.info(f"Saved: {filepath}")
    except Exception:
        logger.exception(f"Failed saving: {filepath}")

def download_images_from_page(page_url, outdir):
    try:
        resp = requests.get(page_url, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        logger.exception(f"Cannot fetch album page: {page_url}")
        return

    soup = BeautifulSoup(html, 'html.parser')
    imgs = soup.find_all('img')
    logger.info(f"{len(imgs)} <img> tags on {page_url}")
    for img in imgs:
        best = get_best_image_url(img, page_url)
        if best:
            download_image(best, outdir)


# ─── INTERACTIVE MENU ──────────────────────────────────────────────────────────

def prompt_menu():
    print("\n*** Master Image Downloader ***")
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

    os.makedirs(images_dir, exist_ok=True)

    if mode == '1':
        use_s = input("Use Selenium scrolling? (y/N): ").strip().lower() == 'y'
        pages = prompt_urls("Enter GALLERY page URLs:")
        albums = set()
        for p in pages:
            logger.info(f"Scanning: {p}")
            albums.update(extract_album_urls(p, use_selenium=use_s))
        if not albums:
            logger.warning("No albums found; exiting.")
        else:
            print(f"\nDownloading from {len(albums)} albums…")
            for alb in sorted(albums):
                logger.info(f"Album: {alb}")
                download_images_from_page(alb, images_dir)

    else:
        albums = prompt_urls("Enter ALBUM page URLs:")
        print(f"\nDownloading from {len(albums)} album(s)…")
        for alb in albums:
            logger.info(f"Album: {alb}")
            download_images_from_page(alb, images_dir)

    print(f"\nDone! Images saved in:\n  {images_dir}")
    input("Press ENTER to exit.")

if __name__ == '__main__':
    try:
        main()
    except Exception:
        logger.exception("Unexpected error")
        input("Press ENTER to exit.")
        sys.exit(1)
