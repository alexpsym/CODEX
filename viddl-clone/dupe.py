#!/usr/bin/env python3
"""
remove_dupe_images.py

Scan a folder for duplicate images and delete all but one copy in each duplicate set,
showing live progress bars and completion percentages.

Criteria for “duplicate”:
  • Exact same pixel dimensions (width × height)
  • Exact same file size in bytes
  • Filename similarity ≥ threshold (default 0.6)

Usage:
    python remove_dupe_images.py /path/to/folder [--threshold 0.6] [--dry-run]

Options:
  folder            Path to the directory containing your images.
  --threshold, -t   Filename similarity threshold between 0 and 1 (default 0.6).
  --dry-run,  -d    Show what would be deleted without actually removing files.
"""

import os
import sys
import argparse
import logging
from difflib import SequenceMatcher

from PIL import Image
from tqdm import tqdm

# --- Setup logging ---
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# --- Helpers ---

def get_image_info(path):
    """
    Returns (width, height, filesize) for an image file,
    or None if the file can't be opened as an image.
    """
    try:
        size = os.path.getsize(path)
        with Image.open(path) as img:
            return img.width, img.height, size
    except Exception as e:
        logger.warning(f"Skipping non-image/unreadable: {os.path.basename(path)} ({e})")
        return None

def filename_similarity(a, b):
    """
    Compute a ratio [0–1] of how similar two filenames are (without extensions).
    """
    n1 = os.path.splitext(os.path.basename(a))[0]
    n2 = os.path.splitext(os.path.basename(b))[0]
    return SequenceMatcher(None, n1, n2).ratio()

def cluster_by_name(files, threshold):
    """
    Cluster files so that any two in a cluster have filename_similarity ≥ threshold (transitively).
    Returns a list of sets (clusters).
    """
    clusters = []
    unvisited = set(files)
    while unvisited:
        seed = unvisited.pop()
        group = {seed}
        changed = True
        while changed:
            changed = False
            for other in list(unvisited):
                if any(filename_similarity(other, member) >= threshold for member in group):
                    unvisited.remove(other)
                    group.add(other)
                    changed = True
        clusters.append(group)
    return clusters

# --- Main Logic ---

def main():
    p = argparse.ArgumentParser(
        description="Delete duplicate images based on dimensions, filesize, and filename similarity"
    )
    p.add_argument("folder", help="Directory containing images to scan")
    p.add_argument(
        "--threshold", "-t",
        type=float,
        default=0.6,
        help="Filename similarity threshold (0–1). Default: 0.6"
    )
    p.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Show duplicates without deleting"
    )
    args = p.parse_args()

    folder = args.folder
    if not os.path.isdir(folder):
        logger.error(f"Not a directory: {folder}")
        sys.exit(1)

    # Gather image files
    exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
    all_files = [
        os.path.join(folder, f) for f in os.listdir(folder)
        if os.path.splitext(f.lower())[1] in exts
    ]
    if not all_files:
        logger.info("No image files found.")
        return

    # 1) Scan & group by (w, h, filesize)
    info_map = {}
    logger.info("Scanning images for dimensions & size...")
    for path in tqdm(all_files, desc="Scanning", unit="file"):
        info = get_image_info(path)
        if info:
            info_map.setdefault(info, []).append(path)

    # 2) Build deletion job list
    jobs = []  # list of (keep_path, delete_path)
    for sig, files in info_map.items():
        if len(files) < 2:
            continue
        clusters = cluster_by_name(files, args.threshold)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            sorted_files = sorted(cluster)
            keep = sorted_files[0]
            for dup in sorted_files[1:]:
                jobs.append((keep, dup))

    if not jobs:
        logger.info("No duplicates detected.")
        return

    # 3) Delete duplicates with progress bar
    logger.info(f"\nFound {len(jobs)} duplicates to remove:")
    deleted = 0
    for keep, dup in tqdm(jobs, desc="Deleting", unit="file"):
        logger.info(f" KEEP: {os.path.basename(keep)}   DELETE: {os.path.basename(dup)}")
        if not args.dry_run:
            try:
                os.remove(dup)
                deleted += 1
            except Exception as e:
                logger.error(f"  Error deleting {dup}: {e}")

    # 4) Summary
    if args.dry_run:
        logger.info(f"\nDry-run mode: no files were deleted.")
    else:
        logger.info(f"\nDeletion complete: {deleted}/{len(jobs)} files removed.")

if __name__ == "__main__":
    main()
