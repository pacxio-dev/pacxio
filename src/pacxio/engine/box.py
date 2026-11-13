"""几何引擎刀版的盒子模型（摆正效果图、信息清单用）：直接用立体目标，不按面名猜。"""

from __future__ import annotations

from pacxio.mockup.fold import BoxModel, Face
from pacxio.model.dieline import Dieline

WALL_ZH = {"back": "背面", "side_a": "左侧面", "front": "正面", "side_b": "右侧面"}


def structure_box(dl: Dieline) -> BoxModel:
    """每面墙一张：四角是立体上那面墙的四个角（左上、右上、右下、左下，从盒外看）。"""
    info = dl.style_meta["structure"]
    body = info.get("recipe", {}).get("body", {})
    axis = body.get("axis", "H")
    turned_tray = (
        body.get("form") == "tray"
        and body.get("corners") == "ear"
        and body.get("corner_on") == "long"
    )
    max_y = max((row[4] for row in info["targets"]), default=0.0) if axis == "W" else 0.0
    max_x = max((row[3] for row in info["targets"]), default=0.0) if turned_tray else 0.0

    def orient(
        pt: tuple[float, float, float], *, vector: bool = False
    ) -> tuple[float, float, float]:
        x, y, z = pt
        if turned_tray:
            return (y, -x if vector else max_x - x, z)
        if axis == "L":
            return (z, x, y)
        if axis == "W":
            # 保持右手坐标系：筒长沿产品进深，原筒身 y 轴翻成高度。
            return (x, z, -y if vector else max_y - y)
        return pt

    pts: dict[str, list[tuple[float, float, float]]] = {}
    for pid, _x, _y, X, Y, Z in info["targets"]:
        pts.setdefault(pid, []).append(orient((X, Y, Z)))
    xs = [p[0] for v in pts.values() for p in v]
    ys = [p[1] for v in pts.values() for p in v]
    zs = [p[2] for v in pts.values() for p in v]
    box = BoxModel(
        x=max(xs) - min(xs), y=max(ys) - min(ys), z=max(max(zs) - min(zs), dl.caliper_mm)
    )
    for i, pid in enumerate(info["walls"]):
        if len(pts.get(pid, [])) < 4:  # 正多边形托盘的墙名不是 front / back：没有目标的跳过
            continue
        lb, rb, rt, lt = pts[pid][:4]  # 目标按刀版包围盒 左下、右下、右上、左上 存
        # 刀版是印刷面（盒外）视图，四角在刀版上逆时针：(右下 − 左下) × (左上 − 左下) 朝外
        u = (rb[0] - lb[0], rb[1] - lb[1], rb[2] - lb[2])
        v = (lt[0] - lb[0], lt[1] - lb[1], lt[2] - lb[2])
        nx, ny, nz = u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]
        n = (nx * nx + ny * ny + nz * nz) ** 0.5
        box.faces.append(Face(pid, pid, WALL_ZH.get(pid, f"第 {i + 1} 面"), (nx / n, ny / n, nz / n),
                              dl.panel(pid).polygon.bbox(), (lt, rt, rb, lb)))  # fmt: skip
    for f in info.get("faces", []):  # 封口积木声明的顶 / 底面（合上后外面看到的那块）
        top = f["key"] == "top"
        corners = tuple(orient(tuple(c)) for c in f["corners"])
        normal = orient((0.0, 0.0, 1.0 if top else -1.0), vector=True)
        box.faces.append(Face(f["key"], f["panel"], "顶面" if top else "底面", normal,
                              tuple(f["rect"]), corners))  # type: ignore[arg-type]  # fmt: skip
    return box


__all__ = ["structure_box"]
