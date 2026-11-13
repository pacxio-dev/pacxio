"""刀版外轮廓自由角倒圆角（配方 `corner_radius`，2026-11-06 用户：彩盒纸张边角应该有倒圆角的选项）。

只倒外轮廓上**直线和直线相交的凸角**；角上（或离角不到 1.5·r）有压线、齿线、内切口端点的不倒（那是铰链、
接缝，倒了会切进去）；相邻两条边短到放不下（切点超过边长的 0.45）的不倒。外轮廓和那块面的轮廓同时改，
两者保持一致（面拼起来 = 外轮廓）。倒角只往里收：面的包围盒不变，折法、自检目标都不受影响。
"""

from __future__ import annotations

import math
from typing import Any

from pacxio.geometry import Path, Segment, arc_segment
from pacxio.model.dieline import OUTLINE_EDGE_ID, Dieline

TANGENT_MAX = 0.45  # 切点离角不超过相邻边长的这一成
GUARD_K = 1.5  # 离角 1.5·r 以内有其他线的端点就不倒
TOL = 1e-6


def _fillet(a: Segment, b: Segment, r: float) -> tuple[Segment, Segment, Segment] | None:
    """a 走到角 V、b 从 V 走出：返回 (缩短的 a, 圆弧, 缩短的 b)；放不下返回 None。只处理直线。"""
    if a.is_arc or b.is_arc:
        return None
    v = a.end
    u1 = (a.end - a.start).normalized()
    u2 = (b.end - b.start).normalized()
    cos_int = max(-1.0, min(1.0, (u1 * -1.0).dot(u2)))  # 两边夹角（内角）的余弦
    alpha = math.acos(cos_int)
    if alpha < math.radians(5) or alpha > math.radians(175):
        return None
    d = r / math.tan(alpha / 2)  # 切点离角
    if d > TANGENT_MAX * min(a.length(), b.length()):
        return None
    p1, p2 = v - u1 * d, v + u2 * d
    bis = (u1 * -1.0 + u2).normalized()
    c = v + bis * (r / math.sin(alpha / 2))
    s_ang = math.atan2(p1.y - c.y, p1.x - c.x)
    e_ang = math.atan2(p2.y - c.y, p2.x - c.x)
    ccw = (p1 - c).cross(p2 - c) > 0
    if ccw and e_ang < s_ang:
        e_ang += 2 * math.pi
    if not ccw and e_ang > s_ang:
        e_ang -= 2 * math.pi
    return Segment(a.start, p1), arc_segment(c, r, s_ang, e_ang), Segment(p2, b.end)


def _convex(a: Segment, b: Segment, orient: float) -> bool:
    return (a.end - a.start).cross(b.end - b.start) * orient > TOL


def _area(segs: list[Segment]) -> float:
    return sum(q.start.cross(q.end) for q in segs) / 2


