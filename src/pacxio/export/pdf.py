"""刀版 PDF（）与打样稿（）。

刀版 PDF 只含各线型的命名专色（Separation），全部叠印，1:1；刀线不用套版色。
PDF 没有圆弧图元，ReportLab 把圆弧转成贝塞尔曲线——这只发生在 PDF 里，
DXF / CF2 仍是精确的直线 + 圆弧。

打样稿是人工折叠 / 切割用的工作稿，不是印刷文件：用 Helvetica（标准字体，不嵌入），
面名用英文 id。
"""

from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib.colors import CMYKColorSep, Color, black, gray
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfgen.pathobject import PDFPathObject

from pacxio.export.pdfx import clean_pdf
from pacxio.geometry import Path as GPath
from pacxio.geometry import Segment, Vec
from pacxio.geometry.bridge import path_to_polygon
from pacxio.messages import t as translate
from pacxio.model.dieline import Dieline, Edge, EdgeType
from pacxio.model.profile import Profile

PT_PER_MM = 72.0 / 25.4  # D2：1 mm = 72/25.4 pt
PAGE_MARGIN_MM = 10.0  # 版面外留白（放文件信息；不属于刀版）
DIELINE_STROKE_PT = 0.25
PROOF_STROKE_PT = 0.5
# 专色在屏幕上的替代色（仅影响显示，不影响分版）
_ALT_CMYK: dict[EdgeType, tuple[float, float, float, float]] = {
    EdgeType.CUT: (0.0, 1.0, 1.0, 0.0),
    EdgeType.CREASE: (1.0, 0.0, 1.0, 0.0),
    EdgeType.PERF: (1.0, 1.0, 0.0, 0.0),
    EdgeType.PARTIAL_CUT: (0.0, 1.0, 0.0, 0.0),
    EdgeType.REVERSE_CREASE: (0.0, 0.0, 1.0, 0.0),
}
_GLUE_ALT_CMYK = (0.6, 0.0, 0.0, 0.0)  # 糊胶区标注的屏幕替代色
_KEEP_ALT_CMYK = (0.0, 0.45, 0.9, 0.0)  # 封口 / 拉链 / 搭接区标注的屏幕替代色（橙）
GLUE_HATCH_MM = 3.0  # 糊胶区斜线间距（只是标注画法）
_DRAW_ORDER = (
    EdgeType.CREASE,
    EdgeType.PERF,
    EdgeType.PARTIAL_CUT,
    EdgeType.REVERSE_CREASE,
    EdgeType.CUT,
)


def _pt(mm: float) -> float:
    return mm * PT_PER_MM


def _add_segment(p: PDFPathObject, s: Segment, ox: float, oy: float) -> None:
    if s.is_line:
        p.lineTo(_pt(s.end.x + ox), _pt(s.end.y + oy))
        return
    c, r, start_ang, theta, ccw = s.arc_info()
    p.arcTo(
        _pt(c.x - r + ox),
        _pt(c.y - r + oy),
        _pt(c.x + r + ox),
        _pt(c.y + r + oy),
        math.degrees(start_ang),  # type: ignore[arg-type]  # 存根误写为 int，实际接受 float
        math.degrees(theta) if ccw else -math.degrees(theta),  # type: ignore[arg-type]
    )


def _stroke_edges(c: Canvas, edges: list[Edge], ox: float, oy: float) -> None:
    for e in edges:
        p = c.beginPath()
        cursor = None
        for s in e.segments:
            if cursor is None or not cursor.is_close(s.start):
                p.moveTo(_pt(s.start.x + ox), _pt(s.start.y + oy))
            _add_segment(p, s, ox, oy)
            cursor = s.end
        c.drawPath(p, stroke=1, fill=0)


