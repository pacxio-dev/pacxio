"""ECMA 编号 ↔ 结构配方（几何引擎第 1 步）。只用公开资料（用户 2026-11-05 定）：编号规则来自 Labelprint24 /
reference 的说明，各封口长什么样来自 reference 在线刀版库（dielines.reference.com，只在网页上读）。

A 类（长方形、侧边糊口）：A + 底部封口 . 顶部封口 . 底部封口在第几面 . 顶部封口在第几面（00 = 不分面）。
面从紧挨糊口的那面数起：01 = 背面（引擎第 0 面墙），03 = 正面（第 2 面墙）。每种封口的面号指：
插舌盖 / 侧插盖挂的面、全重叠 / 缩小翼里在最外（满翼）的那片、扣底锁舌翼、挂板（盖在对面）。
"""

from __future__ import annotations

import re
from typing import Any

from pacxio.messages import t as translate

# 封口编号 → 配方里的一端
CLOSURES: dict[str, dict[str, Any]] = {
    "01": {"kind": "open"},
    "10": {
        "kind": "flaps",
        "flap": "full_long",
    },  # 长翼全宽、端翼 ½W（§20：和 FEFCO 0203 的 full 分开）
    "11": {"kind": "reduced"},
    "20": {"kind": "lid_tuck"},
    "21": {"kind": "hanger"},
    "40": {"kind": "side_tuck"},
    "55": {"kind": "snaplock"},
    "60": {"kind": "autolock"},
}
CLOSURE_ZH = {"01": "开口", "10": "四翼全重叠", "11": "缩小翼", "20": "插舌 + 防尘翼", "21": "插舌 + 背板加高挂板",
              "40": "侧插（翼插、无防尘翼）", "55": "扣底（1-2-3 底）", "60": "自动锁底"}  # fmt: skip
PANEL_WALL = {"00": None, "01": 0, "03": 2}
WALL_PANEL = {0: "01", 2: "03"}
CODE_RE = re.compile(r"^A(\d\d)\.(\d\d)\.(\d\d)\.(\d\d)$")

# reference 在线刀版库列出的 ECMA 款（全部 A 类，130 款；）
PUBLIC_A: tuple[str, ...] = (
    "A01.01.00.00",
    "A01.10.00.01",
    "A01.10.00.03",
    "A01.11.00.01",
    "A01.11.00.03",
    "A01.20.00.01",
    "A01.20.00.03",
    "A01.21.00.01",
    "A01.21.00.03",
    "A01.40.00.01",
    "A01.40.00.03",
    "A10.01.01.00",
    "A10.01.03.00",
    "A10.10.01.01",
    "A10.10.01.03",
    "A10.10.03.01",
    "A10.10.03.03",
    "A10.11.01.01",
    "A10.11.01.03",
    "A10.11.03.01",
    "A10.11.03.03",
    "A10.20.01.01",
    "A10.20.01.03",
    "A10.20.03.01",
    "A10.20.03.03",
    "A10.21.01.01",
    "A10.21.01.03",
    "A10.21.03.01",
    "A10.21.03.03",
    "A10.40.01.01",
    "A10.40.01.03",
    "A10.40.03.01",
    "A10.40.03.03",
    "A11.01.01.00",
    "A11.01.03.00",
    "A11.10.01.01",
    "A11.10.01.03",
    "A11.10.03.01",
    "A11.10.03.03",
    "A11.11.01.03",
    "A11.11.03.01",
    "A11.20.01.01",
    "A11.20.01.03",
    "A11.20.03.01",
    "A11.20.03.03",
    "A11.21.01.01",
    "A11.21.01.03",
    "A11.21.03.01",
    "A11.21.03.03",
    "A11.40.01.01",
    "A11.40.01.03",
    "A11.40.03.01",
    "A11.40.03.03",
    "A20.01.01.00",
    "A20.01.03.00",
    "A20.10.01.01",
    "A20.10.01.03",
    "A20.10.03.01",
    "A20.10.03.03",
    "A20.11.01.01",
    "A20.11.01.03",
    "A20.11.03.01",
    "A20.11.03.03",
    "A20.20.01.01",
    "A20.20.01.03",
    "A20.20.03.01",
    "A20.20.03.03",
    "A20.21.01.01",
    "A20.21.01.03",
    "A20.21.03.01",
    "A20.21.03.03",
    "A20.40.01.01",
    "A20.40.01.03",
    "A20.40.03.01",
    "A20.40.03.03",
    "A40.01.01.00",
    "A40.01.03.00",
    "A40.10.01.01",
    "A40.10.01.03",
    "A40.10.03.01",
    "A40.10.03.03",
    "A40.11.01.01",
    "A40.11.01.03",
    "A40.11.03.01",
    "A40.11.03.03",
    "A40.20.01.01",
    "A40.20.01.03",
    "A40.20.03.01",
    "A40.20.03.03",
    "A40.21.01.01",
    "A40.21.01.03",
    "A40.21.03.01",
    "A40.21.03.03",
    "A40.40.01.01",
    "A40.40.01.03",
    "A40.40.03.01",
    "A40.40.03.03",
    "A55.01.01.00",
    "A55.01.03.00",
    "A55.10.01.01",
    "A55.10.01.03",
    "A55.10.03.01",
    "A55.10.03.03",
    "A55.11.01.01",
    "A55.11.01.03",
    "A55.11.03.01",
    "A55.11.03.03",
    "A55.20.01.01",
    "A55.20.01.03",
    "A55.20.03.01",
    "A55.20.03.03",
    "A55.21.01.01",
    "A55.21.01.03",
    "A55.21.03.01",
    "A55.21.03.03",
    "A55.40.01.01",
    "A55.40.01.03",
    "A55.40.03.01",
    "A55.40.03.03",
    "A60.01.00.00",
    "A60.10.00.01",
    "A60.10.00.03",
    "A60.11.00.01",
    "A60.11.00.03",
    "A60.20.00.01",
    "A60.20.00.03",
    "A60.21.00.01",
    "A60.21.00.03",
    "A60.40.00.01",
    "A60.40.00.03",
)


