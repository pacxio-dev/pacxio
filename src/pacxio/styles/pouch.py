"""软包装（C5，）：平面就是印刷排版——正、背（背封袋还有两半背面和侧风琴）并排，
封口 / 拉链 / 撕口 / 挂孔位置记在 `Dieline.keep_clear`（不放文字和条码）。

- `pouch_standup` 自立袋：正 | 背两片（中间一刀分开），四周封边，底部弧形底封线（底褶焊上去的地方）；
  可选拉链、撕口、挂孔、圆角。
- `sachet_3side` 三边封小袋：正 | 背两片，四周封边（顶边灌装后再封）；可选撕口、挂孔、圆角。
- `pouch_backseal` 背封枕式袋：一整片 [中缝封 | 背左半 | (侧风琴) | 正 | (侧风琴) | 背右半 | 中缝封]，上下封；
  折边画成压线（膜上没有压痕，只是告诉制袋厂折在哪）。
- `pouch_flat_bottom` 八边封平底袋：袋身同带风琴的背封袋 [背左半 | 左风琴 | 正 | 右风琴 | 背右半]（风琴面宽 = 底深，
  中间对折），右边另一片底（reference 模板没画底，这里补上，单独一刀分开）；顶封、底边封、四条竖角封，
  可选拉链、排气阀、撕口。

几何参照 reference 参数化软袋模板（网页读 SVG，未下载）：封边 W ≤ 90 → 5、否则 7；自立袋底褶 G = 0.3·W − 10；
拉链带离顶 13–25；背封中缝 9、上下封 12。全部【初值】，规格书写"请制袋厂确认"。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import LineString, Polygon
from shapely.geometry import box as shp_box
from shapely.geometry.base import BaseGeometry
from shapely.ops import split as shp_split

from pacxio.geometry import Affine, Path, Segment, Vec, outline_shape, path_to_polygon
from pacxio.geometry.arcfit import ellipse as fit_ellipse
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, KeepClear, PanelRole
from pacxio.model.profile import Material, MaterialType, PouchRules, Profile
from pacxio.styles.components.svg_curves import curve_contour
from pacxio.styles.corrugated import assemble, crease

MIN_SIZE_MM = 30.0  # 袋子最小边【初值】
ROUND_HOLE_D = 6.0  # 圆挂孔直径【初值】
EURO_W, EURO_H = 30.0, 8.0  # 欧式挂孔（跑道形）【初值】
HangHole = Literal["none", "round", "euro"]
TearCut = Literal["mark", "v", "slit"]


def pouch_rules(profile: Profile | None) -> PouchRules:
    return (profile.pouches if profile is not None else None) or PouchRules()


def seal_for(width: float, rules: PouchRules) -> float:
    return rules.seal_small_mm if width <= rules.seal_break_w else rules.seal_large_mm


class _PouchBase(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    L: float = Field(gt=0, description="Pouch width mm (laid flat, seals included)")
    W: float = Field(gt=0, description="Pouch height mm (laid flat, seals included)")
    H: float = Field(
        default=0.0, ge=0, description="Bottom gusset / side gusset depth mm; 0 = derived from pouch width (stand-up) or none (back seal)"
    )
    dim_basis: Literal["inner"] = "inner"
    seal: float | None = Field(default=None, gt=0, description="Seal width; if not given, 5 / 7 by pouch width (provisional)")
    tear_notch: bool = Field(default=True, description="Tear notch (small notch position on both side seals, punched by the converter)")
    tear_cut: TearCut = Field(
        default="mark",
        description="Tear notch style: mark = position only (punched by the converter) / v = V-notch / slit = straight slit",
    )
    hang_hole: HangHole = "none"
    corner_radius: float = Field(default=0.0, ge=0, description="Rounded corners on all four corners")


WindowShape = Literal["none", "rect", "oval"]
Spout = Literal["none", "top", "corner"]
Supply = Literal["pouch", "roll"]
Seam = Literal["back", "corner"]


class _WindowOpts(BaseModel):
    """透明窗：正面上一块不印白墨、不印底色的区域（看得到里面）；只标位置，图文可以印在上面。"""

    window: WindowShape = Field(default="none", description="Clear window rect / oval (front, position marked)")
    window_w: float | None = Field(default=None, gt=0, description="Window width; default 0.45 × pouch width")
    window_h: float | None = Field(default=None, gt=0, description="Window height; default 0.3 × pouch height")
    window_y: float | None = Field(default=None, gt=0, description="Window centre above the pouch bottom; default 0.42 × pouch height")


class StandupParams(_PouchBase, _WindowOpts):
    zipper: bool | None = Field(
        default=None, description="Zipper (13–25 mm from the top, provisional); not given = none with a spout, otherwise yes"
    )
    spout: Spout = Field(
        default="none", description="Spout: top = top centre / corner = top-right angled corner (jelly, juice, sauce)"
    )
    spout_d: float | None = Field(default=None, gt=0, description="Spout diameter; default 10 (provisional)")
    handle: bool = Field(default=False, description="Handle hole (punched at the top, large pouches)")


class SachetParams(_PouchBase, _WindowOpts):
    shape: Literal["rect", "custom"] = Field(
        default="rect", description="custom shape: outline_svg gives the outline"
    )
    outline_svg: str | None = Field(
        default=None, description="Custom outline: one closed outline as SVG, Béziers become arcs; as seen from the front"
    )
    supply: Supply = Field(
        default="pouch", description="pouch = finished pouches / roll = roll stock (formed on a packing machine, with eye marks)"
    )


class FlatBottomParams(_PouchBase, _WindowOpts):
    L: float = Field(gt=0, description="Front width mm")
    W: float = Field(gt=0, description="Pouch height mm (top seal included)")
    H: float = Field(
        default=0.0, ge=0, description="Bottom depth mm (= unfolded side gusset width); 0 = 0.4 × front width"
    )
    seal: float | None = Field(default=None, gt=0, description="Top seal width; default 10 (provisional)")
    zipper: bool = Field(default=True, description="Zipper (13–25 mm from the top, provisional)")
    valve: bool = Field(default=False, description="One-way degassing valve (coffee, fresh tea): centred at the upper front, position marked")
    corner_seals: bool = Field(default=True, description="Four vertical corner seals (quad seal; the pouch stands up straighter)")
    fin: float | None = Field(default=None, gt=0, description="Back centre seal width (each side); default 10 (provisional)")
    bottom_seal: float | None = Field(
        default=None, gt=0, description="Bottom panel four-side seal width; default 6 (provisional)"
    )
    seam: Seam = Field(
        default="back", description="Seam: back = centre back seal / corner = at the front left corner (true eight-side seal, plain back)"
    )
    handle: bool = Field(default=False, description="Handle hole (punched at the top, large pouches; needs seam=corner)")


class BacksealParams(_PouchBase, _WindowOpts):
    L: float = Field(gt=0, description="Front width mm")
    supply: Supply = Field(
        default="pouch", description="pouch = finished pouches / roll = roll stock (formed on a packing machine, with eye marks)"
    )
    seam: Seam = Field(
        default="back", description="Seam: back = centre back seal / corner = at the front left corner (quad seal, needs side gussets)"
    )
    handle: bool = Field(default=False, description="Handle hole (punched at the top, large pouches; needs seam=corner)")
    fin: float | None = Field(default=None, gt=0, description="Centre seal width (each side); default 9 (provisional)")
    end_seal: float | None = Field(default=None, gt=0, description="Top and bottom seal width; default 12 (provisional)")


def _rect(x0: float, y0: float, x1: float, y1: float) -> Path:
    return Path.from_points([Vec(x0, y0), Vec(x1, y0), Vec(x1, y1), Vec(x0, y1)], closed=True)


def _obround(cx: float, cy: float, w: float, h: float) -> Path:
    """跑道形（两头半圆），D4：两段直线 + 两段半圆。"""
    r = h / 2
    a, b = Vec(cx - w / 2 + r, cy - r), Vec(cx + w / 2 - r, cy - r)
    c, d = Vec(cx + w / 2 - r, cy + r), Vec(cx - w / 2 + r, cy + r)
    return Path([Segment(a, b), Segment(b, c, 1.0), Segment(c, d), Segment(d, a, 1.0)], closed=True)


def _circle(cx: float, cy: float, r: float) -> Path:
    """圆：四段 90° 圆弧（两段半圆端点相同，会被 S01 当成重复线）。"""
    pts = [Vec(cx + r, cy), Vec(cx, cy + r), Vec(cx - r, cy), Vec(cx, cy - r)]
    b = math.tan(math.pi / 8)
    return Path([Segment(pts[i], pts[(i + 1) % 4], b) for i in range(4)], closed=True)


def _outline(width: float, height: float, r: float) -> Path:
    return Path.rounded_rect(0, 0, width, height, r) if r else _rect(0, 0, width, height)


def _notched(path: Path, notches: list[tuple[float, float]], r: float) -> Path:
    """在外轮廓的竖直边上开 V 形撕口：notches 是 (x, y)，缺口深 r、高 2r，开口朝外。"""
    segs = list(path.segments)
    for nx, ny in notches:
        for k, sg in enumerate(segs):
            a, b = sg.start, sg.end
            if sg.is_arc or abs(a.x - nx) > 1e-6 or abs(b.x - nx) > 1e-6:
                continue
            if not min(a.y, b.y) + r < ny < max(a.y, b.y) - r:
                continue
            up = 1.0 if b.y > a.y else -1.0
            inward = -up  # 逆时针绕：往上走的边（右边）里面在左边
            p1, tip, p2 = Vec(nx, ny - up * r), Vec(nx + inward * r, ny), Vec(nx, ny + up * r)
            segs[k : k + 1] = [Segment(a, p1), Segment(p1, tip), Segment(tip, p2), Segment(p2, b)]
            break
        else:
            raise ValueError(
                translate(
                    "The tear notch at ({x:g}, {y:g}) is outside a straight edge of the outline",
                    x=nx,
                    y=ny,
                )
            )
    return Path(segs, closed=True)


def _diamond(cx: float, cy: float, r: float) -> list[Segment]:
    """两层膜折边上的 V 形撕口展开成菱形（内切口）。"""
    pts = [Vec(cx, cy - r), Vec(cx + r, cy), Vec(cx, cy + r), Vec(cx - r, cy)]
    return [Segment(pts[i], pts[(i + 1) % 4]) for i in range(4)]


def _tear_cuts(p: _PouchBase, edges: list[float], folds: list[float], ty: float, r: float,
               outline: Path, split: list[float] | None = None) -> tuple[Path, list[Edge]]:  # fmt: skip
    """撕口画成刀口（tear_cut = v / slit）。edges：外轮廓左右两条竖边的 x；folds：折边 / 分切线上的撕口 x
    （两层一起冲，展开是菱形或横穿的一字）。返回 (外轮廓, 内切口)。"""
    if not p.tear_notch or p.tear_cut == "mark":
        return outline, []
    cuts: list[Edge] = []
    if p.tear_cut == "v":
        outline = _notched(outline, [(x, ty) for x in edges], r)
        for i, fx in enumerate(folds):
            cuts.append(Edge(id=f"cut_tear_{i}", type=EdgeType.CUT, segments=_diamond(fx, ty, r)))
        return outline, cuts
    segs = []
    if edges:
        left, right = min(edges), max(edges)
        segs += [
            Segment(Vec(left, ty), Vec(left + r, ty)),
            Segment(Vec(right - r, ty), Vec(right, ty)),
        ]
    segs += [Segment(Vec(fx - r, ty), Vec(fx + r, ty)) for fx in folds]
    cuts.append(Edge(id="cut_tear_slits", type=EdgeType.CUT, segments=segs))
    return outline, cuts


class _PouchStyle:
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"film_laminate"})
    default_print_method: ClassVar[str] = (
        "digital"  # DTC 小批量常用数码；大批量改 --print-method gravure
    )
    refs: ClassVar[dict[str, str]] = {
        "design": "参照 reference 参数化软袋模板（网页读刀版，未下载）；无标准编号"
    }
    version: ClassVar[str] = "0.1.0"
    name_zh: ClassVar[str]
    id: ClassVar[str]

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return []

    def _common_issues(self, p: _PouchBase, material: Material, face_w: float) -> list[Issue]:
        out = []
        if material.type not in self.material_types:
            out.append(Issue(id="S03", message=translate("Pouches require laminate film. The current material type is {material}.", material=material.type),
                             fix=translate("Use --material pouch_pet_vmpet_pe, pouch_kraft_pe, pouch_pet_pe_clear, or another pouch film preset")))  # fmt: skip
        if min(face_w, p.W) < MIN_SIZE_MM:
            out.append(Issue(id="S03", message=translate("Pouch {width:g} × {height:g} is too small (provisional minimum side length {minimum:g})", width=face_w, height=p.W, minimum=MIN_SIZE_MM),
                             fix=translate("Set the dimensions to at least {minimum:g}", minimum=MIN_SIZE_MM)))  # fmt: skip
        if p.corner_radius > min(face_w, p.W) / 4:
            out.append(
                Issue(
                    id="S03",
                    message=translate("The corner radius is too large"),
                    fix=translate("Set corner_radius to at most one quarter of the shorter side"),
                )
            )
        return out

    @staticmethod
    def _tear_issue(p: _PouchBase, rules: PouchRules, tear_from_top: float) -> list[Issue]:
        if (
            p.tear_notch
            and p.tear_cut != "mark"
            and p.corner_radius + rules.tear_size_mm >= tear_from_top
        ):
            return [Issue(id="PO06", message=translate("The tear notch falls on a rounded corner"),
                          fix=translate("Reduce the radius or use --set tear_cut=mark to mark the position"))]  # fmt: skip
        return []

    def _hang(
        self, p: _PouchBase, cx: float, top: float, seal: float, below: float
    ) -> tuple[Path | None, list[Issue]]:
        """挂孔：在顶封 / 顶部空白区居中；below 是它下面不能碰到的线（拉链带上沿或安全线）。"""
        if p.hang_hole == "none":
            return None, []
        h = ROUND_HOLE_D if p.hang_hole == "round" else EURO_H
        cy = top - max(seal, (top - below) / 2)
        path = (
            _circle(cx, cy, h / 2) if p.hang_hole == "round" else _obround(cx, cy, EURO_W, EURO_H)
        )
        if cy - h / 2 < below + 1.0:
            return path, [Issue(id="PO06", message=translate("The hang hole does not fit. Only {available:g} mm is available between the top and the zipper band.", available=top - below),
                                fix=translate("Remove the zipper or hang hole, or use a round hole"))]  # fmt: skip
        return path, []


# ---------------------------------------------------------------------------
# 选项共用：透明窗、手提孔、光标、异形封边
# ---------------------------------------------------------------------------


def _poly_path(poly: Polygon) -> Path:
    pts = [Vec(x, y) for x, y in list(poly.exterior.coords)[:-1]]
    return Path.from_points(pts, closed=True)


def _pieces(g: object, cx: float) -> list[Path]:
    """（可能带洞的）区域 → 不带洞的几块（KeepClear 只认单条闭合轮廓）：在 x = cx 竖切一刀。"""
    polys = [q for q in getattr(g, "geoms", [g]) if isinstance(q, Polygon) and not q.is_empty]
    out: list[Path] = []
    for poly in polys:
        if not poly.interiors:
            out.append(_poly_path(poly))
            continue
        x0, y0, x1, y1 = poly.bounds
        for part in shp_split(poly, LineString([(cx, y0 - 1), (cx, y1 + 1)])).geoms:
            if isinstance(part, Polygon) and part.area > 1e-6:
                out += (
                    _pieces(part, (part.bounds[0] + part.bounds[2]) / 2)
                    if part.interiors
                    else [_poly_path(part)]
                )
    return out


def _clip_zones(keep: list[KeepClear], outline: Path) -> list[KeepClear]:
    """伸出外轮廓的区（V 形撕口把封边切掉一角）裁到轮廓里；本来就在里面的保持原样（圆弧不丢）。"""
    whole = outline_shape(outline.segments)
    shape = whole.buffer(1e-6)
    out = []
    for k in keep:
        poly = path_to_polygon(k.path)
        if shape.covers(poly):
            out.append(k)
            continue
        part = poly.intersection(whole)
        parts = [
            q for q in getattr(part, "geoms", [part]) if isinstance(q, Polygon) and q.area > 1e-6
        ]
        if parts:
            out.append(k.model_copy(update={"path": _poly_path(max(parts, key=lambda q: q.area))}))
    return out


def window_size(p: _WindowOpts, w: float, h: float) -> tuple[float, float, float]:
    return (
        p.window_w or round(0.45 * w, 1),
        p.window_h or round(0.3 * h, 1),
        p.window_y or round(0.42 * h, 1),
    )


def _window(p: _WindowOpts, fx0: float, w: float, h: float, rules: PouchRules) -> KeepClear | None:
    if p.window == "none":
        return None
    ww, wh, wy = window_size(p, w, h)
    cx = fx0 + w / 2
    if p.window == "rect":
        path = _rect(cx - ww / 2, wy - wh / 2, cx + ww / 2, wy + wh / 2)
    else:
        path = Path(fit_ellipse(cx, wy, ww / 2, wh / 2, rules.shape_tol_mm).segments, closed=True)
    return KeepClear(id="window", kind="window", path=path, panel="front")


def _window_issues(
    p: _WindowOpts, w: float, h: float, rules: PouchRules, clear: BaseGeometry
) -> list[Issue]:
    """窗要在正面的内容区里（clear：正面去掉封口 / 拉链等以后的区域，面局部坐标）。"""
    if p.window == "none":
        return []
    ww, wh, wy = window_size(p, w, h)
    if min(ww, wh) < rules.window_min_mm:
        return [Issue(id="S03", message=translate("Clear window {width:g} × {height:g} is too small (provisional minimum side length {minimum:g})", width=ww, height=wh, minimum=rules.window_min_mm),
                      fix=translate("Increase window_w or window_h"))]  # fmt: skip
    win = shp_box(w / 2 - ww / 2, wy - wh / 2, w / 2 + ww / 2, wy + wh / 2)
    if not clear.buffer(1e-6).covers(win):
        return [Issue(id="PO06", message=translate("The clear window overlaps a seal area such as the edge seal, zipper, or bottom gusset"),
                      fix=translate("Adjust window_w, window_h, or window_y to keep the window within the front content area"))]  # fmt: skip
    return []


def handle_slot(w: float, h: float, rules: PouchRules) -> tuple[float, float, float, float]:
    """手提孔（面局部坐标）：(中心 x, 中心 y, 宽, 高)。"""
    hw = min(rules.handle_w_k * w, rules.handle_max_w_mm)
    return w / 2, h - rules.handle_top_mm - rules.handle_h_mm / 2, hw, rules.handle_h_mm


def handle_bottom(h: float, rules: PouchRules) -> float:
    """手提封的下沿（面局部 y）：拉链等要在它下面。"""
    return h - rules.handle_top_mm - rules.handle_h_mm - rules.handle_margin_mm


def _handle(
    face: str, fx0: float, w: float, h: float, s: float, rules: PouchRules
) -> tuple[Edge, KeepClear]:
    cx, cy, hw, hh = handle_slot(w, h, rules)
    hole = _obround(fx0 + cx, cy, hw, hh)
    cut = Edge(
        id=f"cut_hang_handle_{face}", type=EdgeType.CUT, segments=hole.segments, panels=[face]
    )
    zone = KeepClear(id=f"{face}_handle", kind="handle", path=_rect(fx0 + s, handle_bottom(h, rules), fx0 + w - s, h),
                     panel=face)  # fmt: skip
    return cut, zone


def _eye_mark(x: float, y_mid: float, h: float, rules: PouchRules) -> tuple[list[float], KeepClear]:
    """卷膜的光标（电眼色标）：x 是光标左边，y_mid 是它在横封里的中心；电眼通道沿走膜方向（整个袋高）。"""
    roll = rules.roll
    mark = [x, y_mid - roll.eye_h_mm / 2, roll.eye_w_mm, roll.eye_h_mm]
    lane = _rect(max(0.0, x - roll.eye_lane_mm), 0, x + roll.eye_w_mm + roll.eye_lane_mm, h)
    return mark, KeepClear(id="eye_lane", kind="eyemark", path=lane)


# ---------------------------------------------------------------------------
# 自立袋
# ---------------------------------------------------------------------------


class PouchStandupStyle(_PouchStyle):
    """自立袋（Doypack）：正 | 背两片，四周封边，底部弧形底封线；可选拉链、撕口、挂孔、吸嘴、手提孔、透明窗。"""

    id: ClassVar[str] = "pouch_standup"
    name_zh: ClassVar[str] = "自立袋（三边封 + 底褶）"

    def param_schema(self) -> type[BaseModel]:
        return StandupParams

    @staticmethod
    def dims(p: StandupParams, rules: PouchRules) -> dict[str, float]:
        s = p.seal or seal_for(p.L, rules)
        g = p.H or max(s + 5, rules.gusset_k * p.L + rules.gusset_b)
        zip_top = rules.zipper_top_mm
        if p.handle:  # 拉链移到手提封下面
            zip_top = max(zip_top, p.W - handle_bottom(p.W, rules) + 3)
        spout_d = p.spout_d or rules.spout_d_mm
        return {"seal": s, "gusset": g, "zip_top": zip_top, "zip_w": rules.zipper_w_mm,
                "spout_d": spout_d, "spout_w": rules.spout_base_k * spout_d,
                "spout_c": rules.spout_corner_mm if p.spout == "corner" else 0.0}  # fmt: skip

    @staticmethod
    def has_zipper(p: StandupParams) -> bool:
        return p.zipper if p.zipper is not None else p.spout == "none"

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: StandupParams = params
        out = self._common_issues(p, material, p.L)
        if out:
            return out
        rules = pouch_rules(profile)
        d = self.dims(p, rules)
        zipper = self.has_zipper(p)
        if d["gusset"] <= d["seal"] + 2 or d["gusset"] > 0.4 * p.W:
            out.append(Issue(id="S03", message=translate("Bottom gusset depth {depth:g} must be between the seal width plus 2 and 0.4 × pouch height", depth=d['gusset']),
                             fix=translate("Adjust --H (bottom gusset depth). Set it to 0 to derive it from pouch width.")))  # fmt: skip
        out += self._tear_issue(p, rules, rules.tear_from_top_mm)
        if zipper and d["zip_top"] + d["zip_w"] + 20 > p.W - d["gusset"]:
            out.append(
                Issue(
                    id="PO06",
                    message=translate(
                        "The pouch is too short to leave a usable area between the zipper band and bottom gusset"
                    ),
                    fix=translate("Increase pouch height or remove the zipper"),
                )
            )
        if p.tear_notch and zipper and rules.tear_from_top_mm + rules.tear_size_mm > d["zip_top"]:
            out.append(
                Issue(
                    id="PO06",
                    message=translate("The tear notch falls on the zipper band"),
                    fix=translate(
                        "Set tear_from_top_mm in the profile to place the notch above the zipper band"
                    ),
                )
            )
        if p.spout != "none":
            if zipper:
                out.append(
                    Issue(
                        id="PO06",
                        message=translate("Spouted pouches cannot have a zipper"),
                        fix=translate(
                            "Use --set zipper=false. With a spout, the zipper defaults to off."
                        ),
                    )
                )
            if p.hang_hole != "none" or p.handle:
                out.append(
                    Issue(
                        id="PO06",
                        message=translate(
                            "The spout and hang or handle hole cannot both fit at the pouch opening"
                        ),
                        fix=translate("Remove the hang or handle hole"),
                    )
                )
            if p.spout == "corner":
                if d["spout_w"] + 4 > d["spout_c"] * math.sqrt(2):
                    out.append(Issue(id="PO06", message=translate("The angled cut {cut:g} cannot fit a spout base of diameter {diameter:g}", cut=d['spout_c'], diameter=d['spout_d']),
                                     fix=translate("Increase spout_corner_mm in the profile or reduce spout_d")))  # fmt: skip
                if p.corner_radius:
                    out.append(
                        Issue(
                            id="S03",
                            message=translate("An angled corner spout requires square corners"),
                            fix="--set corner_radius=0",
                        )
                    )
            elif d["spout_w"] + 2 * d["seal"] + 10 > p.L:
                out.append(
                    Issue(
                        id="PO06",
                        message=translate("The pouch is too narrow for the spout base"),
                        fix=translate("Increase pouch width or reduce spout_d"),
                    )
                )
        if p.handle and p.hang_hole != "none":
            out.append(
                Issue(
                    id="PO06",
                    message=translate("The handle hole and hang hole both occupy the top seal"),
                    fix=translate("Choose one of the two"),
                )
            )
        if (
            p.handle
            and handle_bottom(p.W, rules) - (d["zip_w"] if zipper else 0) - 30 < d["gusset"]
        ):
            out.append(
                Issue(
                    id="PO06",
                    message=translate(
                        "The pouch is too short to leave a usable area below the handle seal"
                    ),
                    fix=translate("Increase pouch height or remove the handle hole"),
                )
            )
        if p.hang_hole != "none" and not p.handle:
            top_free = d["zip_top"] if zipper else p.W
            _, issues = self._hang(
                p, p.L / 2, p.W, d["seal"], p.W - top_free if zipper else p.W - 25
            )
            out += issues
        out += _window_issues(p, p.L, p.W, rules, _content_area(p.L, p.W, d["seal"], d["gusset"],
                                                               d["zip_top"] + d["zip_w"] if zipper else d["seal"]))  # fmt: skip
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        return {"front": (params.L, params.W), "back": (params.L, params.W)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: StandupParams = params
        rules = pouch_rules(profile)
        d = self.dims(p, rules)
        return _two_face(
            self, p, material, profile, rules, d, gusset=d["gusset"], zipper=self.has_zipper(p)
        )


def _content_area(w: float, h: float, s: float, bottom: float, top: float) -> Polygon:
    """面局部坐标里能放内容的区域（两侧封边、底部 bottom、顶上 top 以内）。"""
    return shp_box(s, bottom, w - s, h - top)


# ---------------------------------------------------------------------------
# 三边封小袋
# ---------------------------------------------------------------------------


class SachetStyle(_PouchStyle):
    """三边封小袋：正 | 背两片，四周封边（顶边灌装后封）；可选撕口、挂孔、透明窗、异形、卷膜。"""

    id: ClassVar[str] = "sachet_3side"
    name_zh: ClassVar[str] = "三边封小袋"

    def param_schema(self) -> type[BaseModel]:
        return SachetParams

    @staticmethod
    def contour(p: SachetParams, rules: PouchRules) -> tuple[Path, float]:
        if not p.outline_svg:
            raise ValueError(
                translate(
                    "A custom shape (shape=custom) requires outline_svg with a single closed outline"
                )
            )
        return curve_contour(p.outline_svg, p.L, p.W, rules.shape_tol_mm)

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: SachetParams = params
        out = self._common_issues(p, material, p.L)
        if out:
            return out
        rules = pouch_rules(profile)
        if p.H:
            out.append(
                Issue(
                    id="S03",
                    message=translate("A three-side seal sachet has no bottom gusset"),
                    fix=translate("Set --H to 0, or use pouch_standup for a bottom gusset"),
                )
            )
        s = p.seal or seal_for(p.L, rules)
        out += self._tear_issue(p, rules, rules.tear_from_top_mm)
        if p.shape == "custom":
            try:
                contour, err = self.contour(p, rules)
                if err > rules.shape_tol_mm * 1.0001:
                    raise ValueError(
                        translate(
                            "The custom outline-to-arc fitting error {error:.3f} mm exceeds {tolerance:g} mm",
                            error=err,
                            tolerance=rules.shape_tol_mm,
                        )
                    )
                if path_to_polygon(contour).buffer(-s).area < 100:
                    raise ValueError(
                        translate(
                            "The custom outline is too narrow to leave a usable area inside the seals"
                        )
                    )
            except ValueError as e:
                out.append(
                    Issue(id="S03", message=str(e), fix=translate("Check outline_svg, L, and W"))
                )
            if p.hang_hole != "none" or p.corner_radius or p.tear_cut != "mark":
                out.append(Issue(id="S03", message=translate("Custom sachets allow a tear-position mark. Hang holes and rounded corners are unavailable."),
                                 fix="--set hang_hole=none corner_radius=0 tear_cut=mark"))  # fmt: skip
        if p.hang_hole != "none" and p.shape == "rect":
            _, issues = self._hang(p, p.L / 2, p.W, s, p.W - 25)
            out += issues
        if p.shape == "rect":
            out += _window_issues(p, p.L, p.W, rules, _content_area(p.L, p.W, s, s, s))
        elif p.window != "none" and not out:
            contour, _ = self.contour(p, rules)
            out += _window_issues(p, p.L, p.W, rules, path_to_polygon(contour).buffer(-s))
        if p.supply == "roll" and s < rules.roll.eye_h_mm:
            out.append(Issue(id="PO06", message=translate("Edge seal {seal:g} is too narrow for the registration mark ({mark_height:g} along the film direction)", seal=s, mark_height=rules.roll.eye_h_mm),
                             fix=translate("Increase the seal width with --set seal=… or adjust roll.eye_h_mm in the profile")))  # fmt: skip
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        return {"front": (params.L, params.W), "back": (params.L, params.W)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: SachetParams = params
        rules = pouch_rules(profile)
        d = {"seal": p.seal or seal_for(p.L, rules), "gusset": 0.0}
        return _two_face(self, p, material, profile, rules, d, gusset=0.0, zipper=False)


def _two_face(style: _PouchStyle, p: _PouchBase, material: Material, profile: Profile, rules: PouchRules,
              d: dict[str, float], gusset: float, zipper: bool) -> Dieline:  # fmt: skip
    """正 | 背两片并排（中间一刀分开；卷膜是折边）；封口区、拉链、撕口、挂孔、吸嘴、手提孔、窗、异形、光标。

    背面是正面左右镜像排的（从背后看）：正面右边挨着背面左边。"""
    w, h, s = p.L, p.W, d["seal"]
    spout = getattr(p, "spout", "none")
    handle = bool(getattr(p, "handle", False))
    roll = getattr(p, "supply", "pouch") == "roll"
    shaped = getattr(p, "shape", "rect") == "custom"
    c = d.get("spout_c", 0.0)
    polys = {
        "front": (PanelRole.BODY, _rect(0, 0, w, h)),
        "back": (PanelRole.BODY, _rect(w, 0, 2 * w, h)),
    }
    creases: list[Edge] = []
    inner: list[Edge] = []
    if roll:  # 卷膜：一整片对折，正背之间是折边（三边封名副其实）
        creases.append(crease("fold_front_back", (w, 0), (w, h), ["front", "back"]))
    else:
        inner.append(Edge(id="cut_split", type=EdgeType.CUT, segments=[Segment(Vec(w, 0), Vec(w, h - c))],
                          panels=["front", "back"]))  # fmt: skip
    if c:  # 斜角吸嘴：正面右上角、背面左上角（都在中缝处）切掉
        outline = Path.from_points([Vec(0, 0), Vec(2 * w, 0), Vec(2 * w, h), Vec(w + c, h), Vec(w, h - c),
                                    Vec(w - c, h), Vec(0, h)], closed=True)  # fmt: skip
    else:
        outline = _outline(2 * w, h, p.corner_radius)
    keep: list[KeepClear] = []
    z1 = h - d.get("zip_top", rules.zipper_top_mm)
    z0 = z1 - d.get("zip_w", rules.zipper_w_mm)
    meta_extra: dict[str, Any] = {"zipper": zipper, "layout_faces": ["front", "back"]}
    inner_s = 0.0 if roll else s  # 中缝那侧的封边（卷膜是折边，没有）
    contour = SachetStyle.contour(p, rules)[0] if shaped else None  # type: ignore[arg-type]
    for face, x0 in (("front", 0.0), ("back", w)):
        mirror = face == "back"

        def X(xl: float, x0: float = x0, mirror: bool = mirror) -> float:  # 面局部 x → 刀版 x
            return x0 + (w - xl if mirror else xl)

        def R(a: float, b: float, y0: float, y1: float) -> Path:  # 面局部 [a, b] × [y0, y1]
            xa, xb = sorted((X(a), X(b)))
            return _rect(xa, y0, xb, y1)

        if contour is not None:  # 异形：沿外形的封边环 + 外面裁掉的废边
            t = (
                Affine.translate(2 * w, 0) @ Affine.scale(-1, 1)
                if mirror
                else Affine.translate(0, 0)
            )
            shape = contour.transformed(t)
            inner.append(
                Edge(
                    id=f"cut_shape_{face}",
                    type=EdgeType.CUT,
                    segments=shape.segments,
                    panels=[face],
                )
            )
            poly = path_to_polygon(shape)
            cx = x0 + w / 2
            ring = poly.difference(poly.buffer(-s, join_style="mitre"))
            keep += [KeepClear(id=f"{face}_seal_{i}", kind="seal", path=q, panel=face)
                     for i, q in enumerate(_pieces(ring, cx))]  # fmt: skip
            waste = shp_box(x0, 0, x0 + w, h).difference(poly)
            keep += [KeepClear(id=f"{face}_waste_{i}", kind="waste", path=q, panel=face)
                     for i, q in enumerate(_pieces(waste, cx)) if path_to_polygon(q).area > 0.5]  # fmt: skip
        else:
            # 外侧封边（正面左、背面右）；中缝那侧（正面右、背面左）卷膜时是折边，不用封
            keep.append(
                KeepClear(id=f"{face}_seal_out", kind="seal", path=R(0, s, 0, h), panel=face)
            )
            if not roll:
                keep.append(
                    KeepClear(
                        id=f"{face}_seal_in", kind="seal", path=R(w - s, w, 0, h - c), panel=face
                    )
                )
            keep.append(KeepClear(id=f"{face}_seal_top", kind="seal", path=R(s, w - inner_s - max(c - s, 0.0), h - s, h),
                                  panel=face))  # fmt: skip
            if c:  # 斜切口上的封边带
                pts = [(w - c, h), (w, h - c), (w - s / math.sqrt(2), h - c - s / math.sqrt(2)),
                       (w - c - s / math.sqrt(2), h - s / math.sqrt(2))]  # fmt: skip
                keep.append(KeepClear(id=f"{face}_seal_spout", kind="seal", panel=face,
                                      path=Path.from_points([Vec(X(x), y) for x, y in pts], closed=True)))  # fmt: skip
            if gusset > 0:
                keep.append(
                    KeepClear(
                        id=f"{face}_gusset",
                        kind="gusset",
                        path=_gusset_zone(x0, w, s, gusset),
                        panel=face,
                    )
                )
            else:
                keep.append(
                    KeepClear(
                        id=f"{face}_seal_bottom",
                        kind="seal",
                        path=R(s, w - inner_s, 0, s),
                        panel=face,
                    )
                )
        below = h - 25.0
        if zipper:
            keep.append(
                KeepClear(id=f"{face}_zipper", kind="zipper", path=R(s, w - s, z0, z1), panel=face)
            )
            below = z1
        if p.tear_notch:
            ty, r = h - rules.tear_from_top_mm, rules.tear_size_mm
            keep.append(
                KeepClear(
                    id=f"{face}_tear_out", kind="tear", path=R(0, s + r, ty - r, ty + r), panel=face
                )
            )
            if not c and not roll:  # 卷膜的折边那侧没有封边、不开撕口
                keep.append(
                    KeepClear(
                        id=f"{face}_tear_in",
                        kind="tear",
                        path=R(w - s - r, w, ty - r, ty + r),
                        panel=face,
                    )
                )
        if spout == "top":
            bw, bh = d["spout_w"] + 4, rules.spout_base_h_mm + 4
            keep.append(
                KeepClear(
                    id=f"{face}_spout",
                    kind="spout",
                    path=R(w / 2 - bw / 2, w / 2 + bw / 2, h - bh, h),
                    panel=face,
                )
            )
        elif spout == "corner":
            bw, bh = d["spout_w"] + 4, rules.spout_base_h_mm + 4
            mx, my, k = (
                w - c / 2,
                h - c / 2,
                1 / math.sqrt(2),
            )  # 斜切口中点；沿切口 (1, −1)，往里 (−1, −1)
            seg = [Vec(X(x), y) for x, y in ((mx - k * bw / 2, my + k * bw / 2), (mx + k * bw / 2, my - k * bw / 2),
                                             (mx + k * bw / 2 - k * bh, my - k * bw / 2 - k * bh),
                                             (mx - k * bw / 2 - k * bh, my + k * bw / 2 - k * bh))]  # fmt: skip
            keep.append(
                KeepClear(
                    id=f"{face}_spout",
                    kind="spout",
                    path=Path.from_points(seg, closed=True),
                    panel=face,
                )
            )
        if handle:
            cut, zone = _handle(face, x0, w, h, s, rules)
            inner.append(cut)
            keep.append(zone)
        hole, _ = style._hang(p, x0 + w / 2, h, s, below)
        if hole is not None and not handle:
            inner.append(
                Edge(
                    id=f"cut_hang_{face}", type=EdgeType.CUT, segments=hole.segments, panels=[face]
                )
            )
            x_0, y_0, x_1, y_1 = hole.bbox()
            keep.append(
                KeepClear(
                    id=f"{face}_hang",
                    kind="hang",
                    path=_rect(x_0 - 2, y_0 - 2, x_1 + 2, y_1 + 2),
                    panel=face,
                )
            )
    win = _window(p, 0.0, w, h, rules)  # type: ignore[arg-type]
    if win is not None:
        keep.append(win)
        meta_extra["window"] = getattr(p, "window", "none")
    if spout != "none":
        if spout == "top":
            meta_extra["spout"] = {"kind": "top", "d": d["spout_d"], "at": [w / 2, h], "angle": 0.0}
        else:
            meta_extra["spout"] = {
                "kind": "corner",
                "d": d["spout_d"],
                "at": [w - c / 2, h - c / 2],
                "angle": 45.0,
            }
    if handle:
        meta_extra["handle"] = list(handle_slot(w, h, rules))
    if shaped:
        meta_extra["shape"] = "custom"
    if roll:
        mark, lane = _eye_mark(rules.roll.eye_edge_mm, s / 2, h, rules)
        keep.append(lane)
        meta_extra |= {"supply": "roll", "eye_mark": mark, "eye_cmyk": list(rules.roll.eye_cmyk),
                       "web_width": 2 * w, "repeat": h}  # fmt: skip
    meta = _meta(style, p, d, meta_extra, 2 * w, h)
    outline, tears = _tear_cuts(p, [0.0, 2 * w], [] if (roll or c) else [w], h - rules.tear_from_top_mm,
                                rules.tear_size_mm, outline)  # fmt: skip
    dl = assemble(style_id=style.id, params=p, material=material, profile=profile,
                  outline=outline, polys=polys, creases=creases,
                  inner_cuts=inner + tears, ups={}, provisional=set(), meta=meta)  # fmt: skip
    dl.keep_clear = _clip_zones(keep, outline)
    return dl


def _gusset_zone(x0: float, w: float, s: float, g: float) -> Path:
    """自立袋底部：底封 + 底褶焊上去的区域。上边是从两侧底褶线弯到底封线中点的圆弧（弧高 = G − s，reference）。"""
    c = w - 2 * s
    sag = g - s
    bulge = -2 * sag / c  # 从右往左走、往下凹：顺时针
    pts = [Vec(x0, 0), Vec(x0 + w, 0), Vec(x0 + w, g), Vec(x0 + w - s, g)]
    segs = [Segment(a, b) for a, b in zip(pts, pts[1:], strict=False)]
    segs.append(Segment(Vec(x0 + w - s, g), Vec(x0 + s, g), bulge))
    segs += [Segment(Vec(x0 + s, g), Vec(x0, g)), Segment(Vec(x0, g), Vec(x0, 0))]
    return Path(segs, closed=True)


def _body(w: float, h: float, gw: float, fin: float, seam: str, sides: bool = True
          ) -> tuple[dict[str, tuple[PanelRole, Path]], list[Edge], list[float], float, list[tuple[str, Path]]]:  # fmt: skip
    """带风琴的袋身一整片。seam = back：[背左半 | 左风琴 | 正 | 右风琴 | 背右半]（背中缝）；
    seam = corner：[正 | 右风琴 | 背 | 左风琴]，接缝在正面左角（正面左边、左风琴右边各多一条封边）。
    返回 (面, 折边, 折边 x, 总宽, 接缝封边 [(面, 区)])。"""
    if seam == "corner":
        order = [("front", fin + w), ("side_b", gw), ("back", w), ("side_a", gw + fin)]
    else:
        order = [("back_a", w / 2 + fin)] + ([("side_a", gw)] if sides else []) + [("front", w)]
        order += ([("side_b", gw)] if sides else []) + [("back_b", w / 2 + fin)]
    polys: dict[str, tuple[PanelRole, Path]] = {}
    creases: list[Edge] = []
    folds: list[float] = []
    x, prev = 0.0, None
    for pid, width in order:
        polys[pid] = (PanelRole.BODY, _rect(x, 0, x + width, h))
        if prev is not None:  # 折边：膜上没有压痕，标给制袋厂看
            creases.append(crease(f"fold_{prev}_{pid}", (x, 0), (x, h), [prev, pid]))
            folds.append(x)
        prev, x = pid, x + width
    first, last = order[0][0], order[-1][0]
    fins = [(first, _rect(0, 0, fin, h)), (last, _rect(x - fin, 0, x, h))]
    return polys, creases, folds, x, fins


def _seam_issues(p: Any, gusset: float) -> list[Issue]:
    out = []
    if p.seam == "corner" and gusset <= 0:
        out.append(
            Issue(
                id="S03",
                message=translate("A corner-seam four-side seal pouch requires side gussets"),
                fix=translate("Give --H (side gusset depth) or use --set seam=back"),
            )
        )
    if p.handle and p.seam != "corner":
        out.append(Issue(id="PO06", message=translate("A handle hole would cut through the centre back seam"),
                         fix=translate("Use --set seam=corner for a four- or eight-side seal with an uninterrupted back panel")))  # fmt: skip
    if getattr(p, "supply", "pouch") == "roll" and (p.seam == "corner" or p.handle):
        out.append(Issue(id="S03", message=translate("Roll stock for automatic forming requires a centre back seam and no handle hole"),
                         fix=translate("Use --set seam=back handle=false, or set supply=pouch")))  # fmt: skip
    return out


# ---------------------------------------------------------------------------
# 背封枕式袋（可带侧风琴）
# ---------------------------------------------------------------------------


class PouchBacksealStyle(_PouchStyle):
    """背封枕式袋：一整片，背面在中缝两边各一半；--H > 0 时两侧有风琴（侧风琴背封袋）。"""

    id: ClassVar[str] = "pouch_backseal"
    name_zh: ClassVar[str] = "背封枕式袋（可带侧风琴）"

    def param_schema(self) -> type[BaseModel]:
        return BacksealParams

    @staticmethod
    def dims(p: BacksealParams, rules: PouchRules) -> dict[str, float]:
        return {
            "fin": p.fin or rules.fin_seal_mm,
            "end": p.end_seal or rules.end_seal_mm,
            "gusset": p.H,
        }

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: BacksealParams = params
        out = self._common_issues(p, material, p.L)
        if out:
            return out
        rules = pouch_rules(profile)
        d = self.dims(p, rules)
        out += self._tear_issue(p, rules, d["end"] / 2)
        out += _seam_issues(p, p.H)
        if p.H and p.H > 0.6 * p.L:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "Side gusset depth {depth:g} exceeds 0.6 × the front width", depth=p.H
                    ),
                    fix=translate("Reduce --H"),
                )
            )
        if 2 * d["end"] + 20 > p.W:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "The pouch is too short to leave a usable area between the top and bottom seals"
                    ),
                    fix=translate("Increase pouch height or reduce end_seal"),
                )
            )
        if p.hang_hole != "none":
            if p.handle:
                out.append(
                    Issue(
                        id="PO06",
                        message=translate("The handle hole and hang hole both occupy the top seal"),
                        fix=translate("Choose one of the two"),
                    )
                )
            _, issues = self._hang(p, p.L / 2, p.W, d["end"], p.W - 25)
            out += issues
        if p.handle and handle_bottom(p.W, rules) - 30 < d["end"]:
            out.append(
                Issue(
                    id="PO06",
                    message=translate(
                        "The pouch is too short to leave a usable area below the handle seal"
                    ),
                    fix=translate("Increase pouch height or remove the handle hole"),
                )
            )
        if p.supply == "roll" and d["end"] < rules.roll.eye_h_mm:
            out.append(
                Issue(
                    id="PO06",
                    message=translate(
                        "End seal {seal:g} is too narrow for the registration mark", seal=d["end"]
                    ),
                    fix=translate("Increase end_seal"),
                )
            )
        top = (p.W - handle_bottom(p.W, rules)) if p.handle else d["end"]
        out += _window_issues(p, p.L, p.W, rules, _content_area(p.L, p.W, 0.0, d["end"], top))
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = self.dims(params, pouch_rules(profile))
        w, h, g, fin = params.L, params.W, params.H, d["fin"]
        if params.seam == "corner":
            return {
                "front": (w + fin, h),
                "side_b": (2 * g, h),
                "back": (w, h),
                "side_a": (2 * g + fin, h),
            }
        sizes = {"back_a": (w / 2 + fin, h), "front": (w, h), "back_b": (w / 2 + fin, h)}
        if g:
            sizes |= {"side_a": (2 * g, h), "side_b": (2 * g, h)}
        return sizes

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: BacksealParams = params
        rules = pouch_rules(profile)
        d = self.dims(p, rules)
        fin, end, g, w, h = d["fin"], d["end"], p.H, p.L, p.W
        corner = p.seam == "corner"
        polys, creases, folds, total, fins = _body(w, h, 2 * g, fin, p.seam, sides=bool(g))
        keep = [
            KeepClear(id=f"fin_{i}", kind="seal", path=z, panel=pid)
            for i, (pid, z) in enumerate(fins)
        ]
        keep += [
            KeepClear(id="end_top", kind="seal", path=_rect(fin, h - end, total - fin, h)),
            KeepClear(id="end_bottom", kind="seal", path=_rect(fin, 0, total - fin, end)),
        ]
        if corner:  # 四边封：另外三个角也是竖封
            cs = rules.corner_seal_mm
            keep += [KeepClear(id=f"corner_{i}", kind="seal", path=_rect(fx - cs / 2, end, fx + cs / 2, h - end))
                     for i, fx in enumerate(folds) if cs]  # fmt: skip
        inner: list[Edge] = []
        fx0 = polys["front"][1].bbox()[0] + (fin if corner else 0.0)
        hole, _ = self._hang(p, fx0 + w / 2, h, end, h - 25)
        if hole is not None and not p.handle:
            inner.append(
                Edge(
                    id="cut_hang_front", type=EdgeType.CUT, segments=hole.segments, panels=["front"]
                )
            )
            x_0, y_0, x_1, y_1 = hole.bbox()
            keep.append(KeepClear(id="front_hang", kind="hang", path=_rect(x_0 - 2, y_0 - 2, x_1 + 2, y_1 + 2),
                                  panel="front"))  # fmt: skip
        extra: dict[str, Any] = {"layout_faces": list(polys), "seam": p.seam, "front_inset": fin if corner else 0.0,
                                 "side_inset": fin if corner else 0.0}  # fmt: skip
        if p.handle:
            for face, x0 in (("front", fx0), ("back", polys["back"][1].bbox()[0])):
                cut, zone = _handle(face, x0, w, h, end, rules)
                inner.append(cut)
                keep.append(zone)
            extra["handle"] = list(handle_slot(w, h, rules))
        if p.tear_notch:  # 撕口在上封两端（接缝的封边上）
            ty, r = h - end / 2, rules.tear_size_mm
            keep += [KeepClear(id="tear_a", kind="tear", path=_rect(0, ty - r, fin + r, ty + r), panel=fins[0][0]),
                     KeepClear(id="tear_b", kind="tear", path=_rect(total - fin - r, ty - r, total, ty + r), panel=fins[1][0])]  # fmt: skip
        win = _window(p, fx0, w, h, rules)
        if win is not None:
            keep.append(win)
            extra["window"] = p.window
        if p.supply == "roll":
            mark, lane = _eye_mark(fin + rules.roll.eye_edge_mm, end / 2, h, rules)
            keep.append(lane)
            extra |= {"supply": "roll", "eye_mark": mark, "eye_cmyk": list(rules.roll.eye_cmyk),
                      "web_width": total, "repeat": h}  # fmt: skip
        meta = _meta(self, p, {"seal": end, "fin": fin, "gusset": g}, extra, total, h)
        outline, tears = _tear_cuts(p, [0.0, total], [], h - end / 2, rules.tear_size_mm,
                                    _outline(total, h, p.corner_radius))  # fmt: skip
        dl = assemble(style_id=self.id, params=p, material=material, profile=profile,
                      outline=outline, polys=polys, creases=creases,
                      inner_cuts=inner + tears, ups={}, provisional=set(), meta=meta)  # fmt: skip
        dl.keep_clear = _clip_zones(keep, outline)
        return dl


# ---------------------------------------------------------------------------
# 八边封平底袋
# ---------------------------------------------------------------------------


class PouchFlatBottomStyle(_PouchStyle):
    """八边封平底袋（box pouch）：袋身五片 + 一片底；装满后是盒形、立在平底上。"""

    id: ClassVar[str] = "pouch_flat_bottom"
    name_zh: ClassVar[str] = "八边封平底袋"

    def param_schema(self) -> type[BaseModel]:
        return FlatBottomParams

    @staticmethod
    def dims(p: FlatBottomParams, rules: PouchRules) -> dict[str, float]:
        depth = p.H or round(rules.flat_depth_k * p.L, 1)
        top = p.seal or rules.flat_top_seal_mm
        zip_top, zip_w = rules.zipper_top_mm, rules.zipper_w_mm
        if p.handle:  # 拉链移到手提封下面
            zip_top = max(zip_top, p.W - handle_bottom(p.W, rules) + 3)
        below_top = (
            (zip_top + zip_w)
            if p.zipper
            else (p.W - handle_bottom(p.W, rules) if p.handle else top)
        )
        r = rules.valve_d_mm / 2
        return {
            "depth": depth,
            "seal": top,
            "fin": p.fin or rules.flat_fin_mm,
            "bottom_seal": p.bottom_seal or rules.flat_bottom_seal_mm,
            "corner": rules.corner_seal_mm if p.corner_seals else 0.0,
            "zip_top": zip_top,
            "zip_w": zip_w,
            "valve_r": r,
            "valve_y": p.W - below_top - rules.valve_gap_mm - r,
            "top_used": below_top,
        }

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: FlatBottomParams = params
        out = self._common_issues(p, material, p.L)
        if out:
            return out
        rules = pouch_rules(profile)
        d = self.dims(p, rules)
        out += _seam_issues(p, d["depth"])
        if p.hang_hole != "none":
            out.append(
                Issue(
                    id="S03",
                    message=translate("Flat-bottom pouches cannot have hang holes"),
                    fix=translate("Use --set hang_hole=none. Use handle for larger pouches."),
                )
            )
        if p.corner_radius:
            out.append(
                Issue(
                    id="S03",
                    message=translate("Flat-bottom pouches require square corners"),
                    fix="--set corner_radius=0",
                )
            )
        if not (MIN_SIZE_MM / 2 <= d["depth"] <= p.L):
            out.append(Issue(id="S03", message=translate("Bottom depth {depth:g} must be between {minimum:g} and the front width {width:g}", depth=d['depth'], minimum=MIN_SIZE_MM / 2, width=p.L),
                             fix=translate("Adjust --H (bottom depth). Set it to 0 to use 0.4 × the front width.")))  # fmt: skip
        if d["top_used"] + d["depth"] / 2 + d["bottom_seal"] + 30 > p.W:
            out.append(Issue(id="PO06", message=translate("The pouch is too short to leave a usable area between the top seal, zipper, or handle seal, the tucked gusset, and the bottom seal"),
                             fix=translate("Increase pouch height, reduce bottom depth, or remove the zipper or handle hole")))  # fmt: skip
        if p.tear_notch and p.zipper and rules.tear_from_top_mm + rules.tear_size_mm > d["zip_top"]:
            out.append(Issue(id="PO06", message=translate("The tear notch falls on the zipper band"), fix=translate("Set tear_from_top_mm in the profile to place the notch above the zipper band")))  # fmt: skip
        if p.valve:
            r = d["valve_r"]
            if 2 * r + 20 > p.L or d["valve_y"] - r < d["bottom_seal"] + 20:
                out.append(Issue(id="PO06", message=translate("The vent valve (diameter {diameter:g}) does not fit on the front", diameter=2 * r),
                                 fix=translate("Increase pouch size or remove the vent valve")))  # fmt: skip
        clear: BaseGeometry = _content_area(
            p.L, p.W, d["corner"] / 2, d["bottom_seal"], d["top_used"]
        )
        if p.valve:
            clear = clear.difference(shp_box(0, d["valve_y"] - d["valve_r"] - 2, p.L, p.W))
        out += _window_issues(p, p.L, p.W, rules, clear)
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = self.dims(params, pouch_rules(profile))
        w, h, depth, fin = params.L, params.W, d["depth"], d["fin"]
        if params.seam == "corner":
            sizes = {
                "front": (w + fin, h),
                "side_b": (depth, h),
                "back": (w, h),
                "side_a": (depth + fin, h),
            }
        else:
            sizes = {"back_a": (w / 2 + fin, h), "side_a": (depth, h), "front": (w, h), "side_b": (depth, h),
                     "back_b": (w / 2 + fin, h)}  # fmt: skip
        return sizes | {"bottom": (w, depth)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: FlatBottomParams = params
        rules = pouch_rules(profile)
        d = self.dims(p, rules)
        w, h, depth, fin = p.L, p.W, d["depth"], d["fin"]
        top, bs, c = d["seal"], d["bottom_seal"], d["corner"]
        corner = p.seam == "corner"
        polys, creases, folds, body, fins = _body(w, h, depth, fin, p.seam)
        gap = rules.flat_bottom_gap_mm  # 底是单独一片：和袋身分开排，中间留空
        bx = body + gap
        polys["bottom"] = (PanelRole.BODY, _rect(bx, 0, bx + w, depth))
        outline = Path(  # 两块料各一圈
            [*_rect(0, 0, body, h).segments, *_rect(bx, 0, bx + w, depth).segments], closed=True
        )
        keep = [
            KeepClear(id=f"fin_{i}", kind="seal", path=z, panel=pid)
            for i, (pid, z) in enumerate(fins)
        ]
        keep += [
            KeepClear(id="seal_top", kind="seal", path=_rect(fin, h - top, body - fin, h)),
            KeepClear(id="seal_bottom_edge", kind="seal", path=_rect(fin, 0, body - fin, bs)),
            KeepClear(id="bottom_seal_front", kind="seal", path=_rect(bx, 0, bx + w, bs), panel="bottom"),
            KeepClear(id="bottom_seal_back", kind="seal", path=_rect(bx, depth - bs, bx + w, depth), panel="bottom"),
            KeepClear(id="bottom_seal_left", kind="seal", path=_rect(bx, bs, bx + bs, depth - bs), panel="bottom"),
            KeepClear(id="bottom_seal_right", kind="seal", path=_rect(bx + w - bs, bs, bx + w, depth - bs), panel="bottom"),
        ]  # fmt: skip
        if c:
            keep += [KeepClear(id=f"corner_{i}", kind="seal", path=_rect(fx - c / 2, bs, fx + c / 2, h - top))
                     for i, fx in enumerate(folds)]  # fmt: skip
        sa, sb = polys["side_a"][1].bbox()[0], polys["side_b"][1].bbox()[0]
        for sid, sx in (
            ("side_a", sa),
            ("side_b", sb),
        ):  # 风琴顶部折进袋口（装满后是两个三角），看不到
            keep.append(KeepClear(id=f"{sid}_tuck", kind="tuck", path=_rect(sx, h - top - depth / 2, sx + depth, h - top),
                                  panel=sid))  # fmt: skip
        if p.zipper:
            z1 = h - d["zip_top"]
            keep.append(
                KeepClear(
                    id="zipper", kind="zipper", path=_rect(fin, z1 - d["zip_w"], body - fin, z1)
                )
            )
        if p.tear_notch:  # 撕口在压平后的两侧边上 = 风琴中线
            ty, r = h - rules.tear_from_top_mm, rules.tear_size_mm
            keep += [KeepClear(id=f"tear_{sid}", kind="tear", path=_rect(sx + depth / 2 - r, ty - r, sx + depth / 2 + r, ty + r),
                               panel=sid) for sid, sx in (("side_a", sa), ("side_b", sb))]  # fmt: skip
        fx0 = polys["front"][1].bbox()[0] + (fin if corner else 0.0)
        inner: list[Edge] = []
        extra: dict[str, Any] = {"zipper": p.zipper, "seam": p.seam, "front_inset": fin if corner else 0.0,
                                 "side_inset": fin if corner else 0.0}  # fmt: skip
        if p.handle:
            for face, x0 in (("front", fx0), ("back", polys["back"][1].bbox()[0])):
                cut, zone = _handle(face, x0, w, h, top, rules)
                inner.append(cut)
                keep.append(zone)
            extra["handle"] = list(handle_slot(w, h, rules))
        valve = None
        if p.valve:
            valve = [fx0 + w / 2, d["valve_y"]]
            keep.append(KeepClear(id="valve", kind="valve", path=_circle(valve[0], valve[1], d["valve_r"] + 2.0),
                                  panel="front"))  # fmt: skip
        win = _window(p, fx0, w, h, rules)
        if win is not None:
            keep.append(win)
            extra["window"] = p.window
        extra |= {"valve": valve, "valve_r": d["valve_r"] if p.valve else None,
                  "layout_faces": [pid for pid in polys if pid != "bottom"] + ["bottom"]}  # fmt: skip
        meta = _meta(self, p, {"seal": top, "fin": fin, "gusset": depth / 2, "depth": depth, "bottom_seal": bs,
                               "corner_seal": c}, extra, bx + w, h)  # fmt: skip
        meta["dims"]["depth"] = depth
        meta["assumptions"].append(
            "平底袋的底是单独一片（排在右边，和袋身之间空开），四条边分别封在正面、右风琴、背面、左风琴的下沿；制袋厂可能另出一卷，排版以工厂为准"
        )
        _, tears = _tear_cuts(p, [], [sa + depth / 2, sb + depth / 2], h - rules.tear_from_top_mm,
                              rules.tear_size_mm, outline)  # fmt: skip
        dl = assemble(style_id=self.id, params=p, material=material, profile=profile, outline=outline,
                      polys=polys, creases=creases, inner_cuts=inner + tears, ups={}, provisional=set(),
                      meta=meta)  # fmt: skip
        dl.keep_clear = _clip_zones(keep, outline)
        return dl


def _meta(style: _PouchStyle, p: _PouchBase, d: dict[str, float], extra: dict[str, Any],
          total_w: float, h: float) -> dict[str, Any]:  # fmt: skip
    pouch = {"kind": style.id, "width": p.L, "height": p.W, **{k: round(v, 4) for k, v in d.items()},
             "tear_notch": p.tear_notch, "tear_cut": p.tear_cut, "hang_hole": p.hang_hole,
             "corner_radius": p.corner_radius, **extra}  # fmt: skip
    return {
        "name_zh": style.name_zh,
        "refs": style.refs,
        "version": style.version,
        "layout": "pouch",
        "accuracy_level": "L0",
        "compensation_applied": False,
        "pouch": pouch,
        "dims": {"width": p.L, "height": p.W, "flat": [total_w, h]},
        "assumptions": [
            "软袋平面是印刷排版：封边、拉链、撕口、底褶位置参照 reference 参数化模板，全部【初值】，请制袋厂确认",
            "撕口、挂孔由制袋机冲出；这里标位置（挂孔画成内切口，效果图里看得到）",
        ],
    }


__all__ = [
    "PouchBacksealStyle",
    "PouchFlatBottomStyle",
    "PouchStandupStyle",
    "SachetStyle",
    "pouch_rules",
    "seal_for",
]
