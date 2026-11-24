"""标准包装法规与环保矢量符号库（开源）：PAO 开口罐、莫比乌斯回收环、EU ℮ 标。

小尺寸行业通用标志（约 6–15 mm）不应从低分辨率平面图硬描（易断线、锯齿，且在罐内抠字会啃断细线框）。
本模块直接按标准比例生成纯路径、无文字标签、无渐变/透明度的印刷级 SVG，与印前检查和 CMYK 重映射兼容。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pacxio import dielines
from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.visual.meta import AssetMeta, PrepStep, now_iso, sha256_of, write_meta

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_SYMBOL_COLOR = "#3f3f42"


@dataclass(frozen=True)
class SymbolSpec:
    id: str
    title: str
    aliases: tuple[str, ...]
    viewbox: tuple[float, float]
    note: str
    text_box_frac: tuple[float, float, float, float] | None = None  # 面局部相对框 (x, y, w, h)，y 朝上


SYMBOLS: dict[str, SymbolSpec] = {
    "pao": SymbolSpec(
        id="pao",
        title="Period-After-Opening open jar (EC 1223/2009)",
        aliases=("pao-jar", "open-jar", "jar"),
        viewbox=(100.0, 84.0),
        note="Cosmetics PAO open jar frame; pair with centered engine text (e.g. 12M) bound to content inventory.",
        text_box_frac=(0.18, 0.12, 0.64, 0.34),
    ),
    "mobius": SymbolSpec(
        id="mobius",
        title="Mobius recycling loop (ISO 7000-1135)",
        aliases=("recycle", "recycling", "mobius-loop"),
        viewbox=(100.0, 88.0),
        note="Universal three-arrow folded Mobius recycling loop.",
    ),
    "e-mark": SymbolSpec(
        id="e-mark",
        title="EU estimated sign ℮ (Directive 76/211/EEC)",
        aliases=("estimated", "eu-e", "emark"),
        viewbox=(108.0, 100.0),
        note="Standard European e-mark for prepackaged net quantity.",
    ),
}


def resolve_symbol_id(name: str) -> str | None:
    key = name.strip().lower().replace("_", "-")
    if key in SYMBOLS:
        return key
    for sid, spec in SYMBOLS.items():
        if key in spec.aliases:
            return sid
    return None


def list_symbols() -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "title": s.title,
            "aliases": list(s.aliases),
            "aspect": round(s.viewbox[0] / s.viewbox[1], 3),
            "note": s.note,
        }
        for s in SYMBOLS.values()
    ]


def pao_text_box(jar_box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """给定 PAO 罐子放置框 (x, y, w, h)（面局部 mm，y 朝上），返回居中放 12M/6M 文字的建议框。"""
    fx, fy, fw, fh = SYMBOLS["pao"].text_box_frac or (0.18, 0.12, 0.64, 0.34)
    x, y, w, h = jar_box
    return (round(x + fx * w, 2), round(y + fy * h, 2), round(fw * w, 2), round(fh * h, 2))


def _rot(x: float, y: float, cx: float, cy: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    dx, dy = x - cx, y - cy
    return cx + dx * c - dy * s, cy + dx * s + dy * c


def _poly_d(pts: list[tuple[float, float]]) -> str:
    head = f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"
    body = " ".join(f"L {x:.2f} {y:.2f}" for x, y in pts[1:])
    return f"{head} {body} Z"


def _pao_svg(color: str) -> str:
    """EC 1223/2009 开口霜罐矢量框（纯路径描边，3.8–4.0 单位粗细，10 mm 高度时线宽 ≥ 0.45 mm）。"""
    # 倾斜盖子：围绕 (48, 19.5) 旋转 -21° 的胶囊圆顶与双沿，与罐口分离干净
    cx, cy, ang = 48.0, 19.2, -21.0
    a, b_ry, thick = 35.5, 4.2, 5.2

    def lid_pt(lx: float, ly: float) -> tuple[float, float]:
        return _rot(cx + lx, cy + ly, cx, cy, ang)

    steps = 24
    top_upper = [
        lid_pt(a * math.cos(t), -thick / 2.0 - b_ry * math.sin(t))
        for t in (math.pi * i / steps for i in range(steps + 1))
    ]
    top_lower = [
        lid_pt(a * math.cos(t), -thick / 2.0 + b_ry * math.sin(t))
        for t in (math.pi * i / steps for i in range(steps + 1))
    ]
    bot_lower = [
        lid_pt(a * math.cos(t), thick / 2.0 + b_ry * math.sin(t))
        for t in (math.pi * i / steps for i in range(steps + 1))
    ]

    def open_d(pts: list[tuple[float, float]]) -> str:
        return "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)

    rim_pts = [
        (50.0 + 36.0 * math.cos(2 * math.pi * i / 48), 42.0 + 6.5 * math.sin(2 * math.pi * i / 48))
        for i in range(48)
    ]
    bottom_pts = [
        (50.0 + 36.0 * math.cos(math.pi - math.pi * i / 24), 72.5 + 6.8 * math.sin(math.pi * i / 24))
        for i in range(25)
    ]
    jar_wall = [(14.0, 42.0), *bottom_pts, (86.0, 42.0)]

    lid_outline = [*top_upper, *reversed(bot_lower)]
    stroke = f'fill="none" stroke="{color}" stroke-width="4.0" stroke-linecap="round" stroke-linejoin="round"'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 84">\n'
        f'  <path d="{_poly_d(lid_outline)}" {stroke}/>\n'
        f'  <path d="{open_d(top_lower)}" {stroke}/>\n'
        f'  <path d="{_poly_d(rim_pts)}" {stroke}/>\n'
        f'  <path d="{open_d(jar_wall)}" {stroke}/>\n'
        "</svg>\n"
    )


def _fillet_corner(
    p_prev: tuple[float, float],
    p_corner: tuple[float, float],
    p_next: tuple[float, float],
    inset: float,
    steps: int = 4,
) -> list[tuple[float, float]]:
    """用二次贝塞尔平滑折角（返回折角前后离散点序列，保持纯 L/M 多边形兼容全部解析器）。"""
    v1x, v1y = p_prev[0] - p_corner[0], p_prev[1] - p_corner[1]
    v2x, v2y = p_next[0] - p_corner[0], p_next[1] - p_corner[1]
    l1, l2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
    if l1 <= 1e-6 or l2 <= 1e-6:
        return [p_corner]
    d1, d2 = min(inset, l1 * 0.4), min(inset, l2 * 0.4)
    a = (p_corner[0] + v1x / l1 * d1, p_corner[1] + v1y / l1 * d1)
    b = (p_corner[0] + v2x / l2 * d2, p_corner[1] + v2y / l2 * d2)
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        u = i / steps
        om = 1.0 - u
        pts.append(
            (
                om * om * a[0] + 2 * om * u * p_corner[0] + u * u * b[0],
                om * om * a[1] + 2 * om * u * p_corner[1] + u * u * b[1],
            )
        )
    return pts


def _mobius_svg(color: str) -> str:
    """ISO 7000-1135 三箭头莫比乌斯循环标（120° 精确旋转对称，轻盈舒展丝带、宽平外折角、大中空三角）。"""
    # 以 (50, 48.5) 为 120° 中心构造单支底边右→左箭头 + 右斜边折尾，再旋转 0°/120°/240°
    cx, cy = 50.0, 48.5
    h_in = 18.0  # 更大中空内三角（底内沿 y=66.5）
    t = 11.4  # 舒展轻盈的丝带宽度（底外沿 y=77.9）
    h_out = h_in + t  # 29.4
    d_cap = 43.2  # 宽平截顶外折角（顶边 y=5.3，平折带宽 19.6 单位，整体自然宽扁）
    sq3 = math.sqrt(3.0)

    y_in = cy + h_in
    y_out = cy + h_out
    y_mid = (y_in + y_out) * 0.5

    p_in_br = (cx + h_in * sq3, y_in)
    p_bevel_bot = (cx + (d_cap - 0.5 * h_out) / (sq3 * 0.5), y_out)
    p_bevel_right = (cx + (d_cap + h_out) / sq3, cy + (d_cap - h_out))

    # 旋转 +120° 后底边左尾切在 x_tail=34.0，与本支箭尖 x_tip=42.2 留出 8.2 单位清晰白缝
    x_tip, x_barb, barb_ext = 42.2, 53.6, 5.4
    x_tail_bot = 34.0
    p_tail_in = _rot(x_tail_bot, y_in, cx, cy, -120.0)
    p_tail_out = _rot(x_tail_bot, y_out, cx, cy, -120.0)

    c_bot = _fillet_corner((x_barb, y_out), p_bevel_bot, p_bevel_right, inset=2.8)
    c_right = _fillet_corner(p_bevel_bot, p_bevel_right, p_tail_out, inset=2.8)

    arrow_base: list[tuple[float, float]] = [
        *c_bot,
        *c_right,
        p_tail_out,
        p_tail_in,
        p_in_br,
        (x_barb, y_in),
        (x_barb, y_in - barb_ext),
        (x_tip, y_mid),
        (x_barb, y_out + barb_ext),
        (x_barb, y_out),
    ]
    paths: list[str] = []
    for deg in (0.0, 120.0, 240.0):
        pts = [_rot(x, y, cx, cy, deg) for x, y in arrow_base]
        paths.append(f'  <path d="{_poly_d(pts)}" fill="{color}"/>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 88">\n'
        + "\n".join(paths)
        + "\n</svg>\n"
    )


def _emark_svg(color: str) -> str:
    """EU 76/211/EEC Annex II 法定 ℮ 标（高反差几何圆弧：粗左月牙、粗右上腹、细上下弧与细中横）。"""
    cx, cy = 54.0, 50.0
    rx_out, ry_out = 48.0, 44.5
    icx, icy = 56.5, 50.0
    rx_in, ry_in = 28.5, 40.3  # 上下弧厚 4.2（轻盈细弧），左脊厚 22.0，右上腹厚 17.0
    bar_top, bar_bot = 47.9, 52.1  # 4.2 单位细中横
    mouth_deg = 39.0  # 右下开口端角（向下）

    def ellipse_pts(
        ox: float, oy: float, rx: float, ry: float, a0: float, a1: float, n: int
    ) -> list[tuple[float, float]]:
        return [
            (
                ox + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                oy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
            )
            for i in range(n + 1)
        ]

    def x_on_ellipse(ox: float, oy: float, rx: float, ry: float, y: float, right: bool) -> float:
        s = max(-0.999, min(0.999, (y - oy) / ry))
        dx = rx * math.sqrt(max(0.0, 1.0 - s * s))
        return ox + (dx if right else -dx)

    # 右下开口水平截线 y_cut
    y_cut = cy + ry_out * math.sin(math.radians(mouth_deg))
    in_mouth_deg = math.degrees(math.asin(max(-0.98, min(0.98, (y_cut - icy) / ry_in))))

    # 外椭圆：从 mouth_deg 顺时针绕经底(90°)、左(180°)、顶(270°) 到中横下缘右端(360° + bar_bot_deg)
    bar_bot_deg = math.degrees(math.asin((bar_bot - cy) / ry_out))
    outer_pts = ellipse_pts(cx, cy, rx_out, ry_out, mouth_deg, 360.0 + bar_bot_deg, 84)

    # 沿中横下缘向左走到内椭圆左交点，再沿内椭圆下半弧逆时针回到 in_mouth_deg
    in_bar_bot_left_deg = 180.0 - math.degrees(math.asin((bar_bot - icy) / ry_in))
    inner_lower = ellipse_pts(icx, icy, rx_in, ry_in, in_bar_bot_left_deg, in_mouth_deg, 40)
    body_pts = [*outer_pts, *inner_lower]

    # 上半 D 形内孔（逆时针绕向，兼容 nonzero 与 evenodd）
    in_bar_top_right_deg = math.degrees(math.asin((bar_top - icy) / ry_in))
    hole_right_deg = 360.0 + in_bar_top_right_deg
    hole_left_deg = 180.0 - in_bar_top_right_deg
    hole_arc = ellipse_pts(icx, icy, rx_in, ry_in, hole_right_deg, hole_left_deg, 44)
    hole_x_l = x_on_ellipse(icx, icy, rx_in, ry_in, bar_top, right=False)
    hole_x_r = x_on_ellipse(icx, icy, rx_in, ry_in, bar_top, right=True)
    hole_pts = [(hole_x_r, bar_top), *hole_arc[1:-1], (hole_x_l, bar_top)]

    d_all = f"{_poly_d(body_pts)} {_poly_d(hole_pts)}"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 100">\n'
        f'  <path d="{d_all}" fill="{color}" fill-rule="evenodd"/>\n'
        "</svg>\n"
    )


def render_symbol_svg(name: str, color: str = DEFAULT_SYMBOL_COLOR) -> tuple[str, SymbolSpec]:
    """按符号名返回标准纯路径 SVG 字符串与规格元数据。"""
    sid = resolve_symbol_id(name)
    if sid is None:
        avail = ", ".join(SYMBOLS)
        raise ValueError(f"Unknown regulatory symbol '{name}' (available: {avail})")
    hx = color.strip()
    if not HEX_COLOR_RE.match(hx):
        raise ValueError(f"Symbol color must be #RRGGBB hex, got '{color}'")
    spec = SYMBOLS[sid]
    if sid == "pao":
        return _pao_svg(hx), spec
    if sid == "mobius":
        return _mobius_svg(hx), spec
    return _emark_svg(hx), spec


def add_symbol_asset(
    directory: Path,
    symbol: str,
    name: str | None = None,
    color: str = DEFAULT_SYMBOL_COLOR,
) -> Result:
    """将标准法规矢量符号写入 job 的 assets/<name>.svg 并生成元数据。"""
    r = Result(command="visual symbol", job=str(directory))
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    try:
        svg_text, spec = render_symbol_svg(symbol, color=color)
    except ValueError as e:
        return r.fail(
            Issue(
                id="ARG",
                message=str(e),
                fix=t("Available symbols: ") + ", ".join(SYMBOLS),
            ),
            2,
        )
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", (name or f"symbol-{spec.id}").strip()).strip("_")
    stem = stem or f"symbol-{spec.id}"
    dest = directory / "assets" / f"{stem}.svg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(svg_text, encoding="utf-8")
    meta = AssetMeta(
        name=stem,
        role="icon",
        source_type="file",
        source_path=f"builtin:symbol/{spec.id}",
        created=now_iso(),
        sha256=sha256_of(dest),
        size_px=(int(spec.viewbox[0]), int(spec.viewbox[1])),
        steps=[PrepStep(step="symbol", params={"symbol": spec.id, "color": color})],
    )
    meta_path = write_meta(dest, meta)
    r.artifacts = [str(dest), str(meta_path)]
    r.data = {
        "asset": str(dest.relative_to(directory)),
        "symbol": spec.id,
        "title": spec.title,
        "color": color,
        "aspect": round(spec.viewbox[0] / spec.viewbox[1], 3),
        "text_box_frac": list(spec.text_box_frac) if spec.text_box_frac else None,
    }
    return r


__all__ = [
    "DEFAULT_SYMBOL_COLOR",
    "SYMBOLS",
    "SymbolSpec",
    "add_symbol_asset",
    "list_symbols",
    "pao_text_box",
    "render_symbol_svg",
    "resolve_symbol_id",
]
