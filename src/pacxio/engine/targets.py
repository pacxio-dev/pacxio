"""折好以后每块面该在哪（几何引擎的折叠自检）：刀版上的点 → 立体上的位置。

立体是中面长方体（墙宽就是刀版上的墙宽），原点在一个底角，墙按 背 → 侧 a → 正 → 侧 b 逆时针围一圈，
高度就是刀版上离背面下沿的距离。接头在墙中间（开槽箱 0230 / 0231）时那面墙分两截，tube 给每截在哪面墙、
从墙的哪儿起（{"L", "W", "pieces": {面: [墙, 起点]}}）。封口积木给每块面一种放法：
- wall    接着墙往上 / 往下（挂板）
- flat    在墙的上沿 / 下沿折进去平铺（盖、翼、防尘翼、底翼）
- drop_v  平铺的面远边再往下折 90°（插舌插进对面墙里）
- drop_u  平铺的面两侧再往下折 90°（侧插翼插进侧墙里）
- fold_back 整片折 180° 贴在墙里面（开槽箱 0214 的窄边）
自检时按刀版自带的折法折到最后一步（不算分层的纸厚），墙对齐立体后量每个点离目标多远。
"""

from __future__ import annotations

import math
from typing import Any

from pacxio.engine.solid import rect_polygon
from pacxio.model.dieline import Dieline

WALLS = ("back", "side_a", "front", "side_b")
Target = tuple[str, tuple[float, float], tuple[float, float, float]]
EPS = 1e-6


def _pieces(tube: dict[str, Any] | None) -> dict[str, tuple[str, float]]:
    out = {pid: (pid, 0.0) for pid in WALLS}
    for pid, (wall, u0) in (tube or {}).get("pieces", {}).items():
        out[pid] = (wall, u0)
    return out


def _base(dl: Dieline, tube: dict[str, Any] | None) -> list[tuple[float, float]]:
    if tube:
        return rect_polygon(tube["L"], tube["W"])
    back, side = dl.panel("back").polygon.bbox(), dl.panel("side_a").polygon.bbox()
    return rect_polygon(back[2] - back[0], side[2] - side[0])


def tube_targets(dl: Dieline, tube: dict[str, Any] | None = None) -> list[Target]:
    """长方筒身的墙：刀版上每块墙的四个角 → 中面长方体上那面墙的对应位置（左下、右下、右上、左上）。"""
    base = _base(dl, tube)
    yb = dl.panel("back").polygon.bbox()[1]
    out = []
    for pid, (wall, u0) in _pieces(tube).items():
        x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
        i = WALLS.index(wall)
        a, b = base[i], base[(i + 1) % 4]
        n = math.dist(a, b)
        e = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
        # 墙的实际宽（开槽箱最后一面故意短一层纸厚，给接头让位）
        p, c = ((a[0] + e[0] * u, a[1] + e[1] * u) for u in (u0, u0 + x1 - x0))
        for (x, y), q in (((x0, y0), p), ((x1, y0), c), ((x1, y1), c), ((x0, y1), p)):
            out.append((pid, (x, y), (q[0], q[1], y - yb)))
    part = (tube or {}).get("partition")
    if part:  # 隔板（0228）：挂在墙上 u 处，往箱子里横过去（刀版上它的右边挨着墙）
        x0, y0, x1, y1 = dl.panel(part["panel"]).polygon.bbox()
        i = WALLS.index(part["wall"])
        a, b = base[i], base[(i + 1) % 4]
        n = math.dist(a, b)
        e = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
        root = (a[0] + e[0] * part["u"], a[1] + e[1] * part["u"])
        far = (root[0] - e[1] * (x1 - x0), root[1] + e[0] * (x1 - x0))  # 沿朝里的法线
        for (x, y), q in (((x1, y0), root), ((x0, y0), far), ((x0, y1), far), ((x1, y1), root)):
            out.append((part["panel"], (x, y), (q[0], q[1], y - yb)))
    return out


class _Frames:
    def __init__(self, dl: Dieline, tube: dict[str, Any] | None = None) -> None:
        pieces = _pieces(tube)
        self.box = {pid: dl.panel(pid).polygon.bbox() for pid in pieces}
        base = _base(dl, tube)
        self.yb = self.box["back"][1]
        self.frame: dict[
            str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
        ] = {}
        for pid, (wall, u0) in pieces.items():
            i = WALLS.index(wall)
            a, b = base[i], base[(i + 1) % 4]
            n = math.dist(a, b)
            e = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
            origin = (a[0] + e[0] * u0, a[1] + e[1] * u0)
            self.frame[pid] = (origin, e, (-e[1], e[0]))  # 逆时针一圈：左手法线朝里

    def point(self, wall: str, u: float, v: float, z: float) -> tuple[float, float, float]:
        a, e, n = self.frame[wall]
        return (a[0] + e[0] * u + n[0] * v, a[1] + e[1] * u + n[1] * v, z)


def _end_of(dl: Dieline, pid: str, wall_box: tuple[float, float, float, float]) -> bool:
    """这块面挂在墙的上沿（True）还是下沿。"""
    _, y0, _, y1 = dl.panel(pid).polygon.bbox()
    return (y0 + y1) / 2 > (wall_box[1] + wall_box[3]) / 2


