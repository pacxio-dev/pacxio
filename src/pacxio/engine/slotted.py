"""瓦楞开槽箱（FEFCO 02）的封口积木（几何引擎第 1 步，）。

盒身、接头（糊 / 钉 / 胶带）、开槽、最后一面让一层纸厚、摇盖长度的补偿都用校准过的 0201 那一套（SlotDims）。
两端各一块积木（每块都按“底端”画：铰链线 y = 0、翼往 −y、从左往右走；顶端沿 y 镜像再反着走）：
- open      开口（0200 的顶、0221 的顶）
- flaps     摇盖，按 FEFCO 的长度规则（flap）：
            half 0201（四片 ½W）、overlap 0202（(W + o) / 2）、full 0203（四片 W）、
            center 0204（长边 ½W、端边 ½L，四片都合到中间）、center_overlap 0205（四片 ½L）、
            center_full 0206（长边 W、端边 ½L）、rim 0209 / 0214（四片窄边 v，平铺成框或折下去贴墙）、
            rim_long 0229（长边窄边 v + 往下折的内板，端边 ½L 盖在上面）、rim_end 0229.1（端边窄边 + 内板，长边 ½W）、
            gusset 0226（不开槽、端摇盖三角折页）、divider 0208（头上接隔板）、cells 0207（分格底）、
            slot_lock / tongue_slot / tongue_notch 0232 / 0233 / 0233.1（不用胶带的自锁底）；
            选项：wing 侧翼（0225 / 0227）、thumb 拇指缺口、lock 锁舌 + 斜缝（0218）、end_flaps 端墙带不带摇盖
- lid_tuck  插舌盖（0210 / 0211 / 0212 / 0215）：盖挂一面长边、远边带插舌，两端边带防尘翼，对面长边不带翼
- snaplock  扣底（0215 / 0216 / 0220 / 0221）：同彩盒扣底的比例（那套比例本来就取自 0215 的样子），顶部也能用（0220）
- hanger    双层挂板 + 插舌盖（0240，只在顶）：on 那面往上加高一块挂板、对折成两层（两层挂孔对齐），盖挂对面
- gable     提手山形顶（0217，只在顶）：长边屋顶 + 手提板（提手孔），端边带缝的三角端翼——直接用彩盒山形顶的组件
- handle    提手平顶（0222）、hang_tab 端摇盖挂板 + 开缝盖（0241）、hang_mid 中间双层挂板 + 套盖（0242）、
            display 展示板 + 盖（0219）——都只在顶
盒身另有“接头在墙中间”（joint_offset / joint_wall，0230 / 0231）和接头那截外面接隔板（joint_partition，0228）。

0201 / 0202 / 0203 的摇盖和现有入口逐面一样；其余长度规则、插舌盖、瓦楞扣底的数值都是【初值】（推导，没有折样），
0207 / 0208 / 0218 / 0219 / 0222 / 0225–0228 / 0232 / 0233 / 0233.1 / 0241 / 0242 的机构按示意图推读，待核对。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, Segment, Vec
from pacxio.messages import lang
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, Profile, load_profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.components.base import Port
from pacxio.styles.components.carton import (
    DUST_BACK_TIP_MM,
    DUST_BASE_INSET_MM,
    GLUE_ARC_H_MM,
    GLUE_BEVEL_MM,
    GLUE_MIN_STRAIGHT_MM,
    RteDims,
    _backwards,
    _chain,
    _rect,
    _trapezoid,
)
from pacxio.styles.components.folds import FoldAction, action
from pacxio.styles.components.holes import HoleParams, hanging_hole
from pacxio.styles.components.lockbottom import SnapBottom, SnaplockParams
from pacxio.styles.components.tube4 import BodyX
from pacxio.styles.components.tuck import DustFlap, TuckLid
from pacxio.styles.corrugated import assemble, crease
from pacxio.styles.fefco_0201 import SlotDims, SlottedParams, SlottedStyle
from pacxio.styles.hanger import hanger_piece
from pacxio.styles.lockbottom import SnaplockStyle

Wall = Literal["back", "front"]
OPPOSITE = {"back": "front", "front": "back"}
FACES = ("back", "side_a", "front", "side_b")
LONG = ("back", "front")
KINDS = frozenset(
    {
        "open",
        "flaps",
        "lid_tuck",
        "snaplock",
        "gable",
        "hanger",
        "handle",
        "hang_tab",
        "hang_mid",
        "display",
        "autolock",
        "slope",
        "reduced",
        "seal",
        "side_tuck",
    }
)
TOP_ONLY = frozenset({"gable", "hanger", "handle", "hang_tab", "hang_mid", "display", "slope"})
FlapRule = Literal[
    "half", "overlap", "full", "center", "center_overlap", "center_full", "rim", "rim_long", "rim_end",
    "gusset", "divider", "cells", "slot_lock", "tongue_slot", "tongue_notch", "full_long",
]  # fmt: skip
OVERLAPPING = frozenset(
    {"overlap", "full", "center_overlap", "center_full", "full_long"}
)  # 两片长摇盖互相叠
GLUED_KINDS = frozenset({"reduced", "seal"})  # 粘合封口（原彩盒拼装器的，§20 第三阶段并进来）
GLUE_BAND_MM = 5.0  # 粘合区宽（离翼远边让开半个开槽宽）【初值】
SEAL_OVERLAP_MM = 10.0  # 四翼搭粘两片长翼重叠（同组合彩盒 end_overlap）【初值】
REDUCED_W_RATIO = 0.4  # 缩小翼深默认 = 0.4·内宽（reference 公开刀版 5 / 12）【初值】
KIND_ZH = {
    "open": "开口",
    "flaps": "摇盖",
    "lid_tuck": "插舌盖",
    "snaplock": "扣底",
    "gable": "提手山形",
    "hanger": "挂板 + 插舌盖",
    "handle": "提手平顶",
    "hang_tab": "端摇盖挂板 + 开缝盖",
    "hang_mid": "中间双层挂板 + 套盖",
    "display": "展示板 + 盖",
    "autolock": "自动锁底",
    "slope": "斜口展示",
    "reduced": "缩小翼粘合",
    "seal": "四翼搭粘",
    "side_tuck": "侧插盖",
}
FLAP_ZH = {"half": "对口", "overlap": "部分重叠", "full": "全重叠", "center": "中心对口",
           "center_overlap": "中心叠盖", "center_full": "长边全叠", "rim": "窄边框",
           "rim_long": "长边窄边 + 内板", "rim_end": "端边窄边 + 内板", "gusset": "三角折页",
           "divider": "隔板摇盖", "cells": "分格底", "slot_lock": "斜缝自锁底", "tongue_slot": "舌头穿缝自锁底",
           "tongue_notch": "舌头嵌缺口自锁底"}  # fmt: skip
# 【初值】
OVERLAP_DEFAULT_MM = 20.0  # 同 0202
RIM_WI_RATIO, RIM_CAP_MM = 0.2, 40.0  # 窄边 v 默认 = min(0.2·W, 40)
RIM_DROP_V_RATIO = 1.5  # 内板默认 = min(1.5·v, 0.5·H)
THUMB_R_MM = 12.0  # 拇指缺口半径
# 提手平顶（0222，按示意图推读）：提手板两边先直上 0.2·高再斜收、顶边每边收进 0.6·高；端摇盖插舌深 min(0.15·W, 25)
HANDLE_STRAIGHT, HANDLE_INSET = 0.2, 0.6
LID_CORNER_R_MM = 10.0  # 0241 盖远边两角圆角
# 0242（按示意图推读）：挂板宽 max(0.33·L, 50)；盖头插舌根宽 0.26·L、高 min(0.15·W, 30)、两边各收 0.15·根宽；
# 端摇盖深 0.55·W、两边各收 0.2·深
HANG_MID_W_RATIO, HANG_MID_W_MIN_MM = 0.33, 50.0
HANG_MID_TAB_RATIO, HANG_MID_TAB_H_RATIO, HANG_MID_TAB_TAPER = 0.26, 0.15, 0.15
HANG_MID_SIDE_RATIO, HANG_MID_SIDE_TAPER = 0.55, 0.2
# 0207 分格底（按示意图推读）：纵隔板第一段 min(0.25·W, ½H)、第二段 0.8 × 第一段；三角口底宽 0.12·W；横隔板顶上短缝 0.13·W
CELLS_LOW_RATIO, CELLS_BACK_RATIO, CELLS_NOTCH_RATIO, CELLS_TOP_SLOT_RATIO = 0.25, 0.8, 0.12, 0.13
# 自锁底（按示意图推读，都 ×W，u 是 ×摇盖宽）
SELF_LOCKS = frozenset({"slot_lock", "tongue_slot", "tongue_notch"})
SLOT_LOCK_INSET = 0.065  # 0232 端摇盖外段两边各收进
SLOT_LOCK_SLIT = ((0.24, 0.065), (0.48, 0.25))  # 0232 斜缝：离长摇盖端 (u, v) → (u, v)
SLOT_LOCK_RISE_T = 2.5  # 0232 端摇盖外段往里折到角抬高 2.5·t（穿过长边摇盖进缝）【初值】
SELF_D, SELF_SIDE_V, SELF_TONGUE_V = 0.65, 0.45, 0.51  # 0233：摇盖深、舌头那片两边直到、舌根
SELF_DIAG_V = {"tongue_slot": 0.09, "tongue_notch": 0.154}  # 两角斜压线起点
SELF_SLOT_U, SELF_BAR_V, SELF_LEG_V, SELF_NOTCH_DEPTH = 0.24, 0.476, 0.563, 0.174
# 0219（按示意图推读）：展示板顶上小舌宽 0.42·L、高 min(0.1·W, 30)；挂槽长 0.4·L；盖远边窄条 min(0.12·W, 40)；
# 端摇盖深 0.37·W；指扣半径 8
DISPLAY_TAB_W_RATIO, DISPLAY_TAB_H_RATIO, DISPLAY_SLOT_RATIO = 0.42, 0.1, 0.4
DISPLAY_BAND_RATIO, DISPLAY_SIDE_RATIO, DISPLAY_NOTCH_R_MM = 0.12, 0.37, 8.0
HANDLE_TAB_W_RATIO, HANDLE_TAB_CAP_MM = 0.15, 25.0
# 锁舌摇盖（0218，按示意图推读，都 ×W）：直边 0.18、斜收到 0.5 处每边收进 0.19、耳 0.08、舌尖 0.7、舌头圆角 0.17；
# 对面那片的斜缝从耳根（收进 0.19、0.5 处）斜到舌尖那条线上、再往里 0.12
LOCK_SHOULDER, LOCK_EAR_V, LOCK_INSET, LOCK_EAR, LOCK_TIP, LOCK_R = 0.18, 0.5, 0.19, 0.08, 0.7, 0.17
LOCK_SLIT_DX = 0.12


class SlotEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    on: Wall | None = None
    hanger_layers: Literal[1, 2] | None = Field(
        default=None, description="挂板层数；不给：卡纸单层、瓦楞双层（hanger_layers()）"
    )
    flap: FlapRule = "half"
    overlap: float | None = None
    depth: float | None = Field(
        default=None, gt=0, description="窄边 v（rim / rim_long / rim_end）"
    )
    drop: float | None = Field(
        default=None, gt=0, description="窄边往下折的内板高（rim_long / rim_end）"
    )
    fold: Literal["in", "down"] = Field(
        default="in", description="rim：平铺成框 / 折下去贴墙（0214）"
    )
    wing: float | None = Field(default=None, gt=0, description="正面摇盖两侧的翼宽（0225 / 0227）")
    thumb: Wall | None = Field(default=None, description="哪片长摇盖远边开拇指缺口")
    lock: bool = Field(default=False, description="on 那片收成锁舌、对面开两道斜缝（0218）")
    glue: bool | None = Field(default=None, description="封口翼上画粘合区；不给按 flap_glue")
    end_flaps: bool = Field(default=True, description="端墙带不带摇盖")
    form: Literal["hook", "tab", "web"] = Field(
        default="hook", description="自动锁底（autolock）的样子，见 engine/slotted_lock.py"
    )
    tear_strip: bool = Field(
        default=False, description="插舌盖（lid_tuck）上一条撕拉条（两道齿线）"
    )


class SlottedAssembledParams(HoleParams, SlottedParams):
    """开槽箱参数（盒身、接头、开槽）+ 两端积木 + 插舌盖 / 扣底的尺寸（不给按初值）。"""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")  # 不认识的微调名报错，不悄悄忽略
    top_end: SlotEnd
    bottom_end: SlotEnd
    tuck_len: float | None = Field(default=None, gt=0)
    dust_depth: float | None = Field(default=None, gt=0)
    friction_lock: bool | None = Field(
        default=None,
        description="插舌盖卡口（插舌根短压线 + 防尘翼卡口）；不给按纸厚（卡纸带、瓦楞不带）",
    )
    major_depth: float | None = None
    notch_w: float | None = None
    notch_d: float | None = None
    minor_depth: float | None = None
    tongue_w: float | None = None
    tongue_len: float | None = None
    handle_h: float | None = Field(default=None, gt=0, description="提手山形顶的手提板高")
    roof_deg: float = Field(default=0.0, ge=0, le=40, description="山形顶屋顶板和水平面的夹角")
    lean_deg: float | None = Field(default=None, description="山形顶端翼斜度；不给按手提板 V 口算")
    handle_hole_w: float | None = Field(default=None, gt=0, description="山形顶提手孔宽")
    handle_hole_h: float | None = Field(
        default=None, gt=0, description="山形顶提手孔高；不给按组件默认"
    )
    hang_hole: Literal["none", "round", "euro"] = "euro"
    hanger_h: float | None = Field(
        default=None,
        ge=20,
        le=100,
        description="挂板高（双层是对折后）；不给：单层 35、双层 50【初值】",
    )
    hanger_radius: float | None = Field(
        default=None, ge=0, description="挂板外角圆角；不给：单层 4、双层 5"
    )
    joint_offset: float | None = Field(
        default=None,
        gt=0,
        description="接头不在墙角：离墙角 v（FEFCO 0230 / 0231），搭接宽用 joint_w",
    )
    joint_wall: Literal["back", "side_b"] = Field(
        default="back", description="接头开在哪面墙：back 长边（0230）/ side_b 端墙（0231）"
    )
    joint_partition: bool = Field(
        default=False,
        description="接头那截外面多一块隔板（0228）：折进去横过箱子，头上糊口粘在对面墙里面；"
        "不给 joint_offset 时接头在墙正中",
    )
    joint_center_open: bool = Field(default=False, description="墙正中断开、无搭接的开口接头")
    joint_full_overlap: bool = Field(default=False, description="筒身首尾两面完整重叠")
    joint_free_tab: bool = Field(default=False, description="侧边接片作为自由插片，不涂胶")
    window: Literal["front", "back"] | None = Field(default=None, description="哪面长墙开窗")
    window_shape: Literal["octagon", "tap", "low"] = "octagon"
    tear: bool = Field(default=False, description="齿线撕开变展示托盘（端墙 side_a 那头）")


def wall_on(spec: SlotEnd) -> Wall:
    """没给面：扣底锁舌在正面（同 0215）、叠盖的外摇盖在正面、插舌盖挂背面（同 0210）。"""
    if spec.on is not None:
        return spec.on
    return "back" if spec.kind in ("lid_tuck", "hanger", "side_tuck") else "front"


@dataclass
class Block:
    """一端封口（底端画法）。outline 从 (xb0, 0) 走到 (x_end, 0)。"""

    outline: list[Segment]
    panels: dict[str, tuple[PanelRole, Path]] = field(default_factory=dict)
    creases: list[Edge] = field(default_factory=list)
    cuts: list[Edge] = field(default_factory=list)
    folds: dict[str, FoldAction] = field(default_factory=dict)
    steps: int = 0
    poses: dict[str, tuple[Any, ...]] = field(default_factory=dict)  # (放法, 墙, 参数…)
    sizes: dict[str, tuple[float, float]] = field(default_factory=dict)
    cover: str | None = None
    glue_areas: list[Path] = field(default_factory=list)  # 粘合区（底端画法，跟着积木一起翻到顶端）
    native_top: bool = False  # 本来就画在顶端（山形顶）：不用镜像
    wall_tops: dict[str, tuple[float, float]] = field(default_factory=dict)  # 斜口：墙左 / 右上沿高


@dataclass(frozen=True)
class Body:
    d: SlotDims
    spans: tuple[tuple[str, float, float], ...]  # (面, x0, x1)，从左往右
    t: float
    pieces: tuple[tuple[str, str, float], ...] = ()  # 接头在墙中间：(面, 所在的墙, 从墙的哪儿起)

    def wall(self, face: str) -> str:
        """这块面是哪面墙（接头在墙中间时，那面墙分成两截：face 和 face_joint）。"""
        return next((w for f, w, _ in self.pieces if f == face), face)

    @property
    def split(self) -> bool:
        return bool(self.pieces)

    @property
    def x0(self) -> float:
        return self.spans[0][1]

    @property
    def x_end(self) -> float:
        return self.spans[-1][2]

    def span(self, face: str) -> tuple[float, float]:
        return next((a, b) for f, a, b in self.spans if f == face)


# ---- 摇盖 ---------------------------------------------------------------------------------


def flap_depths(spec: SlotEnd, d: SlotDims, k_flap: float, gap: float) -> dict[str, float]:
    """每面摇盖深（盒外量）。half / overlap / full 同 0201 / 0202 / 0203 的公式；
    full_long 长摇盖全宽、端摇盖 ½W（ECMA 10 四翼粘合）；seal 两片长翼重叠、端翼 ½W；reduced on 那片满翼、
    其余缩小（ECMA 11）——后三种原来在彩盒拼装器里，一样的规则卡纸、瓦楞共用（§20 第三阶段）。"""
    half_w = d.flap
    t = d.t
    half_l = 0.5 * d.Li + k_flap * t - 0.5 * gap
    full_w = d.Wi + k_flap * t
    if d.Wi > d.Li:  # 宽大于长：端摇盖从两头往中间合，最深到 ½L（同原彩盒拼装器 min(½W, ½L)）
        half_w = min(half_w, half_l)
    if spec.kind == "seal":
        ov = spec.overlap if spec.overlap is not None else SEAL_OVERLAP_MM
        long_s = 0.5 * (d.Wi + ov) + k_flap * t
        return {"back": long_s, "front": long_s, "side_a": half_w, "side_b": half_w}
    if spec.kind == "reduced":
        r = spec.depth if spec.depth is not None else REDUCED_W_RATIO * d.Wi
        on = wall_on(spec)
        return {on: full_w, OPPOSITE[on]: r, "side_a": min(r, half_w), "side_b": min(r, half_w)}
    ov = spec.overlap if spec.overlap is not None else OVERLAP_DEFAULT_MM
    rim = spec.depth if spec.depth is not None else min(RIM_WI_RATIO * d.Wi, RIM_CAP_MM)
    long, end = {
        "half": (half_w, half_w),
        "overlap": (
            (d.Wi + ov) / 2 + k_flap * t,
            min((d.Wi + ov) / 2 + k_flap * t, half_l if d.Wi > d.Li else 1e9),
        ),
        "full": (full_w, full_w),
        "full_long": (full_w, half_w),
        "center": (half_w, half_l),
        "center_overlap": (half_l, half_l),
        "center_full": (full_w, half_l),
        "rim": (rim, rim),
        "rim_long": (rim, half_l),
        "rim_end": (half_w, rim),
        "gusset": (0.5 * d.ws, 0.5 * d.ws),  # 45° 三角折页：深 = 端墙一半
        "divider": (0.5 * (d.ws - t), half_w),
        "cells": (0.5 * (d.ws - t), d.Li / 3),
        "slot_lock": (half_w, half_w),
        "tongue_slot": (SELF_D * d.Wi, min(SELF_D * d.Wi, 0.5 * d.wb - 2.0)),
        "tongue_notch": (SELF_D * d.Wi, min(SELF_D * d.Wi, 0.5 * d.wb - 2.0)),
    }[spec.flap]
    return {"back": long, "front": long, "side_a": end, "side_b": end}


def rim_drop(spec: SlotEnd, d: SlotDims, rim: float) -> float:
    return spec.drop if spec.drop is not None else min(RIM_DROP_V_RATIO * rim, 0.5 * d.Hi)


@dataclass(frozen=True)
class LockTab:
    """锁舌摇盖（0218，量法见 LOCK_*）：直边 v1 → 斜收到 v2（每边收进 g）→ 两耳往外 ear → 舌头到 tip，
    舌头两角圆角 r；对面那片的斜缝横向跨 slit。"""

    v1: float
    v2: float
    g: float
    ear: float
    tip: float
    r: float
    slit: float


def lock_tab(d: SlotDims) -> LockTab:
    w = d.Wi
    return LockTab(LOCK_SHOULDER * w, LOCK_EAR_V * w, LOCK_INSET * w, LOCK_EAR * w, LOCK_TIP * w,
                   LOCK_R * w, LOCK_SLIT_DX * w)  # fmt: skip


def _lock_edge(a: float, b: float, lt: LockTab) -> list[Segment]:
    """锁舌摇盖的外边（底端画法，不含铰链线）：(a, 0) 往下 → 斜收 → 两耳 → 圆角舌头 → 回到 (b, 0)。"""
    q = math.tan(math.radians(22.5))  # 90° 圆角
    xl, xr, yr = a + lt.g - lt.ear, b - lt.g + lt.ear, -(lt.tip - lt.r)
    return [*_chain([Vec(a, 0), Vec(a, -lt.v1), Vec(a + lt.g, -lt.v2), Vec(xl, -lt.v2), Vec(xl, yr)]),
            Segment(Vec(xl, yr), Vec(xl + lt.r, -lt.tip), q),
            *_chain([Vec(xl + lt.r, -lt.tip), Vec(xr - lt.r, -lt.tip)]),
            Segment(Vec(xr - lt.r, -lt.tip), Vec(xr, yr), q),
            *_chain([Vec(xr, yr), Vec(xr, -lt.v2), Vec(b - lt.g, -lt.v2), Vec(b, -lt.v1), Vec(b, 0)])]  # fmt: skip


def _far_edge(x0: float, x1: float, y: float, r: float | None) -> list[Segment]:
    """摇盖远边（从左往右）；给了 r 就在中间开半圆拇指缺口（往铰链那边凹）。"""
    if r is None:
        return _chain([Vec(x0, y), Vec(x1, y)])
    c = 0.5 * (x0 + x1)
    return [*_chain([Vec(x0, y), Vec(c - r, y)]), Segment(Vec(c - r, y), Vec(c + r, y), -1.0),
            *_chain([Vec(c + r, y), Vec(x1, y)])]  # fmt: skip


def _closed(segs: list[Segment]) -> Path:
    return Path([*segs, Segment(segs[-1].end, segs[0].start)], closed=True)


def _cut(eid: str, pts: list[tuple[float, float]], panels: list[str]) -> Edge:
    return Edge(id=eid, type=EdgeType.CUT, segments=_chain([Vec(x, y) for x, y in pts]),
                panels=panels)  # fmt: skip


def has_options(spec: SlotEnd) -> bool:
    return spec.wing is not None or spec.lock or spec.thumb is not None or not spec.end_flaps


def flap_option_problems(
    spec: SlotEnd, d: SlotDims, depths: dict[str, float]
) -> list[tuple[str, str]]:
    """侧翼 / 拇指缺口 / 锁舌 / 不带端摇盖放不放得下。"""
    out: list[tuple[str, str]] = []
    if spec.flap not in ("half", "overlap", "full"):
        return [(translate("Side wings, thumb notches, locking tabs, and omitted end flaps require half, overlap, or full flaps"),
                 translate("Change flap or remove these options"))]  # fmt: skip
    on = wall_on(spec)
    width = d.wb - d.slot
    if spec.wing is not None:
        w, D = spec.wing, depths[on]
        if on != "front":
            out.append((translate("The side wing can only attach to the front flap (wall 2). The joint is at the left of the back wall."),
                        translate("Set on=2. Rotating the box keeps the same structure.")))  # fmt: skip
        if spec.lock:
            out.append(
                (
                    translate("The locking-tab flap cannot also have a side wing"),
                    translate("Remove wing or lock"),
                )
            )
        if w > d.Hi - 2:
            out.append(
                (
                    translate(
                        "Wing width {width:g} exceeds the box height. The closed wing stands inside the end wall.",
                        width=w,
                    ),
                    translate("Reduce wing"),
                )
            )
        if spec.end_flaps:
            if 2 * w > D - 5:
                out.append(
                    (
                        translate(
                            "Wing width {width:g} is too large to fit the 45° bevels at both ends",
                            width=w,
                        ),
                        f"wing ≤ {(D - 5) / 2:.1f}",
                    )
                )
            if d.wsb - d.slot / 2 - w < 10:
                out.append(
                    (
                        translate("Wing width {width:g} leaves no room for the end flap", width=w),
                        translate("Reduce wing"),
                    )
                )
        elif w > d.wsb - d.slot / 2 - 2:
            out.append(
                (
                    translate("Wing width {width:g} exceeds the end-wall width", width=w),
                    translate("Reduce wing"),
                )
            )
    if spec.lock:
        lt = lock_tab(d)
        if spec.flap != "full":
            out.append(
                (
                    translate(
                        "The locking tab requires full-overlap flaps (full). The angled slot goes on the opposite flap."
                    ),
                    translate("Set flap=full"),
                )
            )
        if width - 2 * (lt.g - lt.ear) - 2 * lt.r < 10:
            out.append(
                (
                    translate("The flap is too narrow to fit the locking tab"),
                    translate("Increase L"),
                )
            )
    if spec.thumb is not None:
        D = depths[spec.thumb]
        if spec.lock and spec.thumb == on:
            out.append(
                (
                    translate("A thumb notch cannot go on the locking-tab flap"),
                    translate("Move thumb to the opposite flap"),
                )
            )
        if 4 * THUMB_R_MM > width or 2 * THUMB_R_MM > D:
            out.append(
                (translate("The flap is too small to fit a thumb notch"), translate("Remove thumb"))
            )
    return out


def gusset_block(body: Body, prefix: str) -> Block:
    """0226：不开槽，四片摇盖深都是端墙一半。长边摇盖整片；端摇盖两条斜压线分成中间一块三角（合上平铺）
    和两侧两块小三角折页——沿斜压线折 180° 夹在中间三角和长摇盖之间，另一边和长摇盖之间是压线。"""
    D = 0.5 * body.d.ws
    end = "top" if prefix == "top" else "btm"
    block = Block(outline=_chain([Vec(body.x0, 0), Vec(body.x0, -D), Vec(body.x_end, -D),
                                  Vec(body.x_end, 0)]))  # fmt: skip
    role = PanelRole.LID if prefix == "top" else PanelRole.BOTTOM
    later: list[Edge] = []  # 小三角和长摇盖之间的压线（不是铰链，放在斜压线后面）
    faces = [f for f, _, _ in body.spans]
    for i, (face, x0, x1) in enumerate(body.spans):
        pid = f"{prefix}_{face}"
        block.creases.append(crease(f"crease_{face}_{end}", (x0, 0), (x1, 0), [face, pid]))
        block.poses[pid] = ("flat", face)
        if face in LONG:
            block.panels[pid] = (role, _rect(x0, -D, x1, 0))
            block.sizes[pid] = (x1 - x0, D)
            block.folds[pid] = action(0)
            continue
        xm = 0.5 * (x0 + x1)
        block.panels[pid] = (PanelRole.DUST_FLAP, _trapezoid([(x0, 0), (xm, -D), (x1, 0)]))
        block.sizes[pid] = (x1 - x0, D)
        block.folds[pid] = action(0, layer=2)
        for side, tri, diag, nb, nx in (
            ("l", [(x0, 0), (x0, -D), (xm, -D)], (x0, 0), i - 1, x0),
            ("r", [(x1, 0), (xm, -D), (x1, -D)], (x1, 0), i + 1, x1),
        ):
            gid = f"{pid}_gusset_{side}"
            block.panels[gid] = (PanelRole.DUST_FLAP, _trapezoid(tri))
            block.creases.append(crease(f"crease_{gid}", diag, (xm, -D), [pid, gid]))
            if 0 <= nb < len(faces):
                later.append(crease(f"crease_{prefix}_{faces[nb]}_{gid}", (nx, 0), (nx, -D),
                                    [f"{prefix}_{faces[nb]}", gid]))  # fmt: skip
            block.sizes[gid] = (xm - x0, D)
            block.folds[gid] = action(0, -180.0, layer=-1)  # 夹在中间三角上面、长摇盖下面
            block.poses[gid] = ("gusset", face)
    block.creases += later
    block.steps = 1
    block.cover = f"{prefix}_front"
    return block


def flaps_block(
    spec: SlotEnd, body: Body, prefix: str, k_flap: float, gap: float, glue: bool = False
) -> Block:
    if spec.flap == "gusset":
        return gusset_block(body, prefix)
    if spec.flap == "divider":
        return divider_block(spec, body, prefix)
    if spec.flap == "cells":
        return cells_block(spec, body, prefix)
    if spec.flap in SELF_LOCKS:
        return self_lock_block(spec, body, prefix)
    d, s, t = body.d, body.d.slot, body.t
    depths = flap_depths(spec, d, k_flap, gap)
    drops = {"rim_long": LONG, "rim_end": ("side_a", "side_b")}.get(spec.flap, ())
    end = "top" if prefix == "top" else "btm"
    on = wall_on(spec)
    opp = OPPOSITE[on]
    w = spec.wing
    lt = lock_tab(d) if spec.lock else None
    thumb = THUMB_R_MM if spec.thumb is not None else None
    D_on = depths[on]
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)

    def go(*pts: Vec) -> None:
        nonlocal cur
        out.extend(_chain([cur, *pts]))
        cur = pts[-1]

    def follow(segs: list[Segment]) -> None:
        nonlocal cur
        go(segs[0].start)
        out.extend(segs)
        cur = segs[-1].end

    role_long = PanelRole.LID if prefix == "top" else PanelRole.BOTTOM
    spans = {f: (x0, x1) for f, x0, x1 in body.spans}
    for face, x0, x1 in body.spans:
        wall = body.wall(face)  # 接头在墙中间：两截都按那面墙的摇盖
        if wall not in FACES:
            continue  # 隔板不带摇盖
        D = depths[wall]
        a, b = x0 + s / 2, x1 - s / 2
        pid = f"{prefix}_{face}"
        hinge = (a, b)
        size = (b - a, D)
        if wall not in LONG and not spec.end_flaps:
            continue  # 端墙不带摇盖：外轮廓顺着墙的下沿走过去
        if face in drops:  # 窄边 + 往下折的内板：内板两头各让一层纸厚 + 1，别顶住端墙
            h = rim_drop(spec, d, D)
            e = t + 1.0
            go(Vec(a, 0), Vec(a, -D), Vec(a + e, -D), Vec(a + e, -D - h), Vec(b - e, -D - h),
               Vec(b - e, -D), Vec(b, -D), Vec(b, 0))  # fmt: skip
            drop = f"{prefix}_{face}_drop"
            block.panels[drop] = (PanelRole.DUST_FLAP, _rect(a + e, -D - h, b - e, -D))
            block.creases.append(crease(f"crease_{prefix}_{face}_drop", (a + e, -D), (b - e, -D),
                                        [f"{prefix}_{face}", drop]))  # fmt: skip
            shape = _rect(a, -D, b, 0)
        elif w is not None and face == on:  # 带翼的那片：铰链通到墙角，两侧翼和它之间是压线
            hinge, size = (x0, x1), (x1 - x0, D)
            wa, wb = f"{prefix}_wing_a", f"{prefix}_wing_b"
            if spec.end_flaps:  # 0227：翼两头 45° 斜角，和旁边端摇盖共用一刀
                go(Vec(x0, -D))
                follow(_far_edge(x0, x1, -D, thumb if spec.thumb == face else None))
                go(Vec(x1 + w, -(D - w)))
                block.panels[wa] = (PanelRole.DUST_FLAP, _trapezoid(
                    [(x0, 0), (x0 - w, -w), (x0 - w, -(D - w)), (x0, -D)]))  # fmt: skip
                block.panels[wb] = (PanelRole.DUST_FLAP, _trapezoid(
                    [(x1, 0), (x1, -D), (x1 + w, -(D - w)), (x1 + w, -w)]))  # fmt: skip
            else:  # 0225：端墙不带摇盖，翼是长方形，翼根和端墙下沿之间切开
                go(Vec(x0 - w, 0), Vec(x0 - w, -D), Vec(x0, -D))
                follow(_far_edge(x0, x1, -D, thumb if spec.thumb == face else None))
                go(Vec(x1 + w, -D), Vec(x1 + w, 0))
                block.panels[wa] = (PanelRole.DUST_FLAP, _rect(x0 - w, -D, x0, 0))
                block.panels[wb] = (PanelRole.DUST_FLAP, _rect(x1, -D, x1 + w, 0))
                block.cuts += [_cut(f"cut_{wa}", [(x0 - w, 0), (x0, 0)], [wa, "side_a"]),
                               _cut(f"cut_{wb}", [(x1, 0), (x1 + w, 0)], [wb, "side_b"])]  # fmt: skip
            block.creases += [crease(f"crease_{pid}_wing_a", (x0, 0), (x0, -D), [pid, wa]),
                              crease(f"crease_{pid}_wing_b", (x1, 0), (x1, -D), [pid, wb])]  # fmt: skip
            block.sizes |= {wa: (w, D), wb: (w, D)}
            block.poses |= {wa: ("drop_u", face), wb: ("drop_u", face)}
            edge = _far_edge(x0, x1, -D, thumb if spec.thumb == face else None)
            shape = _closed(
                [*_chain([Vec(x0, 0), Vec(x0, -D)]), *edge, *_chain([Vec(x1, -D), Vec(x1, 0)])]
            )
        elif w is not None and spec.end_flaps and face in ("side_a", "side_b"):
            # 0227 的端摇盖：让出翼的位置（窄一个翼宽、浅一个翼宽），靠翼那头 45° 斜角
            Ds = D_on - w
            if face == "side_a":  # 翼在右边（正面的左翼）
                xw = x1 - w
                go(Vec(a, 0), Vec(a, -Ds), Vec(xw, -Ds))
                pts = [(a, 0), (a, -Ds), (xw, -Ds), (xw, -w), (x1, 0)]
                block.cuts.append(_cut(f"cut_{pid}", [(xw, -Ds), (xw, -w), (x1, 0)],
                                       [pid, f"{prefix}_wing_a"]))  # fmt: skip
                hinge, size = (a, x1), (x1 - a, Ds)
            else:  # 翼在左边（正面的右翼）
                xw = x0 + w
                go(Vec(b, -Ds), Vec(b, 0))
                pts = [(x0, 0), (xw, -w), (xw, -Ds), (b, -Ds), (b, 0)]
                block.cuts.append(_cut(f"cut_{pid}", [(x0, 0), (xw, -w), (xw, -Ds)],
                                       [pid, f"{prefix}_wing_b"]))  # fmt: skip
                hinge, size = (x0, b), (b - x0, Ds)
            shape = _trapezoid(pts)
        elif lt is not None and face == on:  # 锁舌那片
            edge = _lock_edge(a, b, lt)
            follow(edge)
            shape = _closed(edge)
            size = (b - a, lt.tip)
        elif spec.thumb == face:  # 远边开拇指缺口
            edge = [*_chain([Vec(a, 0), Vec(a, -D)]), *_far_edge(a, b, -D, thumb),
                    *_chain([Vec(b, -D), Vec(b, 0)])]  # fmt: skip
            follow(edge)
            shape = _closed(edge)
        else:
            go(Vec(a, 0), Vec(a, -D), Vec(b, -D), Vec(b, 0))
            shape = _rect(a, -D, b, 0)
        block.panels[pid] = (role_long if wall in LONG else PanelRole.DUST_FLAP, shape)
        block.creases.append(
            crease(f"crease_{face}_{end}", (hinge[0], 0), (hinge[1], 0), [face, pid])
        )
        block.sizes[pid] = size
        block.poses[pid] = ("flat", face)
    go(Vec(body.x_end, 0))
    block.outline = out
    if lt is not None:  # 对面那片开两道斜缝：从锁舌耳根斜到舌尖那条线，合上后舌头两角插进去
        ox0, _ = spans[opp]
        nx0, nx1 = spans[on]
        a, b = nx0 + s / 2, nx1 - s / 2
        for side, (u1, u2) in (("a", (a + lt.g, a + lt.g + lt.slit)),
                               ("b", (b - lt.g, b - lt.g - lt.slit))):  # fmt: skip
            pts = [(ox0 + d.wb - (u - nx0), -(d.ws - v)) for u, v in ((u1, lt.v2), (u2, lt.tip))]
            block.cuts.append(_cut(f"cut_{prefix}_slit_{side}", pts, [f"{prefix}_{opp}"]))
    if spec.flap == "rim":  # 四片窄边：平铺成框（端边在下）或一起折下去贴墙
        angle = 180.0 if spec.fold == "down" else 90.0
        layers = (
            {"side_a": 1, "side_b": 1} if angle == 90 else dict.fromkeys(FACES, 1)
        )  # 折下去的贴在墙里面
        block.folds = {f"{prefix}_{f}": action(0, angle, layers.get(f, 0)) for f in FACES}
        block.steps = 1
        if angle == 180:
            block.poses = {f"{prefix}_{f}": ("fold_back", f) for f in FACES}
    elif drops:  # 窄边和内板先折（在下面），另一对摇盖最后盖上
        others = [f for f in FACES if f not in drops]
        for f in drops:
            block.folds[f"{prefix}_{f}"] = action(0, layer=1)
            block.folds[f"{prefix}_{f}_drop"] = action(0, layer=1)
            block.poses[f"{prefix}_{f}_drop"] = ("drop_v", f)
        block.folds |= {f"{prefix}_{f}": action(1) for f in others}
        block.steps = 2
        block.cover = f"{prefix}_{others[0]}" if others[0] in LONG else None
    elif (
        w is not None and spec.end_flaps
    ):  # 0227：带翼那片先合（翼贴端墙），端摇盖再合，对面那片最外
        block.folds = {f"{prefix}_{on}": action(0, layer=3), f"{prefix}_side_a": action(1, layer=2),
                       f"{prefix}_side_b": action(1, layer=1), f"{prefix}_{opp}": action(2)}  # fmt: skip
        block.steps = 3
        block.cover = f"{prefix}_{opp}"
    elif not spec.end_flaps:  # 只有两片长摇盖
        if w is not None or lt is not None or spec.flap in OVERLAPPING:  # 对面那片先合，on 那片在外
            block.folds = {f"{prefix}_{opp}": action(0, layer=1), f"{prefix}_{on}": action(1)}
            block.steps = 2
        else:
            block.folds = {f"{prefix}_back": action(0), f"{prefix}_front": action(0)}
            block.steps = 1
        block.cover = f"{prefix}_{on}"
    elif spec.flap in OVERLAPPING or spec.kind in GLUED_KINDS:  # 两片长摇盖互相叠：on 那面在最外
        block.folds = {f"{prefix}_side_a": action(0, layer=2), f"{prefix}_side_b": action(0, layer=3),
                       f"{prefix}_{opp}": action(1, layer=1), f"{prefix}_{on}": action(2)}  # fmt: skip
        block.steps = 3
        block.cover = f"{prefix}_{on}"
    else:  # 端边摇盖在里、长边摇盖在外（同 0201 的折法）
        block.folds = {f"{prefix}_side_a": action(0, layer=1), f"{prefix}_side_b": action(0, layer=1),
                       f"{prefix}_back": action(1), f"{prefix}_front": action(1)}  # fmt: skip
        block.steps = 2
        block.cover = f"{prefix}_{on}"
    if glue:  # 粘合区：½W 对口粘在端翼上，其余粘在里面那片长翼上；离翼远边让开半个开槽宽
        faces = ("side_a", "side_b") if spec.kind == "flaps" and spec.flap == "half" else (opp,)
        for f, x0, x1 in body.spans:
            if body.wall(f) in faces and f"{prefix}_{f}" in block.panels:
                D = depths[body.wall(f)]
                band = min(GLUE_BAND_MM, 0.5 * D)
                block.glue_areas.append(
                    _rect(x0 + s / 2, -D + s / 2, x1 - s / 2, -D + s / 2 + band)
                )
    if w is not None:  # 翼和带翼那片同一步折 90°，贴到端墙里面
        step = block.folds[f"{prefix}_{on}"].keys[-1][0]
        for wid in (f"{prefix}_wing_a", f"{prefix}_wing_b"):
            block.folds[wid] = action(step, layer=1)
    for (
        face,
        wall,
        _,
    ) in body.pieces:  # 接头那截的摇盖跟着那面墙的摇盖折，垫在它下面半层（别和下一层打架）
        if face != wall and f"{prefix}_{wall}" in block.folds:
            same = block.folds[f"{prefix}_{wall}"]
            block.folds[f"{prefix}_{face}"] = FoldAction(same.keys, same.layer + 0.5)
    return block


def _halves(block: Block, face: str, pid: str, a: float, b: float, g: float, D: float,
            role: PanelRole) -> tuple[float, float]:  # fmt: skip
    """端摇盖对开两半（中间留缝 g）：两块面、两条铰链压线；返回缝的两边 x。"""
    xm = 0.5 * (a + b)
    g0, g1 = xm - 0.5 * g, xm + 0.5 * g
    block.panels[pid] = (role, _rect(a, -D, g0, 0))
    block.panels[f"{pid}_r"] = (role, _rect(g1, -D, b, 0))
    block.creases += [crease(f"crease_{face}_{pid}", (a, 0), (g0, 0), [face, pid]),
                      crease(f"crease_{face}_{pid}_r", (g1, 0), (b, 0), [face, f"{pid}_r"])]  # fmt: skip
    block.sizes |= {pid: (g0 - a, D), f"{pid}_r": (b - g1, D)}
    block.poses |= {pid: ("flat", face), f"{pid}_r": ("flat", face)}
    return g0, g1


def divider_block(spec: SlotEnd, body: Body, prefix: str) -> Block:
    """隔板摇盖（0208）：四片摇盖都是 ½W，头上各接一段隔板（不给 drop 按 ½H − 纸厚，上下两头合起来一整层高）
    往箱子里折 90°。长边两片的隔板在宽度中间背靠背成纵隔板；端摇盖对开两半（中间留缝让纵隔板过），
    各自的隔板成横隔板。端摇盖先合，长边摇盖后合。按示意图推读，待核对。"""
    d, s, t = body.d, body.d.slot, body.t
    end = "top" if prefix == "top" else "btm"
    Dl, De = 0.5 * (d.ws - t), d.flap
    h = spec.drop if spec.drop is not None else 0.5 * d.Hi - t
    g = max(d.slot, 2 * t + 2)
    e = t + 1.0  # 隔板两头让开墙
    role_long = PanelRole.LID if prefix == "top" else PanelRole.BOTTOM
    block = Block(outline=[])
    pts: list[Vec] = [Vec(body.x0, 0)]
    later: list[Edge] = []
    for face, x0, x1 in body.spans:
        a, b = x0 + s / 2, x1 - s / 2
        pid = f"{prefix}_{face}"
        if face in LONG:
            drop = f"{pid}_drop"
            pts += [Vec(a, 0), Vec(a, -Dl), Vec(a + e, -Dl), Vec(a + e, -Dl - h), Vec(b - e, -Dl - h),
                    Vec(b - e, -Dl), Vec(b, -Dl), Vec(b, 0)]  # fmt: skip
            block.panels |= {pid: (role_long, _rect(a, -Dl, b, 0)),
                             drop: (PanelRole.DUST_FLAP, _rect(a + e, -Dl - h, b - e, -Dl))}  # fmt: skip
            block.creases += [crease(f"crease_{face}_{end}", (a, 0), (b, 0), [face, pid]),
                              crease(f"crease_{drop}", (a + e, -Dl), (b - e, -Dl), [pid, drop])]  # fmt: skip
            block.sizes |= {pid: (b - a, Dl), drop: (b - a - 2 * e, h)}
            block.poses |= {pid: ("flat", face), drop: ("drop_v", face)}
            block.folds |= {pid: action(1), drop: action(1)}
        else:
            g0, g1 = _halves(block, face, pid, a, b, g, De, PanelRole.DUST_FLAP)
            pts += [Vec(a, 0), Vec(a, -De), Vec(a + e, -De), Vec(a + e, -De - h), Vec(g0, -De - h), Vec(g0, 0),
                    Vec(g1, 0), Vec(g1, -De - h), Vec(b - e, -De - h), Vec(b - e, -De), Vec(b, -De), Vec(b, 0)]  # fmt: skip
            for half, lo, hi in ((pid, a + e, g0), (f"{pid}_r", g1, b - e)):
                drop = f"{half}_drop"
                block.panels[drop] = (PanelRole.DUST_FLAP, _rect(lo, -De - h, hi, -De))
                block.creases.append(crease(f"crease_{drop}", (lo, -De), (hi, -De), [half, drop]))
                block.sizes[drop] = (hi - lo, h)
                block.poses[drop] = ("drop_v", face)
                block.folds |= {half: action(0, layer=1), drop: action(0, layer=1)}
    pts.append(Vec(body.x_end, 0))
    block.outline = _chain(pts)
    block.creases += later
    block.steps = 2
    block.cover = f"{prefix}_front"
    return block


def cells_block(spec: SlotEnd, body: Body, prefix: str) -> Block:
    """分格底（0207）：端摇盖深 L/3，头上接一块横隔板（高 min(½W, H − 5)）往箱子里折 90°，中间开缝
    （从铰链开到隔板里纵隔板那么高）、隔板顶上中间一道短缝；长边两片 ½W 合到中间，头上两段：第一段立起来
    背靠背成纵隔板，第二段折回 180° 贴住第一段、在和横隔板交叉处切三角口让位。端摇盖先合，长边摇盖后合，
    分成 2 × 3 格。按示意图推读，待核对。"""
    d, s, t = body.d, body.d.slot, body.t
    end = "top" if prefix == "top" else "btm"
    Dl, de = 0.5 * (d.ws - t), d.Li / 3
    h1 = min(CELLS_LOW_RATIO * d.Wi, 0.5 * d.Hi)
    h2 = CELLS_BACK_RATIO * h1
    hp = min(0.5 * d.Wi, d.Hi - 5.0)
    nw, ht = CELLS_NOTCH_RATIO * d.Wi, CELLS_TOP_SLOT_RATIO * d.Wi
    g = 4 * t + 2.0  # 纵隔板 4 层
    role_long = PanelRole.LID if prefix == "top" else PanelRole.BOTTOM
    block = Block(outline=[])
    pts: list[Vec] = [Vec(body.x0, 0)]
    later: list[Edge] = []
    for face, x0, x1 in body.spans:
        a, b = x0 + s / 2, x1 - s / 2
        pid = f"{prefix}_{face}"
        if face in LONG:
            c, far = -(Dl + h1), -(Dl + h1 + h2)
            xl, xr = x0 + de, x1 - de
            ext, pieces = f"{pid}_wall", [f"{pid}_back_a", f"{pid}_back_c", f"{pid}_back_b"]
            pts += [Vec(a, 0), Vec(a, far), Vec(xl - nw, far), Vec(xl, c), Vec(xl, far), Vec(xr, far), Vec(xr, c),
                    Vec(xr + nw, far), Vec(b, far), Vec(b, 0)]  # fmt: skip
            shapes = [[(a, c), (a, far), (xl - nw, far), (xl, c)], [(xl, c), (xl, far), (xr, far), (xr, c)],
                      [(xr, c), (xr + nw, far), (b, far), (b, c)]]  # fmt: skip
            block.panels |= {
                pid: (role_long, _rect(a, -Dl, b, 0)),
                ext: (PanelRole.DUST_FLAP, _rect(a, c, b, -Dl)),
            }
            block.creases += [crease(f"crease_{face}_{end}", (a, 0), (b, 0), [face, pid]),
                              crease(f"crease_{ext}", (a, -Dl), (b, -Dl), [pid, ext])]  # fmt: skip
            block.sizes |= {pid: (b - a, Dl), ext: (b - a, h1)}
            block.poses |= {pid: ("flat", face), ext: ("drop_v", face)}
            layer = 0.5 if face == "back" else -0.5
            block.folds |= {pid: action(1), ext: action(1, layer=layer)}
            for q, shape in zip(pieces, shapes, strict=True):
                block.panels[q] = (PanelRole.DUST_FLAP, _trapezoid(shape))
                block.creases.append(crease(f"crease_{q}", shape[0], shape[-1], [ext, q]))
                block.sizes[q] = (max(x for x, _ in shape) - min(x for x, _ in shape), h2)
                block.poses[q] = ("drop_back", face, Dl)
                block.folds[q] = action(1, 180.0, -1)
        else:
            g0, g1 = _halves(block, face, pid, a, b, g, de, PanelRole.DUST_FLAP)
            part = f"{pid}_wall"
            top_y = -de - hp
            pts += [Vec(a, 0), Vec(a, top_y), Vec(g0, top_y), Vec(g0, top_y + ht), Vec(g1, top_y + ht), Vec(g1, top_y),
                    Vec(b, top_y), Vec(b, 0)]  # fmt: skip
            slot_end = -de - h1 - 2.0  # 缝开到纵隔板顶上 2 mm
            block.panels[part] = (PanelRole.DUST_FLAP, _trapezoid(
                [(a, -de), (a, top_y), (g0, top_y), (g0, top_y + ht), (g1, top_y + ht), (g1, top_y), (b, top_y),
                 (b, -de), (g1, -de), (g1, slot_end), (g0, slot_end), (g0, -de)]))  # fmt: skip
            block.creases.append(crease(f"crease_{part}", (a, -de), (g0, -de), [pid, part]))
            later.append(crease(f"crease_{part}_r", (g1, -de), (b, -de), [f"{pid}_r", part]))
            block.cuts.append(Edge(id=f"cut_slot_{pid}", type=EdgeType.CUT, panels=[],
                                   segments=Path.rect(g0, slot_end, g1 - g0, -slot_end).segments))  # fmt: skip
            block.sizes[part] = (b - a, hp)
            block.poses[part] = ("drop_v", face)
            block.folds |= {
                pid: action(0, layer=1),
                f"{pid}_r": action(0, layer=1),
                part: action(0, layer=1),
            }
    pts.append(Vec(body.x_end, 0))
    block.outline = _chain(pts)
    block.creases += later
    block.steps = 2
    block.cover = f"{prefix}_front"
    return block


def _inner_crease(eid: str, a: tuple[float, float], b: tuple[float, float], pid: str) -> Edge:
    """面里面的压线（不分开两块面，只在装的时候让摇盖拱起来）。"""
    return Edge(id=eid, type=EdgeType.CREASE, segments=_chain([Vec(*a), Vec(*b)]), panels=[pid])


def _slit(eid: str, a: tuple[float, float], b: tuple[float, float], w: float, pid: str) -> Edge:
    """a → b 一道宽 w 的斜缝（闭合细长条）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    pts = [
        (a[0] + ox, a[1] + oy),
        (b[0] + ox, b[1] + oy),
        (b[0] - ox, b[1] - oy),
        (a[0] - ox, a[1] - oy),
    ]
    return Edge(id=eid, type=EdgeType.CUT, segments=_trapezoid(pts).segments, panels=[pid])


