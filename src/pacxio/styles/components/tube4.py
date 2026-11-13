"""The shared four-wall carton body; no lid, bottom or glue-flap shape policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Protocol

from pacxio.geometry import Path, Segment, Vec
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.styles.components.base import Piece, Port


class BodyDimensions(Protocol):
    """Resolved body sizes. RteDims satisfies this without a reverse import."""

    @property
    def gw(self) -> float: ...
    @property
    def wb(self) -> float: ...
    @property
    def ws(self) -> float: ...
    @property
    def hb(self) -> float: ...
    @property
    def off(self) -> float: ...


class BodyX(NamedTuple):
    """Legacy unnormalized vertical crease x positions, preserving arithmetic order."""

    xb0: float
    xb1: float
    xa1: float
    xf1: float
    xs1: float


@dataclass
class TubeBody:
    xs: BodyX
    piece: Piece
    ports: dict[str, Port]


@dataclass(frozen=True)
class Tube4:
    """Back → side_a → front → side_b, with side-wall dust-offset steps."""

    def build(self, dims: BodyDimensions) -> TubeBody:
        xb0 = dims.gw
        xb1 = xb0 + dims.wb
        xa1 = xb1 + dims.ws
        xf1 = xa1 + dims.wb
        xs1 = xf1 + dims.ws
        hb, off = dims.hb, dims.off
        top = getattr(dims, "top_off", off)  # 顶端让位（几何引擎可以和底端不同）
        panels = {
            "back": (PanelRole.BODY, Path.rect(xb0, 0, xb1 - xb0, hb)),
            "side_a": (PanelRole.BODY, Path.rect(xb1, off, xa1 - xb1, hb - off - top)),
            "front": (PanelRole.BODY, Path.rect(xa1, 0, xf1 - xa1, hb)),
            "side_b": (PanelRole.BODY, Path.rect(xf1, off, xs1 - xf1, hb - off - top)),
        }
        creases = [
            Edge(
                id=eid,
                type=EdgeType.CREASE,
                segments=[Segment(Vec(x, off), Vec(x, hb - top))],
                panels=pair,
            )
            for eid, x, pair in (
                ("crease_back_side_a", xb1, ["back", "side_a"]),
                ("crease_side_a_front", xa1, ["side_a", "front"]),
                ("crease_front_side_b", xf1, ["front", "side_b"]),
            )
        ]
        ports = {}
        for pid, x0, x1, y0, y1 in (
            ("back", xb0, xb1, 0.0, hb),
            ("side_a", xb1, xa1, off, hb - top),
            ("front", xa1, xf1, 0.0, hb),
            ("side_b", xf1, xs1, off, hb - top),
        ):
            ports[f"{pid}.top"] = Port(f"{pid}.top", pid, Vec(x1, y1), Vec(x0, y1), Vec(0, 1))
            ports[f"{pid}.bottom"] = Port(
                f"{pid}.bottom", pid, Vec(x0, y0), Vec(x1, y0), Vec(0, -1)
            )
        ports["back.glue"] = Port("back.glue", "back", Vec(xb0, hb), Vec(xb0, 0), Vec(-1, 0))
        return TubeBody(
            BodyX(xb0, xb1, xa1, xf1, xs1), Piece(panels=panels, creases=creases), ports
        )
