"""托盘（几何引擎第 2 步，）：底板 + 四面墙 + 墙角机构。

刀版按印刷面（盒外）画，同 tray_glued：底板 [0, Lb] × [0, Wb]，正面墙在下（y < 0）、背面墙在上、
side_a 在右、side_b 在左（从盒底外面看，站在正面看的左侧板在右边）。
立体目标放在立着的盒子里：X = Lb − x，Y = y，Z 向上，底板在 Z = 0（刀版绕 y 轴转 180°，不是镜像）。

墙角机构（corner），角上那块按“角坐标”画：u 沿 x 离开底板、v 沿 y 离开底板（u = 0 是长边墙的端线，
v = 0 是端墙的端线）：
- flap    角翼：corner_on 那对墙（end 端墙 / long 长边墙）两头各带一块角翼（宽 = 墙高、深 corner_depth），
          和隔壁墙之间切一道缝；墙立起来后角翼折 90° 贴在隔壁墙里面（in）或外面（out），粘 / 钉（0452 / 0453）
- gusset  连角：角上整块方片两边都连着墙、对角一道压线；立起来后对折成三角，贴在 corner_on 那面墙
          里面 / 外面（0449 / 0449.1）
- open    不连：十字形，墙角靠胶带 / 钉（0458）
- ear     双层端墙 + 耳朵（0422 型）：直接用校准过的托盘部件（styles/tray.py），见 ear_tray
另有两道只在压扁时才折的斜压线（立起来是平的，画成面里面的压线）：corner_diagonal 角翼对角（0448），
wall_diagonal 长边墙两头从底角斜到墙顶、离墙端一个墙高（0450 / 0451）——糊好以后托盘能压扁运输。
数值全部【初值】。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, Profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.corrugated import assemble, crease
from pacxio.styles.tray import TrayPartParams

Vec3 = tuple[float, float, float]
Target = tuple[str, tuple[float, float], Vec3]
WALLS = ("front", "side_a", "back", "side_b")
CORNERS = (("front", "a"), ("front", "b"), ("back", "a"), ("back", "b"))
CORNER_ZH = {
    "flap": "角翼",
    "gusset": "连角",
    "open": "不连角",
    "ear": "双层端墙 + 耳朵",
    "wrap": "包裹式（端墙挂后墙）",
    "strip": "长条包裹",
    "bevel": "切角 / 八角",
    "polygon": "正多边形",
}
# 【初值】
SLOT_T_RATIO, SLOT_MIN_MM = 1.0, 2.0  # 角翼和隔壁墙之间的缝 = max(t, 2)
CORNER_CLEAR_MM = 2.0  # 两头的角翼在墙中间至少留这么多
LID_TUCK_RATIO, LID_TUCK_CAP_MM, LID_TUCK_CHAMFER_MM = (
    0.5,
    40.0,
    8.0,
)  # 盖插舌深 min(0.5·墙高, 40)，两角斜切
LID_DUST_RATIO, LID_DUST_TAPER_DEG = 0.8, 15.0  # 盖防尘翼深 0.8·墙高，两头斜 15°
LOCK_W_RATIO, LOCK_W_MAX, LOCK_D_RATIO, LOCK_D_MAX = 0.3, 80.0, 0.3, 25.0  # 盖锁舌宽 / 深【初值】


class TrayParams(BaseModel):
    """托盘参数（几何引擎内部用，由结构配方换过来）。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0)
    W: float = Field(gt=0)
    H: float = Field(gt=0)
    dim_basis: Literal["inner", "outer"] = "inner"
    apply_compensation: bool = True
    corner: Literal["flap", "gusset", "open", "ear", "wrap", "strip", "bevel", "polygon"] = "flap"
    poly_sides: int | None = Field(
        default=None, ge=5, le=12, description="polygon：正多边形边数（对边距 = L）"
    )
    poly_flaps: bool = Field(
        default=False, description="polygon：每面墙头上一块角翼贴进下一面墙（0351）"
    )
    bevel: float | None = Field(
        default=None, gt=0, description="bevel：切角边长 c（默认 min(0.75·墙高, 0.2·宽)）"
    )
    bevel_regular: bool = Field(default=False, description="bevel：正八边形（L = W）")
    bevel_on: Literal["base", "long"] = Field(
        default="base", description="bevel：斜墙挂在底板斜边上 / 长边墙两头"
    )
    bevel_join: Literal["open", "flap", "wrap"] = Field(
        default="open",
        description="bevel：墙角不连 / 端墙角翼贴斜墙里面 / 斜墙再接一段包到端墙外面",
    )
    strip: list[dict[str, Any]] | None = Field(
        default=None, description="strip：长条上的面序列和端翼（tray_strip）"
    )
    corner_on: Literal["end", "long"] = Field(
        default="end", description="角翼挂在哪对墙上 / 连角贴在哪面墙上"
    )
    corner_side: Literal["in", "out"] = Field(default="in", description="贴在墙里面还是外面")
    corner_depth: float | None = Field(default=None, gt=0, description="角翼深，默认 = 墙高")
    corner_diagonal: bool = Field(default=False, description="角翼对角一道压扁用的压线（0448）")
    wall_diagonal: bool = Field(
        default=False, description="长边墙两头压扁用的斜压线（0450 / 0451）"
    )
    rim: float | None = Field(default=None, gt=0, description="墙顶往里折平的窄边宽 v（平铺成框）")
    rim_on: Literal["all", "long", "end", "front"] = Field(
        default="all", description="哪几面墙带窄边"
    )
    lugs: bool = Field(default=True, description="ear：内墙插脚插进底板插孔")
    ear_shape: Literal["calibrated", "full"] = "calibrated"
    double_walls: Literal["pair", "all"] = "pair"
    inner_ears: bool = False
    inner_foot: bool = Field(
        default=False, description="ear：内墙下沿一条折边平压在底板上，代替插脚和插孔（0428）"
    )
    rim_ends: Literal["none", "posts", "lugs", "mitre", "tabs", "drop", "wrap"] = Field(
        default="none",
        description="长边墙窄边在墙角怎么收（立柱 / 立柱 + 插脚 / 斜接 / 插舌 / 短舌）",
    )
    stack: Literal["none", "center", "pair", "corners"] = Field(
        default="none", description="端墙顶的堆叠凸舌（中间一个 / 两个 / 靠墙角两个）+ 底板对应的孔"
    )
    lug_count: int | None = Field(default=None, ge=1, le=4, description="ear：内墙插脚数（默认 2）")
    stack_on: Literal["end", "long"] = Field(
        default="end", description="堆叠凸舌做在端墙还是长边墙上"
    )
    end_height: float | None = Field(
        default=None, gt=0, description="端墙比长边墙矮时的端墙高 v2（0481）"
    )
    end_double: bool = Field(
        default=False, description="单层墙托盘的端墙卷边成双层，内墙插脚插进底板（0481）"
    )
    triangle_posts: bool = Field(
        default=False, description="端墙卷边成双层，内层折成两个三角角柱（0433）"
    )
    inner_wings: bool = Field(
        default=False, description="ear：内墙两侧各带一块翼，贴长边墙里面（0432）"
    )
    edge_slots: bool = Field(default=False, description="底板四条边上各两个底边孔（0451.1）")
    corner_meet: bool = Field(default=False, description="角翼深到墙中间，两片对口（0434）")
    foot_depth: float | None = Field(
        default=None, gt=0, description="内墙底折边深，默认 clamp(0.2·墙高, 10, 25)【初值】"
    )
    return_wall: TrayPartParams = Field(
        default_factory=TrayPartParams, description="四面双层：新增卷边、内墙、插脚的微调【初值】"
    )
    return_lug_count: int = Field(default=3, ge=1, le=8, description="新增内墙的插脚数【初值】")
    lid: bool = Field(default=False, description="盖挂在后墙上沿，往前合上，远边插舌插进前墙里面")
    cross_flaps: Literal["none", "half", "center", "full", "long"] = Field(
        default="none",
        description="四壁顶端折翼：半宽、小翼 / 半宽、半长 / 长边全幅重叠 / 只有长边半宽（0437）",
    )
    flap_drops: bool = Field(
        default=False, description="长边半宽盖翼两头折下贴端墙、插脚插底板（0437）"
    )
    end_window: bool = Field(
        default=False, description="端墙顶中间开口，那块往里翻 180° 贴墙（0483）"
    )
    corner_posts: bool = Field(
        default=False, description="端墙角翼折成三角角柱，插舌插底板斜缝（0483）"
    )
    cross_side_depth: float | None = Field(default=None, gt=0, description="端边小翼深【初值】")
    lid_dust: bool = Field(default=True, description="盖两侧带防尘翼（折进端墙里面）")
    lid_front: Literal["tuck", "lip", "return", "lock", "none"] = Field(
        default="tuck",
        description="盖前沿：tuck 插舌插进前墙里面 / lip 前翼盖在前墙外面 / return 前翼 + 回折内衬、锁舌插盖沿槽（0418）",
    )
    dust_ears: bool = Field(
        default=False, description="防尘翼前头多一段耳，折 90° 贴进前墙里面（0475）"
    )
    lid_on: Literal["back", "end"] = Field(
        default="back", description="盖挂在后墙（长边）还是端墙上（0440）"
    )
    front_double: bool = Field(
        default=False, description="只有前墙卷边双层、内墙三个插脚插底板（0426 / 0475）"
    )
    front_return: bool = Field(default=False, description="wrap：前墙顶边再接一块回折内衬（0472）")
    back_double: bool = Field(default=False, description="后墙也卷边双层、内墙三个插脚（0461）")
    hand_holes: bool = Field(
        default=False, description="端墙手孔（端墙双层时内墙同位置也开，0465）"
    )
    flap_slots: bool = Field(
        default=False, description="长边半宽盖翼两头开槽，合上时扣住端墙的凸舌（0464）"
    )
    flap_ears: bool = Field(
        default=False, description="长边半宽盖翼两头接梯形防尘耳，合上时折进端墙里面（0416）"
    )
    end_roll: float | None = Field(
        default=None, gt=0, description="end_double 的卷边宽（宽卷边成平顶，0403）"
    )
    end_foot: bool = Field(default=False, description="end_double 的内墙下沿平脚代替插脚（0403）")
    end_slits: bool = Field(default=False, description="端墙两道竖缝分成三段，顶上窄边连着（0445）")
    corner_hooks: bool = Field(
        default=False, description="扣角：外贴角翼头上的钩舌穿进端墙竖缝（0457）"
    )
    cup_holes: bool = Field(default=False, description="长边墙顶两块半宽隔板上开圆杯孔（0446）")
    front_low: bool = Field(default=False, description="展示盒：前墙矮、端墙顶边斜（0485）")
    lid_header: bool = Field(default=False, description="盖中间一道压线，打开后翻起当头卡（0485）")
    dust_lock: bool = Field(
        default=False, description="盖两侧防尘翼做到墙高、下沿两头锁舌插进底板的槽"
    )
    tear_window: bool = Field(default=False, description="盖中间一圈齿线围成撕开窗")
    display_lid: Literal["none", "rect", "tab"] = Field(
        default="none", description="前墙上沿再挂一层展示盖，压在盖外面、里面粘住撕开窗"
    )
    inside: bool = Field(
        default=False, description="内托：L / W / H 是盒子内尺寸，外尺寸比它小一圈间隙（09）"
    )
    base_holes: int = Field(default=0, ge=0, le=12, description="底板中线上一排圆孔")
    open_taper: bool = Field(
        default=False, description="corners=open：四面墙两头斜收成梯形（0955）"
    )
    tuck: float | None = Field(default=None, gt=0, description="盖的插舌深，默认 min(0.5·墙高, 40)")
    dust_depth: float | None = Field(default=None, gt=0, description="盖的防尘翼深，默认 0.8·墙高")


