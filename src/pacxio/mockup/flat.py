"""平铺确认页（ 2.7）：正式稿拼好之后、出 3D 效果图之前，用展开图确认版面。

一张图：整张展开图（成品外观 + 刀线 + 面名称）；有展开平面图（概念图 view=flat）时逐面对照——
拉正的 AI 平面图 | 引擎正式稿 | 50% 叠图（平面对平面，同一个面有几张时取比例最准的那张，正反同款时
正面可以取背面那张）；和选定的主视角概念图并排（正面 + 右侧）；agent 写的差别说明；按面信息清单；
检查事项。确认只是记录（flat_confirmed.json），**不是闸门**——3D 效果图的 approve 才是导出前的
正式确认。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pacxio.concept.rectify import FaceCut, FlatError, face_crop, face_rects, upright
from pacxio.concept.service import (
    FACE_ZH,
    concept_metas,
    flat_checks,
    flat_cut_for,
    in_use_versions,
    no_flat_faces,
    read_chosen,
)
from pacxio.export.canvas import _draw_dieline
from pacxio.inventory import confirmation_status as content_status
from pacxio.messages import display_name, join, t, use_language
from pacxio.mockup.approval import design_fingerprint, style_version
from pacxio.mockup.art import render_art
from pacxio.mockup.content import check_content, collect_content
from pacxio.mockup.fold import build_box
from pacxio.mockup.sheet import (
    GAP,
    H2_PT,
    INK,
    LINE,
    MARGIN,
    MUTED,
    PAPER,
    SHEET_W,
    SMALL_PT,
    SUB_PT,
    TITLE_PT,
    _Cursor,
    _font,
    content_section,
    findings_section,
)
from pacxio.mockup.sheet import (
    caption as draw_caption,
)
from pacxio.model.common import Issue, Result
from pacxio.model.report import Finding
from pacxio.notes import open_notes_issue

FLAT_DIR = "flat"
SHEET_NAME = "00_flat_sheet.png"
MANIFEST = "manifest.json"
FLAT_DPI = 150
STYLES_WITH_FLAT = {
    "rte",
    "ste",
    "snaplock",
    "autolock",
    "fefco_0427",
    "mailer_tuck",
    "mailer_front_lock",
    "mailer_tear_seal",
    "fefco_0201",
    "crashlock_rsc",  # 2026-11-11 示例项目 Tidewell：盒身四面同 0201，平面图对照照常用
    "tray_lid",
    "drawer",
}
# restore 的评分 / 文字 / 图形命令共用上面的能力表；这里只扩展 flat 对照页入口。
FLAT_PAGE_STYLES = STYLES_WITH_FLAT | {"structure"}
SET_ORDER = ("front", "side_b", "back", "side_a", "top", "bottom")  # 多部件盒型：合上后的位置
COMPARE_H = 900  # 概念图与正面 + 右侧对照的高度（像素）
FACE_NAMES = FACE_ZH
FLAT_TILE_H = 960  # 平面图对照：每张小图最高这么多像素
DETAIL_PX_PER_MM = 4  # flat/vs_flat_<面>.png：逐面对照的大图
DETAIL_FILE = "vs_flat_{face}.png"
FACE_NAME_INK = (70, 120, 200)
FOOTER = (
    "Confirming the flat layout is only a record; confirming the exact 3D mockup (approve) is the formal confirmation "
    "before export. Get a proof approved before mass production."
)


def _face_name(face: str) -> str:
    """面的显示名（按当前交付件语言）；FACE_NAMES 存的是中文。"""
    return display_name(FACE_NAMES.get(face, face))


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def read_manifest(directory: Path) -> dict[str, Any] | None:
    f = directory / FLAT_DIR / MANIFEST
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except ValueError:
        return None


def _unfolded(art: Image.Image, m: Any, dl: Any) -> Image.Image:
    img = art.copy()
    _draw_dieline(img, dl, m)
    draw = ImageDraw.Draw(img)
    # 多部件盒的单个部件（如天盖）不一定能独立 build_box；只为配方补动态面名。
    visible = face_rects(dl) if dl.style == "structure" else {}
    for panel in dl.panels:
        if panel.id in FACE_NAMES or panel.id in visible:
            x0, _, _, y1 = panel.polygon.bbox()
            px, py = m.pt(x0, y1)
            draw.text(
                (px + 6, py + 4),
                _face_name(panel.id),
                fill=FACE_NAME_INK,
                font=_font(SMALL_PT, True),
            )
    return img


def _crop_faces(art: Image.Image, m: Any, dl: Any, ids: tuple[str, ...]) -> Image.Image | None:
    """按合上后的面取图、转正并排，不能取几个刀版面合在一起的包围框。

    飞机盒的正面实际是 top_tuck，侧面在刀版上横着；两面之间还隔着其他版面。
    """
    faces = {f.key: f for f in build_box(dl).faces}
    if not ids or any(key not in faces for key in ids):
        return None
    images = [
        upright(_crop_rect(art, m, faces[key].tex_rect), dl.panel(faces[key].panel))
        for key in ids
    ]
    pair = Image.new("RGB", (sum(im.width for im in images), max(im.height for im in images)), PAPER)
    x = 0
    for im in images:
        pair.paste(im, (x, pair.height - im.height))
        x += im.width
    return pair


def _fit_h(img: Image.Image, h: int, max_w: int) -> Image.Image:
    w = round(img.width * h / img.height)
    if w > max_w:
        w, h = max_w, round(img.height * max_w / img.width)
    return img.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)


# ---------------- 和展开平面图对照 ----------------


@dataclass
class FlatRow:
    face: str  # 刀版面 id
    label: str  # 正面（= 背面）
    concept: str  # 取自哪张平面图
    concept_face: str  # 那张图里的哪个面
    aspect_error: float
    folds_found: bool
    ai: Image.Image  # 拉正的 AI 平面图，和 engine 同尺寸
    engine: Image.Image  # 正式稿这个面（正立）
    # 多部件盒型：face 写全名（lid.top），下面是它在部件自己刀版上的面 id 和矩形
    panel: str | None = None
    rect: tuple[float, float, float, float] | None = None
    part: str | None = None

    @property
    def local(self) -> str:
        """这个面在所属刀版上的 id（单部件盒型就是 face）。"""
        return self.panel or self.face

    def record(self) -> dict[str, Any]:
        return {"face": self.face, "concept": self.concept, "concept_face": self.concept_face,
                "aspect_error": round(self.aspect_error, 4), "folds_found": self.folds_found}  # fmt: skip


def _crop_rect(art: Image.Image, m: Any, rect: tuple[float, float, float, float]) -> Image.Image:
    x0, y0, x1, y1 = rect
    left, top = m.pt(x0, y1)
    right, bottom = m.pt(x1, y0)
    return art.crop((round(left), round(top), round(right), round(bottom)))


def flat_sources(
    directory: Path, dl: Any, same_faces: dict[str, str] | None, skip: set[str] | None = None
) -> tuple[dict[str, tuple[Any, Image.Image, FaceCut, bool]], list[Issue]]:
    """每个看得到的面：在用的 AI 平面图里比例最准的那张 → (平面图元数据, 原图, 这个面的像素框, 折线找到没有)。

    同一组面改过几版时只用在用的那版（默认最新导入的，用户可以 `concept use` 改用旧版）；
    没标面的平面图照样拿来量，量不了会提醒去补标。skip：不拿来用的（比例不合格、要重出的）。
    正反同款的两个面互相可以借用。没有平面图的面不在结果里。"""
    chosen = read_chosen(directory)
    cid = chosen.id if chosen else None
    in_use = {mt.id for mt in in_use_versions(directory, "flat", cid).values()}
    no_flat = set(no_flat_faces(dl))  # 不出平面图的面以前导入过的图不拿来对照（抽屉测试 F-17）
    metas = [mt for mt in concept_metas(directory, "flat") if (directory / mt.file).is_file()
             and (mt.id in in_use or not mt.faces) and mt.id not in (skip or set())
             and not (mt.faces and set(mt.faces) <= no_flat)]  # fmt: skip
    if not metas:
        return {}, []
    rects = face_rects(dl)
    same = {tx: s for tx, s in (same_faces or {}).items() if tx in rects and s in rects}
    twins = {f: {f} for f in rects}
    for target, source in same.items():
        twins[target].add(source)
        twins[source].add(target)
    issues: list[Issue] = []
    cuts = []
    for mt in metas:
        try:
            img, cut = flat_cut_for(directory, dl, mt)
        except FlatError as e:
            issues.append(Issue(id="FLAT", message=t("Cannot compare flat concept {id}: {error}", id=mt.id, error=e),
                                fix=t("Check the faces it is tagged with in `concept list`, or run `concept rectify {directory} {id}` to see what was measured",
                                      directory=directory, id=mt.id)))  # fmt: skip
            continue
        cuts.append((mt, img, cut))
        if not cut.folds_found:
            issues.append(Issue(id="FLAT", message=t("No fold lines between faces found in flat concept {id}; it was cut by the real width ratio", id=mt.id),
                                fix=t("If the AI drew the faces at unequal widths the cut will be off, so keep that in mind when comparing; when regenerating, ask for a thin fold line running top to bottom")))  # fmt: skip
    out: dict[str, tuple[Any, Image.Image, FaceCut, bool]] = {}
    for face in rects:
        cands = [(abs(c.aspect_error), mt, img, c, cut.folds_found)
                 for mt, img, cut in cuts for c in cut.faces if c.face in twins[face]]  # fmt: skip
        if cands:
            _, mt, img, c, found = min(cands, key=lambda tx: tx[0])
            out[face] = (mt, img, c, found)
    return out, issues


def flat_rows(
    directory: Path, p: Any, art: Image.Image, m: Any, skip: set[str] | None = None
) -> tuple[list[FlatRow], list[str], list[Issue]]:
    """每个面：在用的 AI 平面图里比例最准的那张（拉正）和正式稿。返回 (对照行, 没有平面图的面, 提醒)。
    怎么挑平面图见 flat_sources。"""
    sources, issues = flat_sources(directory, p.dl, p.job.same_faces, skip)
    if not sources and not issues:
        return [], [], []
    rects = face_rects(p.dl)
    # 按真实可见面遍历：0427 的正面是 top_tuck，原生配方可能是 wall_0 等。
    # 两片摇盖拼出的面、明确不出平面图的面仍不要求单独对照。
    excluded = set(no_flat_faces(p.dl)) | {f.panel for f in build_box(p.dl).faces if f.parts}
    rects = {face: rect for face, rect in rects.items() if face not in excluded}
    same = {tx: s for tx, s in (p.job.same_faces or {}).items() if tx in rects and s in rects}
    panels = {pn.id: pn for pn in p.dl.panels}
    rows: list[FlatRow] = []
    missing: list[str] = []
    for face in rects:
        if face in same:  # 正反同款的复制面和原面一模一样，不单独列
            continue
        if face not in sources:
            missing.append(face)
            continue
        mt, img, c, found = sources[face]
        engine = upright(_crop_rect(art, m, rects[face]), panels[face])
        ai = face_crop(img, c).resize(engine.size, Image.Resampling.LANCZOS)  # 躺着画的面转正
        copies = [tx for tx, s in same.items() if s == face]
        label = _face_name(face)
        for other in copies:
            label = t("{face} (= {other})", face=label, other=_face_name(other))
        rows.append(FlatRow(face, label, mt.id, c.face, c.aspect_error, found, ai, engine))
    return rows, missing, issues


def _tile(img: Image.Image, w: int, h: int) -> Image.Image:
    k = min(w / img.width, h / img.height)
    return img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))),
                      Image.Resampling.LANCZOS)  # fmt: skip


def _row_note(row: FlatRow) -> str:
    src = (row.concept if row.concept_face == row.face
           else t("{face} in {concept}", face=_face_name(row.concept_face), concept=row.concept))  # fmt: skip
    note = t("{label}: taken from {src}; the original aspect ratio differs from the real one by {error} (straightened to real size)",
             label=row.label, src=src, error=f"{row.aspect_error:+.0%}")  # fmt: skip
    return note if row.folds_found else t("{note}; no fold line found, cut by proportion", note=note)


def flat_section(rows: list[FlatRow], missing: list[str]) -> Image.Image:
    """ "和展开平面图对照"一节（单独画好，再贴进确认页）：每个面 AI 平面图 | 正式稿 | 50% 叠图。"""
    avail = SHEET_W - 2 * MARGIN
    tile_w = (avail - 2 * GAP) // 3
    img = Image.new("RGB", (SHEET_W, 400 + len(rows) * (FLAT_TILE_H + 200)), PAPER)
    d = ImageDraw.Draw(img)
    cur = _Cursor(d)
    cur.y = 0
    cur.text(t("Comparison with the flat concept (flat against flat)"), H2_PT, bold=True)
    cur.text(t("Left: the AI flat concept, straightened to real size (its AI-drawn text is for reference only); middle: the "
               "final artwork (what will be printed); right: both overlaid at 50%, where they do not line up is the difference."),
             SMALL_PT + 2, color=MUTED)  # fmt: skip
    for row in rows:
        cur.text(_row_note(row), SMALL_PT + 4, bold=True, gap=6)
        ai = _tile(row.ai, tile_w, FLAT_TILE_H)
        eng = row.engine.resize(ai.size, Image.Resampling.LANCZOS)
        mix = Image.blend(ai, eng, 0.5)
        x, top = MARGIN, cur.y
        for caption, im in ((t("AI flat (straightened)"), ai), (t("Final artwork"), eng), (t("Overlay 50%"), mix)):
            img.paste(im, (x, top))
            d.rectangle([x, top, x + im.width, top + im.height], outline=LINE, width=2)
            d.text((x + 8, top + im.height + 6), caption, font=_font(SMALL_PT, True), fill=INK)
            x += tile_w + GAP
        cur.y = top + ai.height + int(SMALL_PT * 1.6) + GAP
    if missing:
        cur.text(t("Faces without a flat concept: {faces}", faces=join([_face_name(f) for f in missing])),
                 SMALL_PT + 2, color=MUTED)  # fmt: skip
    return img.crop((0, 0, SHEET_W, cur.y))


def flat_detail(row: FlatRow, scale: float) -> Image.Image:
    """一个面的大图对照（AI | 正式稿 | 叠图），flat/vs_flat_<面>.png，放大看细节用。"""
    size = (max(1, round(row.engine.width * scale)), max(1, round(row.engine.height * scale)))
    ai = row.ai.resize(size, Image.Resampling.LANCZOS)
    eng = row.engine.resize(size, Image.Resampling.LANCZOS)
    head, pad = 40, 20
    out = Image.new("RGB", (3 * size[0] + 4 * pad, size[1] + head + pad), PAPER)
    d = ImageDraw.Draw(out)
    for i, (caption, im) in enumerate(((t("AI flat (straightened) · {concept}", concept=row.concept), ai), (t("Final artwork"), eng),
                                       (t("Overlay 50%"), Image.blend(ai, eng, 0.5)))):  # fmt: skip
        x = pad + i * (size[0] + pad)
        out.paste(im, (x, head))
        text, font = _fit_caption(f"{row.label}  {caption}", size[0])
        d.text((x, 8), text, font=font, fill=INK)
    return out


CAPTION_MIN_PT = (
    11  # 列标题放不下时最小缩到这么大，再放不下就截断（抽屉测试：窄的侧面三列标题互相压着）
)


def _fit_caption(text: str, width: float) -> tuple[str, Any]:
    """列标题在这一列的宽度里放下：先缩字号，缩到 CAPTION_MIN_PT 还放不下就截断加"…"。"""
    pt = SMALL_PT
    font = _font(pt, True)
    while font.getlength(text) > width and pt > CAPTION_MIN_PT:
        pt -= 1
        font = _font(pt, True)
    while font.getlength(text) > width and len(text) > 1:
        text = text[:-2] + "…"
    return text, font


def build_flat_sheet(
    title: str, subtitle: str, unfolded: Image.Image, concept: Image.Image | None,
    pair: Image.Image | None, diffs: list[str], content: Any, findings: list[Finding],
    flats: Image.Image | None = None,
) -> Image.Image:  # fmt: skip
    img = Image.new("RGB", (SHEET_W, 12000 + (flats.height if flats else 0)), PAPER)
    d = ImageDraw.Draw(img)
    cur = _Cursor(d)
    cur.text(title, TITLE_PT, bold=True, gap=4)
    cur.text(subtitle, SUB_PT, color=MUTED)
    cur.rule()

    cur.text(t("Flat layout (printed side as seen from outside the box, with dieline)"), H2_PT, bold=True)
    avail = SHEET_W - 2 * MARGIN
    flat = unfolded.resize(
        (avail, round(unfolded.height * avail / unfolded.width)), Image.Resampling.LANCZOS
    )
    img.paste(flat, (MARGIN, cur.y))
    d.rectangle([MARGIN, cur.y, MARGIN + flat.width, cur.y + flat.height], outline=LINE, width=2)
    cur.y += flat.height + GAP
    cur.rule()

    if flats is not None:
        img.paste(flats, (0, cur.y))
        cur.y += flats.height
        cur.rule()

    cur.text(t("Comparison with the chosen concept image (main view)"), H2_PT, bold=True)
    half = (avail - GAP) // 2
    shown = []
    if concept is not None:
        shown.append((t("Chosen concept image (direction reference, not print content)"), _fit_h(concept, COMPARE_H, half)))
    if pair is not None:
        shown.append((t("Final artwork: front + right side (what will be printed)"), _fit_h(pair, COMPARE_H, half)))
    x, top = MARGIN, cur.y
    extra = 0
    for label, im in shown:
        img.paste(im, (x, top))
        d.rectangle([x, top, x + im.width, top + im.height], outline=LINE, width=2)
        h = draw_caption(d, (x + 8, top + im.height + 6), label, max(im.width, half) - 8, SMALL_PT)
        extra = max(extra, h - int(SMALL_PT * 1.35))
        x += im.width + GAP
    if shown:
        cur.y = top + max(im.height for _, im in shown) + extra + int(SMALL_PT * 1.6) + GAP
    if concept is None:
        cur.text(t("(No concept image chosen yet)"), SMALL_PT + 2, color=MUTED)
    cur.text(t("Differences from the flat concepts and concept image:") if flats is not None
             else t("Differences from the concept image:"), SMALL_PT + 4, bold=True, gap=4)  # fmt: skip
    if diffs:
        for item in diffs:
            cur.text("· " + item, SMALL_PT + 2, x=MARGIN + 24, gap=2)
    else:
        cur.text(
            t("(Not written yet: once a concept image is chosen, spell out the differences)"), SMALL_PT + 2, color=MUTED, x=MARGIN + 24
        )
    cur.y += GAP // 2
    cur.rule()

    content_section(cur, content)
    findings_section(cur, findings)
    cur.text(t(FOOTER), SMALL_PT + 2, bold=True, color=MUTED)
    return img.crop((0, 0, SHEET_W, min(cur.y + MARGIN // 2, img.height)))


def _split_lines(diffs: list[str]) -> list[str]:
    """一条 --diff 里带换行的拆成几条（STE 测试 S-07：以前画对照页时崩溃），空行去掉。"""
    return [ln.strip() for d in diffs for ln in d.splitlines() if ln.strip()]


def make_flat(directory: Path, diffs: list[str] | None = None) -> Result:
    """说明文字用交付件语言（工厂配置档 document_language），和刀版确认图、检查报告一致。"""
    from pacxio import dielines

    with use_language(dielines.job_output_language(directory)):
        return _make_flat(directory, diffs)


def _make_flat(directory: Path, diffs: list[str] | None = None) -> Result:
    """和展开平面图逐面对照（agent 用）：正式稿和用户确认过的平面图差在哪，`--diff` 写下来。

    差别说明会显示在工作台 ⑤ 刀版确认图旁边；对照图（vs_flat_<面>.png）和整页对照
    （00_flat_sheet.png）只给 agent 看。2026-10-22 起不再有"确认平铺"：确认的是刀版确认图（approve）。"""
    from pacxio import dielines
    from pacxio_pro import pipeline
    from pacxio_pro.color.icc import icc_name
    from pacxio_pro.preflight.checks import (
        check_t01,
        check_t02,
        check_t04,
        check_t05,
        check_t06,
        layout_warnings,
    )

    r = Result(command="flat", job=str(directory))
    whole_job, _ = dielines.load_job(directory)
    if whole_job is not None and whole_job.style in ("tray_lid", "drawer"):
        return _make_flat_parts(directory, diffs, r)
    p, errs = pipeline.prepare(directory, dpi=FLAT_DPI)
    if p is None:
        r.errors += errs
        r.ok = False
        r.exit_code = 2 if any(i.id == "JOB" for i in errs) else 1
        return r
    if p.job.style not in FLAT_PAGE_STYLES:
        return r.fail(Issue(id="ARG", message=t("Style {style} does not support the flat confirmation page yet", style=p.job.style),
                            fix=t("Supported styles: {styles}", styles=join(sorted(FLAT_PAGE_STYLES)))), 2)  # fmt: skip
    fingerprint = design_fingerprint(directory, p.job)
    previous = read_manifest(directory) or {}
    if diffs is None:  # 没给新的差别说明：沿用上一次的
        diffs = list(previous.get("diffs") or [])
    diffs = _split_lines(diffs)

    art, m = render_art(p.layout, p.dl, FLAT_DPI)
    unfolded = _unfolded(art, m, p.dl)
    pair = _crop_faces(art, m, p.dl, ("front", "side_b"))
    # 比例不合格的平面图要重出，也不拿来对照（对照它只会把错的比例当成目标）
    redo = {i: c.issue for i, c in flat_checks(directory, p.dl).items() if c.issue is not None}
    rows, no_flat, flat_issues = flat_rows(directory, p, art, m, skip=set(redo))
    chosen = read_chosen(directory)
    concept = None
    if chosen is not None and (directory / chosen.file).is_file():
        concept = Image.open(directory / chosen.file).convert("RGB")

    box = build_box(p.dl)
    content = collect_content(p.job, p.dl, box)
    orientation_ok = bool(p.dl.style_meta.get("orientation_verified"))
    inv_findings, _ = check_content(box, content, [], orientation_ok, p.job,
                                    content_status(directory, p.job), icc_name(p.layout.canvas.icc_path))  # fmt: skip
    lay = p.layout
    findings = inv_findings + check_t01(lay, p.dl) + check_t02(lay) + check_t04(lay)
    findings += (
        check_t05(lay, p.profile) + check_t06(lay) + layout_warnings(lay)  # 占位框由 MK11 报
    )

    dims = p.dl.style_meta["dims"]
    name = dielines.style_name(p.dl.style_meta, p.job.style)
    if p.dl.style_meta.get("layout") in ("label", "pouch"):
        subtitle = t("{name}  flat {w} x {h} mm  material {material}", name=name, w=f"{p.dl.bbox[2]:.1f}",
                     h=f"{p.dl.bbox[3]:.1f}", material=p.job.material.preset or t("Custom"))  # fmt: skip
    elif p.dl.style_meta.get("layout") == "flat":
        subtitle = t("{name}  flat {w} x {h} mm  caliper {caliper} mm", name=name, w=f"{p.dl.bbox[2]:g}",
                     h=f"{p.dl.bbox[3]:g}", caliper=f"{p.dl.caliper_mm:g}")  # fmt: skip
    else:
        subtitle = t("{name} ({style})  inner size {l} x {w} x {h} mm  flat {fw} x {fh} mm", name=name,
                     style=p.dl.style.upper(), l=f"{dims['Li']:g}", w=f"{dims['Wi']:g}", h=f"{dims['Hi']:g}",
                     fw=f"{p.dl.bbox[2]:g}", fh=f"{p.dl.bbox[3]:g}")  # fmt: skip
    flats = flat_section(rows, no_flat) if rows else None
    sheet = build_flat_sheet(t("Flat confirmation page  {job}", job=p.job.job_id), subtitle, unfolded, concept, pair, diffs,
                             content, findings, flats)  # fmt: skip
    return _write_flat(directory, r, sheet, unfolded, rows, no_flat, fingerprint, p.job, chosen, diffs,
                       findings, redo, flat_issues)  # fmt: skip


def _write_flat(
    directory: Path, r: Result, sheet: Image.Image, unfolded: Image.Image, rows: list[FlatRow],
    no_flat: list[str], fingerprint: str, job: Any, chosen: Any, diffs: list[str], findings: list[Finding],
    redo: dict[str, Any], flat_issues: list[Issue],
) -> Result:  # fmt: skip
    """写对照页、逐面大图和 manifest，汇报差别说明和检查。"""
    out = directory / FLAT_DIR
    out.mkdir(exist_ok=True)
    sheet_file = out / SHEET_NAME
    sheet.save(sheet_file)
    flat_file = out / "flat.png"
    unfolded.save(flat_file)
    for old in out.glob(DETAIL_FILE.format(face="*")):
        old.unlink()  # 面或平面图变了：旧的逐面大图不留
    details = []
    for row in rows:
        details.append(out / DETAIL_FILE.format(face=row.face))
        flat_detail(row, DETAIL_PX_PER_MM * 25.4 / FLAT_DPI).save(details[-1])
    manifest = {
        "fingerprint": fingerprint,
        "style_version": style_version(job),
        "created": _now(),
        "diffs": diffs,
        "concept": chosen.id if chosen else None,
        "flats": [row.record() for row in rows],
        "faces_without_flat": no_flat if rows else [],
        "findings": [f.model_dump(exclude_none=True) for f in findings],
    }
    (out / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files = [sheet_file, flat_file, *details, out / MANIFEST]
    r.artifacts = [str(f) for f in files]
    r.data = {"sheet": str(sheet_file), "diffs": diffs,
              "concept": chosen.id if chosen else None, "findings": len(findings),
              "flats": [row.record() for row in rows], "flats_to_redo": sorted(redo)}  # fmt: skip
    r.warnings += flat_issues + [i for i in redo.values() if i is not None]
    if (chosen is not None or rows) and not diffs:
        r.warnings.append(
            Issue(
                id="FLAT",
                message=t("No notes yet on the differences from the flat concepts / concept image") if rows
                else t("No notes yet on the differences from the concept image"),
                fix=t('List the differences one by one (title size, photo position, side panel layout…): `flat <job> --diff "…" --diff "…"`'),
            )
        )
    for f in findings:
        if f.level in ("error", "warning") and f.id not in ("MK04",):
            r.warnings.append(Issue(id=f.id, message=f.message, fix=f.fix))
    pending = open_notes_issue(directory)
    if pending is not None:
        r.warnings.append(pending)
    return r


def _set_face_name(face: str) -> str:
    """多部件盒型的面：天盖 lid.top → 盖顶面（按合上后的位置叫）。"""
    part, local = face.split(".", 1)
    names = {"top": "顶面", "bottom": "底面"}
    return t("{part}·{face}", part=display_name(PART_ZH.get(part, part)),
             face=display_name(names.get(local) or FACE_NAMES.get(local, local)))


PART_ZH = {"base": "地盒", "lid": "天盖", "tray": "内托", "sleeve": "外套"}


def _part_engine_face(
    whole: Any, by_part: dict[str, Any], arts: dict[str, Any], face: str
) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """全名的面（lid.top）在所属部件版面上的样子（正立）和它在部件刀版上的矩形：目录刀版的矩形挪回去。"""
    part, local = face.split(".", 1)
    q = by_part[part]
    art, m = arts[part]
    panel = q.dl.panel(local)
    dx = whole.panel(face).polygon.bbox()[0] - panel.polygon.bbox()[0]
    x0, y0, x1, y1 = face_rects(whole)[face]
    rect = (x0 - dx, y0, x1 - dx, y1)
    return upright(_crop_rect(art, m, rect), panel), rect


def _part_rows(
    directory: Path, same_faces: dict[str, str] | None, whole: Any, by_part: dict[str, Any],
    arts: dict[str, Any], skip: set[str],
) -> tuple[list[FlatRow], list[str], list[Issue]]:  # fmt: skip
    """多部件盒型：按合上后看得到的面（lid.top……）逐面对照，正式稿取各部件自己的版面。"""
    at = whole.style_meta.get("set_faces") or {}
    rects = face_rects(whole)
    sources, issues = flat_sources(directory, whole, same_faces, skip=skip)
    same = {tx: s for tx, s in (same_faces or {}).items() if tx in rects and s in rects}
    rows: list[FlatRow] = []
    no_flat: list[str] = []
    for face in (str(at[k]) for k in SET_ORDER if k in at):
        if face in same:
            continue
        if face not in sources:
            no_flat.append(face)
            continue
        mt, img, c, found = sources[face]
        engine, rect = _part_engine_face(whole, by_part, arts, face)
        ai = face_crop(img, c).resize(engine.size, Image.Resampling.LANCZOS)  # 躺着画的面转正
        part, local = face.split(".", 1)
        rows.append(
            FlatRow(
                face,
                _set_face_name(face),
                mt.id,
                c.face,
                c.aspect_error,
                found,
                ai,
                engine,
                panel=local,
                rect=rect,
                part=part,
            )  # fmt: skip
        )
    return rows, no_flat, issues


def job_flat_rows(
    directory: Path, parts: list[Any], dset: Any, arts: dict[Any, Any]
) -> tuple[list[FlatRow], list[str], list[Issue]]:
    """还原评分、量文字用：每个面的平面图对照行（单部件、多部件都走这里）。arts：部件 → render_art 的结果。"""
    from pacxio import dielines

    if not dset.multipart:
        p = parts[0]
        art, m = arts[None]
        redo = {i for i, c in flat_checks(directory, p.dl).items() if c.issue is not None}
        return flat_rows(directory, p, art, m, skip=redo)
    whole = dset.merged()
    job, _ = dielines.load_job(directory)
    redo = {i for i, c in flat_checks(directory, whole).items() if c.issue is not None}
    by_part = {str(q.part): q for q in parts}
    same = job.same_faces if job is not None else None
    return _part_rows(directory, same, whole, by_part, {str(k): v for k, v in arts.items()}, redo)


def _make_flat_parts(directory: Path, diffs: list[str] | None, r: Result) -> Result:
    """多部件盒型（天地盖、抽屉盒）的平面图对照：按合上后看得到的面（lid.top……）逐面对照，正式稿取
    各部件自己的版面（天地盖测试 F-15）。"""
    from pacxio import dielines
    from pacxio.mockup.fold import FACE_LABELS, BoxModel
    from pacxio.mockup.scene import build_scene
    from pacxio_pro import pipeline
    from pacxio_pro.color.icc import icc_name
    from pacxio_pro.preflight.checks import (
        check_t01,
        check_t02,
        check_t04,
        check_t05,
        check_t06,
        layout_warnings,
    )

    parts, dset, errs = pipeline.prepare_parts(directory, dpi=FLAT_DPI)
    job, jerrs = dielines.load_job(directory)
    if errs or dset is None or job is None:
        r.errors += errs + jerrs
        r.ok, r.exit_code = False, 2 if any(i.id == "JOB" for i in errs + jerrs) else 1
        return r
    whole = dset.merged()
    fingerprint = design_fingerprint(directory, job)
    previous = read_manifest(directory) or {}
    if diffs is None:
        diffs = list(previous.get("diffs") or [])
    diffs = _split_lines(diffs)
    by_part = {str(q.part): q for q in parts}
    arts = {part: render_art(q.layout, q.dl, FLAT_DPI) for part, q in by_part.items()}
    at = whole.style_meta.get("set_faces") or {}
    redo = {i: c.issue for i, c in flat_checks(directory, whole).items() if c.issue is not None}
    rows, no_flat, flat_issues = _part_rows(
        directory, job.same_faces, whole, by_part, arts, set(redo)
    )

    def engine_face(face: str) -> Image.Image:
        return _part_engine_face(whole, by_part, arts, face)[0]

    pair = None
    if "front" in at and "side_b" in at:
        a, b = engine_face(str(at["front"])), engine_face(str(at["side_b"]))
        pair = Image.new("RGB", (a.width + b.width, max(a.height, b.height)), "white")
        pair.paste(a, (0, 0))
        pair.paste(b, (a.width, 0))
    unfolded_parts = [_unfolded(*arts[part], q.dl) for part, q in by_part.items()]
    unfolded = Image.new("RGB", (sum(u.width for u in unfolded_parts) + GAP * (len(unfolded_parts) - 1),
                                 max(u.height for u in unfolded_parts)), PAPER)  # fmt: skip
    x = 0
    for u in unfolded_parts:
        unfolded.paste(u, (x, 0))
        x += u.width + GAP
    chosen = read_chosen(directory)
    concept = None
    if chosen is not None and (directory / chosen.file).is_file():
        concept = Image.open(directory / chosen.file).convert("RGB")
    closed = build_scene(job.style, {str(k): v for k, v in dset.parts.items()})
    box = BoxModel(x=closed.box.x, y=closed.box.y, z=closed.box.z,
                   faces=[f for f in closed.box.faces if f.key in FACE_LABELS])  # fmt: skip
    content = collect_content(job, whole, box)
    orientation_ok = all(bool(q.dl.style_meta.get("orientation_verified")) for q in parts)
    findings, _ = check_content(box, content, [], orientation_ok, job, content_status(directory, job),
                                icc_name(parts[0].layout.canvas.icc_path))  # fmt: skip
    for q in parts:
        lay = q.layout
        for f in (check_t01(lay, q.dl) + check_t02(lay) + check_t04(lay) + check_t05(lay, q.profile)
                  + check_t06(lay) + layout_warnings(lay)):  # fmt: skip
            findings.append(f.model_copy(update={"message": t("{part}: {message}", part=dset.shown(q.part), message=f.message)}))
    set_meta = parts[0].dl.style_meta.get("set") or {}
    lc, wc, hc = set_meta.get("closed_outer") or (0.0, 0.0, 0.0)
    subtitle = t("{name} ({style})  closed outer size {l} x {w} x {h} mm", name=dielines.style_name(parts[0].dl.style_meta, job.style),
                 style=job.style.upper(), l=f"{lc:.1f}", w=f"{wc:.1f}", h=f"{hc:.1f}")  # fmt: skip
    flats = flat_section(rows, no_flat) if rows else None
    sheet = build_flat_sheet(t("Flat comparison  {job}", job=job.job_id), subtitle, unfolded, concept, pair, diffs,
                             content, findings, flats)  # fmt: skip
    return _write_flat(directory, r, sheet, unfolded, rows, no_flat, fingerprint, job, chosen, diffs,
                       findings, redo, flat_issues)  # fmt: skip


__all__ = ["FLAT_DIR", "FlatRow", "flat_rows", "flat_section", "job_flat_rows", "make_flat"]
