"""Four glued end flaps, with explicit overlap and interior glue-band regions."""

from dataclasses import dataclass

from pacxio.geometry import Path, Segment, Vec
from pacxio.model.dieline import PanelRole
from pacxio.styles.components.base import Piece
from pacxio.styles.components.carton import RteDims, _backwards, _chain, _crease
from pacxio.styles.components.tube4 import TubeBody


@dataclass(frozen=True)
class GlueFlapEnd:
    dims: RteDims
    overlap: float
    relief: float
    glue_band: float

    def build(self, body: TubeBody, top: bool) -> Piece:
        end = "top" if top else "btm"
        result = Piece()
        ids: tuple[str, ...] = ("back", "side_a", "front", "side_b")
        if top:
            ids = tuple(reversed(ids))
        previous = None
        for face in ids:
            port = body.ports[f"{face}.{'top' if top else 'bottom'}"]
            x0, x1 = sorted((port.start.x, port.end.x))
            y = port.start.y
            depth = (
                (self.dims.Wi + self.overlap) / 2
                if face in ("back", "front")
                else min(self.dims.Wi / 2, self.dims.Li / 2 - self.relief)
            )
            outer = y + depth if top else y - depth
            a, b = x0 + self.relief, x1 - self.relief
            raw = _chain([Vec(a, y), Vec(a, outer), Vec(b, outer), Vec(b, y)])
            outline = _backwards(raw) if top else raw
            if previous is not None:
                result.outline += _chain([previous, port.start])
            result.outline += (
                _chain([port.start, outline[0].start])
                + outline
                + _chain([outline[-1].end, port.end])
            )
            previous = port.end
            pid = f"{end}_{face}"
            poly = Path([*raw, Segment(raw[-1].end, raw[0].start)], closed=True)
            result.panels[pid] = (
                PanelRole.LID
                if top and face in ("back", "front")
                else PanelRole.BOTTOM
                if not top and face in ("back", "front")
                else PanelRole.DUST_FLAP,
                poly,
            )
            result.creases.append(_crease(f"crease_{face}_{end}", (a, y), (b, y), [face, pid]))
            if face == "back":
                gy = outer - self.relief - self.glue_band if top else outer + self.relief
                result.glue_areas.append(Path.rect(a, gy, b - a, self.glue_band))
        return result
