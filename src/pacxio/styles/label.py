"""瓶罐标签（C4，）：一张标签 = 一个面（角色 label）、只有刀线。

- `label_panel` 前 / 背标、盖顶标：外形是选项（rect / round_rect / circle / ellipse / custom），不按外形出条目。
- `label_wrap` 环标：绕瓶身一圈，宽 = π·D + 搭接；起贴那头的搭接条被盖住，不放重要信息（LB02）。
- `label_cone` 锥形扇形标：锥台面可展开，展开是圆环扇形（两条圆弧 + 两条直线，守 D4）。

平面坐标：印刷面朝看的人、标签正立（上 = 瓶口方向）；原点在包围盒左下角。H 是 CLI 兼容占位（同卡片）。
贴到容器上的映射写在 style_meta["label"]，3D 效果图和检查按它走（mockup/container.py）。全部数值【初值】。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Path, Segment, Vec, arc_segment
from pacxio.geometry.arcfit import ellipse
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, KeepClear, PanelRole
from pacxio.model.profile import LabelRules, Material, MaterialType, Profile
from pacxio.styles.components.svg_curves import curve_contour
from pacxio.styles.corrugated import assemble

MIN_SIZE_MM = 10.0  # 标签最小边【初值】
MAX_CONE_DEG = 330.0  # 扇形标展开角上限：再大就接近整圆、排不了版【初值】
LabelShape = Literal["rect", "round_rect", "circle", "ellipse", "custom"]


def label_rules(profile: Profile | None) -> LabelRules:
    return (profile.labels if profile is not None else None) or LabelRules()


class LabelBase(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    L: float = Field(gt=0)
    W: float = Field(gt=0, description="Label height mm")
    H: float = Field(default=1.0, gt=0, description="CLI compatibility placeholder; not used in the dieline")
    dim_basis: Literal["inner"] = "inner"


class PanelParams(LabelBase):
    L: float = Field(gt=0, description="Label width mm (diameter for round labels)")
    shape: LabelShape = "round_rect"
    corner_radius: float | None = Field(default=None, ge=0, description="Corner radius; round_rect default 3")
    outline_svg: str | None = Field(
        default=None, description="custom outline: one closed outline as SVG; Béziers become arcs"
    )


class WrapParams(LabelBase):
    L: float = Field(gt=0, description="Bottle diameter mm (label width = π·L + overlap)")
    overlap: float | None = Field(
        default=None, ge=0, description="Overlap mm; default from the factory profile labels.wrap_overlap_mm"
    )
    corner_radius: float = Field(default=0.0, ge=0)


class ConeParams(LabelBase):
    L: float = Field(gt=0, description="Bottle diameter mm at the label's bottom edge")
    top_d: float = Field(gt=0, description="Bottle diameter mm at the label's top edge")
    W: float = Field(gt=0, description="Label vertical height mm (along the bottle axis)")
    arc_deg: float | None = Field(
        default=None, gt=0, le=360, description="Partial wrap: angle around the bottle; not given = full wrap + overlap"
    )
    overlap: float | None = Field(default=None, ge=0)


def _meta(
    style: Any, label: dict[str, Any], dims: dict[str, Any], extra: list[str]
) -> dict[str, Any]:
    return {
        "name_zh": style.name_zh,
        "refs": style.refs,
        "version": style.version,
        "layout": "label",
        "accuracy_level": "L0",
        "compensation_applied": False,
        "label": label,
        "dims": dims,
        "assumptions": [
            "标签几何为自定义，没有标准编号；尺寸、搭接、出血、安全区都是【初值】，要标签厂确认",
            *extra,
        ],
    }


class _LabelStyle:
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"label_stock"})
    default_print_method: ClassVar[str] = "digital"
    refs: ClassVar[dict[str, str]] = {"design": "不干胶标签（自定义，无标准编号）"}
    version: ClassVar[str] = "0.1.0"
    name_zh: ClassVar[str]
    id: ClassVar[str]

    def _material(self, material: Material) -> list[Issue]:
        if material.type not in self.material_types:
            return [Issue(id="S03", message=t("Labels require self-adhesive label stock. The current material type is {material}.", material=material.type),
                          fix=t("Use --material label_coated, label_pp_white, label_pp_clear, or another preset from `pacxio styles list`"))]  # fmt: skip
        return []

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return []

    def _assemble(
        self, params: Any, material: Material, profile: Profile, outline: Path, meta: dict[str, Any]
    ) -> Dieline:
        return assemble(
            style_id=self.id,
            params=params,
            material=material,
            profile=profile,
            outline=outline,
            polys={"front": (PanelRole.LABEL, outline)},
            creases=[],
            inner_cuts=[],
            ups={},
            provisional=set(),
            meta=meta,
        )


# ---------------------------------------------------------------------------
# 前 / 背标、盖顶标
# ---------------------------------------------------------------------------


class LabelPanelStyle(_LabelStyle):
    """前 / 背标、盖顶标：一块平面标签，外形是选项。"""

    id: ClassVar[str] = "label_panel"
    name_zh: ClassVar[str] = "瓶罐贴标（前 / 背标、盖顶标）"

    def param_schema(self) -> type[BaseModel]:
        return PanelParams

    def _outline(self, p: PanelParams, rules: LabelRules) -> tuple[Path, float]:
        if p.shape == "rect":
            return Path.rect(0, 0, p.L, p.W), 0.0
        if p.shape == "round_rect":
            r = 3.0 if p.corner_radius is None else p.corner_radius
            if not 0 <= r <= min(p.L, p.W) / 2:
                raise ValueError(
                    t("Radius {radius:g} cannot exceed half the shorter side", radius=r)
                )
            return (Path.rounded_rect(0, 0, p.L, p.W, r) if r else Path.rect(0, 0, p.L, p.W)), 0.0
        if p.shape in ("circle", "ellipse"):
            if p.shape == "circle" and not math.isclose(p.L, p.W, abs_tol=1e-6):
                raise ValueError(
                    t(
                        "For a round label, L and W must both equal the diameter. Current dimensions: {length:g} × {width:g}.",
                        length=p.L,
                        width=p.W,
                    )
                )
            fit = ellipse(p.L / 2, p.W / 2, p.L / 2, p.W / 2, rules.arc_fit_tol_mm)
            return Path(fit.segments, closed=True), fit.max_error
        if not p.outline_svg:
            raise ValueError(t("A custom shape requires outline_svg with a single closed outline"))
        return curve_contour(p.outline_svg, p.L, p.W, rules.arc_fit_tol_mm)

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: PanelParams = params
        issues = self._material(material)
        if min(p.L, p.W) < MIN_SIZE_MM:
            issues.append(Issue(id="S03", message=t("Label {length:g} × {width:g} is too small (provisional minimum side length {minimum:g})", length=p.L, width=p.W, minimum=MIN_SIZE_MM),
                                fix=t("Set L and W to at least {minimum:g}", minimum=MIN_SIZE_MM)))  # fmt: skip
            return issues
        rules = label_rules(profile)
        try:
            _, err = self._outline(p, rules)
            if err > rules.arc_fit_tol_mm * 1.0001:
                raise ValueError(
                    t(
                        "The outline-to-arc fitting error {error:.3f} mm exceeds {tolerance:g} mm",
                        error=err,
                        tolerance=rules.arc_fit_tol_mm,
                    )
                )
        except ValueError as e:
            issues.append(
                Issue(
                    id="S03",
                    message=str(e),
                    fix=t("Check shape, corner_radius, outline_svg, L, and W"),
                )
            )
        return issues

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        return {"front": (params.L, params.W)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: PanelParams = params
        outline, err = self._outline(p, label_rules(profile))
        label = {
            "kind": "panel",
            "shape": p.shape,
            "width": p.L,
            "height": p.W,
            "arc_fit_error": round(err, 4),
        }
        dims = {"width": p.L, "height": p.W}
        return self._assemble(p, material, profile, outline, _meta(self, label, dims, []))


# ---------------------------------------------------------------------------
# 环标
# ---------------------------------------------------------------------------


class LabelWrapStyle(_LabelStyle):
    """环标：绕瓶身一圈。x = 0 那头先贴、被另一头盖住（搭接条不放重要信息）。"""

    id: ClassVar[str] = "label_wrap"
    name_zh: ClassVar[str] = "环标（绕瓶身一圈）"

    def param_schema(self) -> type[BaseModel]:
        return WrapParams

    @staticmethod
    def sizes(p: WrapParams, rules: LabelRules) -> tuple[float, float]:
        overlap = rules.wrap_overlap_mm if p.overlap is None else p.overlap
        return math.pi * p.L + overlap, overlap

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: WrapParams = params
        issues = self._material(material)
        rules = label_rules(profile)
        width, overlap = self.sizes(p, rules)
        if p.L < MIN_SIZE_MM or p.W < MIN_SIZE_MM:
            issues.append(Issue(id="S03", message=t("Bottle diameter {diameter:g} or label height {height:g} is too small (provisional minimum {minimum:g})", diameter=p.L, height=p.W, minimum=MIN_SIZE_MM),
                                fix=t("Set L and W to at least {minimum:g}", minimum=MIN_SIZE_MM)))  # fmt: skip
        if overlap > 0.25 * width:
            issues.append(
                Issue(
                    id="S03",
                    message=t(
                        "Overlap {overlap:g} exceeds one quarter of the label width",
                        overlap=overlap,
                    ),
                    fix=t("Reduce overlap"),
                )
            )
        if not 0 <= p.corner_radius <= min(width, p.W) / 2:
            issues.append(
                Issue(
                    id="S03",
                    message=t("The radius cannot exceed half the shorter side"),
                    fix=t("Reduce corner_radius"),
                )
            )
        return issues

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        width, _ = self.sizes(params, label_rules(profile))
        return {"front": (width, params.W)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: WrapParams = params
        width, overlap = self.sizes(p, label_rules(profile))
        r = p.corner_radius
        outline = Path.rounded_rect(0, 0, width, p.W, r) if r else Path.rect(0, 0, width, p.W)
        label = {"kind": "wrap", "width": width, "height": p.W, "diameter": p.L, "overlap": overlap}
        dims = {"width": width, "height": p.W, "diameter": p.L, "overlap": overlap}
        extra = ["宽 = π·瓶径 + 搭接，没算标签厚度和贴标机张力；x = 0 那头先贴、被盖住"]
        dl = self._assemble(p, material, profile, outline, _meta(self, label, dims, extra))
        if overlap:  # 先贴那头的搭接条会被另一头盖住：不放文字 / 条码（LB02）
            strip = Path.rect(0, 0, overlap, p.W)
            dl.keep_clear = [KeepClear(id="overlap", kind="overlap", path=strip, panel="front")]
        return dl


# ---------------------------------------------------------------------------
# 锥形扇形标
# ---------------------------------------------------------------------------


def cone_geometry(p: ConeParams, rules: LabelRules) -> dict[str, Any]:
    """锥台展开：apex below（上口大，杯）时圆心在标签下方；above（下口大）时在上方。"""
    r1, r2, h = p.L / 2, p.top_d / 2, p.W
    s = math.hypot(h, r2 - r1)
    k = abs(r2 - r1) / s  # 瓶面半径 / 展开半径
    if r2 > r1:
        apex, rho_in = "below", r1 / k
    else:
        apex, rho_in = "above", r2 / k
    rho_out = rho_in + s
    full = 2 * math.pi * k  # 整圈的展开角
    overlap = rules.wrap_overlap_mm if p.overlap is None else p.overlap
    if p.arc_deg is None:
        phi = full + overlap / ((rho_in + rho_out) / 2)
        wrap = True
    else:
        phi, overlap, wrap = full * p.arc_deg / 360, 0.0, False
    return {"r_bottom": r1, "r_top": r2, "height": h, "slant": s, "k": k, "apex": apex,
            "rho_in": rho_in, "rho_out": rho_out, "phi": phi, "full": full, "overlap": overlap,
            "wrap": wrap}  # fmt: skip


class LabelConeStyle(_LabelStyle):
    """锥形扇形标：杯、锥形瓶 / 罐；整圈（加搭接）或只包一部分（arc_deg）。"""

    id: ClassVar[str] = "label_cone"
    name_zh: ClassVar[str] = "锥形扇形标（杯 / 锥形瓶罐）"

    def param_schema(self) -> type[BaseModel]:
        return ConeParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: ConeParams = params
        issues = self._material(material)
        if min(p.L, p.top_d, p.W) < MIN_SIZE_MM:
            issues.append(
                Issue(
                    id="S03",
                    message=t(
                        "The dimensions are too small (provisional minimum {minimum:g})",
                        minimum=MIN_SIZE_MM,
                    ),
                    fix=t("Increase L, top_d, or W"),
                )
            )
            return issues
        if abs(p.top_d - p.L) < 1.0:
            issues.append(
                Issue(
                    id="S03",
                    message=t(
                        "The top and bottom diameters differ by less than 1 mm. Use a cylindrical label."
                    ),
                    fix=t("Use label_wrap (wraparound label)"),
                )
            )
            return issues
        g = cone_geometry(p, label_rules(profile))
        if math.degrees(g["phi"]) > MAX_CONE_DEG:
            issues.append(Issue(id="S03", message=t("The flat sector angle {angle:.0f}° is too large. The taper makes the blank nearly a full circle.", angle=math.degrees(g['phi'])),
                                fix=t("Reduce the taper or use arc_deg for a partial wrap (flat sector angle ≤ {maximum:g}°)", maximum=MAX_CONE_DEG)))  # fmt: skip
        return issues

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Any]:
        return {}  # 扇形没有"宽 × 高"，几何由测试逐点锁

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: ConeParams = params
        g = cone_geometry(p, label_rules(profile))
        up = 1.0 if g["apex"] == "below" else -1.0  # 标签中线方向（从圆心看）
        mid = math.pi / 2 if up > 0 else -math.pi / 2
        a_left, a_right = mid + g["phi"] / 2 * up, mid - g["phi"] / 2 * up
        c = Vec(0, 0)
        bottom_r = g["rho_in"] if up > 0 else g["rho_out"]
        top_r = g["rho_out"] if up > 0 else g["rho_in"]

        def at(r: float, a: float) -> Vec:
            return c + Vec(math.cos(a), math.sin(a)) * r

        segs = [
            arc_segment(c, bottom_r, a_left, a_right),  # 下沿：左 → 右
            Segment(at(bottom_r, a_right), at(top_r, a_right)),
            arc_segment(c, top_r, a_right, a_left),  # 上沿：右 → 左
            Segment(at(top_r, a_left), at(bottom_r, a_left)),
        ]
        outline = Path(segs, closed=True)
        x0, y0, _, _ = outline.bbox()
        label = {"kind": "cone", **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in g.items()},
                 "centre": [-x0, -y0], "bottom_rho": bottom_r, "top_rho": top_r,
                 "a_left": a_left, "a_right": a_right}  # fmt: skip
        if g["wrap"] and g["overlap"]:
            # 先贴的那头（左端）被盖住：沿左边直线往里 overlap 的扇形条
            label["no_key_angle"] = g["overlap"] / ((g["rho_in"] + g["rho_out"]) / 2)
        dims = {"width": outline.bbox()[2] - x0, "height": outline.bbox()[3] - y0,
                "diameter": p.L, "top_d": p.top_d, "label_h": p.W}  # fmt: skip
        extra = ["锥台面可展开，展开扇形按圆台精确计算；搭接按中径弧长算"]
        return self._assemble(p, material, profile, outline, _meta(self, label, dims, extra))


__all__ = ["LabelConeStyle", "LabelPanelStyle", "LabelWrapStyle", "cone_geometry", "label_rules"]