def self_lock_block(spec: SlotEnd, body: Body, prefix: str) -> Block:
    """不用胶带的自锁底（0232 / 0233 / 0233.1）。按示意图推读：合好后整个底是平的，斜压线只在装的时候让摇盖
    拱起来，锁合过程不模拟（折法按平铺的终态核对）——待核对。
    - slot_lock（0232）：长边两片 ½W，各开两道斜缝；端摇盖 ½W，¼W 处一道压线、外段两边各收进一点，
      合在长边摇盖外面，外段的角从斜缝里顶进去
    - tongue_slot（0233）：背面那片中间伸出舌头、两角斜压线；正面那片中间 U 形缝、两角斜压线到缝腿上；
      舌头从 U 形缝的横边下面穿过去
    - tongue_notch（0233.1）：背面那片同上；正面那片远边中间一个缺口，舌头平嵌进缺口"""
    d, s, t = body.d, body.d.slot, body.t
    W = d.Wi
    end = "top" if prefix == "top" else "btm"
    on = wall_on(spec)
    tongue_wall = OPPOSITE[on]
    role_long = PanelRole.LID if prefix == "top" else PanelRole.BOTTOM
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)
    q = math.tan(math.radians(22.5))

    def go(*pts: Vec) -> None:
        nonlocal cur
        out.extend(_chain([cur, *pts]))
        cur = pts[-1]

    def follow(segs: list[Segment]) -> None:
        nonlocal cur
        go(segs[0].start)
        out.extend(segs)
        cur = segs[-1].end

    lock = spec.flap
    D = d.flap if lock == "slot_lock" else SELF_D * W
    De = d.flap if lock == "slot_lock" else min(SELF_D * W, 0.5 * d.wb - 2.0)
    r = min(5.0, 0.03 * W)
    for face, x0, x1 in body.spans:
        a, b = x0 + s / 2, x1 - s / 2
        w = b - a
        pid = f"{prefix}_{face}"
        if face not in LONG:
            if lock == "slot_lock":  # ¼W 处压线，外段两边各收进
                k, e = 0.25 * W, SLOT_LOCK_INSET * W
                go(Vec(a, 0), Vec(a, -k), Vec(a + e, -k), Vec(a + e, -De), Vec(b - e, -De), Vec(b - e, -k),
                   Vec(b, -k), Vec(b, 0))  # fmt: skip
                # 外段是一块能折的子面：端摇盖合在长边摇盖外面，外段再往里折一个小角，角从斜缝里顶进去
                # （原来整片平板、压线只画不折，角搁在长边摇盖外面进不了缝——S07，2026-11-06）
                tip = f"{pid}_tip"
                block.panels[pid] = (PanelRole.DUST_FLAP, _rect(a, -k, b, 0))
                block.panels[tip] = (PanelRole.DUST_FLAP, _rect(a + e, -De, b - e, -k))
                block.creases.append(
                    crease(f"crease_{pid}_lock", (a + e, -k), (b - e, -k), [pid, tip])
                )
                rise = min(1.0, SLOT_LOCK_RISE_T * t / max(De - k, 1e-6))
                block.folds[pid] = action(1)
                block.folds[tip] = action(1, math.degrees(math.asin(rise)))
                block.sizes[tip] = (w - 2 * e, De - k)
            else:
                go(Vec(a, 0), Vec(a, -De), Vec(b, -De), Vec(b, 0))
                block.panels[pid] = (PanelRole.DUST_FLAP, _rect(a, -De, b, 0))
                block.folds[pid] = action(0, layer=2)
            block.creases.append(crease(f"crease_{face}_{end}", (a, 0), (b, 0), [face, pid]))
            block.sizes[pid] = (w, 0.25 * W if lock == "slot_lock" else De)
            block.poses[pid] = ("flat", face)
            continue
        block.creases.append(crease(f"crease_{face}_{end}", (a, 0), (b, 0), [face, pid]))
        block.poses[pid] = ("flat", face)
        block.sizes[pid] = (w, D)
        if lock == "slot_lock":  # 长边两片：两头各一道斜缝
            go(Vec(a, 0), Vec(a, -D), Vec(b, -D), Vec(b, 0))
            block.panels[pid] = (role_long, _rect(a, -D, b, 0))
            (u1, v1), (u2, v2) = SLOT_LOCK_SLIT
            for side, sgn, x_end in (("a", 1.0, a), ("b", -1.0, b)):
                block.cuts.append(_slit(f"cut_{pid}_slit_{side}", (x_end + sgn * u1 * W, -v1 * W),
                                        (x_end + sgn * u2 * W, -v2 * W), t + 1.0, pid))  # fmt: skip
            block.folds[pid] = action(0, layer=1)
            continue
        dv0 = (
            SELF_DIAG_V["tongue_slot"] if lock == "tongue_slot" else SELF_DIAG_V["tongue_notch"]
        ) * W
        if face == tongue_wall:  # 舌头那片
            v1, v2 = SELF_SIDE_V * W, SELF_TONGUE_V * W
            ta, tb = a + 0.25 * w, b - 0.25 * w
            edge = _chain([Vec(a, 0), Vec(a, -v1), Vec(ta, -v2), Vec(ta, -D), Vec(tb, -D), Vec(tb, -v2), Vec(b, -v1),
                           Vec(b, 0)])  # fmt: skip
            follow(edge)
            block.panels[pid] = (role_long, _closed(edge))
            block.cuts += [_inner_crease(f"crease_{pid}_diag_a", (a, -dv0), (ta, -v2), pid),
                           _inner_crease(f"crease_{pid}_diag_b", (b, -dv0), (tb, -v2), pid)]  # fmt: skip
            block.folds[pid] = action(1, layer=1)
            continue
        la, lb = a + SELF_SLOT_U * w, b - SELF_SLOT_U * w  # 缝 / 缺口两边比舌头各宽 0.01·宽
        if lock == "tongue_slot":  # U 形缝：横边在 bar，两条腿往远边开到 leg
            bar, leg, mid = SELF_BAR_V * W, SELF_LEG_V * W, SELF_TONGUE_V * W
            go(Vec(a, 0), Vec(a, -D), Vec(b, -D), Vec(b, 0))
            block.panels[pid] = (role_long, _rect(a, -D, b, 0))
            u_cut = [*_chain([Vec(la, -leg), Vec(la, -bar - r)]), Segment(Vec(la, -bar - r), Vec(la + r, -bar), -q),
                     *_chain([Vec(la + r, -bar), Vec(lb - r, -bar)]), Segment(Vec(lb - r, -bar), Vec(lb, -bar - r), -q),
                     *_chain([Vec(lb, -bar - r), Vec(lb, -leg)])]  # fmt: skip
            block.cuts.append(
                Edge(id=f"cut_{pid}_slot", type=EdgeType.CUT, segments=u_cut, panels=[pid])
            )
            block.cuts += [_inner_crease(f"crease_{pid}_diag_a", (a, -dv0), (la, -mid), pid),
                           _inner_crease(f"crease_{pid}_diag_b", (b, -dv0), (lb, -mid), pid)]  # fmt: skip
        else:  # 远边中间的缺口
            top_v = D - SELF_NOTCH_DEPTH * W
            edge = [*_chain([Vec(a, 0), Vec(a, -D), Vec(la, -D), Vec(la, -top_v - r)]),
                    Segment(Vec(la, -top_v - r), Vec(la + r, -top_v), -q),
                    *_chain([Vec(la + r, -top_v), Vec(lb - r, -top_v)]),
                    Segment(Vec(lb - r, -top_v), Vec(lb, -top_v - r), -q),
                    *_chain([Vec(lb, -top_v - r), Vec(lb, -D), Vec(b, -D), Vec(b, 0)])]  # fmt: skip
            follow(edge)
            block.panels[pid] = (role_long, _closed(edge))
            corner = -top_v - r - 2.0
            block.cuts += [_inner_crease(f"crease_{pid}_diag_a", (a, -dv0), (la, corner), pid),
                           _inner_crease(f"crease_{pid}_diag_b", (b, -dv0), (lb, corner), pid)]  # fmt: skip
        block.folds[pid] = action(1)
    go(Vec(body.x_end, 0))
    block.outline = out
    block.steps = 2
    block.cover = f"{prefix}_{on}" if lock != "slot_lock" else f"{prefix}_side_a"
    return block