def round_corners(dl: Dieline, r: float) -> int:
    """原地改刀版：外轮廓自由凸角倒半径 r 的圆角。返回倒了几个角。"""
    k = next((i for i, e in enumerate(dl.edges) if e.id == OUTLINE_EDGE_ID), None)
    if k is None or r <= 0:
        return 0
    outline = list(dl.edges[k].segments)
    guards = [
        p for e in dl.edges if e.id != OUTLINE_EDGE_ID for s in e.segments for p in (s.start, s.end)
    ]
    orient = 1.0 if _area(outline) > 0 else -1.0
    done = 0
    n = len(outline)
    out: list[Segment] = []
    replaced: dict[int, tuple[Segment, Segment, Segment]] = {}
    for i in range(n):
        a, b = outline[i], outline[(i + 1) % n]
        v = a.end
        if not _convex(a, b, orient) or any(v.dist(g) < GUARD_K * r for g in guards):
            continue
        f = _fillet(a, b, r)
        if f is None:
            continue
        replaced[i] = f
    if not replaced:
        return 0
    # 外轮廓：逐段拼（相邻两个角都倒了时，中间那条边两头都缩）
    starts = {i: outline[i].start for i in range(n)}
    ends = {i: outline[i].end for i in range(n)}
    arcs: dict[int, Segment] = {}
    for i, (sa, arc, sb) in replaced.items():
        ends[i] = sa.end
        starts[(i + 1) % n] = sb.start
        arcs[i] = arc
    for i in range(n):
        seg = outline[i]
        if not seg.is_arc:
            seg = Segment(starts[i], ends[i])
        out.append(seg)
        if i in arcs:
            out.append(arcs[i])
    dl.edges[k] = dl.edges[k].model_copy(update={"segments": out})
    # 面：找到以这个角为顶点的那块面，同样倒
    corners = {outline[i].end: replaced[i][1] for i in replaced}
    for pi, panel in enumerate(dl.panels):
        segs = list(panel.polygon.segments)
        hit = [j for j in range(len(segs)) if any(segs[j].end.is_close(v, TOL) for v in corners)]
        if not hit:
            continue
        m = len(segs)
        new_starts = {j: segs[j].start for j in range(m)}
        new_ends = {j: segs[j].end for j in range(m)}
        add: dict[int, Segment] = {}
        for j in hit:
            f = _fillet(segs[j], segs[(j + 1) % m], r)
            if f is None:
                continue
            sa, arc, sb = f
            new_ends[j] = sa.end
            new_starts[(j + 1) % m] = sb.start
            add[j] = arc
            done += 1
        rebuilt: list[Segment] = []
        for j in range(m):
            seg = segs[j] if segs[j].is_arc else Segment(new_starts[j], new_ends[j])
            rebuilt.append(seg)
            if j in add:
                rebuilt.append(add[j])
        dl.panels[pi] = panel.model_copy(update={"polygon": Path(rebuilt, closed=True)})
    return done


# ---- 内角（凹角）倒圆、卡纸默认圆角（2026-11-07 用户：彩盒卡纸常规工艺都用圆角）----

INNER_GUARD_K = 1.5  # 角上的圆弧半径不超过（离角最近的别的线头的距离）/ 1.5
FOLD_TYPES = ("crease", "reverse_crease")
ROUND_TOL = 1e-3
INNER_MIN_MM = 0.3  # 内角圆角放不下 r 时缩小，缩到这么小还放不下就不倒【初值】
CARD_OUTER_MM, CARD_INNER_MM = 2.0, 1.0  # 卡纸默认：外角 2、内角 1；瓦楞默认不倒【初值】


def _panel_paths(dl: Dieline) -> dict[int, list[Segment]]:
    return {i: list(p.polygon.segments) for i, p in enumerate(dl.panels)}


def _owner(segs: list[Segment], v: Any, u: Any, ending: bool) -> int | None:
    """面边界里沿方向 u、在 v 结束（ending）或从 v 开始的那一段的序号。"""
    for j, q in enumerate(segs):
        if q.is_arc:
            continue
        end, start = (q.end, q.start) if ending else (q.start, q.end)
        if not end.is_close(v, 1e-6) or q.length() < TOL:
            continue
        d = (q.end - q.start).normalized()
        if abs(d.cross(u)) < 1e-6 and d.dot(u) > 0:
            return j
    return None


def _fit_radius(a: Segment, b: Segment) -> float:
    """a、b 两条直线的角上放得下的最大圆角半径（切点离角不超过两边较短者的 TANGENT_MAX）。"""
    u1, u2 = (a.end - a.start).normalized(), (b.end - b.start).normalized()
    alpha = math.acos(max(-1.0, min(1.0, (u1 * -1.0).dot(u2))))
    if alpha < math.radians(5) or alpha > math.radians(175):
        return 0.0
    return TANGENT_MAX * min(a.length(), b.length()) * math.tan(alpha / 2) * (1 - 1e-9)


