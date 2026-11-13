"""标签贴在瓶 / 罐上的 3D（ 节）：容器只是展示载体。

- 容器按参数生成旋转体（圆柱瓶、广口罐、锥形杯 / 罐）或方瓶，切成很多小平面（`BoxModel.faces`），交给
  `raster.render_folded`（深度缓冲）出图，和盒子的静态效果图同一套相机、灯光。
- 标签按展开几何精确贴：圆柱、锥台、平面都是可展曲面，平面图稿贴上去不拉伸。每一小片标签的贴图是反算出来的：
  小平面上的每个像素 → 三维点 → 回到标签展开图上的位置 → 取成品外观图的颜色（标签外是透明，露出瓶子）。
- 没给的容器尺寸按标签参数推一个（`resolve`），job 里 `container` 给了的项照用。
- 透明玻璃 / PET 第一版画成带色的不透明瓶身（不做折射），在汇总页上写明。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from pacxio.export.canvas import _Map
from pacxio.messages import display_name, t
from pacxio.mockup.fold import BoxModel, Face
from pacxio.model.dieline import Dieline
from pacxio.model.job import Container

SEGMENTS = 96  # 旋转体一圈切多少片（静态图）
SCENE_SEGMENTS = 36  # 工作台 3D 用的粗网格
LABEL_GAP_MM = 0.3  # 标签离瓶面（防闪烁；实物是背胶 + 面材厚）
OVERLAP_GAP_MM = 0.3  # 环标搭接处上面那层再往外
TEX_PX_PER_MM = 6.0
TEX_MAX = 1400
FINISH_RGB: dict[str, tuple[int, int, int]] = {
    "glass_clear": (205, 222, 216),
    "glass_amber": (128, 70, 28),
    "glass_frosted": (226, 230, 230),
    "pet_clear": (212, 224, 232),
    "hdpe_white": (240, 239, 234),
    "plastic_black": (44, 44, 48),
    "tin": (186, 189, 194),
}
FINISH_ZH = {"glass_clear": "透明玻璃", "glass_amber": "茶色玻璃", "glass_frosted": "磨砂玻璃",
             "pet_clear": "透明 PET", "hdpe_white": "白色 HDPE", "plastic_black": "黑色塑料",
             "tin": "马口铁"}  # fmt: skip
CAP_RGB = {"tin": (176, 178, 184), "hdpe_white": (236, 236, 232)}
DEFAULT_CAP_RGB = (36, 36, 40)

Vec3 = tuple[float, float, float]


@dataclass
class Resolved:
    """容器的全部尺寸（mm）和标签贴的位置。z = 0 在瓶底，瓶轴在 (r_max, r_max)。"""

    shape: str
    diameter: float  # 瓶身（锥形：瓶底）直径
    top_diameter: float  # 锥形：瓶身顶的直径；其余 = diameter
    height: float  # 瓶身（可贴标段 + 底部圆角）高
    shoulder: float
    neck_d: float
    neck_h: float
    cap_d: float
    cap_h: float
    bottom_r: float
    finish: str
    label_z: float
    angle: float  # 度
    on: str
    defaults: list[str] = field(default_factory=list)  # 推出来（不是用户给）的项

    @property
    def r_max(self) -> float:
        return max(self.diameter, self.top_diameter, self.cap_d) / 2

    @property
    def total_h(self) -> float:
        return self.height + self.shoulder + self.neck_h + self.cap_h

    def radius_at(self, z: float) -> float:
        """瓶身（不含肩）在高度 z 处的半径。"""
        t = 0.0 if self.height <= 0 else min(1.0, max(0.0, z / self.height))
        return (self.diameter + (self.top_diameter - self.diameter) * t) / 2

    def describe(self) -> str:
        zh = {"cylinder": "圆柱瓶", "jar": "广口罐", "taper": "锥形杯 / 罐", "square": "方瓶"}[
            self.shape
        ]
        size = (f"Ø{self.diameter:g}→{self.top_diameter:g}" if self.shape == "taper"
                else f"Ø{self.diameter:g}") + f" × {self.total_h:.0f} mm"  # fmt: skip
        return t("{shape} {size}, {finish}", shape=display_name(zh), size=size,
                 finish=display_name(FINISH_ZH.get(self.finish, self.finish)))


def label_info(dl: Dieline) -> dict[str, Any]:
    info: dict[str, Any] = dl.style_meta.get("label") or {}
    if not info:
        raise ValueError(f"{dl.style} 不是标签")
    return info


def resolve(c: Container | None, dl: Dieline) -> Resolved:
    """容器尺寸：job 给了的照用，没给的按标签推一个（推的项记在 defaults 里，汇总页写明）。"""
    c = c or Container()
    info = label_info(dl)
    kind = info["kind"]
    given = c.model_dump(exclude_none=True)
    w, h = float(info.get("width", 0)), float(info.get("height", 0))
    on = c.on or "body"
    if kind == "cone":
        r1, r2, lh = info["r_bottom"], info["r_top"], info["height"]
        gap = max(6.0, 0.12 * lh)
        slope = (r2 - r1) / lh
        base = {"shape": "taper", "diameter": 2 * (r1 - gap * slope), "top_diameter": 2 * (r2 + gap * slope),
                "height": lh + 2 * gap, "shoulder": 0.0, "neck_d": 2 * (r2 + gap * slope), "neck_h": 0.0,
                "cap_d": 2 * (r2 + gap * slope), "cap_h": 0.0, "bottom_r": 0.0, "label_z": gap}  # fmt: skip
    else:
        if kind == "wrap":
            d = float(info["diameter"])
        elif on == "lid":
            d = (w + 8) / 0.92
        else:
            d = max(w / (0.3 * math.pi), 40.0)
        lh = h if on == "body" else 0.7 * d
        gap = max(8.0, 0.15 * lh)
        tall = (lh + 2 * gap) >= 1.1 * d and on == "body"
        base = {"shape": "cylinder" if tall else "jar", "diameter": d, "height": lh + 2 * gap,
                "shoulder": 0.35 * d if tall else 0.05 * d, "neck_d": 0.36 * d if tall else 0.86 * d,
                "neck_h": 0.22 * d if tall else 0.05 * d, "cap_d": 0.42 * d if tall else 0.92 * d,
                "cap_h": 0.3 * d if tall else 0.26 * d, "bottom_r": min(6.0, 0.08 * d), "label_z": gap}  # fmt: skip
        if on == "lid":
            base["cap_d"] = w + 8
        base["top_diameter"] = base["diameter"]
    shape = given.get("shape", base["shape"])
    if (
        "shape" in given
        and shape != base["shape"]
        and shape in ("cylinder", "jar")
        and kind != "cone"
    ):
        d = given.get("diameter", base["diameter"])
        tall = shape == "cylinder"
        base.update({"shoulder": 0.35 * d if tall else 0.05 * d, "neck_d": 0.36 * d if tall else 0.86 * d,
                     "neck_h": 0.22 * d if tall else 0.05 * d, "cap_d": 0.42 * d if tall else 0.92 * d,
                     "cap_h": 0.3 * d if tall else 0.26 * d})  # fmt: skip
    if shape == "square":
        base.update(
            {"shoulder": 0.25 * base["diameter"], "bottom_r": min(4.0, 0.06 * base["diameter"])}
        )
    vals = {**base, **{k: v for k, v in given.items() if k in base or k == "top_diameter"}}
    if shape != "taper" and "top_diameter" not in given:
        vals["top_diameter"] = vals["diameter"]
    defaults = [k for k in ("diameter", "height", "label_z") if k not in given]
    return Resolved(
        shape=shape, diameter=vals["diameter"], top_diameter=vals["top_diameter"], height=vals["height"],
        shoulder=vals["shoulder"], neck_d=vals["neck_d"], neck_h=vals["neck_h"], cap_d=vals["cap_d"],
        cap_h=vals["cap_h"], bottom_r=vals["bottom_r"], finish=c.finish or "glass_clear",
        label_z=vals["label_z"], angle=c.angle or 0.0, on=on, defaults=defaults,
    )  # fmt: skip


# ---------------------------------------------------------------------------
# 网格
# ---------------------------------------------------------------------------


def _solid(rgb: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGBA", (1, 1), (*rgb, 255))


def _disc(r_in: float, r_out: float, rgb: tuple[int, int, int], px: int = 384) -> Image.Image:
    """水平圆盘 / 圆环贴在一个正方形面上：圆环外透明。"""
    yy, xx = np.mgrid[0:px, 0:px]
    rr = np.hypot((xx + 0.5) / px * 2 - 1, (yy + 0.5) / px * 2 - 1) * r_out
    alpha = ((rr <= r_out) & (rr >= r_in)).astype(np.uint8) * 255
    arr = np.zeros((px, px, 4), dtype=np.uint8)
    arr[..., :3] = rgb
    arr[..., 3] = alpha
    return Image.fromarray(arr, "RGBA")


def _bounding_rect(quad: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """平面四边形（左上、右上、右下、左下）在它的平面里的包围矩形：(左上角, 向右边, 向下边)。"""
    top_e, bot_e = quad[1] - quad[0], quad[2] - quad[3]
    e1 = top_e if np.linalg.norm(top_e) >= np.linalg.norm(bot_e) else bot_e
    e1 = e1 / np.linalg.norm(e1)
    side = quad[3] - quad[0]
    e2 = side - (side @ e1) * e1
    e2 = e2 / np.linalg.norm(e2)
    rel = quad - quad[0]
    su, sv = rel @ e1, rel @ e2
    origin = quad[0] + su.min() * e1 + sv.min() * e2
    return origin, (su.max() - su.min()) * e1, (sv.max() - sv.min()) * e2


def _corners(origin: np.ndarray, du: np.ndarray, dv: np.ndarray) -> tuple[Vec3, Vec3, Vec3, Vec3]:
    pts = (origin, origin + du, origin + du + dv, origin + dv)
    return tuple((float(p[0]), float(p[1]), float(p[2])) for p in pts)  # type: ignore[return-value]


def _trapezoid_tex(quad: np.ndarray, origin: np.ndarray, du: np.ndarray, dv: np.ndarray,
                   rgb: tuple[int, int, int], px: tuple[int, int] = (24, 6)) -> Image.Image:  # fmt: skip
    """纯色、四边形之外透明的贴图（瓶身斜面一圈的小梯形）。"""
    tw, th = px
    ss, vv = np.meshgrid((np.arange(tw) + 0.5) / tw, (np.arange(th) + 0.5) / th)
    e1 = du / np.linalg.norm(du)
    e2 = dv / np.linalg.norm(dv)
    q = np.stack([(quad - origin) @ e1, (quad - origin) @ e2], axis=1)
    x, y = ss * np.linalg.norm(du), vv * np.linalg.norm(dv)
    inside = np.ones_like(x, dtype=bool)
    # 顶点顺序左上、右上、右下、左下；在 (e1 向右, e2 向下) 坐标里内部在每条边叉积为负的一侧
    pad = 0.6 * max(np.linalg.norm(du) / tw, np.linalg.norm(dv) / th)
    for i in range(4):
        a, b_ = q[i], q[(i + 1) % 4]
        ex, ey = b_ - a
        n = math.hypot(ex, ey)
        if n < 1e-12:
            continue
        inside &= ((x - a[0]) * ey - (y - a[1]) * ex) / n <= pad
    arr = np.zeros((th, tw, 4), dtype=np.uint8)
    arr[..., :3] = rgb
    arr[..., 3] = inside.astype(np.uint8) * 255
    return Image.fromarray(arr, "RGBA")


class _Builder:
    def __init__(self, centre: tuple[float, float]) -> None:
        self.cx, self.cy = centre
        self.faces: list[Face] = []
        self.tex: dict[str, Image.Image] = {}
        self.groups: list[int] = []

    def add(self, corners: tuple[Vec3, Vec3, Vec3, Vec3], normal: Vec3, tex: Image.Image, group: int,
            label: str = "瓶身") -> None:  # fmt: skip
        key = f"c{len(self.faces):04d}"
        self.faces.append(Face(key, "container", label, normal, (0, 0, 0, 0), corners))
        self.tex[key] = tex
        self.groups.append(group)

    def ring(
        self, r: float, z: float, rgb: tuple[int, int, int], group: int, up: bool, r_in: float = 0.0
    ) -> None:
        """水平圆盘（up：法向朝上）。"""
        cx, cy = self.cx, self.cy
        if up:
            corners = (
                (cx - r, cy + r, z),
                (cx + r, cy + r, z),
                (cx + r, cy - r, z),
                (cx - r, cy - r, z),
            )
            normal: Vec3 = (0.0, 0.0, 1.0)
        else:
            corners = (
                (cx - r, cy - r, z),
                (cx + r, cy - r, z),
                (cx + r, cy + r, z),
                (cx - r, cy + r, z),
            )
            normal = (0.0, 0.0, -1.0)
        self.add(corners, normal, _disc(r_in, r, rgb), group)

    def revolve(
        self, profile: list[tuple[float, float]], rgb: tuple[int, int, int], group: int, n: int
    ) -> None:
        """把 (半径, 高) 折线绕瓶轴转一圈；每一段切成 n 个小平面。"""
        tex = _solid(rgb)
        cx, cy = self.cx, self.cy
        for (r0, z0), (r1, z1) in zip(profile, profile[1:], strict=False):
            if r0 < 1e-6 and r1 < 1e-6 or (abs(r0 - r1) < 1e-9 and abs(z0 - z1) < 1e-9):
                continue
            if abs(z0 - z1) < 1e-9:  # 水平环：一张带透明的正方形
                self.ring(max(r0, r1), z0, rgb, group, up=r1 < r0, r_in=min(r0, r1))
                continue
            for i in range(n):
                a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n

                def p(r: float, z: float, a: float) -> Vec3:
                    return (cx + r * math.sin(a), cy - r * math.cos(a), z)

                top_l, top_r = p(r1, z1, a0), p(r1, z1, a1)
                bot_r, bot_l = p(r0, z0, a1), p(r0, z0, a0)
                quad = np.array([top_l, top_r, bot_r, bot_l], dtype=np.float64)
                am = (a0 + a1) / 2
                dz, dr = z1 - z0, r1 - r0
                ln = math.hypot(dz, dr)
                nx, nz = dz / ln, -dr / ln  # 母线的外法向（半径方向、竖直方向的分量）
                normal = (nx * math.sin(am), -nx * math.cos(am), nz)
                if abs(r1 - r0) < 1e-9:  # 竖直圆柱段：本来就是矩形
                    self.add((top_l, top_r, bot_r, bot_l), normal, tex, group)
                    continue
                origin, du, dv = _bounding_rect(quad)  # 斜面（梯形）：包住它的矩形 + 透明遮罩
                self.add(
                    _corners(origin, du, dv),
                    normal,
                    _trapezoid_tex(quad, origin, du, dv, rgb),
                    group,
                )


def _body(rc: Resolved, b: _Builder, n: int) -> None:
    rgb = FINISH_RGB.get(rc.finish, FINISH_RGB["glass_clear"])
    cap_rgb = CAP_RGB.get(rc.finish, DEFAULT_CAP_RGB)
    R, Rt, H, br = rc.diameter / 2, rc.top_diameter / 2, rc.height, rc.bottom_r
    prof: list[tuple[float, float]] = [(R - br, 0.0)]
    for k in range(1, 5):  # 瓶底圆角四段
        a = math.pi / 2 * k / 4
        prof.append((R - br + br * math.sin(a), br - br * math.cos(a)))
    prof.append((Rt, H))
    z = H
    if rc.shoulder > 0:
        rn = rc.neck_d / 2
        for k in range(1, 7):
            t = k / 6
            prof.append(
                (
                    rn + (Rt - rn) * math.cos(t * math.pi / 2),
                    z + rc.shoulder * math.sin(t * math.pi / 2),
                )
            )
        z += rc.shoulder
    if rc.neck_h > 0:
        prof.append((rc.neck_d / 2, z + rc.neck_h))
        z += rc.neck_h
    b.ring(R - br, 0.0, rgb, 0, up=False)
    if rc.shape == "square":
        _square_walls(rc, b, rgb)
        prof = [(rc.neck_d / 2, H + rc.shoulder), (rc.neck_d / 2, z)]
        h = R  # 方瓶肩：平顶（一张正方形）
        b.add(((b.cx - h, b.cy + h, H), (b.cx + h, b.cy + h, H), (b.cx + h, b.cy - h, H),
               (b.cx - h, b.cy - h, H)), (0.0, 0.0, 1.0), _solid(rgb), 0)  # fmt: skip
    b.revolve(prof, rgb, 0, n)
    if rc.cap_h > 0:
        rc_ = rc.cap_d / 2
        b.revolve([(rc.neck_d / 2, z), (rc_, z), (rc_, z + rc.cap_h)], cap_rgb, 1, n)
        b.ring(rc_, z + rc.cap_h, cap_rgb, 1, up=True)
    else:
        b.ring(prof[-1][0], z, rgb, 0, up=True)


def _square_walls(rc: Resolved, b: _Builder, rgb: tuple[int, int, int]) -> None:
    h = rc.diameter / 2
    cx, cy, H = b.cx, b.cy, rc.height
    tex = _solid(rgb)
    for nx, ny in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        # 从外面看：左上、右上、右下、左下
        tx, ty = -ny, nx  # 面上向右的方向
        ox, oy = cx + nx * h, cy + ny * h
        tl = (ox - tx * h, oy - ty * h, H)
        tr = (ox + tx * h, oy + ty * h, H)
        br_ = (ox + tx * h, oy + ty * h, 0.0)
        bl = (ox - tx * h, oy - ty * h, 0.0)
        b.add((tl, tr, br_, bl), (float(nx), float(ny), 0.0), tex, 0)


# ---------------------------------------------------------------------------
# 标签
# ---------------------------------------------------------------------------


def _sample(
    art: np.ndarray, m: _Map, xs: np.ndarray, ys: np.ndarray, inside: np.ndarray
) -> Image.Image:
    """按标签展开图上的位置 (mm) 取成品外观图的颜色；inside 之外透明。"""
    h, w = art.shape[:2]
    px = np.clip(((xs - m.x0) * m.s).astype(np.int64), 0, w - 1)
    py = np.clip(((m.y1 - ys) * m.s).astype(np.int64), 0, h - 1)
    out = art[py, px].copy()
    out[..., 3] = np.where(inside, out[..., 3], 0)
    return Image.fromarray(out, "RGBA")


def _strip_tex(width_mm: float, height_mm: float) -> tuple[int, int]:
    tw = int(min(TEX_MAX, max(2, math.ceil(width_mm * TEX_PX_PER_MM))))
    th = int(min(TEX_MAX, max(2, math.ceil(height_mm * TEX_PX_PER_MM))))
    return tw, th


def _cyl_label(
    rc: Resolved, info: dict[str, Any], art: np.ndarray, m: _Map, b: _Builder, n: int
) -> None:
    """环标 / 前背标贴在圆柱（或锥度很小的瓶身）上：沿标签宽切成竖条。"""
    width, height = float(info["width"]), float(info["height"])
    R = rc.diameter / 2
    circ = math.pi * rc.diameter
    overlap = float(info.get("overlap") or 0.0)
    # 标签中线（环标：露在外面那段的中点）对着 angle
    centre_x = overlap + circ / 2 if info["kind"] == "wrap" else width / 2
    psi0 = math.radians(rc.angle)
    strips = max(4, math.ceil(n * width / circ))
    z0 = rc.label_z
    for i in range(strips):
        xa, xb = width * i / strips, width * (i + 1) / strips
        top_layer = info["kind"] == "wrap" and xa >= circ - 1e-6
        r = R + LABEL_GAP_MM + (OVERLAP_GAP_MM if top_layer else 0.0)

        def p(x: float, z: float, r: float = r) -> Vec3:
            a = psi0 + (x - centre_x) / R
            return (b.cx + r * math.sin(a), b.cy - r * math.cos(a), z)

        tl, tr, br_, bl = p(xa, z0 + height), p(xb, z0 + height), p(xb, z0), p(xa, z0)
        am = psi0 + ((xa + xb) / 2 - centre_x) / R
        tw, th = _strip_tex(xb - xa, height)
        # 竖条是弦平面：贴图的列按弦上的位置反算回弧长（误差很小，仍按真实角度算）
        s = (np.arange(tw) + 0.5) / tw
        v = (np.arange(th) + 0.5) / th
        ss, vv = np.meshgrid(s, v)
        da, db = (xa - centre_x) / R, (xb - centre_x) / R
        # 弦上一点：(1−s)·A + s·B，对应的圆周角
        ax_, ay_ = math.sin(da), -math.cos(da)
        bx_, by_ = math.sin(db), -math.cos(db)
        qx, qy = (1 - ss) * ax_ + ss * bx_, (1 - ss) * ay_ + ss * by_
        ang = np.arctan2(qx, -qy)
        xs = centre_x + ang * R
        ys = height * (1 - vv)
        inside = (xs >= xa - 0.05) & (xs <= xb + 0.05)
        group = 3 if top_layer else 2  # 一张标签一组（搭接盖上去那段另一组：看得到接缝）
        b.add((tl, tr, br_, bl), (math.sin(am), -math.cos(am), 0.0), _sample(art, m, xs, ys, inside), group,
              label="标签")  # fmt: skip


def _flat_label(rc: Resolved, info: dict[str, Any], art: np.ndarray, m: _Map, b: _Builder) -> None:
    """方瓶正面、盖顶：标签是平的。"""
    width, height = float(info["width"]), float(info["height"])
    tw, th = _strip_tex(width, height)
    ss, vv = np.meshgrid((np.arange(tw) + 0.5) / tw, (np.arange(th) + 0.5) / th)
    tex = _sample(art, m, ss * width, height * (1 - vv), np.ones_like(ss, dtype=bool))
    if rc.on == "lid":
        z = rc.total_h + LABEL_GAP_MM
        x0, y0 = b.cx - width / 2, b.cy - height / 2  # 标签上沿朝瓶的后面
        corners = (
            (x0, y0 + height, z),
            (x0 + width, y0 + height, z),
            (x0 + width, y0, z),
            (x0, y0, z),
        )
        b.add(corners, (0.0, 0.0, 1.0), tex, 2, label="标签")
        return
    y = b.cy - rc.diameter / 2 - LABEL_GAP_MM
    x0, z0 = b.cx - width / 2, rc.label_z
    corners = ((x0, y, z0 + height), (x0 + width, y, z0 + height), (x0 + width, y, z0), (x0, y, z0))
    b.add(corners, (0.0, -1.0, 0.0), tex, 2, label="标签")


def _cone_label(
    rc: Resolved, info: dict[str, Any], art: np.ndarray, m: _Map, b: _Builder, n: int
) -> None:
    """锥形扇形标：沿展开角切成条，每条是两条母线夹的平面梯形；贴图按展开扇形反算。"""
    cxf, cyf = info["centre"]
    k = info["k"]
    a_left, a_right = info["a_left"], info["a_right"]
    phi = abs(a_left - a_right)
    sgn = 1.0 if info["apex"] == "below" else -1.0  # 展开图里从左往右，角度是减小（below）还是增大
    bot_rho, top_rho = info["bottom_rho"], info["top_rho"]
    r1, r2, lh = info["r_bottom"], info["r_top"], info["height"]
    full = info["full"]
    overlap = info.get("overlap") or 0.0
    z0 = rc.label_z
    psi0 = math.radians(rc.angle)
    # 露在外面那段的中点对着 angle：整圈时是从左端往右 (overlap 角 + 整圈的一半)
    mid_flat = (
        (overlap / ((info["rho_in"] + info["rho_out"]) / 2) + full / 2) if info["wrap"] else phi / 2
    )
    strips = max(4, math.ceil(n * phi / (2 * math.pi * k)))

    def to3d(t_flat: float, rho: float, lift: float) -> Vec3:
        """t_flat：从左端往右的展开角；rho：离展开圆心的距离。"""
        psi = psi0 + (t_flat - mid_flat) / k
        frac = (rho - bot_rho) / (top_rho - bot_rho)  # 0 = 下沿，1 = 上沿
        z = z0 + frac * lh
        r = r1 + frac * (r2 - r1) + lift
        return (b.cx + r * math.sin(psi), b.cy - r * math.cos(psi), z)

    for i in range(strips):
        ta, tb = phi * i / strips, phi * (i + 1) / strips
        top_layer = info["wrap"] and ta >= full - 1e-9
        lift = LABEL_GAP_MM + (OVERLAP_GAP_MM if top_layer else 0.0)
        tl, tr = to3d(ta, top_rho, lift), to3d(tb, top_rho, lift)
        bl = to3d(ta, bot_rho, lift)
        br_ = to3d(tb, bot_rho, lift)
        # 两条母线夹的梯形在一个平面里：取平面内包住它的矩形当这一片（梯形之外透明）
        origin, du, dv = _bounding_rect(np.array([tl, tr, br_, bl], dtype=np.float64))
        corners = _corners(origin, du, dv)
        tw, th = _strip_tex(float(np.linalg.norm(du)), float(np.linalg.norm(dv)))
        ss, vv = np.meshgrid((np.arange(tw) + 0.5) / tw, (np.arange(th) + 0.5) / th)
        pts = origin[None, None, :] + ss[..., None] * du + vv[..., None] * dv
        rel_x, rel_y = pts[..., 0] - b.cx, pts[..., 1] - b.cy
        psi = np.arctan2(rel_x, -rel_y)
        t_flat = mid_flat + np.angle(np.exp(1j * (psi - psi0))) * k
        frac = (pts[..., 2] - z0) / lh
        rho = bot_rho + frac * (top_rho - bot_rho)
        a = a_left - sgn * t_flat
        xs = cxf + rho * np.cos(a)
        ys = cyf + rho * np.sin(a)
        pad = 0.05 * (tb - ta)  # 相邻两条略微重叠（同一张标签），不漏缝
        inside = (t_flat >= ta - pad) & (t_flat <= tb + pad) & (frac >= -1e-3) & (frac <= 1 + 1e-3)
        tm = (ta + tb) / 2
        psi_m = psi0 + (tm - mid_flat) / k
        nr, nz = lh, -(r2 - r1)  # 母线外法向
        ln = math.hypot(nr, nz)
        normal = (nr / ln * math.sin(psi_m), -nr / ln * math.cos(psi_m), nz / ln)
        b.add(
            corners,
            normal,
            _sample(art, m, xs, ys, inside),
            3 if top_layer else 2,
            label="标签",
        )


def container_scene(
    dl: Dieline,
    art: Image.Image,
    mapping: _Map,
    container: Container | None,
    segments: int = SEGMENTS,
) -> tuple[BoxModel, dict[str, Image.Image]]:
    """瓶 / 罐 + 贴好的标签：BoxModel（很多小平面）和每片的贴图。"""
    rc = resolve(container, dl)
    info = label_info(dl)
    r = rc.r_max + 1
    b = _Builder((r, r))
    _body(rc, b, segments)
    arr = np.asarray(art.convert("RGBA"))
    if info["kind"] == "cone":
        _cone_label(rc, info, arr, mapping, b, segments)
    elif rc.on == "lid" or rc.shape == "square":
        _flat_label(rc, info, arr, mapping, b)
    else:
        _cyl_label(rc, info, arr, mapping, b, segments)
    box = BoxModel(x=2 * r, y=2 * r, z=rc.total_h + 1, faces=b.faces)
    box.edge_groups = b.groups
    return box, b.tex


__all__ = ["FINISH_ZH", "Resolved", "container_scene", "label_info", "resolve"]
