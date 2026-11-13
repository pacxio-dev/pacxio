"""Strict, single-contour SVG import: straight segments and circular arcs only."""

import math
import re
from xml.etree import ElementTree as ET

from pacxio.geometry import Affine, Path, Segment, Vec, path_to_polygon
from pacxio.messages import t

TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")


def svg_contour(xml: str, width: float, height: float) -> Path:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError(t("Invalid SVG XML: {error}", error=error)) from error
    shapes = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] not in {"svg", "g"}]
    if any(e.get("transform") for e in root.iter()) or len(shapes) != 1:
        raise ValueError(
            t(
                "The SVG must contain a single outline. Flatten transforms first. Multiple shapes, references, and compound holes are unsupported."
            )
        )
    e = shapes[0]
    kind = e.tag.rsplit("}", 1)[-1]
    if kind == "polygon":
        values = [
            float(v)
            for v in re.findall(
                r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", e.get("points", "")
            )
        ]
        if len(values) < 6 or len(values) % 2:
            raise ValueError(t("The SVG polygon point list is invalid"))
        path = Path.from_points([Vec(values[i], values[i + 1]) for i in range(0, len(values), 2)])
    elif kind == "rect":
        x, y, w, h = [float(e.get(k, "0")) for k in ("x", "y", "width", "height")]
        rx = float(e.get("rx", e.get("ry", "0")))
        ry = float(e.get("ry", e.get("rx", "0")))
        if rx != ry or min(w, h) <= 0 or not 0 <= rx <= min(w, h) / 2:
            raise ValueError(t("SVG rect supports only valid circular corner radii"))
        path = Path.rounded_rect(x, y, w, h, rx)
    elif kind == "path":
        path = _path(e.get("d", ""))
    else:
        raise ValueError(
            t("Use a single SVG path, polygon, or rect. Curves must be circular arcs.")
        )
    if not all(
        math.isfinite(v)
        for s in path.segments
        for v in (s.start.x, s.start.y, s.end.x, s.end.y, s.bulge)
    ):
        raise ValueError(t("The SVG outline contains nonfinite coordinates"))
    poly = path_to_polygon(path)
    if not path.is_closed() or not poly.is_valid or poly.area <= 0:
        raise ValueError(
            t(
                "The SVG outline must be closed, have positive area, and be free of self-intersections"
            )
        )
    x0, y0, x1, y1 = path.bbox()
    if min(x1 - x0, y1 - y0) <= 0:
        raise ValueError(t("The SVG outline width and height must be positive"))
    scale = width / (x1 - x0)
    if not math.isclose((y1 - y0) * scale, height, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError(
            t(
                "The SVG outline aspect ratio does not match L×W. Circular arcs require uniform scaling."
            )
        )
    return path.transformed(Affine(scale, 0, 0, -scale, -x0 * scale, y1 * scale))


def _path(data: str) -> Path:
    tokens = TOKEN.findall(data)
    if TOKEN.sub("", data).replace(",", "").strip():
        raise ValueError(t("The SVG path contains invalid characters"))
    i, cmd, current = 0, "", Vec(0, 0)
    first: Vec | None = None
    segments = []
    closed = False
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        if cmd.upper() not in {"M", "L", "H", "V", "A", "Z"}:
            raise ValueError(
                t(
                    "The SVG path supports M, L, H, V, A, and Z commands. Bézier curves are unsupported."
                )
            )
        if closed:
            raise ValueError(t("The SVG may contain only one closed subpath"))
        if cmd.upper() == "Z":
            if first is None:
                raise ValueError(t("The SVG path has no starting point"))
            if not current.is_close(first):
                segments.append(Segment(current, first))
            closed = True
            continue
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "A": 7}[cmd.upper()]
        if i + n > len(tokens) or any(v.isalpha() for v in tokens[i : i + n]):
            raise ValueError(t("The SVG path has too few parameters"))
        vals = [float(v) for v in tokens[i : i + n]]
        i += n
        relative = cmd.islower()
        if cmd.upper() in {"M", "L"}:
            end = Vec(*vals) + (current if relative else Vec(0, 0))
        elif cmd.upper() == "H":
            end = Vec(vals[0] + (current.x if relative else 0), current.y)
        elif cmd.upper() == "V":
            end = Vec(current.x, vals[0] + (current.y if relative else 0))
        else:
            rx, ry, _angle, large, sweep, x, y = vals
            if rx <= 0 or not math.isclose(rx, ry) or large not in (0, 1) or sweep not in (0, 1):
                raise ValueError(
                    t("SVG A requires a positive-radius circular arc and flags of 0 or 1")
                )
            end = Vec(x, y) + (current if relative else Vec(0, 0))
        if cmd.upper() == "M":
            if first is not None:
                raise ValueError(t("The SVG may contain only one subpath"))
            first = end
            cmd = "l" if relative else "L"
        else:
            if first is None:
                raise ValueError(t("The SVG path must start with M"))
            bulge = 0.0
            if cmd.upper() == "A":
                chord = current.dist(end)
                if chord <= 1e-9:
                    raise ValueError(t("Split a complete circle into two arcs"))
                angle = 2 * math.asin(min(1, chord / (2 * rx)))
                angle = 2 * math.pi - angle if large else angle
                bulge = math.tan(angle / 4) * (1 if sweep else -1)
            if not current.is_close(end):
                segments.append(Segment(current, end, bulge))
        current = end
    if not closed:
        raise ValueError(t("Close the SVG path explicitly with Z"))
    return Path(segments, closed=True)
