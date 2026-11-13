"""机构功能核对（2026-11-06，用户看 3D 发现 0217 提手扣不上之后加）：折好以后这个结构能不能用。

折叠自检（`fold_deviation`）只量“每块面有没有折到配方给它定的位置”——定的位置本身错了也照样通过。
这里不看目标，只看按刀版和折法真正折好以后（带分层纸厚）各块面在空间里的位置，查四件事：

1. 封闭：从盒子里往各个方向打射线，封口的盒子一条都不该漏出去；开口的只能从开口那一面漏
   （面按外轮廓算、不挖孔——手提孔、窗口是有意开的；转角、压线处让开一点纸厚的缝）。
2. 伸出：翼、舌、卷边这些小面不能伸到盒体（墙 + 盖 + 底围成的外包络）外面去。
3. 缝 / 槽用上没有：面上的窄缝、窄槽，折好后要有别的面插进去或伸到它跟前。
4. 孔：手提孔、窗口不能被背后贴着的另一层挡住；顶上的手提板要背靠背合在一起、孔对齐。

只查几何，不代替折样：插得进、扣得牢、承不承重要实物。数值都是【初值】。
片材（09 隔板、衬垫）和多件套之间的插接是装配关系，单件不查封闭和缝。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from pacxio.messages import lang
from pacxio.messages import t as translate
from pacxio.model.dieline import Dieline

SHELL_ROLES = frozenset({"body", "lid", "bottom"})
GAP_T, GAP_MM = 1.5, 1.0  # 封闭：面外轮廓放大 1.5·t + 1 mm（转角、压线处的纸厚缝不算漏）【初值】
LEAK_MIN, LEAK_MIN_RAYS = (
    0.005,
    3,
)  # 某一面漏出去的射线超过 0.5 % 且至少 3 条，就算这一面有缺口【初值】
PROTRUDE_T, PROTRUDE_MM, PROTRUDE_MIN = (
    3.0,
    1.0,
    8.0,
)  # 顺着自己的平面伸出盒体超过 max(3·t + 1, 8) mm 才报【初值】
# （山形顶的端翼要比手提板顶高出几毫米，缝上面才留得住纸；没折好的翼舌都伸出 14 mm 以上）
STACK_OUT_T = 5.0  # 一层层贴在墙外（和超出的那面平行）最多 5 层纸厚【初值】
PIERCE_T, PIERCE_MM = 2.0, 1.0  # 穿插：离边 > 2·t，两侧都穿出 > 2·t + 1 mm【初值】
SLOT_MAX_T, SLOT_MAX_MM = 3.0, 8.0  # 窄缝：宽 ≤ max(3·t, 8 mm) 而且长 ≥ 2 倍宽【初值】
THROUGH_T = 0.25  # 插进缝：别的面穿过缝所在的面，两侧都超过 ¼·t【初值】
REACH_T, REACH_MM = 2.0, 1.0  # 插进缝：别的面穿过缝，或者端头离缝 ≤ 2·t + 1 mm【初值】
STACK_T, STACK_MM = 3.0, 1.0  # 两层算贴在一起：平行、相距 ≤ 3·t + 1 mm【初值】
PARALLEL_COS = 0.98
N_RAYS = 1000
RAISED_TOPS = frozenset({"gable", "handle", "hanger", "display", "slope"})  # 本来就高出墙的顶


@dataclass(frozen=True)
class Finding:
    check: str  # enclosure / protrusion / slot / hole / handle
    message: str
    panels: tuple[str, ...] = ()
    value_mm: float | None = None


@dataclass
class Folded:
    dl: Dieline
    mats: dict[str, np.ndarray]
    outlines: dict[str, BaseGeometry]  # 面的外轮廓（不挖孔），刀版坐标
    t: float
    inv: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.inv = {pid: np.linalg.inv(m) for pid, m in self.mats.items()}

    def world(self, pid: str, pts: np.ndarray) -> np.ndarray:
        """面上的刀版点（n×2）→ 折好后中面上的世界坐标（n×3）。"""
        local = np.column_stack([pts, np.full(len(pts), -self.t / 2), np.ones(len(pts))])
        out: np.ndarray = np.asarray((self.mats[pid] @ local.T).T[:, :3], dtype=float)
        return out

    def normal(self, pid: str) -> np.ndarray:
        return self.mats[pid][:3, 2]

    def vertices(self, pid: str) -> np.ndarray:
        geom = self.outlines[pid]
        polys = list(getattr(geom, "geoms", [geom]))
        pts = np.array([c for p in polys for c in p.exterior.coords])
        return self.world(pid, pts)


def fold(dl: Dieline) -> Folded | None:
    from pacxio.geometry import path_to_polygon
    from pacxio.mockup.folding import build_part, panel_matrices, plan_for

    plan = plan_for(dl)
    if plan is None:
        return None
    mats = panel_matrices(build_part(dl, plan), plan.steps)
    outlines: dict[str, BaseGeometry] = {
        p.id: path_to_polygon(p.polygon).buffer(0) for p in dl.panels
    }
    return Folded(dl, {k: np.asarray(v) for k, v in mats.items()}, outlines, dl.caliper_mm)


def _shell_box(f: Folded) -> tuple[np.ndarray, np.ndarray] | None:
    shell = [p.id for p in f.dl.panels if p.role.value in SHELL_ROLES]
    if not shell:
        return None
    pts = np.vstack([f.vertices(pid) for pid in shell])
    return pts.min(axis=0), pts.max(axis=0)


def _walls(f: Folded) -> list[str]:
    """筒身的墙：长方筒 back / side_a / front / side_b，多边形筒 wall_*；没有就用所有 body 面。"""
    ids = {p.id for p in f.dl.panels}
    rect: list[str] = [w for w in ("back", "side_a", "front", "side_b") if w in ids]
    if len(rect) == 4:
        return rect
    poly = sorted(i for i in ids if i.startswith("wall_"))
    if len(poly) >= 3:
        return poly
    return [p.id for p in f.dl.panels if p.role.value == "body"]


def _directions(n: int = N_RAYS) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5**0.5) * i
    return np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])


FACE_ZH = {(0, 0): "x−", (0, 1): "x+", (1, 0): "y−", (1, 1): "y+", (2, 0): "z−", (2, 1): "z+"}


def enclosure(f: Folded, open_ends: int) -> list[Finding]:
    """从盒子里打射线：漏出去的射线按穿出外包络的哪一面归类；开着的面超过 open_ends 个就报。"""
    walls = _walls(f)
    if len(walls) < 3:
        return []
    pts = np.vstack([f.vertices(w) for w in walls])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    if ((hi - lo) < 4 * f.t).any():  # 扁平的片，没有“里面”
        return []
    grow = GAP_T * f.t + GAP_MM
    shapes = {pid: g.buffer(grow) for pid, g in f.outlines.items()}
    dirs = _directions()
    centres = [f.vertices(w).mean(axis=0) for w in walls]
    centre = np.mean(centres, axis=0)  # 墙围出来的中心（凸筒一定在里面）
    origins = [centre] + [(centre + c) / 2 for c in centres]
    escaped = np.zeros(len(dirs), dtype=int)
    exits: dict[tuple[int, int], int] = {}
    totals: dict[tuple[int, int], int] = {}
    for o in origins:
        blocked = np.zeros(len(dirs), dtype=bool)
        for pid, shape in shapes.items():
            inv = f.inv[pid]
            o_l = inv @ np.array([*o, 1.0])
            d_l = (inv[:3, :3] @ dirs.T).T
            dz = d_l[:, 2]
            ok = np.abs(dz) > 1e-9
            s = np.where(ok, (-f.t / 2 - o_l[2]) / np.where(ok, dz, 1.0), -1.0)
            hit = ok & (s > 0)
            if not hit.any():
                continue
            idx = np.flatnonzero(hit & ~blocked)
            if not len(idx):
                continue
            x = o_l[0] + s[idx] * d_l[idx, 0]
            y = o_l[1] + s[idx] * d_l[idx, 1]
            blocked[idx[shapely.contains_xy(shape, x, y)]] = True
        # 每条射线从外包络哪一面出去
        with np.errstate(divide="ignore", invalid="ignore"):
            tt = np.where(dirs > 0, (hi - o) / dirs, np.where(dirs < 0, (lo - o) / dirs, np.inf))
        axis = np.argmin(tt, axis=1)
        side = (dirs[np.arange(len(dirs)), axis] > 0).astype(int)
        for k, (a, sd) in enumerate(zip(axis, side, strict=True)):
            key = (int(a), int(sd))
            totals[key] = totals.get(key, 0) + 1
            if not blocked[k]:
                exits[key] = exits.get(key, 0) + 1
                escaped[k] += 1
    open_faces = {k: v / totals[k] for k, v in exits.items()
                  if v >= LEAK_MIN_RAYS and v / totals[k] > LEAK_MIN}  # fmt: skip
    if len(open_faces) <= open_ends:
        return []
    want = (
        translate("a closed box")
        if open_ends == 0
        else translate("only {count} open sides", count=open_ends)
    )
    worst = sorted(open_faces.items(), key=lambda kv: -kv[1])
    faces = translate(", ").join(
        translate("{face} ({ratio:.0%} of rays escape)", face=FACE_ZH[k], ratio=v) for k, v in worst
    )
    return [
        Finding(
            "enclosure",
            translate(
                "{count} sides remain open after folding (recipe calls for {expected}): {sides}",
                count=len(open_faces),
                expected=want,
                sides=faces,
            ),
        )
    ]


def protrusion(f: Folded) -> list[Finding]:
    """翼 / 舌 / 卷边伸出墙 + 盖 + 底的外包络。"""
    box = _shell_box(f)
    if box is None:
        return []
    lo, hi = box
    out = []
    held = through_slots(f)
    for p in f.dl.panels:
        if p.role.value in SHELL_ROLES or p.id in held:
            continue
        v = f.vertices(p.id)
        n = np.abs(f.normal(p.id))
        worst = 0.0
        for k in range(3):  # 每个方向分开看：和这面平行叠在外面的是纸厚分层，顺着平面伸出去才算伸出
            over = float(max((lo[k] - v[:, k]).max(), (v[:, k] - hi[k]).max()))
            tol = (
                STACK_OUT_T * f.t
                if n[k] > PARALLEL_COS
                else max(PROTRUDE_T * f.t + PROTRUDE_MM, PROTRUDE_MIN)
            )
            if over > tol:
                worst = max(worst, over)
        if worst:
            out.append(
                Finding(
                    "protrusion",
                    translate(
                        "{panel} projects {distance:.1f} mm beyond the body after folding",
                        panel=p.id,
                        distance=worst,
                    ),
                    (p.id,),
                    round(worst, 1),
                )
            )
    return out


def _loops(f: Folded, with_ids: bool = False) -> list[Any]:
    """内部的闭合切口 → (所在的面, 多边形[, 切口 id])。"""
    from pacxio.geometry import Path as GPath
    from pacxio.geometry import path_to_polygon

    out = []
    for e in f.dl.inner_cuts():
        if not e.segments[0].start.is_close(e.segments[-1].end):
            continue
        poly = path_to_polygon(GPath(e.segments, closed=True)).buffer(0)
        if poly.is_empty:
            continue
        c = poly.representative_point()
        owner = next((p for p in f.dl.panels if f.outlines[p.id].contains(c)), None)
        if owner is not None:
            out.append((owner.id, poly, e.id) if with_ids else (owner.id, poly))
    return out


def _width_length(poly: Polygon) -> tuple[float, float]:
    rect: Any = poly.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    a = math.dist((xs[0], ys[0]), (xs[1], ys[1]))
    b = math.dist((xs[1], ys[1]), (xs[2], ys[2]))
    return min(a, b), max(a, b)


def _hang_ids(f: Folded) -> set[str]:
    """不是给本件插舌用的切口：挂孔、手孔、开孔、留给上面那只箱子凸舌的堆叠槽、托盘底边孔。"""
    return {
        e.id
        for e in f.dl.inner_cuts()
        if any(k in e.id for k in ("hang", "stack", "edgehole", "hand", "hole"))
    }


def _is_slot(poly: Polygon, t: float, eid: str = "") -> bool:
    """窄缝（宽 ≤ max(3·t, 8)、长 ≥ 2 倍宽），或锁舌槽（cut_lock_slot_*：厚纸上的槽可能不细长）。"""
    if "lock_slot" in eid:
        return True
    w, length = _width_length(poly)
    return w <= max(SLOT_MAX_T * t, SLOT_MAX_MM) and length >= 2 * w


def _crossing(
    f: Folded, host: str, other: str, holes: bool = False
) -> tuple[BaseGeometry | None, float]:
    """other 和 host 中面相交的那几段（host 的刀版坐标，按 other 的外轮廓裁好；holes=True 时挖掉
    other 自己的缝 / 孔）+ other 两侧都穿出多深。"""
    from pacxio.mockup.folding import _panel_shape

    m = f.inv[host] @ f.mats[other]  # other 的局部 → host 的局部
    a, b, c = (
        m[2, 0],
        m[2, 1],
        m[2, 2] * (-f.t / 2) + m[2, 3] + f.t / 2,
    )  # host 的 z（以中面为 0）= a·x + b·y + c
    shape = _panel_shape(f.dl, other) if holes else f.outlines[other]
    hull: Any = f.outlines[other]
    xs, ys = (
        hull.exterior.coords.xy
        if hasattr(hull, "exterior")
        else hull.convex_hull.exterior.coords.xy
    )
    z = a * np.asarray(xs) + b * np.asarray(ys) + c
    depth = float(min(z.max(), -z.min()))
    if abs(a) < 1e-9 and abs(b) < 1e-9:
        return None, depth
    x0, y0, x1, y1 = shape.bounds
    big = 10 * (x1 - x0 + y1 - y0 + 1)
    if abs(b) >= abs(a):  # 直线 a·x + b·y + c = 0
        line = LineString([(x, -(a * x + c) / b) for x in (x0 - big, x1 + big)])
    else:
        line = LineString([(-(b * y + c) / a, y) for y in (y0 - big, y1 + big)])
    cut = line.intersection(shape)
    if cut.is_empty:
        return None, depth
    pieces = []
    for g in getattr(cut, "geoms", [cut]):
        if g.geom_type != "LineString":
            continue
        pts = np.array(g.coords)
        loc = (m @ np.column_stack([pts, np.full(len(pts), -f.t / 2), np.ones(len(pts))]).T).T
        pieces.append(LineString(loc[:, :2]))
    return (shapely.union_all(pieces) if pieces else None), depth


def _tips_near(f: Folded, host: str, other: str) -> BaseGeometry | None:
    """other 插进 host 的端头：越过 host 纸厚的中线（或离中线 ≤ ¼·t），host 的刀版坐标。
    只是搁在 host 上面（和 other 自己在中线同一侧）的不算。"""
    v = f.vertices(other)
    loc = (f.inv[host] @ np.column_stack([v, np.ones(len(v))]).T).T[:, :3]
    z = loc[:, 2] + f.t / 2
    side = float(np.sign(z[np.argmax(np.abs(z))]))  # other 主体在 host 的哪一侧
    into = (z * side <= 0.25 * f.t) & (np.abs(z) <= REACH_T * f.t + REACH_MM)
    return shapely.MultiPoint(loc[into, :2]) if into.any() else None


def _neighbours(f: Folded, pid: str) -> set[str]:
    """pid 自己和用压线直接连着的面。"""
    me = f.dl.panel(pid)
    out = {pid} | {p.id for p in f.dl.panels if p.hinge and p.hinge.parent_panel == pid}
    if me.hinge:
        out.add(me.hinge.parent_panel)
    return out


def _up(f: Folded, walls: list[str]) -> np.ndarray | None:
    """墙的“上”方向（刀版上墙的 y 增大的方向 = 盒子的高度方向，朝顶）。"""
    ups = []
    for w in walls:
        x0, y0, x1, y1 = f.outlines[w].bounds
        a, b = f.world(w, np.array([[(x0 + x1) / 2, y0], [(x0 + x1) / 2, y1]]))
        ups.append(b - a)
    v = np.mean(ups, axis=0)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def shell_overhang(f: Folded, params: dict[str, Any]) -> list[Finding]:
    """盖、底翘到墙围成的盒体外面（没盖严 / 没锁上）。山形顶、提手、挂板、展示盖、斜口往上本来就高出墙，不查往上。"""
    walls = _walls(f)
    if len(walls) < 3 or (params.get("body") or {}).get("form") == "sheet":
        return []
    pts = np.vstack([f.vertices(w) for w in walls])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    up = _up(f, walls)
    raised = (params.get("top") or {}).get("kind") in RAISED_TOPS
    out = []
    held = through_slots(f)
    for p in f.dl.panels:
        if p.role.value not in ("lid", "bottom") or p.id in walls or p.id in held:
            continue
        v = f.vertices(p.id)
        n = np.abs(f.normal(p.id))
        worst = 0.0
        for k in range(3):
            below, above = float((lo[k] - v[:, k]).max()), float((v[:, k] - hi[k]).max())
            if raised and up is not None and abs(up[k]) > 0.9:
                if up[k] > 0:
                    above = 0.0
                else:
                    below = 0.0
            over = max(below, above)
            tol = (
                STACK_OUT_T * f.t
                if n[k] > PARALLEL_COS
                else max(PROTRUDE_T * f.t + PROTRUDE_MM, PROTRUDE_MIN)
            )
            if over > tol:
                worst = max(worst, over)
        if worst:
            out.append(Finding("overhang", translate("{panel} stands {distance:.1f} mm proud of the body after folding. The panel does not close or lock fully.", panel=p.id, distance=worst), (p.id,),
                               round(worst, 1)))  # fmt: skip
    return out


def through_slots(f: Folded) -> set[str]:
    """穿进缝里的面（插舌、插脚）：按设计会从缝里伸到墙外，不算伸出 / 翘出。"""
    return {other for _, other in _slot_pairs(f)}


def _slot_pairs(f: Folded) -> set[tuple[str, str]]:
    """(开缝的面, 穿进这道缝的面)。"""
    out: set[tuple[str, str]] = set()
    hang = _hang_ids(f)
    for host, poly, eid in _loops(f, with_ids=True):
        if eid in hang or not _is_slot(poly, f.t, eid):
            continue
        zone = poly.buffer(f.t + 0.5)
        for p in f.dl.panels:
            if p.id in _neighbours(f, host):
                continue
            cut, depth = _crossing(f, host, p.id)
            if cut is not None and depth > THROUGH_T * f.t and cut.intersects(zone):
                out.add((host, p.id))
    return out


def slots(f: Folded) -> list[Finding]:
    out = []
    hang = _hang_ids(f)
    for host, poly, eid in _loops(f, with_ids=True):
        if eid in hang or not _is_slot(poly, f.t, eid):
            continue
        zone = poly.buffer(f.t + 0.5)  # 真正进到缝里（缝外扩一层纸厚）
        neighbours = _neighbours(f, host)
        used = False
        for p in f.dl.panels:
            if p.id in neighbours:
                continue
            # 穿过去（两侧都超过 ¼·t）才算插进去：平铺盖在缝上的不算；斜着顶进去的角度再小也算
            # （0232 端摇盖外段只往里折几度、靠纸弯一下让角顶进斜缝——刚体模型的近似）
            cut, depth = _crossing(f, host, p.id)
            cut = cut if depth > THROUGH_T * f.t else None
            tips = (
                _tips_near(f, host, p.id)
                if abs(float(f.normal(p.id) @ f.normal(host))) <= PARALLEL_COS
                else None
            )
            if (cut is not None and cut.intersects(zone)) or (
                tips is not None and tips.intersects(poly.buffer(0.5))
            ):
                used = True
                break
        if not used:
            w, length = _width_length(poly)
            out.append(
                Finding(
                    "slot",
                    translate(
                        "Nothing enters the slot on {panel} ({length:.0f} × {width:.0f} mm) after folding",
                        panel=host,
                        length=length,
                        width=w,
                    ),
                    (host,),
                )
            )
    return out


def _stacked(f: Folded, a: str, b: str) -> float | None:
    """a、b 平行且相距 ≤ 贴合距离时返回相距多少，否则 None。"""
    if abs(float(f.normal(a) @ f.normal(b))) < PARALLEL_COS:
        return None
    pa = f.vertices(a).mean(axis=0)
    pb = f.vertices(b).mean(axis=0)
    d = abs(float((pb - pa) @ f.normal(a)))
    return d if d <= STACK_T * f.t + STACK_MM else None


def holes(f: Folded) -> list[Finding]:
    """孔（不是缝）不能被贴在背后的另一层挡住；顶上的手提板要成对贴合、孔对齐。"""
    out = []
    loops = [
        (pid, poly) for pid, poly, eid in _loops(f, with_ids=True) if not _is_slot(poly, f.t, eid)
    ]
    centres = {}
    for pid, poly in loops:
        c = np.array(poly.centroid.coords[0])
        centres[(pid, id(poly))] = (f.world(pid, c[None, :])[0], poly)
    by_panel: dict[str, list[Polygon]] = {}
    for pid, poly in loops:
        by_panel.setdefault(pid, []).append(poly)
    for pid, poly in loops:
        w, _ = _width_length(poly)
        cw = f.world(pid, np.array(poly.centroid.coords[0])[None, :])[0]
        for q in f.dl.panels:
            if q.id == pid or _stacked(f, pid, q.id) is None:
                continue
            ql = f.inv[q.id] @ np.array([*cw, 1.0])
            if not f.outlines[q.id].buffer(-f.t).contains(shapely.Point(ql[0], ql[1])):
                continue
            # q 盖住了孔的中心：q 自己在那儿也得有孔
            if not any(
                h.buffer(0.25 * w).contains(shapely.Point(ql[0], ql[1]))
                for h in by_panel.get(q.id, [])
            ):
                out.append(
                    Finding(
                        "hole",
                        translate(
                            "The hole in {panel} is blocked by {obstruction} behind it",
                            panel=pid,
                            obstruction=q.id,
                        ),
                        (pid, q.id),
                    )
                )
    handles = [pid for pid in by_panel if f.dl.panel(pid).role.value == "lid"]
    for pid in handles:
        partners = [h for h in handles if h != pid and _stacked(f, pid, h) is not None]
        if len(handles) > 1 and not partners:
            gaps = []
            for h in handles:
                if h == pid:
                    continue
                pa, pb = f.vertices(pid).mean(axis=0), f.vertices(h).mean(axis=0)
                gaps.append(float(np.linalg.norm(pa - pb)))
            out.append(Finding("handle", translate("Handle panel {panel} does not align with the other handle panel after folding (centres {gap:.0f} mm apart)", panel=pid, gap=min(gaps)),
                               (pid,), round(min(gaps), 1)))  # fmt: skip
    return out


def piercing(f: Folded) -> list[Finding]:
    """两块不平行的面，一块从另一块中间穿过去（穿过缝 / 孔的不算；只碰到边、贴着的不算）。"""
    from pacxio.mockup.folding import _panel_shape

    t = f.t
    edge = PIERCE_T * t
    inner = {p.id: _panel_shape(f.dl, p.id).buffer(-edge) for p in f.dl.panels}
    out: list[Finding] = []
    ids = [p.id for p in f.dl.panels]
    seen: set[tuple[str, str]] = set()
    # 一块面从另一块面的缝里穿过去（插舌、0232 端摇盖外段的角）：这一对不算穿插——刚体模型里斜着的
    # 外段会整条线穿过去，实物是纸弯一下、只有角顶进缝
    held = {pair for a, b in _slot_pairs(f) for pair in ((a, b), (b, a))}
    for host in ids:
        if inner[host].is_empty:
            continue
        for other in ids:
            if other == host or abs(float(f.normal(other) @ f.normal(host))) > PARALLEL_COS:
                continue
            if (host, other) in held:
                continue
            cut, depth = _crossing(f, host, other, holes=True)
            if cut is None or depth <= PIERCE_T * t + PIERCE_MM:
                continue
            inside = cut.intersection(inner[host])
            if inside.length <= edge:
                continue
            key = tuple(sorted((host, other)))
            if key in seen:
                continue
            seen.add(key)  # type: ignore[arg-type]
            out.append(Finding("pierce", translate("{panel} passes through the middle of {through} after folding (over {length:.0f} mm)", panel=other, through=host, length=inside.length),
                               (other, host), round(inside.length, 1)))  # fmt: skip
    return out


def open_ends(params: dict[str, Any]) -> int | None:
    """配方有几头开着（None = 不查封闭：片材）。"""
    body = params.get("body") or {}
    if body.get("form") == "sheet":
        return None
    n = 0
    for end in ("top", "bottom"):
        e = params.get(end) or {}
        if e.get("kind", "flaps") in ("open", "slope", "display") or e.get("flap") == "rim":
            n += 1  # 开口、斜口展示、展示盖、法兰 / 翻边顶（窄边围一圈，中间开着）
    if body.get("form") == "tray" and (params.get("top") or {}).get("kind", "open") == "open":
        n = max(n, 1)
    if body.get("front_low") or body.get("end_height") or body.get("window"):
        n += 1
    return n


def check(dl: Dieline, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """一张刀版 → {status: pass / fail / not_applicable, findings: [...]}。"""
    f = fold(dl)
    if f is None:
        return {"status": "not_applicable", "reason": translate("No folding plan"), "findings": []}
    params = params if params is not None else dict(dl.params)
    ends = open_ends(params)
    findings: list[Finding] = []
    if ends is not None:
        if ends == 0:  # 只查完全封口的；开着的盒子哪几面算“开口”说不准，靠下面几项
            findings += enclosure(f, 0)
        findings += slots(f)
    findings += protrusion(f)
    findings += shell_overhang(f, params)
    findings += piercing(f)
    findings += holes(f)
    return {"status": "fail" if findings else "pass",
            "findings": [{"check": x.check, "message": x.message, "panels": list(x.panels),
                          "value_mm": x.value_mm} for x in findings]}  # fmt: skip


def part_recipe(params: Any, part: str | None) -> dict[str, Any]:
    """盒型参数 → 这张刀版的配方（多件套取那一件）。"""
    data = params.model_dump() if hasattr(params, "model_dump") else dict(params)
    if part is not None and "parts" in data:
        return dict(data["parts"][part])
    return data


def issues(dl: Dieline, recipe: dict[str, Any]) -> list[Any]:
    """S07：几何引擎的刀版折好后机构不成立 → error（不出工厂交付包）。"""
    from pacxio.model.common import Issue

    if "structure" not in dl.style_meta:
        return []
    r = check(dl, recipe)
    part = dl.style_meta.get("part")
    if lang() == "zh":
        part = dl.style_meta.get("part_label") or part
    tag = translate("{part}: ", part=part) if part else ""
    return [Issue(id="S07", message=translate("{tag}Mechanism check failed: {message}", tag=tag, message=x['message']),
                  fix=translate("This structure does not work at these dimensions. Change the dimensions, choose another structure, or adjust the recipe so it closes and locks, then check again."))
            for x in r["findings"]]  # fmt: skip


__all__ = [
    "Finding",
    "check",
    "issues",
    "part_recipe",
    "enclosure",
    "fold",
    "holes",
    "open_ends",
    "piercing",
    "protrusion",
    "shell_overhang",
    "slots",
]
