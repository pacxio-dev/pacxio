"""Shared sleeve geometry, extracted byte-for-byte from the validated drawer sleeve."""

from __future__ import annotations

import math
from typing import Any

from pacxio.geometry import Path, Segment, Vec, arc_segment
from pacxio.model.dieline import Dieline, Edge, PanelRole
from pacxio.model.profile import Material, Profile
from pacxio.styles.corrugated import assemble, crease

SLEEVE_ORDER = ("glue", "bottom", "side_a", "top", "side_b")


class Sleeve:
    def build(
        self,
        p: Any,
        d: Any,
        material: Material,
        profile: Profile,
        meta: dict[str, Any],
        *,
        style_id: str,
        part_label: str = "外套",
    ) -> Dieline:
        gw, gi, gt, n = d.gw, d.gi, d.gt, d.length
        widths = {
            "glue": gw,
            "bottom": d.top_w,
            "side_a": d.side_w,
            "top": d.top_w,
            "side_b": d.side_w,
        }
        xs: dict[str, tuple[float, float]] = {}
        x = 0.0
        for name in SLEEVE_ORDER:
            xs[name] = (x, x + widths[name])
            x += widths[name]
        x_end = x
        notched = {"top"} | ({"bottom"} if p.notch_on == "top_bottom" else set())
        r = d.notch_r if p.pull == "notch" else 0.0

        def front_edge(name: str) -> list[Segment]:
            """面的前口（y = 0，自左向右）；有手指缺口时往面里凹。"""
            x0, x1 = xs[name]
            if name not in notched or r <= 0:
                return [Segment(Vec(x0, 0), Vec(x1, 0))]
            cx = 0.5 * (x0 + x1)
            return [Segment(Vec(x0, 0), Vec(cx - r, 0)), arc_segment(Vec(cx, 0), r, math.pi, 0.0),
                    Segment(Vec(cx + r, 0), Vec(x1, 0))]  # fmt: skip

        glue_out = gi + gw * gt
        x_g = xs["glue"][1]
        out: list[Segment] = [
            Segment(Vec(0, glue_out), Vec(x_g, gi)),
            Segment(Vec(x_g, gi), Vec(x_g, 0)),
        ]
        for name in SLEEVE_ORDER[1:]:
            out += front_edge(name)
        out += [Segment(Vec(x_end, 0), Vec(x_end, n)), Segment(Vec(x_end, n), Vec(x_g, n)),
                Segment(Vec(x_g, n), Vec(x_g, n - gi)), Segment(Vec(x_g, n - gi), Vec(0, n - glue_out)),
                Segment(Vec(0, n - glue_out), Vec(0, glue_out))]  # fmt: skip
        outline = Path(out, closed=True)

        def panel_path(name: str) -> Path:
            x0, x1 = xs[name]
            return Path([*front_edge(name), Segment(Vec(x1, 0), Vec(x1, n)), Segment(Vec(x1, n), Vec(x0, n)),
                         Segment(Vec(x0, n), Vec(x0, 0))], closed=True)  # fmt: skip

        polys: dict[str, tuple[PanelRole, Path]] = {
            "glue": (
                PanelRole.GLUE_FLAP,
                Path.from_points(
                    [Vec(x_g, gi), Vec(x_g, n - gi), Vec(0, n - glue_out), Vec(0, glue_out)],
                    closed=True,
                ),
            ),  # fmt: skip
            **{name: (PanelRole.BODY, panel_path(name)) for name in SLEEVE_ORDER[1:]},
        }
        creases: list[Edge] = [
            crease("crease_bottom_glue", (x_g, gi), (x_g, n - gi), ["bottom", "glue"])
        ]
        for a, b in zip(SLEEVE_ORDER[1:], SLEEVE_ORDER[2:], strict=False):
            xb = xs[b][0]
            creases.append(crease(f"crease_{a}_{b}", (xb, 0), (xb, n), [a, b]))
        return assemble(
            style_id=style_id, params=p, material=material, profile=profile, outline=outline, polys=polys,
            creases=creases, inner_cuts=[], ups={"side_a": "right", "side_b": "left"},
            provisional={"bottom", "side_a", "side_b", "glue"},
            meta={**meta, "part": "sleeve", "part_label": part_label, "layout": "sleeve",
                  "faces": {"top": "top", "bottom": "bottom", "side_a": "side_a", "side_b": "side_b"},
                  "glue_note": "The sleeve is glued at the side seam (joint flap inside the right side): normally straight-line glued on a folder-gluer; small runs can be glued by hand",
                  "dims": {"Li": d.Ls, "Wi": n, "Hi": d.Hs, "glue_w": gw, "outer": [d.top_w + d.t, n, d.side_w + d.t]}},
        )  # fmt: skip
