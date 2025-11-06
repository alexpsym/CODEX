"""Move every file from subfolders into the current folder.

This script walks through each folder inside the folder where it is run and
moves any files it finds into the current folder.  If a file with the same
name already exists, it adds a number to the new file's name to avoid
overwriting anything.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def move_files_to_current_directory() -> None:
    """Move files from subdirectories into the current working directory."""
    root = Path.cwd()

    for folder, _subdirs, files in os.walk(root):
        current_folder = Path(folder)

        # Skip the root folder itself
        if current_folder == root:
            continue

        for name in files:
            source = current_folder / name
            destination = root / name

            if destination.exists():
                stem = destination.stem
                suffix = destination.suffix
                counter = 1
                while (root / f"{stem}_{counter}{suffix}").exists():
                    counter += 1
                destination = root / f"{stem}_{counter}{suffix}"

            shutil.move(str(source), str(destination))


if __name__ == "__main__":
    move_files_to_current_directory()
