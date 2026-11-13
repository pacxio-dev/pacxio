#!/usr/bin/env python3
"""Check pacxio's environment: what is missing and how to install it. Standard library only,
so it runs even when the engine is not installed.

Never installs anything (no pip install while the Skill runs) — it only reports and gives
install instructions; network access depends on the current environment.

    python scripts/check_env.py           # human-readable result
    python scripts/check_env.py --json    # machine-readable result

Exit codes: 0 environment usable; 3 environment missing (engine not installed / required
dependency missing / no fonts). Missing Ghostscript, API keys or ICC are only warnings.
Messages are English; PACXIO_LANG=zh switches them to Chinese.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BIN_ENV = "PACXIO_BIN"  # the pacxio executable to use (tests / non-standard installs)
CODEX_HOME_ENV = "CODEX_HOME"
EXIT_OK, EXIT_MISSING = 0, 3
ZH = os.environ.get("PACXIO_LANG", "").strip().lower().startswith("zh")


def _t(en: str, zh: str) -> str:
    return zh if ZH else en


INSTALL_ENGINE = _t(
    "The engine is not installed. In the pacxio source folder run: uv tool install .  "
    "(once published: uv tool install pacxio); without uv, install uv first: https://docs.astral.sh/uv/",
    "引擎没有安装。在 pacxio 源码目录里运行：uv tool install .  （已发布到包索引后：uv tool install pacxio）；"
    "没有 uv 的话先装 uv：https://docs.astral.sh/uv/",
)
INSTALL_FONT = _t(
    "Missing font: python scripts/fetch_fonts.py  (downloads Source Han Sans to ~/.pacxio/fonts, about 33 MB, needs the network once)",
    "缺字体：python scripts/fetch_fonts.py  （下载思源黑体到 ~/.pacxio/fonts，约 33MB，需要联网一次）",
)
INSTALL_SERIF = _t(
    "Optional serif font: python scripts/fetch_fonts.py --with-serif  (also downloads Source Han Serif, about 50 MB, needs the network once)",
    "可选的衬线字体：python scripts/fetch_fonts.py --with-serif  （另下载思源宋体，约 50MB，需要联网一次）",
)
INSTALL_GS = _t(
    "Missing Ghostscript (optional but strongly recommended; used by the separation checks B02, F01): on macOS run brew install ghostscript",
    "缺 Ghostscript（可选，但强烈建议；用于分色类检查 B02、F01）：macOS 运行 brew install ghostscript",
)


def find_command() -> list[str] | None:
    """The command-line prefix that runs pacxio."""
    explicit = os.environ.get(BIN_ENV)
    if explicit:
        return [explicit] if Path(explicit).exists() or shutil.which(explicit) else None
    exe = shutil.which("pacxio")
    if exe:
        return [exe]
    probe = subprocess.run(
        [sys.executable, "-c", "import pacxio"], capture_output=True, check=False
    )
    return [sys.executable, "-m", "pacxio"] if probe.returncode == 0 else None


def run_env_check(cmd: list[str]) -> dict | None:  # type: ignore[type-arg]
    proc = subprocess.run(
        [*cmd, "env", "check", "--json"], capture_output=True, text=True, check=False
    )
    try:
        return json.loads(proc.stdout)  # type: ignore[no-any-return]
    except ValueError:
        return None


def codex_images() -> dict[str, object]:
    home = os.environ.get(CODEX_HOME_ENV)
    d = (Path(home) if home else Path.home() / ".codex") / "generated_images"
    n = (
        sum(1 for p in d.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
        if d.is_dir()
        else 0
    )
    return {"dir": str(d), "exists": d.is_dir(), "images": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--json", action="store_true", help=_t("Print JSON", "输出 JSON"))
    args = ap.parse_args()

    result: dict[str, object] = {
        "ok": True,
        "engine": None,
        "problems": [],
        "warnings": [],
        "install": [],
    }
    problems: list[str] = result["problems"]  # type: ignore[assignment]
    warnings: list[str] = result["warnings"]  # type: ignore[assignment]
    install: list[str] = result["install"]  # type: ignore[assignment]

    cmd = find_command()
    if cmd is None:
        problems.append(
            _t(
                "The engine (pacxio) is not installed or not on PATH",
                "引擎（pacxio）没有安装或不在 PATH 上",
            )
        )
        install.append(INSTALL_ENGINE)
    else:
        env = run_env_check(cmd)
        if env is None:
            problems.append(
                _t("pacxio env check returned no valid result", "pacxio env check 没有返回有效结果")
            )
            install.append(INSTALL_ENGINE)
        else:
            data = env.get("data", {})
            result["engine"] = {"command": cmd, "python": data.get("python"), "ghostscript": data.get("ghostscript"),
                                "font": data.get("font"), "serif_font": data.get("serif_font"),
                                "profile": data.get("profile_default"), "icc": data.get("icc"),
                                "icc_auto": data.get("icc_auto")}  # fmt: skip
            for e in env.get("errors", []):
                problems.append(e["message"])
                if e.get("fix"):
                    install.append(e["fix"])
            if not data.get("font"):
                problems.append(
                    _t(
                        "Source Han Sans not found: layouts with text cannot be set",
                        "没有找到思源黑体：带文字的版面无法排版",
                    )
                )
                install.append(INSTALL_FONT)
            if "serif_font" in data and not data.get("serif_font"):
                warnings.append(
                    _t(
                        "Optional: no serif font (Source Han Serif) — only needed for serif titles",
                        "可选：没有衬线字体（思源宋体）——需要衬线标题时才要装",
                    )
                )
                install.append(INSTALL_SERIF)
            cloud = (
                data.get("composition") == "cloud"
            )  # open-source edition: separations and ICC run in the cloud
            if not data.get("ghostscript") and not cloud:
                warnings.append(
                    _t(
                        "Ghostscript not installed: separation checks B02, F01 will be skipped (the report says so; that is not a pass)",
                        "未安装 Ghostscript：分色类检查 B02、F01 将被跳过（报告里会标明，不等于通过）",
                    )
                )
                install.append(INSTALL_GS)
            # Without a factory ICC in the profile the engine picks a local standard ICC by paper: only papers whose ICC is missing are unreliable
            icc_auto = data.get("icc_auto")
            missing = (
                [k for k, v in icc_auto.items() if not v] if isinstance(icc_auto, dict) else []
            )
            if data.get("icc") is None and missing:
                warnings.append(
                    _t(
                        f"Standard ICC missing on this machine ({', '.join(missing)}): photos on these papers are converted to print colors with a simple formula; the proof decides the color",
                        f"本机缺标准 ICC（{'、'.join(missing)}）：这类纸张的照片按简单公式转成印刷色，颜色以打样为准",
                    )
                )
            prof = data.get("profile_default") or {}
            if prof and not prof.get("verified"):
                warnings.append(
                    _t(
                        "The default profile has not been confirmed by a factory: all values are initial",
                        "默认配置档未经工厂确认：所有数值都是初值",
                    )
                )

    # Image sources (only whether they exist; never read or print the key itself)
    result["image_sources"] = {
        "openai_api_key": bool(os.environ.get("OPENAI_API_KEY")),
        "codex_generated_images": codex_images(),
    }

    result["ok"] = not problems
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            _t("Environment check: ", "环境检查：")
            + (_t("usable", "可用") if result["ok"] else _t("problems found", "有问题"))
        )
        eng = result["engine"]
        if eng:
            print(
                _t("  Engine: ", "  引擎：")
                + f"{' '.join(eng['command'])} (Python {eng['python']})"
            )  # type: ignore[index]
        for p in problems:
            print(f"  ✗ {p}")
        for w in warnings:
            print(f"  ! {w}")
        src = result["image_sources"]
        key_set = src["openai_api_key"]  # type: ignore[index]
        n_images = src["codex_generated_images"]["images"]  # type: ignore[index]
        print(
            _t(
                f"  Images: OPENAI_API_KEY {'set' if key_set else 'not set'}; Codex built-in image folder has {n_images} images",
                f"  出图：OPENAI_API_KEY {'已设置' if key_set else '未设置'}；Codex 内置出图目录 {n_images} 张图",
            )
        )
        if install:
            print(_t("Install instructions:", "安装指引："))
            for i in dict.fromkeys(install):
                print(f"  - {i}")
    return EXIT_OK if result["ok"] else EXIT_MISSING


if __name__ == "__main__":
    raise SystemExit(main())
