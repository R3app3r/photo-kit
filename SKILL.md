---
name: photo-kit
description: Batch-prepare real project photos for ads and social posts. Corrects white balance, levels, converging verticals and vibrance, exports every platform size (Google Ads, Business Profile, LinkedIn) from one master with metadata stripped, builds before/after review sheets, and renders branded cards. Deterministic script; the model only reviews reports and writes small JSON overrides.
model: haiku
license: MIT
metadata:
  author: Marton Rozsas
  version: "1.0"
---

# photo-kit

All pixel decisions are made by `scripts/photo_kit.py`. Your job is to run it, read the report and review sheets, write overrides for the few photos that need them, rerun only those, then hand the folder to the human.  
Do not edit images any other way. Do not rewrite the script unless a check fails twice.

## Setup (once per project)
- Python 3.10+ and `pip install -r requirements.txt` (Pillow 10.1+, numpy, opencv-python-headless, fontTools, brotli).
- A project config JSON that lists only what differs from `config.default.json` (brand logo, font file(s), colours, URL, optional material swatch).  
  Web fonts are often split into subsets: list every file in `brand.font`.

## Run
```
python <skill-folder>/scripts/photo_kit.py process IN_DIR OUT_DIR --config <project-config.json> [--overrides OUT_DIR/overrides.json] [--only stem1,stem2]
python <skill-folder>/scripts/photo_kit.py cards OUT_DIR cards.json --config <project-config.json>
python <skill-folder>/scripts/photo_kit.py verify OUT_DIR
```

Output per photo: `OUT_DIR/<stem>/00_master.png` (lossless), platform JPEGs, optional branded cards; plus `manifest.csv`, `report.json`, and `_review/before_after_NN.jpg`.  
Never write the output directory inside the input folder or a website source tree.

## Review loop (text first, images last)
1. Read the console summary and `report.json`. Act only on clear signals (large lean that was skipped, `TOO SMALL` flags, obvious colour cast, people that look stretched, etc.).
2. Open the review sheets once. For a bad crop set `{"focus": [x, y]}` (0–1 of the master).
3. Write `OUT_DIR/overrides.json` and rerun with `--only` the affected stems.
4. Cards: write `cards.json` with approved headlines only (max ~45 characters). Use `"fit": "whole"` for portrait photos on portrait cards.
5. Run `verify` (must print `clean`), then report the folder path, platform purposes, remaining flags, and any photo that could not be fixed. The human audits everything before upload.

## Ranking a photo library
Run `process` on a folder with one sub-folder per topic. Rank by the `quality` scores in `report.json` (`sharpness` × resolution factor), penalise `TOO SMALL` flags, then review a contact sheet of the top candidates.

## Rules that never change
- Google Ads images: plain exports only (no logo, text, frame or border).
- Business Profile: light correction only; plain export by default.
- Never change the product: no recolouring, no added texture, no defect removal, no AI fill. Cropping clutter is fine.
- Only the organisation’s own photos. No client faces, house numbers, or third-party photographer credits.
- Keep originals; outputs are always new files.
- Card headlines come only from approved copy.

## Tuning
Adjust `correction` strengths and `card_formats` in the project config (not the script). Per-photo overrides handle exceptions.