def pose_point(
    f: _Frames,
    kind: str,
    wall: str,
    top: bool,
    x: float,
    y: float,
    v0: float = 0.0,
    left: bool = False,
) -> tuple[float, float, float]:
    x0, y0, x1, y1 = f.box[wall]
    u = x - x0
    if kind == "wall":
        return f.point(wall, u, 0.0, y - f.yb)
    hinge = y1 if top else y0
    v = (y - hinge) if top else (hinge - y)
    zh, s = hinge - f.yb, (1.0 if top else -1.0)
    if kind == "flat":
        return f.point(wall, u, v, zh)
    if kind == "wall_fold":  # 墙往上加高的一块在 v0 处对折 180°，贴回来（双层挂板的第二层）
        return f.point(wall, u, 0.0, zh + s * (2 * v0 - v))
    if kind == "fold_back":  # 整片折 180° 贴在墙里面（0214 的窄边）
        return f.point(wall, u, 0.0, zh - s * v)
    if kind == "drop_v":
        return f.point(wall, u, v0, zh - s * (v - v0))
    if kind == "rise_v":  # 平铺的面远边再往外立起来 90°（0222 的提手板）
        return f.point(wall, u, v0, zh + s * (v - v0))
    if kind == "drop_u":  # left：翼在墙的左边（u ≤ 0），否则在右边（u ≥ 墙宽）
        w = x1 - x0
        if left:
            return f.point(wall, 0.0, v, zh + s * u)
        return f.point(wall, w, v, zh - s * (u - w))
    raise ValueError(f"未知放法 {kind}")


def _reflect(
    q: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    """点 q 沿 a→b 这条线翻过去。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    px, py = q[0] - a[0], q[1] - a[1]
    k = 2 * (px * ux + py * uy)
    return (a[0] + k * ux - px, a[1] + k * uy - py)


def closure_targets(
    dl: Dieline, poses: dict[str, Any], tube: dict[str, Any] | None = None
) -> list[Target]:
    """封口积木给的放法 → 每块面每个折点的目标位置。"""
    f = _Frames(dl, tube)
    out: list[Target] = []
    for pid, (kind, wall, *extra) in poses.items():
        top = _end_of(dl, pid, f.box[wall])
        pts = [(s.start.x, s.start.y) for s in dl.panel(pid).polygon.segments]
        if kind in ("rise_back", "rise_lie", "drop_back"):
            # 0242：立起的挂板对折回来那块 / 再平铺出去那块；0207：伸进箱子的隔板对折回来那块（extra[0] = 立起处离铰链）
            hinge = f.box[wall][3] if top else f.box[wall][1]
            zh, sz = hinge - f.yb, (1.0 if top else -1.0)
            sz = -sz if kind == "drop_back" else sz
            root, near = extra[0], min(abs(y - hinge) for _, y in pts)
            for x, y in pts:
                u, v = x - f.box[wall][0], abs(y - hinge)
                if kind in ("rise_back", "drop_back"):
                    q = f.point(wall, u, root, zh + sz * (2 * near - v - root))
                else:
                    q = f.point(wall, u, root + v - near, zh)
                out.append((pid, (x, y), q))
            continue
        v0 = 0.0
        if kind in ("drop_v", "wall_fold", "rise_v"):
            hinge = f.box[wall][3] if top else f.box[wall][1]
            v0 = min(abs(y - hinge) for _, y in pts)  # 插舌根线离盖铰链多远（= 盖深）
        left = min(x for x, _ in pts) < f.box[wall][0] - EPS
        if (
            kind == "gusset"
        ):  # 三角折页（0226）：沿斜压线（墙角 → 中间三角尖）翻 180°，再跟着中间三角平铺
            hinge = f.box[wall][3] if top else f.box[wall][1]
            corner = min(pts, key=lambda q: abs(q[1] - hinge))
            apex = max(pts, key=lambda q: abs(q[0] - corner[0]))
            out += [(pid, (x, y), pose_point(f, "flat", wall, top, *_reflect((x, y), corner, apex)))
                    for x, y in pts]  # fmt: skip
            continue
        out += [(pid, (x, y), pose_point(f, kind, wall, top, x, y, v0, left)) for x, y in pts]
    return out


def cover_faces(
    dl: Dieline, poses: dict[str, Any], covers: dict[str, str], tube: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """效果图的顶 / 底面：合上后外面看到的那块面，四角（左上、右上、右下、左下）在立体上的位置。"""
    f = _Frames(dl, tube)
    out = []
    for key, pid in covers.items():
        kind, wall, *_ = poses[pid]
        if kind != "flat":
            continue
        top = _end_of(dl, pid, f.box[wall])
        x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
        corners = [
            pose_point(f, kind, wall, top, x, y)
            for x, y in ((x0, y1), (x1, y1), (x1, y0), (x0, y0))
        ]
        out.append({"key": key, "panel": pid, "rect": [x0, y0, x1, y1],
                    "corners": [[round(c, 6) for c in p] for p in corners]})  # fmt: skip
    return out


__all__ = ["closure_targets", "cover_faces", "pose_point", "tube_targets"]
