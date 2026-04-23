"""
Competition Photo Workflow Automation
=====================================
Automates: SD copy → contestant splitting → resize/watermark → Google Drive upload → HDD backup

Requirements (install via pip):
    pip install pillow piexif tqdm rclone-python

External tools needed:
    - rclone  : https://rclone.org/downloads/  (configured with Google Drive)
    - ImageMagick : https://imagemagick.org/script/download.php

Usage:
    python workflow.py
"""

import os
import re
import sys
import shutil
import subprocess
import json
import csv
import logging
from pathlib import Path
from datetime import datetime, timedelta
from PIL import Image
import piexif
from tqdm import tqdm

# ─────────────────────────────────────────────
#  USER CONFIGURATION  ← Edit this section
# ─────────────────────────────────────────────

CONFIG = {
    # Where photos land after copying from SD card
    "session_root": r"C:\CompPhotos",           # Windows example
    # "session_root": "/Users/you/CompPhotos",  # Mac example

    # Your watermark PNG file (full path)
    "watermark_path": r"C:\CompPhotos\watermark.png",

    # External HDD backup root folder
    "hdd_backup_path": r"D:\PhotoBackup",       # Change D:\ to your HDD drive letter

    # rclone remote name (set up via `rclone config`, name it "gdrive")
    "rclone_remote": "gdrive",

    # Google Drive folder where contestant folders will be uploaded
    "gdrive_root_folder": "CompetitionPhotos",

    # ── Detection thresholds ──
    # Minimum seconds gap between photos to consider it an intermission
    "timestamp_gap_seconds": 90,

    # Black frame: average brightness below this = black shot (0–255)
    "black_brightness_threshold": 20,

    # Require this many consecutive black frames to confirm a boundary
    "black_frame_count": 2,

    # Resize longest edge to this many pixels for the compressed copies
    "resize_max_px": 2048,

    # JPEG quality for compressed copies (1–95)
    "jpeg_quality": 85,

    # Watermark opacity (0.0 = invisible, 1.0 = fully opaque)
    "watermark_opacity": 0.35,

    # Watermark position: "bottom-right", "bottom-left", "top-right", "top-left", "center"
    "watermark_position": "bottom-right",

    # Margin from edge in pixels when placing watermark
    "watermark_margin": 30,
}

