"""片材（几何引擎第 1 步，FEFCO 01；以后 09 内衬也用）：一张板 + 一组平行折线，每条折线折多少度。

折线顺着 L（FEFCO 011x）时横线排在 W 方向上；顺着 W（012x）时竖线排在 L 方向上。中间最宽的那条当树根不动，
两边按给的角度往同一侧卷（正）或反折（负）；±180 是对折压平（0130 的连续折叠板）。
立体目标按横截面算：从树根往两边，每过一条折线方向转那么多度，每条的四个角落在横截面折线的两头。
"""

from __future__ import annotations

import math
from typing import Any

from pacxio.engine.recipe import StructureParams, symbol_value
from pacxio.geometry import Path, Segment, Vec, arc_segment
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, Profile
from pacxio.styles.corrugated import assemble, crease

Target = tuple[str, tuple[float, float], tuple[float, float, float]]
# 【初值】插槽宽 t + 1（宽槽 2t + 2）、深到一半；两头槽离端头 min(0.1·长, 30)；两道槽至少隔槽宽 + 10
SLOT_T, SLOT_MM, SLOT_WIDE_T, SLOT_WIDE_MM = 1.0, 1.0, 2.0, 2.0
SLOT_END_RATIO, SLOT_END_MM, SLOT_MIN_GAP_MM = 0.1, 30.0, 10.0
HOLE_W_RATIO, HOLE_PITCH_RATIO, HOLE_MAX_MM = 0.25, 0.6, 40.0  # 孔径 min(0.25·条宽, 0.6·孔距, 40)


def horizontal(p: StructureParams) -> bool:
    """横条（x 是折线方向）：折线顺着 L 或 H；顺着 W 时竖条。"""
    return p.body.crease != "along_W"


def symbolic(p: StructureParams) -> bool:
    return any(isinstance(w, str) for w in p.body.panels or [])


def along_len(p: StructureParams) -> float:
    b = p.body
    v = {"along_L": b.L, "along_W": b.W, "along_H": b.H}[b.crease]
    assert v is not None
    return v


def strips(p: StructureParams) -> list[float]:
    b = p.body
    if b.panels and symbolic(p):  # 09 内衬：条宽按盒子内尺寸算
        assert b.L is not None and b.W is not None and b.H is not None
        return [symbol_value(w, b.L, b.W, b.H) for w in b.panels]
    across = b.W if b.crease == "along_L" else b.L
    assert across is not None
    return [float(w) for w in b.panels] if b.panels else [across]


def angles(p: StructureParams) -> list[float]:
    n = len(strips(p))
    return list(p.body.folds) if p.body.folds is not None else [90.0] * (n - 1)


def slot_width(p: StructureParams, t: float) -> float:
    s = p.body.slots
    assert s is not None
    return SLOT_WIDE_T * t + SLOT_WIDE_MM if s.wide else SLOT_T * t + SLOT_MM


def slot_centers(p: StructureParams) -> list[float]:
    """插槽中心横过折线的位置（在最宽那条上）。"""
    s = p.body.slots
    assert s is not None
    widths = strips(p)
    r = _root(widths)
    a = sum(widths[:r])
    w = widths[r]
    if s.at == "ends":
        e = min(SLOT_END_RATIO * w, SLOT_END_MM)
        return [a + e, a + w - e]
    return [a + w * (i + 1) / (s.count + 1) for i in range(s.count)]