# ---- 插舌盖 -------------------------------------------------------------------------------


@dataclass(frozen=True)
class LidDims:
    ld: float  # 盖深
    tl: float  # 插舌长
    ti: float  # 插舌两边各收进
    tr: float  # 插舌圆角
    dd: float  # 防尘翼深


def lid_dims(p: SlottedAssembledParams, d: SlotDims, k_lid: float) -> LidDims:
    """插舌盖尺寸：插舌长、插舌圆角、插舌两边让位、防尘翼深都按彩盒插舌头 v2（RTE 已校准）的规则，
    卡纸、瓦楞一样（2026-11-07 合并第三阶段，§20：插舌盖留彩盒那份）；盖深 = W + k_lid·t。"""
    from pacxio.styles.components.carton import (
        DUST_DEPTH_LI_RATIO,
        TUCK_LEN_DEFAULT_HI_RATIO,
        TUCK_LEN_EXTRA_MM,
        TUCK_LEN_RANGE,
        TUCK_LEN_WI_RATIO,
        TUCK_RADIUS_CAP_MM,
    )

    tl = p.tuck_len if p.tuck_len is not None else min(TUCK_LEN_WI_RATIO * d.Wi + TUCK_LEN_EXTRA_MM,
                                                       TUCK_LEN_RANGE[1], TUCK_LEN_DEFAULT_HI_RATIO * d.Hi)  # fmt: skip
    dd = p.dust_depth if p.dust_depth is not None else min(DUST_DEPTH_LI_RATIO * d.Li, d.Wi)
    return LidDims(ld=d.Wi + k_lid * d.t, tl=tl, ti=0.5 * (d.wb - d.Li), tr=min(TUCK_RADIUS_CAP_MM, tl / 2),
                   dd=dd)  # fmt: skip