def round_inner_corners(dl: Dieline, r: float) -> int:
    """原地改刀版：外轮廓上直线和直线的凹角补半径 r 的圆弧（刀线不打尖角，防撕、好脱模）。
    圆弧补出的那一小块纸（≤ 半径）要归到一块面上：角上有压线时只能归到压线同一侧的面，不越过折线
    （越过了折好会凸出来）；找不到合适的面就不倒。面的包围盒因此大了一点的，倒角前的记在 style_meta，S02 照它量。
    附近有别的线头（离角 < 1.5·r）或边太短放不下 r 的，半径缩到放得下为止，小于 INNER_MIN_MM 就不倒。
    改完面拼起来要等于外轮廓，否则整张不改。"""
    import copy

    from shapely.geometry import Point

    from pacxio.geometry import Path as GPath
    from pacxio.geometry import path_to_polygon

    k = next((i for i, e in enumerate(dl.edges) if e.id == OUTLINE_EDGE_ID), None)
    if k is None or r <= 0:
        return 0
    backup = copy.deepcopy((dl.edges, dl.panels))
    outline = list(dl.edges[k].segments)
    orient = 1.0 if _area(outline) > 0 else -1.0
    guards = [
        p for e in dl.edges if e.id != OUTLINE_EDGE_ID for s in e.segments for p in (s.start, s.end)
    ]
    folds = [
        s for e in dl.edges if e.type in FOLD_TYPES for s in e.segments if s.is_line
    ]  # 压线 / 反压线：判断补出的那块在压线哪一侧
    n = len(outline)
    fillets: dict[int, tuple[Segment, Segment, Segment, float]] = {}
    for i in range(n):
        a, b = outline[i], outline[(i + 1) % n]
        if a.is_arc or b.is_arc or (a.end - a.start).cross(b.end - b.start) * orient > -TOL:
            continue  # 只要直线和直线的凹角
        v = a.end
        near = min((v.dist(g) for g in guards if v.dist(g) > TOL), default=math.inf)
        rr = min(r, near / INNER_GUARD_K, _fit_radius(a, b))
        if rr < min(r, INNER_MIN_MM) - TOL:
            continue
        f = _fillet(a, b, rr)
        if f is not None:
            fillets[i] = (*f, rr)
    if not fillets:
        return 0
    paths = _panel_paths(dl)
    flips = {i: (_area(s) > 0) != (orient > 0) for i, s in paths.items()}
    work = {
        i: ([q.reversed() for q in reversed(s)] if flips[i] else s) for i, s in paths.items()
    }  # 和外轮廓同向
    polys = {pi: path_to_polygon(dl.panels[pi].polygon) for pi in work}
    owners: dict[int, tuple[int, str, int]] = {}
    for i, (sa, _arc, sb, rr) in fillets.items():
        a, b = outline[i], outline[(i + 1) % n]
        v, p1, p2 = a.end, sa.end, sb.start
        u1, u2 = (a.end - a.start).normalized(), (b.end - b.start).normalized()
        bis = (u1 * -1.0 + u2).normalized()
        piece = v + bis * (0.2 * rr)  # 补出来那块里的一点
        lines = [
            (q.end - q.start).normalized()
            for q in folds
            if v.dist(q.start) < ROUND_TOL or v.dist(q.end) < ROUND_TOL
        ]
        cands: list[tuple[float, int, str, int]] = []
        for pi, segs in work.items():
            for side, u, ending in (("a", u1, True), ("b", u2, False)):
                j = _owner(segs, v, u, ending)
                if j is None:
                    continue
                into = (u * -1.0) if side == "a" else u
                ang = math.acos(max(-1.0, min(1.0, into.dot((((p1 + p2) * 0.5) - v).normalized()))))
                cands.append((ang, pi, side, j))
        best: tuple[float, int, str, int] | None = None
        for c in sorted(cands):
            pi = c[1]
            poly = polys[pi]
            local = poly.intersection(Point(v.x, v.y).buffer(3 * rr + 1.0))
            if local.is_empty:
                continue
            rp = local.representative_point()
            # 补出的那块和这块面在角上那条压线的两侧：那块属于压线另一边的面
            if any(
                lu.cross(piece - v) * lu.cross(type(v)(rp.x, rp.y) - v) < -1e-12 for lu in lines
            ):
                continue
            best = c
            break
        if best is not None:
            owners[i] = best[1:]
    if not owners:
        return 0
    starts = {i: outline[i].start for i in range(n)}
    ends = {i: outline[i].end for i in range(n)}
    for i in owners:
        sa, _, sb, _ = fillets[i]
        ends[i] = sa.end
        starts[(i + 1) % n] = sb.start
    out: list[Segment] = []
    for i in range(n):
        seg = outline[i] if outline[i].is_arc else Segment(starts[i], ends[i])
        out.append(seg)
        if i in owners:
            out.append(fillets[i][1])
    dl.edges[k] = dl.edges[k].model_copy(update={"segments": out})
    # 拼进面里：每次现找那条边（相邻两个角可能拼在同一条边上，前一次拼完下标就变了）
    for i, (pi, side, _j) in owners.items():
        sa, arc, sb, _ = fillets[i]
        a, b = outline[i], outline[(i + 1) % n]
        v, p1, p2 = a.end, sa.end, sb.start
        u = (a.end - a.start).normalized() if side == "a" else (b.end - b.start).normalized()
        segs = work[pi]
        j = _owner(segs, v, u, side == "a")
        if j is None:
            dl.edges, dl.panels = backup
            return 0
        q = segs[j]
        if side == "a":  # 沿 a 走到 v：改成 走到 p1 → 圆弧 → p2 → 回到 v
            segs[j : j + 1] = [Segment(q.start, p1), arc, Segment(p2, v)]
        else:  # 从 v 沿 b 出去：改成 v → p1 → 圆弧 → p2 → 接着沿 b
            segs[j : j + 1] = [Segment(v, p1), arc, Segment(p2, q.end)]
    for pi, segs in work.items():
        segs = [q.reversed() for q in reversed(segs)] if flips[pi] else segs
        dl.panels[pi] = dl.panels[pi].model_copy(update={"polygon": Path(segs, closed=True)})
    # 自检：面拼起来 = 外轮廓、外轮廓有效；不对就整张退回直角
    from shapely.ops import unary_union

    try:
        outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
        union = unary_union([path_to_polygon(p.polygon) for p in dl.panels])
        ok = outer.is_valid and union.symmetric_difference(outer).area < 1e-3
    except Exception:  # noqa: BLE001  任何几何异常都退回
        ok = False
    if not ok:
        dl.edges, dl.panels = backup
        return 0
    return len(owners)


