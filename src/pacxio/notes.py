"""批注：用户在工作台上点一下 / 框一块写的意见，带着位置交给 agent（ 节）。

- 存在 `review/notes.json`，**不进 job.json**：批注不是设计输入，不影响重建输出，也不影响确认的设计指纹；
- 位置换算靠图片旁边的对照（`preview.map.json`、`plan/maps.json`，格式见 layout/elements.py）：
  图上位置 → 部件、面、面局部毫米、框里的元素；
- 每条批注存一张截图 `review/<id>.png`（框选区域连同周围一圈，画上框），图后来重出也看得到当时指的是什么；
- 状态：open（等 agent）→ answered（agent 改完 / 回了话，等用户看）→ resolved（关掉）。
  用户再回复会回到 open；agent 不关用户的批注（除非用户在对话里明确说可以关）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, ValidationError

from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.visual.meta import now_iso

REVIEW_DIR = "review"
NOTES_FILE = "notes.json"
LOCK_FILE = ".lock"
USER = "用户"
AGENT = "agent"
MAX_TEXT = 2000
MAX_ELEMENTS = 8
PIN_TOL_MM = 1.0  # 图钉落点周围这么多毫米内的元素都算
SNAP_PAD_RATIO = 0.35  # 截图在框外多留框大小的这么多
SNAP_PAD_MIN_PX = 60
SNAP_PIN_RATIO = 0.08  # 图钉的截图在落点周围多留图宽的这么多（只截一小块看不出是哪里）
SNAP_MIN_PX = 360  # 截图太小就放大到这么宽，便于看
SNAP_MAX_PX = 1200
SNAP_INK = (225, 111, 36)
SNAP_LINE_PX = 3
PIN_R_PX = 9
MAP_SUFFIX = ".map.json"
PLAN_MAPS = "plan/maps.json"
CARD_NAMES = {
    "content": "Content list", "plan": "Layout draft", "concept": "Concepts", "rectified": "Rectified flats",
    "design": "Final artwork", "flat": "Flat proof", "mockup": "Mockup", "delivery": "Delivery pack",
}  # fmt: skip
VIEW_HINTS = {
    "view_front_right.png": ("效果图视角：正面 + 右侧 + 顶面", "Mockup view: front, right, and top"),
    "view_back_left.png": ("效果图视角：背面 + 左侧 + 顶面", "Mockup view: back, left, and top"),
    "view_bottom.png": ("效果图视角：底面", "Mockup view: bottom"),
    "view_open.png": ("效果图视角：打开", "Mockup view: open"),
}
CONCEPT_HINT = "概念图：只作参考，位置是图上的相对位置"
HINT_MESSAGES = {saved: message for saved, message in VIEW_HINTS.values()}
HINT_MESSAGES[CONCEPT_HINT] = "Concept image: location is relative to the image"

Status = Literal["open", "answered", "resolved"]
Shape = Literal["pin", "box", "item", "none"]
Box = tuple[float, float, float, float]


class Reply(BaseModel):
    by: str
    at: str
    text: str


class Target(BaseModel):
    """批注指的是什么。rel 是图上位置（0–1，左上角为原点；图钉的 x0 = x1、y0 = y1）。"""

    card: str | None = None
    file: str | None = None
    file_sha256: str | None = None
    shape: Shape = "none"
    rel: Box | None = None
    part: str | None = None
    panel: str | None = None
    box_mm: Box | None = None  # 面局部：x, y, 宽, 高（面正立时左下角为原点；和 art --box 同一套）
    world_mm: Box | None = None  # 刀版坐标 x0, y0, x1, y1（多部件盒型是这个部件的刀版）
    elements: list[dict[str, Any]] = Field(default_factory=list)  # 最上层的在前
    faces: list[str] | None = None  # 只知道"大概是哪几个面"时（概念图、平铺对照）
    hint: str | None = None
    item: str | None = None  # 信息清单条目 id


class Note(BaseModel):
    id: str
    created: str
    updated: str
    by: str
    status: Status
    text: str
    target: Target = Field(default_factory=Target)
    snapshot: str | None = None
    replies: list[Reply] = Field(default_factory=list)
    resolved_at: str | None = None
    resolved_by: str | None = None


class NoteBook(BaseModel):
    notes: list[Note] = Field(default_factory=list)

    def get(self, nid: str) -> Note | None:
        return next((n for n in self.notes if n.id == nid), None)


# ---------------- 读写 ----------------


def notes_file(directory: Path) -> Path:
    return directory / REVIEW_DIR / NOTES_FILE


def load_notes(directory: Path) -> NoteBook:
    f = notes_file(directory)
    if not f.is_file():
        return NoteBook()
    return NoteBook.model_validate_json(f.read_text(encoding="utf-8"))


def _save(directory: Path, book: NoteBook) -> Path:
    f = notes_file(directory)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(book.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    return f


@contextlib.contextmanager
def _locked(directory: Path) -> Iterator[None]:
    """工作台和命令行可能同时写：POSIX 上用文件锁排队。"""
    d = directory / REVIEW_DIR
    d.mkdir(parents=True, exist_ok=True)
    with (d / LOCK_FILE).open("a") as fh:
        try:
            import fcntl
        except ImportError:  # Windows：不加锁
            yield
            return
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _bad_file_issue(e: Exception) -> Issue:
    return Issue(id="NOTE", message=t("Could not read review/notes.json: {error}", error=e),
                 fix=t("Check the file for damage. If needed, rename it to keep a backup and add the notes again."))  # fmt: skip


def _next_id(book: NoteBook) -> str:
    nums = [int(n.id[1:]) for n in book.notes if n.id[1:].isdigit()]
    return f"n{max(nums, default=0) + 1}"


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


# ---------------- 位置换算 ----------------


def read_map(directory: Path, file: str) -> dict[str, Any] | None:
    """图片的对照：旁边的 <名>.map.json，或 plan/maps.json 里这张图的一项。"""
    side = (directory / file).with_suffix(MAP_SUFFIX)
    try:
        if side.is_file():
            data = json.loads(side.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        plan = directory / PLAN_MAPS
        if file.startswith("plan/") and plan.is_file():
            entry = json.loads(plan.read_text(encoding="utf-8")).get("images", {}).get(file)
            return entry if isinstance(entry, dict) else None
    except (OSError, ValueError):
        return None
    return None


def _apply(frame: list[float], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = frame
    return a * x + c * y + e, b * x + d * y + f


def _invert(frame: list[float]) -> list[float]:
    a, b, c, d, e, f = frame
    det = a * d - b * c
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return [ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f)]


def _bounds(pts: list[tuple[float, float]]) -> Box:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _corners(b: Box) -> list[tuple[float, float]]:
    return [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]


def rel_to_world(m: dict[str, Any], rel: Box) -> Box:
    """图上位置（0–1）→ 刀版坐标外框。"""
    w, h = m["size_px"]
    if m.get("space") == "panel":
        u0, v0, u1, v1 = m["local_bbox"]
        local = [
            (u0 + rx * (u1 - u0), v1 - ry * (v1 - v0))
            for rx, ry in ((rel[0], rel[1]), (rel[2], rel[3]))
        ]
        return _bounds([_apply(m["frame"], x, y) for x, y in _corners(_bounds(local))])
    k = m["dpi"] / 25.4
    xs = [m["x0_mm"] + rel[0] * w / k, m["x0_mm"] + rel[2] * w / k]
    ys = [m["y1_mm"] - rel[1] * h / k, m["y1_mm"] - rel[3] * h / k]
    return min(xs), min(ys), max(xs), max(ys)


def world_to_rel(m: dict[str, Any], world: Box) -> Box:
    """刀版坐标外框 → 图上位置（0–1）。"""
    w, h = m["size_px"]
    if m.get("space") == "panel":
        u0, v0, u1, v1 = m["local_bbox"]
        lb = _bounds([_apply(_invert(m["frame"]), x, y) for x, y in _corners(world)])
        return (
            (lb[0] - u0) / (u1 - u0),
            (v1 - lb[3]) / (v1 - v0),
            (lb[2] - u0) / (u1 - u0),
            (v1 - lb[1]) / (v1 - v0),
        )
    k = m["dpi"] / 25.4
    return ((world[0] - m["x0_mm"]) * k / w, (m["y1_mm"] - world[3]) * k / h,
            (world[2] - m["x0_mm"]) * k / w, (m["y1_mm"] - world[1]) * k / h)  # fmt: skip


def _area(b: list[float] | Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a: list[float] | Box, b: list[float] | Box) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _pick_panel(m: dict[str, Any], world: Box) -> dict[str, Any] | None:
    if m.get("space") == "panel":
        return next((p for p in m["panels"] if p["id"] == m["panel"]), None)
    cx, cy = (world[0] + world[2]) / 2, (world[1] + world[3]) / 2
    inside = [
        p
        for p in m["panels"]
        if p["bbox"][0] <= cx <= p["bbox"][2] and p["bbox"][1] <= cy <= p["bbox"][3]
    ]
    if inside:
        smallest: dict[str, Any] = min(inside, key=lambda p: _area(p["bbox"]))
        return smallest
    best: dict[str, Any] | None = max(
        m["panels"], key=lambda p: _overlap(p["bbox"], world), default=None
    )
    return best if best is not None and _overlap(best["bbox"], world) > 0 else None


def _hits(m: dict[str, Any], world: Box, pin: bool) -> list[dict[str, Any]]:
    probe = (
        (world[0] - PIN_TOL_MM, world[1] - PIN_TOL_MM, world[2] + PIN_TOL_MM, world[3] + PIN_TOL_MM)
        if pin
        else world
    )
    hits = [e for e in m.get("elements", []) if _overlap(e["bbox"], probe) > 0]
    front = [e for e in hits if not e.get("background")]
    chosen = sorted(front or hits, key=lambda e: -int(e.get("order", 0)))
    keys = ("id", "kind", "panel", "summary", "content", "tag")
    return [{k: e.get(k) for k in keys if e.get(k) is not None} for e in chosen[:MAX_ELEMENTS]]


def locate(
    directory: Path,
    card: str | None,
    file: str | None,
    shape: Shape,
    rel: Box | None = None,
    item: str | None = None,
) -> Target:
    """把"在哪张图的哪个位置"换算成部件、面、面局部毫米和框里的元素（换算不了的只留图上位置）。"""
    t = Target(card=card, file=file, shape=shape, rel=rel, item=item)
    if file:
        t.file_sha256 = _sha(directory / file)
    if file and rel is not None:
        m = read_map(directory, file)
        if m is not None:
            world = rel_to_world(m, rel)
            panel = _pick_panel(m, world)
            t.world_mm = tuple(round(v, 2) for v in world)  # type: ignore[assignment]
            t.part = m.get("part")
            t.elements = _hits(m, world, shape == "pin")
            if panel is not None:
                t.panel = panel["id"]
                lb = _bounds([_apply(_invert(panel["frame"]), x, y) for x, y in _corners(world)])
                t.box_mm = (
                    round(lb[0], 1),
                    round(lb[1], 1),
                    round(lb[2] - lb[0], 1),
                    round(lb[3] - lb[1], 1),
                )
    name = Path(file).name if file else (card or "").partition(":")[2]  # 卡片 id 的后半就是文件名
    if card and card.startswith("mockup:") and name in VIEW_HINTS:
        t.hint = VIEW_HINTS[name][0]
    elif card and card.startswith("flat:") and name.startswith("vs_flat_"):
        t.faces = [name[len("vs_flat_") : -len(".png")]]
    elif card and (card.startswith("concept:") or card.startswith("rectified:")):
        t.hint = CONCEPT_HINT
        cid = card.split(":", 1)[1]
        meta = directory / "concepts" / f"{cid}.meta.json"
        if meta.is_file():
            with contextlib.suppress(OSError, ValueError):
                faces = json.loads(meta.read_text(encoding="utf-8")).get("faces")
                t.faces = [str(f) for f in faces] if isinstance(faces, list) else None
    return t


def snapshot(directory: Path, note: Note) -> str | None:
    """截图：框选区域连同周围一圈，画上框 / 图钉，存 review/<id>.png。"""
    t = note.target
    if not t.file or t.rel is None or not (directory / t.file).is_file():
        return None
    with Image.open(directory / t.file) as src:
        img = src.convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = t.rel[0] * w, t.rel[1] * h, t.rel[2] * w, t.rel[3] * h
    pad = max(SNAP_PAD_MIN_PX, SNAP_PAD_RATIO * max(x1 - x0, y1 - y0))
    if t.shape == "pin":
        pad = max(pad, SNAP_PIN_RATIO * w)
    cx0, cy0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    cx1, cy1 = min(w, int(x1 + pad) + 1), min(h, int(y1 + pad) + 1)
    crop = img.crop((cx0, cy0, cx1, cy1))
    scale = 1.0
    if crop.width < SNAP_MIN_PX:
        scale = SNAP_MIN_PX / crop.width
    elif crop.width > SNAP_MAX_PX:
        scale = SNAP_MAX_PX / crop.width
    if scale != 1.0:
        crop = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
            Image.Resampling.LANCZOS,
        )
    draw = ImageDraw.Draw(crop)
    bx0, by0, bx1, by1 = (
        (x0 - cx0) * scale,
        (y0 - cy0) * scale,
        (x1 - cx0) * scale,
        (y1 - cy0) * scale,
    )
    if t.shape == "pin":
        r = PIN_R_PX
        draw.ellipse((bx0 - r, by0 - r, bx0 + r, by0 + r), outline=SNAP_INK, width=SNAP_LINE_PX)
        draw.line((bx0 - 2 * r, by0, bx0 + 2 * r, by0), fill=SNAP_INK, width=1)
        draw.line((bx0, by0 - 2 * r, bx0, by0 + 2 * r), fill=SNAP_INK, width=1)
    else:
        draw.rectangle((bx0, by0, bx1, by1), outline=SNAP_INK, width=SNAP_LINE_PX)
    out = directory / REVIEW_DIR / f"{note.id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out)
    return out.relative_to(directory).as_posix()


def describe(note: Note) -> str:
    """一行位置说明，如"正式稿 · lid.top · 面内 12,140 起 54×48 mm · text7「Warning…」"。"""
    target = note.target
    parts: list[str] = []
    if target.card:
        name = CARD_NAMES.get(target.card.split(":", 1)[0])
        parts.append(t(name) if name else target.card)
    if target.panel:
        parts.append(f"{target.part}.{target.panel}" if target.part else target.panel)
    if target.box_mm:
        x, y, bw, bh = target.box_mm
        parts.append(
            t("At ({x:g}, {y:g}) on the panel", x=x, y=y)
            if target.shape == "pin"
            else t("On the panel at ({x:g}, {y:g}), {w:g}×{h:g} mm", x=x, y=y, w=bw, h=bh)
        )
    elif target.faces:
        parts.append(t("Possible panels: {faces}", faces=t(", ").join(target.faces)))
    if target.hint:
        hint = HINT_MESSAGES.get(target.hint)
        parts.append(t(hint) if hint else target.hint)
    if target.item:
        parts.append(t("Item {id}", id=target.item))
    if target.elements:
        e = target.elements[0]
        parts.append(
            t("{id} “{summary}”", id=e["id"], summary=e["summary"]) if e.get("summary") else e["id"]
        )
    return " · ".join(parts) if parts else t("(General note)")


# ---------------- 操作 ----------------


def _text_issue(text: str) -> Issue | None:
    if not text.strip():
        return Issue(
            id="NOTE", message=t("The note is empty"), fix=t("Describe what needs to change")
        )
    if len(text) > MAX_TEXT:
        return Issue(
            id="NOTE",
            message=t("Note too long ({n} characters, limit {limit})", n=len(text), limit=MAX_TEXT),
            fix=t("Split it into several notes"),
        )
    return None


def _note_data(note: Note) -> dict[str, Any]:
    return {**note.model_dump(mode="json"), "where": describe(note)}


def add_note(
    directory: Path,
    text: str,
    by: str = USER,
    card: str | None = None,
    file: str | None = None,
    shape: Shape = "none",
    rel: Box | None = None,
    item: str | None = None,
) -> Result:
    """加一条批注；有图上位置就换算、截图。用户的批注是 open（等 agent），agent 的是 answered（等用户看）。"""
    r = Result(command="notes add", job=str(directory))
    bad = _text_issue(text)
    if bad is not None:
        return r.fail(bad, 2)
    if rel is not None and not all(0 <= v <= 1 for v in rel):
        return r.fail(Issue(id="NOTE", message=t("Image coordinates must be between 0 and 1"), fix=t("Check rel")), 2)
    if file is not None and _unsafe(directory, file):
        return r.fail(
            Issue(
                id="NOTE", message=t("The job does not contain {file}", file=file), fix=t("Use a file path relative to the job folder")
            ),
            2,
        )
    target = locate(directory, card, file, shape, rel, item)
    with _locked(directory):
        try:
            book = load_notes(directory)
        except (ValueError, ValidationError) as e:
            return r.fail(_bad_file_issue(e))
        now = now_iso()
        note = Note(id=_next_id(book), created=now, updated=now, by=by, status="open" if by == USER else "answered",
                    text=text.strip(), target=target)  # fmt: skip
        note.snapshot = snapshot(directory, note)
        book.notes.append(note)
        r.artifacts = [str(_save(directory, book))] + (
            [str(directory / note.snapshot)] if note.snapshot else []
        )
    r.data = {"note": _note_data(note)}
    return r


def _unsafe(directory: Path, file: str) -> bool:
    root = directory.resolve()
    try:
        (root / file).resolve().relative_to(root)
    except ValueError:
        return True
    return not (root / file).is_file()


def add_note_at_panel(directory: Path, text: str, panel: str, box: Box, by: str = AGENT) -> Result:
    """agent 在正式稿上留言：面全名（多部件写 部件.面）+ 面局部的框（x, y, 宽, 高，mm）。"""
    part, _, pid = panel.rpartition(".")
    png = f"preview_{part}.png" if part else "preview.png"
    m = read_map(directory, png)
    if m is None:
        r = Result(command="notes add", job=str(directory))
        return r.fail(Issue(id="NOTE", message=t("The final artwork preview {path} is missing, so the note cannot be placed on the image", path=png),
                            fix=t("Run `pacxio render <job>` first, or omit --panel and --box to leave a general note")), 2)  # fmt: skip
    entry = next((p for p in m["panels"] if p["id"] == pid), None)
    if entry is None:
        r = Result(command="notes add", job=str(directory))
        ids = ", ".join(p["id"] for p in m["panels"])
        return r.fail(Issue(id="NOTE", message=t("The preview has no panel {panel}", panel=pid), fix=t("Available panels in the preview: {panels}", panels=ids)), 2)
    x, y, w, h = box
    world = _bounds([_apply(entry["frame"], px, py) for px, py in _corners((x, y, x + w, y + h))])
    rel = tuple(min(1.0, max(0.0, v)) for v in world_to_rel(m, world))
    shape: Shape = "pin" if w == 0 and h == 0 else "box"
    return add_note(directory, text, by, f"design:{png}", png, shape, rel, None)  # type: ignore[arg-type]


def _change(directory: Path, nid: str, command: str, edit: Any) -> Result:
    r = Result(command=command, job=str(directory))
    with _locked(directory):
        try:
            book = load_notes(directory)
        except (ValueError, ValidationError) as e:
            return r.fail(_bad_file_issue(e))
        note = book.get(nid)
        if note is None:
            ids = ", ".join(n.id for n in book.notes) or t("(no notes yet)")
            return r.fail(Issue(id="NOTE", message=t("Note {id} does not exist", id=nid), fix=t("Existing notes: {ids}", ids=ids)), 2)
        problem = edit(book, note)
        if isinstance(problem, Issue):
            return r.fail(problem, 2)
        note.updated = now_iso()
        r.artifacts = [str(_save(directory, book))]
    r.data = {"note": _note_data(note)} if note in book.notes else {"deleted": nid}
    return r


def reply_note(
    directory: Path, nid: str, text: str, by: str = AGENT, keep_open: bool = False
) -> Result:
    """回复。agent 回复后变成 answered（等用户看），除非 keep_open；用户回复后回到 open（等 agent）。"""
    bad = _text_issue(text)
    if bad is not None:
        return Result(command="notes reply", job=str(directory)).fail(bad, 2)

    def edit(book: NoteBook, note: Note) -> Issue | None:
        note.replies.append(Reply(by=by, at=now_iso(), text=text.strip()))
        if note.status != "resolved":
            note.status = "open" if by == USER or keep_open else "answered"
        return None

    return _change(directory, nid, "notes reply", edit)


def resolve_note(directory: Path, nid: str, by: str = USER) -> Result:
    def edit(book: NoteBook, note: Note) -> Issue | None:
        note.status, note.resolved_at, note.resolved_by = "resolved", now_iso(), by
        return None

    return _change(directory, nid, "notes resolve", edit)


def reopen_note(directory: Path, nid: str, by: str = USER) -> Result:
    def edit(book: NoteBook, note: Note) -> Issue | None:
        note.status, note.resolved_at, note.resolved_by = "open", None, None
        return None

    return _change(directory, nid, "notes reopen", edit)


def delete_note(directory: Path, nid: str, by: str = USER) -> Result:
    """只能删自己写的、还没人回复的批注（写错了、点错了）。"""

    def edit(book: NoteBook, note: Note) -> Issue | None:
        if note.by != by or note.replies:
            return Issue(id="NOTE", message=t("Cannot delete {id}. You can delete your own notes before anyone replies.", id=nid),
                         fix=t("Resolve the note if it is no longer needed"))  # fmt: skip
        book.notes.remove(note)
        if note.snapshot:
            (directory / note.snapshot).unlink(missing_ok=True)
        return None

    return _change(directory, nid, "notes delete", edit)


Which = Literal["unresolved", "open", "all"]


def list_notes(directory: Path, which: Which = "unresolved") -> Result:
    """unresolved：等 agent 的 + 等用户看的；open：只要等 agent 处理的；all：全部。"""
    r = Result(command="notes list", job=str(directory))
    try:
        book = load_notes(directory)
    except (ValueError, ValidationError) as e:
        return r.fail(_bad_file_issue(e))
    keep = {
        "unresolved": ("open", "answered"),
        "open": ("open",),
        "all": ("open", "answered", "resolved"),
    }[which]
    notes = [n for n in book.notes if n.status in keep]
    r.data = {
        "notes": [_note_data(n) for n in notes],
        "counts": {
            s: sum(1 for n in book.notes if n.status == s) for s in ("open", "answered", "resolved")
        },
    }
    return r


def open_notes_issue(directory: Path) -> Issue | None:
    """有等 agent 处理的批注时给一条提醒（render / mockup / flat / preflight / export 的输出里，不挡流程）。"""
    try:
        book = load_notes(directory)
    except (ValueError, ValidationError, OSError):
        return None
    ids = [n.id for n in book.notes if n.status == "open"]
    if not ids:
        return None
    shown = "、".join(ids[:6]) + ("……" if len(ids) > 6 else "")
    return Issue(
        id="NOTE",
        message=t("There are {count} open Workbench notes: {ids}", count=len(ids), ids=shown),
        fix=t("Run `pacxio notes list <job> --open` to see the notes, locations, and screenshots. After making changes, reply with `notes reply <job> <id> --text …`."),
    )


__all__ = [
    "AGENT", "USER", "Note", "NoteBook", "Reply", "Target", "add_note", "add_note_at_panel", "delete_note",
    "describe", "list_notes", "load_notes", "locate", "open_notes_issue", "read_map", "rel_to_world",
    "reopen_note", "reply_note", "resolve_note", "snapshot", "world_to_rel",
]  # fmt: skip