INSIDE_CLEARANCE_MM = 3.0  # 内托比盒子内长宽小 3（两边合计）【初值】


@dataclass(frozen=True)
class TrayDims:
    t: float
    Li: float
    Wi: float
    Hi: float
    Lb: float  # 底板长（x）
    Wb: float  # 底板宽（y）
    h: float  # 墙高
    slot: float
    depth: float  # 角翼深


def tray_dims(p: TrayParams, material: Material, profile: Profile) -> TrayDims:
    t = material.caliper_mm
    k = coefficients(profile, material, p.apply_compensation)
    if p.dim_basis == "outer":
        Li, Wi, Hi = p.L - 2 * t, p.W - 2 * t, p.H - t
    else:
        Li, Wi, Hi = p.L, p.W, p.H
    if p.inside:  # 放进盒子：外长宽 = 盒内长宽 − 间隙，外高 ≤ 盒内高
        Li, Wi, Hi = Li - INSIDE_CLEARANCE_MM - 2 * t, Wi - INSIDE_CLEARANCE_MM - 2 * t, Hi - t
    Lb, Wb, h = Li + 2 * k.k_panel * t, Wi + 2 * k.k_panel * t, Hi + k.k_height * t
    if p.cross_flaps == "full":
        Lb += 2 * t  # 两端回折墙进入盒内，补偿后仍保留标称内长【初值】
    along = Lb if p.corner_on == "end" else Wb  # 角翼贴的那面墙有多长
    depth = p.corner_depth if p.corner_depth is not None else min(h, 0.5 * along - CORNER_CLEAR_MM)
    if p.corner_meet:
        depth = 0.5 * (along - CORNER_CLEAR_MM)
    if p.corner_hooks and p.corner_depth is None:  # 扣角：两头钩舌的缝留在端墙两侧【初值】
        depth = min(depth, 0.35 * along)
    return TrayDims(t, Li, Wi, Hi, Lb, Wb, h, max(SLOT_T_RATIO * t, SLOT_MIN_MM), depth)


def _display_problems(p: TrayParams) -> list[Issue]:
    """展示开盒选项（tray_display）：只用在挂后墙的托盘连盖上。"""
    used = [k for k, on in (("lid_front=none", p.lid_front == "none"), ("dust_lock", p.dust_lock),
                            ("tear_window", p.tear_window), ("display_lid", p.display_lid != "none")) if on]  # fmt: skip
    if not used:
        return []
    if not p.lid or p.lid_on != "back":
        return [Issue(id="S03", message=translate("{options} apply only to a hinged tray lid attached to the rear wall", options=', '.join(used)),
                      fix="top.kind = hinged_lid，lid_on = back")]  # fmt: skip
    if p.dust_lock and not p.lid_dust:
        return [
            Issue(
                id="S03",
                message=translate("dust_lock requires dust flaps on both sides of the lid"),
                fix=translate("Remove dust=false"),
            )
        ]
    if p.display_lid == "tab":
        from pacxio.engine.tray_display import display_problems

        bad = display_problems(p.L, p.W)
        if bad:
            return [
                Issue(
                    id="S03",
                    message=bad[0],
                    fix=translate(
                        "Increase length or height, or use display_lid=rect for a straight edge without a pull tab"
                    ),
                )
            ]
    if p.lid_header and (p.tear_window or p.display_lid != "none"):
        return [Issue(id="S03", message=translate("The header crease on the lid cannot be combined with a tear-off window or display lid"),
                      fix=translate("Use either lid_header or tear_window with display_lid"))]  # fmt: skip
    return []


