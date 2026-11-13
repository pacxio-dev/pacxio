"""crashlock_rsc 自动锁底开槽箱（瓦楞）， 节。

印刷面视图，横向排列（同 0201），盒身面正立方向为 +y::

           [ top_back ][top_side_a][ top_front ][top_side_b]
    [glue] [   back   ][  side_a  ][   front   ][  side_b  ]
           [ btm_back ][ btm_sa   ][ btm_front ][  btm_sb  ]   ← 自动锁底，主翼上带糊胶三角

- 盒身、厂商接头、顶摇盖照 0201：接舌挂在 back 左边，最后一块面（side_b）短 t；顶部四片摇盖一样长、
  外摇盖合上在中间对齐、相邻摇盖之间开槽；
- 底部是卡纸自动锁底的同一套机构（`components.lockbottom.CrashBottom`）：两片主翼各一条过角的 45° 斜压线
  分出糊胶三角，糊胶三角的里面粘在侧翼的外面；出厂压扁，撑开时底自己落位。比例按 reference 模板 11201001
  （标注 "FEFCO 0711 regular slotted top - crash lock box"）在网页上读的三份刀版（250×200×160、
  300×200×160、300×120×160；t 1.5 / 2）定：主翼深 0.8·W、主翼外侧斜边 5°、侧翼斜边 20°、糊胶三角离分界
  3 mm、斜压线靠角切开 0.0447·W + 1.4 mm。和卡纸自动锁底不同的这几项都作为参数，卡纸那一款不变。

全部是【初值】：没有折样、没有工厂确认；FEFCO 0711 是参考页的标注，FEFCO 原文未核对。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.components.assembly import stitch
from pacxio.styles.components.carton import _chain, _crease, _trapezoid
from pacxio.styles.components.lockbottom import (
    SHOULDER_MIN_MM,
    TOOTH_MIN_MM,
    WEB_BOTTOM_MIN_MM,
    AutolockParams,
    CrashBottom,
    auto_dims,
)
from pacxio.styles.components.tube4 import BodyX, Tube4
from pacxio.styles.corrugated import advisory, assemble, corrugated_only, flute_advisory
from pacxio.styles.fefco_0201 import (
    BLANK_WARN_MM,
    DOUBLE_WALL_FLUTES,
    FACE_RATIO,
    FLAP_GAP_MAX_MM,
    JOINT_BEVEL_MAX_DEG,
    JOINT_W_DEFAULT_MM,
    JOINT_W_DOUBLE_WALL_MM,
    JOINT_W_RANGE,
    MIN_DIM_MM,
    SLOT_W_MIN_MM,
    SLOT_W_T_RATIO,
    USUAL_FLUTES,
)
from pacxio.styles.rte import RteDims, RteParams, resolve

# ---- 【初值】按 reference 0711 三份刀版定，待折样与工厂确认 ----
MAJOR_W_RATIO = 0.8  # 主翼全深 = 0.8·Wi
MAJOR_TAPER_DEG = 5.0  # 主翼外侧斜边
MINOR_TAPER_DEG = 20.0  # 侧翼靠糊胶三角那边的斜边
WEB_CLEAR_MM = 3.0  # 糊胶三角离面的分界
WEB_CORNER_W_RATIO, WEB_CORNER_EXTRA_MM = 0.0447, 1.4  # 斜压线靠角那段切开（按深度量）
MAX_L_OVER_W = 3.0
OUTER_HEIGHT_LAYERS = 4  # 外高换算：顶两层摇盖 + 底至少两层【初值】


class CrashRscParams(BaseModel):
    """自动锁底开槽箱参数。可选项为 None 时按公式取默认值。"""

    model_config = ConfigDict(allow_inf_nan=False)
    L: float = Field(gt=0, description="Length (front width, the long side of the opening; the major flaps hang here)")
    W: float = Field(gt=0, description="Width (side width)")
    H: float = Field(gt=0, description="Height")
    dim_basis: Literal["inner", "outer"] = "inner"
    joint_w: float | None = Field(default=None, description="Joint flap width; default 35 (40 for BC flute)")
    joint_bevel_deg: float = Field(default=15.0, description="Joint flap end bevel")
    slot_w: float | None = Field(default=None, description="Slot width between top flaps; default max(3, 2t)")
    flap_gap: float = Field(default=2.0, description="Gap between the two outer top flaps when closed")
    major_depth: float | None = Field(default=None, description="Full depth of the bottom major flap; default 0.8·Wi")
    lock_step: float | None = Field(
        default=None, description="Step at the centre of the major flap; default min(4, 0.32·(major flap depth − Ws/2))"
    )
    major_taper_deg: float = Field(default=MAJOR_TAPER_DEG, description="Outer taper angle of the major flap")
    web_corner: float | None = Field(
        default=None, description="How deep the diagonal crease is cut near the corner; default 0.0447·Wi + 1.4"
    )
    web_clear: float = Field(default=WEB_CLEAR_MM, description="Boundary between the glued web triangle and the panel")
    flute_dir: Literal["y"] = "y"
    apply_compensation: bool = True


class CrashDims:
    """解析后的尺寸（mm）。盒身沿用 RTE 的面宽 / 高补偿（和 0201 同一组系数）。"""

    def __init__(self, p: CrashRscParams, material: Material, profile: Profile) -> None:
        t = self.t = material.caliper_mm
        if p.dim_basis == "outer":
            self.Li, self.Wi = p.L - 2 * t, p.W - 2 * t
            self.Hi = p.H - OUTER_HEIGHT_LAYERS * t
        else:
            self.Li, self.Wi, self.Hi = p.L, p.W, p.H
        double = (material.flute or "").upper() in DOUBLE_WALL_FLUTES
        self.jw = (
            p.joint_w
            if p.joint_w is not None
            else (JOINT_W_DOUBLE_WALL_MM if double else JOINT_W_DEFAULT_MM)
        )
        self.ji = t  # 接舌两端缩进
        self.jt = math.tan(math.radians(p.joint_bevel_deg))
        self.rte: RteDims = resolve(
            RteParams(
                L=self.Li,
                W=self.Wi,
                H=self.Hi,
                glue_w=self.jw,
                dust_offset=0,
                apply_compensation=p.apply_compensation,
            ),
            material,
            profile,
        )
        k = coefficients(profile, material, p.apply_compensation)
        self.flap = FACE_RATIO * self.Wi + k.k_flap * t - 0.5 * p.flap_gap
        self.slot = p.slot_w if p.slot_w is not None else max(SLOT_W_MIN_MM, SLOT_W_T_RATIO * t)
        self.short = t  # 最后一块面（side_b）短一层纸厚，同 0201
        self.major = p.major_depth if p.major_depth is not None else MAJOR_W_RATIO * self.Wi
        self.corner = (
            p.web_corner
            if p.web_corner is not None
            else WEB_CORNER_W_RATIO * self.Wi + WEB_CORNER_EXTRA_MM
        )
        self.lock = AutolockParams(
            L=self.Li,
            W=self.Wi,
            H=self.Hi,
            major_depth=self.major,
            lock_step=p.lock_step,
            major_taper_deg=p.major_taper_deg,
        )
        self.bottom = CrashBottom(
            corner=self.corner,
            clear=p.web_clear,
            minor_taper=math.tan(math.radians(MINOR_TAPER_DEG)),
        )


class CrashRscStyle:
    """自动锁底开槽箱（瓦楞）：0201 的盒身和顶摇盖 + 预糊自动锁底。"""

    id: ClassVar[str] = "crashlock_rsc"
    name_zh: ClassVar[str] = "自动锁底开槽箱"
    version: ClassVar[str] = "0.1.0"
    refs: ClassVar[dict[str, str]] = {
        "fefco": "0711（参考页 reference 11201001 的标注，FEFCO 原文未核对）"
    }
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"corrugated"})
    default_print_method: ClassVar[str] = "flexo"
    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b",
        "top_back", "top_side_a", "top_front", "top_side_b",
        "btm_back", "glue_back", "btm_sa", "btm_front", "glue_front", "btm_sb",
    )  # fmt: skip

    def param_schema(self) -> type[BaseModel]:
        return CrashRscParams

    def resolve_dims(self, p: CrashRscParams, material: Material, profile: Profile) -> CrashDims:
        return CrashDims(p, material, profile)

    # ---- 约束 ----

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: CrashRscParams = params
        issues = corrugated_only(self.id, material)
        if issues:
            return issues

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        for name in ("L", "W", "H"):
            if getattr(p, name) < MIN_DIM_MM:
                bad(translate("{parameter}={value:g} is below the provisional minimum {minimum:g}", parameter=name, value=getattr(p, name), minimum=MIN_DIM_MM),
                    translate("Set {parameter} to at least {minimum:g}", parameter=name, minimum=MIN_DIM_MM))  # fmt: skip
        if p.W > p.L:
            bad(
                translate(
                    "The crash-lock bottom requires L ≥ W because the major flaps attach to the long sides. W={width:g} > L={length:g}.",
                    width=p.W,
                    length=p.L,
                ),
                translate("Swap L and W"),
            )
        if issues:
            return issues
        d = self.resolve_dims(p, material, profile or load_profile("default"))
        t, r = d.t, d.rte
        if min(d.Li, d.Wi, d.Hi) <= 0:
            bad(
                translate(
                    "The inside dimensions are not positive after subtracting board thickness"
                ),
                translate("Increase the dimensions or use --basis inner"),
            )
            return issues
        if d.Li / d.Wi > MAX_L_OVER_W:
            bad(
                translate(
                    "L / W = {ratio:.2f} exceeds the provisional limit {maximum:g}",
                    ratio=d.Li / d.Wi,
                    maximum=MAX_L_OVER_W,
                ),
                translate("Reduce L or increase W"),
            )
        lo, hi = JOINT_W_RANGE
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
        if not 0 <= p.joint_bevel_deg <= JOINT_BEVEL_MAX_DEG:
            bad(translate("joint_bevel_deg={value:g} is outside 0–{maximum:g}", value=p.joint_bevel_deg, maximum=JOINT_BEVEL_MAX_DEG),
                translate("Set joint_bevel_deg to 0–{maximum:g}", maximum=JOINT_BEVEL_MAX_DEG))  # fmt: skip
        elif d.ji + d.jw * d.jt >= r.hb / 2:
            bad(
                translate("The joint tab is too short after beveling both ends"),
                translate("Reduce joint_bevel_deg"),
            )
        if not t <= d.slot <= 3 * t + 2:
            bad(translate("slot_w={value:g} is outside t–3t+2 ({minimum:g}–{maximum:g})", value=d.slot, minimum=t, maximum=3 * t + 2),
                translate("Set slot_w to {minimum:g}–{maximum:g}", minimum=t, maximum=3 * t + 2))  # fmt: skip
        if not 0 <= p.flap_gap <= FLAP_GAP_MAX_MM or p.flap_gap >= d.Wi:
            bad(translate("flap_gap={value:g} is outside 0–{maximum:g} or is at least as large as the inside width", value=p.flap_gap, maximum=FLAP_GAP_MAX_MM),
                translate("Set flap_gap to 0–{maximum:g}", maximum=FLAP_GAP_MAX_MM))  # fmt: skip
        if d.flap <= 0 or r.ws - d.short - d.slot <= 0:
            bad(
                translate("The top flap dimensions are not positive"),
                translate("Increase the box dimensions or reduce slot_w or flap_gap"),
            )
        a = auto_dims(d.lock, r)
        clear, corner = p.web_clear, d.corner
        if not 0 < clear < a.half / 2:
            bad(
                translate(
                    "web_clear={value:g} is outside 0–{maximum:.2f}",
                    value=clear,
                    maximum=a.half / 2,
                ),
                translate("Reduce web_clear"),
            )
        if not 0 < corner < a.half - clear:
            bad(translate("web_corner={value:.2f} is outside 0–{maximum:.2f}, leaving no diagonal crease", value=corner, maximum=a.half - clear),
                translate("Reduce web_corner"))  # fmt: skip
        lo, hi = a.half + SHOULDER_MIN_MM, 2 * a.half - clear - WEB_BOTTOM_MIN_MM
        if not lo <= d.major <= hi:
            bad(translate("major_depth={value:.2f} is outside {minimum:.2f}–{maximum:.2f}. Too shallow prevents locking. Too deep removes the base of the glue triangle.", value=d.major, minimum=lo, maximum=hi),
                translate("Set major_depth to {minimum:.2f}–{maximum:.2f}", minimum=lo, maximum=hi))  # fmt: skip
        elif not 0 <= a.step <= 0.5 * (d.major - a.half):
            bad(translate("lock_step={value:.2f} is outside 0–{maximum:.2f}", value=a.step, maximum=0.5 * (d.major - a.half)),
                translate("Set lock_step to 0–{maximum:.2f}", maximum=0.5 * (d.major - a.half)))  # fmt: skip
        elif not 0 <= p.major_taper_deg <= 25:
            bad(
                translate("major_taper_deg={value:g} is outside 0–25", value=p.major_taper_deg),
                translate("Set major_taper_deg to 0–25"),
            )
        elif a.tooth(r.wb) < TOOTH_MIN_MM:
            bad(translate("The tooth on the left section of the major flap is only {width:.2f} mm wide (minimum {minimum:g}), so the two major flaps cannot lock", width=a.tooth(r.wb), minimum=TOOTH_MIN_MM),
                translate("Reduce major_taper_deg or major_depth"))  # fmt: skip
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        d = self.resolve_dims(params, material, profile or load_profile("default"))
        out = flute_advisory(
            material, USUAL_FLUTES, translate("Crash-lock bottom slotted container")
        )
        r = d.rte
        w = d.jw + 2 * r.wb + 2 * r.ws - d.short
        h = r.hb + d.flap + d.major
        if w > BLANK_WARN_MM[0] or h > BLANK_WARN_MM[1]:
            out.append(advisory(translate("The blank is {width:.0f} × {height:.0f} mm, beyond typical equipment capacity ({max_width:g} × {max_height:g} mm, provisional)", width=w, height=h, max_width=BLANK_WARN_MM[0], max_height=BLANK_WARN_MM[1]),
                                translate("Ask the factory for the board width and maximum print-slotter size")))  # fmt: skip
        out.append(advisory(translate("The crash-lock bottom requires a folder-gluer for the side seam and two bottom glue areas. Confirm the glue and ink-free areas on corrugated board with the factory."),
                            translate("Confirm the gluing process with the factory before ordering")))  # fmt: skip
        return out

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = self.resolve_dims(params, material, profile)
        r = d.rte
        a = auto_dims(d.lock, r)
        clear = params.web_clear
        last = r.ws - d.short
        out = {"back": (r.wb, r.hb), "side_a": (r.ws, r.hb), "front": (r.wb, r.hb),
               "side_b": (last, r.hb)}  # fmt: skip
        for face, w in (("back", r.wb), ("side_a", r.ws), ("front", r.wb), ("side_b", last)):
            out[f"top_{face}"] = (w - d.slot, d.flap)
        main, web = (r.wb - a.relief, d.major), (a.half - clear, d.major - d.corner)
        out |= {"btm_back": main, "btm_front": main, "glue_back": web, "glue_front": web,
                "btm_sa": (r.ws, a.half), "btm_sb": (last, a.half)}  # fmt: skip
        return out

    # ---- 生成 ----

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: CrashRscParams = params
        d = self.resolve_dims(p, material, profile)
        r = d.rte
        body = Tube4().build(r)
        x = body.xs
        xs = BodyX(x.xb0, x.xb1, x.xa1, x.xf1, x.xs1 - d.short)  # side_b 短一层纸厚
        hb, f, s, ji = r.hb, d.flap, d.slot, d.ji
        glue_out = ji + d.jw * d.jt
        bottom = d.bottom.build(d.lock, r, xs)

        faces = ("back", "side_a", "front", "side_b")
        spans = list(zip(xs[:4], xs[1:], strict=True))
        top: list[Vec] = []
        for x0, x1 in reversed(spans):
            top += [Vec(x1 - s / 2, hb), Vec(x1 - s / 2, hb + f), Vec(x0 + s / 2, hb + f),
                    Vec(x0 + s / 2, hb)]  # fmt: skip
        outline = stitch(
            _chain([Vec(0, glue_out), Vec(xs.xb0, ji), Vec(xs.xb0, 0)]),
            bottom.outline,
            _chain([Vec(xs.xs1, 0), Vec(xs.xs1, hb)]),
            _chain([Vec(xs.xs1, hb), *top, Vec(xs.xb0, hb)]),
            _chain(
                [Vec(xs.xb0, hb), Vec(xs.xb0, hb - ji), Vec(0, hb - glue_out), Vec(0, glue_out)]
            ),
        )
        body_panels = dict(body.piece.panels)
        body_panels["side_b"] = (PanelRole.BODY, Path.rect(xs.xf1, 0, xs.xs1 - xs.xf1, hb))
        polys: dict[str, tuple[PanelRole, Path]] = {
            "glue": (
                PanelRole.GLUE_FLAP,
                _trapezoid([(xs.xb0, ji), (0, glue_out), (0, hb - glue_out), (xs.xb0, hb - ji)]),
            ),
            **body_panels,
        }
        roles = {"back": PanelRole.LID, "front": PanelRole.LID}
        creases = [
            _crease("crease_glue_back", (xs.xb0, ji), (xs.xb0, hb - ji), ["back", "glue"]),
            *body.piece.creases,
        ]
        for face, (x0, x1) in zip(faces, spans, strict=True):
            polys[f"top_{face}"] = (
                roles.get(face, PanelRole.DUST_FLAP),
                Path.rect(x0 + s / 2, hb, x1 - x0 - s, f),
            )
            creases.append(
                _crease(
                    f"crease_{face}_top", (x0 + s / 2, hb), (x1 - s / 2, hb), [face, f"top_{face}"]
                )  # fmt: skip
            )
        polys |= bottom.polys
        creases += bottom.creases
        order = {pid: i for i, pid in enumerate(self.panel_order)}
        dl = assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=outline,
            polys=dict(sorted(polys.items(), key=lambda kv: order[kv[0]])),
            creases=creases,
            inner_cuts=[],
            ups={"top_back": "down"},  # 顶面两片外摇盖合上后都从正面读正（同 0201）
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
                "accuracy_level": "L0",
                "compensation_applied": p.apply_compensation,
                "dims": {
                    "Li": d.Li,
                    "Wi": d.Wi,
                    "Hi": d.Hi,
                    "outer": [d.Li + 2 * d.t, d.Wi + 2 * d.t, d.Hi + OUTER_HEIGHT_LAYERS * d.t],
                    "panel_back_front": r.wb,
                    "panel_side": r.ws,
                    "panel_side_last": r.ws - d.short,
                    "body_height": hb,
                    "flap": f,
                    "slot_w": s,
                    "joint_w": d.jw,
                    "major_depth": d.major,
                    "web_corner": d.corner,
                    "web_clear": p.web_clear,
                },
                "flute_dir": "y",
                "flute_note": "Flute vertical (along the box height, as 0201); not marked on the dieline",
                "joint": "glue",
                "joint_note": "Manufacturer's joint: glued (joint flap glued inside side_b)",
                "print_note": "Corrugated cases default to flexo: total ink and minimum type size follow the profile's flexo section (provisional); for preprint use --print-method offset",
                "top": {"halves": ["top_front", "top_back"]},
                "bottom": {
                    "type": "autolock",
                    "halves": ["btm_front", "btm_back"],
                    "glue": {
                        "glue_back": "btm_sa",
                        "glue_front": "btm_sb",
                        "faces": "tab_inside_to_flap_outside",
                    },
                },  # fmt: skip
                "assumptions": [
                    "盒身、接头、顶摇盖照 0201；底部为预糊自动锁底，比例按 reference 0711 三份刀版（主翼 0.8·W、"
                    "外侧斜边 5°、侧翼斜边 20°、糊胶三角离分界 3、斜压线靠角切开 0.0447·W + 1.4）【初值】",
                    "外高按顶两层、底至少两层纸板换算【初值】；未折样，楞向、糊胶面与禁墨待工厂确认",
                    "面的图文方向：盒身 +y；顶面外摇盖从正面读正；底面待打样",
                ],
            },
        )
        x0, y0, _, _ = outline.bbox()
        dl.glue_areas = [g.transformed(Affine.translate(-x0, -y0)) for g in bottom.glue_areas]
        return dl
