"""用户自带字体（品牌字体）的收录与授权检查。

字体收进 job 自己的 `fonts/` 目录（Job 目录即状态：同一个 job 在哪里重新生成结果都一致），
旁边写 `<名>.meta.json` 记录来源与授权信息。字体文件不进代码仓库（`jobs/` 被忽略），
也**不会**进入交付包：交付文件里只有转曲后的轮廓（D7）。

授权位 fsType 是字体作者在文件里写的"嵌入许可"声明，只能作为技术提示，不是法律意见：
转曲印刷是否被允许最终取决于字体的许可证，要由用户向权利人确认。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fontTools.ttLib import TTFont, TTLibError
from pydantic import BaseModel

from pacxio.cloud.config import home as pacxio_home
from pacxio.fontfiles import (
    JOB_FONT_DIR,
    TextError,
    describe_font,
    ephemeral_font_dirs,
    find_font,
    find_font_face,
    font_sha256,
    font_version,
    invalidate_cached_lookups,
    is_cloud_builtin_font,
    parse_font_chain,
)
from pacxio.fontsubset import verify_subset_source_sha
from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.model.job import FontPin, Job, load_job_checked
from pacxio.sysfonts import (
    SystemFont,
    discover_system_fonts,
    extract_static_font,
    find_system_font,
    static_font_bytes,
    system_fonts_enabled,
)
from pacxio.visual.meta import now_iso, sha256_of

META_SUFFIX = ".meta.json"
CONSENTS_FILE = "font_consents.json"
CONSENTS_SCHEMA_VERSION = 1
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
FontOrigin = Literal["brand", "system"]
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
    origin: FontOrigin = "brand"
    source_path: str | None = None
    face_index: int = 0
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
    upload_consent_at: str | None = None
    upload_consent_by: str | None = None
    allow_full_upload: bool = False
    added: str = ""


class FontUploadConsent(BaseModel):
    """用户对单个非开源字体（系统字体或品牌字体）的首次云端上传授权记录（按原始字体 SHA-256 索引）。"""

    sha256: str
    font_name: str
    origin: FontOrigin = "brand"
    original_filename: str
    family: str | None = None
    copyright: str | None = None
    license_description: str | None = None
    embedding: Embedding = "installable"
    no_subsetting: bool = False
    allow_full_upload: bool = False
    confirmed_by: str
    confirmed_at: str


@dataclass(frozen=True)
class ResolvedUploadFont:
    """云端上传前解析出的非开源字体信息（来源、原始完整字体哈希与本地路径）。"""

    name: str
    path: Path
    face_index: int
    source_sha256: str
    version: str | None
    origin: FontOrigin
    original_filename: str
    family: str | None
    copyright: str | None
    license_description: str | None
    license_url: str | None
    embedding: Embedding
    no_subsetting: bool
    meta_file: Path | None = None


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
        other = find_font(fname, include_system=False)
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


def referenced_fonts(job: Job) -> set[str]:
    """job 里所有文字放置和文字样式实际引用的字体名（按字体链拆开）。"""
    names: set[str] = set()
    for p in job.placements:
        if p.kind == "text" and p.font:
            names.update(parse_font_chain(p.font))
    for st in (job.styles or {}).values():
        if st.font:
            names.update(parse_font_chain(st.font))
    return names


def _pin_label(version: str | None, sha256: str) -> str:
    short = sha256[:12]
    return f"{version}, sha256 {short}" if version else f"sha256 {short}"


def _face_sha256(path: Path, face_index: int = 0) -> str:
    """单张字体面的内容哈希：.ttf/.otf 直接哈希文件；.ttc 提取单面静态 SFNT 哈希，确保与 font use 一致。"""
    if path.suffix.lower() != ".ttc":
        return font_sha256(path)
    ft_logger = logging.getLogger("fontTools")
    prev_level = ft_logger.level
    ft_logger.setLevel(logging.ERROR)
    try:
        with TTFont(str(path), fontNumber=face_index, lazy=False, recalcTimestamp=False) as tt:
            tt.flavor = None
            buf = io.BytesIO()
            tt.save(buf)
            return hashlib.sha256(buf.getvalue()).hexdigest()
    finally:
        ft_logger.setLevel(prev_level)


# ---------------- 非开源字体上传授权表（每字体首次确认）----------------


def consents_path() -> Path:
    return pacxio_home() / CONSENTS_FILE


def load_font_consents() -> dict[str, FontUploadConsent]:
    p = consents_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("consents") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    out: dict[str, FontUploadConsent] = {}
    for sha, val in items.items():
        if not isinstance(sha, str) or not isinstance(val, dict):
            continue
        try:
            out[sha] = FontUploadConsent.model_validate({"sha256": sha, **val})
        except ValueError:
            continue
    return out


def _save_font_consents(consents: dict[str, FontUploadConsent]) -> Path:
    p = consents_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONSENTS_SCHEMA_VERSION,
        "consents": {
            sha: c.model_dump(exclude={"sha256"}) for sha, c in sorted(consents.items())
        },
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def get_font_consent(sha256: str) -> FontUploadConsent | None:
    return load_font_consents().get(sha256)


def record_font_consent(consent: FontUploadConsent) -> Path:
    consents = load_font_consents()
    consents[consent.sha256] = consent
    return _save_font_consents(consents)


def revoke_font_consent(sha256: str) -> bool:
    consents = load_font_consents()
    if sha256 not in consents:
        return False
    del consents[sha256]
    _save_font_consents(consents)
    return True


def _is_under(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def is_open_library_font(name: str, directory: Path | None = None) -> bool:
    """是否为内置开源字体或 OFL / Apache 字体库字体（服务器自带，无需上传与授权确认）。"""
    try:
        path, _ = find_font_face(name, directory, include_system=False)
    except TextError:
        return False
    if directory is not None and _is_under(path, directory / JOB_FONT_DIR):
        return False
    if any(_is_under(path, d) for d in ephemeral_font_dirs(directory)):
        return False
    return True


def resolve_non_open_font(name: str, directory: Path) -> ResolvedUploadFont | None:
    """解析 job 引用的非开源字体（job/fonts 品牌/系统字体 或 本机系统字体）。

    返回 None 表示该字体属于服务端自带的开源字体（或轻量客户端未本地下载的服务端字体），无需本地上传。
    """
    if is_open_library_font(name, directory):
        return None
    # 1. 优先查 job/fonts 里的字体（含 brand 与通过 font use / restore text 收入的 system 字体）
    job_fonts_dir = directory / JOB_FONT_DIR
    for suffix in ("", ".otf", ".ttf", ".ttc"):
        p = job_fonts_dir / f"{name}{suffix}"
        if not p.is_file():
            continue
        mf = meta_path(p)
        m = FontMeta.model_validate_json(mf.read_text(encoding="utf-8")) if mf.is_file() else None
        sha = font_sha256(p)
        ver = font_version(p, sha)
        if m is not None:
            return ResolvedUploadFont(
                name=m.name,
                path=p,
                face_index=0,
                source_sha256=sha,
                version=ver,
                origin=m.origin,
                original_filename=m.original_filename,
                family=m.family,
                copyright=m.copyright,
                license_description=m.license_description,
                license_url=m.license_url,
                embedding=m.embedding,
                no_subsetting=m.no_subsetting,
                meta_file=mf if mf.is_file() else None,
            )
        with TTFont(str(p), fontNumber=0, lazy=True) as tt:
            info = inspect_font(tt)
        return ResolvedUploadFont(
            name=name,
            path=p,
            face_index=0,
            source_sha256=sha,
            version=ver,
            origin="brand",
            original_filename=p.name,
            family=info.family,
            copyright=info.copyright,
            license_description=info.license_description,
            license_url=info.license_url,
            embedding=info.embedding,
            no_subsetting=info.no_subsetting,
            meta_file=None,
        )
    # 2. 查本机系统字体（未收入 job/fonts 但直接在 job.json 里按名引用）
    if system_fonts_enabled():
        entry = find_system_font(name)
        if entry is not None:
            src = Path(entry.path)
            _, _, sha = static_font_bytes(entry)
            return ResolvedUploadFont(
                name=entry.name,
                path=src,
                face_index=entry.face_index,
                source_sha256=sha,
                version=entry.version,
                origin="system",
                original_filename=src.name,
                family=entry.family,
                copyright=entry.copyright,
                license_description=entry.license_description,
                license_url=entry.license_url,
                embedding=_as_embedding(entry.embedding),
                no_subsetting=entry.no_subsetting,
                meta_file=None,
            )
    return None


def _as_embedding(value: str) -> Embedding:
    if value in ("installable", "restricted", "preview_print", "editable", "bitmap_only"):
        return value  # type: ignore[return-value]
    return "installable"


def _license_warnings(meta: FontMeta) -> list[Issue]:
    out: list[Issue] = []
    if meta.risk_accepted:
        out.append(
            Issue(
                id="L01",
                message=t("Font {font} has the “{embedding}” embedding flag and was added with your confirmation", font=meta.name, embedding=meta.embedding),
                fix=t("Confirm permission to outline for print with the rights holder. The report keeps a record."),
            )
        )
    elif meta.embedding in {"preview_print", "editable"}:
        out.append(
            Issue(
                id="L01",
                message=t("Font {font} has the “{embedding}” flag, which limits embedding to preview and print or to document editing", font=meta.name, embedding=meta.embedding),
                fix=t("Check whether the licence allows outlining for print. The delivery pack does not include the font file."),
            )
        )
    if not (meta.license_description or meta.license_url or meta.copyright):
        out.append(
            Issue(
                id="L01",
                message=t("Font {font} contains no licence information", font=meta.name),
                fix=t("Confirm that the font is licensed for print. Brand fonts are usually licensed by the brand owner."),
            )
        )
    if not meta.has_cjk:
        out.append(
            Issue(
                id="FONT",
                message=t("Font {font} lacks common Chinese glyphs", font=meta.name),
                fix=t("For Chinese copy, add Source Han Sans as a fallback in --font: {font},SourceHanSansSC-Regular", font=meta.name),
            )
        )
    if not meta.has_latin:
        out.append(
            Issue(id="FONT", message=t("Font {font} lacks basic Latin letters", font=meta.name), fix=t("Add a fallback font for Latin copy"))
        )
    return out


def _entry_to_meta(
    entry: SystemFont, fname: str, dest: Path, directory: Path, sha: str, *, risk_accepted: bool
) -> FontMeta:
    return FontMeta(
        name=fname,
        file=str(dest.relative_to(directory)),
        original_filename=Path(entry.path).name,
        sha256=sha,
        origin="system",
        source_path=entry.path,
        face_index=entry.face_index,
        family=entry.family,
        full_name=entry.full_name,
        copyright=entry.copyright,
        license_description=entry.license_description,
        license_url=entry.license_url,
        fs_type=entry.fs_type,
        embedding=_as_embedding(entry.embedding),
        no_subsetting=entry.no_subsetting,
        num_glyphs=entry.num_glyphs,
        has_latin=entry.has_latin,
        has_cjk=entry.has_cjk,
        risk_accepted=risk_accepted,
        added=now_iso(),
    )


def list_system_fonts(
    *,
    search: str | None = None,
    cjk: bool = False,
    serif: bool = False,
    limit: int = 50,
    refresh: bool = False,
) -> Result:
    """列出本机发现的可用系统字体（仅在本机生成候选列表）。"""
    r = Result(command="font system")
    entries = discover_system_fonts(refresh=refresh)
    consents = load_font_consents()
    consented_names = {c.font_name.lower() for c in consents.values()}
    q = (search or "").strip().lower()
    filtered: list[SystemFont] = []
    for e in entries:
        if cjk and not e.has_cjk:
            continue
        if serif and not e.serif:
            continue
        if q:
            hay = " ".join(
                x for x in (e.name, e.family, e.full_name, e.postscript_name, e.subfamily) if x
            ).lower()
            if q not in hay:
                continue
        filtered.append(e)
    shown = filtered[: max(1, limit)] if limit > 0 else filtered
    r.data = {
        "enabled": system_fonts_enabled(),
        "total": len(entries),
        "matched": len(filtered),
        "shown": len(shown),
        "fonts": [
            {
                "name": e.name,
                "family": e.family,
                "subfamily": e.subfamily,
                "serif": e.serif,
                "weight_class": e.weight_class,
                "italic": e.italic,
                "has_latin": e.has_latin,
                "has_cjk": e.has_cjk,
                "embedding": e.embedding,
                "no_subsetting": e.no_subsetting,
                "authorized": e.name.lower() in consented_names,
                "path": e.path,
                "face_index": e.face_index,
            }
            for e in shown
        ],
    }
    return r


def use_system_font(
    directory: Path,
    system_name: str,
    *,
    name: str | None = None,
    accept_license_risk: bool = False,
    confirm_upload: bool = False,
    by: str = "User",
    allow_full_upload: bool = False,
) -> Result:
    """从本机系统字体按名字提取静态字体到 job/fonts，并记录来源与授权信息。"""
    r = Result(command="font use", job=str(directory))
    job, errs = load_job_checked(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    entry = find_system_font(system_name)
    if entry is None:
        return r.fail(
            Issue(
                id="FONT",
                message=t("System font {font} was not found on this machine", font=system_name),
                fix=t("Run `pacxio font system --search {font}` to see available system fonts", font=system_name),
            ),
            2,
        )
    fname = _clean_name(name or entry.name)
    other: Path | None = None
    try:
        other = find_font(fname, include_system=False)
    except TextError:
        pass
    if other is not None:
        return r.fail(
            Issue(
                id="FONT",
                message=t("Font name {name} conflicts with an installed font ({file})", name=fname, file=other),
                fix=t("Choose a distinct --name so neither font hides the other"),
            ),
            2,
        )
    embedding = _as_embedding(entry.embedding)
    if embedding in {"restricted", "bitmap_only"} and not accept_license_risk:
        return r.fail(
            Issue(
                id="FONT",
                message=t("The font's embedding flag is “{embedding}” (fsType={flag}), which restricts embedding or distributing its outlines", embedding=embedding, flag=entry.fs_type),
                fix=t("The font licence determines whether outlining for print is allowed. Confirm with the rights holder before adding it with --accept-license-risk. The check report records that choice."),
            ),
            2,
        )
    fonts_dir = directory / JOB_FONT_DIR
    existing_meta = read_job_font_meta(directory, fname)
    dest: Path | None = None
    sha: str | None = None
    if existing_meta is not None:
        cand = directory / existing_meta.file
        if (
            cand.is_file()
            and existing_meta.origin == "system"
            and existing_meta.source_path == entry.path
            and existing_meta.face_index == entry.face_index
        ):
            dest, sha = cand, existing_meta.sha256
        else:
            return r.fail(
                Issue(id="FONT", message=t("Font file {file} already exists", file=cand.name), fix=t("Choose another --name")),
                2,
            )
    if dest is None or sha is None:
        dest, sha = extract_static_font(entry, fonts_dir, fname)
    meta = _entry_to_meta(
        entry,
        fname,
        dest,
        directory,
        sha,
        risk_accepted=accept_license_risk and embedding in {"restricted", "bitmap_only"},
    )
    existing_consent = get_font_consent(sha)
    if confirm_upload:
        consent = FontUploadConsent(
            sha256=sha,
            font_name=fname,
            origin="system",
            original_filename=Path(entry.path).name,
            family=entry.family,
            copyright=entry.copyright,
            license_description=entry.license_description,
            embedding=embedding,
            no_subsetting=entry.no_subsetting,
            allow_full_upload=allow_full_upload,
            confirmed_by=by,
            confirmed_at=now_iso(),
        )
        record_font_consent(consent)
        existing_consent = consent
    if existing_consent is not None:
        meta.upload_consent_at = existing_consent.confirmed_at
        meta.upload_consent_by = existing_consent.confirmed_by
        meta.allow_full_upload = existing_consent.allow_full_upload

    mf = meta_path(dest)
    mf.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    invalidate_cached_lookups()
    r.artifacts = [str(dest), str(mf)]
    r.data = {
        **meta.model_dump(),
        "use_as": fname,
        "upload_authorized": existing_consent is not None,
    }
    r.warnings.extend(_license_warnings(meta))
    if meta.no_subsetting and not meta.allow_full_upload:
        r.warnings.append(
            Issue(
                id="FONT_SUBSET",
                message=t("Font {font} sets the no_subsetting flag (fsType={flag}) and cannot be subset automatically", font=fname, flag=meta.fs_type),
                fix=t("Switch to a font that allows subsetting, or authorize ephemeral full-file upload with `pacxio font authorize <job> {font} --allow-full-upload`", font=fname),
            )
        )
    return r


def stage_system_font_if_needed(directory: Path, font_name: str) -> tuple[bool, bool]:
    """restore text --apply --fonts 选中本机系统字体时，自动把静态面提取进 job/fonts。

    返回 `(本次是否新提取, 是否已有云端上传授权)`。受限字体（restricted/bitmap_only）不自动提取。
    """
    if is_open_library_font(font_name, directory):
        return False, True
    existing = read_job_font_meta(directory, font_name)
    if existing is not None:
        return False, get_font_consent(existing.sha256) is not None
    entry = find_system_font(font_name)
    if entry is None:
        return False, False
    embedding = _as_embedding(entry.embedding)
    if embedding in {"restricted", "bitmap_only"}:
        return False, False
    dest, sha = extract_static_font(entry, directory / JOB_FONT_DIR, entry.name)
    meta = _entry_to_meta(entry, entry.name, dest, directory, sha, risk_accepted=False)
    consent = get_font_consent(sha)
    if consent is not None:
        meta.upload_consent_at = consent.confirmed_at
        meta.upload_consent_by = consent.confirmed_by
        meta.allow_full_upload = consent.allow_full_upload
    meta_path(dest).write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    invalidate_cached_lookups()
    return True, consent is not None


def authorize_font(
    directory: Path,
    font_name: str | None = None,
    *,
    by: str = "User",
    allow_full_upload: bool = False,
    revoke: bool = False,
) -> Result:
    """查看、逐字体确认或撤销非开源字体（品牌字体与系统字体）的云端上传授权。"""
    r = Result(command="font authorize", job=str(directory))
    job, errs = load_job_checked(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r

    if font_name is None:
        candidates: set[str] = set(referenced_fonts(job))
        fonts_dir = directory / JOB_FONT_DIR
        if fonts_dir.is_dir():
            for mf in fonts_dir.glob(f"*{META_SUFFIX}"):
                candidates.add(mf.name[: -len(META_SUFFIX)])
        rows: list[dict[str, Any]] = []
        for name in sorted(candidates):
            info = resolve_non_open_font(name, directory)
            if info is None:
                continue
            consent = get_font_consent(info.source_sha256)
            rows.append(
                {
                    "name": info.name,
                    "origin": info.origin,
                    "sha256": info.source_sha256,
                    "family": info.family,
                    "embedding": info.embedding,
                    "no_subsetting": info.no_subsetting,
                    "authorized": consent is not None,
                    "allow_full_upload": consent.allow_full_upload if consent else False,
                    "confirmed_by": consent.confirmed_by if consent else None,
                    "confirmed_at": consent.confirmed_at if consent else None,
                }
            )
        r.data = {
            "fonts": rows,
            "pending": [x["name"] for x in rows if not x["authorized"]],
            "consents_file": str(consents_path()),
        }
        return r

    if is_open_library_font(font_name, directory):
        r.data = {
            "name": font_name,
            "open_library": True,
            "authorized": True,
            "note": t("Open-license library fonts are built into the cloud service and do not require upload authorization"),
        }
        return r

    info = resolve_non_open_font(font_name, directory)
    if info is None:
        return r.fail(
            Issue(
                id="FONT",
                message=t("Could not find non-open font {font} in this job or on this machine", font=font_name),
                fix=t("Add a brand font with `pacxio font add`, or check system fonts with `pacxio font system`"),
            ),
            2,
        )

    if revoke:
        removed = revoke_font_consent(info.source_sha256)
        if info.meta_file is not None and info.meta_file.is_file():
            m = FontMeta.model_validate_json(info.meta_file.read_text(encoding="utf-8"))
            m.upload_consent_at = None
            m.upload_consent_by = None
            m.allow_full_upload = False
            info.meta_file.write_text(m.model_dump_json(indent=2) + "\n", encoding="utf-8")
        r.data = {
            "name": info.name,
            "sha256": info.source_sha256,
            "revoked": removed,
            "authorized": False,
        }
        return r

    consent = FontUploadConsent(
        sha256=info.source_sha256,
        font_name=info.name,
        origin=info.origin,
        original_filename=info.original_filename,
        family=info.family,
        copyright=info.copyright,
        license_description=info.license_description,
        embedding=info.embedding,
        no_subsetting=info.no_subsetting,
        allow_full_upload=allow_full_upload,
        confirmed_by=by,
        confirmed_at=now_iso(),
    )
    saved = record_font_consent(consent)
    if info.meta_file is not None and info.meta_file.is_file():
        m = FontMeta.model_validate_json(info.meta_file.read_text(encoding="utf-8"))
        m.upload_consent_at = consent.confirmed_at
        m.upload_consent_by = consent.confirmed_by
        m.allow_full_upload = consent.allow_full_upload
        info.meta_file.write_text(m.model_dump_json(indent=2) + "\n", encoding="utf-8")
    r.artifacts = [str(saved)]
    r.data = {
        **consent.model_dump(),
        "authorized": True,
        "upload_mode": "ephemeral_full" if (info.no_subsetting and allow_full_upload) else "ephemeral_subset",
        "delete_after_run": True,
    }
    if info.no_subsetting and not allow_full_upload:
        r.warnings.append(
            Issue(
                id="FONT_SUBSET",
                message=t("Font {font} sets the no_subsetting flag (fsType={flag}) and cannot be subset automatically", font=info.name, flag=FSTYPE_NO_SUBSETTING),
                fix=t("Switch to a font that allows subsetting, or authorize ephemeral full-file upload with `pacxio font authorize <job> {font} --allow-full-upload`", font=info.name),
            )
        )
    return r


def verify_job_font_pins(job: Job, directory: Path) -> list[Issue]:
    """核对 job.font_pins：用到的已锁定字体必须存在且哈希一致，缺了或版本变了直接报错，不悄悄替换。

    云端瞬时字形子集（附带校验一致的 .subset.json）按原始完整字体的 source_sha256 核对。
    """
    if not job.font_pins:
        return []
    errors: list[Issue] = []
    for name in sorted(referenced_fonts(job) & set(job.font_pins)):
        pin = job.font_pins[name]
        wanted = _pin_label(pin.version, pin.sha256)
        try:
            path, face_index = find_font_face(name, directory)
        except TextError:
            if pin.source == "open" and is_cloud_builtin_font(name):
                continue
            errors.append(
                Issue(
                    id="FONT",
                    message=t("Pinned font {font} ({pinned}) is missing", font=name, pinned=wanted),
                    fix=t(
                        "Install the matching version of {font} (or add it with `pacxio font add <job> <file> --name {font}`), or switch fonts and update the pin with `pacxio font pin <job> --refresh`",
                        font=name,
                    ),
                )
            )
            continue
        raw_sha = _face_sha256(path, face_index)
        effective_sha, effective_ver = verify_subset_source_sha(path, raw_sha)
        if effective_sha != pin.sha256:
            actual = _pin_label(
                effective_ver or font_version(path, raw_sha, face_index=face_index),
                effective_sha,
            )
            errors.append(
                Issue(
                    id="FONT",
                    message=t(
                        "Font {font} does not match the version pinned in job.json (pinned {pinned}; installed {installed})",
                        font=name,
                        pinned=wanted,
                        installed=actual,
                    ),
                    fix=t(
                        "Restore the pinned version of {font}, or review the layout and run `pacxio font pin <job> --refresh` to accept the installed version",
                        font=name,
                    ),
                )
            )
    return errors


def prune_font_pins(job: Job) -> None:
    """删掉 job.font_pins 里已经不再被任何放置或样式引用的字体。"""
    if not job.font_pins:
        return
    used = referenced_fonts(job)
    kept = {k: v for k, v in job.font_pins.items() if k in used}
    job.font_pins = dict(sorted(kept.items())) or None


def sync_font_pins(job: Job, directory: Path, *, refresh: bool = False) -> list[Issue]:
    """按当前引用到的字体同步 job.font_pins：已锁定的先核对（除非 refresh=True），未锁定的补锁，不再用的清理。"""
    used = referenced_fonts(job)
    if not refresh:
        errs = verify_job_font_pins(job, directory)
        if errs:
            return errs
    pins: dict[str, FontPin] = {
        k: v for k, v in (job.font_pins or {}).items() if k in used and not refresh
    }
    errors: list[Issue] = []
    for name in sorted(used):
        if name in pins:
            continue
        try:
            path, face_index = find_font_face(name, directory)
        except TextError as e:
            if is_cloud_builtin_font(name):
                continue
            errors.append(e.issue)
            continue
        info = describe_font(path, directory, face_index=face_index)
        sha = _face_sha256(path, face_index) if path.suffix.lower() == ".ttc" else info["sha256"]
        assert sha is not None
        pins[name] = FontPin(sha256=sha, version=info["version"], source=info["source"])
    if errors:
        return errors
    job.font_pins = dict(sorted(pins.items())) or None
    return []


def pin_fonts(directory: Path, *, refresh: bool = False, check: bool = False) -> Result:
    """`font pin`：把 job 用到的字体版本与哈希写进 job.json，或核对现有锁定（--check）。"""
    r = Result(command="font pin", job=str(directory))
    if refresh and check:
        return r.fail(
            Issue(
                id="ARG",
                message=t("Cannot combine --refresh with --check"),
                fix=t("Use --check to verify existing pins, or --refresh to update them to the installed fonts"),
            ),
            2,
        )
    job, errs = load_job_checked(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    used = sorted(referenced_fonts(job))
    if check:
        pin_errs = verify_job_font_pins(job, directory)
        pins = job.font_pins or {}
        unpinned = [n for n in used if n not in pins]
        r.data = {
            "checked": True,
            "used": used,
            "pinned": {k: v.model_dump(exclude_none=True) for k, v in pins.items()},
            "unpinned": unpinned,
        }
        if pin_errs:
            r.errors += pin_errs
            r.ok, r.exit_code = False, 1
        return r
    pin_errs = sync_font_pins(job, directory, refresh=refresh)
    if pin_errs:
        r.errors += pin_errs
        r.ok, r.exit_code = False, 1
        return r
    r.artifacts = [str(job.save(directory))]
    r.data = {
        "refreshed": refresh,
        "used": used,
        "pinned": {k: v.model_dump(exclude_none=True) for k, v in (job.font_pins or {}).items()},
    }
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
        consent = get_font_consent(m.sha256)
        fonts.append(
            {
                **m.model_dump(exclude={"added", "copyright"}),
                "license": license_summary(m),
                "upload_authorized": consent is not None,
            }
        )
    used = sorted({x.font for x in job.placements if x.kind == "text" and x.font})
    pinned = {k: v.model_dump(exclude_none=True) for k, v in (job.font_pins or {}).items()}
    r.data = {"job_fonts": fonts, "used_in_placements": used, "pinned": pinned}
    return r