def validate(p: TrayParams, material: Material, profile: Profile) -> list[Issue]:
    found = _display_problems(p)
    if found:
        return found
    t = material.caliper_mm
    long_only = p.cross_flaps == "long"  # 只有长边半宽折翼：端墙顶还能带窄边，ear 的长边墙是单层
    if p.cross_flaps != "none" and (
        (p.corner == "ear" and not long_only)
        or (p.rim is not None and not (long_only and p.rim_on == "end"))
        or p.lid
    ):
        return [Issue(id="S03", message=translate("Four wall flaps cannot share the wall top edge with double walls, a rim, or a hinged lid"),
                      fix=translate("Use single walls and remove rim or the hinged lid. Half-width long-side flaps (flap=long) can use an end-wall rim or ear."))]  # fmt: skip
    if p.cross_flaps != "half" and p.cross_side_depth is not None:
        return [
            Issue(
                id="S03",
                message=translate("End flap depth applies only to half flaps"),
                fix=translate("Remove depth or use half"),
            )
        ]
    if p.ear_shape == "full" and (p.corner != "ear" or p.lugs or p.inner_ears or p.lid):
        return [Issue(id="S03", message=translate("Full-width corner flaps require a lidless tray with no locking tabs or inner-wall ears"),
                      fix=translate("Use corner=ear, lugs=false, inner_ears=false, and remove the lid"))]  # fmt: skip
    if (p.double_walls != "pair" or p.inner_ears) and p.corner != "ear":
        return [
            Issue(
                id="S03",
                message=translate("Four double walls and inner-wall ears apply only to ear trays"),
                fix=translate("Use corner=ear"),
            )
        ]
    if p.inner_ears and p.double_walls == "pair" and p.lid:
        return [
            Issue(
                id="S03",
                message=translate("Trays with ears on two inner walls do not support a hinged lid"),
                fix=translate("Remove the hinged lid"),
            )
        ]
    if p.double_walls != "all" and (p.return_wall.model_fields_set or p.return_lug_count != 3):
        return [
            Issue(
                id="S03",
                message=translate("The additional inner-wall parameters require four double walls"),
                fix=translate("Use double_walls=all"),
            )
        ]
    if p.corner == "ear":
        from pacxio.styles.tray import validate_tray

        rim_ok = p.rim is None or (p.rim_ends == "tabs" and p.rim_on == "long")
        if p.dim_basis != "inner" or not rim_ok or p.wall_diagonal:
            return [Issue(id="S03", message=translate("Double-end-wall trays (ear) use inside dimensions and no rim or diagonal wall crease, except for long-side rim tucks"),
                          fix=translate("Set dim_basis=inner and remove rim or wall_diagonal"))]  # fmt: skip
        if p.corner_on == "long" and p.lid:
            return [Issue(id="S03", message=translate("Double long-side walls (ear with corner_on=long) do not support a lid"),
                          fix=translate("Remove the lid or use double end walls with the lid on a long-side wall (0421, 0428, or 0429)"))]  # fmt: skip
        if p.double_walls == "all" and p.lid:
            return [Issue(id="S03", message=translate("The back-wall rollover on a tray with four double walls shares an edge with the hinged lid"),
                          fix=translate("Remove the lid or use one pair of double walls"))]  # fmt: skip
        if p.inner_foot and (
            p.lugs or p.double_walls != "pair" or p.inner_ears or p.ear_shape != "calibrated"
        ):
            return [Issue(id="S03", message=translate("Inner wall foot flaps require one pair of double walls, calibrated ears, and no locking tabs (0428 style)"),
                          fix=translate("Set lugs=false and double_walls=pair, then remove inner_ears or ear_shape"))]  # fmt: skip
        k = coefficients(profile, material, p.apply_compensation)
        li, wi = (p.W, p.L) if p.corner_on == "long" else (p.L, p.W)
        part = _ear_dims(p, li, wi, t, k.k_panel, k.k_height, material.type == "corrugated")
        if p.inner_ears and p.double_walls == "pair":
            part = replace(
                part, ed=part.hf, lug_y=[part.ii + (i + 1) * part.wall_w / 4 for i in range(3)]
            )
        # 新组合底槽为分层加深；旧部件对槽深的上限不适用，额外检查底槽间距。
        check_part = (
            replace(part, sd=min(part.sd, max(4 * t, 4.0)))
            if p.double_walls == "all" or p.inner_ears
            else part
        )
        if p.ear_shape == "full":
            check_part = replace(check_part, eo=t, eg=t)
        out, wall = validate_tray(check_part, "托盘", "tray"), part.hf
        if p.rim_ends == "tabs" and p.rim is not None:
            from pacxio.engine.tray_rims import tab_width

            if tab_width(p.rim, part.ii) < 6.0:
                out.append(Issue(id="S03", message=translate("Rim width {width:g} is too narrow for the tuck tab, which needs at least 6", width=p.rim),
                                 fix=translate("Increase rim or W")))  # fmt: skip
        if p.inner_ears and p.double_walls == "pair" and 2 * part.hf > part.li - 2 * t:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "The inner-wall ears are too deep and collide at the end wall"
                    ),
                    fix=translate("Reduce H or lengthen the end wall"),
                )
            )
        if p.inner_ears and p.double_walls == "pair" and p.lugs:
            if part.wall_w / 4 - part.sw < 5.0:
                out.append(
                    Issue(
                        id="S03",
                        message=translate(
                            "There is too little spacing between the three bottom slots for the locking tabs"
                        ),
                        fix=translate("Lengthen the double wall or reduce the locking tab size"),
                    )
                )
            if 2 * part.sd + 2 >= part.bl:
                out.append(
                    Issue(
                        id="S03",
                        message=translate(
                            "The slots for tabs on opposite sides collide in the middle of the base"
                        ),
                        fix=translate("Lengthen the end wall or use thinner board"),
                    )
                )
        if p.double_walls == "all":
            from pacxio.engine.tray_roll import return_dims, validate_return

            out += validate_return(p, part, return_dims(p, part))
        if p.inner_foot:
            from pacxio.engine.tray_foot import validate_feet

            out += validate_feet(p, part)
    else:
        d = tray_dims(p, material, profile)
        out, wall = problems(p, d), d.h
        if p.corner == "wrap" and (
            p.lid_dust and p.lid or p.rim is not None or p.cross_flaps != "none"
        ):
            out.append(Issue(id="S03", message=translate("The wrap-around construction has double end walls and cannot combine its lid with dust flaps, a rim, or four wall flaps"),
                             fix=translate("Set lid dust=false and remove rim or flaps")))  # fmt: skip
        if p.corner == "polygon" and (p.lid or p.rim is not None or abs(d.Lb - d.Wb) > 1e-6):
            out.append(
                Issue(
                    id="S03",
                    message=translate("Regular polygon trays require L = W and have no lid or rim"),
                    fix=translate("Set W equal to L"),
                )
            )
        if p.corner == "bevel":
            from pacxio.engine.tray_bevel import validate as bevel_validate

            out += bevel_validate(p, d)
            if p.rim is not None and p.rim_on != "long" or p.lid and p.lid_front != "tuck":
                out.append(
                    Issue(
                        id="S03",
                        message=translate(
                            "Cut-corner trays support a long-side rim and tuck lid only"
                        ),
                        fix="rim_on=long，lid_front=tuck",
                    )
                )
        if p.corner == "strip":
            from pacxio.engine.tray_strip import validate as strip_validate

            if p.strip is None or p.lid or p.rim is not None:
                out.append(Issue(id="S03", message=translate("A strip box (corner=strip) requires strip and has no separate lid or rim"),
                                 fix=translate("Specify the panel sequence in body.strip")))  # fmt: skip
            else:
                out += strip_validate(p.strip, d.Wb, d.h, d.Lb)
        if p.front_return and p.corner != "wrap":
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "front_return applies only to wrap-around trays (corner=wrap)"
                    ),
                    fix="corner=wrap",
                )
            )
    if p.stack != "none":
        from pacxio.engine import tray_stack

        length = part.bw if p.corner == "ear" else (d.Lb if p.stack_on == "long" else d.Wb)
        out += tray_stack.validate(p, length, t)
        if p.corner == "ear" and p.stack_on == "long":
            out.append(
                Issue(
                    id="S03",
                    message=translate("Stacking tabs on ear trays go on double end walls only"),
                    fix="stack_on=end",
                )
            )
        if p.corner == "ear" and p.stack == "center" and (p.lug_count or 2) % 2 == 0:
            top = tray_stack.tab_h(t) + tray_stack.STACK_CLEAR_MM - part.rg
            if top + tray_stack.recess_depth(t) > part.hin - 8.0:
                out.append(Issue(id="S03", message=translate("The wall is too short. Notches for stacking tabs at the top and bottom would leave no continuous inner wall."),
                                 fix=translate("Increase H or set lug_count=1 so the tab sits at the centre without a notch")))  # fmt: skip
        if p.corner == "ear" and p.corner_on == "long":
            out.append(
                Issue(
                    id="S03",
                    message=translate("Stacking tabs are supported on end walls only"),
                    fix="corner_on=end",
                )
            )
    if p.lug_count is not None and p.corner != "ear":
        out.append(
            Issue(
                id="S03",
                message=translate("lug_count applies only to trays with double end walls (ear)"),
                fix=translate("Remove lug_count"),
            )
        )
    if (p.rim_ends != "none" or p.triangle_posts or p.inner_wings or p.edge_slots or p.end_double
            or p.end_height is not None or p.flap_drops or p.end_window or p.corner_posts
            or p.corner_hooks or p.cup_holes or p.front_low):  # fmt: skip
        from pacxio.engine import tray_rims

        out += tray_rims.validate(p, None if p.corner == "ear" else d)
    if p.lid and p.tuck is not None and p.tuck > wall - t:
        out.append(
            Issue(
                id="S03",
                message=translate("Lid tuck depth {depth:g} exceeds the wall height", depth=p.tuck),
                fix=f"tuck ≤ {wall - t:.1f}",
            )
        )
    if p.lid and p.lid_dust and p.dust_depth is not None and p.dust_depth > wall:
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "Dust flap depth {depth:g} exceeds the wall height", depth=p.dust_depth
                ),
                fix=translate("Reduce dust_depth"),
            )
        )
    return out


def problems(p: TrayParams, d: TrayDims) -> list[Issue]:
    out = []
    if min(d.Li, d.Wi, d.Hi) <= 0:
        out.append(
            Issue(
                id="S03",
                message=translate("The inside tray dimensions are not positive"),
                fix=translate("Increase L, W, or H"),
            )
        )
        return out
    if p.cross_flaps == "full" and d.Wb <= 4 * d.t:
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "The inner layer of the double end wall has no width after relief"
                ),
                fix=translate("Increase W or use thinner board"),
            )
        )
    if p.corner == "flap":
        along = d.Lb if p.corner_on == "end" else d.Wb
        if 2 * d.depth > along - CORNER_CLEAR_MM:
            out.append(Issue(id="S03", message=translate("Corner flap depth {depth:.1f} is too large. The flaps collide at the centre of the wall.", depth=d.depth),
                             fix=f"corner_depth ≤ {0.5 * (along - CORNER_CLEAR_MM):.1f}"))  # fmt: skip
        if d.h <= d.slot + 5:
            out.append(
                Issue(
                    id="S03",
                    message=translate("The wall is too short to fit the corner flap"),
                    fix=translate("Increase the height H"),
                )
            )
    if p.rim is not None and p.rim > 0.5 * min(d.Lb, d.Wb) - CORNER_CLEAR_MM:
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "Rim width {width:g} is too large. The opposite rims touch.", width=p.rim
                ),
                fix=translate("Reduce rim"),
            )
        )
    if p.cross_flaps == "half" and p.cross_side_depth is not None and p.cross_side_depth > d.Lb / 2:
        out.append(
            Issue(
                id="S03",
                message=translate("The end flaps exceed half the tray length and would collide"),
                fix=translate("Reduce top.depth"),
            )
        )
    if p.corner_diagonal and p.corner != "flap":
        out.append(
            Issue(
                id="S03",
                message=translate("A diagonal corner crease requires a corner flap (flap)"),
                fix=translate("Remove corner_diagonal"),
            )
        )
    if p.wall_diagonal and 2 * d.h > d.Lb - CORNER_CLEAR_MM:
        out.append(Issue(id="S03", message=translate("Diagonal creases from both ends of the long wall extend by one wall height each and meet in the middle"),
                         fix=translate("Reduce H or remove wall_diagonal")))  # fmt: skip
    if p.corner == "gusset":
        along = d.Lb if p.corner_on == "long" else d.Wb
        if 2 * d.h > along - CORNER_CLEAR_MM:
            out.append(Issue(id="S03", message=translate("Corner gussets folded against a wall of height {height:.1f} collide at the ends", height=d.h),
                             fix=translate("Reduce H or attach the gussets to the other pair of walls (corner_on)")))  # fmt: skip
    return out


# ---- 几何 ---------------------------------------------------------------------------------


def _inner_crease(eid: str, a: tuple[float, float], b: tuple[float, float], pid: str) -> Edge:
    """面里面的压线（立起来不折）。"""
    return Edge(id=eid, type=EdgeType.CREASE, segments=[Segment(Vec(*a), Vec(*b))], panels=[pid])


def _ccw(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))
    return pts if area > 0 else pts[::-1]


