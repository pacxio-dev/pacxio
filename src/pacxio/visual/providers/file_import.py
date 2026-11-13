"""文件导入（：用户提供图片 / Codex 内置出图）。

Codex 的内置出图不在工作目录：图片存放在 `$CODEX_HOME/generated_images/`
（默认 `~/.codex/generated_images/`），所以支持"取该目录最新一张"。
本模块只读本地文件，不联网。
"""

from __future__ import annotations

import os
from pathlib import Path

CODEX_HOME_ENV = "CODEX_HOME"
CODEX_SUBDIR = "generated_images"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VECTOR_SUFFIXES = {".svg"}


def codex_generated_dir() -> Path:
    home = os.environ.get(CODEX_HOME_ENV)
    base = Path(home) if home else Path.home() / ".codex"
    return base / CODEX_SUBDIR


def latest_image(directory: Path) -> Path | None:
    """目录（含子目录）里修改时间最新的图片；没有则 None。"""
    if not directory.is_dir():
        return None
    files = [p for p in directory.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime, default=None)
