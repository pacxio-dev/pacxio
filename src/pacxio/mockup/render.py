"""合上的盒子的透视渲染：纹理贴图 + 简单明暗 + 地面阴影。纯软件，不依赖 3D 库。

盒子是凸多面体，可见的面互不遮挡，所以不需要深度排序，只需要背面剔除。
每个面用透视变换把纹理贴到屏幕四边形上；用 2 倍超采样再缩小做抗锯齿。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from pacxio.mockup.fold import BoxModel, Face, Vec3

SUPERSAMPLE = 2
MARGIN = 0.09  # 盒子外接框距画布边缘的比例
CAMERA_DISTANCE = 3.4  # 相机距离 = 该倍数 × 盒子对角线（越大透视越弱）
AMBIENT = 0.66
KEY_WEIGHT = 0.30
FILL_WEIGHT = 0.16
KEY_LIGHT: Vec3 = (-0.35, -0.80, 0.50)  # 主光：从左前上方来
FILL_LIGHT: Vec3 = (
    0.55,
    0.70,
    0.30,
)  # 补光：从右后方来，让背面和右侧面不至于发黑，签字确认时看得清
EDGE_ALPHA = 70
SHADOW_ALPHA = 85
SHADOW_BLUR = 0.025  # 阴影模糊半径占画布宽度的比例
SHADOW_SHIFT = 0.012
BG_TOP = (246, 246, 248)
BG_BOTTOM = (222, 224, 228)


@dataclass(frozen=True)
class View:
    name: str
    az: float  # 方位角（度）：0 = 正对正面，正值向右（看到右侧面），180 = 正对背面
    el: float  # 俯仰角（度）：正值从上往下看，负值从下往上看
    label: str


VIEWS = (
    View("front_right", 35.0, 24.0, "正面 + 右侧 + 顶面"),
    View("back_left", 215.0, 24.0, "背面 + 左侧 + 顶面"),
    View("bottom", 25.0, -38.0, "底面"),
)
OPEN_VIEW = View("open", 35.0, 32.0, "打开（多部件盒型：盖子拿开 / 抽屉抽出一半）")
# 扁平盒的背面视角是从底下看的（底、背、左）：出一张同角度的精确效果图和它并排，底面的字一样是正的
# （抽屉测试 F-23：以前和"底面"那张并排，那张从右前方往上看，角度不同、底面的字是倒的）
BELOW_BACK_VIEW = View("below_back_left", 215.0, -38.0, "底面 + 背面 + 左侧（从底下看）")


def _norm(v: Vec3) -> Vec3:
    n = math.sqrt(sum(c * c for c in v))
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: Vec3, b: Vec3) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


class Camera:
    def __init__(self, box: BoxModel, az_deg: float, el_deg: float, size: tuple[int, int]) -> None:
        self.size = size
        c: Vec3 = (box.x / 2, box.y / 2, box.z / 2)
        diag = math.sqrt(box.x**2 + box.y**2 + box.z**2)
        az, el = math.radians(az_deg), math.radians(el_deg)
        direction = (math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
        self.pos: Vec3 = tuple(c[i] + CAMERA_DISTANCE * diag * direction[i] for i in range(3))  # type: ignore[assignment]
        self.fwd = _norm(tuple(c[i] - self.pos[i] for i in range(3)))  # type: ignore[arg-type]
        self.right = _norm(_cross(self.fwd, (0.0, 0.0, 1.0)))
        self.up = _cross(self.right, self.fwd)
        # 先用单位焦距投影 8 个角，再缩放平移到画布里
        pts = [self._project_raw(p) for p in box.corners8()]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        w, h = size
        scale = min(
            w * (1 - 2 * MARGIN) / (max(xs) - min(xs)), h * (1 - 2 * MARGIN) / (max(ys) - min(ys))
        )
        self.scale = scale
        self.ox = w / 2 - scale * (max(xs) + min(xs)) / 2
        self.oy = h / 2 - scale * (max(ys) + min(ys)) / 2

    def _project_raw(self, p: Vec3) -> tuple[float, float]:
        d = (p[0] - self.pos[0], p[1] - self.pos[1], p[2] - self.pos[2])
        zc = _dot(d, self.fwd)
        return (_dot(d, self.right) / zc, -_dot(d, self.up) / zc)

    def project(self, p: Vec3) -> tuple[float, float]:
        x, y = self._project_raw(p)
        return (self.ox + self.scale * x, self.oy + self.scale * y)


def _homography(dest: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    """PIL 的 PERSPECTIVE 系数：输出像素 (x, y) → 输入像素 ((ax+by+c)/(gx+hy+1), (dx+ey+f)/(gx+hy+1))。"""
    a: list[list[float]] = []
    b: list[float] = []
    for (x, y), (u, v) in zip(dest, src, strict=True):
        a.append([x, y, 1, 0, 0, 0, -x * u, -y * u])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -x * v, -y * v])
        b.append(v)
    return [
        float(c)
        for c in np.linalg.solve(np.array(a, dtype=np.float64), np.array(b, dtype=np.float64))
    ]


def _background(size: tuple[int, int]) -> Image.Image:
    w, h = size
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    top, bot = np.array(BG_TOP, dtype=np.float32), np.array(BG_BOTTOM, dtype=np.float32)
    arr = np.repeat(top * (1 - t) + bot * t, w, axis=1)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def shade(normal: Vec3) -> float:
    key = max(0.0, _dot(normal, _norm(KEY_LIGHT)))
    fill = max(0.0, _dot(normal, _norm(FILL_LIGHT)))
    return min(1.0, AMBIENT + KEY_WEIGHT * key + FILL_WEIGHT * fill)


def camera_rays(cam: Camera, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """屏幕点 → 相机射线方向（`Camera._project_raw` 的逆：x = d·right / zc，y = −d·up / zc；fwd 分量为 1）。"""
    rx = (px - cam.ox) / cam.scale
    ry = (py - cam.oy) / cam.scale
    fwd, right, up = np.array(cam.fwd), np.array(cam.right), np.array(cam.up)
    return np.asarray(fwd + rx[..., None] * right + (-ry)[..., None] * up, dtype=np.float64)


def _window(d: np.ndarray, half: float, edge: np.ndarray | float) -> np.ndarray:
    """|d| ≤ half 为 1，往外在 edge 宽度内平滑降到 0（smoothstep）。"""
    t = np.clip(0.5 - (np.abs(d) - half) / edge, 0.0, 1.0)
    return np.asarray(t * t * (3 - 2 * t), dtype=np.float32)


def reflection(
    hit: np.ndarray,
    normal: np.ndarray,
    cam_pos: np.ndarray,
    rough: np.ndarray,
    cfg: dict[str, float],
    box: BoxModel,
) -> np.ndarray:
    """覆膜、光油、局部 UV 的镜面反光（静态示意，0–1，往白色混）：固定的影棚，不随视角换灯。

    影棚是左前方一块竖长的柔光灯板加浅色地面（render-materials.json 的 static_reflection，
    灯板的距离、宽、高、软边按盒子对角线的倍数给，大盒小盒看起来一样）。每个像素沿视线在纸面上的
    镜面方向射出去：打到灯板上就亮（灯板离得近，反光是纸面上一条有边的亮带，不是整面一样亮），
    往下看到地面就微微提亮。粗糙度（和工作台 3D 同一套 0–255）越低反光越强、亮带边越锐，纸和哑膜几乎没有。
    hit：(n, 3) 纸面上的点；rough：(n,) 这些点的粗糙度。"""
    view = cam_pos[None, :] - hit
    view /= np.linalg.norm(view, axis=1, keepdims=True)
    refl = 2 * (view @ normal)[:, None] * normal[None, :] - view
    diag = math.sqrt(box.x**2 + box.y**2 + box.z**2)
    az, el = math.radians(cfg["strip_az_deg"]), math.radians(cfg["strip_el_deg"])
    toward = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    centre = np.array([box.x / 2, box.y / 2, box.z / 2]) + cfg["strip_distance"] * diag * toward
    across = np.array([math.cos(az), math.sin(az), 0.0])  # 灯板的横向（水平）
    along = np.cross(toward, across)  # 灯板的竖向
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((centre[None, :] - hit) @ toward) / (refl @ toward)
    on = hit + np.nan_to_num(t)[:, None] * refl - centre[None, :]
    r = rough.astype(np.float32) / 255.0
    edge = (cfg["edge"] + cfg["edge_per_rough"] * r) * diag
    strip = (
        _window(on @ across, cfg["strip_width"] * diag / 2, edge)
        * _window(on @ along, cfg["strip_height"] * diag / 2, edge)
        * (t > 0)
    )
    down = np.degrees(np.arcsin(np.clip(-refl[:, 2], -1.0, 1.0)))
    floor = np.clip(down / cfg["floor_fade_deg"], 0.0, 1.0)
    # 粗糙度 ≥ rough_none 不反光，≤ rough_full 反光最强
    gloss = np.clip(
        (cfg["rough_none"] - rough.astype(np.float32)) / (cfg["rough_none"] - cfg["rough_full"]),
        0.0,
        1.0,
    )
    amount = gloss * (cfg["strip_strength"] * strip + cfg["floor_strength"] * floor)
    return np.asarray(np.clip(amount, 0.0, 1.0), dtype=np.float32)


def add_reflection(color: np.ndarray, amount: np.ndarray) -> np.ndarray:
    """颜色 (n, 3) 按反光量往白色混。"""
    return np.asarray(color + (255.0 - color) * amount[:, None], dtype=np.float32)


def visible(face: Face, cam: Camera, box: BoxModel) -> bool:
    centre = tuple(sum(c[i] for c in face.corners) / 4 for i in range(3))
    to_cam = tuple(cam.pos[i] - centre[i] for i in range(3))
    return _dot(face.normal, to_cam) > 0  # type: ignore[arg-type]


def render_view(
    box: BoxModel,
    textures: dict[str, Image.Image],
    view: View,
    size: tuple[int, int] = (1200, 1200),
    depth_sort: bool = False,
    outline_edges: bool = True,
    rough: dict[str, Image.Image] | None = None,
    reflect: dict[str, float] | None = None,
) -> Image.Image:
    """渲染一个视角。textures：面 key → 该面的纹理（RGB）。

    outline_edges：默认画矩形边；异形纸面按透明裁切呈现时可关闭，避免画出包围框。
    depth_sort：按面中心离相机的远近从远到近画（多部件盒型"打开"的那张：敞口的托盘不是凸多面体）。
    rough / reflect：面 key → 粗糙度纹理（和 textures 同样裁切）与影棚参数；给了才画覆膜、UV 的反光，
    没有粗糙度纹理的面（纸背、内面）不反光。
    """
    ss = SUPERSAMPLE
    big = (size[0] * ss, size[1] * ss)
    cam = Camera(box, view.az, view.el, big)
    canvas = _background(big)

    # 地面阴影：底面投影后向右下略偏移并模糊
    floor = [cam.project((x, y, 0.0)) for x, y in ((0, 0), (box.x, 0), (box.x, box.y), (0, box.y))]
    shift = SHADOW_SHIFT * big[0]
    shadow = Image.new("L", big, 0)
    ImageDraw.Draw(shadow).polygon(
        [(x + shift, y + shift * 0.6) for x, y in floor], fill=SHADOW_ALPHA
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR * big[0]))
    canvas.paste((40, 40, 48), mask=shadow)

    faces = box.faces
    if depth_sort:

        def dist(f: Face) -> float:
            c = tuple(sum(p[i] for p in f.corners) / 4 for i in range(3))
            return sum((cam.pos[i] - c[i]) ** 2 for i in range(3))

        faces = sorted(faces, key=dist, reverse=True)
    for face in faces:
        if not visible(face, cam, box):
            continue
        raw = textures[face.key]
        tex = raw.convert("RGB")
        quad = [cam.project(c) for c in face.corners]  # 左上、右上、右下、左下
        tw, th = tex.size
        src = [(0.0, 0.0), (float(tw), 0.0), (float(tw), float(th)), (0.0, float(th))]
        coeffs = _homography(quad, src)
        warped = tex.transform(big, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)
        k = shade(face.normal)
        lit = np.asarray(warped, dtype=np.float32) * k
        mask = Image.new("L", big, 0)
        ImageDraw.Draw(mask).polygon(quad, fill=255)
        if rough is not None and reflect is not None and face.key in rough:
            box_px = mask.getbbox()
            if box_px is not None:
                x0, y0, x1, y1 = box_px
                rmap = rough[face.key].convert("L")
                rw, rh = rmap.size
                rc = _homography(quad, [(0.0, 0.0), (rw, 0.0), (rw, rh), (0.0, rh)])
                r = np.asarray(
                    rmap.transform(big, Image.Transform.PERSPECTIVE, rc, Image.Resampling.BILINEAR)
                )[y0:y1, x0:x1]
                inside = np.asarray(mask)[y0:y1, x0:x1] > 0
                px, py = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
                ray = camera_rays(cam, px[inside], py[inside])
                normal = np.array(face.normal, dtype=np.float64)
                pos = np.array(cam.pos, dtype=np.float64)
                t = ((np.array(face.corners[0]) - pos) @ normal) / (ray @ normal)
                amount = reflection(pos + t[:, None] * ray, normal, pos, r[inside], reflect, box)
                patch = lit[y0:y1, x0:x1]
                patch[inside] = add_reflection(patch[inside], amount)
        arr = np.clip(lit, 0, 255).astype(np.uint8)
        if raw.mode == "RGBA":  # 纹理带透明（刀版外轮廓以外，如手指缺口）：透出后面已经画好的东西
            alpha = raw.split()[3].transform(
                big, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BILINEAR
            )
            mask = ImageChops.multiply(mask, alpha)
        canvas.paste(Image.fromarray(arr, "RGB"), mask=mask)
        if outline_edges:
            edge = Image.new("RGBA", big, (0, 0, 0, 0))
            ImageDraw.Draw(edge).polygon(quad, outline=(0, 0, 0, EDGE_ALPHA), width=max(1, ss))
            canvas.paste(edge, mask=edge.split()[3])
    return canvas.resize(size, Image.Resampling.LANCZOS)