@dataclass(frozen=True)
class Corner:
    fb: str  # front / back
    ab: str  # a / b
    x0: float
    y0: float
    sx: float
    sy: float

    def xy(self, u: float, v: float) -> tuple[float, float]:
        return (self.x0 + self.sx * u, self.y0 + self.sy * v)

    def uv(self, x: float, y: float) -> tuple[float, float]:
        return (self.sx * (x - self.x0), self.sy * (y - self.y0))

    @property
    def long(self) -> str:
        return self.fb

    @property
    def end(self) -> str:
        return f"side_{self.ab}"


def corners(d: TrayDims) -> list[Corner]:
    out = []
    for fb, ab in CORNERS:
        x0, sx = (d.Lb, 1.0) if ab == "a" else (0.0, -1.0)
        y0, sy = (0.0, -1.0) if fb == "front" else (d.Wb, 1.0)
        out.append(Corner(fb, ab, x0, y0, sx, sy))
    return out


class Frame:
    """刀版点 → 立着的盒子（X = Lb − x，Y = y，Z 向上）。"""

    def __init__(self, d: TrayDims) -> None:
        self.d = d

    def base(self, x: float, y: float) -> Vec3:
        return (self.d.Lb - x, y, 0.0)

    def wall(self, wall: str, x: float, y: float) -> Vec3:
        d = self.d
        if wall == "front":
            return (d.Lb - x, 0.0, -y)
        if wall == "back":
            return (d.Lb - x, d.Wb, y - d.Wb)
        if wall == "side_a":
            return (0.0, y, x - d.Lb)
        return (d.Lb, y, -x)

    def rim(self, wall: str, x: float, y: float) -> Vec3:
        """墙顶窄边折平：在墙高处往盒里伸。"""
        d = self.d
        if wall == "front":
            return (d.Lb - x, -d.h - y, d.h)
        if wall == "back":
            return (d.Lb - x, d.Wb - (y - d.Wb - d.h), d.h)
        if wall == "side_a":
            return (x - d.Lb - d.h, y, d.h)
        return (d.Lb - (-d.h - x), y, d.h)

    def return_end(self, wall: str, x: float, y: float) -> Vec3:
        """端墙内层从墙顶回折贴向底部（零厚度目标）。"""
        d = self.d
        if wall == "side_a":
            return (0.0, y, 2 * d.h - (x - d.Lb))
        return (d.Lb, y, 2 * d.h + x)

    def corner_axes(self, c: Corner) -> tuple[Vec3, Vec3, Vec3]:
        """墙角底点、沿长边墙往里、沿端墙往里（立体）。"""
        origin = self.base(c.x0, c.y0)
        return origin, (c.sx, 0.0, 0.0), (0.0, -c.sy, 0.0)

    def at(self, c: Corner, z: float, along_long: float, along_end: float) -> Vec3:
        o, el, ee = self.corner_axes(c)
        return (o[0] + el[0] * along_long + ee[0] * along_end,
                o[1] + el[1] * along_long + ee[1] * along_end, z)  # fmt: skip


@dataclass
class Tray:
    outline: Path
    panels: dict[str, tuple[PanelRole, Path]]
    creases: list[Edge]
    cuts: list[Edge]
    folds: dict[str, dict[str, Any]]
    targets: list[Target]
    sizes: dict[str, tuple[float, float]]
    faces: list[dict[str, Any]]
    glue: list[Path] = field(default_factory=list)  # 糊胶区（刀版坐标）：展示盖粘撕开窗


def build(p: TrayParams, d: TrayDims) -> Tray:
    Lb, Wb, h, g = d.Lb, d.Wb, d.h, d.slot
    f = Frame(d)
    panels: dict[str, tuple[PanelRole, Path]] = {
        "base": (PanelRole.BOTTOM, Path.rect(0, 0, Lb, Wb)),
        "front": (PanelRole.BODY, Path.rect(0, -h, Lb, h)),
        "back": (PanelRole.BODY, Path.rect(0, Wb, Lb, h)),
        "side_a": (PanelRole.BODY, Path.rect(Lb, 0, h, Wb)),
        "side_b": (PanelRole.BODY, Path.rect(-h, 0, h, Wb)),
    }
    creases = [crease("crease_base_front", (0, 0), (Lb, 0), ["base", "front"]),
               crease("crease_base_back", (0, Wb), (Lb, Wb), ["base", "back"]),
               crease("crease_base_side_a", (Lb, 0), (Lb, Wb), ["base", "side_a"]),
               crease("crease_base_side_b", (0, 0), (0, Wb), ["base", "side_b"])]  # fmt: skip
    later: list[Edge] = []
    cuts: list[Edge] = []
    folds: dict[str, dict[str, Any]] = {w: {"keys": [[1, 90.0]], "layer": 0.0} for w in WALLS}
    sizes = {
        "base": (Lb, Wb),
        "front": (Lb, h),
        "back": (Lb, h),
        "side_a": (h, Wb),
        "side_b": (h, Wb),
    }
    pose: dict[str, Any] = {}  # 面 → 点 (x, y) → 立体
    for w in WALLS:
        pose[w] = lambda x, y, w=w: f.wall(w, x, y)
    pose["base"] = f.base
    if (
        p.rim is not None and p.rim_ends == "none" and p.lid_on == "back"
    ) or p.cross_flaps != "none":  # 墙顶折翼，往里折 90° 平铺（盖挂端墙时窄边另做）
        e = d.t
        long_rim = p.rim_on in ("all", "long")
        for w in WALLS:
            is_long = w in ("front", "back")
            # 这面墙顶上是折翼（cross_flaps）还是窄边（rim）；长边半宽折翼可以和端墙窄边同用（0412 / 0415）
            flap_here = p.cross_flaps not in ("none", "long") or (
                p.cross_flaps == "long" and is_long
            )
            rim_here = (
                not flap_here
                and p.rim is not None
                and p.rim_ends == "none"
                and p.lid_on == "back"
                and (
                    p.rim_on == "all"
                    or (p.rim_on in ("long", "end") and is_long == (p.rim_on == "long"))
                    or (p.rim_on == "front" and w == "front")
                )
            )
            if not flap_here and not rim_here:
                continue
            if flap_here:
                v = (
                    (Wb if p.cross_flaps == "full" else Wb / 2)
                    if is_long
                    else h
                    if p.cross_flaps == "full"
                    else Lb / 2
                    if p.cross_flaps == "center"
                    else p.cross_side_depth
                    if p.cross_side_depth is not None
                    else min(h, Lb / 2 - CORNER_CLEAR_MM)
                )
                inset = 2 * d.t if p.cross_flaps == "full" and not is_long else 0.0
            else:
                assert p.rim is not None
                v = p.rim
                inset = e if is_long else (v if long_rim else e)
            rid = f"rim_{w}"
            if w == "front":
                rect, hinge = (inset, -h - v, Lb - inset, -h), ((inset, -h), (Lb - inset, -h))
            elif w == "back":
                rect, hinge = (
                    (inset, Wb + h, Lb - inset, Wb + h + v),
                    ((inset, Wb + h), (Lb - inset, Wb + h)),
                )
            elif w == "side_a":
                rect, hinge = (
                    (Lb + h, inset, Lb + h + v, Wb - inset),
                    ((Lb + h, inset), (Lb + h, Wb - inset)),
                )
            else:
                rect, hinge = (-h - v, inset, -h, Wb - inset), ((-h, inset), (-h, Wb - inset))
            x0, y0, x1, y1 = rect
            panels[rid] = (PanelRole.DUST_FLAP, Path.rect(x0, y0, x1 - x0, y1 - y0))
            creases.append(crease(f"crease_{rid}", hinge[0], hinge[1], [w, rid]))
            sizes[rid] = (x1 - x0, y1 - y0)
            folds[rid] = {"keys": [[3 if is_long else 2, 90.0]], "layer": 0.0 if is_long else 1.0}
            if not flap_here:
                folds[rid]["keys"] = [[3, 90.0]]
                if p.rim_on == "end":  # 效果图：只有端墙窄边时压在角翼顶上
                    folds[rid]["layer"] = -1.0
            elif p.cross_flaps == "full" and is_long:
                folds[rid] = {"keys": [[3 if w == "front" else 2, 90.0]],
                              "layer": -2.0 if w == "front" else 0.0}  # fmt: skip
            if p.cross_flaps == "full" and not is_long:
                panels[rid] = (PanelRole.INNER_WALL, panels[rid][1])
                folds[rid] = {"keys": [[2, 180.0]], "layer": -2.0}
                pose[rid] = lambda x, y, w=w: f.return_end(w, x, y)
            else:
                pose[rid] = lambda x, y, w=w: f.rim(w, x, y)
    inward = 1.0 if p.corner_side == "in" else -1.0
    for c in corners(d):
        tag = f"{c.fb}_{c.ab}"
        if (
            p.corner == "flap" and p.corner_on == "end"
        ):  # 挂在端墙的端线上（v = 0），和长边墙之间一道缝
            pid, D = f"corner_{tag}", d.depth
            pts = [c.xy(u, v) for u, v in ((g, 0.0), (h, 0.0), (h, D), (g, D))]
            panels[pid] = (
                PanelRole.GLUE_FLAP,
                Path.from_points([Vec(*q) for q in _ccw(pts)], closed=True),
            )
            creases.append(crease(f"crease_{pid}", c.xy(g, 0.0), c.xy(h, 0.0), [c.end, pid]))
            pose[pid] = lambda x, y, c=c: f.at(c, c.uv(x, y)[0], c.uv(x, y)[1], 0.0)
            sizes[pid] = (h - g, D)
            folds[pid] = {"keys": [[2, 90.0]], "layer": inward}
            if p.corner_diagonal:
                cuts.append(_inner_crease(f"crease_{pid}_diag", c.xy(g, 0.0), c.xy(h, D), pid))
        elif p.corner == "flap":  # 挂在长边墙的端线上（u = 0）
            pid, D = f"corner_{tag}", d.depth
            pts = [c.xy(u, v) for u, v in ((0.0, g), (0.0, h), (D, h), (D, g))]
            panels[pid] = (
                PanelRole.GLUE_FLAP,
                Path.from_points([Vec(*q) for q in _ccw(pts)], closed=True),
            )
            creases.append(crease(f"crease_{pid}", c.xy(0.0, g), c.xy(0.0, h), [c.long, pid]))
            pose[pid] = lambda x, y, c=c: f.at(c, c.uv(x, y)[1], 0.0, c.uv(x, y)[0])
            sizes[pid] = (D, h - g)
            folds[pid] = {"keys": [[2, 90.0]], "layer": inward}
            if p.corner_diagonal:
                cuts.append(_inner_crease(f"crease_{pid}_diag", c.xy(0.0, g), c.xy(D, h), pid))
        elif p.corner == "gusset":  # 整块方片，对角压线；贴在 corner_on 那面墙上
            tl, te = f"gusset_{tag}_long", f"gusset_{tag}_end"
            tri_l = [c.xy(0, 0), c.xy(0, h), c.xy(h, h)]
            tri_e = [c.xy(0, 0), c.xy(h, 0), c.xy(h, h)]
            panels[tl] = (
                PanelRole.DUST_FLAP,
                Path.from_points([Vec(*q) for q in _ccw(tri_l)], closed=True),
            )
            panels[te] = (
                PanelRole.DUST_FLAP,
                Path.from_points([Vec(*q) for q in _ccw(tri_e)], closed=True),
            )
            creases += [crease(f"crease_{tl}", c.xy(0, 0), c.xy(0, h), [c.long, tl]),
                        crease(f"crease_{te}", c.xy(0, 0), c.xy(h, 0), [c.end, te])]  # fmt: skip
            later.append(crease(f"crease_{tag}_diag", c.xy(0, 0), c.xy(h, h), [tl, te]))
            sizes |= {tl: (h, h), te: (h, h)}
            if p.corner_on == "long":  # 长边那块翻回贴长边墙，端墙那块转 90° 叠上去
                pose[tl] = lambda x, y, c=c: f.at(c, c.uv(x, y)[1], c.uv(x, y)[0], 0.0)
                pose[te] = lambda x, y, c=c: f.at(c, c.uv(x, y)[0], c.uv(x, y)[1], 0.0)
                folds[tl] = {
                    "keys": [[2, 180.0 * inward]],
                    "layer": -2 * inward,
                }  # 贴着墙（纸按厚度画，差一层会和墙面打架）
                folds[te] = {"keys": [[2, 90.0]], "layer": 4 * inward}  # 叠在上面
            else:
                pose[te] = lambda x, y, c=c: f.at(c, c.uv(x, y)[0], 0.0, c.uv(x, y)[1])
                pose[tl] = lambda x, y, c=c: f.at(c, c.uv(x, y)[1], 0.0, c.uv(x, y)[0])
                folds[te] = {"keys": [[2, 180.0 * inward]], "layer": -2 * inward}
                folds[tl] = {"keys": [[2, 90.0]], "layer": 4 * inward}
        if p.wall_diagonal:  # 长边墙端：底角 → 墙顶离墙端一个墙高
            cuts.append(
                _inner_crease(f"crease_{c.long}_{c.ab}_diag", c.xy(0.0, 0.0), c.xy(-h, h), c.long)
            )
    creases += later
    shape = unary_union([Polygon([(s.start.x, s.start.y) for s in path.segments])
                         for _, path in panels.values()])  # fmt: skip
    if not isinstance(shape, Polygon):
        raise ValueError(translate("The tray outline is disconnected"))
    ring = [(float(x), float(y)) for x, y in shape.exterior.coords[:-1]]
    outline = Path.from_points([Vec(x, y) for x, y in _ccw(ring)], closed=True)
    targets: list[Target] = []
    for pid, (_, path) in panels.items():
        x0, y0, x1, y1 = path.bbox()
        pts = ([(x0, y0), (x1, y0), (x1, y1), (x0, y1)] if pid in WALLS
               else [(s.start.x, s.start.y) for s in path.segments])  # fmt: skip
        targets += [(pid, q, pose[pid](*q)) for q in pts]
    bx = panels["base"][1].bbox()
    faces = [{"key": "bottom", "panel": "base", "rect": list(bx),
              "corners": [list(f.base(x, y)) for x, y in ((bx[0], bx[3]), (bx[2], bx[3]), (bx[2], bx[1]),
                                                          (bx[0], bx[1]))]}]  # fmt: skip
    return Tray(outline, panels, creases, cuts, folds, targets, sizes, faces)


