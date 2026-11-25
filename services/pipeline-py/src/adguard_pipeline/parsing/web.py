"""Landing-page ingestion (FR-1 URL assets + FR-2 render).

Two render modes:
- `screenshot` (optional, ADGUARD_PLAYWRIGHT=1): headless Chromium/Edge renders the page,
  the screenshot is OCR'd so landing-page text has the same pixel bbox evidence as image
  creatives. Requires the `playwright` python package + a browser (Edge/Chrome/Chromium).
- `dom-only` (default; always available): fetch HTML and extract visible DOM text. Documented
  degradation: no pixel coordinates, marked `degraded=true` so reviewers know why.

The 2C4G ECS keeps `dom-only` as the default to protect memory; screenshot rendering runs on
the demo/worker host or an optional worker with more RAM (see ADGUARD_PLAYWRIGHT* env vars).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import httpx

from . import layout, ocr
from .types import Element, ParsedAsset

MAX_BYTES = int(os.environ.get("ADGUARD_URL_MAX_BYTES", str(2 * 1024 * 1024)))
MAX_CHARS = int(os.environ.get("ADGUARD_URL_MAX_CHARS", "20000"))
TIMEOUT = float(os.environ.get("ADGUARD_URL_TIMEOUT", "10"))
MAX_REDIRECTS = 5
SCREENSHOT_DIR = os.environ.get("ADGUARD_SCREENSHOT_DIR", "")


class UnsafeURLError(ValueError):
    """Raised when the URL is invalid or points at a private / loopback / link-local address."""


def _is_public_ip(host: str) -> bool:
    if os.environ.get("ADGUARD_URL_ALLOW_PRIVATE", "0") in {"1", "true", "yes"}:
        # test-only escape hatch: unit tests point at a local HTTP server
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def guard_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"unsupported scheme {parsed.scheme!r} (only http/https)")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no hostname")
    if not _is_public_ip(parsed.hostname):
        raise UnsafeURLError(f"host {parsed.hostname!r} resolves to a private/internal address")
    return parsed.geturl()


def _extract(html: str) -> tuple[str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:  # noqa: BLE001 - stdlib fallback keeps the service alive
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return "", " ".join(text.split())
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return title, text


def render_enabled() -> bool:
    return os.environ.get("ADGUARD_PLAYWRIGHT", "0").lower() in {"1", "true", "yes"}


def _launch_options() -> dict:
    executable = os.environ.get("ADGUARD_PLAYWRIGHT_EXECUTABLE", "").strip()
    channel = os.environ.get("ADGUARD_PLAYWRIGHT_CHANNEL", "").strip()
    options: dict = {"headless": True}
    if executable:
        options["executable_path"] = executable
        return options
    if channel:
        options["channel"] = channel
        return options
    if os.name == "nt":
        options["channel"] = "msedge"
    # Linux: playwright bundle if installed; otherwise Chromium from PATH is picked up by
    # playwright's default launch when installed as a system package.
    return options


def screenshot(url: str) -> tuple[bytes | None, dict]:
    """Render the page headlessly and return (png bytes, meta). Never raises."""
    if not render_enabled():
        return None, {"render": "dom-only", "reason": "ADGUARD_PLAYWRIGHT not enabled"}
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, {"render": "dom-only", "reason": f"playwright unavailable: {exc}"}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**_launch_options())
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, timeout=int(TIMEOUT * 1000), wait_until="networkidle")
            png = page.screenshot(full_page=True)
            title = page.title()
            browser.close()
        return png, {"render": "screenshot", "title": title, "bytes": len(png)}
    except Exception as exc:  # noqa: BLE001 - degrade to DOM-only, never fail the parse
        return None, {"render": "dom-only", "reason": f"render failed: {exc}"}


def _persist_screenshot(png: bytes) -> str | None:
    if not SCREENSHOT_DIR or not png:
        return None
    try:
        from pathlib import Path

        import hashlib

        directory = Path(SCREENSHOT_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{hashlib.sha256(png).hexdigest()[:24]}.png"
        path.write_bytes(png)
        return str(path)
    except OSError:
        return None


def _screenshot_elements(png: bytes) -> tuple[list[Element], list, dict, list[str]]:
    try:
        output = ocr.ocr_image(png)
        elements = output.elements
        warnings = list(output.warnings)
        engine = output.engine
    except ocr.OcrUnavailable as exc:
        return [], [], {}, [str(exc)]
    width, height = 1280.0, 900.0
    try:
        import io

        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(png)) as image:
            width, height = float(image.width), float(image.height)
    except Exception:  # noqa: BLE001 - keep fallback viewport size
        pass
    elements, lines, counts, layout_warnings = layout.analyze(elements, width, height)
    warnings.extend(layout_warnings)
    return elements, lines, counts, warnings + [f"ocr engine={engine}"]


def fetch_url(url: str) -> ParsedAsset:
    warnings: list[str] = []
    current = guard_url(url)
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "AdGuard-P2/1.0 (+compliance-bot)"}) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            response = client.get(current, follow_redirects=False)
            if response.is_redirect and response.headers.get("location"):
                current = guard_url(str(response.url.join(response.headers["location"])))
                continue
            break
        else:
            warnings.append(f"redirect limit exceeded ({MAX_REDIRECTS})")
            response = client.get(current, follow_redirects=False)
    content = response.content[:MAX_BYTES]
    if len(response.content) > MAX_BYTES:
        warnings.append(f"response truncated at {MAX_BYTES} bytes")
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "xml" not in content_type and "text" not in content_type:
        return ParsedAsset(
            asset_type="landing_page",
            text="",
            degraded=True,
            warnings=warnings + [f"unsupported content-type {content_type!r}"],
            meta={"url": current, "status_code": response.status_code, "content_type": content_type},
        )

    dom_title, dom_text = _extract(content.decode(response.encoding or "utf-8", "replace"))
    png, render_meta = screenshot(current)
    elements: list[Element] = []
    lines: list = []
    role_counts: dict[str, int] = {}
    rendered_text = ""
    if png:
        elements, lines, role_counts, ocr_warnings = _screenshot_elements(png)
        warnings.extend(ocr_warnings)
        rendered_text = "\n".join(line.text for line in lines).strip()
        screenshot_path = _persist_screenshot(png)
        if screenshot_path:
            render_meta["screenshot_path"] = screenshot_path
    else:
        warnings.append(render_meta.get("reason", "headless render unavailable; DOM text only"))

    text = rendered_text or dom_text
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        warnings.append(f"page text truncated at {MAX_CHARS} chars")
    degraded = not png or not text.strip()
    if not text.strip():
        warnings.append("landing page produced no text")
    return ParsedAsset(
        asset_type="landing_page",
        text=text,
        elements=elements,
        lines=lines,
        role_counts=role_counts,
        degraded=degraded,
        warnings=warnings,
        meta={
            "url": current,
            "title": render_meta.get("title") or dom_title,
            "status_code": response.status_code,
            "content_type": content_type,
            "render": render_meta.get("render", "dom-only"),
            "screenshot_bytes": render_meta.get("bytes"),
            "screenshot_path": render_meta.get("screenshot_path"),
            "dom_text_chars": len(dom_text),
        },
    )
