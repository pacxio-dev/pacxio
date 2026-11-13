"""counter_display 柜台展示盒（带头卡）， 节。

印刷面视图，底板居中（y 向上：前板在下、后板 + 头卡在上）::

                       [ header_front（头卡正面 + 后内壁，插脚插底板）]
                       [ header_rim ]
                       [   back（后板 + 头卡背面，同一块）   ]
       [ear_bb]                                             [ear_ba]
    [side_b_in][rim_b][side_b][        base        ][side_a][rim_a][side_a_in]
       [ear_fb]                                             [ear_fa]
                       [ front（上沿中间挖低，方便取货）]

- 托盘和飞机盒的卷边托盘是同一种做法：前、后板两端的耳朵折进侧板夹层；侧板外壁 → 卷边 → 内壁，内壁的插脚
  插进底板的插孔。卷边宽 3t + 0.5（夹层放下一层耳朵再留 0.5，和 S06 查出来改过的三款新飞机盒一致）。
  卷边后端缩到 W − t − 0.5：后板往上延伸成头卡，卷边不能伸进后板（S06 查出）。
- 后板往上延伸成头卡背面；头卡顶上一条窄卷边（2t + 0.5），翻过来的 header_front 一路往下贴着后板里面到底，
  下端插脚插进底板后边的插孔：头卡和后内壁是同一块纸，印刷面朝顾客。
- 前板上沿中间挖低（梯形缺口，45° 斜边），露出商品、方便拿。

结构对照 reference 模板 170311 "Counter display box"（网页上读刀版 SVG）：托盘双层侧壁 + 插脚、前板缺口、
后板翻成双层头卡的机构一致；它的头卡还有一个 U 形翻舌、侧卷边只有 2t，这里按上面的几何自定（L0）。
全部数值【初值】，未折样。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import shapely
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import Polygon

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile
from pacxio.styles.components.carton import _crease
from pacxio.styles.corrugated import assemble

# ---- 【初值】自定义几何，结构对照 reference 170311，待折样 ----
RIM_T, RIM_EXTRA_MM = 3.0, 0.5  # 侧卷边 3t + 0.5（夹层放一层耳朵）
HEADER_RIM_T = 2.0  # 头卡顶卷边 2t + 0.5（两层背靠背）
EAR_GAP_MM, EAR_TOP_MM = 3.0, 1.0  # 耳朵离底板压线 3，离卷边 1
EAR_W_RATIO = 0.33  # 耳朵宽 = 0.33·W
LUG_T, LUG_W_RATIO, LUG_W_MAX = 2.5, 0.2, 40.0  # 插脚长 2.5t，宽 min(40, 0.2·边长)
SLOT_CLEAR_MM = 0.5  # 插孔比插脚 / 纸厚每边多留
INNER_CLEAR_MM = 0.5  # 内壁两端让开别的纸层
CUT_W_RATIO, CUT_D_RATIO = 0.6, 0.35  # 前板缺口宽 0.6·L、深 0.35·H
MIN_DIM_MM = 60.0


class CounterParams(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    L: float = Field(gt=0, description="Base length (front width)")
    W: float = Field(gt=0, description="Base depth")
    H: float = Field(gt=0, description="Tray wall height")
    header_h: float | None = Field(default=None, description="Header card height above the tray; default = W")
    cut_w: float | None = Field(default=None, description="Front panel cut-out width; default 0.6·L")
    cut_d: float | None = Field(default=None, description="Front panel cut-out depth; default 0.35·H; 0 = no cut-out")
    flute_dir: Literal["y"] = "y"


class CounterDims:
    def __init__(self, p: CounterParams, material: Material) -> None:
        t = self.t = material.caliper_mm
        self.L, self.W, self.H = p.L, p.W, p.H
        self.rs = RIM_T * t + RIM_EXTRA_MM  # 侧卷边
        self.rb = HEADER_RIM_T * t + RIM_EXTRA_MM  # 头卡顶卷边
        self.fi = t + 0.25  # 前、后板两端各缩进：耳朵落在侧板夹层里
        self.hin = p.H - t  # 侧内壁高（卷边到底板上面）
        self.hh = p.header_h if p.header_h is not None else p.W
        self.ew = EAR_W_RATIO * p.W
        self.eh = p.H - t - EAR_GAP_MM - EAR_TOP_MM
        self.lug = LUG_T * t
        self.cut_d = p.cut_d if p.cut_d is not None else CUT_D_RATIO * p.H
        # 默认缺口宽：0.6·L，但两边斜边（各 cut_d）外再各留 10
        room = p.L - 2 * self.fi - 2 * self.cut_d - 20
        self.cut_w = p.cut_w if p.cut_w is not None else min(CUT_W_RATIO * p.L, room)
        # 侧卷边后端让开后板（后板往上延伸成头卡，卷边不能伸进它）
        self.rim_y1 = p.W - t - INNER_CLEAR_MM
        # 侧内壁沿 y 的范围：让开前板和后内壁（头卡翻下来那一层）
        self.iy0 = t + INNER_CLEAR_MM
        self.iy1 = p.W - self.rb - INNER_CLEAR_MM
        # 头卡 / 后内壁宽：在两块侧内壁之间
        self.hx0 = self.rs + INNER_CLEAR_MM
        self.hx1 = p.L - self.rs - INNER_CLEAR_MM

    def lugs(self, a: float, b: float) -> list[tuple[float, float]]:
        """沿一条边 [a, b] 放插脚：长 ≥ 120 两个（0.3、0.7），否则一个居中；返回 (中心, 宽)。"""
        span = b - a
        w = min(LUG_W_MAX, LUG_W_RATIO * span)
        centres = (0.3, 0.7) if span >= 120 else (0.5,)
        return [(a + k * span, w) for k in centres]


def _rect(x0: float, y0: float, x1: float, y1: float) -> Path:
    # 四角直接给坐标：Path.rect 的 x0 + w 会有浮点误差，相邻面 union 时会断开
    return _poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _poly(pts: list[tuple[float, float]]) -> Path:
    return Path.from_points([Vec(x, y) for x, y in pts], closed=True)


class CounterDisplayStyle:
    """柜台展示盒：卷边双层侧壁托盘 + 前板缺口 + 后板翻成的双层头卡。"""

    id: ClassVar[str] = "counter_display"
    name_zh: ClassVar[str] = "柜台展示盒"
    version: ClassVar[str] = "0.1.0"
    refs: ClassVar[dict[str, str]] = {
        "design": "结构对照 reference 170311 counter display box；编号待查"
    }
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"corrugated", "folding_carton"})
    default_print_method: ClassVar[str] = "flexo"

    def param_schema(self) -> type[BaseModel]:
        return CounterParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: CounterParams = params
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        if material.type not in self.material_types:
            bad(
                translate(
                    "Counter display cartons do not support material type {material}",
                    material=material.type,
                ),
                translate("Use corrugated board or folding carton"),
            )
            return issues
        for name in ("L", "W", "H"):
            if getattr(p, name) < MIN_DIM_MM:
                bad(translate("{parameter}={value:g} is below the provisional minimum {minimum:g}", parameter=name, value=getattr(p, name), minimum=MIN_DIM_MM),
                    translate("Set {parameter} to at least {minimum:g}", parameter=name, minimum=MIN_DIM_MM))  # fmt: skip
        if issues:
            return issues
        d = CounterDims(p, material)
        if d.eh < 10 or d.ew < 10:
            bad(
                translate("The tray is too low or too shallow for the locking ears"),
                translate("Increase H or W"),
            )
        if d.hh < 20 or d.hh > 3 * p.W:
            bad(
                translate("header_h={value:g} is outside 20–3·W", value=d.hh),
                translate("Adjust header_h"),
            )
        if not 0 <= d.cut_d <= p.H - 15:
            bad(
                translate("Front notch depth {depth:g} must be between 0 and H−15", depth=d.cut_d),
                translate("Reduce cut_d"),
            )
        if d.cut_d > 0 and not 20 <= d.cut_w + 0 <= p.L - 2 * d.fi - 2 * d.cut_d - 20:
            bad(
                translate(
                    "Front notch width {width:g} does not fit. Allow cut_d for each bevel plus 10 on each side.",
                    width=d.cut_w,
                ),
                translate("Adjust cut_w or cut_d"),
            )
        if d.iy1 - d.iy0 < 30 or d.hx1 - d.hx0 < 30:
            bad(translate("The inner wall is too short"), translate("Increase L or W"))
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return []

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = CounterDims(params, material)
        L, W, H = d.L, d.W, d.H
        sizes = {
            "base": (L, W),
            "front": (L - 2 * d.fi, H),
            "back": (L - 2 * d.fi, H + d.hh),
            "side_a": (H, W),
            "side_b": (H, W),
            "rim_a": (d.rs, d.rim_y1),
            "rim_b": (d.rs, d.rim_y1),
            "header_rim": (d.hx1 - d.hx0, d.rb),
        }
        return sizes

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: CounterParams = params
        d = CounterDims(p, material)
        L, W, H, t = d.L, d.W, d.H, d.t
        fi, rs, rb, hh, hin = d.fi, d.rs, d.rb, d.hh, d.hin
        polys: dict[str, tuple[PanelRole, Path]] = {}
        creases: list[Edge] = []
        cuts: list[Edge] = []

        polys["base"] = (PanelRole.BOTTOM, _rect(0, 0, L, W))
        # 前板：上沿中间挖低（展开图里前板的自由边在下）
        cx, cw, cd = L / 2, d.cut_w, d.cut_d
        if cd > 0:
            front = _poly([(fi, 0), (fi, -H), (cx - cw / 2 - cd, -H), (cx - cw / 2, -H + cd),
                           (cx + cw / 2, -H + cd), (cx + cw / 2 + cd, -H), (L - fi, -H), (L - fi, 0)])  # fmt: skip
        else:
            front = _rect(fi, -H, L - fi, 0)
        polys["front"] = (PanelRole.BODY, front)
        polys["back"] = (PanelRole.BODY, _rect(fi, W, L - fi, W + H + hh))
        creases += [
            _crease("crease_base_front", (fi, 0), (L - fi, 0), ["base", "front"]),
            _crease("crease_base_back", (fi, W), (L - fi, W), ["base", "back"]),
        ]
        # 耳朵（前后板两端，折进侧板夹层）
        e0, e1 = EAR_GAP_MM, EAR_GAP_MM + d.eh
        for wall, ys, tag in (("front", (-e1, -e0), "f"), ("back", (W + e0, W + e1), "b")):
            for side, x_hinge, x_out in (("a", L - fi, L - fi + d.ew), ("b", fi, fi - d.ew)):
                ear = f"ear_{tag}{side}"
                polys[ear] = (
                    PanelRole.DUST_FLAP,
                    _rect(min(x_hinge, x_out), ys[0], max(x_hinge, x_out), ys[1]),
                )
                creases.append(
                    _crease(f"crease_{wall}_{ear}", (x_hinge, ys[0]), (x_hinge, ys[1]), [wall, ear])
                )
        # 侧板：外壁 → 卷边 → 内壁（插脚在内壁自由边上）
        for side, sgn, x_base in (("a", 1, L), ("b", -1, 0.0)):
            x1 = x_base + sgn * H
            x2 = x1 + sgn * rs
            x3 = x2 + sgn * hin
            outer, rim, inner = f"side_{side}", f"rim_{side}", f"side_{side}_in"
            polys[outer] = (PanelRole.BODY, _rect(min(x_base, x1), 0, max(x_base, x1), W))
            polys[rim] = (PanelRole.RIM, _rect(min(x1, x2), 0, max(x1, x2), d.rim_y1))
            body: Any = Polygon([(x2, d.iy0), (x3, d.iy0), (x3, d.iy1), (x2, d.iy1)])
            for c, w in d.lugs(d.iy0, d.iy1):
                body = body.union(Polygon([(x3, c - w / 2), (x3 + sgn * d.lug, c - w / 2),
                                           (x3 + sgn * d.lug, c + w / 2), (x3, c + w / 2)]))  # fmt: skip
                # 插孔在底板里：内壁落在离侧边 rs − t … rs 处（纸厚朝外壁那边）
                lo, hi = sorted(
                    (x_base - sgn * (rs - t - SLOT_CLEAR_MM), x_base - sgn * (rs + SLOT_CLEAR_MM))
                )
                slot = _rect(lo, c - w / 2 - SLOT_CLEAR_MM, hi, c + w / 2 + SLOT_CLEAR_MM)
                cuts.append(Edge(id=f"cut_slot_{side}_{len(cuts)}", type=EdgeType.CUT,
                                 segments=slot.segments, panels=["base"]))  # fmt: skip
            polys[inner] = (PanelRole.INNER_WALL, _shape_path(body))
            creases += [
                _crease(f"crease_base_{outer}", (x_base, 0), (x_base, W), ["base", outer]),
                _crease(f"crease_{outer}_{rim}", (x1, 0), (x1, d.rim_y1), [outer, rim]),
                _crease(f"crease_{rim}_{inner}", (x2, d.iy0), (x2, d.iy1), [rim, inner]),
            ]
        # 头卡：后板顶上一条卷边，翻过来的 header_front 一直下到底板，下端插脚插后边插孔
        y_top = W + H + hh
        polys["header_rim"] = (PanelRole.RIM, _rect(d.hx0, y_top, d.hx1, y_top + rb))
        y2 = y_top + rb
        y3 = y2 + hh + H - t  # header_front 下端（展开图里在上）
        hf: Any = Polygon([(d.hx0, y2), (d.hx1, y2), (d.hx1, y3), (d.hx0, y3)])
        for c, w in d.lugs(d.hx0, d.hx1):
            hf = hf.union(
                Polygon(
                    [
                        (c - w / 2, y3),
                        (c + w / 2, y3),
                        (c + w / 2, y3 + d.lug),
                        (c - w / 2, y3 + d.lug),
                    ]
                )
            )
            lo, hi = W - rb - SLOT_CLEAR_MM, W - rb + t + SLOT_CLEAR_MM
            slot = _rect(c - w / 2 - SLOT_CLEAR_MM, lo, c + w / 2 + SLOT_CLEAR_MM, hi)
            cuts.append(Edge(id=f"cut_slot_back_{len(cuts)}", type=EdgeType.CUT,
                             segments=slot.segments, panels=["base"]))  # fmt: skip
        polys["header_front"] = (PanelRole.BODY, _shape_path(hf))
        creases += [
            _crease(
                "crease_back_header_rim", (d.hx0, y_top), (d.hx1, y_top), ["back", "header_rim"]
            ),
            _crease(
                "crease_header_rim_front", (d.hx0, y2), (d.hx1, y2), ["header_rim", "header_front"]
            ),
        ]

        union = shapely.union_all([_to_shapely(path) for _, path in polys.values()])
        outline = _shape_path(union)
        dl = assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=outline,
            polys=polys,
            creases=creases,
            inner_cuts=cuts,
            ups={"front": "down"},
            provisional={pid for pid in polys if pid not in ("base",)},
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "accuracy_level": "L0",
                "compensation_applied": True,  # Fixed inner dimensions with board allowances in CounterDims.
                "layout": "tray",
                "faces": {
                    "bottom": "base",
                    "front": "front",
                    "back": "back",
                    "side_a": "side_a",
                    "side_b": "side_b",
                },  # fmt: skip
                "dims": {
                    "Li": L,
                    "Wi": W,
                    "Hi": H,
                    "outer": [L, W, H + hh],
                    "rim": rs,
                    "header_h": hh,
                    "front_cut": [d.cut_w, d.cut_d],
                },
                "assumptions": [
                    "托盘同飞机盒卷边托盘（耳朵进侧板夹层、内壁插脚插底板）；头卡由后板翻过来、一路贴到底并插脚；"
                    "结构对照 reference 170311，尺寸关系自定、未逐点校准【初值】",
                ],
            },
        )
        return dl


def _to_shapely(path: Path) -> Polygon:
    from pacxio.geometry import path_to_polygon

    return path_to_polygon(path)


def _shape_path(shape: Any) -> Path:
    """shapely 多边形（只有直线）→ 闭合 Path。"""
    coords = list(shapely.normalize(shape).exterior.coords)[:-1]
    pts = [Vec(float(x), float(y)) for x, y in coords]
    segs = [Segment(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    return Path([s for s in segs if s.length() > 1e-9], closed=True)
