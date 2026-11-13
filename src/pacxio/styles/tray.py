"""托盘部件（ 节）：天地盖的地盒 / 天盖、抽屉盒的内托共用。

结构同 FEFCO 0422（也是 fefco_0427 的"底板 + 四壁"）：底居中，两条边上是单层壁、两端带耳朵；
另两端是双层壁——外壁立起，内壁沿卷边翻进盒内夹住耳朵，内壁自由边的插脚插进底的端线上的插孔。
不用胶、不用钉。

这里按一个**标准朝向**生成（同 fefco_0427：双层壁在 x 两端，单层壁在 y 两侧，印刷面视图），
面名也用 0427 的叫法；具体盒型再改名、需要时转 90°（抽屉的双层壁在抽拉方向两端）。
耳朵、插脚、插孔这些常量原来和 fefco_0427 共用；0427 从 0.2.0 起换成插耳做法（），
这里仍是 v1 的几何（天地盖、抽屉经打样确认，黄金快照锁住），常量留在本模块。全部数值【初值】。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, Profile
from pacxio.styles.corrugated import Up, assemble, crease
from pacxio.styles.rte import _rect, _trapezoid

# ---- 【初值】和 fefco_0427 v1 相同（ 节） ----
EAR_SLANT_DEG = 15.0
EAR_DEPTH_WI_RATIO = 0.4
EAR_DEPTH_MIN_MM = 15.0
EAR_FREE_MIN_MM = 10.0  # 耳朵自由端至少这么高
EAR_CLEAR_MM = 2.0  # ear_depth ≤ 0.5·Wi − 2（前、后耳朵在侧板里不相撞）
LUG_W_WI_RATIO, LUG_W_CLAMP = 0.2, (10.0, 30.0)
LUG_W_MIN_MM, LUG_W_MAX_RATIO = 8.0, 0.4
LUG_LEN_EXTRA_MM = 3.0  # lug_len 默认 = t + 3
LUG_CHAMFER_MAX_MM = 1.5
LUG_SPACING_MM = 5.0  # 两个插孔之间、插孔到内侧板两端至少留这么多
LUG_SLOT_EXTRA_MM = 2.0  # 插孔比插脚宽 2
LUG_FRACTIONS = (0.25, 0.75)  # 插脚中心在内侧板宽的 1/4、3/4
SLOT_DEPTH_T, SLOT_DEPTH_EXTRA = 2.5, 0.5  # slot_depth 默认 = 2.5t + 0.5
INNER_INSET_EXTRA_MM = 1.0  # inner_inset 默认 = t + 1

SLOT_DEPTH_MIN_MM = 2.0  # 卡纸 2.5t + 0.5 只有 1.7 mm，太小的孔不好清废
CARD_ROLL_GAP_T = 2.0  # 卡纸卷边默认双压线、间距 2t（★3）


class TrayPartParams(BaseModel):
    """一个托盘部件自己的结构参数（写在 params.<部件> 里）。为空时按公式取默认值。"""

    model_config = ConfigDict(extra="forbid")

    roll_gap: float | None = None
    inner_inset: float | None = None
    ear_offset: float | None = None
    ear_depth: float | None = None
    ear_gap: float | None = None
    lugs: bool = True
    lug_w: float | None = None
    lug_len: float | None = None
    slot_depth: float | None = None
    flute_dir: Literal["x", "y"] = "y"


@dataclass
class TrayDims:
    """标准朝向下的托盘尺寸（mm）：底 x 0..bl、y 0..bw；单层壁高 hf。"""

    t: float
    li: float  # 两块内壁之间（x）
    wi: float  # 两块单层壁之间（y）
    hi: float
    bl: float
    bw: float
    hf: float
    rg: float
    ho: float
    hin: float
    ii: float
    eo: float
    ed: float
    eg: float
    et: float
    lugs: bool
    lug_w: float
    lug_len: float
    lug_c: float
    sd: float
    sw: float
    wall_w: float
    lug_y: list[float] = field(default_factory=list)

    @property
    def X0(self) -> float:  # noqa: N802
        return self.bl - self.X1  # 用镜像算，和镜像出来的耳朵逐位一致

    @property
    def X1(self) -> float:  # noqa: N802
        return self.bl - self.eo

    @property
    def outer(self) -> tuple[float, float, float]:
        """外尺寸（压线中心距 + t）：Lo、Wo、Ho。"""
        return self.bl + self.t, self.bw + self.t, self.hf + self.t

    @property
    def ear_free(self) -> float:
        return self.hf - self.t - self.eg - self.ed * self.et

    @property
    def lug_total(self) -> float:
        return self.lug_len if self.lugs else 0.0


def tray_dims(
    li: float, wi: float, hi: float, t: float, k_panel: float, k_height: float,
    p: TrayPartParams, corrugated: bool,
) -> TrayDims:  # fmt: skip
    bl = li + k_panel * t + 4 * t
    bw = wi + k_panel * t
    hf = hi + k_height * t
    rg = p.roll_gap if p.roll_gap is not None else (t if corrugated else CARD_ROLL_GAP_T * t)
    ho = hf - 0.5 * rg
    ii = p.inner_inset if p.inner_inset is not None else t + INNER_INSET_EXTRA_MM
    eg = p.ear_gap if p.ear_gap is not None else t
    et = math.tan(math.radians(EAR_SLANT_DEG))
    ed = (
        p.ear_depth
        if p.ear_depth is not None
        else min(EAR_DEPTH_WI_RATIO * wi, (hf - t - eg - EAR_FREE_MIN_MM) / et)
    )
    lo, hi_ = LUG_W_CLAMP
    lug_w = p.lug_w if p.lug_w is not None else min(hi_, max(lo, LUG_W_WI_RATIO * wi))
    lug_len = p.lug_len if p.lug_len is not None else t + LUG_LEN_EXTRA_MM
    wall_w = bw - 2 * ii
    return TrayDims(
        t=t, li=li, wi=wi, hi=hi, bl=bl, bw=bw, hf=hf, rg=rg, ho=ho, hin=ho - t, ii=ii,
        eo=p.ear_offset if p.ear_offset is not None else t, ed=ed, eg=eg, et=et, lugs=p.lugs,
        lug_w=lug_w, lug_len=lug_len, lug_c=min(LUG_CHAMFER_MAX_MM, lug_len / 2, lug_w / 4),
        sd=p.slot_depth if p.slot_depth is not None else max(SLOT_DEPTH_T * t + SLOT_DEPTH_EXTRA, SLOT_DEPTH_MIN_MM),
        sw=lug_w + LUG_SLOT_EXTRA_MM, wall_w=wall_w, lug_y=[ii + f * wall_w for f in LUG_FRACTIONS],
    )  # fmt: skip


def min_wall_height(d: TrayDims) -> float:
    """耳朵放得下（深 ≥ 15、自由端 ≥ 10）的最小壁高 hf。"""
    return d.t + d.eg + EAR_FREE_MIN_MM + EAR_DEPTH_MIN_MM * d.et


def validate_tray(d: TrayDims, label: str, prefix: str) -> list[Issue]:
    """第 1.4 节的约束。label 是部件中文名（报错时说清是哪个部件），prefix 是 --set 的前缀。"""
    t = d.t
    out: list[Issue] = []

    def rng(name: str, v: float, lo: float, hi: float) -> None:
        if not lo - 1e-9 <= v <= hi + 1e-9:
            out.append(Issue(id="S03", message=translate("{part}: {parameter}={value:.2f} is outside the provisional range {minimum:.2f}–{maximum:.2f}", part=label, parameter=name, value=v, minimum=lo, maximum=hi),
                             fix=translate("Set `--set {part}.{parameter}=…` to {minimum:.2f}–{maximum:.2f}", part=prefix, parameter=name, minimum=lo, maximum=hi)))  # fmt: skip

    rng("roll_gap", d.rg, 0, 3 * t)
    rng("inner_inset", d.ii, t, 3 * t + 2)
    rng("ear_offset", d.eo, 0.5 * t, 2 * t)
    rng("ear_gap", d.eg, 0.5 * t, 3 * t)
    if d.lugs:
        rng("lug_w", d.lug_w, LUG_W_MIN_MM, LUG_W_MAX_RATIO * d.wall_w)
        rng("lug_len", d.lug_len, t + 1, 3 * t + 5)
        rng("slot_depth", d.sd, max(1.5 * t, SLOT_DEPTH_MIN_MM), max(4 * t, 4.0))
    if d.ed < EAR_DEPTH_MIN_MM:
        out.append(Issue(id="S03", message=translate("{part}: wall height {height:.1f} mm leaves too little room for the ear (depth at least {min_depth:g}, free end at least {min_free:g}, provisional)", part=label, height=d.hf, min_depth=EAR_DEPTH_MIN_MM, min_free=EAR_FREE_MIN_MM),
                         fix=translate("Increase this part's wall height to at least {minimum:.1f} mm", minimum=min_wall_height(d))))  # fmt: skip
        return out
    if d.ed > 0.5 * d.wi - EAR_CLEAR_MM + 1e-9:
        out.append(Issue(id="S03", message=translate("{part}: ear_depth={depth:.1f} is too large. The ears will collide inside the end wall (maximum {maximum:.1f}).", part=label, depth=d.ed, maximum=0.5 * d.wi - EAR_CLEAR_MM),
                         fix=f"`--set {prefix}.ear_depth={0.5 * d.wi - EAR_CLEAR_MM:.1f}`"))  # fmt: skip
    if d.ear_free < EAR_FREE_MIN_MM - 1e-9:
        out.append(Issue(id="S03", message=translate("{part}: the ear's free end is only {height:.1f} mm high (minimum {minimum:g})", part=label, height=d.ear_free, minimum=EAR_FREE_MIN_MM),
                         fix=translate("Reduce `{ear_part}.ear_depth` or `{gap_part}.ear_gap`, or increase this part's height", ear_part=prefix, gap_part=prefix)))  # fmt: skip
    if d.lugs and 2 * d.lug_w + 3 * LUG_SPACING_MM + 2 * LUG_SLOT_EXTRA_MM > d.wall_w:
        out.append(Issue(id="S03", message=translate("{part}: the locking tab does not fit in an inner wall only {width:.1f} mm wide", part=label, width=d.wall_w),
                         fix=translate("Reduce `{width_part}.lug_w` or use `--set {tab_part}.lugs=false`", width_part=prefix, tab_part=prefix)))  # fmt: skip
    if d.hin <= 0:
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "{part}: the rollover is too wide and leaves no positive inner wall height",
                    part=label,
                ),
                fix=translate("Reduce `{part}.roll_gap`", part=prefix),
            )
        )
    return out


@dataclass
class TrayGeom:
    """标准朝向下的几何（归一化之前）。"""

    outline: Path
    polys: dict[str, tuple[PanelRole, Path]]
    creases: list[Edge]
    slots: list[Edge]
    ups: dict[str, Up]


def _dedupe(points: list[Vec]) -> list[Vec]:
    out: list[Vec] = []
    for v in points:
        if not out or not out[-1].is_close(v):
            out.append(v)
    return out


def _chain(points: list[Vec]) -> list[Segment]:
    pts = _dedupe(points)
    return [Segment(a, b) for a, b in zip(pts, pts[1:], strict=False)]


def _lugs(d: TrayDims, xe: float) -> list[Vec]:
    if not d.lugs:
        return []
    hw, ll, c = d.lug_w / 2, d.lug_len, d.lug_c
    pts: list[Vec] = []
    for yc in d.lug_y:
        pts += [Vec(xe, yc - hw), Vec(xe + ll - c, yc - hw), Vec(xe + ll, yc - hw + c),
                Vec(xe + ll, yc + hw - c), Vec(xe + ll - c, yc + hw), Vec(xe, yc + hw)]  # fmt: skip
    return pts


def tray_geometry(d: TrayDims, notch_r: float | None = None) -> TrayGeom:
    """生成托盘（标准朝向）。notch_r 给了时，两块单层壁的自由边正中开半圆手指缺口。"""
    t, bl, bw, X0, X1, hf = d.t, d.bl, d.bw, d.X0, d.X1, d.hf
    xr = bl + d.ho
    xe = xr + d.rg + d.hin
    cx = 0.5 * bl
    right = [
        Vec(X1, -hf), Vec(X1, -hf + t), Vec(X1 + d.ed, -hf + t + d.ed * d.et), Vec(X1 + d.ed, -d.eg),
        Vec(X1, -d.eg), Vec(X1, 0), Vec(bl, 0), Vec(xr, 0), Vec(xr, d.ii), Vec(xr + d.rg, d.ii), Vec(xe, d.ii),
        *_lugs(d, xe),
        Vec(xe, bw - d.ii), Vec(xr + d.rg, bw - d.ii), Vec(xr, bw - d.ii), Vec(xr, bw), Vec(bl, bw),
        Vec(X1, bw), Vec(X1, bw + d.eg), Vec(X1 + d.ed, bw + d.eg), Vec(X1 + d.ed, bw + hf - t - d.ed * d.et),
        Vec(X1, bw + hf - t), Vec(X1, bw + hf),
    ]  # fmt: skip
    left = [Vec(bl - v.x, v.y) for v in reversed(right)]
    top_y = bw + hf
    out: list[Segment] = []
    if notch_r:  # 前壁自由边（y = −hf），往壁里凹
        out += _chain([Vec(X0, -hf), Vec(cx - notch_r, -hf)])
        out.append(arc_segment(Vec(cx, -hf), notch_r, math.pi, 0.0))
        out += _chain([Vec(cx + notch_r, -hf), Vec(X1, -hf)])
    else:
        out += _chain([Vec(X0, -hf), Vec(X1, -hf)])
    out += _chain(right)
    if notch_r:  # 后壁自由边（y = bw + hf），自右向左走，往壁里凹
        out += _chain([Vec(X1, top_y), Vec(cx + notch_r, top_y)])
        out.append(arc_segment(Vec(cx, top_y), notch_r, 0.0, -math.pi))
        out += _chain([Vec(cx - notch_r, top_y), Vec(X0, top_y)])
    else:
        out += _chain([Vec(X1, top_y), Vec(X0, top_y)])
    out += _chain(left)
    outline = Path(out, closed=True)

    def mirror_path(path: Path) -> Path:
        pts = [Vec(bl - v.x, v.y) for v in path.vertices()]
        return Path.from_points(list(reversed(pts)), closed=True)

    def wall(y_root: float, y_free: float, notch_up: bool) -> Path:
        if not notch_r:
            return _rect(X0, min(y_root, y_free), X1, max(y_root, y_free))
        if notch_up:  # 前壁：自由边在下
            return Path([*_chain([Vec(X0, y_free), Vec(cx - notch_r, y_free)]),
                         arc_segment(Vec(cx, y_free), notch_r, math.pi, 0.0),
                         *_chain([Vec(cx + notch_r, y_free), Vec(X1, y_free), Vec(X1, y_root), Vec(X0, y_root),
                                  Vec(X0, y_free)])], closed=True)  # fmt: skip
        return Path([*_chain([Vec(X1, y_free), Vec(cx + notch_r, y_free)]),
                     arc_segment(Vec(cx, y_free), notch_r, 0.0, -math.pi),
                     *_chain([Vec(cx - notch_r, y_free), Vec(X0, y_free), Vec(X0, y_root), Vec(X1, y_root),
                              Vec(X1, y_free)])], closed=True)  # fmt: skip

    xi0 = xr + d.rg
    inner_pts = [
        Vec(xi0, d.ii),
        Vec(xe, d.ii),
        *_lugs(d, xe),
        Vec(xe, bw - d.ii),
        Vec(xi0, bw - d.ii),
    ]
    inner_a = Path.from_points(_dedupe(inner_pts), closed=True)
    ear_f = _trapezoid(
        [(X1, -hf + t), (X1 + d.ed, -hf + t + d.ed * d.et), (X1 + d.ed, -d.eg), (X1, -d.eg)]
    )
    ear_b = _trapezoid([(X1, bw + d.eg), (X1 + d.ed, bw + d.eg), (X1 + d.ed, bw + hf - t - d.ed * d.et),
                        (X1, bw + hf - t)])  # fmt: skip
    polys: dict[str, tuple[PanelRole, Path]] = {
        "base": (PanelRole.BOTTOM, _rect(0, 0, bl, bw)),
        "front": (PanelRole.BODY, wall(0, -hf, True)),
        "back": (PanelRole.BODY, wall(bw, bw + hf, False)),
        "side_a": (PanelRole.BODY, _rect(bl, 0, xr, bw)),
        "side_b": (
            PanelRole.BODY,
            _rect(bl - xr, 0, 0, bw),
        ),  # b 端一律用镜像 bl − x，和镜像出来的面逐位一致
        "side_a_in": (PanelRole.INNER_WALL, inner_a),
        "side_b_in": (PanelRole.INNER_WALL, mirror_path(inner_a)),
        "ear_fa": (PanelRole.DUST_FLAP, ear_f),
        "ear_fb": (PanelRole.DUST_FLAP, mirror_path(ear_f)),
        "ear_ba": (PanelRole.DUST_FLAP, ear_b),
        "ear_bb": (PanelRole.DUST_FLAP, mirror_path(ear_b)),
    }
    if d.rg > 0:
        polys["rim_a"] = (PanelRole.RIM, _rect(xr, d.ii, xi0, bw - d.ii))
        polys["rim_b"] = (PanelRole.RIM, _rect(bl - xi0, d.ii, bl - xr, bw - d.ii))

    creases = [crease("crease_base_front", (X0, 0), (X1, 0), ["base", "front"]),
               crease("crease_base_back", (X0, bw), (X1, bw), ["base", "back"])]  # fmt: skip
    if d.lugs:
        cuts = [(yc - d.sw / 2, yc + d.sw / 2) for yc in d.lug_y]
        ys = [0.0] + [v for c in cuts for v in c] + [bw]
        spans = [(ys[i], ys[i + 1]) for i in range(0, len(ys), 2)]
    else:
        spans = [(0.0, bw)]
    for i, (y0, y1) in enumerate(spans, 1):
        creases.append(crease(f"crease_base_side_a_{i}", (bl, y0), (bl, y1), ["base", "side_a"]))
        creases.append(crease(f"crease_base_side_b_{i}", (0, y0), (0, y1), ["base", "side_b"]))
    for side, x_out, x_in in (("a", xr, xi0), ("b", bl - xr, bl - xi0)):
        y0, y1 = d.ii, bw - d.ii
        if d.rg > 0:
            creases.append(
                crease(
                    f"crease_side_{side}_rim_{side}",
                    (x_out, y0),
                    (x_out, y1),
                    [f"side_{side}", f"rim_{side}"],
                )
            )
            creases.append(
                crease(
                    f"crease_rim_{side}_side_{side}_in",
                    (x_in, y0),
                    (x_in, y1),
                    [f"rim_{side}", f"side_{side}_in"],
                )
            )
        else:
            creases.append(
                crease(
                    f"crease_side_{side}_side_{side}_in",
                    (x_out, y0),
                    (x_out, y1),
                    [f"side_{side}", f"side_{side}_in"],
                )
            )
    for ear, x, y0, y1, parent in (
        ("ear_fa", X1, -hf + t, -d.eg, "front"), ("ear_fb", bl - X1, -hf + t, -d.eg, "front"),
        ("ear_ba", X1, bw + d.eg, bw + hf - t, "back"), ("ear_bb", bl - X1, bw + d.eg, bw + hf - t, "back"),
    ):  # fmt: skip
        creases.append(crease(f"crease_{parent}_{ear}", (x, y0), (x, y1), [parent, ear]))

    slots: list[Edge] = []
    if d.lugs:
        for side, x_line, sign in (("a", bl, -1.0), ("b", 0.0, 1.0)):
            for i, yc in enumerate(d.lug_y, 1):
                y0, y1 = yc - d.sw / 2, yc + d.sw / 2
                xi = x_line + sign * d.sd
                pts = [Vec(x_line, y0), Vec(xi, y0), Vec(xi, y1), Vec(x_line, y1), Vec(x_line, y0)]
                slots.append(
                    Edge(
                        id=f"cut_slot_{side}{i}",
                        type=EdgeType.CUT,
                        segments=_chain(pts),
                        panels=["base"],
                    )
                )
    ups: dict[str, Up] = {  # back 也要写上：抽屉内托整体转 90° 时只转这里列出的面（抽屉测试 F-02）
        "front": "down",
        "back": "up",
        "side_a": "right",
        "side_b": "left",
        "side_a_in": "left",
        "side_b_in": "right",
    }
    return TrayGeom(outline, polys, creases, slots, ups)


def tray_sizes(
    d: TrayDims, rename: dict[str, str] | None = None, turned: bool = False
) -> dict[str, tuple[float, float]]:
    """S02 用：面 → 展开图里的 (宽, 高)。turned：部件转了 90°（宽高互换）。"""
    out = {
        "base": (d.bl, d.bw), "front": (d.X1 - d.X0, d.hf), "back": (d.X1 - d.X0, d.hf),
        "side_a": (d.ho, d.bw), "side_b": (d.ho, d.bw),
        "side_a_in": (d.hin + d.lug_total, d.wall_w), "side_b_in": (d.hin + d.lug_total, d.wall_w),
    }  # fmt: skip
    for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb"):
        out[ear] = (d.ed, d.hf - d.t - d.eg)
    if d.rg > 0:
        out["rim_a"] = out["rim_b"] = (d.rg, d.wall_w)
    names = rename or {}
    return {names.get(k, k): ((h, w) if turned else (w, h)) for k, (w, h) in out.items()}


ROTATE_CCW: dict[Up, Up] = {"right": "up", "up": "left", "left": "down", "down": "right"}


def build_part(
    *,
    style_id: str,
    part: str,
    part_label: str,
    params: BaseModel,
    material: Material,
    profile: Profile,
    geom: TrayGeom,
    rename: dict[str, str],
    turn: bool = False,
    ups: dict[str, Up] | None = None,
    keep_upright: set[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Dieline:
    """标准朝向的托盘几何 → 一个部件的 Dieline：改面名、需要时整体逆时针转 90°（抽屉内托）。

    ups：改名后的面 → 朝向（覆盖标准朝向转过来的结果）；keep_upright：只有这些面不标"方向待定"。
    """
    rot = (
        Affine(0, 1, -1, 0, 0, 0) if turn else Affine.identity()
    )  # 精确的逆时针 90°（cos 90° 的浮点残差会让压线对不上面）

    def name(pid: str) -> str:
        return rename.get(pid, pid)

    outline = geom.outline.transformed(rot)
    polys = {name(k): (role, poly.transformed(rot)) for k, (role, poly) in geom.polys.items()}
    counts: dict[tuple[str, str], int] = {}
    for e in geom.creases:
        key = (name(e.panels[0]), name(e.panels[1]))
        counts[key] = counts.get(key, 0) + 1
    seen: dict[tuple[str, str], int] = {}
    creases: list[Edge] = []
    for e in geom.creases:
        p, c = name(e.panels[0]), name(e.panels[1])
        seen[(p, c)] = seen.get((p, c), 0) + 1
        eid = f"crease_{p}_{c}" + (f"_{seen[(p, c)]}" if counts[(p, c)] > 1 else "")
        creases.append(e.model_copy(update={"id": eid, "panels": [p, c],
                                            "segments": [rot.apply_segment(s) for s in e.segments]}))  # fmt: skip
    slots = []
    for e in geom.slots:
        end = name("side_a" if e.id.startswith("cut_slot_a") else "side_b")
        slots.append(e.model_copy(update={"id": f"cut_slot_{end}{e.id[-1]}", "panels": [name("base")],
                                          "segments": [rot.apply_segment(s) for s in e.segments]}))  # fmt: skip
    turned_ups: dict[str, Up] = {
        name(k): (ROTATE_CCW[v] if turn else v) for k, v in geom.ups.items()
    }
    turned_ups.update(ups or {})
    return assemble(
        style_id=style_id,
        params=params,
        material=material,
        profile=profile,
        outline=outline,
        polys=polys,
        creases=creases,
        inner_cuts=slots,
        ups=turned_ups,
        provisional=set(polys) - (keep_upright or set()),
        meta={"part": part, "part_label": part_label, **(meta or {})},
    )
