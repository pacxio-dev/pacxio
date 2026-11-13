"""Static and rigid assembly poses for C2 platforms and quantity-based dividers."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from PIL import Image

from pacxio.mockup.fold import BoxModel, Face, Vec3
from pacxio.mockup.folding import Mat, apply
from pacxio.model.dieline import Dieline

if TYPE_CHECKING:
    from pacxio.mockup.scene import Scene, Source

Corners = tuple[Vec3, Vec3, Vec3, Vec3]

DIVIDER_LIFT_MM = 20.0  # Display gap only; no production allowance.


def interior_surfaces(
    box: BoxModel, textures: dict[str, Image.Image], dl: Dieline
) -> tuple[BoxModel, dict[str, Image.Image]]:
    """Add the paper reverse of open walls for static views, retaining all cutouts."""
    from pacxio.mockup.scene import paper_rgb

    faces = list(box.faces)
    maps = dict(textures)
    for face in box.faces:
        key = f"{face.key}:inside"
        corners = cast(
            Corners,
            tuple(
                tuple(c[i] - face.normal[i] * dl.caliper_mm for i in range(3)) for c in face.corners
            ),
        )
        faces.append(
            replace(
                face, key=key, normal=cast(Vec3, tuple(-v for v in face.normal)), corners=corners
            )
        )
        source = textures[face.key].convert("RGBA")
        paper = Image.new("RGBA", source.size, paper_rgb(dl))
        paper.putalpha(source.getchannel("A"))
        maps[key] = paper
    return replace(box, faces=faces), maps


def platform_box(dl: Dieline) -> BoxModel:
    x0, y0, x1, y1 = dl.panel("platform").polygon.bbox()
    height = float(dl.params["H"])
    box = BoxModel(x1 - x0, y1 - y0, height)

    def point(pid: str, u: float, v: float) -> Vec3:
        if pid == "platform":
            return u - x0, v - y0, height
        if pid == "front":
            return u - x0, 0, height + v - y0
        return u - x0, y1 - y0, height - (v - y1)

    for key, pid, normal in (
        ("top", "platform", (0, 0, 1)),
        ("front", "front", (0, -1, 0)),
        ("back", "back", (0, 1, 0)),
    ):
        a, b, c, d = dl.panel(pid).polygon.bbox()
        box.faces.append(
            Face(
                key,
                pid,
                key,
                normal,
                (a, b, c, d),
                cast(Corners, tuple(point(pid, u, v) for u, v in ((a, d), (c, d), (c, b), (a, b)))),
            )
        )
    return box


def divider_pose(dl: Dieline, index: int, opened: bool = False) -> Mat:
    p = dl.params
    part = dl.style_meta["part"]
    t = dl.caliper_mm
    m = np.eye(4)
    if part == "x":
        m[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
        m[:3, 3] = [0, index * p["W"] / p["rows"] - t / 2, 0]
    else:
        m[:3, :3] = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]
        m[:3, 3] = [
            index * p["L"] / p["cols"] + t / 2,
            0,
            p["H"] + DIVIDER_LIFT_MM if opened else 0,
        ]
    return m


def divider_instances(parts: dict[str, Dieline]) -> list[tuple[str, str, int]]:
    return [
        (f"{name}_{i}", name, i)
        for name, dl in parts.items()
        for i in range(1, int(dl.style_meta["quantity"]) + 1)
    ]


def divider_poses(parts: dict[str, Dieline], pose: Any) -> dict[str, list[dict[str, Any]]]:
    out = {}
    flat_x = 0.0
    for ident, name, i in divider_instances(parts):
        dl = parts[name]
        flat = np.eye(4)
        flat[0, 3] = flat_x
        opened = divider_pose(dl, i, True)
        closed = divider_pose(dl, i)
        out[ident] = [pose(0, flat), pose(1, opened), pose(2, opened), pose(3, closed)]
        flat_x += dl.bbox[2] + DIVIDER_LIFT_MM
    return out


def divider_scene(parts: dict[str, Dieline], opened: bool = False) -> Scene:
    from pacxio.mockup.scene import Scene, _normalize

    sources: dict[str, Source] = {}
    faces = []
    for ident, name, i in divider_instances(parts):
        dl = parts[name]
        m = divider_pose(dl, i, opened)
        a, b, c, d = dl.panel("front").polygon.bbox()
        rect = (a, b, c, d)
        corners = tuple(apply(m, u, v) for u, v in ((a, d), (c, d), (c, b), (a, b)))
        normal = cast(Vec3, tuple(float(v) for v in m[:3, 2]))
        faces.append(
            Face(
                ident,
                f"{name}.front",
                dl.style_meta["part_label"],
                normal,
                rect,
                cast(Corners, corners),
            )
        )
        sources[ident] = (name, rect)
        backside = tuple(
            apply(m, u, v, -dl.caliper_mm) for u, v in ((a, d), (c, d), (c, b), (a, b))
        )
        faces.append(
            Face(
                f"{ident}:in",
                f"{name}.front",
                dl.style_meta["part_label"],
                cast(Vec3, tuple(-v for v in normal)),
                rect,
                cast(Corners, backside),
            )
        )
        sources[f"{ident}:in"] = (name, rect)
    return Scene(_normalize(faces), sources, [], "纵向隔条提起后，沿半高槽插合")