def problems(p: StructureParams, t: float = 0.0) -> list[Issue]:
    b = p.body
    out = []
    across = b.W if b.crease == "along_L" else b.L
    total = sum(strips(p))
    if b.panels and not symbolic(p) and across is not None and abs(total - across) > 0.01:
        name = "W" if b.crease == "along_L" else "L"
        out.append(Issue(id="S03", message=translate("The strip widths total {total:g}, which does not match {dimension} = {value:g}", total=total, dimension=name, value=across),
                         fix=translate("Make panels sum to {dimension} or omit {field}", dimension=name, field=name)))  # fmt: skip
    if any(w <= 0 for w in strips(p)):
        out.append(
            Issue(
                id="S03",
                message=translate("A sheet strip has a computed width of zero or less"),
                fix=translate("Check the panels expressions and box dimensions"),
            )
        )
    if any(abs(a) > 180 for a in angles(p)):
        out.append(
            Issue(
                id="S03",
                message=translate("Sheet creases can fold up to 180°"),
                fix=translate("Set the angle between −180° and 180°"),
            )
        )
    if b.strip_flaps is not None and not out:
        widths = strips(p)
        for i, d in flap_layout(p).items():
            if i >= len(widths) or widths[i] <= 2 * d + 10:
                out.append(
                    Issue(
                        id="S03",
                        message=translate(
                            "Strip {strip} cannot fit an end flap of depth {depth:.1f}",
                            strip=i,
                            depth=d,
                        ),
                        fix=translate("Reduce strip_flaps.depth or remove that entry"),
                    )
                )
                break
    if b.slots is not None and not out:
        widths = strips(p)
        w = widths[_root(widths)]
        if b.slots.at == "ends" and b.slots.count != 2:
            out.append(
                Issue(
                    id="S03",
                    message=translate("End slots (at=ends) require exactly two slots"),
                    fix=translate("Set slots.count to 2"),
                )
            )
        sw = slot_width(p, t)
        c = slot_centers(p)
        gaps = [c[0] - sum(widths[: _root(widths)])] + [
            q - o for o, q in zip(c, c[1:], strict=False)
        ]
        if min(gaps) < sw + SLOT_MIN_GAP_MM or w <= 2 * sw:
            out.append(Issue(id="S03", message=translate("Slots are too close. Slot width is {width:.1f} and the nearest pair is only {spacing:.1f} apart.", width=sw, spacing=min(gaps)),
                             fix=translate("Reduce slots.count or widen this strip")))  # fmt: skip
    return out


def _root(widths: list[float]) -> int:
    return max(range(len(widths)), key=lambda i: (widths[i], -abs(i - (len(widths) - 1) / 2)))


