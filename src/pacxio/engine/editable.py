"""结构的可编辑项（可编辑刀版引擎第一步，2026-11-07 用户“做可编辑”）。

给一个结构配方，回答“这个结构能改什么、改了动到哪儿”：

- **参数**：只列真正生效的——每个候选参数实际改一下、重新生成刀版，比较每块面、每条线；刀版不变的不列
  （旧兼容但不生效的字段不会被当成能改的，CLAUDE.md）。每项给当前值（没写的给“自动”和按规则算出的值，
  能量出来时）、范围 / 可选值、单位、改它动到哪些面和线；
- **依赖**：长宽高变了，哪些自动值跟着变；
- **结构**：面怎么连（哪块面通过哪条压线挂在哪块面上）、刀线 / 压线 / 齿线、锁合机构（哪块舌插进哪道缝、
  哪里糊胶、两端是什么封口）。

只读、无全局状态、不联网；改参数用 `structures update`（配方按层合并）。候选参数来自配方模型和这条路线的
拼装器参数（微调 tuning），一律按刀版实际变化判断，不手写清单。
"""

from __future__ import annotations

import copy
import math
import re
import types
import typing
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ValidationError

from pacxio.messages import t
from pacxio.model.dieline import Dieline, EdgeType
from pacxio.model.profile import Material, Profile

NUMERIC_STEP = 0.15  # 数值参数在当前值上下各试 15%
# 不知道现在多少时：按最小尺寸的这些比例试，再按最大尺寸的这些比例试
GUESS_FRACTIONS = (0.02, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3, 0.4)
GUESS_LARGE = (0.15, 0.2, 0.25, 0.3, 0.4)
ROUND = 4  # 比较几何时坐标保留的位数（0.1 µm）
DIM_STEP = 0.1  # 依赖：长宽高各加 10%
SKIP_FIELDS = {"kind", "L", "W", "H", "form", "base"}  # 换封口 / 换形状 / 尺寸另算：不算“参数”
PLAIN_SKIP = {
    "L",
    "W",
    "H",
    "dim_basis",
    "apply_compensation",
}  # 手写盒型：这些在“基础”里或不是结构参数
SECTIONS = ("body", "top", "bottom", "seam")
TOP_LEVEL = ("corner_radius",)
# 自动值从刀版上量：参数 → 那块面（挂在哪条压线上），量面离铰链最远多深 / 沿铰链多宽
AUTO_PANELS: dict[str, tuple[tuple[str, ...], str]] = {
    "tuck_len": (("{p}_tuck",), "depth"),
    "dust_depth": (("{p}_dust_a", "dust_{q}a", "{p}_dust_b"), "depth"),
    "wing": (("{p}_wing_a",), "depth"),
    "drop": (("{p}_back_drop", "{p}_front_drop", "{p}_side_a_drop"), "depth"),
    "seam.width": (("glue",), "depth"),
    "glue_w": (("glue",), "depth"),  # 手写彩盒的糊口宽
}


class Probe:
    """按参数生成刀版（失败返回原因）。"""

    def __init__(self, material: Material, profile: Profile, style_id: str = "structure") -> None:
        from pacxio.styles import get_style

        self.style = get_style(style_id)
        self.material, self.profile = material, profile
        self.count = 0
        self._memo: dict[str, Dieline | str] = {}  # 同一组参数只生成一次（试范围时会反复碰到）

    def run(self, recipe: dict[str, Any]) -> Dieline | str:
        import json

        key = json.dumps(recipe, sort_keys=True, default=str)
        if key not in self._memo:
            self._memo[key] = self._run(recipe)
        return self._memo[key]

    def _run(self, recipe: dict[str, Any]) -> Dieline | str:
        self.count += 1
        try:
            p = self.style.param_schema()(**recipe)
        except (ValidationError, ValueError) as e:
            return t("Invalid recipe: {detail}", detail=str(e).splitlines()[0])
        # 几何引擎的 validate 自己会试生成一次：这里只跑参数检查，生成一次、出错照样算“不成立”
        check = getattr(self.style, "_validate", None) or self.style.validate
        try:
            issues = check(p, self.material, self.profile)
        except Exception as e:  # noqa: BLE001  试值让校验本身出错（如套盒按外件定尺寸时外件不成形）：不成立
            return t("Could not build the structure: {error}", error=e)
        if issues:
            return issues[0].message
        from pacxio.dielines import style_parts
        from pacxio.preflight.rules import check_crease_sides, check_s01

        try:
            if style_parts(self.style, p):  # 多件套：每件一张，合成一张（id 加“部件.”）来比较
                gen = getattr(self.style, "generate_parts")  # noqa: B009  多部件盒型才有
                parts: dict[str, Dieline] = gen(p, self.material, self.profile)
            else:
                parts = {"": self.style.generate(p, self.material, self.profile)}
        except Exception as e:  # noqa: BLE001  校验漏掉的放不下（引擎会报 S03）：不成立
            return t("Could not build the structure: {error}", error=e)
        for dl in parts.values():
            try:
                bad = check_s01(dl) + check_crease_sides(dl)  # 外轮廓自交、压线两侧不对：刀版不成立
            except Exception as e:  # noqa: BLE001  线挂在不存在的面上之类：也是刀版不成立
                return t("Invalid dieline: {error}", error=e)
            if bad:
                return bad[0].message
        return parts[""] if "" in parts else combine(parts)


