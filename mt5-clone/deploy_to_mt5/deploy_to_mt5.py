import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# ----------------------------
# User-configurable defaults
# ----------------------------

# Your MT5 Data Folder (Terminal ID folder)
DEFAULT_MT5_DATA = r"C:\Users\User\AppData\Roaming\MetaQuotes\Terminal\73B7A2420D6397DFF9014A20F1201F97"

# Source folder in your repo clone
# This script is intended to live at: ...\CODEX\mt5-clone\deploy_to_mt5\deploy_to_mt5.py
# So MQL5 is one level up: ...\CODEX\mt5-clone\MQL5
DEFAULT_SRC_MQL5_REL = r"..\MQL5"

# What to deploy (entire MQL5 tree)
DEPLOY_ROOT_NAME = "MQL5"

# ----------------------------
# Utility
# ----------------------------

def eprint(*args):
    print(*args, file=sys.stderr)

def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False

def safe_copy_file(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    # copy2 preserves timestamps
    shutil.copy2(str(src), str(dst))

def compute_target_paths(mt5_data: Path) -> dict:
    """
    Returns expected MT5 subtree targets.
    We also "scan" by verifying these exist (or creating them as needed).
    """
    # Standard MT5 data folder contains MQL5\Experts, MQL5\Presets, MQL5\Profiles\Tester etc.
    targets = {
        "MQL5": mt5_data / "MQL5",
    }
    return targets

def list_all_files(root: Path) -> list[Path]:
    files = []
    for p in root.rglob("*"):
        if p.is_file():
            files.append(p)
    return files

def deploy_tree(src_mql5: Path, mt5_data: Path) -> int:
    """
    Copy everything under src_mql5 into mt5_data\\MQL5, replacing existing files.
    Returns number of files copied.
    """
    if not src_mql5.exists() or not src_mql5.is_dir():
        raise FileNotFoundError(f"Source MQL5 folder not found: {src_mql5}")

    # Target root
    targets = compute_target_paths(mt5_data)
    dst_mql5 = targets["MQL5"]

    # Safety checks
    if not mt5_data.exists() or not mt5_data.is_dir():
        raise FileNotFoundError(f"MT5 data folder not found: {mt5_data}")

    # Ensure destination MQL5 exists
    dst_mql5.mkdir(parents=True, exist_ok=True)

    # Copy all files from src_mql5 into dst_mql5, preserving relative paths
    src_files = list_all_files(src_mql5)
    copied = 0

    for src_file in src_files:
        rel = src_file.relative_to(src_mql5)
        dst_file = dst_mql5 / rel

        # Extra safety: ensure we never write outside dst_mql5
        if not is_within(dst_file, dst_mql5):
            raise RuntimeError(f"Refusing to write outside destination root: {dst_file}")

        safe_copy_file(src_file, dst_file)
        copied += 1

    return copied

def main():
    script_dir = Path(__file__).resolve().parent
    src_mql5 = (script_dir / DEFAULT_SRC_MQL5_REL).resolve()

    # Allow override via CLI:
    # deploy_to_mt5.py [optional_mt5_data_folder]
    if len(sys.argv) >= 2:
        mt5_data = Path(sys.argv[1]).expanduser().resolve()
    else:
        mt5_data = Path(DEFAULT_MT5_DATA).expanduser().resolve()

    print("=== MT5 Deploy (Repo -> Data Folder) ===")
    print(f"Source (repo MQL5): {src_mql5}")
    print(f"Target (MT5 data):  {mt5_data}")

    # Pre-flight: show what we expect to exist
    expected_mql5 = mt5_data / "MQL5"
    if not expected_mql5.exists():
        print(f"Note: Target MQL5 folder does not exist yet; will create: {expected_mql5}")

    try:
        copied = deploy_tree(src_mql5, mt5_data)
    except Exception as ex:
        eprint("\nERROR:", str(ex))
        return 1

    # Also copy EA .set files into MQL5\\Profiles\\Tester (your requirement)
    src_presets = src_mql5 / "Presets"
    dst_tester = mt5_data / "MQL5" / "Profiles" / "Tester"
    if src_presets.exists():
        dst_tester.mkdir(parents=True, exist_ok=True)
        for set_file in src_presets.glob("*.set"):
            safe_copy_file(set_file, dst_tester / set_file.name)

    print(f"\nDone. Files copied/replaced: {copied}")
    print("If MT5 is open, refresh Navigator or restart MT5 to see new/updated Experts/Presets.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
