"""多部件盒型的三维场景（ 0.6）：每个部件折成自己的盒子，再按整套摆好。

- 合上的三张：天地盖 = 地盒 + 倒扣的天盖；抽屉盒 = 外套里的内托（前后齐平）。先画里面的部件、
  再画外面的部件，各自背面剔除——合上时外面的只会挡住里面的，不用排深度。
- 打开的一张：天地盖的盖放在地盒旁边、地盒敞口；抽屉抽出一半（`PULL_OUT_RATIO`，只是效果图的常量，
  不进 job.json）。托盘的壁两面都画：双层壁的内壁贴它自己的印刷图，单层壁、底、外套的里面是纸的背面
  （按材料画白 / 灰 / 牛皮色，示意）。这一张按深度从远到近画。

场景里的面 key：合上时外面看得到的六个面用标准 key（front / back / side_a / side_b / top / bottom，
面全名在 panel 里），其余用"部件:面"（里面加 ":in"）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from PIL import Image, ImageDraw

from pacxio.export.canvas import _Map
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.fold import FACE_LABELS, NORMALS, BoxModel, Face, Rect, Vec3, _tray
from pacxio.model.dieline import Dieline

PULL_OUT_RATIO = 0.5  # 打开的那张里抽屉抽出多少【初值，只影响效果图】
OPEN_GAP_MM = 40.0  # 打开的那张里盖放在地盒旁边隔多远
OPEN_LIFT_RATIO = (
    1.8  # 折叠 3D 里天地盖打开时盖抬多高（盖高的几倍）：下沿离开地盒，看得到里面【只影响显示】
)
INSIDE_EPS_MM = 0.05  # 里面的面往里挪一点，免得和外面的面重合
PAPER_RGB = {"kraft": (186, 146, 104), "gd": (198, 198, 194), "corrugated": (176, 138, 96),
             "white": (236, 233, 226)}  # fmt: skip
DOUBLE_WALLS = {"tray_lid": {"side_a": "side_a_in", "side_b": "side_b_in"},
                "drawer": {"front": "front_in", "back": "back_in"}}  # fmt: skip
Source = tuple[str, Rect] | tuple[int, int, int]  # (部件, 刀版矩形) 或纯色


def paper_rgb(dl: Dieline) -> tuple[int, int, int]:
    """纸的背面（不印刷的一面）：效果图里托盘里面、外套里面的颜色，只是示意。"""
    preset = (dl.material_preset or "").lower()
    if "kraft" in preset:
        return PAPER_RGB["kraft"]
    if preset.startswith("gd"):
        return PAPER_RGB["gd"]
    if preset.startswith("flute"):
        return PAPER_RGB["corrugated"]
    return PAPER_RGB["white"]


@dataclass
class Scene:
    box: BoxModel  # 面的三维角点都是场景坐标（包围盒左前下角为原点）
    sources: dict[str, Source] = field(default_factory=dict)
    open_only: list[str] = field(default_factory=list)  # 合上看不到、打开才看得到的面（全名）
    open_hint: str = ""  # "盖子拿开" / "抽屉抽出来"


def _shift(corners: tuple[Vec3, ...], d: Vec3) -> tuple[Vec3, Vec3, Vec3, Vec3]:
    c = [(p[0] + d[0], p[1] + d[1], p[2] + d[2]) for p in corners]
    return (c[0], c[1], c[2], c[3])


def _neg(v: Vec3) -> Vec3:
    return (-v[0], -v[1], -v[2])


def _faces_from(bb: dict[str, Rect], names: dict[str, str], to3d: Callable[[str, float, float], Vec3],
                keys: tuple[str, ...], full_width: Rect | None = None) -> list[Face]:  # fmt: skip
    out = []
    for key in keys:
        x0, y0, x1, y1 = bb[key]
        if full_width is not None and key in (
            "front",
            "back",
        ):  # 前后壁比底短 2·ear_offset：按底整宽取
            x0, x1 = full_width[0], full_width[2]
        rect = (x0, y0, x1, y1)
        corners = (to3d(key, x0, y1), to3d(key, x1, y1), to3d(key, x1, y0), to3d(key, x0, y0))
        out.append(Face(key, names[key], FACE_LABELS[key], NORMALS[key], rect, corners))
    return out


def _lid(dl: Dieline) -> BoxModel:
    """天盖（从盖顶外面看的展开图）：盖顶在上（z = 壁高），四壁往下垂。"""
    names = dl.style_meta["faces"]
    bb = {k: dl.panel(pid).polygon.bbox() for k, pid in names.items()}
    tx0, ty0, tx1, ty1 = bb["top"]
    X, Y, H = tx1 - tx0, ty1 - ty0, bb["back"][3] - bb["back"][1]

    def to3d(key: str, u: float, v: float) -> Vec3:
        return {
            "top": (u - tx0, v - ty0, H),
            "front": (u - tx0, 0.0, H - (ty0 - v)),
            "back": (u - tx0, Y, H - (v - ty1)),
            "side_a": (0.0, v - ty0, H - (tx0 - u)),
            "side_b": (X, v - ty0, H - (u - tx1)),
        }[key]

    box = BoxModel(x=X, y=Y, z=H)
    box.faces = _faces_from(
        bb, names, to3d, ("front", "side_b", "back", "side_a", "top"), bb["top"]
    )
    return box


def _sleeve(dl: Dieline) -> BoxModel:
    """外套：两头开口的方筒。展开图 [接舌][底][左][顶][右]，y 是抽拉方向（y = 0 是前口）。"""
    names = dl.style_meta["faces"]
    bb = {k: dl.panel(pid).polygon.bbox() for k, pid in names.items()}
    tx0, ty0, tx1, ty1 = bb["top"]
    X, Y, Z = tx1 - tx0, ty1 - ty0, bb["side_a"][2] - bb["side_a"][0]
    bx1 = bb["bottom"][2]

    def to3d(key: str, u: float, v: float) -> Vec3:
        return {
            "top": (u - tx0, v - ty0, Z),
            "side_a": (0.0, v - ty0, Z - (tx0 - u)),
            "side_b": (X, v - ty0, Z - (u - tx1)),
            "bottom": (bx1 - u, v - ty0, 0.0),
        }[key]

    box = BoxModel(x=X, y=Y, z=Z)
    box.faces = _faces_from(bb, names, to3d, ("side_b", "side_a", "top", "bottom"))
    return box


def _texture_map(f: Face) -> Callable[[float, float], Vec3]:
    """面的贴图映射：刀版 (u, v) → 三维（纹理四角 = 面的四个角点，仿射）。"""
    ox0, oy0, ox1, oy1 = f.tex_rect
    tl, tr, bl = f.corners[0], f.corners[1], f.corners[3]

    def to3d(u: float, v: float) -> Vec3:
        a, b = (u - ox0) / (ox1 - ox0), (oy1 - v) / (oy1 - oy0)
        return (tl[0] + a * (tr[0] - tl[0]) + b * (bl[0] - tl[0]),
                tl[1] + a * (tr[1] - tl[1]) + b * (bl[1] - tl[1]),
                tl[2] + a * (tr[2] - tl[2]) + b * (bl[2] - tl[2]))  # fmt: skip

    return to3d


def _mirror_line(
    wall: Rect, inner: Rect
) -> tuple[Callable[[float, float], tuple[float, float]], Rect]:
    """内壁翻进去：刀版上关于卷边中线镜像。返回 (镜像, 内壁取贴图的矩形——高不超过外壁、不含插脚)。"""
    wx0, wy0, wx1, wy1 = wall
    ix0, iy0, ix1, iy1 = inner
    if ix0 >= wx1 - 1e-6 or ix1 <= wx0 + 1e-6:  # 内壁在外壁右边 / 左边：镜像线是竖线
        right = ix0 >= wx1 - 1e-6
        c = 0.5 * (wx1 + ix0) if right else 0.5 * (wx0 + ix1)
        h = wx1 - wx0
        rect = (ix0, iy0, min(ix1, ix0 + h), iy1) if right else (max(ix0, ix1 - h), iy0, ix1, iy1)
        return (lambda u, v: (2 * c - u, v)), rect
    top = iy0 >= wy1 - 1e-6  # 内壁在外壁上边 / 下边：镜像线是横线
    c = 0.5 * (wy1 + iy0) if top else 0.5 * (wy0 + iy1)
    h = wy1 - wy0
    rect = (ix0, iy0, ix1, min(iy1, iy0 + h)) if top else (ix0, max(iy0, iy1 - h), ix1, iy1)
    return (lambda u, v: (u, 2 * c - v)), rect


def _inside_faces(
    part: str, dl: Dieline, box: BoxModel, style: str, sources: dict[str, Source]
) -> list[Face]:
    """敞口托盘 / 外套的里面：双层壁的内壁贴它自己的印刷图，其余是纸的背面。"""
    out: list[Face] = []
    doubles = DOUBLE_WALLS.get(style, {})
    ids = {p.id for p in dl.panels}
    paper = paper_rgb(dl)
    for f in box.faces:
        n = _neg(f.normal)
        push = (n[0] * INSIDE_EPS_MM, n[1] * INSIDE_EPS_MM, n[2] * INSIDE_EPS_MM)
        key = f"{part}:{f.key}:in"
        inner = doubles.get(f.key)
        if inner is None or inner not in ids:
            sources[key] = paper
            out.append(
                replace(
                    f, key=key, panel=f"{part}.{f.key}", normal=n, corners=_shift(f.corners, push)
                )
            )
            continue
        outer_map = _texture_map(f)
        mirror, rect = _mirror_line(dl.panel(f.key).polygon.bbox(), dl.panel(inner).polygon.bbox())
        rx0, ry0, rx1, ry1 = rect
        corners = tuple(
            outer_map(*mirror(u, v)) for u, v in ((rx0, ry1), (rx1, ry1), (rx1, ry0), (rx0, ry0))
        )
        sources[key] = (part, rect)
        out.append(Face(key, f"{part}.{inner}", f.label, n, rect, _shift(corners, push)))
    return out


def _place(
    faces: list[Face], part: str, d: Vec3, standard: dict[str, str], sources: dict[str, Source]
) -> list[Face]:
    """把一个部件的面挪到场景位置；standard：部件的面 → 场景里的标准 key（合上时外面看得到的）。"""
    out = []
    for f in faces:
        if f.key.endswith(":in"):
            out.append(replace(f, corners=_shift(f.corners, d)))
            continue
        key = standard.get(f.key, f"{part}:{f.key}")
        sources[key] = (part, f.tex_rect)
        out.append(replace(f, key=key, panel=f"{part}.{f.panel}", corners=_shift(f.corners, d)))
    return out


def part_box(dl: Dieline) -> BoxModel:
    """多部件盒型里一个部件自己的盒子（部件坐标）。几何引擎多件套的件按它自己的结构建盒子。"""
    if dl.style == "structure":
        from pacxio.mockup.fold import build_box

        return build_box(dl)
    return {"lid": _lid, "sleeve": _sleeve}.get(str(dl.style_meta.get("part")), _tray)(dl)


def part_offsets(style: str, parts: dict[str, Dieline], opened: bool) -> dict[str, Vec3]:
    """每个部件在场景里的位置：合上时天盖扣在地盒上、内托在外套里；打开时盖放旁边、抽屉抽出一半。"""
    boxes = {name: part_box(dl) for name, dl in parts.items()}
    if style == "tray_lid":
        base, lid = boxes["base"], boxes["lid"]
        dx, dy = (base.x - lid.x) / 2, (base.y - lid.y) / 2
        if opened:  # 盖放在地盒右边，盖顶朝上；地盒敞口
            return {"base": (0.0, 0.0, 0.0), "lid": (base.x + OPEN_GAP_MM, dy, 0.0)}
        # 盖倒扣：盖顶内表面压在地盒口沿上
        return {"base": (0.0, 0.0, 0.0),
                "lid": (dx, dy, base.z + parts["base"].caliper_mm - lid.z)}  # fmt: skip
    if style == "drawer":
        tray, sleeve = boxes["tray"], boxes["sleeve"]
        dx, dz = (sleeve.x - tray.x) / 2, (sleeve.z - tray.z) / 2
        pull = -PULL_OUT_RATIO * tray.y if opened else 0.0
        return {"tray": (dx, pull, dz), "sleeve": (0.0, 0.0, 0.0)}
    if style == "structure_set":  # 几何引擎多件套：各件折好并排摆（怎么组装还没画，合上和打开一样）
        out: dict[str, Vec3] = {}
        x = 0.0
        for name, b in boxes.items():
            out[name] = (x, 0.0, 0.0)
            x += b.x + OPEN_GAP_MM
        return out
    raise ValueError(style)


def build_scene(style: str, parts: dict[str, Dieline], opened: bool = False) -> Scene:
    """合上（opened = False）或打开的整套场景。"""
    if style == "divider":
        from pacxio.mockup.regular import divider_scene

        return divider_scene(parts, opened)
    sources: dict[str, Source] = {}
    faces: list[Face] = []
    at = part_offsets(style, parts, opened)
    if style == "tray_lid":
        base, lid = _tray(parts["base"]), _lid(parts["lid"])
        if opened:  # 盖放在地盒右边，盖顶朝上；地盒敞口
            faces += _place(
                _inside_faces("base", parts["base"], base, style, sources),
                "base",
                (0, 0, 0),
                {},
                sources,
            )
            faces += _place(base.faces, "base", (0, 0, 0), {}, sources)
            faces += _place(lid.faces, "lid", at["lid"], {}, sources)
        else:
            faces += _place(base.faces, "base", (0, 0, 0), {"bottom": "bottom"}, sources)
            faces += _place(lid.faces, "lid", at["lid"],
                            {k: k for k in ("front", "back", "side_a", "side_b", "top")}, sources)  # fmt: skip
        open_only = [f"base.{p.id}" for p in parts["base"].panels if p.id not in ("bottom",)]
        hint = "盖子拿开"
    elif style == "drawer":
        tray, sleeve = _tray(parts["tray"]), _sleeve(parts["sleeve"])
        std_sleeve = {k: k for k in ("top", "bottom", "side_a", "side_b")}
        if opened:
            faces += _place(
                _inside_faces("sleeve", parts["sleeve"], sleeve, style, sources),
                "sleeve",
                (0, 0, 0),
                {},
                sources,
            )
            faces += _place(
                _inside_faces("tray", parts["tray"], tray, style, sources),
                "tray",
                at["tray"],
                {},
                sources,
            )
            faces += _place(tray.faces, "tray", at["tray"], {}, sources)
            faces += _place(sleeve.faces, "sleeve", (0, 0, 0), {}, sources)
        else:
            # 抽屉里面先画：外套顶面的手指缺口里看得到
            inside = _inside_faces("tray", parts["tray"], tray, style, sources)
            faces += _place(inside, "tray", at["tray"], {}, sources)
            std_tray = {"front": "front", "back": "back"}
            tray_faces = _place(tray.faces, "tray", at["tray"], std_tray, sources)
            for i, f in enumerate(tray_faces):
                if f.key in (
                    "front",
                    "back",
                ):  # 抽屉脸 / 后壁铺满外套的口（间隙约 1 mm，示意图里不留缝）
                    mx = sum(c[0] for c in f.corners) / 4
                    mz = sum(c[2] for c in f.corners) / 4
                    snap = [(0.0 if c[0] < mx else sleeve.x, c[1], 0.0 if c[2] < mz else sleeve.z)
                            for c in f.corners]  # fmt: skip
                    tray_faces[i] = replace(f, corners=(snap[0], snap[1], snap[2], snap[3]))
            faces += tray_faces
            faces += _place(sleeve.faces, "sleeve", (0, 0, 0), std_sleeve, sources)
        open_only = [f"tray.{p.id}" for p in parts["tray"].panels if p.id not in ("front", "back")]
        hint = "抽屉抽出来"
    else:
        raise ValueError(style)
    return Scene(_normalize(faces), sources, open_only, hint)


def _normalize(faces: list[Face]) -> BoxModel:
    """场景坐标挪到包围盒左前下角为原点（相机按 BoxModel 的 x / y / z 取景）。"""
    pts = [c for f in faces for c in f.corners]
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    box = BoxModel(x=hi[0] - lo[0], y=hi[1] - lo[1], z=hi[2] - lo[2])
    box.faces = [replace(f, corners=_shift(f.corners, (-lo[0], -lo[1], -lo[2]))) for f in faces]
    return box


def scene_textures(
    scene: Scene, arts: dict[str, tuple[Image.Image, _Map]], dls: dict[str, Dieline] | None = None
) -> dict[str, Image.Image]:
    """每个面的纹理：从部件的成品外观图里按刀版矩形裁；纸背面是纯色。arts：部件 → (成品外观图, 坐标映射)。

    给了 dls 时纹理带透明通道：部件外轮廓以外（手指缺口等）是透明的，效果图里透出后面的东西。
    """
    out: dict[str, Image.Image] = {}
    masks: dict[str, Image.Image] = {}
    for part, (art, m) in arts.items():
        if dls is not None and part in dls:
            outline = path_to_polygon(GPath(dls[part].outline_segments(), closed=True))
            mask = Image.new("L", art.size, 0)
            ImageDraw.Draw(mask).polygon([m.pt(x, y) for x, y in outline.exterior.coords], fill=255)
            masks[part] = mask
    for f in scene.box.faces:
        src = scene.sources.get(f.key)
        if src is None:
            continue
        if isinstance(src[1], tuple):
            part, (x0, y0, x1, y1) = str(src[0]), src[1]
            art, m = arts[part]
            left, top = m.pt(x0, y1)
            right, bottom = m.pt(x1, y0)
            crop = (round(left), round(top), round(right), round(bottom))
            tex = art.crop(crop)
            if part in masks:
                tex = tex.convert("RGBA")
                tex.putalpha(masks[part].crop(crop))
            if f.key.endswith(":in") and dls and dls[part].style == "divider":
                paper = Image.new("RGBA", tex.size, paper_rgb(dls[part]))
                paper.putalpha(tex.getchannel("A"))
                tex = paper
            out[f.key] = tex
        else:
            out[f.key] = Image.new("RGB", (8, 8), (int(src[0]), int(src[1]), int(src[2])))
    return out
