"""Experiment registry: every training/eval run records config + data version + metrics.

PRD M4 requires reproducibility: model, data version, hyper-parameters and results
must all be recorded. This module is the single place that writes those records.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

REPORTS = Path(
    os.environ.get(
        "ADGUARD_REPORTS_DIR",
        Path(__file__).resolve().parent.parent.parent / "reports",
    )
)


def data_manifest(data_dir: Path) -> dict[str, Any]:
    manifest = json.loads((Path(data_dir) / "manifest.json").read_text(encoding="utf-8"))
    return {
        "version": manifest["version"],
        "generated_at": manifest["generated_at"],
        "split_protocol": manifest["split_protocol"],
        "splits": {
            name: {"rows": info["rows"], "sha256": info["sha256"]}
            for name, info in manifest["splits"].items()
        },
    }


def start_run(name: str, config: dict[str, Any], data_dir: Path, notes: str = "") -> Path:
    run_dir = REPORTS / "experiments" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    config_digest = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(Path(data_dir) / "manifest.json", run_dir / "data_manifest.json")
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "name": name,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "config_sha256": config_digest,
                "data": data_manifest(data_dir),
                "notes": notes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def finish_run(run_dir: Path, metrics: dict[str, Any], artifacts: dict[str, Any] | None = None) -> Path:
    payload = {
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": metrics,
        "artifacts": artifacts or {},
    }
    (run_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir
