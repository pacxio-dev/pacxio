"""反向插舌盒 RTE（）——第一个实现的盒型。

印刷面视图（从盒外侧看），横向排列，body 面正立方向为 +y::

                 [top_tuck]
                 [top_lid ]  [dust_ta]              [dust_tb]
          [glue] [  back  ]  [ side_a ]  [ front  ]  [ side_b ]
                             [dust_ba]   [btm_lid ]  [dust_bb]
                                         [btm_tuck]

顶盖挂在 back 上沿、底盖挂在 front 下沿；防尘翼从 side_a / side_b 上下沿伸出。

规格没有写死、由本实现自行解释的细节（均记入 style_meta.assumptions，须经结构顾问确认）：
- dust_offset：防尘翼压线相对盖压线内移，实现为 side 面上下各缩短 dust_offset，
  外轮廓在该处有一小段竖直切线；
- 插舌头（v2，2026-10-22，按 reference 两份矢量刀版校准，）：防尘翼靠盖那侧
  几乎直，靠插舌那侧（插舌从那一面插进去）顶角圆角、斜下到根部前一个 45° 卡口；插舌满宽（= 内长），
  friction_lock 开时插舌根部两端各一道钩形刀线（离插舌压线一层纸厚、往里 8、末端拐到压线，尺寸照 reference，tuck_slit="cut"，
  2026-11-07 用户要的默认），围出的小卡位留在盖上，和防尘翼的卡口配合卡住插舌；tuck_slit="crease" 是校准时
  reference 刀版的老做法——压线不断，插舌根部离压线 2 mm 两端各一段短压线（作用待折样确认）；
- 糊口（v2，2026-10-22，按 reference 两份刀版，）：不缩进，两端到压线角；
  插舌头那一端外沿低 5 直线斜切到压线角；锁底那一端（扣底、自动锁底）是外沿高 20 的圆弧。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from pacxio.geometry import Affine, Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import (
    Dieline,
    Edge,
    EdgeType,
    Hinge,
    Panel,
    PanelRole,
)
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.base import ORIENTATION_VERIFIED
from pacxio.styles.compensation import coefficients
from pacxio.styles.components.assembly import stitch
from pacxio.styles.components.base import Piece
from pacxio.styles.components.carton import (
    DUST_BACK_TIP_MM as DUST_BACK_TIP_MM,
)
from pacxio.styles.components.carton import (
    DUST_BASE_INSET_MM as DUST_BASE_INSET_MM,
)
from pacxio.styles.components.carton import (
    DUST_CORNER_MM as DUST_CORNER_MM,
)
from pacxio.styles.components.carton import (
    DUST_DEPTH_LI_RATIO as DUST_DEPTH_LI_RATIO,
)
from pacxio.styles.components.carton import (
    DUST_DEPTH_MARGIN_MM as DUST_DEPTH_MARGIN_MM,
)
from pacxio.styles.components.carton import (
    DUST_LOCK_LI_RATIO as DUST_LOCK_LI_RATIO,
)
from pacxio.styles.components.carton import (
    DUST_LOCK_MM as DUST_LOCK_MM,
)
from pacxio.styles.components.carton import (
    DUST_MIN_SLANT_MM as DUST_MIN_SLANT_MM,
)
from pacxio.styles.components.carton import (
    DUST_OFFSET_T_RATIO as DUST_OFFSET_T_RATIO,
)
from pacxio.styles.components.carton import (
    DUST_RELIEF_MM as DUST_RELIEF_MM,
)
from pacxio.styles.components.carton import (
    DUST_TIP_WS_RATIO as DUST_TIP_WS_RATIO,
)
from pacxio.styles.components.carton import (
    GLUE_ARC_H_MM as GLUE_ARC_H_MM,
)
from pacxio.styles.components.carton import (
    GLUE_BEVEL_MM as GLUE_BEVEL_MM,
)
from pacxio.styles.components.carton import (
    GLUE_MIN_STRAIGHT_MM as GLUE_MIN_STRAIGHT_MM,
)
from pacxio.styles.components.carton import (
    GLUE_W_MARGIN_MM as GLUE_W_MARGIN_MM,
)
from pacxio.styles.components.carton import (
    GLUE_W_RANGE as GLUE_W_RANGE,
)
from pacxio.styles.components.carton import (
    MIN_BOX_DIM_MM as MIN_BOX_DIM_MM,
)
from pacxio.styles.components.carton import (
    TUCK_LEN_DEFAULT_HI_RATIO as TUCK_LEN_DEFAULT_HI_RATIO,
)
from pacxio.styles.components.carton import (
    TUCK_LEN_EXTRA_MM as TUCK_LEN_EXTRA_MM,
)
from pacxio.styles.components.carton import (
    TUCK_LEN_RANGE as TUCK_LEN_RANGE,
)
from pacxio.styles.components.carton import (
    TUCK_LEN_WI_RATIO as TUCK_LEN_WI_RATIO,
)
from pacxio.styles.components.carton import (
    TUCK_RADIUS_CAP_MM as TUCK_RADIUS_CAP_MM,
)
from pacxio.styles.components.carton import (
    TUCK_SLIT_GAP_MM as TUCK_SLIT_GAP_MM,
)
from pacxio.styles.components.carton import (
    TUCK_SLIT_LI_RATIO as TUCK_SLIT_LI_RATIO,
)
from pacxio.styles.components.carton import (
    BottomPart as BottomPart,
)
from pacxio.styles.components.carton import (
    RteDims as RteDims,
)
from pacxio.styles.components.carton import (
    TuckEnd as TuckEnd,
)
from pacxio.styles.components.carton import (
    _backwards as _backwards,
)
from pacxio.styles.components.carton import (
    _chain as _chain,
)
from pacxio.styles.components.carton import (
    _closed_flap as _closed_flap,
)
from pacxio.styles.components.carton import (
    _crease as _crease,
)
from pacxio.styles.components.carton import (
    _dust_flap as _dust_flap,
)
from pacxio.styles.components.carton import (
    _dust_local as _dust_local,
)
from pacxio.styles.components.carton import (
    _ordered as _ordered,
)
from pacxio.styles.components.carton import (
    _ordered_edges as _ordered_edges,
)
from pacxio.styles.components.carton import (
    _rect as _rect,
)
from pacxio.styles.components.carton import (
    _trapezoid as _trapezoid,
)
from pacxio.styles.components.carton import (
    _tuck_polygon as _tuck_polygon,
)
from pacxio.styles.components.carton import (
    _tuck_segments as _tuck_segments,
)
from pacxio.styles.components.carton import (
    _tuck_slits as _tuck_slits,
)
from pacxio.styles.components.tube4 import BodyX as BodyX
from pacxio.styles.components.tube4 import Tube4, TubeBody
from pacxio.styles.components.tuck import TuckTop


class RteParams(BaseModel):
    """RTE 参数表。可选项为 None 时按规格公式取默认值。"""

    L: float = Field(gt=0, description="Length (front width)")
    W: float = Field(gt=0, description="Width (side width)")
    H: float = Field(gt=0, description="Height")
    dim_basis: Literal["inner", "outer"] = "inner"
    glue_w: float = 15.0
    # v1 的参数：v2 的糊口不缩进、端头按标准刀版，不再使用，留着只为旧 job.json 读得进来
    glue_inset: float = 2.0
    glue_bevel_deg: float = 15.0
    tuck_len: float | None = None
    tuck_inset: float | None = None
    tuck_radius: float | None = None
    friction_lock: bool = True
    # 插舌压线两端：cut = 顺着压线切口（默认，2026-11-07）；crease = 离压线 2 mm 的短压线（reference 老做法）
    tuck_slit: Literal["cut", "crease"] = "cut"
    dust_depth: float | None = None
    # v1 的参数：v2 的插舌头不再使用（防尘翼的形状由两份标准刀版定，锁扣不再是插舌上的 V 形缺口），
    # 留着只为旧 job.json 读得进来
    friction_lock_depth: float = 1.5
    friction_lock_pos: float = 2.0
    dust_taper_deg: float = 15.0
    dust_offset: float | None = None
    # 补偿系数来自配置档（）；关闭时面尺寸等于内尺寸，仅用于对照
    apply_compensation: bool = True


def resolve(params: RteParams, material: Material, profile: Profile) -> RteDims:
    t = material.caliper_mm
    if params.dim_basis == "outer":
        Li, Wi, Hi = params.L - 2 * t, params.W - 2 * t, params.H - 2 * t
    else:
        Li, Wi, Hi = params.L, params.W, params.H
    k = coefficients(profile, material, params.apply_compensation)
    tl = (
        params.tuck_len
        if params.tuck_len is not None
        else min(TUCK_LEN_WI_RATIO * Wi + TUCK_LEN_EXTRA_MM, TUCK_LEN_RANGE[1],
                 TUCK_LEN_DEFAULT_HI_RATIO * Hi)
    )  # fmt: skip
    wb = Li + k.k_panel * t
    ti = params.tuck_inset if params.tuck_inset is not None else 0.5 * (wb - Li)  # 插舌宽 = Li
    tr = params.tuck_radius if params.tuck_radius is not None else min(TUCK_RADIUS_CAP_MM, tl / 2)
    dd = params.dust_depth if params.dust_depth is not None else min(DUST_DEPTH_LI_RATIO * Li, Wi)
    off = params.dust_offset if params.dust_offset is not None else DUST_OFFSET_T_RATIO * t
    return RteDims(
        t=t,
        Li=Li,
        Wi=Wi,
        Hi=Hi,
        wb=wb,
        ws=Wi + k.k_panel * t,
        hb=Hi + k.k_height * t,
        ld=Wi + k.k_lid * t,
        tl=tl,
        ti=ti,
        tr=tr,
        dd=dd,
        off=off,
        gw=params.glue_w,
    )


class RteStyle:
    """反向插舌盒；STE 继承本类，仅底盖所挂的面不同。"""

    id: ClassVar[str] = "rte"
    name_zh: ClassVar[str] = "反向插舌盒"
    refs: ClassVar[dict[str, str]] = {"ecma": "A20.20.01.01（待核对）"}
    # 0.2.0：插舌头按标准刀版重做；0.3.0：糊口不缩进、端头按标准刀版（都是 2026-10-22）；
    # 0.4.0：插舌压线两端改成切口（2026-11-07）
    version: ClassVar[str] = "0.4.0"
    bottom_lid_on: ClassVar[str] = "front"  # 底盖挂在哪个面：RTE=front，STE=back
    glue_bottom: ClassVar[Literal["bevel", "arc"]] = "bevel"  # 糊口下端：插舌底斜切，锁底圆弧
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})

    def param_schema(self) -> type[BaseModel]:
        return RteParams

    # ---- 约束（）----

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: RteParams = params
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        if material.type not in self.material_types:
            bad(
                translate(
                    "{style} supports {supported}. The current material type is {material}.",
                    style=self.id.upper(),
                    supported=sorted(self.material_types),
                    material=material.type,
                ),
                translate("Use a folding carton board preset such as sbs_350"),
            )
        for name in ("L", "W", "H"):
            v = getattr(p, name)
            if v < MIN_BOX_DIM_MM:
                bad(
                    translate(
                        "{parameter}={value} is below the minimum {minimum}",
                        parameter=name,
                        value=v,
                        minimum=MIN_BOX_DIM_MM,
                    ),
                    translate(
                        "Set {parameter} to at least {minimum}",
                        parameter=name,
                        minimum=MIN_BOX_DIM_MM,
                    ),
                )
        t = material.caliper_mm
        if min(p.L, p.W, p.H) <= 2 * t and p.dim_basis == "outer":
            bad(
                translate(
                    "The outside dimensions leave no positive inside dimensions after subtracting twice the board caliper"
                ),
                translate("Increase the dimensions or use --basis inner"),
            )
            return issues
        if issues:
            return issues

        # 补偿系数来自配置档；调用方未传入时用 default（仅影响启用补偿时的尺寸判断）
        d = resolve(p, material, profile or load_profile("default"))
        if not (GLUE_W_RANGE[0] <= p.glue_w <= GLUE_W_RANGE[1]):
            bad(
                translate(
                    "glue_w={value} is outside the range {allowed}",
                    value=p.glue_w,
                    allowed=GLUE_W_RANGE,
                ),
                translate(
                    "Set glue_w to between {minimum} and {maximum}",
                    minimum=GLUE_W_RANGE[0],
                    maximum=GLUE_W_RANGE[1],
                ),
            )
        if p.glue_w > d.Wi - GLUE_W_MARGIN_MM:
            bad(
                translate(
                    "glue_w={value} must be at most Wi − {margin} (currently {maximum:.2f})",
                    value=p.glue_w,
                    margin=GLUE_W_MARGIN_MM,
                    maximum=d.Wi - GLUE_W_MARGIN_MM,
                ),
                translate("Set glue_w to at most {maximum:.2f}", maximum=d.Wi - GLUE_W_MARGIN_MM),
            )
        if d.hb - GLUE_BEVEL_MM - self._glue_bottom_h(d) <= 0:
            bad(
                translate(
                    "The carton is too short for the bevels or arcs at the ends of the glue flap"
                ),
                translate("Increase H"),
            )
        issues += self._tuck_issues(p, d)
        issues += self._validate_bottom(p, d)
        return issues

    def _tuck_issues(self, p: RteParams, d: RteDims) -> list[Issue]:
        """插舌头（插舌、防尘翼、卡口）放不放得下。几何引擎拼装时只在有插舌的一端才查。"""
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        if p.tuck_len is not None and not (TUCK_LEN_RANGE[0] <= p.tuck_len <= TUCK_LEN_RANGE[1]):
            bad(
                translate(
                    "tuck_len={value} is outside the range {allowed}",
                    value=p.tuck_len,
                    allowed=TUCK_LEN_RANGE,
                ),
                translate(
                    "Set tuck_len to between {minimum} and {maximum}",
                    minimum=TUCK_LEN_RANGE[0],
                    maximum=TUCK_LEN_RANGE[1],
                ),
            )
        if d.tl >= d.Hi:
            bad(
                translate(
                    "tuck_len must be smaller than Hi (currently {value} ≥ {inside_height:.2f})",
                    value=d.tl,
                    inside_height=d.Hi,
                ),
                translate(
                    "Set tuck_len to at most {maximum:.2f}",
                    maximum=min(TUCK_LEN_RANGE[1], d.Hi - 1),
                ),
            )
        max_dust = 0.5 * d.Li - DUST_DEPTH_MARGIN_MM
        if d.dd > max_dust or d.dd <= 0:
            bad(
                translate(
                    "dust_depth={value:.2f} must be in (0, 0.5·Li − 1 = {maximum:.2f}]",
                    value=d.dd,
                    maximum=max_dust,
                ),
                translate("Set dust_depth to at most {maximum:.2f}", maximum=max_dust),
            )
        else:
            lock = DUST_LOCK_LI_RATIO * d.Li + DUST_LOCK_MM if p.friction_lock else 0.0
            inward = (
                d.ws * DUST_TIP_WS_RATIO
                - DUST_BASE_INSET_MM
                - (DUST_LOCK_MM if p.friction_lock else 0.0)
            )
            if inward <= 0 or d.dd - max(lock, DUST_RELIEF_MM) < DUST_MIN_SLANT_MM:
                bad(translate("The dust flap does not fit. Depth {depth:.2f} and side width {side_width:.2f} leave too little room for the root relief and lock.", depth=d.dd, side_width=d.ws),
                    translate("Increase dust_depth or W, or disable friction_lock"))  # fmt: skip
        tuck_w = d.wb - 2 * d.ti
        if tuck_w <= 2 * d.tr:
            bad(
                translate(
                    "The tuck tab width {width:.2f} is too small for corner radius {radius}",
                    width=tuck_w,
                    radius=d.tr,
                ),
                translate("Reduce tuck_inset or tuck_radius"),
            )
        if p.friction_lock and p.tuck_slit == "crease" and TUCK_SLIT_GAP_MM >= d.tl - d.tr:
            bad(
                translate(
                    "The short crease at the tuck root extends beyond the straight section of the tab"
                ),
                translate("Increase tuck_len or reduce tuck_radius"),
            )
        if d.tl > d.ld:
            bad(
                translate(
                    "Tuck length {length} exceeds lid depth {depth:.2f}", length=d.tl, depth=d.ld
                ),
                translate("Reduce tuck_len"),
            )
        return issues

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        """面 id → 参数推出的 (宽, 高)，供 S02 与几何量回值比对。"""
        d = resolve(params, material, profile)
        return {
            "back": (d.wb, d.hb),
            "front": (d.wb, d.hb),
            "top_lid": (d.wb, d.ld),
            **self._bottom_sizes(params, d),
            "side_a": (d.ws, d.hb - 2 * d.off),
            "side_b": (d.ws, d.hb - 2 * d.off),
        }

    # ---- 生成 ----

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: RteParams = params
        d = self._resolve(p, material, profile)
        gw = d.gw
        hb, off = d.hb, d.off
        yc = hb - d.top_off  # 上防尘翼压线高度、根部离面分界

        body = Tube4().build(d)
        xs = body.xs
        xb0, xb1, xa1, xf1, xs1 = xs
        gh = self._glue_bottom_h(d)  # 糊口下端：外沿离下压线多高
        if self.glue_bottom == "arc":  # 外沿处竖直相切、过压线角的圆弧（逆时针）
            r = (gw**2 + gh**2) / (2 * gw)
            sweep = math.atan2(-gh, gw - r) % (2 * math.pi) - math.pi
            glue_end = [Segment(Vec(0, gh), Vec(xb0, 0), math.tan(sweep / 4))]
        else:
            glue_end = _chain([Vec(0, gh), Vec(xb0, 0)])

        def V(x: float, y: float) -> Vec:
            return Vec(x, y)

        top = self._top(p, d, body)
        bottom = self._bottom(p, d, xs)
        outline = stitch(
            glue_end,
            bottom.outline,
            _chain([V(xs1, off), V(xs1, yc)]),
            top.outline,
            _chain([V(xb0, hb), V(0, hb - GLUE_BEVEL_MM), V(0, gh)]),
        )

        # ---- 面（多边形）----
        polys: dict[str, tuple[PanelRole, Path]] = {
            "glue": (
                PanelRole.GLUE_FLAP,
                Path(
                    _chain([V(xb0, 0), V(xb0, hb), V(0, hb - GLUE_BEVEL_MM), V(0, gh)]) + glue_end,
                    closed=True,
                ),
            ),  # fmt: skip
            **body.piece.panels,
            **top.panels,
            **bottom.polys,
        }
        polys = _ordered(polys, self.panel_order)

        creases = [
            _crease("crease_glue_back", (xb0, 0), (xb0, hb), ["back", "glue"]),
            *body.piece.creases,
            *top.creases,
            *bottom.creases,
        ]
        creases = _ordered_edges(creases, self.crease_order)
        edges = [Edge(id="cut_outline", type=EdgeType.CUT, segments=outline.segments), *creases]
        edges += bottom.extra_edges  # 内部切口、插舌根部的短压线等（不在两个面的分界上）
        edges += top.inner_edges

        # ---- 归一化：原点移到刀版包围盒左下角（D3）----
        minx, miny, maxx, maxy = outline.bbox()
        shift = Affine.translate(-minx, -miny)
        edges = [
            e.model_copy(update={"segments": [shift.apply_segment(s) for s in e.segments]})
            for e in edges
        ]
        glue_areas = [g.transformed(shift) for g in bottom.glue_areas]

        hinge_of = {e.panels[1]: Hinge(parent_panel=e.panels[0], edge_id=e.id) for e in creases}
        hinge_of["front"] = Hinge(parent_panel="side_a", edge_id="crease_side_a_front")
        hinge_of["side_a"] = Hinge(parent_panel="back", edge_id="crease_back_side_a")
        hinge_of["side_b"] = Hinge(parent_panel="front", edge_id="crease_front_side_b")

        verified = self.id in ORIENTATION_VERIFIED
        provisional = (
            set()
            if verified
            else {
                PanelRole.LID,
                PanelRole.TUCK,
                PanelRole.BOTTOM,
                PanelRole.GLUE_TAB,
            }
        )  # 盖、底翼的"正立"待打样
        panels: list[Panel] = []
        for pid, (role, poly) in polys.items():
            poly = poly.transformed(shift)
            bx, by, bx1, by1 = poly.bbox()
            frame = Affine.translate(bx, by)
            if pid == "top_lid":
                # 顶盖挂在背面上沿、插舌从正面打开：盖上的图文按开盒的顺手方向，从正面看是正的，
                # 所以在展开图里转 180°（2026-10-20 用户决定；实物方向仍待打样确认）
                frame = Affine(-1, 0, 0, -1, bx1, by1)
            panels.append(
                Panel(
                    id=pid,
                    role=role,
                    polygon=poly,
                    frame=frame,
                    hinge=hinge_of.get(pid),
                    printable=not (role == PanelRole.GLUE_FLAP and profile.glue_flap.ink == "none"),
                    orientation_provisional=role in provisional,
                )
            )

        return Dieline(
            style=self.id,
            params=p.model_dump(),
            material_preset=material.preset,
            caliper_mm=material.caliper_mm,
            profile_id=profile.id,
            style_meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "orientation_verified": verified,
                "compensation_applied": p.apply_compensation,
                "dims": {
                    "Li": d.Li,
                    "Wi": d.Wi,
                    "Hi": d.Hi,
                    "panel_back_front": d.wb,
                    "panel_side": d.ws,
                    "body_height": d.hb,
                    "lid_depth": d.ld,
                },
                "assumptions": [
                    "dust_offset：side 面上下各缩短 dust_offset，外轮廓在该处有竖直切线（规格未规定具体形状）",
                    "friction_lock：插舌根部上方的 V 形缺口（规格只给了切口深 1.5，形状与位置为本实现假设）",
                    "糊口不缩进（v2，按 reference 刀版）：插舌头那端外沿低 5 斜切到压线角，"
                    "锁底那端外沿高 20 的圆弧到压线角",
                    "盖/插舌正立方向经实物打样确认（2026-10-24）"
                    if verified
                    else "盖/插舌正立方向待 M1 方向测试确认（orientation_provisional）",
                    *self.bottom_assumptions,
                ],
                **self._bottom_meta(d),
            },
            bbox=(0.0, 0.0, maxx - minx, maxy - miny),
            panels=panels,
            edges=edges,
            glue_areas=glue_areas,
        )

    def _resolve(self, p: RteParams, material: Material, profile: Profile) -> RteDims:
        """生成用的尺寸（几何引擎的组合在这里改两端的防尘让位）。"""
        return resolve(p, material, profile)

    def _top(self, p: RteParams, d: RteDims, body: TubeBody) -> Piece:
        return TuckTop(d, p.friction_lock, slit=p.tuck_slit).build(body)

    # ---- 底部（子类替换：snaplock、autolock）----

    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b", "top_lid", "btm_lid", "top_tuck", "btm_tuck",
        "dust_ta", "dust_tb", "dust_ba", "dust_bb",
    )  # fmt: skip
    crease_order: ClassVar[tuple[str, ...]] = (
        "crease_glue_back", "crease_back_side_a", "crease_side_a_front", "crease_front_side_b",
        "crease_back_top_lid", "crease_front_btm_lid", "crease_back_btm_lid", "crease_top_lid_tuck",
        "crease_btm_lid_tuck", "crease_side_a_dust_ta", "crease_side_a_dust_ba",
        "crease_side_b_dust_tb", "crease_side_b_dust_bb",
    )  # fmt: skip
    bottom_assumptions: ClassVar[tuple[str, ...]] = ()

    def _glue_bottom_h(self, d: RteDims) -> float:
        """糊口下端外沿离下压线多高：插舌底 5；锁底 20（盒子太矮时压低，给直边留 10）。"""
        if self.glue_bottom == "bevel":
            return GLUE_BEVEL_MM
        return min(GLUE_ARC_H_MM, d.hb - GLUE_BEVEL_MM - GLUE_MIN_STRAIGHT_MM)

    def _bottom_meta(self, d: RteDims) -> dict[str, Any]:
        """style_meta 里和底部有关的信息（效果图怎样拼底面等）。"""
        return {}

    def _bottom(self, p: RteParams, d: RteDims, xs: BodyX) -> BottomPart:
        return TuckEnd(self.bottom_lid_on).build(p, d, xs)

    def _bottom_sizes(self, p: RteParams, d: RteDims) -> dict[str, tuple[float, float]]:
        return {"btm_lid": (d.wb, d.ld)}

    def _validate_bottom(self, p: RteParams, d: RteDims) -> list[Issue]:
        return []
