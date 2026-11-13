"""双层墙托盘的内墙底折边（FEFCO 0428）：代替插脚和底板插孔，尺寸【初值】。

0421 和 0428 的托盘、耳朵、连盖一样，差在端墙内层的下沿：0421 是插脚插进底板插孔，
0428 是一条窄折边往盒里折 90° 平压在底板上，底板不开孔。折边两头斜切，让开长边墙。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shapely.geometry import Polygon
from shapely.ops import unary_union

from pacxio.geometry import Path, Vec
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import PanelRole
from pacxio.styles.corrugated import crease
from pacxio.styles.tray import TrayDims, TrayGeom

if TYPE_CHECKING:
    from pacxio.engine.tray import TrayParams

# 【初值】
FOOT_H_RATIO, FOOT_MIN_MM, FOOT_MAX_MM = 0.2, 10.0, 25.0  # 折边深 clamp(0.2·墙高, 10, 25)
FOOT_CHAMFER_RATIO = 0.5  # 两头斜切 = 0.5·折边深
FOOT_CLEAR_MM = 5.0  # 两头的折边在底板中间至少留这么多


def foot_depth(p: TrayParams, d: TrayDims) -> float:
    if p.foot_depth is not None:
        return p.foot_depth
    return min(FOOT_MAX_MM, max(FOOT_MIN_MM, FOOT_H_RATIO * d.hf))


def validate_feet(p: TrayParams, d: TrayDims) -> list[Issue]:
    f = foot_depth(p, d)
    out: list[Issue] = []
    room = 0.5 * (d.bl - FOOT_CLEAR_MM) - d.rg
    if f > room + 1e-9:
        out.append(Issue(id="S03", message=t("The inner-wall foot flap is {depth:.1f} mm deep. The two ends collide at the centre of the base.", depth=f),
                         fix=t("Set `tuning.foot_depth` to at most {maximum:.1f} or increase the length", maximum=max(room, 0))))  # fmt: skip
    if 2 * FOOT_CHAMFER_RATIO * f >= d.wall_w:
        out.append(
            Issue(
                id="S03",
                message=t("The inner-wall foot flap has no length after beveling"),
                fix=t("Reduce `tuning.foot_depth`"),
            )
        )
    return out


def add_feet(d: TrayDims, geom: TrayGeom, f: float) -> TrayGeom:
    """两块内墙的自由边（x = xe 和镜像的 bl − xe）各接一条折边，两头斜切。"""
    from pacxio.engine.tray import _ccw

    xe = d.bl + d.ho + d.rg + d.hin
    c = FOOT_CHAMFER_RATIO * f
    y0, y1 = d.ii, d.bw - d.ii
    panels = dict(geom.polys)
    creases = list(geom.creases)
    for side, root, s in (("a", xe, 1.0), ("b", d.bl - xe, -1.0)):
        pid = f"foot_{side}"
        pts = [(root, y0), (root + s * f, y0 + c), (root + s * f, y1 - c), (root, y1)]
        panels[pid] = (
            PanelRole.INNER_WALL,
            Path.from_points([Vec(x, y) for x, y in _ccw(pts)], closed=True),
        )
        creases.append(
            crease(f"crease_side_{side}_in_{pid}", (root, y0), (root, y1), [f"side_{side}_in", pid])
        )
    shape = unary_union(
        [Polygon([(q.start.x, q.start.y) for q in path.segments]) for _, path in panels.values()]
    )
    if not isinstance(shape, Polygon) or not shape.is_valid:
        raise ValueError(t("The inner-wall foot flap and tray do not form a continuous blank"))
    ring = [(float(x), float(y)) for x, y in list(shape.exterior.coords)[:-1]]
    outline = Path.from_points([Vec(x, y) for x, y in _ccw(ring)], closed=True)
    return TrayGeom(outline, panels, creases, geom.slots, geom.ups)
