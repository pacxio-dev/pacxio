"""折叠后的装配检查：直接使用带分层的折叠矩阵，不把零厚度目标当作实物结果。"""

from __future__ import annotations

from typing import Any

import numpy as np
from shapely.geometry import Point, Polygon

from pacxio.mockup.folding import apply, build_part, panel_matrices, plan_for
from pacxio.model.dieline import Dieline

SLOT_TOL_MM = 0.05  # 点到槽的距离容差【初值】；不是插脚 / 槽的生产间隙


def tray_inner_clearance(dl: Dieline, length: float, width: float) -> dict[str, Any]:
    """从两对折好后的内墙面量回可用长度 / 宽度，而不是从展开图推算。"""
    plan = plan_for(dl)
    if plan is None:
        return {"status": "not_applicable", "reason": "没有折法"}
    matrices = panel_matrices(build_part(dl, plan), plan.steps)

    def separation(a: str, b: str) -> float:
        def centre(pid: str) -> np.ndarray:
            x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
            return np.asarray(apply(matrices[pid], (x0 + x1) / 2, (y0 + y1) / 2))

        return abs(float((centre(a) - centre(b)) @ matrices[a][:3, 2]))

    panels = {panel.id for panel in dl.panels}
    if {"side_a_in", "side_b_in", "front_in", "back_in"} <= panels:
        measured = (separation("side_a_in", "side_b_in"), separation("front_in", "back_in"))
    elif {"side_a_in", "side_b_in", "front", "back"} <= panels:
        measured = (separation("side_a_in", "side_b_in"),
                    separation("front", "back") - 2 * dl.caliper_mm)  # fmt: skip
    elif {"front_in", "back_in", "side_a", "side_b"} <= panels:
        measured = (separation("side_a", "side_b") - 2 * dl.caliper_mm,
                    separation("front_in", "back_in"))  # fmt: skip
    else:
        return {"status": "not_applicable", "reason": "缺少对向内壁 / 外墙"}
    deviations = (measured[0] - length, measured[1] - width)
    return {"status": "pass" if max(abs(v) for v in deviations) <= SLOT_TOL_MM else "fail",
            "inner_mm": [round(v, 4) for v in measured], "expected_mm": [length, width],
            "deviation_mm": [round(v, 4) for v in deviations], "tolerance_mm": SLOT_TOL_MM}  # fmt: skip


def tray_lug_alignment(dl: Dieline) -> dict[str, Any]:
    """量托盘内墙插脚是否仍落入底槽；保留真实折法的纸厚分层。

    根面必须是底板。只检查穿过底板平面的插脚顶点，不能替代完整的厚度碰撞或成型过程检查。
    没有底槽时返回 not_applicable；不改变生成的刀版、折法或目标。
    """
    plan = plan_for(dl)
    if plan is None or plan.root != "base":
        return {"status": "not_applicable", "reason": "没有以底板为根面的折法"}
    slots = [Polygon([(seg.start.x, seg.start.y) for seg in edge.segments])
             for edge in dl.edges if edge.id.startswith("cut_slot_")]  # fmt: skip
    if not slots:
        return {"status": "not_applicable", "reason": "没有底槽"}
    mats = panel_matrices(build_part(dl, plan), plan.steps)
    misses: dict[str, float] = {}
    checked = 0
    for panel in dl.panels:
        if panel.role.value != "inner_wall":
            continue
        for seg in panel.polygon.segments:
            x, y, z = apply(mats[panel.id], seg.start.x, seg.start.y)
            if z <= SLOT_TOL_MM:
                continue
            checked += 1
            distance = min(slot.distance(Point(x, y)) for slot in slots)
            if distance > SLOT_TOL_MM:
                misses[panel.id] = max(distance, misses.get(panel.id, 0.0))
    if not checked:
        return {"status": "not_applicable", "reason": "没有穿过底板平面的插脚顶点"}
    return {
        "status": "fail" if misses else "pass",
        "checked_vertices": checked,
        "max_miss_mm": round(max(misses.values(), default=0.0), 4),
        "misses_mm": {key: round(value, 4) for key, value in misses.items()},
        "tolerance_mm": SLOT_TOL_MM,
    }
