"""出图提示词的强制约束（、CLAUDE.md "禁止让 AI 生成的位图中出现文字"）。

约束由引擎追加，调用方无法关闭：所有文字由引擎排版并转曲，AI 只产出视觉素材。
"""

from __future__ import annotations

NO_TEXT = "Absolutely no text, letters, numbers, words, logos, watermarks or signatures anywhere in the image."
FLAT_VIEW = (
    "Flat, straight-on, front-facing composition. No perspective, no drop shadows, "
    "not a product photo or mockup, no packaging or box shown."
)
SEAMLESS = "Seamless tileable pattern: all four edges must continue into each other without a visible seam."
GREEN_BACKGROUND = (
    "Place the subject on a solid, flat, pure green background (#00FF00) with no gradients, "
    "no shadows on the background, and no green inside the subject."
)
PATTERN_ROLES: set[str] = {"pattern", "texture"}
FLAT_SHAPES = (
    "Graphic shapes only, made of a few solid flat colors: no gradients, no textures, no shading, "
    "no outlines, no photographic detail; clean smooth vector-like edges on a plain solid background."
)
GRAPHIC_ROLES: set[str] = {"graphic"}  # 之后要描成矢量的装饰图形（曲线色块、色带、叶片、图标）


def build_prompt(user_prompt: str, role: str, transparent: bool) -> str:
    """用户提示词 + 强制约束。pattern / texture 追加无缝平铺；graphic 追加纯色平涂；要透明背景时追加纯绿背景。"""
    parts = [user_prompt.strip(), NO_TEXT, FLAT_VIEW]
    if role in PATTERN_ROLES:
        parts.append(SEAMLESS)
    if role in GRAPHIC_ROLES:
        parts.append(FLAT_SHAPES)
    if transparent:
        parts.append(GREEN_BACKGROUND)
    return " ".join(p for p in parts if p)


# ---------------- 概念图（例外：允许文字）----------------
#
# 概念图是"想做到的成品最终样子"，用来让决策者确认设计方向，所以允许、并且要求出现给定的文字。
# 它的图本身不进印刷文件（引擎会拒绝把 concepts/ 下的图直接用于放置、预处理、工艺）。
# 印刷文件里的文字仍然一律由引擎排版并转曲（CLAUDE.md）。

STYLE_SHAPES = {
    "rte": "a reverse tuck end folding carton (printed paperboard box whose top lid tucks in at the front "
    "and bottom lid tucks in at the back)",
    "ste": "a straight tuck end folding carton (printed paperboard box whose top and bottom lids both "
    "tuck in at the front)",
    "snaplock": "a folding carton with a tuck-in top lid and a snap lock (1-2-3) bottom "
    "(printed paperboard box)",
    "autolock": "a folding carton with a tuck-in top lid and an auto lock (crash lock) bottom "
    "(printed paperboard box)",
    "fefco_0427": "a corrugated mailer box (FEFCO 0427: one-piece die-cut box with double side walls "
    "rolled over at the top; the hinged lid sits between the two rolled edges, which stay visible as "
    "narrow strips along the left and right of the top; the lid's front flap folds down over the outside of the "
    "front wall to the bottom edge, so the front face you see is that flap)",
    "fefco_0201": "a corrugated regular slotted shipping case (FEFCO 0201: four top flaps and four "
    "bottom flaps, the two outer flaps meet in the middle)",
    # 2026-11-11 示例项目 Tidewell：以前落到默认"纸盒"，AI 不画顶上的摇盖
    "crashlock_rsc": "a corrugated shipping case (FEFCO 0201 body: four top flaps, the two outer flaps meet "
    "in the middle; with a pre-glued auto-lock crash bottom)",
    "tray_lid": "a two-piece paperboard gift box (a lid that slides down over a base tray)",
    "drawer": "a paperboard drawer box (an inner tray that slides out of an open-ended sleeve)",
}
# 软袋（2026-11-10 示例项目 Saltwick：以前落到默认"纸盒"，L / W / H 也按盒子写，AI 画成了纸盒）。
# 软袋的 L 是袋宽、W 是袋高、H 是底褶 / 侧风琴 / 底深（box-styles.md）。
POUCH_SHAPES = {
    "pouch_standup": "a flexible stand-up pouch (doypack: printed laminated film with heat-sealed edges and "
    "a bottom gusset that lets it stand upright; NOT a paperboard box)",
    "sachet_3side": "a flat three-side-seal sachet (printed laminated film, heat-sealed on three sides; "
    "NOT a paperboard box)",
    "pouch_backseal": "a flexible pillow pouch (printed laminated film with a back seam and crimped top "
    "and bottom seals, like a chips bag; NOT a paperboard box)",
    "pouch_flat_bottom": "a flexible flat-bottom pouch (box pouch: printed laminated film with side gussets "
    "and a flat rectangular bottom; NOT a paperboard box)",
}
POUCH_DEPTH = {
    "pouch_standup": "a {h:g} mm bottom gusset",
    "pouch_backseal": "{h:g} mm deep side gussets",
    "pouch_flat_bottom": "a {h:g} mm deep flat bottom and side gussets",
}
CONCEPT_VIEW_POUCH = (
    "Front view of the filled pouch standing upright, turned slightly to one side so it reads as soft "
    "film with sealed edges; the front face fills most of the image."
)
CONCEPT_VIEW = "Three-quarter view showing the front panel and the right side panel clearly, the top slightly visible."
# 扁平盒（高 ≤ 长、宽里较小那个的一半）：主设计面是顶面，按俯拍画，顶面才不会被压扁（天地盖测试 F-05）
FLAT_BOX_RATIO = 0.5
CONCEPT_VIEW_FLAT = (
    "High-angle three-quarter view from above: the top of the box fills most of the image, "
    "the front panel and the right side panel are visible but foreshortened."
)
CONCEPT_SCENE_DEFAULT = (
    "Photorealistic premium commercial product photography on a simple surface, soft natural daylight, "
    "shallow depth of field."
)
CONCEPT_TEXT_RULE = (
    "Spell every text exactly as given, in the given language and capitalisation. "
    "Do not add any other words, letters, numbers or fake brand names."
)
CONCEPT_NO_TEXT = "The packaging has no text at all."
# 路线 A 的主视角（--brief）：要画的字在后面的版式简报里（STE 测试 S-01：以前写"没有文字"，和简报矛盾）
CONCEPT_BRIEF_TEXT = (
    "the quoted texts in the layout draft below. Spell short texts exactly; long texts may be drawn "
    "as blocks of small text. Do not add any other words, letters, numbers or fake brand names."
)


