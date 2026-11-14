"""Fail closed to the legacy scene if prepared display assets no longer match."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=128)
def _content_hash(path: str, size: int, mtime: int, ctime: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validated_scene(scene: dict[str, Any], directory: Path) -> dict[str, Any]:
    descriptor = scene.get("display")
    if not descriptor:
        return scene
    try:
        records = descriptor["sourceFiles"]
        expected = {rel for part in scene["parts"] for rel in part["textures"].values()}
        if descriptor["version"] != 1 or set(records) != expected or not expected:
            raise ValueError("Incomplete display provenance")
        root = directory.resolve()
        for rel, record in records.items():
            target = (root / rel).resolve()
            target.relative_to(root)
            stat = target.stat()
            expected_hash = record.get("sha256")
            if expected_hash:
                # File identities include ctime: touching timestamps cannot reuse stale bytes.
                if (
                    _content_hash(str(target), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                    != expected_hash
                ):
                    raise ValueError("Source texture changed")
            elif stat.st_size != record["size"] or str(stat.st_mtime_ns) != record["mtime_ns"]:
                raise ValueError("Source texture changed")
        return scene
    except (OSError, ValueError, TypeError, KeyError):
        return {key: value for key, value in scene.items() if key != "display"}