# ---- 盖（连盖托盘 / 飞机盒类） -------------------------------------------------------------


def add_lid(p: TrayParams, tray: Tray, x0: float, x1: float, yh: float, depth: float, hb: float, xref: float,
            t: float, inset: float | None = None) -> Tray:  # fmt: skip
    """盖挂在后墙上沿（刀版上 y = yh，x0..x1），往前合上深 depth（盖到前墙）；远边插舌往下插进前墙里面，
    盖两侧可带防尘翼（折进端墙里面）。立体：X = xref − x，Y 从前墙 0 到后墙 depth，hb = 后墙高。"""
    tk = p.tuck if p.tuck is not None else min(LID_TUCK_RATIO * hb, LID_TUCK_CAP_MM)
    ti, c = (t + 1.0 if inset is None else inset), t + 1.0
    r = min(LID_TUCK_CHAMFER_MM, 0.3 * tk)
    yl, yt = yh + depth, yh + depth + tk
    panels = {"lid": (PanelRole.LID, Path.rect(x0, yh, x1 - x0, depth)),
              "tuck": (PanelRole.TUCK, Path.from_points([Vec(x, y) for x, y in _ccw(
                  [(x0 + ti, yl), (x0 + ti, yt - r), (x0 + ti + r, yt), (x1 - ti - r, yt), (x1 - ti, yt - r),
                   (x1 - ti, yl)])], closed=True))}  # fmt: skip
    creases = [crease("crease_back_lid", (x0, yh), (x1, yh), ["back", "lid"]),
               crease("crease_lid_tuck", (x0 + ti, yl), (x1 - ti, yl), ["lid", "tuck"])]  # fmt: skip
    step = int(max(k[0] for m in tray.folds.values() for k in m["keys"])) + 1
    folds = {"lid": {"keys": [[step + 1, 90.0]], "layer": 0.0},
             "tuck": {"keys": [[step, 45.0], [step + 1, 90.0]], "layer": 1.0}}  # fmt: skip
    pose: dict[str, Any] = {
        "lid": lambda x, y: (xref - x, depth - (y - yh), hb),
        "tuck": lambda x, y: (xref - x, 0.0, hb - (y - yl)),
    }
    sizes = {"lid": (x1 - x0, depth), "tuck": (x1 - x0 - 2 * ti, tk)}
    if p.lid_dust:
        dd = p.dust_depth if p.dust_depth is not None else LID_DUST_RATIO * hb
        k = dd * math.tan(math.radians(LID_DUST_TAPER_DEG))
        for side, xe, s in (("a", x1, 1.0), ("b", x0, -1.0)):
            pid = f"dust_{side}"
            pts = [(xe, yh + c), (xe + s * dd, yh + c + k), (xe + s * dd, yl - c - k), (xe, yl - c)]
            panels[pid] = (
                PanelRole.DUST_FLAP,
                Path.from_points([Vec(x, y) for x, y in _ccw(pts)], closed=True),
            )
            creases.append(crease(f"crease_lid_{pid}", (xe, yh + c), (xe, yl - c), ["lid", pid]))
            folds[pid] = {"keys": [[step, 90.0]], "layer": 1.0}
            pose[pid] = lambda x, y, xe=xe, s=s: (xref - xe, depth - (y - yh), hb - s * (x - xe))
            sizes[pid] = (dd, depth - 2 * c)
            if p.dust_ears:  # 防尘翼前沿取直，再接一段耳（同插舌深），折 90° 贴进前墙里面
                pts = [(xe, yh + c), (xe + s * dd, yh + c + k), (xe + s * dd, yl - c), (xe, yl - c)]
                panels[pid] = (
                    PanelRole.DUST_FLAP,
                    Path.from_points([Vec(x, y) for x, y in _ccw(pts)], closed=True),
                )
                eid = f"{pid}_ear"
                pts = [
                    (xe, yl - c),
                    (xe + s * dd, yl - c),
                    (xe + s * dd, yl - c + tk),
                    (xe, yl - c + tk),
                ]
                panels[eid] = (
                    PanelRole.DUST_FLAP,
                    Path.from_points([Vec(x, y) for x, y in _ccw(pts)], closed=True),
                )
                creases.append(
                    crease(f"crease_{pid}_{eid}", (xe, yl - c), (xe + s * dd, yl - c), [pid, eid])
                )
                folds[eid] = {"keys": [[step, 90.0]], "layer": 1.0}
                pose[eid] = lambda x, y, xe=xe, s=s: (
                    xref - xe + s * (y - yl + c),
                    c,
                    hb - s * (x - xe),
                )
                sizes[eid] = (dd, tk)
    if p.lid_front == "lock":  # 前沿中间一块锁舌（插进前墙顶的槽）
        tw, tl2 = min(LOCK_W_RATIO * (x1 - x0), LOCK_W_MAX), min(LOCK_D_RATIO * hb, LOCK_D_MAX)
        xm = (x0 + x1) / 2
        r2 = min(LID_TUCK_CHAMFER_MM, 0.3 * tl2)
        pts = [(xm - tw / 2, yl), (xm - tw / 2, yl + tl2 - r2), (xm - tw / 2 + r2, yl + tl2), (xm + tw / 2 - r2, yl + tl2),
               (xm + tw / 2, yl + tl2 - r2), (xm + tw / 2, yl)]  # fmt: skip
        panels["tuck"] = (
            PanelRole.TUCK,
            Path.from_points([Vec(x, y) for x, y in _ccw(pts)], closed=True),
        )
        creases[1] = crease(
            "crease_lid_tuck", (xm - tw / 2, yl), (xm + tw / 2, yl), ["lid", "tuck"]
        )
        folds["tuck"] = {"keys": [[step + 1, 90.0]], "layer": 1.0}
        sizes["tuck"] = (tw, tl2)
    if p.lid_front == "lip":  # 前翼整幅、盖在前墙外面
        pts = [(x0, yl), (x0, yt), (x1, yt), (x1, yl)]
        panels["tuck"] = (
            PanelRole.TUCK,
            Path.from_points([Vec(x, y) for x, y in _ccw(pts)], closed=True),
        )
        creases[1] = crease("crease_lid_tuck", (x0, yl), (x1, yl), ["lid", "tuck"])
        folds["tuck"] = {"keys": [[step + 1, 90.0]], "layer": -2.0}
        sizes["tuck"] = (x1 - x0, tk)
    shape = unary_union([Polygon([(q.start.x, q.start.y) for q in tray.outline.segments]),
                         *(Polygon([(q.start.x, q.start.y) for q in path.segments]) for _, path in panels.values())])  # fmt: skip
    if not isinstance(shape, Polygon):
        raise ValueError(translate("The lid and tray do not form a continuous blank"))
    ring = [(float(x), float(y)) for x, y in shape.exterior.coords[:-1]]
    targets = list(tray.targets)
    for pid, (_, path) in panels.items():
        targets += [
            (pid, (q.start.x, q.start.y), pose[pid](q.start.x, q.start.y)) for q in path.segments
        ]
    lx = panels["lid"][1].bbox()
    top = {"key": "top", "panel": "lid", "rect": list(lx),
           "corners": [list(pose["lid"](x, y)) for x, y in ((lx[0], lx[3]), (lx[2], lx[3]), (lx[2], lx[1]),
                                                            (lx[0], lx[1]))]}  # fmt: skip
    return Tray(Path.from_points([Vec(x, y) for x, y in _ccw(ring)], closed=True), tray.panels | panels,
                tray.creases + creases, tray.cuts, tray.folds | folds, targets, tray.sizes | sizes,
                [*tray.faces, top])  # fmt: skip


