"""开源 / 闭源的模块边界（，，  / ）。

闭源部分是单独的私有包 `pacxio_pro`（本仓库 `pro/`），只在服务器和开发机上装。开源代码只在用到时导入它；
开源版里用到它的命令由 `cloud.route` 转给服务器。`block_closed()` 在装着闭源包的机器上模拟"开源版"——
测试用它确认开源命令在没有闭源包时照常跑。
"""

from __future__ import annotations

import importlib.abc
import sys
from types import ModuleType

from pacxio.messages import t

CLOSED = "pacxio_pro"
# 有这个模块就说明本机装着闭源部分（开发机、服务器）
PROBE = "pacxio_pro.layout.compose"


def is_closed(name: str) -> bool:
    return name == CLOSED or name.startswith(CLOSED + ".")


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object, target: ModuleType | None = None) -> None:
        if is_closed(fullname):
            raise ModuleNotFoundError(
                t("The open-source version has no closed module {name}", name=fullname),
                name=fullname,
            )
        return None


def block_closed() -> None:
    """让之后的 import 看不到闭源包（只在全新的进程里调用；已导入的模块不受影响）。"""
    loaded = [m for m in sys.modules if is_closed(m)]
    if loaded:
        raise RuntimeError(
            t("Closed modules already imported: {names}", names=", ".join(sorted(loaded)))
        )
    sys.meta_path.insert(0, _Blocker())
