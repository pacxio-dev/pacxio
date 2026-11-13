"""FEFCO 0200/0202/0203 recipes; dimensions remain provisional until calibration."""

from typing import Any, ClassVar

from pydantic import ConfigDict, Field

from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, Profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.fefco_0201 import SlotDims, SlottedParams, SlottedStyle


class HalfSlottedParams(SlottedParams):
    model_config = ConfigDict(allow_inf_nan=False)


class HalfSlottedStyle(SlottedStyle):
    id: ClassVar[str] = "fefco_0200"
    name_zh: ClassVar[str] = "半开槽箱"
    refs: ClassVar[dict[str, str]] = {"fefco": "0200"}
    version: ClassVar[str] = "0.1.0-c1"
    has_top: ClassVar[bool] = False

    def param_schema(self) -> type[HalfSlottedParams]:
        return HalfSlottedParams

    def resolve_dims(self, p: SlottedParams, material: Material, profile: Profile) -> SlotDims:
        adjusted = (
            p.model_copy(update={"H": p.H + 2 * material.caliper_mm})
            if p.dim_basis == "outer"
            else p
        )
        return SlotDims(adjusted, material, profile)

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        dl = super().generate(params, material, profile)
        d = self.resolve_dims(params, material, profile)
        dl.style_meta["dims"]["outer"] = [d.Li + 2 * d.t, d.Wi + 2 * d.t, d.Hi + 2 * d.t]
        dl.style_meta["assumptions"][0] = (
            "无顶摇盖；底部两层纸厚，外高换算及补偿均为【初值】，未折样校准"
        )
        return dl


class OverlapParams(SlottedParams):
    model_config = ConfigDict(allow_inf_nan=False)
    flap_gap: float = Field(default=0.0, ge=0, le=0)
    flap_overlap: float = Field(default=20.0, gt=0, description="Overlap width of the two outer flaps (provisional)")


class FullOverlapParams(SlottedParams):
    model_config = ConfigDict(allow_inf_nan=False)
    flap_gap: float = Field(default=0.0, ge=0, le=0)


class OverlapSlottedStyle(SlottedStyle):
    id: ClassVar[str] = "fefco_0202"
    name_zh: ClassVar[str] = "叠盖开槽箱"
    refs: ClassVar[dict[str, str]] = {"fefco": "0202"}
    version: ClassVar[str] = "0.1.0-c1"

    def param_schema(self) -> type[OverlapParams]:
        return OverlapParams

    def resolve_dims(self, p: SlottedParams, material: Material, profile: Profile) -> SlotDims:
        d = SlotDims(p, material, profile)
        overlap = getattr(p, "flap_overlap", d.Wi)
        k = coefficients(profile, material, p.apply_compensation)
        d.flap = (d.Wi + overlap) / 2 + k.k_flap * d.t
        if p.dim_basis == "outer":
            # Two outer layers plus one/two inner flaps at the thickest section.
            layers = 2 + (2 if 2 * d.flap > d.Li else 1)
            d.Hi = p.H - 2 * layers * d.t
            d.hb = d.Hi + k.k_height * d.t
        return d

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        issues = super().validate(params, material, profile)
        wi = params.W - 2 * material.caliper_mm if params.dim_basis == "outer" else params.W
        overlap = getattr(params, "flap_overlap", None)
        if overlap is not None and not 0 < overlap < wi:
            issues.append(
                Issue(
                    id="S03",
                    message=t(
                        "flap_overlap must be greater than 0 and smaller than the inside width"
                    ),
                    fix=t("Reduce flap_overlap. Use fefco_0203 for full overlap."),
                )
            )
        return issues

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        dl = super().generate(params, material, profile)
        d = self.resolve_dims(params, material, profile)
        layers = 2 + (2 if 2 * d.flap > d.Li else 1)
        dl.style_meta["dims"]["outer"] = [d.Li + 2 * d.t, d.Wi + 2 * d.t, d.Hi + 2 * layers * d.t]
        for end in ("top", "bottom"):
            dl.style_meta[end]["overlap"] = True
        dl.style_meta["assumptions"][0] = (
            "全部摇盖同深，外摇盖按部分 / 全叠盖机构重叠；补偿、外尺寸层厚均为【初值】，未折样校准"
        )
        return dl


class FullOverlapSlottedStyle(OverlapSlottedStyle):
    id: ClassVar[str] = "fefco_0203"
    name_zh: ClassVar[str] = "全叠盖开槽箱"
    refs: ClassVar[dict[str, str]] = {"fefco": "0203"}

    def param_schema(self) -> type[FullOverlapParams]:  # type: ignore[override]
        return FullOverlapParams
