"""Glued-end carton, provisional custom recipe without an asserted ECMA number."""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.components.assembly import stitch
from pacxio.styles.components.carton import GLUE_BEVEL_MM, _chain, _crease
from pacxio.styles.components.glue_end import GlueFlapEnd
from pacxio.styles.components.tube4 import Tube4
from pacxio.styles.corrugated import assemble
from pacxio.styles.rte import RteDims, RteParams, resolve


class SealParams(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    L: float = Field(gt=0)
    W: float = Field(gt=0)
    H: float = Field(gt=0)
    dim_basis: Literal["inner", "outer"] = "inner"
    glue_w: float = Field(default=15, ge=10, le=20)
    end_overlap: float = Field(default=10, ge=2)
    end_relief: float = Field(default=1, gt=0)
    glue_band: float = Field(default=5, gt=0)
    apply_compensation: bool = True


def _dims(p: SealParams, material: Material, profile: Profile) -> RteDims:
    t = material.caliper_mm
    values = {k: getattr(p, k) for k in ("L", "W", "H")}
    if p.dim_basis == "outer":
        values = {"L": p.L - 2 * t, "W": p.W - 2 * t, "H": p.H - 6 * t}
    return resolve(
        RteParams(
            **values, glue_w=p.glue_w, dust_offset=0, apply_compensation=p.apply_compensation
        ),
        material,
        profile,
    )


class SealEndStyle:
    id: ClassVar[str] = "seal_end"
    name_zh: ClassVar[str] = "粘合封口盒"
    version: ClassVar[str] = "0.1.0-c1"
    refs: ClassVar[dict[str, str]] = {"design": "两端四翼搭粘；ECMA 编号待核对"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})

    def param_schema(self) -> type[SealParams]:
        return SealParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        errors = []
        try:
            if material.type not in self.material_types:
                raise ValueError(translate("Seal-end cartons support folding carton board"))
            d = _dims(params, material, profile or load_profile("default"))
            if min(d.Li, d.Wi, d.Hi) < 20:
                raise ValueError(
                    translate(
                        "Seal-end cartons require inside dimensions of at least 20 mm (provisional)"
                    )
                )
            if params.glue_w > d.Wi - 2 or not 2 <= params.end_overlap <= d.Wi - 2:
                raise ValueError(
                    translate(
                        "glue_w and end_overlap must fit inside the width with 2 mm clearance"
                    )
                )
            if (
                params.end_relief >= min(d.wb, d.ws) / 2
                or params.glue_band + params.end_relief > d.Wi + params.end_overlap - d.ws
            ):
                raise ValueError(
                    translate(
                        "Flap relief is too large, or the glue band and relief extend past the overlap"
                    )
                )
        except ValueError as e:
            errors.append(
                Issue(
                    id="S03",
                    message=str(e),
                    fix=translate(
                        "Check dimensions, material, glue flap, flap relief, end_overlap, and glue_band"
                    ),
                )
            )
        return errors

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = _dims(params, material, profile)
        result = {
            "back": (d.wb, d.hb),
            "front": (d.wb, d.hb),
            "side_a": (d.ws, d.hb),
            "side_b": (d.ws, d.hb),
        }
        for end in ("top", "btm"):
            for pid, w in (("back", d.wb), ("front", d.wb), ("side_a", d.ws), ("side_b", d.ws)):
                depth = (
                    (d.Wi + params.end_overlap) / 2
                    if pid in ("front", "back")
                    else min(d.Wi / 2, d.Li / 2 - params.end_relief)
                )
                result[f"{end}_{pid}"] = (w - 2 * params.end_relief, depth)
        return result

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        d = _dims(params, material, profile)
        body = Tube4().build(d)
        recipe = GlueFlapEnd(d, params.end_overlap, params.end_relief, params.glue_band)
        top, bottom = recipe.build(body, True), recipe.build(body, False)
        gw, hb = d.gw, d.hb
        end = body.xs.xs1
        bevel = GLUE_BEVEL_MM
        glue = Path.from_points([Vec(gw, 0), Vec(gw, hb), Vec(0, hb - bevel), Vec(0, bevel)])
        outline = stitch(
            _chain([Vec(0, bevel), Vec(gw, 0)]),
            bottom.outline,
            _chain([Vec(end, 0), Vec(end, hb)]),
            top.outline,
            _chain([Vec(gw, hb), Vec(0, hb - bevel), Vec(0, bevel)]),
        )
        polys = {
            "glue": (PanelRole.GLUE_FLAP, glue),
            **body.piece.panels,
            **top.panels,
            **bottom.panels,
        }
        dl = assemble(
            style_id=self.id,
            params=params,
            material=material,
            profile=profile,
            outline=outline,
            polys=polys,
            creases=[
                _crease("crease_glue_back", (gw, 0), (gw, hb), ["back", "glue"]),
                *body.piece.creases,
                *bottom.creases,
                *top.creases,
            ],
            inner_cuts=[],
            ups={"top_back": "down"},
            provisional={pid for pid in polys if pid not in body.piece.panels},
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "accuracy_level": "L0",
                "compensation_applied": params.apply_compensation,
                "top": {"halves": ["top_front", "top_back"], "overlap": True},
                "bottom": {"type": "sealed", "halves": ["btm_front", "btm_back"], "overlap": True},
                "dims": {
                    "Li": d.Li,
                    "Wi": d.Wi,
                    "Hi": d.Hi,
                    "outer": [d.Li + 2 * d.t, d.Wi + 2 * d.t, d.Hi + 6 * d.t],
                    "body_height": d.hb,
                    "flap": (d.Wi + params.end_overlap) / 2,
                },
                "assumptions": [
                    "两端四翼搭粘，重叠和胶带为初值；外高按每端最多三层厚度换算，须折样校准；ECMA 编号及朝向未确认"
                ],
            },
        )
        x0, y0, _, _ = outline.bbox()
        dl.glue_areas = [
            g.transformed(Affine.translate(-x0, -y0)) for g in [*top.glue_areas, *bottom.glue_areas]
        ]
        return dl
