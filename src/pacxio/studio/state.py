"""工作台：汇总一个 job 各阶段的产物与状态（ 节）。

纯函数：只读 job 目录，不写文件、不跑引擎；状态都用各模块现有的判断（确认记录、设计指纹），
不另记一份。以后 MCP 也能直接用。
"""

from __future__ import annotations

import functools
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from pacxio import dielines
from pacxio.cloud.config import use_cloud
from pacxio.concept.rectify import face_rects, face_size
from pacxio.concept.service import (
    CHOSEN,
    CONCEPT_DIR,
    FACE_ZH,
    IN_USE,
    ConceptMeta,
    FlatCheck,
    check_flat,
    concept_metas,
    flat_checks,
    flats_confirmation,
    in_use_versions,
    no_flat_faces,
    read_chosen,
    split_face_issues,
    version_group,
    version_groups,
)
from pacxio.inventory import confirmation_status as content_status
from pacxio.messages import display_name, lang, t, table
from pacxio.mockup import flat as flat_mod
from pacxio.mockup.approval import (
    MOCKUP_DIR,
    PLAN_DIR,
    approval_state,
    design_fingerprint,
    is_current,
)
from pacxio.mockup.approval import confirmation_status as plan_confirmation
from pacxio.mockup.content import blocking_content
from pacxio.mockup.render import BELOW_BACK_VIEW, OPEN_VIEW, VIEWS
from pacxio.mockup.scene3d import SCENE_FILE
from pacxio.mockup.service import VIEW_FILE
from pacxio.model.common import Issue
from pacxio.model.content import CATEGORIES
from pacxio.model.job import JOB_FILE, Job
from pacxio.model.profile import ProfileNotFoundError, load_materials, load_profile
from pacxio.model.report import Finding, Report
from pacxio.notes import USER, describe, load_notes
from pacxio.styles import get_style

Tone = Literal["ok", "warn", "wait", "mute", "bad"]
Who = Literal["user", "agent"]

PREVIEW_PNG = "preview.png"
PREVIEW_MAP_SUFFIX = ".map.json"  # 与 api.render_job 写的对照文件同名规则
# 下面这些是英文原文（消息表的键）：用的时候再 t()，工作台按每个请求的语言出字
CONCEPT_BADGE = "Concept image"  # 卡片底栏的小标签（不压在图上）
FOLD_OTHER_DIRECTIONS = "Directions not chosen"
FOLD_OTHER_FLATS = "Flats of other directions"  # ④ 只用来筛掉，不显示
FOLD_OLD_BACKS = "Older back views (not in use)"
FOLD_OLD_FLATS = "Older flats (not in use)"  # 同上
FLAT_MM_H = 520  # ④ 里最高的面画这么高（像素）：几张平面图同一个比例尺，等高的面显示一样高
BADGE_IN_USE = "In use"  # 改过几版的背面视角 / 平面图：在用的那张
FOLD_PLAN_DETAILS = "Panel views, layout brief"
FLAT_SHEET_FOLD = "Panel-by-panel comparison and originals"
PRINT_NAMES = {
    "offset": "Offset",
    "flexo": "Flexo (water-based)",
    "digital": "Digital",
    "gravure": "Gravure",
}
BASIS_NAMES = {"inner": "inside", "outer": "outside"}
VIEW_LABELS = {v.name: v.label for v in (*VIEWS, OPEN_VIEW, BELOW_BACK_VIEW)}
# 确认记录里的确认人（命令行默认、工作台上点的）：存的是中文，显示按语言
BY_NAMES = {"用户": "User", "用户（工作台）": "User (Workbench)"}
BRIEF_MAX_CHARS = 4000  # 版式简报卡片最多显示这么多字


ActionId = Literal["content", "plan", "concept", "use", "flats", "approve"]


class Action(BaseModel):
    """用户可以在工作台上点的确认（和命令行同一个函数， 节）。"""

    id: ActionId
    label: str
    enabled: bool = True
    reason: str | None = None  # 现在不能点的原因
    target: str | None = None  # 选方向 / 改用某一版时是概念图 id


class Card(BaseModel):
    """画布上的一张卡片。file 相对 job 目录；mtime 用来让页面只换掉变了的卡片。"""

    id: str
    kind: Literal["image", "table", "text", "report", "files", "viewer3d"]
    title: str
    file: str | None = None
    mtime: float | None = None
    size: tuple[int, int] | None = None  # 图片像素
    caption: str | None = None
    badges: list[str] = Field(default_factory=list)
    stale: bool = False  # 设计改过之后没重出
    group: str | None = None  # 同一区里分行（多部件的部件、概念图的视角）
    fold: str | None = None  # 过程稿：默认收起在这个折叠组里（如"没选的方向"），点开才显示
    action: Action | None = None  # 卡片自己的确认（主视角概念图："选这个方向"）
    data: dict[str, Any] | None = None


class Stage(BaseModel):
    id: str
    no: int
    title: str
    status: str  # 机器读的状态码
    label: str  # 给人看的状态
    tone: Tone
    todo: Who | None = None  # 现在等谁
    action: str | None = None  # 等做什么
    action_code: Literal["reexport"] | None = None
    cards: list[Card] = Field(default_factory=list)
    notes_open: int = 0  # 这个阶段的卡片上还没关的批注
    actions: list[Action] = Field(default_factory=list)  # 这个阶段要用户确认的


class StudioState(BaseModel):
    job_dir: str
    job_id: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    stages: list[Stage] = Field(default_factory=list)
    now: str = ""
    now_who: Who | None = None
    findings: dict[str, Any] | None = None
    notes: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[Issue] = Field(default_factory=list)
    # 开源版（闭源命令走云端）：确认图是 100 DPI 的预览，工作台给"看清小字"按钮（ ）
    cloud: bool = False


# ---------------- 小工具 ----------------


def _rel(directory: Path, path: Path) -> str:
    return path.relative_to(directory).as_posix()


SPLIT_ASPECT = 2.2  # 高是宽的这么多倍以上的图（确认页这类长图），画布上分栏排
SPLIT_PROBE_W = 300  # 找切口时先缩到这么宽
SPLIT_BLANK_TOL = 6  # 一行像素的最亮与最暗相差不超过这么多，算空白行
SPLIT_MIN_GAP = 2  # 缩小后连续这么多行空白，才算段落之间的空隙
SPLIT_MAX_COLS = 4
SPLIT_EDGE = 0.05  # 离顶 / 底这么近的空隙不当切口


