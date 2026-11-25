"""Unit tests for the parsing subsystem (FR-1 extras + FR-2)."""

from __future__ import annotations

import http.server
import io
import threading
from pathlib import Path

import pytest

from adguard_pipeline.parsing import layout, media, scan, web
from adguard_pipeline.parsing.types import Element


EICAR = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def test_scan_detects_eicar():
    result = scan.scan_bytes(EICAR.encode())
    assert result.status == "infected"
    assert result.engine == "eicar"


def test_scan_clean_text():
    result = scan.scan_bytes("全网最低价".encode())
    assert result.status == "clean"


def test_scan_unknown_when_clamd_unreachable(monkeypatch):
    monkeypatch.setenv("ADGUARD_CLAMD_ADDR", "127.0.0.1:1")
    result = scan.scan_bytes(b"hello")
    assert result.status == "unknown"
    assert any("clamav" in w for w in result.warnings)


def _el(text: str, x0: float, y0: float, x1: float, y1: float) -> Element:
    return Element(text=text, bbox=[x0, y0, x1, y1], confidence=0.9)


def test_layout_roles_and_lines():
    elements = [
        _el("全网最低价", 100, 40, 500, 100),          # top, large -> title
        _el("限时抢购", 120, 300, 420, 340),           # body
        _el("广告", 940, 960, 990, 990),               # bottom-right -> corner
    ]
    classified, lines, counts, warnings = layout.analyze(elements, 1000, 1000)
    assert counts == {"title": 1, "body": 1, "corner": 1}
    assert [l.role for l in lines] == ["title", "body", "corner"]
    assert warnings == []


def test_layout_merges_same_row():
    elements = [
        _el("活动", 100, 200, 200, 240),
        _el("规则详见门店公告", 210, 202, 600, 242),
    ]
    _, lines, _, _ = layout.analyze(elements, 1000, 1000)
    assert len(lines) == 1
    assert "活动" in lines[0].text and "规则详见门店公告" in lines[0].text


def test_parse_text_gbk():
    parsed = media.parse_text("全网最低价".encode("gb18030"))
    assert parsed.text == "全网最低价"
    assert parsed.degraded is False


def test_parse_image_degrades_when_ocr_unavailable(monkeypatch):
    def boom(_data):
        raise media.ocr.OcrUnavailable("engine missing")

    monkeypatch.setattr(media.ocr, "ocr_image", boom)
    parsed = media.parse_image(b"\x89PNG\r\n\x1a\n")
    assert parsed.degraded is True
    assert any("OCR unavailable" in w for w in parsed.warnings)


def test_parse_image_with_ocr(monkeypatch):
    class Output:
        elements = [_el("全网最低价", 10, 10, 200, 60)]
        engine = "fake"
        warnings: list[str] = []

    monkeypatch.setattr(media.ocr, "ocr_image", lambda _data: Output())
    parsed = media.parse_image(b"fake-image")
    assert "全网最低价" in parsed.text
    assert parsed.elements[0].role == "title"
    assert parsed.meta["engine"] == "fake"


def test_parse_video_degrades_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "_ffmpeg_bin", lambda _name: None)
    parsed = media.parse_video(b"fake-mp4")
    assert parsed.degraded is True
    assert any("ffmpeg" in w for w in parsed.warnings)


def test_guard_url_blocks_private():
    with pytest.raises(web.UnsafeURLError):
        web.guard_url("http://127.0.0.1/admin")
    with pytest.raises(web.UnsafeURLError):
        web.guard_url("file:///etc/passwd")


def test_fetch_url_dom_text(monkeypatch):
    monkeypatch.setenv("ADGUARD_URL_ALLOW_PRIVATE", "1")
    html = (
        "<html><head><title>测试落地页</title><script>var x=1;</script></head>"
        "<body><h1>全网最低价</h1><p>限时抢购</p></body></html>"
    ).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        parsed = web.fetch_url(f"http://127.0.0.1:{port}/landing")
    finally:
        server.shutdown()
    assert "全网最低价" in parsed.text
    assert parsed.meta["title"] == "测试落地页"
    assert parsed.degraded is True  # no headless render on this box
    assert any(("ADGUARD_PLAYWRIGHT" in w) or ("DOM text" in w) for w in parsed.warnings)

# --------------------------------------------------------------------------- ASR (video speech)
from adguard_pipeline.parsing import asr  # noqa: E402


def test_asr_disabled_raises(monkeypatch):
    monkeypatch.delenv("ADGUARD_ASR_ENABLED", raising=False)
    assert asr.enabled() is False
    with pytest.raises(asr.AsrUnavailable):
        asr.transcribe_bytes(b"RIFF....")


def test_transcribe_video_appends_asr_text(monkeypatch):
    monkeypatch.setenv("ADGUARD_ASR_ENABLED", "1")
    monkeypatch.setattr(media, "_extract_audio", lambda _ffmpeg, _path: (b"wav-bytes", []))
    monkeypatch.setattr(media.asr, "transcribe_bytes",
                        lambda data, suffix=".wav": asr.AsrOutput(
                            text="全网最低价，限时抢购", segments=[{"start": 0.0, "end": 2.1, "text": "全网最低价，限时抢购"}],
                            language="zh"))
    text, meta = media._transcribe_video("ffmpeg", Path("video.mp4"))
    assert "全网最低价" in text
    assert meta["enabled"] is True and meta["degraded"] is False
    assert meta["segments"][0]["start"] == 0.0


def test_transcribe_video_degrades_on_audio_failure(monkeypatch):
    monkeypatch.setenv("ADGUARD_ASR_ENABLED", "1")
    monkeypatch.setattr(media, "_extract_audio", lambda _ffmpeg, _path: (None, ["audio extraction failed"]))
    text, meta = media._transcribe_video("ffmpeg", Path("video.mp4"))
    assert text == ""
    assert meta["degraded"] is True
    assert any("audio extraction" in w for w in meta["warnings"])


def test_transcribe_video_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("ADGUARD_ASR_ENABLED", raising=False)
    text, meta = media._transcribe_video("ffmpeg", Path("video.mp4"))
    assert text == "" and meta["enabled"] is False