def parse(code: str) -> tuple[str, str, str, str]:
    m = CODE_RE.match(code.strip().upper())
    if not m:
        raise ValueError(
            translate("Not a class A ECMA code: {code} (expected format A20.20.03.01)", code=code)
        )
    bottom, top, bpos, tpos = m.groups()
    for c in (bottom, top):
        if c not in CLOSURES:
            raise ValueError(
                translate(
                    "{code}: closure {closure} has no corresponding component. Supported closures: {supported}.",
                    code=code,
                    closure=c,
                    supported=sorted(CLOSURES),
                )
            )
    for pos in (bpos, tpos):
        if pos not in PANEL_WALL:
            raise ValueError(
                translate(
                    "{code}: wall position {position} must be 00, 01, or 03",
                    code=code,
                    position=pos,
                )
            )
    return bottom, top, bpos, tpos


def recipe(code: str, L: float, W: float, H: float) -> dict[str, Any]:
    """ECMA 编号 + 内尺寸 → 结构配方（长方彩盒筒身）。"""
    bottom, top, bpos, tpos = parse(code)

    def end(closure: str, pos: str) -> dict[str, Any]:
        out = dict(CLOSURES[closure])
        wall = PANEL_WALL[pos]
        if wall is not None:
            out["on"] = wall
        return out

    return {"body": {"base": "rect", "L": L, "W": W, "H": H},
            "top": end(top, tpos), "bottom": end(bottom, bpos)}  # fmt: skip


def code_of(top: dict[str, Any], bottom: dict[str, Any]) -> str | None:
    """拼装后的两端（kind / flap / on 已定：back / front）→ ECMA 编号；没有对应的返回 None。"""

    def closure(e: dict[str, Any]) -> str | None:
        for num, spec in CLOSURES.items():
            if all(e.get(k) == v for k, v in spec.items()):
                return num
        return None

    def pos(e: dict[str, Any], num: str) -> str:
        if num in ("01", "60"):
            return "00"
        return "01" if e.get("on") == "back" else "03"

    b, t = closure(bottom), closure(top)
    if b is None or t is None:
        return None
    return f"A{b}.{t}.{pos(bottom, b)}.{pos(top, t)}"


__all__ = ["CLOSURES", "PUBLIC_A", "code_of", "parse", "recipe"]
