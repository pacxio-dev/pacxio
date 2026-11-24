"""云端排字前的最小字形子集切分与瞬时字体校验（R2-5，开源客户端执行）。

设计要点（ R2-5）：
1. 只把当前 job 实际用到字符的字形子集传给云端（不传完整商业/系统字库）；
2. 尊重 OS/2 `fsType` 的 `no_subsetting`（0x0100）位：默认拦截提示，只有用户显式确认
   `--allow-full-upload` 才以瞬时整文件方式上传；
3. 子集旁随附 `<name>.subset.json` 记录 `subset_sha256`、`source_sha256`、`delete_after_run: true`
   与用户授权确认信息，供服务端 `verify_job_font_pins` 在核验防篡改的同时匹配原始完整字体版本锁；
4. 服务端仅在单次 run 期间挂载 `PACXIO_EPHEMERAL_FONT_DIR`（或 `.ephemeral_fonts/`），
   渲染/转曲完成后立即删除。
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
from pathlib import Path
from typing import Literal

from fontTools import subset as ft_subset
from fontTools.ttLib import TTFont, TTLibError
from pydantic import BaseModel

from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.job import Job

SUBSET_SUFFIX = ".subset.json"
EPHEMERAL_FONT_DIR_ENV = "PACXIO_EPHEMERAL_FONT_DIR"
EPHEMERAL_FONT_DIR = ".ephemeral_fonts"
FONT_BINARY_SUFFIXES = frozenset({".ttf", ".otf", ".ttc", ".woff", ".woff2"})

_FSTYPE_NO_SUBSETTING = 0x0100


class FontSubsetError(Exception):
    """子集切分失败或被字体 no_subsetting 标志拦截。"""

    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


class SubsetMeta(BaseModel):
    """随瞬时字体子集附带的校验与清理元数据（<name>.subset.json）。"""

    name: str
    subset: bool = True  # False = 用户对 no_subsetting 字体显式授权瞬时整文件上传
    subset_sha256: str
    source_sha256: str
    source_version: str | None = None
    origin: Literal["brand", "system"] = "brand"
    glyph_count: int = 0
    char_count: int = 0
    ephemeral: bool = True
    delete_after_run: bool = True
    consent_confirmed_at: str | None = None
    consent_confirmed_by: str | None = None


def job_font_chars(job: Job) -> dict[str, set[str]]:
    """收集 job 中每个被引用字体实际需要排版的字符集合（展开信息清单与文字样式）。"""
    from pacxio.fontfiles import parse_font_chain

    resolved = job.resolved()
    chars: dict[str, set[str]] = {}
    for pl in resolved.placements:
        if pl.kind != "text" or not pl.font:
            continue
        text = pl.text or ""
        used = {c for c in text if c != "\r"} | {" "}
        for fname in parse_font_chain(pl.font):
            chars.setdefault(fname, set()).update(used)
    return chars


def subset_font_bytes(
    source_path: Path,
    chars: set[str],
    *,
    font_name: str | None = None,
    face_index: int = 0,
    allow_full_upload: bool = False,
) -> tuple[bytes, str, bool, int]:
    """按 chars 切出确定性最小静态 SFNT 字形子集。

    返回 `(bytes, ext, is_subset, glyph_count)`；`ext` 为 `.ttf` 或 `.otf`。
    """
    label = font_name or source_path.stem
    suffix = source_path.suffix.lower()
    is_ttc = suffix == ".ttc"
    ft_logger = logging.getLogger("fontTools")
    prev_level = ft_logger.level
    ft_logger.setLevel(logging.ERROR)
    try:
        try:
            tt = TTFont(str(source_path), fontNumber=face_index if is_ttc else -1, lazy=False)
        except (TTLibError, OSError, ValueError, AssertionError) as e:
            raise FontSubsetError(
                Issue(
                    id="FONT_SUBSET",
                    message=t("Could not read font {font}: {error}", font=label, error=e),
                    fix=t("Check that the font file exists and is a valid static OTF/TTF/TTC font"),
                )
            ) from e
        try:
            is_cff = "CFF " in tt or "CFF2" in tt
            ext = ".otf" if is_cff else ".ttf"
            fs_type = int(tt["OS/2"].fsType) if "OS/2" in tt else 0
            no_subset = bool(fs_type & _FSTYPE_NO_SUBSETTING)
            if no_subset:
                if not allow_full_upload:
                    raise FontSubsetError(
                        Issue(
                            id="FONT_SUBSET",
                            message=t(
                                "Font {font} sets the no_subsetting flag (fsType={flag}) and cannot be subset automatically",
                                font=label,
                                flag=fs_type,
                            ),
                            fix=t(
                                "Switch to a font that allows subsetting, or authorize ephemeral full-file upload with `pacxio font authorize <job> {font} --allow-full-upload`",
                                font=label,
                            ),
                        )
                    )
                tt.flavor = None
                buf = io.BytesIO()
                tt.save(buf)
                return buf.getvalue(), ext, False, int(tt["maxp"].numGlyphs)

            opts = ft_subset.Options()
            opts.layout_features = ["*"]  # 保留字距、连字与 CJK GSUB/GPOS 排版特性
            opts.hinting = False  # 印刷输出全部转曲，不需要屏幕 hinting
            opts.notdef_outline = True
            opts.name_IDs = ["*"]  # 保留名称与版本号，便于版本核对与 L01 报告留痕
            # 丢弃数字签名与嵌入式位图/彩色表（只保留矢量轮廓；避免 macOS Geneva 等字体的旧 EBLC 表触发 decompile 崩溃）
            opts.drop_tables += [
                "DSIG",
                "EBDT",
                "EBLC",
                "EBSC",
                "bdat",
                "bloc",
                "bhed",
                "CBDT",
                "CBLC",
                "sbix",
            ]
            opts.recalc_bounds = True
            opts.recalc_timestamp = False  # 确定性字节：相同字库 + 相同字符 → 相同哈希

            subsetter = ft_subset.Subsetter(options=opts)
            payload_text = "".join(sorted(chars | {" "}))
            try:
                subsetter.populate(text=payload_text)
                subsetter.subset(tt)
                tt.flavor = None
                buf = io.BytesIO()
                tt.save(buf)
            except Exception as e:  # noqa: BLE001
                raise FontSubsetError(
                    Issue(
                        id="FONT_SUBSET",
                        message=t("Could not read font {font}: {error}", font=label, error=e),
                        fix=t("Check that the font file exists and is a valid static OTF/TTF/TTC font"),
                    )
                ) from e
            glyph_count = int(tt["maxp"].numGlyphs) if "maxp" in tt else 0
            return buf.getvalue(), ext, True, glyph_count
        finally:
            tt.close()
    finally:
        ft_logger.setLevel(prev_level)


def subset_meta_path(font_path: Path) -> Path:
    return font_path.with_name(font_path.stem + SUBSET_SUFFIX)


def read_subset_meta(font_path: Path) -> SubsetMeta | None:
    p = subset_meta_path(font_path)
    if not p.is_file():
        return None
    try:
        return SubsetMeta.model_validate_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_subset_meta(font_path: Path, meta: SubsetMeta) -> Path:
    p = subset_meta_path(font_path)
    p.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return p


def verify_subset_source_sha(font_path: Path, actual_sha: str) -> tuple[str, str | None]:
    """若 font_path 旁有完整一致的 .subset.json，返回原始完整字体的 (source_sha256, source_version)。

    若子集字节被篡改（actual_sha != meta.subset_sha256），直接退回 actual_sha，使外层锁核对失败。
    """
    meta = read_subset_meta(font_path)
    if meta is None:
        return actual_sha, None
    if meta.subset_sha256 != actual_sha:
        return actual_sha, None
    return meta.source_sha256, meta.source_version


def stage_ephemeral_subset(
    target_dir: Path,
    meta: SubsetMeta,
    font_bytes: bytes,
    ext: str,
) -> tuple[Path, Path]:
    """把瞬时子集和校验清单写入临时目录（供服务端单次 run 或测试验证用）。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    font_path = target_dir / f"{meta.name}{ext}"
    font_path.write_bytes(font_bytes)
    assert hashlib.sha256(font_bytes).hexdigest() == meta.subset_sha256
    meta_path = write_subset_meta(font_path, meta)
    return font_path, meta_path


def cleanup_ephemeral_fonts(job_dir: Path | None = None, *, include_env: bool = False) -> int:
    """删除瞬时字体目录里的全部文件（服务端渲染/转曲完成后在 finally 中调用）。"""
    removed = 0
    targets: list[Path] = []
    if job_dir is not None:
        targets.append(job_dir / EPHEMERAL_FONT_DIR)
    if include_env:
        env = os.environ.get(EPHEMERAL_FONT_DIR_ENV, "").strip()
        if env:
            targets.append(Path(env))
    for folder in targets:
        if not folder.is_dir():
            continue
        for item in folder.rglob("*"):
            if item.is_file():
                removed += 1
        shutil.rmtree(folder, ignore_errors=True)
    return removed
