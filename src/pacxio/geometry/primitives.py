"""几何基元：向量、仿射变换、路径。

单位统一为毫米(mm)。坐标系：X 向右，Y 向上（与 DXF/数学惯例一致，
与 SVG 的 Y 向下相反 —— SVG 导出时在 exporter 里翻转）。

圆弧用 DXF 的 bulge 约定表达，这是刀模图行业的通用表示：
bulge = tan(θ/4)，θ 为圆弧包含角。正值逆时针，负值顺时针，
0 表示直线段。bulge 是刀模厂割样机唯一通吃的圆弧表达方式，
所以内核里就存 bulge，而不是圆心+半径+起止角（后者在导出时
还要转回 bulge，且会引入浮点歧义）。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# 浮点比较容差。几何量单位是 mm，1e-9 已是纳米级，远超印刷精度。
EPS = 1e-9
# 判定两点重合的距离阈值。0.001mm = 1微米，小于任何实际刀模精度。
TOL = 1e-3


@dataclass(frozen=True)
class Vec:
    """二维点/向量。"""

    x: float
    y: float

    def __add__(self, o: Vec) -> Vec:
        return Vec(self.x + o.x, self.y + o.y)

    def __sub__(self, o: Vec) -> Vec:
        return Vec(self.x - o.x, self.y - o.y)

    def __mul__(self, k: float) -> Vec:
        return Vec(self.x * k, self.y * k)

    __rmul__ = __mul__

    def __truediv__(self, k: float) -> Vec:
        return Vec(self.x / k, self.y / k)

    def __neg__(self) -> Vec:
        return Vec(-self.x, -self.y)

    def dot(self, o: Vec) -> float:
        return self.x * o.x + self.y * o.y

    def cross(self, o: Vec) -> float:
        """二维叉积（标量），正负号表示转向。"""
        return self.x * o.y - self.y * o.x

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def length_sq(self) -> float:
        return self.x * self.x + self.y * self.y

    def normalized(self) -> Vec:
        n = self.length()
        if n < EPS:
            raise ValueError("无法归一化零向量")
        return Vec(self.x / n, self.y / n)

    def perpendicular(self) -> Vec:
        """逆时针 90°。"""
        return Vec(-self.y, self.x)

    def rotate(self, angle_rad: float) -> Vec:
        c, s = math.cos(angle_rad), math.sin(angle_rad)
        return Vec(self.x * c - self.y * s, self.x * s + self.y * c)

    def angle(self) -> float:
        return math.atan2(self.y, self.x)

    def dist(self, o: Vec) -> float:
        return (self - o).length()

    def is_close(self, o: Vec, tol: float = TOL) -> bool:
        return self.dist(o) <= tol

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def round(self, nd: int = 4) -> Vec:
        """取整到 nd 位小数。用于输出前消除浮点毛刺，
        避免 DXF 里出现 89.99999999999999 这种值。"""
        return Vec(round(self.x, nd), round(self.y, nd))


def vec(x: float, y: float) -> Vec:
    return Vec(float(x), float(y))


@dataclass(frozen=True)
class Segment:
    """路径的一段：直线或圆弧。

    bulge=0 时为直线段（end 为终点）；否则为从 start 到 end 的圆弧，
    bulge = tan(包含角/4)，正值逆时针。
    """

    start: Vec
    end: Vec
    bulge: float = 0.0

    @property
    def is_line(self) -> bool:
        return abs(self.bulge) < EPS

    @property
    def is_arc(self) -> bool:
        return not self.is_line

    def reversed(self) -> Segment:
        """反向：起止点互换，bulge 取负（圆弧方向翻转）。"""
        return Segment(self.end, self.start, -self.bulge)

    def length(self) -> float:
        if self.is_line:
            return self.start.dist(self.end)
        return self.arc_length()

    # ---- 圆弧参数 ----

    def arc_info(self) -> tuple[Vec, float, float, float, bool]:
        """返回 (圆心, 半径, 起始角, 包含角(正值), 是否逆时针)。

        bulge -> 弧的推导：弦长 c，bulge b，包含角 θ = 4·atan(b)，
        半径 R = c·(1+b²)/(4|b|)。

        圆心用半径方向反推，而非"弦中点 + 垂距" —— 后者在优弧
        (θ>180°，|b|>1) 时会把圆心放到弦的错误一侧。这里的推导对
        劣弧与优弧一律成立：

            chord = R·(rot(u,θ) − u) = 2R·sin(θ/2)·rot(u, π/2 + θ/2)

        即弦方向 = rot(u, π/2 + θ/2)，反解出半径方向 u，圆心 = 起点 − R·u。
        顺时针(b<0)按反向段求得同一圆心。
        """
        if self.is_line:
            raise ValueError("直线段无圆弧参数")
        chord = self.end - self.start
        c = chord.length()
        b = self.bulge

        if b < 0:
            # 顺时针弧与反向的逆时针弧是同一段圆弧，圆心相同
            center, r, _, theta_abs, _ = Segment(self.end, self.start, -b).arc_info()
            return center, r, (self.start - center).angle(), theta_abs, False

        theta = 4.0 * math.atan(b)
        r = c * (1.0 + b * b) / (4.0 * b)
        chord_dir = chord.normalized()
        u = chord_dir.perpendicular().rotate(-(math.pi + theta / 2))
        center = self.start - u * r
        return center, r, (self.start - center).angle(), theta, True

    def arc_length(self) -> float:
        _, r, _, theta, _ = self.arc_info()
        return abs(r * theta)

    def arc_midpoint(self) -> Vec:
        """圆弧上参数中点（不是弦中点）。"""
        center, r, start_ang, theta, ccw = self.arc_info()
        half = theta * 0.5
        # 逆时针则加半角，顺时针则减
        ang = start_ang + (half if ccw else -half)
        return center + Vec(math.cos(ang), math.sin(ang)) * r

    def point_at(self, s: float) -> Vec:
        """按弧长参数 s∈[0,1] 取点。直线用线性插值。"""
        if self.is_line:
            return self.start + (self.end - self.start) * s
        center, r, start_ang, theta, ccw = self.arc_info()
        ang = start_ang + (theta if ccw else -theta) * s
        return center + Vec(math.cos(ang), math.sin(ang)) * r

    def tangent_at(self, s: float) -> Vec:
        """切向单位向量。用于判断折痕侧与画面朝向。"""
        if self.is_line:
            return (self.end - self.start).normalized()
        _, _, start_ang, theta, ccw = self.arc_info()
        ang = start_ang + (theta if ccw else -theta) * s
        # 切向 = 径向旋转 ±90°
        radial = Vec(math.cos(ang), math.sin(ang))
        return radial.perpendicular() if ccw else -radial.perpendicular()

    def bbox(self) -> tuple[float, float, float, float]:
        """返回 (minx, miny, maxx, maxy)。圆弧需考虑极值点。"""
        xs = [self.start.x, self.end.x]
        ys = [self.start.y, self.end.y]
        if self.is_arc:
            center, r, start_ang, theta, ccw = self.arc_info()
            # 圆弧的极值只可能出现在 0/90/180/270 四个方向角上。
            # 判断某方向角 a 是否被弧扫过：沿弧的行进方向从起点走到 a
            # 所转过的（规约到 [0,2π) 的）角度，若不超过弧的总扫过角即在弧内。
            # 直接把 a 规约进 [lo,hi] 区间是不对的 —— 区间本身可能跨 2π。
            for k in range(4):
                a = k * math.pi / 2
                if ccw:
                    rel = (a - start_ang) % (2.0 * math.pi)
                else:
                    rel = (start_ang - a) % (2.0 * math.pi)
                if rel <= theta + EPS:
                    xs.append(center.x + r * math.cos(a))
                    ys.append(center.y + r * math.sin(a))
        return min(xs), min(ys), max(xs), max(ys)

    def signed_area_contribution(self) -> float:
        """本段对闭合路径的有向面积贡献（原点相关的线积分）。

        闭路面积 = (1/2)∮(x·dy − y·dx)。直线段被积函数线性，
        贡献恰为端点叉积的一半。

        圆弧不能沿用叉积式：把 P = C + R(cosθ, sinθ) 代入线积分得

            (1/2)∫ = R²·Δθ/2 + (R/2)[Cx(sinθ₁−sinθ₀) − Cy(cosθ₁−cosθ₀)]

        圆心不在原点时存在第二项。用端点叉积代替它会在圆弧处
        系统性偏差（整圆偏 2πR·|C|，100×50 圆角矩形偏 50mm²），
        而板料用量与自交判定都依赖该面积，故按上式解析求取。
        """
        if self.is_line:
            return (self.start.x * self.end.y - self.end.x * self.start.y) * 0.5

        center, r, start_ang, theta, ccw = self.arc_info()
        delta = theta if ccw else -theta
        t1 = start_ang + delta

        polar = 0.5 * r * r * delta
        offset = (
            0.5
            * r
            * (
                center.x * (math.sin(t1) - math.sin(start_ang))
                - center.y * (math.cos(t1) - math.cos(start_ang))
            )
        )
        return polar + offset


def bulge_from_angle(theta: float) -> float:
    """包含角(弧度) -> bulge。"""
    return math.tan(theta / 4.0)


def _ccw_arc(center: Vec, radius: float, a0: float, a1: float) -> Segment:
    """由起止角构造逆时针圆弧，自动取逆时针方向（可能跨 2π）。

    圆角矩形的每个圆角都是 90° 逆时针，但 a0/a1 由 atan2 得出，
    跨越 ±π 时会得到 a1 < a0 的假象，这里统一规约到逆时针区间。
    """
    while a1 <= a0 + EPS:
        a1 += 2.0 * math.pi
    return arc_segment(center, radius, a0, a1)


def arc_segment(center: Vec, radius: float, start_ang: float, end_ang: float) -> Segment:
    """由圆心/半径/起止角构造圆弧段。

    逆时针从 start_ang 到 end_ang；若 end_ang < start_ang 则视为顺时针。
    """
    ccw = end_ang > start_ang
    theta = (end_ang - start_ang) if ccw else (start_ang - end_ang)
    start = center + Vec(math.cos(start_ang), math.sin(start_ang)) * radius
    end = center + Vec(math.cos(end_ang), math.sin(end_ang)) * radius
    b = bulge_from_angle(theta)
    if not ccw:
        b = -b
    return Segment(start, end, b)


# ---------------------------------------------------------------------------
# 仿射变换
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Affine:
    """二维仿射变换，6 参数：x' = a·x + c·y + e, y' = b·x + d·y + f。

    与 DXF/PDF 的矩阵约定一致，便于直接写给 exporter。
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    @staticmethod
    def identity() -> Affine:
        return Affine()

    @staticmethod
    def translate(tx: float, ty: float) -> Affine:
        return Affine(1, 0, 0, 1, tx, ty)

    @staticmethod
    def rotate(angle_rad: float) -> Affine:
        c, s = math.cos(angle_rad), math.sin(angle_rad)
        return Affine(c, s, -s, c, 0, 0)

    @staticmethod
    def scale(sx: float, sy: float | None = None) -> Affine:
        return Affine(sx, 0, 0, sy if sy is not None else sx, 0, 0)

    @staticmethod
    def mirror_x() -> Affine:
        """沿 Y 轴镜像（X 取反）。用于同一面在展开图里的左右翻版。"""
        return Affine(-1, 0, 0, 1, 0, 0)

    def __matmul__(self, o: Affine) -> Affine:
        """组合：self ∘ o（先施加 o，再施加 self）。"""
        return Affine(
            self.a * o.a + self.c * o.b,
            self.b * o.a + self.d * o.b,
            self.a * o.c + self.c * o.d,
            self.b * o.c + self.d * o.d,
            self.a * o.e + self.c * o.f + self.e,
            self.b * o.e + self.d * o.f + self.f,
        )

    def inverse(self) -> Affine:
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            raise ValueError("仿射变换不可逆")
        a, b, c, d = self.d / det, -self.b / det, -self.c / det, self.a / det
        return Affine(a, b, c, d, -(a * self.e + c * self.f), -(b * self.e + d * self.f))

    def apply(self, p: Vec) -> Vec:
        return Vec(self.a * p.x + self.c * p.y + self.e, self.b * p.x + self.d * p.y + self.f)

    def apply_vector(self, v: Vec) -> Vec:
        """只施加线性部分（不含平移），用于变换方向向量。"""
        return Vec(self.a * v.x + self.c * v.y, self.b * v.x + self.d * v.y)

    def apply_segment(self, s: Segment) -> Segment:
        """变换一段路径。

        bulge 在以原点为中心的均匀缩放下不变（角度保持），
        但在镜像（负行列式）下需要取负 —— 因为圆弧方向翻转了。
        非均匀缩放无法保持圆弧为圆弧，调用方不应在本项目里这么用。
        """
        det = self.a * self.d - self.b * self.c
        b = s.bulge if det >= 0 else -s.bulge
        return Segment(self.apply(s.start), self.apply(s.end), b)

    def rotate_about(self, origin: Vec, angle_rad: float) -> Affine:
        return (
            Affine.translate(origin.x, origin.y)
            @ Affine.rotate(angle_rad)
            @ Affine.translate(-origin.x, -origin.y)
        )


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


