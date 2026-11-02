#!/usr/bin/env python3
"""photo_kit: batch-correct marketing photos, export every platform size, build a before/after sheet and branded cards.

Deterministic on purpose: every decision is made by the code and written to report.json, so a reviewer (human or a
small model) only reads the report and the sheets and edits overrides.json, never the pixels.

  python photo_kit.py process IN_DIR OUT_DIR [--config cfg.json] [--overrides overrides.json] [--only stem,stem]
  python photo_kit.py cards OUT_DIR CARDS.json [--config cfg.json]
  python photo_kit.py verify OUT_DIR

Requires: Pillow, numpy, opencv-python-headless; fontTools + brotli only for woff2 fonts.
"""
import argparse, csv, json, math, os, sys
from pathlib import Path

import numpy as np
import cv2
from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
DEFAULTS = HERE.parent / "config.default.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def deep_merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path):
    cfg = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    if path:
        p = Path(path)
        cfg = deep_merge(cfg, json.loads(p.read_text(encoding="utf-8")))
        cfg["_base"] = str(p.parent)
    else:
        cfg["_base"] = str(Path.cwd())
    return cfg


def rel(cfg, p):
    if not p:
        return None
    q = Path(p).expanduser()
    return q if q.is_absolute() else Path(cfg["_base"]) / q


def load_rgb(path, long_edge):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(__import__("io").BytesIO(icc))
            im = ImageCms.profileToProfile(im.convert("RGB"), src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:
            im = im.convert("RGB")
    im = im.convert("RGB")
    if max(im.size) > long_edge:
        s = long_edge / max(im.size)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    return np.asarray(im).astype(np.float32) / 255.0


def to_img(a):
    return Image.fromarray(np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8), "RGB")


def luma(a):
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def white_balance(a, c, log):
    L = luma(a)
    chroma = a.max(axis=2) - a.min(axis=2)
    lo, hi = np.percentile(L, c["wb_luma_from_pct"]), np.percentile(L, 99.5)
    mask = (L >= lo) & (L <= hi) & (chroma < c["wb_max_chroma"])
    share = float(mask.mean())
    if share < c["wb_min_share"]:
        log["wb"] = f"skipped (neutral area {share:.1%} < {c['wb_min_share']:.0%})"
        return a
    m = a[mask].mean(axis=0)
    gains = m.mean() / np.maximum(m, 1e-4)
    gains = np.clip(gains, 1 - c["wb_max_gain"], 1 + c["wb_max_gain"])
    gains = 1 + (gains - 1) * c["wb_strength"]
    gains /= gains.max()
    log["wb"] = "gains R%.3f G%.3f B%.3f (neutral area %.0f%%)" % (*gains, share * 100)
    return a * gains


def levels(a, c, log):
    L = luma(a)
    lo = float(np.percentile(L, c["levels_clip_low"]))
    hi = float(np.percentile(L, c["levels_clip_high"]))
    lo = min(lo, c["levels_max_black"])
    hi = max(hi, c["levels_min_white"])
    scale = min(1.0 / max(hi - lo, 1e-3), c["levels_max_scale"])
    a = np.clip((a - lo) * scale, 0, 1)
    med = float(np.median(luma(a)))
    gamma = 1.0
    if 0.05 < med < 0.95:
        gamma = float(np.clip(math.log(c["levels_target_median"]) / math.log(med), *c["levels_gamma_range"]))
        a = np.power(a, gamma)
    log["levels"] = f"black {lo:.3f} white {hi:.3f} scale {scale:.2f} gamma {gamma:.2f} (median {med:.2f})"
    return a


def vibrance(a, c, log):
    v = c["vibrance"]
    if v <= 0:
        return a
    L = luma(a)[..., None]
    s = (a.max(axis=2) - a.min(axis=2))[..., None]
    a = np.clip(L + (a - L) * (1 + v * (1 - np.clip(s * 2, 0, 1))), 0, 1)
    log["vibrance"] = f"+{v:.2f}"
    return a