# ---- 双层端墙 + 耳朵（0422 型，校准过的托盘部件） ----------------------------------------


def _ear_dims(
    p: TrayParams, li: float, wi: float, t: float, kp: float, kh: float, corrugated: bool
) -> Any:
    """沿用原托盘尺寸；四面双层按折后内墙位置补偿输入内尺寸及底槽。"""
    from pacxio.styles.tray import tray_dims as part_dims

    args = (p.H, t, kp, kh, TrayPartParams(lugs=p.lugs), corrugated)
    if p.double_walls != "all":
        if p.inner_ears:
            # 内墙耳片的新组合：按带分层内壁和纸背量出的空间补偿【初值】。
            result = part_dims(li + t, wi + t, *args)
            return replace(result, sd=max(result.sd, result.rg + 3 * t + 1.0))
        if p.ear_shape == "full":
            gap = t if corrugated else 2 * t
            # 按折后内壁 / 单墙纸背间距补偿；3 层夹持、槽和间隙均是【初值】。
            li += 2 * (gap + 3 * t) - (kp + 4) * t
            wi += 2 * t - kp * t
            result = part_dims(li, wi, *args)
            return replace(
                result,
                eo=0.0,
                eg=0.0,
                et=0.0,
                ed=p.corner_depth if p.corner_depth is not None else result.hf,
            )
        return part_dims(li, wi, *args)
    from pacxio.engine.tray_roll import (
        RETURN_INNER_LAYERS,
        SLOT_LAYER_CLEAR_MM,
        compensate_inner_dims,
        end_slot_dims,
    )

    first = part_dims(li, wi + 4 * t, *args)
    adjusted_l, adjusted_w = compensate_inner_dims(p, first, kp)
    if p.inner_ears:
        result = part_dims(adjusted_l, adjusted_w, *args)
    else:
        gap = first.rg if p.return_wall.roll_gap is None else p.return_wall.roll_gap
        inset = max(first.ii, gap + RETURN_INNER_LAYERS * t + SLOT_LAYER_CLEAR_MM)
        part_params = TrayPartParams(lugs=p.lugs, inner_inset=inset)
        result = part_dims(adjusted_l, adjusted_w, p.H, t, kp, kh, part_params, corrugated)
    if p.ear_shape == "full":
        result = replace(
            result,
            eo=0.0,
            eg=0.0,
            et=0.0,
            ed=p.corner_depth if p.corner_depth is not None else result.hf,
        )
    return end_slot_dims(p, result)


