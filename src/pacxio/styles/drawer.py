"""drawer 抽屉盒（内托 + 外套）， 节。

- 内托 tray：托盘部件，双层壁在抽拉方向两头（前壁就是抽屉脸），两块侧壁单层带耳朵；
  展开图从盒底外面看，前壁在下（标准朝向转 90°）。
- 外套 sleeve：[接舌][底][左][顶][右] 一条长条，糊成两头开口的方筒（接舌糊在右侧面里面，
  要糊盒机糊侧缝）；展开图的 y 是抽拉方向（y = 0 是前口），顶面前沿正中开半圆手指缺口。

机构参照 FEFCO 0509（内衬滑进套里）。第一版只做卡纸。全部数值【初值】。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.preflight.rules import DIM_TOLERANCE_MM, measure_panel
from pacxio.styles.compensation import coefficients
from pacxio.styles.components.sleeve import Sleeve
from pacxio.styles.tray import (
    TrayDims,
    TrayPartParams,
    build_part,
    tray_dims,
    tray_geometry,
    tray_sizes,
    validate_tray,
)

# ---- 【初值】----
LW_RANGE, H_RANGE = (40.0, 400.0), (15.0, 150.0)
SLEEVE_CLEARANCE_DEFAULT, SLEEVE_CLEARANCE_RANGE = 1.0, (0.5, 3.0)
SLEEVE_LEN_MIN_RATIO = 0.6
GLUE_W_CAP_MM, GLUE_W_RANGE, GLUE_W_MARGIN_MM = 15.0, (10.0, 20.0), 2.0
GLUE_INSET_RANGE, GLUE_BEVEL_MAX_DEG = (0.0, 5.0), 30.0
NOTCH_R_CAP_MM, NOTCH_R_TOP_RATIO, NOTCH_R_LEN_RATIO = 12.0, 0.25, 0.3
NOTCH_R_MIN_MM, NOTCH_R_MAX_TOP_RATIO, NOTCH_R_MAX_LEN_RATIO = 5.0, 0.4, 0.5

# 标准朝向（双层壁在 x 两端）转 90° 以后：x+ 端 → 后壁，x− 端 → 前壁（抽屉脸），y− 单层壁 → 右边的 side_a
TRAY_RENAME = {"base": "bottom", "side_a": "back", "side_b": "front", "front": "side_a", "back": "side_b",
               "rim_a": "rim_b", "rim_b": "rim_f", "side_a_in": "back_in", "side_b_in": "front_in",
               "ear_fa": "ear_a_back", "ear_fb": "ear_a_front", "ear_ba": "ear_b_back", "ear_bb": "ear_b_front"}  # fmt: skip
SLEEVE_ORDER = ("glue", "bottom", "side_a", "top", "side_b")


class DrawerParams(BaseModel):
    """抽屉盒参数。L × W × H：inner 时是内托内尺寸（脸宽 × 抽拉深 × 高），outer 时是外套外宽 × 长 × 外高。"""

    L: float = Field(gt=0, description="Drawer front width (left to right)")
    W: float = Field(gt=0, description="Depth in the pull direction")
    H: float = Field(gt=0, description="Height")
    dim_basis: Literal["inner", "outer"] = "inner"
    sleeve_clearance: float = SLEEVE_CLEARANCE_DEFAULT
    sleeve_len: float | None = None
    pull: Literal["notch", "none"] = "notch"
    notch_on: Literal["top", "top_bottom"] = "top"
    notch_r: float | None = None
    glue_w: float | None = None
    glue_inset: float = 2.0
    glue_bevel_deg: float = 15.0
    tray: TrayPartParams = Field(default_factory=TrayPartParams)
    apply_compensation: bool = True


class DrawerDims:
    def __init__(self, p: DrawerParams, material: Material, profile: Profile) -> None:
        t = self.t = material.caliper_mm
        k = coefficients(profile, material, p.apply_compensation)
        c = self.c = p.sleeve_clearance
        if p.dim_basis == "outer":
            Li, Wi, Hi = p.L - 5 * t - c, p.W - 6 * t, p.H - 4 * t - c
        else:
            Li, Wi, Hi = p.L, p.W, p.H
        self.Li, self.Wi, self.Hi = Li, Wi, Hi
        # 内托：标准朝向下 li = 抽拉深（前后内壁之间），wi = 脸宽（两侧壁之间）
        self.tray: TrayDims = tray_dims(Wi, Li, Hi, t, k.k_panel, k.k_height, p.tray, False)
        tl, tw, th = self.tray.outer  # 标准朝向：(抽拉深, 脸宽, 高)
        self.Lt, self.Wt, self.Ht = tw, tl, th  # 脸宽、深、高
        self.Ls = self.Lt + c + t  # 外套净宽（接舌糊在里面占一层）
        self.Hs = self.Ht + c
        self.top_w = self.Ls + k.k_panel * t
        self.side_w = self.Hs + k.k_panel * t
        self.length = p.sleeve_len if p.sleeve_len is not None else self.Wt
        self.gw = (
            p.glue_w if p.glue_w is not None else min(GLUE_W_CAP_MM, self.side_w - GLUE_W_MARGIN_MM)
        )
        self.gi = p.glue_inset
        self.gt = math.tan(math.radians(p.glue_bevel_deg))
        self.notch_r = (p.notch_r if p.notch_r is not None
                        else min(NOTCH_R_CAP_MM, NOTCH_R_TOP_RATIO * self.top_w, NOTCH_R_LEN_RATIO * self.length))  # fmt: skip
        self.k_panel = k.k_panel


class DrawerStyle:
    """抽屉盒：内托 + 外套。"""

    id: ClassVar[str] = "drawer"
    name_zh: ClassVar[str] = "抽屉盒"
    version: ClassVar[str] = "0.1.0-m4"
    refs: ClassVar[dict[str, str]] = {
        "fefco": "0509 型机构（内托按 0422、外套按 0503）",
        "ecma": "外套 A 组、内托 B 组（编号待核对）",
    }
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})
    parts: ClassVar[tuple[str, ...]] = ("tray", "sleeve")
    part_labels: ClassVar[dict[str, str]] = {"tray": "内托", "sleeve": "外套"}
    window_panels: ClassVar[dict[str, set[str]]] = {"sleeve": {"top", "bottom", "side_a", "side_b"}}

    def param_schema(self) -> type[BaseModel]:
        return DrawerParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: DrawerParams = params
        if material.type != "folding_carton":
            return [
                Issue(
                    id="S03",
                    message=translate("This drawer box supports folding carton board"),
                    fix=translate("Use a folding carton preset such as sbs_350"),
                )
            ]
        out: list[Issue] = []
        for name, (a, b) in (("L", LW_RANGE), ("W", LW_RANGE), ("H", H_RANGE)):
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
        d = DrawerDims(p, material, profile or load_profile("default"))
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

        def bad(msg: str, fix: str) -> None:
            out.append(Issue(id="S03", message=msg, fix=fix))

        lo, hi = SLEEVE_CLEARANCE_RANGE
        if not lo <= d.c <= hi:
            bad(
                translate(
                    "sleeve_clearance={value:g} is outside the provisional range {minimum:g}–{maximum:g}",
                    value=d.c,
                    minimum=lo,
                    maximum=hi,
                ),
                translate("Set it to {minimum:g}–{maximum:g}", minimum=lo, maximum=hi),
            )
        if not SLEEVE_LEN_MIN_RATIO * d.Wt - 1e-9 <= d.length <= d.Wt + 1e-9:
            bad(
                translate(
                    "sleeve_len={value:.1f} is outside {minimum:.1f}–{maximum:.1f} (inner tray depth)",
                    value=d.length,
                    minimum=SLEEVE_LEN_MIN_RATIO * d.Wt,
                    maximum=d.Wt,
                ),
                translate("Adjust sleeve_len"),
            )
        glo, ghi = GLUE_W_RANGE
        if not glo <= d.gw <= ghi or d.gw > d.side_w - GLUE_W_MARGIN_MM:
            bad(translate("The glue flap width {value:.1f} does not fit. It must be {minimum:g}–{maximum:g} and no more than the side width minus 2 ({side_limit:.1f}).", value=d.gw, minimum=glo, maximum=ghi, side_limit=d.side_w - GLUE_W_MARGIN_MM),
                translate("Set glue_w to at most {maximum:.1f} or increase H", maximum=min(ghi, d.side_w - GLUE_W_MARGIN_MM)))  # fmt: skip
        if not GLUE_INSET_RANGE[0] <= d.gi <= GLUE_INSET_RANGE[1]:
            bad(
                translate("glue_inset={value:g} is outside 0–5", value=d.gi),
                translate("Set it to 0–5"),
            )
        if not 0 <= p.glue_bevel_deg <= GLUE_BEVEL_MAX_DEG:
            bad(
                translate(
                    "glue_bevel_deg={value:g} is outside 0–{maximum:g}",
                    value=p.glue_bevel_deg,
                    maximum=GLUE_BEVEL_MAX_DEG,
                ),
                translate("Set it to 0–{maximum:g}", maximum=GLUE_BEVEL_MAX_DEG),
            )
        elif d.gi + d.gw * d.gt >= d.length / 2:
            bad(
                translate("The glue flap is too short after beveling both ends"),
                translate("Reduce glue_inset or glue_bevel_deg"),
            )
        if p.pull == "notch":
            rmax = min(NOTCH_R_MAX_TOP_RATIO * d.top_w, NOTCH_R_MAX_LEN_RATIO * d.length)
            if not NOTCH_R_MIN_MM <= d.notch_r <= rmax:
                bad(
                    translate(
                        "Thumb notch radius {radius:.1f} is outside {minimum:g}–{maximum:.1f}",
                        radius=d.notch_r,
                        minimum=NOTCH_R_MIN_MM,
                        maximum=rmax,
                    ),
                    translate("Adjust notch_r or set pull=none"),
                )
        out += validate_tray(d.tray, translate("Inner tray"), "tray")
        return out

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return []

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, dict[str, tuple[float, float]]]:
        d = DrawerDims(params, material, profile)
        sleeve = {"glue": (d.gw, d.length - 2 * d.gi), "bottom": (d.top_w, d.length), "side_a": (d.side_w, d.length),
                  "top": (d.top_w, d.length), "side_b": (d.side_w, d.length)}  # fmt: skip
        return {"tray": tray_sizes(d.tray, TRAY_RENAME, turned=True), "sleeve": sleeve}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        raise NotImplementedError(
            f"{self.id} 是多部件盒型：用 generate_parts（或 pipeline.build_parts_for）"
        )

    def generate_parts(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Dieline]:
        p: DrawerParams = params
        d = DrawerDims(p, material, profile)
        common = {"name_zh": self.name_zh, "refs": self.refs, "version": self.version,
                  "compensation_applied": p.apply_compensation}  # fmt: skip
        assembly = {
            "assembly": "drawer",
            "clearance": d.c,
            "closed_outer": [d.top_w + d.t, d.length, d.side_w + d.t],  # 外套外尺寸
        }
        # 平面图：外套 左 | 顶 | 右 照刀版画成一条（渐变、色带从顶面绕到两侧，画面没有方向，躺着画看不出错）；
        # 内托前脸 / 后壁正着上下叠一张（；单独出时 150 × 40 的细长面 AI 画不对比例，抽屉测试 F-17）
        assembly["flat_strips"] = [["sleeve.side_a", "sleeve.top", "sleeve.side_b"]]
        if p.pull == "notch":  # 概念图提示词要画出来、让字避开（抽屉测试 F-06）
            notched = ["top"] + (["bottom"] if p.notch_on == "top_bottom" else [])
            assembly["pull_notch"] = {
                "faces": [f"sleeve.{f}" for f in notched],
                "r": round(d.notch_r, 2),
            }
        tray = build_part(
            style_id=self.id, part="tray", part_label=self.part_labels["tray"], params=p, material=material,
            profile=profile, geom=tray_geometry(d.tray), rename=TRAY_RENAME, turn=True,
            ups={"bottom": "up"}, keep_upright={"back"},
            meta={**common, "layout": "tray", "set": assembly,
                  "faces": {"front": "front", "back": "back", "side_a": "side_a", "side_b": "side_b", "bottom": "bottom"},
                  "fold_order": {"ear_a_back": 1, "ear_a_front": 1, "ear_b_back": 1, "ear_b_front": 1, "front": 2,
                                 "back": 2, "front_in": 3, "back_in": 3},
                  "dims": {"Li": d.Li, "Wi": d.Wi, "Hi": d.Hi, "outer": [d.Lt, d.Wt, d.Ht]}},
        )  # fmt: skip
        sleeve = self._sleeve(p, d, material, profile, {**common, "set": assembly})
        return {"tray": tray, "sleeve": sleeve}

    def _sleeve(
        self,
        p: DrawerParams,
        d: DrawerDims,
        material: Material,
        profile: Profile,
        meta: dict[str, Any],
    ) -> Dieline:
        return Sleeve().build(
            p, d, material, profile, meta, style_id=self.id, part_label=self.part_labels["sleeve"]
        )

    def check_fit(
        self, parts: dict[str, Dieline], params: Any, material: Material, profile: Profile
    ) -> list[Issue]:
        """S05：外套净宽 − 内托外宽 = sleeve_clearance + t；净高 − 内托外高 = sleeve_clearance。"""
        d = DrawerDims(params, material, profile)
        t = d.t
        face_w, _ = measure_panel(parts["tray"], "bottom")  # 转了 90°：x 是脸宽
        tray_w = face_w + t
        side_h, _ = measure_panel(parts["tray"], "side_a")  # 转了 90°：侧壁的 x 方向是壁高
        tray_h = side_h + t
        top_w, _ = measure_panel(parts["sleeve"], "top")
        side_w, _ = measure_panel(parts["sleeve"], "side_a")
        clear_w, clear_h = top_w - d.k_panel * t, side_w - d.k_panel * t
        out: list[Issue] = []
        for axis, got, want in (("宽", clear_w - tray_w, d.c + t), ("高", clear_h - tray_h, d.c)):
            if abs(got - want) > DIM_TOLERANCE_MM:
                out.append(Issue(id="S05", message=f"外套{axis}方向的净尺寸比内托大 {got:.2f}，参数要求 {want:.2f}",
                                 fix="检查两个部件的尺寸换算（改了一个部件的参数、另一个没跟着变？）"))  # fmt: skip
        return out
