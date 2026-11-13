"""结构类检查（规格第 12 节 S01 / S02）。S03 由各盒型的 validate 提供。

M0 只实现结构检查；印刷类检查（B/T/I/C/P/F/X）在 M2。
"""

from __future__ import annotations

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from pacxio.geometry import Path, split_loops
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, EdgeType

DIM_TOLERANCE_MM = 0.05  # S02：几何量回的尺寸容差（规格 12 节）
GEOM_TOL_MM = 1e-6
SAGITTA_MM = 0.01


def _polygon(path: Path) -> Polygon:
    return Polygon([p.as_tuple() for p in path.to_points(SAGITTA_MM)])


def check_s01(dl: Dieline) -> list[Issue]:
    """S01：裁切轮廓闭合、不自交、无重复线。"""
    issues: list[Issue] = []
    loops = split_loops(dl.outline_segments())  # 几块分开的料各自一圈
    if not loops or not all(Path(loop, closed=True).is_closed() for loop in loops):
        issues.append(
            Issue(
                id="S01",
                message=t("The cut outline is open"),
                fix=t("Check the outline endpoints in the box style generator"),
            )
        )
        return issues
    pieces = [_polygon(Path(loop, closed=True)) for loop in loops]
    poly = pieces[0] if len(pieces) == 1 else unary_union(pieces)
    if len(pieces) > 1 and sum(q.area for q in pieces) - poly.area > GEOM_TOL_MM:
        issues.append(
            Issue(
                id="S01",
                message=t("The outer outlines of the blanks overlap"),
                fix=t("Check the outer outlines in the box style generator"),
            )
        )
    if not poly.is_valid:
        issues.append(
            Issue(
                id="S01",
                message=t("The cut outline is self-intersecting or invalid"),
                fix=t("Check whether the parameters cause flaps or tabs to overlap"),
            )
        )
    for e in dl.inner_cuts():  # 内部切口（可以从外轮廓切进来）不能跑出外轮廓
        pts = [e.segments[0].start.as_tuple()] + [s.end.as_tuple() for s in e.segments]
        if not poly.buffer(GEOM_TOL_MM).covers(LineString(pts)):
            issues.append(
                Issue(
                    id="S01",
                    message=t("Internal cut {edge} extends beyond the outer outline", edge=e.id),
                    fix=t(
                        "Check the cutout position parameters, including slots, notches, and flap depths"
                    ),
                )  # fmt: skip
            )

    # 重复线：任意两条线段（含压线）端点集合完全相同即视为重复
    seen: set[tuple[tuple[float, float], tuple[float, float], float]] = set()
    for e in dl.edges:
        for s in e.segments:
            a = (round(s.start.x, 4), round(s.start.y, 4))
            b = (round(s.end.x, 4), round(s.end.y, 4))
            key = (min(a, b), max(a, b), round(abs(s.bulge), 6))
            if key in seen:
                issues.append(
                    Issue(
                        id="S01",
                        message=t("Duplicate segment ({edge})", edge=e.id),
                        fix=t("Remove the duplicate line"),
                    )
                )
            seen.add(key)
    return issues


def check_crease_sides(dl: Dieline) -> list[Issue]:
    """每条压线两侧都有面（）；面里面的压线（插舌根部的短压线）只要落在那个面里。"""
    issues: list[Issue] = []
    polys = {p.id: _polygon(p.polygon) for p in dl.panels}
    for e in dl.edges_of(EdgeType.CREASE):
        line = LineString(
            [(s.start.x, s.start.y) for s in e.segments] + [e.segments[-1].end.as_tuple()]
        )
        if len(e.panels) == 1:
            if not polys[e.panels[0]].buffer(GEOM_TOL_MM).contains(line):
                issues.append(Issue(id="S01", message=t("Crease {edge} extends outside panel {panel}", edge=e.id, panel=e.panels[0]),
                                    fix=t("Check the crease coordinates against the panel polygon")))  # fmt: skip
            continue
        if len(e.panels) != 2:
            issues.append(
                Issue(
                    id="S01",
                    message=t("Crease {edge} must have exactly two adjoining panels", edge=e.id),
                )
            )
            continue
        for pid in e.panels:
            # 按容差算“落在边界上”：斜压线和面边共线时，精确求交会因 1e-12 的误差算成一个点
            off_edge = line.difference(polys[pid].boundary.buffer(GEOM_TOL_MM)).length
            if polys[pid].boundary.distance(line) > GEOM_TOL_MM or off_edge > GEOM_TOL_MM:
                issues.append(
                    Issue(
                        id="S01",
                        message=t(
                            "Crease {edge} does not lie entirely on the boundary of panel {panel}",
                            edge=e.id,
                            panel=pid,
                        ),
                        fix=t("Check the crease coordinates against the panel polygon"),
                    )
                )
    return issues


def measure_panel(dl: Dieline, panel_id: str) -> tuple[float, float]:
    """面的 (宽, 高)，由结构包围盒量得（刀模内角补块、插舌根部的小卡位不算，见 Dieline.structural_bbox）。"""
    x0, y0, x1, y1 = dl.structural_bbox(panel_id)
    return x1 - x0, y1 - y0


def check_s02(dl: Dieline, expected: dict[str, tuple[float, float]]) -> list[Issue]:
    """S02：几何量回的尺寸与参数一致。expected: 面 id → (宽, 高)。"""
    issues: list[Issue] = []
    for pid, (ew, eh) in expected.items():
        w, h = measure_panel(dl, pid)
        if abs(w - ew) > DIM_TOLERANCE_MM or abs(h - eh) > DIM_TOLERANCE_MM:
            issues.append(
                Issue(
                    id="S02",
                    message=t(
                        "Panel {panel} measures {width:.3f}×{height:.3f}, expected {expected_width:.3f}×{expected_height:.3f}",
                        panel=pid,
                        width=w,
                        height=h,
                        expected_width=ew,
                        expected_height=eh,
                    ),
                    fix=t("Check the style's dimension conversion and compensation factors"),
                )
            )
    return issues
