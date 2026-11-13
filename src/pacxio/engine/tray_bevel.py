"""切角 / 八角托盘（FEFCO 0439、0459、0459.1、0462、0463、0476；按示意图推读，数值【初值】）。

底板是长方形四角各切 45°（切角边长 c），或正八边形（L = W，八条边一样长）。四面直墙挂在底板的四条直边上，
四面斜墙站在底板的四条斜边上方：
- bevel_on = base：斜墙挂在底板斜边上（0439、0462、0476）
- bevel_on = long：斜墙挂在长边墙两头（同一条长片上，0459、0459.1、0463）
墙角怎么连：
- bevel_join = flap：端墙两头各一块角翼，折 135° 贴在斜墙里面（0459）
- bevel_join = wrap：斜墙再接一段，折 45° 包到端墙外面（0459.1）
- bevel_join = open：不连（0439、0462、0463、0476，靠盖 / 窄边 / 双层墙压住）
盖（lid）：八角盖挂在后墙上沿，前沿插舌插进前墙里面，可带两侧防尘翼。
刀版坐标同 tray.build（底板外包 [0, Lb] × [0, Wb]，前墙 y < 0）；立体 X = Lb − x。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from pacxio.engine.tray_kit import Kit, Rigid, rect
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import PanelRole

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray, TrayDims, TrayParams

BEVEL_H_RATIO, BEVEL_W_RATIO = 0.75, 0.2  # 切角边 c = min(0.75·墙高, 0.2·宽)【初值】
SQ2 = math.sqrt(2.0)


def bevel_size(p: TrayParams, Lb: float, Wb: float, h: float) -> float:
    if p.bevel_regular:
        return min(Lb, Wb) / (2 + SQ2)
    return p.bevel if p.bevel is not None else min(BEVEL_H_RATIO * h, BEVEL_W_RATIO * min(Lb, Wb))


def validate(p: TrayParams, d: TrayDims) -> list[Issue]:
    c = bevel_size(p, d.Lb, d.Wb, d.h)
    out: list[Issue] = []
    if p.bevel_regular and abs(d.Lb - d.Wb) > 1e-6:
        out.append(
            Issue(
                id="S03",
                message=translate("A regular octagonal tray requires L = W"),
                fix=translate("Set W to match L"),
            )
        )
    if 2 * c >= min(d.Lb, d.Wb) - 10:
        out.append(
            Issue(
                id="S03",
                message=translate("Corner cut {cut:.1f} leaves no room for straight edges", cut=c),
                fix=translate("Reduce bevel or increase L or W"),
            )
        )
    if p.bevel_join == "flap" and c - d.slot < 8:
        out.append(
            Issue(
                id="S03",
                message=translate("The corner cut is too small to fit the end-wall flaps"),
                fix=translate("Increase bevel"),
            )
        )
    if p.bevel_join != "open" and p.bevel_on != "long":
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "Corner flaps and corner wraps require the angled wall to attach to a long-side wall"
                ),
                fix="bevel_on=long",
            )
        )
    return out


def build_bevel(p: TrayParams, d: TrayDims) -> Tray:
    Lb, Wb, h, t, g = d.Lb, d.Wb, d.h, d.t, d.slot
    c = bevel_size(p, Lb, Wb, h)
    s = c * SQ2  # 斜边长
    base = [(c, 0), (Lb - c, 0), (Lb, c), (Lb, Wb - c), (Lb - c, Wb), (c, Wb), (0, Wb - c), (0, c)]
    kit = Kit.root("base", PanelRole.BOTTOM, base,
                   Rigid(np.array([Lb, 0.0, 0.0]), np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])))  # fmt: skip
    up = (0.0, 0.0, 1.0)
    kit.add("front", PanelRole.BODY, rect(c, -h, Lb - c, 0), "base", ((c, 0), (Lb - c, 0)), up, 1,
            crease_id="crease_base_front")  # fmt: skip
    kit.add("back", PanelRole.BODY, rect(c, Wb, Lb - c, Wb + h), "base", ((c, Wb), (Lb - c, Wb)), up, 1,
            crease_id="crease_base_back")  # fmt: skip
    kit.add("side_a", PanelRole.BODY, rect(Lb, c, Lb + h, Wb - c), "base", ((Lb, c), (Lb, Wb - c)), up, 1,
            crease_id="crease_base_side_a")  # fmt: skip
    kit.add("side_b", PanelRole.BODY, rect(-h, c, 0, Wb - c), "base", ((0, c), (0, Wb - c)), up, 1,
            crease_id="crease_base_side_b")  # fmt: skip
    for fb, yw, sy in (("front", 0.0, -1.0), ("back", Wb, 1.0)):
        for ab, xw, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
            pid = f"bevel_{fb}_{ab}"
            # 立体：斜墙从长边墙头 (X = c, Y = 0 一侧) 斜到端墙头 (X = 0, Y = c 一侧)
            X = -1.0 if ab == "a" else 1.0  # 立体里离开墙角的 X 方向取反
            Y = 1.0 if fb == "front" else -1.0
            if p.bevel_on == "base":
                p0, p1 = (xw - sx * c, yw), (xw, yw - sy * c)  # 底板斜边（刀版）
                nx, ny = sx / SQ2, sy / SQ2  # 往外的法向
                pts = [p0, p1, (p1[0] + nx * h, p1[1] + ny * h), (p0[0] + nx * h, p0[1] + ny * h)]
                kit.add(pid, PanelRole.BODY, pts, "base", (p0, p1), up, 1)
            else:
                xe = xw - sx * c  # 长边墙头（刀版）
                pts = rect(xe, yw, xe + sx * s, yw + sy * h)
                kit.add(
                    pid,
                    PanelRole.BODY,
                    pts,
                    fb,
                    ((xe, yw), (xe, yw + sy * h)),
                    (X / SQ2, Y / SQ2, 0),
                    2,
                )
                if p.bevel_join == "wrap":  # 斜墙再接一段，折 45° 包到端墙外面
                    xw2 = xe + sx * s
                    ww = min(c, 0.25 * Wb)
                    kit.add(f"{pid}_wrap", PanelRole.GLUE_FLAP, rect(xw2, yw + sy * g, xw2 + sx * ww, yw + sy * h),
                            pid, ((xw2, yw + sy * g), (xw2, yw + sy * h)), (0, Y, 0), 2, layer=-1.0)  # fmt: skip
            if p.bevel_join == "flap":  # 端墙头的角翼折 135° 贴在斜墙里面
                ye = yw - sy * c  # 端墙头（刀版 y）
                D = c - g
                pts = rect(xw + sx * g, ye, xw + sx * h, ye + sy * D)
                kit.add(f"corner_{fb}_{ab}", PanelRole.GLUE_FLAP, pts, f"side_{ab}", ((xw + sx * g, ye), (xw + sx * h, ye)),
                        (-X / SQ2, -Y / SQ2, 0), 2, layer=1.0)  # fmt: skip
    if p.rim is not None and p.rim_on == "long":  # 长边墙顶窄边（0439）
        v = p.rim
        for fb, y0, sy in (("front", -h, -1.0), ("back", Wb + h, 1.0)):
            kit.add(f"rim_{fb}", PanelRole.DUST_FLAP, rect(c, y0, Lb - c, y0 + sy * v), fb, ((c, y0), (Lb - c, y0)),
                    (0, -sy, 0), 3)  # fmt: skip
    kit.face("bottom", "base")
    if p.lid:
        _octagon_lid(p, kit, Lb, Wb, h, t, c)
    return kit.tray()


def _octagon_lid(
    p: TrayParams, kit: Kit, Lb: float, Wb: float, h: float, t: float, c: float
) -> None:
    from pacxio.engine.tray import LID_DUST_RATIO, LID_TUCK_CAP_MM, LID_TUCK_RATIO

    y0 = Wb + h
    pts = [(c, y0), (Lb - c, y0), (Lb, y0 + c), (Lb, y0 + Wb - c), (Lb - c, y0 + Wb), (c, y0 + Wb), (0, y0 + Wb - c),
           (0, y0 + c)]  # fmt: skip
    step = kit.last_step() + 1
    kit.add(
        "lid", PanelRole.LID, pts, "back", ((c, y0), (Lb - c, y0)), (0, -1, 0), step + 1, layer=-1.0
    )
    tk = p.tuck if p.tuck is not None else min(LID_TUCK_RATIO * h, LID_TUCK_CAP_MM)
    ti = 4 * t + 1.0
    yl = y0 + Wb
    r = min(8.0, 0.3 * tk)
    tuck = [(c + ti, yl), (c + ti, yl + tk - r), (c + ti + r, yl + tk), (Lb - c - ti - r, yl + tk), (Lb - c - ti, yl + tk - r),
            (Lb - c - ti, yl)]  # fmt: skip
    kit.add(
        "tuck",
        PanelRole.TUCK,
        tuck,
        "lid",
        ((c + ti, yl), (Lb - c - ti, yl)),
        (0, 0, -1),
        step + 1,
        layer=2.0,
    )
    if p.lid_dust:  # 盖两侧（端墙上方那两条直边）的防尘翼，折进端墙里面
        dd = LID_DUST_RATIO * h
        k = dd * math.tan(math.radians(15.0))
        e = t + 1.0
        for ab, xe, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
            ya, yb = y0 + c + e, y0 + Wb - c - e
            dpts = [(xe, ya), (xe + sx * dd, ya + k), (xe + sx * dd, yb - k), (xe, yb)]
            kit.add(
                f"dust_{ab}",
                PanelRole.DUST_FLAP,
                dpts,
                "lid",
                ((xe, ya), (xe, yb)),
                (0, 0, -1),
                step,
                layer=3.0,
            )
    kit.face("top", "lid")


def polygon_vertices(n: int, a: float) -> list[tuple[float, float]]:
    """正 n 边形（对边距 a，底边水平在 y = 0），中心在 (a/2, a/2) 附近；逆时针。"""
    e = a * math.tan(math.pi / n)
    rc = e / (2 * math.sin(math.pi / n))
    cx, cy = a / 2, a / 2 if n % 2 == 0 else rc * math.cos(math.pi / n)
    out = []
    for k in range(n):
        ang = -math.pi / 2 - math.pi / n + 2 * math.pi * k / n
        out.append((cx + rc * math.cos(ang), cy + rc * math.sin(ang)))
    y0 = min(y for _, y in out)
    return [(x, y - y0) for x, y in out]


def build_polygon(p: TrayParams, d: TrayDims) -> Tray:
    """正多边形托盘 / 盖帽（03 的八角、六角盖帽）：底板正 n 边形（对边距 = Lb），每条边一面墙；
    poly_flaps：每面墙右头一块角翼，折过外角贴进下一面墙里面（0351）。"""
    n = p.poly_sides or 8
    h, g = d.h, d.slot
    pts = polygon_vertices(n, d.Lb)
    xmax = max(x for x, _ in pts)
    kit = Kit.root("base", PanelRole.BOTTOM, pts,
                   Rigid(np.array([xmax, 0.0, 0.0]), np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])))  # fmt: skip
    walls = []
    for k in range(n):
        a, b = np.array(pts[k]), np.array(pts[(k + 1) % n])
        u = (b - a) / np.linalg.norm(b - a)
        nrm = np.array([u[1], -u[0]])  # 逆时针多边形的外法向
        wid = f"wall_{k}"
        quad = [tuple(a), tuple(b), tuple(b + h * nrm), tuple(a + h * nrm)]
        if p.poly_flaps:  # 角翼铰链下端在墙边上：把这个点也放进墙的轮廓，两块面共用同一个顶点
            quad.insert(2, tuple(b + g * nrm))
        kit.add(
            wid,
            PanelRole.BODY,
            quad,
            "base",
            (tuple(a), tuple(b)),
            (0, 0, 1),
            1,
            crease_id=f"crease_base_{wid}",
        )
        walls.append((wid, a, b, u, nrm))
    if p.poly_flaps:  # 角翼在刀版上不能伸进下一面墙：沿墙走 s ≤ r·tan(外角) − g（r 是离底边的高）
        e = float(np.linalg.norm(walls[0][2] - walls[0][1]))
        dd = 0.5 * e
        tphi = math.tan(2 * math.pi / n)
        for k, (wid, _a, b, u, nrm) in enumerate(walls):
            nxt = walls[(k + 1) % n]

            def pt(
                s: float, r: float, b: np.ndarray = b, u: np.ndarray = u, nrm: np.ndarray = nrm
            ) -> tuple[float, float]:
                q = b + s * u + r * nrm
                return (float(q[0]), float(q[1]))

            r0 = g
            local = [(0.0, r0), (0.0, h), (min(dd, h * tphi - g), h)]
            rc = (dd + g) / tphi
            if rc < h:
                local.append((dd, rc))
            local.append((max(0.0, r0 * tphi - g), r0))
            u3 = (-float(nxt[3][0]), float(nxt[3][1]), 0.0)  # 立体里下一面墙的走向（X = xmax − x）
            kit.add(f"corner_{k}", PanelRole.GLUE_FLAP, [pt(*q) for q in local], wid, (pt(0, r0), pt(0, h)), u3, 2,
                    layer=1.0)  # fmt: skip
    kit.face("bottom", "base")
    return kit.tray()
