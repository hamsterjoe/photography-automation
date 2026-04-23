"""
test_workflow.py
─────────────────
Generates synthetic test data (real JPEGs with EXIF timestamps and black frames)
and runs the full detection + sorting + watermark pipeline WITHOUT Google Drive or HDD.

Run this BEFORE using the real workflow to verify everything works on your machine.

Usage:
    python test_workflow.py
"""

import os
import sys
import shutil
import random
from pathlib import Path
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import piexif
import struct

# ── Point at the workflow module next to this file ──
sys.path.insert(0, str(Path(__file__).parent))
import workflow as wf

# ─────────────────────────────────────────────
#  TEST CONFIGURATION
# ─────────────────────────────────────────────

TEST_DIR = Path("test_session")
NUM_CONTESTANTS = 3
PHOTOS_PER_CONTESTANT = (5, 10)   # random range
BLACK_FRAMES_BETWEEN = 2           # how many black frames to insert as marker
BASE_TIME = datetime(2024, 6, 15, 9, 0, 0)
BETWEEN_SHOT_SECONDS = 3           # seconds between shots within a contestant
INTERMISSION_SECONDS = 120         # gap during intermission (should trigger detection)

# Override config so test doesn't touch real paths
wf.CONFIG.update({
    "session_root": str(TEST_DIR / "output"),
    "watermark_path": str(TEST_DIR / "watermark.png"),
    "hdd_backup_path": str(TEST_DIR / "hdd_backup"),
    "timestamp_gap_seconds": 90,
    "black_brightness_threshold": 20,
    "black_frame_count": 2,
    "resize_max_px": 800,
    "jpeg_quality": 80,
    "watermark_opacity": 0.4,
    "watermark_position": "bottom-right",
    "watermark_margin": 20,
})


def make_exif_bytes(dt: datetime) -> bytes:
    """Create minimal EXIF blob with DateTimeOriginal."""
    dt_str = dt.strftime("%Y:%m:%d %H:%M:%S").encode("utf-8")
    exif_dict = {
        "0th": {},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: dt_str},
        "GPS": {},
        "1st": {},
    }
    return piexif.dump(exif_dict)


def create_test_photo(path: Path, dt: datetime, label: str, color: tuple):
    """Create a small coloured JPEG with EXIF timestamp and a label burned in."""
    img = Image.new("RGB", (400, 300), color=color)
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), label, fill=(255, 255, 255))
    draw.text((10, 30), dt.strftime("%H:%M:%S"), fill=(220, 220, 220))
    exif_bytes = make_exif_bytes(dt)
    img.save(str(path), "JPEG", exif=exif_bytes, quality=85)


def create_black_frame(path: Path, dt: datetime):
    """Create a pure black JPEG (lens cap shot)."""
    img = Image.new("RGB", (400, 300), color=(4, 4, 4))
    exif_bytes = make_exif_bytes(dt)
    img.save(str(path), "JPEG", exif=exif_bytes, quality=85)


def create_watermark(path: Path):
    """Create a simple test watermark PNG."""
    img = Image.new("RGBA", (300, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 299, 79], fill=(0, 0, 0, 160))
    draw.text((10, 20), "FOR VIEWING ONLY", fill=(255, 255, 255, 230))
    draw.text((10, 45), "© YourCompany", fill=(200, 200, 200, 180))
    img.save(str(path), "PNG")


def generate_test_files(flat_dir: Path) -> list[Path]:
    """
    Generate a realistic sequence of JPEGs:
      [contestant 1 photos] [black frames] [contestant 2 photos] [black frames] ...
    """
    flat_dir.mkdir(parents=True, exist_ok=True)
    files = []
    current_time = BASE_TIME
    file_counter = 1
    contestant_colors = [
        (180, 60, 60),    # red-ish for contestant 1
        (60, 120, 180),   # blue-ish for contestant 2
        (60, 160, 80),    # green-ish for contestant 3
    ]

    print(f"\n  Generating test photos for {NUM_CONTESTANTS} contestants...")

    for c_idx in range(NUM_CONTESTANTS):
        n_photos = random.randint(*PHOTOS_PER_CONTESTANT)
        color = contestant_colors[c_idx % len(contestant_colors)]

        print(f"    Contestant {c_idx + 1}: {n_photos} photos", end="")

        for _ in range(n_photos):
            fname = f"YKZ_{file_counter:04d}.JPG"
            fpath = flat_dir / fname
            create_test_photo(
                fpath, current_time,
                f"Contestant {c_idx + 1} - {fname}",
                color
            )
            files.append(fpath)
            file_counter += 1
            current_time += timedelta(seconds=BETWEEN_SHOT_SECONDS)

        print(f"  ({files[-n_photos].name} → {files[-1].name})")

        # Insert intermission + black frames (except after last contestant)
        if c_idx < NUM_CONTESTANTS - 1:
            current_time += timedelta(seconds=INTERMISSION_SECONDS)
            for b in range(BLACK_FRAMES_BETWEEN):
                fname = f"YKZ_{file_counter:04d}.JPG"
                fpath = flat_dir / fname
                create_black_frame(fpath, current_time)
                files.append(fpath)
                file_counter += 1
                current_time += timedelta(seconds=2)
            print(f"    → {BLACK_FRAMES_BETWEEN} black frames inserted (boundary marker)")

    print(f"\n  Total files generated: {len(files)}")
    return sorted(files)


