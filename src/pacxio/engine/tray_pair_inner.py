"""两面回折内墙带耳片的托盘，几何和尺寸均为初值。"""

from shapely.geometry import Polygon
from shapely.ops import unary_union

from pacxio.engine.tray import _ccw
from pacxio.geometry import Path, Vec
from pacxio.messages import t
from pacxio.model.dieline import PanelRole
from pacxio.styles.corrugated import crease
from pacxio.styles.tray import TrayDims, TrayGeom


def transform(d: TrayDims, geom: TrayGeom) -> TrayGeom:
    panels = {k: v for k, v in geom.polys.items() if not k.startswith("ear_")}
    panels["front"] = (PanelRole.BODY, Path.rect(0, -d.hf, d.bl, d.hf))
    panels["back"] = (PanelRole.BODY, Path.rect(0, d.bw, d.bl, d.hf))
    creases = [
        e
        for e in geom.creases
        if not e.id.startswith(
            ("crease_front_ear_", "crease_back_ear_", "crease_base_front", "crease_base_back")
        )
    ]
    creases.extend(
        [
            crease("crease_base_front", (0, 0), (d.bl, 0), ["base", "front"]),
            crease("crease_base_back", (0, d.bw), (d.bl, d.bw), ["base", "back"]),
        ]
    )
    xi = d.bl + d.ho + d.rg
    for ab, x0, x1 in (("a", xi, xi + d.hin), ("b", d.bl - xi - d.hin, d.bl - xi)):
        for fb, y0, y1, yh in (
            ("f", d.ii - d.ed, d.ii, d.ii),
            ("b", d.bw - d.ii, d.bw - d.ii + d.ed, d.bw - d.ii),
        ):
            pid = f"ear_{fb}{ab}"
            panels[pid] = (PanelRole.DUST_FLAP, Path.rect(x0, y0, x1 - x0, y1 - y0))
            creases.append(
                crease(f"crease_side_{ab}_in_{pid}", (x0, yh), (x1, yh), [f"side_{ab}_in", pid])
            )
    shape = unary_union(
        [Polygon([(s.start.x, s.start.y) for s in path.segments]) for _, path in panels.values()]
    )
    if not isinstance(shape, Polygon) or not shape.is_valid:
        raise ValueError(t("The tray with inner-wall ears does not form a continuous blank"))
    ring = [(float(x), float(y)) for x, y in list(shape.exterior.coords)[:-1]]
    return TrayGeom(
        Path.from_points([Vec(x, y) for x, y in _ccw(ring)], closed=True),
        panels,
        creases,
        geom.slots,
        geom.ups,
    )