PARTS_KEY = "_parts"  # 合成刀版里存各部件刀版（只在内存里用）


def combine(parts: dict[str, Dieline]) -> Dieline:
    """多件套各件并排合成一张（面、线 id 加“部件.”前缀），只用来比较几何、给高亮坐标；比 DielineSet.merged
    少算外面看得到的面（那个要搭场景，试一次参数太慢）。"""
    from pacxio.dielines import PART_GAP_MM
    from pacxio.geometry import Affine

    panels, edges, glue = [], [], []
    x, height = 0.0, 0.0
    first = next(iter(parts.values()))
    for part, dl in parts.items():
        m = Affine.translate(x, 0.0)
        pre = f"{part}."
        for pn in dl.panels:
            hinge = pn.hinge.model_copy(update={"parent_panel": pre + pn.hinge.parent_panel,
                                                "edge_id": pre + pn.hinge.edge_id}) if pn.hinge else None  # fmt: skip
            panels.append(pn.model_copy(update={"id": pre + pn.id, "polygon": pn.polygon.transformed(m),
                                                "frame": m @ pn.frame, "hinge": hinge}))  # fmt: skip
        for e in dl.edges:
            edges.append(e.model_copy(update={"id": pre + e.id, "panels": [pre + q for q in e.panels],
                                              "segments": [m.apply_segment(sg) for sg in e.segments]}))  # fmt: skip
        glue += [g.transformed(m) for g in dl.glue_areas]
        x += dl.bbox[2] + PART_GAP_MM
        height = max(height, dl.bbox[3])
    return first.model_copy(update={
        "style_meta": {"structure": {"parts": True}, PARTS_KEY: parts}, "bbox": (0.0, 0.0, x - PART_GAP_MM, height),
        "panels": panels, "edges": edges, "glue_areas": glue,
    })  # fmt: skip


def _sig(dl: Dieline) -> dict[str, tuple[Any, ...]]:
    def segs(ss: Any) -> tuple[Any, ...]:
        return tuple(
            tuple(round(v, ROUND) for v in (s.start.x, s.start.y, s.end.x, s.end.y, s.bulge))
            for s in ss
        )

    out: dict[str, tuple[Any, ...]] = {f"panel:{q.id}": segs(q.polygon.segments) for q in dl.panels}
    out |= {f"edge:{e.id}": segs(e.segments) for e in dl.edges if e.id != "cut_outline"}
    out["glue"] = tuple(segs(g.segments) for g in dl.glue_areas)
    return out


def _shape_sig(dl: Dieline) -> dict[str, tuple[Any, ...]]:
    """只看形状不看位置：每块面、每条线各自平移到自己的左下角再比。改插舌长时下面的面整体挪了，
    挪动的不算“动到”，高亮只标形状真变了的。"""

    def segs(ss: Any) -> tuple[Any, ...]:
        ss = list(ss)
        if not ss:
            return ()
        x0 = min(min(q.start.x, q.end.x) for q in ss)
        y0 = min(min(q.start.y, q.end.y) for q in ss)
        return tuple(
            tuple(
                round(v, ROUND)
                for v in (q.start.x - x0, q.start.y - y0, q.end.x - x0, q.end.y - y0, q.bulge)
            )
            for q in ss
        )

    out: dict[str, tuple[Any, ...]] = {f"panel:{q.id}": segs(q.polygon.segments) for q in dl.panels}
    out |= {f"edge:{e.id}": segs(e.segments) for e in dl.edges if e.id != "cut_outline"}
    out["glue"] = tuple(segs(g.segments) for g in dl.glue_areas)
    return out


def _diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, list[str]]:
    keys = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    return {
        "panels": sorted(k[6:] for k in keys if k.startswith("panel:")),
        "edges": sorted(k[5:] for k in keys if k.startswith("edge:")),
        "glue": ["glue"] if "glue" in keys else [],
    }


def _changed(d: dict[str, list[str]]) -> bool:
    return any(d.values())


