"""字体文件在哪里（开源）：按名字找字体文件、解析字体链。

查找顺序见 `pacxio_pro.layout.text` 的说明（job 自己的 `fonts/` → PACXIO_FONT_DIR → 当前目录 fonts/ →
源码仓库 fonts/ → ~/.pacxio/fonts → ~/.packengine/fonts）。排字、转曲在 `layout.text`（闭源）。
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pacxio.fontsubset import EPHEMERAL_FONT_DIR, EPHEMERAL_FONT_DIR_ENV, read_subset_meta
from pacxio.messages import t
from pacxio.model.common import Issue

FONT_DIR_ENV = "PACXIO_FONT_DIR"
USER_FONT_DIR = Path.home() / ".pacxio" / "fonts"
# 2026-11-09 改名 pacxio 前的用户级目录：已下载的字体照样能找到，确认过的设计不因"找不到字体"过期
LEGACY_USER_FONT_DIR = Path.home() / ".packengine" / "fonts"
FONT_SUFFIXES = (".otf", ".ttf", ".ttc")
JOB_FONT_DIR = "fonts"  # job 目录下存放自带字体的子目录
# 开源字体库（tools/fetch_font_library.py 下载到字体目录的这个子目录；）：
# 按名字找得到，排在所有字体目录后面（用户自己的字体优先）
LIBRARY_DIR = "library"
LIBRARY_INDEX = "library.json"
# 设成 0 / off：restore text 不比字体库（测试结果不随本机装没装字体库变；按名字找字体不受影响）
LIBRARY_OFF_ENV = "PACXIO_FONT_LIBRARY"
CHAIN_SEP = ","
NAME_VERSION = 5  # OpenType nameID 5（Version string）


class TextError(Exception):
    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def ephemeral_font_dirs(job_dir: Path | None = None) -> list[Path]:
    """单次云端运行的瞬时字体子集目录（优先于 job/fonts，用后即删）。"""
    dirs: list[Path] = []
    env = os.environ.get(EPHEMERAL_FONT_DIR_ENV, "").strip()
    if env:
        dirs.append(Path(env))
    if job_dir is not None:
        dirs.append(job_dir / EPHEMERAL_FONT_DIR)
    return dirs


def font_dirs(job_dir: Path | None = None) -> list[Path]:
    dirs: list[Path] = []
    if job_dir is not None:
        dirs.append(job_dir / JOB_FONT_DIR)  # 用户自带字体优先：同一个 job 在哪里重新生成都一致
    env = os.environ.get(FONT_DIR_ENV)
    if env:
        dirs.append(Path(env))
    dirs.append(Path.cwd() / "fonts")
    dirs.append(Path(__file__).resolve().parents[2] / "fonts")  # 源码仓库
    dirs.append(USER_FONT_DIR)  # 独立安装（uv tool install）时的用户级目录
    dirs.append(LEGACY_USER_FONT_DIR)
    shared = dirs[1:] if job_dir is not None else list(dirs)
    return dirs + [d / LIBRARY_DIR for d in shared]


def fetch_command(*args: str) -> str:
    """下载开源字体的命令：随 Skill 带的 scripts/fetch_fonts.py（源码仓库的 skill/ 或随包安装的 _skill/）。"""
    here = Path(__file__).resolve().parent
    for d in (here.parents[1] / "skill", here / "_skill"):
        script = d / "packaging-prepress" / "scripts" / "fetch_fonts.py"
        if script.is_file():
            return " ".join(["python3", str(script), *args])
    return " ".join(["python3", "<Skill folder>/scripts/fetch_fonts.py", *args])


_LOOKUPS: contextvars.ContextVar[dict[tuple[str, ...], tuple[Path, int]] | None] = (
    contextvars.ContextVar("pacxio_font_lookups", default=None)
)


def invalidate_cached_lookups() -> None:
    """job/fonts 写入新字体后清掉当前上下文的查找缓存，避免同次调用里仍命中旧系统路径。"""
    cache = _LOOKUPS.get()
    if cache is not None:
        cache.clear()


@contextlib.contextmanager
def cached_lookups() -> Iterator[None]:
    """这一段里按名字找字体，同一个名字只查一次文件系统（restore text 要排几千次字）。
    只在这一段里有效：出了这一段照旧每次查，加了字体马上找得到。"""
    token = _LOOKUPS.set({})
    try:
        yield
    finally:
        _LOOKUPS.reset(token)


def find_font_face(
    name: str, job_dir: Path | None = None, *, include_system: bool = True
) -> tuple[Path, int]:
    """按名字找字体文件及 .ttc 面序号 `(path, face_index)`。

    查找顺序：瞬时字体子集目录 → job/fonts → 环境变量/工作目录/用户字体目录 → 开源字体库 → 本机系统字体。
    """
    from pacxio.sysfonts import (
        SYSTEM_FONT_DIRS_ENV,
        SYSTEM_FONTS_ENV,
        find_system_font,
        system_fonts_enabled,
    )

    cache = _LOOKUPS.get()
    key = (
        name,
        str(job_dir),
        os.environ.get(FONT_DIR_ENV, ""),
        os.environ.get(EPHEMERAL_FONT_DIR_ENV, ""),
        os.environ.get(SYSTEM_FONTS_ENV, ""),
        os.environ.get(SYSTEM_FONT_DIRS_ENV, ""),
        "1" if include_system else "0",
        str(Path.cwd()),
    )
    if cache is not None and key in cache:
        return cache[key]
    for d in [*ephemeral_font_dirs(job_dir), *font_dirs(job_dir)]:
        for suffix in ("", *FONT_SUFFIXES):
            p = d / f"{name}{suffix}"
            if p.is_file():
                found = (p, 0)
                if cache is not None:
                    cache[key] = found
                return found
    if include_system and system_fonts_enabled():
        sys_entry = find_system_font(name)
        if sys_entry is not None:
            found = (Path(sys_entry.path), sys_entry.face_index)
            if cache is not None:
                cache[key] = found
            return found
    searched = ", ".join(str(d) for d in font_dirs(job_dir))
    if name.startswith("SourceHanSerif"):
        fix = t("Download Source Han Serif with `{command}` (about 50 MB, needs the network), "
                "or set PACXIO_FONT_DIR", command=fetch_command("--with-serif"))  # fmt: skip
    else:
        fix = t("Add a brand font with `pacxio font add <job> <font-file>`. For Source Han Sans, run "
                "`pacxio setup --yes` or `{command}`, or set PACXIO_FONT_DIR.", command=fetch_command())  # fmt: skip
    raise TextError(Issue(id="FONT", message=t("Could not find font {font} (searched: {paths})", font=name, paths=searched), fix=fix))


def find_font(name: str, job_dir: Path | None = None, *, include_system: bool = True) -> Path:
    """按名字（可带或不带后缀）在字体目录（及本机系统字体）里找文件。"""
    return find_font_face(name, job_dir, include_system=include_system)[0]


def parse_font_chain(font: str) -> list[str]:
    """ "A, B" → ["A", "B"]。"""
    names = [n.strip() for n in font.split(CHAIN_SEP) if n.strip()]
    if not names:
        raise TextError(
            Issue(id="FONT", message=t("font is empty"), fix=t("Supply a font name such as SourceHanSansSC-Bold"))
        )
    return names


def font_sha256(path: Path) -> str:
    """字体文件的 SHA-256 十六进制摘要。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@functools.lru_cache(maxsize=256)
