"""素材元数据（ 第 6 步）：来源、提示词、模型、生成时间，保证可追溯。

每个素材 `assets/<name>.png` 旁边有 `assets/<name>.meta.json`。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from pacxio.export.pdfx import document_date
from pacxio.messages import t

Role = Literal["hero", "pattern", "texture", "logo", "icon", "graphic"]
SourceType = Literal["openai", "file", "codex_latest"]
META_SUFFIX = ".meta.json"
CONCEPT_DIR = "concepts"
RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")
RESTORE_STEPS = ("strip", "face")  # restore strip / face 记在素材元数据里的那一步
MAX_DERIVED = 8


class PrepStep(BaseModel):
    """prepare 做过的一步处理及其参数。"""

    step: str
    params: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class AssetMeta(BaseModel):
    name: str
    role: Role
    source_type: SourceType
    source_path: str | None = None  # 导入时的原路径（仅记录，不再读取）
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None  # 用户给的提示词
    full_prompt: str | None = None  # 实际发给模型的提示词（含强制约束）
    revised_prompt: str | None = None  # 模型改写后的提示词（如果返回了）
    created: str
    sha256: str
    size_px: tuple[int, int]
    steps: list[PrepStep] = Field(default_factory=list)
    derived_from: str | None = None  # prepare 的输出指向它的原始素材


def now_iso() -> str:
    """文档日期；设置了 SOURCE_DATE_EPOCH 时用它（可复现构建的通用约定）。"""
    d: datetime = document_date()
    return d.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def is_illustration(meta: AssetMeta | None) -> bool:
    """整片插画：vtracer 全彩描出来的（最后一步是 method=vtracer 的 trace）。颜色不逐个指定 CMYK，
    印刷时和照片一样按 ICC 换算、压总墨量（）。"""
    if meta is None or not meta.steps:
        return False
    last = meta.steps[-1]
    return last.step == "trace" and last.params.get("method") == "vtracer"


def _same_picture(a: Path, b: Path) -> bool:
    """同一张图：字节一样，或存成别的格式但像素一样。"""
    if a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes():
        return True
    try:
        with Image.open(a) as ia, Image.open(b) as ib:
            return ia.size == ib.size and ia.convert("RGB").tobytes() == ib.convert("RGB").tobytes()
    except (OSError, ValueError):
        return False


def not_printable(job_dir: Path, path: Path) -> str | None:
    """这个素材为什么不能进印刷文件（能进返回 None）：概念图、和概念图像素相同的图、从展开平面图裁出来的
    位图（`restore strip / face` 的图，和用它再处理出来的）。这些图里可能有 AI 画的字，分辨率也不够，
    只给描摹当源头；描成的矢量（.svg）可以进（、13.7，）。"""
    concepts = job_dir / CONCEPT_DIR
    try:
        path.resolve().relative_to(concepts.resolve())
        return t("{file} is a concept image", file=path.name)
    except ValueError:
        pass
    if path.suffix.lower() not in RASTER_SUFFIXES or not path.is_file():
        return None
    current = path
    for _ in range(MAX_DERIVED):  # 顺着 derived_from 往回找：prepare 过的也算
        meta = read_meta(current)
        if meta is None:
            break
        if any(step.step in RESTORE_STEPS for step in meta.steps):
            return t("{file} was cropped from an unfolded concept ({source}) and can only be used as a tracing reference", file=path.name, source=current.name)
        if not meta.derived_from:
            break
        current = job_dir / meta.derived_from
        if not current.is_file():
            break
    if concepts.is_dir():
        for image in concepts.iterdir():
            if image.suffix.lower() in RASTER_SUFFIXES and _same_picture(path, image):
                return t("{file} is identical to concept image {concept}", file=path.name, concept=image.name)
    return None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meta_path(asset: Path) -> Path:
    return asset.with_name(asset.stem + META_SUFFIX)


def write_meta(asset: Path, meta: AssetMeta) -> Path:
    p = meta_path(asset)
    p.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return p


def read_meta(asset: Path) -> AssetMeta | None:
    """读素材元数据；没有、或不是素材元数据的格式（例如概念图的元数据）时返回 None，不抛异常。"""
    p = meta_path(asset)
    if not p.is_file():
        return None
    try:
        return AssetMeta.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError:
        return None
