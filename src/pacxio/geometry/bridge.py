"""Path 与 shapely 几何互转。

刀版本身保持直线 + 圆弧（D4）；出血、安全区、遮罩这类派生区域用 shapely 多边形运算，
圆弧按固定矢高离散化。派生区域不进入 DXF / CF2，所以离散化是安全的。
"""

from __future__ import annotations

from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from pacxio.geometry.primitives import Affine, Path, Segment, Vec

SAGITTA_MM = 0.01  # 圆弧离散化矢高：远小于任何印刷精度


def path_to_polygon(path: Path, sagitta: float = SAGITTA_MM) -> Polygon:
    pts = path.to_points(sagitta)
    return Polygon([p.as_tuple() for p in pts])


def split_loops(segments: list[Segment], tol: float = 1e-6) -> list[list[Segment]]:
    """首尾相接的段分成一圈一圈（外轮廓可以是几块分开的料，如平底袋的袋身和底）。

    整串首尾相接、中途没有回到起点的，照旧当一圈（中间断开的毛病交给 S01 查）。"""
    if (
        segments
        and segments[-1].end.is_close(segments[0].start, tol)
        and not any(sg.end.is_close(segments[0].start, tol) for sg in segments[:-1])
    ):
        return [list(segments)]
    loops: list[list[Segment]] = []
    for sg in segments:
        if (
            loops
            and loops[-1][-1].end.is_close(sg.start, tol)
            and not loops[-1][-1].end.is_close(loops[-1][0].start, tol)
        ):
            loops[-1].append(sg)
        else:
            loops.append([sg])
    return loops


def outline_shape(segments: list[Segment], sagitta: float = SAGITTA_MM) -> BaseGeometry:
    """外轮廓 → 区域：一圈就是那个多边形（和 path_to_polygon 一样）；几圈是 MultiPolygon。"""
    loops = split_loops(segments)
    polys = [path_to_polygon(Path(loop, closed=True), sagitta) for loop in loops]
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def affine_geom(geom: BaseGeometry, m: Affine) -> BaseGeometry:
    """对 shapely 几何施加 Affine（shapely 矩阵顺序为 [a, c, b, d, e, f]）。"""
    return affinity.affine_transform(geom, [m.a, m.c, m.b, m.d, m.e, m.f])


def polygons_of(geom: BaseGeometry) -> list[Polygon]:
    """把任意几何拆成多边形列表（忽略点、线）。"""
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out += polygons_of(g)
        return out
    return []


def polygon_to_path(poly: Polygon) -> Path:
    """外环转闭合 Path（全直线）。孔洞忽略——M2 涉及的区域没有孔洞。"""
    coords = list(poly.exterior.coords)[:-1]
    return Path.from_points([Vec(x, y) for x, y in coords], closed=True)


def shallow_polygons(geom: BaseGeometry) -> list[Polygon]:
    """只拆一层：Polygon / MultiPolygon / 集合里的 Polygon（和 polygons_of 不同，不递归、不去空）。"""
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def paint_order(geom: BaseGeometry) -> list[Polygon]:
    """画遮罩的顺序：外框大的先画。每块"先填外形、再把洞清成 0"时，落在别的块的洞里的块（细环里面的字、
    镂空字里的小岛）外框一定更小、排在后面，不会被前面那块的洞清掉（ 金属标：细环把里面的字清没了）。"""
    return sorted(shallow_polygons(geom), key=lambda p: -p.envelope.area)
