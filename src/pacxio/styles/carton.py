"""carton 组合彩盒：四面筒 × 顶 × 底，任意搭配（ 节，2026-11-04 用户要求先做组合）。

顶：tuck（插舌顶盖挂 back）、hanger（插舌顶盖挂 front + back 上的挂板）、seal（四翼搭粘）、gable（山形手提顶）。
底：tuck（插舌底盖，挂在和顶盖相对的一面——反插）、tuck_same（挂在同一面——同向插）、snaplock（扣底）、
autolock（自动锁底）、seal（四翼搭粘）。没有盖的顶（seal / gable）配插舌底时，tuck 挂 front、tuck_same 挂 back。

每一端都直接用已有盒型的组件（TuckTop / TuckEnd / SnapBottom / CrashBottom / GlueFlapEnd / GableTop），
盒身、糊口、补偿和 RTE 系列一样。已有的组合和原盒型几何一致（测试逐面、逐线比对：rte、ste、snaplock、
autolock、四种挂钩盒；gable 只有糊口下端不同——组合里锁底一律用圆弧糊口端，和 snaplock 一样），
新组合由 S06 折叠检查把关。组合本身不提升任何一端的验证等级：整盒一律 L0。
"""

from __future__ import annotations

import copy
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, Profile
from pacxio.styles.components.base import Piece
from pacxio.styles.components.carton import GLUE_BEVEL_MM, BottomPart, RteDims
from pacxio.styles.components.glue_end import GlueFlapEnd
from pacxio.styles.components.lockbottom import (
    AutolockParams,
    CrashBottom,
    SnapBottom,
    SnaplockParams,
)
from pacxio.styles.components.tube4 import BodyX, Tube4, TubeBody
from pacxio.styles.components.tuck import TuckTop
from pacxio.styles.gable import HOLE_H_MM, ROOF_DEG, GableTop
from pacxio.styles.hanger import HangerParams, _hanger_piece
from pacxio.styles.lockbottom import AutolockStyle, SnaplockStyle
from pacxio.styles.mailer_closures import _perf
from pacxio.styles.rte import RteStyle, TuckEnd, resolve

Top = Literal["tuck", "hanger", "seal", "gable"]
Bottom = Literal["tuck", "tuck_same", "snaplock", "autolock", "seal"]
TOPS: tuple[str, ...] = ("tuck", "hanger", "seal", "gable")
BOTTOMS: tuple[str, ...] = ("tuck", "tuck_same", "snaplock", "autolock", "seal")
TOP_ZH = {"tuck": "插舌顶", "hanger": "挂钩插舌顶", "seal": "粘合封口顶", "gable": "山形手提顶"}
BOTTOM_ZH = {
    "tuck": "反插底",
    "tuck_same": "同向插底",
    "snaplock": "扣底",
    "autolock": "自动锁底",
    "seal": "粘合封底",
}
TOP_EN = {"tuck": "tuck top", "hanger": "hanger with tuck top", "seal": "sealed top", "gable": "gable top with handle"}
BOTTOM_EN = {"tuck": "reverse tuck bottom", "tuck_same": "straight tuck bottom", "snaplock": "snap-lock bottom", "autolock": "auto-lock bottom", "seal": "sealed bottom"}
# 和已注册盒型重合的组合（结构库里不重复列，测试按它们逐面比对）
EQUIVALENT: dict[tuple[str, str], str] = {
    ("tuck", "tuck"): "rte",
    ("tuck", "tuck_same"): "ste",
    ("tuck", "snaplock"): "snaplock",
    ("tuck", "autolock"): "autolock",
    ("hanger", "tuck"): "rte_hanger",
    ("hanger", "tuck_same"): "ste_hanger",
    ("hanger", "snaplock"): "snaplock_hanger",
    ("hanger", "autolock"): "autolock_hanger",
    ("gable", "snaplock"): "gable",
    ("seal", "seal"): "seal_end",
}
LOCK_BOTTOMS = {"snaplock", "autolock"}
# 附件【初值】
NOTCH_MARGIN_MM = 10.0  # 手指缺口离面两边至少这么多
TEAR_W_MM, PERF_CUT_MM, PERF_BRIDGE_MM = 8.0, 3.0, 2.0  # 同自封撕拉飞机盒
TEAR_MARGIN_MM = 5.0  # 撕拉条离盒身上下压线（含防尘翼让位 / 糊口端头）至少这么多
TEAR_TAB_OFFSET_MM = 12.0  # 起撕口离正面左边