@functools.lru_cache(maxsize=64)
def split_columns(path: str, mtime_ns: int) -> list[list[int]] | None:
    """长图在画布上分几栏、每栏从哪一行到哪一行（原图像素）。

    只在段落之间的空白处切（整行都是底色），不会切到文字或图片；栏数按高宽比定，让整块接近方形。
    文件本身不动。mtime_ns 只用来让缓存跟着文件变。
    """
    del mtime_ns
    try:
        with Image.open(path) as im:
            w, h = im.size
            if h < SPLIT_ASPECT * w:
                return None
            sh = max(1, round(h * SPLIT_PROBE_W / w))
            small = np.asarray(
                im.convert("L").resize((SPLIT_PROBE_W, sh), Image.Resampling.BOX), dtype=np.int16
            )
    except OSError:
        return None
    k = max(2, min(SPLIT_MAX_COLS, round(math.sqrt(1.5 * h / w))))
    blank = (small.max(axis=1) - small.min(axis=1)) <= SPLIT_BLANK_TOL
    gaps: list[int] = []
    run = 0
    for i, b in enumerate([*blank.tolist(), False]):
        if b:
            run += 1
            continue
        if run >= SPLIT_MIN_GAP:
            mid = round((i - run / 2) * h / sh)
            if SPLIT_EDGE * h < mid < (1 - SPLIT_EDGE) * h:
                gaps.append(mid)
        run = 0
    cuts: list[int] = []
    for j in range(1, k):
        options = [g for g in gaps if g > (cuts[-1] if cuts else 0)]
        if not options:
            break
        cuts.append(min(options, key=lambda g: abs(g - h * j / k)))
    edges = [0, *sorted(set(cuts)), h]
    cols = [[edges[i], edges[i + 1]] for i in range(len(edges) - 1)]
    return cols if len(cols) > 1 else None


def _image_card(directory: Path, path: Path, card_id: str, title: str, **extra: Any) -> Card:
    try:
        with Image.open(path) as im:
            size: tuple[int, int] | None = im.size
    except OSError:
        size = None
    st = path.stat()
    if size is not None and size[1] >= SPLIT_ASPECT * size[0]:
        cols = split_columns(str(path), st.st_mtime_ns)
        if cols:
            extra["data"] = {**(extra.get("data") or {}), "columns": cols}
    return Card(
        id=card_id,
        kind="image",
        title=title,
        file=_rel(directory, path),
        mtime=st.st_mtime,
        size=size,
        **extra,
    )


