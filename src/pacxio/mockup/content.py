"""效果图确认页的"信息清单"：每个面上有哪些品牌 / 产品文字、图片、工艺，以及完整性检查。

确认的前提是效果图带齐所有信息。引擎不知道哪些信息"必须有"（那取决于品类与市场的法规，
引擎无法判断），所以：文字 / 图片 / 工艺可以带 `tag`（如"品牌名""品名""净含量""配料"），
确认页按标签归类；`mockup --require 品名,净含量` 声明必须出现的标签，缺了就不能确认。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pacxio.layout.errors import LayoutError
from pacxio.layout.spaces import placement_panels as _panels
from pacxio.messages import display_name, t
from pacxio.mockup.fold import FACE_LABELS, BoxModel
from pacxio.model.content import PENDING_PLACEHOLDER, shown_as_logo, template_ids
from pacxio.model.dieline import Dieline
from pacxio.model.job import FINISH_ZH, Job, Placement
from pacxio.model.report import Finding

UNTAGGED = "未标注"
TEXT_PREVIEW_CHARS = 60


@dataclass(frozen=True)
class Item:
    kind: str  # text | image | vector | placeholder | finish
    id: str
    tag: str | None
    summary: str  # 文字内容 / 素材名 / 工艺类型
    panel: str


@dataclass
class ContentMap:
    by_face: dict[str, list[Item]] = field(default_factory=dict)
    hidden: list[Item] = field(default_factory=list)  # 放在效果图里看不到的面上的内容
    background_only: list[str] = field(default_factory=list)  # 只有底色 / 图案的面
    tags: list[str] = field(default_factory=list)  # 出现过的标签（去重，保持顺序）
    untagged_texts: int = 0
    backgrounds: list[str] = field(default_factory=list)  # 全局底色 / 图案说明


def _clip(s: str) -> str:
    s = s.replace("\n", " / ")
    return s if len(s) <= TEXT_PREVIEW_CHARS else s[: TEXT_PREVIEW_CHARS - 1] + "…"


def placement_panels(p: Placement, dl: Dieline) -> list[str]:
    offsets = dl.style_meta.get("part_offsets")
    if p.part and p.coord_space == "blank" and p.box is not None and isinstance(offsets, dict):
        # 多部件盒型的目录刀版：框是这个部件刀版里的坐标，挪到目录刀版里再找面、只算这个部件的面
        # （以前落到了左边内托的面上，抽屉测试 F-21）
        box = p.box.model_copy(update={"x": p.box.x + float(offsets.get(p.part, 0.0))})
        moved = p.model_copy(update={"box": box})
        return [f for f in placement_panels(moved.model_copy(update={"part": None}), dl)
                if f.startswith(f"{p.part}.")]  # fmt: skip
    try:
        return _panels(p, dl)
    except LayoutError:  # 盒型不支持 space=body：排版时会报错，这里不重复
        return []


def collect_content(job: Job, dl: Dieline, box: BoxModel) -> ContentMap:
    job = job.resolved()  # 引用信息清单的文字以清单为准
    face_of = {f.panel: f.key for f in box.faces}
    face_of.update({pid: f.key for f in box.faces for pid in f.also})
    cm = ContentMap(by_face={f.key: [] for f in box.faces})
    seen: list[str] = []

    def add(item: Item) -> None:
        key = face_of.get(item.panel)
        if key is None:
            cm.hidden.append(item)
        else:
            cm.by_face[key].append(item)
        if item.tag and item.tag not in seen:
            seen.append(item.tag)

    for p in job.placements:
        if p.kind == "text":
            assert p.panel is not None and p.text is not None
            add(Item("text", p.id, p.tag, _clip(p.text), p.panel))
            if not p.tag:
                cm.untagged_texts += 1
        elif p.kind == "placeholder":
            for panel in placement_panels(p, dl):
                add(Item("placeholder", p.id, p.tag, p.label or p.id, panel))
        elif p.kind in ("image", "vector"):
            # 跨面的图形（space=body / blank）在它覆盖到的每个面上各列一次
            summary = p.source or p.id
            if p.coord_space != "panel":
                summary += t(" (across panels)")
            for panel in placement_panels(p, dl):
                add(Item(p.kind, p.id, p.tag, summary, panel))
        else:  # fill / pattern：底色与图案
            names = p.scope_panels()
            where = t("whole outer surface") if names is None else t(", ").join(names)
            cm.backgrounds.append(t("{kind} {id} ({where})", kind=t("Background color") if p.kind == "fill" else t("Pattern"), id=p.id, where=where))
    for f in job.finishes:
        add(Item("finish", f.id, f.tag, display_name(FINISH_ZH.get(f.type, f.type)), f.panel))

    cm.tags = seen
    cm.background_only = [k for k, items in cm.by_face.items() if not items]
    return cm


def blocking_content(job: Job, content_status: str, required: list[str] | None = None) -> list[str]:
    """不补齐就不能确认的（只看 job，不用画图）：`--require` 声明的标签没出现、信息清单里必须的
    条目没放上 / 措辞待定、还有占位框。和效果图检查（check_content）里阻止确认的是同一套。"""
    resolved = job.resolved()
    tags = {p.tag for p in resolved.placements if p.tag} | {
        f.tag for f in resolved.finishes if f.tag
    }
    missing = [tag for tag in required or [] if tag not in tags]
    missing += _inventory_findings(job, content_status, [])
    missing += [t("Placeholder “{name}”", name=p.label or p.id) for p in job.placements if p.kind == "placeholder"]
    return missing


def _inventory_findings(job: Job, content_status: str, out: list[Finding]) -> list[str]:
    """信息清单：必须的条目都放上版面了吗、措辞定了吗、确认了吗。返回阻止确认的条目说明。"""
    inv = job.content
    if inv is None or not inv.items:
        return []
    placed = set(job.content_refs())
    blocking: list[str] = []
    for it in inv.items:
        what = t("{label} “{text}”", label=display_name(it.label), text=_clip(it.text)) if it.text else t("{label} ({id})", label=display_name(it.label), id=it.id)
        if it.required and it.id not in placed and shown_as_logo(inv, it, placed):
            out.append(
                Finding(
                    id="MK12",
                    level="info",
                    message=t("{what} appears only as a logo; the layout has no separate brand-name text", what=what),
                    fix=t("If needed, add a brand-name line with `art add ... --content {id}`", id=it.id),
                )
            )
        elif it.required and it.id not in placed:
            blocking.append(what)
            out.append(
                Finding(
                    id="MK01",
                    level="error",
                    message=t("Required {what} from the content list is not on the layout yet", what=what),
                    fix=t("Place it with `art add ... --content {id}` (the text follows the list); if it is really not needed, run `content edit <job> {id} --optional`", id=it.id),
                )
            )
        elif it.required and it.pending:
            blocking.append(t("{what} (pending)", what=what))
            out.append(
                Finding(
                    id="MK08",
                    level="error",
                    message=t("Required {what} is not settled yet: {pending}; the layout currently shows {shown}", what=what, pending=it.pending, shown=t("a draft") if it.text else t("the placeholder {text}", text=PENDING_PLACEHOLDER)),
                    fix=t("Once you have the exact content, run `content edit <job> {id} --text ... --clear pending` and ask the user to confirm the wording again", id=it.id),
                )
            )
    if content_status != "confirmed":
        out.append(
            Finding(
                id="MK09",
                level="warning",
                message=t("The wording of the content list has not been confirmed")
                if content_status == "none"
                else t("The content list changed after its wording was confirmed"),
                fix=t("Show the list to the user item by item; after they confirm, run `pacxio content confirm <job> --by <name>`"),
            )
        )
    unlinked = [
        p.id
        for p in job.placements
        if p.kind == "text" and not p.content and not template_ids(p.text)
    ]
    if unlinked:
        out.append(
            Finding(
                id="MK10",
                level="info",
                message=t("{n} text block(s) are not linked to the content list: {ids}", n=len(unlinked), ids=", ".join(unlinked)),
                fix=t("Use `art add --content <item>` or write {{item-id}} in the text to reference the list, so the text follows the list and changes with it"),
            )
        )
    return blocking


def check_content(
    box: BoxModel,
    cm: ContentMap,
    required: list[str],
    orientation_verified: bool,
    job: Job,
    content_status: str = "none",
    icc_name: str | None = None,
) -> tuple[list[Finding], list[str]]:
    """返回 (发现, 缺失的必须信息)。MK01 / MK08 为 error（会阻止确认），其余为 warning / info。"""
    out: list[Finding] = []
    missing = [tag for tag in required if tag not in cm.tags]
    for tag in missing:
        out.append(
            Finding(
                id="MK01",
                level="error",
                message=t("Required information “{tag}” is missing from the mockup", tag=tag),
                fix=t("Add this information and label it with `art add --tag {tag}` (or add the label to existing text, images or finishes)", tag=tag),
            )
        )
    missing += _inventory_findings(job, content_status, out)
    for p in job.placements:
        if p.kind == "placeholder":
            what = t("Placeholder “{name}”", name=p.label or p.id)
            missing.append(what)
            out.append(
                Finding(
                    id="MK11",
                    level="error",
                    message=t("{what} has not been replaced with a real image or graphic", what=what),
                    fix=t("Once the image is ready, replace the placeholder with `art fill <job> {id} --source <image>`, then make the mockup again", id=p.id),
                )
            )
    inside = [i for i in cm.hidden if i.panel in box.open_only]
    hidden = [i for i in cm.hidden if i.panel not in box.open_only]
    if hidden:
        names = t(", ").join(t("{id} (on {panel})", id=i.id, panel=i.panel) for i in hidden)
        out.append(
            Finding(
                id="MK02",
                level="warning",
                message=t("This content is on panels the mockup does not show (tuck flaps, dust flaps, ears, glue flap): {names}", names=names),
                fix=t("Check that they are meant to be inside the box; information that must be seen belongs on the front, back, sides, top or bottom"),
            )
        )
    if inside:
        names = t(", ").join(t("{id} (on {panel})", id=i.id, panel=i.panel) for i in inside)
        out.append(
            Finding(
                id="MK02",
                level="info",
                message=t("This content is hidden when the box is closed and only visible when {how}: {names}", how=display_name(box.open_hint) if box.open_hint else t("the box is opened"), names=names),
                fix=t("Check that it is meant to be seen only after opening (unboxing information)"),
            )
        )
    if not orientation_verified:
        out.append(
            Finding(
                id="MK03",
                level="warning",
                message=t("The artwork direction on the top and bottom has not been confirmed by a proof; the mockup draws it as it is now, and the real box may be rotated 90° or 180°"),
                fix=t("After the orientation test (fold the `export --proof` proof), confirm the text direction on the top and bottom"),
            )
        )
    if cm.background_only:
        faces = t(", ").join(
            display_name(FACE_LABELS[k]) if k in FACE_LABELS else t("Hanger") if k == "hanger" else k
            for k in cm.background_only
        )
        out.append(
            Finding(
                id="MK05",
                level="info",
                message=t("These panels have no text, images or finishes, only a background color or pattern: {faces}", faces=faces),
                fix=t("If these panels should carry information (brand, product description, barcode, etc.), add it"),
            )
        )
    if cm.untagged_texts:
        out.append(
            Finding(
                id="MK06",
                level="info",
                message=t("{n} text block(s) have no information label and are grouped as “{group}” on the confirmation page", n=cm.untagged_texts, group=t("Untagged")),
                fix=t("Label them with `--tag` (brand name / product name / net contents / ingredients / manufacturer …) so the confirmation page can check them by category"),
            )
        )
    if job.windows:
        out.append(
            Finding(
                id="MK13",
                level="info",
                message=t("The window shows a sketch of the inside of the box (not the product); film windows get a reflection"),
                fix=t("What is actually visible through the window depends on the product and insert; go by the sample box"),
            )
        )
    out.append(
        Finding(
            id="MK04",
            level="info",
            message=(
                t("The mockup is a sketch: colors are soft-proofed with {icc}, close to the printed look but not color-accurate", icc=icc_name)
                if icc_name
                else t("The mockup is a sketch: there is no ICC on this computer, so colors are converted from CMYK with a simple formula (brighter and darker than print), not color-accurate")
            )
            + t("; the gloss of foil, spot UV and embossing is simulated"),
            fix=t("Confirm the real look with a printed proof"),
        )
    )
    return out, missing
