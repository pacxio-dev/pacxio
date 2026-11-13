"""软袋的平放效果图（ 节第一步）：正、背两面贴图，不变形。

只加两样很轻的效果（按"自然、不夸张"，强度可调）：
- 中间略鼓：内容区中间稍亮、往封边过渡（PILLOW）；
- 热封边、底褶焊上去的区域有压纹（CRIMP，细条纹，横封竖纹、竖封横纹）。
装满鼓起的曲面是第二步，不在这里。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from pacxio.export.canvas import _Map
from pacxio.geometry import path_to_polygon, polygons_of
from pacxio.mockup.fold import BoxModel, Face
from pacxio.mockup.render import View
from pacxio.model.dieline import Dieline

Vec3 = tuple[float, float, float]

PILLOW = 0.06  # 中间比边缘亮多少
CRIMP = 0.07  # 压纹的明暗幅度
CRIMP_PITCH_MM = 1.0
SLAB_MM = 3.0  # 平放时袋子的厚度（只为侧面能看到一条边）
EDGE_RGB = (214, 214, 210)
LAID_VIEW = View("open", 20.0, 22.0, "平放（装袋前，看印刷平面）")
# 不印刷的膜（自立袋底）按膜的本色
FILM_RGB: dict[str, tuple[int, int, int]] = {
    "white": (238, 238, 234),
    "clear": (222, 228, 230),
    "metallized": (196, 199, 204),
    "kraft": (186, 150, 108),
}


def _crop(art: Image.Image, m: _Map, rect: tuple[float, float, float, float]) -> Image.Image:
    x0, y0, x1, y1 = rect
    a, b = m.pt(x0, y1), m.pt(x1, y0)
    return art.crop((round(a[0]), round(a[1]), round(b[0]), round(b[1]))).convert("RGBA")


CONTENTS_RGB = (150, 104, 70)  # 透明窗里内容物的默认颜色（坚果 / 咖啡豆的棕，只作展示）
WINDOW_WHITE = 238  # 窗里接近白（没印）的像素算透明
WINDOW_GLOSS = 0.12


def window_art(art: Image.Image, m: _Map, dl: Dieline, contents: str | None = None) -> Image.Image:
    """透明窗：窗里没印东西（接近白）的地方透出内容物；印了的照常（印在透明膜上）。"""
    zones = [k for k in dl.keep_clear or [] if k.kind == "window"]
    if not zones:
        return art
    rgb = tuple(int(contents[i : i + 2], 16) for i in (1, 3, 5)) if contents else CONTENTS_RGB
    mask = Image.new("L", art.size, 0)
    draw = ImageDraw.Draw(mask)
    for k in zones:
        for poly in polygons_of(path_to_polygon(k.path)):
            draw.polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=255)
    arr = np.asarray(art.convert("RGBA"), dtype=np.float32).copy()
    win = np.asarray(mask) > 127
    blank = win & (arr[..., :3].min(axis=-1) >= WINDOW_WHITE)
    h, w = blank.shape
    yy, xx = np.mgrid[0:h, 0:w]
    sheen = 1 + WINDOW_GLOSS * np.sin((xx + yy) / max(w, h) * 6 * np.pi)  # 膜的一点反光
    for c in range(3):
        arr[..., c] = np.where(blank, np.clip(rgb[c] * sheen, 0, 255), arr[..., c])
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def _seal_masks(dl: Dieline, size: tuple[int, int], to_px: Any) -> tuple[np.ndarray, np.ndarray]:
    """封边 / 底褶区（全部、横向的）在一张图上的遮罩 0–1；to_px：刀版 mm → 像素。"""
    seal = Image.new("L", size, 0)
    horizontal = Image.new("L", size, 0)  # 横向的封边（上下封、底褶）：压纹竖着
    draw, hdraw = ImageDraw.Draw(seal), ImageDraw.Draw(horizontal)
    for k in dl.keep_clear or []:
        if k.kind not in ("seal", "gusset"):
            continue
        for poly in polygons_of(path_to_polygon(k.path)):
            pts = [to_px(x, y) for x, y in poly.exterior.coords]
            draw.polygon(pts, fill=255)
            bx0, by0, bx1, by1 = poly.bounds
            if (bx1 - bx0) > (by1 - by0):
                hdraw.polygon(pts, fill=255)
    return (
        np.asarray(seal, dtype=np.float32) / 255,
        np.asarray(horizontal, dtype=np.float32) / 255,
    )


def _crimp(
    arr: np.ndarray, mask: np.ndarray, hmask: np.ndarray, px_per_mm: tuple[float, float]
) -> np.ndarray:
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    phase_x = np.sin(2 * np.pi * xx / (CRIMP_PITCH_MM * px_per_mm[0]))
    phase_y = np.sin(2 * np.pi * yy / (CRIMP_PITCH_MM * px_per_mm[1]))
    return np.asarray(
        np.where(mask > 0.5, 1 + CRIMP * np.where(hmask > 0.5, phase_x, phase_y), 1.0)
    )


def crimp_art(art: Image.Image, m: _Map, dl: Dieline) -> Image.Image:
    """整张成品外观图上给封边 / 底褶加压纹（装满效果图用；明暗由曲面本身给）。"""
    mask, hmask = _seal_masks(dl, art.size, m.pt)
    arr = np.asarray(art.convert("RGBA"), dtype=np.float32).copy()
    k = _crimp(arr, mask, hmask, (m.s, m.s))
    arr[..., :3] = np.clip(arr[..., :3] * k[..., None], 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def _finish(tex: Image.Image, dl: Dieline, rect: tuple[float, float, float, float]) -> Image.Image:
    """在一面的贴图上加中间略鼓的明暗和封边压纹。"""
    x0, y0, x1, y1 = rect
    w, h = tex.size
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    mask, _hmask = _seal_masks(dl, (w, h), lambda x, y: ((x - x0) * sx, (y1 - y) * sy))
    arr = np.asarray(tex, dtype=np.float32).copy()
    yy, xx = np.mgrid[0:h, 0:w]
    u, v = (xx + 0.5) / w, (yy + 0.5) / h
    pillow = 1 + PILLOW * (np.sin(np.pi * u) * np.sin(np.pi * v) - 0.5)
    k = np.where(mask > 0.5, _crimp(arr, mask, _hmask, (sx, sy)), pillow)
    arr[..., :3] = np.clip(arr[..., :3] * k[..., None], 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def face_rect(dl: Dieline, pid: str) -> tuple[float, float, float, float]:
    """一面露在外面的那块（刀版坐标）：四边封 / 八边封的接缝封边在正面左边、左风琴右边，去掉。"""
    info = dl.style_meta["pouch"]
    x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
    if pid == "front":
        x0 += float(info.get("front_inset") or 0.0)
    elif pid == "side_a":
        x1 -= float(info.get("side_inset") or 0.0)
    return x0, y0, x1, y1


def _back_rects(dl: Dieline) -> list[tuple[float, float, float, float]]:
    """背面在刀版上取哪几块：两片袋是 back；背封袋是 back_b 左半 + back_a 右半（去掉中缝封那条）。"""
    info = dl.style_meta["pouch"]
    if "back" in info["layout_faces"]:
        return [dl.panel("back").polygon.bbox()]
    half, fin = float(info["width"]) / 2, float(info["fin"])
    bx0, y0, _, y1 = dl.panel("back_b").polygon.bbox()
    ax0 = dl.panel("back_a").polygon.bbox()[0]
    return [(bx0, y0, bx0 + half, y1), (ax0 + fin, y0, ax0 + fin + half, y1)]


def _strip(
    art: Image.Image, m: _Map, dl: Dieline, rects: list[tuple[float, float, float, float]]
) -> Image.Image:
    """几块面横着拼成一张（背封袋的背面 = 右半 + 左半）。"""
    tiles = []
    for rect in rects:
        tiles.append(_finish(_crop(art, m, rect), dl, rect))
    h = max(t.height for t in tiles)
    out = Image.new("RGBA", (sum(t.width for t in tiles), h))
    x = 0
    for t in tiles:
        out.paste(t, (x, 0))
        x += t.width
    return out


def pouch_scene(
    dl: Dieline, art: Image.Image, mapping: _Map
) -> tuple[BoxModel, dict[str, Image.Image]]:
    """平放的袋子：正面朝前（-y），背面朝后；侧边、上下是一条很薄的边。"""
    info = dl.style_meta["pouch"]
    w, h, d = float(info["width"]), float(info["height"]), SLAB_MM
    back = ["back"] if "back" in info["layout_faces"] else ["back_b", "back_a"]
    tex = {"front": _strip(art, mapping, dl, [face_rect(dl, "front")]),
           "back": _strip(art, mapping, dl, _back_rects(dl))}  # fmt: skip
    edge = Image.new("RGBA", (1, 1), (*EDGE_RGB, 255))
    tex |= {"side_a": edge, "side_b": edge, "top": edge, "bottom": edge}
    faces = [
        Face(
            "front",
            "front",
            "正面",
            (0, -1, 0),
            (0, 0, 0, 0),
            ((0, 0, h), (w, 0, h), (w, 0, 0), (0, 0, 0)),
        ),
        Face(
            "back",
            back[0],
            "背面",
            (0, 1, 0),
            (0, 0, 0, 0),
            ((w, d, h), (0, d, h), (0, d, 0), (w, d, 0)),
            also=tuple(back[1:]),
        ),  # fmt: skip
        Face(
            "side_b",
            "front",
            "右侧",
            (1, 0, 0),
            (0, 0, 0, 0),
            ((w, 0, h), (w, d, h), (w, d, 0), (w, 0, 0)),
        ),
        Face(
            "side_a",
            "front",
            "左侧",
            (-1, 0, 0),
            (0, 0, 0, 0),
            ((0, d, h), (0, 0, h), (0, 0, 0), (0, d, 0)),
        ),
        Face(
            "top",
            "front",
            "顶",
            (0, 0, 1),
            (0, 0, 0, 0),
            ((0, d, h), (w, d, h), (w, 0, h), (0, 0, h)),
        ),
        Face(
            "bottom",
            "front",
            "底",
            (0, 0, -1),
            (0, 0, 0, 0),
            ((0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0)),
        ),
    ]
    box = BoxModel(x=w, y=d, z=h, faces=faces)
    box.edge_groups = [0, 1, 2, 2, 2, 2]
    return box, tex


def pouch_box(dl: Dieline) -> BoxModel:
    """信息清单 / 面贴图用的两面模型：正面、背面各对应哪块刀版面（背封袋的背面是右半 + 左半）。"""
    info = dl.style_meta["pouch"]
    w, h = float(info["width"]), float(info["height"])
    back = ["back"] if "back" in info["layout_faces"] else ["back_b", "back_a"]
    d = SLAB_MM
    front = Face("front", "front", "正面", (0, -1, 0), face_rect(dl, "front"),
                 ((0, 0, h), (w, 0, h), (w, 0, 0), (0, 0, 0)))  # fmt: skip
    rect = dl.panel(back[0]).polygon.bbox()
    if len(back) == 2:
        x0, y0, _, y1 = dl.panel("back_a").polygon.bbox()
        _, _, x1, _ = dl.panel("back_b").polygon.bbox()
        rect = (x0, y0, x1, y1)  # 只作面贴图的范围；两半顺序由效果图拼
    back_face = Face("back", back[0], "背面", (0, 1, 0), rect,
                     ((w, d, h), (0, d, h), (0, d, 0), (w, d, 0)), also=tuple(back[1:]))  # fmt: skip
    faces = [front, back_face]
    sides: tuple[tuple[str, str, Vec3, tuple[Vec3, Vec3, Vec3, Vec3]], ...] = (
        ("side_a", "左侧面", (-1, 0, 0), ((0, d, h), (0, 0, h), (0, 0, 0), (0, d, 0))),
        ("side_b", "右侧面", (1, 0, 0), ((w, 0, h), (w, d, h), (w, d, 0), (w, 0, 0))),
        ("bottom", "底面", (0, 0, -1), ((0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0))),
    )
    for pid, label, normal, corners in sides:  # 侧风琴、平底袋的底：信息清单里也列出来
        if pid in info["layout_faces"]:
            faces.append(Face(pid, pid, label, normal, face_rect(dl, pid), corners))
    return BoxModel(x=w, y=d, z=h, faces=faces)


__all__ = ["pouch_box", "pouch_scene"]