def _pngs(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.png")) if folder.is_dir() else []


def _face_name(face: str) -> str:
    part, _, panel = face.rpartition(".")
    zh = FACE_ZH.get(panel)
    name = f"{display_name(zh)} {panel}" if zh else panel
    return f"{part}.{name}" if part else name


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@functools.cache
def _english_of() -> dict[str, str]:
    """中文表反查：存在文件里的中文名（工艺、面、范围）→ 英文。只收不带参数的短语。"""
    out: dict[str, str] = {}
    for en, zh in table("zh").items():
        if "{" not in en:
            out.setdefault(zh, en)
    return out


def _stored_text(text: str) -> str:
    """生成时写进文件的内置名字（按当时的语言，可能是中英对照）按界面语言显示；认不出的（用户起的名字）照原样。"""
    halves = text.split(" / ")
    if len(halves) == 2:  # 中英对照 "Silver foil / 烫银"
        return halves[1] if lang() == "zh" else halves[0]
    name = display_name(text)
    if name != text:
        return name
    if lang() == "zh":
        return table("zh").get(text, text)
    return _english_of().get(text, text)


def _ui_regions(regions: Any) -> list[dict[str, Any]]:
    """工艺区域（3D 场景、刀版确认图对照文件里的）的名字和范围说明换成界面语言；文件不改。"""
    if not isinstance(regions, list):
        return []
    out = []
    for r in regions:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        if isinstance(r.get("label"), str):
            r["label"] = _stored_text(r["label"])
        if isinstance(r.get("detail"), str):
            r["detail"] = " · ".join(_stored_text(x) for x in r["detail"].split(" · "))
        out.append(r)
    return out


# ---------------- 各阶段 ----------------


def _content(directory: Path, job: Job) -> Stage:
    st = Stage(
        id="content", no=1, title=t("Content list"), status="none", label=t("Not done"), tone="mute"
    )
    if job.content is None or not job.content.items:
        return st
    rows = [
        {
            "id": it.id,
            "category": display_name(CATEGORIES.get(it.category, it.category)),
            "text": it.text
            if it.text is not None
            else (t("(graphic) {asset}", asset=it.asset) if it.asset else ""),
            "required": it.required,
            "pending": it.pending,
        }
        for it in job.content.items
    ]
    st.cards.append(
        Card(
            id="content:list",
            kind="table",
            title=t("Content list · {n} items", n=len(rows)),
            data={"rows": rows},
        )
    )
    status = content_status(directory, job)
    if status == "confirmed":
        st.status, st.label, st.tone = "confirmed", t("Wording confirmed"), "ok"
    elif status == "stale":
        st.status, st.label, st.tone = "stale", t("Changed, needs confirming again"), "warn"
        st.todo, st.action = "user", t("Confirm the changed wording")
        st.actions.append(Action(id="content", label=t("Confirm wording again")))
    else:
        st.status, st.label, st.tone = (
            "pending",
            t("Waiting for you to confirm the wording"),
            "wait",
        )
        st.todo, st.action = "user", t("Confirm the wording of the content list")
        st.actions.append(Action(id="content", label=t("Confirm wording")))
    return st


def _plan(
    directory: Path, job: Job, fp: str, later_started: bool, flats_made: bool = False
) -> Stage:
    """flats_made：已经出了平面图——正式稿照平面图做，初稿过期是常态，不再请用户重新确认（抽屉测试 F-25：
    导出后用户点了"重新确认初稿"，"现在"倒回了"确认这组平面图"）。"""
    st = Stage(
        id="plan",
        no=2,
        title=t("Layout draft"),
        status="none",
        label=t("Not done (optional on route B)"),
        tone="mute",
    )
    d = directory / PLAN_DIR
    if not d.is_dir():
        return st
    for p in _pngs(d / "pairs"):
        st.cards.append(
            _image_card(
                directory,
                p,
                f"plan:pairs/{p.name}",
                t("Adjacent panels ") + p.stem,
                group=t("Adjacent panels"),
            )
        )
    for p in _pngs(d / "faces"):
        st.cards.append(
            _image_card(
                directory,
                p,
                f"plan:faces/{p.name}",
                _face_name(p.stem),
                group=t("Each panel"),
                fold=t(FOLD_PLAN_DETAILS),
            )
        )
    for p in _pngs(d):
        st.cards.append(_image_card(directory, p, f"plan:{p.name}", p.stem, group=t("Whole sheet")))
    brief = d / "brief.txt"
    if brief.is_file():
        text = brief.read_text(encoding="utf-8")
        st.cards.append(
            Card(id="plan:brief", kind="text", title=t("Layout brief"), file=_rel(directory, brief),
                 mtime=brief.stat().st_mtime, group=t("Whole sheet"), fold=t(FOLD_PLAN_DETAILS),
                 data={"text": text[:BRIEF_MAX_CHARS], "truncated": len(text) > BRIEF_MAX_CHARS})
        )  # fmt: skip
    status = plan_confirmation(directory, job, fp)
    if status == "confirmed":
        st.status, st.label, st.tone = "confirmed", t("Confirmed"), "ok"
    elif status == "stale" and flats_made:  # 正式稿照平面图做：初稿只是当时的记录
        st.status, st.label, st.tone = (
            "stale",
            t(
                "Confirmed (the final design follows the flats; the draft needs no new confirmation)"
            ),
            "mute",
        )
    elif status == "stale":  # 只是记录：确认之后设计往下走是常态
        st.status, st.label, st.tone = "stale", t("Confirmed (the design changed since)"), "mute"
        st.actions.append(
            Action(id="plan", label=t("Confirm the draft again for the current design"))
        )
    elif later_started:
        st.status, st.label, st.tone = "unconfirmed", t("Not confirmed"), "mute"
        st.actions.append(Action(id="plan", label=t("Confirm draft")))
    else:
        st.status, st.label, st.tone = "pending", t("Waiting for you to confirm the draft"), "wait"
        st.todo, st.action = "user", t("Confirm the layout draft")
        st.actions.append(Action(id="plan", label=t("Confirm draft")))
    return st


def _concept_card(directory: Path, m: ConceptMeta, chosen: str | None, group: str) -> Card | None:
    path = directory / m.file
    if not path.is_file():
        return None
    badges = [t(CONCEPT_BADGE)] + ([t("★ Chosen")] if m.id == chosen else [])
    title = m.id  # 视角写在分组标题里（主视角 / 背面视角 / AI 平面图），卡片底栏不重复
    return _image_card(
        directory,
        path,
        f"concept:{m.id}",
        title,
        caption=m.design,
        badges=badges,
        group=group,
        data={"chosen": m.id == chosen, "in_use": False},
    )


def of_direction(cid: str, chosen: str | None) -> bool:
    """概念图是不是选定方向这一支的：选定的那张，或按约定命名为 `<选定>-…` 的背面视角 / 平面图。"""
    return chosen is not None and (cid == chosen or cid.startswith(f"{chosen}-"))


def _version_fold(
    i: str, cid: str | None, in_use: set[str], versions: dict[str, int], other: str, old: str
) -> str | None:
    """背面视角 / 平面图收不收起：同组的旧版收进 old，别的方向收进 other，在用的照常显示。"""
    if i in versions:
        return None if i in in_use else old
    return other if cid is not None and not of_direction(i, cid) else None


def _versioned(
    directory: Path, view: str, cid: str | None, hide: frozenset[str] = frozenset()
) -> tuple[set[str], dict[str, int]]:
    """在用的那几张 id；分了版本的每张图 → 它那一组有几版（引擎的同一套判断；不算 hide 里的）。

    还没选方向时不分版本：分不清哪张是哪个方向的，都摆出来比。"""
    if cid is None:
        return set(), {}
    in_use = {m.id for m in in_use_versions(directory, view, cid).values()}
    groups = version_groups(directory, view, cid)
    shown = {g: sum(1 for m in vs if m.id not in hide) for g, vs in groups.items()}
    return in_use, {m.id: shown[g] for g, vs in groups.items() for m in vs}


def _version_cards(
    directory: Path, view: str, cid: str | None, group: str, other: str, old: str,
    hide: frozenset[str] = frozenset(),
) -> list[Card]:  # fmt: skip
    """改过几版时只有在用的那张摊开（标"在用"），旧版收起、可以"改用这张"。hide 里的不上画布。"""
    in_use, versions = _versioned(directory, view, cid, hide)
    cards = []
    for m in concept_metas(directory, view):
        card = _concept_card(directory, m, cid, group) if m.id not in hide else None
        if card is None:
            continue
        fold = _version_fold(m.id, cid, in_use, versions, other, old)
        # 这一支里被新的一组盖掉的（顶面单张被外套一整条盖掉）也收进旧版：以前照常摊开，像在用（抽屉测试 F-18）
        if fold is None and versions and m.id not in versions and version_group(m) is not None:
            fold = old
        card.fold = t(fold) if fold is not None else None
        if view != "flat" and m.id in in_use and versions[m.id] > 1:
            card.badges.append(t(BADGE_IN_USE))
            card.data = {**(card.data or {}), "in_use": True}
        elif fold == old:
            card.action = Action(id="use", label=t("Use this one"), target=m.id)
        cards.append(card)
    return cards


def _concepts(directory: Path) -> Stage:
    st = Stage(
        id="concept", no=3, title=t("Concepts"), status="none", label=t("Not done"), tone="mute"
    )
    chosen = read_chosen(directory)
    cid = chosen.id if chosen else None
    for m in concept_metas(directory, "main"):
        card = _concept_card(directory, m, cid, t("Main view"))
        if card is not None:
            if cid is not None and not of_direction(m.id, cid):  # 选定之后，别的方向是过程稿
                card.fold = t(FOLD_OTHER_DIRECTIONS)
            if m.id != cid:  # 只有主视角能选定为方向
                card.action = Action(id="concept", label=t("Choose this direction"), target=m.id)
            st.cards.append(card)
    st.cards += _version_cards(
        directory, "back", cid, t("Back view"), FOLD_OTHER_DIRECTIONS, FOLD_OLD_BACKS
    )
    if not st.cards:
        return st
    if chosen is not None:
        st.status, st.label, st.tone = "chosen", t("Chose {id}", id=chosen.id), "ok"
    else:
        st.status, st.label, st.tone = "pending", t("Waiting for you to choose a direction"), "wait"
        st.todo, st.action = "user", t("Choose a direction from the main-view concepts")
    return st


@functools.lru_cache(maxsize=16)
def _flat_checks_cached(directory: str, key: tuple[tuple[str, int], ...]) -> dict[str, FlatCheck]:
    """平面图比例检查要量图：按 job.json、选定 / 在用记录和平面图文件的修改时间缓存。

    在用的按 flat_checks（含两张盒身图互相比）；旧版、别的方向的也逐张量：不合格的同样不上画布，
    免得在旧版里被"改用这张"选回来。"""
    del key
    d = Path(directory)
    job, _ = dielines.load_job(d)
    dl = dielines.build_dieline_for(job)[0] if job is not None else None
    if dl is None:  # 多部件盒型按合上后看得到的面量（天地盖测试 F-15）
        return {}
    checks = flat_checks(d, dl)
    no_flat = set(no_flat_faces(dl))  # 不出平面图的面以前导入过的图：不量（抽屉测试 F-17）
    for m in concept_metas(d, "flat"):
        if (
            m.id not in checks
            and m.faces
            and not set(m.faces) <= no_flat
            and (d / m.file).is_file()
        ):
            checks[m.id] = check_flat(d, dl, m)
    return checks


def _flat_checks(directory: Path) -> dict[str, FlatCheck]:
    files = [
        directory / JOB_FILE,
        directory / CONCEPT_DIR / IN_USE,
        directory / CONCEPT_DIR / CHOSEN,
    ]
    files += [directory / m.file for m in concept_metas(directory, "flat")]
    key = tuple((str(f), f.stat().st_mtime_ns if f.is_file() else 0) for f in files)
    return _flat_checks_cached(str(directory), key)


@functools.lru_cache(maxsize=16)
def _split_lines_cached(directory: str, job_mtime_ns: int, language: str) -> tuple[str, ...]:
    """不出平面图的面（锁底盒的底这类两片拼的面）：在 ④ 说明为什么少一张。"""
    del job_mtime_ns, language  # 只是缓存键（文字按语言出）
    job, _ = dielines.load_job(Path(directory))
    dl = dielines.build_dieline_for(job)[0] if job is not None else None
    if dl is None:
        return ()
    return tuple(t("{msg}; ", msg=i.message) + (t("the agent draws its artwork separately from the main view") if i.id == "SPLIT"
                                     else t("the agent lays it out from the 3D concept and the layout draft"))
                 for i in split_face_issues(dl))  # fmt: skip


@functools.lru_cache(maxsize=16)
def _stacked_cached(directory: str, job_mtime_ns: int) -> frozenset[tuple[str, ...]]:
    """正着上下叠的那几组平面图（天地盖侧壁、抽屉内托前后脸，）：卡片按高相加排。"""
    del job_mtime_ns
    from pacxio.concept.prompts import _flat_groups
    from pacxio.concept.rectify import stack_axis

    job, _ = dielines.load_job(Path(directory))
    dl = dielines.build_dieline_for(job)[0] if job is not None else None
    if dl is None:
        return frozenset()
    return frozenset(tuple(g) for g in _flat_groups(dl) if stack_axis(dl, g) == "y")


@functools.lru_cache(maxsize=16)
def _no_flat_cached(directory: str, job_mtime_ns: int) -> frozenset[str]:
    """不出平面图的面（抽屉的内托前后脸）：以前导入过的这些面的平面图不上画布（抽屉测试 F-17）。"""
    del job_mtime_ns
    job, _ = dielines.load_job(Path(directory))
    dl = dielines.build_dieline_for(job)[0] if job is not None else None
    return frozenset(no_flat_faces(dl)) if dl is not None else frozenset()


@functools.lru_cache(maxsize=16)
def _face_mm_cached(directory: str, job_mtime_ns: int) -> dict[str, tuple[float, float]]:
    """看得到的面正立时的宽、高（mm），和平面图提示词、拉正用的是同一个来源。"""
    del job_mtime_ns
    job, _ = dielines.load_job(Path(directory))
    dl = dielines.build_dieline_for(job)[0] if job is not None else None
    if dl is None:
        return {}
    rects, panels = face_rects(dl), {pn.id: pn for pn in dl.panels}
    return {f: face_size(rects[f], panels[f]) for f in rects}


def _mm_width(
    mm: dict[str, tuple[float, float]], faces: list[str], size: tuple[int, int] | None = None,
    art: tuple[int, int, int, int] | None = None, stacked: bool = False,
) -> int | None:  # fmt: skip
    """这几个面并排时卡片多宽（像素）：最高的面画 FLAT_MM_H 高，同一个比例尺。
    量得出画面在图里的像素框（art）时，卡片只显示这一块（裁掉四周留白，见 data.crop），宽按它的
    宽高比定——AI 平面图四周留白不一样多，不裁的话等高的面会显示得一高一矮、上下对不齐。"""
    if not mm or not faces or any(f not in mm for f in faces):
        return None
    k = FLAT_MM_H / max(h for _, h in mm.values())
    tall = (
        sum(mm[f][1] for f in faces) if stacked else max(mm[f][1] for f in faces)
    )  # 上下叠：高相加
    if size and art and art[3] > art[1]:
        return round(tall * k * (art[2] - art[0]) / (art[3] - art[1]))
    return round((max(mm[f][0] for f in faces) if stacked else sum(mm[f][0] for f in faces)) * k)


def _flats(directory: Path) -> Stage:
    st = Stage(
        id="flat_concept", no=4, title=t("Flats"), status="none", label=t("Not done"), tone="mute"
    )
    chosen = read_chosen(directory)
    cid = chosen.id if chosen else None
    # 用户在这里只看一件事：效果图展开成平面后，设计是不是一致。所以只摆在用的那一组；
    # 旧版、别的方向（过程稿）和拉正的面（agent 量尺寸用）都不上画布。
    # 比例不合格的也不上：免得被当成参考、被确认。在用的不合格就要重出，留一张说明
    checks = _flat_checks(directory)
    bad = {i: c.issue for i, c in checks.items() if c.issue is not None}
    in_use_now = {m.id for m in in_use_versions(directory, "flat", cid).values()}
    redo = {i: issue for i, issue in bad.items() if i in in_use_now}
    no_flat = _no_flat_cached(str(directory), (directory / JOB_FILE).stat().st_mtime_ns)
    unused = {m.id for m in concept_metas(directory, "flat") if m.faces and set(m.faces) <= no_flat}
    cards = _version_cards(directory, "flat", cid, t("AI flats"), FOLD_OTHER_FLATS, FOLD_OLD_FLATS,
                           hide=frozenset(bad) | unused)  # fmt: skip
    order = ("front", "side_b", "back", "side_a", "top_lid", "btm_lid")  # 按盒子读：正、背、顶、底
    first = {m.id: min((order.index(f) for f in m.faces or [] if f in order), default=len(order))
             for m in concept_metas(directory, "flat")}  # fmt: skip
    shown = [c for c in cards if c.fold is None]
    st.cards += sorted(shown, key=lambda c: first.get(c.id.removeprefix("concept:"), len(order)))
    # 按真实毫米排：正面 + 右侧和背面 + 左侧一样宽、一样高，顶面窄一截
    mm = _face_mm_cached(str(directory), (directory / JOB_FILE).stat().st_mtime_ns)
    faces = {m.id: m.faces or [] for m in concept_metas(directory, "flat")}
    stacked = _stacked_cached(str(directory), (directory / JOB_FILE).stat().st_mtime_ns)
    for card in st.cards:
        cid = card.id.removeprefix("concept:")
        art = checks[cid].bounds if cid in checks else None
        width = _mm_width(
            mm, faces.get(cid, []), card.size, art, tuple(faces.get(cid, [])) in stacked
        )
        if width is not None:
            crop = {"crop": list(art)} if art and card.size else {}
            card.data = {**(card.data or {}), "width": width, **crop}
    if st.cards:
        n = len(shown)
        st.status, st.label, st.tone = (
            "done",
            t("{n} images (the final design is rebuilt from them)", n=n),
            "ok",
        )
        split = _split_lines_cached(
            str(directory), (directory / JOB_FILE).stat().st_mtime_ns, lang()
        )
        if split:
            st.cards.append(Card(id="flat_concept:split", kind="text", title=t("Panels without a flat"),
                                 data={"lines": list(split)}))  # fmt: skip
    if redo:  # 比例差太多的不能请用户确认：先重出
        st.cards.insert(0, Card(id="flat_concept:redo", kind="text", title=t("Proportions failed, not shown (waiting for the agent to redo)"),
                                data={"lines": [i.message for i in redo.values() if i is not None]}))  # fmt: skip
        st.status, st.label, st.tone = "redo", t("{n} to redo", n=len(redo)), "warn"
        st.todo, st.action = (
            "agent",
            t("Proportions too far off; redo them before asking you to confirm the set: ")
            + t(", ").join(redo),
        )
    return st


def _key_colors_card(job: Job) -> Card:
    """⑤ 旁边的"关键颜色"卡：名称、潘通号、CMYK、用在哪；色块是屏幕示意（颜色以签样为准）。"""
    from pacxio_pro.color import cmyk_to_rgb_naive
    from pacxio_pro.color.keys import cmyk_text

    keys = job.key_colors or []
    lines, swatches = [], []
    for kc in keys:
        parts = [kc.name, kc.pantone or t("no Pantone given"), cmyk_text(kc.cmyk)] + (
            [kc.where] if kc.where else []
        )
        lines.append(" · ".join(parts))
        ink = np.array([[[round(v * 255 / 100) for v in kc.cmyk]]], dtype=np.uint8)
        r, g, b = (int(x) for x in cmyk_to_rgb_naive(ink)[0, 0])
        swatches.append(f"#{r:02x}{g:02x}{b:02x}")
    return Card(id="design:key_colors", kind="text", title=t("Key colors (for color matching at the factory)"),
                caption=t("The Pantone number is the color standard; swatches are only a screen preview"), data={"lines": lines, "swatches": swatches})  # fmt: skip


def _design(
    directory: Path, job: Job, fp: str, parts: tuple[str, ...], labels: dict[str, str]
) -> Stage:
    """⑤ 刀版确认图：render 出的展开图（刀线、面名与尺寸、工艺标注），用户在这里确认（导出前唯一的闸门）。"""
    st = Stage(
        id="design",
        no=5,
        title=t("Dieline confirmation"),
        status="none",
        label=t("Not made yet"),
        tone="mute",
    )
    job_mtime = (directory / JOB_FILE).stat().st_mtime
    for part in parts or (None,):
        png = directory / (f"preview_{part}.png" if part else PREVIEW_PNG)
        if not png.is_file():
            continue
        side = _read_json(png.with_suffix(PREVIEW_MAP_SUFFIX))
        if side is not None and side.get("fingerprint"):  # 设计或盒型刀版改过：不是最新
            stale = not is_current(side, fp, job)
        else:  # 旧预览没有对照文件：按修改时间判断
            stale = png.stat().st_mtime < job_mtime
        title = (
            t(
                "{label} {part} · dieline confirmation image",
                label=labels.get(part, part),
                part=part,
            )
            if part
            else t("Dieline confirmation image")
        )
        st.cards.append(_image_card(directory, png, f"design:{png.name}", title, stale=stale,
                                    group=labels.get(part, part) if part else None,
                                    data={"map": {**side, "regions": _ui_regions(side.get("regions"))}} if side else None))  # fmt: skip
    if not st.cards:
        st.todo, st.action = (
            "agent",
            t(
                "(after the flats are confirmed) lay out the design, then render the dieline confirmation image"
            ),
        )
        return st
    flat_manifest = flat_mod.read_manifest(directory) or {}
    diffs = flat_manifest.get("diffs") or []
    # agent 写的差别（flat --diff），设计没再改过才算数
    if diffs and is_current(flat_manifest, fp, job):
        st.cards.append(Card(id="design:diffs", kind="text", title=t("Differences from the flats (written by the agent)"),
                             data={"lines": diffs}))  # fmt: skip
    if job.key_colors:  # 关键颜色只是给工厂对色的标注：在这里核对，不影响确认
        st.cards.append(_key_colors_card(job))
    state = approval_state(directory, job, fp)
    label = t("Confirm dieline again") if state.approval is not None else t("Confirm dieline")
    if state.status == "approved":
        by = state.approval.by if state.approval and state.approval.by else USER
        by = t(BY_NAMES[by]) if by in BY_NAMES else by  # 记录里存的是中文（数据不翻），显示时换
        if by == "用户（工作台）":
            by = display_name(by)
        st.status, st.label, st.tone = "approved", t("Confirmed · {by}", by=by), "ok"
        return st
    if state.status != "pending":  # stale（或旧预览没有指纹）
        why = _why_stale(
            _read_json(directory / PREVIEW_PNG.replace(".png", PREVIEW_MAP_SUFFIX)), fp
        )
        st.actions.append(Action(id="approve", label=label, enabled=False,
                                 reason=t("{why}; ask the agent to render the dieline confirmation image again first", why=why)))  # fmt: skip
        st.status, st.label, st.tone = "stale", t("Out of date"), "warn"
        st.todo, st.action = (
            "agent",
            t("{why}; render the dieline confirmation image again", why=why),
        )
        return st
    required = (_read_json(directory / MOCKUP_DIR / "manifest.json") or {}).get("required") or []
    missing = blocking_content(job, content_status(directory, job), required)
    if missing:  # 缺必须出现的信息：现在确认不了，别显示成"等你确认"
        first = missing[0] + (t(" ({n} in all)", n=len(missing)) if len(missing) > 1 else "")
        some = t(", ").join(missing[:3]) + (
            t(" ({n} in all)", n=len(missing)) if len(missing) > 3 else ""
        )
        st.actions.append(Action(id="approve", label=label, enabled=False,
                                 reason=t("Required information is missing: {some} (see ① Content list for all)", some=some)))  # fmt: skip
        st.status, st.label, st.tone = "blocked", t("Information missing"), "warn"
        st.todo, st.action = (
            "user",
            t(
                "First fill in the required information: {first}; then the agent renders again and you confirm",
                first=first,
            ),
        )
        return st
    st.actions.append(Action(id="approve", label=label))
    errors = _preflight_errors(directory, job)
    if errors:  # 修 error 多半要改设计，现在确认了还得再确认一次：先修（抽屉测试 F-22）
        ids = t(", ").join(dict.fromkeys(f.id for f in errors))
        st.status, st.label, st.tone = (
            "fixing",
            t("Fix {n} preflight errors first", n=len(errors)),
            "warn",
        )
        st.todo, st.action = (
            "agent",
            t(
                "Preflight still has errors ({ids}): fix them as suggested (show the user what they need to see first), render again, then ask you to confirm the dieline confirmation image",
                ids=ids,
            ),
        )
        return st
    st.status, st.label, st.tone = "pending", t("Waiting for your confirmation"), "wait"
    st.todo, st.action = (
        "user",
        t(
            "Check the dieline confirmation image: if the text, images, sizes and finish callouts are right, confirm it (the only gate before export)"
        ),
    )
    return st


def _preflight_errors(directory: Path, job: Job) -> list[Finding]:
    """最近一次印前检查（设计没再改过）的 error；没检查过、检查不是最新时为空。"""
    path = _report_file(directory, directory / "out" / dielines.pack_name(job))
    if path is None or path.stat().st_mtime < (directory / JOB_FILE).stat().st_mtime:
        return []
    try:
        return list(Report.model_validate_json(path.read_text(encoding="utf-8")).errors)
    except (OSError, ValidationError):
        return []


def _why_stale(record: dict[str, Any] | None, fp: str) -> str:
    """过期的原因：设计指纹没变、只是盒型版本对不上，就是引擎修正了盒型的刀版结构。"""
    if record and record.get("fingerprint") == fp:
        return t("The dieline structure of the box style was updated")
    return t("The design changed")


def _mockup(directory: Path, job: Job, fp: str) -> Stage:
    """⑥ 3D 效果图：只看最终效果，不确认。"""
    st = Stage(
        id="mockup", no=6, title=t("3D mockup"), status="none", label=t("Not done"), tone="mute"
    )
    out = directory / MOCKUP_DIR
    manifest = _read_json(out / "manifest.json")
    if manifest is None:
        return st
    stale = not is_current(manifest, fp, job)  # 设计或盒型刀版改过
    scene = out / SCENE_FILE
    scene_data = _read_json(scene)
    from pacxio.mockup.materials import presets
    from pacxio.mockup.scene3d import RENDERER_VERSION

    render_stale = bool(
        scene_data
        and (
            scene_data.get("renderer_version") != RENDERER_VERSION
            # v1 场景（瓶罐标签、软袋）不用材质预设，没有 material_version
            or (
                scene_data.get("version", 1) >= 2
                and scene_data.get("material_version") != presets()["version"]
            )
        )
    )
    # 静态效果图从方盒子改成按折叠几何画（2026-11-04）：旧项目重跑 mockup 即可，不影响确认
    from pacxio.dielines import style_parts
    from pacxio.mockup.raster import expected_static_renderer
    from pacxio.styles import get_style

    try:
        style_obj = get_style(job.style)
        multipart = bool(style_parts(style_obj, _params_or_none(style_obj, job)))
    except KeyError:
        multipart = False
    static_stale = manifest.get("static_renderer", "box") != expected_static_renderer(
        job.style, multipart
    )
    stale = stale or render_stale or static_stale
    if scene_data is not None and isinstance(scene_data.get("states"), dict):
        st.cards.append(Card(id="mockup:3d", kind="viewer3d", title=t("Turnable 3D"), file=_rel(directory, scene),
                             mtime=scene.stat().st_mtime, stale=stale, caption=t("Drag to rotate, scroll to zoom"),
                             data={"states": list(scene_data["states"]), "version": scene_data.get("version", 1), "regions": _ui_regions(scene_data.get("regions"))}))  # fmt: skip
    else:  # 旧效果图没有 3D 场景：退回静态视角图
        for name in manifest.get("views", []):
            p = out / VIEW_FILE.format(name=name)
            if p.is_file():
                st.cards.append(_image_card(directory, p, f"mockup:{p.name}", display_name(VIEW_LABELS.get(name, name)),
                                            stale=stale))  # fmt: skip
    if not st.cards:
        return st
    if stale:
        st.status, st.label, st.tone = "stale", t("Out of date"), "warn"
        why = (
            t("The 3D materials or renderer were updated")
            if render_stale
            else _why_stale(manifest, fp)
        )
        st.todo, st.action = "agent", t("{why}; make the 3D mockup again (mockup)", why=why)
    else:
        st.status, st.label, st.tone = "fresh", t("Up to date"), "ok"
    return st


def _report_file(directory: Path, pack: Path) -> Path | None:
    cands = [
        directory / dielines.PREFLIGHT_DIR / dielines.REPORT_JSON,
        pack / dielines.REPORT_JSON,  # 检查不通过时写在交付包文件夹里
        dielines.archive_dir(pack) / dielines.ARCHIVE_REPORT_JSON,
    ]
    found = [c for c in cands if c.is_file()]
    return max(found, key=lambda c: c.stat().st_mtime) if found else None


def _files(directory: Path, root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": _rel(directory, f),
            "name": f.relative_to(root).as_posix(),
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
        }  # fmt: skip
        for f in sorted(root.rglob("*"))
        if f.is_file() and not f.name.startswith(".")
    ]