def run_test():
    print("\n" + "═" * 60)
    print("  WORKFLOW TEST RUNNER")
    print("═" * 60)

    # Clean up previous test run
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir()

    # Create test watermark
    wm_path = Path(wf.CONFIG["watermark_path"])
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    create_watermark(wm_path)
    print(f"\n  ✓ Watermark created: {wm_path}")

    # Create output session folder
    session_dir = wf.create_session_folder()

    # Generate test files
    flat_dir = session_dir / "originals" / "_all_files"
    all_files = generate_test_files(flat_dir)

    # ── Test boundary detection ──
    print("\n" + "─" * 60)
    print("  STEP 1: Hybrid boundary detection")
    print("─" * 60)
    boundaries = wf.detect_boundaries(all_files)

    expected_boundaries = NUM_CONTESTANTS - 1
    print(f"\n  Expected boundaries : {expected_boundaries}")
    print(f"  Detected boundaries : {len(boundaries)}")

    if len(boundaries) == expected_boundaries:
        print("  ✓ PASS — correct number of boundaries detected")
    else:
        print("  ✗ FAIL — boundary count mismatch")
        print("    Tip: Check black_frame_count and timestamp_gap_seconds in CONFIG")

    # Show split preview (auto-accept in test mode)
    print("\n  Auto-accepting detected splits for test...")
    split_points = [0] + boundaries + [len(all_files)]
    for i in range(len(split_points) - 1):
        s, e = split_points[i], split_points[i + 1] - 1
        print(f"    Contestant {i + 1}: {all_files[s].name} → {all_files[e].name}  ({e - s + 1} files)")

    # ── Test sorting ──
    print("\n" + "─" * 60)
    print("  STEP 2: Sort into contestant folders")
    print("─" * 60)
    contestant_folders = wf.sort_into_folders(all_files, boundaries, session_dir)
    print(f"\n  Folders created: {len(contestant_folders)}")
    for folder in contestant_folders:
        count = len(list(folder.glob("*.JPG")))
        print(f"    {folder.name}: {count} photos")

    if len(contestant_folders) == NUM_CONTESTANTS:
        print("  ✓ PASS — correct number of contestant folders")
    else:
        print("  ✗ FAIL — folder count mismatch")

    # ── Test resize + watermark ──
    print("\n" + "─" * 60)
    print("  STEP 3: Resize + watermark")
    print("─" * 60)
    compressed_folders = wf.resize_and_watermark(contestant_folders, session_dir)

    all_passed = True
    for comp_folder in compressed_folders:
        files_out = list(comp_folder.glob("*.jpg"))
        print(f"    {comp_folder.name}: {len(files_out)} compressed files")
        if files_out:
            sample = files_out[0]
            with Image.open(sample) as img:
                max_dim = max(img.size)
                if max_dim <= wf.CONFIG["resize_max_px"]:
                    print(f"      ✓ Size OK: {img.size}")
                else:
                    print(f"      ✗ FAIL: image too large {img.size}")
                    all_passed = False
        else:
            print(f"      ✗ FAIL: no output files")
            all_passed = False

    if all_passed:
        print("  ✓ PASS — all images resized correctly")

    # ── Skip Drive upload in test ──
    print("\n" + "─" * 60)
    print("  STEP 4: Google Drive upload — SKIPPED in test mode")
    print("  (Run workflow.py with real SD card to test upload)")

    # ── Final summary ──
    print("\n" + "═" * 60)
    print("  TEST COMPLETE")
    print("═" * 60)
    print(f"  Output folder: {session_dir.resolve()}")
    print("\n  Open the folder and inspect:")
    print("    originals/_all_files/  — all generated test photos")
    print("    originals/Contestant_01..N/  — sorted originals")
    print("    compressed/Contestant_01..N/  — resized + watermarked")
    print("\n  If all steps show ✓ PASS, your environment is ready.")
    print("  You can now run:  python workflow.py\n")


if __name__ == "__main__":
    run_test()