PAPERBOARD_T_MAX = (
    1.0  # 纸厚不到这个按卡纸的做法默认（插舌卡口、单层挂板、卡纸自动锁底比例）；瓦楞另一套【初值】
)
HANGER_SINGLE, HANGER_DOUBLE = (
    (35.0, 4.0),
    (50.0, 5.0),
)  # 挂板（高, 圆角）默认：单层同挂钩盒、双层同 0240


def paperboard(d: SlotDims) -> bool:
    """按纸厚选默认做法（不看材料名、不看走哪套拼装器，§20 第三阶段）。"""
    return d.t < PAPERBOARD_T_MAX


LOCK_BOTTOMS = frozenset({"snaplock", "autolock"})


def rte_glue(p: SlottedAssembledParams, d: SlotDims, body: Body) -> bool:
    """糊口两端用不用彩盒（RTE 插舌头 v2 校准）的形状：卡纸、接头在墙角、粘的糊口（不是自由插片）。"""
    return paperboard(d) and not body.split and not p.joint_free_tab


def rte_glue_end(p: SlottedAssembledParams, d: SlotDims) -> tuple[float, list[Segment]]:
    """糊口下端：外沿离下压线 gh（插舌底斜切 5；锁底竖直相切圆弧 20，盒子矮时压低、给直边留 10），
    和从外沿走到压线角的那一段（同 styles/rte.py）。"""
    gw, hb = d.jw, d.hb
    if p.bottom_end.kind in LOCK_BOTTOMS:
        gh = min(GLUE_ARC_H_MM, hb - GLUE_BEVEL_MM - GLUE_MIN_STRAIGHT_MM)
        r = (gw**2 + gh**2) / (2 * gw)
        sweep = math.atan2(-gh, gw - r) % (2 * math.pi) - math.pi
        return gh, [Segment(Vec(0, gh), Vec(gw, 0), math.tan(sweep / 4))]
    return GLUE_BEVEL_MM, _chain([Vec(0, GLUE_BEVEL_MM), Vec(gw, 0)])


def lid_lock(p: SlottedAssembledParams, d: SlotDims) -> bool:
    """插舌卡口（短压线 + 防尘翼卡口）：卡纸默认带、瓦楞默认不带。"""
    return p.friction_lock if p.friction_lock is not None else paperboard(d)


def hanger_layers(spec: SlotEnd, d: SlotDims) -> int:
    """挂板层数：给了照给的；不给卡纸单层、瓦楞双层。"""
    return spec.hanger_layers or (1 if paperboard(d) else 2)


def hanger_hr(p: SlottedAssembledParams, layers: int) -> tuple[float, float]:
    h, r = HANGER_SINGLE if layers == 1 else HANGER_DOUBLE
    return (p.hanger_h if p.hanger_h is not None else h,
            p.hanger_radius if p.hanger_radius is not None else r)  # fmt: skip


def _proxy_dims(d: SlotDims, lid: LidDims, wb: float) -> RteDims:
    return RteDims(t=d.t, Li=d.Li, Wi=d.Wi, Hi=d.Hi, wb=wb, ws=d.ws, hb=d.hb, ld=lid.ld, tl=lid.tl,
                   ti=lid.ti, tr=lid.tr, dd=lid.dd, off=0.0, gw=d.jw)  # fmt: skip


def header(p: SlottedAssembledParams, x0: float, x1: float, wall: str, layers: int = 2) -> Block:
    """挂板（底端画法：从 y = 0 往 −y），和彩盒共用 styles.hanger.hanger_piece：
    双层（默认）第一层高 v、对折线、第二层高 v（远端圆角），两层的挂孔按对折线对称；单层同彩盒挂钩盒。"""
    v, r = hanger_hr(p, layers)
    piece = hanger_piece(p, v, r, x0, x1, 0.0, wall, layers)  # 顶端画法，翻到底端
    flip = Affine(1, 0, 0, -1, 0, 0)
    block = Block(outline=_backwards([flip.apply_segment(q) for q in piece.outline]))
    block.panels = {k: (r, q.transformed(flip)) for k, (r, q) in piece.panels.items()}
    block.creases += [e.model_copy(update={"segments": [flip.apply_segment(q) for q in e.segments]})
                      for e in piece.creases]  # fmt: skip
    block.cuts += [e.model_copy(update={"segments": [flip.apply_segment(q) for q in e.segments]})
                   for e in piece.inner_edges]  # fmt: skip
    block.folds = {"hanger": FoldAction(())}
    block.poses = {"hanger": ("wall", wall)}
    block.sizes = {"hanger": (x1 - x0, v)}
    if layers == 2:
        block.folds["hanger_back"] = action(1, 180.0, layer=1)
        block.poses["hanger_back"] = ("wall_fold", wall)
        block.sizes["hanger_back"] = (x1 - x0, v)
    return block


def lid_block(
    spec: SlotEnd, p: SlottedAssembledParams, body: Body, prefix: str, k_lid: float
) -> Block:
    d, s = body.d, body.d.slot
    lid = lid_dims(p, d, k_lid)
    hang = wall_on(spec) if spec.kind == "hanger" else None
    wall = OPPOSITE[hang] if hang else wall_on(spec)
    head = header(p, *body.span(hang), hang, hanger_layers(spec, d)) if hang else None
    lx0, lx1 = body.span(wall)
    # 盖 + 插舌、防尘翼用彩盒那份已校准的组件（RTE 插舌头 v2、防尘翼卡口），开槽箱只给尺寸、
    # 防尘翼根部按开槽宽让开（2026-11-07 插舌盖合并，GEOMETRY_ENGINE.md §20）
    rd = _proxy_dims(d, lid, lx1 - lx0)
    lock = lid_lock(p, d)
    opp = OPPOSITE[wall]
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)
    spans = list(body.spans)
    for i, (face, x0, x1) in enumerate(spans):
        port = Port(f"{face}.{prefix}", face, Vec(x0, 0), Vec(x1, 0), Vec(0, -1))
        if face == wall:
            piece = TuckLid(rd, prefix, lock).build(port)
            out += _chain([cur, Vec(x0, 0)]) + piece.outline
            cur = Vec(x1, 0)
            role = PanelRole.LID if prefix == "top" else PanelRole.BOTTOM
            block.panels |= {
                k: (role if k == f"{prefix}_lid" else r, q) for k, (r, q) in piece.panels.items()
            }
            block.creases += piece.creases
            block.cuts += piece.inner_edges
        elif face == hang and head is not None:
            out += _chain([cur, Vec(x0, 0)]) + head.outline
            cur = Vec(x1, 0)
        elif face in ("side_a", "side_b"):
            nxt = spans[i + 1][0] if i + 1 < len(spans) else spans[0][0]
            pid = f"{prefix}_dust_{face[-1]}"
            e = max(DUST_BASE_INSET_MM, s / 2)
            piece = DustFlap(rd, pid, nxt == opp, lock, inset=e).build(port)
            out += _chain([cur, piece.outline[0].start]) + piece.outline
            cur = piece.outline[-1].end
            block.panels |= piece.panels
            block.creases += piece.creases
            block.poses[pid] = ("flat", face)
            block.sizes[pid] = (x1 - x0 - 2 * e, lid.dd)
    out += _chain([cur, Vec(body.x_end, 0)])
    block.outline = out
    if spec.tear_strip:  # 撕拉条：盖上离插舌压线 TEAR_OFF、宽 TEAR_W 的两道齿线，横过整片盖
        for k, y in enumerate((-lid.ld + TEAR_OFF_MM, -lid.ld + TEAR_OFF_MM + TEAR_W_MM)):
            block.cuts.append(Edge(id=f"perf_{prefix}_tear_{k + 1}", type=EdgeType.PERF, panels=[f"{prefix}_lid"],
                                   segments=[Segment(Vec(lx0, y), Vec(lx1, y))]))  # fmt: skip
    block.folds = {f"{prefix}_dust_a": action(0, layer=1), f"{prefix}_dust_b": action(0, layer=1),
                   f"{prefix}_lid": action(1),
                   f"{prefix}_tuck": FoldAction(((0, 45.0), (1, 90.0)), 1)}  # fmt: skip
    block.steps = 2
    block.poses |= {f"{prefix}_lid": ("flat", wall), f"{prefix}_tuck": ("drop_v", wall)}
    block.sizes[f"{prefix}_lid"] = (lx1 - lx0, lid.ld)
    block.cover = f"{prefix}_lid"
    if head is not None:
        block.panels |= head.panels
        block.creases += head.creases
        block.cuts += head.cuts
        block.folds |= head.folds
        block.poses |= head.poses
        block.sizes |= head.sizes
    return block


