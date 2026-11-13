"""结构配方（几何引擎第 0 步）：agent 写配方，不写坐标；引擎按配方生成刀版和折法。

长方筒身（卡纸、瓦楞同一套，engine/slotted.py；原彩盒拼装器 engine/carton.py 2026-11-07 并入，§20）的两端用封口积木：open / flaps / reduced / seal / lid_tuck /
hanger / side_tuck / snaplock（底）/ autolock（底）/ gable（顶），on 只能是 0（背面）或 2（正面）。
ECMA A 类编号可以直接换成配方（engine/ecma.py）。

引擎原生（任意直棱柱，第 0 步）：直棱柱盒身（长方形 / 正多边形）+ 侧边糊口 + 两端各一种封口：
- open      不封（开口）
- flaps     摇盖：长方形盒身是开槽箱那种四片摇盖（half 对口、full 全重叠、overlap 部分重叠）；
            多边形盒身是每面一片、合到中心的扇形翼
- lid_tuck  盖 + 插舌 + 防尘翼（盖挂在 on 指定的那面墙上，插舌插进对面那面墙）
- lid_glue  盖 + 粘翼（其余每面墙一片小翼，粘在盖里面）

数值没给的都按初值规则推，见 net.py；全部【初值】，要折样确认。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pacxio.messages import t

EndKind = Literal[
    "open",
    "flaps",
    "lid_tuck",
    "lid_glue",  # 引擎原生（任意多边形盒身）
    "seal",
    "snaplock",
    "autolock",
    "hanger",
    "gable",  # 校准过的长方形彩盒封口
    "handle",  # 瓦楞开槽箱的提手平顶（0222）
    "hang_tab",  # 瓦楞开槽箱的端摇盖挂板 + 开缝盖（0241）
    "hang_mid",  # 瓦楞开槽箱的中间双层挂板 + 套盖（0242）
    "display",  # 瓦楞开槽箱的展示板 + 盖（0219）
    "slope",  # 瓦楞开槽箱的斜口展示顶（正面墙矮、端墙斜，0801 / 0841）
    "reduced",
    "side_tuck",  # 长方彩盒的新积木（第 1 步）：缩小翼（ECMA 11）、侧插（ECMA 40）
    "mailer",
    "hinged_lid",  # 托盘盒身的盖：飞机盒（瓦楞）、连体翻盖（纸盒）
]


class StripItem(BaseModel):
    """长条包裹式盒（corners = strip）上的一块面：第一块是底板，之后按 side 往右（后墙、顶、前墙、底……）
    或往左（前墙、顶、后墙……）绕，每过一条压线转 90°。"""

    model_config = ConfigDict(extra="forbid")
    side: Literal["right", "left"] = "right"
    size: Literal["full", "half", "tuck"] = Field(
        default="full", description="整面 / 一半 / 插舌深 min(0.5·H, 40)"
    )
    ends: Literal["wall", "wall+rim", "wall+over", "half", "wing", "dust"] | None = Field(
        default=None,
        description="两头端翼：整面端墙（可带窄边 / 盖过顶面的一段）、半深、夹层翼、梯形防尘翼",
    )
    inset: bool = Field(default=False, description="两头让开端墙（内衬、插舌）")


SYMBOL = re.compile(r"(\d+(?:\.\d+)?)?\*?([LWHvj])(?:/(\d+))?")
V_RATIO, V_CAP_MM = 0.5, 30.0  # 内衬窄边 v = min(0.5·H, 30)【初值】
J_MM = 35.0  # 片材上的糊口条 j（三片式盒身，06）= 35，同瓦楞接舌默认【初值】


def symbol_value(token: float | str, L: float, W: float, H: float) -> float:
    """片材条宽：数原样；L / W / H / v 的式子按盒子内尺寸算（1.414H = 1.414 × H，W/2 = W ÷ 2）。"""
    if not isinstance(token, str):
        return float(token)
    m = SYMBOL.fullmatch(token)
    if m is None:
        raise ValueError(t("Unrecognised sheet strip width {width!r}", width=token))
    k, name, n = m.groups()
    base = {"L": L, "W": W, "H": H, "v": min(V_RATIO * H, V_CAP_MM), "j": J_MM}[name]
    return float(k or 1.0) * base / float(n or 1)


class SheetSlots(BaseModel):
    """隔板插槽：从一条边开进去 depth（默认一半高），宽 t + 1（wide：2t + 2）。"""

    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1, le=12, description="几道槽")
    edge: Literal["top", "bottom"] = Field(
        default="top", description="从哪条边开：top 折线方向的远端（立起来是上沿）/ bottom 近端"
    )
    at: Literal["even", "ends"] = Field(
        default="even",
        description="even 均分（格子）/ ends 两头各一道、离端头 min(0.1·长, 30)（围框，0931）",
    )
    wide: bool = Field(default=False, description="宽槽 2t + 2（0934）")
    depth: float = Field(
        default=0.5, gt=0, lt=1, description="槽深占这条折线方向长的比例（头卡插脚 0.15）"
    )


class SheetFlaps(BaseModel):
    """片材每条在折线方向两头的翼（三片式盒身 06 的端翼）：折 90° 往筒里，压在端板上。"""

    model_config = ConfigDict(extra="forbid")
    panels: list[int] | None = Field(default=None, description="哪几条带翼（从 0 数），不给 = 全部")
    depth: float | str = Field(default="v", description="翼深：数，或 v / H/4 这样的式子")


class SheetHoles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    panel: int = Field(default=0, ge=0, description="开在第几条（从 0 数）")
    count: int = Field(default=2, ge=1, le=12, description="沿折线方向均分几个")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: Literal["tube", "tray", "sheet"] = Field(
        default="tube",
        description="tube 筒身（墙围一圈 + 两端封口）/ tray 托盘（底板四周墙往上折）/ "
        "sheet 片材（一张板 + 一组平行折线，FEFCO 01）",
    )
    corners: Literal[
        "glue", "lock", "flap", "gusset", "open", "ear", "wrap", "strip", "bevel", "polygon"
    ] = Field(
        default="glue",
        description="托盘墙角：glue 糊角 / lock 扣舌（彩盒托盘，校准过）；flap 角翼（贴隔壁墙，粘 / 钉）、"
        "gusset 连角（对角折成三角贴墙）、open 不连（胶带 / 钉，十字形）、ear 双层端墙 + 耳朵（0422 型，校准过）、"
        "wrap 包裹式（端墙挂后墙、不连底，0469–0472）、strip 长条包裹（见 strip）、bevel 切角 / 八角"
        "（见 bevel_*）、polygon 正多边形托盘 / 盖帽（见 poly_*）——几何引擎托盘",
    )
    corner_on: Literal["end", "long"] = Field(
        default="end",
        description="托盘：角翼挂在哪对墙上 / 连角贴在哪面墙上（end 端墙 / long 长边墙）",
    )
    corner_side: Literal["in", "out"] = Field(
        default="in", description="托盘：角翼 / 连角贴在墙里面还是外面"
    )
    corner_depth: float | None = Field(
        default=None, gt=0, description="托盘：角翼深，默认 = 墙高（放不下时取墙长一半）"
    )
    corner_diagonal: bool = Field(
        default=False, description="托盘：角翼对角一道压扁用的压线（立起来不折，0448）"
    )
    wall_diagonal: bool = Field(
        default=False, description="托盘：长边墙两头压扁用的斜压线（立起来不折，0450 / 0451）"
    )
    rim: float | None = Field(
        default=None, gt=0, description="托盘：墙顶往里折平的窄边宽 v（平铺成框，0430）"
    )
    rim_on: Literal["all", "long", "end", "front"] = Field(
        default="all", description="托盘：哪几面墙带窄边"
    )
    lugs: bool = Field(default=True, description="托盘 ear：内墙插脚插进底板插孔")
    ear_shape: Literal["calibrated", "full"] = Field(
        default="calibrated", description="双层墙托盘：校准耳片 / 整幅矩形角翼（0454 / 0455）"
    )
    double_walls: Literal["pair", "all"] = Field(
        default="pair", description="托盘 ear：一对双层墙 / 四面双层墙（0425 类）"
    )
    inner_ears: bool = Field(default=False, description="四面双层托盘：新增内墙两端也带夹角耳片")
    inner_foot: bool = Field(
        default=False,
        description="托盘 ear：内墙下沿一条折边平压在底板上，代替插脚和底板插孔（0428）",
    )
    stack: Literal["none", "center", "pair", "corners"] = Field(
        default="none",
        description="托盘：端墙顶的堆叠凸舌（中间一个 / 两个 / 靠墙角两个），底板对应位置开孔（0435、0436、0484）",
    )
    lug_count: int | None = Field(
        default=None, ge=1, le=4, description="托盘 ear：内墙插脚数（默认 2，0435 是 1）"
    )
    stack_on: Literal["end", "long"] = Field(
        default="end", description="托盘：堆叠凸舌做在端墙还是长边墙上（0481 在长边墙）"
    )
    end_height: float | None = Field(
        default=None, gt=0, description="托盘：端墙比长边墙矮时的端墙高 v2（0481）"
    )
    end_double: bool = Field(
        default=False, description="托盘：端墙卷边成双层，内墙插脚插进底板（单层墙托盘，0481）"
    )
    front_double: bool = Field(
        default=False, description="托盘：只有前墙卷边双层、内墙三个插脚插底板（0426 / 0475）"
    )
    back_double: bool = Field(
        default=False, description="托盘：后墙也卷边双层、内墙三个插脚（0461）"
    )
    hand_holes: bool = Field(
        default=False, description="托盘：端墙手孔（端墙双层时内墙同位置也开，0465）"
    )
    flap_slots: bool = Field(
        default=False, description="托盘：长边半宽盖翼两头开槽，合上时扣住端墙的堆叠凸舌（0464）"
    )
    front_return: bool = Field(
        default=False, description="托盘 wrap：前墙顶边再接一块回折内衬（0472）"
    )
    strip: list[StripItem] | None = Field(
        default=None,
        description="长条包裹式盒（corners = strip）：第一块是底板，之后每块面和它两头的端翼",
    )
    flap_ears: bool = Field(
        default=False, description="托盘：长边半宽盖翼两头接梯形防尘耳，合上时折进端墙里面（0416）"
    )
    end_roll: float | None = Field(
        default=None, gt=0, description="托盘 end_double 的卷边宽（宽卷边成平顶，0403）"
    )
    end_foot: bool = Field(
        default=False, description="托盘 end_double 的内墙下沿平脚代替插脚（0403）"
    )
    end_slits: bool = Field(
        default=False, description="托盘：端墙两道竖缝分成三段，顶上窄边连着（0445）"
    )
    corner_hooks: bool = Field(
        default=False, description="托盘扣角：长边墙外贴角翼头上的钩舌穿进端墙竖缝（0457）"
    )
    poly_sides: int | None = Field(
        default=None,
        ge=5,
        le=8,
        description="正多边形托盘（corners = polygon）的边数，对边距 = L（要 L = W）",
    )
    poly_flaps: bool = Field(
        default=False, description="正多边形托盘：每面墙头上一块角翼贴进下一面墙（0351）"
    )
    cup_holes: bool = Field(
        default=False, description="托盘：长边墙顶两块半宽隔板（rim）平铺在顶上、开圆杯孔（0446）"
    )
    front_low: bool = Field(default=False, description="托盘展示盒：前墙矮、端墙顶边斜（0485）")
    bevel: float | None = Field(
        default=None, gt=0, description="切角托盘：切角边长 c（默认 min(0.75·H, 0.2·宽)）"
    )
    bevel_regular: bool = Field(default=False, description="切角托盘：正八边形（要 L = W，0476）")
    bevel_on: Literal["base", "long"] = Field(
        default="base", description="切角托盘：斜墙挂在底板斜边上 / 长边墙两头（0459、0463）"
    )
    bevel_join: Literal["open", "flap", "wrap"] = Field(
        default="open",
        description="切角托盘墙角：不连 / 端墙角翼贴斜墙里面（0459）/ 斜墙再接一段包端墙外面（0459.1）",
    )
    flap_drops: bool = Field(
        default=False,
        description="托盘：长边半宽盖翼（top flap=long）两头折下贴端墙、插脚插底板（0437）",
    )
    end_window: bool = Field(
        default=False, description="托盘：端墙顶中间开口，那块往里翻 180° 贴墙（0483）"
    )
    corner_posts: bool = Field(
        default=False, description="托盘：端墙角翼折成三角角柱，斜面插舌插进底板斜缝（0483）"
    )
    rim_ends: Literal["none", "posts", "lugs", "mitre", "tabs", "drop", "wrap"] = Field(
        default="none",
        description="托盘长边墙窄边在墙角怎么收：posts 立柱折下贴端墙、lugs 立柱 + 插脚穿端墙脚的槽、"
        "mitre 绕过墙角斜接、tabs 插舌插进双层端墙卷边的槽（0431–0434）、drop 短舌折下贴端墙（0436）、"
        "wrap 端墙窄边两头折下压住外贴的角翼（0480）",
    )
    triangle_posts: bool = Field(
        default=False, description="托盘：端墙卷边成双层，内层折成两个三角角柱（0433）"
    )
    inner_wings: bool = Field(
        default=False, description="托盘 ear：内墙两侧各带一块翼，贴长边墙里面（0432）"
    )
    edge_slots: bool = Field(default=False, description="托盘：底板四条边上各两个底边孔（0451.1）")
    corner_meet: bool = Field(default=False, description="托盘：角翼深到墙中间，两片对口（0434）")
    axis: Literal["H", "W", "L"] = Field(
        default="H",
        description="瓦楞筒身方向：哪个产品尺寸是筒长（H 竖着；W / L 横放，四面墙绕另外两个尺寸围一圈，"
        "如套筒 0502 / 0503）",
    )
    base: Literal["rect", "regular", "octagon"] = Field(
        default="rect",
        description="盒身底面：rect 长方形 / regular 正多边形 / octagon 长八角（长方形切四角，0352）",
    )
    chamfer: float | None = Field(
        default=None, gt=0, description="长八角：切角边长（默认 0.25·短边）"
    )
    L: float | None = Field(default=None, gt=0, description="长方形：长（背、正面的内宽）")
    W: float | None = Field(default=None, gt=0, description="长方形：宽（侧面的内宽）")
    sides: int | None = Field(default=None, ge=3, le=12, description="正多边形：边数")
    edge: float | None = Field(default=None, gt=0, description="正多边形：每条边的内长")
    H: float | None = Field(default=None, gt=0, description="内高（片材不用）")
    # 片材（form = sheet）
    panels: list[float | str] | None = Field(
        default=None,
        description="片材：折线之间每一条的宽（横过折线量），合起来就是板宽；不给 = 一张平板。"
        "09 内衬可写成盒子尺寸的式子：L / W / H / v（窄边，默认 min(0.5·H, 30)），可带倍数和分母，"
        "如 W/2、1.414H、H/3——这时 L / W / H 是盒子内尺寸，改尺寸内衬跟着变",
    )
    folds: list[float] | None = Field(
        default=None,
        description="片材：每条折线折多少度（正 = 往同一侧卷，负 = 反折），不给都按 90",
    )
    crease: Literal["along_L", "along_W", "along_H"] = Field(
        default="along_L",
        description="片材：折线顺着 L（FEFCO 011x）还是顺着 W（012x）；09 内衬还可以顺着 H（竖着立在盒里）",
    )
    roll: bool = Field(default=False, description="片材：卷材（FEFCO 0100），刀版就是展开的一段")
    slots: SheetSlots | None = Field(
        default=None, description="片材：插槽（隔板互相插，FEFCO 0930–0935），开在最宽那条上"
    )
    holes: SheetHoles | None = Field(default=None, description="片材：圆孔（瓶口 / 定位孔）")
    strip_flaps: SheetFlaps | None = Field(
        default=None,
        description="片材：每条两头的翼（三片式盒身，翼折进来压在端板上，0601 / 0602 / 0606）",
    )
    steps: int | None = Field(
        default=None, ge=2, le=8, description="片材：一张平板切成台阶形（阶梯内衬的侧板，0960）"
    )
    # 托盘内衬 / 开孔（第 3 步，FEFCO 07 / 09）
    inside: bool = Field(
        default=False,
        description="托盘：放进盒子里的内托——L / W / H 是盒子的内尺寸，托盘外尺寸比它小一圈间隙（09）",
    )
    base_holes: int = Field(
        default=0,
        ge=0,
        le=12,
        description="托盘：底板中线上一排圆孔（瓶口 / 透气，0751、0771、0954）",
    )
    open_taper: bool = Field(
        default=False, description="托盘 corners=open：四面墙两头斜收成梯形，墙角留 V 口（0955）"
    )
    # 开槽箱墙上的刀口（第 3 步）
    window: Literal["front", "back"] | None = Field(
        default=None, description="瓦楞开槽箱：哪面长墙开窗（0722 八角窗、0740 出液口）"
    )
    window_shape: Literal["octagon", "tap", "low"] = Field(
        default="octagon",
        description="开窗形状：octagon 八角窗 / tap 齿线圆口 + 齿线提片（盒中袋出液口）/ low 墙下部取货口（0825）",
    )
    tear: bool = Field(
        default=False,
        description="瓦楞开槽箱：齿线撕开变展示托盘——端墙 side_a 离底 v 一道横齿线、两面长墙斜齿线（0716 / 0728）",
    )

    @property
    def h(self) -> float:
        if self.H is None:
            raise ValueError(t("This body requires H"))
        return self.H

    @model_validator(mode="after")
    def _shape(self) -> Body:
        if self.ear_shape != "calibrated":
            if self.form != "tray" or self.base != "rect" or self.corners != "ear":
                raise ValueError(t("ear_shape applies only to rectangular ear trays"))
            if self.inner_ears or self.lugs:
                raise ValueError(
                    t("Full-width corner flaps require no inner-wall ears or locking tabs")
                )
        if self.double_walls != "pair" or self.inner_ears:
            if self.form != "tray" or self.base != "rect" or self.corners != "ear":
                raise ValueError(
                    t("double_walls and inner_ears apply only to rectangular ear trays")
                )
        tray_only = (self.rim_ends != "none", self.triangle_posts, self.inner_wings, self.edge_slots,
                     self.corner_meet, self.stack != "none", self.lug_count is not None,
                     self.stack_on != "end", self.end_height is not None, self.end_double,
                     self.flap_drops, self.end_window, self.corner_posts, self.front_double,
                     self.back_double, self.hand_holes, self.flap_slots, self.front_return,
                     self.strip is not None, self.flap_ears, self.end_roll is not None, self.end_foot,
                     self.end_slits, self.bevel is not None, self.bevel_regular, self.bevel_on != "base",
                     self.bevel_join != "open", self.corner_hooks, self.cup_holes, self.front_low,
                     self.poly_sides is not None, self.poly_flaps, self.inside, self.base_holes > 0,
                     self.open_taper)  # fmt: skip
        if any(tray_only) and (self.form != "tray" or self.base != "rect"):
            raise ValueError(
                t(
                    "rim_ends, triangle_posts, inner_wings, edge_slots, and corner_meet apply only to rectangular trays"
                )
            )
        if self.inner_foot:
            if self.form != "tray" or self.base != "rect" or self.corners != "ear":
                raise ValueError(t("inner_foot applies only to rectangular ear trays"))
            if self.lugs:
                raise ValueError(
                    t(
                        "An inner-wall foot flap replaces the locking tabs. Use inner_foot with lugs=false."
                    )
                )
            if self.double_walls != "pair" or self.inner_ears or self.ear_shape != "calibrated":
                raise ValueError(
                    t(
                        "Inner-wall foot flaps require one pair of double walls and calibrated ears (0428 style)"
                    )
                )
        if self.form != "sheet" and self.H is None:
            raise ValueError(t("Tube and tray bodies require H"))
        if self.form == "sheet":
            if self.L is None or self.W is None:
                raise ValueError(
                    t(
                        "A sheet requires L (length along the creases) and W (total width across the creases)"
                    )
                )
            n = len(self.panels or [0])
            if self.folds is not None and len(self.folds) != n - 1:
                raise ValueError(
                    t(
                        "The sheet has {creases} creases, so folds requires {angles} angles",
                        creases=n - 1,
                        angles=n - 1,
                    )
                )
            symbolic = any(isinstance(w, str) for w in self.panels or [])
            if symbolic:
                for w in self.panels or []:
                    if isinstance(w, str) and SYMBOL.fullmatch(w) is None:
                        raise ValueError(
                            t(
                                "Unrecognised sheet strip width {width!r}. Use a number or an expression with L, W, H, or v such as W/2 or 1.414H.",
                                width=w,
                            )
                        )
                if self.H is None:
                    raise ValueError(
                        t(
                            "Supply H when sheet strip widths are expressions based on box dimensions"
                        )
                    )
            elif self.panels is not None and any(float(w) <= 0 for w in self.panels):
                raise ValueError(t("Every sheet strip must have a positive width"))
            if self.crease == "along_H" and self.H is None:
                raise ValueError(t("Supply H when sheet creases run along H"))
            if self.steps is not None and len(self.panels or [0]) != 1:
                raise ValueError(t("steps applies only to a flat sheet without folds"))
            if self.holes is not None and self.holes.panel >= len(self.panels or [0]):
                raise ValueError(
                    t(
                        "The strip selected by holes.panel={panel} does not exist",
                        panel=self.holes.panel,
                    )
                )
            return self
        if (
            self.slots is not None
            or self.holes is not None
            or self.steps is not None
            or self.strip_flaps
        ):
            raise ValueError(t("slots, holes, steps, and strip_flaps apply only to sheets"))
        if (self.window is not None or self.tear) and (self.form != "tube" or self.base != "rect"):
            raise ValueError(t("window and tear apply only to rectangular corrugated tube bodies"))
        if self.open_taper and self.corners != "open":
            raise ValueError(t("open_taper applies only to trays with corners=open"))
        if self.base in ("rect", "octagon") and (self.L is None or self.W is None):
            raise ValueError(t("Rectangular and elongated octagonal bodies require L and W"))
        if (
            self.base == "octagon"
            and self.chamfer is not None
            and 2 * self.chamfer >= min(self.L or 0, self.W or 0)
        ):
            raise ValueError(t("The corner cuts are too large for the elongated octagon"))
        if self.base == "regular" and (self.sides is None or self.edge is None):
            raise ValueError(t("A regular polygon body requires sides and edge"))
        return self


class Seam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["glue", "none", "tuck"] = "glue"
    width: float | None = Field(
        default=None,
        gt=0,
        description="糊口宽，默认按材料（纸盒 15、瓦楞 35）【初值】；接头在墙中间时是两截的搭接宽",
    )
    offset: float | None = Field(
        default=None,
        gt=0,
        description="瓦楞开槽箱：接头不在墙角，开在墙中间、离墙角 offset（FEFCO 0230 / 0231 的 v）",
    )
    wall: Literal["long", "short"] = Field(
        default="long",
        description="接头开在墙中间时在哪面：long 长边墙（0230）/ short 端墙（0231）",
    )
    partition: bool = Field(
        default=False,
        description="瓦楞开槽箱：接头那截外面多一块隔板，折进去把箱子分成两格（FEFCO 0228）；"
        "不给 offset 时接头在墙正中",
    )
    center_open: bool = Field(
        default=False, description="接头开在墙正中、不搭接不粘合（横放折叠盒的两半盖）"
    )
    full_overlap: bool = Field(
        default=False, description="接缝两端各延伸出完整一面墙，折后全幅重叠"
    )

    @model_validator(mode="after")
    def _open_joint(self) -> Seam:
        if self.center_open or self.full_overlap:
            if (
                self.kind != "none"
                or self.offset is not None
                or self.width is not None
                or self.partition
                or (self.center_open and self.full_overlap)
            ):
                raise ValueError(
                    t(
                        "center_open and full_overlap require kind=none without an offset, overlap, or partition"
                    )
                )
        elif self.kind == "none":
            raise ValueError(t("A jointless seam requires center_open or full_overlap"))
        elif self.kind == "tuck" and (self.offset is not None or self.partition):
            raise ValueError(
                t("A free tuck tab cannot be combined with a joint offset or partition")
            )
        return self


class End(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EndKind = "open"
    on: int | None = Field(
        default=None, ge=0, description="盖挂在第几面墙（从 0 数）；不给 = 第 0 面"
    )
    flap: Literal[
        "long",
        "half",
        "full",
        "overlap",
        "center",
        "center_overlap",
        "center_full",
        "rim",
        "rim_long",
        "rim_end",
        "gusset",
        "divider",
        "cells",
        "slot_lock",
        "tongue_slot",
        "tongue_notch",
        "full_long",
    ] = Field(
        default="half",
        description="摇盖的长短（长方形盒身）：half ½W、overlap 部分重叠（四片一样深）、full 全重叠（四片都 W，0203）、"
        "full_long 长摇盖 W + 端摇盖 ½W（ECMA 10 四翼粘合）；卡纸、瓦楞同一个意思（§20）；"
        "瓦楞开槽箱另有 center ½W + ½L（0204）、center_overlap 全部 ½L（0205）、center_full 长边 W + 端边 ½L（0206）、"
        "rim 窄边框（0209 / 0214）、rim_long / rim_end 窄边 + 内板（0229 / 0229.1）",
    )
    overlap: float = Field(default=0.0, ge=0, description="flap=overlap 时两片外摇盖重叠多少")
    tuck_len: float | None = Field(default=None, gt=0, description="插舌长，默认按盒宽推")
    dust_depth: float | None = Field(
        default=None, gt=0, description="防尘翼 / 粘翼深，默认按盖的大小推"
    )
    depth: float | None = Field(default=None, gt=0, description="缩小翼深（reduced），默认 0.4·宽")
    wing: float | None = Field(
        default=None,
        gt=0,
        description="侧插翼宽（side_tuck），默认同插舌长；"
        "开槽箱摇盖（flaps）：正面那片摇盖两侧带翼，合上后翼折进去贴端墙里面（0225 / 0227），给翼宽",
    )
    drop: float | None = Field(
        default=None, gt=0, description="开槽箱窄边往下折的内板高（rim_long / rim_end）"
    )
    fold: Literal["in", "down"] = Field(
        default="in", description="开槽箱窄边 rim：平铺成框 / 折下去贴墙（0214）"
    )
    thumb: int | None = Field(
        default=None, ge=0, description="开槽箱摇盖：第几面（0 / 2）那片长摇盖远边开拇指缺口"
    )
    dust: bool = Field(default=True, description="托盘连盖（hinged_lid，瓦楞）：盖两侧带不带防尘翼")
    lid_front: Literal["tuck", "lip", "return", "lock", "none"] = Field(
        default="tuck",
        description="托盘连盖前沿：tuck 插舌插进前墙里面 / lip 前翼盖在前墙外面（0440–0442）/ "
        "return 前翼 + 回折内衬、锁舌插盖沿槽（0418）/ lock 中间一块锁舌插进前墙顶的槽（0465 / 0466）/ "
        "none 前沿平的、什么都不带（靠两侧锁舌或外面的展示盖压住）",
    )
    dust_lock: bool = Field(
        default=False,
        description="托盘连盖：两侧防尘翼做到墙高、下沿两头各一个倒钩锁舌，插进底板靠端墙压线的槽扣住"
        "（侧面不糊胶，角翼靠这片翼压住；要糊侧面就不用它）",
    )
    tear_window: bool = Field(
        default=False,
        description="托盘连盖：盖中间一圈齿线围成撕开窗（第一次打开时撕掉，常和 display_lid 一起用）",
    )
    display_lid: Literal["none", "rect", "tab"] = Field(
        default="none",
        description="托盘连盖：对面墙上沿再挂一层展示盖，合上时压在盖外面、里面粘住撕开窗；打开时撕开窗跟着"
        "撕下来、展示盖立起来当头卡（胶条在窗口靠展示盖铰链那一边）。rect 远边平 / tab 两角圆角 + 中间半圆易开拉手",
    )
    dust_ears: bool = Field(
        default=False, description="托盘连盖：防尘翼前头多一段耳，贴进前墙里面（0475）"
    )
    lid_on: Literal["back", "end"] = Field(
        default="back", description="托盘连盖挂在后墙还是端墙上（0440）"
    )
    lid_header: bool = Field(
        default=False, description="托盘连盖中间一道压线，打开后翻起当头卡（0485）"
    )
    lock: bool = Field(
        default=False,
        description="开槽箱摇盖（full）：on 那片摇盖收成锁舌，对面那片开两道斜缝，锁舌两角插进缝里（0218）",
    )
    end_flaps: bool = Field(
        default=True, description="开槽箱摇盖：端墙（侧面）带不带摇盖（0225 的底不带）"
    )

    autolock_form: Literal["hook", "tab", "web"] = Field(
        default="hook",
        description="瓦楞自动锁底（autolock）的样子：hook 主翼带钩齿 + 糊胶三角（0711）/ "
        "tab 端墙翼带糊胶三角、一片长翼整幅深带插舌插进对面墙脚的缝（0701–0705）/ "
        "web 四片 ½W 翼、长翼带 45° 糊胶三角、没有钩齿（0706–0709）——后两种按示意图推读",
    )

    tear_strip: bool = Field(
        default=False,
        description="瓦楞开槽箱插舌盖（lid_tuck）：盖上一条撕拉条（两道齿线，电商一次性开箱，0886 / 0887）",
    )
    glue: bool | None = Field(
        default=None,
        description="封口翼（flaps / reduced / seal）上画粘合区：不给——缩小翼、搭粘画；摇盖卡纸画（彩盒封口上胶）、"
        "瓦楞不画（一般封箱胶带）。只是标注，几何不变",
    )
    hanger_layers: Literal[1, 2] | None = Field(
        default=None,
        description="挂板（hanger）层数：1 单层（挂孔离远边）/ 2 双层对折（挂孔离对折线，0240）；"
        "不给：卡纸单层、瓦楞双层（同一份挂板代码，§20）",
    )

    def slot_options(self) -> list[str]:
        """用了哪些只有瓦楞开槽箱摇盖才有的选项。"""
        out = [k for k, on in (("thumb", self.thumb is not None), ("lock", self.lock),
                               ("end_flaps", not self.end_flaps)) if on]  # fmt: skip
        if self.wing is not None and self.kind == "flaps":
            out.append("wing")
        return out

    @model_validator(mode="after")
    def _options(self) -> End:
        if self.kind != "flaps" and self.slot_options():
            raise ValueError(
                t(
                    "{options} apply only to flap closures (flaps)",
                    options=", ".join(self.slot_options()),
                )
            )
        if self.tear_strip and self.kind != "lid_tuck":
            raise ValueError(t("tear_strip applies only to tuck lids (lid_tuck)"))
        if self.glue is not None and self.kind not in ("flaps", "reduced", "seal"):
            raise ValueError(t("glue applies only to closure flaps (flaps, reduced, or seal)"))
        if self.hanger_layers is not None and self.kind != "hanger":
            raise ValueError(t("hanger_layers applies only to hanger panels (hanger)"))
        if self.autolock_form != "hook" and self.kind != "autolock":
            raise ValueError(t("autolock_form applies only to auto-lock bottoms (autolock)"))
        if not self.dust and self.kind != "hinged_lid":
            raise ValueError(t("dust controls dust flaps on hinged tray lids (hinged_lid)"))
        lid_opts = (
            self.lid_front != "tuck"
            or self.dust_ears
            or self.lid_on != "back"
            or self.lid_header
            or self.dust_lock
            or self.tear_window
            or self.display_lid != "none"
        )
        if lid_opts and self.kind != "hinged_lid":
            raise ValueError(
                t(
                    "lid_front, dust_ears, lid_on, dust_lock, tear_window, and display_lid apply only to hinged tray lids (hinged_lid)"
                )
            )
        return self


class StructureParams(BaseModel):
    """结构配方（style = "structure"）。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    body: Body
    seam: Seam = Field(default_factory=Seam)
    top: End = Field(default_factory=lambda: End(kind="lid_tuck", on=0))
    bottom: End = Field(default_factory=lambda: End(kind="lid_tuck", on=2))
    apply_compensation: bool = True
    dim_basis: Literal["inner", "outer"] = "inner"
    corner_radius: float | None = Field(
        default=None,
        gt=0,
        le=20,
        description="刀版外轮廓自由凸角倒圆角半径（mm）：只倒不碰压线 / 齿线 / 内切口的直角，彩盒常用 2–5",
    )
    tuning: dict[str, Any] = Field(
        default_factory=dict,
        description="机构微调（按校准过的机构的参数名，如 friction_lock、dust_offset）",
    )

    @model_validator(mode="after")
    def _center_open_body(self) -> StructureParams:
        if (self.seam.center_open or self.seam.full_overlap or self.seam.kind == "tuck") and (
            self.body.form != "tube" or self.body.base != "rect"
        ):
            raise ValueError(
                t(
                    "center_open, full_overlap, and free tuck tabs apply only to rectangular tube bodies"
                )
            )
        return self


__all__ = ["Body", "End", "EndKind", "Seam", "StructureParams"]