def _delivery(directory: Path, job: Job) -> tuple[Stage, dict[str, Any] | None]:
    st = Stage(
        id="delivery",
        no=7,
        title=t("Preflight and delivery"),
        status="none",
        label=t("Not checked"),
        tone="mute",
    )
    pack = directory / "out" / dielines.pack_name(job)
    report_path = _report_file(directory, pack)
    findings: dict[str, Any] | None = None
    n_warn = 0
    if report_path is not None:
        try:
            report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            report = None
        if report is not None:
            stale = report_path.stat().st_mtime < (directory / JOB_FILE).stat().st_mtime
            shown = [f for f in report.findings if f.level != "info"]
            pdf = report_path.with_suffix(".pdf")
            findings = {
                "file": _rel(directory, report_path),
                "pdf": _rel(directory, pdf) if pdf.is_file() else None,
                "mtime": report_path.stat().st_mtime,
                "stale": stale,
                "passed": report.passed,
                "profile_verified": report.profile_verified,
                "skipped": report.skipped,
                "info": sum(1 for f in report.findings if f.level == "info"),
                "items": [f.model_dump(mode="json", exclude_none=True) for f in shown],
            }
            n_err, n_warn = len(report.errors), len(report.warnings)
            st.cards.append(Card(id="delivery:report", kind="report", title=t("Preflight"),
                                 file=findings["file"], mtime=findings["mtime"], stale=stale,
                                 data={"errors": n_err, "warnings": n_warn, "passed": report.passed,
                                       "profile_verified": report.profile_verified}))  # fmt: skip
            if n_err:
                st.status, st.label, st.tone = "errors", f"{n_err} error · {n_warn} warning", "bad"
                st.todo, st.action = (
                    "agent",
                    t(
                        "Fix the errors as the report suggests (no delivery pack while there are errors)"
                    ),
                )
            elif stale:
                st.status, st.label, st.tone = "stale", t("Check out of date"), "warn"
                st.todo, st.action = "agent", t("The design changed; run preflight again")
            else:
                st.status, st.label, st.tone = (
                    "passed",
                    t("Check passed · {n} warning", n=n_warn),
                    "ok",
                )
    archive = dielines.archive_dir(pack)
    failed, preview = pack / dielines.FAILED_PREVIEW, archive / dielines.PREVIEW_PNG
    if failed.is_file():
        st.cards.append(
            _image_card(directory, failed, "delivery:failed", t("Failed (D12: no delivery pack)"))
        )
    elif preview.is_file():
        st.cards.append(
            _image_card(directory, preview, "delivery:preview", t("Flat preview (archive)"))
        )
    if pack.is_dir():
        files = _files(directory, pack)
        st.cards.append(Card(id="delivery:files", kind="files", title=t("Production pack"),
                             data={"files": files, "folder": _rel(directory, pack)}))  # fmt: skip
        pdfs = [directory / f["path"] for f in files if f["path"].endswith(dielines.PRODUCTION_PDF)]
        if st.status == "passed" and pdfs:
            st.status, st.label = "exported", t("Exported · {n} warning", n=n_warn)
            # 导出之后设计又改过：包里还是旧版，不能当成已交付
            if min(p.stat().st_mtime for p in pdfs) < (directory / JOB_FILE).stat().st_mtime:
                st.status, st.label, st.tone = "stale", t("Exported an old version"), "warn"
                st.todo, st.action_code = "agent", "reexport"
                st.action = t(
                    "The design changed: once the dieline confirmation image is approved, export the delivery pack again"
                )
    if archive.is_dir():
        st.cards.append(Card(id="delivery:archive", kind="files", title=t("Archive"),
                             data={"files": _files(directory, archive), "folder": _rel(directory, archive)}))  # fmt: skip
    if st.status == "passed":  # 检查通过、还没导出：印刷文件要 agent 跑 export 才有
        st.todo, st.action = "agent", t("Export the delivery pack")
    return st, findings