def clarity(a, c, log):
    k = c.get("clarity", 0)
    if k <= 0:
        return a
    lab = cv2.cvtColor(a.astype(np.float32), cv2.COLOR_RGB2LAB)
    L = lab[..., 0].copy()
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    Lc = clahe.apply(np.clip(L * 2.55, 0, 255).astype(np.uint8)).astype(np.float32) / 2.55
    lab[..., 0] = L + (Lc - L) * k
    log["clarity"] = f"+{k:.2f}"
    return np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB), 0, 1)


def detect_vertical_lines(gray8, c):
    h, w = gray8.shape
    s = 1000 / max(h, w)
    small = cv2.resize(gray8, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else gray8
    s = small.shape[1] / w
    edges = cv2.Canny(cv2.GaussianBlur(small, (5, 5), 0), 40, 120)
    min_len = c["verticals_min_line"] * min(small.shape)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=60, minLineLength=min_len, maxLineGap=8)
    out = []
    if lines is None:
        return out
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        dx, dy = x2 - x1, y2 - y1
        ang = math.degrees(math.atan2(dx, dy))
        ang = (ang + 90) % 180 - 90
        if abs(ang) <= c["verticals_search_deg"]:
            out.append((x1 / s, y1 / s, x2 / s, y2 / s, math.hypot(dx, dy) / s, ang))
    return out


def vanishing_point(lines, w, h):
    cx, cy = w / 2, h / 2
    rows, wts = [], []
    for x1, y1, x2, y2, ln, _ in lines:
        p, q = np.array([x1 - cx, y1 - cy, 1.0]), np.array([x2 - cx, y2 - cy, 1.0])
        l = np.cross(p, q)
        n = math.hypot(l[0], l[1])
        if n == 0:
            continue
        rows.append(l / n)
        wts.append(ln)
    A = np.array(rows) * np.sqrt(np.array(wts))[:, None]
    _, _, vt = np.linalg.svd(A)
    v = vt[-1]
    if abs(v[2]) < 1e-9:
        return None
    return v[0] / v[2], v[1] / v[2]


