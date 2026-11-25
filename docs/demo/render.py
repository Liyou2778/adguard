"""Render the one-page demo HTML to A4 PDF + PNG preview with Edge via Playwright."""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = REPO / ".tmp" / "pw-tmp"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TMP)
os.environ["TMP"] = str(TMP)
os.environ["TMPDIR"] = str(TMP)

HTML = Path(__file__).resolve().parent / "adguard-交付验收一页纸.html"
PDF = Path(__file__).resolve().parent / "adguard-交付验收一页纸.pdf"
PNG = Path(__file__).resolve().parent / "adguard-交付验收一页纸_preview.png"

from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 820, "height": 1200})
        page.goto(HTML.as_uri())
        page.emulate_media(media="print")
        page.wait_for_timeout(400)
        height = page.evaluate("document.body.scrollHeight")
        width = page.evaluate("document.body.scrollWidth")
        print(f"screen/print layout: width={width}px height={height}px (A4 usable ~1060px at 96dpi)")
        page.pdf(path=str(PDF), print_background=True, prefer_css_page_size=True)
        page.emulate_media(media="screen")
        page.screenshot(path=str(PNG), full_page=True)
        browser.close()
    raw = PDF.read_bytes()
    counts = [int(value) for value in re.findall(rb"/Count\s+(\d+)", raw)]
    pages = max(counts) if counts else 1
    print(f"pdf: {PDF.stat().st_size} bytes, pages={pages}")
    print(f"png: {PNG.stat().st_size} bytes")
    if pages != 1:
        raise SystemExit(f"expected a single-page A4 deliverable, got {pages} pages")


if __name__ == "__main__":
    main()