def ear_tray(p: TrayParams, material: Material, profile: Profile) -> tuple[Any, Tray]:
    """0422 型：直接用校准过的托盘部件（styles/tray.py，天地盖 / 抽屉打样确认过）的几何，数值不重写：
    端墙双层（外墙 → 卷边 → 内墙翻进来，内墙的插脚插进底板端线上的插孔），长边墙两头的耳朵折进端墙夹层。
    引擎只补折法和立体目标。"""
    from pacxio.styles.tray import tray_geometry
    from pacxio.styles.tray import tray_sizes as part_sizes

    t = material.caliper_mm
    k = coefficients(profile, material, p.apply_compensation)
    li, wi = (p.W, p.L) if p.corner_on == "long" else (p.L, p.W)  # 双层墙在长边墙上 = 0422 转 90°
    d = _ear_dims(p, li, wi, t, k.k_panel, k.k_height, material.type == "corrugated")
    if p.inner_ears and p.double_walls == "pair":
        d = replace(d, ed=d.hf, lug_y=[d.ii + (i + 1) * d.wall_w / 4 for i in range(3)])
    if p.lug_count is not None:
        n = p.lug_count
        d = replace(d, lug_y=[d.ii + (i + 1) * d.wall_w / (n + 1) for i in range(n)])
    g = tray_geometry(d)
    if p.inner_ears and p.double_walls == "pair":
        from pacxio.engine.tray_pair_inner import transform

        g = transform(d, g)
    if p.ear_shape == "full":
        from pacxio.engine.tray_full_ear import add_full_ears

        g = add_full_ears(d, g)
    if p.inner_foot:
        from pacxio.engine.tray_foot import add_feet, foot_depth

        g = add_feet(d, g, foot_depth(p, d))
    bl, bw, ho, rg = d.bl, d.bw, d.ho, d.rg
    xr, xi0 = bl + ho, bl + ho + rg

    def side_a(
        x: float, y: float
    ) -> Vec3:  # 端墙 a：外墙 → 卷边（墙顶往里）→ 内墙（往下，插脚插进底板）
        if x <= xr:
            return (0.0, y, x - bl)
        if x <= xi0:
            return (x - xr, y, ho)
        return (rg, y, ho - (x - xi0))

    def mirror(fn: Any) -> Any:  # b 端 = a 端按 x → bl − x 镜像，立体上 X → bl − X
        def out(x: float, y: float) -> Vec3:
            X, Y, Z = fn(bl - x, y)
            return (bl - X, Y, Z)

        return out

    def ear_front(x: float, y: float) -> Vec3:  # 正面墙右头的耳朵：绕墙端竖线折 90°，伸进端墙夹层
        return (bl - d.X1, x - d.X1, -y)

    def ear_back(x: float, y: float) -> Vec3:
        return (bl - d.X1, bw - (x - d.X1), y - bw)

    pose: dict[str, Any] = {
        "base": lambda x, y: (bl - x, y, 0.0),
        "front": lambda x, y: (bl - x, 0.0, -y),
        "back": lambda x, y: (bl - x, bw, y - bw),
        "side_a": side_a, "rim_a": side_a, "side_a_in": side_a,
        "side_b": mirror(side_a), "rim_b": mirror(side_a), "side_b_in": mirror(side_a),
        "ear_fa": ear_front, "ear_ba": ear_back, "ear_fb": mirror(ear_front), "ear_bb": mirror(ear_back),
    }  # fmt: skip
    if p.inner_ears and p.double_walls == "pair":
        for fb, y0, sy in (("f", d.ii, -1.0), ("b", bw - d.ii, 1.0)):
            for ab, sx in (("a", 1.0), ("b", -1.0)):

                def inner_ear(
                    x: float, y: float, y0: float = y0, sy: float = sy, sx: float = sx
                ) -> Vec3:
                    radial = x if sx == 1 else bl - x
                    return (
                        (rg if sx == 1 else bl - rg) + sx * sy * (y - y0),
                        y0,
                        ho - (radial - xi0),
                    )

                pose[f"ear_{fb}{ab}"] = inner_ear
    folds: dict[str, dict[str, Any]] = {
        "front": {"keys": [[1, 90.0]], "layer": 0.0}, "back": {"keys": [[1, 90.0]], "layer": 0.0},
        "side_a": {"keys": [[2, 90.0]], "layer": 0.0}, "side_b": {"keys": [[2, 90.0]], "layer": 0.0},
        "rim_a": {"keys": [[3, 90.0]], "layer": 0.0}, "rim_b": {"keys": [[3, 90.0]], "layer": 0.0},
        "side_a_in": {"keys": [[3, 90.0]], "layer": -2.0}, "side_b_in": {"keys": [[3, 90.0]], "layer": -2.0},
    }  # fmt: skip
    # 效果图：一对双层墙时耳朵夹在内外墙中间，错开两层纸厚（四面双层 / 整幅角翼的分层另算）
    ear_layer = 2.0 if p.double_walls == "pair" and p.ear_shape == "calibrated" else 1.0
    for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb"):
        folds[ear] = {"keys": [[1, 90.0]], "layer": ear_layer}
        if p.inner_ears and p.double_walls == "pair":
            folds[ear] = {"keys": [[4, -90.0]], "layer": -1.0}
    if p.ear_shape == "full" and p.double_walls == "pair":
        for side in ("a", "b"):
            folds[f"side_{side}_in"]["layer"] = -3.0
    if rg <= 0:  # 没有卷边：内墙直接挂在外墙上，翻 180°
        for s in ("a", "b"):
            folds[f"side_{s}_in"] = {"keys": [[3, 180.0]], "layer": 0.0}
    if p.inner_foot:  # 内墙下沿的折边：反着折 90°，往盒里平压在底板上
        xe = xi0 + d.hin

        def foot_a(x: float, y: float) -> Vec3:
            return (rg + (x - xe), y, ho - d.hin)

        pose |= {"foot_a": foot_a, "foot_b": mirror(foot_a)}
        for s in ("a", "b"):
            folds[f"foot_{s}"] = {"keys": [[3, -90.0]], "layer": 1.0}
    targets: list[Target] = []
    for pid, (_, path) in g.polys.items():
        x0, y0, x1, y1 = path.bbox()
        pts = ([(x0, y0), (x1, y0), (x1, y1), (x0, y1)] if pid in WALLS
               else [(s.start.x, s.start.y) for s in path.segments])  # fmt: skip
        targets += [(pid, q, pose[pid](*q)) for q in pts]
    bx = g.polys["base"][1].bbox()
    faces = [{"key": "bottom", "panel": "base", "rect": list(bx),
              "corners": [list(pose["base"](x, y)) for x, y in ((bx[0], bx[3]), (bx[2], bx[3]), (bx[2], bx[1]),
                                                                (bx[0], bx[1]))]}]  # fmt: skip
    sizes = part_sizes(d)
    if p.inner_ears and p.double_walls == "pair":
        sizes.update({"front": (d.bl, d.hf), "back": (d.bl, d.hf)})
        sizes.update({ear: (d.hin, d.ed) for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb")})
    if p.ear_shape == "full":
        sizes.update({ear: (d.ed, d.hf) for ear in ("ear_fa", "ear_fb", "ear_ba", "ear_bb")})
    if p.inner_foot:
        sizes.update({f"foot_{s}": (foot_depth(p, d), d.wall_w) for s in ("a", "b")})
    tray = Tray(g.outline, g.polys, g.creases, g.slots, folds, targets, sizes, faces)
    if p.rim_ends == "tabs" or p.inner_wings or p.cross_flaps == "long":
        from pacxio.engine.tray_rims import extend_ear

        tray = extend_ear(p, d, tray)
    if p.stack != "none":
        from pacxio.engine.tray_kit import Kit
        from pacxio.engine.tray_stack import add_stack_tabs, recess_depth

        kit = Kit.of(tray)
        xe = xi0 + d.hin
        centre = p.stack == "center" and d.lugs and 0.5 * bw not in [round(y, 9) for y in d.lug_y]
        rd = recess_depth(t)
        recess = {"a": (xe, rd), "b": (bl - xe, rd)} if centre else None
        recess = {f"side_{ab}": v for ab, v in recess.items()} if recess else None
        add_stack_tabs(p, kit, [("side_a", "x", xr, bl, 1.0, 0.0, bw), ("side_b", "x", bl - xr, 0.0, -1.0, 0.0, bw)],
                       t, recess)  # fmt: skip
        tray = kit.tray()
    if p.double_walls == "all":
        from pacxio.engine.tray_roll import add_return_walls

        tray = add_return_walls(p, d, tray)
    if p.lid:
        inset = 4 * t + 1.0 if p.lid_front == "return" else None  # 回折前沿让开两头的端墙内层
        tray = add_lid(p, tray, d.X0, d.X1, bw + d.hf, bw, d.hf, bl, t, inset)
        for dust in ("dust_a", "dust_b"):  # 效果图：防尘翼在内墙里面
            if dust in tray.folds:
                tray.folds[dust]["layer"] = 3.0
        if p.lid_front == "return":
            from pacxio.engine.tray_lids import lid_return

            tray = lid_return(p, tray, t)
    if p.corner_on == "long":  # 双层那对是长边墙：改面名（几何、立体目标不动）
        tray = _rename(tray, LONG_DOUBLE)
    return d, tray


LONG_DOUBLE = {"side_a": "front", "side_b": "back", "side_a_in": "front_in", "side_b_in": "back_in",
               "rim_a": "rim_front", "rim_b": "rim_back", "front": "side_a", "back": "side_b",
               "front_in": "side_a_in", "back_in": "side_b_in", "rim_front": "rim_a", "rim_back": "rim_b"}  # fmt: skip


def _rename(tray: Tray, names: dict[str, str]) -> Tray:
    def n(pid: str) -> str:
        return names.get(pid, pid)

    creases = [e.model_copy(update={"panels": [n(q) for q in e.panels]}) for e in tray.creases]
    cuts = [e.model_copy(update={"panels": [n(q) for q in e.panels]}) for e in tray.cuts]
    return Tray(tray.outline, {n(k): v for k, v in tray.panels.items()}, creases, cuts,
                {n(k): v for k, v in tray.folds.items()}, [(n(q), xy, t) for q, xy, t in tray.targets],
                {n(k): v for k, v in tray.sizes.items()}, tray.faces)  # fmt: skip


def generate(p: TrayParams, material: Material, profile: Profile, style_id: str) -> Dieline:
    if p.corner == "ear":
        part, tray = ear_tray(p, material, profile)
        outer = list(part.outer)
        if p.corner_on == "long":  # 尺寸表按产品的 L / W 写
            outer = [outer[1], outer[0], outer[2]]
        dims: dict[str, Any] = {"Li": p.L, "Wi": p.W, "Hi": part.hi, "base_l": part.bl, "base_w": part.bw,
                                "wall_h": part.hf, "outer": outer}  # fmt: skip
    else:
        d = tray_dims(p, material, profile)
        tray = plain_tray(p, d)
        dims = {"Li": d.Li, "Wi": d.Wi, "Hi": d.Hi, "base_l": d.Lb, "base_w": d.Wb, "wall_h": d.h,
                "corner_depth": d.depth, "slot": d.slot}  # fmt: skip
    steps = int(max(k[0] for m in tray.folds.values() for k in m["keys"]))
    open_step = steps - 2 if p.lid else steps  # 带盖：停在防尘翼、插舌、盖之前
    plan = {"root": "base", "moves": tray.folds, "steps": steps, "open_step": open_step, "pose_step": 1,
            "ground_step": 1}  # fmt: skip
    zh = "双层长边墙 + 耳朵" if p.corner == "ear" and p.corner_on == "long" else CORNER_ZH[p.corner]
    meta: dict[str, Any] = {
        "name_zh": f"结构配方（几何引擎）（托盘：{zh}）",
        "refs": {"design": "几何引擎：托盘 + 墙角机构（）"},
        "version": "0.1.0",
        "accuracy_level": "L0",
        "compensation_applied": p.apply_compensation,
        "dims": dims,
        "fold_plan": plan,
        "assumptions": [
            "托盘底板、墙高按压线补偿算；角翼深、缝宽、连角折法都是【初值】，要折样确认"
        ],
    }
    if material.type == "corrugated":
        meta["flute_dir"] = "y"
    if p.double_walls == "all" and p.ear_shape == "calibrated":
        meta["assumptions"].append(
            "四面双层墙按 FEFCO 0425 / 0425.1 示意图推读，待核对；新增内墙、耳片、插脚参数为【初值】。"
            "折回检查不含显示分层，未验证成型过程的干涉、夹层松紧和实物承重"
        )
    if p.ear_shape == "full":
        meta["assumptions"].append(
            "整幅角翼和双层墙按 FEFCO 0454 / 0455 / 0456 示意图推读，待核对；"
            "墙角固定方式、纸厚补偿和折叠分层为【初值】，须实物折样确认"
        )
    if p.cross_flaps != "none":
        meta["assumptions"].append(
            "四壁折翼按 FEFCO 0401 / 0402 / 0407 示意图推读，待核对；端翼深和叠层为【初值】。"
            "开角没有自锁，墙角与盖翼的固定方式需按用途确认"
        )
    if p.corner == "ear" and p.inner_foot:
        meta["assumptions"].append(
            "内墙底折边（代替插脚）按 FEFCO 0428 示意图推读，待核对；折边深、斜切为【初值】，"
            "平压在底板上不锁，靠盖和内装物压住，须折样确认"
        )
    if p.corner == "ear" and p.inner_ears and p.double_walls == "pair":
        meta["assumptions"].append(
            "两面内墙带耳片和三个插脚按 FEFCO 0424 示意图推读；耳片深度、分层及插脚槽均为【初值】，须折样确认"
        )
    dl = assemble(style_id=style_id, params=p, material=material, profile=profile, outline=tray.outline,
                  polys=tray.panels, creases=tray.creases, inner_cuts=tray.cuts, ups={},
                  provisional={pid for pid, (role, _) in tray.panels.items()
                               if role not in (PanelRole.BODY, PanelRole.BOTTOM)},
                  hinges=None if p.corner in ("ear", "wrap", "strip", "bevel", "polygon") else {w: f"crease_base_{w}" for w in WALLS},
                  meta=meta)  # fmt: skip
    # assemble 把原点挪到刀版包围盒左下角：目标的刀版坐标一起挪
    minx, miny, _, _ = tray.outline.bbox()
    if p.dust_lock:
        dl.style_meta.setdefault("assumptions", []).append(
            "盖两侧翼倒钩锁舌扣底板的槽：侧面不糊胶，角翼靠这片翼压住；倒刺宽度、颈长是【初值】，要折样确认扣不扣得住"
        )
    if tray.glue:  # 展示盖粘撕开窗：糊胶区画在撕开窗的印刷面上（展示盖背面粘这里）
        from pacxio.geometry import Affine

        dl.glue_areas = [g.transformed(Affine.translate(-minx, -miny)) for g in tray.glue]
        dl.style_meta.setdefault("assumptions", []).append(
            "撕开窗 + 展示盖：撕开窗印刷面上的糊胶区和展示盖背面粘住，打开时撕开窗沿齿线撕下、展示盖立起；"
            "齿线刀距、胶量、锁舌松紧都是【初值】，要折样确认"
        )
    dl.style_meta["structure"] = {
        "tray": True,
        "box": "targets",
        "walls": list(WALLS),
        "corner": p.corner,
        "corner_on": p.corner_on,
        "corner_side": p.corner_side,
        "ear_shape": p.ear_shape,
        "cross_flaps": p.cross_flaps,
        "corner_diagonal": p.corner_diagonal,
        "wall_diagonal": p.wall_diagonal,
        "rim": p.rim,
        "double_walls": p.double_walls,
        "inner_ears": p.inner_ears,
        "inner_foot": p.inner_foot,
        "lid": p.lid,
        "targets": [
            [pid, round(x - minx, 6), round(y - miny, 6), *(round(c, 6) for c in t)]
            for pid, (x, y), t in tray.targets
        ],  # fmt: skip
        "faces": [
            f
            | {
                "rect": [
                    f["rect"][0] - minx,
                    f["rect"][1] - miny,
                    f["rect"][2] - minx,
                    f["rect"][3] - miny,
                ]
            }
            for f in tray.faces
        ],  # fmt: skip
        "sizes": {k: list(v) for k, v in tray.sizes.items()},
    }
    return dl


def sizes(p: TrayParams, material: Material, profile: Profile) -> dict[str, tuple[float, float]]:
    if p.corner == "ear":
        return ear_tray(p, material, profile)[1].sizes
    return plain_tray(p, tray_dims(p, material, profile)).sizes


def plain_tray(p: TrayParams, d: TrayDims) -> Tray:
    """单层墙托盘：build + 窄边收头 / 角柱 / 底边孔 + 连盖。"""
    from pacxio.engine.tray_rims import extend, extend_display, extend_more

    if p.corner == "bevel":
        from pacxio.engine.tray_bevel import build_bevel

        return build_bevel(p, d)
    if p.corner == "polygon":
        from pacxio.engine.tray_bevel import build_polygon

        return build_polygon(p, d)
    if p.corner == "strip":
        from pacxio.engine.tray_strip import build_strip

        assert p.strip is not None
        return build_strip(p.strip, d.Lb, d.Wb, d.h, d.t)
    if p.corner == "wrap":
        from pacxio.engine.tray_wrap import build_wrap

        tray = build_wrap(p, d)
    else:
        tray = extend_display(p, d, extend_more(p, d, extend(p, d, build(p, d))))
    if p.stack != "none":
        from pacxio.engine.tray_kit import Kit
        from pacxio.engine.tray_stack import add_stack_tabs, stack_walls

        kit = Kit.of(tray)
        he = p.end_height if p.end_height is not None else d.h
        add_stack_tabs(p, kit, stack_walls(p, d.Lb, d.Wb, he, d.h), d.t)
        tray = kit.tray()
    if p.lid and p.lid_on == "end":
        from pacxio.engine.tray_lids import lid_on_end

        return lid_on_end(p, d, tray)
    if p.lid:
        # 前墙双层时插舌窄一些，让开两侧折进来的防尘翼【初值】
        inset = (
            4 * d.t + 1.0
            if p.front_double or p.corner == "wrap" or p.lid_front == "return"
            else None
        )
        tray = add_lid(p, tray, 0.0, d.Lb, d.Wb + d.h, d.Wb, d.h, d.Lb, d.t, inset)
        if p.rim is not None or p.front_double:  # 效果图：盖压在窄边、角翼、卷边顶上
            tray.folds["lid"]["layer"] = -2.0
        # 效果图分层：防尘翼在角翼里面；前墙双层时插舌、防尘翼耳在内墙里面
        for pid, layer in (
            ("dust_a", 3.0),
            ("dust_b", 3.0),
            ("dust_a_ear", 3.0),
            ("dust_b_ear", 3.0),
        ):
            if pid in tray.folds and p.corner == "flap":
                tray.folds[pid]["layer"] = layer
        if p.front_double and "tuck" in tray.folds:
            tray.folds["tuck"]["layer"] = 6.0
        if p.lid_front == "return":
            from pacxio.engine.tray_lids import lid_return

            tray = lid_return(p, tray, d.t)
        if p.lid_header:  # 盖中间一道单面压线：合上时不折，打开后翻起当头卡
            from pacxio.engine.tray_kit import Kit

            kit = Kit.of(tray)
            x0, y0, x1, y1 = kit.panels["lid"][1].bbox()
            kit.score("crease_lid_header", (x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2), "lid")
            tray = kit.tray()
        if p.lid_front == "lock" and (p.front_double or p.rim_on == "front" and p.rim is not None):
            from pacxio.engine.tray_lids import lock_slot

            tray = lock_slot(p, tray, d)
        from pacxio.engine.tray_display import extend_lid

        tray = extend_lid(p, d, tray)  # 去插舌 / 侧翼锁舌 / 撕开窗 / 展示盖
    return tray


__all__ = ["TrayDims", "TrayParams", "generate", "problems", "sizes", "tray_dims", "validate"]


# ---- 校准过的飞机盒（fefco_0427）：按面名和包围盒给立体目标 ------------------------------


def mailer_targets(dl: Dieline) -> list[Target]:
    """fefco_0427（插耳做法，校准过）折好后每块面在哪：几何不动，按面名和各面包围盒推。
    盒子坐标同托盘：X = 底板右边 − x，Y = y − 底板下边，Z 向上；盖从后墙上沿往前合，插舌包在前墙外面，
    插耳绕前角折进侧墙夹层，防尘翼折进盒里。"""
    box = {p.id: p.polygon.bbox() for p in dl.panels}
    bx0, by0, bx1, by1 = box["base"]
    bw = by1 - by0
    sa, rim = box["side_a"], box["rim_a"]
    ho, rg = sa[2] - sa[0], rim[2] - rim[0]
    front, back, lid, tuck = (
        box["front"],
        box["back"],
        box["top_lid"],
        box["tuck"] if "tuck" in box else box["top_tuck"],
    )
    hb = back[3] - back[1]
    depth = lid[3] - lid[1]

    def side(x: float, y: float) -> Vec3:  # 端墙 a：外墙 → 卷边 → 内墙
        if x <= sa[2]:
            return (0.0, y - by0, x - bx1)
        if x <= rim[2]:
            return (x - sa[2], y - by0, ho)
        return (rg, y - by0, ho - (x - rim[2]))

    def mirror(fn: Any) -> Any:  # b 端按底板中线镜像
        def out(x: float, y: float) -> Vec3:
            X, Y, Z = fn(bx0 + bx1 - x, y)
            return ((bx1 - bx0) - X, Y, Z)

        return out

    def lid_pt(x: float, y: float) -> Vec3:
        return (bx1 - x, bw - (y - lid[1]), hb)

    def tuck_pt(x: float, y: float) -> Vec3:
        return (bx1 - x, bw - depth, hb - (y - lid[3]))

    pose: dict[str, Any] = {
        "base": lambda x, y: (bx1 - x, y - by0, 0.0),
        "front": lambda x, y: (bx1 - x, 0.0, by0 - y),
        "back": lambda x, y: (bx1 - x, bw, y - by1),
        "side_a": side, "rim_a": side, "side_a_in": side,
        "side_b": mirror(side), "rim_b": mirror(side), "side_b_in": mirror(side),
        "ear_fa": lambda x, y: (bx1 - front[2], x - front[2], by0 - y),
        "ear_fb": lambda x, y: (bx1 - front[0], front[0] - x, by0 - y),
        "ear_ba": lambda x, y: (bx1 - back[2], bw - (x - back[2]), y - by1),
        "ear_bb": lambda x, y: (bx1 - back[0], bw - (back[0] - x), y - by1),
        "top_lid": lid_pt,
        "dust_la": lambda x, y: (bx1 - lid[2], bw - (y - lid[1]), hb - (x - lid[2])),
        "dust_lb": lambda x, y: (bx1 - lid[0], bw - (y - lid[1]), hb - (lid[0] - x)),
        "top_tuck": tuck_pt,
        "tuck_ear_a": lambda x, y: (bx1 - tuck[2], bw - depth + (x - tuck[2]), hb - (y - lid[3])),
        "tuck_ear_b": lambda x, y: (bx1 - tuck[0], bw - depth + (tuck[0] - x), hb - (y - lid[3])),
    }  # fmt: skip
    out: list[Target] = []
    for p in dl.panels:
        if p.id not in pose:
            raise ValueError(translate("No placement rule for mailer panel {panel}", panel=p.id))
        x0, y0, x1, y1 = p.polygon.bbox()
        pts = ([(x0, y0), (x1, y0), (x1, y1), (x0, y1)] if p.id in WALLS
               else [(s.start.x, s.start.y) for s in p.polygon.segments])  # fmt: skip
        out += [(p.id, q, pose[p.id](*q)) for q in pts]
    return out