def _bounds(field: Any) -> tuple[float | None, float | None]:
    lo = hi = None
    for m in field.metadata:
        for name, side in (("gt", "lo"), ("ge", "lo"), ("lt", "hi"), ("le", "hi")):
            v = getattr(m, name, None)
            if v is not None:
                lo, hi = (v, hi) if side == "lo" else (lo, v)
    return lo, hi


def _kind(annotation: Any) -> tuple[str, list[Any]]:
    """字段类型 → ("number" | "int" | "bool" | "choice" | "", 可选值)。Optional 剥掉。"""
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _kind(args[0])
        return "", []
    if origin is Literal:
        return "choice", list(get_args(annotation))
    if annotation is bool:
        return "bool", [True, False]
    if annotation is int:
        return "int", []
    if annotation is float:
        return "number", []
    return "", []


def _get(recipe: dict[str, Any], path: str) -> Any:
    cur: Any = recipe
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set(recipe: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(recipe)
    cur = out
    keys = path.split(".")
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value
    return out


def _depth(dl: Dieline, pid: str) -> float | None:
    """面离它的铰链压线最远多深（插舌长、防尘翼深、糊口宽……）。"""
    try:
        panel = dl.panel(pid)
    except KeyError:
        return None
    if panel.hinge is None:
        return None
    edge = next((e for e in dl.edges if e.id == panel.hinge.edge_id), None)
    if edge is None or not edge.segments:
        return None
    a, b = edge.segments[0].start, edge.segments[-1].end
    n = math.hypot(b.x - a.x, b.y - a.y)
    if n < 1e-9:
        return None
    pts = [s.start for s in panel.polygon.segments]
    return round(
        max(abs((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)) / n for p in pts), 3
    )


def _auto(dl: Dieline, path: str) -> float | None:
    """没写的参数按规则用了多少：从刀版上量（量不出来返回 None）。"""
    section, _, name = path.rpartition(".")
    key = path if path in AUTO_PANELS else name
    if key not in AUTO_PANELS:
        return None
    ids, _how = AUTO_PANELS[key]
    if section in ("top", "bottom"):
        ends = [({"top": "top", "bottom": "btm"}[section], section[0])]
    else:  # 手写盒型的参数不分上下（两头共用一个值）：顶上量不到再量底下
        ends = [("top", "t"), ("btm", "b"), ("", "")]
    for p, q in ends:
        for pattern in ids:
            v = _depth(dl, pattern.format(p=p, q=q))
            if v is not None:
                return v
    return None


# 插舌盒一族（RTE / STE / 挂钩 RTE……）的参数 → 解析后尺寸（styles/rte.resolve 的 RteDims）的字段
RTE_DIMS = {"tuck_len": "tl", "tuck_inset": "ti", "tuck_radius": "tr", "dust_depth": "dd",
            "dust_offset": "off", "glue_w": "gw"}  # fmt: skip


def _style_dims(
    style: Any, recipe: dict[str, Any], material: Material, profile: Profile
) -> dict[str, float]:
    """手写盒型没写的参数按规则算出来是多少（刀版上量不出来的，如插舌圆角、插舌两边让位）。"""
    from pacxio.styles.rte import RteParams, RteStyle, resolve

    if not isinstance(style, RteStyle):
        return {}
    try:
        params = style.param_schema()(**recipe)
    except (ValidationError, ValueError, TypeError):
        return {}
    if not isinstance(params, RteParams):
        return {}
    dims = resolve(params, material, profile)
    return {k: round(float(getattr(dims, v)), 3) for k, v in RTE_DIMS.items()}


def _candidates(
    recipe: dict[str, Any], dl: Dieline, style: Any = None
) -> list[tuple[str, Any, str]]:
    """(路径, pydantic 字段, 出处 recipe / tuning / params)。手写盒型：它自己的参数（除尺寸、内外尺寸、补偿开关）。"""
    from pacxio.engine.recipe import Body

    if style is not None and style.id == "structure_set":  # 引擎多件套：每件一份配方
        out: list[tuple[str, Any, str]] = []
        master = set_master(recipe)
        for part in recipe.get("parts", {}):
            prefix = f"parts.{part}."
            out += [(prefix + path, field, "recipe") for path, field, _ in _recipe_candidates()]
            if part != master:  # 套在外面的件长宽跟着被套的件，高度是它自己的（盖高之类）
                out.append((prefix + "body.H", Body.model_fields["H"], "recipe"))
        return out
    if (
        style is not None and style.id != "structure"
    ):  # 手写盒型：它的参数；部件参数（地盒 / 盖……）展开一层
        out = []
        for name, field in style.param_schema().model_fields.items():
            if (
                name in PLAIN_SKIP
            ):  # 只跳过尺寸和开关；“base”在这里是部件（天地盖的地盒），不是配方的底面形状
                continue
            sub = field.annotation
            if isinstance(sub, type) and issubclass(sub, BaseModel):
                out += [(f"{name}.{k}", f, "params") for k, f in sub.model_fields.items()]
            else:
                out.append((name, field, "params"))
        return out
    out = list(_recipe_candidates())
    schema = _route_schema(dl)
    if schema is not None:
        mapped = _route_fields(recipe, dl)
        for name, field in schema.model_fields.items():
            if name not in mapped and name not in SKIP_FIELDS:
                out.append((f"tuning.{name}", field, "tuning"))
    return out


def _recipe_candidates() -> list[tuple[str, Any, str]]:
    from pacxio.engine.recipe import Body, End, Seam, StructureParams

    models: dict[str, type[BaseModel]] = {"body": Body, "top": End, "bottom": End, "seam": Seam}
    out: list[tuple[str, Any, str]] = []
    for section in SECTIONS:
        for name, field in models[section].model_fields.items():
            if name not in SKIP_FIELDS:
                out.append((f"{section}.{name}", field, "recipe"))
    for name in TOP_LEVEL:
        out.append((name, StructureParams.model_fields[name], "recipe"))
    return out


def set_master(recipe: dict[str, Any]) -> str | None:
    """引擎多件套里长宽高说了算的那件：第一件不靠别的件定长宽的（套盖的盒身、套筒的芯……）。"""
    fits = recipe.get("fits", {})
    return next((p for p in recipe.get("parts", {}) if p not in fits), None)


def _route_schema(dl: Dieline) -> type[BaseModel] | None:
    """这条路线的拼装器参数（微调 tuning 能给的名字）。"""
    from pacxio.styles import get_style

    info = dl.style_meta.get("structure", {})
    if info.get("calibrated_from"):
        return get_style(info["calibrated_from"]).param_schema()
    if info.get("assembled"):
        from pacxio.engine.slotted import SlottedAssembledParams

        return SlottedAssembledParams
    return None


ROUTE_MAPPED = {  # 配方已经管着的拼装器参数（不再当微调列）
    "L", "W", "H", "dim_basis", "apply_compensation", "top_end", "bottom_end", "top", "bottom",
    "joint_w", "glue_w", "joint_offset", "joint_wall", "joint_partition", "joint_center_open",
    "joint_full_overlap", "joint_free_tab", "joint", "window", "window_shape", "tear", "tuck_len",
    "dust_depth", "flap_overlap",
}  # fmt: skip


def _route_fields(recipe: dict[str, Any], dl: Dieline) -> set[str]:
    return ROUTE_MAPPED


def _num_probes(
    base: float | None,
    lo: float | None,
    hi: float | None,
    scale: tuple[float, float],
    integer: bool,
) -> list[float]:
    """不知道现在多少：按最小、最大尺寸的一串比例从小到大试，试到有两个成立的为止（见 describe）。"""
    if base is None or base == 0:
        small = [f * scale[0] for f in GUESS_FRACTIONS]
        guesses = small + [f * scale[1] for f in GUESS_LARGE] if not integer else [1, 2, 3, 4]
    else:
        guesses = [base * (1 - NUMERIC_STEP), base * (1 + NUMERIC_STEP)]
    out = []
    for g in guesses:
        if lo is not None:
            g = max(g, lo + (1e-6 if not integer else 0))
        if hi is not None:
            g = min(g, hi)
        out.append(round(g) if integer else round(g, 2))
    return list(dict.fromkeys(out))  # 去重，保持从小到大试的次序


WALL_FIELDS = ("on", "thumb")
END_MARKS = {"top": ("top_", "dust_t", "_top", "hanger", "roof_", "handle_", "end_a", "end_b"),
             "bottom": ("btm_", "dust_b", "_btm", "_bottom")}  # fmt: skip


def end_of(path: str) -> str | None:
    """参数属于顶端还是底端（多件套的 parts.盖.top.xxx 也认）。"""
    segs = path.split(".")[:-1]
    return next((x for x in reversed(segs) if x in END_MARKS), None)


def _owned(path: str, d: dict[str, list[str]]) -> bool:
    """top.* / bottom.* 的参数至少要动到这一端的东西（两端共用一个值时，另一端那份不算它的）。"""
    end = end_of(path)
    if end is None:
        return True
    other = END_MARKS["bottom" if end == "top" else "top"]
    ids = d["panels"] + d["edges"]
    return not ids or not all(any(m in i for m in other) for i in ids)


# 参数模型里没写说明的常用名字（通用说法；各盒型的具体含义以它的参数说明为准）
LABELS = {
    "base": "底的做法", "cols": "列数", "rows": "行数", "corner_bevel": "切角", "corner_depth": "角翼深",
    "corner_radius": "圆角半径", "corner_relief": "角上让位", "diag_crease": "对角压线", "diag_deg": "斜压线角度",
    "dust": "防尘翼", "dust_depth": "防尘翼深", "dust_offset": "防尘让位（侧墙缩进）", "dust_taper_deg": "防尘翼斜度",
    "ear_depth": "插耳深", "ear_gap": "插耳间隙", "ear_offset": "插耳位置", "end_overlap": "粘合翼重叠",
    "end_relief": "翼两边让位", "flap_gap": "两片外摇盖之间的缝", "folded": "出厂折好", "friction_lock": "插舌卡口",
    "friction_lock_depth": "卡口深", "friction_lock_pos": "卡口位置", "glue_band": "粘合区宽",
    "glue_bevel_deg": "糊口斜角", "glue_inset": "糊口让位", "glue_leg": "糊胶脚长", "glue_w": "糊口宽",
    "handle_h": "手提板高", "handle_hole_h": "提手孔高", "handle_hole_w": "提手孔宽", "hang_hole": "挂孔样式",
    "hanger_h": "挂板高", "hanger_radius": "挂板圆角", "hole_d": "孔径", "hole_h": "孔高", "hole_rise": "欧式孔顶凸起",
    "hole_shape": "孔形", "hole_top": "孔离上边", "hole_w": "孔宽", "hook": "钩", "inner_inset": "内板让位",
    "joint": "接头方式", "joint_bevel_deg": "接舌斜角", "joint_inset": "接舌让位", "joint_w": "接舌宽",
    "last_panel_short": "最后一面短多少", "lean_deg": "端翼斜度", "lid": "盖", "lid_clearance": "盖的松量",
    "lid_flap_depth": "盖侧翼深", "lid_flap_taper_deg": "盖侧翼斜度", "lid_h": "盖高", "lid_notch": "盖边缺口",
    "lid_notch_r": "缺口半径", "lock_slits": "锁缝", "lock_step": "锁扣台阶", "lock_w": "锁舌宽", "lug_len": "插脚长",
    "lug_w": "插脚宽", "major_depth": "主翼深", "major_taper_deg": "主翼斜度", "margin": "边距", "minor_depth": "侧翼深",
    "notch_d": "缺口深", "notch_on": "缺口在哪", "notch_r": "缺口半径", "notch_w": "缺口宽", "overlap": "重叠量",
    "pitch_x": "横向间距", "pitch_y": "纵向间距", "pull": "拉手", "roll_gap": "卷边让位", "roof_deg": "屋顶角",
    "shape": "形状", "sleeve_clearance": "套筒松量", "sleeve_len": "套筒长", "slot_clearance": "槽的松量",
    "slot_depth": "槽深", "slot_w": "开槽宽", "thumb_notch": "拇指缺口", "tongue_len": "锁舌长", "tongue_r": "锁舌圆角",
    "tongue_w": "锁舌宽", "tuck_ear_len": "插耳长", "tuck_inset": "插舌两边让位", "tuck_len": "插舌长", "tuck_r": "插舌圆角",
    "tuck_radius": "插舌圆角", "window_shape": "窗形",
}  # fmt: skip


def _label(path: str, field: Any) -> str:
    if field.description:  # 说明是英文原文，按当前语言换；取第一句
        return re.split(r"；|; ", t(str(field.description)))[0][:80]
    return LABELS.get(path.rsplit(".", 1)[-1], "")


def _unit(path: str) -> str:
    name = path.rsplit(".", 1)[-1]
    if name.endswith("_deg"):
        return "°"
    if name in ("on", "thumb", "steps", "sides", "base_holes", "hanger_layers"):
        return ""
    return "mm"


def describe(
    recipe: dict[str, Any], material: Material, profile: Profile, style_id: str = "structure"
) -> dict[str, Any]:
    """一个结构（几何引擎配方，或手写盒型的参数）能改什么、改了动到哪儿、结构怎么连。
    生成不了时 data["error"] 给原因。"""
    probe = Probe(material, profile, style_id)
    base = probe.run(recipe)
    if isinstance(base, str):
        return {"error": base}
    base_sig = _sig(base)
    base_shape = _shape_sig(base)
    base_route = route_name(base)
    dpaths = dims_paths(style_id, recipe)
    labels: dict[str, str] = {}
    try:
        from pacxio.dielines import style_part_labels, style_parts

        pobj = probe.style.param_schema()(**recipe)
        labels = {
            k: style_part_labels(probe.style, pobj).get(k, k)
            for k in style_parts(probe.style, pobj)
        }
    except (ValidationError, ValueError):
        pass
    sizes = [v for v in (_get(recipe, dpaths[k]) for k in ("L", "W", "H")) if v] or [100.0]
    scale = (min(sizes), max(sizes))
    params: list[dict[str, Any]] = []
    dims = base.style_meta.get("dims", {})
    resolved = _style_dims(probe.style, recipe, material, profile)
    for path, field, where in _candidates(recipe, base, probe.style):
        kind, options = _kind(field.annotation)
        name = path.rsplit(".", 1)[-1]
        if name in WALL_FIELDS:  # 挂在第几面墙：是“选哪面”，不是数值
            kind, options = "choice", [None, 0, 1, 2, 3]
        if not kind:
            continue
        current = _get(recipe, path)
        if path.startswith("tuning."):
            current = recipe.get("tuning", {}).get(path[7:])
        default = None if field.default is ... else field.default
        # 没写、但参数有固定默认值（如糊口宽 15）：就是这个值，不是“自动”
        fixed = current is None and kind in ("number", "int") and isinstance(default, (int, float))
        fixed = fixed and not isinstance(default, bool)
        auto = current is None and not fixed
        auto_value = _auto(base, path) if auto and kind in ("number", "int") else None
        if auto and auto_value is None and isinstance(dims.get(name), (int, float)):
            auto_value = round(float(dims[name]), 3)  # 拼装器记下的实际值（开槽宽、接舌宽……）
        if auto and auto_value is None and path in resolved:
            auto_value = resolved[path]  # 手写盒型按规则算出的值
        if kind in ("number", "int"):
            lo, hi = _bounds(field)
            seed = current if current is not None else (default if fixed else auto_value)
            tries = _num_probes(seed, lo, hi, scale, kind == "int")
        else:
            lo = hi = None
            tries = [o for o in options if o != (current if current is not None else default)]
        affects: dict[str, set[str]] = {"panels": set(), "edges": set(), "glue": set()}
        valid: list[Any] = []
        routes: dict[str, str] = {}
        adds = False  # 改了以后刀版上多出原来没有的面 / 线：这是加部件（墙顶折边、底板孔……），不是调尺寸
        for v in tries:
            dl = probe.run(_set(recipe, path, v))
            if isinstance(dl, str):
                continue
            sig = _sig(dl)
            d = _diff(base_sig, sig)
            if _changed(d) and _owned(path, d):
                valid.append(v)
                adds = adds or any(k not in base_sig for k in sig)
                if route_name(dl) != base_route:  # 换了生成路线（如离开已校准做法）：照列，但标出来
                    routes[str(v)] = route_name(dl)
                shape = _diff(base_shape, _shape_sig(dl))
                shown = shape if _changed(shape) else d  # 只是挪动位置的不算动到
                for k in affects:
                    affects[k] |= set(shown[k])
                if kind in ("number", "int") and len(valid) >= 2:
                    break  # 数值：试出两个能用的就够了（选项要一个个都试）
        if not valid:
            continue  # 改了刀版不变（或这组尺寸下改不了）：不算可编辑项
        item: dict[str, Any] = {
            "path": path,
            "where": where,
            "label": _label(path, field),
            "type": kind,
            # 做法选项没写时就是参数定义的默认值（不是“自动”）
            "value": current
            if current is not None
            else (default if fixed or kind not in ("number", "int") else None),
            "auto": auto and kind in ("number", "int"),
            "unit": _unit(path),
            "affects": {k: sorted(v) for k, v in affects.items() if v},
            "group": "size" if kind in ("number", "int") else "option",
        }
        if routes:
            item["changes_route"] = routes
        if adds and current is None:
            item["adds_parts"] = True  # 现在没有这个部件，给值就加上（改结构页“高级”里不列）
        ends = {e for e, marks in END_MARKS.items()
                for i in item["affects"].get("panels", []) + item["affects"].get("edges", [])
                if any(m in i for m in marks)}  # fmt: skip
        if (end_of(path) or "." not in path) and len(
            ends
        ) == 2:  # 手写盒型的参数不分上下：两头都动到就是共用
            item["both_ends"] = True  # 两端共用一个值（如校准过的插舌盒两头插舌一样长）
        if auto and auto_value is not None:
            item["auto_value"] = auto_value
        if kind in ("number", "int"):
            seed = (
                current
                if current is not None
                else (default if fixed else item.get("auto_value", valid[0]))
            )
            item["range"] = _valid_range(
                probe, recipe, path, float(seed or 0), lo, hi, kind == "int"
            )
            item["tried"] = valid
        else:
            item["options"] = sorted(
                {*(o for o in valid), current if current is not None else default},
                key=lambda o: (o is None, str(o)),
            )
        from pacxio.engine.editable_terms import option_names, term

        item |= term(path)
        comp = component_of(path, labels, style_id)
        if comp:  # 多件套：这项属于哪个部件（地盒 / 盖 / 外套 / 内托……）
            item["component"], item["component_name"] = (
                comp,
                labels.get(comp, comp),
            )  # 设计师说法：name / part / common（页面显示用，保存仍用 path）
        if not item["name"]:
            item["name"] = item["label"] or path.rsplit(".", 1)[-1]
        if "options" in item:
            item["option_names"] = option_names(path, item["options"])
        if routes and base_route.startswith("calibrated"):
            leave = [v for v, r in routes.items() if not r.startswith("calibrated")]
            if leave:  # 给人看的提醒：离开校准过的做法（成熟度变成标准款 / 自定义，要先打样）
                item["leaves_calibrated"] = leave
        params.append(item)
    return {
        "style": style_id,
        "route": route_name(base) if style_id == "structure" else f"style:{style_id}",
        "parameters": params,
        "dependencies": dependencies(recipe, base, params, probe, dims_paths(style_id, recipe)),
        "structure": structure(base),
        "probes": probe.count,
    }


RANGE_STEPS, RANGE_BISECT = 6, 5  # 往外每次乘 / 除 1.5 试 6 次，再二分 5 次
RANGE_GRID_MM = 0.5


def _valid_range(
    probe: Probe,
    recipe: dict[str, Any],
    path: str,
    seed: float,
    lo: float | None,
    hi: float | None,
    integer: bool,
) -> list[float | None]:
    """从当前值往小、往大试，找刀版仍成立（能生成、S01、压线两侧都过）的范围；不含机构核对（S07）。
    往外试到头还成立的那一边给 None（没找到上 / 下限，或参数定义自己的界）。"""

    def ok(v: float) -> bool:
        return not isinstance(
            probe.run(_set(recipe, path, round(v) if integer else round(v, 3))), str
        )

    if seed <= 0 or not ok(seed):
        return [lo, hi]
    out: list[float | None] = []
    for grow in (False, True):
        good, bad = seed, None
        v = seed
        for _ in range(RANGE_STEPS):
            v = v * 1.5 if grow else v / 1.5
            edge = hi if grow else lo
            if edge is not None and (v >= edge if grow else v <= edge):
                v = edge  # 碰到参数定义自己的界：界本身试一下（gt 的界会不成立，下面二分逼近）
                if ok(v):
                    good = v
                else:
                    bad = v
                break
            if ok(v):
                good = v
            else:
                bad = v
                break
        if bad is None:  # 往外试了几次都成立：贴着参数定义自己的界再试一次，没界就是没找到
            edge = hi if grow else lo
            if edge is None:
                out.append(None)
                continue
            near = edge - RANGE_GRID_MM if grow else edge + RANGE_GRID_MM
            if (near < good if grow else near > good) or ok(near):
                out.append(round(edge) if integer else edge)
                continue
            bad = near
        for _ in range(RANGE_BISECT):
            mid = (good + bad) / 2
            if ok(mid):
                good = mid
            else:
                bad = mid
        grid = 1.0 if integer else RANGE_GRID_MM
        edge = math.floor(good / grid) * grid if grow else math.ceil(good / grid) * grid
        out.append(round(edge, 2))
    return out


def route_name(dl: Dieline) -> str:
    info = dl.style_meta.get("structure", {})
    if info.get("calibrated_from"):
        return f"calibrated:{info['calibrated_from']}"
    for k in ("assembled", "tray", "sheet", "set", "parts"):
        if info.get(k):
            return k
    return "native"


# 手写多件套里放在最外层、其实只管一个部件的参数
COMPONENT_OF_FIELD = {
    "drawer": dict.fromkeys(("sleeve_clearance", "sleeve_len", "pull", "notch_on", "notch_r", "glue_w",
                             "glue_inset", "glue_bevel_deg"), "sleeve"),
    "tray_lid": dict.fromkeys(("lid_h", "lid_clearance", "lid_notch", "lid_notch_r"), "lid"),
}  # fmt: skip


def component_of(path: str, labels: dict[str, str], style_id: str = "") -> str | None:
    """参数属于哪个部件：parts.盖.xxx（引擎多件套）、lid.xxx（手写多件套的部件参数）、或按上表；整套共用的返回 None。"""
    segs = path.split(".")
    if segs[0] == "parts" and len(segs) > 2:
        return segs[1]
    if len(segs) > 1 and segs[0] in labels:
        return segs[0]
    return COMPONENT_OF_FIELD.get(style_id, {}).get(path)


def dims_paths(style_id: str, recipe: dict[str, Any]) -> dict[str, str]:
    """长宽高在参数里的路径：几何引擎结构在 body 里；引擎多件套在说了算的那件的 body 里；手写盒型在最外层。"""
    if style_id == "structure":
        return {k: f"body.{k}" for k in ("L", "W", "H")}
    if style_id == "structure_set":
        master = set_master(recipe)
        return {k: f"parts.{master}.body.{k}" for k in ("L", "W", "H")}
    return {k: k for k in ("L", "W", "H")}


def dependencies(
    recipe: dict[str, Any],
    base: Dieline,
    params: list[dict[str, Any]],
    probe: Probe,
    paths: dict[str, str],
) -> dict[str, list[str]]:
    """长宽高各加 10%：哪些自动值（从刀版上量的）跟着变。"""
    autos = [p["path"] for p in params if p.get("auto") and "auto_value" in p]
    out: dict[str, list[str]] = {}
    for dim in ("L", "W", "H"):
        path = paths[dim]
        v = _get(recipe, path)
        if not v:
            continue
        dl = probe.run(_set(recipe, path, round(v * (1 + DIM_STEP), 2)))
        if isinstance(dl, str):
            continue
        moved = [a for a in autos if _auto(dl, a) != _auto(base, a)]
        if moved:
            out[path] = moved
    return out


def structure(dl: Dieline) -> dict[str, Any]:
    """面怎么连、各种线、锁合机构。多件套按部件各算一遍，id 加“部件.”。"""
    parts = dl.style_meta.get(PARTS_KEY)
    if parts:
        out: dict[str, Any] = {
            "panels": [],
            "lines": {},
            "line_counts": {},
            "mechanisms": [],
            "crease_types": [],
        }
        for part, pdl in parts.items():
            sub = structure(pdl)
            pre = f"{part}."
            out["panels"] += [q | {"id": pre + q["id"], "parent": pre + q["parent"] if q["parent"] else None,
                                   "part": part} for q in sub["panels"]]  # fmt: skip
            for kind, items in sub["lines"].items():
                out["lines"].setdefault(kind, []).extend(
                    {"id": pre + e["id"], "panels": [pre + x for x in e["panels"]]} for e in items
                )
            for k, v in sub["line_counts"].items():
                out["line_counts"][k] = out["line_counts"].get(k, 0) + v
            for m in sub["mechanisms"]:
                m = dict(m, part=part)
                for key in ("tab", "slot_in"):
                    if key in m:
                        m[key] = pre + m[key]
                if "panels" in m:
                    m["panels"] = [pre + x for x in m["panels"]]
                out["mechanisms"].append(m)
            out["crease_types"] = sorted(set(out["crease_types"]) | set(sub["crease_types"]))
        return out
    panels = []
    for q in dl.panels:
        x0, y0, x1, y1 = q.polygon.bbox()
        panels.append({
            "id": q.id, "role": q.role.value, "size": [round(x1 - x0, 2), round(y1 - y0, 2)],
            "parent": q.hinge.parent_panel if q.hinge else None,
            "hinge": q.hinge.edge_id if q.hinge else None,
        })  # fmt: skip
    lines: dict[str, list[dict[str, Any]]] = {}
    for e in dl.edges:
        if e.id == "cut_outline":
            continue
        lines.setdefault(e.type.value, []).append({"id": e.id, "panels": e.panels})
    mechanisms: list[dict[str, Any]] = []
    info = dl.style_meta.get("structure", {})
    recipe = info.get("recipe", {})
    for end in ("top", "bottom"):
        spec = info.get(end) if isinstance(info.get(end), dict) else recipe.get(end)
        if isinstance(spec, dict) and spec.get("kind"):
            mechanisms.append({"type": "closure", "end": end, "kind": spec["kind"],
                               "on": spec.get("on")})  # fmt: skip
    from pacxio.engine import function_check

    folded = function_check.fold(dl)
    if folded is not None:
        for host, tab in sorted(function_check._slot_pairs(folded)):
            mechanisms.append({"type": "tab_slot", "tab": tab, "slot_in": host})
    glue_panels = [q.id for q in dl.panels if q.role.value in ("glue_flap", "glue_tab")]
    if glue_panels or dl.glue_areas:
        mechanisms.append({"type": "glue", "panels": glue_panels, "areas": len(dl.glue_areas)})
    counts = {k: len(v) for k, v in lines.items()}
    counts["outline"] = sum(1 for e in dl.edges if e.id == "cut_outline")
    return {"panels": panels, "lines": lines, "line_counts": counts, "mechanisms": mechanisms,
            "crease_types": sorted({e.type.value for e in dl.edges if e.type != EdgeType.CUT})}  # fmt: skip


__all__ = ["describe", "structure"]
