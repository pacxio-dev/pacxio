"""job.json 模型（规格第 6.1 节）。Job 目录即状态：这是唯一事实来源。"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pacxio.messages import t
from pacxio.model.common import Issue, validation_message
from pacxio.model.content import (
    ContentInventory,
    bad_line_refs,
    fill_template,
    template_ids,
)
from pacxio.model.profile import Material

SCHEMA_VERSION = "0.1"
JOB_FILE = "job.json"


class PrintSpec(BaseModel):
    # digital：数码（标签、小批量软袋常用）；gravure：凹印（软包装大批量，C5）
    method: Literal["offset", "flexo", "digital", "gravure"] = "offset"
    sides: Literal["outside_only", "both"] = "outside_only"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(_Strict):
    """面局部坐标下的矩形（mm）：从盒外看、面处于正立状态时，左下角为原点。"""

    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class ColorSpec(_Strict):
    """印刷色。矢量颜色直接用 CMYK 百分比（0–100），不经任何转换。"""

    cmyk: tuple[float, float, float, float]

    @field_validator("cmyk")
    @classmethod
    def _range(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if any(not 0 <= c <= 100 for c in v):
            raise ValueError(t("CMYK components must each be between 0 and 100 percent"))
        return v


class KeyColor(_Strict):
    """关键颜色（）：给工厂对色用的标注，不改印刷内容。潘通号由用户给，引擎不换算。"""

    name: str = Field(min_length=1)
    pantone: str | None = None  # 如 "PANTONE 7473 C"
    cmyk: tuple[float, float, float, float]  # 印刷用的数值（0–100），即版面里实际用的颜色
    where: str | None = None  # 用在哪

    @field_validator("cmyk")
    @classmethod
    def _range(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if any(not 0 <= c <= 100 for c in v):
            raise ValueError(t("CMYK components must each be between 0 and 100 percent"))
        return v


OUTSIDE_ALL = "outside_all"


Space = Literal["panel", "body", "blank"]
HANGING_AUTO = "auto"
HangingIndent = Literal["auto"] | Annotated[float, Field(ge=0)]
HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value: str) -> str:
    """'#ABC' / 'aabbcc' → '#aabbcc'。不是十六进制颜色时抛 ValueError。"""
    m = HEX_COLOR.match(value.strip())
    if m is None:
        raise ValueError(t("Invalid hexadecimal colour {value!r}. Use #RRGGBB.", value=value))
    h = m.group(1).lower()
    return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)


class Placement(_Strict):
    """图文放置（）。按列表顺序自下而上绘制（文字也一样，后放的盖住先放的）。

    - fill：铺底色（color），scope 默认 outside_all；
    - pattern：在刀版全局坐标中平铺（跨折线连续），source + tile_mm；
    - image：位图放进 box，fit 决定缩放方式；可用 clip（SVG 形状）裁成任意形状（占位框也可以，
      换图时沿用）；
    - vector：SVG 矢量图形，每种颜色按 CMYK 油墨原样印刷（colors 指定 SVG 颜色 → CMYK，
      color 把所有形状印成同一个颜色）；
    - text：由引擎排版并转曲（D7）；style 引用文字样式（层级），没给的字体、字号等取自样式；
    - placeholder：占位框（排版初稿里图片 / 图形的位置），label 写明放什么；正式文件前必须换掉。

    space（image / vector / placeholder）决定 box 的坐标系：
    - panel：面局部坐标（从盒外看、面正立时左下角为原点），需要 panel；
    - body：盒身展开一圈（正面左下角为原点，向右依次 正面 → 右侧面 → 背面 → 左侧面，
      一圈周长处回到正面），用来放跨折线连续的图形，只画在盒身四个面上；
    - blank：整张展开图（刀版坐标，包围盒左下角为原点），可以跨到盖、底等任何面上。
    """

    id: str
    # 信息标签（自由文字，如"品牌名""品名""净含量""配料""生产商"）：效果图确认页按它归类，
    # 并可用 `mockup --require` 检查必须出现的信息是否都在
    tag: str | None = None
    kind: Literal["fill", "image", "pattern", "text", "vector", "placeholder"]
    source: str | None = None
    space: Space | None = None  # None 即 panel（新字段一律默认 None：旧 job 的序列化与指纹不变）
    panel: str | None = None  # 多部件盒型写全名"部件.面"，如 lid.top
    # 多部件盒型：没指定某个面时（铺底 / 平铺 / space=blank）说是哪个部件；为空 = 每个部件都铺
    part: str | None = None
    scope: str | list[str] | None = None
    box: Box | None = None
    fit: Literal["cover", "contain", "stretch"] | None = None
    clip: str | None = None
    tile_mm: float | None = Field(default=None, gt=0)
    text: str | None = None
    font: str | None = None
    size_pt: float | None = Field(default=None, gt=0)
    color: ColorSpec | None = None
    colors: dict[str, ColorSpec] | None = None
    # 竖排时 left / top 是靠上、center 居中、right / bottom 靠下（）
    align: Literal["left", "center", "right", "top", "bottom"] = "left"
    # 字距：1/1000 em（与 InDesign / Illustrator 的"字距调整"同单位），正值拉开、负值收紧
    tracking: float | None = Field(default=None, ge=-200, le=1000)
    # 行距：字号的倍数（不给时 1.2）
    line_height: float | None = Field(default=None, ge=0.8, le=4.0)
    # 悬挂缩进（编号列表 / 项目符号）："auto" 量出标记后文字的起点；数值（mm）直接给这个起点；
    # 0 = 不缩进（样式里有缩进时，放置用 0 关掉）。规则见 layout/text.py
    hanging_indent: HangingIndent | None = None
    vertical: bool | None = None  # 竖排（天地盖测试 F-02）；None 是横排，旧 job 不变
    # 倾斜（度，2026-11-11 Tidewell 大数字卖点）：以墨迹中心为基点错切整块文字的轮廓。
    # skew_x 正值上端往右倒（像斜体）；skew_y 正值往右上升、竖笔保持竖直。None 是不斜，旧 job 不变
    skew_x: float | None = Field(default=None, ge=-30, le=30)
    skew_y: float | None = Field(default=None, ge=-30, le=30)
    # 引用信息清单的条目 id：文字内容（或图形素材）以清单为准
    content: str | None = None
    style: str | None = None  # 文字样式（层级）名，见 Job.styles
    label: str | None = None  # 是什么 / 放什么（如"卫浴照片""喷码区"）；占位框换图后沿用
    # 英文说明：只用在英文版式简报和出图提示词里（label 写中文给人看时补上）；不进设计指纹
    label_en: str | None = None

    @field_validator("colors")
    @classmethod
    def _hex_keys(cls, v: dict[str, ColorSpec] | None) -> dict[str, ColorSpec] | None:
        if v is None:
            return None
        return {normalize_hex(k): c for k, c in v.items()}

    @model_validator(mode="after")
    def _required_fields(self) -> Placement:
        placed = ["source", "panel", "box"] if self.coord_space == "panel" else ["source", "box"]
        need: dict[str, list[str]] = {
            "fill": ["color"],
            "pattern": ["source", "tile_mm"],
            "image": placed,
            "vector": placed,
            "text": ["panel", "box", "text", "font", "size_pt"],
            "placeholder": [f for f in placed if f != "source"],
        }
        missing = [f for f in need[self.kind] if getattr(self, f) is None]
        if self.content is not None:  # 文字 / 素材来自信息清单
            missing = [f for f in missing if f not in ("text", "source")]
        if self.style is not None:  # 字体、字号来自文字样式
            missing = [f for f in missing if f not in ("font", "size_pt")]
        if missing:
            raise ValueError(t("kind={kind} requires: {fields}", kind=self.kind, fields=', '.join(missing)))
        if self.coord_space != "panel":
            if self.kind not in ("image", "vector", "placeholder"):
                raise ValueError(
                    t("space={space} applies to image, vector, and placeholder. It is unavailable for {kind}.", space=self.space, kind=self.kind)
                )
            if self.panel is not None:
                raise ValueError(t("With space={space}, the box is not tied to a panel. Omit panel.", space=self.space))
        if self.clip is not None and self.kind not in ("image", "placeholder"):
            raise ValueError(t("clip applies only to kind=image or kind=placeholder"))
        if self.colors is not None and self.kind != "vector":
            raise ValueError(t("colors applies only to kind=vector"))
        if self.kind == "vector" and self.color is not None and self.colors is not None:
            raise ValueError(t("Use either color for one overall colour or colors for per-colour mapping"))
        if self.kind != "text" and (self.tracking is not None or self.line_height is not None):
            raise ValueError(t("tracking and line_height apply only to kind=text"))
        if self.vertical and self.kind != "text":
            raise ValueError(t("vertical applies only to kind=text"))
        if (self.skew_x or self.skew_y) and (self.kind != "text" or self.vertical):
            raise ValueError(t("skew_x and skew_y apply only to horizontal kind=text"))
        if self.vertical and self.hanging_indent is not None:
            raise ValueError(t("hanging_indent is unavailable for vertical text"))
        if self.align in ("top", "bottom") and not self.vertical:
            raise ValueError(
                t("Use align=top or bottom for vertical text, or left, center, or right for horizontal text")
            )
        if self.hanging_indent is not None:
            if self.kind != "text":
                raise ValueError(t("hanging_indent applies only to kind=text"))
            if self.align != "left":
                raise ValueError(t("hanging_indent requires left-aligned text (align=left)"))
            hi = self.hanging_indent
            if hi != HANGING_AUTO and self.box is not None and float(hi) >= self.box.w:
                raise ValueError(t("hanging_indent {indent:g} mm must be smaller than the text box width {width:g} mm", indent=hi, width=self.box.w))
        if self.style is not None and self.kind != "text":
            raise ValueError(t("style applies only to kind=text"))
        labelled = self.label is not None or self.label_en is not None
        if labelled and self.kind not in ("placeholder", "image", "vector"):
            raise ValueError(t("Use label or label_en to describe a placeholder, image, or vector"))
        # 注意：旧版本给每个放置都写了 fit（如 fill 上的 "cover"），这里不能拒绝，否则旧 job 读不进来
        return self

    @property
    def coord_space(self) -> str:
        """box 的坐标系：panel（默认）| body | blank。"""
        return self.space or "panel"

    def fit_mode(self) -> str:
        """缩放方式：图片默认 cover（铺满裁切），矢量默认 contain（完整放进框里）。"""
        if self.fit is not None:
            return self.fit
        return "contain" if self.kind == "vector" else "cover"

    def scope_panels(self) -> list[str] | None:
        """None 表示整张外表面（outside_all）；否则是面 id 列表。"""
        if self.scope is None or self.scope == OUTSIDE_ALL:
            return None
        return [self.scope] if isinstance(self.scope, str) else list(self.scope)


STYLE_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


class TextStyle(_Strict):
    """文字样式（层级）：标题、小标题、正文、小字……放置用 style 引用，改样式时同层级一起变。"""

    font: str
    size_pt: float = Field(gt=0)
    label: str | None = None  # 中文层级名，如"主标题""正文"
    line_height: float | None = Field(default=None, ge=0.8, le=4.0)
    tracking: float | None = Field(default=None, ge=-200, le=1000)
    color: ColorSpec | None = None
    # 悬挂缩进：同 Placement.hanging_indent；用这个样式的居中 / 右对齐文字不缩进
    hanging_indent: HangingIndent | None = None


FINISH_ZH = {
    "foil_gold": "烫金",
    "foil_silver": "烫银",
    "foil_copper": "烫铜金",
    "foil_holo": "镭射烫",
    "spot_uv": "局部 UV",
    "emboss": "击凸",
    "deboss": "压凹",
    "white": "白墨",
}


class FinishPlacement(_Strict):
    """工艺放置（规格 10 节）。source 为 SVG（首选）或二值 PNG；text 是一段文字放置的 id——工艺就是这段字
    的字形（引擎排的、转曲后的轮廓），文字改了工艺跟着变（天地盖测试 F-03）。两者给且只给一个。"""

    id: str
    tag: str | None = None
    type: str
    source: str | None = None
    text: str | None = None
    panel: str
    box: Box
    threshold: int = Field(default=128, ge=1, le=254, description="位图二值化阈值")
    content: str | None = None  # 引用信息清单的条目（如烫金的品牌 Logo）
    # 给工厂的说明（箔的实际颜色如铜金、光泽……）：写进规格书的工艺一节；不改工艺图形，不进设计指纹
    note: str | None = Field(default=None, max_length=200)
    # 局部 UV 的做法：None = 常规（丝印，配置档 spot_uv 规则）；digital = 数码 UV（喷墨，spot_uv_digital 规则，
    # 线更细、间距更小）。只有用户确认工厂能做数码 UV 时才标（2026-10-30 用户定，）
    process: Literal["digital"] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> FinishPlacement:
        if (self.source is None) == (self.text is None):
            raise ValueError(t("Finish {id} requires either a source graphic or a text placement", id=self.id))
        if self.process is not None and self.type != "spot_uv":
            raise ValueError(t("Finish {id}: --process digital applies only to spot_uv", id=self.id))
        return self


ATTACHMENT_ZH = {"metal_badge": "金属标"}
MATERIAL_ZH = {"zinc_alloy": "锌合金（压铸）", "aluminum": "铝（冲压）"}
PLATING_ZH = {
    "champagne_rose_gold": "香槟玫瑰金",
    "rose_gold": "玫瑰金",
    "champagne_gold": "香槟金",
    "gold": "金色",
    "silver": "银色",
}
MOUNT_ZH = {"adhesive": "背胶平贴"}


class Attachment(_Strict):
    """配件（，2026-10-30 用户选金属标贴）：另外做好、盒厂组装时贴到盒上的东西，
    不印、不出工艺版。第一版只有金属标（背胶平贴）。source 是标的外形（SVG），relief 是凸起的图案
    （字、边、纹样，可不给）——两个文件用同一个画板，放进 box 时一起缩放。"""

    id: str
    tag: str | None = None
    type: Literal["metal_badge"] = "metal_badge"
    panel: str
    box: Box
    source: str
    relief: str | None = None
    material: Literal["zinc_alloy", "aluminum"] = "zinc_alloy"
    plating: Literal["champagne_rose_gold", "rose_gold", "champagne_gold", "gold", "silver"] = (
        "champagne_rose_gold"
    )
    thickness_mm: float = Field(default=3.0, gt=0, le=20)  # 【初值】
    mount: Literal["adhesive"] = "adhesive"
    # 给标牌厂、盒厂的说明（浮雕层次、底面喷砂……）：写进规格书；不进设计指纹
    note: str | None = Field(default=None, max_length=200)


class SurfaceCoverage(_Strict):
    scope: Literal["outside_all", "panels"] = "outside_all"
    part: str | None = None
    panels: list[str] | None = None

    @model_validator(mode="after")
    def _scope(self) -> SurfaceCoverage:
        if (self.scope == "panels") != bool(self.panels):
            raise ValueError(t("scope=panels requires panels. Omit panels for outside_all."))
        if self.part and any(not p.startswith(self.part + ".") for p in self.panels or []):
            raise ValueError(t("When a part is specified, all panels must belong to it and use part.panel names"))
        return self


class SurfaceExclusion(_Strict):
    panel: str
    box: Box
    source: str | None = None  # 为空时整个矩形，否则用 SVG 实际轮廓（包括孔洞）


class SurfaceTreatment(_Strict):
    id: str
    label: str | None = None
    type: Literal["matte_lamination", "gloss_lamination", "matte_varnish", "gloss_varnish"]
    coverage: SurfaceCoverage = Field(default_factory=SurfaceCoverage)
    exclusions: list[SurfaceExclusion] = Field(default_factory=list)
    # 粘接区（糊口、自动锁底涂胶区）怎么处理：full_sheet 整张覆盖、不避让（常规做法，2026-10-22
    # 用户问过工厂）；explicit_exclusions 按 exclusions 避让。factory_pending 是旧记录，按整张处理
    bonding_policy: Literal["full_sheet", "explicit_exclusions", "factory_pending"] = "full_sheet"
    note: str | None = None

    @model_validator(mode="after")
    def _bonding(self) -> SurfaceTreatment:
        if self.bonding_policy == "explicit_exclusions" and not self.exclusions:
            raise ValueError(t("Explicit exclusions require at least one exclusions area. Use full_sheet for full coverage."))
        for e in self.exclusions:
            if self.coverage.panels is not None and e.panel not in self.coverage.panels:
                raise ValueError(t("Excluded panel {panel} is outside coverage.panels", panel=e.panel))
            if self.coverage.part and not e.panel.startswith(self.coverage.part + "."):
                raise ValueError(t("Excluded panel {panel} is not part of {part}", panel=e.panel, part=self.coverage.part))
        return self


ContainerShape = Literal["cylinder", "jar", "taper", "square"]
ContainerFinish = Literal[
    "glass_clear", "glass_amber", "glass_frosted", "pet_clear", "hdpe_white", "plastic_black", "tin"
]


class Container(_Strict):
    """标签贴在哪种瓶 / 罐上（ 节）。只用于效果图和检查 L01，
    不出生产文件、不进确认指纹（换个瓶子看效果不算改设计）。没给的尺寸按标签参数推一个。

    尺寸都是 mm：diameter 瓶身直径（taper 是下口）、top_diameter 锥形上口、height 瓶身（可贴标段 +
    肩以下的圆角）高、shoulder 肩高（jar / cylinder 才有，瓶身到瓶口的收口段）、neck_d / neck_h
    瓶口、cap_d / cap_h 盖。label_z 标签下沿离瓶底的高度；angle 标签中线朝哪（0 = 正对看的人，
    度，顺时针为正）；on = lid 时标签贴在盖顶（圆标）。"""

    shape: ContainerShape | None = (
        None  # 不给 = 按标签推（环标 / 前标：高瘦是圆柱瓶、矮胖是广口罐；扇形标：锥形）
    )
    diameter: float | None = Field(default=None, gt=0)
    top_diameter: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    shoulder: float | None = Field(default=None, ge=0)
    neck_d: float | None = Field(default=None, gt=0)
    neck_h: float | None = Field(default=None, ge=0)
    cap_d: float | None = Field(default=None, gt=0)
    cap_h: float | None = Field(default=None, ge=0)
    bottom_r: float | None = Field(default=None, ge=0)  # 瓶底圆角（不能贴标）
    finish: ContainerFinish | None = None
    label_z: float | None = Field(default=None, ge=0)
    angle: float | None = None
    on: Literal["body", "lid"] | None = None


class PouchView(_Strict):
    """软袋装满后的样子（ 节）：fill 鼓起量 0（平）– 1（很满），不给 = 配置档
    pouches.fill_default【初值】。只影响效果图和 PO05（装满后拉伸）检查。"""

    fill: float | None = Field(default=None, ge=0, le=1)
    # 透明窗里看到的内容物颜色（#rrggbb，只作展示），不给 = 一种中性的棕色
    contents: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


WindowShape = Literal["rect", "circle", "obround"]


class CornerFinish(_Strict):
    """刀模圆角（整个项目一道工艺，出刀版最后一步加）：外角 = 翼、舌的自由角，内角 = 刀线的凹角。
    不写 = 按材料（卡纸外 2 / 内 1，瓦楞不倒）；写 0 = 不倒。2026-11-07 用户：彩盒卡纸常规都用圆角。"""

    outer: float | None = Field(default=None, ge=0, le=20)
    inner: float | None = Field(default=None, ge=0, le=5)


class Window(_Strict):
    """开窗（）：卡纸盒身面上的一个内部切口。

    box 用面局部坐标（同 art add --box）。corner_r 为空时取配置档的 window 规则。film 只记录工厂
    贴不贴窗膜（贴窗是工厂的后续工序，引擎不管，2026-10-20 用户决定）。
    """

    id: str
    panel: str
    box: Box
    shape: WindowShape = "rect"
    corner_r: float | None = Field(default=None, gt=0)  # 只对 rect
    film: Literal["pet", "none"] = "pet"
    film_margin: float | None = Field(
        default=None, gt=0
    )  # 已不用（贴窗由工厂管）；留着让旧 job.json 能读


class Job(BaseModel):
    schema_version: str = SCHEMA_VERSION
    job_id: str
    style: str
    params: dict[str, Any]
    material: Material
    print: PrintSpec = Field(default_factory=PrintSpec)
    profile: str = "default"
    placements: list[Placement] = Field(default_factory=list)
    finishes: list[FinishPlacement] = Field(default_factory=list)
    content: ContentInventory | None = None  # 信息清单（ 2.1）
    styles: dict[str, TextStyle] | None = None  # 文字样式（层级）， 2.2
    # 面与面相同（正反同款）：{"back": "front"} 表示背面的版面就是正面的版面——只有一份数据，
    # 排版、效果图里自动复制过去，改正面时背面一起变
    same_faces: dict[str, str] | None = None
    windows: list[Window] | None = None  # 开窗（结构的一部分：改窗口要重新确认效果图）
    corner_finish: CornerFinish | None = None  # 刀模圆角；不写按材料默认
    surface_treatments: list[SurfaceTreatment] | None = None
    # 关键颜色：只是给工厂对色的标注（规格书、生产文件标注层），不算设计改动、不进确认指纹
    key_colors: list[KeyColor] | None = None
    attachments: list[Attachment] | None = None  # 配件（金属标……）：另做、贴装，
    # 标签贴在哪种瓶 / 罐上（C4）：只给效果图和检查用，不进确认指纹
    container: Container | None = None
    # 软袋装满效果（C5 第二步）：鼓起量；只给效果图和 PO05 用，不进确认指纹
    pouch: PouchView | None = None

    @field_validator("styles")
    @classmethod
    def _style_names(cls, v: dict[str, TextStyle] | None) -> dict[str, TextStyle] | None:
        for name in v or {}:
            if not STYLE_NAME.match(name):
                raise ValueError(
                    t(
                        "Style names may only contain letters, numbers, underscores, and hyphens: {name!r}",
                        name=name,
                    )
                )
        return v

    def text_of(self, p: Placement) -> str | None:
        """放置实际排的文字：引用清单时以清单为准；文字里的 {{条目id}} 换成清单原文。"""
        if self.content is None:
            return p.text
        if p.content is not None:
            item = self.content.get(p.content)
            if item is not None:
                shown = item.display_text()
                return fill_template(shown, self.content) if shown is not None else None
        if p.text is not None and template_ids(p.text):
            return fill_template(p.text, self.content)
        return p.text

    def content_refs(self) -> dict[str, list[str]]:
        """条目 id → 引用它的放置 / 工艺 id（--content 引用与文字里的 {{条目id}} 都算）。"""
        refs: dict[str, list[str]] = {}
        for p in self.placements:
            ids = ([p.content] if p.content else []) + template_ids(p.text)
            for i in dict.fromkeys(ids):
                refs.setdefault(i, []).append(p.id)
        for f in self.finishes:
            if f.content:
                refs.setdefault(f.content, []).append(f.id)
        if self.content is not None:  # 条目原文里引用的条目，跟着它一起算"已放上"
            for ref, users in list(refs.items()):
                for sub in self.content.expand_refs([ref]) - {ref}:
                    refs.setdefault(sub, [])
                    refs[sub] += [u for u in users if u not in refs[sub]]
        return refs

    def source_of(self, p: Placement) -> str | None:
        """放置实际用的素材：自己没给 source 时用清单条目的 asset。"""
        if p.source is None and p.content is not None and self.content is not None:
            item = self.content.get(p.content)
            if item is not None:
                return item.asset
        return p.source

    def box_dims(self) -> dict[str, Any]:
        """盒子的产品尺寸 {L, W, H, dim_basis}。普通盒型就是参数里的；几何引擎的结构配方（style = structure）
        尺寸在 body 里（正多边形按对边距当 L / W，片材没给 H 时 H = 0）。"""
        q = self.params
        if (
            "parts" in q and "body" not in q and q["parts"]
        ):  # 几何引擎多件套：按第一件（地盒 / 盒身）
            first = next(iter(q["parts"].values()))
            return self.model_copy(update={"params": first}).box_dims() | {
                "dim_basis": first.get("dim_basis", "inner")
            }
        if "L" in q or "body" not in q:
            return {"L": q.get("L", 0.0), "W": q.get("W", 0.0), "H": q.get("H", 0.0),
                    "dim_basis": q.get("dim_basis", "inner")}  # fmt: skip
        b = q["body"]
        L, W = b.get("L"), b.get("W")
        if L is None and b.get("edge") and b.get("sides"):
            import math

            L = W = b["edge"] / math.tan(math.pi / b["sides"])
        return {"L": float(L or 0.0), "W": float(W if W is not None else L or 0.0),
                "H": float(b.get("H") or 0.0), "dim_basis": q.get("dim_basis", "inner")}  # fmt: skip

    def resolved(self) -> Job:
        """把放置补全（清单里的文字与素材、默认标签、文字样式），供排版与效果图使用；job.json 不变。"""
        job = self._with_styles()
        if job.content is not None:
            job = job._with_content()
        return job._with_same_faces()

    def for_part(self, part: str | None) -> Job:
        """多部件盒型的一个部件（ 0.4）：只留这个部件的放置 / 工艺 / 窗口，
        面名去掉"部件."前缀；没指定部件的铺底 / 平铺每个部件都留一份。part 为空（单部件盒型）原样返回。

        正反同款先在整套上展开（可以跨部件），视图里不再带 same_faces。
        """
        if part is None:
            return self
        pre = f"{part}."

        def local(name: str | None) -> str | None:
            return name[len(pre) :] if name is not None and name.startswith(pre) else None

        job = self._with_same_faces()
        placements: list[Placement] = []
        for p in job.placements:
            if p.panel is not None:
                name = local(p.panel)
                if name is not None:
                    placements.append(p.model_copy(update={"panel": name, "part": None}))
            elif p.scope_panels() is not None:
                names = [n for n in (local(x) for x in p.scope_panels() or []) if n is not None]
                if names:
                    placements.append(p.model_copy(update={"scope": names, "part": None}))
            elif p.part is None or p.part == part:
                placements.append(p.model_copy(update={"part": None}))
        finishes = [
            f.model_copy(update={"panel": local(f.panel)}) for f in self.finishes if local(f.panel)
        ]
        windows = [
            w.model_copy(update={"panel": local(w.panel)})
            for w in self.windows or []
            if local(w.panel)
        ]
        attachments = [
            a.model_copy(update={"panel": local(a.panel)})
            for a in self.attachments or []
            if local(a.panel)
        ]
        surfaces = []
        for s in self.surface_treatments or []:
            cov = s.coverage
            if cov.part is not None and cov.part != part:
                continue
            names = [n for x in cov.panels or [] if (n := local(x)) is not None]
            if cov.scope == "panels" and not names:
                continue
            exclusions = [
                e.model_copy(update={"panel": local(e.panel)})
                for e in s.exclusions
                if local(e.panel)
            ]
            surfaces.append(
                s.model_copy(
                    update={
                        "coverage": cov.model_copy(update={"part": None, "panels": names or None}),
                        "exclusions": exclusions,
                    }
                )
            )
        return job.model_copy(update={
            "job_id": f"{self.job_id}-{part}", "placements": placements, "finishes": finishes,
            "windows": windows or None, "same_faces": None, "surface_treatments": surfaces or None,
            "attachments": attachments or None,
        })  # fmt: skip

    def _with_same_faces(self) -> Job:
        """把源面上（面局部坐标）的放置复制到相同的面上，id 加 @目标面。"""
        if not self.same_faces:
            return self
        extra: list[Placement] = []
        for target, source in self.same_faces.items():
            for p in self.placements:
                if p.coord_space == "panel" and p.panel == source:
                    extra.append(p.model_copy(update={"id": f"{p.id}@{target}", "panel": target}))
        return self.model_copy(update={"placements": [*self.placements, *extra]})

    def _with_styles(self) -> Job:
        if not self.styles or not any(p.style for p in self.placements):
            return self
        ps: list[Placement] = []
        for p in self.placements:
            st = self.styles.get(p.style) if p.style else None
            if st is None:
                ps.append(p)
                continue
            upd: dict[str, Any] = {}
            for f in ("font", "size_pt", "line_height", "tracking", "color", "hanging_indent"):
                if getattr(p, f) is None and getattr(st, f) is not None:
                    upd[f] = getattr(st, f)
            if p.align != "left":  # 样式的悬挂缩进只给左对齐的文字
                upd.pop("hanging_indent", None)
            ps.append(p.model_copy(update=upd))
        return self.model_copy(update={"placements": ps})

    def _with_content(self) -> Job:
        assert self.content is not None
        ps: list[Placement] = []
        for p in self.placements:
            item = self.content.get(p.content) if p.content else None
            if item is None:
                refs = template_ids(p.text) if p.kind == "text" else []
                if refs:  # 组合文字：内容以清单为准，标签默认取第一个条目的类别
                    first = self.content.get(refs[0])
                    tag = p.tag or (first.label if first is not None else None)
                    p = p.model_copy(update={"text": self.text_of(p), "tag": tag})
                ps.append(p)
                continue
            upd: dict[str, Any] = {"tag": p.tag or item.label}
            if p.kind == "text":
                upd["text"] = self.text_of(p)
            elif p.source is None:
                upd["source"] = item.asset
            ps.append(p.model_copy(update=upd))
        fs = [
            f.model_copy(update={"tag": f.tag or item.label})
            if f.content and (item := self.content.get(f.content)) is not None
            else f
            for f in self.finishes
        ]
        return self.model_copy(update={"placements": ps, "finishes": fs})

    def layout_issues(self) -> list[Issue]:
        """排版前就能发现的引用问题：清单条目、文字样式。"""
        out = self.content_issues()
        region_ids = [f.id for f in self.finishes] + [s.id for s in self.surface_treatments or []]
        region_ids += [a.id for a in self.attachments or []]
        if len(region_ids) != len(set(region_ids)):
            out.append(
                Issue(
                    id="FINISH",
                    message=t("A finish, surface treatment, or attachment ID is duplicated"),
                    fix=t("Use a unique ID for each area"),
                )
            )
        for p in self.placements:
            if p.style is not None and (not self.styles or p.style not in self.styles):
                names = ", ".join(self.styles or {}) or "（还没有样式）"
                out.append(
                    Issue(
                        id="STYLE",
                        message=t("Placement {id} refers to missing text style {style}", id=p.id, style=p.style),
                        fix=t("Create the style with `pacxio style set <job> {style} --font ... --size-pt ...`. Existing styles: {styles}", style=p.style, styles=names),
                    )
                )
        return out

    def content_issues(self) -> list[Issue]:
        """放置 / 工艺引用了清单里不存在的条目，或引用方式不对。"""
        out: list[Issue] = []
        refs: list[tuple[str, str, str]] = [
            (p.id, p.content, p.kind) for p in self.placements if p.content is not None
        ]
        refs += [(f.id, f.content, "finish") for f in self.finishes if f.content is not None]
        for pid, ref, kind in refs:
            item = self.content.get(ref) if self.content is not None and ref else None
            if item is None:
                out.append(
                    Issue(
                        id="CONTENT",
                        message=t("Placement {id} references content item {item}, which is missing from the list", id=pid, item=ref),
                        fix=t("Run `pacxio content list <job>` to check item IDs, or add the item with `content add`"),
                    )
                )
            elif (
                kind in ("image", "vector", "pattern")
                and item.asset is None
                and not any(p.source for p in self.placements if p.id == pid)
            ):
                out.append(
                    Issue(
                        id="CONTENT",
                        message=t("Placement {id} references item {item}, which has no graphic. The placement also has no --source.", id=pid, item=ref),
                        fix=t("Add the item graphic with `content edit <job> {item} --asset <file>`, or supply --source on the placement", item=ref),
                    )
                )
            elif kind == "text" and item.display_text() is None:
                out.append(
                    Issue(
                        id="CONTENT",
                        message=t("Text placement {id} references item {item}, which contains artwork without text", id=pid, item=ref),
                        fix=t("Place graphic items with `art add --kind image / vector --content`"),
                    )
                )
        for p in self.placements:
            for ref in template_ids(p.text):
                item = self.content.get(ref) if self.content is not None else None
                if item is None:
                    out.append(
                        Issue(
                            id="CONTENT",
                            message=t("Placement {id} contains a reference to missing item {{{{{item}}}}}", id=p.id, item=ref),
                            fix=t("Run `pacxio content list <job>` to see item IDs or add the item with `content add`"),
                        )
                    )
                elif item.display_text() is None:
                    out.append(
                        Issue(
                            id="CONTENT",
                            message=t("Placement {id} contains a text reference to graphic item {item}, which has no text", id=p.id, item=ref),
                            fix=t("Place graphic items with `art add --kind image / vector --content`"),
                        )
                    )
            if self.content is not None:
                for ref, n, lines in bad_line_refs(p.text, self.content):
                    out.append(
                        Issue(
                            id="CONTENT",
                            message=t("Placement {id} references line {line} of item {item}, which has only {total} lines", id=p.id, item=ref, line=n, total=lines),
                            fix=t("Use {{{{{first}:1}}}}–{{{{{last}:{lines}}}}}, or use {{{{{item}}}}} for the entire item", first=ref, last=ref, lines=lines, item=ref),
                        )
                    )
        return out

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / JOB_FILE
        # 先写临时文件再一步换上：两处同时保存（工作台连点、agent 和页面同时改）也不会留下半截 / 两份拼起来的
        # job.json（2026-11-08 导入刀版页连点后出现过）
        tmp = path.with_name(f".{JOB_FILE}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(self.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path

    @staticmethod
    def load(directory: Path) -> Job:
        return Job.model_validate_json((directory / JOB_FILE).read_text(encoding="utf-8"))


def load_job_checked(directory: Path) -> tuple[Job | None, list[Issue]]:
    """读 job.json；失败时返回带修复建议的 Issue，而不是抛异常。"""
    try:
        return Job.load(directory), []
    except FileNotFoundError:
        return None, [
            Issue(
                id="JOB",
                message=t(
                    "{filename} not found in {directory}", filename=JOB_FILE, directory=directory
                ),
                fix=t("Run `pacxio job init <dir> ...` to create a job"),
            )
        ]
    except ValidationError as e:
        return None, [
            Issue(
                id="JOB",
                message=t(
                    "{filename} is invalid ({count} errors): ",
                    filename=JOB_FILE,
                    count=e.error_count(),
                )
                + t("; ").join(
                    f"{'.'.join(str(x) for x in err['loc']) or t('Entire file')} {validation_message(err)}"
                    for err in e.errors()[:3]
                ),
                fix=t(
                    "Correct the listed fields in job.json. Remove any extra text after the JSON object."
                ),
            )
        ]