def _params_or_none(style: Any, job: Job) -> Any:
    """部件按配方给的盒型（几何引擎多件套）要先读参数；读不出来当没有。"""
    if not hasattr(style, "parts_for"):
        return None
    try:
        return style.param_schema().model_validate(job.params)
    except Exception:  # noqa: BLE001 — job.json 正在被改时读不出来，状态页照常
        return None


def _summary(
    job: Job, parts: tuple[str, ...], labels: dict[str, str], style_name: str
) -> dict[str, Any]:
    q = job.box_dims()
    material = job.material.preset or job.material.type
    profile_name = job.profile
    try:
        preset = load_materials().get(job.material.preset or "")
        if preset is not None:
            material = display_name(preset.name)
        profile = load_profile(job.profile)
        verified = profile.verified
        profile_name = profile.name
        if profile.id == "default":
            profile_name = display_name(profile_name)
    except ProfileNotFoundError:
        verified = False
    return {
        "job_id": job.job_id,
        "style": job.style,
        "style_name": style_name,
        "dims": t(
            "{l:g}×{w:g}×{h:g} mm ({basis})",
            l=q["L"],
            w=q["W"],
            h=q["H"],
            basis=t(BASIS_NAMES.get(q.get("dim_basis", "inner"), "inside")),
        ),
        "material": material,
        "caliper_mm": job.material.caliper_mm,
        "print_method": t(PRINT_NAMES[job.print.method])
        if job.print.method in PRINT_NAMES
        else job.print.method,
        "parts": [{"id": p, "label": labels.get(p, p)} for p in parts],
        "profile": job.profile,
        "profile_name": profile_name,
        "profile_verified": verified,
    }