@dataclass
class Path:
    """一串相连的 Segment。

    不强制闭合，因为展开图里切口常常是开放路径（例如插舌轮廓）。
    `closed` 标记由构造方声明，`is_closed()` 用几何判断做交叉验证。
    """

    segments: list[Segment]
    closed: bool = False

    @staticmethod
    def from_points(points: Sequence[Vec], closed: bool = True) -> Path:
        """由折线点列构造全直线路径。"""
        segs: list[Segment] = []
        n = len(points)
        if n < 2:
            raise ValueError("构造路径至少需要 2 个点")
        last = n if closed else n - 1
        for i in range(last):
            segs.append(Segment(points[i], points[(i + 1) % n]))
        return Path(segs, closed)

    @staticmethod
    def rect(x: float, y: float, w: float, h: float) -> Path:
        """左下角在 (x,y)，宽 w 高 h 的矩形。逆时针。"""
        pts = [Vec(x, y), Vec(x + w, y), Vec(x + w, y + h), Vec(x, y + h)]
        return Path.from_points(pts, closed=True)

    @staticmethod
    def rounded_rect(
        x: float, y: float, w: float, h: float, r: float, corners: str = "all"
    ) -> Path:
        """圆角矩形。corners 可含 'bl'/'br'/'tr'/'tl' 字母子集，或 'all'。

        刀模图里的圆角是常见需求（避免应力集中、便于脱模、视觉柔和）。
        用圆弧段表达，导出 DXF 时即为 bulge。
        """
        if corners == "all":
            want = {"bl", "br", "tr", "tl"}
        else:
            want = {corners[i : i + 2] for i in range(0, len(corners), 2)}
        r = min(r, w / 2, h / 2)

        # 逆时针绕一周的四个角。每个角给出「角点」与「圆心」，
        # 以及圆角时直边与圆弧的相接点。未圆角的角，入口/出口即角点本身，
        # 两条直边直接在该角相接。
        #
        # 有意不硬编码圆弧的起止角度 —— 角度由端点位置反推（见下）。
        # 硬编码角度表一旦与端点不符，圆弧会整体偏移却仍能拼成闭合路径，
        # 面积与形状一起错而难以察觉。
        corner_pt = {
            "br": Vec(x + w, y),
            "tr": Vec(x + w, y + h),
            "tl": Vec(x, y + h),
            "bl": Vec(x, y),
        }
        corner_center = {
            "br": Vec(x + w - r, y + r),
            "tr": Vec(x + w - r, y + h - r),
            "tl": Vec(x + r, y + h - r),
            "bl": Vec(x + r, y + r),
        }
        # 圆角时直边端点相对角点的内缩方向
        corner_inset = {
            "br": (Vec(x + w - r, y), Vec(x + w, y + r)),
            "tr": (Vec(x + w, y + h - r), Vec(x + w - r, y + h)),
            "tl": (Vec(x + r, y + h), Vec(x, y + h - r)),
            "bl": (Vec(x, y + r), Vec(x + r, y)),
        }

        def entry_of(name: str) -> Vec:
            return corner_inset[name][0] if name in want else corner_pt[name]

        def exit_of(name: str) -> Vec:
            return corner_inset[name][1] if name in want else corner_pt[name]

        segs: list[Segment] = []
        order = ["br", "tr", "tl", "bl"]
        prev_exit = exit_of("bl")  # 左下角出口 = 底边起点
        for name in order:
            entry = entry_of(name)
            if not prev_exit.is_close(entry):
                segs.append(Segment(prev_exit, entry))
            if name in want:
                c = corner_center[name]
                segs.append(_ccw_arc(c, r, (entry - c).angle(), (exit_of(name) - c).angle()))
            prev_exit = exit_of(name)
        return Path(segs, closed=True)

    # ---- 查询 ----

    def start_point(self) -> Vec:
        return self.segments[0].start

    def end_point(self) -> Vec:
        return self.segments[-1].end

    def is_closed(self, tol: float = TOL) -> bool:
        """几何判断闭合：末段终点是否回到首段起点。"""
        if not self.segments:
            return False
        return self.end_point().is_close(self.start_point(), tol)

    def length(self) -> float:
        return sum(s.length() for s in self.segments)

    def bbox(self) -> tuple[float, float, float, float]:
        if not self.segments:
            raise ValueError("空路径无包围盒")
        boxes = [s.bbox() for s in self.segments]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def vertices(self) -> list[Vec]:
        """所有段的起点（闭合路径时即多边形顶点序列）。"""
        return [s.start for s in self.segments]

    def transformed(self, m: Affine) -> Path:
        return Path([m.apply_segment(s) for s in self.segments], self.closed)

    def translated(self, dx: float, dy: float) -> Path:
        return self.transformed(Affine.translate(dx, dy))

    def reversed(self) -> Path:
        return Path([s.reversed() for s in reversed(self.segments)], self.closed)

    def to_points(self, max_sagitta: float = 0.05) -> list[Vec]:
        """离散化为点列。圆弧按最大矢高 max_sagitta(mm) 细分。

        默认 0.05mm 矢高远高于任何刀模精度，保证离散化不引入可见误差。
        用于生成 3D mesh、做多边形运算、以及粗略预览。

        闭合路径末尾显式回到起点：各段端点经浮点运算可能漂移
        （如 4.999999999999999 vs 5.0），不显式闭合会让下游的多边形
        运算与预览出现 1e-15 量级的缺口。
        """
        pts: list[Vec] = []
        for s in self.segments:
            pts.append(s.start)
            if s.is_arc:
                _, r, _, theta, _ = s.arc_info()
                if r > max_sagitta:
                    # 由矢高求所需分段数：n = θ / (2·acos(1 - sag/r))
                    cos_arg = max(1.0 - max_sagitta / r, -1.0)
                    step = 2.0 * math.acos(cos_arg)
                    n = max(1, int(math.ceil(abs(theta) / step)))
                else:
                    n = 1
                for i in range(1, n):
                    pts.append(s.point_at(i / n))
        if self.closed:
            if pts and not pts[-1].is_close(pts[0], 0.0):
                pts.append(pts[0])
        else:
            pts.append(self.end_point())
        return pts

    def area(self) -> float:
        """闭合路径的净面积。逆时针为正，顺时针为负。

        圆弧段解析积分（见 Segment.signed_area_contribution），
        不做多边形近似 —— 板料用量与自交判定都依赖这个值。
        """
        return sum(s.signed_area_contribution() for s in self.segments)

    def shape_area(self) -> float:
        """几何面积（恒为正）。用于关心"多大一块料"而非方向的场合。"""
        return abs(self.area())

    def self_intersects(self) -> bool:
        """自交检测。

        刀模图的切口自交会导致割样机路径错误，属必须拦下的错误。
        这里做 O(n²) 线段两两检测 —— 展开图的段数在数百量级，
        朴素算法足够快，且比 Bentley-Ottmann 少很多边界情况。
        相邻段共享端点属正常，跳过；非相邻段相交即判定自交。
        """
        segs = [(s.start, s.end) for s in self.segments]
        n = len(segs)
        for i in range(n):
            for j in range(i + 1, n):
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue  # 相邻段共享端点
                if _segments_intersect(segs[i], segs[j]):
                    return True
        return False


