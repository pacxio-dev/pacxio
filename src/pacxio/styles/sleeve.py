"""Standalone sleeve and belly-band adapters over the validated sleeve component."""

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.components.sleeve import Sleeve

GLUE_MARGIN_MM = 2.0
NOTCH_CAP_MM = 12.0


class SleeveParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0, description="Sleeve inside clear width")
    W: float = Field(gt=0, description="Length along the tube axis / belly band width")
    H: float = Field(gt=0, description="Sleeve inside clear height")
    dim_basis: Literal["inner", "outer"] = "inner"
    glue_w: float = Field(default=15, ge=5, le=30)
    glue_inset: float = Field(default=2, ge=0, le=5)
    glue_bevel_deg: float = Field(default=15, ge=0, le=30)
    pull: Literal["none", "notch"] = "none"
    notch_on: Literal["top", "top_bottom"] = "top"
    notch_r: float | None = Field(default=None, gt=0)
    apply_compensation: bool = True


@dataclass(frozen=True)
class SleeveDims:
    t: float
    Ls: float
    Hs: float
    top_w: float
    side_w: float
    length: float
    gw: float
    gi: float
    gt: float
    notch_r: float


def resolve(p: SleeveParams, m: Material, profile: Profile) -> SleeveDims:
    t = m.caliper_mm
    k = coefficients(profile, m, p.apply_compensation)
    li = p.L - 2 * t if p.dim_basis == "outer" else p.L
    hi = p.H - 2 * t if p.dim_basis == "outer" else p.H
    top, side = li + k.k_panel * t, hi + k.k_panel * t
    return SleeveDims(
        t,
        li,
        hi,
        top,
        side,
        p.W,
        p.glue_w,
        p.glue_inset,
        math.tan(math.radians(p.glue_bevel_deg)),
        p.notch_r if p.notch_r is not None else min(NOTCH_CAP_MM, 0.25 * top, 0.3 * p.W),
    )


class SleeveStyle:
    id: ClassVar[str] = "sleeve"
    name_zh: ClassVar[str] = "独立外套"
    version: ClassVar[str] = "0.1.0-c2"
    refs: ClassVar[dict[str, str]] = {
        "design": "沿用 drawer 的四面筒外套机构；独立净尺寸，未另做标准校准"
    }
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})

    def param_schema(self) -> type[SleeveParams]:
        return SleeveParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        errors = []
        d = resolve(params, material, profile or load_profile("default"))

        def bad(msg: str) -> None:
            errors.append(
                Issue(
                    id="S03",
                    message=msg,
                    fix=translate(
                        "Check the inside dimensions, material, glue flap, and notch parameters"
                    ),
                )
            )

        if material.type not in self.material_types:
            bad(translate("Sleeves and belly bands support folding carton board"))
        if d.Ls < 30 or d.Hs < 15 or d.length < 15:
            bad(
                translate(
                    "Minimum inside width is 30 mm. Minimum usable height and sleeve length are 15 mm (provisional)."
                )
            )
        if d.gw > d.side_w - GLUE_MARGIN_MM:
            bad(translate("The glue flap must fit on the side panel with 2 mm clearance"))
        if d.gi + d.gw * d.gt >= d.length / 2:
            bad(
                translate(
                    "Too little straight edge remains after beveling both ends of the glue flap"
                )
            )
        if params.pull == "notch" and not 0 < d.notch_r < min(0.4 * d.top_w, 0.5 * d.length):
            bad(translate("The notch radius exceeds the panel width or sleeve length"))
        return errors

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = resolve(params, material, profile)
        return {
            "bottom": (d.top_w, d.length),
            "top": (d.top_w, d.length),
            "side_a": (d.side_w, d.length),
            "side_b": (d.side_w, d.length),
        }

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        d = resolve(params, material, profile)
        dl = Sleeve().build(
            params,
            d,
            material,
            profile,
            {
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "compensation_applied": params.apply_compensation,
                "accuracy_level": "L0",
                "assumptions": [
                    "独立净尺寸，不包含产品配合间隙；两端开口，糊口 / 图文方向与配合均待校准折样【初值】"
                ],
            },
            style_id=self.id,
            part_label=self.name_zh,
        )
        dl.style_meta.pop("part", None)
        dl.style_meta.pop("part_label", None)
        return dl


class BellyBandStyle(SleeveStyle):
    id: ClassVar[str] = "belly_band"
    name_zh: ClassVar[str] = "腰封"
