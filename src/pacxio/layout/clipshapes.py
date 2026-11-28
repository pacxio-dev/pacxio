"""Built-in geometric clip and punch-out shapes for packaging artwork.

Supports standard framing silhouettes (`arch`, `circle`, `oval`, `pill`, `rounded:<mm>`)
so agents and users do not have to hand-author SVG paths for common photo windows or frames.
"""

from __future__ import annotations

import math
import re

from shapely.geometry import Polygon

from pacxio.model.job import Box

BUILTIN_CLIP_NAMES = frozenset({"arch", "circle", "oval", "ellipse", "pill", "capsule", "rounded", "rect"})
_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")


def is_builtin_clip(spec: str | None) -> bool:
    """Return True if *spec* names a built-in geometric clip rather than an SVG file path."""
    if not spec:
        return False
    s = spec.strip().lower()
    if "/" in s or "\\" in s or s.endswith(".svg"):
        return False
    head = s.split(":", 1)[0]
    return head in BUILTIN_CLIP_NAMES


def parse_builtin_clip(spec: str) -> tuple[str, float | None]:
    """Parse a built-in clip spec like ``'arch'``, ``'arch:0.45'``, ``'circle'``, ``'rounded:6'``."""
    raw = spec.strip().lower()
    parts = raw.split(":")
    kind = parts[0]
    if kind not in BUILTIN_CLIP_NAMES or len(parts) > 2:
        raise ValueError(
            f"Unknown built-in clip shape {spec!r}; use arch, circle, oval, pill, or rounded:<mm>"
        )
    param: float | None = None
    if len(parts) == 2:
        if not _NUM_RE.match(parts[1]):
            raise ValueError(f"Invalid numeric parameter in clip shape {spec!r}")
        param = float(parts[1])
        if param <= 0:
            raise ValueError(f"Clip shape parameter must be positive: {spec!r}")
    if kind == "ellipse":
        kind = "oval"
    elif kind == "capsule":
        kind = "pill"
    return kind, param


def _arc_points(
    cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, steps: int = 48
) -> list[tuple[float, float]]:
    return [
        (
            cx + rx * math.cos(a0 + (a1 - a0) * i / steps),
            cy + ry * math.sin(a0 + (a1 - a0) * i / steps),
        )
        for i in range(steps + 1)
    ]


def builtin_clip_polygon(spec: str, box: Box) -> Polygon:
    """Generate a smooth Shapely polygon in panel-local mm coordinates (y-up) inside *box*."""
    kind, param = parse_builtin_clip(spec)
    x0, y0, w, h = float(box.x), float(box.y), float(box.w), float(box.h)
    x1, y1 = x0 + w, y0 + h
    cx, cy = x0 + w / 2.0, y0 + h / 2.0

    if kind == "rect":
        return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    if kind == "circle":
        r = min(w, h) / 2.0
        pts = _arc_points(cx, cy, r, r, 0.0, 2.0 * math.pi, steps=72)[:-1]
        return Polygon(pts)

    if kind == "oval":
        pts = _arc_points(cx, cy, w / 2.0, h / 2.0, 0.0, 2.0 * math.pi, steps=72)[:-1]
        return Polygon(pts)

    if kind == "arch":
        # Top half-ellipse/semicircle + vertical sides + flat bottom.
        # Default is a true semicircle (ry = w/2) capped to box height; optional param is arch height ratio (0..1) or mm if > 1.
        if param is None:
            ry = min(w / 2.0, h * 0.65)
        elif param <= 1.0:
            ry = min(h, max(1e-3, h * param))
        else:
            ry = min(h, param)
        spring_y = y1 - ry
        pts = [(x0, y0), (x1, y0)]
        pts.extend(_arc_points(cx, spring_y, w / 2.0, ry, 0.0, math.pi, steps=64))
        return Polygon(pts)

    if kind == "pill":
        r = min(w, h) / 2.0
    else:  # rounded
        r = min(w / 2.0, h / 2.0, param if param is not None else min(w, h) * 0.12)

    if r <= 1e-6:
        return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    # Rounded rectangle / stadium (CCW, y-up): bottom-right, top-right, top-left, bottom-left arcs.
    pts = []
    pts.extend(_arc_points(x1 - r, y0 + r, r, r, -math.pi / 2.0, 0.0, steps=18))
    pts.extend(_arc_points(x1 - r, y1 - r, r, r, 0.0, math.pi / 2.0, steps=18))
    pts.extend(_arc_points(x0 + r, y1 - r, r, r, math.pi / 2.0, math.pi, steps=18))
    pts.extend(_arc_points(x0 + r, y0 + r, r, r, math.pi, 1.5 * math.pi, steps=18))
    return Polygon(pts)


def parse_punch_spec(raw: str) -> tuple[str, Box]:
    """Parse ``--punch`` spec into ``(clip_spec, box_mm)``.

    Accepted forms (panel-local mm from bottom-left):
      - ``arch:x,y,w,h`` / ``arch:0.45:x,y,w,h``
      - ``circle:x,y,w,h`` / ``oval:x,y,w,h`` / ``pill:x,y,w,h`` / ``rect:x,y,w,h``
      - ``rounded:x,y,w,h`` / ``rounded:6:x,y,w,h``
      - ``x,y,w,h`` (shorthand for ``rect:x,y,w,h``)
    """
    text = raw.strip()
    if ":" not in text:
        clip_spec, coords_str = "rect", text
    else:
        parts = text.rsplit(":", 1)
        clip_spec, coords_str = parts[0].strip(), parts[1].strip()
    parse_builtin_clip(clip_spec)
    vals = [v.strip() for v in coords_str.split(",")]
    if len(vals) != 4:
        raise ValueError(
            f"Punch spec {raw!r} must end with x,y,w,h in mm (e.g. arch:14,22,52,78)"
        )
    x, y, w, h = (float(v) for v in vals)
    if w <= 0 or h <= 0:
        raise ValueError(f"Punch width and height must be positive: {raw!r}")
    return clip_spec, Box(x=x, y=y, w=w, h=h)


__all__ = [
    "BUILTIN_CLIP_NAMES",
    "builtin_clip_polygon",
    "is_builtin_clip",
    "parse_builtin_clip",
    "parse_punch_spec",
]
