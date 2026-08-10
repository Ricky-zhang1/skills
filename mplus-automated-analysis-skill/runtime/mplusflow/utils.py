from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_copy(src: Path, dst_dir: Path) -> Path:
    ensure_dir(dst_dir)
    candidate = dst_dir / src.name
    if not candidate.exists():
        shutil.copy2(src, candidate)
        return candidate
    stem, suffix = src.stem, src.suffix
    i = 2
    while True:
        candidate = dst_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            shutil.copy2(src, candidate)
            return candidate
        i += 1


def parse_csv_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [x.strip() for x in re.split(r"[,，]", text) if x.strip()]


def parse_number_list(text: str | None) -> list[float]:
    values: list[float] = []
    for token in parse_csv_list(text):
        values.append(float(token))
    return values


def wrap_mplus_names(names: Iterable[str], indent: str = "    ", width: int = 78) -> str:
    lines: list[str] = []
    current = ""
    for name in names:
        candidate = name if not current else f"{current} {name}"
        if len(indent) + len(candidate) > width and current:
            lines.append(current)
            current = name
        else:
            current = candidate
    if current:
        lines.append(current)
    if not lines:
        return ""
    return ("\n" + indent).join(lines)
