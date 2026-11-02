# photo-kit guide

Photo pipeline for ads and social posts · deterministic batch processing · reusable for any brand

> [!NOTE]
> **In one line:** put real project photos in a folder, run one command, get straightened and colour-corrected photos in every platform size (metadata stripped), a before/after review sheet, and branded post cards. Every decision is written to a report, so review and fixes need only small text edits, no image editing.

## 1. Where it lives

| Path | What |
|---|---|
| `~/.claude/skills/photo-kit/SKILL.md` | Instructions Claude follows (recommended model: haiku) |
| `…/scripts/photo_kit.py` | The tool: `process`, `cards`, `verify` |
| `…/config.default.json` | All defaults (sizes, correction strengths, card layout) |
| `…/GUIDE.md` | This guide |
| project config JSON | Brand-specific overrides (logo, fonts, colours, url), deep-merged with defaults |
| `~/.cache/photo_kit/` | Fonts converted from woff2 to ttf (created automatically) |

A skill is picked up when a Claude Code session starts. The script itself works from any terminal.

## 2. Dependencies

| Package | Used for |
|---|---|
| Python 3.10+ | runs everything |
| Pillow 10.1+ | load/save, colour profiles, resize, unsharp mask, drawing cards |
| numpy | pixel maths (white balance, levels, vibrance) |
| opencv-python-headless | line detection, perspective warp, denoising, Sobel/Laplacian |
| fontTools + brotli | convert web fonts (woff2) to ttf; read font coverage |

```
python -m pip install -r requirements.txt
```

## 3. Use cases

- Google Ads image assets (1.91:1, 1:1, 4:5) from real job photos (plain: no logos, text or frames).
- Business Profile post photos (4:3), light correction only.
- LinkedIn feed photos (4:5, 1:1, 1.91:1) and branded LinkedIn cards with a headline.
- Straightening phone shots of rooms and furniture (converging verticals) in bulk.
- Cleaning compressed or re-saved JPEGs before publishing.
- Ranking a photo library by sharpness and resolution.
- Any brand: supply a project config with logo, fonts, and colours.

## 4. Workflow

1. **Collect** originals into a folder (sub-folders become name prefixes). Prefer original files over chat-app copies.
2. **Process**: `python photo_kit.py process IN OUT --config <project config>`
3. **Review text first**: console summary, `report.json`, `manifest.csv` flags. Then look at `_review/before_after_NN.jpg`.
4. **Fix by overrides**: write `OUT/overrides.json`, rerun only those photos with `--only stem1,stem2`.
5. **Cards**: write `cards.json` with approved headlines; `python photo_kit.py cards OUT cards.json --config …`
6. **Verify**: `python photo_kit.py verify OUT` must say `clean`.
7. **Human audit** of every file, then upload.

## 5. Commands

| Command | Arguments |
|---|---|
| `process IN OUT` | `--config` project JSON · `--overrides` per-photo JSON · `--only` comma-separated stems |
| `cards OUT CARDS.json` | `--config`. Reads `OUT/<stem>/00_master.png` |
| `verify OUT` | exit code 1 if any file still carries metadata |

## 6. What comes out

| File in `OUT/<stem>/` | Size | Destination |
|---|---|---|
| `00_master.png` | long edge ≤ 3000 | corrected master, lossless (source for cards); not for upload |
| `gads-landscape_1200x628.jpg` | 1.91:1 | Google Ads image asset |
| `gads-square_1200x1200.jpg` | 1:1 | Google Ads image asset |
| `gads-portrait_960x1200.jpg` | 4:5 | Google Ads image asset |
| `gbp-post_1200x900.jpg` | 4:3 | Google Business Profile post |
| `li-portrait_1080x1350.jpg` | 4:5 | LinkedIn feed |
| `li-square_1200x1200.jpg` | 1:1 | LinkedIn feed |
| `li-landscape_1200x627.jpg` | 1.91:1 | LinkedIn feed / link image |
| `card_<id>_<format>_WxH.jpg` | see card formats | branded card |

Plus in `OUT/`: `manifest.csv`, `report.json`, `_review/before_after_NN.jpg`.