def _now_text(stages: Iterable[Stage], directory: Path) -> tuple[str, Who | None]:
    """工作台的"现在"：和 `pacxio next` 同一套判断——⑤ 还没出图时的"等 agent 拼版"只是流程上的位置，
    不算待办；没有阶段在等谁时按流程顺序找下一步（抽屉测试 F-01：新 job 以前显示成"拼版"）。"""
    stages = list(stages)
    for st in stages:
        if not st.todo or (st.id, st.status) == ("design", "none"):
            continue
        message = (t("Waiting for you: {action} ({title})", action=st.action, title=st.title)
                   if st.todo == "user" else t("Waiting for agent: {action} ({title})", action=st.action, title=st.title))  # fmt: skip
        return message, st.todo
    from pacxio.studio.next import _flow_step

    current, step = _flow_step({st.id: st for st in stages}, str(directory))
    if current is None:
        return t("Nothing to do"), None
    if step.commands:
        return t(
            "Waiting for agent: {action} ({title})", action=step.what, title=current.title
        ), "agent"
    return t("{action} ({title})", action=step.what, title=current.title), None


# 这之后导入的平面图要有确认记录（concept confirm，2026-11-01 加）；更早的照旧，见 _legacy_flats_used
FLAT_CONFIRM_SINCE = "2026-10-31T20:00:00+00:00"


