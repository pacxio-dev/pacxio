"""Two unique strip tools with explicit per-set quantities for a cross divider."""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import LineString

from pacxio.geometry import Path, Vec, path_to_polygon
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile
from pacxio.styles.corrugated import assemble

MIN_WEB_MM = 3.0


class DividerParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0)
    W: float = Field(gt=0)
    H: float = Field(gt=0)
    dim_basis: Literal["inner"] = "inner"
    rows: int = Field(default=2, ge=2, le=8)
    cols: int = Field(default=2, ge=2, le=8)
    slot_clearance: float = Field(default=0.3, ge=0, le=2)


class DividerStyle:
    id: ClassVar[str] = "divider"
    name_zh: ClassVar[str] = "格子隔板 / 刀卡"
    version: ClassVar[str] = "0.1.0-c2"
    refs: ClassVar[dict[str, str]] = {"design": "两组半高槽互插，自定义 L0"}
    parts: ClassVar[tuple[str, ...]] = ("x", "y")
    part_labels: ClassVar[dict[str, str]] = {"x": "横向隔条", "y": "纵向隔条"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton", "corrugated"})

    def param_schema(self) -> type[DividerParams]:
        return DividerParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p = params
        sw = material.caliper_mm + p.slot_clearance
        if (
            material.type not in self.material_types
            or p.H / 2 < MIN_WEB_MM
            or min(p.L / p.cols, p.W / p.rows) - sw < MIN_WEB_MM
        ):
            return [
                Issue(
                    id="S03",
                    message=t(
                        "The divider half-height or cell width is too small. Allow at least a 3 mm bridge for the slots (provisional)."
                    ),
                    fix=t("Increase the span or height, or reduce the number of rows or columns"),
                )
            ]
        return []

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, dict[str, tuple[float, float]]]:
        return {"x": {"front": (params.L, params.H)}, "y": {"front": (params.W, params.H)}}

    def generate_parts(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Dieline]:
        p = params
        sw = material.caliper_mm + p.slot_clearance
        out = {}
        for name, length, cells, count in (
            ("x", p.L, p.cols, p.rows - 1),
            ("y", p.W, p.rows, p.cols - 1),
        ):
            # Walk a single continuous external knife around each top/bottom notch.
            points = [Vec(0, 0)]
            if name == "y":
                for i in range(1, cells):
                    c = i * length / cells
                    points += [
                        Vec(c - sw / 2, 0),
                        Vec(c - sw / 2, p.H / 2),
                        Vec(c + sw / 2, p.H / 2),
                        Vec(c + sw / 2, 0),
                    ]
            points += [Vec(length, 0), Vec(length, p.H)]
            if name == "x":
                for i in reversed(range(1, cells)):
                    c = i * length / cells
                    points += [
                        Vec(c + sw / 2, p.H),
                        Vec(c + sw / 2, p.H / 2),
                        Vec(c - sw / 2, p.H / 2),
                        Vec(c - sw / 2, p.H),
                    ]
            points += [Vec(0, p.H)]
            outline = Path.from_points(points, closed=True)
            out[name] = assemble(
                style_id=self.id,
                params=p,
                material=material,
                profile=profile,
                outline=outline,
                polys={"front": (PanelRole.BODY, outline)},
                creases=[],
                inner_cuts=[],
                ups={},
                provisional={"front"},
                meta={
                    "name_zh": self.name_zh,
                    "refs": self.refs,
                    "version": self.version,
                    "layout": "flat",
                    "paper_cutout": True,
                    "part": name,
                    "part_label": self.part_labels[name],
                    "quantity": count,
                    "accuracy_level": "L0",
                    "compensation_applied": False,
                    "dims": {"width": length, "height": p.H},
                    "set": {
                        "assembly": "divider",
                        "closed_outer": [p.L, p.W, p.H],
                        "rows": p.rows,
                        "cols": p.cols,
                        "quantities": {"x": p.rows - 1, "y": p.cols - 1},
                    },
                    "assumptions": [
                        "槽宽按实际纸厚 + 间隙初值，半高互插；配合 / 承载 / 方向待折样【初值】"
                    ],
                },
            )
        return out

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        raise ValueError(t("divider is a multi-part structure. Use generate_parts."))

    def check_fit(
        self, parts: dict[str, Dieline], params: Any, material: Material, profile: Profile
    ) -> list[Issue]:
        """Measure actual notch sections, half-height and per-set quantities (not load capacity)."""
        p = params
        errors = []
        for name, length, cells, count in (
            ("x", p.L, p.cols, p.rows - 1),
            ("y", p.W, p.rows, p.cols - 1),
        ):
            dl = parts[name]
            polygon = path_to_polygon(dl.panel("front").polygon)
            y = p.H * (0.75 if name == "x" else 0.25)
            missing = LineString([(0, y), (length, y)]).difference(polygon)
            gaps = (
                list(missing.geoms)
                if hasattr(missing, "geoms")
                else [missing]
                if not missing.is_empty
                else []
            )
            measured = sorted((g.bounds[0], g.bounds[2]) for g in gaps)
            ok = len(measured) == cells - 1 and dl.style_meta.get("quantity") == count
            ok = ok and abs(dl.bbox[3] - p.H) < 1e-6
            for i, (lo, hi) in enumerate(measured, 1):
                ok = ok and abs((lo + hi) / 2 - i * length / cells) < 1e-6
                ok = ok and abs(hi - lo - material.caliper_mm - p.slot_clearance) < 1e-6
            # The complementary half must remain solid at every crossing.
            solid = LineString([(0, p.H - y), (length, p.H - y)]).difference(polygon)
            ok = ok and solid.length < 1e-6
            if not ok:
                errors.append(
                    Issue(
                        id="S05",
                        message=f"{name} 隔条的实测插槽 / 数量与另一方向不匹配",
                        fix="按当前材料纸厚与行列数重新生成两种隔条；不要修改单组槽位",
                    )
                )
        return errors
