"""面映射（规格 9 节第 1 步）：供 agent 规划版式与出图尺寸。"""

from __future__ import annotations

from typing import Any

from pacxio.geometry import Vec, mm_to_px
from pacxio.layout.errors import LayoutError
from pacxio.layout.spaces import body_strip
from pacxio.layout.zones import compute_zones
from pacxio.messages import t
from pacxio.model.dieline import Dieline
from pacxio.model.job import Job
from pacxio.model.profile import Profile
from pacxio.windows import windows_by_panel

PLANNING_DPI = 300  # 规格 9：300ppi 下建议的像素尺寸


def panel_map(
    dl: Dieline, profile: Profile, dpi: int = PLANNING_DPI, job: Job | None = None
) -> dict[str, Any]:
    zg = compute_zones(dl, profile)
    windows = windows_by_panel(dl, job, profile)
    panels: list[dict[str, Any]] = []
    for p in dl.panels:
        x0, y0, x1, y1 = p.polygon.bbox()
        up = p.frame.apply_vector(Vec(0.0, 1.0))
        # 面正立时的宽 × 高（和 --box 的面局部坐标、刀版确认图上标的一致）；天地盖的侧壁在展开图里
        # 是横躺的，展开图方向另给 blank_size_mm（2026-10-29 天地盖测试 F-04）
        sideways = abs(up.x) > abs(up.y)
        w, h = (y1 - y0, x1 - x0) if sideways else (x1 - x0, y1 - y0)
        info: dict[str, Any] = {
            "id": p.id,
            "role": p.role.value,
            "printable": p.printable,
            "orientation_provisional": p.orientation_provisional,
            "size_mm": {"w": round(w, 3), "h": round(h, 3)},
            "px": {"w": mm_to_px(w, dpi), "h": mm_to_px(h, dpi), "dpi": dpi},
            "blank_size_mm": {"w": round(x1 - x0, 3), "h": round(y1 - y0, 3)},
            # 面局部原点（左下角）在刀版坐标中的位置，及局部 +y 在刀版中的方向
            "origin_in_blank_mm": {"x": round(p.frame.e, 3), "y": round(p.frame.f, 3)},
            "up_in_blank": {"x": round(up.x, 6), "y": round(up.y, 6)},
        }
        safe = zg.safe.get(p.id)
        if safe is not None and not safe.is_empty:
            sx0, sy0, sx1, sy1 = safe.bounds
            # 转成面局部坐标（顶盖的 frame 含 180° 旋转）
            inv = p.frame.inverse()
            pts = [inv.apply(Vec(x, y)) for x in (sx0, sx1) for y in (sy0, sy1)]
            lx0, ly0 = min(q.x for q in pts), min(q.y for q in pts)
            info["safe_box_local_mm"] = {
                "x": round(lx0, 3),
                "y": round(ly0, 3),
                "w": round(max(q.x for q in pts) - lx0, 3),
                "h": round(max(q.y for q in pts) - ly0, 3),
            }
        if p.id in windows:  # 开窗：安全区里挖掉了窗口 + 安全边距，排字时避开 keep_out
            info["windows"] = windows[p.id]
        panels.append(info)
    bx0, by0, bx1, by1 = zg.bleed.bounds
    return {
        "style": dl.style,
        "blank_mm": {"w": round(dl.bbox[2], 3), "h": round(dl.bbox[3], 3)},
        "blank_with_bleed_px": {
            "w": mm_to_px(bx1 - bx0, dpi),
            "h": mm_to_px(by1 - by0, dpi),
            "dpi": dpi,
        },
        "bleed_mm": profile.bleed_mm,
        "safe_margin_mm": profile.safe_margin_mm,
        "glue_flap": profile.glue_flap.model_dump(),
        "finish_crease_clearance_mm": profile.finish_rules.crease_clearance_mm,
        "orientation_verified": dl.style_meta.get("orientation_verified", False),
        "panels": panels,
        "body": _body_info(dl),
    }


def _body_info(dl: Dieline) -> dict[str, Any] | None:
    """space=body 的坐标：各盒身面在展开一圈里的 x 范围（正面起，向右绕一圈）。"""
    try:
        strip = body_strip(dl)
    except LayoutError:
        return None
    c = strip.circumference
    faces: list[dict[str, Any]] = []
    for pid in strip.walls:
        x0, _, x1, _ = dl.panel(pid).polygon.bbox()
        bx0 = (x0 - strip.origin.x) % c
        faces.append({"id": pid, "x0": round(bx0, 3), "x1": round(bx0 + x1 - x0, 3)})
    faces.sort(key=lambda f: f["x0"])
    return {
        "origin_in_blank_mm": {"x": round(strip.origin.x, 3), "y": round(strip.origin.y, 3)},
        "circumference_mm": round(c, 3),
        "faces": faces,
        # 盒身这一条在刀版里的右端（接糊口的那条边）：整圈图形的接缝放在这里，正好藏在盒角
        "seam_x_mm": round((strip.x1 - strip.origin.x) % c, 3),
        "note": t("x may be negative or exceed one turn (it wraps around the circumference); y starts at the bottom edge of the front"),
    }
