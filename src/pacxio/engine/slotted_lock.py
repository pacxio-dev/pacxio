"""瓦楞开槽箱的自动锁底积木（几何引擎第 3 步，FEFCO 07）。底端画法：翼挂在 y = 0 往 −y。

三种样子（数值全部【初值】，hook 的比例来自 crashlock_rsc 的参考刀版，tab / web 按示意图推读、待核对）：
- hook  主翼带钩齿 + 过角 45° 糊胶三角，侧翼 20° 斜边（同 crashlock_rsc / FEFCO 0711，用同一个 CrashBottom）
- tab   两片端墙翼靠前一面墙的那头各带 45° 糊胶三角（粘在长翼上）；on 那面的长翼整幅深、远边中间一块插舌，
        对面墙脚开一道插缝；另一片长翼 ½W（0701–0705）
- web   四片翼都约 ½W，两片长翼靠后一面墙的那头各带 45° 糊胶三角（粘在端墙翼上），没有钩齿（0706–0709）

糊胶三角：斜压线从角上 c 处 45° 斜下（c = 0.0447·W + 1.4，同 crashlock_rsc），离分界 3 mm 竖直；
出厂糊好压扁，撑开时四片翼一起落平。立体目标：四片翼和糊胶三角都平放在底面（糊胶三角压在相邻翼上）。
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.styles.components.carton import _chain
from pacxio.styles.components.folds import action
from pacxio.styles.components.lockbottom import AutolockParams, CrashBottom, auto_dims
from pacxio.styles.components.tube4 import BodyX
from pacxio.styles.corrugated import crease
from pacxio.styles.crashlock import (
    MAJOR_TAPER_DEG,
    MAJOR_W_RATIO,
    MINOR_TAPER_DEG,
    WEB_CLEAR_MM,
    WEB_CORNER_EXTRA_MM,
    WEB_CORNER_W_RATIO,
)
from pacxio.styles.rte import RteDims

if TYPE_CHECKING:
    from pacxio.engine.slotted import Block, Body, SlotEnd, SlottedAssembledParams
    from pacxio.styles.fefco_0201 import SlotDims

# 【初值】tab / web（按示意图推读）
SIDE_TAB_RATIO = 0.45  # tab：端墙翼深 0.45·端墙宽（糊胶三角在一头、另一头 45° 斜边，要放得下）
TAB_W_RATIO, TAB_W_MAX_MM = 0.25, 50.0  # 插舌宽 min(0.25·长墙宽, 50)
TAB_D_RATIO, TAB_D_MAX_MM = 0.15, 15.0  # 插舌深 min(0.15·W, 15)
TAB_TAPER_MM = 3.0  # 插舌两角各收 3
# 对面墙脚的插缝：整幅长翼铺在底的最上层（下面压着对面短翼、两片端墙翼），中线在压线上方 2.5·t；
# 缝以它为中线、上下各让 t/2 + 1，宽 = 插舌宽 + 2。长翼本体做到对面墙内壁前 0.5，插舌穿过缝伸到墙外
# （原来翼深 W − 舌深 − t、缝在压线上方 1–(t + 3)，舌尖够不着墙，S07 量出来的，2026-11-06）【初值】
SLIT_LAYER_T, SLIT_CLEAR_MM = 2.5, 1.0
DEEP_CLEAR_MM = 0.5
WEB_SIDE_RATIO = 0.5  # web：四片翼深 ½ 端墙宽


def _corner(Wi: float) -> float:
    return WEB_CORNER_W_RATIO * Wi + WEB_CORNER_EXTRA_MM


def paperboard(d: SlotDims) -> bool:
    """钩齿自动锁底用哪套校准比例：卡纸（ECMA 60）/ 瓦楞（0711），按纸厚（engine/slotted.paperboard）。"""
    from pacxio.engine.slotted import paperboard as by_caliper

    return by_caliper(d)


def hook_bottom(body: Body) -> tuple[AutolockParams, CrashBottom, BodyX]:
    d = body.d
    if paperboard(d):  # 卡纸自动锁底：组件自己的初值（同组合彩盒 / ECMA 60）
        lock, part = AutolockParams(L=d.Li, W=d.Wi, H=d.Hi), CrashBottom()
    else:
        lock = AutolockParams(L=d.Li, W=d.Wi, H=d.Hi, major_depth=MAJOR_W_RATIO * d.Wi,
                              major_taper_deg=MAJOR_TAPER_DEG)  # fmt: skip
        part = CrashBottom(corner=_corner(d.Wi), clear=WEB_CLEAR_MM,
                           minor_taper=math.tan(math.radians(MINOR_TAPER_DEG)))  # fmt: skip
    (_, xb0, xb1), (_, _, xa1), (_, _, xf1), (_, _, xs1) = body.spans
    return lock, part, BodyX(xb0, xb1, xa1, xf1, xs1)


def problems(spec: SlotEnd, body: Body) -> list[tuple[str, str]]:
    """放不下的情况：(问题, 怎么改)。"""
    d = body.d
    out: list[tuple[str, str]] = []
    if body.split:
        return [("自动锁底要求接头在墙角", "去掉 joint_offset / joint_partition / 居中断开")]
    if d.Wi > d.Li:
        out.append(
            (
                translate(
                    "An auto-lock bottom requires L ≥ W because the long flaps attach to the long walls. W={width:g} > L={length:g}.",
                    width=d.Wi,
                    length=d.Li,
                ),
                translate("Swap L and W"),
            )
        )
        return out
    c, g = _corner(d.Wi), WEB_CLEAR_MM
    ws = min(w for f, a, b in body.spans for w in [b - a] if f.startswith("side"))
    if spec.form == "hook" and paperboard(d):
        from pacxio.styles.lockbottom import AutolockStyle

        lock, _, _ = hook_bottom(body)
        return [(i.message, i.fix or "") for i in AutolockStyle()._validate_bottom(lock, _rte(d))]
    if spec.form == "hook":
        lock, _, _ = hook_bottom(body)
        a = auto_dims(lock, _rte(d))
        if not 0 < c < a.half - g:
            out.append(
                (
                    translate(
                        "The corner relief cuts too far into the diagonal crease and removes the glue triangle"
                    ),
                    translate("Increase W"),
                )
            )
        if a.tooth(d.wb) < 5.0:
            out.append(
                (
                    translate("The hooks are too narrow for the two major flaps to lock"),
                    translate("Increase the L / W ratio"),
                )
            )
        return out
    D = WEB_SIDE_RATIO * ws if spec.form == "web" else SIDE_TAB_RATIO * ws
    if not g < c < D - g:
        out.append(
            (
                translate(
                    "The glue triangle does not fit (flap depth {depth:.1f}, corner relief {relief:.1f})",
                    depth=D,
                    relief=c,
                ),
                translate("Increase W"),
            )
        )
    if spec.form == "tab":
        deep = d.Wi - DEEP_CLEAR_MM
        if (
            d.wb - deep * math.tan(math.radians(MAJOR_TAPER_DEG)) - D
            < min(TAB_W_RATIO * d.wb, TAB_W_MAX_MM) + 10
        ):
            out.append(
                (
                    translate("The tuck tab on the full-width long flap does not fit"),
                    translate("Increase L"),
                )
            )
    elif d.wb - D * (1 + math.tan(math.radians(MAJOR_TAPER_DEG))) < 10:
        out.append(
            (translate("The bottom edge of the long flap is too short"), translate("Increase L"))
        )
    return out


def _rte(d: SlotDims) -> RteDims:
    """自动锁底的部件按彩盒尺寸算（只用到面宽、内宽、纸厚）：直接给开槽箱尺寸。"""
    return RteDims(t=d.t, Li=d.Li, Wi=d.Wi, Hi=d.Hi, wb=d.wb, ws=d.ws, hb=d.hb, ld=0.0, tl=0.0, ti=0.0,
                   tr=0.0, dd=0.0, off=0.0, gw=d.jw)  # fmt: skip


def _poly(pts: list[tuple[float, float]]) -> Path:
    vs = [Vec(x, y) for x, y in pts]
    return Path([Segment(a, b) for a, b in zip(vs, vs[1:] + vs[:1], strict=True)], closed=True)


def _web_right(x1: float, D: float, c: float, g: float) -> tuple[list[tuple[float, float]], ...]:
    """翼右头的糊胶三角：(翼上的点 Q → P，糊胶三角 Q R S P，外形 Q R S P (x1, 0))。"""
    P, Q, R, S = (x1 - c, -c), (x1 - D, -D), (x1 - g, -D), (x1 - g, -(2 * c - g))
    return [Q, P], [Q, R, S, P], [Q, R, S, P, (x1, 0.0)]


def _web_left(x0: float, D: float, c: float, g: float) -> tuple[list[tuple[float, float]], ...]:
    P, Q, R, S = (x0 + c, -c), (x0 + D, -D), (x0 + g, -D), (x0 + g, -(2 * c - g))
    return [P, Q], [P, S, R, Q], [(x0, 0.0), P, S, R, Q]


def autolock_block(spec: SlotEnd, p: SlottedAssembledParams, body: Body, prefix: str) -> Block:
    from pacxio.engine.slotted import OPPOSITE, Block, _rename, wall_on

    d = body.d
    if spec.form == "hook":
        lock, part, xs = hook_bottom(body)
        rd = _rte(d)
        built = part.build(lock, rd, xs)
        names = {"glue_back": "btm_glue_back", "glue_front": "btm_glue_front"}

        def nm(s: str) -> str:
            for a, b in names.items():
                s = s.replace(a, b)
            return s

        a = auto_dims(lock, rd)
        a = a if paperboard(d) else replace(a, corner=_corner(d.Wi), clear=WEB_CLEAR_MM)
        hook_polys = {nm(k): v for k, v in built.polys.items()}
        hook_creases = [
            e.model_copy(update={"id": nm(e.id), "panels": [nm(q) for q in e.panels]})
            for e in built.creases
        ]
        web_size = (a.half - a.clear, a.major - a.corner)
        sizes = {"btm_back": (d.wb - a.relief, a.major), "btm_front": (d.wb - a.relief, a.major),
                 "btm_glue_back": web_size, "btm_glue_front": web_size, "btm_sa": (body.span("side_a")[1] - body.span("side_a")[0], a.half),
                 "btm_sb": (body.span("side_b")[1] - body.span("side_b")[0], a.half)}  # fmt: skip
        block = Block(outline=built.outline, panels=hook_polys, creases=hook_creases, steps=1,
                      folds={"btm_back": action(0), "btm_sa": action(0, layer=2), "btm_sb": action(0, layer=2),
                             "btm_front": action(0, layer=1)},
                      poses={"btm_back": ("flat", "back"), "btm_front": ("flat", "front"),
                             "btm_sa": ("flat", "side_a"), "btm_sb": ("flat", "side_b"),
                             "btm_glue_back": ("flat", "back"), "btm_glue_front": ("flat", "front")},
                      sizes=sizes, cover="btm_back")  # fmt: skip
        return _rename(block, prefix)
    c, g, t = _corner(d.Wi), WEB_CLEAR_MM, d.t
    taper = math.tan(math.radians(MAJOR_TAPER_DEG))
    side_taper = math.tan(math.radians(MINOR_TAPER_DEG))
    deep_wall = wall_on(spec)
    out: list[tuple[float, float]] = []
    polys: dict[str, tuple[PanelRole, Path]] = {}
    creases: list[Edge] = []
    cuts: list[Edge] = []
    poses: dict[str, tuple[str, ...]] = {}
    key = {"back": "back", "front": "front", "side_a": "sa", "side_b": "sb"}
    for face, x0, x1 in body.spans:
        pid = f"btm_{key[face]}"
        ws = x1 - x0
        if face.startswith("side"):
            D = (WEB_SIDE_RATIO if spec.form == "web" else SIDE_TAB_RATIO) * ws
            if spec.form == "tab":  # 糊胶三角在左头（挨着前一面长墙）
                diag, web, edge = _web_left(x0, D, c, g)
                flap = [(x0, 0.0), diag[0], diag[1], (x1 - D, -D), (x1, 0.0)]
                out += edge + [(x1 - D, -D)]
                polys[f"btm_glue_{key[face]}"] = (PanelRole.GLUE_TAB, _poly(web))
                creases.append(crease(f"crease_{pid}_glue", diag[0], diag[1], [pid, f"btm_glue_{key[face]}"],
                                      EdgeType.CREASE))  # fmt: skip
                poses[f"btm_glue_{key[face]}"] = ("flat", face)
            else:
                flap = [(x0, 0.0), (x0 + D * side_taper, -D), (x1 - D, -D), (x1, 0.0)]
                out += flap[:-1]
        else:
            deep = spec.form == "tab" and face == deep_wall
            if spec.form == "tab":
                e = SIDE_TAB_RATIO * min(b - a for f, a, b in body.spans if f.startswith("side"))
                tab_d = min(TAB_D_RATIO * d.Wi, TAB_D_MAX_MM)
                D = d.Wi - DEEP_CLEAR_MM if deep else d.flap
                pts = [(x0, 0.0), (x0 + D * taper, -D)]
                if deep:
                    xc, tw = 0.5 * (x0 + x1 - e), min(TAB_W_RATIO * ws, TAB_W_MAX_MM)
                    pts += [(xc - tw / 2, -D), (xc - tw / 2 + TAB_TAPER_MM, -D - tab_d),
                            (xc + tw / 2 - TAB_TAPER_MM, -D - tab_d), (xc + tw / 2, -D)]  # fmt: skip
                    ox0, ox1 = body.span(OPPOSITE[face])
                    oc = ox1 - (xc - x0)  # 对面墙方向相反：缝对准插舌（插舌为躲糊胶三角偏了 e/2）
                    sw = tw + 2.0
                    yc = SLIT_LAYER_T * t
                    y0, y1 = yc - t / 2 - SLIT_CLEAR_MM, yc + t / 2 + SLIT_CLEAR_MM
                    slot = [
                        (oc - sw / 2, y0),
                        (oc + sw / 2, y0),
                        (oc + sw / 2, y1),
                        (oc - sw / 2, y1),
                    ]
                    cuts.append(Edge(id=f"cut_{prefix}_lock_slit", type=EdgeType.CUT,
                                     segments=_poly(slot).segments, panels=[OPPOSITE[face]]))  # fmt: skip
                pts += [(x1 - e, -D), (x1 - e, -e), (x1, 0.0)]
                flap = pts
                out += pts[:-1]
            else:  # web：右头糊胶三角（挨着下一面端墙）
                D = WEB_SIDE_RATIO * min(b - a for f, a, b in body.spans if f.startswith("side"))
                diag, web, edge = _web_right(x1, D, c, g)
                flap = [(x0, 0.0), (x0 + D * taper, -D), diag[0], diag[1], (x1, 0.0)]
                out += [(x0, 0.0), (x0 + D * taper, -D)] + edge[:-1]
                polys[f"btm_glue_{key[face]}"] = (PanelRole.GLUE_TAB, _poly(web))
                creases.append(crease(f"crease_{pid}_glue", diag[1], diag[0], [pid, f"btm_glue_{key[face]}"],
                                      EdgeType.CREASE))  # fmt: skip
                poses[f"btm_glue_{key[face]}"] = ("flat", face)
        polys[pid] = (
            PanelRole.BOTTOM if face in ("back", "front") else PanelRole.DUST_FLAP,
            _poly(flap),
        )
        creases.append(crease(f"crease_{face}_{pid}", (x0, 0.0), (x1, 0.0), [face, pid]))
        poses[pid] = ("flat", face)
    out.append((body.x_end, 0.0))
    keep = [out[0]]
    for q in out[1:]:
        if math.dist(q, keep[-1]) > 1e-9:
            keep.append(q)
    outline = _chain([Vec(x, y) for x, y in keep])
    sizes = {}
    for pid, (_, path) in polys.items():
        x0, y0, x1, y1 = path.bbox()
        sizes[pid] = (x1 - x0, y1 - y0)
    if spec.form == "tab":
        shallow = f"btm_{key[OPPOSITE[deep_wall]]}"
        folds = {shallow: action(0), "btm_sa": action(0, layer=1), "btm_sb": action(0, layer=1),
                 f"btm_{key[deep_wall]}": action(0, layer=2)}  # fmt: skip
        cover = f"btm_{key[deep_wall]}"
    else:
        folds = {"btm_back": action(0), "btm_sa": action(0, layer=2), "btm_sb": action(0, layer=2),
                 "btm_front": action(0, layer=1)}  # fmt: skip
        cover = "btm_back"
    block = Block(outline=outline, panels=polys, creases=creases, cuts=cuts, folds=folds, steps=1,
                  poses=poses, sizes=sizes, cover=cover)  # fmt: skip
    if prefix != "btm":
        block.cuts = [e.model_copy(update={"id": e.id.replace("btm", "top")}) for e in block.cuts]
    return _rename(block, prefix)


__all__ = ["autolock_block", "problems"]
