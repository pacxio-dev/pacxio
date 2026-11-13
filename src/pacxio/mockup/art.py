"""效果图的纹理：分析画布（底图与按顺序叠放的图形、图片、转曲文字）+ 工艺效果（金属光泽、亮面、凹凸），再按面裁出来。

工艺效果是**示意**：烫金 / 烫银 / 烫铜用整块的金属色加一道斜向的柔和高光（2026-10-30 以前是一道道周期光带，
放大看像条纹板，用户嫌和概念图的金属章差很多），镭射烫用彩色光带，局部 UV 用半透明亮面加高光边，
击凸 / 压凹用错位的明暗边，白墨为不透明白。真实效果取决于材料与工艺，必须打样确认。
颜色用简单公式从 CMYK 转 sRGB，不是色准预览。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from shapely.geometry.base import BaseGeometry

from pacxio.attachments import PLATING_RGB, AttachmentLayout, split_rim
from pacxio.export.canvas import _Map
from pacxio.geometry import Path as GeometryPath
from pacxio.geometry import outline_shape, path_to_polygon
from pacxio.geometry.bridge import paint_order, shallow_polygons
from pacxio.mockup.fold import BoxModel, Face
from pacxio.model.dieline import Dieline, DielineWindow

if TYPE_CHECKING:  # 闭源（）：只在服务器上有，这里只用作类型
    from pacxio_pro.finishes import FinishLayout
    from pacxio_pro.finishes.surface import SurfaceLayout
    from pacxio_pro.layout.build import LayoutResult

MOCKUP_DPI = 150
FOIL_STOPS = {  # 渐变色标（暗 → 亮），金属光泽
    "foil_gold": [(138, 96, 18), (214, 164, 52), (255, 226, 130), (190, 140, 36), (255, 238, 170)],
    "foil_silver": [
        (120, 124, 132),
        (196, 200, 208),
        (250, 251, 255),
        (160, 164, 172),
        (236, 238, 244),
    ],
    "foil_copper": [(112, 56, 30), (184, 108, 66), (240, 172, 128), (156, 84, 48), (248, 200, 164)],
}
HOLO_BANDS = [(255, 120, 200), (120, 200, 255), (255, 240, 120), (150, 255, 180), (200, 150, 255)]
SHEEN_PERIOD_MM = 14.0  # 光带周期（镭射烫、局部 UV）
SHEEN_ANGLE = 0.8  # 光带沿对角方向
FOIL_HIGHLIGHT_AT = 0.38  # 烫金的高光：沿对角方向在工艺区域的这个位置（0–1）
FOIL_HIGHLIGHT_WIDTH = 0.22  # 高光宽度（占对角长度）
FOIL_BASE = 0.55  # 高光以外的亮度：0 = 色标里的中间色，1 = 亮色【初值】
# 局部亮 UV（多在哑膜上）：颜色更润、略深（2026-10-30 以前是蒙一层白，像把图洗淡了）
UV_DEEPEN = 0.035
UV_HIGHLIGHT = 0.08  # 斜向一道柔和高光的最大强度（同烫金的高光位置）
UV_EDGE_ALPHA = 0.25  # 左上边沿迎光的一道亮边
EMBOSS_SHIFT_MM = 0.35
EMBOSS_SHADOW = 0.38
EMBOSS_LIGHT = 0.30
WHITE_INK = (250, 250, 250)
# 开窗：窗口里画盒子内部（示意，不是产品）——上沿被盒壁挡光较暗，往下渐亮；贴膜窗加一道斜向反光
WINDOW_INSIDE_TOP = (118, 114, 108)
WINDOW_INSIDE_BOTTOM = (200, 196, 188)
FILM_GLOSS_ALPHA = 0.28
FILM_GLOSS_WIDTH = 0.18  # 反光带宽占窗口宽度的比例
FILM_EDGE_RGB = (236, 240, 246)


def _mask(size: tuple[int, int], f: FinishLayout | SurfaceLayout, m: _Map) -> Image.Image:
    img = Image.new("L", size, 0)
    d = ImageDraw.Draw(img)
    for poly in paint_order(f.world):
        d.polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=255)
        for hole in poly.interiors:
            d.polygon([m.pt(x, y) for x, y in hole.coords], fill=0)
    return img


def _ramp(t: np.ndarray, stops: list[tuple[int, int, int]]) -> np.ndarray:
    """t ∈ [0,1] → 在色标之间循环插值的 RGB (…,3)。"""
    pts = np.array(stops, dtype=np.float32)
    n = len(pts)
    pos = np.clip(t, 0, 0.9999) * (n - 1)
    i = np.floor(pos).astype(int)
    frac = (pos - i)[..., None]
    return np.asarray(pts[i] * (1 - frac) + pts[np.minimum(i + 1, n - 1)] * frac, dtype=np.float32)


def _sheen_t(shape: tuple[int, int], s: float, origin: tuple[int, int]) -> np.ndarray:
    """沿对角方向周期变化的 0–1 光带。"""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    phase = ((xx + origin[0]) + SHEEN_ANGLE * (yy + origin[1])) / (SHEEN_PERIOD_MM * s)
    return np.asarray(0.5 + 0.5 * np.sin(2 * np.pi * phase), dtype=np.float32)


def _foil_sheen(shape: tuple[int, int]) -> np.ndarray:
    """烫金的亮度 0–1：整块是中间偏亮的金属色，沿对角方向一道柔和的高光（不是周期条纹）。"""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = (xx / max(w - 1, 1) + SHEEN_ANGLE * yy / max(h - 1, 1)) / (1 + SHEEN_ANGLE)
    peak = np.exp(-(((u - FOIL_HIGHLIGHT_AT) / FOIL_HIGHLIGHT_WIDTH) ** 2))
    return np.asarray(FOIL_BASE + (1 - FOIL_BASE) * peak, dtype=np.float32)


def _apply_finish(img: Image.Image, f: FinishLayout, m: _Map) -> None:
    mask = _mask(img.size, f, m)
    bbox = mask.getbbox()
    if bbox is None:
        return
    pad = int(EMBOSS_SHIFT_MM * m.s * 3) + 4
    x0, y0 = max(bbox[0] - pad, 0), max(bbox[1] - pad, 0)
    x1, y1 = min(bbox[2] + pad, img.width), min(bbox[3] + pad, img.height)
    win = (x0, y0, x1, y1)
    region = img.crop(win)
    mk = mask.crop(win)
    mk_arr = (np.asarray(mk, dtype=np.float32) / 255.0)[..., None]
    base = np.asarray(region, dtype=np.float32)
    t = _sheen_t((y1 - y0, x1 - x0), m.s, (x0, y0))

    if f.type in FOIL_STOPS:
        stops = FOIL_STOPS[f.type]
        metal = _ramp(_foil_sheen((y1 - y0, x1 - x0)), [stops[1], stops[2], stops[4]])
        out = base * (1 - mk_arr) + metal * mk_arr
    elif f.type == "foil_holo":
        metal = _ramp(t, HOLO_BANDS)
        out = base * (1 - mk_arr) + metal * mk_arr
    elif f.type == "white":
        out = base * (1 - mk_arr) + np.array(WHITE_INK, dtype=np.float32) * mk_arr
    elif f.type == "spot_uv":
        # 亮 UV：那块颜色更润、略深，斜向一道柔和高光，左上边沿迎光一道细亮边（不是整块蒙白）
        peak = (_foil_sheen((y1 - y0, x1 - x0)) - FOIL_BASE) / (1 - FOIL_BASE)
        hl = (UV_HIGHLIGHT * peak)[..., None] * mk_arr
        lit = mk_arr[..., 0] * (1 - np.asarray(_offset(mk, 1, 1), dtype=np.float32) / 255.0)
        edge_a = np.clip(lit, 0, 1)[..., None] * UV_EDGE_ALPHA
        out = base * (1 - UV_DEEPEN * mk_arr)
        out = out * (1 - hl) + 255.0 * hl
        out = out * (1 - edge_a) + 255.0 * edge_a
    else:  # emboss / deboss：错位的明暗边
        shift = max(1, round(EMBOSS_SHIFT_MM * m.s))
        sign = 1 if f.type == "emboss" else -1
        dark = np.asarray(_offset(mk, sign * shift, sign * shift), dtype=np.float32) / 255.0
        light = np.asarray(_offset(mk, -sign * shift, -sign * shift), dtype=np.float32) / 255.0
        dark_a = np.clip(dark - mk_arr[..., 0], 0, 1)[..., None] * EMBOSS_SHADOW
        light_a = np.clip(light - mk_arr[..., 0], 0, 1)[..., None] * EMBOSS_LIGHT
        out = base * (1 - dark_a)
        out = out * (1 - light_a) + 255.0 * light_a
        out = out * (1 - mk_arr * 0.06)  # 面本身略压暗
    img.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), win)


ATTACH_SHADOW_ALPHA = 0.35  # 金属标在纸上的投影（示意）
ATTACH_BEVEL_MM = 0.8  # 标的外缘倒角：左上一圈亮、右下一圈暗
ATTACH_RELIEF_EDGE_MM = 0.25  # 凸起图案的边：同样左上亮、右下暗


def _geom_mask(size: tuple[int, int], g: BaseGeometry, m: _Map) -> Image.Image:
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    for poly in paint_order(g):
        d.polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=255)
        for hole in poly.interiors:
            d.polygon([m.pt(x, y) for x, y in hole.coords], fill=0)
    return mask


def _apply_attachment(img: Image.Image, a: AttachmentLayout, m: _Map) -> None:
    """金属标（示意，）：纸上一圈投影；标面是电镀色加一道柔和高光，外缘倒角左上亮右下暗；
    凸起的字和纹样深一些，边上同样有明暗。真实效果以标牌厂样品为准。"""
    mask = _geom_mask(img.size, a.world, m)
    bbox = mask.getbbox()
    if bbox is None:
        return
    shift = max(1, round(a.spec.thickness_mm * 0.5 * m.s))
    pad = 3 * shift + 4
    x0, y0 = max(bbox[0] - pad, 0), max(bbox[1] - pad, 0)
    x1, y1 = min(bbox[2] + pad, img.width), min(bbox[3] + pad, img.height)
    win = (x0, y0, x1, y1)
    mk = mask.crop(win)
    mk_a = np.asarray(mk, dtype=np.float32)[..., None] / 255.0
    base = np.asarray(img.crop(win), dtype=np.float32)
    shadow = _offset(mk, shift, shift).filter(ImageFilter.GaussianBlur(shift))
    sh_a = np.asarray(shadow, dtype=np.float32)[..., None] / 255.0 * ATTACH_SHADOW_ALPHA
    out = base * (1 - sh_a)
    mid, light, deep = (np.array(c, dtype=np.float32) for c in PLATING_RGB[a.spec.plating])
    s = _foil_sheen((y1 - y0, x1 - x0))[..., None]
    metal = mid * 0.9 + (light - mid * 0.9) * (s - FOIL_BASE) / (1 - FOIL_BASE)

    def edges(mk_img: Image.Image, px: int) -> tuple[np.ndarray, np.ndarray]:
        arr = np.asarray(mk_img, dtype=np.float32) / 255.0
        lit = arr * (1 - np.asarray(_offset(mk_img, px, px), dtype=np.float32) / 255.0)
        dark = arr * (1 - np.asarray(_offset(mk_img, -px, -px), dtype=np.float32) / 255.0)
        return lit[..., None], dark[..., None]

    lit, dark = edges(mk, max(1, round(ATTACH_BEVEL_MM * m.s)))
    face = metal * (1 - lit * 0.55) + 255.0 * lit * 0.55
    face = face * (1 - dark * 0.45)
    rim, pattern = split_rim(a)
    for g, tone in ((rim, None), (pattern, deep)):  # 宽边：底面的金属色；字、纹样：深古铜色
        if g.is_empty:
            continue
        rk = _geom_mask(img.size, g, m).crop(win)
        r_a = np.asarray(rk, dtype=np.float32)[..., None] / 255.0
        if tone is not None:
            face = face * (1 - r_a) + (tone + (metal - mid) * 0.35) * r_a
        rlit, rdark = edges(rk, max(1, round(ATTACH_RELIEF_EDGE_MM * m.s)))
        face = face * (1 - rlit * 0.5) + 255.0 * rlit * 0.5
        face = face * (1 - rdark * 0.45)
    out = out * (1 - mk_a) + face * mk_a
    img.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), win)


def _offset(img: Image.Image, dx: int, dy: int) -> Image.Image:
    """整体平移（不回绕，空出）。"""
    out = Image.new("L", img.size, 0)
    out.paste(img, (dx, dy))
    return out


def render_art(
    layout: LayoutResult, dl: Dieline, dpi: int = MOCKUP_DPI
) -> tuple[Image.Image, _Map]:
    """整张展开图的"成品外观"：分析画布（图形、图片、文字已按顺序叠好）+ 工艺效果 + 开窗，不含刀线。"""
    from pacxio_pro.export.preview import _base_image  # 闭源：合成后的画布转屏幕颜色

    img = _base_image(layout, dpi)
    m = _Map(layout, dpi)
    for f in layout.finishes:
        _apply_finish(img, f, m)
    for a in layout.attachments:
        _apply_attachment(img, a, m)
    for w in dl.windows:
        _paint_window(img, w, m)
    # Flat cards and upright headers are actual cut sheets. Their corners and
    # hanging holes must also be absent in static views, not painted as paper.
    if (
        dl.style_meta.get("layout") in ("flat", "label", "pouch")
        or dl.style.endswith("_hanger")
        or dl.style_meta.get("paper_cutout")
    ):
        alpha = _geom_mask(img.size, outline_shape(dl.outline_segments()), m)
        draw = ImageDraw.Draw(alpha)
        for edge in dl.inner_cuts():
            if edge.id.startswith("cut_hang_") or dl.style_meta.get("paper_cutout"):
                hole = path_to_polygon(GeometryPath(edge.segments, closed=True))
                draw.polygon([m.pt(x, y) for x, y in hole.exterior.coords], fill=0)
        shapes = [e for e in dl.inner_cuts() if e.id.startswith("cut_shape_")]
        if shapes:  # 异形小袋：外形以外是裁掉的废边
            keep = Image.new("L", img.size, 0)
            kd = ImageDraw.Draw(keep)
            for edge in shapes:
                poly = path_to_polygon(GeometryPath(edge.segments, closed=True))
                kd.polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=255)
            alpha = ImageChops.multiply(alpha, keep)
        img = img.convert("RGBA")
        img.putalpha(alpha)
    return img, m


def _paint_window(img: Image.Image, w: DielineWindow, m: _Map) -> None:
    """窗口：切掉的地方露出盒子内部（示意）。只开在盒身面上，面的上方就是图像上方。"""
    poly = path_to_polygon(w.cut)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=255)
    bbox = mask.getbbox()
    if bbox is None:
        return
    x0, y0, x1, y1 = bbox
    h, wd = y1 - y0, x1 - x0
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    top = np.array(WINDOW_INSIDE_TOP, dtype=np.float32)
    bottom = np.array(WINDOW_INSIDE_BOTTOM, dtype=np.float32)
    inside = np.broadcast_to(top * (1 - t) + bottom * t, (h, wd, 3)).copy()
    if w.film:
        yy, xx = np.mgrid[0:h, 0:wd].astype(np.float32)
        u = (xx + 0.6 * yy) / max(wd + 0.6 * h, 1.0)  # 斜向位置 0–1
        band = np.clip(1 - np.abs(u - 0.35) / FILM_GLOSS_WIDTH, 0, 1)[..., None] * FILM_GLOSS_ALPHA
        inside = inside * (1 - band) + 255.0 * band
    patch = Image.fromarray(np.clip(inside, 0, 255).astype(np.uint8), "RGB")
    img.paste(patch, (x0, y0), mask.crop(bbox))
    if w.film:
        ImageDraw.Draw(img).line(
            [m.pt(x, y) for x, y in poly.exterior.coords], fill=FILM_EDGE_RGB, width=1
        )


def _fold_texture(
    art: Image.Image,
    m: _Map,
    face: Face,
    fill: str | int | tuple[int, ...],
    cutout: bool,
) -> Image.Image:
    """同一纸片轮廓裁切、折合、遮挡适用于底色、材质、高度与区域遮罩。"""
    tl, tr, _, bl = face.corners
    width = sum((a - b) ** 2 for a, b in zip(tl, tr, strict=True)) ** 0.5
    height = sum((a - b) ** 2 for a, b in zip(tl, bl, strict=True)) ** 0.5
    size = (max(1, round(width * m.s)), max(1, round(height * m.s)))
    sx, sy = size[0] / width, size[1] / height
    out = Image.new(art.mode, size, fill)
    coverage = Image.new("L", size, 0)
    for patch in face.patches:
        inv = patch.to_face.inverse()
        coefficients = (
            inv.a * m.s / sx,
            inv.c * m.s / sy,
            (inv.e - m.x0) * m.s,
            -inv.b * m.s / sx,
            -inv.d * m.s / sy,
            (m.y1 - inv.f) * m.s,
        )
        pixels = art.transform(
            size, Image.Transform.AFFINE, coefficients, Image.Resampling.BILINEAR
        )
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        folded: BaseGeometry = path_to_polygon(patch.outline.transformed(patch.to_face))
        for cut_path in patch.cutouts:
            folded = folded.difference(path_to_polygon(cut_path.transformed(patch.to_face)))
        for poly in shallow_polygons(folded):
            draw.polygon([(x * sx, y * sy) for x, y in poly.exterior.coords], fill=255)
            for hole in poly.interiors:
                draw.polygon([(x * sx, y * sy) for x, y in hole.coords], fill=0)
        out.paste(pixels, (0, 0), mask)
        coverage = ImageChops.lighter(coverage, mask)
    if cutout:
        out = out.convert("RGBA")
        out.putalpha(coverage)
    return out


def face_textures(
    art: Image.Image,
    m: _Map,
    box: BoxModel,
    fill: str | int | tuple[int, ...] = "white",
    *,
    cutout: bool = False,
) -> dict[str, Image.Image]:
    """按每个面的刀版矩形从整张图里裁出纹理。"""
    out: dict[str, Image.Image] = {}

    def crop(rect: tuple[float, float, float, float]) -> Image.Image:
        x0, y0, x1, y1 = rect
        left, top = m.pt(x0, y1)
        right, bottom = m.pt(x1, y0)
        return art.crop((round(left), round(top), round(right), round(bottom)))

    for face in box.faces:
        if face.patches:
            out[face.key] = _fold_texture(art, m, face, fill, cutout)
            continue
        if not face.parts:
            out[face.key] = crop(face.tex_rect)
            continue
        pieces = [crop(r).rotate(180) if rot else crop(r) for r, rot in face.parts]
        tex = Image.new(
            art.mode, (max(p.width for p in pieces), sum(p.height for p in pieces)), fill
        )
        y = 0
        for p in pieces:
            tex.paste(p, (0, y))
            y += p.height
        out[face.key] = tex
    return out