class CartonParams(HangerParams):
    """组合彩盒参数：RTE 的盒身 / 插舌参数 + 挂板 + 扣底 / 自动锁底 + 粘合封口 + 山形顶。用不到的项不生效。"""

    top: Top = "tuck"
    bottom: Bottom = "tuck"
    hang_hole: Literal["none", "round", "euro"] = "euro"
    # 扣底 / 自动锁底
    major_depth: float | None = None
    notch_w: float | None = None
    notch_d: float | None = None
    minor_depth: float | None = None
    hook: float | None = None
    tongue_w: float | None = None
    tongue_len: float | None = None
    tongue_r: float | None = None
    lock_step: float | None = None
    major_taper_deg: float | None = None
    diag_crease: Literal["crease", "reverse_crease"] = "crease"
    # 粘合封口（同 seal_end）
    end_overlap: float = Field(default=10, ge=2)
    end_relief: float = Field(default=1, gt=0)
    glue_band: float = Field(default=5, gt=0)
    # 山形顶（同 gable；提手孔另起名，免得和挂孔撞）
    roof_deg: float = ROOF_DEG
    lean_deg: float | None = None  # 不给：按手提板 V 口的位置算（styles/gable.py）
    handle_h: float | None = None
    handle_hole_w: float | None = None
    handle_hole_h: float = HOLE_H_MM
    # 附件
    thumb_notch: bool = Field(default=False, description="Thumb notch: a half-round notch in the top edge of the front when the tuck is at the top")
    thumb_w: float = Field(default=25.0, gt=0, description="Thumb notch width (half-circle diameter)")
    tear_strip: bool = Field(default=False, description="Tear strip: two perforation lines around the body plus a pull tab")
    tear_y: float | None = Field(default=None, description="Tear strip centre line above the lower body crease; default is half the body height")
    tear_w: float = Field(default=TEAR_W_MM, gt=0, description="Tear strip width (distance between the two perforation lines)")
    perf_cut: float = Field(default=PERF_CUT_MM, gt=0, description="Length of each perforation cut")
    perf_bridge: float = Field(
        default=PERF_BRIDGE_MM, gt=0, description="Perforation paper bridge (bridges are also left on both sides of creases)"
    )


def _proxy(model: type[BaseModel], p: CartonParams, **extra: Any) -> Any:
    names = set(model.model_fields) & set(CartonParams.model_fields)
    values = {k: v for k, v in p.model_dump(include=names).items() if v is not None}
    return model(**values | extra)


def snap_params(p: CartonParams) -> SnaplockParams:
    return _proxy(SnaplockParams, p, major_taper_deg=15.0)  # type: ignore[no-any-return]


def auto_params(p: CartonParams) -> AutolockParams:
    return _proxy(AutolockParams, p)  # type: ignore[no-any-return]


def lid_side(p: CartonParams) -> str:
    """插舌底挂哪一面：顶盖挂 back（tuck）时反插挂 front；顶盖挂 front（hanger）时反插挂 back。"""
    top_lid = "front" if p.top == "hanger" else "back"
    if p.bottom == "tuck_same":
        return top_lid if p.top in ("tuck", "hanger") else "back"
    return {"front": "back", "back": "front"}[top_lid] if p.top in ("tuck", "hanger") else "front"


def _seal(p: CartonParams, d: RteDims) -> GlueFlapEnd:
    return GlueFlapEnd(d, p.end_overlap, p.end_relief, p.glue_band)


def _gable(p: CartonParams, d: RteDims) -> GableTop:
    return GableTop(d, p.roof_deg, p.lean_deg, p.handle_h, p.handle_hole_w, p.handle_hole_h)


def tear_centre(p: CartonParams, d: RteDims) -> float:
    return p.tear_y if p.tear_y is not None else d.hb / 2


