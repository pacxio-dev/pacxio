"""标签异形外形导入：单条闭合轮廓，贝塞尔 / 椭圆弧用双圆弧逼近成圆弧（守 D4，C4 第 2 节）。

和 `svg_contour`（盒子用的严格版，只收直线 + 圆弧）的区别：这里收 C/S/Q/T 贝塞尔、rx ≠ ry 的 A、
<ellipse> / <circle>，全部转成圆弧，返回最大逼近误差，交给生成器写进 style_meta、超限报错。
"""

from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from pacxio.geometry import Affine, Path, Segment, Vec, path_to_polygon
from pacxio.geometry.arcfit import cubic, ellipse, fit_curve, quadratic
from pacxio.messages import t as translate

NUM = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
TOKEN = re.compile(r"[A-Za-z]|" + NUM)
ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def curve_contour(xml: str, width: float, height: float, tol: float) -> tuple[Path, float]:
    """读 SVG 轮廓，等比缩放到 width × height（y 翻成向上）。返回 (路径, 最大逼近误差 mm)。"""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError(translate("Invalid SVG XML: {error}", error=error)) from error
    shapes = [
        e for e in root.iter() if e.tag.rsplit("}", 1)[-1] not in {"svg", "g", "title", "desc"}
    ]
    if any(e.get("transform") for e in root.iter()) or len(shapes) != 1:
        raise ValueError(
            translate(
                "The SVG must contain a single outline. Flatten transforms first. Multiple shapes, references, and compound holes are unsupported."
            )
        )
    e = shapes[0]
    kind = e.tag.rsplit("}", 1)[-1]
    # 先在 SVG 单位里逼近，误差按最终缩放折算：先粗算缩放
    if kind == "path":
        data = e.get("d", "")
        rough, _ = _path(data, math.inf)  # 先粗逼近量出宽，把 mm 容差换成 SVG 单位
        bx0, _, bx1, _ = Path(rough, closed=True).bbox() if rough else (0.0, 0.0, 1.0, 0.0)
        scale_hint = width / max(bx1 - bx0, 1e-9)
        segs, err_svg = _path(data, tol / scale_hint)
    elif kind in ("ellipse", "circle"):
        cx, cy = float(e.get("cx", "0")), float(e.get("cy", "0"))
        rx = float(e.get("r", e.get("rx", "0")))
        ry = float(e.get("r", e.get("ry", "0")))
        if rx <= 0 or ry <= 0:
            raise ValueError(translate("SVG ellipse and circle radii must be positive"))
        k = width / (2 * rx)
        fit = ellipse(cx, cy, rx, ry, tol / k)
        segs, err_svg, scale_hint = fit.segments, fit.max_error, k
    elif kind == "polygon":
        vals = [float(v) for v in re.findall(NUM, e.get("points", ""))]
        if len(vals) < 6 or len(vals) % 2:
            raise ValueError(translate("The SVG polygon point list is invalid"))
        pts = [Vec(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
        segs = Path.from_points(pts).segments
        err_svg, scale_hint = 0.0, 1.0
    elif kind == "rect":
        x, y, w, h = [float(e.get(k, "0")) for k in ("x", "y", "width", "height")]
        r = float(e.get("rx", e.get("ry", "0")))
        if min(w, h) <= 0 or not 0 <= r <= min(w, h) / 2:
            raise ValueError(translate("The SVG rect has invalid dimensions or corner radii"))
        segs = Path.rounded_rect(x, y, w, h, r).segments if r else Path.rect(x, y, w, h).segments
        err_svg, scale_hint = 0.0, 1.0
    else:
        raise ValueError(translate("Use a single SVG path, polygon, rect, ellipse, or circle"))
    path = Path([s for s in segs if s.length() > 1e-9], closed=True)
    if not all(
        math.isfinite(v)
        for s in path.segments
        for v in (s.start.x, s.start.y, s.end.x, s.end.y, s.bulge)
    ):
        raise ValueError(translate("The SVG outline contains nonfinite coordinates"))
    poly = path_to_polygon(path)
    if not path.is_closed() or not poly.is_valid or poly.area <= 0:
        raise ValueError(
            translate(
                "The SVG outline must be closed, have positive area, and be free of self-intersections"
            )
        )
    x0, y0, x1, y1 = path.bbox()
    if min(x1 - x0, y1 - y0) <= 0:
        raise ValueError(translate("The SVG outline width and height must be positive"))
    scale = width / (x1 - x0)
    if not math.isclose((y1 - y0) * scale, height, rel_tol=1e-3, abs_tol=0.05):
        raise ValueError(
            translate(
                "The SVG outline aspect ratio {ratio:.4f} does not match L×W. Set W to {width:.2f}.",
                ratio=(x1 - x0) / (y1 - y0),
                width=(y1 - y0) * scale,
            )
        )
    return path.transformed(Affine(scale, 0, 0, -scale, -x0 * scale, y1 * scale)), err_svg * scale


def _path(data: str, tol_svg: float) -> tuple[list[Segment], float]:
    """SVG path → 圆弧 / 直线（SVG 单位）；tol_svg 是 SVG 单位里的逼近容差。返回 (段, 最大误差)。"""
    tokens = TOKEN.findall(data)
    if TOKEN.sub("", data).replace(",", "").strip():
        raise ValueError(translate("The SVG path contains invalid characters"))
    pieces: list[Segment] = []
    err = 0.0
    i, cmd = 0, ""
    cur = Vec(0, 0)
    first: Vec | None = None
    last_ctrl: Vec | None = None
    last_kind = ""
    closed = False
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        up = cmd.upper()
        if up not in ARGS:
            raise ValueError(translate("SVG path command {command} is unsupported", command=cmd))
        if closed:
            raise ValueError(translate("The SVG may contain only one closed subpath"))
        if up == "Z":
            if first is None:
                raise ValueError(translate("The SVG path has no starting point"))
            if not cur.is_close(first):
                pieces.append(Segment(cur, first))
            cur, closed = first, True
            continue
        n = ARGS[up]
        if i + n > len(tokens) or any(v.isalpha() for v in tokens[i : i + n]):
            raise ValueError(translate("The SVG path has too few parameters"))
        v = [float(t) for t in tokens[i : i + n]]
        i += n
        rel = cmd.islower()
        base = cur if rel else Vec(0, 0)

        def pt(k: int, b: Vec = base, v: list[float] = v) -> Vec:
            return Vec(v[k], v[k + 1]) + b

        if up == "M":
            if first is not None:
                raise ValueError(translate("The SVG may contain only one subpath"))
            cur = first = pt(0)
            cmd = "l" if rel else "L"
            last_kind = ""
            continue
        if first is None:
            raise ValueError(translate("The SVG path must start with M"))
        ctrl: Vec | None = None
        if up == "L":
            end = pt(0)
            pieces.append(Segment(cur, end))
        elif up == "H":
            end = Vec(v[0] + (cur.x if rel else 0), cur.y)
            pieces.append(Segment(cur, end))
        elif up == "V":
            end = Vec(cur.x, v[0] + (cur.y if rel else 0))
            pieces.append(Segment(cur, end))
        elif up in ("C", "S"):
            if up == "C":
                c1, c2, end = pt(0), pt(2), pt(4)
            else:
                c1 = cur * 2 - last_ctrl if last_kind == "C" and last_ctrl else cur
                c2, end = pt(0), pt(2)
            r = fit_curve(cubic(cur, c1, c2, end), 0, 1, tol_svg)
            pieces += r.segments
            err = max(err, r.max_error)
            ctrl = c2
        elif up in ("Q", "T"):
            if up == "Q":
                c, end = pt(0), pt(2)
            else:
                c = cur * 2 - last_ctrl if last_kind == "Q" and last_ctrl else cur
                end = pt(0)
            r = fit_curve(quadratic(cur, c, end), 0, 1, tol_svg)
            pieces += r.segments
            err = max(err, r.max_error)
            ctrl = c
        else:  # A
            rx, ry, rot, large, sweep = v[0], v[1], v[2], int(v[3]), int(v[4])
            end = Vec(v[5], v[6]) + base
            segs, e = _elliptic_arc(cur, end, abs(rx), abs(ry), rot, large, sweep, tol_svg)
            pieces += segs
            err = max(err, e)
        last_ctrl = ctrl
        last_kind = "C" if up in ("C", "S") else "Q" if up in ("Q", "T") else ""
        cur = end
    if not closed:
        raise ValueError(translate("Close the SVG path explicitly with Z"))
    return pieces, err


def _elliptic_arc(
    p0: Vec, p1: Vec, rx: float, ry: float, rot_deg: float, large: int, sweep: int, tol: float
) -> tuple[list[Segment], float]:
    """SVG 椭圆弧（端点参数化 → 中心参数化，SVG 规范 F.6.5）；rx = ry 时直接是圆弧。"""
    if p0.is_close(p1):
        return [], 0.0
    if rx <= 0 or ry <= 0:
        return [Segment(p0, p1)], 0.0
    phi = math.radians(rot_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0.x - p1.x) / 2, (p0.y - p1.y) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (p0.x + p1.x) / 2
    cy = sp * cxp + cp * cyp + (p0.y + p1.y) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi

    def f(t: float) -> Vec:
        a = t1 + dt * t
        x, y = rx * math.cos(a), ry * math.sin(a)
        return Vec(cx + cp * x - sp * y, cy + sp * x + cp * y)

    if math.isclose(rx, ry, rel_tol=1e-9):
        # 圆弧：按 90° 以内分段，精确（SVG y 向下，sweep=1 在 y 向下坐标里是顺时针 → 这里 bulge 取正）
        n = max(1, math.ceil(abs(dt) / (math.pi / 2)))
        out = []
        for k in range(n):
            a, b = f(k / n), f((k + 1) / n)
            theta = dt / n
            out.append(Segment(a, b, math.tan(theta / 4)))
        return out, 0.0
    r = fit_curve(f, 0, 1, tol)
    return r.segments, r.max_error


__all__ = ["curve_contour"]
