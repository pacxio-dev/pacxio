"""选定方向之后的出图提示词（ 2.4、2.5；references/concept.md"选定之后①/②"）。

`concept prompt <job> --step back|flat`：按骨架填好盒型尺寸、比例、版式简报（`plan/brief.txt` 里对应
面的段落）、参考图路径（`plan/pairs/`、`plan/faces/`）和选定的概念图，写到 job 的 `concept-prompts/`。
`--step main`：主视角概念图的提示词（和 `concept gen` 同一个骨架），只写文件、不出图——给 Codex 内置出图
或手工出图用。输入图是 SVG 时（Logo 常是）另存一张 PNG：有的出图工具读不了 SVG。

引擎不出图（核心不联网）：每个文件开头写明"先用 image_gen 出图、出完再导入"，并给出带
`--view` / `--faces` 的导入命令——导入后平铺确认页就能逐面对照。提示词里的面尺寸和拉正用的尺寸
（`rectify.face_rects`）是同一个来源，量出来的比例误差才有意义。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from pacxio.brief import FACE_EN, PART_EN, box_name, chinese_brief_lines, label_language_issue
from pacxio.concept.rectify import (
    CORNER_PAIRS,
    blank_strip,
    corner_pair,
    drawn_layout,
    face_rects,
    face_size,
    hinge_side,
    joined_stack,
    stack_axis,
)
from pacxio.concept.service import (
    FACE_ZH,
    ConceptMeta,
    _load,
    back_view_for,
    box_faces,
    concept_dir,
    no_flat_faces,
    read_chosen,
    read_meta,
    split_face_issues,
)
from pacxio.messages import lang, t
from pacxio.mockup.fold import build_box
from pacxio.model.common import Issue, Result
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.job import Job
from pacxio.visual.prompts import POUCH_SHAPES, liner_line, pouch_size

PROMPT_DIR = "concept-prompts"
INPUT_DIR = f"{PROMPT_DIR}/inputs"  # SVG 输入图转出来的 PNG
STEPS = ("main", "back", "flat")
# 平面图画布（，抽屉测试的验证）：出图接口的预设比例一定照给，像素尺寸有时被改成别的比例；画面铺满
# 画布，AI 没有自己定宽窄的白边可放，比例就跑不掉。画面和预设比例差得多时，多出的部分做成一侧的白条。
CANVAS_RATIOS = ((1, 1), (5, 4), (4, 3), (3, 2), (16, 9), (2, 1), (21, 9), (3, 1))  # 竖版取倒数
MAX_CANVAS_ASPECT = 3.0  # 预设比例最宽 3:1
BAND_MIN = 0.02  # 画面和预设比例差不到这么多就直接铺满（拉正时按真实尺寸拉回）【初值】
TRACE_PX_PER_MM = 8.0  # 平面图上的插画要描成矢量：每个面至少这么多像素 / mm，细线才描得清【初值】
SAME_WIDTH_MM = 0.5  # 面宽差不到这么多就说"等宽"
# 多部件盒型转角两面左右拼起来不比这更扁时按转角配对（高盖子：花纹绕转角；浅盖子 7:1 的细长条 AI 画不对，仍上下叠）
CORNER_PAIR_MAX_ASPECT = 2.0
SLUG = {"front": "front", "back": "back", "top_lid": "top", "btm_lid": "bottom"}
LIDS = ("top_lid", "btm_lid")
HEADER_RE = re.compile(r"^(?P<face>[A-Z][A-Z ]+?) \(")
EN_TO_FACES: dict[
    str, list[str]
] = {}  # 简报里的英文面名 → 所有对应的面 id（BOTTOM 既是 btm_lid 也是 base）
for _face, _en in FACE_EN.items():
    EN_TO_FACES.setdefault(_en, []).append(_face)
EN_TO_PART = {
    en: part for part, en in PART_EN.items()
}  # 多部件盒型简报的标题前缀：LID TOP → lid.top
# 多部件盒型的面写全名"部件.面"，面名就是合上后的位置（盖顶 top、地盒底 bottom……）；照单部件盒型的面 id
# 取英文名、中文名、照哪张 3D 图画
LOCAL_AS = {"top": "top_lid", "bottom": "btm_lid"}


def _local(face: str) -> str:
    local = face.split(".", 1)[-1]
    return LOCAL_AS.get(local, local) if "." in face else face


def _face_en(face: str) -> str:
    return FACE_EN.get(face) or FACE_EN.get(_local(face), face)


def _face_zh(face: str) -> str:
    return FACE_ZH.get(face) or FACE_ZH.get(_local(face), face)


STEP_NUMBER = {"main": 3, "back": 4, "flat": 5}
HOW_TO = (
    "# Generate one new image with the prompt below and attach the listed reference images.\n"
    "#   Run the import command after generation. --codex-latest imports the latest existing image; it does not generate one.\n"
    "#   Identical images are rejected (DUPLICATE). Tell the user how many images you will generate first."
)
SPELL = (
    "Spell headings and short texts exactly. Long paragraphs and numbered lists: follow the text blocks "
    "of the layout wireframe (their position, size and number of lines) - you may copy the wireframe "
    "text, or draw realistic small-text blocks. In quoted strings ' / ' means a line break."
)


@dataclass
class _Prompt:
    name: str  # 导入时的概念图编号
    title: str
    view: str
    faces: list[str]
    inputs: list[tuple[str, str]] = field(default_factory=list)  # (路径, 说明)
    body: list[str] = field(default_factory=list)
    canvas: Canvas | None = None  # 平面图的画布（）

    def image(self, path: str, what: str) -> int:
        self.inputs.append((path, what))
        return len(self.inputs)


def brief_sections(text: str) -> dict[str, list[str]]:
    """把 plan/brief.txt 按面切开：面 id → 这一面的段落（标题行 + 条目）。同一个英文面名对应几个面 id 时
    （BOTTOM：btm_lid / base / bottom），每个 id 都指向同一段。"""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        faces: list[str] = []
        if m:
            name = m.group("face")
            faces = EN_TO_FACES.get(name, [])
            for en, part in EN_TO_PART.items():  # 多部件盒型：LID TOP → lid.top
                if not faces and name.startswith(en + " "):
                    faces = [f"{part}.{f}" for f in EN_TO_FACES.get(name[len(en) + 1 :], [])]
        if faces:
            current = out.get(faces[0], [])
            for face in faces:
                out[face] = current
            current.append(line)
        elif current is not None and line.startswith("- "):
            current.append(line)
        else:
            current = None
    return out


def _section(face: str, sections: dict[str, list[str]], same: dict[str, str]) -> list[str]:
    if face in sections:
        return sections[face]
    src = same.get(face)
    if src in sections:  # 正反同款：简报里只写了原面
        return [f"{_face_en(face)} (identical to the {_face_en(src)}):",
                *sections[src][1:]]  # fmt: skip
    return [f"{_face_en(face)}:", "- (no layout brief for this face)"]


def _logo(job: Job, directory: Path) -> str | None:
    """Logo 原件：信息清单里 logo 条目的素材，或关联到它的放置的素材。"""
    items = [it for it in (job.content.items if job.content else []) if it.category == "logo"]
    ids = {it.id for it in items}
    paths = [it.asset for it in items if it.asset]
    paths += [p.source for p in job.placements if p.content in ids and p.source]
    return next((s for s in paths if (directory / s).is_file()), None)


def _viewable(directory: Path, rel: str) -> str:
    """输入图给出图工具看：SVG 另存一张白底 PNG（Codex 内置出图读不了 SVG），提示词里列 PNG；
    SVG 画不出来就照旧给 SVG。"""
    if Path(rel).suffix.lower() != ".svg":
        return rel
    try:  # SVG 解析在闭源的 layout 里（）：开源版画不出来就照旧给 SVG
        from pacxio_pro.layout.svgart import SvgArtError, load_svg_art, render_rgb
    except ImportError:
        return rel
    try:
        img = render_rgb(load_svg_art(directory / rel))
    except SvgArtError:
        return rel
    out = Path(INPUT_DIR) / f"{Path(rel).stem}.png"
    (directory / out).parent.mkdir(parents=True, exist_ok=True)
    img.save(directory / out)
    return out.as_posix()


def _free_name(directory: Path, base: str, first: int | None) -> str:
    """还没用过的概念图编号：base + first（back1、back2…）；first 为 None 时先试 base 本身。"""
    n = first
    while True:
        name = f"{base}{'' if n is None else n}"
        if not (concept_dir(directory) / f"{name}.png").exists():
            return name
        n = 2 if n is None else n + 1


@dataclass(frozen=True)
class Canvas:
    """平面图的画布：预设比例 + 画面占画布的比例（< 1 时另一侧是白条）+ 分辨率档。"""

    ratio: tuple[int, int]  # 宽 : 高
    art: float  # 画面占画布宽（band="right"）或高（band="bottom"）的比例；1 = 铺满
    band: str | None  # 白条在哪一侧：bottom / right；None = 铺满
    tier: str  # 1k / 2k / 4k
    need_px: int  # 描插画要的长边像素

    @property
    def name(self) -> str:
        return f"{self.ratio[0]}:{self.ratio[1]}"

    @property
    def orient(self) -> str:
        w, h = self.ratio
        return "landscape" if w > h else ("portrait" if h > w else "square")

    def frac(self, along: str, f: float) -> float:
        """画面里的位置比例 → 画布里的位置比例（along：x 宽 / y 高）。"""
        cut = (along == "x" and self.band == "right") or (along == "y" and self.band == "bottom")
        return f * self.art if cut else f


def _tier_long(tier: str, aspect: float) -> float:
    """每档出图的长边像素（gpt-image-2，按接口文档的表估）。"""
    a = max(aspect, 1 / aspect)
    if tier == "1k":
        return math.sqrt(1.57e6 * a)
    if tier == "2k":
        return 2048.0 if a < 2 else 2688.0
    return 2880.0 if a < 1.3 else 3840.0


def flat_canvas(w_mm: float, h_mm: float) -> Canvas:
    """画面 w × h mm → 画布：最接近的预设比例（竖的取倒数），差 BAND_MIN 以上时多出的做成白条；
    分辨率档取描插画够用（TRACE_PX_PER_MM）的最小一档（：大盒子出 4k）。"""
    aspect = w_mm / h_mm
    options = [(a, b) for a, b in CANVAS_RATIOS] + [(b, a) for a, b in CANVAS_RATIOS if a != b]
    ratio = min(options, key=lambda r: abs(math.log(aspect / (r[0] / r[1]))))
    preset = ratio[0] / ratio[1]
    band: str | None = None
    art = 1.0
    if abs(math.log(aspect / preset)) > BAND_MIN:
        band, art = ("bottom", preset / aspect) if aspect > preset else ("right", aspect / preset)
    need = round(max(w_mm, h_mm) * TRACE_PX_PER_MM)

    def art_width(t: str) -> float:  # 这一档画面宽多少像素
        long = _tier_long(t, preset)
        width = long if preset >= 1 else long * preset
        return width * (art if band == "right" else 1.0)

    tier = next((t for t in ("1k", "2k") if art_width(t) >= w_mm * TRACE_PX_PER_MM), "4k")
    return Canvas(ratio, round(art, 4), band, tier, need)


def _canvas_lines(cv: Canvas) -> tuple[str, str]:
    """提示词第一行（画布）和画面怎么占画布的一句。"""
    first = (f"OUTPUT FORMAT REQUIREMENT: generate a {cv.name} {cv.orient} image. This is an aspect-ratio-"
             "critical packaging drawing.")  # fmt: skip
    if cv.band is None:
        fill = ("The printed artwork FILLS THE WHOLE CANVAS edge to edge: no white margin, no border, no frame "
                "and no background around it - the canvas IS the printed sheet.")  # fmt: skip
    elif cv.band == "bottom":
        fill = (f"The printed artwork fills the full width of the canvas, from the top edge down to exactly "
                f"{cv.art:.1%} of the canvas height; the bottom {1 - cv.art:.1%} is a plain pure-white strip that is "
                "NOT part of the carton - nothing is drawn there. No other margin, border or frame.")  # fmt: skip
    else:
        fill = (f"The printed artwork fills the full height of the canvas, from the left edge to exactly "
                f"{cv.art:.1%} of the canvas width; the right {1 - cv.art:.1%} is a plain pure-white strip that is "
                "NOT part of the carton - nothing is drawn there. No other margin, border or frame.")  # fmt: skip
    return first, fill


def _orient(cw: int, ch: int) -> str:
    return "landscape" if cw > ch else ("portrait" if ch > cw else "square")


def _mm(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _shape(w: float, h: float) -> str:
    if h > w * 1.05:
        return "the box is clearly TALLER than wide"
    if w > h * 1.05:
        return "the box is clearly WIDER than tall"
    return "the box is about as tall as wide"


def _body_order(dl: Dieline) -> list[str]:
    body = sorted(
        (p for p in dl.panels if p.role == PanelRole.BODY), key=lambda p: p.polygon.bbox()[0]
    )
    return [p.id for p in body]


def _back_prompt(
    job: Job, dl: Dieline, chosen: ConceptMeta, name: str, sections: dict[str, list[str]], logo: str | None,
    design: str | None, faces: dict[str, str] | None = None, bottom: str | None = None,
    top: str | None = "top_lid",
) -> _Prompt:  # fmt: skip
    """faces：多部件盒型合上后的位置 → "部件.面"（盖的背面是 lid.back……）；尺寸用合上后的外形。
    top：顶面的面 id；两片摇盖拼成的顶（开槽箱）是 None，不列进这张图画到的面（开槽箱测试 R-05）。
    bottom：盒底的面 id（单独成面的才有）。扁平盒的主视角是俯拍顶面，背面视角就从底下看：底面占大部分
    画面，再带上背面和左侧（抽屉测试 F-12：产品信息常在底面，以前背面视角又拍了一遍顶面、底面从没给人看过）。"""
    from pacxio.concept.service import _box_dims
    from pacxio.visual.prompts import is_flat_box

    if faces:
        L, W, H = _box_dims(job)
        basis = "outer"
    else:
        dims = job.box_dims()
        L, W, H = float(dims["L"]), float(dims["W"]), float(dims["H"])
        basis = str(dims["dim_basis"])
    at = faces or {
        "back": "back",
        "side_a": "side_a",
        "side_b": "side_b",
        "front": "front",
        **({"top": top} if top else {}),
    }
    pouch = job.style in POUCH_SHAPES
    back_ids = [at["back"]]
    below = bottom if not pouch and is_flat_box((L, W, H)) else None
    if pouch:
        model = build_box(dl)
        at = {face.key: face.panel for face in model.faces}
        back_ids = [at["back"], *model.face("back").also]
        rear_faces = [*back_ids, *([at["side_a"]] if "side_a" in at else [])]
        p = _Prompt(name, t("Pouch rear 3D view"), "back", rear_faces)
    elif below is not None:
        p = _Prompt(
            name,
            t("Bottom, back and left 3D view (from below)"),
            "back",
            [below, at["back"], at["side_a"]],
        )
    else:
        p = _Prompt(name, t("Back and left 3D view"), "back",
                    [at["back"], at["side_a"], *([at["top"]] if "top" in at else [])])  # fmt: skip
    same = job.same_faces or {}
    main = p.image(chosen.file, t("Chosen design {id}", id=chosen.id))
    body = [
        "Use case: product-mockup, "
        + ("view from BELOW" if below is not None else "REAR view")
        + " of an already chosen design.",
        f"Input image {main} ({chosen.file}) is the CHOSEN DESIGN seen from the front. Keep exactly the same "
        "design language: colors, typography style, graphic motifs, photo style, "
        f"{'film' if pouch else 'paper'}, scene and lighting.",
    ]
    body += liner_line(job.material.liner)  # 牛皮面纸（开槽箱测试 R-01）
    if design:
        body.append(f"Design language: {design}")
    if logo:
        n = p.image(logo, t("Original logo artwork"))
        body.append(f"Input image {n} ({logo}) is the real brand logo artwork; reproduce it faithfully "
                    "wherever the logo appears.")  # fmt: skip
    camera = (
        "turned upside down so that its BOTTOM panel faces up toward the camera, seen in a high "
        "three-quarter view from the back-left: the BOTTOM fills most of the image; the BACK face and the "
        "LEFT SIDE panel (the side panel that is on the left when looking at the front) are visible but "
        "foreshortened. Proportions: back width : side depth : height = "
        if below is not None
        else "from behind: a rear three-quarter view showing the BACK face and, to its right, the LEFT SIDE "
        "panel (the side panel that is on the left when looking at the front), plus the top. Proportions: "
        "back width : side depth : height = "
    )
    if pouch:
        info = dl.style_meta["pouch"]
        depth = float(info.get("depth", info.get("gusset", 0)))
        shape = POUCH_SHAPES[job.style]
        if job.style == "pouch_backseal" and info.get("seam") == "corner":
            shape = "a flexible side-gusset pouch with a corner seam and heat-sealed ends"
        body += [
            "",
            f"Show the SAME sealed pouch from the rear: {shape}.",
            pouch_size(job.style, (L, W, depth), "back"),
            "Keep the pouch upright and the back facing the camera. Preserve the flexible film, seals "
            "and any closures from the reference. Full product with quiet margins, landscape 3:2.",
        ]
        if len(back_ids) > 1:
            body.append("The two back halves meet at a vertical center seam.")
        if "side_a" in at:
            body.append("Show the left side gusset at a shallow angle.")
        body.append("")
    else:
        body += [
            "",
            f"Show the SAME closed {box_name(job)}, {basis} {L:g} x {W:g} x {H:g} mm, {camera}"
            f"1 : {W / L:.2f} : {H / L:.2f} - {_shape(L, H)}; keep these proportions. Full product with quiet "
            "margins, landscape 3:2.",
            *_back_structure(job, faces),
            "",
        ]
    if below is not None:
        body += ["The BOTTOM contains (layout brief):", *_section(below, sections, same), ""]
    if len(back_ids) == 1 and same.get(at["back"]) == at["front"]:
        body.append(
            f"The BACK is identical to the FRONT in image {main} (same layout and content)."
        )
    else:
        body.append("The BACK contains (layout brief):")
        for face in back_ids:
            body.extend(_section(face, sections, same))
    if "side_a" in at and same.get(at["side_a"]) == at["side_b"]:
        body.append(f"The LEFT SIDE is identical to the RIGHT SIDE in image {main}.")
    elif "side_a" in at:
        body += [
            "The LEFT SIDE contains (layout brief; positions approximate, composition up to the designer; "
            "in quoted strings ' / ' means a line break):",
            *_section(at["side_a"], sections, same),
            "",
            f"Design the left side in the same visual language as the front and right side of image {main}.",
        ]
    # 成段的小字只给了"大约几行"：AI 会自己编地址、食用方法这类内容（抽屉测试 F-14），要它画成看不清的小字行
    body.append("Spell short texts exactly. Long texts (blocks of about N lines): draw them as rows of tiny, "
                "illegible grey text lines - do NOT invent readable wording such as addresses, instructions, "
                "company names or claims.")  # fmt: skip
    p.body = body
    return p


def _back_structure(job: Job, faces: dict[str, str] | None) -> list[str]:
    """背面视角的结构说明：抽屉盒的外套两头开口，背面看到的是内托后壁（抽屉测试 F-08）。"""
    if job.style != "drawer" or not faces:
        return []
    return ["The sleeve is OPEN at both ends and the drawer is fully closed: the BACK face is the back wall "
            "of the inner drawer tray, seen inside the open back end of the sleeve and framed by the thin "
            "edges of the sleeve around it."]  # fmt: skip


def _closed_width_notes(dl: Dieline, faces: list[str], names: list[str]) -> list[str]:
    """平面图按合上后看到的宽度画（和效果图同一个口径），印刷的那块面窄一点时写清楚，免得两个数对不上
    （LUMERA 测试 ISSUE-002：飞机盒正面看到的是整个底板宽，插舌本身窄两层纸厚）。"""
    rects, out = face_rects(dl), []
    for face, en in zip(faces, names, strict=True):
        if face not in rects:
            continue
        panel = dl.panel(face)
        seen = face_size(rects[face], panel)
        x0, y0, x1, y1 = panel.polygon.bbox()
        own = face_size((x0, y0, x1, y1), panel)
        if abs(seen[0] - own[0]) > 0.5:
            edge = (seen[0] - own[0]) / 2
            out.append(f"The {en} panel is drawn {_mm(seen[0])} mm wide as seen on the closed box; the printed "
                       f"panel itself is {_mm(own[0])} mm, so the outer {_mm(abs(edge))} mm at its left and right "
                       "ends is the edge of the box: keep it plain background, no text or logo there.")  # fmt: skip
    return out


def _notch_line(dl: Dieline, face: str, edge: str) -> str | None:
    """抽屉外套前沿的半圆拉手缺口：平面图要画出来、字避开（抽屉测试 F-06）。edge：前沿在图里哪一边。"""
    notch = (dl.style_meta.get("set") or {}).get("pull_notch")
    if not isinstance(notch, dict) or face not in notch.get("faces", []):
        return None
    r = float(notch["r"])
    w, h = face_size(face_rects(dl)[face], dl.panel(face))
    return (f"The FRONT edge of the {_face_en(face)} panel (the {edge} edge of the drawing) has a semicircular "
            f"finger notch cut out at its center (radius {_mm(r)} mm: about {2 * r / w:.0%} of the panel width "
            f"wide and {r / h:.0%} of its height deep, as in input image 1): draw it as a white half-circle "
            "cut into that edge and keep all text, labels and the logo away from it.")  # fmt: skip


def _part_note(part: str | None, face: str) -> str:
    """多部件盒型的侧壁：3D 图里这一侧是盖沿和下面的盒子叠在一起，只画这个部件的那一条。"""
    if part is None or face == "top_lid" or part == "sleeve":  # 抽屉外套：整面都是它
        return ""
    if part == "tray":  # 抽屉内托的前后脸：在外套开口里看到的那面墙（抽屉测试 F-10）
        return (" It is the wall of the inner drawer tray, seen inside the open end of the sleeve: draw only the "
                "wall itself, without the sleeve edges around it; follow input image 1 for what is on it.")  # fmt: skip
    if part == "lid":
        return (" Only the LID's rim: the upper band of that side in the 3D image. The band below it belongs "
                "to the BASE box, a separate part - do not draw it. The badge, title and other artwork of the "
                "lid TOP are not on this rim; follow input image 1 for what is on it.")  # fmt: skip
    return (f" Only the {PART_EN.get(part, part.upper())} part of that side (in the 3D image the lid rim covers "
            "its upper part); follow input image 1 for what is on it.")  # fmt: skip


def _look(
    face: str,
    main: int,
    back: int | None,
    same: dict[str, str],
    back_faces: list[str] | None = None,
) -> str:
    """这个面该照哪张 3D 图的样子画。back_faces：背面视角画到的面（扁平盒从底下看时有底面）。"""
    en = _face_en(face)
    src = same.get(face)
    part = face.split(".", 1)[0] if "." in face else None
    full, face = face, _local(face)  # 多部件：lid.top → top_lid（照主视角的顶面画）
    if src is not None:
        return (f"The {en} is identical to the {_face_en(src)} (same layout and content); "
                f"take it from input image {main}, flattened.")  # fmt: skip
    if face in ("front", "side_b", "top_lid"):
        where = {"front": "front face", "side_b": "right side", "top_lid": "top face"}[face]
        return (
            f"The {en} must look like the {where} of input image {main}, flattened."
            + _part_note(part, face)
        )
    if face in ("back", "side_a") and back is not None:
        where = "back face" if face == "back" else "left side"
        return (f"The {en} must look like the {where} of input image {back} (the approved rear view), "
                "flattened.") + _part_note(part, face)  # fmt: skip
    if back is not None and full in (back_faces or []):  # 扁平盒：背面视角是从底下看的
        return f"The {en} must look like the bottom face of input image {back} (the approved view from below), flattened."
    # 3D 图里看不到的面（底盖）：以前写"照主视角的设计语言设计"，AI 画满插画（STE 测试 S-03）；
    # 底面默认纯底色，初稿给它排了插画占位时简报里会写
    return (f"The {en} is not visible in the 3D images: use the background colour of input image {main} "
            "and draw only what the layout brief lists for it - no extra illustration or pattern.")  # fmt: skip


def _flat_prompt(
    job: Job, dl: Dieline, chosen: ConceptMeta, back: ConceptMeta | None, faces: list[str], name: str,
    ref: str, sections: dict[str, list[str]], logo: str | None, design: str | None,
    face_refs: dict[str, str] | None = None, separate: bool = False,
) -> _Prompt:  # fmt: skip
    """separate：各面各自成画（盒身一圈 / 两面一张时图形不跨折线）。"""
    layout = drawn_layout(dl, faces)  # 抽屉外套 左侧 | 顶 | 右侧 照刀版画成一条：侧面躺着
    sizes = [size for size, _ in layout]
    axis = stack_axis(dl, faces)  # y：等宽的面正着上下叠（天地盖侧壁、抽屉内托前后脸）
    if axis == "y":
        total_w, height = max(w for w, _ in sizes), sum(h for _, h in sizes)
    else:
        total_w, height = sum(w for w, _ in sizes), max(h for _, h in sizes)
    strip_mp = blank_strip(dl, faces)
    ring = len(faces) > 2 and not strip_mp  # 盒身一圈画在一张里
    cv = flat_canvas(total_w, height)
    same = job.same_faces or {}
    names = [_face_en(f) for f in faces]
    joiner = " / " if axis == "y" else (" | " if ring or strip_mp else " + ")
    title = joiner.join(_face_zh(f) if lang() == "zh" else _face_en(f) for f in faces)
    p = _Prompt(
        name, t("Unfolded view ({title})", title=t("Body wrap: {title}", title=title) if ring else title), "flat", faces, canvas=cv
    )
    wire = p.image(ref, t("Layout wireframe: actual proportions and content placement"))  # 第一张：模型照它分格
    main = p.image(chosen.file, t("Chosen design {id}, front and right 3D view", id=chosen.id))
    below = back is not None and any(
        _local(f) == "btm_lid" or f == "base" for f in back.faces or []
    )
    back_what = t("Bottom, back and left 3D view (from below)") if below else t("Back and left 3D view")
    back_n = p.image(back.file, t("Rear reference {id}: {view}", id=back.id, view=back_what)) if back else None
    logo_n = p.image(logo, t("Original logo artwork")) if logo else None
    # 画布要求放第一行、结尾再核一遍：只写在中间时，Codex 连出两张仍是 1024 x 1536（2026-10-21）
    first, fill = _canvas_lines(cv)
    body = [
        first,
        "",
        "Use case: flat packaging artwork - a straight-on DESIGN DRAWING of the printed panels, exactly as "
        "printed on the flat unfolded carton. NOT a photo, NOT a mockup: no perspective, no lighting or "
        "shadows, no scene, no paper texture, no box edges, no flaps, no glue tab, no dieline, no crop marks.",
        fill,
        *liner_line(job.material.liner, flat=True),
    ]  # fmt: skip
    if len(faces) > 1:
        sizes_txt = ", ".join(
            f"{n} {_mm(w)} x {_mm(h)} mm" for n, (w, h) in zip(names, sizes, strict=True)
        )
        acc, cuts = 0.0, []
        for w, h in sizes[:-1]:
            acc += h if axis == "y" else w
            cuts.append(f"{cv.frac(axis, acc / (height if axis == 'y' else total_w)):.1%}")
        if axis == "y" and joined_stack(
            dl, faces
        ):  # 合上后连着的面（飞机盒的盖和插舌）：跨折线连着画
            order = "; ".join(f"{n} (position {i + 1} from the top)" for i, n in enumerate(names))
            body += [
                "These panels are CONNECTED surfaces of the closed box, each drawn UPRIGHT as seen on the box "
                f"(text upright), stacked from top to bottom with NO gap: {order}. On the closed box the bottom "
                f"edge of the {names[0]} is the same edge as the top edge of the {names[1]} (a fold).",
                f"Panel sizes: {sizes_txt}. Each panel spans the full width of the artwork.",
                f"Draw the fold as ONE thin straight horizontal hairline at exactly {_and(cuts)} of the canvas "
                "height, from the left edge to the right edge of the artwork (it is used to measure the panels). "
                "Graphics that cross this edge on the closed box (rings, bands, gradients) must continue across "
                "the hairline without a break, as in the chosen design.",
            ]  # fmt: skip
        elif axis == "y":
            body += [
                "These panels are SEPARATE walls of the box, each drawn UPRIGHT as seen standing in front of it "
                "(sky and mountain tops up, things growing upward, text upright), stacked from top to bottom with "
                "NO gap: " + "; ".join(f"{n} (position {i + 1} from the top)" for i, n in enumerate(names)) + ".",
                f"Panel sizes: {sizes_txt}. Each panel spans the full width of the artwork.",
                f"Separate the panels with ONE thin straight horizontal hairline at exactly {_and(cuts)} of the "
                "canvas height, from the left edge to the right edge of the artwork (it is used to measure the "
                "panels). Graphics do not continue across these hairlines.",
            ]  # fmt: skip
        else:
            part = faces[0].split(".", 1)[0]
            ring_txt = (
                f"In the unfolded {PART_EN.get(part, part).lower()} the panels run "
                + " | ".join(names) + ", exactly as printed on the flat carton (input image 1)."
                if strip_mp
                else f"These two walls of the {PART_EN.get(part, part).lower()} meet at a vertical CORNER of the "
                f"closed box: the right edge of the {names[0]} is the same edge as the left edge of the {names[1]}. "
                "Draw them as that corner looks unfolded flat, both upright."
                if corner_pair(dl, faces)
                else "In the unfolded carton the body panels run "
                + " | ".join(_face_en(f) for f in _body_order(dl)) + "."
            )  # fmt: skip
            widest = max(range(len(sizes)), key=lambda i: sizes[i][0])
            rel = [f"the {n} is {w / sizes[widest][0]:.0%} as wide as the {names[widest]}"
                   for i, (n, (w, _)) in enumerate(zip(names, sizes, strict=True))
                   if i != widest and abs(w - sizes[widest][0]) >= SAME_WIDTH_MM]  # fmt: skip
            body += [
                ring_txt,
                "Draw " + " and ".join(f"the {n} panel ({pos})" for n, pos in zip(names, _positions(len(faces)), strict=True))
                + f" side by side, touching along the vertical fold{'s' if len(faces) > 2 else ''} between them.",
                f"Panel sizes: {sizes_txt}; all panels have the same height."
                + (f" Relative widths (measure them): {_and(rel)}." if rel else ""),
                f"Draw {'each' if len(cuts) > 1 else 'the'} fold as ONE thin straight vertical hairline at exactly "
                f"{_and(cuts)} of the canvas width, from the top edge to the bottom edge of the artwork (it is used "
                "to measure the panels); no other lines that are not part of the design.",
            ]  # fmt: skip
        for en, (_, turn) in zip(names, layout, strict=True):  # 躺着画的面：上边朝哪、字怎么走
            if turn % 2:
                side, run = (
                    ("right", "top to bottom") if turn % 4 == 1 else ("left", "bottom to top")
                )
                body.append(f"The {en} panel lies sideways in this unfolded strip, as in input image {wire}: its top "
                            f"edge is on the {side} (the fold to the next panel). Its text is turned with the panel: "
                            f"the letters lie on their side with their tops toward the {side} and the line runs from "
                            f"{run} - do NOT write it as upright characters stacked in a vertical column.")  # fmt: skip
        tops = [
            f
            for f, (_, turn) in zip(faces, layout, strict=True)
            if _local(f) == "top_lid" and turn == 0
        ]
        if strip_mp and tops:
            body.append(f"The {_face_en(tops[0])} is upright: the bottom edge of the strip is the FRONT end of "
                        "the box (the top is read from the front).")  # fmt: skip
            notch = _notch_line(dl, tops[0], "bottom")
            if notch:
                body.append(notch)
    else:
        (w, h), face = sizes[0], faces[0]
        shape = "a square" if abs(w - h) < 0.5 else f"aspect {w / h:.2f} : 1"
        body.append(f"Draw the {names[0]} panel only: {_mm(w)} x {_mm(h)} mm ({shape}).")
        hinge = hinge_side(dl, face)
        if (
            dl.style_meta.get("parts") and _local(face) == "top_lid"
        ):  # 盖顶：从正面读正（和效果图一样）
            body.append(f"Orientation: upright as in input image {wire} (the layout wireframe); the bottom edge "
                        "of the drawing is the FRONT edge of the box (the top is read from the front).")  # fmt: skip
        elif hinge is not None:
            body.append(f"Orientation: upright as in input image {wire} (the layout wireframe); the {hinge[0]} "
                        f"edge of the drawing is the fold to the {_face_en(hinge[1])} panel.")  # fmt: skip
        notch = _notch_line(dl, face, "bottom")  # 抽屉外套顶 / 底的前沿都在图的下边
        if notch:
            body.append(notch)
    body += _closed_width_notes(dl, faces, names)
    body += [
        f"Input image {wire} ({ref}) is the layout wireframe with the EXACT panel proportions and the real "
        "text: follow which information goes where and the size of each text block; do not copy its grey "
        "styling or grey boxes. The small captions inside the grey boxes are instructions about what to "
        "draw there - never draw these captions as text.",
        f"Input image {main} ({chosen.file}) is the CHOSEN DESIGN in 3D (front, right side and top).",
    ]
    if back is not None and back_n is not None:
        body.append(
            f"Input image {back_n} ({back.file}) is the approved view from below (bottom, back and left side)."
            if below
            else f"Input image {back_n} ({back.file}) is the approved rear view (back, left side and top)."
        )
    if logo_n is not None:
        body.append(
            f"Input image {logo_n} ({logo}) is the real brand logo artwork; reproduce it faithfully."
        )
    for f in faces:  # 选定设计里这一面拉正后的样子：照它的构图画
        path = (face_refs or {}).get(f)
        if path is not None:
            n = p.image(path, t("Chosen design, {face} straightened from the 3D view (match composition, whitespace and line weight)", face=_face_zh(f) if lang() == "zh" else _face_en(f)))
            body.append(
                f"Input image {n} ({path}) is the {_face_en(f)} of the chosen design, straightened from the 3D "
                "image: the artwork of this face must reproduce its composition - where each element sits, "
                "how much is left empty, the line weight and tone. Redraw it sharp at full size; do not add "
                "more illustration than it has."
            )
    body.append("Keep exactly the design language of the chosen design.")
    body.append(
        "Keep the density of the chosen design: areas that are empty or pale in it stay empty or pale; "
        "do not fill the panels with extra illustration."
    )
    if design:
        body.append(f"Design language: {design}")
    body += [_look(f, main, back_n, same, back.faces if back else None) for f in faces]
    if axis == "x" and len(faces) > 1 and separate:
        body.append(
            "The panels are SEPARATE designs: nothing crosses the folds; each panel is composed on its own."
        )
    elif ring:
        body += [
            "This is the WHOLE BODY of the box in ONE connected strip: design it as one continuous wide "
            "artwork, not as separate panels. Graphics that cross the folds must be continuous across them "
            "(same height and direction on both sides); do not restart or end lines at a fold.",
            f"SEAMLESS WRAP: when the box is folded, the far RIGHT edge ({names[-1]}) meets the far LEFT edge "
            f"({names[0]}) at the glue seam. Graphics running off one outer edge must continue at the other: "
            "same colors, same heights and same directions at both outer edges, so no seam shows.",
        ]
    elif axis == "x" and len(faces) > 2:
        body.append(
            "Graphics that cross the folds between the panels must be continuous across them."
        )
    elif axis == "x" and len(faces) > 1:
        body.append("Graphics that cross the fold between the panels must be continuous across it.")
    body.append("Information (layout brief):")
    for f in faces:
        body += _section(f, sections, same)
    body.append(SPELL)
    aspect = total_w / height
    shape = f"{aspect:.0%} as wide as tall" if aspect < 1 else f"{aspect:.2f} times as wide as tall"
    where = ("fills the whole canvas edge to edge" if cv.band is None
             else f"fills the canvas except the plain white strip on the {cv.band}")  # fmt: skip
    body += [
        "",
        f"FINAL GEOMETRY CHECK BEFORE OUTPUT: a {cv.name} canvas. The printed artwork is {shape} "
        f"({_mm(total_w)} : {_mm(height)}) and {where}; the panel boundaries are at the positions given above. "
        "Repaint the first image's diagram with the approved design; do not reshape the diagram.",
    ]
    p.body = body
    return p


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _positions(n: int) -> list[str]:
    return ["left", "right"] if n == 2 else [f"position {i + 1} from the left" for i in range(n)]


def _flat_groups(dl: Dieline) -> list[list[str]]:
    """单部件盒型：相邻两面一张（正面 + 右侧、背面 + 左侧），顶、底各一张（盒身一圈一张在 make_prompts 里定）。
    多部件盒型（面写全名）：盒型声明的刀版条（抽屉外套 左 | 顶 | 右）一张；同一部件里一样宽的前 / 后、
    右 / 左正着上下叠一张（天地盖盖子侧壁、抽屉内托前后脸）；其余各一张（：细长的面要夹在别的面和
    画布边之间，单独放、四周留白缝会被画扁）。"""
    from pacxio.brief import _pairs

    at = dl.style_meta.get("set_faces")
    if isinstance(at, dict):
        skip = no_flat_faces(dl)
        faces = {k: str(v) for k, v in at.items() if str(v) not in skip}
        part = {k: v.split(".", 1)[0] for k, v in faces.items()}
        groups: list[list[str]] = []
        # 盒型声明照刀版拼成一条的面（抽屉外套 左 | 顶 | 右：渐变、色带绕着走，）
        for strip in (dl.style_meta.get("set") or {}).get("flat_strips") or []:
            names = [str(f) for f in strip]
            if all(f in faces.values() for f in names) and blank_strip(dl, names):
                groups.append(names)
        used = {f for g in groups for f in g}
        rects = face_rects(dl)
        panels = {pn.id: pn for pn in dl.panels}
        # 合上后在转角相邻的两面（正面 + 右侧、背面 + 左侧），拼起来不太扁就左右并排一张：花纹能绕过转角
        # （2026-11-11 用户定，customer 香薰盒的高盖子；浅盖子拼起来是细长条，下面照 上下叠）
        for a, b in CORNER_PAIRS:
            if a in faces and b in faces and part[a] == part[b] and not {faces[a], faces[b]} & used:
                wa, ha = face_size(rects[faces[a]], panels[faces[a]])
                wb, hb = face_size(rects[faces[b]], panels[faces[b]])
                if (wa + wb) / max(ha, hb) <= CORNER_PAIR_MAX_ASPECT:
                    groups.append([faces[a], faces[b]])
        used = {f for g in groups for f in g}
        # 刀版上不相接的一对（前 / 后、右 / 左）：同一部件、一样宽就正着上下叠（：天地盖侧壁、
        # 抽屉内托前后脸；以前合上后相邻的两面左右拼，7:1 的细长条 AI 画不对）
        for a, b in (("front", "back"), ("side_b", "side_a")):
            if a in faces and b in faces and part[a] == part[b] and not {faces[a], faces[b]} & used:
                wa = face_size(rects[faces[a]], panels[faces[a]])[0]
                wb = face_size(rects[faces[b]], panels[faces[b]])[0]
                if abs(wa - wb) < SAME_WIDTH_MM:
                    groups.append([faces[a], faces[b]])
        order = ("front", "side_b", "back", "side_a", "top", "bottom")
        out: list[list[str]] = []
        for k in (k for k in order if k in faces):
            g = next((g for g in groups if faces[k] in g), [faces[k]])
            if g not in out:
                out.append(g)
        return out
    stacks = [list(s.get("faces", [])) for s in dl.style_meta.get("flat_stacks") or []]
    if stacks:  # 盒型声明的上下叠（飞机盒，LUMERA 测试 ISSUE-004）；其余面各一张
        seen = [f.panel for f in build_box(dl).faces if not f.parts]
        groups = [g for g in stacks if all(f in seen for f in g)]
        done = {f for g in groups for f in g}
        return groups + [[f] for f in seen if f not in done]
    pairs = [list(pr) for pr in _pairs(dl)]
    done = {f for pr in pairs for f in pr}
    singles = [[f.panel] for f in build_box(dl).faces if not f.parts and f.panel not in done]
    return (
        pairs + singles
    )  # 不是盒身一圈的盒型（飞机盒）每个面一张；两半拼的面（锁底、开槽箱顶底）不出


def _render(p: _Prompt, step: str, directory: Path, chosen: str, provisional: bool = True) -> str:
    inputs = _prompt_inputs(p)
    cmd = (f"pacxio concept import {directory} --codex-latest --name {p.name} --view {p.view} "
           f"--faces {','.join(p.faces)}")  # fmt: skip
    head = [
        t("# Workflow v2.1, step {step}: {title}, based on chosen design {chosen} (generated by `pacxio concept prompt`)", step=STEP_NUMBER[step], title=p.title, chosen=chosen),
        t(HOW_TO),
        t("# Input images: {inputs}", inputs=inputs),
        t("# Import: {command}", command=cmd),
    ]
    if step == "back":
        head.insert(3, "# 画布：1536 × 1024（横版）")  # 出图插件按它定尺寸（抽屉测试 F-09）
        head.append(
            t("# Review the appearance of these panels here. Use the later unfolded views for proportions. Do not run concept choose on this image.")
        )
    else:
        head.append(t("# This image includes text and is for reference only. Do not run concept choose on it. Once the set is ready, compare it to the two 3D views, list differences and get the user's approval before using it for the final artwork."))  # fmt: skip
        head.append(t("# Rebuild the final artwork from these views: vectorize illustrations, colour blocks and contours with `restore strip` then `visual trace --lines`. Remove AI-drawn text and typeset it in pacxio. Use hand-drawn SVG for simple icons when it gives a cleaner result (references/vector.md)."))  # fmt: skip
        cv = p.canvas
        if cv is not None:
            where = ("画面铺满" if cv.band is None else
                     f"画面占{'上面' if cv.band == 'bottom' else '左边'} {cv.art:.1%}，{'下面' if cv.band == 'bottom' else '右边'}是白条")  # fmt: skip
            head.insert(3, f"# 画布：比例 {cv.name}（{where}）")
            head.insert(
                4,
                f"# 分辨率：{cv.tier}（描插画每面至少 {TRACE_PX_PER_MM:g} px/mm，这一张长边要约 {cv.need_px} px）",
            )
        head.append(t("# Use the aspect ratio and resolution above if the image tool supports them, or follow the first line of the prompt below. After import, a canvas ratio off by more than 10% requires regeneration; individual panel differences are warnings. Try at most two rounds, then ask the user how to proceed."))  # fmt: skip
    if step == "flat" and any(f in LIDS for f in p.faces):
        head.append(t("# Follow the wireframe orientation for top and bottom panels; the prompt names the hinge edge.") + (
            t(" Lid orientation is provisional (MK03); use a folded sample to confirm.") if provisional else ""))  # fmt: skip
    return "\n".join(head) + "\n\n" + "\n".join(p.body) + "\n"


def _prompt_inputs(p: _Prompt) -> str:
    return "  ".join(t("{n}) {path} ({description})", n=i, path=path, description=what)
                     for i, (path, what) in enumerate(p.inputs, 1)) or t("None")  # fmt: skip


def _render_main(p: _Prompt, directory: Path, canvas: tuple[int, int]) -> str:
    head = [
        t("# Workflow v2.1, step 3: {title} (generated by `pacxio concept prompt --step main`)", title=p.title),
        t(HOW_TO),
        t("# Input images: {inputs}", inputs=_prompt_inputs(p)),
        f"# 画布：{canvas[0]} × {canvas[1]}（{'横版' if canvas[0] > canvas[1] else '竖版'}）",
        t("# Import: {command}", command=f"pacxio concept import {directory} --codex-latest --name {p.name} --view main"),
        t("# This view is a design reference. Proportions, text and details may be inaccurate. Do not use it in print files. The user chooses a design in the workbench."),
    ]
    return "\n".join(head) + "\n\n" + "\n".join(p.body) + "\n"


def _main_prompt(
    r: Result, job: Job, directory: Path, design: str | None, texts: list[str],
    scene: str | None, name: str | None, force: bool, brief: bool = False,
) -> Result:  # fmt: skip
    """主视角概念图的提示词：和 concept gen 同一个骨架，只写文件、不出图。"""
    from pacxio.concept.service import CONCEPT_SIZE, _box_dims, _next_id, _structure_lines
    from pacxio.visual.prompts import build_concept_prompt

    if not design:
        return r.fail(Issue(id="ARG", message=t("A main-view prompt needs --design (a design description)"),
                            fix=t("Describe style, colors, graphics and layout in --design, e.g. \"Flat vector hills, cream background\"")), 2)  # fmt: skip
    name = name or _next_id(directory)
    if (concept_dir(directory) / f"{name}.png").exists():
        return r.fail(Issue(id="ARG", message=t("Concept name {name} is already used", name=name),
                            fix=t("Pick another --name (such as main-B), or leave out --name to use the next free one")), 2)  # fmt: skip
    p = _Prompt(name=name, title=t("Main concept view ({name})", name=name), view="main", faces=[])
    body = build_concept_prompt(
        design, texts, job.style, _box_dims(job), scene, _structure_lines(job), brief_texts=brief
    )
    logo = _logo(job, directory)
    if logo:
        path = _viewable(directory, logo)
        n = p.image(path, t("Original logo artwork"))
        body += (f" Input image {n} ({path}) is the real brand logo artwork; reproduce it faithfully "
                 "wherever the logo appears.")  # fmt: skip
    p.body = [body]
    if brief:  # 路线 A：版式简报里主视角看得到的面那几段（抽屉测试 F-07：以前要 agent 手工挑面）
        lines, issue = _main_brief(job, directory)
        if issue is not None:
            return r.fail(issue, 2)
        p.body += ["", "Layout draft for the faces seen in this view (follow which information goes on "
                   "which face, its hierarchy and density; exact positions may vary):", *lines]  # fmt: skip
    if not texts and not brief:  # --brief 时要画的字在简报里
        r.warnings.append(Issue(id="CONCEPT", message=t("No --text given: the prompt has no text to draw on the box"),
                                fix=t("Give the main text from the content list (brand name, product name …), one --text each")))  # fmt: skip
    f = directory / PROMPT_DIR / f"{name}.txt"
    text = _render_main(p, directory, CONCEPT_SIZE)
    if f.is_file() and f.read_text(encoding="utf-8") != text and not force:
        return r.fail(Issue(id="PROMPT", message=t("Prompt file {name} already exists with different content (it may have been edited by hand)", name=f.name),
                            fix=t("If the old content is not needed, add --force to overwrite; to keep it, rename it first")), 2)  # fmt: skip
    f.parent.mkdir(exist_ok=True)
    f.write_text(text, encoding="utf-8")
    r.artifacts.append(str(f))
    row = {"file": str(f), "name": name, "view": "main", "faces": [],
           "inputs": [path for path, _ in p.inputs],
           "import": f"pacxio concept import {directory} --codex-latest --name {name} --view main"}  # fmt: skip
    r.data = {"step": "main", "prompts": [row], "images": 1}
    return r


MAIN_FACES = ("front", "side_b", "top")  # 主视角看得到的面：正面、右侧、顶面


def _main_brief(job: Job, directory: Path) -> tuple[list[str], Issue | None]:
    """版式简报里主视角看得到的面（多部件盒型按合上后的位置）那几段。"""
    from pacxio.dielines import build_dieline_for

    brief_file = directory / "plan" / "brief.txt"
    if not brief_file.is_file():
        return [], Issue(id="PLAN", message=t("There is no layout brief plan/brief.txt"),
                         fix=t("Run `pacxio plan {job}` first, or leave out --brief", job=directory))  # fmt: skip
    dl, _ = build_dieline_for(job)
    if dl is None:
        return [], Issue(id="PLAN", message=t("The dieline cannot be built, so the visible panels are unknown"), fix=t("Fix the structure parameters first"))
    at = dl.style_meta.get("set_faces")
    faces = (
        {str(k): str(v) for k, v in at.items()}
        if isinstance(at, dict)
        else {f.key: f.panel for f in build_box(dl).faces}
    )
    sections = brief_sections(brief_file.read_text(encoding="utf-8"))
    # 两片摇盖拼的顶（开槽箱）：刀版面是 top_front，简报按 "TOP (front flap)" 写在 top 下（开槽箱测试 R-04）
    lines = [line for k in MAIN_FACES if k in faces
             for line in sections.get(faces[k]) or sections.get(k, [])]  # fmt: skip
    if not lines:
        return [], Issue(id="PLAN", message=t("The layout brief has no sections for the front, right side and top"),
                         fix=t("Run `pacxio plan {job}` again", job=directory))  # fmt: skip
    return lines, None


REF_LONG_PX = 1400  # 拉正的参考图长边


def _straighten_refs(
    directory: Path, dl: Dieline, groups: list[list[str]],
    face_refs: dict[str, tuple[str, list[tuple[float, float]]]],
) -> tuple[dict[str, str], Issue | None]:  # fmt: skip
    """按给的四个角把概念图里的面拉正，存到 concepts/rectified/<概念图>.<面>.ref.png。返回 面 → 文件。"""
    from pacxio.concept.rectify import RECTIFIED_DIR, straighten
    from pacxio.concept.service import concept_dir

    faces = {f for g in groups for f in g}
    rects = face_rects(dl)
    panels = {pn.id: pn for pn in dl.panels}
    out: dict[str, str] = {}
    for face, (cid, corners) in face_refs.items():
        if face not in faces:
            return {}, Issue(id="ARG", message=t("--ref {face}: this panel has no flat prompt", face=face),
                             fix=t("Available: ") + t(", ").join(sorted(faces)))  # fmt: skip
        meta = read_meta(directory, cid)
        if meta is None or not (directory / meta.file).is_file():
            return {}, Issue(id="ARG", message=t("--ref {face}={id}: there is no such concept image", face=face, id=cid),
                             fix=t("See the names in `concept list` (usually the chosen main view or back view)"))  # fmt: skip
        if len(corners) != 4:
            return {}, Issue(id="ARG", message=t("--ref {face}: give four corners (8 numbers)", face=face),
                             fix=t("Pixel coordinates of the top-left, top-right, bottom-right and bottom-left corners with the panel upright: x0,y0,x1,y1,x2,y2,x3,y3"))  # fmt: skip
        w_mm, h_mm = face_size(rects[face], panels[face])
        k = REF_LONG_PX / max(w_mm, h_mm)
        with Image.open(directory / meta.file) as img:
            flat = straighten(img, corners, (max(1, round(w_mm * k)), max(1, round(h_mm * k))))
        dest = concept_dir(directory) / RECTIFIED_DIR / f"{cid}.{face}.ref.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        flat.save(dest)
        out[face] = str(dest)
    return out, None


def make_prompts(
    directory: Path,
    step: str,
    design: str | None = None,
    back_id: str | None = None,
    force: bool = False,
    words: list[str] | None = None,
    scene: str | None = None,
    name: str | None = None,
    body: bool = False,
    face_refs: dict[str, tuple[str, list[tuple[float, float]]]] | None = None,
    pairs: bool = False,
    separate: bool = False,
    brief: bool = False,
) -> Result:
    """生成出图提示词，写到 concept-prompts/，不出图：第 3 步主视角（main）、第 4 步背面视角（back）、
    第 5 步展开平面图（flat）。words（盒子上的文字）/ scene / name 只用于 main。flat 必须先有背面视角
    （2026-10-29 用户决定：先出背面视角再出平面图，背面、左侧的设计更稳定）；单部件盒型的平面图默认盒身一圈
    （背 | 左 | 正 | 右）画成一张——跨折线、跨糊口接缝的图形在一张图里才连得上（）；一圈比 3:1 还细长
    时两面一张，body 强制一圈、pairs 强制两面一张，separate 写明各面各自成画、图形不跨折线。
    face_refs 只用于 flat：面 → (概念图 id, 图里这个面的四个角)：把选定设计里这个面拉正，当"照这个构图画"的
    参考图加进含这个面的提示词（2026-10-30：平面图重画时构图、留白、线条深浅偏离概念图）。"""
    r = Result(command="concept prompt", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    if step not in STEPS:
        return r.fail(Issue(id="ARG", message=t("Unknown step {step}", step=step),
                            fix=t("--step main (main view) | back (back + left view) | flat (flats)")), 2)  # fmt: skip
    if step == "main":
        return _main_prompt(r, job, directory, design, words or [], scene, name, force, brief)
    chosen = read_chosen(directory)
    chosen_meta = read_meta(directory, chosen.id) if chosen else None
    if chosen is None or chosen_meta is None:
        return r.fail(Issue(id="CONCEPT", message=t("No design direction has been chosen yet"),
                            fix=t("After the user chooses a main-view concept, run `concept choose <job> <id>`, then make the back view / flats")), 2)  # fmt: skip
    brief_file = directory / "plan" / "brief.txt"
    if not brief_file.is_file():
        return r.fail(Issue(id="PLAN", message=t("There is no layout brief plan/brief.txt"),
                            fix=t("Run `pacxio plan {job}` first to make the brief and reference images", job=directory)), 2)  # fmt: skip
    from pacxio.dielines import build_dieline_for

    dl, errs = build_dieline_for(job)
    if dl is None:
        r.errors += errs
        r.ok, r.exit_code = False, 1
        return r
    at = (
        dl.style_meta.get("set_faces") if dl.style_meta.get("parts") else None
    )  # 多部件：合上后的面
    brief_text = brief_file.read_text(encoding="utf-8")
    # 版式简报是英文的、照抄进提示词：中文说明会让出图工具把中文画上去，先改成英文再出
    lang = label_language_issue(job, directory)
    if lang is not None:
        return r.fail(
            Issue(id=lang.id, message=lang.message, fix=t("{fix}; then make the prompts again", fix=lang.fix)), 2
        )
    stale = chinese_brief_lines(brief_text)
    if stale:
        return r.fail(Issue(id="LABEL_LANG", message=t("The layout brief still has Chinese notes (the brief is older than job.json): ") +
                            t("; ").join(ln.strip() for ln in stale),
                            fix=t("Run pacxio plan {job} again to refresh the brief, then make the prompts again", job=directory)), 2)  # fmt: skip
    sections = brief_sections(brief_text)
    logo = _logo(job, directory)
    logo = _viewable(directory, logo) if logo else None
    if logo is None:
        r.warnings.append(Issue(id="CONCEPT", message=t("The content list has no logo file, so the prompt has no original logo"),
                                fix=t("If there is a logo, add its file to the logo item in the list (content edit --asset), then make the prompts again")))  # fmt: skip
    back: ConceptMeta | None = None
    prompts: list[_Prompt] = []
    if step == "back":
        name = _free_name(directory, f"{chosen.id}-back", 1)
        seen = box_faces(dl)
        bottom = seen["bottom"][0] if "bottom" in seen and not seen["bottom"][1] else None
        top = (
            seen["top"][0] if "top" in seen and not seen["top"][1] else None
        )  # 开槽箱：两片摇盖拼的
        prompts.append(_back_prompt(job, dl, chosen_meta, name, sections, logo, design,
                                    at if isinstance(at, dict) else None, bottom, top))  # fmt: skip
    else:
        if back_id is not None:
            back = read_meta(directory, back_id)
            if back is None or (back.view or "main") != "back":
                return r.fail(Issue(id="CONCEPT", message=t("{id} is not a back-view concept", id=back_id),
                                    fix=t("See the views in `concept list`; tag untagged ones with `concept tag <job> <id> --view back` first")), 2)  # fmt: skip
        else:
            back = back_view_for(directory, chosen.id)
        if back is None:
            return r.fail(Issue(id="CONCEPT", message=t("There is no back-view (view=back) concept yet: the flats draw the back and left side from it"),
                                fix=t("Make the back view first with `pacxio concept prompt {job} --step back`, import it with `--view back` (`--name {id}-back1`), then make the flats", job=directory, id=chosen.id)), 2)  # fmt: skip
        groups = _flat_groups(dl)
        r.warnings += split_face_issues(dl)  # 锁底盒的底这类面没有提示词：说明为什么
        from pacxio.brief import body_walls

        walls = [] if pairs or dl.style_meta.get("parts") else body_walls(dl)
        if body and not walls:
            return r.fail(Issue(id="ARG", message=t("The body of style {style} is not one ring of joined panels, so it cannot be drawn as one image", style=dl.style),
                                fix=t("Leave out --body and make one image per two panels")), 2)  # fmt: skip
        if walls:
            # 单部件盒型默认盒身一圈一张（，用户定：连续的画面一张里才接得上，不连续的加 --separate）；
            # 一圈比 3:1 还细长时（矮的扁盒）照旧两面一张，除非给了 --body
            sizes = [size for size, _ in drawn_layout(dl, walls)]
            ring_aspect = sum(w for w, _ in sizes) / max(h for _, h in sizes)
            if body or ring_aspect <= MAX_CANVAS_ASPECT:
                groups = [walls] + [g for g in groups if not set(g) & set(walls)]
        refs, issue = _straighten_refs(directory, dl, groups, face_refs or {})
        if issue is not None:
            return r.fail(issue, 2)
        r.artifacts += list(refs.values())
        refs = {f: str(Path(v).relative_to(directory)) for f, v in refs.items()}
        for faces in groups:
            sub = "pairs" if len(faces) > 1 else "faces"
            ref = f"plan/{sub}/{'+'.join(faces)}.png"
            if not (directory / ref).is_file():
                return r.fail(Issue(id="PLAN", message=t("Layout reference image {ref} is missing", ref=ref),
                                    fix=t("Run `pacxio plan {job}` first to refresh the reference images", job=directory)), 2)  # fmt: skip
            slug = (
                faces[0].split(".", 1)[0]  # 抽屉外套一条：sleeve
                if blank_strip(dl, faces)
                else "body"
                if len(faces) > 2
                else "-".join(
                    f.split(".", 1)[-1] for f in faces
                )  # 上下叠：front-back、side_b-side_a
                if stack_axis(dl, faces) == "y"
                else SLUG.get(_local(faces[0]), faces[0].replace(".", "-"))
            )
            name = _free_name(directory, f"{chosen.id}-flat-{slug}", None)
            prompts.append(
                _flat_prompt(
                    job,
                    dl,
                    chosen_meta,
                    back,
                    faces,
                    name,
                    ref,
                    sections,
                    logo,
                    design,
                    refs,
                    separate,
                )
            )
    out = directory / PROMPT_DIR
    provisional = any(dl.panel(f).orientation_provisional for p in prompts for f in p.faces)
    texts = [(out / f"{p.name}.txt", _render(p, step, directory, chosen.id, provisional))
             for p in prompts]  # fmt: skip
    clash = [f for f, text in texts if f.is_file() and f.read_text(encoding="utf-8") != text]
    if clash and not force:
        return r.fail(Issue(id="PROMPT", message=t("These prompt files already exist with different content (they may have been edited by hand): ") +
                            t(", ").join(f.name for f in clash),
                            fix=t("If the old content is not needed, add --force to overwrite; to keep it, rename it first")), 2)  # fmt: skip
    out.mkdir(exist_ok=True)
    rows = []
    for (f, text), p in zip(texts, prompts, strict=True):
        f.write_text(text, encoding="utf-8")
        r.artifacts.append(str(f))
        rows.append({"file": str(f), "name": p.name, "view": p.view, "faces": p.faces,
                     "inputs": [path for path, _ in p.inputs],
                     "import": f"pacxio concept import {directory} --codex-latest --name {p.name} "
                               f"--view {p.view} --faces {','.join(p.faces)}"})  # fmt: skip
    r.data = {"step": step, "chosen": chosen.id, "back_view": back.id if back else None,
              "prompts": rows, "images": len(rows)}  # fmt: skip
    return r


__all__ = ["PROMPT_DIR", "brief_sections", "make_prompts"]
