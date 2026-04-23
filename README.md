# Competition Photo Workflow — Setup & Usage Guide

## What this does
Fully automates the competition photography pipeline:
SD card → copy → detect contestant splits → sort folders →
resize + watermark → Google Drive upload → HDD backup

---

## Folder structure (put all 3 files in one folder)

```
CompWorkflow/
├── workflow.py          ← main script
├── test_workflow.py     ← test runner
├── requirements.txt     ← Python dependencies
└── watermark.png        ← your watermark (you add this)
```

---

## ONE-TIME SETUP

### Step 1 — Install Python
Download from https://python.org/downloads  
Version 3.11 or newer. During install, tick **"Add Python to PATH"**.

Verify it worked — open Terminal (Mac) or Command Prompt (Windows) and type:
```
python --version
```
You should see something like `Python 3.11.x`

---

### Step 2 — Install VS Code
Download from https://code.visualstudio.com  
Install the **Python extension** from the Extensions panel (Ctrl+Shift+X).

---

### Step 3 — Open the project in VS Code
1. Put all 3 script files in a folder, e.g. `C:\CompWorkflow\` (Windows) or `~/CompWorkflow/` (Mac)
2. In VS Code: File → Open Folder → select that folder
3. Open the Terminal inside VS Code: Terminal → New Terminal

---

### Step 4 — Install Python libraries
In the VS Code terminal, run:
```
pip install -r requirements.txt
```
This installs Pillow (image processing), piexif (EXIF reading), and tqdm (progress bars).

---

### Step 5 — Install rclone (for Google Drive upload)
Download from: https://rclone.org/downloads/
- Windows: download the .exe, put it somewhere like `C:\rclone\rclone.exe`, add that folder to your PATH
- Mac: run `brew install rclone` in Terminal (requires Homebrew)

Then configure it for Google Drive — run this in terminal:
```
rclone config
```
Follow the prompts:
- Choose `n` (new remote)
- Name it exactly: `gdrive`
- Choose `drive` (Google Drive)
- Follow OAuth login in your browser
- Choose scope: `drive` (full access)
- Leave client_id and client_secret blank (press Enter)
- At the end, test with: `rclone ls gdrive:`

---

### Step 6 — Install ImageMagick (optional, used for fallback)
Download from: https://imagemagick.org/script/download.php  
The script uses Pillow primarily, but ImageMagick is useful to have.

---

### Step 7 — Edit the CONFIG section in workflow.py
Open `workflow.py` in VS Code and find the CONFIG block near the top.
Edit these values to match your setup:

```python
CONFIG = {
    "session_root": r"C:\CompPhotos",         # where sessions are saved
    "watermark_path": r"C:\CompWorkflow\watermark.png",  # your watermark PNG
    "hdd_backup_path": r"D:\PhotoBackup",     # your external HDD
    "rclone_remote": "gdrive",                # must match rclone config name
    "gdrive_root_folder": "CompetitionPhotos", # Drive folder name
    ...
}
```

For Mac, use forward slashes:
```python
    "session_root": "/Users/yourname/CompPhotos",
```

---

### Step 8 — Prepare your watermark PNG
Create a PNG file with a transparent background.
Recommended: white text on transparent, sized around 800×200px.
Save it at the path you set in `watermark_path`.

---

## TESTING (do this before event day)

Run the test script — it generates fake photos and runs the full pipeline:
```
python test_workflow.py
```

You should see output like:
```
  Generating test photos for 3 contestants...
    Contestant 1: 7 photos  (YKZ_0001.JPG → YKZ_0007.JPG)
    → 2 black frames inserted (boundary marker)
    Contestant 2: 9 photos  (YKZ_0010.JPG → YKZ_0018.JPG)
    → 2 black frames inserted
    Contestant 3: 6 photos  (YKZ_0021.JPG → YKZ_0026.JPG)

  STEP 1: Hybrid boundary detection
    Expected: 2   Detected: 2   ✓ PASS

  STEP 2: Sort into contestant folders
    Contestant_01: 7 photos   ✓ PASS
    ...

  STEP 3: Resize + watermark
    ✓ PASS — all images resized correctly
```

Open the generated `test_session/` folder and visually check the output photos.

---

## USING ON EVENT DAY

### Your workflow:
1. Shoot normally on your Nikon
2. At the end of a contestant's round: **cover the lens and take 2 shots**
3. When ready, insert the SD card into the laptop
4. In VS Code terminal, run:
   ```
   python workflow.py
   ```
5. The script will:
   - Auto-detect the SD card
   - Copy all files
   - Start HDD backup in background
   - Scan for black frames + timestamp gaps
   - Show you the proposed splits for review
   - Ask you to confirm (or edit) before proceeding
   - Resize + watermark the compressed copies
   - Upload to Google Drive and print the shareable links
   - Save all links to `drive_links.csv`

6. Press Enter to accept splits, or type corrections if anything looks off.
7. Share the Drive links from `drive_links.csv` with each contestant.

---

## EDGE CASES & TIPS

| Situation | What happens | What to do |
|-----------|-------------|------------|
| Forgot to take black frames | No boundaries detected | Script switches to manual entry |
| Very dark venue shot looks black | Timestamp gap won't match → safely ignored | Nothing, hybrid handles it |
| Fast stage crew (gap < 90s) | Gap not detected | Script shows warning, use preview to add boundary |
| Wrong number of splits detected | Preview screen shows splits | Press [e] to edit before files are moved |
| rclone not configured | Upload step fails gracefully | Run `rclone config` to set up, or skip upload |
| SD card on unusual drive letter | Script prompts you to type path manually | Type the drive letter when asked |

---

## FILE STRUCTURE AFTER A SESSION

```
CompPhotos/
└── session_2024-06-15_09-00/
    ├── originals/
    │   ├── _all_files/         ← flat copy of everything from SD
    │   ├── Contestant_01/      ← sorted originals (untouched)
    │   ├── Contestant_02/
    │   └── Contestant_03/
    ├── compressed/
    │   ├── Contestant_01/      ← resized + watermarked JPEGs
    │   ├── Contestant_02/
    │   └── Contestant_03/
    └── drive_links.csv         ← shareable links for each contestant
```
