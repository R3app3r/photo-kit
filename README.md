# photo-kit

Deterministic batch photo pipeline for marketing assets. One command produces platform-sized, metadata-free images, a review sheet, and optional branded cards. The model only reads reports and writes small JSON overrides.

> **Built to be cheap to run, for everyday use.**
> All pixel work is done by a plain Python script, not by the model. Claude only runs commands, reads short text reports and writes small JSON files, so the skill runs well on a small, inexpensive model (`haiku`) and uses few tokens: no images are generated, and review sheets are read once, after the text reports.
> It is a practical everyday helper for small businesses and personal projects, not a replacement for professional photo-editing software or a retoucher. Always check the results before you publish them.

## Main description
A Claude skill that wraps a deterministic Python script (`photo_kit.py`) for preparing real project photos for Google Ads, Google Business Profile, and LinkedIn. It handles colour correction, straightening of converging verticals, export to multiple platform sizes, metadata stripping, before/after review sheets, and optional branded post cards. All pixel decisions stay in the script; the model only reviews text reports and writes small JSON overrides.

## Use cases
- Preparing Google Ads image assets (1.91:1, 1:1, 4:5) from real job photos.
- Creating Google Business Profile post images (4:3) with light correction.
- Generating LinkedIn feed images and branded cards with logo, brand colours, and approved headlines.
- Straightening phone photos of rooms and furniture in bulk.
- Cleaning compressed or re-saved JPEGs before publishing.
- Ranking a photo library by sharpness and resolution so the best candidates surface first.
- Reusing the same pipeline for any brand by supplying a project-specific config.

## Best practices
- Always start from original files, not chat-app copies.
- Review `report.json` and the before/after sheets before looking at individual images.
- Use `--only` for reruns so other photos stay untouched.
- Keep brand assets (logo, fonts, colours) in a project-specific config that deep-merges with `config.default.json`.
- Run `verify` and do a final human audit before any upload.
- Prefer the skill on a smaller model (`haiku`); the script does the pixel work.
- Never change the product itself (no recolouring, no AI fill, no defect removal). Cropping clutter is fine.
- Card headlines must come only from approved copy.

## Installation
1. Place this folder at `~/.claude/skills/photo-kit/` (or the equivalent skills directory).
2. Install dependencies (Python 3.10+): `pip install -r requirements.txt`.
3. Optional: Microsoft Edge or Chrome, only for `scripts/html_to_pdf.py` (turns `GUIDE.html` into a PDF).

The script lives in `scripts/photo_kit.py` and the defaults in `config.default.json`. Everything the skill needs is in this folder; no network access or API keys are used. `GUIDE.html` is the full reference (commands, config keys, overrides, card format).

## Author
Marton Rozsas · marton.rozsas@gmail.com  
Feedback and issue reports are welcome.

## License
MIT, see [LICENSE](LICENSE). Provided as is, without warranty.
