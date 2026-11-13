#!/usr/bin/env python3
"""Download open-source fonts (never committed to the repository).

Only fonts free for commercial use: Source Han Sans / Source Han Serif (Adobe, SIL OFL 1.1).
All text is set by the engine and outlined, PDFs embed no fonts, so these fonts are only used
while generating files.

    python scripts/fetch_fonts.py                 # Source Han Sans Regular + Bold
    python scripts/fetch_fonts.py --with-serif    # also Source Han Serif
    python scripts/fetch_fonts.py --dir /path     # another folder

Default folder: the PACXIO_FONT_DIR environment variable, otherwise ~/.pacxio/fonts (where the
engine looks for fonts).

Shows progress while downloading; when a source is too slow (under 100 KB/s on average after
20 seconds) it moves to the next. Source Han Serif files are about 25 MB each, over jsDelivr's
20 MB limit, so they come straight from GitHub.

Downloads are checked against SHA-256. The Source Han Sans hashes were recorded on first download
(jsDelivr release branch, sizes matching the CDN) — "trust on first use", not yet cross-checked
against the upstream release page; Source Han Serif has no recorded hash yet.
Messages are English; PACXIO_LANG=zh switches them to Chinese.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

# (file name, [download URLs, tried in order], expected SHA-256 or None)
CDN = "https://cdn.jsdelivr.net/gh/adobe-fonts/{repo}@release/OTF/SimplifiedChinese/{name}"
RAW = "https://github.com/adobe-fonts/{repo}/raw/release/OTF/SimplifiedChinese/{name}"


def _urls(repo: str, name: str, large: bool = False) -> list[str]:
    """large: files over jsDelivr's single-file limit (20 MB) only come from GitHub."""
    raw = RAW.format(repo=repo, name=name)
    return [raw] if large else [CDN.format(repo=repo, name=name), raw]


SANS = [
    (
        "SourceHanSansSC-Regular.otf",
        _urls("source-han-sans", "SourceHanSansSC-Regular.otf"),
        "f1d8611151880c6c336aabeac4640ef434fa13cbfbf1ffe82d0a71b2a5637256",
    ),
    (
        "SourceHanSansSC-Bold.otf",
        _urls("source-han-sans", "SourceHanSansSC-Bold.otf"),
        "df2b90f5bcc6d01dfc964cec5f6d535d6b6aebd26ed7fd79a9c1b3f2112fcb6b",
    ),
]
SERIF = [
    (
        "SourceHanSerifSC-Regular.otf",
        _urls("source-han-serif", "SourceHanSerifSC-Regular.otf", large=True),
        None,
    ),
    (
        "SourceHanSerifSC-Bold.otf",
        _urls("source-han-serif", "SourceHanSerifSC-Bold.otf", large=True),
        None,
    ),
]
CHUNK = 1 << 20
READ = 64 << 10  # bytes per read (whatever arrives, to measure speed)
TIMEOUT_S = 60
STALL_S = 20  # after this many seconds
MIN_RATE_KB = 100  # still slower than this on average: try the next URL
PROGRESS_MB = 5  # print progress every this many MB


ZH = os.environ.get("PACXIO_LANG", "").strip().lower().startswith("zh")


def _t(en: str, zh: str) -> str:
    return zh if ZH else en


class Stalled(OSError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, tmp: Path) -> None:
    t0, got, shown = time.monotonic(), 0, 0
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r, tmp.open("wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while chunk := r.read1(READ):
            f.write(chunk)
            got += len(chunk)
            dt = time.monotonic() - t0
            if dt > STALL_S and got / dt < MIN_RATE_KB * 1024:
                raise Stalled(
                    _t(
                        f"too slow: only {got / 1e6:.1f} MB in {dt:.0f} s",
                        f"太慢：{dt:.0f} 秒只下了 {got / 1e6:.1f} MB",
                    )
                )
            if got - shown >= PROGRESS_MB * 1e6:
                shown = got
                of = f" / {total / 1e6:.1f}" if total else ""
                print(f"  {got / 1e6:.1f}{of} MB ({got / 1e6 / dt:.1f} MB/s)", flush=True)


def download(urls: list[str], dest: Path) -> bool:
    """Download to dest; False means a file of the same name appeared meanwhile (kept, not overwritten)."""
    last: Exception | None = None
    for url in urls:
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            _fetch(url, tmp)
        except OSError as e:
            last = e
            tmp.unlink(missing_ok=True)
            print(_t(f"  failed {url}: {e}", f"  失败 {url}：{e}"), file=sys.stderr)
            continue
        if dest.exists():  # someone downloaded it by hand meanwhile: do not overwrite
            tmp.unlink()
            return False
        tmp.replace(dest)
        return True
    raise SystemExit(_t(f"all download URLs failed: {last}", f"所有下载地址都失败：{last}"))


def default_dir() -> Path:
    env = os.environ.get("PACXIO_FONT_DIR")
    return Path(env) if env else Path.home() / ".pacxio" / "fonts"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dir", type=Path, default=default_dir())
    ap.add_argument(
        "--with-serif",
        action="store_true",
        help=_t("Also download Source Han Serif", "同时下载思源宋体"),
    )
    args = ap.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    for name, urls, want in SANS + (SERIF if args.with_serif else []):
        dest = args.dir / name
        if dest.exists() and (want is None or sha256_of(dest) == want):
            print(_t(f"already there: {dest}", f"已存在 {dest}"))
            continue
        print(_t(f"downloading {name} ...", f"下载 {name} ..."), flush=True)
        if not download(urls, dest):
            print(
                _t(
                    f"  {dest} appeared from elsewhere during the download; kept it (not overwritten)",
                    f"  下载期间 {dest} 已经由别处放好，保留它（没有覆盖）",
                )
            )
        got = sha256_of(dest)
        if want is None:
            print(
                _t(
                    f"  sha256 {got} (no expected hash in the list; check it by hand before recording it)",
                    f"  sha256 {got}（清单里没有期望哈希，请人工核对后写入 tools/fetch_fonts.py）",
                )
            )
        elif got != want:
            dest.unlink()
            raise SystemExit(
                _t(
                    f"{name} checksum failed: expected {want}, got {got}",
                    f"{name} 校验失败：期望 {want}，实际 {got}",
                )
            )
        print(f"  ok {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