def side_tuck_block(
    spec: SlotEnd, p: SlottedAssembledParams, body: Body, prefix: str, k_lid: float
) -> Block:
    """侧插盖（ECMA 40）：共用的 engine/side_tuck.py，尺寸给开槽箱的盖深 / 插舌长 / 接舌宽（§20 第三阶段）。"""
    from pacxio.engine import side_tuck as stm

    st = side_tuck_of(spec, p, body, k_lid)
    (_, xb0, xb1), (_, _, xa1), (_, _, xf1), (_, _, xs1) = body.spans
    piece = st._local(BodyX(xb0, xb1, xa1, xf1, xs1), prefix).transformed(Affine(1, 0, 0, -1, 0, 0))
    piece.outline = _backwards(piece.outline)  # 底端画法（顶端由拼装器翻上去），面名按这一端
    return Block(outline=piece.outline, panels=piece.panels, creases=piece.creases, cuts=piece.inner_edges,
                 folds=stm.folds(st, prefix), steps=2, poses=dict(stm.poses(st, prefix)),
                 sizes=st.sizes(prefix), cover=f"{prefix}_lid")  # fmt: skip


def side_tuck_of(spec: SlotEnd, p: SlottedAssembledParams, body: Body, k_lid: float) -> Any:
    from pacxio.engine.side_tuck import side_tuck

    d = body.d
    return side_tuck(wall_on(spec), spec.wing, _proxy_dims(d, lid_dims(p, d, k_lid), d.wb))


# ---- 扣底 ---------------------------------------------------------------------------------


def snap_params(p: SlottedAssembledParams) -> SnaplockParams:
    keep = ("major_depth", "notch_w", "notch_d", "minor_depth", "tongue_w", "tongue_len")
    values = {k: getattr(p, k) for k in keep if getattr(p, k) is not None}
    return SnaplockParams(L=p.L, W=p.W, H=p.H, **values)


def snap_dims_proxy(p: SlottedAssembledParams, d: SlotDims, k_lid: float) -> RteDims:
    return _proxy_dims(d, lid_dims(p, d, k_lid), d.wb)


def _rename(block: Block, prefix: str) -> Block:
    if prefix == "btm":
        return block

    def r(s: str) -> str:
        return s.replace("btm_", "top_")

    block.panels = {r(k): v for k, v in block.panels.items()}
    block.creases = [
        e.model_copy(update={"id": r(e.id), "panels": [r(x) for x in e.panels]})
        for e in block.creases
    ]
    block.folds = {r(k): v for k, v in block.folds.items()}
    block.poses = {r(k): v for k, v in block.poses.items()}
    block.sizes = {r(k): v for k, v in block.sizes.items()}
    block.cover = r(block.cover) if block.cover else None
    return block


def snap_block(
    spec: SlotEnd, p: SlottedAssembledParams, body: Body, prefix: str, k_lid: float
) -> Block:
    d = body.d
    rd = snap_dims_proxy(p, d, k_lid)
    (_, xb0, xb1), (_, _, xa1), (_, _, xf1), (_, _, xs1) = body.spans
    wall = wall_on(spec)
    sp = snap_params(p)
    part = SnapBottom(tongue_on=wall).build(sp, rd, BodyX(xb0, xb1, xa1, xf1, xs1))
    notched = f"btm_{OPPOSITE[wall]}"
    block = Block(
        outline=part.outline,
        panels=part.polys,
        creases=part.creases,
        folds={
            notched: action(0),
            "btm_sa": action(1, layer=2),
            "btm_sb": action(1, layer=2),
            f"btm_{wall}": action(2, layer=1),
        },  # fmt: skip
        steps=3,
        poses={
            "btm_back": ("flat", "back"),
            "btm_front": ("flat", "front"),
            "btm_sa": ("flat", "side_a"),
            "btm_sb": ("flat", "side_b"),
        },  # fmt: skip
        sizes=SnaplockStyle()._bottom_sizes(sp, rd),
        cover=notched,
    )
    w, h = block.sizes["btm_sb"]
    block.sizes["btm_sb"] = (w - d.short, h)  # 最后一面短一层纸厚（接头让位）
    return _rename(block, prefix)


WIDE_OK_RULES = frozenset(
    {"half", "overlap", "full_long"}
)  # 宽大于长也能做的摇盖规则（端摇盖收到 ½L）
WIDE_OK_KINDS = frozenset(
    {"open", "lid_tuck", "hanger", "side_tuck", "snaplock", "gable", "reduced", "seal"}
)


def needs_l_ge_w(spec: SlotEnd) -> bool:
    """这一端的封口要不要 L ≥ W（L 是开口的长边）：FEFCO 专有的摇盖规则 / 翼 / 锁舌 / 拇指缺口、挂板类、
    展示 / 提手 / 斜口要；½W、重叠、长翼全宽摇盖和彩盒类封口不要（端摇盖收到 ½L）。自动锁底自己查。"""
    if spec.kind == "flaps":
        return (
            spec.flap not in WIDE_OK_RULES
            or spec.wing is not None
            or spec.lock
            or spec.thumb is not None
        )
    return spec.kind not in WIDE_OK_KINDS and spec.kind != "autolock"


def body_split(p: SlottedAssembledParams) -> bool:
    return (
        p.joint_offset is not None
        or p.joint_partition
        or p.joint_center_open
        or p.joint_full_overlap
    )


def flap_glue(spec: SlotEnd, material: Material) -> bool:
    """封口翼上画不画粘合区：给了 glue 照给的；不给——粘合封口（缩小翼、搭粘）画，摇盖卡纸画（彩盒封口上胶）、
    瓦楞不画（一般封箱胶带）。只是粘合区标注，几何不变。"""
    if spec.glue is not None:
        return spec.glue
    return spec.kind in GLUED_KINDS or material.type == "folding_carton"


def build_block(spec: SlotEnd, p: SlottedAssembledParams, body: Body, top: bool, material: Material,
                profile: Profile) -> Block:  # fmt: skip
    prefix = "top" if top else "btm"
    k = coefficients(profile, material, p.apply_compensation)
    if spec.kind == "open":
        return Block(outline=_chain([Vec(body.x0, 0), Vec(body.x_end, 0)]))
    if spec.kind in ("flaps", *GLUED_KINDS):
        return flaps_block(spec, body, prefix, k.k_flap, p.flap_gap, flap_glue(spec, material))
    if spec.kind in ("lid_tuck", "hanger"):
        return lid_block(spec, p, body, prefix, k.k_lid)
    if spec.kind == "side_tuck":
        return side_tuck_block(spec, p, body, prefix, k.k_lid)
    if spec.kind == "snaplock":
        return snap_block(spec, p, body, prefix, k.k_lid)
    if spec.kind == "gable":
        return gable_block(p, body, k.k_lid)
    if spec.kind == "handle":
        return handle_block(p, body)
    if spec.kind == "hang_tab":
        return hang_tab_block(spec, p, body)
    if spec.kind == "hang_mid":
        return hang_mid_block(spec, p, body)
    if spec.kind == "display":
        return display_block(spec, p, body)
    if spec.kind == "autolock":
        from pacxio.engine.slotted_lock import autolock_block

        return autolock_block(spec, p, body, prefix)
    if spec.kind == "slope":
        return slope_block(spec, body)
    raise ValueError(translate("Unknown closure {kind}", kind=spec.kind))


def gable_top(p: SlottedAssembledParams, body: Body, k_lid: float) -> Any:
    from pacxio.styles.gable import GableTop

    d = body.d
    hole_h = {} if p.handle_hole_h is None else {"hole_h": p.handle_hole_h}
    return GableTop(snap_dims_proxy(p, d, k_lid), p.roof_deg, p.lean_deg, p.handle_h, p.handle_hole_w,
                    **hole_h)  # fmt: skip


def gable_block(p: SlottedAssembledParams, body: Body, k_lid: float) -> Block:
    """提手山形顶（0217）：彩盒山形顶的组件（屋顶 + 手提板 + 带缝端翼），瓦楞没有防尘让位。"""
    from types import SimpleNamespace

    gable = gable_top(p, body, k_lid)
    (_, xb0, xb1), (_, _, xa1), (_, _, xf1), (_, _, xs1) = body.spans
    piece = gable.build(SimpleNamespace(xs=BodyX(xb0, xb1, xa1, xf1, xs1)))
    roof, lean = 90.0 - gable.roof_deg, gable.lean_deg
    sizes = gable.sizes()
    w, h = sizes["end_b"]
    sizes["end_b"] = (w - body.d.short, h)  # 最后一面短一层纸厚（接头让位）
    return Block(
        outline=piece.outline,
        panels=piece.panels,
        creases=piece.creases,
        cuts=piece.inner_edges,
        folds={
            "end_a": action(0, lean),
            "end_b": action(0, lean),
            "roof_back": action(1, roof),
            "roof_front": action(1, roof),
            "handle_back": action(1, -roof),
            "handle_front": action(1, -roof),
        },  # fmt: skip
        steps=2,
        sizes=sizes,
        native_top=True,
    )


TEAR_OFF_MM, TEAR_W_MM = 15.0, 6.0  # 插舌盖撕拉条：离盖远边 15、宽 6【初值】
SLOPE_V_RATIO = 0.35  # 斜口展示：正面墙剩 0.35·墙高（不给 depth 时）【初值】


def slope_v(spec: SlotEnd, d: SlotDims) -> float:
    return spec.depth if spec.depth is not None else SLOPE_V_RATIO * d.hb


def slope_block(spec: SlotEnd, body: Body) -> Block:
    """斜口展示（0801 / 0841 类开顶陈列箱）：开顶，正面墙矮到 v，两面端墙上沿从背面墙高斜到 v，背面整高。
    画在顶端：外轮廓从 (x_end, hb) 往左走回 (x0, hb)。"""
    d = body.d
    hb, v = d.hb, slope_v(spec, d)
    (_, xb0, xb1), (_, _, xa1), (_, _, xf1), (_, _, xs1) = body.spans
    out = _chain([Vec(xs1, hb), Vec(xf1, v), Vec(xa1, v), Vec(xb1, hb), Vec(xb0, hb)])
    return Block(outline=out, native_top=True,
                 wall_tops={"side_a": (hb, v), "front": (v, v), "side_b": (v, hb)})  # fmt: skip


def _to_top(block: Block, hb: float) -> Block:
    """底端画法 → 顶端：沿 y 镜像到盒身上沿，外轮廓反着走。"""
    m = Affine(1, 0, 0, -1, 0, hb)

    def edges(es: list[Edge]) -> list[Edge]:
        return [
            e.model_copy(update={"segments": [m.apply_segment(s) for s in e.segments]}) for e in es
        ]

    block.outline = _backwards([m.apply_segment(s) for s in block.outline])
    block.panels = {  # 镜像把面的走向翻了：反过来，保持和画在顶端的一样（逆时针）
        k: (role, Path(_backwards(path.transformed(m).segments), closed=True))
        for k, (role, path) in block.panels.items()
    }
    block.creases, block.cuts = edges(block.creases), edges(block.cuts)
    block.glue_areas = [
        Path(_backwards(g.transformed(m).segments), closed=True) for g in block.glue_areas
    ]
    return block


# ---- 提手平顶（0222） --------------------------------------------------------------------


@dataclass(frozen=True)
class HandleDims:
    band: float  # 长边那片平铺的深（两片在中间对上，各让半层纸厚）
    hh: float  # 提手板高
    straight: float  # 提手板两边先直上这么高，再斜收
    inset: float  # 提手板顶边每边收进
    hole_w: float
    hole_h: float
    end: float  # 端摇盖（对开两片）深：合到长度中间
    tab: float  # 端摇盖头上的插舌深
    tab_r: float
    gap: float  # 端摇盖两片之间、长边那片中间的缝宽


def handle_dims(p: SlottedAssembledParams, d: SlotDims) -> HandleDims:
    from pacxio.styles.gable import (
        HANDLE_EXTRA_MM,
        HANDLE_RANGE,
        HANDLE_W_RATIO,
        HOLE_H_MM,
        HOLE_L_RATIO,
        HOLE_W_RANGE,
    )

    hh = (p.handle_h if p.handle_h is not None
          else min(max(HANDLE_W_RATIO * d.Wi + HANDLE_EXTRA_MM, HANDLE_RANGE[0]), HANDLE_RANGE[1]))  # fmt: skip
    tab = min(HANDLE_TAB_W_RATIO * d.Wi, HANDLE_TAB_CAP_MM)
    return HandleDims(band=0.5 * (d.ws - d.t), hh=hh, straight=HANDLE_STRAIGHT * hh, inset=HANDLE_INSET * hh,
                      hole_w=min(max(HOLE_L_RATIO * d.Li, HOLE_W_RANGE[0]), HOLE_W_RANGE[1]), hole_h=HOLE_H_MM,
                      end=0.5 * (d.wb - d.t), tab=tab, tab_r=min(5.0, tab / 3), gap=max(d.slot, 2 * d.t + 2))  # fmt: skip


def _tab(ax: float, bx: float, y: float, depth: float, r: float) -> list[Segment]:
    """(ax, y) 往下一块圆角舌头到 (bx, y)（底端画法，不含根线）。"""
    q = math.tan(math.radians(22.5))
    yt = y - depth
    return [*_chain([Vec(ax, y), Vec(ax, yt + r)]), Segment(Vec(ax, yt + r), Vec(ax + r, yt), q),
            *_chain([Vec(ax + r, yt), Vec(bx - r, yt)]), Segment(Vec(bx - r, yt), Vec(bx, yt + r), q),
            *_chain([Vec(bx, yt + r), Vec(bx, y)])]  # fmt: skip


def handle_block(p: SlottedAssembledParams, body: Body) -> Block:
    """提手平顶（0222，只在顶；底端画法后镜像）：长边两片各平铺到中间，中间一道缝把它分成左右两半；
    两片的提手板在中间立起来背靠背（提手孔对齐）。端墙的摇盖对开两片、各合到长度中间，
    两片之间留缝让提手板穿过，头上的插舌往下插进长边那片中间的缝里。按示意图推读，待核对。"""
    d, s, t = body.d, body.d.slot, body.t
    h = handle_dims(p, d)
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)

    def go(*pts: Vec) -> None:
        nonlocal cur
        out.extend(_chain([cur, *pts]))
        cur = pts[-1]

    def follow(segs: list[Segment]) -> None:
        nonlocal cur
        go(segs[0].start)
        out.extend(segs)
        cur = segs[-1].end

    later: list[Edge] = []
    ti = t + 1.0
    for face, x0, x1 in body.spans:
        xm = 0.5 * (x0 + x1)
        if face in LONG:
            B, top_y = -h.band, -h.band - h.hh
            left, right, handle = f"top_{face}", f"top_{face}_r", f"handle_{face}"
            edge = _chain([Vec(x0, 0), Vec(x0, B), Vec(x0, B - h.straight), Vec(x0 + h.inset, top_y),
                           Vec(x1 - h.inset, top_y), Vec(x1, B - h.straight), Vec(x1, B), Vec(x1, 0)])  # fmt: skip
            follow(edge)
            g0, g1 = xm - 0.5 * h.gap, xm + 0.5 * h.gap
            block.panels[left] = (PanelRole.LID, _rect(x0, B, g0, 0))
            block.panels[right] = (PanelRole.LID, _rect(g1, B, x1, 0))
            block.panels[handle] = (PanelRole.LID, _trapezoid([(x0, B), (x0, B - h.straight), (x0 + h.inset, top_y),
                                                               (x1 - h.inset, top_y), (x1, B - h.straight), (x1, B)]))  # fmt: skip
            block.creases += [crease(f"crease_{face}_{left}", (x0, 0), (g0, 0), [face, left]),
                              crease(f"crease_{face}_{right}", (g1, 0), (x1, 0), [face, right]),
                              crease(f"crease_{left}_{handle}", (x0, B), (g0, B), [left, handle])]  # fmt: skip
            later.append(crease(f"crease_{right}_{handle}", (g1, B), (x1, B), [right, handle]))
            block.cuts.append(Edge(id=f"cut_slot_{face}", type=EdgeType.CUT,
                                   segments=Path.rect(g0, B, h.gap, h.band).segments, panels=[]))  # fmt: skip
            cy = B - 0.5 * h.hh
            hole = Path.rounded_rect(
                xm - 0.5 * h.hole_w, cy - 0.5 * h.hole_h, h.hole_w, h.hole_h, 0.5 * h.hole_h
            )
            block.cuts.append(Edge(id=f"cut_hole_{face}", type=EdgeType.CUT, segments=hole.segments,
                                   panels=[handle]))  # fmt: skip
            block.sizes |= {
                left: (g0 - x0, h.band),
                right: (x1 - g1, h.band),
                handle: (x1 - x0, h.hh),
            }
            block.poses |= {left: ("flat", face), right: ("flat", face), handle: ("rise_v", face)}
            layer = 0.5 if face == "back" else -0.5  # 两片提手板背靠背
            block.folds |= {left: action(0), right: action(0), handle: action(0, -90.0, layer)}
        else:
            E = -h.end
            for side, a, b in (
                ("", x0 + s / 2, xm - 0.5 * h.gap),
                ("_r", xm + 0.5 * h.gap, x1 - s / 2),
            ):
                pid, tab = f"top_{face}{side}", f"top_{face}{side}_tab"
                tab_edge = _tab(a + ti, b - ti, E, h.tab, h.tab_r)
                go(Vec(a, 0), Vec(a, E))
                follow(tab_edge)
                go(Vec(b, E), Vec(b, 0))
                block.panels[pid] = (PanelRole.DUST_FLAP, _rect(a, E, b, 0))
                block.panels[tab] = (PanelRole.TUCK, _closed(tab_edge))
                block.creases += [crease(f"crease_{face}_{pid}", (a, 0), (b, 0), [face, pid]),
                                  crease(f"crease_{pid}_tab", (a + ti, E), (b - ti, E), [pid, tab])]  # fmt: skip
                block.sizes |= {pid: (b - a, h.end), tab: (b - a - 2 * ti, h.tab)}
                block.poses |= {pid: ("flat", face), tab: ("drop_v", face)}
                block.folds |= {pid: action(1, layer=-1), tab: action(1)}
    go(Vec(body.x_end, 0))
    block.outline = out
    block.creases += later
    block.steps = 2
    return block


