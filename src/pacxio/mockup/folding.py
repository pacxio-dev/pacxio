"""按刀版折叠（）：铰链树 + 每种盒型的折法表 → 每块面在任意进度下的位姿。

约定（查看器 viewer3d.js 用同一套算法，改这里要一起改）：
- 每块面的网格都在刀版坐标里：印刷面 z = 0，纸厚向 -z（盒内）；
- 折法表按面写，折角相对父面，正值向盒内折（印刷面朝外）；转轴就是压线所在的直线，
  所以合上后外形等于各面的尺寸，和方盒子效果图一致；
- 进度 s 的单位是"步"：第 n 步从 s = n - 1 走到 s = n，步内按 ease 缓动；
- layer：合上时再往里（负值往外）挪几层纸厚，分开本来会重合的两层（糊口在侧板里面、内摇盖在外摇盖
  下面）。只用于叶子面；随折角一起从 0 长到满，平铺时没有错位。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon, polygons_of
from pacxio.mockup.fold import BoxModel, Face
from pacxio.model.dieline import Dieline, EdgeType

Mat = NDArray[np.float64]
MIN_AREA_MM2 = 1e-3  # 布尔运算留下的碎片
BAND_MAX_RATIO = 0.45  # 压线过渡带最多占相邻面宽度的比例（卷边很窄时圆角跟着变小）
ALONG_EPS_MM = 1e-3


@dataclass(frozen=True)
class Move:
    """一块面怎么折：keys 依次是（第几步结束时, 折到多少度）。"""

    keys: tuple[tuple[int, float], ...]
    layer: float = 0.0


def fold(step: int, angle: float = 90.0, layer: float = 0.0) -> Move:
    return Move(((step, angle),), layer)


@dataclass(frozen=True)
class FoldPlan:
    root: str  # 查看时不动的那块面（只是树根，不影响折出来的形状）
    moves: dict[str, Move]  # 面（刀版铰链里的子面）→ 折法；不写的面不折（自动锁底的糊胶三角）
    steps: int
    open_step: int  # 停在第几步是"打开"：盖子还没盖、看得到里面
    pose_step: int  # 第几步结束时部件摆到合上时的朝向（之前从平铺慢慢转过去）
    ground_step: int  # 第几步结束时地面阴影完全出来（平铺时不画：阴影是按矩形算的）


@dataclass(frozen=True)
class Joint:
    child: str
    parent: str
    origin: tuple[float, float]  # 转轴上一点
    axis: tuple[float, float]  # 转轴方向（单位向量）：绕它转正角，子面往 -z 走
    keys: tuple[tuple[int, float], ...]
    layer: float
    edges: tuple[str, ...]  # 这条铰链上的所有压线（被插孔断开的算几条）


@dataclass
class FoldedPart:
    dl: Dieline
    plan: FoldPlan
    joints: list[Joint]  # 从树根往外排，父面总在子面前面


def _edge_axis(dl: Dieline, edge_id: str) -> tuple[tuple[float, float], tuple[float, float]]:
    edge = next(e for e in dl.edges if e.id == edge_id)
    seg = edge.segments[0]
    p, q = np.array(seg.start.as_tuple()), np.array(seg.end.as_tuple())
    d = (q - p) / np.linalg.norm(q - p)
    return (float(p[0]), float(p[1])), (float(d[0]), float(d[1]))


def _side_of(poly: BaseGeometry, origin: tuple[float, float], axis: tuple[float, float]) -> float:
    """面的形心在转轴的哪一边：axis × r 的 z 分量。"""
    c = poly.centroid
    return axis[0] * (c.y - origin[1]) - axis[1] * (c.x - origin[0])


def _collinear_creases(dl: Dieline, a: str, b: str, edge_id: str) -> tuple[str, ...]:
    """同一条铰链上的所有压线（中间被插孔断开时刀版上是几段）。"""
    pair = {a, b}
    others = [
        e.id
        for e in dl.edges
        if e.id != edge_id
        and e.type in (EdgeType.CREASE, EdgeType.REVERSE_CREASE)
        and set(e.panels) == pair
    ]
    return (edge_id, *others)


def build_part(dl: Dieline, plan: FoldPlan) -> FoldedPart:
    """按刀版铰链搭树，从 plan.root 往外定每条铰链的方向。"""
    neighbours: dict[str, list[tuple[str, str]]] = {}
    moved: dict[frozenset[str], str] = {}  # 铰链两端 → 刀版里的子面（折法表按它查）
    for p in dl.panels:
        if p.hinge is None:
            continue
        neighbours.setdefault(p.id, []).append((p.hinge.parent_panel, p.hinge.edge_id))
        neighbours.setdefault(p.hinge.parent_panel, []).append((p.id, p.hinge.edge_id))
        moved[frozenset((p.id, p.hinge.parent_panel))] = p.id
    joints: list[Joint] = []
    seen = {plan.root}
    queue = deque([plan.root])
    while queue:
        parent = queue.popleft()
        for child, edge_id in neighbours.get(parent, []):
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
            move = plan.moves.get(moved[frozenset((child, parent))], Move(()))
            origin, axis = _edge_axis(dl, edge_id)
            if _side_of(path_to_polygon(dl.panel(child).polygon), origin, axis) > 0:
                axis = (-axis[0], -axis[1])
            joints.append(
                Joint(
                    child,
                    parent,
                    origin,
                    axis,
                    move.keys,
                    move.layer,
                    _collinear_creases(dl, child, parent, edge_id),
                )  # fmt: skip
            )
    missing = {p.id for p in dl.panels} - seen
    if missing:
        raise ValueError(f"{dl.style} 这些面不在铰链树上：{sorted(missing)}")
    return FoldedPart(dl, plan, joints)


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def angle_at(keys: tuple[tuple[int, float], ...], s: float) -> float:
    """进度 s 时的折角：每一段从前一个关键角度走到这一个，只在自己那一步里动。"""
    angle = 0.0
    for step, target in keys:
        angle += (target - angle) * ease(s - (step - 1))
    return angle


def _rotation(origin: tuple[float, float], axis: tuple[float, float], deg: float) -> Mat:
    """绕刀版平面内的直线转 deg 度（右手系）。"""
    a = np.radians(deg)
    x, y = axis
    c, sn = float(np.cos(a)), float(np.sin(a))
    rot = np.array(
        [
            [c + x * x * (1 - c), x * y * (1 - c), y * sn, 0.0],
            [x * y * (1 - c), c + y * y * (1 - c), -x * sn, 0.0],
            [-y * sn, x * sn, c, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    to = np.eye(4)
    to[:2, 3] = origin
    back = np.eye(4)
    back[:2, 3] = (-origin[0], -origin[1])
    result: Mat = to @ rot @ back
    return result


def panel_matrices(part: FoldedPart, s: float) -> dict[str, Mat]:
    """进度 s 时每块面的矩阵：刀版坐标 → 树根的刀版坐标。"""
    out: dict[str, Mat] = {part.plan.root: np.eye(4)}
    t = part.dl.caliper_mm
    for j in part.joints:
        deg = angle_at(j.keys, s)
        m = out[j.parent] @ _rotation(j.origin, j.axis, deg)
        if j.layer and j.keys and j.keys[-1][1]:
            shift = np.eye(4)
            shift[2, 3] = -j.layer * t * deg / j.keys[-1][1]
            m = m @ shift
        out[j.child] = m
    return out


def apply(m: Mat, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
    v = m @ np.array([x, y, z, 1.0])
    return (float(v[0]), float(v[1]), float(v[2]))


def fit_pose(pairs: Iterable[tuple[tuple[float, float, float], tuple[float, float, float]]]) -> Mat:
    """最小二乘的刚体变换 src → dst（Kabsch）。"""
    src = np.array([p[0] for p in pairs], dtype=float)
    dst = np.array([p[1] for p in pairs], dtype=float)
    sc, dc = src.mean(axis=0), dst.mean(axis=0)
    u, _, vt = np.linalg.svd((src - sc).T @ (dst - dc))
    sign = float(np.sign(np.linalg.det(vt.T @ u.T)))
    rot = vt.T @ np.diag([1.0, 1.0, sign]) @ u.T
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = dc - rot @ sc
    return m


def face_pairs(
    part: FoldedPart, faces: Iterable[Face]
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """方盒子上的面的四个角（按贴图矩形）和折出来的同一处。拼出来的面不算（不是一张平贴的矩形）。"""
    closed = panel_matrices(part, part.plan.steps)
    pairs = []
    for f in faces:
        if f.parts or f.patches or f.panel not in closed:
            continue
        x0, y0, x1, y1 = f.tex_rect
        src = ((x0, y1), (x1, y1), (x1, y0), (x0, y0))
        for (u, v), corner in zip(src, f.corners, strict=True):
            pairs.append((apply(closed[f.panel], u, v), corner))
    return pairs


def closed_pose(part: FoldedPart, box: BoxModel) -> Mat:
    """把折好的部件摆到方盒子的坐标系（x 向右、y 向里、z 向上）：两边同一块面对齐。

    插舌不参与对齐：飞机盒的插舌包在前板外面，比方盒子的正面多一层纸厚。"""
    tucks = {p.id for p in part.dl.panels if p.role.value == "tuck"}
    return fit_pose(face_pairs(part, [f for f in box.faces if f.panel not in tucks]))


# ---- 每种盒型的折法表 ----------------------------------------------------------------


def _ring_body() -> dict[str, Move]:
    """盒身一圈：先折糊口，再围成筒，糊口贴在对面侧板里面。"""
    return {"glue": fold(1, layer=1), "side_a": fold(2), "front": fold(2), "side_b": fold(2)}


def _tuck_end(lid: str, tuck: str, a: str, b: str, first: int) -> dict[str, Move]:
    """插舌的一头：防尘翼 → 盖 → 插舌（盖合到一半时插舌先弯一点，才进得去）。"""
    return {
        a: fold(first),
        b: fold(first),
        lid: fold(first + 1),
        tuck: Move(((first, 45.0), (first + 1, 90.0)), layer=1),
    }


def _component_plan(kind: str, hanger: bool = False) -> FoldPlan:
    from pacxio.styles.components.folds import carton_recipe

    r = carton_recipe(kind, hanger)
    return FoldPlan(
        r.root,
        {pid: Move(a.keys, a.layer) for pid, a in r.moves.items()},
        r.steps,
        r.open_step,
        r.pose_step,
        r.ground_step,
    )


def _rte_plan(dl: Dieline) -> FoldPlan:
    return _component_plan("tuck", dl.style.endswith("_hanger"))


def _snaplock_plan(dl: Dieline) -> FoldPlan:
    return _component_plan("snaplock", dl.style.endswith("_hanger"))


def _autolock_plan(dl: Dieline) -> FoldPlan:
    return _component_plan("autolock", dl.style.endswith("_hanger"))


def _glued_plan(dl: Dieline) -> FoldPlan:
    from pacxio.styles.components.folds import glued_ends

    r = glued_ends(not dl.style_meta.get("top", {}).get("open"))
    return FoldPlan(
        r.root,
        {pid: Move(a.keys, a.layer) for pid, a in r.moves.items()},
        r.steps,
        r.open_step,
        r.pose_step,
        r.ground_step,
    )


def _flat_plan(dl: Dieline) -> FoldPlan:
    return FoldPlan(
        "front",
        {"back": fold(1, 180, layer=-2)} if dl.style_meta.get("folded") else {},
        steps=1,
        open_step=0,
        pose_step=1,
        ground_step=1,
    )


def _slotted_plan(dl: Dieline) -> FoldPlan:
    """开槽箱：先折底，内摇盖在下、外摇盖压上面；顶同样。"""
    moves = _ring_body()
    moves |= {
        "btm_side_a": fold(3, layer=1),
        "btm_side_b": fold(3, layer=1),
        "btm_back": fold(4),
        "btm_front": fold(4),
        "top_side_a": fold(5, layer=1),
        "top_side_b": fold(5, layer=1),
        "top_back": fold(6),
        "top_front": fold(6),
    }
    return FoldPlan("front", moves, steps=6, open_step=4, pose_step=2, ground_step=2)


def _crashlock_plan(dl: Dieline) -> FoldPlan:
    """自动锁底开槽箱：盒身一圈 → 自动锁底（同卡纸自动锁底，撑开后的样子）→ 顶部内摇盖在下、外摇盖压上面。"""
    moves = _ring_body()
    moves |= {
        "btm_back": fold(3),
        "btm_sa": fold(3, layer=2),
        "btm_sb": fold(3, layer=2),
        "btm_front": fold(3, layer=1),
        "top_side_a": fold(4, layer=1),
        "top_side_b": fold(4, layer=1),
        "top_back": fold(5),
        "top_front": fold(5),
    }
    return FoldPlan("front", moves, steps=5, open_step=4, pose_step=2, ground_step=2)


def _gable_plan(dl: Dieline) -> FoldPlan:
    """山形手提盒：盒身一圈 → 扣底 → 端翼向内斜 → 屋顶板斜上、手提板反折竖直立起。"""
    roof = 90.0 - float(dl.style_meta["fold"]["roof_deg"])
    lean = float(dl.style_meta["fold"]["lean_deg"])
    moves = _ring_body()
    moves |= {
        "btm_back": fold(3),
        "btm_sa": fold(4, layer=2),
        "btm_sb": fold(4, layer=2),
        "btm_front": fold(5, layer=1),
        "end_a": fold(6, lean),
        "end_b": fold(6, lean),
        "roof_back": fold(7, roof),
        "roof_front": fold(7, roof),
        "handle_back": fold(7, -roof),
        "handle_front": fold(7, -roof),
    }
    return FoldPlan("front", moves, steps=7, open_step=5, pose_step=2, ground_step=2)


def _carton_plan(dl: Dieline) -> FoldPlan:
    """组合彩盒：按选的顶、底拼折法（先底后顶，每一端用原盒型同一套折法）。"""
    from pacxio.styles.components.folds import action, ring, tuck_end

    combo = dl.style_meta["carton"]
    top, bottom = combo["top"], combo["bottom"]
    acts = ring()
    if bottom in ("tuck", "tuck_same"):
        acts |= tuck_end("btm", 3)
        first = 5
    elif bottom == "snaplock":
        acts |= {"btm_back": action(3), "btm_sa": action(4, layer=2),
                 "btm_sb": action(4, layer=2), "btm_front": action(5, layer=1)}  # fmt: skip
        first = 6
    elif bottom == "autolock":
        acts |= {"btm_back": action(3), "btm_sa": action(3, layer=2),
                 "btm_sb": action(3, layer=2), "btm_front": action(3, layer=1)}  # fmt: skip
        first = 4
    else:  # seal
        acts |= {"btm_side_a": action(3, layer=2), "btm_side_b": action(3, layer=3),
                 "btm_back": action(4, layer=1), "btm_front": action(5)}  # fmt: skip
        first = 6
    if top in ("tuck", "hanger"):
        acts |= tuck_end("top", first)
        last = first + 1
    elif top == "seal":
        acts |= {"top_side_a": action(first, layer=2), "top_side_b": action(first, layer=3),
                 "top_back": action(first + 1, layer=1), "top_front": action(first + 2)}  # fmt: skip
        last = first + 2
    else:  # gable
        roof = 90.0 - float(dl.style_meta["fold"]["roof_deg"])
        lean = float(dl.style_meta["fold"]["lean_deg"])
        acts |= {"end_a": action(first, lean), "end_b": action(first, lean),
                 "roof_back": action(first + 1, roof), "roof_front": action(first + 1, roof),
                 "handle_back": action(first + 1, -roof), "handle_front": action(first + 1, -roof)}  # fmt: skip
        last = first + 1
    moves = {pid: Move(a.keys, a.layer) for pid, a in acts.items()}
    if top == "hanger":
        moves["hanger"] = Move(())
    return FoldPlan("front", moves, last, first - 1, 2, 2)


def _counter_plan(dl: Dieline) -> FoldPlan:
    """柜台展示盒：耳朵 → 前后板立起 → 侧外壁 → 卷边 + 内壁翻进去夹住耳朵 → 头卡顶翻过来贴后板里面下到底。"""
    moves = {ear: fold(1) for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb")}
    moves |= {"front": fold(2), "back": fold(2), "side_a": fold(3), "side_b": fold(3)}
    moves |= {"rim_a": fold(4), "rim_b": fold(4), "side_a_in": fold(4), "side_b_in": fold(4)}
    moves |= {"header_rim": fold(5), "header_front": fold(5)}
    return FoldPlan("base", moves, steps=5, open_step=5, pose_step=1, ground_step=1)


def _mailer_plan(dl: Dieline) -> FoldPlan:
    """飞机盒：翻面 → 前后板带耳朵立起 → 侧板 → 卷边夹住耳朵 → 盖防尘翼、插耳 → 合盖、插舌包在前板外面。

    盒子各层的位置都由刀版尺寸决定（卷边宽就是夹层宽），不用 layer。"""
    moves = {ear: fold(2) for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb")}
    moves |= {"front": fold(3), "back": fold(3), "side_a": fold(4), "side_b": fold(4)}
    moves |= {"rim_a": fold(5), "rim_b": fold(5), "side_a_in": fold(5), "side_b_in": fold(5)}
    moves |= {"dust_la": fold(6), "dust_lb": fold(6), "tuck_ear_a": fold(6), "tuck_ear_b": fold(6)}
    moves |= {"top_lid": fold(7), "top_tuck": Move(((6, 45.0), (7, 90.0)))}
    return FoldPlan("base", moves, steps=7, open_step=5, pose_step=1, ground_step=4)


def _inner_tuck_plan(dl: Dieline) -> FoldPlan:
    """Same rolled tray, with an inner front tuck and no outer locking ears."""
    plan = _mailer_plan(dl)
    moves = {
        pid: move for pid, move in plan.moves.items() if pid not in {"tuck_ear_a", "tuck_ear_b"}
    }
    return FoldPlan("base", moves, steps=7, open_step=5, pose_step=1, ground_step=4)


def _closure_mailer_plan(dl: Dieline) -> FoldPlan:
    plan = _inner_tuck_plan(dl)
    moves = dict(plan.moves)
    if dl.style == "mailer_front_lock":
        moves |= {"lock_tab_a": fold(8), "lock_tab_b": fold(8)}
    return FoldPlan(
        "base",
        moves,
        steps=8 if dl.style == "mailer_front_lock" else 7,
        open_step=5,
        pose_step=1,
        ground_step=4,
    )


def _tray_plan(dl: Dieline) -> FoldPlan:
    """天地盖的地盒、天盖：卷边只有两层纸厚，耳朵夹在中间时内侧板要再让一层纸厚。"""
    root = "top" if dl.style_meta.get("part") == "lid" else "bottom"
    moves = {ear: fold(2) for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb")}
    moves |= {"front": fold(3), "back": fold(3), "side_a": fold(4), "side_b": fold(4)}
    moves |= {"rim_a": fold(5), "rim_b": fold(5)}
    moves |= {"side_a_in": fold(5, layer=-1), "side_b_in": fold(5, layer=-1)}
    return FoldPlan(root, moves, steps=5, open_step=5, pose_step=1, ground_step=4)


def _drawer_plan(dl: Dieline) -> FoldPlan:
    """抽屉盒：内托同托盘，耳朵挂在侧板上、卷边在前后板上；外套围成筒，糊口在顶盖对面的侧板里面。"""
    if dl.style_meta.get("layout") == "sleeve":
        moves = {"glue": fold(2, layer=1), "side_a": fold(3), "top": fold(3), "side_b": fold(3)}
        return FoldPlan("bottom", moves, steps=3, open_step=3, pose_step=1, ground_step=3)
    moves = {ear: fold(2) for ear in ("ear_a_back", "ear_a_front", "ear_b_back", "ear_b_front")}
    moves |= {"side_a": fold(3), "side_b": fold(3), "front": fold(4), "back": fold(4)}
    moves |= {"rim_b": fold(5), "rim_f": fold(5)}
    moves |= {"back_in": fold(5, layer=-1), "front_in": fold(5, layer=-1)}
    return FoldPlan("bottom", moves, steps=5, open_step=5, pose_step=1, ground_step=4)


def _regular_tray_plan(dl: Dieline) -> FoldPlan:
    moves = {"front": fold(2), "back": fold(2), "side_a": fold(4), "side_b": fold(4)}
    moves |= {p.id: fold(3, layer=1) for p in dl.panels if p.id.startswith("corner_")}
    moves |= {p.id: fold(5, 180, layer=1) for p in dl.panels if p.id.startswith("tongue_")}
    hinged = dl.style == "hinged_lid"
    if hinged:
        moves |= {
            "lid": fold(6),
            "lid_tuck": fold(7, layer=1),
            "lid_dust_a": fold(5, layer=1),
            "lid_dust_b": fold(5, layer=1),
        }
    return FoldPlan("base", moves, 7 if hinged else 5, 4 if hinged else 5, 1, 4)


def _platform_plan(dl: Dieline) -> FoldPlan:
    return FoldPlan("platform", {"front": fold(1), "back": fold(1)}, 1, 1, 1, 1)


PLANS: dict[str, Callable[[Dieline], FoldPlan]] = {
    "tray_glued": _regular_tray_plan,
    "tray_lock": _regular_tray_plan,
    "hinged_lid": _regular_tray_plan,
    "sleeve": _drawer_plan,
    "belly_band": _drawer_plan,
    "insert_card": _platform_plan,
    "divider": _flat_plan,
    "rte": _rte_plan,
    "ste": _rte_plan,
    "snaplock": _snaplock_plan,
    "autolock": _autolock_plan,
    "fefco_0201": _slotted_plan,
    "crashlock_rsc": _crashlock_plan,
    "gable": _gable_plan,
    "carton": _carton_plan,
    "counter_display": _counter_plan,
    "fefco_0427": _mailer_plan,
    "mailer_tuck": _inner_tuck_plan,
    "mailer_front_lock": _closure_mailer_plan,
    "mailer_tear_seal": _closure_mailer_plan,
    "tray_lid": _tray_plan,
    "drawer": _drawer_plan,
    "rte_hanger": _rte_plan,
    "ste_hanger": _rte_plan,
    "snaplock_hanger": _snaplock_plan,
    "autolock_hanger": _autolock_plan,
    "seal_end": _glued_plan,
    "fefco_0200": _glued_plan,
    "fefco_0202": _glued_plan,
    "fefco_0203": _glued_plan,
    "card": _flat_plan,
    "header_card": _flat_plan,
}


def plan_for(dl: Dieline) -> FoldPlan | None:
    meta = dl.style_meta.get("fold_plan")
    if meta:  # 几何引擎生成的刀版自带折法（从立体推出来的），不查表
        moves = {pid: Move(tuple((int(a), float(b)) for a, b in m["keys"]), float(m.get("layer", 0.0)))
                 for pid, m in meta["moves"].items()}  # fmt: skip
        return FoldPlan(meta["root"], moves, int(meta["steps"]), int(meta["open_step"]),
                        int(meta.get("pose_step", 2)), int(meta.get("ground_step", 2)))  # fmt: skip
    fn = PLANS.get(dl.style)
    return fn(dl) if fn else None


# ---- 网格 ------------------------------------------------------------------------------


@dataclass
class PanelMesh:
    panel: str
    vertices: list[tuple[float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    rings: list[list[tuple[float, float]]] = field(
        default_factory=list
    )  # 轮廓（外环逆时针、孔顺时针）


@dataclass(frozen=True)
class CreaseStrip:
    """压线处的过渡带：两块面各让出 half_width，中间这一条跟着折角弯。"""

    child: str
    parent: str
    origin: tuple[float, float]  # 压线起点
    along: tuple[float, float]  # 沿压线的单位向量
    to_parent: tuple[float, float]  # 面内指向父面的单位向量
    half_width: float
    crease: tuple[float, float]  # 压线上的范围（沿 along 量）
    parent_end: tuple[float, float]  # 过渡带靠父面那条边的范围
    child_end: tuple[float, float]  # 靠子面那条边的范围


def _panel_shape(dl: Dieline, panel_id: str) -> BaseGeometry:
    """面的轮廓，挖掉落在它上面的内部切口和窗口。"""
    shape: BaseGeometry = path_to_polygon(dl.panel(panel_id).polygon).buffer(0)
    holes = [  # 闭合的切口才挖洞；开口的切缝（插翼根部、共用一刀的翼边）没有面积
        GPath(e.segments, closed=True)
        for e in dl.inner_cuts()
        if e.segments[0].start.is_close(e.segments[-1].end)
    ]
    holes += [w.cut for w in dl.windows]
    for hole in holes:
        poly = path_to_polygon(hole).buffer(0)
        if poly.intersects(shape):
            shape = shape.difference(poly)
    return shape


def _across(shape: BaseGeometry, origin: tuple[float, float], normal: tuple[float, float]) -> float:
    """面沿 normal 方向离压线最远多少。"""
    coords = np.array([c for poly in polygons_of(shape) for c in poly.exterior.coords])
    return float(((coords - np.array(origin)) @ np.array(normal)).max())


def _span_at(
    shape: BaseGeometry,
    origin: tuple[float, float],
    along: tuple[float, float],
    normal: tuple[float, float],
    offset: float,
    crease: tuple[float, float],
) -> tuple[float, float]:
    """离压线 offset 处那条线在面里的范围（不超出压线本身的范围）。"""
    o = np.array(origin) + offset * np.array(normal)
    d = np.array(along)
    line = LineString([tuple(o + d * (crease[0] - 1.0)), tuple(o + d * (crease[1] + 1.0))])
    hit = shape.intersection(line)
    if hit.is_empty:
        return crease
    coords = [c for g in hit.geoms for c in g.coords] if hasattr(hit, "geoms") else list(hit.coords)
    along_at = [float((np.array(c) - np.array(origin)) @ d) for c in coords]
    lo, hi = max(crease[0], min(along_at)), min(crease[1], max(along_at))
    return (lo, hi) if hi > lo + ALONG_EPS_MM else crease


def build_meshes(part: FoldedPart, radius: float) -> tuple[list[PanelMesh], list[CreaseStrip]]:
    """每块面让出压线过渡带后的网格，以及过渡带。radius：90° 时压线外侧的圆角半径。"""
    dl = part.dl
    full = {p.id: _panel_shape(dl, p.id) for p in dl.panels}
    trimmed = dict(full)
    strips: list[CreaseStrip] = []
    edges = {e.id: e for e in dl.edges}
    for j in part.joints:
        if not j.keys or not j.keys[-1][1]:  # 不折的（自动锁底的糊胶三角）：和父面连成一片
            continue
        for edge_id in j.edges:
            seg = edges[edge_id].segments[0]
            o = seg.start.as_tuple()
            d = np.array(seg.end.as_tuple()) - np.array(o)
            length = float(np.linalg.norm(d))
            along = (float(d[0] / length), float(d[1] / length))
            normal = (-along[1], along[0])  # 面内指向父面：子面在 along 左边时指向右边
            if _side_of(full[j.child], o, along) > 0:
                normal = (along[1], -along[0])
            away = (-normal[0], -normal[1])
            half = min(
                radius,
                BAND_MAX_RATIO * _across(full[j.parent], o, normal),
                BAND_MAX_RATIO * _across(full[j.child], o, away),
            )
            crease = (0.0, length)
            strips.append(
                CreaseStrip(
                    j.child,
                    j.parent,
                    o,
                    along,
                    normal,
                    half,
                    crease,
                    _span_at(full[j.parent], o, along, normal, half, crease),
                    _span_at(full[j.child], o, along, away, half, crease),
                )  # fmt: skip
            )
            trimmed[j.parent] = trimmed[j.parent].difference(_band(o, along, normal, half, length))
            trimmed[j.child] = trimmed[j.child].difference(_band(o, along, away, half, length))
    return [_mesh(pid, shape) for pid, shape in trimmed.items()], strips


def _band(
    o: tuple[float, float], along: tuple[float, float], normal: tuple[float, float], width: float,
    length: float,
) -> Polygon:  # fmt: skip
    a, n = np.array(along), np.array(normal)
    p = np.array(o)
    corners = (p, p + a * length, p + a * length + n * width, p + n * width)
    return Polygon([tuple(c) for c in corners])


def _mesh(panel: str, shape: BaseGeometry) -> PanelMesh:
    import shapely

    mesh = PanelMesh(panel)
    index: dict[tuple[float, float], int] = {}

    def vid(c: tuple[float, ...]) -> int:
        key = (round(float(c[0]), 4), round(float(c[1]), 4))
        if key not in index:
            index[key] = len(mesh.vertices)
            mesh.vertices.append(key)
        return index[key]

    for poly in polygons_of(shape):
        if poly.area < MIN_AREA_MM2:
            continue
        poly = orient(poly, 1.0)
        mesh.rings.append([(round(x, 4), round(y, 4)) for x, y in poly.exterior.coords[:-1]])
        mesh.rings += [
            [(round(x, 4), round(y, 4)) for x, y in r.coords[:-1]] for r in poly.interiors
        ]
        for tri in polygons_of(shapely.constrained_delaunay_triangles(poly)):
            c = list(tri.exterior.coords)
            mesh.triangles.append((vid(c[0]), vid(c[1]), vid(c[2])))
    return mesh


def mesh_area(mesh: PanelMesh) -> float:
    total = 0.0
    for a, b, c in mesh.triangles:
        ax, ay = mesh.vertices[a]
        bx, by = mesh.vertices[b]
        cx, cy = mesh.vertices[c]
        total += abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / 2
    return total


def part_bounds(part: FoldedPart, pose: Mat, s: float) -> tuple[Mat, Mat]:
    """进度 s 时印刷面轮廓的包围盒（给测试和查看器取景用）。"""
    mats = panel_matrices(part, s)
    pts = []
    for p in part.dl.panels:
        x0, y0, x1, y1 = p.polygon.bbox()
        for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            pts.append(apply(pose @ mats[p.id], u, v))
    arr = np.array(pts)
    return arr.min(axis=0), arr.max(axis=0)


__all__ = [
    "CreaseStrip",
    "FoldPlan",
    "FoldedPart",
    "Joint",
    "Move",
    "PanelMesh",
    "angle_at",
    "apply",
    "build_meshes",
    "build_part",
    "closed_pose",
    "ease",
    "face_pairs",
    "fit_pose",
    "fold",
    "mesh_area",
    "panel_matrices",
    "part_bounds",
    "plan_for",
]
