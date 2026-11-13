"""版式简报（ 2.3）：把排版初稿写成给出图模型的一段文字。

只描述**信息怎么分配到各面、谁大谁小、哪里是密集小字、照片 / 图形在面的哪一块**，不写毫米坐标——
AI 守不住精确版式（第二次试验），构图交给它；精确位置在平面图阶段由引擎做准。
英文写（出图模型对英文提示词更稳）；短文字逐字给出，长段只说几行小字。
"""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any

from pacxio.concept.rectify import upright_turns
from pacxio.geometry import affine_geom, path_to_polygon
from pacxio.layout.errors import LayoutError
from pacxio.layout.spaces import body_strip, placement_world_box
from pacxio.mockup.fold import visible_panels
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.job import Box, Job, Placement, Window

if TYPE_CHECKING:  # 闭源（）：只用作类型
    from pacxio_pro.layout.build import LayoutResult

PART_EN = {"base": "BASE", "lid": "LID", "tray": "DRAWER TRAY", "sleeve": "SLEEVE"}
# 中日韩文字和全角标点：版式简报是英文的，出图提示词照抄简报，这些字符不该出现在占位框 / 图形的说明里
CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")
# 简报里由放置的标签写成的说明（占位框、图片、图形）；文字放置原样照抄是对的，不在此列
LABEL_PHRASE = re.compile(r": (area reserved for|image|graphic): ")
FACE_EN = {"front": "FRONT", "back": "BACK", "side_a": "LEFT SIDE", "side_b": "RIGHT SIDE",
           "back_b": "BACK LEFT HALF", "back_a": "BACK RIGHT HALF",
           "top": "TOP", "bottom": "BOTTOM", "side_a_in": "INSIDE LEFT END", "side_b_in": "INSIDE RIGHT END",
           "front_in": "INSIDE FRONT", "back_in": "INSIDE BACK",
           "top_lid": "TOP", "btm_lid": "BOTTOM", "base": "BOTTOM",
           "top_tuck": "FRONT LID FLAP",  # 飞机盒合上后的正面；简报标题只能用大写字母和空格
           "top_front": "TOP (front flap)", "top_back": "TOP (back flap)",
           "btm_front": "BOTTOM (front flap)", "btm_back": "BOTTOM (back flap)"}  # fmt: skip
STYLE_EN = {"rte": "reverse tuck end", "ste": "straight tuck end", "snaplock": "snap lock bottom (1-2-3 bottom)",
            "autolock": "auto lock bottom (crash lock)",
            "mailer_front_lock": "corrugated mailer with two front locking tabs and matching receiver slots",
            "mailer_tear_seal": "corrugated mailer with an adhesive front closure and a perforated tear strip",
            "mailer_tuck": "corrugated rolled-end mailer with a short inner front tuck (no outer locking ears)",
            "fefco_0427": "corrugated mailer box (FEFCO 0427, roll end tuck front)",
            "fefco_0201": "corrugated regular slotted shipping case (FEFCO 0201)",
            "gable": "gable carry box: two sloping roof panels rising to back-to-back handle panels with a hand hole, slotted end flaps, snap lock bottom",
            "counter_display": "counter display tray with a front cut-away and a tall double-layer header panel at the back",
            "label_panel": "self-adhesive product label on a bottle or jar",
            "label_wrap": "self-adhesive wrap-around label going all the way around a cylindrical bottle or jar",
            "label_cone": "self-adhesive wrap label on a tapered cup or conical container",
            "pouch_standup": "flexible stand-up pouch (doypack) with a bottom gusset",
            "sachet_3side": "flat three-side-seal flexible sachet",
            "pouch_backseal": "flexible pillow pouch with a vertical back seal (center fin seal)",
            "pouch_flat_bottom": "flat-bottom box pouch (eight-side seal) with side gussets, a square bottom and a top zipper",
            "carton": "folding carton (four-panel tube) with the chosen top and bottom closures",
            "crashlock_rsc": "corrugated shipping case with a pre-glued auto lock (crash lock) bottom and slotted top flaps",
            "tray_lid": "two-piece telescope box: a tray base with a separate lid",
            "drawer": "drawer box: a tray that slides into an open-ended sleeve"}  # fmt: skip