def _legacy_flats_used(directory: Path) -> bool:
    """确认记录出现之前的老项目（在用的平面图都是 FLAT_CONFIRM_SINCE 之前导入的）：刀版确认图是在平面图
    之后出的，就当用户已经确认过（那时只在对话里说），不倒回去请用户再确认。"""
    chosen = read_chosen(directory)
    metas = list(in_use_versions(directory, "flat", chosen.id if chosen else None).values())
    if not metas or any((m.created or "") >= FLAT_CONFIRM_SINCE for m in metas):
        return False
    previews = [p.stat().st_mtime for p in directory.glob("preview*.png")]
    flats = [(directory / m.file).stat().st_mtime for m in metas if (directory / m.file).is_file()]
    return bool(previews and flats) and max(previews) > max(flats)


def job_state(directory: Path) -> StudioState:
    """一个 job 的全部阶段、卡片与状态。job.json 读不出来时只返回 errors。"""
    state = StudioState(job_dir=str(directory), cloud=use_cloud())
    job, errs = dielines.load_job(directory)
    if job is None:
        state.errors = errs
        state.now = t("job.json cannot be read (the agent may be editing it)")
        return state
    try:
        style = get_style(job.style)
        params = _params_or_none(style, job)
        parts = dielines.style_parts(style, params)
        labels = {
            part: display_name(name)
            for part, name in dielines.style_part_labels(style, params).items()
        }
        style_name = display_name(style.name_zh)
    except KeyError:
        parts, labels, style_name = (), {}, job.style
    fp = design_fingerprint(directory, job)
    concepts = _concepts(directory)
    flats = _flats(directory)
    design = _design(directory, job, fp, parts, labels)
    plan_confirmed = plan_confirmation(directory, job, fp) == "confirmed"
    delivery, findings = _delivery(directory, job)
    # 刀版图已确认、交付包已导出：平面图早就确认过了（初稿是后来又确认的），不倒回去（抽屉测试 F-25）
    finished = design.status == "approved" or delivery.status == "exported"
    # 平面图齐了：按确认记录（concept confirm / 工作台上点）判断，下一步是用户确认这组，不是 agent
    # 直接拼版（LUMERA 测试 ISSUE-003：以前靠"初稿确认后设计改没改过"猜，改个占位说明就猜错）
    if flats.status == "done":
        rec = flats_confirmation(directory)
        if rec == "confirmed":
            flats.label += t(" · confirmed")
        elif rec == "stale" or not (finished or _legacy_flats_used(directory)):
            flats.status, flats.tone = "pending", "wait"
            flats.label = (
                t("Flats changed, waiting for you to confirm again")
                if rec == "stale"
                else t("Waiting for you to confirm the set of flats")
            )
            flats.todo = "user"
            flats.action = t(
                "Check that these flats match the main view: tell the agent to confirm in the chat, or click confirm here"
            )
            flats.actions.append(Action(id="flats", label=t("Confirm these flats")))
    # 正式稿已经照平面图做了（同上：初稿阶段出过的确认图不算）：比例提醒只留标签，不再是待办
    # 刀版图确认过 / 已导出也一样（规则改了以后，旧项目在用的平面图按新分组量会不合格，不倒回去要重出）
    if flats.status == "redo" and design.status != "none" and (not plan_confirmed or finished):
        flats.todo = flats.action = None
    later = concepts.status == "chosen" or design.status != "none"
    state.job_id = job.job_id
    state.summary = _summary(job, parts, labels, style_name)
    state.stages = [
        _content(directory, job),
        _plan(directory, job, fp, later, flats.status != "none"),
        concepts,
        flats,
        design,
        _mockup(directory, job, fp),
        delivery,
    ]
    state.findings = findings
    state.now, state.now_who = _now_text(state.stages, directory)
    _attach_notes(directory, state)
    return state


def _attach_notes(directory: Path, state: StudioState) -> None:
    """批注（没关的数到所在阶段上）；卡片没了的批注照样列出，页面上只在侧栏显示。"""
    try:
        book = load_notes(directory)
    except (ValueError, ValidationError) as e:
        state.errors.append(
            Issue(id="NOTE", message=t("review/notes.json cannot be read: {e}", e=e))
        )
        return
    stage_of = {c.id: st for st in state.stages for c in st.cards}
    for n in book.notes:
        st = stage_of.get(n.target.card or "")
        if st is not None and n.status != "resolved":
            st.notes_open += 1
        state.notes.append(
            {**n.model_dump(mode="json"), "where": describe(n), "stage": st.id if st else None}
        )


def find_action(state: StudioState, action: str, target: str | None = None) -> Action | None:
    """在汇总里找这一项确认（选概念图按卡片找）。找不到就是现在不该点。"""
    for st in state.stages:
        for a in st.actions:
            if a.id == action:
                return a
        for c in st.cards:
            if c.action is not None and c.action.id == action and c.action.target == target:
                return c.action
    return None


__all__ = ["Action", "Card", "Stage", "StudioState", "find_action", "job_state"]
