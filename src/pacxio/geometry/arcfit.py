"""曲线 → 圆弧（双圆弧逼近），守 D4：椭圆、贝塞尔等曲线外形转成只有直线和圆弧的路径。

 节：标签的椭圆 / 异形外形用双圆弧逼近，误差 ≤ 配置档
labels.arc_fit_tol_mm（默认 0.05 mm【初值】）。每段曲线在端点处切线连续；误差超了就从参数中点切开再逼近。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from pacxio.geometry.primitives import Segment, Vec

Curve = Callable[[float], Vec]
MAX_DEPTH = 18
SAMPLES = 9  # 每段曲线上拿来量误差的点


@dataclass
class FitResult:
    segments: list[Segment]
    max_error: float


def _unit(v: Vec) -> Vec | None:
    n = v.length()
    return None if n < 1e-12 else v / n


def _tangent(f: Curve, t: float, t0: float, t1: float) -> Vec:
    """数值切线（单位向量）；端点处只往区间里面取差分。退化（控制点重合）时把步长放大再试。"""
    h = (t1 - t0) * 1e-4
    for _ in range(6):
        a, b = max(t0, t - h), min(t1, t + h)
        d = _unit(f(b) - f(a))
        if d is not None:
            return d
        h *= 10
    d = _unit(f(t1) - f(t0))
    return d if d is not None else Vec(1, 0)


def _arc_from_tangent(p: Vec, tangent: Vec, q: Vec) -> Segment:
    """从 p 出发、在 p 处切线为 tangent、到 q 的圆弧（切线和弦共线时是直线）。"""
    chord = q - p
    alpha = math.atan2(tangent.cross(chord), tangent.dot(chord))
    if abs(alpha) < 1e-9:
        return Segment(p, q)
    return Segment(p, q, math.tan(alpha / 2))


def _biarc(p0: Vec, t0: Vec, p1: Vec, t1: Vec) -> list[Segment] | None:
    v = p1 - p0
    if v.length() < 1e-9:
        return []
    t = t0 + t1
    denom = 2 * (1 - t0.dot(t1))
    vt = v.dot(t)
    if abs(denom) < 1e-12:  # 两端切线平行同向
        if abs(t0.cross(v)) < 1e-9 * max(1.0, v.length()):
            return [Segment(p0, p1)]
        vt1 = v.dot(t1)
        if abs(vt1) < 1e-12:
            return None
        d = v.dot(v) / (4 * vt1)
    else:
        disc = vt * vt + denom * v.dot(v)
        if disc < 0:
            return None
        d = (-vt + math.sqrt(disc)) / denom
    if d <= 0:
        return None
    j = (p0 + p1 + (t0 - t1) * d) / 2
    if j.dist(p0) < 1e-9 or j.dist(p1) < 1e-9:
        return [_arc_from_tangent(p0, t0, p1)]
    first = _arc_from_tangent(p0, t0, j)
    second = _arc_from_tangent(p1, -t1, j).reversed()
    return [first, second]


def _dist_to_segment(p: Vec, s: Segment) -> float:
    if s.is_line:
        d = s.end - s.start
        n = d.length_sq()
        k = 0.0 if n < 1e-18 else max(0.0, min(1.0, (p - s.start).dot(d) / n))
        return p.dist(s.start + d * k)
    centre, r, a0, sweep, ccw = s.arc_info()
    ang = math.atan2(p.y - centre.y, p.x - centre.x)
    rel = (ang - a0) if ccw else (a0 - ang)
    rel %= 2 * math.pi
    if rel <= sweep + 1e-12:
        return abs(p.dist(centre) - r)
    return min(p.dist(s.start), p.dist(s.end))


def _error(f: Curve, ta: float, tb: float, segs: list[Segment]) -> float:
    worst = 0.0
    for i in range(1, SAMPLES + 1):
        p = f(ta + (tb - ta) * i / (SAMPLES + 1))
        worst = max(worst, min(_dist_to_segment(p, s) for s in segs))
    return worst


def fit_curve(f: Curve, ta: float, tb: float, tol: float, depth: int = 0) -> FitResult:
    """把参数曲线 f 在 [ta, tb] 上逼近成圆弧（相邻段切线连续），最大误差 ≤ tol（到 MAX_DEPTH 为止）。"""
    p0, p1 = f(ta), f(tb)
    t0, t1 = _tangent(f, ta, ta, tb), _tangent(f, tb, ta, tb)
    segs = _biarc(p0, t0, p1, t1)
    if segs is not None:
        err = _error(f, ta, tb, segs) if segs else 0.0
        if err <= tol or depth >= MAX_DEPTH:
            return FitResult(segs, err)
    mid = (ta + tb) / 2
    a = fit_curve(f, ta, mid, tol, depth + 1)
    b = fit_curve(f, mid, tb, tol, depth + 1)
    return FitResult(a.segments + b.segments, max(a.max_error, b.max_error))


def ellipse(cx: float, cy: float, rx: float, ry: float, tol: float) -> FitResult:
    """椭圆（逆时针，起点在右端），按四个象限各自逼近：象限端点处切线是精确的。"""
    if math.isclose(rx, ry, rel_tol=1e-9):
        c = Vec(cx, cy)
        top, bottom = c + Vec(0, ry), c - Vec(0, ry)
        right, left = c + Vec(rx, 0), c - Vec(rx, 0)
        b = math.tan(math.pi / 8)  # 每段 90°：bulge = tan(90°/4)
        segs = [Segment(right, top, b), Segment(top, left, b), Segment(left, bottom, b),
                Segment(bottom, right, b)]  # fmt: skip
        return FitResult(segs, 0.0)

    def f(t: float) -> Vec:
        return Vec(cx + rx * math.cos(t), cy + ry * math.sin(t))

    out: list[Segment] = []
    err = 0.0
    for k in range(4):
        r = fit_curve(f, k * math.pi / 2, (k + 1) * math.pi / 2, tol)
        out += r.segments
        err = max(err, r.max_error)
    return FitResult(out, err)


def cubic(p0: Vec, c1: Vec, c2: Vec, p1: Vec) -> Curve:
    def f(t: float) -> Vec:
        u = 1 - t
        return p0 * (u * u * u) + c1 * (3 * u * u * t) + c2 * (3 * u * t * t) + p1 * (t * t * t)

    return f


def quadratic(p0: Vec, c: Vec, p1: Vec) -> Curve:
    def f(t: float) -> Vec:
        u = 1 - t
        return p0 * (u * u) + c * (2 * u * t) + p1 * (t * t)

    return f


__all__ = ["FitResult", "cubic", "ellipse", "fit_curve", "quadratic"]
