from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path: Path | str, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_json(path: Path | str, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def append_event(path: Path | str, event: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"wall_time": time.time(), **event}, ensure_ascii=False,
                                allow_nan=False) + "\n")
        handle.flush()


def require_new_output(path: Path | str) -> Path:
    path = Path(path).expanduser().resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Output directory is not empty; use a new directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path
