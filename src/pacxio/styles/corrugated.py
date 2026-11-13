"""瓦楞盒型共用的部分（ 节）：材料提醒、面的朝向、归一化成 Dieline。

瓦楞盒型的几何与 RTE 系列不同（十字形展开、开槽），只复用 rte 里的小工具；这里放两个瓦楞盒型
共用、又不属于 RTE 的东西。所有数值都是【初值】。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from pacxio.geometry import Affine, Path, Segment
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, Hinge, Panel, PanelRole
from pacxio.model.profile import Material, Profile
from pacxio.styles.base import ORIENTATION_VERIFIED

ADVISORY_ID = "S04"  # 盒型对材料 / 尺寸的建议：只提醒，不拦（S03 才是约束）
Up = Literal["up", "down", "right", "left"]  # 面上图文的"上"在展开图里朝哪


def advisory(message: str, fix: str) -> Issue:
    return Issue(id=ADVISORY_ID, message=message, fix=fix)


def flute_advisory(material: Material, usual: set[str], what: str) -> list[Issue]:
    """楞型不是这个盒型常用的：提醒（不拦）。"""
    flute = (material.flute or "").upper()
    if not flute or flute in usual:
        return []
    names = " / ".join(sorted(usual))
    return [advisory(t("{style} normally uses {usual} flute. This job uses {flute} flute (provisional guidance).", style=what, usual=names, flute=flute),
                     t("Confirm that {flute} flute is intended, or choose a {usual} flute material preset", flute=flute, usual=names))]  # fmt: skip


def corrugated_only(style_id: str, material: Material) -> list[Issue]:
    if material.type == "corrugated":
        return []
    return [Issue(id="S03", message=t("{style} is a corrugated style. The current material type is {material}.", style=style_id, material=material.type),
                  fix=t("Use a corrugated preset (flute_e, flute_b, flute_c, or flute_bc)"))]  # fmt: skip


def frame_for(up: Up, bbox: tuple[float, float, float, float]) -> Affine:
    """面局部坐标 → 刀版坐标：局部 +y 是图文的"上"，局部原点在正立时的左下角。"""
    x0, y0, x1, y1 = bbox
    if up == "down":
        return Affine(-1, 0, 0, -1, x1, y1)
    if up == "right":  # 局部 +y → 刀版 +x
        return Affine(0, -1, 1, 0, x0, y1)
    if up == "left":  # 局部 +y → 刀版 −x
        return Affine(0, 1, -1, 0, x1, y0)
    return Affine.translate(x0, y0)


def crease(eid: str, a: tuple[float, float], b: tuple[float, float], panels: list[str],
           kind: EdgeType = EdgeType.CREASE) -> Edge:  # fmt: skip
    """panels = [父面, 子面]（子面的铰链挂在父面上）。"""
    from pacxio.geometry import Vec

    return Edge(id=eid, type=kind, segments=[Segment(Vec(*a), Vec(*b))], panels=panels)


def assemble(
    *,
    style_id: str,
    params: BaseModel,
    material: Material,
    profile: Profile,
    outline: Path,
    polys: dict[str, tuple[PanelRole, Path]],
    creases: list[Edge],
    inner_cuts: list[Edge],
    ups: dict[str, Up],
    provisional: set[str],
    meta: dict[str, Any],
    hinges: dict[str, str] | None = None,
) -> Dieline:
    """把面、线归一化（原点移到刀版包围盒左下角，D3）并组成 Dieline。

    hinges：子面 → 铰链压线 id（默认取以它为子面的第一条压线）。
    """
    minx, miny, maxx, maxy = outline.bbox()
    shift = Affine.translate(-minx, -miny)
    edges = [
        Edge(id="cut_outline", type=EdgeType.CUT, segments=outline.segments),
        *creases,
        *inner_cuts,
    ]
    edges = [
        e.model_copy(update={"segments": [shift.apply_segment(s) for s in e.segments]})
        for e in edges
    ]
    hinge_of: dict[str, Hinge] = {}
    for e in creases:
        hinge_of.setdefault(e.panels[1], Hinge(parent_panel=e.panels[0], edge_id=e.id))
    for child, eid in (hinges or {}).items():
        parent = next(e.panels[0] for e in creases if e.id == eid)
        hinge_of[child] = Hinge(parent_panel=parent, edge_id=eid)
    verified = style_id in ORIENTATION_VERIFIED  # 打样确认过的盒型：没有"方向待定"的面
    panels: list[Panel] = []
    for pid, (role, poly) in polys.items():
        poly = poly.transformed(shift)
        panels.append(
            Panel(
                id=pid,
                role=role,
                polygon=poly,
                frame=frame_for(ups.get(pid, "up"), poly.bbox()),
                hinge=hinge_of.get(pid),
                printable=not (role == PanelRole.GLUE_FLAP and profile.glue_flap.ink == "none"),
                orientation_provisional=pid in provisional and not verified,
            )
        )
    return Dieline(
        style=style_id,
        params=params.model_dump(),
        material_preset=material.preset,
        caliper_mm=material.caliper_mm,
        profile_id=profile.id,
        style_meta={"orientation_verified": verified, **meta},
        bbox=(0.0, 0.0, maxx - minx, maxy - miny),
        panels=panels,
        edges=edges,
    )