def verticals(a, c, log, force=None):
    if force is False or not c["verticals"]:
        log["verticals"] = "off"
        return a
    h, w = a.shape[:2]
    gray8 = (luma(a) * 255).astype(np.uint8)
    lines = detect_vertical_lines(gray8, c)
    if len(lines) < c["verticals_min_lines"]:
        log["verticals"] = f"skipped ({len(lines)} vertical lines found)"
        return a
    before = float(np.average([abs(l[5]) for l in lines], weights=[l[4] for l in lines]))
    vp = vanishing_point(lines, w, h)
    cx, cy = w / 2, h / 2
    if vp is None or abs(vp[1]) > 1e6:
        tilt = float(np.average([l[5] for l in lines], weights=[l[4] for l in lines]))
        H = np.array([[1, math.tan(math.radians(tilt)), 0], [0, 1, 0], [0, 0, 1.0]])
        kind = f"tilt {tilt:+.2f} deg"
    else:
        vx, vy = vp
        lean = max(abs(math.degrees(math.atan2(vx - x, vy - y))) for x in (-cx, cx) for y in (-cy, cy))
        lean = min(lean, 180 - lean)
        if lean > c["verticals_max_deg"]:
            log["verticals"] = f"skipped (needs {lean:.1f} deg > max {c['verticals_max_deg']}; check by hand)"
            return a
        if abs(vy) < 0.5 * h:
            log["verticals"] = "skipped (vanishing point inside the frame: not a vertical set)"
            return a
        k = c["verticals_strength"]
        H = np.array([[1, -k * vx / vy, 0], [0, 1, 0], [0, -k / vy, 1.0]])
        kind = f"keystone (vp {vx:+.0f},{vy:+.0f} px, lean {lean:.1f} deg)"
    T = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1.0]])
    Ti = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1.0]])
    M = T @ H @ Ti
    corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], float).T
    wc = M @ corners
    wc = (wc[:2] / wc[2]).T
    minx, miny = wc.min(axis=0)
    S = np.array([[1, 0, -minx], [0, 1, -miny], [0, 0, 1.0]])
    M2 = S @ M
    ow, oh = int(math.ceil(wc[:, 0].max() - minx)), int(math.ceil(wc[:, 1].max() - miny))
    warped = cv2.warpPerspective(a, M2, (ow, oh), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    tl, tr, br, bl = wc - [minx, miny]
    x0, x1 = math.ceil(max(tl[0], bl[0])), math.floor(min(tr[0], br[0]))
    y0, y1 = math.ceil(max(tl[1], tr[1])), math.floor(min(bl[1], br[1]))
    crop = warped[y0:y1, x0:x1]
    if crop.size == 0 or crop.shape[0] < 0.8 * h or crop.shape[1] < 0.8 * w:
        log["verticals"] = f"skipped ({kind}: would crop more than 20%)"
        return a
    after_lines = detect_vertical_lines((luma(np.clip(crop, 0, 1)) * 255).astype(np.uint8), c)
    if after_lines:
        after = float(np.average([abs(l[5]) for l in after_lines], weights=[l[4] for l in after_lines]))
        if after >= before:
            log["verticals"] = f"reverted ({kind}: mean lean {before:.2f} -> {after:.2f} deg)"
            return a
        log["verticals"] = f"{kind}; mean lean {before:.2f} -> {after:.2f} deg; kept {crop.shape[1]}x{crop.shape[0]}"
    else:
        log["verticals"] = f"{kind}; no lines to re-check"
    return np.clip(crop, 0, 1)


def denoise(a, c, log):
    if c.get("denoise_max", 0) <= 0:
        return a
    u8 = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    g = cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F)
    sigma = float(np.median(np.abs(lap - np.median(lap))) / 0.6745 / 2.0)
    h = float(np.clip(sigma * c["denoise_k"], 0, c["denoise_max"]))
    if h < 1.0:
        log["denoise"] = f"skipped (noise {sigma:.1f})"
        return a
    out = cv2.fastNlMeansDenoisingColored(u8, None, h, h * 0.8, 5, 15)
    log["denoise"] = f"h {h:.1f} (noise {sigma:.1f})"
    return out.astype(np.float32) / 255.0


def correct(a, c, ov, log):
    c = deep_merge(c, ov.get("correction", {}))
    a = denoise(a, c, log)
    if ov.get("rotate"):
        img = to_img(a).rotate(-float(ov["rotate"]), resample=Image.BICUBIC, expand=False)
        a = np.asarray(img).astype(np.float32) / 255.0
        log["rotate"] = f"{ov['rotate']} deg (override)"
    a = verticals(a, c, log, force=ov.get("verticals"))
    if ov.get("wb", True):
        a = white_balance(a, c, log)
    a = levels(a, c, log)
    a = clarity(a, c, log)
    a = vibrance(a, deep_merge(c, {"vibrance": ov.get("vibrance", c["vibrance"])}), log)
    return np.clip(a, 0, 1)


def energy_map(a):
    g = (luma(a) * 255).astype(np.uint8)
    s = 400 / max(g.shape)
    g = cv2.resize(g, (max(1, round(g.shape[1] * s)), max(1, round(g.shape[0] * s))), interpolation=cv2.INTER_AREA)
    e = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    e = cv2.GaussianBlur(e, (0, 0), 6)
    yy, xx = np.mgrid[0:e.shape[0], 0:e.shape[1]]
    centre = np.exp(-(((xx / e.shape[1] - 0.5) ** 2) + ((yy / e.shape[0] - 0.5) ** 2)) / 0.18)
    return e * (0.5 + centre), s