def _segments_intersect(a: tuple[Vec, Vec], b: tuple[Vec, Vec]) -> bool:
    """两条线段是否真交叉（不含仅端点接触）。"""
    p1, p2 = a
    p3, p4 = b

    def orient(o: Vec, p: Vec, q: Vec) -> float:
        return (p - o).cross(q - o)

    d1 = orient(p3, p4, p1)
    d2 = orient(p3, p4, p2)
    d3 = orient(p1, p2, p3)
    d4 = orient(p1, p2, p4)

    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and (
        (d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)
    ):
        return True
    return False


def polyline_area(points: Sequence[Vec]) -> float:
    """点列面积（鞋带公式）。"""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        p, q = points[i], points[(i + 1) % n]
        total += p.x * q.y - q.x * p.y
    return total * 0.5


def bbox_of(points: Iterable[Vec]) -> tuple[float, float, float, float]:
    pts = list(points)
    if not pts:
        raise ValueError("空点集无包围盒")
    return (
        min(p.x for p in pts),
        min(p.y for p in pts),
        max(p.x for p in pts),
        max(p.y for p in pts),
    )


def lerp(a: Vec, b: Vec, t: float) -> Vec:
    return a + (b - a) * t


def mm_to_px(mm: float, dpi: float) -> int:
    """毫米 -> 像素。印刷分辨率的唯一换算入口，保证全局一致。"""
    return int(round(mm * dpi / 25.4))


def px_to_mm(px: float, dpi: float) -> float:
    return px * 25.4 / dpi


def snap(v: float, grid: float = 0.01) -> float:
    """吸附到网格。输出前统一吸附，避免图纸上出现 89.99999999。"""
    return round(v / grid) * grid


__all__ = [
    "EPS",
    "TOL",
    "Affine",
    "Path",
    "Segment",
    "Vec",
    "arc_segment",
    "bbox_of",
    "bulge_from_angle",
    "lerp",
    "mm_to_px",
    "polyline_area",
    "px_to_mm",
    "snap",
    "vec",
]
