"""Static cut-paper views from the same hinge matrices used by interactive 3D."""

from copy import copy
from typing import cast

from PIL import Image

from pacxio.export.canvas import _Map
from pacxio.mockup.art import _geom_mask
from pacxio.mockup.fold import BoxModel, Face, Vec3, build_box
from pacxio.mockup.folding import (
    _panel_shape,
    apply,
    build_part,
    closed_pose,
    panel_matrices,
    plan_for,
)
from pacxio.mockup.scene import _normalize, paper_rgb
from pacxio.model.dieline import Dieline

Corners = tuple[Vec3, Vec3, Vec3, Vec3]


def open_paper_view(
    dl: Dieline,
    art: Image.Image,
    mapping: _Map,
    *,
    step: float | None = None,
    box: BoxModel | None = None,
) -> tuple[BoxModel, dict[str, Image.Image]]:
    """Show physical paper at open_step (or a chosen step), with its unprinted reverse.

    box：摆正用的方盒子；多部件盒型的一个部件给 `scene.part_box(dl)`，不给就按单部件的 `build_box`。"""
    plan = plan_for(dl)
    if plan is None:
        raise ValueError(f"{dl.style} 没有折叠方案，不能生成真实开箱图")
    part = build_part(dl, plan)
    pose = closed_pose(part, box if box is not None else build_box(dl))
    matrices = panel_matrices(part, plan.open_step if step is None else step)
    faces = []
    textures = {}
    for panel in dl.panels:
        matrix = pose @ matrices[panel.id]
        normal = cast(Vec3, tuple(float(v) for v in matrix[:3, 2]))
        a, b, c, d = panel.polygon.bbox()
        corners = cast(
            Corners, tuple(apply(matrix, u, v) for u, v in ((a, d), (c, d), (c, b), (a, b)))
        )
        back_corners = cast(
            Corners,
            tuple(apply(matrix, u, v, -dl.caliper_mm) for u, v in ((a, d), (c, d), (c, b), (a, b))),
        )
        left, top = mapping.pt(a, d)
        right, bottom = mapping.pt(c, b)
        crop = (round(left), round(top), round(right), round(bottom))
        ink = art.crop(crop).convert("RGBA")
        local = copy(mapping)
        local.x0 += crop[0] / mapping.s
        local.y1 -= crop[1] / mapping.s
        cut_alpha = _geom_mask(ink.size, _panel_shape(dl, panel.id), local)
        ink.putalpha(cut_alpha)
        paper = Image.new("RGBA", ink.size, paper_rgb(dl))
        paper.putalpha(cut_alpha)
        faces.append(Face(panel.id, panel.id, panel.id, normal, (a, b, c, d), corners))
        back = f"{panel.id}:reverse"
        faces.append(
            Face(
                back,
                panel.id,
                panel.id,
                cast(Vec3, tuple(-v for v in normal)),
                (a, b, c, d),
                back_corners,
            )
        )
        textures[panel.id] = ink
        textures[back] = paper
    return _normalize(faces), textures
