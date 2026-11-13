"""Hanging cartons: upright rear header and a front-hinged tuck lid."""

from typing import Any, ClassVar, Literal

from pydantic import Field

from pacxio.geometry import Affine, Path, Segment, Vec
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import PanelRole
from pacxio.model.profile import Material, Profile, load_profile
from pacxio.styles.components.base import Piece
from pacxio.styles.components.carton import RteDims, _crease
from pacxio.styles.components.holes import Hanger, HoleParams, hanging_hole
from pacxio.styles.components.tube4 import Tube4, TubeBody
from pacxio.styles.components.tuck import TuckTop
from pacxio.styles.lockbottom import (
    AutolockParams,
    AutolockStyle,
    SnaplockParams,
    SnaplockStyle,
)
from pacxio.styles.rte import RteParams, RteStyle, resolve
from pacxio.styles.ste import SteStyle


class HangerParams(HoleParams, RteParams):
    hang_hole: Literal["none", "round", "euro"] = "euro"
    hanger_h: float = Field(default=35, ge=20, le=100)
    hanger_radius: float = Field(default=4, ge=0)


class SnapHangerParams(HangerParams, SnaplockParams):
    pass


class AutoHangerParams(HangerParams, AutolockParams):
    pass


def hanger_piece(p: HoleParams, h: float, radius: float, x0: float, x1: float, y: float,
                 wall: str, layers: int = 1) -> Piece:  # fmt: skip
    """挂板（顶端画法：从 y 往 +y 直立伸出），彩盒和开槽箱共用（2026-11-07 合并，GEOMETRY_ENGINE.md §20）。

    layers = 1：单层，挂孔离远边 hole_top（原彩盒挂钩盒）；
    layers = 2：双层，第一层高 h、对折线、第二层高 h（远端圆角）对折贴回来，挂孔离对折线 hole_top、
    第二层按对折线对称（原开槽箱 0240 挂板）。"""
    if layers == 1:
        contour = Hanger(h, radius).contour(x0, x1, y)
        hole = hanging_hole(p, contour, "hanger")
        return Piece(
            outline=contour.segments[:-1],
            panels={"hanger": (PanelRole.BODY, contour)},
            creases=[_crease(f"crease_{wall}_hanger", (x0, y), (x1, y), [wall, "hanger"])],
            inner_edges=[hole] if hole else [],
        )
    up = Hanger(2 * h, radius).contour(x0, x1, y)
    first = Path.rect(x0, y, x1 - x0, h)
    upper = [q for q in up.segments[:-1] if min(q.start.y, q.end.y) >= y + h - 1e-9]
    upper = [
        Segment(Vec(x1, y + h), upper[0].start),
        *upper,
        Segment(upper[-1].end, Vec(x0, y + h)),
    ]
    second = Path([*upper, Segment(Vec(x0, y + h), Vec(x1, y + h))], closed=True)
    holes = []
    hole = hanging_hole(p, first, "hanger")
    if hole is not None:
        back = Affine(1, 0, 0, -1, 0, 2 * (y + h))
        holes = [hole, hole.model_copy(update={"id": "cut_hang_hanger_back", "panels": ["hanger_back"],
                                               "segments": [back.apply_segment(q) for q in hole.segments]})]  # fmt: skip
    return Piece(
        outline=up.segments[:-1],
        panels={"hanger": (PanelRole.BODY, first), "hanger_back": (PanelRole.BODY, second)},
        creases=[
            _crease(f"crease_{wall}_hanger", (x0, y), (x1, y), [wall, "hanger"]),
            _crease("crease_hanger_back", (x0, y + h), (x1, y + h), ["hanger", "hanger_back"]),
        ],  # fmt: skip
        inner_edges=holes,
    )


def _hanger_piece(
    p: HangerParams, d: RteDims, body: TubeBody, wall: str = "back", layers: int = 1
) -> Piece:
    """挂板：wall 上沿直立伸出（现有挂钩盒挂 back；几何引擎的另一种面号挂 front）。"""
    port = body.ports[f"{wall}.top"]
    x0, x1 = sorted((port.start.x, port.end.x))
    return hanger_piece(p, p.hanger_h, p.hanger_radius, x0, x1, d.hb, wall, layers)


class HangerStyle(RteStyle):
    id: ClassVar[str] = "rte_hanger"
    name_zh: ClassVar[str] = "挂钩反插盒"
    refs: ClassVar[dict[str, str]] = {"design": "背面直立挂板 + 正面插舌顶盖；ECMA 待核对"}
    version: ClassVar[str] = "0.2.0-c1"  # 0.2.0：插舌压线两端改成切口（2026-11-07）
    bottom_lid_on: ClassVar[str] = "back"  # 顶盖改挂 front 后，反插底盖须在对面。

    def param_schema(self) -> type[HangerParams]:
        return HangerParams

    def _top(self, p: RteParams, d: RteDims, body: TubeBody) -> Piece:
        assert isinstance(p, HangerParams)
        return TuckTop(d, p.friction_lock, lid_on="front", slit=p.tuck_slit).build(
            body, {"back": _hanger_piece(p, d, body)}
        )

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        errors = super().validate(params, material, profile)
        if errors:
            return errors
        try:
            d = resolve(params, material, profile or load_profile("default"))
            _hanger_piece(params, d, Tube4().build(d))
        except ValueError as e:
            errors.append(
                Issue(
                    id="S03",
                    message=str(e),
                    fix=t(
                        "Increase the hanger panel size, or reduce the radius or hole width and adjust hole_top"
                    ),
                )
            )
        return errors

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = resolve(params, material, profile)
        return {
            **super().expected_panel_sizes(params, material, profile),
            "hanger": (d.wb, params.hanger_h),
        }


class SteHangerStyle(HangerStyle, SteStyle):
    id: ClassVar[str] = "ste_hanger"
    name_zh: ClassVar[str] = "挂钩同插盒"
    bottom_lid_on: ClassVar[str] = "front"  # 同向插舌：上下盖仍挂同一面。


class SnaplockHangerStyle(HangerStyle, SnaplockStyle):
    id: ClassVar[str] = "snaplock_hanger"
    name_zh: ClassVar[str] = "挂钩扣底盒"

    def param_schema(self) -> type[SnapHangerParams]:
        return SnapHangerParams


class AutolockHangerStyle(HangerStyle, AutolockStyle):
    id: ClassVar[str] = "autolock_hanger"
    name_zh: ClassVar[str] = "挂钩自动锁底盒"

    def param_schema(self) -> type[AutoHangerParams]:
        return AutoHangerParams
