"""本机系统字体发现与索引（R2-5，开源：候选列表只在本机生成，不在服务端扫系统目录）。

跨平台扫描系统字体目录（macOS / Windows / Linux），支持：
- `PACXIO_SYSTEM_FONTS=off` 关闭扫描（测试隔离或服务器端禁用）；
- `PACXIO_SYSTEM_FONT_DIRS`（按 `os.pathsep` 分隔）覆盖默认目录；
- `.ttc` 字体集拆成 `(path, face_index)` 逐面索引；
- 跳过隐藏私有 UI 字体（`.` 开头）、`LastResort`、无矢量轮廓的纯位图/彩色表情字体与可变字体；
- 按 `(path, mtime_ns, size)` 增量缓存于 `~/.pacxio/system_fonts_index.json`（受 `PACXIO_HOME` 控制）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont, TTLibError
from pydantic import BaseModel

from pacxio.cloud.config import home as pacxio_home
from pacxio.visual.meta import sha256_of

SYSTEM_FONTS_ENV = "PACXIO_SYSTEM_FONTS"
SYSTEM_FONT_DIRS_ENV = "PACXIO_SYSTEM_FONT_DIRS"
SYSTEM_INDEX_FILE = "system_fonts_index.json"
INDEX_SCHEMA_VERSION = 1

SCAN_SUFFIXES = (".otf", ".ttf", ".ttc")
MAX_WALK_DEPTH = 3
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")
SKIP_NAMES = {"LastResort"}

# OpenType name table IDs
_NAME_COPYRIGHT = 0
_NAME_FAMILY = 1
_NAME_SUBFAMILY = 2
_NAME_FULL = 4
_NAME_VERSION = 5
_NAME_POSTSCRIPT = 6
_NAME_LICENSE = 13
_NAME_LICENSE_URL = 14

# OS/2 fsType bits（与 pacxio.fonts 保持一致）
_FSTYPE_RESTRICTED = 0x0002
_FSTYPE_PREVIEW_PRINT = 0x0004
_FSTYPE_EDITABLE = 0x0008
_FSTYPE_NO_SUBSETTING = 0x0100
_FSTYPE_BITMAP_ONLY = 0x0200

# 覆盖度抽样（轻量、快速判断系统字体是否支持基本拉丁或常用中文）
_LATIN_PROBE = "AaEeRrTtSsNn09"
_CJK_PROBE = "的一是不了人我在有他这中大来上国个到说们为子和你地出道也时年"
_CJK_MIN_HIT = 0.75

# OS/2 sFamilyClass 高字节：1–5 与 7 为各类衬线体，8 为无衬线
_SERIF_FAMILY_CLASSES = {1, 2, 3, 4, 5, 7}
_SERIF_KEYWORDS = (
    "serif",
    "baskerville",
    "bodoni",
    "didot",
    "garamond",
    "georgia",
    "palatino",
    "times",
    "caslon",
    "cochin",
    "hoefler",
    "charter",
    "cambria",
    "constantia",
    "clarendon",
    "rockwell",
    "athelas",
    "iowan",
    "marion",
    "song",
    "ming",
    "mincho",
    "fangsong",
    "kaiti",
    "宋体",
    "明朝",
    "仿宋",
    "楷体",
)
_SANS_KEYWORDS = ("sans", "gothic", "grotesk", "grotesque", "helvetica", "arial", "hei", "黑体")


class SystemFont(BaseModel):
    """本机发现的一张可用静态矢量字体面。"""

    name: str  # 规范名（--font 可直接引用）
    path: str  # 本机绝对路径
    face_index: int = 0  # .ttc 内的面序号；.ttf / .otf 为 0
    family: str | None = None
    subfamily: str | None = None
    full_name: str | None = None
    postscript_name: str | None = None
    version: str | None = None
    copyright: str | None = None
    license_description: str | None = None
    license_url: str | None = None
    fs_type: int = 0
    embedding: str = "installable"
    no_subsetting: bool = False
    num_glyphs: int = 0
    has_latin: bool = False
    has_cjk: bool = False
    serif: bool = False
    weight_class: int = 400
    italic: bool = False
    is_cff: bool = False
    mtime_ns: int = 0
    size: int = 0


_MEM_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[SystemFont]]] = {}


def clear_cache() -> None:
    """清空进程内系统字体缓存（测试或字体目录变动后使用）。"""
    _MEM_CACHE.clear()


def system_fonts_enabled() -> bool:
    """是否允许扫描本机系统字体；PACXIO_SYSTEM_FONTS=0 / off / no / false 时关闭。"""
    val = os.environ.get(SYSTEM_FONTS_ENV, "").strip().lower()
    return val not in ("0", "off", "no", "false")


def system_font_dirs() -> list[Path]:
    """按优先级返回要扫描的系统字体目录（用户目录优先于系统目录）。"""
    override = os.environ.get(SYSTEM_FONT_DIRS_ENV)
    if override is not None:
        raw = [Path(p).expanduser() for p in override.split(os.pathsep) if p.strip()]
        return [d for d in raw if d.is_dir()]
    if sys.platform == "darwin":
        candidates = [
            Path.home() / "Library" / "Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
        ]
    elif sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        localapp = os.environ.get("LOCALAPPDATA")
        candidates = []
        if localapp:
            candidates.append(Path(localapp) / "Microsoft" / "Windows" / "Fonts")
        candidates.append(windir / "Fonts")
    else:
        candidates = [
            Path.home() / ".local" / "share" / "fonts",
            Path.home() / ".fonts",
            Path("/usr/local/share/fonts"),
            Path("/usr/share/fonts"),
        ]
    seen: set[Path] = set()
    out: list[Path] = []
    for d in candidates:
        if d.is_dir():
            rd = d.resolve()
            if rd not in seen:
                seen.add(rd)
                out.append(rd)
    return out


def _index_path() -> Path:
    return pacxio_home() / SYSTEM_INDEX_FILE


def _decode_embedding(fs_type: int) -> str:
    if fs_type & _FSTYPE_BITMAP_ONLY:
        return "bitmap_only"
    if fs_type & 0xF == _FSTYPE_RESTRICTED:
        return "restricted"
    if fs_type & _FSTYPE_PREVIEW_PRINT:
        return "preview_print"
    if fs_type & _FSTYPE_EDITABLE:
        return "editable"
    return "installable"


def _name(tt: TTFont, name_id: int) -> str | None:
    if "name" not in tt:
        return None
    raw = tt["name"].getDebugName(name_id)
    text = str(raw).strip() if raw else ""
    return text or None


def _clean_name(raw: str) -> str:
    return NAME_RE.sub("_", raw).strip("_") or "font"


def _is_serif(tt: TTFont, family: str | None, ps_name: str | None) -> bool:
    """结合 OS/2 sFamilyClass、PANOSE 与家族名关键词判是否衬线体。"""
    if "OS/2" in tt:
        os2 = tt["OS/2"]
        fam_cls = (int(getattr(os2, "sFamilyClass", 0)) >> 8) & 0xFF
        if fam_cls in _SERIF_FAMILY_CLASSES:
            return True
        if fam_cls == 8:
            return False
        panose = getattr(os2, "panose", None)
        if panose is not None:
            b_family = int(getattr(panose, "bFamilyType", 0))
            b_serif = int(getattr(panose, "bSerifStyle", 0))
            if b_family == 2 and 2 <= b_serif <= 10:
                return True
            if b_family == 2 and 11 <= b_serif <= 15:
                return False
    probe = f"{family or ''} {ps_name or ''}".lower()
    if any(k in probe for k in _SANS_KEYWORDS):
        return False
    return any(k in probe for k in _SERIF_KEYWORDS)


def _ttc_face_count(path: Path) -> int:
    try:
        with path.open("rb") as f:
            hdr = f.read(12)
        if len(hdr) == 12 and hdr[:4] == b"ttcf":
            cnt = int.from_bytes(hdr[8:12], "big")
            return max(1, min(cnt, 256))
    except OSError:
        pass
    return 1


def _walk_font_files(dirs: list[Path]) -> list[tuple[Path, int, int]]:
    """有界深度遍历字体目录，返回 (文件绝对路径, mtime_ns, size)。"""
    seen_files: set[Path] = set()
    out: list[tuple[Path, int, int]] = []

    def walk(folder: Path, depth: int) -> None:
        if depth > MAX_WALK_DEPTH:
            return
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir() and not entry.is_symlink():
                    walk(entry, depth + 1)
                    continue
                if not entry.is_file() or entry.suffix.lower() not in SCAN_SUFFIXES:
                    continue
                rp = entry.resolve()
                if rp in seen_files:
                    continue
                st = rp.stat()
                seen_files.add(rp)
                out.append((rp, st.st_mtime_ns, st.st_size))
            except OSError:
                continue

    for d in dirs:
        walk(d, 0)
    return out


def _scan_file(path: Path, mtime_ns: int, size: int) -> list[SystemFont]:
    """读取单个 .ttf / .otf / .ttc 里所有可用的静态矢量字体面。"""
    suffix = path.suffix.lower()
    is_ttc = suffix == ".ttc"
    count = _ttc_face_count(path) if is_ttc else 1
    faces: list[SystemFont] = []
    # fontTools 读某些系统字体（如 Apple 字体）的 post 表可能打 INFO/WARNING 日志，扫描时静默
    ft_logger = logging.getLogger("fontTools")
    prev_level = ft_logger.level
    ft_logger.setLevel(logging.ERROR)
    try:
        for idx in range(count):
            try:
                with TTFont(str(path), fontNumber=idx if is_ttc else -1, lazy=True) as tt:
                    if "fvar" in tt:
                        continue  # 跳过可变字体
                    has_outlines = any(t in tt for t in ("glyf", "CFF ", "CFF2"))
                    if not has_outlines or "CBDT" in tt or "sbix" in tt:
                        continue  # 跳过纯位图 / 彩色表情字体
                    ps = _name(tt, _NAME_POSTSCRIPT)
                    fam = _name(tt, _NAME_FAMILY)
                    sub = _name(tt, _NAME_SUBFAMILY)
                    full = _name(tt, _NAME_FULL)
                    if (ps and (ps.startswith(".") or ps in SKIP_NAMES)) or (
                        fam and (fam.startswith(".") or fam in SKIP_NAMES)
                    ):
                        continue
                    raw_id = ps or (f"{fam}-{sub}" if fam and sub else fam or path.stem)
                    clean = _clean_name(raw_id)
                    if not clean or clean in SKIP_NAMES:
                        continue
                    cmap = tt.getBestCmap() or {}
                    has_latin = all(ord(c) in cmap for c in _LATIN_PROBE)
                    cjk_hit = sum(1 for c in _CJK_PROBE if ord(c) in cmap) / len(_CJK_PROBE)
                    has_cjk = cjk_hit >= _CJK_MIN_HIT
                    os2 = tt["OS/2"] if "OS/2" in tt else None
                    fs_type = int(os2.fsType) if os2 is not None else 0
                    weight_class = int(getattr(os2, "usWeightClass", 400)) if os2 is not None else 400
                    fs_sel = int(getattr(os2, "fsSelection", 0)) if os2 is not None else 0
                    mac_style = int(tt["head"].macStyle) if "head" in tt else 0
                    italic = bool(
                        (fs_sel & 1)
                        or (mac_style & 2)
                        or (sub and any(k in sub.lower() for k in ("italic", "oblique")))
                    )
                    num_glyphs = int(tt["maxp"].numGlyphs) if "maxp" in tt else 0
                    faces.append(
                        SystemFont(
                            name=clean,
                            path=str(path),
                            face_index=idx,
                            family=fam,
                            subfamily=sub,
                            full_name=full,
                            postscript_name=ps,
                            version=_name(tt, _NAME_VERSION),
                            copyright=_name(tt, _NAME_COPYRIGHT),
                            license_description=_name(tt, _NAME_LICENSE),
                            license_url=_name(tt, _NAME_LICENSE_URL),
                            fs_type=fs_type,
                            embedding=_decode_embedding(fs_type),
                            no_subsetting=bool(fs_type & _FSTYPE_NO_SUBSETTING),
                            num_glyphs=num_glyphs,
                            has_latin=has_latin,
                            has_cjk=has_cjk,
                            serif=_is_serif(tt, fam, ps),
                            weight_class=weight_class,
                            italic=italic,
                            is_cff="CFF " in tt or "CFF2" in tt,
                            mtime_ns=mtime_ns,
                            size=size,
                        )
                    )
            except (TTLibError, OSError, ValueError, KeyError, AssertionError):
                continue
    finally:
        ft_logger.setLevel(prev_level)
    return faces


def _load_disk_cache() -> dict[str, dict[str, Any]]:
    p = _index_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != INDEX_SCHEMA_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save_disk_cache(by_file: dict[str, dict[str, Any]]) -> None:
    p = _index_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": INDEX_SCHEMA_VERSION, "files": by_file}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError:
        pass  # 只读目录或权限受限时跳过落盘，不影响正常扫描


def _dedupe_names(entries: list[SystemFont]) -> list[SystemFont]:
    """保持目录优先级（用户字体先于系统字体），重名时保留第一个。"""
    seen: set[str] = set()
    out: list[SystemFont] = []
    for e in entries:
        key = e.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def discover_system_fonts(*, refresh: bool = False) -> list[SystemFont]:
    """扫描并返回本机可用系统字体列表（受 PACXIO_SYSTEM_FONTS / PACXIO_SYSTEM_FONT_DIRS 控制）。"""
    if not system_fonts_enabled():
        return []
    dirs = system_font_dirs()
    if not dirs:
        return []
    cache_key = f"{pacxio_home()}|{'|'.join(str(d) for d in dirs)}"
    scanned = _walk_font_files(dirs)
    sig = tuple((str(p), m, s) for p, m, s in scanned)
    if not refresh and cache_key in _MEM_CACHE and _MEM_CACHE[cache_key][0] == sig:
        return list(_MEM_CACHE[cache_key][1])

    disk = {} if refresh else _load_disk_cache()
    updated_disk: dict[str, dict[str, Any]] = dict(disk)
    dirty = False
    raw_entries: list[SystemFont] = []
    for path, mtime_ns, size in scanned:
        pkey = str(path)
        cached = disk.get(pkey)
        if (
            isinstance(cached, dict)
            and cached.get("mtime_ns") == mtime_ns
            and cached.get("size") == size
            and isinstance(cached.get("faces"), list)
        ):
            try:
                raw_entries.extend(SystemFont.model_validate(x) for x in cached["faces"])
                continue
            except ValueError:
                pass
        faces = _scan_file(path, mtime_ns, size)
        raw_entries.extend(faces)
        updated_disk[pkey] = {
            "mtime_ns": mtime_ns,
            "size": size,
            "faces": [f.model_dump() for f in faces],
        }
        dirty = True

    if dirty:
        _save_disk_cache(updated_disk)
    entries = _dedupe_names(raw_entries)
    _MEM_CACHE[cache_key] = (sig, entries)
    return list(entries)


def find_system_font(name: str) -> SystemFont | None:
    """按规范名、PostScript 名、全名或家族常规面（忽略大小写兜底）查找本机系统字体。"""
    if not system_fonts_enabled() or not name.strip():
        return None
    entries = discover_system_fonts()
    for e in entries:
        if e.name == name or (e.postscript_name and e.postscript_name == name):
            return e
    low = name.strip().lower()
    for e in entries:
        if e.name.lower() == low or (e.postscript_name and e.postscript_name.lower() == low):
            return e
    full_hits = [e for e in entries if e.full_name and e.full_name.lower() == low]
    if len(full_hits) == 1:
        return full_hits[0]
    # 家族名兜底（如 "Georgia" / "Big Caslon"）：唯一匹配或优先取该家族的 Regular/Roman 非斜体
    fam_hits = [e for e in entries if e.family and e.family.lower() == low]
    if len(fam_hits) == 1:
        return fam_hits[0]
    if fam_hits:
        regular = [
            e
            for e in fam_hits
            if not e.italic
            and (
                (e.subfamily and e.subfamily.lower() in ("regular", "roman", "book", "normal"))
                or e.weight_class == 400
            )
        ]
        if len(regular) == 1:
            return regular[0]
    return None


def extract_static_font(
    entry: SystemFont, dest_dir: Path, name: str | None = None
) -> tuple[Path, str]:
    """把系统字体提取为静态 .ttf / .otf 写入 dest_dir，返回 (文件路径, sha256)。"""
    src = Path(entry.path)
    fname = _clean_name(name or entry.name)
    suffix = src.suffix.lower()
    ext = suffix if suffix in (".otf", ".ttf") else (".otf" if entry.is_cff else ".ttf")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{fname}{ext}"
    if suffix in (".otf", ".ttf"):
        shutil.copyfile(src, dest)
    else:
        ft_logger = logging.getLogger("fontTools")
        prev_level = ft_logger.level
        ft_logger.setLevel(logging.ERROR)
        try:
            with TTFont(
                str(src), fontNumber=entry.face_index, lazy=False, recalcTimestamp=False
            ) as tt:
                tt.flavor = None
                tt.save(str(dest))
        finally:
            ft_logger.setLevel(prev_level)
    return dest, sha256_of(dest)


def static_font_bytes(entry: SystemFont) -> tuple[bytes, str, str]:
    """返回系统字体面的静态 SFNT 字节、(后缀 .ttf/.otf) 和 sha256（不写盘）。"""
    import hashlib
    import io

    src = Path(entry.path)
    suffix = src.suffix.lower()
    ext = suffix if suffix in (".otf", ".ttf") else (".otf" if entry.is_cff else ".ttf")
    if suffix in (".otf", ".ttf"):
        data = src.read_bytes()
    else:
        ft_logger = logging.getLogger("fontTools")
        prev_level = ft_logger.level
        ft_logger.setLevel(logging.ERROR)
        try:
            with TTFont(
                str(src), fontNumber=entry.face_index, lazy=False, recalcTimestamp=False
            ) as tt:
                tt.flavor = None
                buf = io.BytesIO()
                tt.save(buf)
                data = buf.getvalue()
        finally:
            ft_logger.setLevel(prev_level)
    return data, ext, hashlib.sha256(data).hexdigest()
