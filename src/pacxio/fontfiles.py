"""字体文件在哪里（开源）：按名字找字体文件、解析字体链。

查找顺序见 `pacxio_pro.layout.text` 的说明（job 自己的 `fonts/` → PACXIO_FONT_DIR → 当前目录 fonts/ →
源码仓库 fonts/ → ~/.pacxio/fonts → ~/.packengine/fonts）。排字、转曲在 `layout.text`（闭源）。
"""

from __future__ import annotations

import os
from pathlib import Path

from pacxio.messages import t
from pacxio.model.common import Issue

FONT_DIR_ENV = "PACXIO_FONT_DIR"
USER_FONT_DIR = Path.home() / ".pacxio" / "fonts"
# 2026-11-09 改名 pacxio 前的用户级目录：已下载的字体照样能找到，确认过的设计不因"找不到字体"过期
LEGACY_USER_FONT_DIR = Path.home() / ".packengine" / "fonts"
FONT_SUFFIXES = (".otf", ".ttf", ".ttc")
JOB_FONT_DIR = "fonts"  # job 目录下存放自带字体的子目录
CHAIN_SEP = ","


class TextError(Exception):
    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


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
    return dirs


def fetch_command(*args: str) -> str:
    """下载开源字体的命令：随 Skill 带的 scripts/fetch_fonts.py（源码仓库的 skill/ 或随包安装的 _skill/）。"""
    here = Path(__file__).resolve().parent
    for d in (here.parents[1] / "skill", here / "_skill"):
        script = d / "packaging-prepress" / "scripts" / "fetch_fonts.py"
        if script.is_file():
            return " ".join(["python3", str(script), *args])
    return " ".join(["python3", "<Skill folder>/scripts/fetch_fonts.py", *args])


def find_font(name: str, job_dir: Path | None = None) -> Path:
    """按名字（可带或不带后缀）在字体目录里找文件。"""
    for d in font_dirs(job_dir):
        for suffix in ("", *FONT_SUFFIXES):
            p = d / f"{name}{suffix}"
            if p.is_file():
                return p
    searched = ", ".join(str(d) for d in font_dirs(job_dir))
    if name.startswith("SourceHanSerif"):
        fix = t("Download Source Han Serif with `{command}` (about 50 MB, needs the network), "
                "or set PACXIO_FONT_DIR", command=fetch_command("--with-serif"))  # fmt: skip
    else:
        fix = t("Add a brand font with `pacxio font add <job> <font-file>`. For Source Han Sans, run "
                "`pacxio setup --yes` or `{command}`, or set PACXIO_FONT_DIR.", command=fetch_command())  # fmt: skip
    raise TextError(Issue(id="FONT", message=t("Could not find font {font} (searched: {paths})", font=name, paths=searched), fix=fix))


def parse_font_chain(font: str) -> list[str]:
    """ "A, B" → ["A", "B"]。"""
    names = [n.strip() for n in font.split(CHAIN_SEP) if n.strip()]
    if not names:
        raise TextError(
            Issue(id="FONT", message=t("font is empty"), fix=t("Supply a font name such as SourceHanSansSC-Bold"))
        )
    return names
