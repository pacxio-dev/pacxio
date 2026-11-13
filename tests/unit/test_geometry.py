"""几何基元回归测试（自旧 packow 项目移植）。

圆弧的 bulge 语义是全部刀版的地基：圆心算错、优弧/负 bulge 处理错误，会一路传到
DXF 与面贴合。断言覆盖：劣弧、优弧、负 bulge(顺时针)、镜像、圆弧 bbox 极值、解析面积。
"""

import math

import pytest

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment, mm_to_px, polyline_area

approx = pytest.approx


def test_rect() -> None:
    r = Path.rect(0, 0, 100, 50)
    assert r.area() == approx(5000)
    assert r.length() == approx(300)
    assert r.is_closed()
    assert not r.self_intersects()
    bb = r.bbox()
    assert (bb[2] - bb[0], bb[3] - bb[1]) == approx((100, 50))
    assert len(r.vertices()) == 4


def test_semicircle() -> None:
    s = Segment(Vec(0, 0), Vec(100, 0), 1.0)  # bulge=1 即半圆，逆时针途经最低点
    c, rad, _, theta, ccw = s.arc_info()
    assert rad == approx(50)
    assert (c.x, c.y) == approx((50, 0), abs=1e-9)
    assert theta == approx(math.pi)
    assert ccw
    assert s.length() == approx(math.pi * 50)
    assert s.arc_midpoint().y == approx(-50)
    neg = Segment(Vec(0, 0), Vec(100, 0), -1.0)
    assert neg.arc_midpoint().y == approx(50)
    assert neg.length() == approx(math.pi * 50)


def test_minor_arc() -> None:
    s = Segment(Vec(0, 0), Vec(10, 10), math.tan(math.radians(22.5)))
    _, r, _, theta, _ = s.arc_info()
    assert theta == approx(math.pi / 2)
    assert r == approx(10)
    assert s.length() == approx(math.pi * 5)


def test_major_arc() -> None:
    """优弧(θ>180°)是"弦中点+垂距"法必然算错的用例。"""
    s = Segment(Vec(10, 0), Vec(0, -10), math.tan(math.radians(67.5)))
    c, r, _, theta, _ = s.arc_info()
    assert theta == approx(1.5 * math.pi)
    assert r == approx(10)
    assert (c.x, c.y) == approx((0, 0), abs=1e-9)
    assert s.length() == approx(1.5 * math.pi * 10)
    mid = s.arc_midpoint()
    assert mid.x == approx(-10 * math.cos(math.pi / 4))
    assert mid.y == approx(10 * math.sin(math.pi / 4))


def test_arc_bbox() -> None:
    q = arc_segment(Vec(0, 0), 10.0, 0.0, math.pi / 2)
    assert q.length() == approx(math.pi * 5, abs=1e-9)
    assert q.bbox() == approx((0, 0, 10, 10), abs=1e-9)
    h = Segment(Vec(0, 0), Vec(100, 0), 1.0).bbox()
    assert (h[1], h[3]) == approx((-50, 0), abs=1e-9)


def test_rounded_rect() -> None:
    rr = Path.rounded_rect(0, 0, 100, 50, 5, "all")
    assert rr.area() == approx(5000 - (4 - math.pi) * 25)
    assert rr.length() == approx(2 * 90 + 2 * 40 + 2 * math.pi * 5)
    assert rr.is_closed()
    assert not rr.self_intersects()
    r2 = Path.rounded_rect(0, 0, 100, 50, 5, "bltr")
    assert r2.area() == approx(5000 - (2 - math.pi / 2) * 25)
    big = Path.rounded_rect(0, 0, 20, 20, 99, "all")  # 半径超过半宽时自动收窄
    assert big.area() == approx(400 - (4 - math.pi) * 100)


def test_transforms() -> None:
    r = Path.rect(0, 0, 100, 50)
    rotated = r.transformed(Affine.rotate(math.pi / 2))
    bb = rotated.bbox()
    assert (bb[2] - bb[0], bb[3] - bb[1]) == approx((50, 100))
    assert rotated.area() == approx(5000)

    q = arc_segment(Vec(0, 0), 10.0, 0.0, math.pi / 2)
    mirrored = Path([q]).transformed(Affine.mirror_x())
    assert mirrored.segments[0].bulge < 0  # 镜像翻转圆弧方向
    assert mirrored.area() == approx(-q.signed_area_contribution())
    back = mirrored.transformed(Affine.mirror_x())
    assert back.segments[0].bulge == approx(q.bulge, abs=1e-12)

    rr = r.transformed(Affine.identity().rotate_about(Vec(0, 0), math.pi / 2))
    bb = rr.bbox()
    assert (bb[0], bb[1]) == approx((-50, 0), abs=1e-9)


def test_self_intersection() -> None:
    bow = Path.from_points([Vec(0, 0), Vec(10, 10), Vec(10, 0), Vec(0, 10)], closed=True)
    assert bow.self_intersects()
    tri = Path.from_points([Vec(0, 0), Vec(10, 0), Vec(5, 8)], closed=True)
    assert not tri.self_intersects()
    assert tri.area() == approx(40)


def test_discretize_and_units() -> None:
    rr = Path.rounded_rect(0, 0, 100, 50, 5, "all")
    pts = rr.to_points()
    assert pts[0].is_close(pts[-1], 0.0)

    exact = rr.area()
    sags = (0.5, 0.1, 0.02, 0.005)
    errs = [abs(exact - abs(polyline_area(rr.to_points(max_sagitta=s)))) for s in sags]
    assert errs[0] > 0
    assert all(errs[i] > errs[i + 1] for i in range(len(errs) - 1))  # 单调收敛
    ratios = [e / s for e, s in zip(errs, sags, strict=True)]
    assert max(ratios) / min(ratios) < 1.6  # 误差与矢高同阶
    assert errs[-1] < 0.1

    assert mm_to_px(100, 300) == 1181
    assert mm_to_px(300, 300) == 3543
    assert mm_to_px(400, 300) == 4724
