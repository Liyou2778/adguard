"""Landing-page ingestion (FR-1 URL assets + FR-2 render).

Scope decision (documented): full headless Chromium rendering is not available on the 2C4G ECS,
so the production path fetches the HTML, extracts visible text (DOM-only) and marks the result
`degraded=true` with an explicit warning. If Playwright/Chromium is installed and
ADGUARD_PLAYWRIGHT=1, a screenshot is additionally captured and reported in meta.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx

from .types import ParsedAsset

MAX_BYTES = int(os.environ.get("ADGUARD_URL_MAX_BYTES", str(2 * 1024 * 1024)))
MAX_CHARS = int(os.environ.get("ADGUARD_URL_MAX_CHARS", "20000"))
TIMEOUT = float(os.environ.get("ADGUARD_URL_TIMEOUT", "10"))
MAX_REDIRECTS = 5


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
        import re

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


def _screenshot(url: str) -> tuple[bool, str]:
    """Optional Playwright screenshot; disabled by default on the 2C4G ECS."""
    if os.environ.get("ADGUARD_PLAYWRIGHT", "0") not in {"1", "true", "yes"}:
        return False, "headless render disabled (ADGUARD_PLAYWRIGHT!=1); DOM text only"
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright unavailable: {exc}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(url, timeout=int(TIMEOUT * 1000), wait_until="networkidle")
            page.screenshot(full_page=True, path="/tmp/adguard-landing.png")
            browser.close()
        return True, "/tmp/adguard-landing.png"
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright screenshot failed: {exc}"


def fetch_url(url: str) -> ParsedAsset:
    warnings: list[str] = []
    current = guard_url(url)
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "AdGuard-P2/1.0 (+compliance-bot)"}) as client:
        for hop in range(MAX_REDIRECTS + 1):
            response = client.get(current, follow_redirects=False)
            if response.is_redirect and response.headers.get("location"):
                current = guard_url(str(response.url.join(response.headers["location"])))
                continue
            break
        else:
            warnings.append(f"redirect limit exceeded ({MAX_REDIRECTS})")
            response = client.get(current, follow_redirects=False)
    content = response.content[:MAX_BYTES]
    truncated = len(response.content) > MAX_BYTES
    if truncated:
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
    title, text = _extract(content.decode(response.encoding or "utf-8", "replace"))
    shot_ok, shot_detail = _screenshot(current)
    if not shot_ok:
        warnings.append(shot_detail)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        warnings.append(f"page text truncated at {MAX_CHARS} chars")
    degraded = not text.strip() or not shot_ok
    return ParsedAsset(
        asset_type="landing_page",
        text=text,
        degraded=degraded,
        warnings=warnings,
        meta={
            "url": current,
            "title": title,
            "status_code": response.status_code,
            "content_type": content_type,
            "render": "screenshot" if shot_ok else "dom-only",
            "screenshot": shot_detail if shot_ok else None,
        },
    )
