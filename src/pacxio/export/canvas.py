"""刀版画到位图上（开源）：毫米 → 像素、刀线 / 压线、开窗镂空。

3D 贴图、工作台的刀版图、带图文的预览（`export.preview`，闭源）共用。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from pacxio.geometry import Path
from pacxio.model.dieline import Dieline, EdgeType

if TYPE_CHECKING:
    from pacxio_pro.layout.build import LayoutResult

SAGITTA_MM = 0.05
_LINE_RGB = {
    EdgeType.CUT: (212, 0, 0),
    EdgeType.CREASE: (0, 160, 0),
    EdgeType.PERF: (0, 80, 208),
    EdgeType.PARTIAL_CUT: (192, 0, 192),
    EdgeType.REVERSE_CREASE: (208, 160, 0),
}
_DASH = {EdgeType.CREASE: (4.0, 2.0), EdgeType.PERF: (1.0, 2.0)}  # 单位 mm：(画, 空)
WINDOW_CHECKER_MM = 2.0  # 开窗镂空画成棋盘格（"透明"的通用画法）：格子边长
WINDOW_CHECKER_RGB = ((255, 255, 255), (222, 225, 231))


class _Map:
    """刀版坐标 (mm) → 预览像素。"""

    def __init__(self, layout: LayoutResult, dpi: int) -> None:
        c = layout.canvas
        self.s = dpi / 25.4
        self.x0, self.y1 = c.x0_mm, c.y1_mm

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.x0) * self.s, (self.y1 - y) * self.s)


def _dashed(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], on: float, off: float,
            color: tuple[int, int, int], width: int) -> None:  # fmt: skip
    """沿折线画虚线（on / off 为像素长度）。"""
    pattern, drawing, left = (on, off), True, on
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
        pos = 0.0
        while pos < seg:
            step = min(left, seg - pos)
            if drawing:
                draw.line([(x0 + ux * pos, y0 + uy * pos), (x0 + ux * (pos + step), y0 + uy * (pos + step))],
                          fill=color, width=width)  # fmt: skip
            pos += step
            left -= step
            if left <= 1e-9:
                drawing = not drawing
                left = pattern[0] if drawing else pattern[1]


def _draw_dieline(img: Image.Image, dl: Dieline, m: _Map) -> None:
    draw = ImageDraw.Draw(img)
    for t in (
        EdgeType.CREASE,
        EdgeType.PERF,
        EdgeType.PARTIAL_CUT,
        EdgeType.REVERSE_CREASE,
        EdgeType.CUT,
    ):
        for e in dl.edges_of(t):
            for seg in e.segments:
                pts = [m.pt(p.x, p.y) for p in Path([seg]).to_points(SAGITTA_MM)]
                if t in _DASH and not e.is_segmented_perf():
                    on, off = _DASH[t]
                    _dashed(draw, pts, on * m.s, off * m.s, _LINE_RGB[t], 1)
                else:
                    draw.line(pts, fill=_LINE_RGB[t], width=1)


def _draw_window_holes(img: Image.Image, dl: Dieline, m: _Map) -> None:
    """开窗切掉的那块画成棋盘格（镂空）。印刷文件里窗口照常印过去（是废料，），
    给人确认的图要看成品：这块印了什么都不显示。"""
    if not dl.windows:
        return
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    for w in dl.windows:
        draw.polygon([m.pt(p.x, p.y) for p in w.cut.to_points(SAGITTA_MM)], fill=255)
    box = mask.getbbox()
    if box is None:
        return
    x0, y0, x1, y1 = box
    cell = max(2, round(WINDOW_CHECKER_MM * m.s))
    yy, xx = np.indices((y1 - y0, x1 - x0))
    odd = ((xx + x0) // cell + (yy + y0) // cell) % 2 == 1
    light, dark = (np.array(c, dtype=np.uint8) for c in WINDOW_CHECKER_RGB)
    tile = Image.fromarray(np.where(odd[..., None], dark, light).astype(np.uint8), "RGB")
    img.paste(tile, (x0, y0), mask.crop(box))
