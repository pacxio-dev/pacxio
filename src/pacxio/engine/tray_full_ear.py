"""双层墙托盘的整幅矩形角翼（FEFCO 0454 / 0455 / 0456，尺寸【初值】）。"""

from __future__ import annotations

from shapely.geometry import Polygon
from shapely.ops import unary_union

from pacxio.geometry import Path, Vec
from pacxio.messages import t
from pacxio.model.dieline import PanelRole
from pacxio.styles.corrugated import crease
from pacxio.styles.tray import TrayDims, TrayGeom


def add_full_ears(d: TrayDims, geom: TrayGeom) -> TrayGeom:
    """将两对单墙端部扩展为完整矩形角翼，保留已有双层墙和卷边。"""
    panels = dict(geom.polys)
    creases = [
        e for e in geom.creases if not e.id.startswith(("crease_front_ear_", "crease_back_ear_"))
    ]
    for wall, ya, yb, prefix in (("front", -d.hf, 0.0, "f"), ("back", d.bw, d.bw + d.hf, "b")):
        for side, x0, x1, root in (("a", d.bl, d.bl + d.ed, d.bl), ("b", -d.ed, 0.0, 0.0)):
            pid = f"ear_{prefix}{side}"
            panels[pid] = (PanelRole.DUST_FLAP, Path.rect(x0, ya, x1 - x0, yb - ya))
            creases.append(crease(f"crease_{wall}_{pid}", (root, ya), (root, yb), [wall, pid]))
    shape = unary_union([Polygon([(seg.start.x, seg.start.y) for seg in path.segments])
                         for _, path in panels.values()])  # fmt: skip
    if not isinstance(shape, Polygon) or not shape.is_valid:
        raise ValueError(
            t("The full-width corner flaps and double walls do not form a valid continuous blank")
        )
    from pacxio.engine.tray import _ccw

    ring = [(float(x), float(y)) for x, y in list(shape.exterior.coords)[:-1]]
    outline = Path.from_points([Vec(x, y) for x, y in _ccw(ring)], closed=True)
    return TrayGeom(outline, panels, creases, geom.slots, geom.ups)
