"""Two-legged locating platform; product fit and load capacity remain unverified."""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, path_to_polygon
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, DielineWindow, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile
from pacxio.styles.components.holes import circle
from pacxio.styles.corrugated import assemble, crease


class InsertParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0)
    W: float = Field(gt=0)
    H: float = Field(gt=0)
    dim_basis: Literal["inner"] = "inner"
    hole_shape: Literal["none", "round", "rect"] = "none"
    hole_w: float | None = Field(default=None, gt=0)
    hole_h: float | None = Field(default=None, gt=0)
    corner_radius: float = Field(default=0, ge=0)
    rows: int = Field(default=1, ge=1, le=8)
    cols: int = Field(default=1, ge=1, le=8)
    pitch_x: float | None = Field(default=None, gt=0)
    pitch_y: float | None = Field(default=None, gt=0)
    margin: float = Field(default=3, ge=3)


def holes(p: InsertParams) -> list[Path]:
    if p.hole_shape == "none":
        return []
    if p.hole_w is None or (p.hole_shape == "rect" and p.hole_h is None):
        raise ValueError(t("Locating holes require hole_w. Rectangular holes also require hole_h."))
    if 2 * p.margin >= min(p.L, p.W):
        raise ValueError(t("margin is too large and leaves no usable area for holes"))
    w = p.hole_w
    h = w if p.hole_shape == "round" else float(p.hole_h or 0)
    if p.corner_radius > min(w, h) / 2:
        raise ValueError(t("The locating hole radius exceeds half its shorter side"))
    px = p.pitch_x if p.pitch_x is not None else p.L / (p.cols + 1)
    py = p.pitch_y if p.pitch_y is not None else p.W / (p.rows + 1)
    out = []
    for row in range(p.rows):
        for col in range(p.cols):
            x = p.L / 2 + (col - (p.cols - 1) / 2) * px
            y = p.W / 2 + (row - (p.rows - 1) / 2) * py
            out.append(
                circle(x, y, w / 2)
                if p.hole_shape == "round"
                else Path.rounded_rect(x - w / 2, y - h / 2, w, h, p.corner_radius)
            )
    safe = path_to_polygon(Path.rect(p.margin, p.margin, p.L - 2 * p.margin, p.W - 2 * p.margin))
    for i, a in enumerate(out):
        geom = path_to_polygon(a)
        if not safe.covers(geom):
            raise ValueError(
                t(
                    "Locating holes must fit within the platform with the specified margin and clear the support creases"
                )
            )
        if any(geom.distance(path_to_polygon(b)) < p.margin - 1e-6 for b in out[:i]):
            raise ValueError(t("The board bridge between locating holes is smaller than margin"))
    return out


class InsertCardStyle:
    id: ClassVar[str] = "insert_card"
    name_zh: ClassVar[str] = "定位内托"
    version: ClassVar[str] = "0.1.0-c2"
    refs: ClassVar[dict[str, str]] = {"design": "平台及两片支撑脚，自定义 L0"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})

    def param_schema(self) -> type[InsertParams]:
        return InsertParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        try:
            if material.type not in self.material_types:
                raise ValueError(t("This insert supports folding carton board"))
            if min(params.L, params.W) < 30 or not 5 <= params.H <= min(params.L, params.W) / 2:
                raise ValueError(
                    t(
                        "Platform length and width must be at least 30 mm. Foot height must be 5 mm to half the shorter side (provisional)."
                    )
                )
            holes(params)
        except ValueError as e:
            return [
                Issue(
                    id="S03",
                    message=str(e),
                    fix=t(
                        "Adjust the platform, foot height, or locating hole size, array, or margin"
                    ),
                )
            ]
        return []

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        return {
            "platform": (params.L, params.W),
            "front": (params.L, params.H),
            "back": (params.L, params.H),
        }

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p = params
        paths = holes(p)
        dl = assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=Path.rect(0, -p.H, p.L, p.W + 2 * p.H),
            polys={
                "platform": (PanelRole.BODY, Path.rect(0, 0, p.L, p.W)),
                "front": (PanelRole.INSERT, Path.rect(0, -p.H, p.L, p.H)),
                "back": (PanelRole.INSERT, Path.rect(0, p.W, p.L, p.H)),
            },
            creases=[
                crease("crease_platform_front", (0, 0), (p.L, 0), ["platform", "front"]),
                crease("crease_platform_back", (0, p.W), (p.L, p.W), ["platform", "back"]),
            ],
            inner_cuts=[
                Edge(
                    id=f"cut_product_{i + 1}",
                    type=EdgeType.CUT,
                    segments=path.segments,
                    panels=["platform"],
                )
                for i, path in enumerate(paths)
            ],
            ups={},
            provisional={"platform", "front", "back"},
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "layout": "platform",
                "paper_cutout": True,
                "accuracy_level": "L0",
                "compensation_applied": False,
                "dims": {"Li": p.L, "Wi": p.W, "Hi": p.H, "outer": [p.L, p.W, p.H]},
                "assumptions": [
                    "平台尺寸为压线中心距；脚高、孔尺寸与承载均待产品配合 / 折样【初值】"
                ],
            },
        )
        dl.windows = [
            DielineWindow(
                id=f"product_{i + 1}",
                panel="platform",
                cut=path.transformed(Affine.translate(0, p.H)),
            )
            for i, path in enumerate(paths)
        ]
        return dl
