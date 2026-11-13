"""把外部文件收进 job 目录（D8：Job 目录即状态），以及概念图守卫。"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from pacxio.concept.service import is_concept_path
from pacxio.visual.meta import not_printable

ASSETS_DIR = "assets"


def import_asset(directory: Path, source: Path, base: Path | None = None) -> str | None:
    """素材放进 job 的 assets/。返回相对 job 目录的路径；文件不存在返回 None。

    相对路径依次按 job 目录、base（如导入清单文件所在目录）、当前目录查找。
    """
    candidates = [source] if source.is_absolute() else [directory / source]
    if not source.is_absolute():
        if base is not None:
            candidates.append(base / source)
        candidates.append(Path.cwd() / source)
    src = next((c for c in candidates if c.is_file()), None)
    if src is None:
        return None
    try:
        return str(src.resolve().relative_to(directory.resolve()))
    except ValueError:
        pass
    assets = directory / ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    dest = assets / src.name
    if dest.exists() and dest.read_bytes() != src.read_bytes():
        tag = hashlib.sha256(src.read_bytes()).hexdigest()[:8]
        dest = assets / f"{src.stem}_{tag}{src.suffix}"
    if not dest.exists():
        shutil.copyfile(src, dest)
    return str(dest.relative_to(directory))


def is_concept(directory: Path, source: Path, base: Path | None = None) -> bool:
    """来源是否不能进印刷文件：job 里的概念图、和它像素相同的图、从平面图裁出来的位图（相对 job 目录、
    base、当前目录或绝对路径都算）。"""
    candidates = [source] if source.is_absolute() else [directory / source, Path.cwd() / source]
    if base is not None and not source.is_absolute():
        candidates.append(base / source)
    return any(
        is_concept_path(directory, c) or not_printable(directory, c) is not None for c in candidates
    )


__all__ = ["ASSETS_DIR", "import_asset", "is_concept"]
