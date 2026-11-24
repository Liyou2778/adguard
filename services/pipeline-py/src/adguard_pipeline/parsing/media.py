"""Image / video / text parsing for the asset pipeline (FR-1 + FR-2).

- image: OCR -> layout (title/body/corner) with original bboxes
- video: keyframe extraction via ffmpeg -> per-frame OCR -> de-duplicated text with frame refs
- text : UTF-8 / GB18030 decoding with replacement tracking

Every degradation is explicit (`degraded` + `warnings`) so the reviewer knows why a coordinate or
text is missing instead of silently receiving an empty result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import layout, ocr
from .types import Element, Line, ParsedAsset


@dataclass
class _Dims:
    width: float
    height: float


def _image_dims(data: bytes) -> _Dims | None:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            return _Dims(float(image.shape[1]), float(image.shape[0]))
    except Exception:  # noqa: BLE001
        pass
    return None


def _assemble_text(lines: list[Line], elements: list[Element]) -> str:
    if lines:
        return "\n".join(line.text for line in lines)
    return "\n".join(e.text for e in elements)


def parse_image(data: bytes, mime: str = "", filename: str = "") -> ParsedAsset:
    warnings: list[str] = []
    dims = _image_dims(data)
    try:
        ocr_out = ocr.ocr_image(data)
        elements = ocr_out.elements
        warnings.extend(ocr_out.warnings)
        engine = ocr_out.engine
    except ocr.OcrUnavailable as exc:
        return ParsedAsset(
            asset_type="image",
            text="",
            elements=[],
            lines=[],
            role_counts={},
            degraded=True,
            warnings=[str(exc), "OCR unavailable: image routed to manual review"],
            meta={"mime": mime, "filename": filename, "engine": None, "dims": None},
        )
    width = dims.width if dims else 0.0
    height = dims.height if dims else 0.0
    elements, lines, counts, layout_warnings = layout.analyze(elements, width, height)
    warnings.extend(layout_warnings)
    text = _assemble_text(lines, elements)
    degraded = not text.strip()
    if degraded:
        warnings.append("OCR produced no text for this image")
    return ParsedAsset(
        asset_type="image",
        text=text,
        elements=elements,
        lines=lines,
        role_counts=counts,
        degraded=degraded,
        warnings=warnings,
        meta={
            "mime": mime,
            "filename": filename,
            "engine": engine,
            "dims": {"width": width, "height": height},
        },
    )


def _ffmpeg_bin(name: str) -> str | None:
    return shutil.which(name)


def _probe_duration(path: Path) -> float | None:
    ffprobe = _ffmpeg_bin("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed binary, no shell
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return None


def parse_video(data: bytes, mime: str = "", filename: str = "") -> ParsedAsset:
    warnings: list[str] = []
    max_frames = int(os.environ.get("ADGUARD_VIDEO_MAX_FRAMES", "8"))
    max_seconds = float(os.environ.get("ADGUARD_VIDEO_MAX_SECONDS", "300"))
    ffmpeg = _ffmpeg_bin("ffmpeg")
    if not ffmpeg:
        return ParsedAsset(
            asset_type="video",
            text="",
            degraded=True,
            warnings=["ffmpeg not installed: video keyframe OCR unavailable, routed to manual review"],
            meta={"mime": mime, "filename": filename, "engine": None, "frames": 0},
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="adguard-video-"))
    video_path = tmpdir / "input.bin"
    try:
        video_path.write_bytes(data)
        duration = _probe_duration(video_path)
        if duration is not None and duration > max_seconds:
            warnings.append(f"video duration {duration:.1f}s exceeds limit {max_seconds:.0f}s; only first frames parsed")
        fps = 1.0
        if duration and duration > 0:
            fps = max(0.05, min(1.0, max_frames / duration))
        frame_pattern = tmpdir / "frame-%03d.jpg"
        cmd = [
            ffmpeg, "-v", "error", "-i", str(video_path),
            "-vf", f"fps={fps:.4f},scale=1280:-2",
            "-frames:v", str(max_frames), "-q:v", "4", str(frame_pattern),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
        if proc.returncode != 0:
            warnings.append(f"ffmpeg keyframe extraction failed: {proc.stderr.strip()[:200]}")
        frames = sorted(tmpdir.glob("frame-*.jpg"))
        if not frames:
            return ParsedAsset(
                asset_type="video",
                text="",
                frames=0,
                degraded=True,
                warnings=warnings + ["no keyframes extracted from video"],
                meta={"mime": mime, "filename": filename, "engine": "rapidocr-onnxruntime", "frames": 0},
            )
        all_lines: list[Line] = []
        seen: set[str] = set()
        frame_meta: list[dict] = []
        for idx, frame_path in enumerate(frames):
            frame_bytes = frame_path.read_bytes()
            dims = _image_dims(frame_bytes)
            try:
                ocr_out = ocr.ocr_image(frame_bytes)
            except ocr.OcrUnavailable as exc:
                return ParsedAsset(
                    asset_type="video",
                    text="",
                    frames=0,
                    degraded=True,
                    warnings=[str(exc), "OCR unavailable: video routed to manual review"],
                    meta={"mime": mime, "filename": filename, "engine": None, "frames": len(frames)},
                )
            for element in ocr_out.elements:
                element.frame = idx
            elements, lines, _counts, _w = layout.analyze(
                ocr_out.elements,
                dims.width if dims else 0.0,
                dims.height if dims else 0.0,
            )
            kept = 0
            for line in lines:
                line.frame = idx
                key = line.text.strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                all_lines.append(line)
                kept += 1
            frame_meta.append({"frame": idx, "kept_lines": kept, "elements": len(elements)})
        text = "\n".join(f"[frame {line.frame}] {line.text}" for line in all_lines if line.frame is not None)
        degraded = not text.strip()
        if degraded:
            warnings.append("OCR produced no text across extracted video frames")
        return ParsedAsset(
            asset_type="video",
            text=text,
            lines=all_lines,
            frames=len(frames),
            degraded=degraded,
            warnings=warnings,
            meta={
                "mime": mime,
                "filename": filename,
                "engine": "rapidocr-onnxruntime",
                "frames": len(frames),
                "duration_seconds": duration,
                "frame_stats": frame_meta,
            },
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def parse_text(data: bytes, mime: str = "", filename: str = "") -> ParsedAsset:
    warnings: list[str] = []
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", "replace")
        warnings.append("text asset had undecodable bytes; replacement characters inserted")
    return ParsedAsset(
        asset_type="text",
        text=text,
        degraded=False,
        warnings=warnings,
        meta={"mime": mime, "filename": filename, "engine": "utf8"},
    )
