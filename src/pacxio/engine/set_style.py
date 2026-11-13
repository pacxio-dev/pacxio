"""多件套进流水线（几何引擎第 5 步）：一份 SetParams（engine/sets.py）= 一个多部件 job。

部件不是盒型写死的（天地盖 base / lid、组件 body / end / shelf……），所以这个盒型的部件按配方给
（`parts_for` / `labels_for`）；每件按结构配方生成，套合的件按被套件定尺寸，和单件一样标结构来源和成熟度。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from pacxio.engine.sets import (
    PART_ZH,
    SET_ZH,
    SetParams,
    generate_set,
    part_params,
    validate_set,
)
from pacxio.messages import display_name, join
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, MaterialType, Profile, load_profile


def _structure() -> Any:
    from pacxio.engine.style import StructureStyle

    return StructureStyle()


class StructureSetStyle:
    id: ClassVar[str] = "structure_set"
    name_zh: ClassVar[str] = "多件套（几何引擎）"
    refs: ClassVar[dict[str, str]] = {"design": "几何引擎多件套（）"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton", "corrugated"})
    version: ClassVar[str] = "0.1.0"
    experimental: ClassVar[bool] = True  # 和 structure 一样：不进结构目录，get_style 照样找得到
    parts: ClassVar[tuple[str, ...]] = ("_",)  # 占位：真正的部件按配方给（parts_for）
    part_labels: ClassVar[dict[str, str]] = {}

    def param_schema(self) -> type[BaseModel]:
        return SetParams

    def parts_for(self, params: SetParams) -> tuple[str, ...]:
        return tuple(params.parts)

    def labels_for(self, params: SetParams) -> dict[str, str]:
        return {k: PART_ZH.get(k, k) for k in params.parts}

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        if material.type not in self.material_types:
            return [
                Issue(
                    id="S03",
                    message=translate(
                        "Multi-part sets support folding carton and corrugated board. The current material type is {material}.",
                        material=material.type,
                    ),
                    fix=translate("Use folding carton or corrugated board"),
                )
            ]
        return validate_set(params, material, profile or load_profile("default"), _structure())

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        from pacxio.engine import fefco

        prof = profile or load_profile("default")
        try:
            parts = generate_set(params, material, prof, _structure())
        except Exception:  # noqa: BLE001 — 生成不了的由 validate 报错
            return []
        levels = {dl.style_meta["structure"]["maturity"]["level"] for dl in parts.values()}
        if levels == {"calibrated"}:
            return []
        code = fefco.set_code(params)
        kind = (
            translate(
                "Standard structure with provisional values inferred from the catalogue drawing"
            )
            if code
            else translate("Custom set generated from the recipe")
        )
        return [Issue(id="S04", message=translate("Multi-part set: {kind}. Clearances and fit are provisional pending folded samples.", kind=kind),
                      fix=translate("Make a sample before ordering to check that all parts fold and fit together"))]  # fmt: skip

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Any]:
        st = _structure()
        pps = part_params(params, material, profile, st)
        return {name: st.expected_panel_sizes(pp, material, profile) for name, pp in pps.items()}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        """整套并排放成一张（只给需要单张刀版的地方用；流水线按部件各做各的）。"""
        from pacxio.dielines import DielineSet

        parts: dict[str | None, Dieline] = {
            k: v for k, v in self.generate_parts(params, material, profile).items()
        }
        return DielineSet(self.id, parts, self.labels_for(params)).merged()

    def check_fit(
        self, parts: dict[str, Dieline], params: Any, material: Material, profile: Profile
    ) -> list[Issue]:
        """S05：套合件的内尺寸 − 被套件外尺寸 = 间隙（engine/sets.check_fit）；组件没有套合，不查。"""
        from pacxio.engine.sets import check_fit

        return check_fit(params, parts)

    def generate_parts(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Dieline]:
        from pacxio.engine import fefco

        sp: SetParams = params
        parts = generate_set(sp, material, profile, _structure())
        boxes = []
        for dl in parts.values():
            d, t = dl.style_meta["dims"], dl.caliper_mm
            outer = d.get("outer")
            if isinstance(outer, list) and len(outer) == 3:
                boxes.append(tuple(float(v) for v in outer))
            elif "Li" in d:
                boxes.append(
                    (
                        float(d["Li"]) + 2 * t,
                        float(d["Wi"]) + 2 * t,
                        float(d.get("Hi") or 0.0) + 2 * t,
                    )
                )
        closed = [max(b[i] for b in boxes) for i in range(3)] if boxes else [0.0, 0.0, 0.0]
        code = fefco.set_code(sp)
        labels = self.labels_for(sp)
        details = summary_data(sp)
        summary = set_summary(details)
        for name, dl in parts.items():
            info = dl.style_meta["set"]
            count = info.get("count", 1)
            dl.style_meta["part"] = name
            dl.style_meta["part_label"] = labels[name]
            dl.style_meta["quantity"] = count
            dl.style_meta["set"] = info | {
                "engine": True,
                "assembly": f"engine_{sp.set}",
                "closed_outer": closed,
                "fefco": code,
                "summary": summary,
                "summary_data": details,  # 交付件按自己的语言重新拼（set_summary）
            }
        return parts


def summary_data(sp: SetParams) -> dict[str, Any]:
    """规格书“整套”一行的数据：几件、各几片、谁套谁（不写编号，）。"""
    return {
        "set": sp.set,
        "parts": [[name, sp.fits[name].count if name in sp.fits else sp.counts.get(name, 1)] for name in sp.parts],
        "fits": [[k, f.over, f.clearance] for k, f in sp.fits.items()],
    }


def set_summary(data: dict[str, Any]) -> str:
    """summary_data → 当前语言的一句话。"""
    from pacxio.messages import lang

    if lang() == "bilingual":
        from pacxio.messages import both, use_language

        with use_language("en"):
            en = set_summary(data)
        with use_language("zh"):
            zh = set_summary(data)
        return both(en, zh)

    def name(part: str) -> str:
        return display_name(PART_ZH.get(part, part))

    bits = [translate("{part} × {n}", part=name(part), n=n) for part, n in data["parts"]]
    fits = [translate("{cover} fits over {base} (clearance {c} mm)", cover=name(k), base=name(over), c=f"{c:g}")
            for k, over, c in data["fits"]]  # fmt: skip
    text = translate("{kind}: {parts}", kind=display_name(SET_ZH.get(data["set"], data["set"])), parts=join(bits))
    return text + (translate("; ") + translate("; ").join(fits) if fits else "")


__all__ = ["StructureSetStyle", "set_summary", "summary_data"]
