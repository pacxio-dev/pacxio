"""工厂配置档与材料预设（规格第 7 节）。所有"因厂而异"的数值只从这里读。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pacxio.messages import document_language, lang, t

# label_stock：不干胶标签（C4）；film_laminate：软包装复合膜（C5）
MaterialType = Literal["folding_carton", "corrugated", "label_stock", "film_laminate"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlueFlapRule(_Strict):
    ink: Literal["none", "bleed_only", "full"]
    bleed_into_mm: float


class IccRef(_Strict):
    path: str | None = None  # 工厂给的 ICC：嵌入生产文件、声明 PDF/X-4
    name: str | None = None
    # 没填 path 时按纸张自动选本机已有的标准 ICC（）：纸面 coated / uncoated →
    # 按顺序找的文件名；只用来把照片转成印刷色，不嵌入、不声明 PDF/X-4
    auto: dict[str, list[str]] = Field(default_factory=dict)
    search_dirs: list[str] = Field(default_factory=list)  # 去哪些文件夹找（~ 展开）


class FinishLimit(_Strict):
    min_line_mm: float
    min_gap_mm: float


class FinishRules(_Strict):
    foil: FinishLimit
    spot_uv: FinishLimit  # 常规局部 UV（丝印）
    # 数码 UV（喷墨，相机对位）：用户确认工厂能做、工艺标了 process=digital 时用（）；旧配置档没有时按常规 UV 查
    spot_uv_digital: FinishLimit | None = None
    emboss: FinishLimit
    white: FinishLimit | None = None  # 白墨（v0.2 补上；旧配置档没有时 F02 只提醒"未检查"）
    crease_clearance_mm: float
    # 工艺超出对应印刷图形多少算没对齐（F04，）；旧配置档没有时不做 F04
    registration_tolerance_mm: float | None = None


class WindowRules(_Strict):
    """开窗规则（ 节），全部【初值】。"""

    min_size_mm: float = Field(gt=0)
    min_corner_r_mm: float = Field(gt=0)
    default_corner_r_mm: float = Field(gt=0)
    edge_clearance_mm: float = Field(ge=0)
    min_web_mm: float = Field(ge=0)
    max_area_ratio: float = Field(gt=0, le=1)


class FlexoRules(_Strict):
    """柔印（瓦楞水印）覆盖的规则：job 的 print.method = flexo 时代替胶印的数值，全部【初值】。"""

    tac_limit_pct: float = Field(gt=0)
    min_text_pt: float = Field(gt=0)
    # 最细的线 / 反白（没墨的细缝、小白点）：柔印网线粗、版材软，比这细的容易糊或断（开槽箱测试 R-09）
    min_print_line_mm: float | None = Field(default=None, gt=0)
    min_print_reverse_mm: float | None = Field(default=None, gt=0)


class LabelRoll(_Strict):
    """卷标交付参数（规格书"卷标"一节）：全部【初值】，要标签厂确认（ 节）。"""

    unwind: int = Field(default=1, ge=1, le=8)  # 出标方向，标准 8 个方位
    core_mm: float = Field(default=76.0, gt=0)  # 卷芯内径
    max_roll_od_mm: float = Field(default=300.0, gt=0)  # 外径上限
    gap_mm: float = Field(default=3.0, ge=0)  # 标签间距（跳距 = 标签长 + 间距）
    across: int = Field(default=1, ge=1)  # 排数
    liner_margin_mm: float = Field(default=1.5, ge=0)  # 底纸两边留边


class LabelRules(_Strict):
    """标签（C4）的规则，全部【初值】。"""

    bleed_mm: float = Field(default=2.0, ge=0)
    safe_margin_mm: float = Field(default=2.0, ge=0)
    wrap_overlap_mm: float = Field(default=6.0, ge=0)  # 环标默认搭接
    wrap_tolerance_mm: float = Field(default=1.0, gt=0)  # L02：环标长度和瓶周长 + 搭接的允差
    panel_max_arc: float = Field(default=0.45, gt=0, le=1)  # 前 / 背标最宽占周长的比例
    min_corner_r_mm: float = Field(default=1.0, ge=0)  # L05：模切外形最小圆弧半径
    min_concave_r_mm: float = Field(default=2.0, ge=0)  # L05：内凹圆弧最小半径
    arc_fit_tol_mm: float = Field(default=0.05, gt=0)  # 椭圆 / 异形用双圆弧逼近的最大误差
    # L04：条码在圆柱上，条码沿圆周方向那条边对应的圆心角超过这个就提醒（GS1 资料待查）
    barcode_max_arc_deg: float = Field(default=30.0, gt=0)
    roll: LabelRoll = Field(default_factory=LabelRoll)


class FilmRoll(_Strict):
    """卷膜交付（自动包装机上制袋：三边封小袋、背封袋 / 条状包），全部【初值】，要印刷厂和包装机厂确认。"""

    unwind: int = Field(default=1, ge=1, le=8)  # 出卷方向，标准 8 个方位
    core_mm: float = Field(default=76.0, gt=0)  # 卷芯内径（3 英寸）
    max_roll_od_mm: float = Field(default=400.0, gt=0)
    max_splices: int = Field(default=2, ge=0)  # 每卷接头上限（接头处做标记）
    eye_w_mm: float = Field(default=10.0, gt=0)  # 光标（电眼色标）横向宽
    eye_h_mm: float = Field(default=5.0, gt=0)  # 光标沿走膜方向长
    eye_edge_mm: float = Field(default=2.0, ge=0)  # 光标离膜边（背封袋离中缝封）
    eye_lane_mm: float = Field(default=3.0, ge=0)  # 光标两边留空（电眼通道）
    eye_cmyk: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 100.0)
    lane_min_lum: float = Field(
        default=0.6, ge=0, le=1
    )  # PO07：通道里亮度低于这么多（0 黑 – 1 白）算深，电眼会误读


class PouchRules(_Strict):
    """软包装（C5，）的规则，全部【初值】，要制袋厂确认。"""

    bleed_mm: float = Field(default=3.0, ge=0)
    safe_margin_mm: float = Field(default=3.0, ge=0)  # 文字离封边内沿
    seal_small_mm: float = Field(default=5.0, gt=0)  # 封边宽：袋宽 ≤ seal_break_w 时
    seal_large_mm: float = Field(default=7.0, gt=0)
    seal_break_w: float = Field(default=90.0, gt=0)  # reference：86 → 5，100 → 7
    gusset_k: float = Field(default=0.3, gt=0)  # 自立袋底褶深 G = k·W + b（reference 两组拟合）
    gusset_b: float = -10.0
    zipper_top_mm: float = Field(default=13.0, ge=0)  # 拉链带离顶
    zipper_w_mm: float = Field(default=12.0, gt=0)
    tear_from_top_mm: float = Field(default=9.0, gt=0)  # 撕口离顶
    tear_size_mm: float = Field(default=3.0, gt=0)
    fin_seal_mm: float = Field(default=9.0, gt=0)  # 背封袋中缝封（每边）
    end_seal_mm: float = Field(default=12.0, gt=0)  # 背封袋上下封
    tolerance_mm: float = Field(default=1.5, ge=0)  # 制袋尺寸公差（规格书）
    # 八边封平底袋（reference 只给了排版，封宽没有数据）
    flat_depth_k: float = Field(default=0.4, gt=0)  # 不给底深时 = k × 正面宽
    flat_top_seal_mm: float = Field(default=10.0, gt=0)
    flat_bottom_seal_mm: float = Field(default=6.0, gt=0)  # 底片四边、袋身下沿
    flat_fin_mm: float = Field(default=10.0, gt=0)  # 背中缝每边（reference 10）
    flat_bottom_gap_mm: float = Field(default=10.0, gt=0)  # 底片和袋身分开排，中间空这么多
    corner_seal_mm: float = Field(default=3.0, ge=0)  # 竖角封（跨折线）
    valve_d_mm: float = Field(default=22.0, gt=0)  # 单向排气阀直径
    valve_gap_mm: float = Field(default=10.0, ge=0)  # 排气阀离拉链带 / 顶封
    # 吸嘴（自立袋）
    spout_d_mm: float = Field(default=10.0, gt=0)  # 吸嘴口径（常见 8.6 / 10 / 16 / 22）
    spout_base_k: float = Field(default=2.6, gt=0)  # 焊在袋口的船形底座宽 = k × 口径
    spout_base_h_mm: float = Field(default=12.0, gt=0)
    spout_corner_mm: float = Field(default=28.0, gt=0)  # 斜角吸嘴：切角直角边长
    # 手提孔（大袋：大米、宠物粮）
    handle_w_k: float = Field(default=0.4, gt=0)  # 孔宽 = k × 袋宽，不超过 handle_max_w_mm
    handle_max_w_mm: float = Field(default=90.0, gt=0)
    handle_h_mm: float = Field(default=20.0, gt=0)
    handle_top_mm: float = Field(default=15.0, ge=0)  # 孔上沿离顶
    handle_margin_mm: float = Field(default=8.0, ge=0)  # 孔四周的手提封
    # 透明窗、异形
    window_min_mm: float = Field(default=15.0, gt=0)
    shape_tol_mm: float = Field(default=0.05, gt=0)  # 异形外形转圆弧的误差
    roll: FilmRoll = Field(default_factory=FilmRoll)
    # 装满效果图与 PO05（第二步）
    fill_default: float = Field(default=0.7, ge=0, le=1)  # 不给鼓起量时
    puff_k: float = Field(default=0.25, gt=0)  # 每面最大鼓起 = fill·k·内容区短边
    base_k: float = Field(default=0.9, gt=0)  # 自立袋底 / 背封袋侧风琴撑开的半深 = fill·k·褶深
    stretch_warn: float = Field(default=0.08, gt=0)  # PO05：膜变形超过这么多
    small_text_pt: float = Field(default=8.0, gt=0)  # PO05：小于这个字号算小字


class AttachmentRules(_Strict):
    """配件（金属标）的检查规则（，），【初值】。"""

    fold_clearance_mm: float = Field(default=3.0, ge=0)


class DxfLayer(_Strict):
    name: str
    aci: int


class DxfConfig(_Strict):
    version: str
    layers: dict[str, DxfLayer]


class Cf2Config(_Strict):
    units: str
    linetypes: dict[str, int]


class CompensationSet(_Strict):
    """厚度补偿系数：补偿 = 系数 × 材料厚度 t（）。"""

    k_panel: float
    k_height: float
    k_lid: float = 0.0
    k_flap: float = 0.0


class Profile(_Strict):
    id: str
    name: str
    verified: bool
    units: Literal["mm"]
    bleed_mm: float
    safe_margin_mm: float
    glue_flap: GlueFlapRule
    tac_limit_pct: float
    # 【初值】最小字号（pt）：小于它给 T05 提醒（印刷可读性；法规对某些信息另有字高要求）
    min_text_pt: float = 6.0
    # 印刷图形最细的线 / 反白（mm）：不写不查；柔印一节有数值时 P03 查（开槽箱测试 R-09）
    min_print_line_mm: float | None = None
    min_print_reverse_mm: float | None = None
    icc: IccRef
    finish_rules: FinishRules
    window: WindowRules | None = None  # 没有这一节的配置档不能开窗（v0.2）
    attachment: AttachmentRules = Field(default_factory=AttachmentRules)  # 配件检查（）
    flexo: FlexoRules | None = None  # 柔印（瓦楞水印）的规则；没有时柔印也按胶印数值查（v0.2）
    labels: LabelRules | None = None  # 标签（C4）；没有这一节时用 LabelRules 的默认【初值】
    pouches: PouchRules | None = None  # 软包装（C5）；没有这一节时用 PouchRules 的默认【初值】
    spot_names: dict[str, str]
    dxf: DxfConfig
    cf2: Cf2Config
    compensation: dict[MaterialType, CompensationSet]
    deliverables: list[str]  # 放进交付包、发给工厂的（：默认生产文件、规格书、刀模 DXF）
    archive: list[str] = Field(default_factory=list)  # 只放进存档、自己留着的
    file_preferences: dict[str, str]
    # 交付件（刀版确认图、印前报告、规格书、效果图说明）的语言：auto 跟着命令行语言（PACXIO_LANG），
    # 也可以定成 en / zh，或 bilingual 中英对照；印刷内容本身从不翻译（）
    document_language: Literal["auto", "en", "zh", "bilingual"] = "auto"


class MaterialPreset(_Strict):
    name: str
    type: MaterialType
    caliper_mm: float = Field(gt=0)
    flute: str | None = None
    # 印刷面是不是涂布纸（按纸张自动选 ICC 用）；瓦楞不写：胶印裱纸按涂布、柔印水印按非涂布
    surface: Literal["coated", "uncoated"] | None = None
    # 膜类标签材料：clear 透明、metallized 镀铝（要白墨打底，L06）；纸类不写
    film: Literal["clear", "metallized", "white", "kraft"] | None = None


class Material(BaseModel):
    """job.json 中的材料：preset 解析后落成具体数值，job 自身可复现。"""

    preset: str | None = None
    type: MaterialType
    caliper_mm: float = Field(gt=0)
    flute: str | None = None
    # 瓦楞的面纸（开槽箱测试 R-01）：kraft 牛皮面纸（没墨的地方是牛皮色，油墨压在上面）| white 白面纸；
    # 不写 = 白（旧 job 照旧，确认指纹不变）
    liner: Literal["kraft", "white"] | None = None
    # 标签膜材（C4）：clear 透明 / metallized 镀铝 / white 白膜；纸和旧 job 不写（指纹不变）
    film: Literal["clear", "metallized", "white", "kraft"] | None = None


PROFILE_DIR_ENV = "PACXIO_PROFILE_DIR"


def profile_dirs() -> list[Path]:
    """配置档搜索路径：环境变量 > 当前目录 profiles/ > 源码仓库根的 profiles/ > 随包安装的副本。"""
    dirs: list[Path] = []
    env = os.environ.get(PROFILE_DIR_ENV)
    if env:
        dirs.append(Path(env))
    dirs.append(Path.cwd() / "profiles")
    dirs.append(Path(__file__).resolve().parents[3] / "profiles")  # 源码仓库（开发 / 可编辑安装）
    dirs.append(Path(__file__).resolve().parents[1] / "_profiles")  # 随 wheel 安装的副本
    return dirs


class ProfileNotFoundError(LookupError):
    pass


def _find(filename: str) -> Path:
    for d in profile_dirs():
        candidate = d / filename
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(d) for d in profile_dirs())
    raise ProfileNotFoundError(
        t("Could not find {filename} (searched: {paths})", filename=filename, paths=searched)
    )


def load_profile(profile_id: str) -> Profile:
    path = _find(f"{profile_id}.yaml")
    return Profile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def output_language(profile_id: str) -> str:
    """交付件（确认图、报告、规格书、效果图说明）的语言：工厂配置档的 document_language。

    auto 跟随 PACXIO_LANG；找不到配置档时也跟随它（缺配置档的错误由各命令自己报）。
    """
    try:
        return document_language(load_profile(profile_id).document_language)
    except ProfileNotFoundError:
        return lang()


def load_materials() -> dict[str, MaterialPreset]:
    raw = yaml.safe_load(_find("materials.yaml").read_text(encoding="utf-8"))
    return {k: MaterialPreset.model_validate(v) for k, v in raw.items()}


def resolve_material(preset: str) -> Material:
    presets = load_materials()
    if preset not in presets:
        raise KeyError(preset)
    p = presets[preset]
    return Material(preset=preset, type=p.type, caliper_mm=p.caliper_mm, flute=p.flute, film=p.film)
