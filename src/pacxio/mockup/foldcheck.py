"""折叠检查 S06（）：合上以后，面和面有没有穿插。

每块面当成一块薄板：刀版多边形（挖掉内切口、窗口）× 纸厚，印刷面在局部 z = 0，纸厚向 -z。
在 A 的中面上按网格取点，换到 B 的局部坐标里看落没落进 B 那块板：
- 板厚方向：两块面平行时 A 的点代表 ±t/2 的纸，B 的板两侧各放宽 t/2·|cos θ|。平行的两层叠进去
  不超过 PARALLEL_ALLOW·t 不算——防尘翼压线比盖压线低 0.5·t（`DUST_OFFSET_T_RATIO`）就是按这样
  设计的，剩下半层由压线圆角和纸的压缩吃掉，8 种 L2 盒型实物都折过；
  斜着 / 垂直碰到 B 时，按 A 的纸在 B 的厚度里占了多深算，同样超过 PARALLEL_ALLOW·t 才报
  （插舌、内侧板的边贴着墙、只进去零点几毫米是接触，纸一压就过去了）；
- 板面方向：B 的轮廓往里收 t + EDGE_TOL，两块面各自压线两边再让 (1 + |layer|)·t + EDGE_TOL。
  转轴在印刷面上的刚体折叠里，压线、拐角处两块面必然在一条 t × t 的角上重叠，layer 把子面往里
  挪了几层纸厚时这个角跟着变大（实物是压线圆角和纸的变形），这一圈不算。

只查合上的状态：插舌、锁底这类靠纸变形才进得去的地方，过程中必然穿插（M5_FOLD_3D.md 第 2.7 节）。
只查几何，不说明能不能插得进、扣得牢；那要实物折样。数值都是【初值】。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from pacxio.mockup.folding import (
    FoldedPart,
    Mat,
    _panel_shape,
    build_part,
    panel_matrices,
    plan_for,
)
from pacxio.model.dieline import Dieline

EDGE_TOL_MM = 0.2  # 【初值】压线 / 拐角那一圈之外再让这么多，才算穿插
PARALLEL_ALLOW = 0.5  # 【初值】最多叠进去几成纸厚还不算（防尘翼按 0.5·t 设计；斜插同样按这个）
PARALLEL_COS = 0.999  # 法向夹角小于约 2.6° 算平行
AREA_MIN_MM2 = 1.0  # 【初值】重叠面积小于这个不报
GRID_MIN_MM, GRID_MAX_MM = 0.25, 0.5  # 取点间距：纸厚一半，夹在这两个数之间


@dataclass(frozen=True)
class Clash:
    """合上后两块面重叠：a、b 是面 id（按 id 排序），where 是 on 这块面上重叠处在刀版坐标里的包围盒。"""

    a: str
    b: str
    area_mm2: float
    overlap_mm: float  # 两块板最多叠进去多少（平行时就是两层纸的重叠厚度）
    parallel: bool
    on: str
    where: tuple[float, float, float, float]


def grid_mm(caliper_mm: float) -> float:
    return min(GRID_MAX_MM, max(GRID_MIN_MM, caliper_mm / 2))


def _samples(shape: BaseGeometry, g: float) -> np.ndarray:
    """面内按网格取点（格子中心）。"""
    if shape.is_empty:
        return np.empty((0, 2))
    x0, y0, x1, y1 = shape.bounds
    xs = np.arange(x0 + g / 2, x1, g)
    ys = np.arange(y0 + g / 2, y1, g)
    if not len(xs) or not len(ys):
        return np.empty((0, 2))
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    return pts[shapely.contains_xy(shape, pts[:, 0], pts[:, 1])]


def _crease_zones(part: FoldedPart) -> dict[str, BaseGeometry]:
    """每块面压线两边不查的那一条（折了的铰链才算；layer 越多让得越宽）。"""
    t = part.dl.caliper_mm
    lines = {e.id: e for e in part.dl.edges}
    zones: dict[str, list[BaseGeometry]] = {}
    for j in part.joints:
        if not j.keys or not j.keys[-1][1]:
            continue
        width = (1 + abs(j.layer)) * t + EDGE_TOL_MM
        for edge_id in j.edges:
            for s in lines[edge_id].segments:
                band = LineString([s.start.as_tuple(), s.end.as_tuple()]).buffer(width)
                zones.setdefault(j.child, []).append(band)
                zones.setdefault(j.parent, []).append(band)
    return {pid: shapely.union_all(bands) for pid, bands in zones.items()}


def fold_clashes(dl: Dieline) -> list[Clash]:
    """合上后穿插的面（每对报一次，取两个方向里重叠大的那个）。没有折法表的盒型返回空。"""
    plan = plan_for(dl)
    if plan is None:
        return []
    part = build_part(dl, plan)
    mats = panel_matrices(part, plan.steps)
    t = dl.caliper_mm
    g = grid_mm(t)
    margin = min(EDGE_TOL_MM, t / 4)
    zones = _crease_zones(part)
    shapes: dict[str, BaseGeometry] = {}
    inner: dict[str, BaseGeometry] = {}
    for p in dl.panels:
        full = _panel_shape(dl, p.id)
        zone = zones.get(p.id)
        shapes[p.id] = full.difference(zone) if zone is not None else full
        inner[p.id] = shapes[p.id].buffer(-(t + EDGE_TOL_MM))
    inverse: dict[str, Mat] = {pid: np.linalg.inv(m) for pid, m in mats.items()}
    normals = {pid: m[:3, 2] for pid, m in mats.items()}  # 局部 +z（印刷面朝向）在合上后的方向
    found: dict[tuple[str, str], Clash] = {}
    for a, shape in shapes.items():
        pts = _samples(shape, g)
        if not len(pts):
            continue
        local = np.column_stack([pts, np.full(len(pts), -t / 2), np.ones(len(pts))])
        world = mats[a] @ local.T
        for b, region in inner.items():
            if b == a or region.is_empty:
                continue
            cos = abs(float(normals[a] @ normals[b]))
            half = t / 2 * cos
            reach = half + t / 2 - PARALLEL_ALLOW * t * cos - margin  # 离 B 中面多近才算叠进去
            uvw = inverse[b] @ world
            off = np.abs(uvw[2] + t / 2)
            hit = off < reach
            if not hit.any():
                continue
            idx = np.flatnonzero(hit)
            idx = idx[shapely.contains_xy(region, uvw[0, idx], uvw[1, idx])]
            area = len(idx) * g * g
            if area < AREA_MIN_MM2:
                continue
            parallel = cos > PARALLEL_COS
            if parallel:  # 两层纸叠进去多厚
                depth = float((half + t / 2 - off[idx]).max())
            else:  # 斜着 / 垂直插进 B：A 的纸在 B 的厚度里占了多深（只碰到边的一列点约为 0）
                w = uvw[2, idx]
                depth = float(w.max() - w.min()) + g * float(np.sqrt(max(0.0, 1 - cos * cos)))
                if depth <= PARALLEL_ALLOW * t + margin:
                    continue
            box = pts[idx]
            first, second = sorted((a, b))
            clash = Clash(
                first,
                second,
                area_mm2=round(area, 2),
                overlap_mm=round(depth, 3),
                parallel=parallel,
                on=a,
                where=(
                    round(float(box[:, 0].min()), 2),
                    round(float(box[:, 1].min()), 2),
                    round(float(box[:, 0].max()), 2),
                    round(float(box[:, 1].max()), 2),
                ),
            )
            key = (clash.a, clash.b)
            if key not in found or found[key].area_mm2 < clash.area_mm2:
                found[key] = clash
    return sorted(found.values(), key=lambda c: (-c.area_mm2, c.a, c.b))


__all__ = ["AREA_MIN_MM2", "EDGE_TOL_MM", "PARALLEL_ALLOW", "Clash", "fold_clashes", "grid_mm"]
