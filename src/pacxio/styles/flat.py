"""Card/tag and folded header-card structures (C1, provisional)."""

import math
from typing import Any, ClassVar, Literal

from pydantic import Field

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile
from pacxio.styles.components.holes import HoleParams, hanging_hole
from pacxio.styles.components.svg_contour import svg_contour
from pacxio.styles.corrugated import assemble, crease


class CardParams(HoleParams):
    L: float = Field(gt=0, description="Flat width mm")
    W: float = Field(gt=0, description="Flat height mm")
    H: float = Field(default=1.0, gt=0, description="CLI compatibility placeholder; not used in the flat structure (board thickness comes from the material)")
    dim_basis: Literal["inner"] = "inner"
    corner_radius: float = Field(default=0.0, ge=0)
    outline_svg: str | None = None


class HeaderParams(CardParams):
    hang_hole: Literal["none", "round", "euro"] = "euro"
    folded: bool = True


def _lower_half(w: float, h: float, r: float) -> Path:
    if not r:
        return Path.rect(0, 0, w, h)
    segments = ([Segment(Vec(r, 0), Vec(w - r, 0))] if w > 2 * r else []) + [
        arc_segment(Vec(w - r, r), r, -math.pi / 2, 0)
    ]
    points = [Vec(w, r), Vec(w, h), Vec(0, h), Vec(0, r)]
    segments += [
        Segment(a, b) for a, b in zip(points, points[1:], strict=False) if not a.is_close(b)
    ]
    segments.append(arc_segment(Vec(r, r), r, math.pi, 3 * math.pi / 2))
    return Path(segments, closed=True)


class CardStyle:
    id: ClassVar[str] = "card"
    name_zh: ClassVar[str] = "卡片 / 吊牌"
    version: ClassVar[str] = "0.1.0-c1"
    refs: ClassVar[dict[str, str]] = {"design": "平面卡片（自定义机构，无标准编号）"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})

    def param_schema(self) -> type[CardParams]:
        return CardParams

    def _geometry(self, p: CardParams) -> tuple[Path, dict[str, tuple[PanelRole, Path]]]:
        if not 0 <= p.corner_radius <= min(p.L, p.W) / 2:
            raise ValueError(t("corner_radius cannot exceed half the shorter side"))
        folded = bool(getattr(p, "folded", False))
        if folded and p.outline_svg:
            raise ValueError(t("A custom SVG outline is available only for flat, unfolded pieces"))
        outline = (
            svg_contour(p.outline_svg, p.L, p.W)
            if p.outline_svg
            else (
                Path.rounded_rect(0, 0, p.L, p.W, p.corner_radius)
                if p.corner_radius
                else Path.rect(0, 0, p.L, p.W)
            )
        )
        if not folded:
            return outline, {"front": (PanelRole.BODY, outline)}
        lower = _lower_half(p.L, p.W / 2, p.corner_radius)
        upper = lower.transformed(Affine(1, 0, 0, -1, 0, p.W))
        return outline, {"front": (PanelRole.BODY, lower), "back": (PanelRole.BODY, upper)}

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        errors = []
        if material.type not in self.material_types:
            errors.append(
                Issue(
                    id="S03",
                    message=t("Flat cards support folding carton board"),
                    fix=t("Use sbs_350 or sbs_400"),
                )
            )
        try:
            if min(params.L, params.W) < 20:
                raise ValueError(t("Card width and height must be at least 20 mm (provisional)"))
            _, polys = self._geometry(params)
            pid = "front"
            hanging_hole(params, polys[pid][1], pid)
        except (ValueError, ArithmeticError) as e:
            errors.append(
                Issue(
                    id="S03",
                    message=str(e),
                    fix=t("Check the flat dimensions, radii, SVG, hole type, and hole clearance"),
                )
            )
        return errors

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        half = 2 if getattr(params, "folded", False) else 1
        return {
            pid: (params.L, params.W / half)
            for pid in (("front", "back") if half == 2 else ("front",))
        }

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        outline, polys = self._geometry(params)
        pid = "front"
        hole = hanging_hole(params, polys[pid][1], pid)
        folded = "back" in polys
        holes = [hole] if hole else []
        if folded and hole is not None:
            mirror = Affine(1, 0, 0, -1, 0, params.W)
            holes.append(
                hole.model_copy(
                    update={
                        "id": "cut_hang_back",
                        "panels": ["back"],
                        "segments": [mirror.apply_segment(s) for s in hole.segments],
                    }
                )
            )
        return assemble(
            style_id=self.id,
            params=params,
            material=material,
            profile=profile,
            outline=outline,
            polys=polys,
            creases=[
                crease(
                    "crease_front_back",
                    (0, params.W / 2),
                    (params.L, params.W / 2),
                    ["front", "back"],
                )
            ]
            if folded
            else [],
            inner_cuts=holes,
            ups={"back": "down"},
            provisional=set(polys),
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "layout": "flat",
                "folded": folded,
                "accuracy_level": "L0",
                "compensation_applied": False,
                "dims": {"width": params.L, "height": params.W / 2 if folded else params.W},
                "assumptions": [
                    "H 为兼容占位，不参与刀线；实际纸厚来自材料；折样及孔承重、图文方向未经验证【初值】"
                ],
            },
        )


class HeaderCardStyle(CardStyle):
    id: ClassVar[str] = "header_card"
    name_zh: ClassVar[str] = "头卡"
    refs: ClassVar[dict[str, str]] = {"design": "平面 / 对折头卡（自定义机构，无标准编号）"}

    def param_schema(self) -> type[HeaderParams]:
        return HeaderParams