# ---- 端摇盖挂板 + 开缝盖（0241） ---------------------------------------------------------


def _tab_hole(
    p: SlottedAssembledParams, ax: float, bx: float, root: float, v: float, pid: str
) -> Edge | None:
    """挂板（底端画法：根在 y = root、往 −y 高 v）上的挂孔：在顶端画法里按挂板顶边定位，再翻下来。"""
    hole = hanging_hole(p, Path.rect(ax, 0, bx - ax, v), pid)
    if hole is None:
        return None
    flip = Affine(1, 0, 0, -1, 0, root)
    return hole.model_copy(update={"segments": [flip.apply_segment(q) for q in hole.segments]})


def hang_tab_block(spec: SlotEnd, p: SlottedAssembledParams, body: Body) -> Block:
    """端摇盖挂板 + 开缝盖（0241，只在顶；底端画法后镜像）：盖挂在 on 那面（默认正面）、深一个 W，
    长度中间开一道缝；对面那面不带摇盖。两端的摇盖各合到长度中间，头上的挂板（带挂孔）立起来
    背靠背、从盖上的缝穿出去。按示意图推读，待核对。"""
    d, s, t = body.d, body.d.slot, body.t
    lid_wall = wall_on(spec)
    ti, v = t + 1.0, hanger_hr(p, 2)[0]
    end_d = 0.5 * (d.wb - t)  # 两端摇盖在长度中间对上，各让半层纸厚
    gap = max(d.slot, 2 * t + 2)
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)

    def go(*pts: Vec) -> None:
        nonlocal cur
        out.extend(_chain([cur, *pts]))
        cur = pts[-1]

    def follow(segs: list[Segment]) -> None:
        nonlocal cur
        go(segs[0].start)
        out.extend(segs)
        cur = segs[-1].end

    for face, x0, x1 in body.spans:
        if face == lid_wall:  # 盖：远边两角圆角，长度中间一道缝从铰链开到快到远边
            D, r = d.ws, min(LID_CORNER_R_MM, 0.25 * d.ws)
            q = math.tan(math.radians(22.5))
            xm = 0.5 * (x0 + x1)
            g0, g1 = xm - 0.5 * gap, xm + 0.5 * gap
            reach = d.ws - (s / 2 + ti) + 2.0  # 缝开到挂板宽外 2 mm
            edge = [*_chain([Vec(x0, 0), Vec(x0, -D + r)]), Segment(Vec(x0, -D + r), Vec(x0 + r, -D), q),
                    *_chain([Vec(x0 + r, -D), Vec(x1 - r, -D)]), Segment(Vec(x1 - r, -D), Vec(x1, -D + r), q),
                    *_chain([Vec(x1, -D + r), Vec(x1, 0)])]  # fmt: skip
            follow(edge)
            notch = _chain(
                [Vec(x1, 0), Vec(g1, 0), Vec(g1, -reach), Vec(g0, -reach), Vec(g0, 0), Vec(x0, 0)]
            )
            pid = f"top_{face}"
            block.panels[pid] = (PanelRole.LID, Path([*edge, *notch], closed=True))
            block.creases += [crease(f"crease_{face}_{pid}", (x0, 0), (g0, 0), [face, pid]),
                              crease(f"crease_{face}_{pid}_r", (g1, 0), (x1, 0), [face, pid])]  # fmt: skip
            block.cuts.append(Edge(id=f"cut_slit_{pid}", type=EdgeType.CUT,
                                   segments=_chain([Vec(g0, 0), Vec(g0, -reach), Vec(g1, -reach), Vec(g1, 0)]),
                                   panels=[pid]))  # fmt: skip
            block.sizes[pid] = (x1 - x0, D)
            block.poses[pid] = ("flat", face)
            block.folds[pid] = action(1)
            block.cover = pid
        elif face in LONG:
            continue  # 对面那面不带摇盖
        else:  # 端摇盖 + 挂板
            a, b = x0 + s / 2, x1 - s / 2
            pid, tab = f"top_{face}", f"hanger_{face}"
            tab_edge = _tab(a + ti, b - ti, -end_d, v, min(hanger_hr(p, 2)[1], v / 3))
            go(Vec(a, 0), Vec(a, -end_d))
            follow(tab_edge)
            go(Vec(b, -end_d), Vec(b, 0))
            block.panels[pid] = (PanelRole.DUST_FLAP, _rect(a, -end_d, b, 0))
            block.panels[tab] = (PanelRole.BODY, _closed(tab_edge))
            block.creases += [crease(f"crease_{face}_{pid}", (a, 0), (b, 0), [face, pid]),
                              crease(f"crease_{pid}_{tab}", (a + ti, -end_d), (b - ti, -end_d), [pid, tab])]  # fmt: skip
            hole = _tab_hole(p, a + ti, b - ti, -end_d, v, tab)
            if hole is not None:
                block.cuts.append(hole)
            block.sizes |= {pid: (b - a, end_d), tab: (b - a - 2 * ti, v)}
            block.poses |= {pid: ("flat", face), tab: ("rise_v", face)}
            layer = 0.5 if face == "side_a" else -0.5  # 两块挂板背靠背
            block.folds |= {pid: action(0, layer=1), tab: action(0, -90.0, layer)}
    go(Vec(body.x_end, 0))
    block.outline = out
    block.steps = 2
    return block


# ---- 中间双层挂板 + 套盖（0242） ---------------------------------------------------------


def hang_mid_block(spec: SlotEnd, p: SlottedAssembledParams, body: Body) -> Block:
    """中间双层挂板 + 套盖（0242，只在顶；底端画法后镜像）。背面摇盖深 ½W + t，头上接两块挂板（对折、
    挂孔对齐）立起来，再接一大块平铺盖住前半边；正面一整片盖（深 W）中间一道缝套过挂板，头上小插舌
    插进背面摇盖根部的缝；端摇盖梯形、远边中间一个缺口，最先合。按示意图推读，待核对。"""
    d, s, t = body.d, body.d.slot, body.t
    back, lid_wall = OPPOSITE[wall_on(spec)], wall_on(spec)
    vh = hanger_hr(p, 2)[0]
    Db = 0.5 * d.ws + t  # 背面摇盖：挂板立在中间偏前一层纸厚
    bp = d.ws - Db - 1.0  # 平铺那块盖住前半边，离正面墙留 1
    hw = min(max(HANG_MID_W_RATIO * d.Li, HANG_MID_W_MIN_MM), d.wb - 20.0)
    tb = min(max(HANG_MID_TAB_RATIO * d.Li, 30.0), d.wb - 20.0)  # 盖头上插舌根宽
    th = min(HANG_MID_TAB_H_RATIO * d.Wi, 30.0)
    side_d = min(HANG_MID_SIDE_RATIO * d.Wi, 0.5 * d.wb - 2.0)
    q = math.tan(math.radians(22.5))
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)

    def go(*pts: Vec) -> None:
        nonlocal cur
        out.extend(_chain([cur, *pts]))
        cur = pts[-1]

    def follow(segs: list[Segment]) -> None:
        nonlocal cur
        go(segs[0].start)
        out.extend(segs)
        cur = segs[-1].end

    for face, x0, x1 in body.spans:
        xm = 0.5 * (x0 + x1)
        if face == back:
            hx0, hx1 = xm - 0.5 * hw, xm + 0.5 * hw
            ya, yb, yc, yd = -Db, -Db - vh, -Db - 2 * vh, -Db - 2 * vh - bp
            go(Vec(x0, 0), Vec(x0, ya), Vec(hx0, ya), Vec(hx0, yc), Vec(x0, yc), Vec(x0, yd), Vec(x1, yd),
               Vec(x1, yc), Vec(hx1, yc), Vec(hx1, ya), Vec(x1, ya), Vec(x1, 0))  # fmt: skip
            flap, ha, hb_, ext = f"top_{face}", "hanger_a", "hanger_b", f"top_{face}_ext"
            block.panels |= {flap: (PanelRole.LID, _rect(x0, ya, x1, 0)),
                             ha: (PanelRole.BODY, _rect(hx0, yb, hx1, ya)),
                             hb_: (PanelRole.BODY, _rect(hx0, yc, hx1, yb)),
                             ext: (PanelRole.LID, _rect(x0, yd, x1, yc))}  # fmt: skip
            block.creases += [crease(f"crease_{face}_{flap}", (x0, 0), (x1, 0), [face, flap]),
                              crease(f"crease_{flap}_{ha}", (hx0, ya), (hx1, ya), [flap, ha]),
                              crease(f"crease_{ha}_{hb_}", (hx0, yb), (hx1, yb), [ha, hb_]),
                              crease(f"crease_{hb_}_{ext}", (hx0, yc), (hx1, yc), [hb_, ext])]  # fmt: skip
            hole = hanging_hole(
                p, Path.rect(hx0, 0, hw, vh), ha
            )  # 挂孔：第一块按对折线往下 hole_top，第二块对称过去
            if hole is not None:
                for pid, m in ((ha, Affine(1, 0, 0, -1, 0, ya)), (hb_, Affine(1, 0, 0, 1, 0, yc))):
                    block.cuts.append(hole.model_copy(update={
                        "id": f"cut_hang_{pid}", "panels": [pid],
                        "segments": [m.apply_segment(g) for g in hole.segments]}))  # fmt: skip
            # 摇盖根部的缝：盖头上的插舌落在翼根往下约 2·t 处，缝以它为中线、上下各 t + 1（上沿离翼根 ≥ 0.5）。
            # 原来固定离翼根 2、高 2t + 2——B 楞正好（和现在一样），卡纸插舌落在缝外（S07，§20）【初值】
            slot_w = tb + 4.0
            y_hi = min(-t + 1.0, -0.5)
            y_lo = -3 * t - 1.0
            block.cuts.append(Edge(id=f"cut_slot_{flap}", type=EdgeType.CUT, panels=[flap],
                                   segments=Path.rect(xm - 0.5 * slot_w, y_lo, slot_w, y_hi - y_lo).segments))  # fmt: skip
            block.sizes |= {flap: (x1 - x0, Db), ha: (hw, vh), hb_: (hw, vh), ext: (x1 - x0, bp)}
            block.poses |= {flap: ("flat", face), ha: ("rise_v", face), hb_: ("rise_back", face, Db),
                            ext: ("rise_lie", face, Db)}  # fmt: skip
            block.folds |= {flap: action(1, layer=1), ha: action(1, -90.0, 0.5),
                            hb_: action(1, -180.0, -0.5), ext: action(1, -90.0, -1)}  # fmt: skip
        elif face == lid_wall:
            D = d.ws
            edge = [*_chain([Vec(x0, 0), Vec(x0, -D), Vec(xm - 0.5 * tb, -D)]),
                    *_tab_trapezoid(xm - 0.5 * tb, xm + 0.5 * tb, -D, th, HANG_MID_TAB_TAPER * tb, q),
                    *_chain([Vec(xm + 0.5 * tb, -D), Vec(x1, -D), Vec(x1, 0)])]  # fmt: skip
            follow(edge)
            lid, tuck = f"top_{face}", f"top_{face}_tuck"
            block.panels[lid] = (PanelRole.LID, _rect(x0, -D, x1, 0))
            block.panels[tuck] = (PanelRole.TUCK, _closed(edge[2:-2]))
            block.creases += [crease(f"crease_{face}_{lid}", (x0, 0), (x1, 0), [face, lid]),
                              crease(f"crease_{lid}_tuck", (xm - 0.5 * tb, -D), (xm + 0.5 * tb, -D), [lid, tuck])]  # fmt: skip
            vs = d.ws - Db  # 盖上的缝正对挂板
            sl, sh = hw + 4.0, 2 * t + 2.0
            block.cuts.append(Edge(id=f"cut_slit_{lid}", type=EdgeType.CUT, panels=[lid],
                                   segments=Path.rounded_rect(xm - 0.5 * sl, -vs - 0.5 * sh, sl, sh, 0.5 * sh).segments))  # fmt: skip
            block.sizes |= {lid: (x1 - x0, D), tuck: (tb, th)}
            block.poses |= {lid: ("flat", face), tuck: ("drop_v", face)}
            block.folds |= {lid: action(2), tuck: FoldAction(((2, 45.0), (3, 90.0)), 1)}
            block.cover = lid
        else:  # 端摇盖：梯形、远边中间一个缺口
            a, b = x0 + s / 2, x1 - s / 2
            k = HANG_MID_SIDE_TAPER * side_d
            pid = f"top_{face}"
            edge = [*_chain([Vec(a, 0), Vec(a + k, -side_d)]), *_far_edge(a + k, b - k, -side_d, THUMB_R_MM),
                    *_chain([Vec(b - k, -side_d), Vec(b, 0)])]  # fmt: skip
            follow(edge)
            block.panels[pid] = (PanelRole.DUST_FLAP, _closed(edge))
            block.creases.append(crease(f"crease_{face}_{pid}", (a, 0), (b, 0), [face, pid]))
            block.sizes[pid] = (b - a, side_d)
            block.poses[pid] = ("flat", face)
            block.folds[pid] = action(0, layer=2)
    go(Vec(body.x_end, 0))
    block.outline = out
    block.steps = 4
    return block


def _tab_trapezoid(
    ax: float, bx: float, y: float, depth: float, inset: float, q: float
) -> list[Segment]:
    """(ax, y) 往下一块梯形插舌（两边各收进 inset、顶上两角小圆角）到 (bx, y)。"""
    r = min(5.0, depth / 3)
    yt = y - depth
    return [*_chain([Vec(ax, y), Vec(ax + inset, yt + r)]), Segment(Vec(ax + inset, yt + r), Vec(ax + inset + r, yt), q),
            *_chain([Vec(ax + inset + r, yt), Vec(bx - inset - r, yt)]),
            Segment(Vec(bx - inset - r, yt), Vec(bx - inset, yt + r), q), *_chain([Vec(bx - inset, yt + r), Vec(bx, y)])]  # fmt: skip


# ---- 展示板 + 盖（0219） ------------------------------------------------------------------


def _notch_edge(x0: float, x1: float, y: float, r: float) -> list[Segment]:
    """压线 (x0, y) → (x1, y)（从左往右）中间的半圆指扣，往 +y 那边凹（底端画法里往铰链那边）。"""
    c = 0.5 * (x0 + x1)
    return [*_chain([Vec(x0, y), Vec(c - r, y)]), Segment(Vec(c - r, y), Vec(c + r, y), -1.0),
            *_chain([Vec(c + r, y), Vec(x1, y)])]  # fmt: skip