SHORT_TEXT_CHARS = 60  # 不超过这么多字的文字逐字写进简报
SHORT_TEXT_LINES = 3
SHORT_ITEMS, SHORT_ITEM_CHARS = 6, 40  # 几条短句（如一列卖点）逐条写出
NARROW_SHARE = 0.6  # 比面窄这么多的元素才说左 / 中 / 右
BANDS = ((0.8, "top"), (0.6, "upper part"), (0.4, "middle"), (0.2, "lower part"), (0.0, "bottom"))
DENSE_LINES = 15  # 一个面上文字行数达到这么多、而且以小字为主，就说是密集小字面
LARGE_PT, MEDIUM_PT = 14.0, 10.0
SPAN_MIN_MM = 3.0  # 跨面的图落在一个面上至少这么宽、这么高，才写进这个面（贴着折线的一窄条不算）
PICTURES = ("image", "vector", "placeholder")
# 没排东西的面、底面（初稿没给它排图片 / 插画时）：纯底色。底面默认纯色，客户要图案时在初稿里加占位框
# （STE 测试 S-03，2026-11-01 用户定）；以前写 "plain background (no text)"，平面图提示词又让 AI "照主视角的
# 设计语言设计"，底盖被画满插画
PLAIN_FACE = "plain solid background colour only: no text, no illustration, no pattern"
PLAIN_BOTTOM = "background: plain solid colour, no illustration or pattern"


def _band(b: Box, panel_h: float) -> str:
    c = (b.y + b.h / 2) / panel_h
    return next(name for limit, name in BANDS if c >= limit)


def _side(b: Box, panel_w: float) -> str:
    if b.w / panel_w >= NARROW_SHARE:
        return ""
    c = (b.x + b.w / 2) / panel_w
    return ", left" if c < 0.38 else (", right" if c > 0.62 else ", centered")


def _spans(placements: list[Placement], dl: Dieline) -> dict[str, dict[str, Box]]:
    """盒身一圈 / 整张展开图坐标的图（跨面的照片、插画、占位框）：{放置 id: {面: 落在这个面上那一块的
    面局部框}}，面按刀版里的顺序。简报按面写，这些图要拆到它跨过的每个面上，并说明是连续的一幅。"""
    out: dict[str, dict[str, Box]] = {}
    for p in placements:
        if p.box is None or p.coord_space == "panel" or p.kind not in PICTURES:
            continue
        world = placement_world_box(p, dl, 0.0)
        pieces: dict[str, Box] = {}
        for panel in dl.panels:
            if panel.id not in FACE_EN:
                continue
            piece = world.intersection(path_to_polygon(panel.polygon))
            if piece.is_empty:
                continue
            x0, y0, x1, y1 = affine_geom(piece, panel.frame.inverse()).bounds
            if min(x1 - x0, y1 - y0) >= SPAN_MIN_MM:
                pieces[panel.id] = Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)
        out[p.id] = pieces
    return out


def _across(faces: list[str]) -> str:
    names = [f"the {FACE_EN[f]}" for f in faces]
    return ", ".join(names[:-1]) + " and " + names[-1]


WINDOW_SHAPE_EN = {"rect": "rounded rectangle", "circle": "circle",
                   "obround": "stadium shape (rectangle with fully rounded ends)"}  # fmt: skip


def box_name(job: Job) -> str:
    """英文盒型名（卡纸盒加 folding carton；瓦楞盒型的名字里已经说了）。"""
    name = STYLE_EN.get(job.style, job.style)
    if job.material.type in ("corrugated", "label_stock", "film_laminate") or job.style in (
        "tray_lid",
        "drawer",
    ):
        return name
    return f"{name} folding carton"