# 牛皮面纸的瓦楞（开槽箱测试 R-01）：概念图、平面图要画成牛皮纸上直接印，没墨的地方是牛皮色、没有白墨
KRAFT_LINER = (
    "The board is brown KRAFT corrugated board printed directly on it: every unprinted area shows the "
    "natural kraft brown, the inks sit on the brown, and there is no white ink - no white areas or white text."
)
KRAFT_LINER_FLAT = (
    "The printed sheet is brown kraft board: the background (every unprinted area) is a flat kraft brown "
    "#BA9268 without texture, the inks are printed on it, and there is no white ink - no white areas or white text."
)


def liner_line(liner: str | None, flat: bool = False) -> list[str]:
    """面纸是牛皮色时要加的一句（白面纸 / 卡纸不加）。"""
    if liner != "kraft":
        return []
    return [KRAFT_LINER_FLAT if flat else KRAFT_LINER]


def is_flat_box(dims_mm: tuple[float, float, float]) -> bool:
    """扁平盒：高 ≤ 长、宽里较小那个的一半。主视角俯拍顶面，背面视角从底下看（抽屉测试 F-12）。"""
    w, d, h = dims_mm
    return h <= FLAT_BOX_RATIO * min(w, d)


def concept_view(dims_mm: tuple[float, float, float]) -> str:
    """主视角的机位：扁平盒俯拍，其余正面 + 右侧、顶面略微露出。"""
    return CONCEPT_VIEW_FLAT if is_flat_box(dims_mm) else CONCEPT_VIEW


def _ratio(dims: tuple[float, float, float]) -> str:
    w, d, h = dims
    return f"front width : side depth : height = 1 : {d / w:.2f} : {h / w:.2f}"


def pouch_size(style: str, dims_mm: tuple[float, float, float], face: str) -> str:
    """软袋的 L / W / H 是袋宽 / 袋高 / 底褶或风琴深。"""
    w, h, depth = dims_mm
    gusset = POUCH_DEPTH.get(style, "")
    extra = f", with {gusset.format(h=depth)}" if gusset and depth > 0 else ""
    return (f"Pouch size {w:g} mm wide and {h:g} mm tall{extra}; keep the {face} proportions "
            f"(width : height = 1 : {h / w:.2f}).")  # fmt: skip


def build_concept_prompt(
    design: str,
    texts: list[str],
    style: str,
    dims_mm: tuple[float, float, float],
    scene: str | None = None,
    structure: list[str] | None = None,
    brief_texts: bool = False,
) -> str:
    """概念图提示词：盒型与真实比例（+ 开窗等结构说明）+ 设计描述 + 必须原样出现的文字 + 场景。
    `brief_texts`：后面接版式简报，简报里引号中的字也要画（不写"没有文字"）。"""
    w, d, h = dims_mm
    pouch = style in POUCH_SHAPES
    if pouch:
        size = pouch_size(style, dims_mm, "front")
        shape, thing = POUCH_SHAPES[style], "pouch"
    else:
        size = f"Box size {w:g} mm wide, {d:g} mm deep, {h:g} mm tall; keep these proportions ({_ratio(dims_mm)})."
        shape, thing = STYLE_SHAPES.get(style, "a printed paperboard box"), "box"
    parts = [
        f"Packaging design concept: {shape}.",
        size,
        *(structure or []),
        f"Design: {design.strip()}",
    ]
    quoted = "; ".join(f'"{t}"' for t in texts)
    if brief_texts:
        lead = f"exactly: {quoted}, plus " if texts else ""
        parts.append(f"The texts printed on the {thing} are {lead}{CONCEPT_BRIEF_TEXT}")
    elif texts:
        parts.append(f"The texts printed on the {thing} are exactly: {quoted}. {CONCEPT_TEXT_RULE}")
    else:
        parts.append(CONCEPT_NO_TEXT)
    parts += [CONCEPT_VIEW_POUCH if pouch else concept_view(dims_mm), (scene or CONCEPT_SCENE_DEFAULT).strip()]
    return " ".join(parts)