def display_block(spec: SlotEnd, p: SlottedAssembledParams, body: Body) -> Block:
    """展示板 + 盖（0219，只在顶；底端画法后镜像）：背面摇盖深 W、整片立起来当展示板（中间一道挂槽，
    顶上一块小舌往后折 180°）；正面一整片盖（深 W）合上，远边一条窄边立起来贴在展示板里面；
    两道压线中间各一个半圆指扣；端摇盖最先合。正面铰链处的小锁舌（斜压线 + 两道让位切口）和盖中间
    两个小圆孔读不准，先没画——待核对。"""
    d, s = body.d, body.d.slot
    lid_wall = wall_on(spec)
    board = OPPOSITE[lid_wall]
    Hh = d.ws
    tw = DISPLAY_TAB_W_RATIO * d.wb
    ht = min(DISPLAY_TAB_H_RATIO * d.Wi, 30.0)
    bh = min(DISPLAY_BAND_RATIO * d.Wi, 40.0)
    side_d = min(DISPLAY_SIDE_RATIO * d.Wi, 0.5 * d.wb - 2.0)
    rn = DISPLAY_NOTCH_R_MM
    block = Block(outline=[])
    out: list[Segment] = []
    cur = Vec(body.x0, 0)

    def go(*pts: Vec) -> None:
        nonlocal cur
        out.extend(_chain([cur, *pts]))
        cur = pts[-1]

    def follow(segs: list[Segment]) -> None:
        nonlocal cur
        go(segs[0].start)
        out.extend(segs)
        cur = segs[-1].end

    for face, x0, x1 in body.spans:
        xm = 0.5 * (x0 + x1)
        if face == board:
            pid, tab = f"top_{face}", f"top_{face}_tab"
            t0, t1 = xm - 0.5 * tw, xm + 0.5 * tw
            tab_edge = _tab(t0, t1, -Hh, ht, min(5.0, ht / 3))
            go(Vec(x0, 0), Vec(x0, -Hh))
            follow(tab_edge)
            go(Vec(x1, -Hh), Vec(x1, 0))
            notch = _notch_edge(t0, t1, -Hh, rn)
            block.panels[pid] = (PanelRole.BODY, Path([*_chain([Vec(x0, 0), Vec(x0, -Hh), Vec(t0, -Hh)]), *notch,
                                                       *_chain([Vec(t1, -Hh), Vec(x1, -Hh), Vec(x1, 0)]),
                                                       Segment(Vec(x1, 0), Vec(x0, 0))], closed=True))  # fmt: skip
            block.panels[tab] = (PanelRole.TUCK, Path([*tab_edge, *_backwards(notch)], closed=True))
            block.creases += [crease(f"crease_{face}_{pid}", (x0, 0), (x1, 0), [face, pid]),
                              crease(f"crease_{pid}_tab", (t0, -Hh), (xm - rn, -Hh), [pid, tab]),
                              crease(f"crease_{pid}_tab_r", (xm + rn, -Hh), (t1, -Hh), [pid, tab])]  # fmt: skip
            block.cuts.append(
                Edge(
                    id=f"cut_notch_{pid}", type=EdgeType.CUT, segments=[notch[1]], panels=[pid, tab]
                )
            )
            sl = min(max(DISPLAY_SLOT_RATIO * d.Li, 40.0), d.wb - 30.0)
            block.cuts.append(Edge(id=f"cut_slot_{pid}", type=EdgeType.CUT, panels=[pid],
                                   segments=Path.rounded_rect(xm - 0.5 * sl, -0.5 * Hh - 6.0, sl, 12.0, 6.0).segments))  # fmt: skip
            block.sizes |= {pid: (x1 - x0, Hh), tab: (tw, ht + rn)}  # 指扣半圆归小舌
            block.poses |= {pid: ("wall", face), tab: ("wall_fold", face)}
            block.folds |= {pid: FoldAction(()), tab: action(0, -180.0, -1)}
        elif face == lid_wall:
            D = d.ws
            pid, band = f"top_{face}", f"top_{face}_band"
            band_edge = _tab(x0, x1, -D, bh, min(5.0, bh / 3))
            go(Vec(x0, 0))
            follow(band_edge)
            notch = _notch_edge(x0, x1, -D, rn)
            block.panels[pid] = (PanelRole.LID, Path([*_chain([Vec(x0, 0), Vec(x0, -D)]), *notch,
                                                      *_chain([Vec(x1, -D), Vec(x1, 0)]),
                                                      Segment(Vec(x1, 0), Vec(x0, 0))], closed=True))  # fmt: skip
            block.panels[band] = (
                PanelRole.LID,
                Path([*band_edge, *_backwards(notch)], closed=True),
            )
            block.creases += [crease(f"crease_{face}_{pid}", (x0, 0), (x1, 0), [face, pid]),
                              crease(f"crease_{pid}_band", (x0, -D), (xm - rn, -D), [pid, band]),
                              crease(f"crease_{pid}_band_r", (xm + rn, -D), (x1, -D), [pid, band])]  # fmt: skip
            block.cuts.append(
                Edge(
                    id=f"cut_notch_{pid}",
                    type=EdgeType.CUT,
                    segments=[notch[1]],
                    panels=[pid, band],
                )
            )
            block.sizes |= {pid: (x1 - x0, D), band: (x1 - x0, bh + rn)}
            block.poses |= {pid: ("flat", face), band: ("rise_v", face)}
            block.folds |= {pid: action(1), band: action(1, -90.0, -1)}  # 贴在展示板里面
            block.cover = pid
        else:
            a, b = x0 + s / 2, x1 - s / 2
            pid = f"top_{face}"
            go(Vec(a, 0), Vec(a, -side_d), Vec(b, -side_d), Vec(b, 0))
            block.panels[pid] = (PanelRole.DUST_FLAP, _rect(a, -side_d, b, 0))
            block.creases.append(crease(f"crease_{face}_{pid}", (a, 0), (b, 0), [face, pid]))
            block.sizes[pid] = (b - a, side_d)
            block.poses[pid] = ("flat", face)
            block.folds[pid] = action(0, layer=1)
    go(Vec(body.x_end, 0))
    block.outline = out
    block.steps = 2
    return block


# ---- 拼装 ----------------------------------------------------------------------------------


MIN_DIM_FLOOR_MM, MIN_DIM_T, MIN_DIM_CAP_MM = (
    20.0,
    15.0,
    50.0,
)  # 长宽高下限 = max(20, min(50, 15·t))【初值】
JOINT_W_ANY_RANGE = (10.0, 50.0)  # 开槽箱拼装器的接舌宽范围【初值】
JOINT_W_SIDE_RATIO = 0.45  # 不给接舌宽时：材料默认，但不超过 0.45·侧面宽


class _AnyBoard(SlottedStyle):
    """旧 0201 入口的尺寸规则，但不只认瓦楞（几何引擎的开槽箱拼装器卡纸也能用，GEOMETRY_ENGINE.md §20）。"""

    material_gate: ClassVar[bool] = False
    require_l_ge_w: ClassVar[bool] = (
        False  # 要不要 L ≥ W 看封口（needs_l_ge_w），彩盒常有宽大于长的
    )

    def joint_range(self, material: Material) -> tuple[float, float]:
        """接舌宽范围 10–50（不给时按材料默认、小盒自动收窄，见 calibrated._slotted_params）【初值】。"""
        return JOINT_W_ANY_RANGE

    def min_dim(self, material: Material) -> float:
        """长宽高下限按纸厚：max(20, min(50, 15·t))（卡纸 20、E 楞约 22、B 楞 45、厚瓦楞仍是原来的 50）【初值】。
        原来瓦楞一律 50，E 楞小盒被冤枉拒掉（能力边界测试 2026-11-06）；只放宽、不比原来严。"""
        return max(MIN_DIM_FLOOR_MM, min(MIN_DIM_CAP_MM, MIN_DIM_T * material.caliper_mm))