def window_phrase(w: Window, panel_w: float, panel_h: float) -> str:
    """开窗的英文描述（相对大小与位置，同简报的写法）：概念图、背面视角、平面图都要画出这个窗口。"""
    film = "covered by clear transparent film" if w.film == "pet" else "open, no film"
    return (f"die-cut window ({WINDOW_SHAPE_EN[w.shape]}, about {w.box.w / panel_w:.0%} of the face width "
            f"and {w.box.h / panel_h:.0%} of its height, {_band(w.box, panel_h)}{_side(w.box, panel_w)}), {film}; "
            "the product inside shows through it - keep the window clear of text")  # fmt: skip


def window_lines(job: Job, dl: Dieline) -> list[str]:
    """给概念图提示词的结构说明：每个窗口一句。"""
    out = []
    for w in job.windows or []:
        x0, y0, x1, y1 = dl.panel(w.panel).polygon.bbox()
        out.append(
            f"The {FACE_EN.get(w.panel, w.panel)} has a {window_phrase(w, x1 - x0, y1 - y0)}."
        )
    return out


def _size_word(size: float, largest: float) -> str:
    if size >= largest - 1e-6:
        return "the largest text on the box"
    if size >= LARGE_PT:
        return "large text"
    if size >= MEDIUM_PT:
        return "medium text"
    return "small text"


def _text_phrase(text: str, lines: int, size: float, largest: float) -> str:
    word = _size_word(size, largest)
    items = [ln.strip() for ln in text.splitlines() if ln.strip()]
    flat = " / ".join(items)
    if len(flat) <= SHORT_TEXT_CHARS and lines <= SHORT_TEXT_LINES:
        return f'{word}: "{flat}"'
    if len(items) <= SHORT_ITEMS and all(len(ln) <= SHORT_ITEM_CHARS for ln in items):
        return f"{word}, {len(items)} short lines: " + "; ".join(f'"{ln}"' for ln in items)
    numbered = sum(1 for ln in items if ln.lstrip()[:1].isdigit())
    if numbered >= 3:
        return f"{word}: a numbered list of {numbered} items (about {lines} lines)"
    return f"{word}: a block of about {lines} lines"


