"""Manual e2e check: render Chinese text to an image, run real OCR + layout + API parse endpoint."""

from __future__ import annotations

import io
import json
import sys

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def make_image() -> bytes:
    font = None
    for path in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, 48)
            break
        except OSError:
            continue
    if font is None:
        raise SystemExit("no Chinese font found")
    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 40), "全网最低价", fill="black", font=font)
    draw.text((80, 260), "限时抢购 买到就是赚到", fill="black", font=font)
    draw.text((780, 450), "广告", fill="black", font=ImageFont.truetype(FONT_CANDIDATES[0], 22))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    from fastapi.testclient import TestClient

    from adguard_pipeline.api import app

    data = make_image()
    client = TestClient(app)
    response = client.post(
        "/v1/parse",
        data={"asset_type": "image", "mime_type": "image/png", "filename": "creative.png"},
        files={"file": ("creative.png", data, "image/png")},
    )
    print("status:", response.status_code)
    body = response.json()
    print(json.dumps({k: body[k] for k in ("asset_type", "text", "degraded", "warnings", "role_counts", "scan")}, ensure_ascii=False, indent=1))
    print("elements:", json.dumps(body["elements"][:4], ensure_ascii=False))
    if "全网" not in body["text"]:
        print("OCR_MISS_EXPECTED_TEXT", file=sys.stderr)
        raise SystemExit(1)
    print("PARSE_OCR_OK")


if __name__ == "__main__":
    main()
