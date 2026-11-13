"""Tuck lids and handed dust flaps, attached through the shared body ports."""

from dataclasses import dataclass

from pacxio.geometry import Segment, Vec
from pacxio.model.dieline import PanelRole
from pacxio.styles.components.base import Piece, Port
from pacxio.styles.components.carton import (
    DUST_BASE_INSET_MM,
    RteDims,
    _backwards,
    _chain,
    _closed_flap,
    _crease,
    _dust_flap,
    _tuck_segments,
    tuck_panels,
)
from pacxio.styles.components.tube4 import TubeBody


@dataclass(frozen=True)
class TuckLid:
    dims: RteDims
    prefix: str
    lock: bool = True
    slit: str = "cut"  # 插舌压线两端：切口（默认）/ 上方短压线（老做法）

    def build(self, port: Port) -> Piece:
        d = self.dims
        x0, x1 = sorted((port.start.x, port.end.x))
        y = port.start.y
        up = port.outward.y > 0
        outer = y + d.ld if up else y - d.ld
        lid, tuck = f"{self.prefix}_lid", f"{self.prefix}_tuck"
        contour = _tuck_segments(d, 0.5 * (x0 + x1), outer, up)
        points = (
            [Vec(x1, y), Vec(x1, outer), Vec(x1 - d.ti, outer)]
            if up
            else [Vec(x0, y), Vec(x0, outer), Vec(x0 + d.ti, outer)]
        )
        tail = (
            [Vec(x0 + d.ti, outer), Vec(x0, outer), Vec(x0, y)]
            if up
            else [Vec(x1 - d.ti, outer), Vec(x1, outer), Vec(x1, y)]
        )
        lid_path, tuck_path, hinge, inner = tuck_panels(
            d, x0, x1, y, outer, up, contour, lid, tuck, self.lock, self.slit
        )
        return Piece(
            outline=[*_chain(points), *contour, *_chain(tail)],
            panels={
                lid: (PanelRole.LID, lid_path),
                tuck: (PanelRole.TUCK, tuck_path),
            },
            creases=[
                _crease(f"crease_{port.parent}_{lid}", (x0, y), (x1, y), [port.parent, lid]),
                *hinge,
            ],
            inner_edges=inner,
        )


@dataclass(frozen=True)
class DustFlap:
    dims: RteDims
    id: str
    tuck_at_x1: bool
    lock: bool = True
    inset: float = DUST_BASE_INSET_MM  # 根部离面分界：彩盒 1，开槽箱 = 开槽宽 / 2

    def build(self, port: Port) -> Piece:
        x0, x1 = sorted((port.start.x, port.end.x))
        y = port.start.y
        raw = _dust_flap(
            self.dims, x0, x1, y, port.outward.y > 0, self.tuck_at_x1, self.lock, self.inset
        )
        e = self.inset
        return Piece(
            outline=_backwards(raw) if port.start.x > port.end.x else raw,
            panels={self.id: (PanelRole.DUST_FLAP, _closed_flap(raw))},
            creases=[
                _crease(
                    f"crease_{port.parent}_{self.id}",
                    (x0 + e, y),
                    (x1 - e, y),
                    [port.parent, self.id],
                )
            ],
        )


@dataclass(frozen=True)
class TuckTop:
    dims: RteDims
    lock: bool = True
    lid_on: str = "back"
    slit: str = "cut"

    def build(self, body: TubeBody, extra: dict[str, Piece] | None = None) -> Piece:
        attachments = {
            self.lid_on: TuckLid(self.dims, "top", self.lock, self.slit).build(
                body.ports[f"{self.lid_on}.top"]
            ),
            "side_a": DustFlap(self.dims, "dust_ta", self.lid_on == "back", self.lock).build(
                body.ports["side_a.top"]
            ),
            "side_b": DustFlap(self.dims, "dust_tb", self.lid_on != "back", self.lock).build(
                body.ports["side_b.top"]
            ),
            **(extra or {}),
        }
        result = Piece()
        previous: Vec | None = None
        for pid in ("side_b", "front", "side_a", "back"):
            port = body.ports[f"{pid}.top"]
            if previous is not None:
                result.outline += _chain([previous, port.start])
            part = attachments.get(pid)
            if part is None:
                result.outline.append(Segment(port.start, port.end))
            else:
                result.outline += _chain([port.start, part.outline[0].start])
                result.outline += part.outline
                result.outline += _chain([part.outline[-1].end, port.end])
            previous = port.end
        # Stable legacy order is part of the export contract.
        for pid in (self.lid_on, "side_a", "side_b", *(extra or {})):
            part = attachments[pid]
            result.panels.update(part.panels)
            result.creases.extend(part.creases)
            result.inner_edges.extend(part.inner_edges)
        return result
