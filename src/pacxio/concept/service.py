"""概念图：设计方向的确认（在正式设计之前）。

流程：需求 → **概念图（AI 画的成品样子，允许文字）** → 选定方向 → 按概念图做正式设计 →
精确效果图（与概念图并排对照）→ 确认 → 印前检查 → 导出。

概念图存放在 job 的 `concepts/` 下，**图本身不进印刷文件**：art add / finish add / visual import /
visual prepare / 排版合成都会拒绝它。比例、文字、字体、细节都不保证准确，正式文件是按它重建的，
以精确效果图为准；展开平面图是还原的源头（restore strip 去字拼好后描成矢量，）。

流程 v2.1（ 2.3–2.5）有三种概念图，meta 里的 `view` 记录是哪一种：
main 主视角（选方向）、back 背面 + 左侧视角（确认其他面）、flat 展开平面图（`faces` 按从左到右
写明是哪几个面）。旧的 meta 没有这两个字段，读出来是 None。

背面视角、平面图改过几版时，同一组（背面视角一组；平面图按画了哪几个面分组）只有一张"在用"：
默认最新导入的；用户可以 `concept use` 改用旧版（记在 `concepts/in_use.json`），之后再导入新版
又回到新版。出平面图提示词、平铺对照、效果图确认页都只用在用的那张。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from pacxio.concept.palette import PaletteColor, Region, extract_palette
from pacxio.concept.rectify import (
    RECTIFIED_DIR,
    FlatCut,
    FlatError,
    draw_grid,
    drawn_layout,
    face_rects,
    measure_flat,
    rectify_face,
    stack_axis,
    swapped_faces,
)
from pacxio.messages import display_name, lang, t
from pacxio.model.common import Issue, Result
from pacxio.model.dieline import Dieline, PanelRole
from pacxio.model.job import Job, load_job_checked
from pacxio.styles import get_style
from pacxio.visual.meta import now_iso, sha256_of
from pacxio.visual.prompts import build_concept_prompt
from pacxio.visual.providers import PROVIDERS, GenRequest, ImageProvider, ProviderError
from pacxio.visual.providers.file_import import codex_generated_dir, latest_image

if TYPE_CHECKING:
    from pacxio_pro.color import ColorConverter

CONCEPT_DIR = "concepts"
CHOSEN = "chosen.json"
IN_USE = "in_use.json"  # 用户改用的旧版（背面视角 / 平面图）
META_SUFFIX = ".meta.json"
CONCEPT_SIZE = (1536, 1024)  # 横版：三分之二视角的产品图
MAX_CONCEPTS_PER_CALL = 4
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")

SourceType = Literal["openai", "file", "codex_latest"]
ConceptView = Literal["main", "back", "flat"]
VIEWS = ("main", "back", "flat")
FACE_ALIASES = {"top": "top_lid", "bottom": "btm_lid", "left": "side_a", "right": "side_b"}
# 简写 → 盒子模型的面（再按盒型找刀版面：底面在反插盒是 btm_lid、扣底盒是 btm_front、飞机盒是 base）
FACE_KEYS = {"top": "top", "bottom": "bottom", "left": "side_a", "right": "side_b"}
FACE_ROLES = {PanelRole.BODY, PanelRole.LID}
NEW_FIELDS = ("view", "faces")  # 没给时不写进 meta：没标的图和以前的 meta 一模一样
FACE_ZH = {"front": "正面", "back": "背面", "side_a": "左侧面", "side_b": "右侧面",
           "top_lid": "顶面", "btm_lid": "底面", "btm_front": "底面", "base": "底面",
           "top_tuck": "正面（插舌）"}  # fmt: skip  # 飞机盒合上后正面是插舌
# 平面图里一个面的宽高比和真实差超过这么多：要重出（先重出，再请用户确认这组平面图）
ASPECT_WARN = 0.10
# 单个面的比例差到这么多才要重出：整张比例对、只是 AI 把折线画偏一点（面宽分配不准）时只提醒，
# 拉正按找到的折线切（，用户定"按整张比例判"）【初值】
FACE_REDO = 0.20
def _redo_fix() -> str:
    return t(
        "Regenerate with `concept prompt --step flat` and import under a new name. The newest image is "
        "used automatically. Ask the user to confirm when the overall ratio is within 10%. If two "
        "attempts still fall outside tolerance, show the best rectified image and ask whether to accept "
        "it, change the layout, or regenerate. Explain the cost before trying again"
    )


def view_label(view: str | None) -> str:
    labels = {"main": t("Main view"), "back": t("Back view"), "flat": t("Flat concept")}
    return labels.get(view or "", t("no view"))


class ConceptMeta(BaseModel):
    id: str
    file: str  # 相对 job 目录
    source_type: SourceType
    source_path: str | None = None
    provider: str | None = None
    model: str | None = None
    design: str | None = None
    texts: list[str] = Field(default_factory=list)
    scene: str | None = None
    full_prompt: str | None = None
    revised_prompt: str | None = None
    created: str
    sha256: str
    size_px: tuple[int, int]
    view: ConceptView | None = None  # 旧图没有：当主视角看待
    faces: list[str] | None = None  # 刀版面 id；平面图按图里从左到右的顺序


class Chosen(BaseModel):
    id: str
    file: str
    sha256: str
    chosen_at: str
    by: str | None = None
    note: str | None = None
    region: tuple[float, float, float, float] | None = None
    palette: list[dict[str, object]] = Field(default_factory=list)


class InUsePick(BaseModel):
    """用户改用的一版。newest 是改用时这一组最新的一张：之后又导入了新版，就回到新版在用。"""

    id: str
    newest: str
    at: str
    by: str | None = None
    note: str | None = None


# ---------------- 路径与守卫 ----------------


def concept_dir(directory: Path) -> Path:
    return directory / CONCEPT_DIR


def is_concept_path(directory: Path, path: Path) -> bool:
    """path 是否落在 job 的 concepts/ 里（概念图不能进入印刷流程）。"""
    try:
        path.resolve().relative_to(concept_dir(directory).resolve())
        return True
    except ValueError:
        return False


def concept_guard_issue(what: str) -> Issue:
    return Issue(
        id="CONCEPT",
        message=t("A concept image cannot be used for {use}. It is a visual reference with perspective, potentially incorrect text, and insufficient print resolution.", use=what),
        fix=t("Rebuild the artwork from the concept. Typeset copy with `art add --kind text`. Trace flat artwork with `restore strip` and `visual trace`, or use `visual gen` for text-free assets when no flat is available."),
    )


def read_chosen(directory: Path) -> Chosen | None:
    p = concept_dir(directory) / CHOSEN
    return Chosen.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None


def _metas(directory: Path) -> list[ConceptMeta]:
    d = concept_dir(directory)
    return [
        ConceptMeta.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(d.glob(f"*{META_SUFFIX}"))
    ]


def concept_metas(directory: Path, view: str | None = None) -> list[ConceptMeta]:
    """job 里的概念图（按导入时间先后）；给了 view 只取这一种（没标的旧图算主视角）。"""
    metas = sorted(_metas(directory), key=lambda m: (m.created, m.id))
    return [m for m in metas if view is None or (m.view or "main") == view]


def read_meta(directory: Path, cid: str) -> ConceptMeta | None:
    p = concept_dir(directory) / f"{cid}{META_SUFFIX}"
    return ConceptMeta.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None


def _write_meta(directory: Path, m: ConceptMeta) -> Path:
    f = concept_dir(directory) / f"{m.id}{META_SUFFIX}"
    skip = {k for k in NEW_FIELDS if getattr(m, k) is None}
    f.write_text(m.model_dump_json(indent=2, exclude=skip) + "\n", encoding="utf-8")
    return f


def version_group(m: ConceptMeta) -> str | None:
    """同一样东西的几版归为一组：背面视角一组，平面图按画了哪几个面分组。主视角靠 choose，不分组。"""
    if m.view == "back":
        return "back"
    if m.view == "flat" and m.faces:
        return "flat:" + "+".join(sorted(m.faces))
    return None


def version_groups(
    directory: Path, view: str, chosen_id: str | None
) -> dict[str, list[ConceptMeta]]:
    """选定方向这一支的背面视角 / 平面图，按组排好（组内按导入先后）。

    按约定命名为 `<选定>-…` 的才算这一支。选定的是照另一张主视角重出的（编号以它开头，如
    main-A-front2 之于 main-A）时，这一支还没重出的组沿用上一支的——只重出背面时，正面、顶、底的
    平面图不会因此不见；上一支的组画的面和这一支已有的组重叠就不沿用（已经重出过了）。
    同一支里画到同样面的组（两面一张和盒身一圈一张）只用最新导入的那组。
    整条线上一张都没有时（旧 job 没按约定命名）全部都算。"""
    metas = [m for m in concept_metas(directory, view) if (directory / m.file).is_file()]
    mains = {m.id for m in concept_metas(directory, "main")} | ({chosen_id} if chosen_id else set())

    def owner(cid: str) -> str | None:  # 最长的那个主视角编号前缀
        return max((i for i in mains if cid.startswith(f"{i}-")), key=len, default=None)

    chain: list[str] = []
    branch = chosen_id
    while branch is not None and branch not in chain:
        chain.append(branch)
        branch = owner(branch)
    groups: dict[str, list[ConceptMeta]] = {}
    covered: set[str] = set()
    for branch in chain:
        found: dict[str, list[ConceptMeta]] = {}
        for m in metas:
            g = version_group(m)
            if g is not None and g not in groups and owner(m.id) == branch:
                found.setdefault(g, []).append(m)
        # 新导入的组先占面：盒身一圈的一张（--body）导入后，同一支更早的两面一张就不再在用
        for g, vs in sorted(
            found.items(), key=lambda kv: (kv[1][-1].created, kv[1][-1].id), reverse=True
        ):
            drawn = set(vs[0].faces or []) or {g}
            if not drawn & covered:
                groups[g] = vs
                covered |= drawn
    if groups:
        return groups
    for m in metas:
        g = version_group(m)
        if g is not None:
            groups.setdefault(g, []).append(m)
    return groups


def read_in_use(directory: Path) -> dict[str, InUsePick]:
    p = concept_dir(directory) / IN_USE
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {g: InUsePick.model_validate(v) for g, v in raw.items()}
    except (ValueError, AttributeError):  # 写坏了：当没改用过，各组用最新的
        return {}


def in_use_versions(directory: Path, view: str, chosen_id: str | None) -> dict[str, ConceptMeta]:
    """每组现在在用的一张：默认最新导入的；用户改用了旧版就用那张，直到又导入新版。"""
    picks = read_in_use(directory)
    out: dict[str, ConceptMeta] = {}
    for g, versions in version_groups(directory, view, chosen_id).items():
        use, pick = versions[-1], picks.get(g)
        if pick is not None and pick.newest == versions[-1].id:
            use = next((m for m in versions if m.id == pick.id), use)
        out[g] = use
    return out


def back_view_for(directory: Path, chosen_id: str | None) -> ConceptMeta | None:
    """选定方向在用的背面视角：默认最新导入的一张，用户可以 `concept use` 改用旧版。"""
    return in_use_versions(directory, "back", chosen_id).get("back")


def _job_converter(directory: Path) -> tuple[ColorConverter | None, str | None]:
    """job 的色彩换算（按纸张自动选的标准 ICC 或工厂 ICC）：主色的近似 CMYK 和照片、插画同一个换算。
    返回换算器和用的 ICC 名字（没有 ICC、按简单公式换算时名字是 None）。"""
    from pacxio import dielines
    from pacxio.model.profile import load_profile
    from pacxio_pro.color import ColorConverter, IccError
    from pacxio_pro.color.icc import resolve_icc

    job = dielines.load_job(directory)[0]
    if job is None:
        return None, None
    profile = load_profile(job.profile)
    try:
        icc = resolve_icc(profile, job, directory)
        return ColorConverter(icc.path, profile.tac_limit_pct), icc.name if icc.path else None
    except IccError:
        return None, None


def box_faces(dl: Dieline) -> dict[str, tuple[str, bool]]:
    """盒子模型里看得到的面：{面 key: (刀版面 id, 是不是两片拼的)}。多部件盒型（目录刀版）是合上后
    看得到的面，刀版面 id 写全名"部件.面"。"""
    if dl.style_meta.get("parts"):
        faces = dl.style_meta.get("set_faces")
        return {k: (str(v), False) for k, v in faces.items()} if isinstance(faces, dict) else {}
    from pacxio.mockup.fold import build_box

    return {f.key: (f.panel, bool(f.parts)) for f in build_box(dl).faces}


SPLIT_NAMES = {"top": "Top panel", "bottom": "Bottom panel"}


def no_flat_faces(dl: Dieline) -> dict[str, str]:
    """看得到、但不出平面图的面 → 中文名：抽屉的内托前后脸（细长的墙面，AI 画不对比例，抽屉测试 F-17）。"""
    faces = (dl.style_meta.get("set") or {}).get("no_flat")
    return {str(k): str(v) for k, v in faces.items()} if isinstance(faces, dict) else {}


def split_face_issues(dl: Dieline) -> list[Issue]:
    """两片翼 / 摇盖拼成的面（锁底盒的底、开槽箱的顶和底）、抽屉的内托前后脸不出平面图：说明原因和怎么做
    （平面图提示词的返回、工作台 ④ 用）。"""
    split = [Issue(id="SPLIT", message=t("{name} ({panel}) is formed by two flaps and has no separate flat", name=t(SPLIT_NAMES.get(key, key)), panel=panel),
                   fix=t("Draw SVG artwork for the component flaps from the main view and layout draft. See references/vector.md."))
             for key, (panel, split) in box_faces(dl).items() if split]  # fmt: skip
    return split + [Issue(id="NO_FLAT", message=t("{label}{face} is a narrow wall and has no flat", label=name if lang() == "zh" else "", face=face),
                          fix=t("Lay out this panel from the main view, back view, and draft. Use fill for the background, text for copy, and SVG for graphics."))
                    for face, name in no_flat_faces(dl).items()]  # fmt: skip


def resolve_face(dl: Dieline, name: str) -> str:
    """面的简写 top / bottom / left / right → 这个盒型里对应的刀版面（底面：反插盒 btm_lid、扣底盒 /
    锁底盒 btm_front、飞机盒 base）；不是简写的原样返回。"""
    name = name.strip()
    key = FACE_KEYS.get(name)
    if key is None:
        return name
    hit = box_faces(dl).get(key)
    return hit[0] if hit else FACE_ALIASES[name]


def normalize_faces(
    dl: Dieline, view: str | None, faces: list[str] | None
) -> tuple[list[str] | None, Issue | None]:
    """校验 --view / --faces：面 id 来自刀版（top / bottom / left / right 可作简写）；
    平面图必须写明面，几个面时要在展开图里从左到右相接（如 front,side_b）。"""
    if view is not None and view not in VIEWS:
        return None, Issue(id="ARG", message=t("Unknown view {view}", view=view),
                           fix=t("Use --view main, back (back and left), or flat"))  # fmt: skip
    if not faces:
        if view == "flat":
            return None, Issue(id="ARG", message=t("Specify which panels are in the unfolded concept image"),
                               fix=t("List panels from left to right, for example --faces front,side_b. For the top, use --faces top_lid."))  # fmt: skip
        return None, None
    if view is None:
        return None, Issue(id="ARG", message=t("--faces requires --view"),
                           fix=t("For example, --view flat --faces front,side_b"))  # fmt: skip
    # 盒身、盖，加上盒子模型里单独成面的底（扣底盒 btm_front、飞机盒 base）：和平面图提示词出的面一致
    seen = box_faces(dl)
    single = {pid for pid, split in seen.values() if not split}
    panels = {p.id: p for p in dl.panels if p.role in FACE_ROLES or p.id in single}
    ids = [resolve_face(dl, f) for f in faces if f.strip()]
    unknown = [f for f in ids if f not in panels]
    if unknown or len(set(ids)) != len(ids):
        split = [f for f in unknown if (f, True) in seen.values()]
        what = t("unknown panels: {panels}", panels=t(", ").join(unknown)) if unknown else t("a panel is listed twice")
        if split:
            what += t(" ({panels} are made of two flaps and get no flat of their own)", panels=t(", ").join(split))
        return None, Issue(id="ARG", message=t("--faces {faces}: {what}", faces=",".join(faces), what=what),
                           fix=t("Available concept panels: ") + ", ".join(panels) + t(" (top, bottom, left, and right are accepted aliases)"))  # fmt: skip
    skipped = [f for f in ids if f in no_flat_faces(dl)]
    if view == "flat" and skipped:
        return None, Issue(id="ARG", message=t("--faces {faces}: {skipped} do not have flat concept images", faces=','.join(faces), skipped=t(", ").join(skipped)),
                           fix=t("Lay out this panel from the main and back views and layout draft"))  # fmt: skip
    if view == "flat" and len(ids) > 1:
        boxes = [panels[f].polygon.bbox() for f in ids]
        joined = all(panels[f].role == PanelRole.BODY for f in ids) and all(
            abs(a[2] - b[0]) < 1.0 for a, b in zip(boxes, boxes[1:], strict=False)
        )
        if isinstance(dl.style_meta.get("set_faces"), dict) or dl.style_meta.get("flat_stacks"):
            # 多部件盒型、声明了上下叠的盒型（飞机盒）：和平面图提示词同样分组（天盖侧壁按合上后的位置成对；
            # 抽屉外套 左侧 | 顶 | 右侧 一条；飞机盒 盖 + 插舌、背板 + 底、两个侧面上下叠）
            from pacxio.concept.prompts import _flat_groups

            groups = [g for g in _flat_groups(dl) if len(g) > 1]
            if ids not in groups:
                return None, Issue(id="ARG",
                                   message=t("--faces {faces}: use the panel groupings from this style's flat concept prompts", faces=','.join(ids)),
                                   fix=t("Available concept panels: ") + t("; ").join(",".join(g) for g in groups) + t(". Make one image for each remaining panel."))  # fmt: skip
            joined = True
        if not joined:
            body = sorted((p for p in panels.values() if p.role == PanelRole.BODY),
                          key=lambda p: p.polygon.bbox()[0])  # fmt: skip
            pairs = ", ".join(f"{a.id},{b.id}" for a, b in zip(body, body[1:], strict=False))
            return None, Issue(id="ARG",
                               message=t("--faces {faces}: panels in a flat image must adjoin from left to right on the dieline", faces=','.join(ids)),
                               fix=t("Adjoining body panels: {pairs}. Make the top and bottom separately.", pairs=pairs))  # fmt: skip
    return ids, None


def _next_id(directory: Path) -> str:
    taken = {m.id for m in _metas(directory)}
    n = 1
    while f"c{n}" in taken:
        n += 1
    return f"c{n}"


def _load(directory: Path, r: Result) -> Job | None:
    job, errs = load_job_checked(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
    return job


def _save(
    directory: Path, cid: str, img: Image.Image, meta: dict[str, object]
) -> tuple[Path, Path]:
    d = concept_dir(directory)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{cid}.png"
    img.save(dest, format="PNG")
    m = ConceptMeta(
        id=cid,
        file=str(dest.relative_to(directory)),
        created=now_iso(),
        sha256=sha256_of(dest),
        size_px=img.size,
        **meta,
    )
    return dest, _write_meta(directory, m)


def _open(data: bytes | Path) -> Image.Image:
    img = Image.open(io.BytesIO(data) if isinstance(data, bytes) else data)
    img.load()
    out = ImageOps.exif_transpose(img)
    assert out is not None
    return out.convert("RGB")


def _set_meta(job: Job) -> dict[str, object] | None:
    """多部件盒型合上后的样子（天地盖、抽屉盒的 style_meta["set"]）；单部件盒型或刀版生成不了时 None。"""
    from pacxio.dielines import build_parts_for

    dset, _ = build_parts_for(job)
    if dset is None or not dset.multipart:
        return None
    meta = next(iter(dset.parts.values())).style_meta.get("set")
    return meta if isinstance(meta, dict) else None


def _box_dims(job: Job) -> tuple[float, float, float]:
    """概念图提示词里的盒子尺寸（mm）：单部件盒型是参数里的 L / W / H；多部件盒型是合上后的外形
    （天地盖的 L / W / H 是地盒内尺寸，画出来的是盖着盖子的盒子，天地盖测试 F-06）。"""
    meta = _set_meta(job)
    outer = meta.get("closed_outer") if meta else None
    if isinstance(outer, list) and len(outer) == 3:
        return (round(float(outer[0]), 1), round(float(outer[1]), 1), round(float(outer[2]), 1))
    if job.style == "structure":
        d = job.box_dims()
        return (float(d["L"]), float(d["W"]), float(d["H"]))
    style = get_style(job.style)
    q = style.param_schema().model_validate(job.params).model_dump()
    return (float(q["L"]), float(q["W"]), float(q["H"]))


ASSEMBLY_EN = {"tray_lid": "Show the box closed, with the lid on (the lid covers the base).",
               "drawer": "Show the drawer pulled about halfway out of its sleeve."}  # fmt: skip


def _structure_lines(job: Job) -> list[str]:
    """盒子怎么摆（多部件盒型）、开窗这类结构说明（概念图要画出来）。刀版生成不了时不加窗口。"""
    from pacxio.visual.prompts import liner_line

    lines = [ASSEMBLY_EN[job.style]] if job.style in ASSEMBLY_EN else []
    lines += liner_line(job.material.liner)  # 牛皮面纸（开槽箱测试 R-01）
    if job.style == "tray_lid":  # 全罩时 AI 爱画出一圈地盒（天地盖测试 F-07）
        meta = _set_meta(job)
        if meta and meta.get("full_cover"):
            lines.append("Full-cover lid: when closed the lid reaches all the way down, so the base tray is "
                         "NOT visible at all; draw only the lid.")  # fmt: skip
        elif meta:
            lines.append(
                "Half lid: the lid covers the upper part; the lower part of the base tray shows below it."
            )
    if job.style == "drawer":  # 拉手缺口是结构，概念图要画出来（抽屉测试 F-06）
        notch = (_set_meta(job) or {}).get("pull_notch")
        if isinstance(notch, dict):
            where = " and of the sleeve bottom" if len(notch.get("faces", [])) > 1 else ""
            lines.append(f"A semicircular finger notch about {2 * float(notch['r']):.0f} mm wide is cut into the "
                         f"middle of the front edge of the sleeve top{where}; keep text away from it.")  # fmt: skip
    if not job.windows:
        return lines
    from pacxio.brief import window_lines
    from pacxio.dielines import build_dieline_for

    dl, _ = build_dieline_for(job)
    return lines + (window_lines(job, dl) if dl is not None else [])


# ---------------- 命令 ----------------


def generate_concepts(
    directory: Path,
    design: str,
    texts: list[str],
    n: int = 1,
    scene: str | None = None,
    provider_name: str = "openai",
    provider: ImageProvider | None = None,
) -> Result:
    """出 n 张概念图（每张一次出图请求，花钱 / 消耗额度）。"""
    r = Result(command="concept gen", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    if not 1 <= n <= MAX_CONCEPTS_PER_CALL:
        return r.fail(
            Issue(
                id="ARG",
                message=t("--n must be between 1 and {maximum}", maximum=MAX_CONCEPTS_PER_CALL),
                fix=t("Two or three directions per run are usually enough"),
            ),
            2,
        )
    if provider is None:
        if provider_name not in PROVIDERS:
            return r.fail(
                Issue(
                    id="ARG",
                    message=t("Unknown image provider {provider}", provider=provider_name),
                    fix=t("Available concept panels: ") + ", ".join(PROVIDERS),
                ),
                2,
            )
        provider = PROVIDERS[provider_name]()
    prompt = build_concept_prompt(
        design, texts, job.style, _box_dims(job), scene, _structure_lines(job)
    )
    made: list[str] = []
    for _ in range(n):
        try:
            res = provider.generate(GenRequest(prompt=prompt, size=CONCEPT_SIZE))
            img = _open(res.image)
        except ProviderError as e:
            r.errors.append(e.issue)
            r.ok = False
            r.exit_code = max(r.exit_code, e.exit_code)
            break
        except (UnidentifiedImageError, OSError) as e:
            r.fail(
                Issue(id="PROVIDER", message=t("The image provider returned an invalid image: {error}", error=e), fix=t("Try again later")), 1
            )
            break
        cid = _next_id(directory)
        dest, meta = _save(
            directory,
            cid,
            img,
            {
                "source_type": "openai" if provider.name == "openai" else "file",
                "provider": provider.name,
                "model": res.model,
                "design": design,
                "texts": texts,
                "scene": scene,
                "full_prompt": prompt,
                "revised_prompt": res.revised_prompt,
            },
        )
        made.append(cid)
        r.artifacts += [str(dest), str(meta)]
    r.data = {"concepts": made, "prompt": prompt}
    if made:
        r.warnings.append(
            Issue(
                id="CONCEPT",
                message=t("Concept images are visual references. Check AI-generated proportions, copy, fonts, and details before rebuilding the print artwork."),
                fix=t("Show the concept images to the decision-maker. Record their choice with `concept choose <job> <id>`, then use it to guide the final artwork."),
            )
        )
    return r


def _check_view(job: Job, view: str | None, faces: list[str] | None, r: Result) -> list[str] | None:
    """校验视角与面；有错时记进 r（r.ok 变 False）。"""
    if view is None and not faces:
        return None
    from pacxio.dielines import build_dieline_for

    dl, errs = build_dieline_for(job)
    if dl is None:
        r.errors += errs
        r.ok, r.exit_code = False, 1
        return None
    ids, issue = normalize_faces(dl, view, faces)
    if issue is not None:
        r.fail(issue, 2)
    return ids


def _pick_flat(
    job: Job, sources: list[Path], faces: list[str], r: Result
) -> tuple[Path | None, list[dict[str, object]]]:
    """同一张平面图的几张备选：逐张量比例，挑各面误差最大值最小的那张（量不出的排最后）。"""
    from pacxio.dielines import build_dieline_for

    dl, errs = build_dieline_for(job)
    if dl is None:
        r.errors += errs
        r.ok, r.exit_code = False, 1
        return None, []
    rows: list[dict[str, object]] = []
    best: tuple[float, Path] | None = None
    for src in sources:
        row: dict[str, object] = {"file": str(src)}
        try:
            img = _open(src)
            sizes, turns = _face_sizes(dl, faces, src.name)
            cut = measure_flat(img, faces, sizes, turns, stack_axis(dl, faces))
        except (FlatError, UnidentifiedImageError, OSError) as e:
            row["error"] = str(e)
        else:
            errors = {c.face: round(c.aspect_error, 4) for c in cut.faces}
            worst = max(abs(e) for e in errors.values())
            x0, y0, x1, y1 = cut.bounds
            total, height = cut.size_mm
            overall = ((x1 - x0) / (y1 - y0)) / (total / height) - 1
            row.update(aspect_errors=errors, worst=round(worst, 4), overall=round(overall, 4))
            score = abs(overall) + worst / 4  # 先看整张比例（），面宽分配其次
            if best is None or score < best[0]:
                best = (score, src)
        rows.append(row)
    pick = best[1] if best is not None else sources[0]
    for row in rows:
        row["picked"] = row["file"] == str(pick)
    return pick, rows


def import_concept(
    directory: Path,
    source: Path | None,
    name: str | None = None,
    codex_latest: bool = False,
    view: str | None = None,
    faces: list[str] | None = None,
    candidates: list[Path] | None = None,
) -> Result:
    """导入别处做好的概念图（设计师 / 其它 AI 工具 / Codex 内置出图）。

    view：main 主视角 / back 背面 + 左侧视角 / flat 展开平面图；faces：图里的面（平面图必填，从左到右）。
    candidates：同一张平面图的几张备选（含 source），量比例后只导入最准的那张——AI 画平面图常把
    宽的面画得更宽，一次出两三张挑一张，比一张一张重出快。"""
    r = Result(command="concept import", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    face_ids = _check_view(job, view, faces, r)
    if not r.ok:
        return r
    rows: list[dict[str, object]] = []
    if candidates and len(candidates) > 1:
        if view != "flat" or not face_ids or codex_latest:
            return r.fail(Issue(id="ARG", message=t("Importing multiple alternatives together is available for flat concept images only"),
                                fix=t("Add --view flat --faces … and supply each image path without --codex-latest")), 2)  # fmt: skip
        missing = [str(p) for p in candidates if not p.is_file()]
        if missing:
            return r.fail(
                Issue(id="ASSET", message=t("Images not found: ") + t(", ").join(missing), fix=t("Check the path")), 2
            )
        source, rows = _pick_flat(job, candidates, face_ids, r)
        if not r.ok:
            return r
    stype: SourceType = "file"
    if codex_latest:
        source, stype = latest_image(codex_generated_dir()), "codex_latest"
        if source is None:
            return r.fail(
                Issue(
                    id="ASSET",
                    message=t("No concept image found in {folder}", folder=codex_generated_dir()),
                    fix=t("Generate an image in Codex first or supply an image path"),
                ),
                2,
            )
    if source is None or not source.is_file():
        return r.fail(Issue(id="ASSET", message=t("Image not found: {path}", path=source), fix=t("Check the path")), 2)
    cid = NAME_RE.sub("_", name).strip("_") if name else _next_id(directory)
    if (concept_dir(directory) / f"{cid}.png").exists():
        return r.fail(Issue(id="ASSET", message=t("Concept {id} already exists", id=cid), fix=t("Choose another concept --name")), 2)
    try:
        img = _open(source)
    except (UnidentifiedImageError, OSError) as e:
        return r.fail(
            Issue(
                id="ASSET", message=t("Could not read image {image}: {error}", image=source.name, error=e), fix=t("Use PNG, JPEG, or WebP")
            ),
            2,
        )
    dup = same_concept(directory, source, img)
    reuse: list[str] = []
    if dup is not None and view == "flat" and face_ids and not codex_latest:
        previous = read_meta(directory, dup)
        matches = [m for m in _metas(directory) if previous and m.sha256 == previous.sha256]
        # Explicitly importing the same art for other faces is legitimate (e.g. plain
        # top/bottom). Check every match so a third import cannot duplicate either face.
        if matches and all(
            m.view == "flat" and m.faces and not set(face_ids).intersection(m.faces)
            for m in matches
        ):
            reuse = [m.id for m in matches]
    if dup is not None and not reuse:  # --codex-latest 误取旧图、相同面重复导入仍拦截
        fix = (
            t("Codex may not have made the new image yet: --codex-latest takes the newest image in "
              "generated_images. Have Codex make the image from the prompt first, then import it")
            if codex_latest
            else t("Use a different image; to use this one, use {id} directly", id=dup)
        )
        return r.fail(Issue(id="DUPLICATE", message=t("This image is identical to existing concept {id}", id=dup),
                            fix=fix), 2)  # fmt: skip
    fields: dict[str, object] = {"source_type": stype, "source_path": str(source)}
    if view is not None:
        fields.update(view=view, faces=face_ids)
    dest, meta = _save(directory, cid, img, fields)
    r.artifacts = [str(dest), str(meta)]
    r.data = {"id": cid, "size_px": list(img.size)}
    if reuse:
        r.data["reused_flat_from"] = reuse
    if view is not None:
        r.data.update(view=view, faces=face_ids)
    if rows:  # 没挑中的备选不导入，只在输出里列出各自的误差
        r.data["candidates"] = rows
    if view == "flat":
        _report_ratio(directory, job, cid, r)
    return r


def tag_concept(directory: Path, cid: str, view: str, faces: list[str] | None = None) -> Result:
    """给已有的概念图补标视角与面（旧图没有这两项）。只改 meta 里的 view / faces，图不动。"""
    r = Result(command="concept tag", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    m = read_meta(directory, cid)
    if m is None:
        ids = ", ".join(x.id for x in _metas(directory)) or t("(no concept images yet)")
        return r.fail(Issue(id="ASSET", message=t("Concept {id} does not exist", id=cid), fix=t("Available concepts: {ids}", ids=ids)), 2)
    face_ids = _check_view(job, view, faces, r)
    if not r.ok:
        return r
    chosen = read_chosen(directory)
    if chosen is not None and chosen.id == cid and view != "main":
        return r.fail(Issue(id="CONCEPT", message=t("{id} is the selected direction and must remain a main-view image", id=cid),
                            fix=t("Choose a different main image with `concept choose` before changing this image's view")), 2)  # fmt: skip
    m = m.model_copy(update={"view": view, "faces": face_ids})  # view 已由 normalize_faces 校验
    r.artifacts = [str(_write_meta(directory, m))]
    r.data = {"id": cid, "view": view, "faces": face_ids}
    if view == "flat":
        _report_ratio(directory, job, cid, r)
    return r


def same_concept(directory: Path, source: Path, img: Image.Image | None = None) -> str | None:
    """和这张图完全相同的已有概念图 id（统一转成 RGB、存成 PNG 后比 sha256）。

    用来拦住"Codex 还没出新图，--codex-latest 取到上一张"——导入成新概念图没意义，导入成印刷
    素材更会把带文字的概念图带进印刷流程。"""
    metas = _metas(directory)
    if not metas:
        return None
    if img is None:
        try:
            img = _open(source)
        except (UnidentifiedImageError, OSError):
            return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    digest = hashlib.sha256(buf.getvalue()).hexdigest()
    return next((m.id for m in metas if m.sha256 == digest), None)


def list_concepts(directory: Path) -> Result:
    r = Result(command="concept list", job=str(directory))
    if _load(directory, r) is None:
        return r
    chosen = read_chosen(directory)
    cid = chosen.id if chosen else None
    versioned: dict[str, int] = {}  # 有几版的组里每张的 id → 这一组有几版
    in_use: dict[str, str] = {}
    for view in ("back", "flat"):
        for versions in version_groups(directory, view, cid).values():
            versioned.update((m.id, len(versions)) for m in versions)
        in_use.update((g, m.id) for g, m in in_use_versions(directory, view, cid).items())
    r.data = {
        "concepts": [
            {
                "id": m.id,
                "file": m.file,
                "source": m.source_type,
                "model": m.model,
                "texts": m.texts,
                "view": m.view,
                "faces": m.faces,
                "chosen": chosen is not None and chosen.id == m.id,
                # 同一组有几版、在用的是不是这张；不分版本的（主视角、别的方向）是 None
                "versions": versioned.get(m.id),
                "in_use": m.id in in_use.values() if m.id in versioned else None,
            }  # fmt: skip
            for m in _metas(directory)
        ],
        "chosen": cid,
        "in_use": in_use,
    }
    return r


def use_concept(
    directory: Path, cid: str, by: str | None = None, note: str | None = None
) -> Result:
    """改用某一版背面视角 / 平面图。同组的其他版本留着，只是不再拿来做参考、对照。"""
    r = Result(command="concept use", job=str(directory))
    if _load(directory, r) is None:
        return r
    m = read_meta(directory, cid)
    if m is None or not (directory / m.file).is_file():
        ids = ", ".join(x.id for x in _metas(directory)) or t("(no concept images yet)")
        return r.fail(Issue(id="ASSET", message=t("Concept {id} does not exist", id=cid), fix=t("Available concepts: {ids}", ids=ids)), 2)
    g = version_group(m)
    if g is None or m.view is None:
        fix = (t("For main views, choose a direction with `concept choose`") if (m.view or "main") == "main"
               else t("First tag which panels it shows: `concept tag {job} {id} --view flat --faces front,side_b`",
                      job=directory, id=cid))  # fmt: skip
        return r.fail(Issue(id="CONCEPT", message=t("{id} must be a back view or a flat with tagged panels to select it as the active version", id=cid),
                            fix=fix), 2)  # fmt: skip
    chosen = read_chosen(directory)
    chosen_id = chosen.id if chosen else None
    versions = version_groups(directory, m.view, chosen_id).get(g, [])
    if all(v.id != cid for v in versions):
        return r.fail(Issue(id="CONCEPT", message=t("{id} is outside the selected direction {chosen}", id=cid, chosen=chosen_id),
                            fix=t("Available in the same group: ") + (", ".join(v.id for v in versions) or t("(none)"))), 2)  # fmt: skip
    before = in_use_versions(directory, m.view, chosen_id)[g]
    picks = read_in_use(directory)
    picks[g] = InUsePick(id=cid, newest=versions[-1].id, at=now_iso(), by=by, note=note)
    out = concept_dir(directory) / IN_USE
    rows = {k: v.model_dump(exclude_none=True) for k, v in sorted(picks.items())}
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    r.artifacts = [str(out)]
    r.data = {"id": cid, "view": m.view, "faces": m.faces, "previous": before.id,
              "versions": [v.id for v in versions]}  # fmt: skip
    if before.id != cid:
        used = (t("flat concept prompts (`concept prompt --step flat`) and mockup confirmation sheets (`mockup`)") if m.view == "back"
                else t("flat confirmation sheets (`flat`)"))  # fmt: skip
        r.warnings.append(Issue(id="CONCEPT", message=t("Switched to {id} from {previous}. Existing {artifacts} still use the previous image.", id=cid, previous=before.id, artifacts=used),
                                fix=t("Rerun the commands for those artifacts to use this image")))  # fmt: skip
    return r


FLATS_CONFIRMED = "flats_confirmed.json"  # 用户确认了在用的这组展开平面图（LUMERA 测试 ISSUE-003）
FlatsConfirmation = Literal["none", "confirmed", "stale"]


def flat_set(directory: Path) -> dict[str, str]:
    """在用的这组展开平面图：概念图 id → 图的 sha256（换了图、改用别的版本，确认就过期）。"""
    chosen = read_chosen(directory)
    metas = in_use_versions(directory, "flat", chosen.id if chosen else None).values()
    return {m.id: m.sha256 for m in metas}


def flats_confirmation(directory: Path) -> FlatsConfirmation:
    """这组平面图有没有用户确认的记录：没有 / 确认了 / 确认后又换过图（要重新确认）。"""
    p = concept_dir(directory) / FLATS_CONFIRMED
    if not p.is_file():
        return "none"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    return "confirmed" if data.get("flats") == flat_set(directory) else "stale"


def confirm_flats(directory: Path, by: str | None = None, note: str | None = None) -> Result:
    """记录用户确认了在用的这组展开平面图（只能用户本人确认：对话里说了，或在工作台上点）。

    只是记录：正式稿照这组平面图做；导出前的闸门仍是刀版确认图。比例不合格的不能确认，先重出。"""
    from pacxio.dielines import build_dieline_for

    r = Result(command="concept confirm", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    flats = flat_set(directory)
    if not flats:
        return r.fail(
            Issue(id="CONCEPT", message=t("There are no active flat concept images"),
                  fix=t("Generate and import flats with concept prompt --step flat and concept import --view flat")),
            2,
        )  # fmt: skip
    dl, errs = build_dieline_for(job)
    if dl is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    bad = [i for i, c in flat_checks(directory, dl).items() if c.issue is not None and i in flats]
    if bad:
        return r.fail(
            Issue(id="FLAT_RATIO", message=t("These flats cannot be confirmed because their proportions are out of range: {ids}", ids=t(", ").join(bad)),
                  fix=t("Generate replacements with concept prompt --step flat and import them with concept import")),
            2,
        )  # fmt: skip
    chosen = read_chosen(directory)
    record = {"by": by, "at": now_iso(), "note": note, "concept": chosen.id if chosen else None,
              "flats": flats}  # fmt: skip
    out = concept_dir(directory) / FLATS_CONFIRMED
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    r.artifacts = [str(out)]
    r.data = {"flats": sorted(flats), "by": by}
    if not by:
        r.warnings.append(
            Issue(id="CONCEPT", message=t("The approver was not specified"), fix=t("Use --by to identify the user who confirmed"))
        )
    return r


def choose_concept(
    directory: Path,
    cid: str,
    region: Region | None = None,
    by: str | None = None,
    note: str | None = None,
) -> Result:
    """记录决策者选定的方向，并提取主色作为正式设计的起点。"""
    r = Result(command="concept choose", job=str(directory))
    if _load(directory, r) is None:
        return r
    path = concept_dir(directory) / f"{cid}.png"
    if not path.is_file():
        ids = ", ".join(m.id for m in _metas(directory)) or t("(no concept images yet)")
        return r.fail(Issue(id="ASSET", message=t("Concept {id} does not exist", id=cid), fix=t("Available concepts: {ids}", ids=ids)), 2)
    meta = read_meta(directory, cid)
    if meta is not None and meta.view not in (None, "main"):
        mains = ", ".join(m.id for m in concept_metas(directory, "main")) or t("(no main-view images yet)")
        return r.fail(Issue(id="CONCEPT",
                            message=t("{id} is a {view} reference and cannot be selected as the main direction", id=cid, view=view_label(meta.view)),
                            fix=t("Choose a main-view image: {ids}", ids=mains)), 2)  # fmt: skip
    try:
        conv, icc_name = _job_converter(directory)
        palette = extract_palette(Image.open(path), region, conv=conv)
    except ValueError as e:
        return r.fail(
            Issue(
                id="ARG",
                message=str(e),
                fix=t("For example, --region 0.2,0.1,0.65,0.85 to select the front of the box"),
            ),
            2,
        )
    chosen = Chosen(
        id=cid,
        file=str(path.relative_to(directory)),
        sha256=sha256_of(path),
        chosen_at=now_iso(),
        by=by,
        note=note,
        region=region,
        palette=[pc.__dict__ for pc in palette],
    )
    out = concept_dir(directory) / CHOSEN
    out.write_text(chosen.model_dump_json(indent=2) + "\n", encoding="utf-8")
    r.artifacts = [str(out)]
    r.data = {"id": cid, "palette": [_palette_row(pc) for pc in palette]}
    if region is None:
        r.warnings.append(
            Issue(
                id="CONCEPT",
                message=t("Colours were sampled from the entire concept image. The background or tabletop may dominate."),
                fix=t("Use --region x0,y0,x1,y1 with values from 0 to 1 to sample only the box"),
            )
        )
    # 有 ICC（按纸张自动选的或工厂给的）就按它说话（天地盖测试 F-09）
    how = t("converted using {icc}", icc=icc_name) if icc_name else t("approximated with a simple formula")
    r.warnings.append(
        Issue(
            id="CONCEPT",
            message=t("CMYK values sampled from the concept are {method} and serve as a starting point", method=how),
            fix=t("Use the brand guide or swatches for brand colours. Record Pantone codes and specified CMYK with `color add`."),
        )
    )
    return r


def flat_cut_for(directory: Path, dl: Dieline, meta: ConceptMeta) -> tuple[Image.Image, FlatCut]:
    """量一张展开平面图（view=flat、带 faces）：返回原图和每个面的像素框。量不出时抛 FlatError。"""
    if meta.view != "flat" or not meta.faces:
        raise FlatError(t("{id} is not tagged as a flat concept (--view flat --faces …)", id=meta.id))
    img = _open(directory / meta.file)
    sizes, turns = _face_sizes(dl, list(meta.faces), meta.id)
    return img, measure_flat(img, list(meta.faces), sizes, turns, stack_axis(dl, list(meta.faces)))


def _face_sizes(
    dl: Dieline, faces: list[str], what: str
) -> tuple[list[tuple[float, float]], list[int]]:
    """平面图里各面（从左到右）画出来的宽、高 mm，和要逆时针转几个 90° 才正立（drawn_layout）。"""
    rects = face_rects(dl)
    missing = [f for f in faces if f not in rects]
    if missing:
        raise FlatError(t("{id} specifies panels that are not visible on this style: {panels}", id=what, panels=t(", ").join(missing)))
    layout = drawn_layout(dl, faces)
    return [size for size, _ in layout], [turn for _, turn in layout]


@dataclass(frozen=True)
class FlatCheck:
    """一张展开平面图量出来的比例。issue 不为 None 就要重出。"""

    errors: dict[str, float]  # 面 → 比例误差（+0.14 = 画宽了 14%）
    overall: float | None  # 整张画面的比例误差；量不出时 None
    issue: Issue | None = None
    bounds: tuple[int, int, int, int] | None = None  # 画面（各面）在图里的像素框，不含四周留白
    note: Issue | None = None  # 整张比例对、单面差 10–20%：只提醒，不用重出


def _faces_txt(errors: dict[str, float]) -> str:
    separator = "、" if lang() == "zh" else ", "
    return separator.join(f"{display_name(FACE_ZH.get(f, f))} {e:+.0%}" for f, e in errors.items())


def check_flat(directory: Path, dl: Dieline, meta: ConceptMeta) -> FlatCheck:
    """量一张平面图：整张画面的比例和真实差超过 ASPECT_WARN、某个面差超过 FACE_REDO，或者量不出，就要重出；
    整张对、单面差 ASPECT_WARN ~ FACE_REDO 只提醒（AI 把折线画偏一点，拉正按折线切，）。

    拉正能把图拉回真实尺寸，但图里的照片、文字也跟着变形，已经不是 AI 画的样子了，量位置也会偏。"""
    try:
        img, cut = flat_cut_for(directory, dl, meta)
    except FlatError as e:
        return FlatCheck({}, None, Issue(id="FLAT_RATIO", message=t("Could not measure {id}'s proportions ({error}). Generate a new image.", id=meta.id, error=e),
                                         fix=_redo_fix()))  # fmt: skip
    errors = {c.face: round(c.aspect_error, 4) for c in cut.faces}
    x0, y0, x1, y1 = cut.bounds
    total, height = cut.size_mm
    overall = round(((x1 - x0) / (y1 - y0)) / (total / height) - 1, 4)
    bad = {f: e for f, e in errors.items() if abs(e) > ASPECT_WARN}
    issue = note = None
    if swapped_faces(img, cut):  # 比例可能看着对，按比例切开的两面内容却是错的（开槽箱测试 R-06）
        separator = "、" if lang() == "zh" else ", "
        names = separator.join(display_name(FACE_ZH.get(c.face, c.face)) for c in cut.faces)
        issue = Issue(id="FLAT_RATIO", fix=_redo_fix() + t(". Specify which panel is on the left and each panel's width. Use the unfolded layout for panel order."),
                      message=t("The crease in {id} is where it would be if the panels were swapped. Regenerate the image and check the order of {panels}.", id=meta.id, panels=names))  # fmt: skip
    elif abs(overall) > ASPECT_WARN and len(errors) > 1:
        issue = Issue(id="FLAT_RATIO", fix=_redo_fix(),
                      message=t("The overall aspect ratio of {id} differs by {error:+.0%} (tolerance ±{tolerance:.0%}). Regenerate the image.", id=meta.id, error=overall, tolerance=ASPECT_WARN))  # fmt: skip
    elif bad and (len(errors) == 1 or any(abs(e) > FACE_REDO for e in bad.values())):
        issue = Issue(id="FLAT_RATIO", fix=_redo_fix(),
                      message=t("The proportions of {id} differ too much ({panels}, tolerance ±{tolerance:.0%}). Regenerate the image.", id=meta.id, panels=_faces_txt(bad), tolerance=ASPECT_WARN))  # fmt: skip
    elif bad:
        note = Issue(id="FLAT_FACE", fix=t("The overall proportions are within tolerance. Rectify using the detected crease and check the individual panel widths."),
                     message=t("The overall proportions of {id} are within tolerance ({error:+.0%}), but the panel widths differ: {panels}", id=meta.id, error=overall, panels=_faces_txt(bad)))  # fmt: skip
    return FlatCheck(errors, overall, issue, cut.bounds, note)


def flat_checks(directory: Path, dl: Dieline) -> dict[str, FlatCheck]:
    """在用的展开平面图逐张量比例。

    除了每个面，盒身的几张（画了几个面的）也互相比：它们画的是一样高的盒身，整张比例差超过
    ASPECT_WARN 时，偏得多的那张要重出——两张图的高度对不上。多部件盒型按合上后看得到的面量。"""
    if dl.style_meta.get("parts") and not dl.style_meta.get("set_rects"):
        return {}
    chosen = read_chosen(directory)
    skip = set(no_flat_faces(dl))  # 不出平面图的面以前导入过的图：不量、不要求重出
    metas = [m for m in in_use_versions(directory, "flat", chosen.id if chosen else None).values()
             if not set(m.faces or []) <= skip or not m.faces]  # fmt: skip
    out = {m.id: check_flat(directory, dl, m) for m in metas}
    bodies = {i: c.overall for i, c in out.items() if c.overall is not None and len(c.errors) > 1}
    if len(bodies) > 1:
        lo, hi = min(bodies, key=lambda i: bodies[i]), max(bodies, key=lambda i: bodies[i])
        if bodies[hi] - bodies[lo] > ASPECT_WARN:
            worse = max((lo, hi), key=lambda i: abs(bodies[i]))
            other = lo if worse == hi else hi
            if out[worse].issue is None:
                msg = t(
                    "{first} and {second} show the same box body height but have different overall proportions ({first_error:+.0%} and {second_error:+.0%}). Regenerate {first}.",
                    first=worse,
                    second=other,
                    first_error=bodies[worse],
                    second_error=bodies[other],
                )
                issue = Issue(id="FLAT_RATIO", message=msg, fix=_redo_fix())
                out[worse] = replace(out[worse], issue=issue)
    return out


def _report_ratio(directory: Path, job: Job, cid: str, r: Result) -> None:
    """刚导入 / 标好的平面图：量比例，要重出时提醒。不挡导入：图留着、新版在用，工作台上标出来。"""
    from pacxio.dielines import build_dieline_for

    dl, _ = build_dieline_for(job)
    meta = read_meta(directory, cid)
    if (
        dl is None
        or meta is None
        or (dl.style_meta.get("parts") and not dl.style_meta.get("set_rects"))
    ):
        return
    check = flat_checks(directory, dl).get(cid) or check_flat(directory, dl, meta)
    r.data.update(
        aspect_errors=check.errors, overall_error=check.overall, redo=check.issue is not None
    )
    r.warnings += [i for i in (check.issue, check.note) if i is not None]


def rectified_path(directory: Path, cid: str, face: str) -> Path:
    return concept_dir(directory) / RECTIFIED_DIR / f"{cid}.{face}.png"


def rectify_concept(
    directory: Path, cid: str, px_per_mm: float = 4.0, grid: bool = False
) -> Result:
    """把一张展开平面图拉正：每个面按真实毫米尺寸存成单独的图（concepts/rectified/，仍是概念图）。"""
    r = Result(command="concept rectify", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    if not 0.5 <= px_per_mm <= 20:
        return r.fail(Issue(id="ARG", message=t("--px-per-mm must be between 0.5 and 20"),
                            fix=t("Use 4–5 px/mm for measurements. A 200 mm panel will be 800–1000 pixels wide.")), 2)  # fmt: skip
    meta = read_meta(directory, cid)
    if meta is None or not (directory / meta.file).is_file():
        ids = (
            ", ".join(m.id for m in concept_metas(directory, "flat"))
            or t("(no images tagged as flat concepts yet)")
        )
        return r.fail(Issue(id="ASSET", message=t("Concept {id} does not exist", id=cid), fix=t("Flat concept images: {ids}", ids=ids)), 2)
    if meta.view != "flat" or not meta.faces:
        return r.fail(Issue(id="CONCEPT", message=t("{id} has not been tagged as a flat concept image", id=cid),
                            fix=t("Run `concept tag {job} {id} --view flat --faces front,side_b` with panels in left-to-right order", job=directory, id=cid)), 2)  # fmt: skip
    from pacxio.dielines import build_dieline_for

    dl, errs = build_dieline_for(job)
    if dl is None:
        r.errors += errs
        r.ok, r.exit_code = False, 1
        return r
    try:
        img, cut = flat_cut_for(directory, dl, meta)
    except FlatError as e:
        return r.fail(Issue(id="CONCEPT", message=t("{id}: {error}", id=cid, error=e),
                            fix=t("Regenerate the flat with a centred image and a solid white border")), 1)  # fmt: skip
    rows = []
    for c in cut.faces:
        out = rectify_face(img, c, px_per_mm)
        if grid:
            out = draw_grid(out, px_per_mm)
        f = rectified_path(directory, cid, c.face)
        f.parent.mkdir(parents=True, exist_ok=True)
        out.save(f)
        r.artifacts.append(str(f))
        rows.append({"face": c.face, "file": str(f), "box_px": list(c.box),
                     "size_mm": [round(v, 2) for v in c.upright_mm],
                     "aspect_error": round(c.aspect_error, 4)})  # fmt: skip
    r.data = {"id": cid, "bounds_px": list(cut.bounds), "folds_found": cut.folds_found,
              "folds_px": [list(f) if f else None for f in cut.folds], "px_per_mm": px_per_mm,
              "faces": rows}  # fmt: skip
    if not cut.folds_found:
        r.warnings.append(Issue(id="CONCEPT", message=t("No crease was found between the panels in {id}. The image was split using the actual panel width ratio.", id=cid),
                                fix=t("Unequal panel widths in the generated image can shift the cut. When regenerating, ask for a thin crease line spanning the full height.")))  # fmt: skip
    check = flat_checks(directory, dl).get(cid) or check_flat(directory, dl, meta)
    r.data["redo"] = check.issue is not None
    if check.issue is not None:  # 拉正了也不算数：图里的东西跟着变形了
        r.warnings.append(check.issue)
    return r


def _palette_row(pc: PaletteColor) -> dict[str, object]:
    c, m, y, k = pc.cmyk_pct
    return {"hex": pc.hex, "share": pc.share, "cmyk": f"{c},{m},{y},{k}"}


def copy_chosen_to(directory: Path, dest: Path) -> Path | None:
    """把选定的概念图复制到 dest（效果图确认页 / 交付包用）。"""
    chosen = read_chosen(directory)
    if chosen is None:
        return None
    src = directory / chosen.file
    if not src.is_file():
        return None
    shutil.copyfile(src, dest)
    return dest


__all__ = [
    "CONCEPT_DIR",
    "Chosen",
    "ConceptMeta",
    "FlatCheck",
    "InUsePick",
    "back_view_for",
    "box_faces",
    "check_flat",
    "choose_concept",
    "concept_guard_issue",
    "concept_metas",
    "flat_checks",
    "generate_concepts",
    "import_concept",
    "in_use_versions",
    "is_concept_path",
    "list_concepts",
    "normalize_faces",
    "read_chosen",
    "read_meta",
    "resolve_face",
    "no_flat_faces",
    "split_face_issues",
    "tag_concept",
    "use_concept",
    "version_group",
    "version_groups",
]
