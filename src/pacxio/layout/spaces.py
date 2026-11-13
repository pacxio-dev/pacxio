"""放置的坐标系（space）：面局部 / 盒身展开一圈 / 整张展开图 → 刀版坐标。

- panel：面的 frame（面局部 → 刀版）。
- body：盒身四个面在展开图里是相邻的一条，展开坐标以正面左下角为原点；一圈周长 C 处回到起点，
  所以同一个图形要在 x 方向平移 k·C 各画一份（k 取覆盖得到盒身的那几个整数），
  这样跨过"首尾相接"那条边（糊口一侧）的图形也是连续的。只画在盒身四个面（及其出血、
  糊口压线旁允许上墨的窄带）上，不会画到盖、底、防尘翼上。
- blank：刀版坐标本身（包围盒左下角为原点），可以画到任何面上，只受允许上墨区域限制。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.geometry import box as rect
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from pacxio.geometry import Affine, Vec, affine_geom, path_to_polygon
from pacxio.layout.errors import LayoutError
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.job import Box, Placement

FRONT = "front"
CONTIGUOUS_TOL_MM = 0.05


@dataclass(frozen=True)
class BodyStrip:
    origin: Vec  # 正面左下角（刀版坐标）= 展开坐标原点
    circumference: float  # 一圈周长（盒身四个面宽度之和）
    x0: float  # 盒身这一条在刀版里的左右范围
    x1: float
    walls: tuple[str, ...]  # 刀版里从左到右的盒身面


def body_strip(dl: Dieline) -> BodyStrip:
    walls = sorted(
        (p for p in dl.panels if p.role == PanelRole.BODY), key=lambda p: p.polygon.bbox()[0]
    )
    unsupported = LayoutError(
        Issue(
            id="SPACE",
            message=t("The body panels in style {style} do not form a continuous horizontal strip. space=body is unavailable.", style=dl.style),
            fix=t("Use space=blank for dieline coordinates, or place the artwork separately on each panel"),
        )
    )
    if FRONT not in {p.id for p in walls} or len(walls) < 2:
        raise unsupported
    for p in walls:
        f = p.frame
        if abs(f.a - 1) > 1e-9 or abs(f.d - 1) > 1e-9 or abs(f.b) > 1e-9 or abs(f.c) > 1e-9:
            raise unsupported
    boxes = [p.polygon.bbox() for p in walls]
    for (_, _, ax1, _), (bx0, _, _, _) in zip(boxes, boxes[1:], strict=False):
        if abs(ax1 - bx0) > CONTIGUOUS_TOL_MM:
            raise unsupported
    front = dl.panel(FRONT).polygon.bbox()
    return BodyStrip(
        origin=Vec(front[0], front[1]),
        circumference=boxes[-1][2] - boxes[0][0],
        x0=boxes[0][0],
        x1=boxes[-1][2],
        walls=tuple(p.id for p in walls),
    )


def body_region(dl: Dieline, bleed_mm: float) -> BaseGeometry:
    """space=body 允许画的区域：盒身四个面外扩出血，再去掉盖、底、插舌、防尘翼。

    糊口不去掉：它在压线旁允许上墨的窄带要接上首尾相接处的图形（再由允许上墨区域限制）。
    """
    walls, others = [], []
    for p in dl.panels:
        poly = path_to_polygon(p.polygon)
        if p.role == PanelRole.BODY:
            walls.append(poly)
        elif p.role != PanelRole.GLUE_FLAP:
            others.append(poly)
    grown = unary_union(walls).buffer(bleed_mm, join_style="mitre")
    return grown.difference(unary_union(others)) if others else grown


def box_polygon(b: Box) -> Polygon:
    return rect(b.x, b.y, b.x + b.w, b.y + b.h)


def placement_frames(p: Placement, dl: Dieline, bleed_mm: float) -> list[Affine]:
    """box 坐标 → 刀版坐标的变换。body 可能有多份（相差一圈周长），只返回落在盒身附近的。"""
    assert p.box is not None
    if p.coord_space == "blank":
        return [Affine.identity()]
    if p.coord_space == "panel":
        assert p.panel is not None
        try:
            return [dl.panel(p.panel).frame]
        except KeyError:
            raise LayoutError(
                Issue(
                    id="PANEL",
                    message=t("Placement {id} refers to missing panel {panel}", id=p.id, panel=p.panel),
                    fix=t("Available panels: ") + ", ".join(x.id for x in dl.panels),
                )
            ) from None
    strip = body_strip(dl)
    c = strip.circumference
    if p.box.w > c + CONTIGUOUS_TOL_MM:
        raise LayoutError(
            Issue(
                id="SPACE",
                message=t("Placement {id} is {width:.1f} mm wide, exceeding the body circumference of {circumference:.1f} mm", id=p.id, width=p.box.w, circumference=c),
                fix=t("Set box width to at most {maximum:.1f} mm. A width of exactly {full_width:.1f} mm wraps once around the body.", maximum=c, full_width=c),
            )
        )
    lo, hi = strip.x0 - bleed_mm - 1, strip.x1 + bleed_mm + 1
    left = strip.origin.x + p.box.x
    right = left + p.box.w
    k0 = math.floor((lo - right) / c)
    k1 = math.ceil((hi - left) / c)
    return [
        Affine.translate(strip.origin.x + k * c, strip.origin.y)
        for k in range(k0, k1 + 1)
        if left + k * c < hi and right + k * c > lo
    ]


def placement_world_box(p: Placement, dl: Dieline, bleed_mm: float) -> BaseGeometry:
    """box 在刀版上覆盖的区域（body 为各份的并集，再限制在盒身区域内）。"""
    assert p.box is not None
    shape = box_polygon(p.box)
    world = unary_union([affine_geom(shape, m) for m in placement_frames(p, dl, bleed_mm)])
    if p.coord_space == "body":
        world = world.intersection(body_region(dl, bleed_mm))
    return world


def placement_panels(p: Placement, dl: Dieline, bleed_mm: float = 0.0) -> list[str]:
    """放置覆盖到的面（按刀版里的顺序）：效果图信息清单用。"""
    if p.coord_space == "panel":
        return [p.panel] if p.panel else []
    world = placement_world_box(p, dl, bleed_mm)
    out = []
    for panel in dl.panels:
        if world.intersection(path_to_polygon(panel.polygon)).area > 1e-6:
            out.append(panel.id)
    return out


__all__ = [
    "BodyStrip",
    "body_region",
    "body_strip",
    "box_polygon",
    "placement_frames",
    "placement_panels",
    "placement_world_box",
]