def _font_version_cached(path_str: str, sha: str, face_index: int) -> str | None:
    del sha  # 按内容哈希缓存：文件一变就重新读
    from fontTools.ttLib import TTFont, TTLibError

    try:
        with TTFont(path_str, fontNumber=face_index, lazy=True) as tt:
            raw = tt["name"].getDebugName(NAME_VERSION) if "name" in tt else None
    except (TTLibError, OSError, ValueError, KeyError):
        return None
    text = str(raw).strip() if raw else ""
    return text or None


def font_version(path: Path, sha: str | None = None, *, face_index: int = 0) -> str | None:
    """字体文件的 OpenType 版本串（nameID 5）；读不出来返回 None。"""
    digest = sha if sha is not None else font_sha256(path)
    return _font_version_cached(str(path), digest, face_index)


@functools.lru_cache(maxsize=16)
def _read_library_index(path_str: str, mtime_ns: int, size: int) -> dict[str, dict[str, Any]]:
    del mtime_ns, size
    try:
        items = json.loads(Path(path_str).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        file_name = item.get("file")
        font_name = item.get("font")
        if isinstance(file_name, str):
            out[file_name] = item
        if isinstance(font_name, str):
            out[font_name] = item
    return out


def library_entry(path: Path) -> dict[str, Any] | None:
    """字体在开源字体库（library/）里时，返回 library.json 里的对应条目。"""
    if path.parent.name != LIBRARY_DIR:
        return None
    index = path.parent / LIBRARY_INDEX
    if not index.is_file():
        return None
    try:
        st = index.stat()
    except OSError:
        return None
    table = _read_library_index(str(index), st.st_mtime_ns, st.st_size)
    return table.get(path.name) or table.get(path.stem)


def describe_font(
    path: Path, job_dir: Path | None = None, *, face_index: int = 0
) -> dict[str, str | None]:
    """字体文件的锁定信息：{sha256, version, source}。"""
    sha = font_sha256(path)
    sub = read_subset_meta(path)
    if sub is not None and sub.subset_sha256 == sha:
        return {
            "sha256": sub.source_sha256,
            "version": sub.source_version or font_version(path, sha, face_index=face_index),
            "source": "system" if sub.origin == "system" else "job",
        }
    entry = library_entry(path)
    version = font_version(path, sha, face_index=face_index)
    if version is None and entry is not None and isinstance(entry.get("version"), str):
        version = entry["version"].strip() or None
    source: str | None = None
    if job_dir is not None:
        try:
            if path.resolve().is_relative_to((job_dir / JOB_FONT_DIR).resolve()):
                meta_file = path.with_name(path.stem + ".meta.json")
                origin = None
                if meta_file.is_file():
                    try:
                        origin = json.loads(meta_file.read_text(encoding="utf-8")).get("origin")
                    except (OSError, ValueError):
                        origin = None
                source = "system" if origin == "system" else "job"
        except OSError:
            pass
    if source is None and path.parent.name == LIBRARY_DIR:
        raw_src = entry.get("source") if entry is not None else None
        source = raw_src if isinstance(raw_src, str) and raw_src else LIBRARY_DIR
    if source is None:
        from pacxio.sysfonts import system_font_dirs

        try:
            rp = path.resolve()
            if any(rp.is_relative_to(d) for d in system_font_dirs()):
                source = "system"
        except OSError:
            pass
    return {"sha256": sha, "version": version, "source": source}
