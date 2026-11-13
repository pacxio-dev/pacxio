"""效果图汇总页：设计方向对照 + 三维视角 + 信息清单 + 检查项，排成一张图。

不放各面平视：和展开平面图那一步重复（用户 2026-11-02）；单独的 face_*.png 还在。
中文用思源黑体绘制；没有字体时回退到 PIL 内置字体（中文会显示成方框，所以会在图上提示）。
"""

from __future__ import annotations

import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from pacxio.fontfiles import TextError, fetch_command, find_font
from pacxio.messages import display_name, join, lang, t
from pacxio.mockup.content import UNTAGGED, ContentMap
from pacxio.mockup.fold import FACE_LABELS, BoxModel
from pacxio.mockup.render import BELOW_BACK_VIEW, OPEN_VIEW, VIEWS
from pacxio.model.report import Finding

SHEET_W = 2400
MARGIN = 70
GAP = 36
VIEW_SIZE = 740
TITLE_PT, SUB_PT, H2_PT, BODY_PT, SMALL_PT = 58, 30, 38, 28, 24
INK = (30, 32, 38)
MUTED = (110, 114, 124)
LINE = (210, 213, 220)
PAPER = (255, 255, 255)
LEVEL_RGB = {"error": (200, 30, 30), "warning": (210, 120, 0), "info": (90, 110, 150)}
LEVEL_TEXT = {"error": "Must fix", "warning": "Attention", "info": "Info"}  # 显示时 t()
FACE_ORDER = ("front", "back", "side_a", "side_b", "top", "bottom")
FOOTER = "Mockups are illustrations and cannot replace a physical proof. Get a proof approved before mass production."


def _tag(tag: str | None) -> str:
    """信息标签：用户写的照原样；没标的显示“未标注”（按当前语言）。"""
    return tag if tag and tag != UNTAGGED else t("Untagged")


@lru_cache(maxsize=16)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "SourceHanSansSC-Bold" if bold else "SourceHanSansSC-Regular"
    try:
        return ImageFont.truetype(str(find_font(name)), size)
    except (TextError, OSError):
        return ImageFont.load_default(size)


def fonts_available() -> bool:
    try:
        find_font("SourceHanSansSC-Regular")
        return True
    except TextError:
        return False


