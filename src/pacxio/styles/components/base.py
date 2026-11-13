"""Ports and local pieces for carton modules (C1).

Coordinates remain millimeters, lines and circular arcs. A port provides an
orthonormal local frame: u along the attachment edge, v outward from its parent.
Placing a piece preserves edge/panel identifiers and their insertion order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pacxio.geometry import EPS, Affine, Path, Segment, Vec
from pacxio.messages import t
from pacxio.model.dieline import Edge, PanelRole


@dataclass(frozen=True)
class Port:
    id: str
    parent: str
    start: Vec
    end: Vec
    outward: Vec

    @property
    def width(self) -> float:
        return self.start.dist(self.end)

    @property
    def frame(self) -> Affine:
        """Map local (u, v) into the unnormalized dieline, including reflections."""
        u, v = (self.end - self.start).normalized(), self.outward.normalized()
        if abs(u.dot(v)) > EPS:
            raise ValueError(
                t(
                    "The outward normal at attachment {attachment} must be perpendicular to its edge",
                    attachment=self.id,
                )
            )
        return Affine(u.x, u.y, v.x, v.y, self.start.x, self.start.y)


@dataclass
class Piece:
    """An open replacement boundary plus named faces, creases, cuts and glue areas."""

    outline: list[Segment] = field(default_factory=list)
    panels: dict[str, tuple[PanelRole, Path]] = field(default_factory=dict)
    creases: list[Edge] = field(default_factory=list)
    inner_edges: list[Edge] = field(default_factory=list)
    glue_areas: list[Path] = field(default_factory=list)

    def transformed(self, frame: Affine) -> Piece:
        """Transform a fresh copy without approximating arcs or mutating the source."""

        def edges(values: list[Edge]) -> list[Edge]:
            return [
                edge.model_copy(
                    update={
                        "segments": [frame.apply_segment(s) for s in edge.segments],
                        "panels": list(edge.panels),
                    }
                )
                for edge in values
            ]

        return Piece(
            outline=[frame.apply_segment(s) for s in self.outline],
            panels={
                pid: (role, path.transformed(frame)) for pid, (role, path) in self.panels.items()
            },
            creases=edges(self.creases),
            inner_edges=edges(self.inner_edges),
            glue_areas=[path.transformed(frame) for path in self.glue_areas],
        )


class Component(Protocol):
    """An attachment recipe; resolved dimensions belong to the component instance."""

    def build(self, port: Port) -> Piece: ...
