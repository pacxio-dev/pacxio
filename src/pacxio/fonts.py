"""用户自带字体（品牌字体）的收录与授权检查。

字体收进 job 自己的 `fonts/` 目录（Job 目录即状态：同一个 job 在哪里重新生成结果都一致），
旁边写 `<名>.meta.json` 记录来源与授权信息。字体文件不进代码仓库（`jobs/` 被忽略），
也**不会**进入交付包：交付文件里只有转曲后的轮廓（D7）。

授权位 fsType 是字体作者在文件里写的"嵌入许可"声明，只能作为技术提示，不是法律意见：
转曲印刷是否被允许最终取决于字体的许可证，要由用户向权利人确认。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fontTools.ttLib import TTFont, TTLibError
from pydantic import BaseModel

from pacxio.fontfiles import JOB_FONT_DIR, TextError, find_font
from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.model.job import load_job_checked
from pacxio.visual.meta import now_iso, sha256_of

META_SUFFIX = ".meta.json"
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")
SFNT_SUFFIXES = {".otf", ".ttf"}
CONVERT_SUFFIXES = {".ttc", ".woff", ".woff2"}
# name 表的 nameID
NAME_FAMILY, NAME_FULL, NAME_COPYRIGHT, NAME_LICENSE, NAME_LICENSE_URL = 1, 4, 0, 13, 14
# 覆盖度取样：常用字与拉丁字母
LATIN_SAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
CJK_SAMPLE = "的一是不了人我在有他这中大来上国个到说们为子和你地出道也时年得就那要下以生会自着去之过家学对可她里后小么心多天而能好都然没日于起"
CJK_COVERED_MIN = 0.8  # 取样字覆盖 80% 以上算"有中文"

Embedding = Literal["installable", "restricted", "preview_print", "editable", "bitmap_only"]
FSTYPE_RESTRICTED = 0x0002
FSTYPE_PREVIEW_PRINT = 0x0004
FSTYPE_EDITABLE = 0x0008
FSTYPE_BITMAP_ONLY = 0x0200
FSTYPE_NO_SUBSETTING = 0x0100


class FontMeta(BaseModel):
    name: str
    file: str  # 相对 job 目录
    original_filename: str
    sha256: str
    family: str | None = None
    full_name: str | None = None
    copyright: str | None = None
    license_description: str | None = None
    license_url: str | None = None
    fs_type: int = 0
    embedding: Embedding = "installable"
    no_subsetting: bool = False
    num_glyphs: int = 0
    has_latin: bool = False
    has_cjk: bool = False
    risk_accepted: bool = False
    added: str = ""


def decode_embedding(fs_type: int) -> Embedding:
    """OS/2 fsType 的低 4 位：0 可安装、2 受限、4 预览与打印、8 可编辑；0x200 仅位图。"""
    if fs_type & FSTYPE_BITMAP_ONLY or fs_type & 0xF == FSTYPE_RESTRICTED:
        return "bitmap_only" if fs_type & FSTYPE_BITMAP_ONLY else "restricted"
    if fs_type & FSTYPE_PREVIEW_PRINT:
        return "preview_print"
    if fs_type & FSTYPE_EDITABLE:
        return "editable"
    return "installable"


def _name(tt: TTFont, name_id: int) -> str | None:
    n = tt["name"].getDebugName(name_id) if "name" in tt else None
    return str(n).strip() or None if n else None


@dataclass(frozen=True)
class FontInfo:
    family: str | None
    full_name: str | None
    copyright: str | None
    license_description: str | None
    license_url: str | None
    fs_type: int
    embedding: Embedding
    no_subsetting: bool
    num_glyphs: int
    has_latin: bool
    has_cjk: bool
    is_variable: bool
    is_cff: bool


def inspect_font(tt: TTFont) -> FontInfo:
    cmap = tt.getBestCmap() or {}
    fs_type = int(tt["OS/2"].fsType) if "OS/2" in tt else 0
    cjk_hit = sum(1 for ch in CJK_SAMPLE if ord(ch) in cmap) / len(CJK_SAMPLE)
    return FontInfo(
        family=_name(tt, NAME_FAMILY),
        full_name=_name(tt, NAME_FULL),
        copyright=_name(tt, NAME_COPYRIGHT),
        license_description=_name(tt, NAME_LICENSE),
        license_url=_name(tt, NAME_LICENSE_URL),
        fs_type=fs_type,
        embedding=decode_embedding(fs_type),
        no_subsetting=bool(fs_type & FSTYPE_NO_SUBSETTING),
        num_glyphs=int(tt["maxp"].numGlyphs),
        has_latin=all(ord(c) in cmap for c in "AaZz09"),
        has_cjk=cjk_hit >= CJK_COVERED_MIN,
        is_variable="fvar" in tt,
        is_cff="CFF " in tt or "CFF2" in tt,
    )


def _clean_name(raw: str) -> str:
    return NAME_RE.sub("_", raw).strip("_") or "font"


def meta_path(font_file: Path) -> Path:
    return font_file.with_name(font_file.stem + META_SUFFIX)


def read_job_font_meta(job_dir: Path, name: str) -> FontMeta | None:
    p = job_dir / JOB_FONT_DIR / f"{name}{META_SUFFIX}"
    return FontMeta.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None


def license_summary(m: FontMeta) -> str:
    bits = [m.license_description, m.license_url]
    return "；".join(b for b in bits if b) or "字体文件里没有附带授权说明"


def add_font(
    directory: Path,
    source: Path,
    name: str | None = None,
    index: int = 0,
    accept_license_risk: bool = False,
) -> Result:
    """把字体收进 job/fonts。"""
    r = Result(command="font add", job=str(directory))
    job, errs = load_job_checked(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r

    def fail(issue: Issue, code: int = 2) -> Result:
        return r.fail(issue, exit_code=code)

    if not source.is_file():
        return fail(Issue(id="FONT", message=t("Font file not found: {path}", path=source), fix=t("Check the path")))
    suffix = source.suffix.lower()
    if suffix not in SFNT_SUFFIXES | CONVERT_SUFFIXES:
        return fail(
            Issue(
                id="FONT",
                message=t("Unsupported font format {format}", format=source.suffix),
                fix=t("Use OTF or TTF. TTC, WOFF, and WOFF2 are also supported."),
            )
        )
    try:
        tt = TTFont(str(source), fontNumber=index if suffix == ".ttc" else -1, lazy=False)
    except (TTLibError, OSError, ImportError, AssertionError, ValueError) as e:
        hint = (
            "WOFF2 需要 brotli 库；请先把字体转成 OTF / TTF"
            if suffix == ".woff2" and "brotli" in str(e).lower()
            else "文件可能已损坏，或不是字体"
        )
        return fail(Issue(id="FONT", message=t("Could not read font {font}: {error}", font=source.name, error=e), fix=hint))
    info = inspect_font(tt)
    if info.is_variable:
        return fail(
            Issue(
                id="FONT",
                message=t("Variable fonts are not supported"),
                fix=t("Supply a static font file for the desired weight, such as Regular or Bold"),
            )
        )

    fname = _clean_name(name or (info.family or source.stem))
    other: Path | None = None
    try:
        other = find_font(fname)
    except TextError:
        pass
    if other is not None:
        return fail(
            Issue(
                id="FONT",
                message=t("Font name {name} conflicts with an installed font ({file})", name=fname, file=other),
                fix=t("Choose a distinct --name so neither font hides the other"),
            )
        )
    fonts_dir = directory / JOB_FONT_DIR
    ext = suffix if suffix in SFNT_SUFFIXES else (".otf" if info.is_cff else ".ttf")
    dest = fonts_dir / f"{fname}{ext}"
    if dest.exists():
        return fail(Issue(id="FONT", message=t("Font file {file} already exists", file=dest.name), fix=t("Choose another --name")))

    embedding = info.embedding
    if embedding in {"restricted", "bitmap_only"} and not accept_license_risk:
        return fail(
            Issue(
                id="FONT",
                message=t("The font's embedding flag is “{embedding}” (fsType={flag}), which restricts embedding or distributing its outlines", embedding=embedding, flag=info.fs_type),
                fix=t("The font licence determines whether outlining for print is allowed. Confirm with the rights holder before adding it with --accept-license-risk. The check report records that choice."),
            )
        )
    fonts_dir.mkdir(parents=True, exist_ok=True)
    if suffix in SFNT_SUFFIXES:
        shutil.copyfile(source, dest)  # 原样复制，保持哈希
    else:
        tt.flavor = None
        tt.save(str(dest))
    meta = FontMeta(
        name=fname,
        file=str(dest.relative_to(directory)),
        original_filename=source.name,
        sha256=sha256_of(dest),
        family=info.family,
        full_name=info.full_name,
        copyright=info.copyright,
        license_description=info.license_description,
        license_url=info.license_url,
        fs_type=info.fs_type,
        embedding=embedding,
        no_subsetting=info.no_subsetting,
        num_glyphs=info.num_glyphs,
        has_latin=info.has_latin,
        has_cjk=info.has_cjk,
        risk_accepted=accept_license_risk and embedding in {"restricted", "bitmap_only"},
        added=now_iso(),
    )
    meta_file = meta_path(dest)
    meta_file.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    r.artifacts = [str(dest), str(meta_file)]
    r.data = meta.model_dump()
    r.data["use_as"] = fname

    if meta.risk_accepted:
        r.warnings.append(
            Issue(
                id="L01",
                message=t("Font {font} has the “{embedding}” embedding flag and was added with your confirmation", font=fname, embedding=embedding),
                fix=t("Confirm permission to outline for print with the rights holder. The report keeps a record."),
            )
        )
    elif embedding in {"preview_print", "editable"}:
        r.warnings.append(
            Issue(
                id="L01",
                message=t("Font {font} has the “{embedding}” flag, which limits embedding to preview and print or to document editing", font=fname, embedding=embedding),
                fix=t("Check whether the licence allows outlining for print. The delivery pack does not include the font file."),
            )
        )
    if not (meta.license_description or meta.license_url or meta.copyright):
        r.warnings.append(
            Issue(
                id="L01",
                message=t("Font {font} contains no licence information", font=fname),
                fix=t("Confirm that the font is licensed for print. Brand fonts are usually licensed by the brand owner."),
            )
        )
    if not meta.has_cjk:
        r.warnings.append(
            Issue(
                id="FONT",
                message=t("Font {font} lacks common Chinese glyphs", font=fname),
                fix=t("For Chinese copy, add Source Han Sans as a fallback in --font: {font},SourceHanSansSC-Regular", font=fname),
            )
        )
    if not meta.has_latin:
        r.warnings.append(
            Issue(id="FONT", message=t("Font {font} lacks basic Latin letters", font=fname), fix=t("Add a fallback font for Latin copy"))
        )
    return r


def list_fonts(directory: Path) -> Result:
    r = Result(command="font list", job=str(directory))
    job, errs = load_job_checked(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    fonts = []
    for meta_file in sorted((directory / JOB_FONT_DIR).glob(f"*{META_SUFFIX}")):
        m = FontMeta.model_validate_json(meta_file.read_text(encoding="utf-8"))
        fonts.append(
            {**m.model_dump(exclude={"added", "copyright"}), "license": license_summary(m)}
        )
    used = sorted({x.font for x in job.placements if x.kind == "text" and x.font})
    r.data = {"job_fonts": fonts, "used_in_placements": used}
    return r