# ─────────────────────────────────────────────
#  LOGGING SETUP
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("workflow.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  STEP 1 — CREATE SESSION FOLDER
# ─────────────────────────────────────────────

def create_session_folder() -> Path:
    """Create a timestamped folder for this session's originals."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    session_dir = Path(CONFIG["session_root"]) / f"session_{timestamp}"
    originals_dir = session_dir / "originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Session folder created: {session_dir}")
    return session_dir


# ─────────────────────────────────────────────
#  STEP 2 — COPY FROM SD CARD
# ─────────────────────────────────────────────

def find_sd_card() -> Path | None:
    """
    Attempt to auto-detect an SD card by scanning drive letters (Windows)
    or /Volumes (Mac). Returns Path if found, None otherwise.
    """
    if sys.platform == "win32":
        import string
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            dcim = drive / "DCIM"
            if dcim.exists():
                log.info(f"SD card detected at {drive}")
                return drive
    elif sys.platform == "darwin":
        volumes = Path("/Volumes")
        for vol in volumes.iterdir():
            if (vol / "DCIM").exists():
                log.info(f"SD card detected at {vol}")
                return vol
    return None


def copy_from_sd(sd_path: Path, destination: Path) -> list[Path]:
    """
    Recursively copy all JPEG/NEF/RAW files from SD card DCIM folder
    into destination. Returns list of copied file paths.
    """
    extensions = {".jpg", ".jpeg", ".nef", ".raw", ".cr2", ".arw", ".dng"}
    source_files = []
    for ext in extensions:
        source_files.extend((sd_path / "DCIM").rglob(f"*{ext}"))
        source_files.extend((sd_path / "DCIM").rglob(f"*{ext.upper()}"))

    if not source_files:
        log.warning("No image files found on SD card.")
        return []

    source_files.sort()  # Sort by filename (Nikon uses sequential names)
    log.info(f"Found {len(source_files)} files to copy...")

    copied = []
    for src in tqdm(source_files, desc="Copying from SD", unit="file"):
        dest = destination / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        copied.append(dest)

    log.info(f"Copy complete. {len(copied)} files in {destination}")
    return sorted(copied)


# ─────────────────────────────────────────────
#  STEP 3 — HYBRID BOUNDARY DETECTION
# ─────────────────────────────────────────────

def get_exif_datetime(filepath: Path) -> datetime | None:
    """Extract DateTimeOriginal from EXIF data."""
    try:
        exif_data = piexif.load(str(filepath))
        dt_bytes = exif_data["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
        if dt_bytes:
            dt_str = dt_bytes.decode("utf-8")
            return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def is_black_frame(filepath: Path, threshold: int) -> bool:
    """Return True if average pixel brightness is below threshold."""
    try:
        with Image.open(filepath) as img:
            # Resize to tiny thumbnail for speed — we just need average brightness
            thumb = img.convert("L").resize((64, 64), Image.LANCZOS)
            pixels = list(thumb.getdata())
            avg = sum(pixels) / len(pixels)
            return avg < threshold
    except Exception:
        return False


def detect_boundaries(files: list[Path]) -> list[int]:
    """
    Hybrid detection: flag an index as a boundary if BOTH conditions are met:
      1. A run of black frames (>= black_frame_count) appears at or near that index
      2. There is a timestamp gap >= timestamp_gap_seconds nearby

    Returns list of indices (in `files`) where a new contestant begins.
    New contestant starts AFTER the black frame(s).
    """
    gap_threshold = CONFIG["timestamp_gap_seconds"]
    brightness_thresh = CONFIG["black_brightness_threshold"]
    required_black = CONFIG["black_frame_count"]

    log.info("Analysing files for contestant boundaries (hybrid detection)...")

    # ── Pass 1: timestamp gaps ──
    gap_indices = set()
    timestamps = []
    for f in files:
        timestamps.append(get_exif_datetime(f))

    for i in range(1, len(files)):
        t_prev, t_curr = timestamps[i - 1], timestamps[i]
        if t_prev and t_curr:
            diff = (t_curr - t_prev).total_seconds()
            if diff >= gap_threshold:
                gap_indices.add(i)
                log.debug(f"  Gap detected at index {i}: {diff:.0f}s ({files[i].name})")

    # ── Pass 2: black frame runs ──
    black_flags = []
    log.info("  Checking for black marker frames...")
    for f in tqdm(files, desc="Black frame scan", unit="file", leave=False):
        black_flags.append(is_black_frame(f, brightness_thresh))

    black_run_end_indices = set()  # Index of first NON-black frame after a run
    i = 0
    while i < len(black_flags):
        if black_flags[i]:
            run_start = i
            while i < len(black_flags) and black_flags[i]:
                i += 1
            run_length = i - run_start
            if run_length >= required_black:
                # The boundary is at index i (the first real photo after the black run)
                if i < len(files):
                    black_run_end_indices.add(i)
                    log.debug(f"  Black run of {run_length} ending before index {i} ({files[i].name if i < len(files) else 'EOF'})")
        else:
            i += 1

    # ── Hybrid: accept boundary only when BOTH signals agree (within 3 files) ──
    confirmed_boundaries = []
    for black_idx in sorted(black_run_end_indices):
        # Check if any gap index is within ±3 positions
        nearby_gaps = [g for g in gap_indices if abs(g - black_idx) <= 3]
        if nearby_gaps:
            confirmed_boundaries.append(black_idx)
            log.info(f"  ✓ Confirmed boundary at index {black_idx}: {files[black_idx].name}")
        else:
            log.warning(
                f"  ✗ Black run at index {black_idx} ({files[black_idx].name}) "
                f"has no nearby timestamp gap — skipped (possible dark venue shot)"
            )

    # Also flag any large gaps that had no matching black frames (warn only)
    unmatched_gaps = gap_indices - {b for boundary in confirmed_boundaries
                                     for b in [boundary]}
    for g in sorted(unmatched_gaps):
        log.warning(
            f"  ! Timestamp gap at index {g} ({files[g].name}) "
            f"has no matching black frame — not used as boundary"
        )

    return confirmed_boundaries


def preview_and_confirm_splits(files: list[Path], boundaries: list[int]) -> list[int]:
    """
    Show detected splits to the user and allow manual correction
    before any files are moved.
    """
    print("\n" + "═" * 60)
    print("  DETECTED CONTESTANT SPLITS — please review")
    print("═" * 60)

    split_points = [0] + boundaries + [len(files)]
    for i in range(len(split_points) - 1):
        start_idx = split_points[i]
        end_idx = split_points[i + 1] - 1
        count = end_idx - start_idx + 1
        print(f"\n  Contestant {i + 1}:")
        print(f"    First : {files[start_idx].name}")
        print(f"    Last  : {files[end_idx].name}")
        print(f"    Count : {count} photos")

    print("\n" + "─" * 60)
    print("  Options:")
    print("    [Enter]  Accept these splits and continue")
    print("    [e]      Edit splits manually")
    print("    [s]      Skip detection, enter all ranges manually")
    print("─" * 60)
    choice = input("  Your choice: ").strip().lower()

    if choice == "e":
        boundaries = manual_edit_boundaries(files, boundaries)
    elif choice == "s":
        boundaries = full_manual_entry(files)

    return boundaries


def manual_edit_boundaries(files: list[Path], boundaries: list[int]) -> list[int]:
    """Let the user add, remove, or correct boundary indices."""
    print("\n  Current boundary file names (first photo of each new contestant):")
    for b in boundaries:
        print(f"    Index {b}: {files[b].name}")
    print("\n  Enter corrected filenames for boundaries (comma-separated),")
    print("  or press Enter to keep current:")
    raw = input("  > ").strip()
    if not raw:
        return boundaries
    names = [n.strip().upper() for n in raw.split(",")]
    name_to_idx = {f.name.upper(): i for i, f in enumerate(files)}
    new_boundaries = []
    for name in names:
        # Accept partial match (user might type YKZ_0131 without extension)
        matches = [idx for fname, idx in name_to_idx.items() if name in fname]
        if matches:
            new_boundaries.append(matches[0])
        else:
            log.warning(f"  Could not find file matching '{name}' — skipped")
    return sorted(new_boundaries)


def full_manual_entry(files: list[Path]) -> list[int]:
    """Fallback: ask for number of contestants and start/end filenames."""
    print("\n  Enter number of contestants: ", end="")
    try:
        n = int(input().strip())
    except ValueError:
        n = 1
    boundaries = []
    name_to_idx = {f.name.upper(): i for i, f in enumerate(files)}
    for c in range(2, n + 1):
        print(f"  Enter FIRST filename for Contestant {c} (e.g. YKZ_0131): ", end="")
        raw = input().strip().upper()
        matches = [idx for fname, idx in name_to_idx.items() if raw in fname]
        if matches:
            boundaries.append(matches[0])
        else:
            log.warning(f"  File '{raw}' not found — skipping contestant {c}")
    return sorted(boundaries)


# ─────────────────────────────────────────────
#  STEP 4 — SORT INTO CONTESTANT FOLDERS
# ─────────────────────────────────────────────

def sort_into_folders(
    files: list[Path],
    boundaries: list[int],
    session_dir: Path,
) -> list[Path]:
    """
    Copy files into originals/Contestant_01, Contestant_02, etc.
    Returns list of contestant folder paths.
    """
    split_points = [0] + boundaries + [len(files)]
    contestant_folders = []

    for i in range(len(split_points) - 1):
        start_idx = split_points[i]
        end_idx = split_points[i + 1]
        contestant_files = files[start_idx:end_idx]

        # Skip if all files in this slice are black frames
        real_files = [f for f in contestant_files
                      if not is_black_frame(f, CONFIG["black_brightness_threshold"])]
        if not real_files:
            log.info(f"  Skipping empty/black-only slice at index {start_idx}")
            continue

        folder_name = f"Contestant_{i + 1:02d}"
        dest_folder = session_dir / "originals" / folder_name
        dest_folder.mkdir(parents=True, exist_ok=True)

        for f in tqdm(real_files, desc=f"Sorting {folder_name}", unit="file", leave=False):
            shutil.copy2(f, dest_folder / f.name)

        log.info(f"  {folder_name}: {len(real_files)} photos → {dest_folder}")
        contestant_folders.append(dest_folder)

    return contestant_folders


# ─────────────────────────────────────────────
#  STEP 5 — RESIZE + WATERMARK
# ─────────────────────────────────────────────

def apply_watermark(img: Image.Image, watermark_path: str, opacity: float, position: str, margin: int) -> Image.Image:
    """Composite a watermark PNG onto an image."""
    wm = Image.open(watermark_path).convert("RGBA")

    # Scale watermark to ~25% of image width
    target_w = img.width // 4
    ratio = target_w / wm.width
    wm = wm.resize((target_w, int(wm.height * ratio)), Image.LANCZOS)

    # Apply opacity
    r, g, b, a = wm.split()
    a = a.point(lambda x: int(x * opacity))
    wm.putalpha(a)

    # Calculate position
    if position == "bottom-right":
        x = img.width - wm.width - margin
        y = img.height - wm.height - margin
    elif position == "bottom-left":
        x, y = margin, img.height - wm.height - margin
    elif position == "top-right":
        x, y = img.width - wm.width - margin, margin
    elif position == "top-left":
        x, y = margin, margin
    else:  # center
        x = (img.width - wm.width) // 2
        y = (img.height - wm.height) // 2

    base = img.convert("RGBA")
    base.paste(wm, (x, y), wm)
    return base.convert("RGB")


def resize_and_watermark(contestant_folders: list[Path], session_dir: Path) -> list[Path]:
    """
    For each contestant folder, create a parallel compressed_* folder
    with resized + watermarked JPEGs. Originals untouched.
    """
    compressed_root = session_dir / "compressed"
    compressed_root.mkdir(exist_ok=True)
    compressed_folders = []

    watermark_path = CONFIG["watermark_path"]
    if not Path(watermark_path).exists():
        log.error(f"Watermark file not found: {watermark_path}")
        log.error("Please update CONFIG['watermark_path'] and re-run.")
        sys.exit(1)

    for folder in contestant_folders:
        comp_folder = compressed_root / folder.name
        comp_folder.mkdir(parents=True, exist_ok=True)

        images = sorted(
            [f for f in folder.iterdir()
             if f.suffix.lower() in {".jpg", ".jpeg", ".nef", ".raw", ".cr2", ".arw", ".dng"}]
        )

        for img_path in tqdm(images, desc=f"Processing {folder.name}", unit="file", leave=False):
            try:
                with Image.open(img_path) as img:
                    # Rotate based on EXIF orientation
                    img = ImageOps_exif_rotate(img)

                    # Resize
                    max_px = CONFIG["resize_max_px"]
                    img.thumbnail((max_px, max_px), Image.LANCZOS)

                    # Watermark
                    img = apply_watermark(
                        img,
                        watermark_path,
                        CONFIG["watermark_opacity"],
                        CONFIG["watermark_position"],
                        CONFIG["watermark_margin"],
                    )

                    out_path = comp_folder / (img_path.stem + ".jpg")
                    img.save(out_path, "JPEG", quality=CONFIG["jpeg_quality"], optimize=True)
            except Exception as e:
                log.warning(f"  Could not process {img_path.name}: {e}")

        log.info(f"  Compressed {folder.name}: {len(images)} files → {comp_folder}")
        compressed_folders.append(comp_folder)

    return compressed_folders


def ImageOps_exif_rotate(img: Image.Image) -> Image.Image:
    """Auto-rotate image based on EXIF orientation tag."""
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


# ─────────────────────────────────────────────
#  STEP 6 — UPLOAD TO GOOGLE DRIVE
# ─────────────────────────────────────────────

def upload_to_drive(compressed_folders: list[Path], session_dir: Path) -> dict[str, str]:
    """
    Upload each compressed contestant folder to Google Drive using rclone.
    Returns dict of {folder_name: shareable_link}.
    """
    remote = CONFIG["rclone_remote"]
    gdrive_root = CONFIG["gdrive_root_folder"]
    session_name = session_dir.name
    links = {}

    for folder in compressed_folders:
        remote_path = f"{remote}:{gdrive_root}/{session_name}/{folder.name}"
        log.info(f"  Uploading {folder.name} → {remote_path}")

        try:
            result = subprocess.run(
                ["rclone", "copy", str(folder), remote_path,
                 "--progress", "--transfers=4"],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                log.error(f"  rclone error: {result.stderr}")
                links[folder.name] = "UPLOAD FAILED"
                continue

            # Get a shareable link via rclone link command
            link_result = subprocess.run(
                ["rclone", "link", remote_path],
                capture_output=True, text=True, timeout=30
            )
            link = link_result.stdout.strip() if link_result.returncode == 0 else "Link unavailable"
            links[folder.name] = link
            log.info(f"  ✓ {folder.name} uploaded. Link: {link}")

        except FileNotFoundError:
            log.error("  rclone not found. Install from https://rclone.org/downloads/")
            links[folder.name] = "RCLONE NOT INSTALLED"
        except subprocess.TimeoutExpired:
            log.error(f"  Upload timed out for {folder.name}")
            links[folder.name] = "TIMEOUT"

    # Save links CSV
    links_file = session_dir / "drive_links.csv"
    with open(links_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Contestant Folder", "Google Drive Link"])
        for name, link in links.items():
            writer.writerow([name, link])
    log.info(f"  Drive links saved to: {links_file}")

    return links


# ─────────────────────────────────────────────
#  STEP 7 — HDD BACKUP (runs in parallel thread)
# ─────────────────────────────────────────────

def backup_to_hdd(originals_dir: Path, session_name: str):
    """Copy originals to external HDD as a 3rd backup."""
    hdd_path = Path(CONFIG["hdd_backup_path"]) / session_name
    hdd_path.mkdir(parents=True, exist_ok=True)

    log.info(f"  Backing up originals to HDD: {hdd_path}")
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["robocopy", str(originals_dir), str(hdd_path), "/E", "/COPYALL", "/NFL", "/NDL"],
                timeout=1800
            )
        else:
            subprocess.run(
                ["rsync", "-a", "--progress", str(originals_dir) + "/", str(hdd_path)],
                timeout=1800
            )
        log.info(f"  ✓ HDD backup complete: {hdd_path}")
    except FileNotFoundError as e:
        log.error(f"  HDD backup tool not found: {e}")
    except subprocess.TimeoutExpired:
        log.error("  HDD backup timed out.")


# ─────────────────────────────────────────────
#  MAIN ORCHESTRATOR
# ─────────────────────────────────────────────

def main():
    print("\n" + "═" * 60)
    print("  COMPETITION PHOTO WORKFLOW  v1.0")
    print("═" * 60 + "\n")

    # ── Create session folder ──
    session_dir = create_session_folder()
    originals_dir = session_dir / "originals"

    # ── Detect or ask for SD card ──
    sd_path = find_sd_card()
    if not sd_path:
        print("  SD card not auto-detected.")
        print("  Enter SD card path manually (e.g. E:\\ or /Volumes/NIKON): ", end="")
        sd_input = input().strip().strip('"')
        sd_path = Path(sd_input)
        if not sd_path.exists():
            log.error(f"Path does not exist: {sd_path}")
            sys.exit(1)

    # ── Copy files ──
    flat_dir = originals_dir / "_all_files"
    flat_dir.mkdir(parents=True, exist_ok=True)
    all_files = copy_from_sd(sd_path, flat_dir)

    if not all_files:
        log.error("No files copied. Exiting.")
        sys.exit(1)

    # ── Start HDD backup in background ──
    import threading
    hdd_thread = threading.Thread(
        target=backup_to_hdd,
        args=(flat_dir, session_dir.name),
        daemon=True,
    )
    hdd_thread.start()
    log.info("HDD backup started in background...")

    # ── Detect contestant boundaries ──
    boundaries = detect_boundaries(all_files)

    if not boundaries:
        log.warning("No boundaries detected automatically.")
        print("\n  No automatic splits found. Switching to manual entry.")
        boundaries = full_manual_entry(all_files)
    else:
        boundaries = preview_and_confirm_splits(all_files, boundaries)

    # ── Sort into contestant folders ──
    log.info("\nSorting files into contestant folders...")
    contestant_folders = sort_into_folders(all_files, boundaries, session_dir)

    if not contestant_folders:
        log.error("No contestant folders created. Exiting.")
        sys.exit(1)

    # ── Resize + watermark ──
    log.info("\nResizing and watermarking compressed copies...")
    compressed_folders = resize_and_watermark(contestant_folders, session_dir)

    # ── Upload to Google Drive ──
    print("\n" + "─" * 60)
    print("  Ready to upload to Google Drive.")
    print("  Press [Enter] to upload, or [s] to skip: ", end="")
    skip_upload = input().strip().lower() == "s"

    links = {}
    if not skip_upload:
        log.info("\nUploading to Google Drive...")
        links = upload_to_drive(compressed_folders, session_dir)
    else:
        log.info("Upload skipped.")

    # ── Wait for HDD backup to finish ──
    log.info("\nWaiting for HDD backup to finish...")
    hdd_thread.join(timeout=300)

    # ── Final summary ──
    print("\n" + "═" * 60)
    print("  SESSION COMPLETE")
    print("═" * 60)
    print(f"  Session folder : {session_dir}")
    print(f"  Contestants    : {len(contestant_folders)}")
    print(f"  Drive links    : {session_dir / 'drive_links.csv'}")
    if links:
        print("\n  Google Drive links:")
        for name, link in links.items():
            print(f"    {name}: {link}")
    print("\n  All done. You can close this window.\n")


if __name__ == "__main__":
    main()