class SlottedAssembled:
    """几何引擎内部用：瓦楞开槽箱筒身 + 两端积木。不注册成盒型（由 structure 配方调用）。"""

    id: ClassVar[str] = "structure"
    name_zh: ClassVar[str] = "结构配方（几何引擎）"
    version: ClassVar[str] = "0.1.0"
    refs: ClassVar[dict[str, str]] = {
        "design": "几何引擎：瓦楞开槽箱筒身 + 封口积木（）"
    }

    def param_schema(self) -> type[BaseModel]:
        return SlottedAssembledParams

    def body(self, p: SlottedAssembledParams, material: Material, profile: Profile) -> Body:
        d = SlotDims(p, material, profile)
        if (
            p.joint_offset is not None
            or p.joint_partition
            or p.joint_center_open
            or p.joint_full_overlap
        ):
            return self._split_body(p, d, material.caliper_mm)
        xb0 = d.jw
        xs = [xb0, xb0 + d.wb, xb0 + d.wb + d.ws, xb0 + 2 * d.wb + d.ws]
        ends = [*xs[1:], xs[3] + d.wsb]
        return Body(d, tuple(zip(FACES, xs, ends, strict=True)), material.caliper_mm)

    def _split_body(self, p: SlottedAssembledParams, d: SlotDims, t: float) -> Body:
        """接头在墙中间（0230 / 0231）：那面墙分两截——第一片是 v + 搭接（挨着下一面墙），
        最后一片是墙长 − v − 一层纸厚；两截搭接 joint_w 粘 / 钉在一起。
        带隔板（0228）：第一片外面再接一块隔板（横过箱子的内宽），隔板头上是糊口。"""
        o, wall = (0.0 if p.joint_center_open else d.jw), p.joint_wall
        full = {"back": d.wb, "side_a": d.ws, "front": d.wb, "side_b": d.ws}
        if p.joint_full_overlap:
            i = FACES.index(wall)
            between = FACES[i + 1 :] + FACES[:i]
            widths = [
                (f"{wall}_joint", full[wall]),
                *((f, full[f]) for f in between),
                (wall, full[wall]),
            ]
            spans, x = [], 0.0
            for face, width in widths:
                spans.append((face, x, x + width))
                x += width
            return Body(d, tuple(spans), t, ((f"{wall}_joint", wall, 0.0), (wall, wall, 0.0)))
        v = p.joint_offset if p.joint_offset is not None else 0.5 * (full[wall] - o)
        i = FACES.index(wall)
        between = FACES[i + 1 :] + FACES[:i]  # 从接头往后绕一圈的三面整墙
        short = 0.0 if p.joint_center_open else d.short
        widths = [(f"{wall}_joint", v + o), *((f, full[f]) for f in between),
                  (wall, full[wall] - v - short)]  # fmt: skip
        if p.joint_partition:  # 隔板横过箱子：长边墙上的隔板跨端墙那么宽，减一层纸厚
            across = full["side_a"] if wall in LONG else full["back"]
            widths.insert(0, ("partition", across - d.t))
        spans, x = [], (d.jw if p.joint_partition else 0.0)
        for f, w in widths:
            spans.append((f, x, x + w))
            x += w
        pieces = ((f"{wall}_joint", wall, full[wall] - v - o), (wall, wall, 0.0))
        return Body(d, tuple(spans), t, pieces)

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: SlottedAssembledParams = params
        profile = profile or load_profile("default")
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        base = SlottedParams(**{k: getattr(p, k) for k in SlottedParams.model_fields})
        issues += _AnyBoard().validate(base, material, profile)  # 卡纸、瓦楞都能用（§20）
        wide = [spec for spec in (p.top_end, p.bottom_end) if needs_l_ge_w(spec)]
        if p.W > p.L and (wide or p.window or p.tear or body_split(p)):
            bad(
                translate(
                    "This closure or window requires L to be the long edge of the opening. W={width:g} > L={length:g}.",
                    width=p.W,
                    length=p.L,
                ),
                translate("Swap L and W"),
            )
        if p.joint_center_open and (p.joint_offset is not None or p.joint_partition):
            bad(
                translate("A centred open joint cannot also specify a joint offset or partition"),
                translate("Remove joint_offset or joint_partition"),
            )
        if p.joint_full_overlap and (
            p.joint_offset is not None
            or p.joint_partition
            or p.joint_center_open
            or p.joint_free_tab
        ):
            bad(
                translate(
                    "A full-wall overlap cannot be combined with a joint offset, partition, centred split, or free tuck tab"
                ),
                translate("Keep only the full-wall overlap"),
            )
        if p.joint_free_tab and (
            p.joint_center_open or p.joint_offset is not None or p.joint_partition
        ):
            bad(
                translate("A free tuck tab can only attach to the edge of the tube body"),
                translate("Remove the centred opening, joint offset, or partition"),
            )
        for end, spec in (("top", p.top_end), ("bottom", p.bottom_end)):
            if spec.kind not in KINDS:
                bad(
                    translate(
                        "The {end} closure {kind} is not a supported slotted container component",
                        end=translate(end),
                        kind=spec.kind,
                    ),
                    translate("Choose one of {kinds}", kinds=sorted(KINDS)),
                )
            elif end == "bottom" and spec.kind in TOP_ONLY:
                bad(
                    translate(
                        "{kind} can only be used at the top",
                        kind=KIND_ZH[spec.kind] if lang() == "zh" else spec.kind,
                    ),
                    translate("Choose a different bottom closure"),
                )
        if (
            p.joint_offset is not None
            or p.joint_partition
            or p.joint_center_open
            or p.joint_full_overlap
        ):
            for end, spec in (("top", p.top_end), ("bottom", p.bottom_end)):
                plain = spec.kind == "open" or (spec.kind == "flaps" and not has_options(spec)
                                                and spec.flap in OVERLAPPING | {"half", "center"})  # fmt: skip
                if not plain:
                    bad(translate("With a mid-wall joint, the {end} end must be open or have plain flaps without rims, side wings, or locking tabs", end=translate(end)),
                        translate("Change the closure or remove joint_offset to move the joint back to the corner"))  # fmt: skip
        if issues:
            return issues
        body = self.body(p, material, profile)
        d = body.d
        k = coefficients(profile, material, p.apply_compensation)
        if body.split and not p.joint_full_overlap:
            wall_len = d.wb if p.joint_wall == "back" else d.ws
            o = 0.0 if p.joint_center_open else d.jw
            short = 0.0 if p.joint_center_open else d.short
            v = wall_len - body.pieces[0][2] - o
            if (
                v < d.slot + 10
                or wall_len - v - short < d.slot + 10
                or v + o > wall_len - d.slot - 10
            ):
                bad(translate("The joint offset {offset:g} does not fit (wall length {wall:.1f}, overlap {overlap:g})", offset=v, wall=wall_len, overlap=o),
                    translate("Set joint_offset to {minimum:.0f}–{maximum:.0f}", minimum=d.slot + 10, maximum=wall_len - o - d.slot - 10))  # fmt: skip
        for end, spec in (("top", p.top_end), ("bottom", p.bottom_end)):
            if spec.kind == "flaps":
                depths = flap_depths(spec, d, k.k_flap, p.flap_gap)
                if min(depths.values()) <= 0:
                    bad(
                        translate("The {end} flap depth is not positive", end=translate(end)),
                        translate("Increase the dimensions or change the flap length rule"),
                    )
                if (
                    spec.flap == "overlap"
                    and spec.overlap is not None
                    and not 0 < spec.overlap < d.Wi
                ):
                    bad(
                        translate(
                            "The {end} flap overlap {overlap:g} must be positive and smaller than the inside width",
                            end=translate(end),
                            overlap=spec.overlap,
                        ),
                        translate("Reduce overlap. Use flap=full for full overlap."),
                    )
                if has_options(spec):
                    for msg, fix in flap_option_problems(spec, d, depths):
                        bad(translate("{end}: {message}", end=translate(end), message=msg), fix)
                if spec.flap == "divider" and spec.drop is not None and spec.drop > 0.5 * d.Hi:
                    bad(
                        translate(
                            "The {end} divider depth {depth:g} exceeds half the box height, so the top and bottom would collide",
                            end=translate(end),
                            depth=spec.drop,
                        ),
                        "drop ≤ ½H",
                    )
                if spec.flap == "cells":
                    h1 = min(CELLS_LOW_RATIO * d.Wi, 0.5 * d.Hi)
                    if min(0.5 * d.Wi, d.Hi - 5.0) < h1 + 2 + CELLS_TOP_SLOT_RATIO * d.Wi + 10:
                        bad(
                            translate(
                                "The cross divider is too low for the {end} compartment base (box height {height:g})",
                                end=translate(end),
                                height=d.Hi,
                            ),
                            translate("Increase the height H"),
                        )
                    if d.Li / 3 < (CELLS_NOTCH_RATIO + 0.05) * d.Wi:
                        bad(
                            translate(
                                "L is too short for the {end} compartment base", end=translate(end)
                            ),
                            translate("Increase L"),
                        )
                if spec.flap == "center_overlap" and depths["back"] > d.Wi + d.t:
                    bad(translate("At the {end}, long-side flap depth ½L = {depth:.0f} exceeds inside width {width:g}. The flap will protrude when closed (requires W ≥ ½L).", end=translate(end), depth=depths['back'], width=d.Wi),
                        translate("Increase W, reduce L, or use flap=center for long-side flaps of depth ½W"))  # fmt: skip
                if spec.flap == "gusset" and d.wb < d.ws:
                    bad(
                        translate(
                            "The {end} triangular flaps require L ≥ W to keep the middle triangles from colliding",
                            end=translate(end),
                        ),
                        translate("Change the flap rule or swap length and width"),
                    )
                if spec.flap.startswith("rim"):
                    rim = depths["back"] if spec.flap in ("rim", "rim_long") else depths["side_a"]
                    if rim > 0.5 * d.Wi - 2:
                        bad(
                            translate(
                                "The {end} rim width {width:g} is too large. The opposite rims would collide.",
                                end=translate(end),
                                width=rim,
                            ),
                            translate("Keep rim width v ≤ 0.5·W − 2"),
                        )
                    if spec.flap != "rim" and rim_drop(spec, d, rim) > d.Hi - 5:
                        bad(
                            translate(
                                "The {end} inner panel is taller than the box", end=translate(end)
                            ),
                            translate("Reduce drop"),
                        )
            if spec.kind == "side_tuck":
                if body.split:
                    bad(
                        translate("A side-tuck lid requires the joint at a corner"),
                        translate("Remove joint_offset, the partition, or the centred split"),
                    )
                else:
                    for msg, fix in side_tuck_of(spec, p, body, k.k_lid).problems():
                        bad(translate("{end}: {message}", end=translate(end), message=msg), fix)
            if spec.kind == "reduced":
                r = spec.depth if spec.depth is not None else REDUCED_W_RATIO * d.Wi
                if not 2 * d.slot < r <= d.Wi - 2:
                    bad(
                        translate(
                            "The {end} reduced flap depth {depth:.1f} is outside twice the slot width to inside width minus 2",
                            end=translate(end),
                            depth=r,
                        ),
                        translate("Adjust depth"),
                    )
            if spec.kind == "seal":
                ov = spec.overlap if spec.overlap is not None else SEAL_OVERLAP_MM
                if not 2 <= ov <= d.Wi - 2:
                    bad(
                        translate(
                            "The {end} glued-flap overlap {overlap:g} is outside 2 to inside width minus 2",
                            end=translate(end),
                            overlap=ov,
                        ),
                        translate("Adjust overlap"),
                    )
            if spec.kind == "hanger":
                try:
                    header(p, *body.span(wall_on(spec)), wall_on(spec), hanger_layers(spec, d))
                except ValueError as e:
                    bad(str(e), translate("Adjust hanger_h or the hang hole dimensions"))
            if spec.kind in ("hang_tab", "hang_mid", "display"):
                try:
                    build_block(spec, p, body, True, material, profile)
                except ValueError as e:
                    bad(
                        str(e),
                        translate("Adjust hanger_h or the hang hole dimensions, or increase W"),
                    )
            if spec.kind in ("lid_tuck", "hanger"):
                lid = lid_dims(p, d, k.k_lid)
                if lid.tl >= d.Hi - 5:
                    bad(
                        translate(
                            "Tuck length {length:.1f} is too long (box height {height:g})",
                            length=lid.tl,
                            height=d.Hi,
                        ),
                        translate("Reduce tuck_len"),
                    )
                if lid.dd > 0.5 * d.Li - 2 or d.wsb - d.slot < 2 * DUST_BACK_TIP_MM:
                    bad(
                        translate("Dust flap depth {depth:.1f} does not fit", depth=lid.dd),
                        translate("Reduce dust_depth"),
                    )
                if d.wb - 2 * lid.ti <= 2 * lid.tr:
                    bad(
                        translate("The tuck tab is too narrow for the radius"),
                        translate("Increase L or reduce tuck_len"),
                    )
            if spec.kind == "snaplock":
                rd = snap_dims_proxy(p, d, k.k_lid)
                issues += SnaplockStyle()._validate_bottom(snap_params(p), rd)
            if spec.kind == "gable" and end == "top":
                for msg, fix in gable_top(p, body, k.k_lid).problems():
                    bad(msg, fix)
            if spec.kind == "slope":
                if wall_on(spec) != "front":
                    bad(
                        translate("The sloped display opening requires a lowered front wall"),
                        translate("Omit on or set on=2 (front)"),
                    )
                elif not 20 <= slope_v(spec, d) <= d.hb - 20:
                    bad(
                        translate(
                            "The sloped front wall height {height:.1f} is outside 20 to wall height minus 20",
                            height=slope_v(spec, d),
                        ),
                        translate("Adjust depth"),
                    )
                if body.split:
                    bad(
                        translate("The sloped display opening requires the joint at a corner"),
                        translate("Remove joint_offset, the partition, or the centred split"),
                    )
                if p.tear:
                    bad(
                        translate(
                            "The front wall is already lowered for the sloped display opening and cannot have a tear-off perforation"
                        ),
                        translate("Remove body.tear or use a different top"),
                    )
            if spec.kind == "autolock":
                from pacxio.engine.slotted_lock import problems as lock_problems

                for msg, fix in lock_problems(spec, body):
                    bad(translate("{end}: {message}", end=translate(end), message=msg), fix)
        for end, spec in (("top", p.top_end), ("bottom", p.bottom_end)):
            if spec.tear_strip and spec.kind != "lid_tuck":
                bad(
                    translate("The {end} tear strip requires a tuck lid", end=translate(end)),
                    translate("Remove tear_strip"),
                )
            elif spec.tear_strip and lid_dims(p, d, k.k_lid).ld < TEAR_OFF_MM + TEAR_W_MM + 10:
                bad(
                    translate(
                        "The {end} lid is too shallow for the tear strip", end=translate(end)
                    ),
                    translate("Increase W"),
                )
        if (p.window or p.tear) and body.split:
            bad(
                translate("The window or tear-off display requires the joint at a corner"),
                translate("Remove joint_offset, the partition, or the centred split"),
            )
        if (
            d.jw > 0
            and rte_glue(p, d, body)
            and d.hb - GLUE_BEVEL_MM - rte_glue_end(p, d)[0] < GLUE_MIN_STRAIGHT_MM
        ):
            bad(
                translate(
                    "The carton is too short for the bevels or arcs at the ends of the glue flap"
                ),
                translate("Increase H"),
            )
        if p.window and p.window_shape == "tap" and d.hb < 80:
            bad(
                translate("A pour opening requires a wall at least 80 mm high"),
                translate("Increase the height H"),
            )
        return issues

    def generate(
        self, params: Any, material: Material, profile: Profile
    ) -> tuple[Dieline, dict[str, Block]]:
        p: SlottedAssembledParams = params
        body = self.body(p, material, profile)
        d = body.d
        hb, xb0, x_end = d.hb, body.x0, body.x_end
        bottom = build_block(p.bottom_end, p, body, False, material, profile)
        top = build_block(p.top_end, p, body, True, material, profile)
        top = top if top.native_top else _to_top(top, hb)
        glue_out = d.ji + d.jw * d.jt
        # 接头在墙中间：搭接是第一片自己的一截，没有糊口；带隔板的糊口在隔板头上
        glue = d.jw > 0 and (not body.split or p.joint_partition)
        first = body.spans[0][0]
        out: list[Segment] = []
        card_glue = glue and rte_glue(
            p, d, body
        )  # 卡纸：糊口两端按彩盒插舌头 v2 的斜切 / 圆弧（§20）
        if card_glue:
            gh, glue_end = rte_glue_end(p, d)
            out += glue_end
        elif glue:
            out += _chain([Vec(0, glue_out), Vec(xb0, d.ji), Vec(xb0, 0)])
        out += bottom.outline + _chain([Vec(x_end, 0), Vec(x_end, hb)]) + top.outline
        if card_glue:
            out += _chain([Vec(xb0, hb), Vec(0, hb - GLUE_BEVEL_MM), Vec(0, gh)])
        elif glue:
            out += _chain(
                [Vec(xb0, hb), Vec(xb0, hb - d.ji), Vec(0, hb - glue_out), Vec(0, glue_out)]
            )
        else:
            out += _chain([Vec(xb0, hb), Vec(xb0, 0)])
        polys: dict[str, tuple[PanelRole, Path]] = {}
        creases: list[Edge] = []
        if card_glue:
            polys["glue"] = (PanelRole.GLUE_FLAP, Path(
                _chain([Vec(xb0, 0), Vec(xb0, hb), Vec(0, hb - GLUE_BEVEL_MM), Vec(0, gh)]) + glue_end,
                closed=True))  # fmt: skip
            creases.append(crease(f"crease_glue_{first}", (xb0, 0), (xb0, hb), [first, "glue"]))
        elif glue:
            polys["glue"] = (PanelRole.TUCK if p.joint_free_tab else PanelRole.GLUE_FLAP, _trapezoid(
                [(xb0, d.ji), (0, glue_out), (0, hb - glue_out), (xb0, hb - d.ji)]))  # fmt: skip
            creases.append(
                crease(f"crease_glue_{first}", (xb0, d.ji), (xb0, hb - d.ji), [first, "glue"])
            )
        tops = top.wall_tops
        for i, (face, x0, x1) in enumerate(body.spans):
            hl, hr = tops.get(face, (hb, hb))
            polys[face] = (PanelRole.BODY, _trapezoid([(x0, 0), (x1, 0), (x1, hr), (x0, hl)])
                           if face in tops else _rect(x0, 0, x1, hb))  # fmt: skip
            if i > 0:
                prev = body.spans[i - 1][0]
                h = min(hl, tops.get(prev, (hb, hb))[1])
                creases.append(crease(f"crease_{prev}_{face}", (x0, 0), (x0, h), [prev, face]))
        polys |= top.panels | bottom.panels
        creases += top.creases + bottom.creases
        wall_cuts = wall_openings(p, body)
        ring = [f for f, _, _ in body.spans]
        tz, bz = KIND_ZH[p.top_end.kind], KIND_ZH[p.bottom_end.kind]
        meta: dict[str, Any] = {
            "name_zh": f"{self.name_zh}（长方筒身：{tz}顶 + {bz}底）",
            "refs": self.refs,
            "version": self.version,
            "accuracy_level": "L0",
            "compensation_applied": p.apply_compensation,
            "dims": {
                "Li": d.Li,
                "Wi": d.Wi,
                "Hi": d.Hi,
                "panel_back_front": d.wb,
                "panel_side": d.ws,
                "panel_side_last": d.wsb,
                "body_height": hb,
                "flap": d.flap,
                "slot_w": d.slot,
                "joint_w": 0.0 if p.joint_center_open or p.joint_full_overlap else d.jw,
                **(
                    {"joint_offset": p.joint_offset, "joint_wall": p.joint_wall}
                    if body.split
                    else {}
                ),
            },  # fmt: skip
            "flute_dir": "y",
            "joint": "none"
            if p.joint_center_open or p.joint_full_overlap
            else "tuck"
            if p.joint_free_tab
            else p.joint,
            "assumptions": [
                "盒身、接头、开槽、最后一面让位、½W / 重叠 / 全叠摇盖同 0201 / 0202 / 0203（校准过的公式）",
                "½L 摇盖、窄边和内板、瓦楞插舌盖和防尘翼、瓦楞扣底（按彩盒扣底的比例）都是【初值】，要折样确认",
            ],
        }
        if p.joint_center_open:
            meta["joint_note"] = "Opening in the middle of a wall, no overlap or side glue; how it is closed must be confirmed (provisional)"
            meta["assumptions"] = [
                "墙正中分成两片等宽面，无搭接；端部摇盖及纸厚补偿均为【初值】，须折样确认"
            ]
        if p.joint_full_overlap:
            meta["joint_note"] = "First and last full walls overlap, no glue; how it is held closed must be confirmed (provisional)"
            meta["assumptions"] = [
                "首尾两整墙及两端摇盖按 FEFCO 0409 示意图推读；纸厚分层、间隙和固定方式均为【初值】，须折样确认"
            ]
        if p.joint_free_tab:
            meta["joint_note"] = "Free tab at the tube side edge, no glue; insertion and closing need a folded sample (provisional)"
            meta["assumptions"].append("侧边接片作为自由插片，不是糊口；松紧和锁合均为【初值】")
        dl = assemble(style_id=self.id, params=p, material=material, profile=profile,
                      outline=Path(out, closed=True), polys=polys, creases=creases,
                      inner_cuts=top.cuts + bottom.cuts + wall_cuts,
                      ups={"top_back": "down", "top_lid": "down"},
                      provisional={pid for pid, (role, _) in polys.items() if role != PanelRole.BODY},
                      hinges={b: f"crease_{a}_{b}" for a, b in zip(ring, ring[1:], strict=False)},
                      meta=meta)  # fmt: skip
        glue_areas = top.glue_areas + bottom.glue_areas
        if glue_areas:  # assemble 把原点移到包围盒左下角：粘合区跟着移
            minx, miny, _, _ = Path(out, closed=True).bbox()
            shift = Affine.translate(-minx, -miny)
            dl.glue_areas = [*dl.glue_areas, *(g.transformed(shift) for g in glue_areas)]
        if body.split:  # 立体目标要知道每截在墙上哪儿
            joint = (
                ("full_overlap" if p.joint_wall == "side_b" else "full_overlap_long")
                if p.joint_full_overlap
                else (
                    ("center_open" if p.joint_wall == "back" else "center_open_short")
                    if p.joint_center_open
                    else "partition"
                    if p.joint_partition
                    else {"back": "long", "side_b": "short"}[p.joint_wall]
                )
            )
            dl.style_meta["tube"] = {"L": d.wb, "W": d.ws, "ring": ring, "joint": joint,
                                     "pieces": {f: [w, u] for f, w, u in body.pieces}}  # fmt: skip
            if p.joint_partition:  # 隔板挂在接头那截的起点，横过箱子
                dl.style_meta["tube"]["partition"] = {"panel": "partition", "wall": p.joint_wall,
                                                      "u": body.pieces[0][2]}  # fmt: skip
        return dl, {"top": top, "bottom": bottom}

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        p: SlottedAssembledParams = params
        body = self.body(p, material, profile)
        d = body.d
        sizes = {f: (x1 - x0, d.hb) for f, x0, x1 in body.spans}
        if p.top_end.kind == "slope":
            v = slope_v(p.top_end, d)
            sizes["front"] = (sizes["front"][0], v)
        for top, spec in ((True, p.top_end), (False, p.bottom_end)):
            sizes |= build_block(spec, p, body, top, material, profile).sizes
        return sizes


# 【初值】开窗 / 出液口 / 撕开展示（按示意图推读）
WINDOW_X_RATIO, WINDOW_Y = 0.2, (0.3, 0.8)  # 八角窗：两边各让 0.2·墙宽，高从 0.3·墙高到 0.8·墙高
WINDOW_LOW_Y = (0.06, 0.3)  # 取货口（low）：墙下部 0.06–0.3·墙高（0825 出货口）
TAP_R_RATIO, TAP_R_MAX_MM, TAP_Y_RATIO = (
    0.15,
    25.0,
    0.7,
)  # 出液口半径 min(0.15·墙高, 25)，圆心 0.7·墙高
TEAR_V_RATIO = 0.3  # 撕开后端墙剩 0.3·墙高


def wall_openings(p: SlottedAssembledParams, body: Body) -> list[Edge]:
    """长墙开窗（八角窗刀线 / 出液口齿线圆 + 齿线提片）、撕开展示的齿线。"""
    from pacxio.geometry import arc_segment

    out: list[Edge] = []
    hb = body.d.hb
    if p.window:
        x0, x1 = body.span(p.window)
        w = x1 - x0
        # 窗按所在那面墙的高算：斜口顶的正面墙是矮的（只有 slope_v 高）
        hw = slope_v(p.top_end, body.d) if p.top_end.kind == "slope" and p.window == "front" else hb
        if p.window_shape in ("octagon", "low"):
            a, b = x0 + WINDOW_X_RATIO * w, x1 - WINDOW_X_RATIO * w
            y0, y1 = WINDOW_Y if p.window_shape == "octagon" else WINDOW_LOW_Y
            y0, y1 = y0 * hw, y1 * hw
            c = min(b - a, y1 - y0) / 4
            pts = [
                (a + c, y0),
                (b - c, y0),
                (b, y0 + c),
                (b, y1 - c),
                (b - c, y1),
                (a + c, y1),
                (a, y1 - c),
                (a, y0 + c),
            ]
            out.append(_cut(f"cut_window_{p.window}", [*pts, pts[0]], [p.window]))
        else:
            r = min(TAP_R_RATIO * hw, TAP_R_MAX_MM)
            cx, cy = 0.5 * (x0 + x1), TAP_Y_RATIO * hw
            q = math.pi / 2
            out.append(Edge(id=f"perf_tap_{p.window}", type=EdgeType.PERF, panels=[p.window],
                            segments=[arc_segment(Vec(cx, cy), r, k * q, (k + 1) * q) for k in range(4)]))  # fmt: skip
            m = 1.6 * r  # 提片：圆外一圈齿线方框
            box = [(cx - m, cy - m), (cx + m, cy - m), (cx + m, cy + m), (cx - m, cy + m)]
            out.append(
                _cut(f"perf_tap_flap_{p.window}", [*box, box[0]], [p.window]).model_copy(
                    update={"type": EdgeType.PERF}
                )
            )
    if p.tear:
        v = TEAR_V_RATIO * hb
        (bx0, bx1), (ax0, ax1), (fx0, fx1) = (
            body.span("back"),
            body.span("side_a"),
            body.span("front"),
        )
        lines: list[tuple[str, tuple[float, float], tuple[float, float], str]] = [
            ("perf_tear_back", (bx0, hb), (bx1, v), "back"),
            ("perf_tear_side_a", (ax0, v), (ax1, v), "side_a"),
            ("perf_tear_front", (fx0, v), (fx1, hb), "front"),
        ]
        for eid, q0, q1, face in lines:
            out.append(
                Edge(
                    id=eid,
                    type=EdgeType.PERF,
                    panels=[face],
                    segments=[Segment(Vec(*q0), Vec(*q1))],
                )
            )
    return out


def fold_plan(
    bottom: Block, top: Block, glue: bool, ring: list[str] | None = None
) -> dict[str, Any]:
    """接头 → 盒身一圈 → 底 → 顶。ring：接头在墙中间时盒身从左往右的面（最后一截搭在第一截外面）。"""
    moves: dict[str, FoldAction] = {f: action(2) for f in (ring or FACES)[1:]}
    if glue:
        moves["glue"] = action(1, layer=1)
    if ring:
        moves[ring[-1]] = action(2, layer=-1)
    first = 3
    open_step = 2
    for block in (bottom, top):
        for pid, act in block.folds.items():
            moves[pid] = FoldAction(tuple((first + s, a) for s, a in act.keys), act.layer)
        first += block.steps
        if block is bottom:
            open_step = first - 1
    return {
        "root": "front",
        "moves": {
            pid: {"keys": [list(k) for k in m.keys], "layer": m.layer} for pid, m in moves.items()
        },
        "steps": first - 1,
        "open_step": open_step,
        "pose_step": 2,
        "ground_step": 2,
    }


__all__ = ["KINDS", "SlotEnd", "SlottedAssembled", "SlottedAssembledParams", "fold_plan"]