def build_brief(
    job: Job, dl: Dieline, lay: LayoutResult, panels: list[str] | None = None, title_prefix: str = "",
    header: bool = True, largest: float | None = None, hidden: set[str] | None = None,
) -> str:  # fmt: skip
    """panels：要描述的面（默认合上看得到的面）；title_prefix：多部件盒型面标题前的部件名；header：写开头两行；
    largest：全盒最大的字号（多部件盒型按整套给，不给就取这个排版里的）；hidden：合上后看不到的面（多部件盒型
    的内托侧壁、内壁这些），标题里写明只在打开时看得到（抽屉测试 F-04）。"""
    resolved = job.resolved()
    texts = {t.placement.id: t for t in lay.texts}
    if largest is None:
        sizes = [t.placement.size_pt or 0.0 for t in lay.texts]
        largest = max(sizes) if sizes else 0.0
    q = job.box_dims()
    L, W, H = float(q["L"]), float(q["W"]), float(q["H"])
    logo_items = {
        it.id for it in (job.content.items if job.content else []) if it.category == "logo"
    }
    out = [
        "Layout brief: how the information is distributed over the faces of the box. Positions are "
        "approximate and the composition is up to the designer. Spell the short texts exactly; long "
        "texts may be drawn as blocks of small text.",
        f"Box: {box_name(job)}, {L:g} x {W:g} x {H:g} mm "
        f"({q.get('dim_basis', 'inner')}); front width : side depth : height = 1 : {W / L:.2f} : {H / L:.2f}.",
    ]
    same = job.same_faces or {}
    for target, source in same.items():
        out.append(f"The {FACE_EN.get(target, target)} is identical to the {FACE_EN.get(source, source)} "
                   "(same layout and content).")  # fmt: skip
    if not header:
        out = []
    spans = _spans(resolved.placements, dl)
    for panel in (dl.panel(pid) for pid in (panels if panels is not None else visible_panels(dl))):
        if panel.id in same:
            continue
        x0, y0, x1, y1 = panel.polygon.bbox()
        pw, ph = x1 - x0, y1 - y0
        if (
            upright_turns(panel) % 2
        ):  # 面在刀版上横躺着（天地盖的侧壁）：按正立的宽高说，和放置框一致
            pw, ph = ph, pw
        items = [(p, p.box) for p in resolved.placements
                 if p.box is not None and p.coord_space == "panel" and p.panel == panel.id
                 and p.kind in ("text", "image", "vector", "placeholder")]  # fmt: skip
        items += [(p, spans[p.id][panel.id]) for p in resolved.placements
                  if panel.id in spans.get(p.id, {})]  # fmt: skip
        items.sort(key=lambda it: -(it[1].y + it[1].h / 2))
        face_lines = [texts[p.id].layout.lines for p, _ in items if p.id in texts]
        face_sizes = [texts[p.id].placement.size_pt or 0.0 for p, _ in items if p.id in texts]
        dense = sum(face_lines) >= DENSE_LINES and face_sizes and median(face_sizes) < MEDIUM_PT
        seen = ", only seen when the box is opened" if panel.id in (hidden or set()) else ""
        title = f"{title_prefix}{FACE_EN.get(panel.id, panel.id)} ({pw:.0f} x {ph:.0f} mm{seen})"
        out.append(f"{title}: dense small-print information panel" if dense else f"{title}:")
        for w in job.windows or []:
            if w.panel == panel.id:
                out.append(f"- {window_phrase(w, pw, ph)}")
        if not items:
            out.append(f"- {PLAIN_FACE}")
        elif FACE_EN.get(panel.id) == "BOTTOM" and not any(
            p.kind in ("image", "placeholder") for p, _ in items
        ):  # 底面默认纯底色：初稿没给它排图片 / 插画，AI 也不要自己加（STE 测试 S-03，用户定）
            out.append(f"- {PLAIN_BOTTOM}")
        for p, b in items:
            where = _band(b, ph) + _side(b, pw)
            if p.kind == "text" and p.id in texts:
                t = texts[p.id]
                phrase = _text_phrase(
                    p.text or "", t.layout.lines, t.placement.size_pt or 0.0, largest
                )
                if t.layout.vertical:  # 竖排：AI 也要竖着画（天地盖测试 F-02）
                    phrase = phrase.replace(
                        ":", ", written VERTICALLY (top to bottom, columns from right to left):", 1
                    )
            elif p.kind == "placeholder":
                phrase = (f"area reserved for: {p.label_en or p.label or 'an image'}, about "
                          f"{b.w / pw:.0%} of the face width and {b.h / ph:.0%} of its height")  # fmt: skip
            elif p.content in logo_items:
                phrase = "brand logo (use the supplied logo artwork)"
            elif p.label or p.label_en or p.content:
                # tag 默认是清单类别的中文名（"卖点图标"），不进英文简报（开槽箱测试 R-03）
                tag = p.tag if p.tag and not CJK.search(p.tag) else None
                what = p.label_en or p.label or tag or p.content
                phrase = f"{'image' if p.kind == 'image' else 'graphic'}: {what}"
            else:  # 没写明是什么的装饰（分隔线、色带）不进简报：简报只讲信息怎么分配
                continue
            across = list(spans.get(p.id, {}))
            if len(across) > 1:
                phrase += f" (one continuous picture running across {_across(across)})"
            out.append(f"- {where}: {phrase}")
    return "\n".join(out)


