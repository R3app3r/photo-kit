#!/usr/bin/env python3
"""html_to_pdf: print a local HTML file to PDF with Microsoft Edge or Chrome (headless).

  python html_to_pdf.py page.html out.pdf [--browser PATH]
"""
import argparse, subprocess, sys
from pathlib import Path

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("pdf")
    ap.add_argument("--browser")
    a = ap.parse_args()
    browser = a.browser or next((c for c in CANDIDATES if Path(c).exists()), None)
    if not browser:
        sys.exit("no Edge/Chrome found: pass --browser")
    src = Path(a.html).resolve().as_uri()
    out = str(Path(a.pdf).resolve())
    try:
        Path(out).unlink(missing_ok=True)
    except PermissionError:
        sys.exit(f"cannot replace {out}: it is open in another program (close the PDF viewer and retry)")
    r = subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--allow-file-access-from-files", f"--print-to-pdf={out}", src],
        capture_output=True, text=True, timeout=120,
    )
    if not Path(out).exists():
        sys.exit(f"PDF not written: {r.stderr[-500:]}")
    print(out, f"{Path(out).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
