"""打包：源码里用到的每个第三方库都必须是运行时依赖，配置档随 wheel 安装。

开发环境装了 dev 依赖组，缺运行时依赖时测试不会失败——这个测试专门防这种漏网
（真实发生过：pikepdf 被留在 dev 组，干净安装后 import 失败）。
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "pacxio"
PRO = ROOT / "pro"  # 闭源包（）；开源仓库里没有
OWN = {"pacxio", "pacxio_pro"}  # 开源代码只在用到时导入 pacxio_pro，没装时转发给服务器
# import 名 → 发行包名
DIST = {"PIL": "pillow", "yaml": "pyyaml", "fontTools": "fonttools"}


def runtime_deps(root: Path = ROOT) -> set[str]:
    data = tomllib.loads((root / "pyproject.toml").read_text())
    return {
        re.split(r"[<>=!~\[ ]", d, maxsplit=1)[0].lower() for d in data["project"]["dependencies"]
    }


def third_party_imports(src: Path = SRC) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for f in src.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if (
                    top not in OWN
                    and top not in sys.stdlib_module_names
                    and top != "__future__"
                ):
                    found.setdefault(top, set()).add(str(f.relative_to(src)))
    return found


def test_every_third_party_import_is_a_runtime_dependency() -> None:
    deps = runtime_deps()
    missing = {
        mod: sorted(files)
        for mod, files in third_party_imports().items()
        if DIST.get(mod, mod).lower() not in deps
    }
    assert not missing, f"这些库被源码导入，却不在 [project].dependencies 里：{missing}"


def test_private_package_dependencies() -> None:
    if not PRO.is_dir():
        pytest.skip("开源仓库里没有闭源包")
    deps = runtime_deps(PRO) | runtime_deps()  # pacxio-pro 依赖 pacxio，它的依赖也装着
    missing = {
        mod: sorted(files)
        for mod, files in third_party_imports(PRO / "src" / "pacxio_pro").items()
        if DIST.get(mod, mod).lower() not in deps
    }
    assert not missing, f"闭源包导入了却没写进 pro/pyproject.toml 的库：{missing}"


def test_open_package_does_not_carry_private_only_dependencies() -> None:
    if not PRO.is_dir():
        pytest.skip("开源仓库里没有闭源包")
    used = {DIST.get(m, m).lower() for m in third_party_imports()}
    extra = (runtime_deps() & runtime_deps(PRO)) - used
    assert not extra, f"只有闭源包用到的库别放在开源包的依赖里：{sorted(extra)}"


def test_profiles_ship_inside_the_wheel_and_are_searched() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    force = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force == {
        "profiles": "pacxio/_profiles",
        "conventions": "pacxio/_conventions",  # 品类常规参考表也随包安装
        # Skill 说明随包安装：MCP 把它当资源给没装 Skill 的 agent 读（）
        "skill/packaging-prepress": "pacxio/_skill/packaging-prepress",
    }
    from pacxio.model.conventions import conventions_dirs
    from pacxio.model.profile import profile_dirs

    assert any(d.name == "_profiles" and d.parent.name == "pacxio" for d in profile_dirs())
    assert any(
        d.name == "_conventions" and d.parent.name == "pacxio" for d in conventions_dirs()
    )