def best_crop(a, ratio, focus=None, energy=None):
    h, w = a.shape[:2]
    if w / h > ratio:
        cw, ch = round(h * ratio), h
    else:
        cw, ch = w, round(w / ratio)
    if focus:
        fx, fy = focus[0] * w, focus[1] * h
        x0 = int(np.clip(fx - cw / 2, 0, w - cw)); y0 = int(np.clip(fy - ch / 2, 0, h - ch))
        return x0, y0, cw, ch
    e, s = energy
    if cw < w:
        col = e.sum(axis=0); win = max(1, round(cw * s))
        cs = np.concatenate([[0], np.cumsum(col)])
        sums = cs[win:] - cs[:-win] if win < len(col) else np.array([cs[-1]])
        x0 = int(round(int(np.argmax(sums)) / s)); x0 = min(max(x0, 0), w - cw)
        return x0, 0, cw, ch
    if ch < h:
        row = e.sum(axis=1); win = max(1, round(ch * s))
        cs = np.concatenate([[0], np.cumsum(row)])
        sums = cs[win:] - cs[:-win] if win < len(row) else np.array([cs[-1]])
        y0 = int(round(int(np.argmax(sums)) / s)); y0 = min(max(y0, 0), h - ch)
        return 0, y0, cw, ch
    return 0, 0, w, h


def sharpen(img, c, shrink):
    sh = c["sharpen"]
    if shrink >= 1.5:
        radius, pct = sh["radius"], sh["percent"]
    elif shrink >= 1.0:
        radius, pct = sh["radius"] * 0.8, sh["percent"] * 0.7
    else:
        radius, pct = sh["radius"] * 0.7, sh["percent"] * 0.35
    y, cb, cr = img.convert("YCbCr").split()
    y = y.filter(ImageFilter.UnsharpMask(radius=radius, percent=round(pct), threshold=sh["threshold"]))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def save_jpeg(img, path, q):
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = Image.frombytes("RGB", img.size, img.convert("RGB").tobytes())
    clean.save(path, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0)


def export_all(a, stem, out, cfg, ov, rep):
    corr = cfg["correction"]
    energy = energy_map(a)
    rows = []
    skip = set(ov.get("exclude", []))
    for p in cfg["platforms"]:
        if p["id"] in skip:
            continue
        x0, y0, cw, ch = best_crop(a, p["w"] / p["h"], ov.get("focus"), energy)
        img = to_img(a[y0:y0 + ch, x0:x0 + cw])
        up = p["w"] / cw
        img = img.resize((p["w"], p["h"]), Image.LANCZOS)
        img = sharpen(img, corr, cw / p["w"])
        name = f"{p['id']}_{p['w']}x{p['h']}.jpg"
        save_jpeg(img, out / stem / name, cfg["jpeg_quality"])
        flag = "" if up <= 1.0 else ("UPSCALED x%.2f" % up if up <= cfg["max_upscale"] else "TOO SMALL x%.2f: replace source" % up)
        rows.append({"file": f"{stem}/{name}", "platform": p["label"], "size": f"{p['w']}x{p['h']}",
                     "crop": f"{x0},{y0},{cw}x{ch}", "flag": flag})
    rep["exports"] = rows
    return rows


_CMAPS = {}


def _ttf(fp):
    if fp.suffix.lower() in (".woff2", ".woff"):
        cache = Path.home() / ".cache" / "photo_kit" / (fp.stem + ".ttf")
        if not cache.exists():
            from fontTools.ttLib import TTFont
            cache.parent.mkdir(parents=True, exist_ok=True)
            f = TTFont(str(fp)); f.flavor = None; f.save(str(cache))
        fp = cache
    if str(fp) not in _CMAPS:
        try:
            from fontTools.ttLib import TTFont
            _CMAPS[str(fp)] = set(TTFont(str(fp)).getBestCmap())
        except Exception:
            _CMAPS[str(fp)] = None
    return fp, _CMAPS[str(fp)]


