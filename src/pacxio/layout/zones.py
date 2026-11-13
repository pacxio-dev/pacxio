"""出血 / 安全区 / 禁墨区 / 工艺可用区（、9 节）。

全部是从 Dieline + 配置档推出的派生几何，不改动刀版。
- bleed：裁切轮廓外扩 bleed_mm；
- no_ink：糊口按配置档 glue_flap.ink 处理（none 整块禁墨；bleed_only 只允许糊口压线内侧
  bleed_into_mm 内有墨；full 不禁）。禁墨区包含糊口外面那圈出血里**只属于糊口**的部分：
  离别的面 bleed_mm 以内的出血算那些面的，照常有墨（2026-10-22 修：以前糊口外扩的圆角
  把糊口两端旁边背面、盖、底翼的出血吃掉了一块， 节）；
- safe：每个可印面内缩 safe_margin_mm（离切线与折线）；
- finish_ok：每个可印面内缩 crease_clearance_mm（工艺区须离压线至少这么远）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from pacxio.geometry import (
    Path,
    Segment,
    outline_shape,
    path_to_polygon,
    polygon_to_path,
    polygons_of,
)
from pacxio.model.dieline import Dieline, PanelRole, Zones
from pacxio.model.profile import Profile

ROUND_JOIN_RESOLUTION = 16
# 出血区与糊口禁墨区是两次独立的 buffer，圆弧的离散顶点不重合，直接相减会留下 ~0.01mm 宽的
# 碎片，栅格化后成为孤立的墨点。禁墨区外扩这个余量盖住碎片，再丢弃面积极小的残片。
SLIVER_GUARD_MM = 0.05
MIN_REGION_AREA_MM2 = 0.01


@dataclass
class ZoneGeometry:
    """shapely 形式的区域，供排版与检查直接做几何运算。"""

    outline: Polygon
    bleed: Polygon
    no_ink: BaseGeometry
    allowed: BaseGeometry  # 允许上墨的区域 = bleed − no_ink
    panels: dict[str, Polygon]
    safe: dict[str, BaseGeometry] = field(default_factory=dict)
    finish_ok: dict[str, BaseGeometry] = field(default_factory=dict)


def outline_polygon(dl: Dieline) -> Polygon:
    """外轮廓区域；几块分开的料（平底袋的袋身和底）是 MultiPolygon（类型照旧标 Polygon，用法相同）。"""
    return outline_shape(dl.outline_segments())  # type: ignore[return-value]


def compute_zones(dl: Dieline, profile: Profile) -> ZoneGeometry:
    outline = outline_polygon(dl)
    bleed = outline.buffer(profile.bleed_mm, quad_segs=ROUND_JOIN_RESOLUTION)
    panels = {p.id: path_to_polygon(p.polygon) for p in dl.panels}

    declared_no_ink = [path_to_polygon(p) for p in dl.zones.no_ink]
    no_ink: BaseGeometry = unary_union(declared_no_ink) if declared_no_ink else Polygon()
    rule = profile.glue_flap
    for p in dl.panels:
        if p.role != PanelRole.GLUE_FLAP or rule.ink == "full":
            continue
        glue = panels[p.id]
        others = unary_union([g for pid, g in panels.items() if pid != p.id])
        reach = profile.bleed_mm + SLIVER_GUARD_MM
        # 刀线外的出血：离别的面 bleed_mm 以内的归那些面（照常有墨），剩下的才是糊口自己的
        ring = glue.buffer(reach, quad_segs=ROUND_JOIN_RESOLUTION).difference(outline)
        theirs = others.buffer(profile.bleed_mm, quad_segs=ROUND_JOIN_RESOLUTION)
        zone = unary_union([glue, ring.difference(theirs)]).difference(others)
        if rule.ink == "bleed_only" and p.hinge is not None:
            hinge = next(e for e in dl.edges if e.id == p.hinge.edge_id)
            line = _polyline(hinge.segments)
            # 允许有墨：压线两侧 bleed_into_mm 内（平头端点，沿压线全长）
            strip = line.buffer(rule.bleed_into_mm, cap_style="flat")
            zone = zone.difference(strip)
        no_ink = unary_union([no_ink, zone])

    allowed = unary_union(
        [g for g in polygons_of(bleed.difference(no_ink)) if g.area >= MIN_REGION_AREA_MM2]
    )

    zg = ZoneGeometry(outline=outline, bleed=bleed, no_ink=no_ink, allowed=allowed, panels=panels)
    # 开窗：窗口里切掉的是废料，图文照常印过去（allowed 不变）；但文字离窗口要有安全边距，
    # 工艺离窗口要有工艺间距——安全区、工艺区挖掉窗口外扩的那一圈
    holes = {w.id: path_to_polygon(w.cut) for w in dl.windows}
    for p in dl.panels:
        if not p.printable or p.role == PanelRole.GLUE_FLAP:
            continue
        mine = [holes[w.id] for w in dl.windows if w.panel == p.id]
        safe: BaseGeometry = panels[p.id].buffer(-profile.safe_margin_mm)
        ok: BaseGeometry = panels[p.id].buffer(-profile.finish_rules.crease_clearance_mm)
        if declared_no_ink:
            safe = safe.difference(no_ink)
            ok = ok.difference(no_ink)
        for hole in mine:
            safe = safe.difference(
                hole.buffer(profile.safe_margin_mm, quad_segs=ROUND_JOIN_RESOLUTION)
            )
            ok = ok.difference(
                hole.buffer(
                    profile.finish_rules.crease_clearance_mm, quad_segs=ROUND_JOIN_RESOLUTION
                )
            )
        if not safe.is_empty:
            zg.safe[p.id] = safe
        if not ok.is_empty:
            zg.finish_ok[p.id] = ok
    return zg


def _polyline(segments: list[Segment]) -> LineString:
    pts = [(s.start.x, s.start.y) for s in segments] + [segments[-1].end.as_tuple()]
    return LineString(pts)


def _first_path(geom: BaseGeometry) -> Path | None:
    polys = polygons_of(geom)
    return polygon_to_path(polys[0]) if polys else None


def zones_model(zg: ZoneGeometry) -> Zones:
    """序列化为 pydantic 的 Zones（写进 Dieline / 报告时用）。"""
    return Zones(
        bleed=_first_path(zg.bleed),
        safe={k: p for k, v in zg.safe.items() if (p := _first_path(v)) is not None},
        no_ink=[polygon_to_path(p) for p in polygons_of(zg.no_ink)],
        finish_ok={k: p for k, v in zg.finish_ok.items() if (p := _first_path(v)) is not None},
    )
