"""给 ear 托盘的两块单层墙加卷边、回折内墙和插脚（0425 / 0425.1）。

复用已校准托盘的插脚轮廓和尺寸规则；新增组合、间隙与折叠顺序仍为【初值】。
内墙两端可带耳片。每块面独立声明目标位置，不从折叠求值结果反推目标。
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from shapely.geometry import Polygon
from shapely.ops import unary_union

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.styles.corrugated import crease
from pacxio.styles.tray import (
    EAR_CLEAR_MM,
    EAR_FREE_MIN_MM,
    LUG_SPACING_MM,
    TrayDims,
    _lugs,
    tray_dims,
    validate_tray,
)

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray, TrayParams

SLOT_LAYER_CLEAR_MM = 1.0  # 折叠分层后的插脚离槽底的让位【初值】
END_INNER_LAYERS_WITH_EARS = 4.0
END_INNER_LAYERS = 3.0
RETURN_INNER_LAYERS = 2.0


def end_slot_dims(p: TrayParams, d: TrayDims) -> TrayDims:
    """端墙内层带分层后更靠盒内：底槽必须覆盖其实际折叠位置。"""
    layers = END_INNER_LAYERS_WITH_EARS if p.inner_ears else END_INNER_LAYERS
    depth = max(d.sd, d.rg + layers * d.t + SLOT_LAYER_CLEAR_MM)
    return replace(d, sd=depth)


def compensate_inner_dims(p: TrayParams, d: TrayDims, k_panel: float) -> tuple[float, float]:
    """按带分层折叠后内壁的位置补偿内腔；返回要传给托盘部件的长、宽。"""
    layers = END_INNER_LAYERS_WITH_EARS if p.inner_ears else END_INNER_LAYERS
    return_gap = d.rg if p.return_wall.roll_gap is None else p.return_wall.roll_gap
    length_extra = 2 * (d.rg + layers * d.t) - (k_panel + 4) * d.t
    width_extra = 2 * (return_gap + RETURN_INNER_LAYERS * d.t) - (k_panel + 4) * d.t
    # 新增的两面内墙与旧部件双层墙使用相同的 4t 初值加放。
    return d.li + length_extra, d.wi + width_extra


def return_dims(p: TrayParams, d: TrayDims) -> TrayDims:
    """新增内墙沿底板长边展开；插脚 / 插孔复用托盘参数，数量由配方控制。"""
    r = tray_dims(d.wi, d.bl, d.hi, d.t, 0, (d.hf - d.hi) / d.t,
                  p.return_wall, True)  # fmt: skip
    # 默认沿用原托盘卷边（卡纸和瓦楞不同）；新增内墙从长边墙顶回到底板。
    r = replace(r, rg=d.rg if p.return_wall.roll_gap is None else r.rg,
                ho=d.hf, hin=d.hf - d.t, lugs=p.lugs and r.lugs,
                ed=d.ed if p.return_wall.ear_depth is None else r.ed)  # fmt: skip
    if p.return_wall.slot_depth is None:
        r = replace(r, sd=max(r.sd, r.rg + RETURN_INNER_LAYERS * d.t + SLOT_LAYER_CLEAR_MM))
    if not p.inner_ears and p.return_wall.inner_inset is None:
        # 新增内墙端头避开折到端墙上的原耳片和端墙内层。
        ii = max(r.ii, d.rg + END_INNER_LAYERS * d.t + SLOT_LAYER_CLEAR_MM)
        r = replace(r, ii=ii, wall_w=d.bl - 2 * ii)
    return replace(r, lug_y=[r.ii + (i + 1) * r.wall_w / (p.return_lug_count + 1)
                            for i in range(p.return_lug_count)])  # fmt: skip


def validate_return(p: TrayParams, d: TrayDims, r: TrayDims) -> list[Issue]:
    """拒绝卷边、插脚、槽和内墙耳片放不下的组合，不用裁掉机构来凑通过。"""
    if not all(math.isfinite(v) for v in (r.rg, r.ii, r.ed, r.eg, r.lug_w, r.lug_len, r.sd)):
        return [
            Issue(
                id="S03",
                message=t("The additional inner-wall parameters must be finite numbers"),
                fix=t("Check tuning.return_wall"),
            )
        ]
    # 四面双层时按夹层加大的端头让位可超过原单层托盘的经验上限；实际净宽仍参与检查。
    check_r = replace(r, ii=min(r.ii, 3 * d.t + 2)) if not p.inner_ears else r
    out = validate_tray(check_r, "新增内墙", "tuning.return_wall")
    if not p.inner_ears and r.ii < d.rg + END_INNER_LAYERS * d.t + SLOT_LAYER_CLEAR_MM:
        out.append(Issue(id="S03", message=t("The ends of the additional inner wall do not clear the end-wall layers"),
                         fix=t("Increase return_wall.inner_inset or use its default")))  # fmt: skip
    if p.return_wall.ear_offset is not None:
        out.append(Issue(id="S03", message=t("The inner-wall endpoints determine the ear locations. ear_offset is unavailable."),
                         fix=t("Remove tuning.return_wall.ear_offset")))  # fmt: skip
    if r.wall_w <= 0:
        out.append(Issue(id="S03", message=t("The insets at both ends leave no usable width in the additional inner wall"),
                         fix=t("Reduce return_wall.inner_inset or increase L")))  # fmt: skip
    if r.lugs and p.return_lug_count * r.sw + (p.return_lug_count + 1) * LUG_SPACING_MM > r.wall_w:
        out.append(Issue(id="S03", message=t("The additional inner wall's tabs or base slots are too close together"),
                         fix=t("Reduce return_lug_count or return_wall.lug_w, or increase L")))  # fmt: skip
    if r.ii < d.X0:
        out.append(Issue(id="S03", message=t("The additional rollover is wider than the outer wall"),
                         fix=f"return_wall.inner_inset ≥ {d.X0:g}"))  # fmt: skip
    if 2 * r.sd + EAR_CLEAR_MM >= d.bw or 2 * r.rg + EAR_CLEAR_MM >= d.bw:
        out.append(
            Issue(
                id="S03",
                message=t("The additional inner walls or slots collide in the middle of the base"),
                fix=t("Increase W or reduce the rollover or base slot depth"),
            )
        )
    if p.inner_ears and 2 * r.ed + 2 * r.rg + EAR_CLEAR_MM > d.bw:
        out.append(Issue(id="S03", message=t("The additional inner-wall ears collide between the end-wall layers"),
                         fix=t("Reduce return_wall.ear_depth or increase W")))  # fmt: skip
    if p.inner_ears and r.hin - d.t - r.eg - r.ed * r.et < EAR_FREE_MIN_MM:
        out.append(Issue(id="S03", message=t("The free ends of the additional inner-wall ears are too short"),
                         fix=t("Increase H or reduce return_wall.ear_depth or ear_gap")))  # fmt: skip
    if d.lugs and 2 * d.sd + EAR_CLEAR_MM >= d.bl:
        out.append(
            Issue(
                id="S03",
                message=t("The end-wall tab slots collide in the middle of the base"),
                fix=t("Increase L or use thinner board"),
            )
        )
    if r.lugs and r.rg + RETURN_INNER_LAYERS * d.t > r.sd:
        out.append(Issue(id="S03", message=t("The additional inner-wall tabs extend beyond the base slots"),
                         fix=t("Increase return_wall.slot_depth or reduce return_wall.roll_gap")))  # fmt: skip
    # 两组底槽的角部留桥，避免一个方向的孔切到另一个方向的孔。
    if r.lugs and d.lugs:
        for x in r.lug_y:
            for y in d.lug_y:
                if (
                    min(x, d.bl - x) - r.sw / 2 <= d.sd + LUG_SPACING_MM
                    and min(y, d.bw - y) - d.sw / 2 <= r.sd + LUG_SPACING_MM
                ):
                    out.append(Issue(id="S03", message=t("The two groups of base slots are too close at the corner"),
                                     fix=t("Reduce tab width or slot depth, or increase L or W")))  # fmt: skip
                    return out
    return out


def add_return_walls(p: TrayParams, d: TrayDims, tray: Tray) -> Tray:
    """在 front / back 上加同一组卷边机构；原端墙、外墙耳朵的几何保持不变。"""
    from pacxio.engine.tray import Tray, _ccw

    r = return_dims(p, d)
    panels, folds, sizes = dict(tray.panels), dict(tray.folds), dict(tray.sizes)
    creases, cuts, targets = list(tray.creases), list(tray.cuts), list(tray.targets)
    lo, hi, u0 = r.ii, d.bl - r.ii, d.hf + r.rg

    # 先立外墙、折原耳朵，再翻新增内墙，最后端墙夹住两组耳朵。
    for side in ("a", "b"):
        folds[f"rim_{side}"] = {**folds[f"rim_{side}"], "keys": [[5, 90.0]]}
        folds[f"side_{side}_in"] = {
            **folds[f"side_{side}_in"],
            "keys": [[5, 90.0]],
            # 新增耳片被端墙内层夹住；只调整显示分层，不改零厚度折回目标。
            "layer": -END_INNER_LAYERS_WITH_EARS if p.inner_ears else -END_INNER_LAYERS,
        }

    for wall, yb, sign in (("front", 0.0, -1.0), ("back", d.bw, 1.0)):

        def xy(x: float, u: float, yb: float = yb, sign: float = sign) -> tuple[float, float]:
            return x, yb + sign * u

        def add_panel(pid: str, role: PanelRole, points: list[tuple[float, float]]) -> None:
            path = Path.from_points(
                [Vec(*v) for v in _ccw([xy(x, u) for x, u in points])], closed=True
            )
            panels[pid] = (role, path)

        rid, iid = f"rim_{wall}", f"{wall}_in"
        if r.rg > 0:
            add_panel(rid, PanelRole.RIM, [(lo, d.hf), (hi, d.hf), (hi, u0), (lo, u0)])
            creases.append(crease(f"crease_{wall}_{rid}", xy(lo, d.hf), xy(hi, d.hf), [wall, rid]))
            folds[rid] = {"keys": [[3, 90.0]], "layer": 0.0}
            sizes[rid] = (hi - lo, r.rg)
            targets += [(rid, xy(x, u), (d.bl - x, yb - sign * (u - d.hf), d.hf))
                        for x, u in ((lo, d.hf), (hi, d.hf), (hi, u0), (lo, u0))]  # fmt: skip
        ue = u0 + r.hin
        points = [(lo, u0), (lo, ue), *[(v.y, v.x) for v in _lugs(r, ue)], (hi, ue), (hi, u0)]
        add_panel(iid, PanelRole.INNER_WALL, points)
        parent = rid if r.rg > 0 else wall
        creases.append(crease(f"crease_{parent}_{iid}", xy(lo, u0), xy(hi, u0), [parent, iid]))
        folds[iid] = {"keys": [[3, 90.0 if r.rg > 0 else 180.0]], "layer": -RETURN_INNER_LAYERS}
        sizes[iid] = (hi - lo, r.hin + r.lug_total)
        targets += [(iid, xy(x, u), (d.bl - x, yb - sign * r.rg, d.hf - (u - u0)))
                    for x, u in points]  # fmt: skip

        if p.inner_ears:
            for side, xe, sx in (("a", hi, 1.0), ("b", lo, -1.0)):
                eid = f"ear_{wall}_{side}_in"
                a, b = u0 + d.t, ue - r.eg
                ep = [(xe, a), (xe + sx * r.ed, a + r.ed * r.et),
                      (xe + sx * r.ed, b), (xe, b)]  # fmt: skip
                add_panel(eid, PanelRole.DUST_FLAP, ep)
                creases.append(crease(f"crease_{iid}_{eid}", xy(xe, a), xy(xe, b), [iid, eid]))
                # 内耳夹在外耳与端墙内层之间；补偿内缩量，避免不同纸厚的纸板重叠。
                ear_layer = -(d.rg + (END_INNER_LAYERS_WITH_EARS - 1) * d.t - r.ii) / d.t
                folds[eid] = {"keys": [[4, -90.0]], "layer": ear_layer}
                sizes[eid] = (r.ed, b - a)
                targets += [(eid, xy(x, u), (d.bl - xe, yb - sign * (r.rg + sx * (x - xe)),
                                            d.hf - (u - u0))) for x, u in ep]  # fmt: skip

        if r.lugs:
            # 槽在底板内，跨原底板 / 外墙压线；压线在孔处断开。
            creases = [e for e in creases if e.id != f"crease_base_{wall}"]
            spans = [d.X0]
            for n, xc in enumerate(r.lug_y, 1):
                x0, x1 = xc - r.sw / 2, xc + r.sw / 2
                spans += [x0, x1]
                pts = [xy(x0, 0), xy(x0, -r.sd), xy(x1, -r.sd), xy(x1, 0), xy(x0, 0)]
                cuts.append(Edge(id=f"cut_slot_{wall}{n}", type=EdgeType.CUT,
                                 segments=[Segment(Vec(*a), Vec(*b)) for a, b in zip(pts, pts[1:], strict=False)],
                                 panels=["base"]))  # fmt: skip
            spans.append(d.X1)
            for n in range(0, len(spans), 2):
                creases.append(crease(f"crease_base_{wall}_{n // 2 + 1}", xy(spans[n], 0),
                                      xy(spans[n + 1], 0), ["base", wall]))  # fmt: skip

    shape = unary_union([Polygon([(v.start.x, v.start.y) for v in path.segments])
                         for _, path in panels.values()])  # fmt: skip
    if not isinstance(shape, Polygon) or not shape.is_valid:
        raise ValueError(
            t(
                "The outline of the tray with four double walls does not form a valid continuous blank"
            )
        )
    ring = [(float(x), float(y)) for x, y in list(shape.exterior.coords)[:-1]]
    outline = Path.from_points([Vec(*v) for v in _ccw(ring)], closed=True)
    return Tray(outline, panels, creases, cuts, folds, targets, sizes, tray.faces)
