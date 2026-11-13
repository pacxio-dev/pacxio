"""锁底盒（，结构规格草案 ）：盒身、顶部与 RTE 相同，只换底部。

- snaplock 扣底盒（1-2-3 底，ECMA A55.20.01.01 待核对；瓦楞对应 FEFCO 0215）：四片翼不用胶。
  v2（2026-10-22，对照 FEFCO 0215 与用户找的标准图重做，）：
  ① 先折糊口那一侧主面（back）上的 U 形缺口翼；② 两片侧翼折进去、塞到它下面（侧翼斜边朝缺口翼，
  末端有倒钩）；③ 最后折 front 上的 V 形锁舌翼，锁舌塞到缺口翼下面卡住；
- autolock 自动锁底盒（crash lock，ECMA A60.20.00.01 待核对；瓦楞对应 FEFCO 0713）。
  v2（2026-10-22，对照 FEFCO 0713，按 reference 免费生成器出的 80×50×120 矢量刀版校准，
   节）：两片主翼右端各有一条过右下角的 45° 斜压线（角平分线），
  分出糊胶三角；糊盒时三角的里面粘在下一片侧翼的外面。主翼自由边左段全深（齿），正中一个 45° 斜肩
  + 竖直台阶，台阶底深 = 侧翼深 = 侧面宽的一半（两条角平分线的交点）。需要工厂糊盒机做
  "侧缝 + 底部两处"糊胶。

ECMA 的标准图不公开；所有数值都是【初值】，须折纸样验证、结构顾问审核后才能标"已验证"。

底部记号（归一化之前）：底部压线在盒身下沿 y = 0，各翼向下伸（y 为负）；侧翼压线在 y = off
（和 RTE 的防尘翼一样）。外轮廓逆时针：沿底部从左（back 左下角）走到右（side_b 右下角）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Literal

from pacxio.geometry import Path, Segment, Vec, arc_segment
from pacxio.messages import t
from pacxio.model.dieline import EdgeType, PanelRole
from pacxio.styles.rte import (
    BodyX,
    BottomPart,
    RteDims,
    RteParams,
    RteStyle,
    _chain,
    _crease,
    _trapezoid,
)

# ---- 【初值】全部待折纸样与结构顾问确认 ----
MIN_LOCK_BOX_DIM_MM = 25.0
MAJOR_RELIEF_T_RATIO = 0.5  # 主翼两侧在压线角处的让位 = 0.5·t + 0.5
MAJOR_RELIEF_EXTRA_MM = 0.5
# snaplock v2（比例取自用户找的标准图，和 FEFCO 0215 的样子一致）
MAJOR_DEPTH_WI_RATIO = 0.75  # 两片主翼深 = 0.75·Wi（在盒底中间重叠，锁舌塞到缺口翼下面）
MAJOR_DEPTH_RANGE_WI = (0.5, 0.95)
NOTCH_W_LI_RATIO = 0.45  # 缺口宽
NOTCH_W_RANGE_LI = (0.3, 0.7)
NOTCH_D_MAJOR_RATIO = 0.3  # 缺口深 = 0.3·主翼深
NOTCH_LEG_MIN_MM = 5.0  # 缺口两边各留这么宽
TONGUE_W_LI_RATIO = 0.42  # 锁舌比缺口窄一点
TONGUE_W_RANGE_LI = (0.25, 0.6)
TONGUE_CLEAR_MM = 1.0  # 锁舌至少比缺口窄这么多
TONGUE_LEN_MAJOR_RATIO = 0.32
TONGUE_LEN_MIN_MM = 5.0
TONGUE_R_CAP_MM = 3.0
CUT_MARGIN_MM = 5.0  # 缺口、锁舌离主翼另一头至少留这么多
MINOR_DEPTH_WI_RATIO = 0.5  # 侧翼深
MINOR_DEPTH_MARGIN_MM = 1.0  # 侧翼深 ≤ 0.5·Li − 1（两片侧翼不打架）
MINOR_DEPTH_MIN_MM = 10.0
MINOR_SLANT_WS_RATIO = 0.45  # 侧翼斜边横向收进 0.45·ws……
MINOR_SLANT_DEPTH_RATIO = 0.75  # ……到侧翼深的 0.75 处，再折回一个倒钩
HOOK_CAP_MM = 3.0  # 倒钩 = min(3, 0.06·ws)
HOOK_WS_RATIO = 0.06
# autolock v2（按 reference 80×50×120 的矢量刀版校准， 3.3）
AUTO_MAJOR_WI_RATIO = 0.75  # 主翼全深 = 0.75·Wi
AUTO_TAPER_DEG_RANGE = (0.0, 25.0)  # 主翼左边斜角（显式给 major_taper_deg 时）
AUTO_TOOTH_INSET_WI_RATIO = (
    0.2  # 不给角度时：主翼外侧斜边到全深处离面分界 0.2·Wi − 2（两份样本 8 / 10）
)
AUTO_TOOTH_INSET_MM = 2.0
LOCK_STEP_CAP_MM = 4.0  # 台阶竖直段 = min(4, 0.32·(主翼深 − R))
LOCK_STEP_SHOULDER_RATIO = 0.32
WEB_CORNER_MM = 5.0  # 斜压线最靠角的这一段（按深度量）是切线
WEB_CLEAR_MM = 2.0  # 糊胶三角离面的分界
WEB_BOTTOM_MIN_MM = 3.0  # 糊胶三角的底边至少这么长
SHOULDER_MIN_MM = 3.0  # 齿至少比台阶底深这么多
TOOTH_MIN_MM = 5.0  # 主翼左段的齿（全深的自由边）至少这么宽
MINOR_TAPER_RATIO = 1 / 3  # 侧翼靠糊胶三角那边每深 3 收 1
AUTOLOCK_MIN_W_MM = 30.0
AUTOLOCK_MAX_L_OVER_W = 3.0


class SnaplockParams(RteParams):
    """扣底盒参数：RTE 的全部参数 + 底部参数（为空时按公式取默认值）。"""

    major_depth: float | None = None
    notch_w: float | None = None
    notch_d: float | None = None
    minor_depth: float | None = None
    hook: float | None = None
    tongue_w: float | None = None
    tongue_len: float | None = None
    tongue_r: float | None = None
    # v1 的参数：v2 不再使用（标准扣底的主翼不斜切、没有卡舌切口），留着只为旧 job.json 读得进来
    major_taper_deg: float = 15.0
    lock_slits: bool = False


class AutolockParams(RteParams):
    """自动锁底盒参数：RTE 的全部参数 + 底部参数（为空时按公式取默认值）。"""

    major_depth: float | None = None
    lock_step: float | None = None
    major_taper_deg: float | None = None
    diag_crease: Literal["crease", "reverse_crease"] = "crease"
    # v1 的参数：v2 不再使用（糊胶三角改到主翼上、斜压线必须是 45° 角平分线），留着只为旧 job.json 读得进来
    overlap: float | None = None
    lock_w: float | None = None
    diag_deg: float = 45.0
    glue_leg: float | None = None


def _relief(d: RteDims) -> float:
    return MAJOR_RELIEF_T_RATIO * d.t + MAJOR_RELIEF_EXTRA_MM


@dataclass(frozen=True)
class SnapDims:
    relief: float
    major: float  # 两片主翼深
    notch_w: float
    notch_d: float
    minor: float  # 侧翼深
    slant: float  # 侧翼斜边横向收进多少
    hook: float
    tongue_w: float
    tongue_len: float
    tongue_r: float


def snap_dims(p: SnaplockParams, d: RteDims) -> SnapDims:
    major = p.major_depth if p.major_depth is not None else MAJOR_DEPTH_WI_RATIO * d.Wi
    tl = p.tongue_len if p.tongue_len is not None else TONGUE_LEN_MAJOR_RATIO * major
    minor = (p.minor_depth if p.minor_depth is not None
             else min(MINOR_DEPTH_WI_RATIO * d.Wi, 0.5 * d.Li - MINOR_DEPTH_MARGIN_MM))  # fmt: skip
    return SnapDims(
        relief=_relief(d),
        major=major,
        notch_w=p.notch_w if p.notch_w is not None else NOTCH_W_LI_RATIO * d.Li,
        notch_d=p.notch_d if p.notch_d is not None else NOTCH_D_MAJOR_RATIO * major,
        minor=minor,
        slant=MINOR_SLANT_WS_RATIO * d.ws,
        hook=p.hook if p.hook is not None else min(HOOK_CAP_MM, HOOK_WS_RATIO * d.ws),
        tongue_w=p.tongue_w if p.tongue_w is not None else TONGUE_W_LI_RATIO * d.Li,
        tongue_len=tl,
        tongue_r=p.tongue_r if p.tongue_r is not None else min(TONGUE_R_CAP_MM, tl / 3),
    )


@dataclass(frozen=True)
class AutoDims:
    relief: float
    major: float  # 主翼全深
    inset: float  # 主翼外侧斜边到全深处离面分界多少
    half: float  # R = ws / 2：台阶底深、侧翼深、斜压线起点的深度（两条角平分线的交点）
    step: float  # 台阶竖直段
    corner: float  # 斜压线最靠角那段切开（按深度量）
    clear: float  # 糊胶三角离面的分界

    def tooth(self, wb: float) -> float:
        """主翼左段全深的自由边（齿）有多宽。"""
        return 0.5 * wb - (self.major - self.half - self.step) - self.inset


def auto_dims(p: AutolockParams, d: RteDims) -> AutoDims:
    half = 0.5 * d.ws
    major = p.major_depth if p.major_depth is not None else AUTO_MAJOR_WI_RATIO * d.Wi
    step = (p.lock_step if p.lock_step is not None
            else max(0.0, min(LOCK_STEP_CAP_MM, LOCK_STEP_SHOULDER_RATIO * (major - half))))  # fmt: skip
    return AutoDims(
        relief=_relief(d),
        major=major,
        inset=(
            AUTO_TOOTH_INSET_WI_RATIO * d.Wi - AUTO_TOOTH_INSET_MM
            if p.major_taper_deg is None
            else _relief(d) + major * math.tan(math.radians(p.major_taper_deg))
        ),  # fmt: skip
        half=half,
        step=step,
        corner=WEB_CORNER_MM,
        clear=WEB_CLEAR_MM,
    )


def _points_chain(pts: list[tuple[float, float]]) -> list[Segment]:
    """折点连成线段，跳过重合的相邻点（L = W 时台阶宽度为 0）。"""
    keep = [pts[0]]
    for q in pts[1:]:
        if math.dist(q, keep[-1]) > 1e-9:
            keep.append(q)
    return _chain([Vec(x, y) for x, y in keep])


def _closed(segs: list[Segment]) -> Path:
    """沿外轮廓的一段 + 沿压线回到起点，闭合成面。"""
    return Path([*segs, Segment(segs[-1].end, segs[0].start)], closed=True)


def _common_checks(style: RteStyle, p: RteParams, bad: Any) -> None:
    for name in ("L", "W", "H"):
        v = getattr(p, name)
        if v < MIN_LOCK_BOX_DIM_MM:
            bad(
                t(
                    "{style}: {parameter}={value} is below the provisional minimum {minimum}",
                    style=style.id,
                    parameter=name,
                    value=v,
                    minimum=MIN_LOCK_BOX_DIM_MM,
                ),
                t(
                    "Set {parameter} to at least {minimum}",
                    parameter=name,
                    minimum=MIN_LOCK_BOX_DIM_MM,
                ),
            )


class SnapBottom:
    """扣底（1-2-3 底）。tongue_on：锁舌翼挂哪一面（front = 现有扣底盒；back = 几何引擎的另一种面号，
    ECMA 55 的 01 / 03），缺口翼在对面，两片侧翼的斜边和倒钩朝缺口翼。"""

    def __init__(self, tongue_on: str = "front") -> None:
        if tongue_on not in ("front", "back"):
            raise ValueError(
                t(
                    "The snap-lock tab flap can attach to front or back only. Current wall: {wall}.",
                    wall=tongue_on,
                )
            )
        self.tongue_on = tongue_on

    def build(self, p: RteParams, d: RteDims, xs: BodyX) -> BottomPart:
        assert isinstance(p, SnaplockParams)
        s = snap_dims(p, d)
        xb0, xb1, xa1, xf1, xs1 = xs
        off, r0, major = d.off, s.relief, s.major
        V = Vec
        k = MINOR_SLANT_DEPTH_RATIO

        # ① 缺口翼：整宽，两边直；自由边中间一个矩形缺口
        def notched(x0: float, x1: float) -> list[Segment]:
            cx, nw, nd = 0.5 * (x0 + x1), 0.5 * s.notch_w, s.notch_d
            return _chain([V(x0 + r0, 0), V(x0 + r0, -major), V(cx - nw, -major), V(cx - nw, -major + nd),
                           V(cx + nw, -major + nd), V(cx + nw, -major), V(x1 - r0, -major), V(x1 - r0, 0)])  # fmt: skip

        # ② 侧翼：靠缺口翼的一边斜、末端折回一个倒钩；靠锁舌翼的一边直。
        #    直边离面的分界让位 r0（和主翼一样），不然切线会和分界处的切线重在一起
        def minor_a(x0: float, x1: float) -> list[tuple[float, float]]:  # 缺口翼在左
            return [(x0, off), (x0 + s.slant, off - k * s.minor), (x0 + s.slant - s.hook, off - s.minor),
                    (x1 - r0, off - s.minor), (x1 - r0, off)]  # fmt: skip

        def minor_b(x0: float, x1: float) -> list[tuple[float, float]]:  # 缺口翼在右
            return [(x0 + r0, off), (x0 + r0, off - s.minor), (x1 - s.slant + s.hook, off - s.minor),
                    (x1 - s.slant, off - k * s.minor), (x1, off)]  # fmt: skip

        # ③ 锁舌翼：两边从压线角斜着收到锁舌，锁舌两个圆角
        def tongue(x0: float, x1: float) -> list[Segment]:
            cf, hw, tl, tr = 0.5 * (x0 + x1), 0.5 * s.tongue_w, s.tongue_len, s.tongue_r
            out = _chain([V(x0 + r0, 0), V(cf - hw, -major + tl), V(cf - hw, -major + tr)])
            if tr > 0:
                out.append(arc_segment(V(cf - hw + tr, -major + tr), tr, math.pi, 1.5 * math.pi))
            out += _chain([V(cf - hw + tr, -major), V(cf + hw - tr, -major)])
            if tr > 0:
                out.append(
                    arc_segment(V(cf + hw - tr, -major + tr), tr, 1.5 * math.pi, 2 * math.pi)
                )
            return out + _chain([V(cf + hw, -major + tr), V(cf + hw, -major + tl), V(x1 - r0, 0)])

        if self.tongue_on == "front":
            back, front = notched(xb0, xb1), tongue(xa1, xf1)
            sa, sb = minor_a(xb1, xa1), minor_b(xf1, xs1)
        else:
            back, front = tongue(xb0, xb1), notched(xa1, xf1)
            sa, sb = minor_b(xb1, xa1), minor_a(xf1, xs1)
        out: list[Segment] = _chain([V(xb0, 0), V(xb0 + r0, 0)]) + back
        out += _chain([V(xb1 - r0, 0), V(xb1, 0), V(xb1, off), V(*sa[0])])
        out += _chain([V(x, y) for x, y in sa])
        out += _chain([V(*sa[-1]), V(xa1, off), V(xa1, 0), V(xa1 + r0, 0)]) + front
        out += _chain([V(xf1 - r0, 0), V(xf1, 0), V(xf1, off), V(*sb[0])])
        out += _chain([V(x, y) for x, y in sb])
        out += _chain([V(*sb[-1]), V(xs1, off)])

        polys = {
            "btm_back": (PanelRole.BOTTOM, _closed(back)),
            "btm_sa": (PanelRole.DUST_FLAP, _trapezoid(sa)),
            "btm_front": (PanelRole.BOTTOM, _closed(front)),
            "btm_sb": (PanelRole.DUST_FLAP, _trapezoid(sb)),
        }
        creases = [
            _crease("crease_back_btm_back", (xb0 + r0, 0), (xb1 - r0, 0), ["back", "btm_back"]),
            _crease("crease_side_a_btm_sa", sa[0], sa[-1], ["side_a", "btm_sa"]),
            _crease("crease_front_btm_front", (xa1 + r0, 0), (xf1 - r0, 0), ["front", "btm_front"]),
            _crease("crease_side_b_btm_sb", sb[0], sb[-1], ["side_b", "btm_sb"]),
        ]
        return BottomPart(outline=out, polys=polys, creases=creases, extra_edges=[])


@dataclass(frozen=True)
class CrashBottom:
    """自动锁底。不给的项按卡纸自动锁底的初值；瓦楞的自动锁底箱（crashlock_rsc）按自己的参考刀版另给。"""

    corner: float | None = None  # 斜压线最靠角那段切开（按深度量）
    clear: float | None = None  # 糊胶三角离面的分界
    minor_taper: float = MINOR_TAPER_RATIO  # 侧翼靠糊胶三角那边每深 1 收多少

    def build(self, p: RteParams, d: RteDims, xs: BodyX) -> BottomPart:
        assert isinstance(p, AutolockParams)
        a = auto_dims(p, d)
        if self.corner is not None:
            a = replace(a, corner=self.corner)
        if self.clear is not None:
            a = replace(a, clear=self.clear)
        xb0, xb1, xa1, xf1, xs1 = xs
        off, r0 = d.off, a.relief
        D, R, s, c, g = a.major, a.half, a.step, a.corner, a.clear

        def major(x0: float, x1: float) -> list[tuple[float, float]]:
            """主翼：左边斜下；齿（全深）→ 正中的 45° 斜肩 + 台阶 → 台阶底到斜压线起点 [5]；
            糊胶三角 [5]–[9]：外边沿后角平分线 45°、底边、离分界 g 竖直上去、45° 回到压线端 [9]；
            [9] → 右下角 [10] 是沿压线延长线的切线。"""
            xm = 0.5 * (x0 + x1)
            return [(x0 + r0, 0), (x0 + a.inset, -D), (xm - (D - R - s), -D), (xm, -R - s),
                    (xm, -R), (x1 - R, -R), (x1 - 2 * R + D, -D), (x1 - g, -D), (x1 - g, g - 2 * c),
                    (x1 - c, -c), (x1, 0)]  # fmt: skip

        def side(x0: float, x1: float) -> list[tuple[float, float]]:
            """侧翼：靠糊胶三角那边（左）每深 3 收 1，另一边 45°（后角平分线）；深 R。"""
            return [(x0, off), (x0 + R * self.minor_taper, off - R), (x1 - R, off - R), (x1, off)]

        def glue_zone(x0: float) -> list[tuple[float, float]]:
            """糊胶三角折过斜压线压在侧翼上的位置（侧翼外面的涂胶区）：主翼上 (x, d) ↔ 侧翼上 (d, wb − x)。"""
            return [(x0 + c, off - c), (x0 + R, off - R), (x0 + D, off - 2 * R + D), (x0 + D, off - g),
                    (x0 + 2 * c - g, off - g)]  # fmt: skip

        back, front = major(xb0, xb1), major(xa1, xf1)
        out = _points_chain([(xb0, 0), *back, (xb1, off), *side(xb1, xa1), (xa1, 0), *front,
                             (xf1, off), *side(xf1, xs1)])  # fmt: skip

        def flap(pts: list[tuple[float, float]]) -> Path:
            return Path(_points_chain([*pts[:6], pts[9], pts[10], pts[0]]), closed=True)

        def web(pts: list[tuple[float, float]]) -> Path:
            return Path(_points_chain([*pts[5:10], pts[5]]), closed=True)

        kind = EdgeType.REVERSE_CREASE if p.diag_crease == "reverse_crease" else EdgeType.CREASE
        polys = {
            "btm_back": (PanelRole.BOTTOM, flap(back)),
            "glue_back": (PanelRole.GLUE_TAB, web(back)),
            "btm_sa": (PanelRole.DUST_FLAP, _trapezoid(side(xb1, xa1))),
            "btm_front": (PanelRole.BOTTOM, flap(front)),
            "glue_front": (PanelRole.GLUE_TAB, web(front)),
            "btm_sb": (PanelRole.DUST_FLAP, _trapezoid(side(xf1, xs1))),
        }
        creases = [
            _crease("crease_back_btm_back", (xb0 + r0, 0), (xb1, 0), ["back", "btm_back"]),
            _crease("crease_btm_back_glue_back", back[9], back[5], ["btm_back", "glue_back"], kind),
            _crease("crease_side_a_btm_sa", (xb1, off), (xa1, off), ["side_a", "btm_sa"]),
            _crease("crease_front_btm_front", (xa1 + r0, 0), (xf1, 0), ["front", "btm_front"]),
            _crease(
                "crease_btm_front_glue_front", front[9], front[5], ["btm_front", "glue_front"], kind
            ),  # fmt: skip
            _crease("crease_side_b_btm_sb", (xf1, off), (xs1, off), ["side_b", "btm_sb"]),
        ]
        glue_areas = [_trapezoid(glue_zone(xb1)), _trapezoid(glue_zone(xf1))]
        return BottomPart(outline=out, polys=polys, creases=creases, glue_areas=glue_areas)