def finish_radii(material_type: str, finish: Any = None) -> tuple[float, float]:
    """这张刀版用多大的外角 / 内角圆角：给了照给的；不给卡纸 2 / 1、瓦楞等不倒。"""
    card = material_type == "folding_carton"
    outer = getattr(finish, "outer", None)
    inner = getattr(finish, "inner", None)
    return (outer if outer is not None else (CARD_OUTER_MM if card else 0.0),
            inner if inner is not None else (CARD_INNER_MM if card else 0.0))  # fmt: skip


def apply_finish(dl: Dieline, material_type: str, finish: Any = None) -> dict[str, Any]:
    """出刀版最后一步的刀模圆角（整个项目一道工艺，不改各盒型的结构生成）：外角、内角。
    几何引擎配方里写了 corner_radius 的，外角已经按它倒过，这里不再倒。"""
    outer, inner = finish_radii(material_type, finish)
    info = (
        dl.style_meta.get("structure", {})
        if isinstance(dl.style_meta.get("structure"), dict)
        else {}
    )
    before = {q.id: q.polygon.bbox() for q in dl.panels}
    n_out = 0 if info.get("rounded_corners") or outer <= 0 else round_corners(dl, outer)
    n_in = round_inner_corners(dl, inner) if inner > 0 else 0
    rec: dict[str, Any] = {
        "outer": outer,
        "inner": inner,
        "outer_corners": n_out,
        "inner_corners": n_in,
    }
    # 内角补块让面的包围盒大了一点（≤ 半径）：记下倒角前的，量结构尺寸（S02）用它
    grown = {
        q.id: [round(v, 4) for v in before[q.id]]
        for q in dl.panels
        if q.id in before
        and any(abs(x - y) > 1e-6 for x, y in zip(q.polygon.bbox(), before[q.id], strict=True))
    }
    if grown:
        rec["structural_bbox"] = grown
    dl.style_meta = {**dl.style_meta, "corner_finish": rec}
    return rec


__all__ = ["apply_finish", "finish_radii", "round_corners", "round_inner_corners"]
