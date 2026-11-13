"""fefco_0201 普通开槽箱（regular slotted container，RSC）， 节。

印刷面视图（从箱外看），横向排列，盒身面正立方向为 +y（摇盖在上的一边是箱顶）::

           [ top_back ][top_side_a][ top_front ][top_side_b]
    [glue] [   back   ][  side_a  ][   front   ][  side_b  ]
           [ btm_back ][btm_side_a][ btm_front ][btm_side_b]

- 所有摇盖一样长 f；两片外摇盖（back、front 上的）各等于箱宽的一半减半个间隙，合上时在中间对齐；
- 每条竖压线上、下各开一条槽（宽 slot_w，开到摇盖压线，方头）；首尾两端的摇盖也各让 slot_w / 2；
- 厂商接头：接舌挂在 back 左边、糊在 side_b 里面（joint = glue / stitch），胶带接头没有接舌；
  最后一块面（side_b）短 last_panel_short（接头在角上占掉一层厚度）。

公开资料（FEFCO 代码、FBA 容差报告）只给了机构与量法，具体数值是我们的初稿，全部【初值】。
FEFCO 的图从箱内看，这里按印刷面建模（D5）。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.corrugated import (
    advisory,
    assemble,
    corrugated_only,
    crease,
    flute_advisory,
)
from pacxio.styles.rte import _chain, _ordered, _ordered_edges, _rect, _trapezoid

# ---- 【初值】全部待折样、结构顾问与工厂确认 ----
MIN_DIM_MM = 50.0
JOINT_W_DEFAULT_MM = 35.0
JOINT_W_DOUBLE_WALL_MM = 40.0  # 双瓦楞（BC）
JOINT_W_RANGE = (30.0, 50.0)
# 卡纸走开槽箱拼装器（几何引擎 §20，结构不分材料）：接舌宽同彩盒糊口
JOINT_W_CARTON_MM, JOINT_W_CARTON_RANGE = 15.0, (10.0, 25.0)
JOINT_W_MAX_SIDE_RATIO = 0.5  # joint_w ≤ 0.5·ws
JOINT_BEVEL_MAX_DEG = 30.0
SLOT_W_MIN_MM = 3.0  # slot_w 默认 = max(3, 2t)
SLOT_W_T_RATIO = 2.0
FLAP_GAP_DEFAULT_MM = 2.0
FLAP_GAP_MAX_MM = 6.0  # FBA：外摇盖合上间隙 ≤ 6.4、不能重叠
BLANK_WARN_MM = (2500.0, 1800.0)  # 展开长 / 宽超过就提醒（设备上限因厂而异）
DOUBLE_WALL_FLUTES = {"BC"}
USUAL_FLUTES = {"B", "C", "BC"}
FACE_RATIO = 0.5  # 外摇盖 = 箱宽的一半


class SlottedParams(BaseModel):
    """开槽箱参数。可选项为 None 时按草案公式取默认值。"""

    L: float = Field(gt=0, description="Length (long side of the opening, front width)")
    W: float = Field(gt=0, description="Width (short side of the opening, side width)")
    H: float = Field(gt=0, description="Height")
    dim_basis: Literal["inner", "outer"] = "inner"
    joint: Literal["glue", "stitch", "tape"] = "glue"
    joint_w: float | None = None
    joint_inset: float | None = None
    joint_bevel_deg: float = 15.0
    slot_w: float | None = None
    flap_gap: float = FLAP_GAP_DEFAULT_MM
    last_panel_short: float | None = None
    flute_dir: Literal["y"] = "y"  # 楞向：竖直（沿高度）；侧开口箱以后再说
    apply_compensation: bool = True


class SlotDims:
    """解析后的尺寸（mm）。"""

    def __init__(self, p: SlottedParams, material: Material, profile: Profile) -> None:
        t = self.t = material.caliper_mm
        if p.dim_basis == "outer":  # 上、下各两层摇盖
            self.Li, self.Wi, self.Hi = p.L - 2 * t, p.W - 2 * t, p.H - 4 * t
        else:
            self.Li, self.Wi, self.Hi = p.L, p.W, p.H
        k = coefficients(profile, material, p.apply_compensation)
        self.wb = self.Li + k.k_panel * t
        self.ws = self.Wi + k.k_panel * t
        self.hb = self.Hi + k.k_height * t
        self.flap = FACE_RATIO * self.Wi + k.k_flap * t - 0.5 * p.flap_gap
        tape = p.joint == "tape"
        double = (material.flute or "").upper() in DOUBLE_WALL_FLUTES
        default_jw = (
            JOINT_W_CARTON_MM
            if material.type == "folding_carton"
            else JOINT_W_DOUBLE_WALL_MM
            if double
            else JOINT_W_DEFAULT_MM
        )
        self.jw = 0.0 if tape else (p.joint_w if p.joint_w is not None else default_jw)
        self.ji = p.joint_inset if p.joint_inset is not None else t
        self.jt = math.tan(math.radians(p.joint_bevel_deg))
        self.slot = p.slot_w if p.slot_w is not None else max(SLOT_W_MIN_MM, SLOT_W_T_RATIO * t)
        default_short = 0.0 if tape else t
        self.short = p.last_panel_short if p.last_panel_short is not None else default_short
        self.wsb = self.ws - self.short  # 最后一块面（side_b）


class SlottedStyle:
    """普通开槽箱。以后的 0203（全重叠摇盖）、0204 等 02 系列从它派生。"""

    id: ClassVar[str] = "fefco_0201"
    name_zh: ClassVar[str] = "普通开槽箱"
    version: ClassVar[str] = "0.1.0-m4"
    refs: ClassVar[dict[str, str]] = {"fefco": "0201"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"corrugated"})
    require_l_ge_w: ClassVar[bool] = True  # L 是开口的长边（几何引擎的拼装器按封口自己判断）
    material_gate: ClassVar[bool] = True  # 旧 0201 入口只认瓦楞；几何引擎的开槽箱拼装器关掉（§20）
    default_print_method: ClassVar[str] = "flexo"  # 开槽箱默认水印（2026-10-20 用户确认）
    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b",
        "top_back", "top_side_a", "top_front", "top_side_b",
        "btm_back", "btm_side_a", "btm_front", "btm_side_b",
    )  # fmt: skip

    has_top: ClassVar[bool] = True

    def resolve_dims(self, p: SlottedParams, material: Material, profile: Profile) -> SlotDims:
        return SlotDims(p, material, profile)

    def param_schema(self) -> type[BaseModel]:
        return SlottedParams

    # ---- 约束 ----

    def joint_range(self, material: Material) -> tuple[float, float]:
        """接舌宽范围：瓦楞 30–50、卡纸 10–25【初值】。"""
        return JOINT_W_CARTON_RANGE if material.type == "folding_carton" else JOINT_W_RANGE

    def min_dim(self, material: Material) -> float:
        """长宽高下限：旧 0201 入口一律 50【初值】（几何引擎的开槽箱拼装器按纸厚算，见 engine/slotted._AnyBoard）。"""
        return MIN_DIM_MM

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: SlottedParams = params
        issues = corrugated_only(self.id, material) if self.material_gate else []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        if issues:
            return issues
        lo = self.min_dim(material)
        for name in ("L", "W", "H"):
            if getattr(p, name) < lo:
                bad(
                    translate(
                        "{parameter}={value:g} is below the provisional minimum {minimum:g}",
                        parameter=name,
                        value=getattr(p, name),
                        minimum=lo,
                    ),
                    translate(
                        "Set {parameter} to at least {minimum:g}", parameter=name, minimum=lo
                    ),
                )
        if p.W > p.L and self.require_l_ge_w:
            bad(
                translate(
                    "L is the long edge of a slotted container opening. W={width:g} > L={length:g}.",
                    width=p.W,
                    length=p.L,
                ),
                translate("Swap L and W"),
            )
        if issues:
            return issues
        d = self.resolve_dims(p, material, profile or load_profile("default"))
        t = d.t
        if min(d.Li, d.Wi, d.Hi) <= 0:
            bad(
                translate(
                    "The inside dimensions are not positive after subtracting board thickness"
                ),
                translate("Increase the dimensions or use --basis inner"),
            )
            return issues
        if p.joint != "tape":
            lo, hi = self.joint_range(material)
            if not lo <= d.jw <= hi:
                bad(
                    translate(
                        "joint_w={value:g} is outside the provisional range {minimum:g}–{maximum:g}",
                        value=d.jw,
                        minimum=lo,
                        maximum=hi,
                    ),
                    translate("Set joint_w to {minimum:g}–{maximum:g}", minimum=lo, maximum=hi),
                )
            if d.jw > JOINT_W_MAX_SIDE_RATIO * d.ws:
                bad(translate("Joint tab width {width:g} exceeds half the side width ({maximum:.1f})", width=d.jw, maximum=JOINT_W_MAX_SIDE_RATIO * d.ws),
                    translate("Reduce joint_w or increase W"))  # fmt: skip
            if not 0 <= d.ji <= 3 * t:
                bad(
                    translate(
                        "joint_inset={value:g} is outside 0–3t ({maximum:g})",
                        value=d.ji,
                        maximum=3 * t,
                    ),
                    translate("Set joint_inset to 0–{maximum:g}", maximum=3 * t),
                )
            if not 0 <= p.joint_bevel_deg <= JOINT_BEVEL_MAX_DEG:
                bad(translate("joint_bevel_deg={value:g} is outside 0–{maximum:g}", value=p.joint_bevel_deg, maximum=JOINT_BEVEL_MAX_DEG),
                    translate("Set joint_bevel_deg to 0–{maximum:g}", maximum=JOINT_BEVEL_MAX_DEG))  # fmt: skip
            elif d.ji + d.jw * d.jt >= d.hb / 2:
                bad(
                    translate("The joint tab is too short after beveling both ends"),
                    translate("Reduce joint_inset or joint_bevel_deg"),
                )
        if not t <= d.slot <= 3 * t + 2:
            bad(
                translate(
                    "slot_w={value:g} is outside t–3t+2 ({minimum:g}–{maximum:g})",
                    value=d.slot,
                    minimum=t,
                    maximum=3 * t + 2,
                ),
                translate("Set slot_w to {minimum:g}–{maximum:g}", minimum=t, maximum=3 * t + 2),
            )
        if not 0 <= p.flap_gap <= FLAP_GAP_MAX_MM:
            bad(translate("flap_gap={value:g} is outside 0–{maximum:g}. The outer flaps cannot overlap.", value=p.flap_gap, maximum=FLAP_GAP_MAX_MM),
                translate("Set flap_gap to 0–{maximum:g}", maximum=FLAP_GAP_MAX_MM))  # fmt: skip
        if p.flap_gap >= d.Wi:
            bad(
                translate("flap_gap must be smaller than the inside width"),
                translate("Reduce flap_gap"),
            )
        if not 0 <= d.short <= 2 * t:
            bad(
                translate(
                    "last_panel_short={value:g} is outside 0–2t ({maximum:g})",
                    value=d.short,
                    maximum=2 * t,
                ),
                translate("Set last_panel_short to 0–{maximum:g}", maximum=2 * t),
            )
        if d.flap <= 0 or d.wsb - d.slot <= 0:
            bad(
                translate("The flap dimensions are not positive"),
                translate("Increase the box dimensions or reduce slot_w or flap_gap"),
            )
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        """只提醒、不拦：楞型、展开尺寸超过常见设备。"""
        p: SlottedParams = params
        out = flute_advisory(material, USUAL_FLUTES, translate("Slotted container"))
        d = self.resolve_dims(p, material, profile or load_profile("default"))
        w, h = d.jw + 2 * d.wb + d.ws + d.wsb, d.hb + 2 * d.flap
        if w > BLANK_WARN_MM[0] or h > BLANK_WARN_MM[1]:
            out.append(advisory(translate("The blank is {width:.0f} × {height:.0f} mm, beyond typical equipment capacity ({max_width:g} × {max_height:g} mm, provisional)", width=w, height=h, max_width=BLANK_WARN_MM[0], max_height=BLANK_WARN_MM[1]),
                                translate("Ask the factory for the board width and maximum print-slotter size")))  # fmt: skip
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = self.resolve_dims(params, material, profile)
        out = {
            "back": (d.wb, d.hb),
            "side_a": (d.ws, d.hb),
            "front": (d.wb, d.hb),
            "side_b": (d.wsb, d.hb),
        }
        for face, w in (("back", d.wb), ("side_a", d.ws), ("front", d.wb), ("side_b", d.wsb)):
            out[f"btm_{face}"] = (w - d.slot, d.flap)
            if self.has_top:
                out[f"top_{face}"] = (w - d.slot, d.flap)
        return out

    # ---- 生成 ----

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: SlottedParams = params
        d = self.resolve_dims(p, material, profile)
        hb, f, s = d.hb, d.flap, d.slot
        xb0 = d.jw
        xs = [xb0, xb0 + d.wb, xb0 + d.wb + d.ws, xb0 + 2 * d.wb + d.ws]
        faces = ("back", "side_a", "front", "side_b")
        x_end = xs[3] + d.wsb
        spans = list(zip(xs, [*xs[1:], x_end], strict=True))  # 每块面的 (x0, x1)

        def V(x: float, y: float) -> Vec:
            return Vec(x, y)

        def flap_pts(x0: float, x1: float, top: bool) -> list[Vec]:
            """一片摇盖沿外轮廓的折点（从左到右；顶部由调用方反向）。"""
            y = hb + f if top else -f
            y0 = hb if top else 0.0
            return [V(x0 + s / 2, y0), V(x0 + s / 2, y), V(x1 - s / 2, y), V(x1 - s / 2, y0)]

        glue_out = d.ji + d.jw * d.jt
        out: list[Segment] = []
        if d.jw > 0:
            out += _chain([V(0, glue_out), V(xb0, d.ji), V(xb0, 0)])
        bottom: list[Vec] = []
        for x0, x1 in spans:  # 槽：相邻两片摇盖之间在 y=0 的那一小段是切线（方头）
            bottom += flap_pts(x0, x1, top=False)
        out += _chain([V(xb0, 0), *bottom, V(x_end, 0), V(x_end, hb)])
        top: list[Vec] = []
        for x0, x1 in reversed(spans) if self.has_top else []:
            top += list(reversed(flap_pts(x0, x1, top=True)))
        out += _chain([V(x_end, hb), *top, V(xb0, hb)])
        if d.jw > 0:
            out += _chain([V(xb0, hb), V(xb0, hb - d.ji), V(0, hb - glue_out), V(0, glue_out)])
        else:
            out += _chain([V(xb0, hb), V(xb0, 0)])
        outline = Path(out, closed=True)

        polys: dict[str, tuple[PanelRole, Path]] = {}
        if d.jw > 0:
            polys["glue"] = (PanelRole.GLUE_FLAP, _trapezoid(
                [(xb0, d.ji), (0, glue_out), (0, hb - glue_out), (xb0, hb - d.ji)]))  # fmt: skip
        roles_top = {"back": PanelRole.LID, "front": PanelRole.LID}  # 外摇盖（顶面看得到）
        roles_btm = {"back": PanelRole.BOTTOM, "front": PanelRole.BOTTOM}
        creases: list[Edge] = []
        if d.jw > 0:
            creases.append(
                crease("crease_glue_back", (xb0, d.ji), (xb0, hb - d.ji), ["back", "glue"])
            )
        for i, (face, (x0, x1)) in enumerate(zip(faces, spans, strict=True)):
            polys[face] = (PanelRole.BODY, _rect(x0, 0, x1, hb))
            if self.has_top:
                polys[f"top_{face}"] = (
                    roles_top.get(face, PanelRole.DUST_FLAP),
                    _rect(x0 + s / 2, hb, x1 - s / 2, hb + f),
                )
            polys[f"btm_{face}"] = (
                roles_btm.get(face, PanelRole.DUST_FLAP),
                _rect(x0 + s / 2, -f, x1 - s / 2, 0),
            )
            if i > 0:
                creases.append(
                    crease(f"crease_{faces[i - 1]}_{face}", (x0, 0), (x0, hb), [faces[i - 1], face])
                )
            if self.has_top:
                creases.append(
                    crease(
                        f"crease_{face}_top",
                        (x0 + s / 2, hb),
                        (x1 - s / 2, hb),
                        [face, f"top_{face}"],
                    )
                )
            creases.append(
                crease(
                    f"crease_{face}_btm", (x0 + s / 2, 0), (x1 - s / 2, 0), [face, f"btm_{face}"]
                )
            )
        order = ("crease_glue_back", *(e.id for e in creases if e.id != "crease_glue_back"))
        joint_note = {"glue": "Manufacturer's joint: glued (joint flap glued inside side_b)",
                      "stitch": "Manufacturer's joint: stitched (joint flap stitched inside side_b)",
                      "tape": "Manufacturer's joint: taped (no joint flap)"}[p.joint]  # fmt: skip
        return assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=outline,
            polys=_ordered(polys, self.panel_order),
            creases=_ordered_edges(creases, order),
            inner_cuts=[],
            ups={"top_back": "down"},  # 顶面两片外摇盖合上后都从正面读正：后摇盖在展开图里转 180°
            provisional={pid for pid, (role, _) in polys.items() if role != PanelRole.BODY},
            hinges={
                "side_a": "crease_back_side_a",
                "front": "crease_side_a_front",
                "side_b": "crease_front_side_b",
            },
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "compensation_applied": p.apply_compensation,
                "dims": {
                    "Li": d.Li,
                    "Wi": d.Wi,
                    "Hi": d.Hi,
                    "panel_back_front": d.wb,
                    "panel_side": d.ws,
                    "panel_side_last": d.wsb,
                    "body_height": hb,
                    "flap": f,
                    "slot_w": s,
                    "joint_w": d.jw,
                },
                "flute_dir": "y",
                "flute_note": "Flute vertical (along the box height, usual for FBA top-opening cases); not marked on the dieline",
                "joint": p.joint,
                "joint_note": joint_note,
                "print_note": "Slotted cases default to flexo: total ink and minimum type size follow the profile's flexo section (provisional); for preprint use --print-method offset",
                "top": {"halves": ["top_front", "top_back"]} if self.has_top else {"open": True},
                "bottom": {"type": "slotted", "halves": ["btm_front", "btm_back"]},
                "assumptions": [
                    "所有摇盖同长：外摇盖 = 0.5·Wi + k_flap·t − 0.5·flap_gap【初值】",
                    "开槽方头、开到摇盖压线；首尾两端摇盖各让 slot_w / 2【初值】",
                    "接舌挂在 back 左边、糊 / 钉在 side_b 里面；side_b 短 last_panel_short【初值】",
                    "面的图文方向：盒身 +y；顶面外摇盖从正面读正；底面摇盖暂按 +y（待打样）",
                ],
            },
        )
