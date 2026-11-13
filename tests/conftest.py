"""全部测试共用：按纸张自动选 ICC 时只在一个空文件夹里找（）。

本机装没装 Adobe 软件都一样：默认走简单公式；要测 ICC 的测试自己把 PACXIO_ICC_DIRS 指向有 ICC 的地方。
在 pytest_configure 里设：模块级 / 会话级的夹具比函数级的 autouse 夹具先建，函数级来不及。
"""

from __future__ import annotations

import importlib.util
import os
import tempfile

import pytest

_EMPTY: list[str] = []


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "pro: 要用到闭源包 pacxio_pro（）；只装开源包时跳过"
    )
    # 已有测试按中文消息断言：默认用中文跑；英文输出由 test_messages.py 在子进程里查（）
    os.environ["PACXIO_LANG"] = "zh"
    _EMPTY.append(tempfile.mkdtemp(prefix="pacxio-no-icc-"))
    os.environ["PACXIO_ICC_DIRS"] = _EMPTY[-1]


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    for folder in _EMPTY:
        os.rmdir(folder)  # 一直是空的


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """开源仓库里没有闭源包：标了 pro 的测试（跑 render / export 这类闭源命令）跳过。"""
    del config
    if importlib.util.find_spec("pacxio_pro") is not None:
        return
    skip = pytest.mark.skip(reason="要用闭源包 pacxio_pro（开源版里这些命令转给服务器）")
    for item in items:
        if "pro" in item.keywords:
            item.add_marker(skip)