def _thumb_notch(piece: Piece, p: CartonParams, d: RteDims, xs: BodyX) -> None:
    """插舌顶：正面上沿（插舌插进去的那条空边）中间挖一个半圆缺口，正面的面一起改。"""
    hb, r = d.hb, p.thumb_w / 2
    cx = (xs.xa1 + xs.xf1) / 2
    notch = [
        Segment(Vec(xs.xf1, hb), Vec(cx + r, hb)),
        Segment(Vec(cx + r, hb), Vec(cx - r, hb), -1.0),  # 顺时针半圆，往面里凹
        Segment(Vec(cx - r, hb), Vec(xs.xa1, hb)),
    ]
    for i, s in enumerate(piece.outline):
        if s.start.is_close(Vec(xs.xf1, hb)) and s.end.is_close(Vec(xs.xa1, hb)):
            piece.outline[i : i + 1] = notch
            break
    else:
        raise ValueError(t("Could not find the upper edge of the front panel for the tuck lid"))
    piece.panels["front"] = (
        PanelRole.BODY,
        Path(
            [
                Segment(Vec(xs.xa1, 0), Vec(xs.xf1, 0)),
                Segment(Vec(xs.xf1, 0), Vec(xs.xf1, hb)),
                *notch,
                Segment(Vec(xs.xa1, hb), Vec(xs.xa1, 0)),
            ],
            closed=True,
        ),  # fmt: skip
    )


def _tear_strip(p: CartonParams, d: RteDims, xs: BodyX) -> list[Edge]:
    """撕拉条：糊口和四个面上各两条齿线（显式切段 + 纸桥，压线两边都留桥），正面左边一个半圆起撕口。"""
    y, half = tear_centre(p, d), p.tear_w / 2
    tab_x = xs.xa1 + TEAR_TAB_OFFSET_MM
    spans = [("glue", 0.0, xs.xb0), ("back", xs.xb0, xs.xb1), ("side_a", xs.xb1, xs.xa1),
             ("front", tab_x, xs.xf1), ("side_b", xs.xf1, xs.xs1)]  # fmt: skip
    edges = []
    for pid, x0, x1 in spans:
        for name, yy in (("upper", y + half), ("lower", y - half)):
            edges.append(
                _perf(f"perf_tear_{name}_{pid}", x0, x1, yy, p.perf_cut, p.perf_bridge, [pid])
            )
    tab = Segment(Vec(tab_x, y + half), Vec(tab_x, y - half), 1.0)  # 逆时针半圆，往左凸
    edges.append(Edge(id="cut_tear_tab", type=EdgeType.CUT, segments=[tab], panels=["front"]))
    return edges


