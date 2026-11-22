"""Text embedders for clause retrieval.

Two implementations behind one interface:

* **BgeEmbedder** - `BAAI/bge-small-zh-v1.5` (512 dims, Chinese, ~90 MB) running on
  CPU via transformers; the production-quality embedder for M3.
* **HashingEmbedder** - deterministic character n-gram hashing, no model download.
  Used by unit tests / offline CI so retrieval logic is always testable without a
  100 MB artefact; the metric gate is evaluated with the real model and that fact
  is recorded in the report.

Both normalise to unit length, so cosine and L2 distance are rank-equivalent.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_MODEL_DIR = Path(
    os.environ.get("ADGUARD_EMBED_MODEL_DIR", REPO_ROOT / ".tmp" / "models" / "bge-small-zh-v1.5")
)
BGE_MODEL_ID = "BAAI/bge-small-zh-v1.5"


class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        ...


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class HashingEmbedder:
    """Dependency-free embedder: hashed character 1-3 grams with signed buckets."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hashing-char-ngram-{dim}"

    def _hash(self, gram: str) -> tuple[int, float]:
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "little") % self.dim
        sign = 1.0 if digest[4] & 1 else -1.0
        return index, sign

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, raw in enumerate(texts):
            text = "".join(ch for ch in raw if ch.strip())
            grams: list[str] = []
            for size in (1, 2, 3):
                grams.extend(text[i : i + size] for i in range(max(0, len(text) - size + 1)))
            for gram in grams:
                index, sign = self._hash(gram)
                matrix[row, index] += sign
        return _l2_normalize(matrix)


class BgeEmbedder:
    """`BAAI/bge-small-zh-v1.5` sentence embedder (CLS pooling + L2 normalisation)."""

    def __init__(self, model_dir: Path | None = None, max_length: int = 256) -> None:
        # Keep HuggingFace cache inside the workspace (.tmp is gitignored): the user
        # home cache directory may be read-only in this environment.
        os.environ.setdefault("HF_HOME", str(REPO_ROOT / ".tmp" / "hf-cache"))
        import torch
        from transformers import AutoModel, AutoTokenizer

        directory = Path(model_dir or DEFAULT_MODEL_DIR)
        if not (directory / "config.json").exists():
            raise FileNotFoundError(f"embedding model not found in {directory}")
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(str(directory))
        self._model = AutoModel.from_pretrained(str(directory))
        self._model.eval()
        self._max_length = max_length
        self.dim = int(self._model.config.hidden_size)
        self.name = BGE_MODEL_ID

    def embed(self, texts: list[str]) -> np.ndarray:
        torch = self._torch
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        with torch.no_grad():
            batch = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=self._max_length,
                return_tensors="pt",
            )
            outputs = self._model(**batch)
            # CLS pooling (bge uses the first token) + L2 normalisation.
            vectors = outputs.last_hidden_state[:, 0]
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        return vectors.cpu().numpy().astype(np.float32)


def resolve_embedder(preference: str = "auto", model_dir: Path | None = None) -> Embedder:
    """Pick the real model when available, otherwise fall back to hashing.

    The fallback is intentional (offline CI / fresh clone), but it must be visible:
    callers can inspect `embedder.name` and the report states which one produced
    the metrics.
    """
    preference = (preference or "auto").lower()
    directory = Path(model_dir or DEFAULT_MODEL_DIR)
    if preference in ("auto", "bge"):
        try:
            embedder = BgeEmbedder(directory)
            logger.info("using embedding model %s (%d dims)", embedder.name, embedder.dim)
            return embedder
        except Exception as exc:  # noqa: BLE001 - fallback is the point
            if preference == "bge":
                raise
            logger.warning("BGE model unavailable (%s); falling back to hashing embedder", exc)
    embedder = HashingEmbedder()
    logger.warning("using fallback embedder %s -- retrieval metrics are NOT comparable "
                   "with the BGE report", embedder.name)
    return embedder


def download_model(target_dir: Path | None = None, endpoint: str | None = None) -> Path:
    """Download the embedding model from HuggingFace (respects a mirror endpoint)."""
    from huggingface_hub import snapshot_download

    directory = Path(target_dir or DEFAULT_MODEL_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    snapshot_download(
        repo_id=BGE_MODEL_ID,
        local_dir=str(directory),
        allow_patterns=[
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "vocab.txt",
        ],
        ignore_patterns=["*.bin", "*.h5", "*.ot"],
    )
    return directory


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Embedding model helper")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", ""))
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.download:
        path = download_model(args.dir, args.endpoint or None)
        print(f"model downloaded to {path}")
    if args.probe:
        embedder = resolve_embedder("bge", args.dir)
        vectors = embedder.embed(["全网最低价", "国家级认证", "支持七天无理由退换"])
        print(f"embedder={embedder.name} dim={embedder.dim}")
        print(np.round(vectors @ vectors.T, 3))


if __name__ == "__main__":
    main()