def write_dieline_pdf(dl: Dieline, profile: Profile, path: Path) -> None:
    """只含各线型专色的刀版 PDF，1:1，叠印。"""
    missing = {e.type.value for e in dl.edges} - set(profile.spot_names)
    if missing:
        raise KeyError(translate("Profile spot_names is missing line types: {types}", types=sorted(missing)))
    _, _, w, h = dl.bbox
    m = PAGE_MARGIN_MM
    c = Canvas(str(path), pagesize=(_pt(w + 2 * m), _pt(h + 2 * m)), invariant=1, pageCompression=0)
    c.setTitle(f"{dl.style} dieline")
    c.setCreator("pacxio")
    c.setTrimBox((_pt(m), _pt(m), _pt(m + w), _pt(m + h)))
    c.setArtBox((_pt(m), _pt(m), _pt(m + w), _pt(m + h)))
    c.setLineWidth(DIELINE_STROKE_PT)
    c.setLineJoin(1)
    c.setStrokeOverprint(True)  # 刀线与工艺必须叠印（CLAUDE.md）
    c.setFillOverprint(True)
    for t in _DRAW_ORDER:
        edges = dl.edges_of(t)
        if not edges:
            continue
        cmyk = _ALT_CMYK[t]
        c.setStrokeColor(CMYKColorSep(*cmyk, spotName=profile.spot_names[t.value], density=1.0))
        _stroke_edges(c, edges, m, m)
    if dl.glue_areas:  # 糊胶区信息层：描边 + 斜线，叠印；不是刀线
        if "glue_area" not in profile.spot_names:
            raise KeyError(translate("Profile spot_names is missing glue_area for the auto-lock bottom glue annotation"))
        name = profile.spot_names["glue_area"]
        c.setStrokeColor(CMYKColorSep(*_GLUE_ALT_CMYK, spotName=name, density=1.0))
        _draw_glue_areas(c, dl.glue_areas, m, m)
    if dl.keep_clear:  # 封口 / 拉链 / 撕口 / 搭接区信息层：只描边、叠印，不印（C5）
        c.setStrokeColor(
            CMYKColorSep(*_KEEP_ALT_CMYK, spotName=keep_clear_spot(profile), density=1.0)
        )
        c.setDash(2, 1)
        _draw_outlines(c, [k.path for k in dl.keep_clear], m, m)
        c.setDash()
    c.showPage()
    c.save()
    clean_pdf(path)  # 去掉 ReportLab 留下的空字体引用


def keep_clear_spot(profile: Profile) -> str:
    """封口区信息层的专色名；旧配置档没有 keep_clear 时用默认名。"""
    return profile.spot_names.get("keep_clear", "Keep_Clear")


def _draw_outlines(c: Canvas, paths: list[GPath], ox: float, oy: float) -> None:
    for area in paths:
        p = c.beginPath()
        p.moveTo(_pt(area.segments[0].start.x + ox), _pt(area.segments[0].start.y + oy))
        for s in area.segments:
            _add_segment(p, s, ox, oy)
        p.close()
        c.drawPath(p, stroke=1, fill=0)


