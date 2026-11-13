"""SVG 导出：内部预览与测试快照用（），按线型着色。

坐标系：内部 y 向上，SVG y 向下，导出时整体翻转。圆弧用 SVG 原生 A 命令，不离散化。
注意：y 翻转只是改变显示坐标，不改变画面看到的转向，所以数学逆时针弧 sweep-flag=0。
"""

from __future__ import annotations

import math

from pacxio.geometry import Segment
from pacxio.model.dieline import Dieline, Edge, EdgeType, Panel

# 仅用于预览的线型样式（stroke, dasharray）；工厂文件的颜色/图层来自配置档
_PREVIEW_STYLE: dict[EdgeType, tuple[str, str | None]] = {
    EdgeType.CUT: ("#d40000", None),
    EdgeType.CREASE: ("#00a000", "4 2"),
    EdgeType.PERF: ("#0050d0", "1 2"),
    EdgeType.PARTIAL_CUT: ("#c000c0", "6 2 1 2"),
    EdgeType.REVERSE_CREASE: ("#d0a000", "2 2"),
}
_STROKE_WIDTH_MM = 0.25
_LABEL_FONT_MM = 4.0
_MARGIN_MM = 10.0
_NUM_DECIMALS = 4


def _f(v: float) -> str:
    s = f"{v:.{_NUM_DECIMALS}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _arc_cmd(seg: Segment, height: float) -> str:
    _, r, _, theta, ccw = seg.arc_info()
    large = 1 if theta > math.pi else 0
    sweep = 0 if ccw else 1
    return f"A{_f(r)} {_f(r)} 0 {large} {sweep} {_f(seg.end.x)} {_f(height - seg.end.y)}"


def _path_d(segments: list[Segment], height: float) -> str:
    """相连的段合成一条 d；断开处另起 M。"""
    parts: list[str] = []
    cursor = None
    for s in segments:
        if cursor is None or not cursor.is_close(s.start):
            parts.append(f"M{_f(s.start.x)} {_f(height - s.start.y)}")
        if s.is_line:
            parts.append(f"L{_f(s.end.x)} {_f(height - s.end.y)}")
        else:
            parts.append(_arc_cmd(s, height))
        cursor = s.end
    return " ".join(parts)


def _edge_element(e: Edge, height: float) -> str:
    stroke, dash = _PREVIEW_STYLE[e.type]
    if e.is_segmented_perf():
        dash = None
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<path id="{e.id}" data-type="{e.type.value}" d="{_path_d(e.segments, height)}" '
        f'fill="none" stroke="{stroke}"{dash_attr}/>'
    )


def _label(p: Panel, height: float) -> str:
    x0, y0, x1, y1 = p.polygon.bbox()
    cx, cy = (x0 + x1) / 2, height - (y0 + y1) / 2
    return (
        f'<text x="{_f(cx)}" y="{_f(cy)}" font-size="{_f(_LABEL_FONT_MM)}" '
        f'text-anchor="middle" fill="#888">{p.id}</text>'
    )


def dieline_to_svg(dl: Dieline, labels: bool = True, banner: str | None = None) -> str:
    """生成 SVG 文本。1 用户单位 = 1 mm；width/height 属性带 mm 单位，打印时 1:1。"""
    _, _, w, h = dl.bbox
    vb_w, vb_h = w + 2 * _MARGIN_MM, h + 2 * _MARGIN_MM
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(vb_w)}mm" height="{_f(vb_h)}mm" '
        f'viewBox="{_f(-_MARGIN_MM)} {_f(-_MARGIN_MM)} {_f(vb_w)} {_f(vb_h)}">',
        f'<g stroke-width="{_f(_STROKE_WIDTH_MM)}" stroke-linejoin="round">',
    ]
    # 先画压线再画刀线，刀线在上
    for t in (
        EdgeType.CREASE,
        EdgeType.PERF,
        EdgeType.PARTIAL_CUT,
        EdgeType.REVERSE_CREASE,
        EdgeType.CUT,
    ):
        out += [_edge_element(e, h) for e in dl.edges_of(t)]
    out.append("</g>")
    if labels:
        out.append('<g font-family="sans-serif">')
        out += [_label(p, h) for p in dl.panels]
        out.append("</g>")
    if banner:
        out.append(
            f'<text x="{_f(-_MARGIN_MM / 2)}" y="{_f(-_MARGIN_MM / 2)}" font-size="{_f(_LABEL_FONT_MM * 2)}" '
            f'font-family="sans-serif" fill="#d40000">{banner}</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"
