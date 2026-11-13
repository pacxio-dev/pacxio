"""tray_lid 天地盖（折叠式）， 节。

两个部件、两张刀版：地盒 base（从盒底外面看）和天盖 lid（从盖顶外面看），都是托盘部件
（FEFCO 0422 型：双层端壁 + 耳朵 + 插脚，不用胶）。天盖的净尺寸 = 地盒外尺寸 + 间隙；
全罩（默认）时盖口碰到桌面，单层壁自由边正中开半圆手指缺口。规格书写"FEFCO 03 系列套合机构，
地盒 / 盖按 0422"。全部数值【初值】。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.preflight.rules import DIM_TOLERANCE_MM, measure_panel
from pacxio.styles.compensation import coefficients
from pacxio.styles.corrugated import advisory, flute_advisory
from pacxio.styles.tray import (
    EAR_DEPTH_MIN_MM,
    TrayDims,
    TrayPartParams,
    build_part,
    min_wall_height,
    tray_dims,
    tray_geometry,
    tray_sizes,
    validate_tray,
)

# ---- 【初值】----
CARD_LW_RANGE, CARD_H_RANGE = (40.0, 400.0), (15.0, 150.0)
CORR_LW_RANGE, CORR_H_MAX, CORR_H_MIN, CORR_H_MIN_T = (60.0, 700.0), 250.0, 20.0, 6.0
LID_CLEARANCE_MIN_MM = 1.0  # 默认 max(1, t)
LID_CLEARANCE_RANGE = (0.5, None)  # 上限 3t + 2
FULL_COVER_TOL_MM = 5.0  # lid_h ≥ Hc − 5 算全罩：默认开手指缺口
NOTCH_R_CAP_MM, NOTCH_R_RATIO, NOTCH_R_MIN_MM = 12.0, 0.4, 5.0
CARD_SHEET_MM = (1000.0, 700.0)  # 卡纸对开约 1000 × 700，展开超过只提醒
USUAL_FLUTES = {"E", "B"}

# 盖从上面看，左右和地盒（从下面看）相反：标准朝向 x+ 端在盖上是 side_b
LID_RENAME = {"base": "top", "side_a": "side_b", "side_b": "side_a", "rim_a": "rim_b", "rim_b": "rim_a",
              "side_a_in": "side_b_in", "side_b_in": "side_a_in", "ear_fa": "ear_fb", "ear_fb": "ear_fa",
              "ear_ba": "ear_bb", "ear_bb": "ear_ba"}  # fmt: skip
BASE_RENAME = {"base": "bottom"}


class TrayLidParams(BaseModel):
    """天地盖参数。L × W × H：inner 时是地盒内尺寸，outer 时是合上后的外尺寸。"""

    L: float = Field(gt=0, description="Length (front width)")
    W: float = Field(gt=0, description="Width (depth)")
    H: float = Field(gt=0, description="Height")
    dim_basis: Literal["inner", "outer"] = "inner"
    lid_h: float | None = None  # 盖的外高；为空 = 全罩
    lid_clearance: float | None = None
    lid_notch: bool | None = None  # 为空：全罩时开、半罩时关
    lid_notch_r: float | None = None
    base: TrayPartParams = Field(default_factory=TrayPartParams)
    lid: TrayPartParams = Field(default_factory=TrayPartParams)
    apply_compensation: bool = True


class LidDims:
    def __init__(self, p: TrayLidParams, material: Material, profile: Profile) -> None:
        t = self.t = material.caliper_mm
        corr = material.type == "corrugated"
        k = coefficients(profile, material, p.apply_compensation)
        self.c = p.lid_clearance if p.lid_clearance is not None else max(LID_CLEARANCE_MIN_MM, t)
        if p.dim_basis == "outer":
            Li, Wi, Hi = p.L - 12 * t - self.c, p.W - 4 * t - self.c, p.H - 3 * t
        else:
            Li, Wi, Hi = p.L, p.W, p.H
        self.Li, self.Wi, self.Hi = Li, Wi, Hi
        self.base: TrayDims = tray_dims(Li, Wi, Hi, t, k.k_panel, k.k_height, p.base, corr)
        Lo, Wo, Ho = self.base.outer
        self.Hc = Ho + t  # 合上后总高：盖顶压在地盒口沿上
        self.lid_h = p.lid_h if p.lid_h is not None else self.Hc
        lid_hi = self.lid_h - t - k.k_height * t  # 盖壁 hf = lid_h − t
        self.lid: TrayDims = tray_dims(
            Lo + self.c, Wo + self.c, lid_hi, t, k.k_panel, k.k_height, p.lid, corr
        )
        self.full = self.lid_h >= self.Hc - FULL_COVER_TOL_MM
        self.notch = p.lid_notch if p.lid_notch is not None else self.full
        self.notch_r = (p.lid_notch_r if p.lid_notch_r is not None
                        else min(NOTCH_R_CAP_MM, NOTCH_R_RATIO * (self.lid_h - t)))  # fmt: skip

    @property
    def closed(self) -> tuple[float, float, float]:
        Lo, Wo, _ = self.lid.outer
        return Lo, Wo, self.Hc


class TrayLidStyle:
    """天地盖：地盒 + 天盖。"""

    id: ClassVar[str] = "tray_lid"
    name_zh: ClassVar[str] = "天地盖"
    version: ClassVar[str] = "0.1.0-m4"
    refs: ClassVar[dict[str, str]] = {
        "fefco": "03 系列套合机构，地盒 / 盖按 0422",
        "ecma": "B 组（编号待核对）",
    }
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton", "corrugated"})
    parts: ClassVar[tuple[str, ...]] = ("base", "lid")
    part_labels: ClassVar[dict[str, str]] = {"base": "地盒", "lid": "天盖"}
    window_panels: ClassVar[dict[str, set[str]]] = {"lid": {"top"}}  # 开窗只开在盖顶

    def param_schema(self) -> type[BaseModel]:
        return TrayLidParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: TrayLidParams = params
        t = material.caliper_mm
        out: list[Issue] = []
        if material.type == "corrugated":
            (lo, hi), (hlo, hhi) = CORR_LW_RANGE, (max(CORR_H_MIN, CORR_H_MIN_T * t), CORR_H_MAX)
        else:
            (lo, hi), (hlo, hhi) = CARD_LW_RANGE, CARD_H_RANGE
        for name, a, b in (("L", lo, hi), ("W", lo, hi), ("H", hlo, hhi)):
            v = getattr(p, name)
            if not a <= v <= b:
                out.append(
                    Issue(
                        id="S03",
                        message=translate(
                            "{parameter}={value:g} is outside the provisional range {minimum:g}–{maximum:g}",
                            parameter=name,
                            value=v,
                            minimum=a,
                            maximum=b,
                        ),
                        fix=translate(
                            "Set {parameter} to {minimum:g}–{maximum:g}",
                            parameter=name,
                            minimum=a,
                            maximum=b,
                        ),
                    )
                )
        if out:
            return out
        d = LidDims(p, material, profile or load_profile("default"))
        if min(d.Li, d.Wi, d.Hi) <= 0:
            return [
                Issue(
                    id="S03",
                    message=translate(
                        "Subtracting board thickness and clearance leaves nonpositive inside dimensions"
                    ),
                    fix=translate("Increase the dimensions or use --basis inner"),
                )
            ]
        if not LID_CLEARANCE_RANGE[0] <= d.c <= 3 * t + 2:
            out.append(Issue(id="S03", message=translate("lid_clearance={value:g} is outside the provisional range {minimum:g}–{maximum:g}", value=d.c, minimum=LID_CLEARANCE_RANGE[0], maximum=3 * t + 2),
                             fix=translate("Set lid_clearance to {minimum:g}–{maximum:g}", minimum=LID_CLEARANCE_RANGE[0], maximum=3 * t + 2)))  # fmt: skip
        if d.lid_h > d.Hc + 1e-9:
            out.append(Issue(id="S03", message=translate("Lid height lid_h={lid_height:.1f} exceeds total closed height {closed_height:.1f}, so the base would hang inside the lid", lid_height=d.lid_h, closed_height=d.Hc),
                             fix=translate("Set lid_h to at most {maximum:.1f}. An equal height makes a full-depth lid.", maximum=d.Hc)))  # fmt: skip
        out += validate_tray(d.base, translate("Base tray"), "base")
        for i in validate_tray(d.lid, translate("Lid tray"), "lid"):
            if d.lid.ed < EAR_DEPTH_MIN_MM:  # 盖太浅：修法说盖高（lid_h = 盖壁 hf + t）
                i.fix = f"把 lid_h 加到 ≥ {min_wall_height(d.lid) + t:.1f}"
            out.append(i)
        if d.notch:
            r = d.notch_r
            hmax = 0.5 * (d.lid_h - t)
            if not NOTCH_R_MIN_MM <= r <= hmax or 2 * r >= d.lid.X1 - d.lid.X0:
                out.append(Issue(id="S03", message=translate("The lid thumb notch radius {radius:.1f} does not fit ({minimum:g}–{maximum:.1f})", radius=r, minimum=NOTCH_R_MIN_MM, maximum=hmax),
                                 fix=translate("Adjust lid_notch_r or set lid_notch=false")))  # fmt: skip
        return out

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        out = (
            flute_advisory(material, USUAL_FLUTES, translate("Two-piece lid and base box"))
            if material.type == "corrugated"
            else []
        )
        if material.type == "folding_carton":
            d = LidDims(params, material, profile or load_profile("default"))
            for label, td in (("地盒", d.base), ("天盖", d.lid)):
                w = td.bl + 2 * (td.ho + td.rg + td.hin + td.lug_total)
                h = td.bw + 2 * td.hf
                if max(w, h) > CARD_SHEET_MM[0] or min(w, h) > CARD_SHEET_MM[1]:
                    out.append(advisory(translate("The {part} blank is {width:.0f} × {height:.0f} mm, larger than a typical half sheet (about {sheet_width:g} × {sheet_height:g} mm, provisional)", part=label, width=w, height=h, sheet_width=CARD_SHEET_MM[0], sheet_height=CARD_SHEET_MM[1]),
                                        translate("Ask the factory for the maximum sheet and die-cutter dimensions")))  # fmt: skip
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, dict[str, tuple[float, float]]]:
        d = LidDims(params, material, profile)
        return {"base": tray_sizes(d.base, BASE_RENAME), "lid": tray_sizes(d.lid, LID_RENAME)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        raise NotImplementedError(
            f"{self.id} 是多部件盒型：用 generate_parts（或 pipeline.build_parts_for）"
        )

    def generate_parts(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Dieline]:
        p: TrayLidParams = params
        d = LidDims(p, material, profile)
        common = {"name_zh": self.name_zh, "refs": self.refs, "version": self.version,
                  "compensation_applied": p.apply_compensation}  # fmt: skip
        flute = ({"flute_dir": "y", "flute_note": "Flute along the flat's y axis (inferred); not marked on the dieline"}
                 if material.type == "corrugated" else {})  # fmt: skip
        Lc, Wc, Hc = d.closed
        base = build_part(
            style_id=self.id, part="base", part_label=self.part_labels["base"], params=p, material=material,
            profile=profile, geom=tray_geometry(d.base), rename=BASE_RENAME,
            ups={"bottom": "up"}, keep_upright={"back"},
            meta={**common, **flute, "layout": "tray",
                  "faces": {"front": "front", "back": "back", "side_a": "side_a", "side_b": "side_b", "bottom": "bottom"},
                  "fold_order": {"ear_fa": 1, "ear_fb": 1, "ear_ba": 1, "ear_bb": 1, "side_a": 2, "side_b": 2,
                                 "side_a_in": 3, "side_b_in": 3},
                  "dims": {"Li": d.Li, "Wi": d.Wi, "Hi": d.Hi, "outer": list(d.base.outer)}},
        )  # fmt: skip
        lid = build_part(
            style_id=self.id, part="lid", part_label=self.part_labels["lid"], params=p, material=material,
            profile=profile, geom=tray_geometry(d.lid, d.notch_r if d.notch else None), rename=LID_RENAME,
            ups={"top": "up", "front": "up", "back": "down", "side_a": "right", "side_b": "left",
                 "side_a_in": "right", "side_b_in": "left"},
            keep_upright={"top"},
            meta={**common, **flute, "layout": "lid",
                  "faces": {"top": "top", "front": "front", "back": "back", "side_a": "side_a", "side_b": "side_b"},
                  "fold_order": {"ear_fa": 1, "ear_fb": 1, "ear_ba": 1, "ear_bb": 1, "side_a": 2, "side_b": 2,
                                 "side_a_in": 3, "side_b_in": 3},
                  "dims": {"Li": d.lid.li, "Wi": d.lid.wi, "Hi": d.lid.hi, "lid_h": d.lid_h,
                           "outer": [d.lid.outer[0], d.lid.outer[1], d.lid_h]}},
        )  # fmt: skip
        assembly = {"assembly": "telescope", "lid_h": d.lid_h, "closed_height": Hc, "clearance": d.c,
                    "full_cover": d.full, "closed_outer": [Lc, Wc, Hc]}  # fmt: skip
        base.style_meta["set"] = assembly
        lid.style_meta["set"] = assembly
        return {"base": base, "lid": lid}

    def check_fit(
        self, parts: dict[str, Dieline], params: Any, material: Material, profile: Profile
    ) -> list[Issue]:
        """S05：从几何量回的配合尺寸——盖的净尺寸 − 地盒外尺寸 = lid_clearance。"""
        d = LidDims(params, material, profile)
        t = d.t
        base_l, base_w = measure_panel(parts["base"], "bottom")
        top_l, top_w = measure_panel(parts["lid"], "top")
        # 托盘的净尺寸：长方向扣掉两端各两层（内壁 + 耳朵）再减 k_panel·t；宽方向减 k_panel·t（见 tray_dims）
        k = coefficients(profile, material, params.apply_compensation)
        base_outer = (base_l + t, base_w + t)
        lid_clear = (top_l - k.k_panel * t - 4 * t, top_w - k.k_panel * t)
        out: list[Issue] = []
        for axis, clear, outer in (
            ("长", lid_clear[0], base_outer[0]),
            ("宽", lid_clear[1], base_outer[1]),
        ):
            gap = clear - outer
            if abs(gap - d.c) > DIM_TOLERANCE_MM:
                out.append(Issue(id="S05", message=f"天盖{axis}方向的净尺寸 {clear:.2f} − 地盒外尺寸 {outer:.2f} = {gap:.2f}，参数要求间隙 {d.c:.2f}",
                                 fix="检查两个部件的尺寸换算（改了一个部件的参数、另一个没跟着变？）"))  # fmt: skip
        return out
