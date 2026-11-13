"""Reusable carton geometry, preserving the calibrated arithmetic and identifiers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.styles.components.tube4 import BodyX

# ----  中的默认值与范围（【初值】，须经打样校准后才可标"已验证"）----
MIN_BOX_DIM_MM = 20.0
GLUE_W_RANGE = (10.0, 20.0)
GLUE_W_MARGIN_MM = 2.0  # glue_w ≤ Wi − 2
GLUE_BEVEL_MM = 5.0  # 糊口插舌头那一端：外沿比压线低（高）5，直线斜切到压线角
GLUE_ARC_H_MM = 20.0  # 糊口锁底那一端：外沿比下压线高 20、外沿处竖直相切的圆弧到压线角
GLUE_MIN_STRAIGHT_MM = 10.0  # 糊口直边至少留这么长（盒子太矮时压低圆弧）
TUCK_LEN_RANGE = (10.0, 25.0)
TUCK_LEN_DEFAULT_HI_RATIO = 0.5
TUCK_RADIUS_CAP_MM = 5.0
DUST_DEPTH_MARGIN_MM = 1.0  # dust_depth ≤ 0.5·Li − 1
DUST_OFFSET_T_RATIO = 0.5  # dust_offset 默认 = 0.5·t
# 插舌头 v2（reference 80×50×120、150×60×80 两份刀版校准， 节）
TUCK_LEN_WI_RATIO = (
    0.2  # 插舌长默认 = min(0.2·Wi + 6, 25, 0.5·Hi)（两份样本也对得上 min(0.2·L, 18)）
)
TUCK_LEN_EXTRA_MM = 6.0
TUCK_SLIT_LI_RATIO = 1 / 24  # 插舌根部两端的短压线长 = Li / 24
TUCK_SLIT_GAP_MM = 2.0  # 短压线离插舌压线（tuck_slit="crease" 的老做法）
# 插舌压线两端的钩形切口（tuck_slit="cut"，2026-11-07 用户：现在很多插舌折痕处这样做，默认改成这样；
# 用户画了位置、选了“钩形，碰到压线”）：末端拐下到压线，尺寸见下面 TUCK_HOOK_*
TUCK_SLIT_STYLES = ("cut", "crease")
# 钩形切口（用户 2026-11-07 选的“钩形，碰到压线”；照 reference 插舌盒刀版：长 8，离压线 0.5——用户说凸起
# 大概是一层纸厚，所以按纸厚）【初值】
TUCK_HOOK_GAP_T_RATIO = 1.0  # 离插舌压线 = 纸厚 × 这个数
TUCK_HOOK_LEN_MM = 8.0  # 从插舌边往里
TUCK_HOOK_MAX_RATIO = 0.25  # 窄插舌上最多占插舌宽的这一成（两头加起来一半）
DUST_DEPTH_LI_RATIO = 0.4  # 防尘翼深默认 = min(0.4·Li, Wi)
DUST_BASE_INSET_MM = 1.0  # 防尘翼根部离两边的面分界
DUST_RELIEF_MM = 2.12  # 靠盖那侧根部的 45° 小让位（直角边）
DUST_BACK_TIP_MM = 5.0  # 靠盖那侧的边到顶时离面分界
DUST_TIP_WS_RATIO = 0.1944  # 靠插舌那侧的顶角（圆角前的交点）离面分界 = 0.1944·ws
DUST_CORNER_MM = 5.0  # 顶角圆角两边的切线长（放不下时缩短）
DUST_LOCK_LI_RATIO = 1 / 30  # 卡口：根部竖直段 = Li / 30……
DUST_LOCK_MM = 2.83  # ……再 45° 收进这么多（直角边）
DUST_MIN_SLANT_MM = 2.0  # 卡口以上的斜边至少这么高


@dataclass(frozen=True)
class RteDims:
    """全部取值解析后的尺寸（mm）。"""

    t: float
    Li: float
    Wi: float
    Hi: float
    wb: float  # back / front 面宽
    ws: float  # side 面宽
    hb: float  # body 面高
    ld: float  # 盖深
    tl: float
    ti: float
    tr: float
    dd: float
    off: float
    gw: float
    off_top: float | None = (
        None  # 顶端的防尘让位；不给 = 和底端一样（off）。几何引擎：开口 / 侧插那端为 0
    )

    @property
    def top_off(self) -> float:
        return self.off if self.off_top is None else self.off_top


def _chain(points: list[Vec]) -> list[Segment]:
    """折点连成线段，跳过长度为 0 的（不补偿时插舌和盖一样宽）。"""
    return [
        Segment(points[i], points[i + 1])
        for i in range(len(points) - 1)
        if not points[i].is_close(points[i + 1])
    ]


def _tuck_segments(d: RteDims, x_center: float, y_root: float, top: bool) -> list[Segment]:
    """插舌轮廓（含根部两端，不含根线本身）：两边直、顶上两个圆角（v2 不再有 V 形缺口）。

    在局部坐标 (u, v) 中构造：u 沿根线、v 向外，从 u=−hw 走到 +hw；
    顶盖插舌沿 −x 方向行走（x = xc − u, y = yr + v），底盖沿 +x（x = xc + u, y = yr − v），
    两者都是镜像变换，圆弧 bulge 的符号由 Affine.apply_segment 自动翻转。
    """
    hw = 0.5 * (d.wb - 2 * d.ti)
    tl, r = d.tl, d.tr
    segs = _chain([Vec(-hw, 0.0), Vec(-hw, tl - r)])
    segs.append(arc_segment(Vec(-hw + r, tl - r), r, math.pi, math.pi / 2))
    segs.append(Segment(Vec(-hw + r, tl), Vec(hw - r, tl)))
    segs.append(arc_segment(Vec(hw - r, tl - r), r, math.pi / 2, 0.0))
    segs += _chain([Vec(hw, tl - r), Vec(hw, 0.0)])
    m = Affine(-1, 0, 0, 1, x_center, y_root) if top else Affine(1, 0, 0, -1, x_center, y_root)
    return [m.apply_segment(s) for s in segs]


def _tuck_slits(d: RteDims, x_center: float, y_root: float, top: bool, panel: str) -> list[Edge]:
    """插舌根部两端的短压线（friction_lock 开时）：离插舌压线 2，从插舌边往里 Li / 24。

    是面里面的压线（只属于插舌这一个面），和防尘翼根部的卡口配合卡住插舌——作用是读
    reference 刀版推的，待折样确认。
    """
    hw, n = 0.5 * (d.wb - 2 * d.ti), TUCK_SLIT_LI_RATIO * d.Li
    y = y_root + (TUCK_SLIT_GAP_MM if top else -TUCK_SLIT_GAP_MM)
    return [
        Edge(id=f"crease_{panel}_slit_{k}", type=EdgeType.CREASE,
             segments=[Segment(Vec(x_center + a, y), Vec(x_center + b, y))], panels=[panel])
        for k, (a, b) in (("l", (-hw, -hw + n)), ("r", (hw - n, hw)))
    ]  # fmt: skip


def hook_gap(d: RteDims) -> float:
    """钩形切口离插舌压线多高：一层纸厚（用户 2026-11-07）。"""
    return TUCK_HOOK_GAP_T_RATIO * d.t


def tuck_panels(
    d: RteDims,
    x0: float,
    x1: float,
    y_parent: float,
    y: float,
    up: bool,
    contour: list[Segment],
    lid: str,
    tuck: str,
    lock: bool,
    slit: str = "cut",
) -> tuple[Path, Path, list[Edge], list[Edge]]:
    """盖和插舌：(盖的面, 插舌的面, 两者之间的压线, 面里面的线)。x0..x1 是盖的宽、y_parent 是盖挂的那条压线、
    y 是盖和插舌之间那条，contour 是 _tuck_segments 给的插舌轮廓（插舌两边各让 ti）。

    slit="cut"（默认，2026-11-07 用户画的，尺寸照 reference）：插舌根部两端各一道钩形刀线——离压线一层纸厚、
    从插舌边往里 8（窄插舌最多插舌宽的 1/4），末端拐下来碰到压线；围出的小块只连着盖（下面不压线），折插舌时留在盖上当卡位，
    所以归盖；压线只在两个钩之间。slit="crease"：整条压线 + 离压线 2 的两段短压线（reference 老做法）。
    lock 关：整条压线，什么都不加。"""
    lid_rect = _rect(x0, min(y_parent, y), x1, max(y_parent, y))
    a, b = x0 + d.ti, x1 - d.ti
    if not lock or slit == "crease":
        crease = _crease(f"crease_{lid}_tuck", (a, y), (b, y), [lid, tuck])
        inner = _tuck_slits(d, 0.5 * (x0 + x1), y, up, tuck) if lock else []
        return lid_rect, _tuck_polygon(contour), [crease], inner
    n = min(TUCK_HOOK_LEN_MM, TUCK_HOOK_MAX_RATIO * (d.wb - 2 * d.ti))
    g = hook_gap(d)
    s = 1.0 if up else -1.0

    def P(x: float, v: float) -> Vec:  # v：从盖和插舌之间的压线往插舌那边量
        return Vec(x, y + s * v)

    # 插舌轮廓两头的直边从压线起，改成从钩的高度起（下面那段归盖的小卡位）
    first, last = contour[0], contour[-1]
    body = [
        Segment(P(first.start.x, g), first.end),
        *contour[1:-1],
        Segment(last.start, P(last.end.x, g)),
    ]
    xs, xe = first.start.x, last.end.x  # 插舌轮廓的起点、终点那一侧
    ins, ine = (xs + n if xs < xe else xs - n), (xe - n if xs < xe else xe + n)
    tuck_path = Path(
        [*_chain([P(ins, 0), P(ins, g), P(xs, g)]), *body,
         *_chain([P(xe, g), P(ine, g), P(ine, 0), P(ins, 0)])],
        closed=True,
    )  # fmt: skip
    far = y_parent
    # 盖：远边 → 插舌终点那侧 → 卡位 → 中间（压线）→ 卡位 → 插舌起点那侧 → 回到远边
    ox_e = x1 if xe > xs else x0
    ox_s = x0 if xe > xs else x1
    lid_pts = [Vec(ox_s, far), Vec(ox_e, far), Vec(ox_e, y), P(xe, 0), P(xe, g), P(ine, g), P(ine, 0),
               P(ins, 0), P(ins, g), P(xs, g), P(xs, 0), Vec(ox_s, y)]  # fmt: skip
    lid_path = Path(_chain([*lid_pts, lid_pts[0]]), closed=True)
    lo, hi = sorted((ins, ine))
    crease = _crease(f"crease_{lid}_tuck", (lo, y), (hi, y), [lid, tuck])
    cuts = [
        Edge(id=f"cut_{tuck}_slit_{k}", type=EdgeType.CUT, segments=_chain([P(x, g), P(xi, g), P(xi, 0)]),
             panels=[lid, tuck])
        for k, (x, xi) in (("l", (min(xs, xe), lo)), ("r", (max(xs, xe), hi)))
    ]  # fmt: skip
    return lid_path, tuck_path, [crease], cuts


def _dust_local(
    d: RteDims, ws: float, lock: bool, inset: float = DUST_BASE_INSET_MM
) -> list[Segment]:
    """防尘翼外轮廓（局部坐标：u 沿压线，从靠盖的一侧量起；v 向外），从 (e, 0) 走到 (ws − e, 0)。

    靠盖那侧：根部 45° 小让位，几乎直着到顶（离分界 5）；靠插舌那侧：顶角（离分界 0.1944·ws）
    圆角，斜下到根部前一个 45° 卡口再竖直到压线（lock=False 时斜边直接到根部）。
    inset：根部离两边面分界（彩盒 1；开槽箱按开槽宽的一半，2026-11-07 插舌盖合并）。
    """
    e, a, D = inset, DUST_RELIEF_MM, d.dd
    corner = Vec(ws * (1 - DUST_TIP_WS_RATIO), D)
    if lock:
        h, k = DUST_LOCK_LI_RATIO * d.Li, DUST_LOCK_MM
        foot = [Vec(ws - e - k, h + k), Vec(ws - e, h), Vec(ws - e, 0.0)]
    else:
        foot = [Vec(ws - e, 0.0)]
    slant = foot[0] - corner
    c = min(DUST_CORNER_MM, 0.4 * slant.length(), 0.4 * (corner.x - DUST_BACK_TIP_MM))
    sd = slant.normalized()
    p1, p2 = corner - Vec(c, 0.0), corner + sd * c
    turn = math.atan2(-sd.y, sd.x)  # 顶边（+u）转到斜边（往下）的角度，顺时针
    segs = _chain([Vec(e, 0.0), Vec(e + a, a), Vec(max(DUST_BACK_TIP_MM, e + a), D), p1])
    segs.append(Segment(p1, p2, -math.tan(turn / 4)))
    return segs + _chain([p2, *foot])


def _dust_flap(
    d: RteDims,
    x0: float,
    x1: float,
    y: float,
    up: bool,
    tuck_at_x1: bool,
    lock: bool,
    inset: float = DUST_BASE_INSET_MM,
) -> list[Segment]:
    """面 [x0, x1] 上沿（up）或下沿的防尘翼，压线在 y；返回从 x0 一侧走到 x1 一侧的轮廓段。

    tuck_at_x1：插舌从 x1 那一边的面插进去（卡口、斜边在那一侧）。
    """
    sy = 1.0 if up else -1.0
    m = Affine(1, 0, 0, sy, x0, y) if tuck_at_x1 else Affine(-1, 0, 0, sy, x1, y)
    segs = [m.apply_segment(s) for s in _dust_local(d, x1 - x0, lock, inset)]
    if not tuck_at_x1:  # 局部坐标从 x1 走到 x0，倒过来
        segs = [s.reversed() for s in reversed(segs)]
    return segs


def _backwards(segs: list[Segment]) -> list[Segment]:
    return [s.reversed() for s in reversed(segs)]


def _closed_flap(segs: list[Segment]) -> Path:
    """翼的轮廓 + 沿压线回到起点。"""
    return Path([*segs, Segment(segs[-1].end, segs[0].start)], closed=True)


def _crease(
    eid: str,
    a: tuple[float, float],
    b: tuple[float, float],
    panels: list[str],
    kind: EdgeType = EdgeType.CREASE,
) -> Edge:
    """panels = [父面, 子面]（子面的铰链挂在父面上）。"""
    return Edge(id=eid, type=kind, segments=[Segment(Vec(*a), Vec(*b))], panels=panels)


def _tuck_polygon(segs: list[Segment]) -> Path:
    return Path([*segs, Segment(segs[-1].end, segs[0].start)], closed=True)


def _ordered(
    polys: dict[str, tuple[PanelRole, Path]], order: tuple[str, ...]
) -> dict[str, tuple[PanelRole, Path]]:
    """按盒型给的次序排面（SVG 标注、黄金快照依赖次序）；次序里没有的放在最后。"""
    rank = {pid: i for i, pid in enumerate(order)}
    return dict(sorted(polys.items(), key=lambda kv: rank.get(kv[0], len(rank))))


def _ordered_edges(edges: list[Edge], order: tuple[str, ...]) -> list[Edge]:
    rank = {eid: i for i, eid in enumerate(order)}
    return sorted(edges, key=lambda e: rank.get(e.id, len(rank)))


def _rect(x0: float, y0: float, x1: float, y1: float) -> Path:
    return Path.rect(x0, y0, x1 - x0, y1 - y0)


def _trapezoid(pts: list[tuple[float, float]]) -> Path:
    return Path.from_points([Vec(x, y) for x, y in pts], closed=True)


@dataclass
class BottomPart:
    """可替换的底部：外轮廓从 (xb0, 0) 走到 (xs1, off)；面、压线、内部切口、糊胶区。"""

    outline: list[Segment]
    polys: dict[str, tuple[PanelRole, Path]]
    creases: list[Edge]
    extra_edges: list[Edge] = field(default_factory=list)
    glue_areas: list[Path] = field(default_factory=list)


class TuckEnd:
    """The four-panel tuck bottom recipe; RTE/STE differ only in lid_on."""

    def __init__(self, lid_on: str = "front") -> None:
        self.lid_on = lid_on

    def build(self, p: Any, d: RteDims, xs: BodyX) -> BottomPart:
        """插舌底：底盖 + 插舌（挂在 bottom_lid_on 面）+ 两片下防尘翼（和顶部镜像）。"""
        xb0, xb1, xa1, xf1, xs1 = xs
        off, ld, ti, e = d.off, d.ld, d.ti, DUST_BASE_INSET_MM

        def V(x: float, y: float) -> Vec:
            return Vec(x, y)

        on_back = self.lid_on == "back"
        bx0, bx1 = (xb0, xb1) if on_back else (xa1, xf1)  # 底盖所在面的 x 范围
        bottom_tuck = _tuck_segments(d, 0.5 * (bx0 + bx1), -ld, top=False)
        # 下防尘翼的卡口朝插舌插进去的那一面：底盖挂 back（STE）→ 从 front 插；挂 front（RTE）→ 从 back 插
        dust_ba = _dust_flap(d, xb1, xa1, off, up=False, tuck_at_x1=on_back, lock=p.friction_lock)
        dust_bb = _dust_flap(
            d, xf1, xs1, off, up=False, tuck_at_x1=not on_back, lock=p.friction_lock
        )
        out: list[Segment] = []
        if on_back:
            out += _chain([V(xb0, 0), V(xb0, -ld), V(xb0 + ti, -ld)])
            out += bottom_tuck
            out += _chain([V(xb1 - ti, -ld), V(xb1, -ld), V(xb1, 0), V(xb1, off)])
        else:
            out += _chain([V(xb0, 0), V(xb1, 0), V(xb1, off)])
        out += (
            _chain([V(xb1, off), V(xb1 + e, off)])
            + dust_ba
            + _chain([V(xa1 - e, off), V(xa1, off)])
        )
        if on_back:
            out += _chain([V(xa1, off), V(xa1, 0), V(xf1, 0), V(xf1, off)])
        else:
            out += _chain([V(xa1, off), V(xa1, 0), V(xa1, -ld), V(xa1 + ti, -ld)])
            out += bottom_tuck
            out += _chain([V(xf1 - ti, -ld), V(xf1, -ld), V(xf1, 0), V(xf1, off)])
        out += (
            _chain([V(xf1, off), V(xf1 + e, off)])
            + dust_bb
            + _chain([V(xs1 - e, off), V(xs1, off)])
        )
        lid_path, tuck_path, hinge, slits = tuck_panels(
            d, bx0, bx1, 0.0, -ld, False, bottom_tuck, "btm_lid", "btm_tuck",
            p.friction_lock, getattr(p, "tuck_slit", "cut"),
        )  # fmt: skip
        polys: dict[str, tuple[PanelRole, Path]] = {
            "btm_lid": (PanelRole.LID, lid_path),
            "btm_tuck": (PanelRole.TUCK, tuck_path),
            "dust_ba": (PanelRole.DUST_FLAP, _closed_flap(dust_ba)),
            "dust_bb": (PanelRole.DUST_FLAP, _closed_flap(dust_bb)),
        }
        creases = [
            _crease(
                f"crease_{self.lid_on}_btm_lid", (bx0, 0), (bx1, 0),
                [self.lid_on, "btm_lid"],
            ),
            _crease("crease_side_a_dust_ba", (xb1 + e, off), (xa1 - e, off), ["side_a", "dust_ba"]),
            _crease("crease_side_b_dust_bb", (xf1 + e, off), (xs1 - e, off), ["side_b", "dust_bb"]),
        ]  # fmt: skip
        creases[1:1] = hinge  # 顺序同原来：盖的压线、插舌压线、两片防尘翼
        return BottomPart(outline=out, polys=polys, creases=creases, extra_edges=slits)