def placeholder_targets(
    job: Job, dl: Dieline, dpi: int, job_dir: Path | None = None
) -> list[dict[str, Any]]:
    """每个占位框的目标尺寸：mm、宽高比、在 dpi 下要多少像素（出照片 / 插图用）；带裁切形状时
    给出形状文件与露出的比例（出图时主体要避开被裁掉的部分）。"""
    from pacxio.geometry import mm_to_px
    from pacxio.layout.spaces import box_polygon, placement_panels
    from pacxio_pro.layout.svgart import SvgArtError, load_svg_art

    out = []
    for p in job.placements:
        if p.kind != "placeholder" or p.box is None:
            continue
        w, h = p.box.w, p.box.h
        item: dict[str, Any] = {
            "id": p.id,
            "label": p.label,
            **({"label_en": p.label_en} if p.label_en else {}),
            "faces": placement_panels(p, dl),
            "size_mm": {"w": round(w, 2), "h": round(h, 2)},
            "aspect": f"{w / h:.3f}:1",
            "px": {"w": mm_to_px(w, dpi), "h": mm_to_px(h, dpi), "dpi": dpi},
        }  # fmt: skip
        if p.clip is not None:
            item["clip"] = p.clip
            if job_dir is not None:
                try:
                    shape = load_svg_art(job_dir / p.clip).region(p.box, "contain")
                    item["visible"] = round(
                        shape.intersection(box_polygon(p.box)).area / (w * h), 2
                    )
                except SvgArtError:
                    pass
        out.append(item)
    return out


def chinese_labels(job: Job) -> list[tuple[str, str]]:
    """会原样进英文版式简报、再进出图提示词的中文说明：(放置 id, 说明)。和 build_brief 的写法一致：
    先用英文说明 label_en，没有才用 label（中文 label 给人看，补上 label_en 就不算）；图片、图形
    有说明或 content 才进简报，最后才用 tag（Logo 写成 "brand logo"，不用说明）。"""
    logos = {it.id for it in (job.content.items if job.content else []) if it.category == "logo"}
    out = []
    for p in job.placements:
        described = p.label or p.label_en
        if p.kind == "placeholder":
            used = p.label_en or p.label
        elif p.kind in ("image", "vector") and p.content not in logos and (described or p.content):
            used = p.label_en or p.label or p.tag
        else:
            continue
        if used and CJK.search(used):
            out.append((p.id, used))
    return out


def chinese_brief_lines(brief: str) -> list[str]:
    """简报里由标签写成、却带中文的说明行（简报可能比 job.json 旧：改了标签没重跑 plan）。"""
    return [ln for ln in brief.splitlines() if LABEL_PHRASE.search(ln) and CJK.search(ln)]


def label_language_issue(job: Job, directory: Path | str) -> Issue | None:
    """有中文说明、又没有英文说明时给一条提醒（art add / set、plan 用）；出图提示词那一步直接拦下。"""
    found = chinese_labels(job)
    if not found:
        return None
    listed = "；".join(f"{pid}「{label}」" for pid, label in found)
    return Issue(
        id="LABEL_LANG",
        message=f"这些放置还没有英文说明，中文会原样进英文版式简报和出图提示词：{listed}",
        fix=f"补上英文说明（--label 的中文照旧给人看）：pacxio art set {directory} <id> "
        f'--label-en "English description"；补完重跑 pacxio plan {directory}',
    )


__all__ = [
    "build_brief", "chinese_brief_lines", "chinese_labels", "label_language_issue", "placeholder_targets",
]  # fmt: skip


def _pairs(dl: Dieline) -> list[tuple[str, str]]:
    """相邻两面：正面 + 它右边的面、背面 + 它右边的面（在展开图里相接，跨折线的图形在一张图里）。"""
    try:
        walls = list(body_strip(dl).walls)
    except LayoutError:
        return []
    out: list[tuple[str, str]] = []
    for main in ("front", "back"):
        if main in walls:
            i = walls.index(main)
            if i + 1 < len(walls):
                out.append((main, walls[i + 1]))
    return out


def body_walls(dl: Dieline) -> list[str]:
    """盒身一圈（三个面以上、在展开图里水平相接）从左到右的面；不是一圈的盒型返回空。
    四面整体平面图（`concept prompt --step flat --body`）照它画一张。"""
    try:
        walls = list(body_strip(dl).walls)
    except LayoutError:
        return []
    return walls if len(walls) > 2 else []