class Fonts:
    def __init__(self, cfg, size, weight):
        paths = cfg["brand"].get("font") or []
        paths = [paths] if isinstance(paths, str) else paths
        self.stack = []
        for p in paths:
            fp = rel(cfg, p)
            if not (fp and fp.exists()):
                continue
            fp, cov = _ttf(fp)
            ft = ImageFont.truetype(str(fp), size)
            try:
                ft.set_variation_by_axes([weight])
            except Exception:
                pass
            self.stack.append((ft, cov))
        self.stack.append((ImageFont.load_default(size=size), None))

    def runs(self, text):
        out = []
        for ch in text:
            ft = next(f for f, cov in self.stack if cov is None or ord(ch) in cov or ch == " ")
            if out and out[-1][0] is ft:
                out[-1][1] += ch
            else:
                out.append([ft, ch])
        return out

    def width(self, draw, text):
        return sum(draw.textlength(t, font=f) for f, t in self.runs(text))

    def draw(self, draw, xy, text, fill):
        x, y = xy
        for f, t in self.runs(text):
            draw.text((x, y), t, font=f, fill=fill)
            x += draw.textlength(t, font=f)


def font(cfg, size, weight=500):
    return Fonts(cfg, size, weight)


def before_after_sheets(items, out, cfg, prefix=""):
    H, pad = 420, 16
    rows_per = 4
    pages = [items[i:i + rows_per] for i in range(0, len(items), rows_per)]
    f, fs = font(cfg, 22, 600), font(cfg, 17, 400)
    paths = []
    for n, page in enumerate(pages, 1):
        blocks = []
        for it in page:
            b = to_img(it["before"]); aimg = to_img(it["after"])
            b = b.resize((round(b.width * H / b.height), H)); aimg = aimg.resize((round(aimg.width * H / aimg.height), H))
            blocks.append((it, b, aimg))
        width = max(b.width + aimg.width for _, b, aimg in blocks) + pad * 3
        height = sum(H + 110 for _ in blocks) + pad
        sheet = Image.new("RGB", (width, height), "#f4f4f2")
        d = ImageDraw.Draw(sheet)
        y = pad
        for it, b, aimg in blocks:
            f.draw(d, (pad, y), f"{it['stem']}   BEFORE  |  AFTER", "#222")
            sheet.paste(b, (pad, y + 34)); sheet.paste(aimg, (pad * 2 + b.width, y + 34))
            notes = " · ".join(f"{k}: {v}" for k, v in it["log"].items() if k in ("verticals", "wb", "levels"))
            for i, ln in enumerate(wrap(d, notes, fs, width - pad * 2)[:3]):
                fs.draw(d, (pad, y + 40 + H + i * 22), ln, "#555")
            y += H + 110
        p = out / "_review" / f"{prefix}before_after_{n:02d}.jpg"
        save_jpeg(sheet, p, 85)
        paths.append(p)
    return paths