class CartonStyle(RteStyle):
    """组合彩盒：同一套盒身，顶和底各选一种已有机构。"""

    id: ClassVar[str] = "carton"
    name_zh: ClassVar[str] = "组合彩盒"
    refs: ClassVar[dict[str, str]] = {"design": "顶 / 底取自已有盒型的组件；组合整盒未单独校准"}
    version: ClassVar[str] = "0.2.0"  # 0.2.0：插舌压线两端改成切口（2026-11-07）
    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b",
        "top_lid", "top_tuck", "dust_ta", "dust_tb", "hanger",
        "top_back", "top_side_a", "top_front", "top_side_b",
        "roof_back", "handle_back", "end_a", "roof_front", "handle_front", "end_b",
        "btm_lid", "btm_tuck", "dust_ba", "dust_bb",
        "btm_back", "glue_back", "btm_sa", "btm_front", "glue_front", "btm_sb",
        "btm_side_a", "btm_side_b",
    )  # fmt: skip
    crease_order: ClassVar[tuple[str, ...]] = ()

    def param_schema(self) -> type[BaseModel]:
        return CartonParams

    # ---- 每一端 ----

    def _top(self, p: Any, d: RteDims, body: TubeBody) -> Piece:
        if p.top == "hanger":
            return TuckTop(d, p.friction_lock, lid_on="front", slit=p.tuck_slit).build(
                body, {"back": _hanger_piece(p, d, body)}
            )
        if p.top == "seal":
            return _seal(p, d).build(body, True)
        if p.top == "gable":
            return _gable(p, d).build(body)
        piece = TuckTop(d, p.friction_lock, slit=p.tuck_slit).build(body)
        if p.thumb_notch:
            _thumb_notch(piece, p, d, body.xs)
        return piece

    def _bottom(self, p: Any, d: RteDims, xs: BodyX) -> BottomPart:
        if p.bottom == "snaplock":
            part = SnapBottom().build(snap_params(p), d, xs)
        elif p.bottom == "autolock":
            part = CrashBottom().build(auto_params(p), d, xs)
        elif p.bottom == "seal":
            piece = _seal(p, d).build(Tube4().build(d), False)
            part = BottomPart(
                outline=piece.outline,
                polys=piece.panels,
                creases=piece.creases,
                extra_edges=piece.inner_edges,
                glue_areas=piece.glue_areas,
            )
        else:
            part = TuckEnd(lid_side(p)).build(p, d, xs)
        if p.tear_strip:
            part.extra_edges += _tear_strip(p, d, xs)
        if p.top == "seal":  # RTE 的拼装只收底部的糊胶区：顶部粘合封口的胶带区一起带上
            part.glue_areas += _seal(p, d).build(Tube4().build(d), True).glue_areas
        return part

    def _bottom_meta(self, d: RteDims) -> dict[str, Any]:
        return {}

    def _bottom_sizes(self, p: Any, d: RteDims) -> dict[str, tuple[float, float]]:
        if p.bottom == "snaplock":
            return SnaplockStyle()._bottom_sizes(snap_params(p), d)
        if p.bottom == "autolock":
            return AutolockStyle()._bottom_sizes(auto_params(p), d)
        if p.bottom == "seal":
            return {}
        return {"btm_lid": (d.wb, d.ld)}

    def _validate_bottom(self, p: Any, d: RteDims) -> list[Issue]:
        issues: list[Issue] = []
        if p.bottom == "snaplock":
            issues += SnaplockStyle()._validate_bottom(snap_params(p), d)
        elif p.bottom == "autolock":
            issues += AutolockStyle()._validate_bottom(auto_params(p), d)
        for end in ("top", "bottom"):
            if getattr(p, end) == "seal":
                overlap_ok = 2 <= p.end_overlap <= d.Wi - 2
                relief_ok = p.end_relief < min(d.wb, d.ws) / 2
                band_ok = p.glue_band + p.end_relief <= d.Wi + p.end_overlap - d.ws
                if not (overlap_ok and relief_ok and band_ok):
                    issues.append(Issue(id="S03", message=t("The glued closure cannot fit end_overlap, end_relief, and glue_band"),
                                        fix=t("Set end_overlap between 2 and inside width minus 2, then reduce end_relief or glue_band")))  # fmt: skip
        if p.top == "gable":
            for msg, fix in _gable(p, d).problems():
                issues.append(Issue(id="S03", message=msg, fix=fix))
        if p.thumb_notch:
            if p.top != "tuck":
                issues.append(Issue(id="S03", message=t("The thumb notch requires a tuck lid with a free upper edge on the front panel"),
                                    fix=t("Set top=tuck or disable thumb_notch")))  # fmt: skip
            elif p.thumb_w + 2 * NOTCH_MARGIN_MM > d.wb or p.thumb_w / 2 > d.hb / 3:
                issues.append(Issue(id="S03", message=t("Thumb notch {width:g} does not fit the front panel", width=p.thumb_w),
                                    fix=t("Reduce thumb_w")))  # fmt: skip
        if p.tear_strip:
            y, half = tear_centre(p, d), p.tear_w / 2
            lo = max(self._for(p)._glue_bottom_h(d), d.off) + TEAR_MARGIN_MM
            hi = d.hb - max(GLUE_BEVEL_MM, d.off) - TEAR_MARGIN_MM
            if y - half < lo or y + half > hi:
                issues.append(Issue(id="S03", message=t("The tear strip (centre {centre:.1f}, width {width:g}) is outside the usable body height {minimum:.1f}–{maximum:.1f}", centre=y, width=p.tear_w, minimum=lo, maximum=hi),
                                    fix=t("Adjust tear_y or tear_w")))  # fmt: skip
            if p.perf_cut + 2 * p.perf_bridge > min(d.gw, d.ws):
                issues.append(Issue(id="S03", message=t("A perforation cut or bridge is too long to fit on the glue flap or side panel"),
                                    fix=t("Reduce perf_cut or perf_bridge")))  # fmt: skip
        if p.top == "hanger":
            try:
                _hanger_piece(p, d, Tube4().build(d))
            except ValueError as e:
                issues.append(
                    Issue(
                        id="S03",
                        message=str(e),
                        fix=t("Increase the hanger panel size or reduce the radius or hole width"),
                    )
                )
        return issues

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p = self._resolved(params)
        return RteStyle.validate(self._for(p), p, material, profile)

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        params = self._resolved(params)
        sizes = RteStyle.expected_panel_sizes(self._for(params), params, material, profile)
        d = resolve(params, material, profile)
        if params.top != "tuck":
            for pid in ("top_lid", "top_tuck", "dust_ta", "dust_tb"):
                sizes.pop(pid, None)
        if params.top == "gable":
            sizes |= _gable(params, d).sizes()
        if params.bottom == "seal":
            for pid in ("btm_lid", "btm_tuck", "dust_ba", "dust_bb"):
                sizes.pop(pid, None)
        return sizes

    # ---- 拼装 ----

    @staticmethod
    def _resolved(p: CartonParams) -> CartonParams:
        """两头都是粘合封口时没有防尘翼，侧面不缩（dust_offset 0，同 seal_end）。"""
        if p.top == "seal" and p.bottom == "seal" and p.dust_offset is None:
            return p.model_copy(update={"dust_offset": 0.0})
        return p

    def _for(self, p: CartonParams) -> CartonStyle:
        """这一次组合用的实例：糊口下端（锁底用圆弧，同 snaplock / autolock；其余斜切）。"""
        impl = copy.copy(self)
        impl.glue_bottom = "arc" if p.bottom in LOCK_BOTTOMS else "bevel"  # type: ignore[misc]
        return impl

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: CartonParams = self._resolved(params)
        dl = RteStyle.generate(self._for(p), p, material, profile)
        d = resolve(p, material, profile)
        meta = dl.style_meta
        meta["name_zh"] = f"{self.name_zh}（{TOP_ZH[p.top]} + {BOTTOM_ZH[p.bottom]}）"
        meta["name_en"] = f"Modular carton ({TOP_EN[p.top]} + {BOTTOM_EN[p.bottom]})"
        meta["accuracy_level"] = "L0"
        meta["carton"] = {
            "top": p.top,
            "bottom": p.bottom,
            "same_as": EQUIVALENT.get((p.top, p.bottom)),
        }
        extras = [
            k for k, on in (("thumb_notch", p.thumb_notch), ("tear_strip", p.tear_strip)) if on
        ]
        if extras:
            meta["carton"] |= {"attachments": extras, "same_as": None}
        if p.bottom == "snaplock":
            meta |= SnaplockStyle()._bottom_meta(d)
        elif p.bottom == "autolock":
            meta |= AutolockStyle()._bottom_meta(d)
        elif p.bottom == "seal":
            meta["bottom"] = {
                "type": "sealed",
                "halves": ["btm_front", "btm_back"],
                "overlap": True,
            }
        if p.top == "seal":
            meta["top"] = {"halves": ["top_front", "top_back"], "overlap": True}
        elif p.top == "gable":
            gable = _gable(p, d)
            meta |= gable.meta()
            meta["dims"] |= {"roof": gable.roof, "handle_h": gable.handle}
        if "seal" in (p.top, p.bottom):
            meta["dims"]["flap"] = (d.Wi + p.end_overlap) / 2
        elif p.top == "gable":
            meta["dims"]["flap"] = meta["dims"]["roof"]
        return dl


def catalog_combos() -> list[tuple[str, str]]:
    """结构库里列的组合：和已注册盒型重合的不重复列。"""
    return [(t, b) for t in TOPS for b in BOTTOMS if (t, b) not in EQUIVALENT]


__all__ = ["BOTTOMS", "TOPS", "CartonParams", "CartonStyle", "catalog_combos"]