def _wrap_chars(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> list[str]:
    """按像素宽度折行（逐字符，兼容中文）。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> list[str]:
    """按像素宽度折行。中文逐字符（和以前一样）；英文 / 中英对照按词，放不下的长词或中文串再逐字符。"""
    if lang() == "zh":
        return _wrap_chars(draw, text, font, width)
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for tok in re.findall(r"\S+\s*|\s+", para):
            if cur and draw.textlength((cur + tok).rstrip(), font=font) > width:
                lines.append(cur.rstrip())
                cur = ""
            if draw.textlength(tok.rstrip(), font=font) > width:
                *full, cur = _wrap_chars(draw, cur + tok, font, width)
                lines += full
            else:
                cur += tok
        lines.append(cur.rstrip())
    return lines or [""]


def caption(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, width: int, pt: int, *,
            bold: bool = True, color: tuple[int, int, int] = INK) -> int:
    """图下说明，返回占的高度。中文单行（和以前一样）；英文 / 中英对照放不下就按宽度换行。"""
    font = _font(pt, bold)
    lines = [text] if lang() == "zh" else _wrap(draw, text, font, width)
    for i, line in enumerate(lines):
        draw.text((xy[0], xy[1] + i * int(pt * 1.35)), line, font=font, fill=color)
    return len(lines) * int(pt * 1.35)


class _Cursor:
    def __init__(self, draw: ImageDraw.ImageDraw) -> None:
        self.d = draw
        self.y = MARGIN

    def text(self, s: str, pt: int, *, bold: bool = False, color: tuple[int, int, int] = INK,
             x: int = MARGIN, width: int | None = None, gap: int = 8) -> None:  # fmt: skip
        font = _font(pt, bold)
        for line in _wrap(self.d, s, font, width or SHEET_W - MARGIN - x):
            self.d.text((x, self.y), line, font=font, fill=color)
            self.y += int(pt * 1.35)
        self.y += gap

    def rule(self) -> None:
        self.d.line([(MARGIN, self.y), (SHEET_W - MARGIN, self.y)], fill=LINE, width=2)
        self.y += GAP // 2


def build_sheet(
    title: str,
    subtitle: str,
    box: BoxModel,
    views: dict[str, Image.Image],
    content: ContentMap,
    findings: list[Finding],
    approval_note: str | None = None,
    concept: Image.Image | None = None,
    concept_label: str | None = None,
    back_concept: Image.Image | None = None,
    back_label: str | None = None,
    back_below: bool = False,
    open_label: str | None = None,
) -> Image.Image:
    """back_below：背面视角是从底下看的（扁平盒），和同角度的 BELOW_BACK_VIEW 精确效果图并排。"""
    # 先在一张很高的画布上依次绘制，最后按实际高度裁剪
    img = Image.new("RGB", (SHEET_W, 9000 + (VIEW_SIZE + 100 if back_concept else 0)), PAPER)
    d = ImageDraw.Draw(img)
    cur = _Cursor(d)
    cur.text(title, TITLE_PT, bold=True, gap=4)
    cur.text(subtitle, SUB_PT, color=MUTED, gap=GAP // 2)
    if not fonts_available():
        cur.text(
            t("(Source Han Sans not found, so Chinese text cannot be shown: run `pacxio setup --yes` or {command} first)", command=fetch_command()),
            SMALL_PT,
            color=LEVEL_RGB["error"],
        )
    if approval_note:
        cur.text(approval_note, BODY_PT, bold=True, color=LEVEL_RGB["warning"])
    cur.rule()

    # ---- 设计方向对照：概念图（主视角、背面视角）vs 同角度的精确效果图 ----
    pairs = [(im, t("{label} (AI direction reference)", label=label or t("Concept image")), view.name, view.label)
             for im, label, view in ((concept, concept_label, VIEWS[0]),
                                     (back_concept, back_label, BELOW_BACK_VIEW if back_below else VIEWS[1]))
             if im is not None]  # fmt: skip
    if pairs:
        cur.text(t("Design direction comparison"), H2_PT, bold=True)
    for shot, label, view_name, view_label in pairs:
        top = cur.y
        ch = VIEW_SIZE
        cw = max(1, round(shot.width * ch / shot.height))
        avail = SHEET_W - 2 * MARGIN - GAP - VIEW_SIZE
        if cw > avail:  # 太宽就按宽度缩
            cw, ch = avail, max(1, round(shot.height * avail / shot.width))
        img.paste(shot.resize((cw, ch), Image.Resampling.LANCZOS), (MARGIN, top))
        d.rectangle([MARGIN, top, MARGIN + cw, top + ch], outline=LINE, width=2)
        vx = MARGIN + cw + GAP
        img.paste(
            views[view_name].resize((VIEW_SIZE, VIEW_SIZE), Image.Resampling.LANCZOS), (vx, top)
        )
        d.rectangle([vx, top, vx + VIEW_SIZE, top + VIEW_SIZE], outline=LINE, width=2)
        cap_y = top + max(ch, VIEW_SIZE) + 6
        h1 = caption(d, (MARGIN + 8, cap_y), label, cw - 8, SMALL_PT)
        h2 = caption(d, (vx + 8, cap_y), t("Exact mockup · {view} (what will be printed)", view=display_name(view_label)),
                     VIEW_SIZE - 8, SMALL_PT)  # fmt: skip
        cur.y = cap_y + max(h1, h2) - int(SMALL_PT * 1.35) + int(SMALL_PT * 1.6) + GAP // 2
    if pairs:
        cur.text(
            t("The two will differ: the proportions, text, fonts and details in the concept image are drawn by AI, and the "
              "final files are rebuilt from it. Go by the exact mockup on the right, and confirm these differences are acceptable."),
            SMALL_PT,
            color=LEVEL_RGB["warning"],
        )
        cur.rule()

    # ---- 三维效果 ----
    cur.text(t("3D views"), H2_PT, bold=True)
    x = MARGIN
    top = cur.y
    for v in VIEWS:
        tile = views[v.name].resize((VIEW_SIZE, VIEW_SIZE), Image.Resampling.LANCZOS)
        img.paste(tile, (x, top))
        d.rectangle([x, top, x + VIEW_SIZE, top + VIEW_SIZE], outline=LINE, width=2)
        d.text((x + 12, top + VIEW_SIZE + 6), display_name(v.label), font=_font(SMALL_PT, False), fill=MUTED)
        x += VIEW_SIZE + GAP
    cur.y = top + VIEW_SIZE + int(SMALL_PT * 1.6) + GAP
    if "open" in views:  # 多部件盒型：打开的样子（盖子拿开 / 抽屉抽出一半）
        top = cur.y
        img.paste(
            views["open"].resize((VIEW_SIZE, VIEW_SIZE), Image.Resampling.LANCZOS), (MARGIN, top)
        )
        d.rectangle([MARGIN, top, MARGIN + VIEW_SIZE, top + VIEW_SIZE], outline=LINE, width=2)
        d.text(
            (MARGIN + 12, top + VIEW_SIZE + 6),
            open_label or display_name(OPEN_VIEW.label),
            font=_font(SMALL_PT, False),
            fill=MUTED,
        )
        cur.y = top + VIEW_SIZE + int(SMALL_PT * 1.6) + GAP
    cur.rule()

    content_section(cur, content)
    findings_section(cur, findings)
    cur.text(t(FOOTER), SMALL_PT + 2, bold=True, color=MUTED)
    return img.crop((0, 0, SHEET_W, min(cur.y + MARGIN // 2, img.height)))


def content_section(cur: _Cursor, content: ContentMap) -> None:
    """信息清单（每个面上有什么）：效果图确认页与平铺确认页共用。"""
    cur.text(t("Content checklist (what is on each panel)"), H2_PT, bold=True)
    if content.backgrounds:
        cur.text(t("Background: {items}", items=join(content.backgrounds, en_sep="; ", zh_sep="；")), SMALL_PT, color=MUTED)
    col_w = (SHEET_W - 2 * MARGIN - GAP) // 2
    base_y = cur.y
    ys = [base_y, base_y]
    order = [k for k in FACE_ORDER if not content.by_face or k in content.by_face] + [
        k for k in content.by_face if k not in FACE_ORDER
    ]
    for i, key in enumerate(order):
        col = i % 2
        cx = MARGIN + col * (col_w + GAP)
        cur.y = ys[col]
        cur.text(
            display_name(FACE_LABELS.get(key, "挂板" if key == "hanger" else key)),
            BODY_PT + 2,
            bold=True,
            x=cx,
            width=col_w,
            gap=2,
        )
        items = content.by_face.get(key, [])
        if not items:
            cur.text(
                t("(No text / images / finishes, only a background color or pattern)"),
                SMALL_PT,
                color=MUTED,
                x=cx + 24,
                width=col_w - 24,
            )
        for it in items:
            tag = _tag(it.tag)
            kind = {
                "text": "",
                "image": t("Image") + " ",
                "vector": t("Graphic") + " ",
                "placeholder": t("Placeholder") + " ",
                "finish": t("Finish") + " ",
            }[it.kind]
            color = MUTED if not it.tag else INK
            cur.text(
                f"[{tag}] {kind}{it.summary}",
                SMALL_PT + 2,
                color=color,
                x=cx + 24,
                width=col_w - 24,
                gap=2,
            )
        ys[col] = cur.y + GAP // 2
    cur.y = max(ys)
    if content.hidden:
        hidden = join([t("[{tag}] {summary} ({panel})", tag=_tag(i.tag), summary=i.summary, panel=i.panel)
                      for i in content.hidden], en_sep="; ", zh_sep="；")
        cur.text(t("Content on panels you cannot see (tuck flaps / dust flaps / glue flap): {items}", items=hidden),
                 SMALL_PT, color=LEVEL_RGB["warning"])  # fmt: skip
    cur.text(
        t("Tags used: {tags}", tags=join(content.tags)) if content.tags else t("Tags used: (none)"),
        SMALL_PT,
        color=MUTED,
    )
    cur.rule()


def findings_section(cur: _Cursor, findings: list[Finding]) -> None:
    """确认前要看的事项：效果图确认页与平铺确认页共用。"""
    cur.text(t("Check before confirming"), H2_PT, bold=True)
    order = {"error": 0, "warning": 1, "info": 2}
    for f in sorted(findings, key=lambda f: (order[f.level], f.id)):
        cur.text(
            f"{t(LEVEL_TEXT[f.level])}  {f.id}  {f.message}",
            SMALL_PT + 2,
            color=LEVEL_RGB[f.level],
            gap=0,
        )
        if f.fix:
            cur.text("→ " + f.fix, SMALL_PT, color=MUTED, x=MARGIN + 36, gap=6)
    cur.y += GAP // 2
    cur.rule()