def cmd_process(args):
    cfg = load_config(args.config)
    ovs = json.loads(Path(args.overrides).read_text(encoding="utf-8")) if args.overrides else {}
    src, out = Path(args.input), Path(args.output)
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in EXTS and "_kit" not in p.parts)
    if args.only:
        keep = set(args.only.split(","))
        files = [p for p in files if p.stem in keep or f"{p.parent.name}__{p.stem}" in keep]
    report, manifest, sheet_items = {}, [], []
    for p in files:
        stem = (p.parent.name + "__" + p.stem) if p.parent != src else p.stem
        stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem)[:80]
        ov = ovs.get(stem, {})
        if ov.get("skip"):
            continue
        log = {"source": str(p)}
        for old in list((out / stem).glob("*.jpg")) + list((out / stem).glob("00_master.*")):
            if not old.name.startswith("card_"):
                old.unlink()
        with Image.open(p) as _src:
            src_w, src_h = ImageOps.exif_transpose(_src).size
        orig = load_rgb(p, cfg["master_long_edge"])
        fixed = correct(orig.copy(), cfg["correction"], ov, log)
        master = to_img(fixed)
        (out / stem).mkdir(parents=True, exist_ok=True)
        Image.frombytes("RGB", master.size, master.tobytes()).save(out / stem / "00_master.png", optimize=False)
        log["master"] = f"{master.width}x{master.height}"
        g = cv2.cvtColor(np.asarray(master.resize((1200, round(1200 * master.height / master.width)) if master.width >= master.height else (round(1200 * master.width / master.height), 1200), Image.LANCZOS)), cv2.COLOR_RGB2GRAY)
        sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
        log["quality"] = {"source_px": f"{src_w}x{src_h}", "sharpness": round(sharp), "short_edge": min(master.size)}
        manifest += export_all(fixed, stem, out, cfg, ov, log)
        report[stem] = log
        sheet_items.append({"stem": stem, "before": orig, "after": fixed, "log": log})
        print(f"{stem}: {log.get('verticals')} | {log.get('wb')}")
    done = set(report)
    rp, mp = out / "report.json", out / "manifest.csv"
    if rp.exists():
        old = json.loads(rp.read_text(encoding="utf-8"))
        report = {**{k: v for k, v in old.items() if k not in done}, **report}
    if mp.exists():
        with open(mp, encoding="utf-8") as fh:
            manifest = [r for r in csv.DictReader(fh) if r["file"].split("/")[0] not in done] + manifest
    with open(mp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "platform", "size", "crop", "flag"])
        w.writeheader(); w.writerows(sorted(manifest, key=lambda r: r["file"]))
    rp.write_text(json.dumps(dict(sorted(report.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    sheets = before_after_sheets(sheet_items, out, cfg, prefix="rerun_" if args.only else "")
    flags = [m for m in manifest if m["flag"]]
    print(f"\n{len(report)} photos, {len(manifest)} exports, {len(flags)} flagged; sheets: {[str(s) for s in sheets]}")
    for m in flags:
        print("  FLAG", m["file"], m["flag"])


def recolor_logo(path, color, height):
    im = Image.open(path).convert("RGBA")
    alpha = im.split()[3]
    if alpha.getextrema() == (255, 255):
        alpha = ImageOps.invert(im.convert("L"))
    bbox = alpha.getbbox()
    alpha = alpha.crop(bbox)
    solid = Image.new("RGBA", alpha.size, color); solid.putalpha(alpha)
    s = height / solid.height
    return solid.resize((round(solid.width * s), height), Image.LANCZOS)


def wrap(draw, text, ft, width):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if ft.width(draw, t) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def card(master, spec, fmt, cfg):
    b = cfg["brand"]; L = cfg["card_formats"][fmt]
    W, Hh = L["w"], L["h"]
    m = round(W * L["margin"])
    canvas = Image.new("RGB", (W, Hh), b["background"])
    logo_path = rel(cfg, b.get("logo"))
    logo_path = logo_path if logo_path and logo_path.exists() else None
    sw = rel(cfg, b.get("swatch"))
    if sw and sw.exists():
        tex = ImageOps.fit(Image.open(sw).convert("RGB"), (W, Hh), Image.LANCZOS)
        canvas = Image.blend(tex, Image.new("RGB", (W, Hh), b["background"]), b.get("swatch_fade", 0.35))
    d = ImageDraw.Draw(canvas)
    band_h = round(Hh * L["band"]) if L["band"] else 0
    photo_box = (m, m, W - m, Hh - band_h - (m if not band_h else round(m * 0.6)))
    pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
    if spec.get("fit") == "whole":
        s = min(pw / master.width, ph / master.height)
        nw, nh = round(master.width * s), round(master.height * s)
        photo = master.resize((nw, nh), Image.LANCZOS)
        photo_box = (photo_box[0] + (pw - nw) // 2, photo_box[1], photo_box[0] + (pw - nw) // 2 + nw, photo_box[1] + nh)
        pw, ph = nw, nh
    else:
        a = np.asarray(master).astype(np.float32) / 255.0
        x0, y0, cw, ch = best_crop(a, pw / ph, spec.get("focus"), energy_map(a))
        photo = master.crop((x0, y0, x0 + cw, y0 + ch)).resize((pw, ph), Image.LANCZOS)
    photo = sharpen(photo, cfg["correction"], master.width / max(pw, 1))
    canvas.paste(photo, photo_box[:2])
    d.rectangle((photo_box[0], photo_box[3] + 1, photo_box[0] + round(pw * 0.18), photo_box[3] + max(3, round(Hh * 0.004))), fill=b["accent"])
    if band_h:
        y_top = Hh - band_h
        d.rectangle((0, y_top, W, Hh), fill=b["primary"])
        tx = m
        if logo_path:
            logo = recolor_logo(logo_path, b["on_primary"], round(band_h * L["logo"]))
            canvas.paste(logo, (m, y_top + (band_h - logo.height) // 2), logo)
            tx = m + logo.width + round(m * 0.9)
        tw = W - tx - m
        head = spec.get("headline", "")
        if head and tw > 200:
            size = round(band_h * L["text"])
            ft = font(cfg, size, 600)
            lines = wrap(d, head, ft, tw)
            while len(lines) > 2 and size > 14:
                size -= 2; ft = font(cfg, size, 600); lines = wrap(d, head, ft, tw)
            sub = spec.get("subline", b.get("url", ""))
            fsub = font(cfg, round(size * 0.62), 400)
            total = len(lines) * round(size * 1.18) + (round(size * 0.9) if sub else 0)
            y = y_top + (band_h - total) // 2
            for ln in lines:
                ft.draw(d, (tx, y), ln, b["on_primary"]); y += round(size * 1.18)
            if sub:
                fsub.draw(d, (tx, y + round(size * 0.12)), sub, b["accent_light"])
    elif logo_path:
        logo = recolor_logo(logo_path, b["primary"], round(m * 0.55))
        canvas.paste(logo, (W - m - logo.width, Hh - m + (m - logo.height) // 2), logo)
    return canvas


def cmd_cards(args):
    cfg = load_config(args.config)
    out = Path(args.output)
    specs = json.loads(Path(args.cards).read_text(encoding="utf-8"))
    for spec in specs:
        mp = out / spec["source"] / "00_master.png"
        if not mp.exists():
            print("missing master (run process first):", mp); continue
        master = Image.open(mp).convert("RGB")
        for fmt in spec.get("formats", list(cfg["card_formats"])):
            img = card(master, spec, fmt, cfg)
            L = cfg["card_formats"][fmt]
            p = out / spec["source"] / f"card_{spec.get('id', 'x')}_{fmt}_{L['w']}x{L['h']}.jpg"
            save_jpeg(img, p, cfg["jpeg_quality"])
            print("card:", p)


def cmd_verify(args):
    out = Path(args.output)
    bad = 0
    for p in sorted(list(out.rglob("*.jpg")) + list(out.rglob("*.png"))):
        raw = p.read_bytes()
        im = Image.open(p)
        leaks = [k for k in ("exif", "icc_profile", "xmp", "photoshop", "comment", "XML:com.adobe.xmp") if k in im.info]
        leaks += [t for t in (b"Exif\x00", b"http://ns.adobe.com/xap", b"c2pa", b"jumb") if t in raw]
        if leaks:
            bad += 1; print("METADATA", p, leaks)
    print("verify:", "clean" if not bad else f"{bad} files with metadata")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("process"); a.add_argument("input"); a.add_argument("output")
    a.add_argument("--config"); a.add_argument("--overrides"); a.add_argument("--only")
    b = sub.add_parser("cards"); b.add_argument("output"); b.add_argument("cards"); b.add_argument("--config")
    c = sub.add_parser("verify"); c.add_argument("output")
    args = ap.parse_args()
    {"process": cmd_process, "cards": cmd_cards, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    main()