def cross_section(
    widths: list[float], folds: list[float]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """每条在横截面上的两头 (s, z)：树根平放在 z = 0，往右的方向每过一条折线顺时针转、往左的逆时针转（往 −z 卷）。"""
    r = _root(widths)
    ends: list[Any] = [None] * len(widths)
    left = sum(widths[:r])
    ends[r] = ((left, 0.0), (left + widths[r], 0.0))
    pos, theta = ends[r][1], 0.0
    for i in range(r + 1, len(widths)):
        theta += math.radians(folds[i - 1])
        nxt = (pos[0] + widths[i] * math.cos(theta), pos[1] - widths[i] * math.sin(theta))
        ends[i] = (pos, nxt)
        pos = nxt
    pos, theta = ends[r][0], 0.0
    for i in range(r - 1, -1, -1):
        theta += math.radians(folds[i])
        nxt = (pos[0] - widths[i] * math.cos(theta), pos[1] - widths[i] * math.sin(theta))
        ends[i] = (nxt, pos)
        pos = nxt
    return ends


def _edge_run(u: float, s0: float, s1: float, notches: list[float], sw: float, depth: float,
              into: float) -> list[tuple[float, float]]:  # fmt: skip
    """沿 u = 常数那条边从 s0 走到 s1，路过插槽中心时开一道槽（往 into 方向深 depth）。"""
    pts: list[tuple[float, float]] = [(u, s0)]
    up = s1 > s0
    for c in sorted(notches, reverse=not up):
        if not (min(s0, s1) < c < max(s0, s1)):
            continue
        a, b = (c - sw / 2, c + sw / 2) if up else (c + sw / 2, c - sw / 2)
        pts += [(u, a), (u + into * depth, a), (u + into * depth, b), (u, b)]
    return pts


def _ring(p: StructureParams, s0: float, s1: float, along: float, t: float,
          root: bool) -> list[tuple[float, float]]:  # fmt: skip
    """横过折线 s0..s1 那一段的外形（u 沿折线）：插槽、台阶只在最宽那条上。"""
    b = p.body
    top: list[float] = []
    bottom: list[float] = []
    sw = depth = 0.0
    if root and b.slots is not None:
        sw, depth = slot_width(p, t), along * b.slots.depth
        (top if b.slots.edge == "top" else bottom).extend(slot_centers(p))
    pts: list[tuple[float, float]] = [(0.0, s0)]
    if root and b.steps:
        n, w = b.steps, s1 - s0
        pts.append((along, s0))
        for i in range(1, n):
            pts += [
                (along * (n - i + 1) / n, s0 + w * i / n),
                (along * (n - i) / n, s0 + w * i / n),
            ]
        pts.append((along / n, s1))
    else:
        pts += _edge_run(along, s0, s1, top, sw, depth, -1.0)
        pts.append((along, s1))
    pts += _edge_run(0.0, s1, s0, bottom, sw, depth, 1.0)[0:]
    out: list[tuple[float, float]] = []
    for q in pts:  # 去掉重复点
        if not out or abs(out[-1][0] - q[0]) > 1e-9 or abs(out[-1][1] - q[1]) > 1e-9:
            out.append(q)
    if abs(out[-1][0] - out[0][0]) < 1e-9 and abs(out[-1][1] - out[0][1]) < 1e-9:
        out.pop()
    return out


def _path(pts: list[tuple[float, float]], horiz: bool) -> Path:
    xy = [(u, s) for u, s in pts] if horiz else [(s, u) for u, s in reversed(pts)]
    vs = [Vec(x, y) for x, y in xy]
    return Path([Segment(a, b) for a, b in zip(vs, vs[1:] + vs[:1], strict=True)], closed=True)


def generate(
    p: StructureParams, material: Material, profile: Profile, style_id: str
) -> tuple[Dieline, list[Target]]:
    b = p.body
    t = material.caliper_mm
    widths, folds = strips(p), angles(p)
    along = along_len(p)
    horiz = horizontal(p)
    total = sum(widths)
    names = [f"panel_{i + 1}" for i in range(len(widths))]
    r = _root(widths)
    polys: dict[str, tuple[PanelRole, Path]] = {}
    creases = []
    a = 0.0
    for i, (pid, w) in enumerate(zip(names, widths, strict=True)):
        role = PanelRole.BODY
        polys[pid] = (role, _path(_ring(p, a, a + w, along, t, i == r), horiz))
        a += w
    for i in range(len(widths) - 1):
        cut = sum(widths[: i + 1])
        seg = ((0.0, cut), (along, cut)) if horiz else ((cut, 0.0), (cut, along))
        parent, child = (names[i], names[i + 1]) if i + 1 > r else (names[i + 1], names[i])
        creases.append(crease(f"crease_{i + 1}", seg[0], seg[1], [parent, child]))
    W, H = (along, total) if horiz else (total, along)
    flaps = flap_layout(p)
    for i, d in flaps.items():  # 端翼：每条两头各一块，45° 斜边，压线在条的两头
        s0, s1 = sum(widths[:i]), sum(widths[: i + 1])
        for end, u, sg in (("a", 0.0, -1.0), ("b", along, 1.0)):
            fid = f"{names[i]}_flap_{end}"
            pts = [(u, s0), (u + sg * d, s0 + d), (u + sg * d, s1 - d), (u, s1)]
            polys[fid] = (PanelRole.DUST_FLAP, _path(pts if sg > 0 else pts[::-1], horiz))
            seg = ((u, s0), (u, s1))
            xy = seg if horiz else ((s0, u), (s1, u))
            creases.append(crease(f"crease_{fid}", xy[0], xy[1], [names[i], fid]))
    if flaps:  # 外形：两条长边路过带翼的条时绕出去
        pts = [(0.0, 0.0)]
        for i, w in enumerate(widths):
            s0 = sum(widths[:i])
            pts.append((along, s0))
            if i in flaps:
                pts += [(along + flaps[i], s0 + flaps[i]), (along + flaps[i], s0 + w - flaps[i])]
        pts.append((along, total))
        for i in reversed(range(len(widths))):
            s0, s1 = sum(widths[:i]), sum(widths[: i + 1])
            pts.append((0.0, s1))
            if i in flaps:
                pts += [(-flaps[i], s1 - flaps[i]), (-flaps[i], s0 + flaps[i])]
        outline = _path([q for k, q in enumerate(pts) if k == 0 or q != pts[k - 1]][:-1]
                        if pts[-1] == (0.0, 0.0) else [q for k, q in enumerate(pts) if k == 0 or q != pts[k - 1]], horiz)  # fmt: skip
    elif b.slots is None and not b.steps:
        outline = Path([Segment(Vec(0, 0), Vec(W, 0)), Segment(Vec(W, 0), Vec(W, H)), Segment(Vec(W, H), Vec(0, H)),
                        Segment(Vec(0, H), Vec(0, 0))], closed=True)  # fmt: skip
    elif b.steps:  # 台阶形只用在一张平板上
        outline = _path(_ring(p, 0.0, total, along, t, True), horiz)
    else:  # 插槽开在最宽那条上：沿两条长边走，路过槽中心就开槽
        assert b.slots is not None
        top = slot_centers(p) if b.slots.edge == "top" else []
        bottom = slot_centers(p) if b.slots.edge == "bottom" else []
        sw, depth = slot_width(p, t), along * b.slots.depth
        pts = [(0.0, 0.0), *_edge_run(along, 0.0, total, top, sw, depth, -1.0), (along, total),
               *_edge_run(0.0, total, 0.0, bottom, sw, depth, 1.0)]  # fmt: skip
        outline = _path([q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]], horiz)
    cuts: list[Edge] = []
    if b.holes is not None:
        i = b.holes.panel
        s0 = sum(widths[:i])
        w = widths[i]
        n = b.holes.count
        rad = min(HOLE_W_RATIO * w, HOLE_PITCH_RATIO * along / (n + 1), HOLE_MAX_MM) / 2
        for k in range(n):
            u, sc = along * (k + 1) / (n + 1), s0 + w / 2
            c = Vec(u, sc) if horiz else Vec(sc, u)
            q = math.pi / 2  # 四段四分之一圆弧
            cuts.append(Edge(id=f"cut_hole_{k + 1}", type=EdgeType.CUT, panels=[names[i]],
                             segments=[arc_segment(c, rad, j * q, (j + 1) * q) for j in range(4)]))  # fmt: skip
    moves = {}
    for i in range(len(widths) - 1):
        k = i + 1 if i + 1 > r else i  # 这条折线带动的那条（离树根远的一边）
        moves[names[k]] = {
            "keys": [[1, folds[i]]],
            "layer": float(abs(k - r)) if abs(folds[i]) > 170 else 0.0,
        }
    for i in flaps:  # 端翼往筒里折 90°（往同一侧卷：和条的折向一样，正）
        for end in ("a", "b"):
            moves[f"{names[i]}_flap_{end}"] = {"keys": [[2, FLAP_FOLD_SIGN * 90.0]], "layer": 0.0}
    plan = {
        "root": names[r],
        "moves": moves,
        "steps": 2 if flaps else 1,
        "open_step": 0,
        "pose_step": 2 if flaps else 1,
        "ground_step": 1,
    }
    kind = "卷材" if b.roll else "平板" if len(widths) == 1 else f"{len(widths) - 1} 道折线的片材"
    if symbolic(p):
        kind = "内衬" + (
            "（隔板）" if b.slots is not None else "（台阶侧板）" if b.steps else f"，{kind}"
        )
    meta: dict[str, Any] = {
        "name_zh": f"结构配方（几何引擎）（{kind}）",
        "refs": {"design": "几何引擎：片材 + 平行折线（）"},
        "version": "0.1.0",
        "accuracy_level": "L0",
        "compensation_applied": p.apply_compensation,
        "dims": {
            "L": b.L,
            "W": b.W,
            **({"H": b.H} if b.H is not None else {}),
            "panels": widths,
            "folds": folds,
            # 规格书 / 效果图标题用：内尺寸按配方的 L / W / H（09 内衬 = 它装进去的盒子内尺寸），
            # 外尺寸按折好以后的外包络（沿折线长 × 横截面宽 × 横截面高 + 纸厚）
            "Li": b.L,
            "Wi": b.W,
            "Hi": b.H or 0.0,
            "outer": _envelope(widths, folds, along, t),
        },
        "fold_plan": plan,
        "assumptions": ["片材按给的每条宽和折线角度画；折线位置不补偿纸厚【初值】"],
    }
    dl = assemble(style_id=style_id, params=p, material=material, profile=profile, outline=outline,
                  polys=polys, creases=creases, inner_cuts=cuts, ups={}, provisional=set(), meta=meta)  # fmt: skip
    targets: list[Target] = []
    for pid, ((s0, z0), (s1, z1)) in zip(names, cross_section(widths, folds), strict=True):
        x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
        if horiz:  # 条的下边（y0）在横截面左头
            corners = (((x0, y0), (0.0, s0, z0)), ((x1, y0), (along, s0, z0)),
                       ((x1, y1), (along, s1, z1)), ((x0, y1), (0.0, s1, z1)))  # fmt: skip
        else:
            corners = (((x0, y0), (s0, 0.0, z0)), ((x1, y0), (s1, 0.0, z1)),
                       ((x1, y1), (s1, along, z1)), ((x0, y1), (s0, along, z0)))  # fmt: skip
        targets += [(pid, xy, xyz) for xy, xyz in corners]
    sections = cross_section(widths, folds)
    sx, sy, _, _ = dl.panel(
        names[0]
    ).polygon.bbox()  # 出刀版时整体挪到包围盒左下角：第一条原来在 (0, 0)
    for i, d in flaps.items():  # 端翼：折到端面里，从条的边沿条的内法线伸 d
        (c0, z0), (c1, z1) = sections[i]
        w = widths[i]
        ty, tz = (c1 - c0) / w, (z1 - z0) / w
        ny, nz = tz, -ty  # 往筒里（树根往 −z 卷）
        f0 = float(sum(widths[:i]))
        for end, ue, sg in (("a", 0.0, -1.0), ("b", along, 1.0)):
            for su, ss in (
                (ue, f0),
                (ue + sg * d, f0 + d),
                (ue + sg * d, f0 + w - d),
                (ue, f0 + w),
            ):
                frac, dep = (ss - f0) / w, abs(su - ue)
                cy, cz = c0 + (c1 - c0) * frac + ny * dep, z0 + (z1 - z0) * frac + nz * dep
                fxy = (su + sx, ss + sy) if horiz else (ss + sx, su + sy)
                fxyz = (ue, cy, cz) if horiz else (cy, ue, cz)
                targets.append((f"{names[i]}_flap_{end}", fxy, fxyz))
    return dl, targets


FLAP_FOLD_SIGN = 1.0


def _envelope(widths: list[float], folds: list[float], along: float, t: float) -> list[float]:
    pts = [q for seg in cross_section(widths, folds) for q in seg]
    ys, zs = [q[0] for q in pts], [q[1] for q in pts]
    return [round(along, 3), round(max(ys) - min(ys) + t, 3), round(max(zs) - min(zs) + t, 3)]


def flap_layout(p: StructureParams) -> dict[int, float]:
    """带端翼的条 → 翼深。"""
    f = p.body.strip_flaps
    if f is None:
        return {}
    b = p.body
    assert b.L is not None and b.W is not None and b.H is not None
    d = symbol_value(f.depth, b.L, b.W, b.H)
    n = len(strips(p))
    return {i: d for i in (f.panels if f.panels is not None else range(n))}


__all__ = ["cross_section", "generate", "problems"]
