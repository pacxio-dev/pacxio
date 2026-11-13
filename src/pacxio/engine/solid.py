"""盒身立体（几何引擎第 0 步，）：直棱柱——底是凸多边形，侧墙竖直。

约定：底面多边形在 xy 平面上、从上往下看逆时针排列，z 向上。第 i 面墙是从顶点 i 到 i+1 的那条边竖起来的；
从盒外看，墙按 0, 1, 2, … 从左往右排（展开时就是这个顺序）。

内尺寸给的是盒子里面的多边形；刀版按纸的中面算：中面多边形 = 内多边形往外偏 δ = k_panel·t / 2
（长方形时每面墙宽 = 内尺寸 + k_panel·t，和现有彩盒的算法一致）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon

from pacxio.messages import t as translate

Pt = tuple[float, float]


def rect_polygon(L: float, W: float) -> list[Pt]:
    """长方形底：墙 0 = 背（长 L）、1 = 侧 a（W）、2 = 正（L）、3 = 侧 b（W），和现有彩盒的面序一致。"""
    return [(L, W), (0.0, W), (0.0, 0.0), (L, 0.0)]


def regular_polygon(n: int, edge: float) -> list[Pt]:
    """正 n 边形（内边长 edge），逆时针；墙 0 是最上面那条水平边（背面一侧，从右往左走）。"""
    r = edge / (2 * math.sin(math.pi / n))
    start = math.pi / 2 - math.pi / n  # 顶点 0 在右上，顶点 1 在左上
    return [(r * math.cos(start + 2 * math.pi * i / n), r * math.sin(start + 2 * math.pi * i / n))
            for i in range(n)]  # fmt: skip


def octagon_polygon(L: float, W: float, c: float) -> list[Pt]:
    """长八角（长方形四角各切 45°，切角边长 c），逆时针；墙 0 是最上面那条水平边（从右往左），同 regular_polygon。"""
    return [(L - c, W), (c, W), (0.0, W - c), (0.0, c), (c, 0.0), (L - c, 0.0), (L, c), (L, W - c)]


def _ccw(poly: list[Pt]) -> bool:
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1], strict=True))
    return area > 0


def offset_polygon(poly: list[Pt], d: float) -> list[Pt]:
    """凸多边形每条边往外平移 d，顶点顺序不变（相邻两边的平移线求交点）。"""
    n = len(poly)
    lines = []
    for i in range(n):
        a, b = np.array(poly[i]), np.array(poly[(i + 1) % n])
        e = (b - a) / np.linalg.norm(b - a)
        out = np.array([e[1], -e[0]])  # 逆时针：右手边是外面
        lines.append((a + out * d, e))
    pts = []
    for i in range(n):
        p0, e0 = lines[i - 1]
        p1, e1 = lines[i]
        m = np.array([[e0[0], -e1[0]], [e0[1], -e1[1]]])
        t = np.linalg.solve(m, p1 - p0)
        q = p0 + e0 * t[0]
        pts.append((float(q[0]), float(q[1])))
    return pts


@dataclass(frozen=True)
class Prism:
    """中面上的直棱柱：base 是中面多边形（逆时针），h 是中面高。"""

    base: tuple[Pt, ...]
    h: float

    def __post_init__(self) -> None:
        if len(self.base) < 3 or not _ccw(list(self.base)):
            raise ValueError(
                translate("The base must be a counterclockwise polygon with at least three sides")
            )
        if (
            not Polygon(self.base).is_valid
            or Polygon(self.base).convex_hull.area - Polygon(self.base).area > 1e-6
        ):
            raise ValueError(translate("The base must be a convex polygon"))

    @property
    def n(self) -> int:
        return len(self.base)

    def edge(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        a = np.array(self.base[i % self.n], dtype=float)
        b = np.array(self.base[(i + 1) % self.n], dtype=float)
        return a, b

    def width(self, i: int) -> float:
        a, b = self.edge(i)
        return float(np.linalg.norm(b - a))

    def interior_deg(self, i: int) -> float:
        """顶点 i 处的内角（墙 i−1 和墙 i 之间）。"""
        p = np.array(self.base[(i - 1) % self.n])
        q = np.array(self.base[i % self.n])
        r = np.array(self.base[(i + 1) % self.n])
        u, v = p - q, r - q
        return math.degrees(math.acos(float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)))))

    def fold_deg(self, i: int) -> float:
        """墙 i−1 → 墙 i 的折角（外角 = 180 − 内角）。"""
        return 180.0 - self.interior_deg(i)

    def centroid(self) -> np.ndarray:
        c = Polygon(self.base).centroid
        return np.array([c.x, c.y])

    def wall_corners(self, i: int) -> list[tuple[float, float, float]]:
        """墙 i 的四个角（从外面看：左下、右下、右上、左上）。"""
        a, b = self.edge(i)
        return [(a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], self.h), (a[0], a[1], self.h)]


__all__ = ["Prism", "octagon_polygon", "offset_polygon", "rect_polygon", "regular_polygon"]
