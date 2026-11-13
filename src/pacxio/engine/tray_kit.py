"""挂面工具：在已有托盘（Tray）上挂新面，按立体声明位置、折角从立体算（GEOMETRY_ENGINE.md §1 原则 3）。

每块新面挂在一块已放好的面的一条刀版边（铰链）上，声明它在立起的盒子里从铰链往哪个方向伸出
（`toward`，立体里的单位向量，和铰链垂直）。面上每个刀版点的立体位置 = 铰链起点 + 沿铰链的分量
+ 离开铰链的距离 × toward。折角 = 父面和子面在立体里的夹角（带山 / 谷方向，正 = 子面往刀版 −z，
即往纸的背面、盒里折）；±180° 的对折用 `sign` 选方向。目标点由声明的立体位置给出，折回自检照样核对。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.styles.corrugated import crease

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Pose = Callable[[float, float], Vec3]


def ccw(pts: Sequence[Vec2]) -> list[Vec2]:
    pts = list(pts)
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))
    return pts if area > 0 else pts[::-1]


def polygon(pts: Sequence[Vec2]) -> Path:
    return Path.from_points([Vec(x, y) for x, y in ccw(pts)], closed=True)


def rect(x0: float, y0: float, x1: float, y1: float) -> list[Vec2]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


@dataclass
class Rigid:
    """刀版平面 → 立体的刚体映射：X = b + x·c1 + y·c2 + z·(c1 × c2)。"""

    b: np.ndarray
    c1: np.ndarray
    c2: np.ndarray

    @property
    def n(self) -> np.ndarray:
        return np.cross(self.c1, self.c2)

    def __call__(self, x: float, y: float) -> Vec3:
        v = self.b + x * self.c1 + y * self.c2
        return (float(v[0]), float(v[1]), float(v[2]))

    def local(self, p: Sequence[float]) -> np.ndarray:
        """立体点 → 这块面的刀版坐标（x, y, z）。"""
        m = np.column_stack([self.c1, self.c2, self.n])
        return np.asarray(m.T @ (np.asarray(p, dtype=float) - self.b))

    @staticmethod
    def fit(pairs: Sequence[tuple[Vec2, Vec3]]) -> Rigid:
        a = np.array([[x, y, 1.0] for (x, y), _ in pairs])
        dst = np.array([t for _, t in pairs], dtype=float)
        sol, *_ = np.linalg.lstsq(a, dst, rcond=None)
        return Rigid(sol[2], sol[0], sol[1])


def _unit(v: Sequence[float]) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    out: np.ndarray = a / float(np.linalg.norm(a))
    return out


@dataclass
class Kit:
    panels: dict[str, tuple[PanelRole, Path]]
    creases: list[Edge]
    cuts: list[Edge]
    folds: dict[str, dict[str, Any]]
    sizes: dict[str, tuple[float, float]]
    faces: list[dict[str, Any]]
    pose: dict[str, Rigid] = field(default_factory=dict)
    targets: list[tuple[str, Vec2, Vec3]] = field(default_factory=list)

    @staticmethod
    def of(tray: Tray) -> Kit:
        by: dict[str, list[tuple[Vec2, Vec3]]] = {}
        for pid, xy, t in tray.targets:
            by.setdefault(pid, []).append((xy, t))
        return Kit(dict(tray.panels), list(tray.creases), list(tray.cuts),
                   {k: dict(v) for k, v in tray.folds.items()}, dict(tray.sizes), list(tray.faces),
                   {pid: Rigid.fit(pairs) for pid, pairs in by.items()}, list(tray.targets))  # fmt: skip

    @staticmethod
    def root(pid: str, role: PanelRole, pts: Sequence[Vec2], pose: Rigid) -> Kit:
        kit = Kit({}, [], [], {}, {}, [])
        kit.panels[pid] = (role, polygon(pts))
        kit.pose[pid] = pose
        kit._targets(pid)
        kit.sizes[pid] = _bbox_size(pts)
        return kit

    def at(self, pid: str, x: float, y: float) -> Vec3:
        return self.pose[pid](x, y)

    def add(
        self,
        pid: str,
        role: PanelRole,
        pts: Sequence[Vec2],
        parent: str,
        hinge: tuple[Vec2, Vec2],
        toward: Sequence[float],
        step: int,
        *,
        layer: float = 0.0,
        sign: float = 1.0,
        keys: list[list[float]] | None = None,
        size: tuple[float, float] | None = None,
        crease_id: str | None = None,
        kind: EdgeType = EdgeType.CREASE,
    ) -> str:
        """pts：新面的刀版轮廓；hinge：和父面共用的那条压线（刀版两点）；toward：立体里离开铰链的方向。"""
        a, b = np.array(hinge[0], dtype=float), np.array(hinge[1], dtype=float)
        u = (b - a) / np.linalg.norm(b - a)
        w = np.array([-u[1], u[0]])
        centre = np.mean(np.array(pts, dtype=float), axis=0)
        if float((centre - a) @ w) < 0:
            w = -w
        par = self.pose[parent]
        A = np.array(par(*hinge[0]))
        e1 = _unit(np.array(par(*hinge[1])) - A)
        D = _unit(toward)
        if abs(float(e1 @ D)) > 1e-6:
            raise ValueError(
                translate("{panel}: toward must be perpendicular to the hinge", panel=pid)
            )
        c1 = u[0] * e1 + w[0] * D
        c2 = u[1] * e1 + w[1] * D
        pose = Rigid(A - a[0] * c1 - a[1] * c2, c1, c2)
        self.panels[pid] = (role, polygon(pts))
        self.pose[pid] = pose
        self._targets(pid)
        if keys is None:
            r = par.local(pose(*centre)) - np.array([a[0], a[1], 0.0])
            deg = math.degrees(math.atan2(-float(r[2]), float(r[:2] @ w)))
            if abs(abs(deg) - 180.0) < 1e-6:
                deg = 180.0 * sign
            keys = [[step, round(deg, 9)]]
        self.folds[pid] = {"keys": keys, "layer": layer}
        self.creases.append(crease(crease_id or f"crease_{parent}_{pid}", hinge[0], hinge[1],
                                   [parent, pid], kind))  # fmt: skip
        self.sizes[pid] = size if size is not None else _bbox_size(pts)
        return pid

    def link(self, eid: str, a: Vec2, b: Vec2, panels: list[str]) -> None:
        """两块已放好的面之间多一条压线（不当铰链，比如一块面两边都连着）。"""
        self.creases.append(crease(eid, a, b, panels))

    def move_crease(self, eid: str, a: Vec2, b: Vec2) -> None:
        k = next(i for i, e in enumerate(self.creases) if e.id == eid)
        self.creases[k] = self.creases[k].model_copy(
            update={"segments": [Segment(Vec(*a), Vec(*b))]}
        )

    def slot(self, eid: str, pts: Sequence[Vec2], panel: str) -> None:
        ring = [Vec(x, y) for x, y in pts] + [Vec(*pts[0])]
        segs = [Segment(p, q) for p, q in zip(ring, ring[1:], strict=False)]
        self.cuts.append(Edge(id=eid, type=EdgeType.CUT, segments=segs, panels=[panel]))

    def score(self, eid: str, a: Vec2, b: Vec2, panel: str) -> None:
        """面里面的单面压线（立起来不折）。"""
        self.cuts.append(Edge(id=eid, type=EdgeType.CREASE, segments=[Segment(Vec(*a), Vec(*b))],
                              panels=[panel]))  # fmt: skip

    def drop(self, pid: str) -> None:
        self.panels.pop(pid)
        self.pose.pop(pid, None)
        self.folds.pop(pid, None)
        self.sizes.pop(pid, None)
        self.targets = [t for t in self.targets if t[0] != pid]
        self.creases = [e for e in self.creases if pid not in e.panels]

    def reshape(self, pid: str, pts: Sequence[Vec2]) -> None:
        """换一块面的轮廓（位置映射不变）。"""
        role, _ = self.panels[pid]
        self.panels[pid] = (role, polygon(pts))
        self.targets = [t for t in self.targets if t[0] != pid]
        self._targets(pid)
        self.sizes[pid] = _bbox_size(pts)

    def clip(self, pid: str, box: Sequence[Vec2]) -> None:
        """从一块面上切掉一块（box 是刀版上的多边形）。"""
        from shapely.geometry import Polygon as P

        path = self.panels[pid][1]
        shape = P([(s.start.x, s.start.y) for s in path.segments]).difference(P(box))
        if shape.is_empty or not isinstance(shape, P):
            raise ValueError(translate("{panel} is disconnected after cutting", panel=pid))
        self.reshape(pid, [(float(x), float(y)) for x, y in list(shape.exterior.coords)[:-1]])

    def cut_tab(self, wall: str, tab: Sequence[Vec2], notch: Sequence[Vec2]) -> None:
        """墙上长出一块凸舌（tab 并进墙的轮廓，不跟着旁边的面折），旁边的面让出缺口 notch：
        被缺口切成两半的面拆成两块（同一个立体位置、同一个折法），压线在缺口里断开。"""
        from shapely.geometry import LineString
        from shapely.geometry import Polygon as P

        hole = P(notch)
        role, path = self.panels[wall]
        grown = P([(s.start.x, s.start.y) for s in path.segments]).union(P(tab))
        if not isinstance(grown, P):
            raise ValueError(
                translate("{wall} is disconnected after adding the stacking tab", wall=wall)
            )
        self.reshape(wall, [(float(x), float(y)) for x, y in list(grown.exterior.coords)[:-1]])
        pieces: dict[str, list[tuple[str, P]]] = {}
        for pid, (_, q) in list(self.panels.items()):
            if pid == wall:
                continue
            poly = P([(s.start.x, s.start.y) for s in q.segments])
            if poly.intersection(hole).area < 1e-9:
                continue
            rest = poly.difference(hole)
            parts = (
                [rest]
                if isinstance(rest, P)
                else sorted(cast(Any, rest).geoms, key=lambda g: g.centroid.y + g.centroid.x)
            )
            names = [pid] + [f"{pid}_{i}" for i in range(2, len(parts) + 1)]
            pieces[pid] = list(zip(names, parts, strict=True))
            for name, part in zip(names, parts, strict=True):
                pts = [(float(x), float(y)) for x, y in list(part.exterior.coords)[:-1]]
                if name != pid:
                    self.panels[name] = (self.panels[pid][0], polygon(pts))
                    self.pose[name] = self.pose[pid]
                    self.folds[name] = dict(self.folds[pid])
                    self._targets(name)
                    self.sizes[name] = _bbox_size(pts)
                else:
                    self.reshape(pid, pts)
        out: list[Edge] = []
        for e in self.creases:
            line = LineString([(e.segments[0].start.x, e.segments[0].start.y),
                               (e.segments[-1].end.x, e.segments[-1].end.y)])  # fmt: skip
            keep = line.difference(hole) if line.intersection(hole).length > 1e-9 else line
            segs = [keep] if isinstance(keep, LineString) else list(cast(Any, keep).geoms)
            for n, seg in enumerate(g for g in segs if g.length > 1e-6):
                (ax, ay), (bx, by) = seg.coords[0], seg.coords[-1]
                mid = seg.interpolate(0.5, normalized=True)
                panels = []
                for pq in e.panels:
                    owner = pq
                    for name, part in pieces.get(pq, []):
                        if part.buffer(1e-6).contains(mid):
                            owner = name
                    panels.append(owner)
                eid = e.id if n == 0 else f"{e.id}_{n + 1}"
                out.append(e.model_copy(update={"id": eid, "panels": panels,
                                                "segments": [Segment(Vec(ax, ay), Vec(bx, by))]}))  # fmt: skip
        self.creases = out

    def open_slot(self, eid: str, pts: Sequence[Vec2], panel: str) -> None:
        """开一个孔；和已有的孔重叠就合成一个外包矩形；穿过 / 贴着孔边的压线在孔处断开。"""
        from shapely.geometry import LineString
        from shapely.geometry import Polygon as P

        hole = P(pts)
        for e in list(self.cuts):
            ring = [(s.start.x, s.start.y) for s in e.segments]
            if len(ring) >= 3 and P(ring).intersects(hole):
                self.cuts.remove(e)
                eid = e.id
                x0, y0, x1, y1 = P(ring).union(hole).bounds
                hole = P(rect(x0, y0, x1, y1))
        ring = [(float(x), float(y)) for x, y in list(hole.exterior.coords)[:-1]]
        self.slot(eid, ring, panel)
        out: list[Edge] = []
        for e in self.creases:
            line = LineString([(e.segments[0].start.x, e.segments[0].start.y),
                               (e.segments[-1].end.x, e.segments[-1].end.y)])  # fmt: skip
            if line.intersection(hole).length < 1e-9:
                out.append(e)
                continue
            keep = line.difference(hole)
            segs = [keep] if isinstance(keep, LineString) else list(cast(Any, keep).geoms)
            for n, seg in enumerate(g for g in segs if g.length > 1e-6):
                (ax, ay), (bx, by) = seg.coords[0], seg.coords[-1]
                out.append(e.model_copy(update={"id": e.id if n == 0 else f"{e.id}_s{n + 1}",
                                                "segments": [Segment(Vec(ax, ay), Vec(bx, by))]}))  # fmt: skip
        self.creases = out

    def face(self, key: str, pid: str) -> None:
        x0, y0, x1, y1 = self.panels[pid][1].bbox()
        corners = [list(self.at(pid, x, y)) for x, y in ((x0, y1), (x1, y1), (x1, y0), (x0, y0))]
        self.faces = [f for f in self.faces if f["key"] != key]
        self.faces.append({"key": key, "panel": pid, "rect": [x0, y0, x1, y1], "corners": corners})

    def last_step(self) -> int:
        return int(max(k[0] for m in self.folds.values() for k in m["keys"]))

    def _targets(self, pid: str) -> None:
        path = self.panels[pid][1]
        self.targets += [(pid, (s.start.x, s.start.y), self.at(pid, s.start.x, s.start.y))
                         for s in path.segments]  # fmt: skip

    def tray(self) -> Tray:
        from pacxio.engine.tray import Tray

        shape = unary_union([Polygon([(s.start.x, s.start.y) for s in path.segments])
                             for _, path in self.panels.values()])  # fmt: skip
        if not isinstance(shape, Polygon) or not shape.is_valid:
            raise ValueError(translate("The flat layout does not form a continuous blank"))
        ring = [(float(x), float(y)) for x, y in list(shape.exterior.coords)[:-1]]
        return Tray(polygon(ring), self.panels, self.creases, self.cuts, self.folds, self.targets,
                    self.sizes, self.faces)  # fmt: skip


def _bbox_size(pts: Sequence[Vec2]) -> tuple[float, float]:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (max(xs) - min(xs), max(ys) - min(ys))
