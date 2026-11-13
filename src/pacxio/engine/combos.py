"""常用组合（2026-11-06 用户要求）：起了名字的积木组合，不是目录条目。

目录（FEFCO / ECMA）之外、用户拿实物或描述要过、我们补了积木做出来的盒子，存成一个常用组合：配方（不含尺寸）、
说明、关键词、默认材料和参考尺寸、来源。它和目录款一起进结构目录（`catalog.entries()`，系列“C 常用组合”），
所以 `structures list / suggest / show / preview / check`、`job init --combo`、MCP 都能按编号或关键词找到；
用户描述里的词（如“撕开”“展示盖”“头卡”“拉手”）命中关键词就能被推荐。成熟度一律“自定义”（没有目录图，
按实物 / 描述推的比例），推荐和交付时如实说、建议先打样。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Combo:
    code: str
    name: str
    description: str
    recipe: dict[str, Any]  # 不含 L / W / H
    material: str  # folding_carton / corrugated
    ref_size: tuple[float, float, float]
    source: str
    keywords: tuple[str, ...] = field(default_factory=tuple)


COMBOS: dict[str, Combo] = {
    c.code: c
    for c in (
        Combo(
            code="tear_display_tray",
            name="撕开式展示盖托盒",
            description="卡纸托盒：顶板挂一面墙、中间齿线撕开窗，两侧翼贴侧墙外面、锁舌插底板 / 侧墙折线上的槽"
            "（侧面不糊胶）；对面墙上再挂一层展示盖压在外面，窗口边一条胶条粘住它——第一次打开时撕开窗跟着撕下、"
            "展示盖立起来当头卡；展示盖远边两角圆角 + 中间半圆易开拉手（拉手收在展示盖里，连拉手一共 = 盒宽，不伸出盒外）；"
            "撕开窗四角圆角；自由角倒圆角",
            recipe={
                "body": {"form": "tray", "corners": "flap", "corner_on": "long"},
                "top": {
                    "kind": "hinged_lid",
                    "lid_front": "none",
                    "dust_lock": True,
                    "tear_window": True,
                    "display_lid": "tab",
                },
                "bottom": {"kind": "open"},
                "corner_radius": 3,
            },  # fmt: skip
            material="folding_carton",
            ref_size=(150, 150, 40),
            source="按用户实物盒推（2026-11-06，食品小礼盒）",
            keywords=(
                "展示",
                "开窗",
                "彩盒",
                "撕开",
                "撕开窗",
                "展示盖",
                "头卡",
                "拉手",
                "易开",
                "免胶",
                "扣",
                "托盒",
                "礼盒",
                "食品",
                "零食",
                "翻盖",
            ),  # fmt: skip
        ),
    )
}


def get(code: str) -> Combo | None:
    return COMBOS.get(code.strip())


def recipe(code: str, L: float, W: float, H: float) -> dict[str, Any]:
    """组合 + 内尺寸 → 结构配方（尺寸写进 body）。"""
    c = COMBOS[code.strip()]
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in c.recipe.items()}
    out["body"] = {**out["body"], "L": L, "W": W, "H": H}
    return out


__all__ = ["COMBOS", "Combo", "get", "recipe"]
