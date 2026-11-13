"""合上的盒子的三维模型（效果图用）。

从刀版的面与铰链关系把盒子"折"起来：四个侧面各自是竖直的矩形，顶盖 / 底盖按各自铰链所在的
边向盒内折 90°，落在顶面 / 底面。插舌、防尘翼、糊口都在盒子里面，效果图里看不到。

坐标系：x 向右、y 向里（远离正面观看者）、z 向上；单位 mm；原点在盒子正面左下角。
从盒外看，刀版展开图自左向右依次是 back、side_a、front、side_b（`dieline` 视图），所以站在正面
看：front 在前，side_b 在右，back 在后，side_a 在左。

纹理取自刀版坐标系（不是面局部坐标系），所以面的 frame 旋转（盖 / 插舌方向确认后会变）
不影响三维摆放——frame 只决定放置的图文怎样落到刀版上，那已经在合成时做完了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pacxio.geometry import Affine
from pacxio.geometry import Path as GeometryPath
from pacxio.model.dieline import Dieline

Vec3 = tuple[float, float, float]
Rect = tuple[float, float, float, float]  # 刀版坐标 (x0, y0, x1, y1)，y 向上

FACE_LABELS = {
    "front": "正面",
    "back": "背面",
    "side_a": "左侧面",
    "side_b": "右侧面",
    "top": "顶面",
    "bottom": "底面",
}
FACE_PANEL_DEFAULT = {
    "front": "front",
    "back": "back",
    "side_a": "side_a",
    "side_b": "side_b",
    "top": "top_lid",
    "bottom": "btm_lid",
}
NORMALS: dict[str, Vec3] = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "side_a": (-1.0, 0.0, 0.0),
    "side_b": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
}
# 盖向盒内折：父面的"向内"方向
INWARD: dict[str, Vec3] = {"front": (0.0, 1.0, 0.0), "back": (0.0, -1.0, 0.0)}
BODY = ("front", "side_b", "back", "side_a")
HIDDEN_ROLES = {"tuck", "dust_flap", "glue_flap", "glue_tab", "inner_wall", "rim"}
OPEN_ONLY_ROLES = {"inner_wall", "rim"}  # 合上看不到、打开盒子看得到（飞机盒的内侧板、卷边）


@dataclass(frozen=True)
class FoldPatch:
    """折后平面上的一片纸：原刀版轮廓、刀版毫米 → 贴图毫米（x 右、y 下）。"""

    panel: str
    outline: GeometryPath
    to_face: Affine
    cutouts: tuple[GeometryPath, ...] = ()


@dataclass(frozen=True)
class Face:
    key: str  # front / back / side_a / side_b / top / bottom
    panel: str  # 对应的刀版面 id
    label: str
    normal: Vec3
    tex_rect: Rect  # 纹理在刀版坐标里取的矩形
    # 纹理四角（左上、右上、右下、左下）对应的三维点
    corners: tuple[Vec3, Vec3, Vec3, Vec3]
    # 拼出来的面（锁底盒的底面）：自上而下依次取这些刀版矩形，True 表示转 180° 再拼
    parts: tuple[tuple[Rect, bool], ...] = ()
    also: tuple[str, ...] = ()  # 也落在这个面上的刀版面（拼出来的面）
    patches: tuple[FoldPatch, ...] = ()  # 从下到上覆盖；裁掉的区域不参与贴图


@dataclass
class BoxModel:
    x: float  # 正面宽
    y: float  # 侧面宽（进深）
    z: float  # 高
    faces: list[Face] = field(default_factory=list)
    hidden_panels: list[str] = field(
        default_factory=list
    )  # 效果图里看不到的面（插舌 / 防尘翼 / 糊口）
    open_only: list[str] = field(default_factory=list)  # 其中打开盒子才看得到的面
    open_hint: str = ""  # 怎么打开才看得到（多部件盒型："盖子拿开" / "抽屉抽出来"）
    # 静态图描边按组：同一组的小平面之间不画边线（瓶身切成的很多片，C4）；None = 每张纸一组
    edge_groups: list[int] | None = None

    def face(self, key: str) -> Face:
        return next(f for f in self.faces if f.key == key)

    def corners8(self) -> list[Vec3]:
        return [(a, b, c) for a in (0.0, self.x) for b in (0.0, self.y) for c in (0.0, self.z)]


def _bbox(dl: Dieline, panel_id: str) -> Rect:
    return dl.structural_bbox(panel_id)  # 插舌根部的小卡位、刀模内角补块不算


def _halves(
    bb: dict[str, Rect],
    y_dim: float,
    halves: list[str],
    lid_corners: Any,
    top: bool,
    depth: float | None = None,
) -> Face:
    """两片翼各盖一半拼成的面：锁底盒的底面、开槽箱的顶面和底面。

    前面那片盖前半、后面那片盖后半（折过来后转了 180°）；纹理取压线旁边半个进深那么宽的一条。
    锁舌、侧翼、开槽露出的缝都不画——合上后的真实样子以打样为准（MK04 示意）。
    """
    front, back = bb["front"], bb["back"]
    half = y_dim / 2 if depth is None else min(depth, y_dim)
    back_depth = half if depth is None else y_dim - half
    if top:
        f_rect = (front[0], front[3], front[2], front[3] + half)
        b_rect = (back[0], back[3], back[2], back[3] + back_depth)
        full = (front[0], front[3], front[2], front[3] + y_dim)
        parts = ((b_rect, True), (f_rect, False))  # 纹理上方是离铰链远的一边（后半）
    else:
        f_rect = (front[0], front[1] - half, front[2], front[1])
        b_rect = (back[0], back[1] - back_depth, back[2], back[1])
        full = (front[0], front[1] - y_dim, front[2], front[1])
        parts = ((f_rect, False), (b_rect, True))
    key = "top" if top else "bottom"
    return Face(key, halves[0], FACE_LABELS[key], NORMALS[key], f_rect,
                lid_corners("front", full, top), parts=tuple(p for p in parts if p[0][3]-p[0][1]>1e-9), also=tuple(halves[1:]))  # fmt: skip


def _snaplock_bottom(dl: Dieline, bb: dict[str, Rect], face: Face, x: float, y: float) -> Face:
    """按四片实际底翼的轮廓折合。

    外侧可见次序：侧翼在主翼下面，front 的锁舌插在 back 缺口翼下面。
    只作合拢后的平面外观映射，不模拟纸厚、插接动作或实物装配公差。
    """
    from dataclasses import replace

    patches = []
    for name in ("btm_sa", "btm_sb", "btm_front", "btm_back"):
        panel = dl.panel(name)
        assert panel.hinge is not None
        parent = panel.hinge.parent_panel
        edge = next(e for e in dl.edges if e.id == panel.hinge.edge_id)
        hinge_y = edge.segments[0].start.y
        left = bb[parent][0]
        transforms = {
            "front": Affine(1, 0, 0, -1, -left, hinge_y),
            "back": Affine(-1, 0, 0, 1, x + left, y - hinge_y),
            "side_a": Affine(0, -1, -1, 0, hinge_y, y + left),
            "side_b": Affine(0, 1, 1, 0, x - hinge_y, -left),
        }
        patches.append(FoldPatch(name, panel.polygon, transforms[parent]))
    return replace(face, parts=(), patches=tuple(patches), also=("btm_back", "btm_sa", "btm_sb"))


def _tray(dl: Dieline) -> BoxModel:
    """以底板为中心的盒子（飞机盒）：底板 + 四壁 + 盖，面由 style_meta.faces 给出。

    印刷面视图是从盒底外面看：展开图里底板右边的面是站在正面看的左侧（side_a）。
    """
    names = dl.style_meta["faces"]
    bb = {k: dl.structural_bbox(pid) for k, pid in names.items()}
    bx0, by0, bx1, by1 = bb["bottom"]
    x_dim, y_dim = bx1 - bx0, by1 - by0
    z_dim = bb["back"][3] - bb["back"][1]
    box = BoxModel(x=x_dim, y=y_dim, z=z_dim)
    lid_y0 = bb["top"][1] if "top" in bb else 0.0  # 托盘部件（天地盖的地盒、抽屉内托）没有盖
    lid_y1 = bb["top"][3] if "top" in bb else 0.0
    tuck = bool(dl.style_meta.get("front_is_tuck")) and "top" in bb  # 飞机盒：插舌包在前板外面

    def to3d(key: str, u: float, v: float) -> Vec3:
        if key == "bottom":
            return (bx1 - u, v - by0, 0.0)
        if key == "front" and tuck:  # 插舌从盖前沿翻下来
            return (bx1 - u, 0.0, z_dim - (v - lid_y1))
        if key == "front":
            return (bx1 - u, 0.0, by0 - v)
        if key == "back":
            return (bx1 - u, y_dim, v - by1)
        if key == "side_a":
            return (0.0, v - by0, u - bx1)
        if key == "side_b":
            return (x_dim, v - by0, bx0 - u)
        return (bx1 - u, y_dim - (v - lid_y0), z_dim)  # top：盖从后板上沿往前合上

    rims = dl.style_meta.get("lid_between_rims") and "top" in bb
    for key in (k for k in ("front", "side_b", "back", "side_a", "top", "bottom") if k in bb):
        x0, y0, x1, y1 = bb[key]
        if key in ("front", "back") or (key == "top" and rims):
            x0, x1 = (
                bx0,
                bx1,
            )  # 前、后板、插舌比底板短：纹理按底板整宽取；盖卡在卷边中间时顶面也整宽
        if key == "front" and tuck:  # 插舌深 = 盒高
            y0, y1 = lid_y1, lid_y1 + z_dim
        if key == "front" and dl.style_meta.get("front_overlays"):
            y0, y1 = by0 - z_dim, by0
        rect = (x0, y0, x1, y1)
        corners = (to3d(key, x0, y1), to3d(key, x1, y1), to3d(key, x1, y0), to3d(key, x0, y0))
        face = Face(key, names[key], FACE_LABELS[key], NORMALS[key], rect, corners)
        if key == "top" and rims:
            face = _top_with_rims(dl, face, bb, x_dim, y_dim)
        if key == "front" and dl.style_meta.get("front_overlays"):
            from dataclasses import replace

            base = dl.panel(face.panel)
            holes = tuple(
                GeometryPath(e.segments, closed=True)
                for e in dl.inner_cuts()
                if base.id in e.panels
            )
            patches = [FoldPatch(base.id, base.polygon, Affine(1, 0, 0, -1, -bx0, by0), holes)]
            overlays = tuple(dl.style_meta["front_overlays"])
            patches += [
                FoldPatch(pid, dl.panel(pid).polygon, Affine(1, 0, 0, -1, -bx0, z_dim + lid_y1))
                for pid in overlays
            ]
            face = replace(face, also=overlays, patches=tuple(patches))
        box.faces.append(face)
    return box


def _top_with_rims(
    dl: Dieline, face: Face, bb: dict[str, Rect], x_dim: float, y_dim: float
) -> Face:
    """盖卡在两条卷边中间（飞机盒）：顶面中间贴盖，两边贴卷边顶。

    纹理坐标（mm）：左上角是刀版上 (底板左边, 盖前沿)，往右、往下为正。盖原样平移过去；卷边顶从外侧板上沿
    往里量，右边那条（side_a 上的）贴到纹理右边，左边那条贴到左边，进深方向和底板对齐。
    """
    from dataclasses import replace

    bx0, by0, _, _ = bb["bottom"]
    _, ly0, _, ly1 = bb["top"]
    lid = dl.panel(face.panel)
    patches = [FoldPatch(lid.id, lid.polygon, Affine(1, 0, 0, -1, -bx0, ly1))]
    shift = (ly1 - ly0) - y_dim - by0  # 盖比底板深出来的那点：卷边顶和底板对齐
    for p in dl.panels:
        if p.role.value != "rim":
            continue
        rx0, _, rx1, _ = p.polygon.bbox()
        e = x_dim + rx0 if rx0 >= bb["bottom"][2] - 1e-6 else rx1  # 右边：外沿在 rx0；左边：在 rx1
        patches.append(FoldPatch(p.id, p.polygon, Affine(-1, 0, 0, 1, e, shift)))
    rims = tuple(p.panel for p in patches[1:])
    return replace(face, patches=tuple(patches), also=rims)


def build_box(dl: Dieline) -> BoxModel:
    """由刀版生成盒子模型：盒身一圈（RTE 系列、开槽箱）或底板 + 四壁（飞机盒）、顶面、底面。"""
    info = dl.style_meta.get("structure") or {}
    if (
        info.get("native") or info.get("box") == "targets"
    ):  # 几何引擎原生 / 积木拼装的刀版：按立体目标
        from pacxio.engine.box import structure_box

        return structure_box(dl)
    if dl.style_meta.get("layout") == "pouch":
        from pacxio.mockup.pouch import pouch_box

        box = pouch_box(dl)
    elif dl.style_meta.get("layout") in ("flat", "label"):
        panel = dl.panel("front")
        x0, y0, x1, y1 = panel.polygon.bbox()
        box = BoxModel(x=x1 - x0, y=y1 - y0, z=dl.caliper_mm)
        box.faces = [
            Face(
                "front",
                "front",
                "正面",
                (0, 0, 1),
                (x0, y0, x1, y1),
                ((0, y1 - y0, 0), (x1 - x0, y1 - y0, 0), (x1 - x0, 0, 0), (0, 0, 0)),
            )
        ]
        if dl.style_meta.get("folded"):
            rect = dl.panel("back").polygon.bbox()
            width, height = x1 - x0, y1 - y0
            z = -2 * dl.caliper_mm
            box.faces.append(
                Face(
                    "back",
                    "back",
                    "背面",
                    (0, 0, -1),
                    rect,
                    ((0, 0, z), (width, 0, z), (width, height, z), (0, height, z)),
                )
            )
    elif dl.style_meta.get("layout") == "sleeve":
        from pacxio.mockup.scene import _sleeve

        box = _sleeve(dl)
    elif dl.style_meta.get("layout") == "platform":
        from pacxio.mockup.regular import platform_box

        box = platform_box(dl)
    elif dl.style_meta.get("layout") == "tray":
        box = _tray(dl)
    else:
        box = _body_ring(dl)
    visible = {name for face in box.faces for name in (face.panel, *face.also)}
    box.hidden_panels = [
        p.id for p in dl.panels if p.role.value in HIDDEN_ROLES and p.id not in visible
    ]
    open_extra = set(dl.style_meta.get("open_only") or [])  # 飞机盒的前板：被插舌挡住
    box.open_only = [
        p.id
        for p in dl.panels
        if (p.role.value in OPEN_ONLY_ROLES or p.id in open_extra) and p.id not in visible
    ]  # 飞机盒的卷边顶合上后就在顶面两边，不算"打开才看得到"
    return box


def visible_panels(dl: Dieline) -> list[str]:
    """合上的盒子外面看得到的刀版面（两半拼的面两片都算），按 正面、右、背面、左、顶、底 的次序。"""
    box = build_box(dl)
    keys = {f.key for f in box.faces}
    out: list[str] = []
    for key in (k for k in ("front", "side_b", "back", "side_a", "top", "bottom") if k in keys):
        f = box.face(key)
        out += [f.panel, *f.also]
    return out


def _body_ring(dl: Dieline) -> BoxModel:
    """盒身一圈：front / back / side_a / side_b、顶盖或两片顶摇盖、底盖或两片底翼。"""
    bb = {pid: _bbox(dl, pid) for pid in BODY}
    x_dim = bb["front"][2] - bb["front"][0]
    y_dim = bb["side_a"][2] - bb["side_a"][0]
    z0, z1 = bb["back"][1], bb["back"][3]  # body 的高度范围以 back（整高）为准
    z_dim = z1 - z0
    box = BoxModel(x=x_dim, y=y_dim, z=z_dim)

    def body_point(panel: str, u: float, v: float) -> Vec3:
        """刀版坐标 (u, v) 落在 body 面上的三维位置。"""
        if panel == "front":
            return (u - bb["front"][0], 0.0, v - z0)
        if panel == "side_b":
            return (x_dim, u - bb["side_b"][0], v - z0)
        if panel == "back":
            return (x_dim - (u - bb["back"][0]), y_dim, v - z0)
        return (0.0, y_dim - (u - bb["side_a"][0]), v - z0)  # side_a

    def rect_corners(panel: str, rect: Rect) -> tuple[Vec3, Vec3, Vec3, Vec3]:
        x0, y0, x1, y1 = rect
        return (
            body_point(panel, x0, y1),
            body_point(panel, x1, y1),
            body_point(panel, x1, y0),
            body_point(panel, x0, y0),
        )

    for key in ("front", "side_b", "back", "side_a"):
        x0, _, x1, _ = bb[key]
        rect = (x0, z0, x1, z1)  # 侧面整高（side 面的多边形上下各缩了 dust_offset，纹理仍取整高）
        box.faces.append(
            Face(key, key, FACE_LABELS[key], NORMALS[key], rect, rect_corners(key, rect))
        )

    def lid_corners(parent: str, rect: Rect, top: bool) -> tuple[Vec3, Vec3, Vec3, Vec3]:
        x0, y0, x1, y1 = rect
        inward = INWARD[parent]
        edge_v = bb[parent][3] if top else bb[parent][1]  # 盖的铰链边（父面的上沿 / 下沿）

        def p(u: float, v: float) -> Vec3:
            base = body_point(parent, u, edge_v)
            d = (v - edge_v) if top else (edge_v - v)  # 离铰链边的距离，向盒内折
            return (
                base[0] + d * inward[0],
                base[1] + d * inward[1],
                base[2] + d * inward[2],
            )

        return (p(x0, y1), p(x1, y1), p(x1, y0), p(x0, y0))

    ids = {p.id for p in dl.panels}
    for key, top in (("top", True), ("bottom", False)):
        if dl.style_meta.get(key, {}).get("open"):
            continue
        pid = FACE_PANEL_DEFAULT[key]
        halves = dl.style_meta.get(key, {}).get("halves")
        if halves and pid not in ids:
            face = _halves(
                bb,
                y_dim,
                halves,
                lid_corners,
                top,
                dl.style_meta["dims"]["flap"]
                if dl.style_meta.get(key, {}).get("overlap")
                else None,
            )
            if not top and dl.style_meta.get("bottom", {}).get("type") == "snaplock":
                face = _snaplock_bottom(dl, bb, face, x_dim, y_dim)
            box.faces.append(face)
            continue
        panel = dl.panel(pid)
        parent = panel.hinge.parent_panel if panel.hinge else ("back" if top else "front")
        rect = dl.structural_bbox(pid)  # 插舌根部的小卡位不算
        box.faces.append(
            Face(key, pid, FACE_LABELS[key], NORMALS[key], rect, lid_corners(parent, rect, top))
        )
    if "hanger" in ids:
        rect = dl.panel("hanger").polygon.bbox()
        x0, y0, x1, y1 = rect
        box.faces.append(
            Face(
                "hanger",
                "hanger",
                "挂板",
                (0, 1, 0),
                rect,
                (
                    body_point("back", x0, y1),
                    body_point("back", x1, y1),
                    body_point("back", x1, y0),
                    body_point("back", x0, y0),
                ),
            )
        )
        box.z = max(box.z, y1 - z0)
    return box
