from PIL import Image
import os
from concurrent.futures import ThreadPoolExecutor
from config import cfg


def compress_single_image(path, target_kb, quality):
    """Compresses an image only if it is larger than target_kb."""
    if os.path.getsize(path) > target_kb * 1024:
        try:
            img = Image.open(path)
            temp_path = path + ".tmp"
            img.save(temp_path, "JPEG", optimize=True, quality=quality)
            os.replace(temp_path, path)
        except Exception as e:
            print(f"[!] Failed to compress {path}: {e}")


def _worker(args):
    compress_single_image(*args)


def get_jpg_files(directory):
    return [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".jpg")]


def get_total_mb(filepaths):
    return sum(os.path.getsize(f) for f in filepaths) / (1024 * 1024)


def standardize_snapshots(directory=None):
    directory = directory or cfg.SNAPSHOTS_DIR

    if not os.path.exists(directory):
        print(f"[!] Directory '{directory}' not found.")
        return

    filepaths = get_jpg_files(directory)
    if not filepaths:
        print("[i] No snapshots found to compress.")
        return

    total_mb = get_total_mb(filepaths)
    print(f"[*] Initial Total Size: {total_mb:.2f} MB ({len(filepaths)} images)")

    # Pass 1: Standard compression — bring oversized individual files down
    print(f"  [>] Pass 1: Normalising images over {cfg.COMPRESS_DEFAULT_MAX_KB} KB...")
    with ThreadPoolExecutor() as executor:
        executor.map(_worker, [(f, cfg.COMPRESS_DEFAULT_MAX_KB, 80) for f in filepaths])

    total_mb = get_total_mb(filepaths)
    print(f"  [>] After Pass 1: {total_mb:.2f} MB")

    # Pass 2+: Dynamic aggressive compression until folder is under the hard limit
    quality = 70
    pass_number = 2

    while total_mb > cfg.COMPRESS_MAX_TOTAL_MB and quality >= 30:
        print(f"  [!] Total {total_mb:.2f} MB exceeds {cfg.COMPRESS_MAX_TOTAL_MB} MB limit. "
              f"Running aggressive pass {pass_number} (quality={quality})...")

        # Calculate exactly how small the average file needs to be, with 5% margin
        dynamic_target_kb = (cfg.COMPRESS_MAX_TOTAL_MB * 1024) / len(filepaths) * 0.95

        with ThreadPoolExecutor() as executor:
            executor.map(_worker, [(f, dynamic_target_kb, quality) for f in filepaths])

        total_mb = get_total_mb(filepaths)
        print(f"  [>] After Pass {pass_number}: {total_mb:.2f} MB")
        quality -= 10
        pass_number += 1

    print(f"[OK] Compression complete. Final Total Size: {total_mb:.2f} MB")


if __name__ == "__main__":
    standardize_snapshots()
