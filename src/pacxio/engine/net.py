"""配方 → 刀版网（几何引擎第 0 步）：盒身立体展开成一条，装上糊口和两端封口，折角从立体算出来。

刀版按印刷面（盒外）看，y 向上；墙按 0, 1, … 从左往右排，糊口在第 0 面墙左边，糊在最后一面墙里面。
每块面的折法（第几步、折多少度、叠在第几层）从立体推出来，不查折法表：
  1 糊口折进去   2 墙围成筒   3–4 底（翼 → 盖 / 外摇盖）   5–6 顶（同上）
折出来以后墙和盖要和中面立体重合——verify.py 用这个检查每个配方。

尺寸规则（全部【初值】）：墙宽 = 中面多边形的边长；高 = 内高 + k_height·t；插舌长 = min(0.2·盖深 + 6, 25, 0.5·高)；
防尘翼深 = min(0.4·盖宽, 墙到中心的距离)，两侧按角平分线收；摇盖之间的槽 = max(槽宽下限, 2t)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely import set_precision
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from pacxio.engine.recipe import End, StructureParams
from pacxio.engine.solid import (
    Prism,
    octagon_polygon,
    offset_polygon,
    rect_polygon,
    regular_polygon,
)
from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.model.profile import CompensationSet

Pt = tuple[float, float]
P3 = tuple[float, float, float]

GLUE_W_CARTON_MM = 15.0  # 【初值】
GLUE_W_CORRUGATED_MM = 35.0
GLUE_BEVEL_MM = 5.0
TUCK_LEN_K, TUCK_LEN_EXTRA_MM, TUCK_LEN_MAX_MM, TUCK_LEN_H_RATIO = 0.2, 6.0, 25.0, 0.5
TUCK_CHAMFER_MM = 3.0
DUST_K = 0.4
DUST_INSET_MM = 1.0  # 防尘翼根部离墙的两边
MIN_FLAP_TOP_MM = 2.0
SLOT_MIN_CARTON_MM, SLOT_MIN_CORRUGATED_MM = 1.0, 3.0
# 多边形摇盖：扇形翼深 = 边到中心的距离 − 半道翼缝（各片沿角平分线收成三角、合到中心，相邻两片合起来让开一道翼缝）。
# 原来是 0.9 ×，中心留下约一成边心距的洞，封口合不严（S07 量出来的，2026-11-06）【初值】
ROOF_DEG = 90.0
FLAP_CLEARANCE_MM = 1.0  # 小翼和盖在刀版上至少隔开这么多
MIN_FLAP_AREA_K = 0.35
OVERLAP_MM2 = 0.01
GRID_MM = 1e-6  # 裁剩不到原来的这么多就不要这片翼  # 盒身是直棱柱：墙和顶 / 底面都是直角

RECT_NAMES = ("back", "side_a", "front", "side_b")


@dataclass(frozen=True)
class Move:
    keys: tuple[tuple[int, float], ...]
    layer: float = 0.0


@dataclass
class Net:
    prism: Prism
    names: list[str]
    x: list[float]  # 墙的竖压线位置（第 0 面墙左边 … 最后一面墙右边）
    panels: dict[str, tuple[PanelRole, Path]] = field(default_factory=dict)
    creases: list[Edge] = field(default_factory=list)
    moves: dict[str, Move] = field(default_factory=dict)
    targets: list[tuple[str, Pt, P3]] = field(
        default_factory=list
    )  # 面、刀版上一点、折好后该在的位置
    root: str = ""
    steps: int = 6
    open_step: int = 4
    meta: dict[str, Any] = field(default_factory=dict)


def _poly(points: list[Pt]) -> Path:
    return Path.from_points([Vec(x, y) for x, y in points], closed=True)


def _crease(eid: str, a: Pt, b: Pt, parent: str, child: str) -> Edge:
    return Edge(
        id=eid, type=EdgeType.CREASE, segments=[Segment(Vec(*a), Vec(*b))], panels=[parent, child]
    )


def base_polygon(p: StructureParams) -> list[Pt]:
    b = p.body
    if b.base == "rect":
        assert b.L is not None and b.W is not None
        return rect_polygon(b.L, b.W)
    if b.base == "octagon":
        assert b.L is not None and b.W is not None
        return octagon_polygon(b.L, b.W, b.chamfer or OCTAGON_CHAMFER_RATIO * min(b.L, b.W))
    assert b.sides is not None and b.edge is not None
    return regular_polygon(b.sides, b.edge)


OCTAGON_CHAMFER_RATIO = 0.25  # 长八角切角边长默认 0.25·短边【初值】


def base_bbox(p: StructureParams) -> tuple[float, float]:
    """内底面的外接矩形（宽、深），给规格书 / 效果图标尺寸用。"""
    xs, ys = zip(*base_polygon(p), strict=True)
    return (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))


def build_net(p: StructureParams, t: float, k: CompensationSet, corrugated: bool) -> Net:
    inner = base_polygon(p)
    prism = Prism(tuple(offset_polygon(inner, k.k_panel * t / 2)), p.body.h + k.k_height * t)
    n = prism.n
    rect = p.body.base == "rect"
    names = list(RECT_NAMES) if rect else [f"wall_{i}" for i in range(n)]
    gw = p.seam.width or (GLUE_W_CORRUGATED_MM if corrugated else GLUE_W_CARTON_MM)
    xs = [gw]
    for i in range(n):
        xs.append(xs[-1] + prism.width(i))
    net = Net(prism=prism, names=names, x=xs, root=names[n // 2])
    h = prism.h
    for i, pid in enumerate(names):
        net.panels[pid] = (
            PanelRole.BODY,
            _poly([(xs[i], 0), (xs[i + 1], 0), (xs[i + 1], h), (xs[i], h)]),
        )
        corners = prism.wall_corners(i)
        for q, c in zip(
            ((xs[i], 0.0), (xs[i + 1], 0.0), (xs[i + 1], h), (xs[i], h)), corners, strict=True
        ):
            net.targets.append((pid, q, c))
        if i:
            net.creases.append(
                _crease(f"crease_{names[i - 1]}_{pid}", (xs[i], 0), (xs[i], h), names[i - 1], pid)
            )
            net.moves[pid] = Move(((2, prism.fold_deg(i)),))
    bev = min(GLUE_BEVEL_MM, h / 4)
    net.panels["glue"] = (PanelRole.GLUE_FLAP, _poly([(gw, 0), (gw, h), (0, h - bev), (0, bev)]))
    net.creases.append(_crease(f"crease_{names[0]}_glue", (gw, 0), (gw, h), names[0], "glue"))
    net.moves["glue"] = Move(((1, prism.fold_deg(0)),), layer=1)
    slot = max(SLOT_MIN_CORRUGATED_MM if corrugated else SLOT_MIN_CARTON_MM, 2 * t)
    for end, top, first in ((p.bottom, False, 3), (p.top, True, 5)):
        _end(net, end, top, first, p, t, k, slot)
    net.meta = {"glue_w": gw, "slot": slot, "height": h}
    return net


# ---------------------------------------------------------------------------
# 两端封口
# ---------------------------------------------------------------------------


def _wall_frame(prism: Prism, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """墙 i 的底边：起点、沿边单位向量、指向盒内的单位向量（底面多边形逆时针，左手边是里面）。"""
    a, b = prism.edge(i)
    e = (b - a) / np.linalg.norm(b - a)
    return a, e, np.array([-e[1], e[0]])


def _lid_point(net: Net, k_wall: int, q: np.ndarray, top: bool) -> Pt:
    """顶 / 底面上一点（中面多边形坐标）→ 刀版：盖挂在墙 k_wall 上、绕它的上 / 下沿翻出去。"""
    a, e, nin = _wall_frame(net.prism, k_wall)
    y0, sgn = (net.prism.h, 1.0) if top else (0.0, -1.0)
    d = q - a
    return (net.x[k_wall] + float(d @ e), y0 + sgn * float(d @ nin))


def _far_edge(prism: Prism, k_wall: int) -> int:
    """离盖的铰链最远的那条边：插舌挂在盖的这条边上、插进这面墙。"""
    a, _, nin = _wall_frame(prism, k_wall)
    best, far = -1.0, (k_wall + prism.n // 2) % prism.n
    for j in range(prism.n):
        if j == k_wall:
            continue
        p, q = prism.edge(j)
        dist = float(((p + q) / 2 - a) @ nin)
        if dist > best + 1e-6:
            best, far = dist, j
    return far


def _flap_poly(
    net: Net, i: int, top: bool, depth: float, x0: float, x1: float, slant: bool
) -> list[Pt]:
    """墙 i 上 / 下沿的一片翼：底边 [x0, x1]，深 depth；slant 时两侧沿顶点的角平分线收（多边形扇形翼、防尘翼）。"""
    y0, sgn = (net.prism.h, 1.0) if top else (0.0, -1.0)
    if slant:
        li = depth / math.tan(math.radians(net.prism.interior_deg(i)) / 2)
        ri = depth / math.tan(math.radians(net.prism.interior_deg(i + 1)) / 2)
    else:
        li = ri = 0.0
    return [(x0, y0), (x1, y0), (x1 - ri, y0 + sgn * depth), (x0 + li, y0 + sgn * depth)]


def _fit_depth(net: Net, i: int, depth: float, x0: float, x1: float) -> float:
    """斜边收进去以后顶边至少留 MIN_FLAP_TOP_MM：放不下就减小深度。"""
    k = 1 / math.tan(math.radians(net.prism.interior_deg(i)) / 2) + 1 / math.tan(
        math.radians(net.prism.interior_deg(i + 1)) / 2
    )
    return max(0.0, min(depth, (x1 - x0 - MIN_FLAP_TOP_MM) / k))


def _end(net: Net, end: End, top: bool, first: int, p: StructureParams, t: float, k: CompensationSet,
         slot: float) -> None:  # fmt: skip
    if end.kind == "open":
        return
    prism, names = net.prism, net.names
    tag = "top" if top else "btm"
    y0 = prism.h if top else 0.0
    if end.kind == "flaps":
        _flaps(net, end, top, first, slot)
        return
    kw = (end.on or 0) % prism.n
    lid = f"{tag}_lid"
    pts = [_lid_point(net, kw, np.array(q), top) for q in prism.base]
    net.panels[lid] = (PanelRole.LID, _poly(pts))
    net.creases.append(
        _crease(f"crease_{names[kw]}_{lid}", (net.x[kw], y0), (net.x[kw + 1], y0), names[kw], lid)
    )
    net.moves[lid] = Move(((first + 1, ROOF_DEG),))
    z = prism.h if top else 0.0
    for q2, q in zip(pts, prism.base, strict=True):
        net.targets.append((lid, q2, (q[0], q[1], z)))
    m = _far_edge(prism, kw)
    lid_depth = float(Polygon(pts).bounds[3] - Polygon(pts).bounds[1])
    skip = {kw}
    if end.kind == "lid_tuck":
        skip.add(m)
        tl = end.tuck_len or min(TUCK_LEN_K * lid_depth + TUCK_LEN_EXTRA_MM, TUCK_LEN_MAX_MM,
                                 TUCK_LEN_H_RATIO * p.body.h)  # fmt: skip
        tuck = f"{tag}_tuck"
        a2, b2 = np.array(pts[m]), np.array(pts[(m + 1) % prism.n])
        u = (b2 - a2) / np.linalg.norm(b2 - a2)
        centre = np.mean(np.array(pts), axis=0)
        out = np.array([-u[1], u[0]])
        if out @ ((a2 + b2) / 2 - centre) < 0:
            out = -out
        ti = max(k.k_panel * t / 2, 0.5)
        c = min(TUCK_CHAMFER_MM, tl / 3)
        a3, b3 = a2 + u * ti, b2 - u * ti
        poly = [
            a3,
            b3,
            b3 + out * (tl - c),
            b3 + out * tl - u * c,
            a3 + out * tl + u * c,
            a3 + out * (tl - c),
        ]
        # 盖的那条边上插进插舌压线的两个端点，盖和插舌共用完全一样的一段边（斜边也对得上）
        lid_pts = (
            pts[: m + 1]
            + [(float(a3[0]), float(a3[1])), (float(b3[0]), float(b3[1]))]
            + pts[m + 1 :]
        )
        net.panels[lid] = (PanelRole.LID, _poly(lid_pts))
        net.panels[tuck] = (PanelRole.TUCK, _poly([(float(v[0]), float(v[1])) for v in poly]))
        net.creases.append(Edge(id=f"crease_{lid}_{tuck}", type=EdgeType.CREASE,
                                segments=[Segment(Vec(*map(float, a3)), Vec(*map(float, b3)))], panels=[lid, tuck]))  # fmt: skip
        net.moves[tuck] = Move(((first, 45.0), (first + 1, ROOF_DEG)), layer=1)
    width = prism.width(kw)
    # 盖（和插舌）在刀版上占的地方：别的墙上的小翼碰到就裁掉（多边形的盖比它挂的那面墙宽）
    keep_out = unary_union([Polygon([(sg.start.x, sg.start.y) for sg in path.segments])
                            for pid, (_, path) in net.panels.items() if pid.startswith(tag + "_")]).buffer(FLAP_CLEARANCE_MM)  # fmt: skip
    for i in range(prism.n):
        if i in skip:
            continue
        a, _, nin = _wall_frame(prism, i)
        to_centre = float((prism.centroid() - a) @ nin)
        x0, x1 = net.x[i] + DUST_INSET_MM, net.x[i + 1] - DUST_INSET_MM
        dd = end.dust_depth or min(DUST_K * width, to_centre)
        dd = _fit_depth(net, i, dd, x0, x1)
        if dd <= 0:
            continue
        fid = f"{tag}_flap_{names[i]}"
        shape = _clear_of(_flap_poly(net, i, top, dd, x0, x1, True), keep_out, (x0, y0), (x1, y0))
        if shape is None:
            continue
        net.panels[fid] = (PanelRole.DUST_FLAP, _poly(shape))
        net.creases.append(_crease(f"crease_{names[i]}_{fid}", (x0, y0), (x1, y0), names[i], fid))
        net.moves[fid] = Move(((first, ROOF_DEG),))


def _clear_of(poly: list[Pt], keep_out: Any, a: Pt, b: Pt) -> list[Pt] | None:
    """翼去掉和盖重叠的部分；剩下的太小（或连压线都被占了）就不要这片翼。"""
    shape = Polygon(poly)
    if not shape.intersects(keep_out):
        return poly
    rest = shape.difference(keep_out)
    pieces = [g for g in getattr(rest, "geoms", [rest]) if isinstance(g, Polygon) and g.area > 1e-6]
    base = LineString([a, b])
    pieces = [g for g in pieces if g.buffer(1e-6).covers(base)]
    if not pieces or pieces[0].area < MIN_FLAP_AREA_K * shape.area:
        return None
    return [(float(x), float(y)) for x, y in list(pieces[0].exterior.coords)[:-1]]


def _flaps(net: Net, end: End, top: bool, first: int, slot: float) -> None:
    """摇盖：长方形盒身四片（侧面两片内摇盖先折，背 / 正两片外摇盖后折、叠在外面）；多边形每面一片扇形翼。"""
    prism, names = net.prism, net.names
    tag = "top" if top else "btm"
    y0 = prism.h if top else 0.0
    rect = prism.n == 4 and names[0] == "back"
    half = prism.width(1) / 2 if rect else 0.0
    for i in range(prism.n):
        x0 = net.x[i] + slot / 2
        x1 = net.x[i + 1] - slot / 2
        fid = f"{tag}_{names[i]}" if rect else f"{tag}_flap_{names[i]}"
        if rect:
            outer = i in (0, 2)
            if end.flap == "full":
                depth = 2 * half
            elif end.flap == "overlap" and outer:
                depth = half + end.overlap / 2
            else:
                depth = half
            poly = _flap_poly(net, i, top, depth, x0, x1, False)
            step, layer = (first + 1, 1.0) if outer else (first, 0.0)
        else:
            a, _, nin = _wall_frame(prism, i)
            depth = _fit_depth(net, i, float((prism.centroid() - a) @ nin) - slot / 2, x0, x1)
            poly = _flap_poly(net, i, top, depth, x0, x1, True)
            step, layer = first, 0.0
        net.panels[fid] = (
            PanelRole.LID if rect and i in (0, 2) else PanelRole.DUST_FLAP,
            _poly(poly),
        )
        net.creases.append(_crease(f"crease_{names[i]}_{fid}", (x0, y0), (x1, y0), names[i], fid))
        net.moves[fid] = Move(((step, ROOF_DEG),), layer)


def outline(net: Net) -> Path:
    """所有面并起来的外轮廓（第 0 步的机构都是直线）；面在刀版上不能互相重叠（展开不能自重叠）。"""
    polys = {
        pid: Polygon([(s.start.x, s.start.y) for s in path.segments])
        for pid, (_, path) in net.panels.items()
    }
    ids = list(polys)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if polys[a].intersection(polys[b]).area > OVERLAP_MM2:
                raise ValueError(
                    translate(
                        "{first} and {second} overlap on the flat dieline. Attach the lid to another wall or reduce flap depth.",
                        first=a,
                        second=b,
                    )
                )
    # 斜边上挂的面（多边形盖上的插舌）浮点误差会留下细缝：按 1e-6 mm 的格子对齐再并
    shape = unary_union([set_precision(q, GRID_MM) for q in polys.values()])
    if not isinstance(shape, Polygon) or shape.interiors:
        raise ValueError(
            translate(
                "The dieline panels do not form a continuous outline, or leave a hole. Check the closure parameters."
            )
        )
    pts = list(shape.exterior.coords)[:-1]
    return _poly([(float(x), float(y)) for x, y in pts])


__all__ = ["Move", "Net", "base_bbox", "base_polygon", "build_net", "outline"]
