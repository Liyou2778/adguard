"""Optional speech-to-text for video assets (FR-1/FR-2 enhancement).

Uses faster-whisper (CTranslate2, CPU) when enabled; the model (default `tiny`, int8) is
loaded lazily and cached for the life of the worker. ASR text is appended to the parsed text so
the rule judge can evaluate spoken claims too; segments are returned for evidence/audit.

Environment:
  ADGUARD_ASR_ENABLED=1        enable ASR (default off: ECS 2C4G keeps it optional)
  ADGUARD_ASR_MODEL=tiny       tiny|base|small ... (tiny is ~75MB int8, CPU friendly)
  ADGUARD_ASR_LANGUAGE=zh      force language (empty = auto detect)
  ADGUARD_ASR_THREADS=2        CPU threads
  ADGUARD_ASR_DOWNLOAD_ROOT    model cache directory
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field

_lock = threading.Lock()
_model = None
_model_name = ""


class AsrUnavailable(RuntimeError):
    """Raised when ASR is disabled or the runtime/model cannot be used."""


@dataclass
class AsrOutput:
    text: str
    segments: list[dict] = field(default_factory=list)
    engine: str = "faster-whisper"
    language: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "segments": self.segments,
            "engine": self.engine,
            "language": self.language,
            "warnings": self.warnings,
        }


def enabled() -> bool:
    return os.environ.get("ADGUARD_ASR_ENABLED", "0").lower() in {"1", "true", "yes"}


def _load_model():
    global _model, _model_name
    if not enabled():
        raise AsrUnavailable("ASR disabled (ADGUARD_ASR_ENABLED!=1)")
    name = os.environ.get("ADGUARD_ASR_MODEL", "tiny").strip() or "tiny"
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise AsrUnavailable(f"faster-whisper unavailable: {exc}") from exc
        download_root = os.environ.get("ADGUARD_ASR_DOWNLOAD_ROOT", "").strip() or None
        threads = int(os.environ.get("ADGUARD_ASR_THREADS", "2"))
        try:
            _model = WhisperModel(name, device="cpu", compute_type="int8",
                                  download_root=download_root, cpu_threads=threads)
            _model_name = name
        except Exception as exc:  # noqa: BLE001
            raise AsrUnavailable(f"cannot load whisper model {name!r}: {exc}") from exc
        return _model


def transcribe_bytes(data: bytes, suffix: str = ".wav") -> AsrOutput:
    """Transcribe an audio file (wav/mp3/m4a bytes); never silently degrades."""
    model = _load_model()
    handle = tempfile.NamedTemporaryFile(prefix="adguard-asr-", suffix=suffix, delete=False)
    path = handle.name
    try:
        handle.write(data)
        handle.close()
        language = os.environ.get("ADGUARD_ASR_LANGUAGE", "zh").strip() or None
        segments, info = model.transcribe(path, language=language, beam_size=1,
                                          vad_filter=True, condition_on_previous_text=False)
        texts: list[str] = []
        items: list[dict] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            texts.append(text)
            items.append({"start": round(float(segment.start), 2),
                          "end": round(float(segment.end), 2), "text": text})
        return AsrOutput(text=" ".join(texts).strip(), segments=items,
                         language=getattr(info, "language", language))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
