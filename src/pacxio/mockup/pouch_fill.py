"""软袋装满后的样子（ 节第二步）与膜的变形量（PO05 用）。

做法（L0 近似，数值【初值】）：
- 每面在封边以内往外鼓：截面 sin(πt)^p，封边处为 0、中间最高；鼓起量 fill（0–1）× puff_k × 内容区短边。
  自立袋越往下越鼓，底褶撑开成底（半深 = fill × base_k × 底褶深），袋子立在底上；背封袋带风琴时，
  两侧风琴按折半的两片撑开（中间那道折往里收）。
- 膜不能伸长：每一行、每一列按弧长把点往里收（鼓得越高、两边收得越多，所以袋子装满后腰会收进去）。
  行、列各自收完以后两个方向耦合不上的部分（双曲面）就是膜要变形的量——实物上表现为褶皱、图文扭曲，
  集中在封边转角处。PO05 拿这个量查小字和条码。
- 图文按刀版上的位置一格一格贴上去（每格一张小贴图），所以贴图和膜的面积对应、不会被"拉开"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from pacxio.export.canvas import _Map
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.fold import BoxModel, Face
from pacxio.mockup.pouch import FILM_RGB, crimp_art, face_rect
from pacxio.mockup.render import AMBIENT, FILL_LIGHT, FILL_WEIGHT, KEY_LIGHT, KEY_WEIGHT
from pacxio.model.dieline import Dieline
from pacxio.model.job import Job
from pacxio.model.profile import PouchRules

PROFILE_P = 1.0  # 鼓起截面 sin(πt)^p：p < 1 封边处无限陡，膜做不到（PO05 的变形量会随网格变），用 1
EPS_MM = 0.15  # 封边处正、背两层膜之间（只为不闪烁）
FIN_GAP_MM = 0.3  # 背封中缝贴在背面上，往外让一点
STANDUP_RISE = 0.3  # 自立袋顶封往下多长一段（× 袋高）鼓到位
STANDUP_FALL = 0.45  # 底部撑开的影响往上到多高（× 袋高）
GRID = (24, 32)  # 静态效果图每面切多少格（横 × 竖）
SCENE_GRID = (10, 14)  # 工作台 3D 的粗网格（每格两个三角形、各一张小贴图）
STRAIN_STEP_MM = 2.0  # PO05 算变形量的网格
TEX_PX_PER_MM = 6.0
BASE_ROWS = 4
GUSSET_REACH = 2.0  # 背封袋侧风琴从上下封往里多长（× 褶深）撑到位

Vec3 = tuple[float, float, float]


@dataclass
class Sheet:
    """一张膜（正面 / 背面 / 侧风琴 / 中缝 / 底）：网格点的三维位置和刀版上的位置。

    pos、flat 都是 (行, 列, ·)：行从上到下，列从左到右（从袋子外面看）。flat 为 None 的不印刷（底）。"""

    key: str
    label: str
    group: int
    pos: np.ndarray
    flat: np.ndarray | None
    rgb: tuple[int, int, int] = (230, 230, 228)


def pouch_fill(job: Job | None, rules: PouchRules) -> float:
    fill = job.pouch.fill if job is not None and job.pouch is not None else None
    return rules.fill_default if fill is None else float(fill)


def _bump(t: np.ndarray) -> np.ndarray:
    return np.asarray(np.sin(np.pi * np.clip(t, 0.0, 1.0)) ** PROFILE_P)


def _pull(coord: np.ndarray, z: np.ndarray, axis: int, anchor: int) -> np.ndarray:
    """膜不伸长：沿 axis 每一段在平面上的投影 = √(原长² − 高差²)；anchor 那一格位置不动。"""
    dc = np.diff(coord, axis=axis)
    dz = np.diff(z, axis=axis)
    step = np.sign(dc) * np.sqrt(np.maximum(dc**2 - dz**2, 0.0))
    first = np.take(coord, [0], axis=axis)
    out = np.concatenate([first, first + np.cumsum(step, axis=axis)], axis=axis)
    shift = np.take(coord, [anchor], axis=axis) - np.take(out, [anchor], axis=axis)
    return np.asarray(out + shift)


def _grid(w: float, h: float, nu: int, nv: int) -> tuple[np.ndarray, np.ndarray]:
    """面局部坐标网格：x 从左到右 0..w，y（向上）从上到下 h..0。"""
    return np.meshgrid(np.linspace(0.0, w, nu + 1), np.linspace(h, 0.0, nv + 1))


def _panel_x0(dl: Dieline, pid: str) -> float:
    return float(dl.panel(pid).polygon.bbox()[0])


def filled_sheets(
    dl: Dieline,
    fill: float,
    rules: PouchRules,
    grid: tuple[int, int] = GRID,
    open_gusset: bool = True,
) -> list[Sheet]:
    """装满后的各张膜；坐标 mm，原点在包围盒左前下角，z 向上，正面朝 -y。"""
    info = dl.style_meta["pouch"]
    nu, nv = grid
    nu += nu % 2  # 背封袋背面在中缝处分两半：中缝落在网格线上
    if info["kind"] in ("pouch_backseal", "pouch_flat_bottom"):
        sheets = _backseal(dl, info, fill, rules, nu, nv, open_gusset)
    else:
        sheets = _two_face(dl, info, fill, rules, nu, nv)
    lo = np.min([s.pos.reshape(-1, 3).min(axis=0) for s in sheets], axis=0)
    for s in sheets:
        s.pos = s.pos - lo
    return sheets


def _two_face(
    dl: Dieline, info: dict[str, Any], fill: float, rules: PouchRules, nu: int, nv: int
) -> list[Sheet]:
    w, h, s = float(info["width"]), float(info["height"]), float(info["seal"])
    standup = info["kind"] == "pouch_standup"
    x0, y0 = _grid(w, h, nu, nv)
    a = _bump((x0 - s) / (w - 2 * s))
    t = fill * rules.puff_k * min(w - 2 * s, h - 2 * s)
    if standup:
        g = float(info.get("gusset") or 0.0)
        rise = np.sin(np.pi / 2 * np.clip((h - s - y0) / (STANDUP_RISE * h), 0.0, 1.0)) ** PROFILE_P
        base = fill * rules.base_k * g
        fall = np.clip(1 - y0 / (STANDUP_FALL * h), 0.0, 1.0) ** 2
        z = a * rise * (t + (base - t) * fall)
    else:
        z = t * a * _bump((y0 - s) / (h - 2 * s))
    if info.get("shape") == "custom":  # 异形：按离封边内沿的距离鼓（外面是废边，不鼓）
        z = _shape_bulge(dl, x0, y0, s, fill, rules)
    x = _pull(x0, z, axis=1, anchor=nu // 2)
    v = _pull(y0, z, axis=0, anchor=nv if standup else nv // 2)
    front = np.stack([x, -(EPS_MM + z), v], axis=-1)
    back = np.stack([x, EPS_MM + z, v], axis=-1)[:, ::-1]  # 从背后看：左边是正面的右边
    fx, bx = _panel_x0(dl, "front"), _panel_x0(dl, "back")
    sheets = [
        Sheet("front", "正面", 0, front, np.stack([fx + x0, y0], axis=-1)),
        Sheet("back", "背面", 1, back, np.stack([bx + x0, y0], axis=-1)),
    ]
    if standup:  # 底：正、背两面的下沿之间撑开（不印刷，按膜的本色）
        k = np.linspace(0.0, 1.0, BASE_ROWS + 1)[:, None, None]
        pos = front[-1][None] * (1 - k) + back[-1, ::-1][None] * k
        sheets.append(Sheet("base", "底", 2, pos, None))
    if info.get("spout"):
        sheets += _spout(front, back, x0, y0, info, rules, 24 if nu >= 20 else 12)
    return sheets


def _shape_bulge(
    dl: Dieline, x0: np.ndarray, y0: np.ndarray, s: float, fill: float, rules: PouchRules
) -> np.ndarray:
    """异形小袋：封边内沿以内按离内沿的距离鼓起 sin(π/2 · d / d_max)，封边和外面的废边不鼓。"""
    from shapely import contains_xy, distance
    from shapely.geometry import Point

    cut = next(e for e in dl.inner_cuts() if e.id == "cut_shape_front")
    inner = path_to_polygon(GPath(cut.segments, closed=True)).buffer(-s, join_style="mitre")
    if inner.is_empty:
        return np.zeros_like(x0)
    fx = _panel_x0(dl, "front")
    px, py = fx + x0, y0
    inside = contains_xy(inner, px, py)
    dist = np.where(inside, distance(inner.exterior, [Point(a, b) for a, b in zip(px.ravel(), py.ravel(), strict=True)]
                                     ).reshape(px.shape), 0.0)  # fmt: skip
    dmax = max(float(dist.max()), 1e-6)
    t = (
        fill * rules.puff_k * 2 * dmax
    )  # 同方袋：鼓起 ≈ puff_k × 内容区短边（短边 ≈ 2 × 最大内切距离）
    return np.asarray(t * np.sin(np.pi / 2 * np.clip(dist / dmax, 0.0, 1.0)))


SPOUT_RGB = ((236, 236, 232), (214, 216, 218))  # 嘴、盖


def _spout(front: np.ndarray, back: np.ndarray, x0: np.ndarray, y0: np.ndarray, info: dict[str, Any],
           rules: PouchRules, segments: int) -> list[Sheet]:  # fmt: skip
    """吸嘴：嘴管 + 盖（圆柱），装在袋口正中或右上斜角；轴线朝外。"""
    sp = info["spout"]
    d = float(sp["d"])
    lx, ly = float(sp["at"][0]), float(sp["at"][1])
    nv, nu = front.shape[0] - 1, front.shape[1] - 1
    w, h = float(x0[0, -1]), float(y0[0, 0])
    fi, fj = min(lx / w * nu, nu - 1e-6), min((h - ly) / h * nv, nv - 1e-6)
    i, j = int(fi), int(fj)
    a, c = fi - i, fj - j

    def at(g: np.ndarray) -> np.ndarray:
        return np.asarray(g[j, i] * (1 - a) * (1 - c) + g[j, i + 1] * a * (1 - c)
                          + g[j + 1, i] * (1 - a) * c + g[j + 1, i + 1] * a * c)  # fmt: skip

    mid = (at(front) + at(back[:, ::-1])) / 2  # 正背两层中间
    ang = math.radians(float(sp["angle"]))
    axis = np.array([math.sin(ang), 0.0, math.cos(ang)])  # 0° 朝上；45° 朝右上
    e1 = np.array([math.cos(ang), 0.0, -math.sin(ang)])
    e2 = np.array([0.0, 1.0, 0.0])
    base = mid - axis * rules.spout_base_h_mm * 0.4  # 底座一部分焊在袋口里
    parts = [(d / 2 + 1.5, 0.0, rules.spout_base_h_mm * 0.4 + 8.0, SPOUT_RGB[0]),
             (d / 2 + 5.0, rules.spout_base_h_mm * 0.4 + 8.0, rules.spout_base_h_mm * 0.4 + 22.0, SPOUT_RGB[1])]  # fmt: skip
    theta = np.linspace(0.0, 2 * np.pi, segments + 1)
    out = []
    for k, (r, z0, z1, rgb) in enumerate(parts):
        zz = np.linspace(z1, z0, 3)[:, None, None]  # 行：上 → 下
        ring = np.cos(theta)[None, :, None] * e1 + np.sin(theta)[None, :, None] * e2
        pos = base + axis * zz + r * ring
        tl, tr, bl = pos[0, 0], pos[0, 1], pos[1, 0]
        if np.cross(bl - tl, tr - tl) @ (tl - (base + axis * zz[0, 0, 0])) < 0:
            pos = pos[:, ::-1]
        out.append(Sheet(f"spout_{k}", "吸嘴", 7, pos, None, rgb))
        top = base + axis * z1  # 顶面：圆片
        rr = np.linspace(r, 0.0, 3)[:, None, None]
        disc = top + rr * ring
        tl, tr, bl = disc[0, 0], disc[0, 1], disc[1, 0]
        if np.cross(bl - tl, tr - tl) @ axis < 0:
            disc = disc[:, ::-1]
        out.append(Sheet(f"spout_{k}_top", "吸嘴", 7, disc, None, rgb))
    return out


def _backseal(
    dl: Dieline,
    info: dict[str, Any],
    fill: float,
    rules: PouchRules,
    nu: int,
    nv: int,
    open_gusset: bool = True,
) -> list[Sheet]:
    w, h = float(info["width"]), float(info["height"])
    end, fin, g = float(info["seal"]), float(info["fin"]), float(info.get("gusset") or 0.0)
    flat_bottom = info["kind"] == "pouch_flat_bottom"
    low = float(info["bottom_seal"]) if flat_bottom else end  # 下沿封宽
    x0, y0 = _grid(w, h, nu, nv)
    b = _bump((y0 - low) / (h - end - low))
    t = fill * rules.puff_k * min(w, h - end - low)
    # 侧风琴撑开的半深（没有风琴为 0）：离上下封 GUSSET_REACH 个褶深的范围里撑开（风琴的三角折角），中间一样深。
    # 平底袋：底片把下面撑到整个底深（半深 = 风琴面宽的一半），只有顶上折进去
    span = max(GUSSET_REACH * g, 1e-9)
    if flat_bottom:
        side = g * np.sin(np.pi / 2 * np.clip((h - end - y0) / span, 0.0, 1.0))
    else:
        reach = np.minimum((y0 - end) / span, (h - end - y0) / span)
        side = fill * rules.base_k * g * np.sin(np.pi / 2 * np.clip(reach, 0.0, 1.0))
    bulge = t * _bump(x0 / w) * b
    # 风琴撑开只让正 / 背面整片斜出去（沿高度方向的柱面，可展、膜不变形）：先按它沿高度量出一条
    # 基准线 (高, 外移)；鼓起再往外加，按鼓起各自收进来的弧长到基准线上取位置。
    # 风琴三角折角处正面和鼓起的耦合不算进变形量（实物上折进去的是风琴，见 strain_map）
    if not open_gusset:
        side = np.zeros_like(side)
    ys, sd = y0[::-1, 0], side[::-1, 0]  # 从下往上
    vs = _pull(ys, sd, axis=0, anchor=0 if flat_bottom else ys.size // 2)  # 平底袋立在底上
    eta = _pull(y0, bulge, axis=0, anchor=nv if flat_bottom else nv // 2)
    v = np.interp(eta, ys, vs)
    z = np.interp(eta, ys, sd) + bulge
    x = _pull(x0, bulge, axis=1, anchor=nu // 2)
    front = np.stack([x, -(EPS_MM + z), v], axis=-1)
    back = np.stack([x, EPS_MM + z, v], axis=-1)[:, ::-1]
    fx = face_rect(dl, "front")[0]
    if (
        info.get("seam") == "corner"
    ):  # 四边封 / 八边封：背面一整片，接缝在正面左角（不画那条小封边）
        bx = _panel_x0(dl, "back")
        sheets = [
            Sheet("front", "正面", 0, front, np.stack([fx + x0, y0], axis=-1)),
            Sheet("back", "背面", 1, back, np.stack([bx + x0, y0], axis=-1)),
        ]
    else:
        # 背面：左半是 back_b（紧挨正面右边的折边），右半是 back_a；两边各去掉中缝封那一条。
        # 两半在刀版上不相连，分成两张（中缝那列点两张都有）
        xb, xa = _panel_x0(dl, "back_b"), _panel_x0(dl, "back_a")
        m = nu // 2
        sheets = [
            Sheet("front", "正面", 0, front, np.stack([fx + x0, y0], axis=-1)),
            Sheet("back", "背面", 1, back[:, : m + 1], np.stack([xb + x0[:, : m + 1], y0[:, : m + 1]], axis=-1)),
            Sheet("back_a", "背面", 1, back[:, m:],
                  np.stack([xa + fin + x0[:, m:] - w / 2, y0[:, m:]], axis=-1)),
        ]  # fmt: skip
        # 中缝：两层封在一起，倒向 back_a 那边贴在背面上；露在外面的是 back_b 那条的印刷面
        kf = np.linspace(0.0, 1.0, 5)
        xs_back = w / 2 + kf * fin  # 背面参数（从背后看从左到右）
        pts = np.empty((nv + 1, kf.size, 3))
        for j in range(nv + 1):
            cols = w - xs_back  # 背面参数 ξ 对应正面网格的 x = w − ξ
            pts[j, :, 0] = np.interp(cols, x0[j], x[j])
            pts[j, :, 1] = EPS_MM + FIN_GAP_MM + np.interp(cols, x0[j], z[j])
            pts[j, :, 2] = np.interp(cols, x0[j], v[j])
        fin_flat = np.stack(np.broadcast_arrays(xb + w / 2 + kf[None, :] * fin, y0[:, :1]), axis=-1)
        sheets.append(Sheet("fin", "中缝", 3, pts, fin_flat))
    if g > 0:
        q = np.linspace(0.0, 1.0, 9 if nu >= 20 else 5)  # 工作台粗网格少切几格
        apex = 1 - np.abs(2 * q - 1)
        for key, label, col, sign, pid in (("side_a", "左侧风琴", 0, 1.0, "side_a"),
                                           ("side_b", "右侧风琴", nu, -1.0, "side_b")):  # fmt: skip
            dd = (EPS_MM + z[:, col])[:, None]
            tuck = np.sqrt(np.maximum(g**2 - dd**2, 0.0))  # 风琴中间那道折往里收
            # side_a 从外面（左边）看：左边是背面那条边；side_b 从右边看：左边是正面那条边
            ys = dd * (1 - 2 * q[None, :]) if key == "side_a" else -dd * (1 - 2 * q[None, :])
            px = x[:, col][:, None] + sign * tuck * apex[None, :]
            pos = np.stack(np.broadcast_arrays(px, ys, v[:, col][:, None]), axis=-1)
            flat = np.stack(
                np.broadcast_arrays(_panel_x0(dl, pid) + q[None, :] * 2 * g, y0[:, :1]), axis=-1
            )
            sheets.append(Sheet(key, label, 4 if key == "side_a" else 5, pos, flat))
    if flat_bottom:
        depth = float(info["depth"])
        k = np.linspace(0.0, 1.0, BASE_ROWS + 1)[:, None, None]  # 从正面那边到背面那边
        pos = front[-1][None] * (1 - k) + back[-1, ::-1][None] * k
        bx = _panel_x0(dl, "bottom")
        flat = np.stack(np.broadcast_arrays(bx + x0[-1][None, :], k[..., 0] * depth), axis=-1)
        sheets.append(Sheet("bottom", "底面", 2, pos, flat))
        if info.get("valve"):
            sheets += _valve(front, x0, y0, fx, info, 24 if nu >= 20 else 12)
    return sheets


VALVE_RGB = ((78, 78, 82), (156, 158, 162))  # 外圈、中间


def _valve(
    front: np.ndarray,
    x0: np.ndarray,
    y0: np.ndarray,
    fx: float,
    info: dict[str, Any],
    segments: int = 24,
) -> list[Sheet]:
    """排气阀：贴在正面上的一个小圆片（外圈深、中间浅），略高出膜面。"""
    vx, vy = float(info["valve"][0]) - fx, float(info["valve"][1])
    r = float(info["valve_r"])
    nv, nu = front.shape[0] - 1, front.shape[1] - 1
    w, h = float(x0[0, -1]), float(y0[0, 0])
    fi, fj = vx / w * nu, (h - vy) / h * nv
    i, j = min(int(fi), nu - 1), min(int(fj), nv - 1)
    a, c = fi - i, fj - j
    p = (front[j, i] * (1 - a) * (1 - c) + front[j, i + 1] * a * (1 - c)
         + front[j + 1, i] * (1 - a) * c + front[j + 1, i + 1] * a * c)  # fmt: skip
    ex = front[j, i + 1] - front[j, i]
    ez = front[j, i] - front[j + 1, i]
    n = np.cross(-ez, ex)  # 同 Face 法向：cross(向下, 向右)
    n, ex = n / np.linalg.norm(n), ex / np.linalg.norm(ex)
    ez = np.cross(ex, n)  # 面内向上
    ez = -ez if ez[2] < 0 else ez
    theta = np.linspace(0.0, 2 * np.pi, segments + 1)
    out = []
    for key, (r0, r1), lift, rgb in (("valve_ring", (0.55 * r, r), 0.8, VALVE_RGB[0]),
                                     ("valve_cap", (0.0, 0.55 * r), 1.2, VALVE_RGB[1])):  # fmt: skip
        rr = np.linspace(r1, r0, 3)[:, None, None]  # 行：外 → 里
        dirs = np.cos(theta)[None, :, None] * ex + np.sin(theta)[None, :, None] * ez
        pos = p + n * lift + rr * dirs
        tl, tr, bl = pos[0, 0], pos[0, 1], pos[1, 0]
        if np.cross(bl - tl, tr - tl) @ n < 0:
            pos = pos[:, ::-1]
        out.append(Sheet(key, "排气阀", 6, pos, None, rgb))
    return out


# ---------------------------------------------------------------------------
# 变形量（PO05）
# ---------------------------------------------------------------------------


def strain(sheet: Sheet) -> tuple[np.ndarray, np.ndarray]:
    """每格膜要变形多少（主伸长偏离 1 的最大值），和格子中心在刀版上的位置 (行, 列, 2)。"""
    assert sheet.flat is not None
    p, f = sheet.pos, sheet.flat
    fx = np.linalg.norm(f[:-1, 1:] - f[:-1, :-1], axis=-1)
    fy = np.linalg.norm(f[1:, :-1] - f[:-1, :-1], axis=-1)
    ex = (p[:-1, 1:] - p[:-1, :-1]) / np.maximum(fx, 1e-9)[..., None]
    ey = (p[1:, :-1] - p[:-1, :-1]) / np.maximum(fy, 1e-9)[..., None]
    g11, g22, g12 = (ex * ex).sum(-1), (ey * ey).sum(-1), (ex * ey).sum(-1)
    half, det = (g11 + g22) / 2, g11 * g22 - g12**2
    root = np.sqrt(np.maximum(half**2 - det, 0.0))
    lmax, lmin = np.sqrt(half + root), np.sqrt(np.maximum(half - root, 0.0))
    centre = (f[:-1, :-1] + f[:-1, 1:] + f[1:, :-1] + f[1:, 1:]) / 4
    return np.maximum(lmax - 1, 1 - lmin), centre


def strain_map(dl: Dieline, fill: float, rules: PouchRules) -> list[tuple[np.ndarray, np.ndarray]]:
    """印刷的各张膜：(变形量, 格子中心)；网格约 STRAIN_STEP_MM。"""
    info = dl.style_meta["pouch"]
    w, h = float(info["width"]), float(info["height"])
    grid = (max(8, math.ceil(w / STRAIN_STEP_MM)), max(8, math.ceil(h / STRAIN_STEP_MM)))
    full = filled_sheets(dl, fill, rules, grid)
    # 正 / 背面按风琴不撑开算：风琴撑开时正面整片斜出去（可展），近似模型里它和鼓起的耦合是假的变形；
    # 风琴本身（side_a / side_b）照撑开算，三角折角处的变形在那里
    flat_faces = {s.key: s for s in filled_sheets(dl, fill, rules, grid, open_gusset=False)}
    faces = ("front", "back", "back_a", "fin")
    sheets = [flat_faces[s.key] if s.key in faces else s for s in full]
    return [strain(s) for s in sheets if s.flat is not None]


# ---------------------------------------------------------------------------
# 效果图
# ---------------------------------------------------------------------------


def _shade(n: np.ndarray) -> np.ndarray:
    def unit(v: tuple[float, float, float]) -> np.ndarray:
        a = np.array(v, dtype=np.float64)
        return np.asarray(a / np.linalg.norm(a))

    key = np.maximum(0.0, n @ unit(KEY_LIGHT))
    fill = np.maximum(0.0, n @ unit(FILL_LIGHT))
    return np.asarray(np.minimum(1.0, AMBIENT + KEY_WEIGHT * key + FILL_WEIGHT * fill))


def _vertex_normals(p: np.ndarray) -> np.ndarray:
    du = np.gradient(p, axis=1)
    dv = np.gradient(p, axis=0)
    n = np.cross(dv, du)
    return np.asarray(n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12))


def _vec(c: np.ndarray) -> Vec3:
    return (float(c[0]), float(c[1]), float(c[2]))


def _tri_face(
    a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> tuple[tuple[Vec3, Vec3, Vec3, Vec3], np.ndarray, np.ndarray, np.ndarray, float, float]:
    """三角形 (a, b, c)（cross(c − a, b − a) 朝外）在它平面里的包围矩形：renderer 只画矩形面。

    返回 (四角 左上 / 右上 / 右下 / 左下, 外法向, 矩形左上角, 三个顶点在矩形 (e1, e2) 坐标里的位置, 宽, 高)。"""
    e1 = (b - a) / np.linalg.norm(b - a)
    side = c - a
    e2 = side - (side @ e1) * e1
    e2 = e2 / np.linalg.norm(e2)
    rel = np.stack([a, b, c]) - a
    uv = np.stack([rel @ e1, rel @ e2], axis=1)
    lo, hi = uv.min(axis=0), uv.max(axis=0)
    origin = a + lo[0] * e1 + lo[1] * e2
    w, h = float(hi[0] - lo[0]), float(hi[1] - lo[1])
    du, dv = w * e1, h * e2
    n = np.cross(dv, du)
    n = n / np.linalg.norm(n)
    corners = (_vec(origin), _vec(origin + du), _vec(origin + du + dv), _vec(origin + dv))
    return corners, n, origin, uv - lo, w, h


def _bary(uv: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """矩形坐标 (x, y) 处对三角形三个顶点的重心坐标 (…, 3)。"""
    (x1, y1), (x2, y2), (x3, y3) = uv
    det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    l1 = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / det
    l2 = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / det
    return np.stack([l1, l2, 1 - l1 - l2], axis=-1)


def filled_scene(dl: Dieline, art: Image.Image, mapping: _Map, fill: float, rules: PouchRules,
                 grid: tuple[int, int] = GRID, bake_shading: bool = True, film: str | None = None) -> tuple[BoxModel, dict[str, Image.Image]]:  # fmt: skip
    """装满的袋子：每格两个三角形，各用一张矩形小平面 + 三角形以外透明的小贴图。bake_shading：
    把格内的光滑明暗烘进贴图（静态图用；工作台自己打光，不烘）。"""
    sheets = filled_sheets(dl, fill, rules, grid)
    for sh in sheets:
        if sh.key == "base":  # 自立袋的底不印刷，按膜的本色
            sh.rgb = FILM_RGB.get(film or "white", FILM_RGB["white"])
    src = np.asarray(crimp_art(art, mapping, dl).convert("RGBA"))
    ih, iw = src.shape[:2]
    faces: list[Face] = []
    tex: dict[str, Image.Image] = {}
    groups: list[int] = []
    pad_mm = 1.5 / TEX_PX_PER_MM  # 三角形边往外多留一点，相邻两片之间不漏缝
    for sheet in sheets:
        p, f = sheet.pos, sheet.flat
        vn = _vertex_normals(p)
        rows, cols = p.shape[0] - 1, p.shape[1] - 1
        for j in range(rows):
            for i in range(cols):
                # 左上半 (左上, 右上, 左下)、右下半 (右下, 左下, 右上)：从外面看都是同一个转向
                for half, idx in enumerate((((j, i), (j, i + 1), (j + 1, i)),
                                            ((j + 1, i + 1), (j + 1, i), (j, i + 1)))):  # fmt: skip
                    pa, pb, pc = (p[k] for k in idx)
                    if np.linalg.norm(np.cross(pc - pa, pb - pa)) < 1e-9:
                        continue
                    corners, n, _origin, uv, w, h = _tri_face(pa, pb, pc)
                    tw = max(3, math.ceil(w * TEX_PX_PER_MM))
                    th = max(3, math.ceil(h * TEX_PX_PER_MM))
                    xs, ys = np.meshgrid(
                        (np.arange(tw) + 0.5) / tw * w, (np.arange(th) + 0.5) / th * h
                    )
                    lam = _bary(uv, xs, ys)
                    # 每个顶点的重心坐标允许负到 −pad / 该顶点到对边的高
                    area2 = abs(
                        (uv[1, 0] - uv[0, 0]) * (uv[2, 1] - uv[0, 1])
                        - (uv[2, 0] - uv[0, 0]) * (uv[1, 1] - uv[0, 1])
                    )
                    edges = [np.linalg.norm(uv[(k + 1) % 3] - uv[(k + 2) % 3]) for k in range(3)]
                    tol = np.array([pad_mm * e / max(area2, 1e-12) for e in edges])
                    keep = (lam >= -tol).all(axis=-1)
                    lam_c = np.clip(lam, 0.0, None)
                    lam_c = lam_c / lam_c.sum(axis=-1, keepdims=True)
                    if f is None:
                        arr = np.empty((th, tw, 4), dtype=np.float32)
                        arr[..., :3] = sheet.rgb
                        arr[..., 3] = 255
                    else:
                        fa, fb, fc = (f[k] for k in idx)
                        xy = lam_c[..., :1] * fa + lam_c[..., 1:2] * fb + lam_c[..., 2:] * fc
                        px = np.clip(
                            ((xy[..., 0] - mapping.x0) * mapping.s).astype(np.int64), 0, iw - 1
                        )
                        py = np.clip(
                            ((mapping.y1 - xy[..., 1]) * mapping.s).astype(np.int64), 0, ih - 1
                        )
                        arr = src[py, px].astype(np.float32)
                    arr[..., 3] = np.where(keep, arr[..., 3], 0)
                    if bake_shading:  # 格内光滑明暗 / 整片平面明暗
                        na, nb, nc = (vn[k] for k in idx)
                        nn = lam_c[..., :1] * na + lam_c[..., 1:2] * nb + lam_c[..., 2:] * nc
                        nn = nn / np.maximum(np.linalg.norm(nn, axis=-1, keepdims=True), 1e-12)
                        k = _shade(nn) / max(float(_shade(n[None])[0]), 1e-6)
                        arr[..., :3] = np.clip(arr[..., :3] * k[..., None], 0, 255)
                    key = f"{sheet.key}_{j:02d}_{i:02d}_{half}"
                    faces.append(Face(key, sheet.key, sheet.label, _vec(n), (0, 0, 0, 0), corners))
                    tex[key] = Image.fromarray(arr.astype(np.uint8), "RGBA")
                    groups.append(sheet.group)
    hi = np.max([s.pos.reshape(-1, 3).max(axis=0) for s in sheets], axis=0)
    box = BoxModel(x=float(hi[0]), y=float(hi[1]), z=float(hi[2]), faces=faces)
    box.edge_groups = groups
    return box, tex


__all__ = ["filled_scene", "filled_sheets", "pouch_fill", "strain", "strain_map"]