| Flag | Meaning |
|---|---|
| *(empty)* | the crop had at least the output's pixels |
| `UPSCALED x1.0–1.25` | slightly enlarged: fine |
| `TOO SMALL xN: replace source` | enlarged more than `max_upscale`: get the original or exclude that size |

## 7. Processing order (per photo)

1. Rotate by Exif, convert any embedded colour profile to sRGB, shrink to `master_long_edge`.
2. Optional manual rotation (override).
3. **Denoise**: measure noise, remove JPEG blocks/grain only when present.
4. **Verticals**: find near-vertical lines, compute vanishing point, warp, crop, re-measure; revert if not straighter.
5. **White balance** from bright near-neutral pixels, gains capped.
6. **Levels**: percentile black/white points with a capped stretch, then mild midtone gamma.
7. **Vibrance**: boosts low-saturation colours most.
8. Per platform: best crop → Lanczos resize → brightness-only sharpening scaled to the resize → JPEG.

## 8. Config reference

A project config lists only what differs from `config.default.json` (deep-merged).

### Top level

| Key | Default | Meaning |
|---|---|---|
| `master_long_edge` | 3000 | masters are shrunk to this |
| `jpeg_quality` | 95 | exports and cards |
| `max_upscale` | 1.25 | above this an export is flagged TOO SMALL |
| `platforms[]` | 7 entries | `id`, `label`, `w`, `h` |

### `correction` (selected keys)

| Key | Default | Meaning |
|---|---|---|
| `verticals` | true | straighten converging verticals |
| `verticals_strength` | 0.9 | 1 = fully vertical; 0.9 looks more natural |
| `verticals_max_deg` | 13 | refuse corrections that need more |
| `wb_strength` | 0.8 | how much of the measured cast to remove |
| `vibrance` | 0.15 | 0 = off; higher values boost low-saturation colours |
| `sharpen` | radius 0.9, percent 80, threshold 3 | scaled by resize factor |

### `brand`

| Key | Meaning |
|---|---|
| `logo` | PNG with transparency (recoloured automatically); optional |
| `font` | one path or a list of subset files |
| `swatch` / `swatch_fade` | optional material photo as card background |
| `primary` / `on_primary` | band colour / text and logo on it |
| `accent` / `accent_light` | thin rule / subline colour |
| `background` | card frame colour |
| `url` | default subline |

## 9. Per-photo overrides (`OUT/overrides.json`)

```json
{ "photo-stem": {"exclude": ["gads-square"]},
  "another": {"verticals": false},
  "third": {"focus": [0.5, 0.35]},
  "fourth": {"correction": {"verticals_max_deg": 15}, "vibrance": 0.08} }
```

| Key | Effect |
|---|---|
| `skip` | true: ignore this photo |
| `exclude` | list of platform ids not to export |
| `focus` | [x, y] 0–1 of the master: crops centre on it |
| `rotate` | degrees, + = clockwise |
| `verticals` | false: no straightening |
| `wb` | false: no white balance |
| `vibrance` | number replacing the default |
| `correction` | any key of `correction` for this photo only |

## 10. Cards (`cards.json`)

```json
[ {"id": "example", "source": "photo-stem",
   "headline": "Approved headline here",
   "subline": "example.com",
   "formats": ["li-portrait-card", "li-square-card"], "fit": "whole"} ]
```

| Key | Meaning |
|---|---|
| `id` | part of the file name |
| `source` | a stem from `process` |
| `headline` | approved copy only; ~45 characters max |
| `subline` | small line under it (default: brand url) |
| `formats` | card formats to render |
| `fit` | `"whole"`: show the entire photo |
| `focus` | [x, y]: steer the crop |

## 11. Rules that never change

- Google Ads images: plain exports only (no logo, text, frame, border).
- Business Profile: light correction only; plain export by default.
- Never change the product: no recolouring, no added texture, no defect removal, no AI fill. Cropping clutter is fine.
- Only the organisation's own photos; no client faces or house numbers; nothing with a third-party photographer credit.
- Card headlines only from approved copy. Originals are never overwritten.

---

Author: Marton Rozsas · marton.rozsas@gmail.com
