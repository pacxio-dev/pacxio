"""Dieline 模型（规格第 6.2 节）：由 styles 生成，供导出与检查使用。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from pacxio.geometry import Affine, Path, Segment
from pacxio.messages import display_name

# 面 id → 中文名（说明文字、图例用）；多部件的面写成"部件.面"，查表时只看点后面
PANEL_ZH = {"front": "正面", "back": "背面", "side_a": "左侧面", "side_b": "右侧面",
            "top_lid": "顶面", "btm_lid": "底面", "base": "底面",
            "top_front": "顶面（前摇盖）", "top_back": "顶面（后摇盖）",
            "btm_front": "底面（前片）", "btm_back": "底面（后片）",
            "back_a": "背面左半", "back_b": "背面右半"}  # fmt: skip


# 多部件盒型部件里才有的面（抽屉外套的 top / bottom、天地盖的内壁）
PART_FACE_ZH = {"top": "顶面", "bottom": "底面", "front_in": "正面内壁", "back_in": "背面内壁",
                "side_a_in": "左侧内壁", "side_b_in": "右侧内壁"}  # fmt: skip


def face_zh(face: str) -> str:
    """面的中文名（部件里的局部面名也认得）。"""
    return PANEL_ZH.get(face) or PART_FACE_ZH.get(face, face)


def panel_zh(panel: str) -> str:
    part, _, face = panel.rpartition(".")
    name = PANEL_ZH.get(face, face)
    return f"{part}.{name}" if part else name


def panel_name(panel: str) -> str:
    """面名，按当前语言（交付件里给人看的；中文和 panel_zh 一样）。"""
    part, _, face = panel.rpartition(".")
    name = display_name(PANEL_ZH.get(face, face))
    return f"{part}.{name}" if part else name


def face_name(face: str) -> str:
    """面名（部件里的局部面名也认得），按当前语言；中文和 face_zh 一样。"""
    return display_name(face_zh(face))


OUTLINE_EDGE_ID = "cut_outline"  # 盒型生成的外轮廓切线的 id


class EdgeType(StrEnum):
    """线型词表（全项目统一）。"""

    CUT = "cut"
    CREASE = "crease"
    PERF = "perf"
    PARTIAL_CUT = "partial_cut"
    REVERSE_CREASE = "reverse_crease"


LINE_TYPE_ZH = {
    EdgeType.CUT: "刀线",
    EdgeType.CREASE: "压线",
    EdgeType.PERF: "齿线",
    EdgeType.PARTIAL_CUT: "半刀",
    EdgeType.REVERSE_CREASE: "反压线",
}


class PanelRole(StrEnum):
    BODY = "body"
    LID = "lid"
    TUCK = "tuck"
    DUST_FLAP = "dust_flap"
    GLUE_FLAP = "glue_flap"
    BOTTOM = "bottom"
    INSERT = "insert"
    GLUE_TAB = "glue_tab"  # 主翼上的糊胶三角（自动锁底），糊盒时里面粘到侧翼外面
    INNER_WALL = "inner_wall"  # 卷边翻进盒内的内侧板（飞机盒）：可印，打开盒子才看得到
    RIM = "rim"  # 卷边顶带（飞机盒）：可印，打开盒子才看得到
    LABEL = "label"  # 不干胶标签（C4）：只有刀线，贴到瓶 / 罐上


class Hinge(BaseModel):
    parent_panel: str
    edge_id: str


class Panel(BaseModel):
    id: str
    role: PanelRole
    polygon: Path
    # 面局部坐标 → 刀版坐标；定义面的"正立"方向（局部 +y）
    frame: Affine
    hinge: Hinge | None = None
    printable: bool = True
    # 正立方向尚未经打样确认时为 True（：M1 方向测试后定）
    orientation_provisional: bool = False


class Edge(BaseModel):
    id: str
    type: EdgeType
    segments: list[Segment]
    # 相邻的面；外轮廓切线只在一侧有面
    panels: list[str] = Field(default_factory=list)

    def is_segmented_perf(self) -> bool:
        """Explicit cut/bridge geometry must not be dashed a second time in previews."""
        return self.type == EdgeType.PERF and any(
            not a.end.is_close(b.start)
            for a, b in zip(self.segments, self.segments[1:], strict=False)
        )


class Zones(BaseModel):
    """出血 / 安全区 / 禁墨区 / 工艺可用区。M2 填充，M0 为空。"""

    bleed: Path | None = None
    safe: dict[str, Path] = Field(default_factory=dict)
    no_ink: list[Path] = Field(default_factory=list)
    finish_ok: dict[str, Path] = Field(default_factory=dict)


KeepClearKind = Literal[
    "seal",
    "zipper",
    "tear",
    "hang",
    "overlap",
    "gusset",
    "tuck",
    "valve",
    "spout",
    "handle",
    "window",
    "waste",
    "eyemark",
]
KEEP_CLEAR_ZH = {"seal": "热封边", "zipper": "拉链压合区", "tear": "撕口", "hang": "挂孔",
                 "overlap": "被盖住的搭接", "gusset": "底褶", "tuck": "风琴折进袋口的部分",
                 "valve": "排气阀", "spout": "吸嘴", "handle": "手提孔", "window": "透明窗（不印白墨）",
                 "waste": "裁掉的废边", "eyemark": "光标（电眼）通道"}  # fmt: skip
# 可以放文字的区（只是告诉工厂 / 设计师位置）：PO01 不查
TEXT_OK_KINDS = frozenset({"window"})


class KeepClear(BaseModel):
    """不放重要信息的区（C4 / C5）：图案、底色照常印，文字和条码不放（PO01 / LB02）。

    软袋的热封边、拉链压合区、撕口、挂孔，环标被盖住的搭接条。只是标注：刀版 PDF / DXF 的信息层、
    印刷 PDF 的不印图层，给制袋厂 / 标签厂看位置。"""

    id: str
    kind: KeepClearKind
    path: Path  # 闭合区域（刀版坐标）
    panel: str | None = None


class DielineWindow(BaseModel):
    """展开图坐标下的一个窗口：切线路径（闭合，也作为 CUT 线放在 edges 里）。"""

    id: str
    panel: str
    cut: Path
    film: bool = False  # 工厂会不会贴窗膜（只影响效果图与规格书；贴窗是工厂的后续工序）


class Dieline(BaseModel):
    style: str
    params: dict[str, Any]
    material_preset: str | None
    caliper_mm: float
    profile_id: str
    style_meta: dict[str, Any]
    bbox: tuple[float, float, float, float]
    panels: list[Panel]
    edges: list[Edge]
    zones: Zones = Field(default_factory=Zones)
    # 糊盒时涂胶的区域（在盒子里面、不印刷的一面），只用于标注：刀版 PDF 信息层、DXF GLUE 图层
    glue_areas: list[Path] = Field(default_factory=list)
    windows: list[DielineWindow] = Field(default_factory=list)  # 开窗（）
    # 不放重要信息的区（软袋封口、标签搭接，C4 / C5）；盒子为 None（序列化与结构基线不变）
    keep_clear: list[KeepClear] | None = None

    def edges_of(self, edge_type: EdgeType) -> list[Edge]:
        return [e for e in self.edges if e.type == edge_type]

    def outline_segments(self) -> list[Segment]:
        """外轮廓的线段。盒型把外轮廓放在 id 为 cut_outline 的切线里；内部切口（卡舌切口、
        以后的开窗）是另外的切线，不算外轮廓。没有 cut_outline 时把所有切线当外轮廓。"""
        named = [e for e in self.edges if e.id == OUTLINE_EDGE_ID]
        return [s for e in named or self.edges_of(EdgeType.CUT) for s in e.segments]

    def inner_cuts(self) -> list[Edge]:
        """外轮廓以外的切线（内部切口）。"""
        if not any(e.id == OUTLINE_EDGE_ID for e in self.edges):
            return []
        return [e for e in self.edges_of(EdgeType.CUT) if e.id != OUTLINE_EDGE_ID]

    def panel(self, panel_id: str) -> Panel:
        for p in self.panels:
            if p.id == panel_id:
                return p
        raise KeyError(panel_id)

    def structural_bbox(self, panel_id: str) -> tuple[float, float, float, float]:
        """面的结构包围盒（量尺寸、摆 3D 面、标注用）：插舌根部钩形切口围出的小卡位（归盖、伸过盖和插舌之间
        那条压线一层纸厚）不算，刀模圆角的内角补块按倒角前记下的算。"""
        finish = self.style_meta.get("corner_finish")
        rec = finish.get("structural_bbox", {}).get(panel_id) if isinstance(finish, dict) else None
        x0, y0, x1, y1 = rec if rec else self.panel(panel_id).polygon.bbox()
        hooks = {e.panels[1] for e in self.edges
                 if e.type == EdgeType.CUT and "tuck_slit" in e.id and e.panels[:1] == [panel_id]}  # fmt: skip
        if not hooks:
            return x0, y0, x1, y1
        tuck = next(iter(hooks))
        for e in self.edges:
            if e.type != EdgeType.CREASE or e.panels != [panel_id, tuck]:
                continue
            y = e.segments[0].start.y  # 盖和插舌之间的压线（横的）：卡位在它插舌那一侧
            _, ty0, _, ty1 = self.panel(e.panels[1]).polygon.bbox()
            if (ty0 + ty1) / 2 > y:
                y1 = min(y1, y)
            else:
                y0 = max(y0, y)
        return x0, y0, x1, y1
