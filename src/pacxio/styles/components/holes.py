"""Circular and euro hanging holes, kept as circular-arc internal knife paths."""

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import Polygon

from pacxio.geometry import Path, Segment, Vec, arc_segment, path_to_polygon
from pacxio.messages import t
from pacxio.model.dieline import Edge, EdgeType

HOLE_MARGIN_MM = 3.0  # 【初值】，孔到刀线 / 压线至少留 3 mm


class HoleParams(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    hang_hole: Literal["none", "round", "euro"] = "none"
    hole_d: float = Field(default=6.0, gt=0)
    hole_w: float = Field(default=32.0, gt=0)
    hole_h: float = Field(default=6.0, gt=0)
    hole_rise: float = Field(default=3.0, gt=0)
    hole_top: float = Field(default=10.0, gt=0)


def circle(cx: float, cy: float, r: float) -> Path:
    return Path(
        [arc_segment(Vec(cx, cy), r, k * math.pi / 2, (k + 1) * math.pi / 2) for k in range(4)],
        closed=True,
    )


def hanging_hole(p: HoleParams, panel: Path, pid: str) -> Edge | None:
    if p.hang_hole == "none":
        return None
    x0, _, x1, y1 = panel.bbox()
    cx, top = (x0 + x1) / 2, y1 - p.hole_top
    if p.hang_hole == "round":
        path = circle(cx, top - p.hole_d / 2, p.hole_d / 2)
    else:
        # Capsule ends with a circular central rise on the upper edge.
        w, h, rise = p.hole_w, p.hole_h, p.hole_rise
        if w <= h + 2 * rise:
            raise ValueError(t("A Euro slot requires hole_w greater than hole_h + 2·hole_rise"))
        y = top - rise - h / 2
        left, right, r = cx - w / 2 + h / 2, cx + w / 2 - h / 2, h / 2
        path = Path(
            [
                Segment(Vec(left, y - r), Vec(right, y - r)),
                arc_segment(Vec(right, y), r, -math.pi / 2, math.pi / 2),
                Segment(Vec(right, y + r), Vec(cx + rise, y + r)),
                arc_segment(Vec(cx, y + r), rise, 0, math.pi),
                Segment(Vec(cx - rise, y + r), Vec(left, y + r)),
                arc_segment(Vec(left, y), r, math.pi / 2, 3 * math.pi / 2),
            ],
            closed=True,
        )
    outer = path_to_polygon(panel)
    hole = path_to_polygon(path)
    safe: Polygon = outer.buffer(-HOLE_MARGIN_MM)
    if not hole.is_valid or not safe.covers(hole):
        raise ValueError(
            t(
                "The hang hole and its {margin:g} mm margin must fit entirely inside {panel}",
                margin=HOLE_MARGIN_MM,
                panel=pid,
            )
        )
    return Edge(id=f"cut_hang_{pid}", type=EdgeType.CUT, segments=path.segments, panels=[pid])


@dataclass(frozen=True)
class Hanger:
    height: float
    radius: float

    def contour(self, x0: float, x1: float, y: float) -> Path:
        r = self.radius
        if not 20 <= self.height <= 100 or not 0 <= r <= min(self.height / 2, (x1 - x0) / 2):
            raise ValueError(
                t(
                    "The hanger panel height must be 20–100 mm. Its corner radius cannot exceed half the shorter side."
                )
            )
        segs = [Segment(Vec(x1, y), Vec(x1, y + self.height - r))]
        if r:
            segs.append(arc_segment(Vec(x1 - r, y + self.height - r), r, 0, math.pi / 2))
        if x1 - r > x0 + r:
            segs.append(Segment(Vec(x1 - r, y + self.height), Vec(x0 + r, y + self.height)))
        if r:
            segs.append(arc_segment(Vec(x0 + r, y + self.height - r), r, math.pi / 2, math.pi))
        segs += [Segment(Vec(x0, y + self.height - r), Vec(x0, y)), Segment(Vec(x0, y), Vec(x1, y))]
        return Path(segs, closed=True)
