"""按刀版真实折叠的静态效果图：深度缓冲软件光栅化（ 节，2026-11-04 用户要求做）。

`folded_view.open_paper_view` 给出每块面的正、反两张纸（正面是成品外观图按刀版裁的、带切口透明；反面是纸色），
四个角在三维里的位置来自和工作台 3D 同一套铰链矩阵。这里对每个像素从相机射出一条线，和纸面所在平面求交，
落在纸里、离相机最近的那张纸赢（深度缓冲），所以斜壁、翻盖、互相插着的翼都按真实前后关系画——旧的
`render.render_view` 只会画凸的方盒子，或按面中心远近从远到近糊上去。

相机、取景、灯光、背景、地面阴影和旧的渲染一致（同一个 `Camera`、`shade`），只换画面的部分。
不同的纸之间画一道淡边线（原来每个面画矩形框），看得出折线和纸边。
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import MultiPoint, Polygon

from pacxio.export.canvas import _Map
from pacxio.mockup.fold import BoxModel
from pacxio.mockup.folding import PLANS, plan_for
from pacxio.mockup.render import (
    EDGE_ALPHA,
    SHADOW_ALPHA,
    SHADOW_BLUR,
    SHADOW_SHIFT,
    SUPERSAMPLE,
    Camera,
    View,
    _background,
    add_reflection,
    camera_rays,
    reflection,
    shade,
)
from pacxio.model.dieline import Dieline

STATIC_RENDERER = "fold-1"  # 写进效果图 manifest；旧项目的静态图不是这个版本时工作台提示重出
ALPHA_CUT = 128  # 纹理透明度低于这个算挖空（切口、刀版外）
EDGE_SHADE = 1 - EDGE_ALPHA / 255  # 边线处颜色乘这个


def expected_static_renderer(style_id: str, multipart: bool) -> str:
    """这个盒型的静态效果图该用哪种画法：有折法表的单部件盒型按折叠几何，其余（多部件）还是方盒子。"""
    # 贴在瓶上的标签（C4）、平放的软袋（C5）也是深度缓冲图
    labels = style_id in (
        "label_panel",
        "label_wrap",
        "label_cone",
        "pouch_standup",
        "sachet_3side",
        "pouch_backseal",
        "pouch_flat_bottom",
    )
    engine = style_id == "structure"  # 几何引擎的刀版自带折法
    return STATIC_RENDERER if (style_id in PLANS or labels or engine) and not multipart else "box"


def folded_static(
    dl: Dieline, art: Image.Image, mapping: _Map
) -> tuple[BoxModel, dict[str, Image.Image]] | None:
    """合上状态的每张纸（正面印刷、反面纸色）；没有折法表的盒型返回 None，用旧的方盒子。"""
    plan = plan_for(dl)
    if plan is None:
        return None
    from pacxio.mockup.folded_view import open_paper_view

    return open_paper_view(dl, art, mapping, step=plan.steps)


def render_folded(
    box: BoxModel,
    textures: dict[str, Image.Image],
    view: View,
    size: tuple[int, int],
    rough: dict[str, Image.Image] | None = None,
    reflect: dict[str, float] | None = None,
) -> Image.Image:
    """渲染一个视角。box.faces 每张纸四个角（左上、右上、右下、左下）、法向朝纸看得到的那一面；textures 按 face.key。

    rough / reflect：粗糙度纹理（和 textures 同样裁切）与影棚参数，给了才画覆膜、UV 的反光（`render.reflection`）；
    没有粗糙度纹理的纸（纸背）不反光。"""
    ss = SUPERSAMPLE
    w, h = size[0] * ss, size[1] * ss
    cam = Camera(box, view.az, view.el, (w, h))
    pos = np.array(cam.pos)
    depth = np.full((h, w), np.inf)
    color = np.zeros((h, w, 3), dtype=np.float32)
    owner = np.full((h, w), -1, dtype=np.int32)

    for index, face in enumerate(box.faces):
        a, b, _, d = (np.array(c, dtype=np.float64) for c in face.corners)
        normal = np.array(face.normal, dtype=np.float64)
        centre = np.mean([np.array(c) for c in face.corners], axis=0)
        if normal @ (pos - centre) <= 0:  # 背对相机：另一面那张纸会画
            continue
        quad = [cam.project(c) for c in face.corners]
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        x0, x1 = max(0, int(np.floor(min(xs)))), min(w, int(np.ceil(max(xs))) + 1)
        y0, y1 = max(0, int(np.floor(min(ys)))), min(h, int(np.ceil(max(ys))) + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        px, py = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        ray = camera_rays(cam, px, py)
        denom = ray @ normal
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((a - pos) @ normal) / denom
        hit = pos + t[..., None] * ray
        du, dv = b - a, d - a
        rel = hit - a
        s = rel @ du / (du @ du)
        v = rel @ dv / (dv @ dv)
        if box.edge_groups is None:
            inside = (t > 0) & (s >= 0) & (s < 1) & (v >= 0) & (v < 1) & np.isfinite(t)
        else:  # 很多小平面拼的曲面：每片往外多算约一个像素，相邻两片之间不漏缝（深度测试决定谁在前）
            with np.errstate(invalid="ignore"):
                pix = np.abs(t) / cam.scale  # 这个像素在纸面上大约多少 mm
                ps, pv = pix / np.sqrt(du @ du), pix / np.sqrt(dv @ dv)
            inside = (
                (t > 0) & (s >= -ps) & (s < 1 + ps) & (v >= -pv) & (v < 1 + pv) & np.isfinite(t)
            )
        zc = t  # ray 的 fwd 分量为 1，参数 t 就是沿视线的深度
        region = depth[y0:y1, x0:x1]
        closer = inside & (zc < region)
        if not closer.any():
            continue
        tex = textures[face.key]
        rgba = np.asarray(tex.convert("RGBA"))
        th, tw = rgba.shape[:2]
        tx = np.clip((s * tw).astype(np.int64), 0, tw - 1)
        ty = np.clip((v * th).astype(np.int64), 0, th - 1)
        sample = rgba[ty, tx]
        closer &= sample[..., 3] >= ALPHA_CUT
        if not closer.any():
            continue
        k = shade(face.normal)
        region[closer] = zc[closer]
        lit = sample[..., :3][closer].astype(np.float32) * k
        if rough is not None and reflect is not None and face.key in rough:
            rmap = np.asarray(rough[face.key].convert("L"))
            rh, rw = rmap.shape
            r = rmap[
                np.clip((v[closer] * rh).astype(np.int64), 0, rh - 1),
                np.clip((s[closer] * rw).astype(np.int64), 0, rw - 1),
            ]
            lit = add_reflection(lit, reflection(hit[closer], normal, pos, r, reflect, box))
        color[y0:y1, x0:x1][closer] = lit
        owner[y0:y1, x0:x1][closer] = index

    canvas = _background((w, h))
    pts = [cam.project((p[0], p[1], 0.0)) for f in box.faces for p in f.corners]
    hull = MultiPoint(pts).convex_hull
    if isinstance(hull, Polygon):  # 地面阴影：纸落在地面上的投影，向右下略偏移并模糊
        shift = SHADOW_SHIFT * w
        shadow = Image.new("L", (w, h), 0)
        ImageDraw.Draw(shadow).polygon(
            [(x + shift, y + shift * 0.6) for x, y in hull.exterior.coords], fill=SHADOW_ALPHA
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR * w))
        canvas.paste((40, 40, 48), mask=shadow)
    out = np.asarray(canvas, dtype=np.float32).copy()
    drawn = owner >= 0
    if box.edge_groups is not None:  # 同一组（瓶身、一张标签）的小平面之间不描边
        lut = np.array([*box.edge_groups, -1], dtype=np.int32)
        owner = lut[owner]
    edge = np.zeros_like(drawn)
    edge[:, :-1] |= owner[:, :-1] != owner[:, 1:]
    edge[:-1, :] |= owner[:-1, :] != owner[1:, :]
    edge &= drawn
    color[edge] *= EDGE_SHADE
    out[drawn] = color[drawn]
    image = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    return image.resize(size, Image.Resampling.LANCZOS)


__all__ = ["STATIC_RENDERER", "expected_static_renderer", "folded_static", "render_folded"]