def glue_hatch(
    area: GPath, spacing: float = GLUE_HATCH_MM
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """糊胶区里的 45° 斜线（裁到区域内）。"""
    from shapely.geometry import LineString

    poly = path_to_polygon(area)
    x0, y0, x1, y1 = poly.bounds
    span = (x1 - x0) + (y1 - y0)
    out = []
    k = spacing
    while k < span:
        line = LineString([(x0 + k, y0), (x0 + k - span, y0 + span)])
        seg = poly.intersection(line)
        for g in getattr(seg, "geoms", [seg]):
            if g.length > 0:
                (a, b) = list(g.coords)[0], list(g.coords)[-1]
                out.append(((a[0], a[1]), (b[0], b[1])))
        k += spacing
    return out


def _draw_glue_areas(c: Canvas, areas: list[GPath], ox: float, oy: float) -> None:
    for area in areas:
        p = c.beginPath()
        first = True
        for s in area.segments:
            if first:
                p.moveTo(_pt(s.start.x + ox), _pt(s.start.y + oy))
                first = False
            _add_segment(p, s, ox, oy)
        p.close()
        c.drawPath(p, stroke=1, fill=0)
        for (ax, ay), (bx, by) in glue_hatch(area):
            c.line(_pt(ax + ox), _pt(ay + oy), _pt(bx + ox), _pt(by + oy))


def _arrow(c: Canvas, cx: float, cy: float, length: float, ux: float = 0.0, uy: float = 1.0,
           both: bool = False) -> None:  # fmt: skip
    """以 (cx, cy) 为中点、指向 (ux, uy) 的箭头（mm）；both 时两头都有箭头（楞向）。"""
    half, head = length / 2, length * 0.25
    tip = (cx + ux * half, cy + uy * half)
    tail = (cx - ux * half, cy - uy * half)
    c.line(_pt(tail[0]), _pt(tail[1]), _pt(tip[0]), _pt(tip[1]))
    for (tx, ty), (dx, dy) in [(tip, (ux, uy))] + ([(tail, (-ux, -uy))] if both else []):
        bx, by = tx - dx * head, ty - dy * head  # 箭头底边中点
        p = c.beginPath()
        p.moveTo(_pt(tx), _pt(ty))
        p.lineTo(_pt(bx - dy * head * 0.6), _pt(by + dx * head * 0.6))
        p.lineTo(_pt(bx + dy * head * 0.6), _pt(by - dx * head * 0.6))
        p.close()
        c.drawPath(p, stroke=1, fill=1)


PROOF_FONT = "Helvetica"
PROOF_LABEL_PT = 8
PROOF_SMALL_PT = 6
PROOF_ARROW_MM = 12.0
PROOF_MIN_DIM_LABEL_MM = 32.0  # 窄于此值的面不标尺寸（放不下）
PROOF_MIN_ARROW_PANEL_MM = 30.0  # 矮于此值的面不画箭头
PROOF_FOOTER_LINES = 3
PROOF_FLUTE_MM = 14.0  # 楞向箭头
PROOF_PERF_COLOR = Color(0.0, 0.31, 0.82)  # 齿线：和预览 SVG / PNG 同一种蓝
PROOF_PERF_DASH = (1, 2)  # pt；只用于没有显式切段的连续齿线


def write_proof_pdf(
    dl: Dieline, profile: Profile, path: Path, job_id: str, extra_notes: list[str]
) -> None:
    """1:1 打样稿：刀版 + 每个面的名称 + UP 箭头 + 关键尺寸标注。"""
    _, _, w, h = dl.bbox
    m = PAGE_MARGIN_MM
    footer = PROOF_FOOTER_LINES * 4.0  # mm，页脚文字区
    c = Canvas(
        str(path),
        pagesize=(_pt(w + 2 * m), _pt(h + 2 * m + footer)),
        invariant=1,
        pageCompression=0,
    )
    c.setTitle(f"{job_id} proof 1:1")
    c.setCreator("pacxio")
    oy = m + footer
    c.setLineJoin(1)
    c.setLineWidth(PROOF_STROKE_PT)
    for t in _DRAW_ORDER:
        edges = dl.edges_of(t)
        if not edges:
            continue
        if t == EdgeType.CUT:
            c.setStrokeColor(black)
            c.setDash()
        elif t == EdgeType.PERF:  # 和压线分开：蓝色；已是切段 / 纸桥的画实线，不再套虚线
            c.setStrokeColor(PROOF_PERF_COLOR)
            segmented = [e for e in edges if e.is_segmented_perf()]
            c.setDash()
            _stroke_edges(c, segmented, m, oy)
            c.setDash(*PROOF_PERF_DASH)
            _stroke_edges(c, [e for e in edges if not e.is_segmented_perf()], m, oy)
            continue
        else:
            c.setStrokeColor(gray)
            c.setDash(3, 2)
        _stroke_edges(c, edges, m, oy)
    c.setDash()
    if dl.glue_areas:  # 糊胶区：灰色斜线，折纸样时知道哪里要粘
        c.setStrokeColor(gray)
        c.setLineWidth(PROOF_STROKE_PT / 2)
        _draw_glue_areas(c, dl.glue_areas, m, oy)
        c.setLineWidth(PROOF_STROKE_PT)
    if dl.keep_clear:  # 封口 / 拉链 / 撕口 / 搭接区：灰色虚线框，知道哪里不放字
        c.setStrokeColor(gray)
        c.setDash(2, 1)
        c.setLineWidth(PROOF_STROKE_PT / 2)
        _draw_outlines(c, [k.path for k in dl.keep_clear], m, oy)
        c.setLineWidth(PROOF_STROKE_PT)
        c.setDash()
    for win in dl.windows:  # 窗口（切线已经画了）：标名称
        x0, y0, x1, y1 = win.cut.bbox()
        c.setFillColor(black)
        c.setFont(PROOF_FONT, PROOF_SMALL_PT)
        c.drawCentredString(_pt((x0 + x1) / 2 + m), _pt((y0 + y1) / 2 + oy), f"{win.id} WINDOW")

    c.setStrokeColor(black)
    c.setFillColor(black)
    fold_order = dl.style_meta.get("fold_order") or dl.style_meta.get("bottom", {}).get(
        "fold_order", {}
    )
    for p in dl.panels:
        x0, y0, x1, y1 = p.polygon.bbox()
        centre = path_to_polygon(p.polygon).centroid  # 矩形等于包围盒中心；三角形、梯形放在形心
        cx, cy = centre.x + m, centre.y + oy
        mark = "?" if p.orientation_provisional else ""
        c.setFont(PROOF_FONT, PROOF_LABEL_PT)
        step = f"  fold #{fold_order[p.id]}" if p.id in fold_order else ""
        c.drawCentredString(
            _pt(cx), _pt(cy + 3), p.id + (" GLUE" if p.role.value == "glue_tab" else "") + step
        )
        c.setFont(PROOF_FONT, PROOF_SMALL_PT)
        if x1 - x0 >= PROOF_MIN_DIM_LABEL_MM:
            c.drawCentredString(_pt(cx), _pt(cy - 1), f"{x1 - x0:.2f} x {y1 - y0:.2f} mm")
        c.setLineWidth(PROOF_STROKE_PT)
        up = p.frame.apply_vector(Vec(0.0, 1.0))  # 面上图文的"上"在展开图里的方向
        span = (y1 - y0) if abs(up.y) >= abs(up.x) else (x1 - x0)
        if min(x1 - x0, y1 - y0) >= PROOF_MIN_ARROW_PANEL_MM:
            arrow_len = min(PROOF_ARROW_MM, 0.6 * span, 0.6 * (y1 - y0))
            ay = cy - 7 - arrow_len / 2
            _arrow(c, cx, ay, arrow_len, up.x, up.y)
            c.drawCentredString(_pt(cx + 5 + arrow_len / 2 * abs(up.x)), _pt(ay), f"UP{mark}")
        else:  # 面太小放不下箭头：只标注方向文字
            way = {(0, 1): "^", (0, -1): "v", (1, 0): ">", (-1, 0): "<"}.get(
                (round(up.x), round(up.y)), "^"
            )
            c.drawCentredString(_pt(cx), _pt(cy - 5), f"{way} UP{mark}")

    lines = [
        f"{job_id}  {dl.style.upper()}  overall {w:.2f} x {h:.2f} mm  "
        f"material {dl.material_preset} ({dl.caliper_mm} mm)  profile {dl.profile_id}",
        "PROOF 1:1 - not a production file. Cut = solid, crease = dashed."
        + (
            " Perf = blue (solid = cut through, gaps = bridges)."
            if any(e.is_segmented_perf() for e in dl.edges_of(EdgeType.PERF))
            else " Perf = blue dashed."
            if dl.edges_of(EdgeType.PERF)
            else ""
        )
        + (
            " UP? = orientation not yet verified by a physical fold test."
            if any(p.orientation_provisional for p in dl.panels)
            else ""
        ),
        *extra_notes,
    ]
    c.setFont(PROOF_FONT, PROOF_SMALL_PT)
    for i, text in enumerate(lines[:PROOF_FOOTER_LINES]):
        c.drawString(_pt(m), _pt(m + footer - 4.0 * (i + 1) + 1.0), text)
    flute = dl.style_meta.get("flute_dir")
    if flute in ("x", "y"):  # 瓦楞：楞向（双头箭头，画在页脚右侧）
        fx, fy = m + w - PROOF_FLUTE_MM, m + footer / 2
        _arrow(
            c,
            fx,
            fy,
            PROOF_FLUTE_MM * 0.6,
            1.0 if flute == "x" else 0.0,
            1.0 if flute == "y" else 0.0,
            both=True,
        )
        c.drawString(_pt(fx + PROOF_FLUTE_MM * 0.4), _pt(fy - 1), "FLUTE")
    c.showPage()
    c.save()
