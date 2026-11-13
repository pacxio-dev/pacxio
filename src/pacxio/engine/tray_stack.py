"""堆叠凸舌和底板孔（FEFCO 0435–0436.2、0484 等水果箱类托盘，CFQ；按示意图推读，数值【初值】）。

端墙顶上留一块凸舌：它是端墙往上的延长，不跟着窄边 / 卷边折下去，装好后竖着冒出墙顶；
底板在端墙压线上对应位置开孔，上一层托盘叠上来时下一层的凸舌插进这个孔里定位。
凸舌旁边的窄边、卷边、内墙让出缺口（挂面工具 cut_tab）。刀版坐标：build() 的端墙 side_a 在 x > Lb、
ear_tray 的端墙外墙在 x ∈ [bl, bl + ho]。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pacxio.engine.tray_kit import Kit, rect
from pacxio.messages import t as translate
from pacxio.model.common import Issue

if TYPE_CHECKING:
    from pacxio.engine.tray import TrayParams

# 【初值】
STACK_W_RATIO, STACK_W_MIN, STACK_W_MAX = 0.2, 20.0, 45.0  # 凸舌宽 clamp(0.2·端墙长, 20, 45)
STACK_H_T, STACK_H_MIN = 3.0, 8.0  # 凸舌高出墙顶 max(3t, 8)
STACK_CLEAR_MM = 1.5  # 缺口比凸舌每边大
STACK_SLOT_EXTRA_MM = 2.0  # 底板孔比凸舌每边宽
STACK_SLOT_T, STACK_SLOT_EXTRA = 2.0, 1.0  # 底板孔深 2t + 1（下一层凸舌是单层纸）
STACK_PAIR_AT = (0.3, 0.7)
STACK_CORNER_T, STACK_CORNER_EXTRA = 3.0, 5.0  # 墙角凸舌离墙头 3t + 5


def recess_depth(t: float) -> float:
    """ear 内墙下沿给下一层凸舌让的缺口深：凸舌穿过底板后还高出 tab_h − t，内墙下沿离底 t，再留 2 mm。"""
    return max(tab_h(t) - t, 5.0) + 2.0


def tab_w(length: float) -> float:
    return min(STACK_W_MAX, max(STACK_W_MIN, STACK_W_RATIO * length))


def tab_h(t: float) -> float:
    return max(STACK_H_T * t, STACK_H_MIN)


def positions(kind: str, length: float, t: float) -> list[float]:
    w = tab_w(length)
    if kind == "center":
        return [0.5 * length]
    if kind == "pair":
        return [f * length for f in STACK_PAIR_AT]
    edge = w / 2 + STACK_CORNER_T * t + STACK_CORNER_EXTRA
    return [edge, length - edge]


def validate(p: TrayParams, length: float, t: float) -> list[Issue]:
    w, ys = tab_w(length), positions(p.stack, length, t)
    if p.stack == "pair" and ys[1] - ys[0] < w + 10:
        return [
            Issue(
                id="S03",
                message=translate(
                    "The end wall is too short for the two stacking tabs without overlap"
                ),
                fix=translate("Increase W or use stack=center"),
            )
        ]
    if min(ys) - w / 2 < 2 * t + 2:
        return [
            Issue(
                id="S03",
                message=translate("The end wall is too short to fit the stacking tabs"),
                fix=translate("Make W larger"),
            )
        ]
    return []


Wall = tuple[
    str, str, float, float, float, float, float
]  # 面、方向、墙顶线、压线、往外的符号、起点、长


def add_stack_tabs(p: TrayParams, kit: Kit, walls: list[Wall], t: float,
                   recess: dict[str, tuple[float, float]] | None = None) -> None:  # fmt: skip
    """walls：每面墙（面 id，"x" = 墙顶是刀版上的竖线 / "y" = 横线，墙顶线坐标，和底板的压线坐标，
    从压线往墙顶的符号，墙沿压线从哪起、多长）。recess：ear 托盘内墙下沿（坐标, 深）——中间凸舌落在两插脚
    之间时，内墙下沿让出一个缺口给下一层的凸舌。"""
    clr = STACK_CLEAR_MM
    h = tab_h(t)
    sd = STACK_SLOT_T * t + STACK_SLOT_EXTRA
    for wall, axis, top, base, sx, y0, length in walls:
        w = tab_w(length)
        sw = w / 2 + STACK_SLOT_EXTRA_MM
        for i, yc in enumerate(positions(p.stack, length, t), 1):
            y = y0 + yc

            def box(
                a0: float, a1: float, b0: float, b1: float, ax: str = axis
            ) -> list[tuple[float, float]]:
                return rect(a0, b0, a1, b1) if ax == "x" else rect(b0, a0, b1, a1)

            kit.cut_tab(wall, box(top, top + sx * h, y - w / 2, y + w / 2),
                        box(top, top + sx * (h + clr), y - w / 2 - clr, y + w / 2 + clr))  # fmt: skip
            kit.open_slot(
                f"cut_slot_stack_{wall}_{i}", box(base - sx * sd, base, y - sw, y + sw), "base"
            )
            if recess and wall in recess:
                xr, depth = recess[wall]
                kit.clip(
                    f"{wall}_in",
                    box(xr - sx * depth, xr + sx * 1.0, y - w / 2 - clr, y + w / 2 + clr),
                )


def stack_walls(p: TrayParams, Lb: float, Wb: float, h_end: float, h_long: float) -> list[Wall]:
    """build() 托盘的端墙 / 长边墙（刀版坐标同 tray.build）。"""
    if p.stack_on == "long":
        return [
            ("front", "y", -h_long, 0.0, -1.0, 0.0, Lb),
            ("back", "y", Wb + h_long, Wb, 1.0, 0.0, Lb),
        ]
    return [
        ("side_a", "x", Lb + h_end, Lb, 1.0, 0.0, Wb),
        ("side_b", "x", -h_end, 0.0, -1.0, 0.0, Wb),
    ]
