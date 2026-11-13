"""gable 山形手提盒（卡纸）， 节。

印刷面视图，盒身横排（同 RTE 系列），盒身面正立方向为 +y::

              [handle_back]          [handle_front]
     [end_a]  [ roof_back ] [end_a]  [ roof_front ] [end_b]      ← 屋顶 / 手提板只在 back、front 上
     [glue][    back     ][ side_a ][    front    ][ side_b ]
           [ btm_back ][btm_sa][ btm_front ][btm_sb]              ← 扣底（同 snaplock，L2 组件）

- 前、后壁上沿各接一片屋顶板，向盒中线斜上 roof_deg（默认 0：平顶，同 FEFCO 0217 和 reference 112321——屋顶
  斜长约 ½W）；屋顶板上沿再接手提板，竖直立起、背靠背贴合，各开一个跑道形提手孔（两孔合上对齐）。
  屋顶斜长 R = (W/2 − t) / cos(roof_deg)：两片手提板的纸各占中线一侧一层纸厚，正好贴上、不叠。
- 手提板整条长（两头只让端翼的斜度和一层纸厚），两头顶角各一个圆头耳朵，耳朵里侧一个 V 口。
- 两个侧壁上沿各接一片端翼，向盒内斜 lean_deg；端翼中线从压线往上开一条缝（宽两层纸厚 + 让位），两片
  手提板的端头从缝里穿出去，**缝顶卡进手提板的 V 口**，耳朵挡在端翼外面——这就是扣。lean_deg 不给时按 V 口
  的位置算：tan(lean) = (耳朵宽 + t) / (手提板高 − V 口深)，缝顶正好落在 V 口底；V 口里侧斜边和端翼一样斜，
  缝顶以上的端翼沿斜边贴住（2026-11-06 按 FEFCO 0217 和 reference 112321 重做；原来手提板短一截、端翼缝是
  封闭的孔、手提板端头整块穿过去，用户看 3D 指出扣不上）。
- 底部是扣底（1-2-3 底），和 snaplock 同一个组件。

结构对照 reference 模板 112291 "Gable box with snap lock"（网页上读刀版 SVG，三组尺寸；同类 112320 顶部机构
相同）：屋顶 30°、手提板 + 跑道孔、端翼带缝的机构一致；它的手提板高、两端收进量、孔宽、糊口宽随尺寸按模板
变化，三组样本拟不出统一规律，所以这里按上面的几何关系自己定，没有逐点校准（L0）。全部数值【初值】。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.components.assembly import stitch
from pacxio.styles.components.base import Piece
from pacxio.styles.components.carton import GLUE_BEVEL_MM, _chain, _crease
from pacxio.styles.components.lockbottom import SnapBottom, SnaplockParams
from pacxio.styles.components.tube4 import Tube4, TubeBody
from pacxio.styles.corrugated import assemble
from pacxio.styles.rte import RteDims, resolve

# ---- 【初值】自定义几何，结构对照 reference 112291，待折样 ----
ROOF_DEG, ROOF_RANGE = (
    0.0,
    (0.0, 40.0),
)  # 默认平顶（FEFCO 0217 屋顶 ½W、reference 112321 屋顶 74.5 ≈ W/2 − t）
LEAN_DEG, LEAN_RANGE = 30.0, (15.0, 45.0)  # 只在给了 lean_deg 时检查范围；不给按 V 口算
# 手提板两头的扣（reference 112321：手提板高 75 时耳朵宽 26、V 口深 28、缝顶以上的端翼 25）【初值】
NOTCH_EAR_RATIO, EAR_L_RATIO = (
    0.35,
    0.1,
)  # 耳朵宽 = min(0.35·手提板高, 0.1·L)（半圆头，V 口在里侧；小盒子按 L 收）
NOTCH_DEPTH_RATIO = 0.37  # V 口底在手提板顶边下 0.37·手提板高
TIP_RISE_RATIO = 0.8  # 缝顶以上的端翼往上再高 0.8·V 口深（端翼尖停在手提板顶边下面）
EAR_MIN_MM = 8.0
NOTCH_SEAT_MM = 0.5  # V 口平底 = t + 0.5
HANDLE_W_RATIO, HANDLE_EXTRA_MM, HANDLE_RANGE = 0.25, 30.0, (40.0, 100.0)  # 手提板高 = 0.25·Wi + 30
HOLE_L_RATIO, HOLE_W_RANGE, HOLE_H_MM = 0.33, (30.0, 90.0), 25.0  # 提手孔宽 = 0.33·Li
HOLE_MARGIN_MM = 8.0  # 提手孔离手提板边至少这么多
SLIT_CLEAR_MM = 1.0  # 端翼缝：两层手提板外每边再留这么多
SLIT_FOOT_MM = 1.0  # 平顶时缝从压线上方 1 mm 起
TIP_SIDE_MM = 8.0  # 端翼尖在缝两边各留这么宽
MIN_DIM_MM = 40.0


class GableParams(BaseModel):
    """山形手提盒参数。可选项为 None 时按公式取默认值。"""

    model_config = ConfigDict(allow_inf_nan=False)
    L: float = Field(gt=0, description="Length (front width; the handle panel runs this way)")
    W: float = Field(gt=0, description="Width (side width; the roof slopes up from front and back to the middle)")
    H: float = Field(gt=0, description="Body height (without roof and handle panel)")
    dim_basis: Literal["inner", "outer"] = "inner"
    glue_w: float = Field(default=15.0, description="Glue flap width")
    roof_deg: float = Field(default=ROOF_DEG, description="Angle between the roof panel and horizontal")
    lean_deg: float | None = Field(
        default=None, description="Inward lean of the end flaps (from vertical); if not given, derived from the handle panel V-notch"
    )
    handle_h: float | None = Field(default=None, description="Handle panel height; default 0.25·Wi + 30")
    hole_w: float | None = Field(default=None, description="Handle hole width; default 0.33·Li")
    hole_h: float = Field(default=HOLE_H_MM, description="Handle hole height (stadium shape, round at both ends)")
    apply_compensation: bool = True


class GableTop:
    """山形顶（屋顶板 + 手提板 + 带缝端翼）：只依赖盒身尺寸，组合盒（carton）也用它。"""

    def __init__(
        self,
        d: RteDims,
        roof_deg: float = ROOF_DEG,
        lean_deg: float | None = None,
        handle_h: float | None = None,
        hole_w: float | None = None,
        hole_h: float = HOLE_H_MM,
    ) -> None:
        self.d = d
        t = self.t = d.t
        a = math.radians(roof_deg)
        self.roof_deg = roof_deg
        self.c = d.ws / 2
        self.roof = (self.c - t) / math.cos(a)  # 屋顶斜长（印刷面上量）
        self.rise = self.roof * math.sin(a)  # 屋顶高
        self.handle = (
            handle_h
            if handle_h is not None
            else min(max(HANDLE_W_RATIO * d.Wi + HANDLE_EXTRA_MM, HANDLE_RANGE[0]), HANDLE_RANGE[1])
        )
        # 扣：V 口底在手提板顶边下 notch；耳朵宽 ear。端翼斜到缝顶正好落在 V 口底：
        # 离端面 inset + ear = (屋顶高 + off + 手提板高 − notch)·tan(lean)，inset = (屋顶高 + off)·tan(lean) + t
        self.notch = NOTCH_DEPTH_RATIO * self.handle
        below = self.handle - self.notch  # V 口底在手提板根部之上多高
        if lean_deg is None:
            self.ear = max(EAR_MIN_MM, min(NOTCH_EAR_RATIO * self.handle, EAR_L_RATIO * d.Li))
            tan = (self.ear + t) / below
            lean_deg = math.degrees(math.atan(tan))
        else:
            tan = math.tan(math.radians(lean_deg))
            self.ear = below * tan - t
        self.lean_deg = lean_deg
        lean = math.radians(lean_deg)
        self.inset = (self.rise + d.off) * tan + t  # 屋顶上沿 / 手提板两头各收进
        self.base_inset = d.off * tan + t  # 屋顶压线两端各让进（端翼压线低 off 又斜进去）
        self.ramp = self.notch * tan  # V 口里侧斜边的水平长度（和端翼一样斜）
        self.seat = t + NOTCH_SEAT_MM  # V 口底一小段平底：端翼的纸厚落在里面，不和斜边叠
        lo, hi = HOLE_W_RANGE
        self.hole_w = hole_w if hole_w is not None else min(max(HOLE_L_RATIO * d.Li, lo), hi)
        self.hole_h = hole_h
        # 端翼（局部：v 从端翼压线往上量；端翼压线比屋顶压线低 dust_offset）
        self.v_ridge = (self.rise + d.off) / math.cos(lean)
        self.v_notch = (self.rise + d.off + below) / math.cos(lean)  # 缝顶（卡进 V 口底）
        self.slit_w = 2 * t + 2 * SLIT_CLEAR_MM
        self.v_tip = self.v_notch + TIP_RISE_RATIO * self.notch / math.cos(lean)
        self.tip_w = self.slit_w + 2 * TIP_SIDE_MM

    def problems(self) -> list[tuple[str, str]]:
        """放不下的情况：(说明, 修法)。"""
        d, out = self.d, []
        top = d.wb - 2 * self.inset
        if not HANDLE_RANGE[0] / 2 <= self.handle <= 2 * HANDLE_RANGE[1]:
            out.append((translate("handle_h={height:g} is outside {minimum:g}–{maximum:g}", height=self.handle, minimum=HANDLE_RANGE[0] / 2, maximum=2 * HANDLE_RANGE[1]),
                        translate("Adjust handle_h")))  # fmt: skip
        if self.ear < EAR_MIN_MM:
            out.append((translate("The ears at both ends of the handle panel are only {width:.1f} mm wide. The end-flap angle {angle:.0f}° is too small.", width=self.ear, angle=self.lean_deg),
                        translate("Omit lean_deg to calculate it from the V-notch, or increase lean_deg or handle_h")))  # fmt: skip
        top -= 2 * (self.ear + self.seat + self.ramp)  # 两头的耳朵和 V 口占掉的
        if self.hole_w + 2 * HOLE_MARGIN_MM > top or self.hole_h + 2 * HOLE_MARGIN_MM > self.handle:
            out.append((translate("Handle hole {width:g} × {height:g} does not fit on the {panel_width:.1f} × {panel_height:.1f} handle panel with a margin of {margin:g} on every side", width=self.hole_w, height=self.hole_h, panel_width=top, panel_height=self.handle, margin=HOLE_MARGIN_MM),
                        translate("Reduce hole_w or hole_h, or increase L or handle_h")))  # fmt: skip
        if self.hole_h >= self.hole_w:
            out.append(
                (
                    translate(
                        "Handle hole height must be smaller than its width to leave room for semicircular ends"
                    ),
                    translate("Reduce hole_h"),
                )
            )
        if self.tip_w >= d.ws - 2 * d.off:
            out.append(
                (
                    translate("The side panel is too narrow to fit a slot in the end flap"),
                    translate("Make W larger"),
                )
            )
        return out

    def sizes(self) -> dict[str, tuple[float, float]]:
        d = self.d
        return {
            "roof_back": (d.wb - 2 * self.base_inset, self.roof),
            "roof_front": (d.wb - 2 * self.base_inset, self.roof),
            "handle_back": (d.wb - 2 * self.inset, self.handle),
            "handle_front": (d.wb - 2 * self.inset, self.handle),
            "end_a": (d.ws, self.v_tip),
            "end_b": (d.ws, self.v_tip),
        }

    def _handle_path(self, xa: float, xb: float, y0: float, y1: float) -> Path:
        """手提板（逆时针，从左下角起）：两头顶角各一个半圆头耳朵（宽 ear），耳朵里侧 V 口底在顶边下 notch，
        V 口里侧斜边水平长 ramp（和端翼一样斜）。"""
        r, yn = self.ear / 2, y1 - self.notch
        segs = _chain([Vec(xa, y0), Vec(xb, y0), Vec(xb, yn)])
        segs.append(arc_segment(Vec(xb - r, yn), r, 0.0, math.pi))  # 右耳：从外侧绕到里侧
        e, q = self.ear + self.seat, self.ear + self.seat + self.ramp
        segs += _chain([Vec(xb - self.ear, yn), Vec(xb - e, yn), Vec(xb - q, y1),
                        Vec(xa + q, y1), Vec(xa + e, yn), Vec(xa + self.ear, yn)])  # fmt: skip
        segs.append(arc_segment(Vec(xa + r, yn), r, 0.0, math.pi))  # 左耳：从里侧绕到外侧
        segs += _chain([Vec(xa, yn), Vec(xa, y0)])
        return Path(segs, closed=True)

    def build(self, body: TubeBody) -> Piece:
        """外轮廓从 (xs1, hb − off) 走到 (xb0, hb)（同插舌顶的接法）。"""
        d, xs = self.d, body.xs
        hb, off, s, e = d.hb, d.off, self.inset, self.base_inset
        yr, yh = hb + self.roof, hb + self.roof + self.handle  # 屋顶 / 手提板上沿
        yb = hb - off  # 端翼压线

        def V(x: float, y: float) -> Vec:
            return Vec(x, y)

        def end_pts(x0: float, x1: float) -> list[Vec]:
            """端翼外轮廓（从右到左）：两条直边从压线两端收到尖上。"""
            cx = (x0 + x1) / 2
            return [V(x1, yb), V(cx + self.tip_w / 2, yb + self.v_tip),
                    V(cx - self.tip_w / 2, yb + self.v_tip), V(x0, yb)]  # fmt: skip

        def roof_segs(x0: float, x1: float) -> list[Segment]:
            """屋顶 + 手提板外轮廓（从右到左），手提板两头带耳朵和 V 口。"""
            handle = self._handle_path(x0 + s, x1 - s, yr, yh).segments
            # _handle_path 从 (x0+s, yr) 逆时针走一圈；外轮廓要的是它上面那段（右下角 → 左下角）
            k = next(i for i, q in enumerate(handle) if q.start.is_close(V(x1 - s, yr)))
            upper = [
                q
                for q in handle[k:] + handle[:k]
                if not (abs(q.start.y - yr) < 1e-9 and abs(q.end.y - yr) < 1e-9)
            ]
            return [*_chain([V(x1, hb), V(x1 - e, hb), V(x1 - s, yr)]), *upper,
                    *_chain([V(x0 + s, yr), V(x0 + e, hb), V(x0, hb)])]  # fmt: skip

        top: list[Segment] = _chain([V(xs.xs1, yb), *end_pts(xs.xf1, xs.xs1)[1:], V(xs.xf1, hb)])
        top += roof_segs(xs.xa1, xs.xf1) + _chain([V(xs.xa1, hb), V(xs.xa1, yb)])
        top += _chain([V(xs.xa1, yb), *end_pts(xs.xb1, xs.xa1)[1:], V(xs.xb1, hb)])
        top += roof_segs(xs.xb0, xs.xb1)

        def poly(pts: list[Vec]) -> Path:
            return Path.from_points(pts, closed=True)

        piece = Piece(outline=top)
        for face, (x0, x1) in (("back", (xs.xb0, xs.xb1)), ("front", (xs.xa1, xs.xf1))):
            roof, handle = f"roof_{face}", f"handle_{face}"
            piece.panels[roof] = (
                PanelRole.LID,
                poly([V(x0 + e, hb), V(x1 - e, hb), V(x1 - s, yr), V(x0 + s, yr)]),
            )
            piece.panels[handle] = (PanelRole.LID, self._handle_path(x0 + s, x1 - s, yr, yh))
            piece.creases.append(
                _crease(f"crease_{face}_{roof}", (x0 + e, hb), (x1 - e, hb), [face, roof])
            )
            piece.creases.append(
                _crease(f"crease_{roof}_{handle}", (x0 + s, yr), (x1 - s, yr), [roof, handle])
            )
            cx, cy = (x0 + x1) / 2, yr + self.handle / 2  # V 口只在两头，中间全高
            hole = Path.rounded_rect(
                cx - self.hole_w / 2,
                cy - self.hole_h / 2,
                self.hole_w,
                self.hole_h,
                self.hole_h / 2,
            )
            piece.inner_edges.append(Edge(id=f"cut_hole_{face}", type=EdgeType.CUT,
                                          segments=hole.segments, panels=[handle]))  # fmt: skip
        for face, end, (x0, x1) in (("side_a", "end_a", (xs.xb1, xs.xa1)),
                                    ("side_b", "end_b", (xs.xf1, xs.xs1))):  # fmt: skip
            piece.panels[end] = (PanelRole.DUST_FLAP, poly(end_pts(x0, x1)[::-1]))
            piece.creases.append(_crease(f"crease_{face}_{end}", (x0, yb), (x1, yb), [face, end]))
            cx = (x0 + x1) / 2
            v0 = max(
                SLIT_FOOT_MM, self.v_ridge - 1
            )  # 从手提板根部（平顶时几乎就是压线）开到 V 口底
            slit = Path.rounded_rect(
                cx - self.slit_w / 2, yb + v0, self.slit_w, self.v_notch - v0, self.slit_w / 2
            )
            piece.inner_edges.append(
                Edge(id=f"cut_slit_{end}", type=EdgeType.CUT, segments=slit.segments, panels=[end])
            )
        return piece

    def meta(self) -> dict[str, Any]:
        return {
            "top": {"halves": ["roof_front", "roof_back"]},
            "fold": {"roof_deg": self.roof_deg, "lean_deg": self.lean_deg},
        }


class GableDims(GableTop):
    def __init__(self, p: GableParams, material: Material, profile: Profile) -> None:
        t = material.caliper_mm
        if p.dim_basis == "outer":
            li, wi, hi = p.L - 2 * t, p.W - 2 * t, p.H - 2 * t
        else:
            li, wi, hi = p.L, p.W, p.H
        self.lock = SnaplockParams(
            L=li, W=wi, H=hi, glue_w=p.glue_w, apply_compensation=p.apply_compensation
        )
        super().__init__(
            resolve(self.lock, material, profile),
            p.roof_deg,
            p.lean_deg,
            p.handle_h,
            p.hole_w,
            p.hole_h,
        )


class GableStyle:
    """山形手提盒：四面筒 + 扣底 + 山形顶（屋顶板、手提板、带缝端翼）。"""

    id: ClassVar[str] = "gable"
    name_zh: ClassVar[str] = "山形手提盒"
    version: ClassVar[str] = "0.1.0"
    refs: ClassVar[dict[str, str]] = {"design": "结构对照 reference 112291 / 112320；ECMA 编号待查"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})
    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b",
        "roof_back", "handle_back", "end_a", "roof_front", "handle_front", "end_b",
        "btm_back", "btm_sa", "btm_front", "btm_sb",
    )  # fmt: skip

    def param_schema(self) -> type[BaseModel]:
        return GableParams

    def resolve_dims(self, p: GableParams, material: Material, profile: Profile) -> GableDims:
        return GableDims(p, material, profile)

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: GableParams = params
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        if material.type not in self.material_types:
            bad(
                translate(
                    "The gable-top carrier supports folding carton board. The current material is {material}.",
                    material=material.type,
                ),
                translate("Use a folding carton material preset"),
            )
            return issues
        for name in ("L", "W", "H"):
            if getattr(p, name) < MIN_DIM_MM:
                bad(translate("{parameter}={value:g} is below the provisional minimum {minimum:g}", parameter=name, value=getattr(p, name), minimum=MIN_DIM_MM),
                    translate("Set {parameter} to at least {minimum:g}", parameter=name, minimum=MIN_DIM_MM))  # fmt: skip
        lo, hi = ROOF_RANGE
        if not lo <= p.roof_deg <= hi:
            bad(
                translate(
                    "roof_deg={value:g} is outside {minimum:g}–{maximum:g}",
                    value=p.roof_deg,
                    minimum=lo,
                    maximum=hi,
                ),
                translate("Set roof_deg to {minimum:g}–{maximum:g}", minimum=lo, maximum=hi),
            )
        lo, hi = LEAN_RANGE
        if p.lean_deg is not None and not lo <= p.lean_deg <= hi:
            bad(
                translate(
                    "lean_deg={value:g} is outside {minimum:g}–{maximum:g}",
                    value=p.lean_deg,
                    minimum=lo,
                    maximum=hi,
                ),
                translate("Set lean_deg to {minimum:g}–{maximum:g}", minimum=lo, maximum=hi),
            )
        if issues:
            return issues
        g = self.resolve_dims(p, material, profile or load_profile("default"))
        d = g.d
        if min(d.Li, d.Wi, d.Hi) <= 0:
            bad(
                translate("Subtracting the board caliper leaves nonpositive inside dimensions"),
                translate("Increase the dimensions or use --basis inner"),
            )
            return issues
        for msg, fix in g.problems():
            bad(msg, fix)
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return []

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        g = self.resolve_dims(params, material, profile)
        d = g.d
        sizes = {
            "back": (d.wb, d.hb),
            "front": (d.wb, d.hb),
            "side_a": (d.ws, d.hb - 2 * d.off),
            "side_b": (d.ws, d.hb - 2 * d.off),
        }
        sizes |= g.sizes()
        return sizes

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: GableParams = params
        g = self.resolve_dims(p, material, profile)
        d = g.d
        body = Tube4().build(d)
        xs = body.xs
        bottom = SnapBottom().build(g.lock, d, xs)
        hb, off, gw = d.hb, d.off, d.gw
        yb = hb - off
        bevel = GLUE_BEVEL_MM
        top = g.build(body)

        def V(x: float, y: float) -> Vec:
            return Vec(x, y)

        outline = stitch(
            _chain([V(0, bevel), V(gw, 0)]),
            bottom.outline,
            _chain([V(xs.xs1, off), V(xs.xs1, yb)]),
            top.outline,
            _chain([V(gw, hb), V(0, hb - bevel), V(0, bevel)]),
        )
        polys: dict[str, tuple[PanelRole, Path]] = {
            "glue": (
                PanelRole.GLUE_FLAP,
                Path.from_points([V(gw, 0), V(gw, hb), V(0, hb - bevel), V(0, bevel)], closed=True),
            ),
            **body.piece.panels,
            **top.panels,
        }
        creases: list[Edge] = [
            _crease("crease_glue_back", (gw, 0), (gw, hb), ["back", "glue"]),
            *body.piece.creases,
            *top.creases,
        ]
        cuts: list[Edge] = list(top.inner_edges)
        polys |= bottom.polys
        creases += bottom.creases
        order = {pid: i for i, pid in enumerate(self.panel_order)}
        dl = assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=outline,
            polys=dict(sorted(polys.items(), key=lambda kv: order[kv[0]])),
            creases=creases,
            inner_cuts=cuts,
            ups={},
            provisional={pid for pid, (role, _) in polys.items() if role != PanelRole.BODY},
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "accuracy_level": "L0",
                "compensation_applied": p.apply_compensation,
                "top": {"halves": ["roof_front", "roof_back"]},
                "bottom": {
                    "type": "snaplock",
                    "halves": ["btm_front", "btm_back"],
                    "fold_order": {"btm_back": 1, "btm_sa": 2, "btm_sb": 2, "btm_front": 3},
                },  # fmt: skip
                "dims": {
                    "Li": d.Li,
                    "Wi": d.Wi,
                    "Hi": d.Hi,
                    "outer": [d.Li + 2 * d.t, d.Wi + 2 * d.t, d.Hi + g.rise + g.handle],
                    "body_height": hb,
                    "roof": g.roof,
                    "roof_rise": g.rise,
                    "roof_inset": g.inset,
                    "handle_h": g.handle,
                    "hole": [g.hole_w, g.hole_h],
                    "flap": g.roof,
                },
                "fold": {"roof_deg": g.roof_deg, "lean_deg": g.lean_deg},
                "assumptions": [
                    "山形顶：平顶（屋顶 ½W）、手提板背靠背、两头耳朵 + V 口，端翼的缝卡进 V 口锁住手提板；"
                    "结构对照 FEFCO 0217 / reference 112321，比例按其推，未逐点校准【初值】",
                    "底部扣底同 snaplock；手提承重、孔位强度未验证，待折样",
                ],
            },
        )
        x0, y0, _, _ = outline.bbox()
        dl.glue_areas = [g_.transformed(Affine.translate(-x0, -y0)) for g_ in bottom.glue_areas]
        return dl
