"""素材元数据（ 第 6 步）：来源、提示词、模型、生成时间，保证可追溯。

每个素材 `assets/<name>.png` 旁边有 `assets/<name>.meta.json`。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from pacxio.export.pdfx import document_date
from pacxio.messages import t

Role = Literal["hero", "pattern", "texture", "logo", "icon", "graphic"]
SourceType = Literal["openai", "file", "codex_latest"]
META_SUFFIX = ".meta.json"
CONCEPT_DIR = "concepts"
RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")
RESTORE_STEPS = ("strip", "face")  # restore strip / face 记在素材元数据里的那一步
MAX_DERIVED = 8


class PrepStep(BaseModel):
    """prepare 做过的一步处理及其参数。"""

    step: str
    params: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class AssetMeta(BaseModel):
    name: str
    role: Role
    source_type: SourceType
    source_path: str | None = None  # 导入时的原路径（仅记录，不再读取）
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None  # 用户给的提示词
    full_prompt: str | None = None  # 实际发给模型的提示词（含强制约束）
    revised_prompt: str | None = None  # 模型改写后的提示词（如果返回了）
    created: str
    sha256: str
    size_px: tuple[int, int]
    steps: list[PrepStep] = Field(default_factory=list)
    derived_from: str | None = None  # prepare 的输出指向它的原始素材


def now_iso() -> str:
    """文档日期；设置了 SOURCE_DATE_EPOCH 时用它（可复现构建的通用约定）。"""
    d: datetime = document_date()
    return d.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def is_illustration(meta: AssetMeta | None) -> bool:
    """整片插画：vtracer 全彩描出来的（最后一步是 method=vtracer 的 trace）。颜色不逐个指定 CMYK，
    印刷时和照片一样按 ICC 换算、压总墨量（）。"""
    if meta is None or not meta.steps:
        return False
    last = meta.steps[-1]
    return last.step == "trace" and last.params.get("method") in ("vtracer", "gradient")


def _same_picture(a: Path, b: Path) -> bool:
    """同一张图：字节一样，或存成别的格式但像素一样。"""
    if a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes():
        return True
    try:
        with Image.open(a) as ia, Image.open(b) as ib:
            return ia.size == ib.size and ia.convert("RGB").tobytes() == ib.convert("RGB").tobytes()
    except (OSError, ValueError):
        return False


# ---------------- R2-4 艺术字（）：准入硬门槛、确认指纹与失效校验 ----------------

LETTERING_ALLOWED_CATEGORIES = frozenset({"brand", "product_name", "variant", "slogan", "selling_point"})
LETTERING_MAX_LATIN_WORDS = 5  # 【初值】：拉丁文 ≤ 5 词
LETTERING_MAX_CJK_CHARS = 8  # 【初值】：中文 ≤ 8 字
LETTERING_MIN_HEIGHT_MM = 4.0  # 【初值】：字高 ≥ 4 mm

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")
_INFO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:ages?\s*\d+\s*\+?|\d+\s*\+\s*years?)\b|适用年龄|\d+\s*岁"), "age information"),
    (re.compile(r"(?i)\b\d+(?:\.\d+)?\s*(?:pcs?|pieces?|count|ct|pack|packs|bags?|sachets?|cookies?|bars?|tablets?|capsules?|ml|l|g|kg|mg|oz|fl\.?\s*oz|lb)\b"), "quantity or net-content information"),
    (re.compile(r"(?i)\b(?:net\s*(?:wt\.?|weight|vol\.?|contents?))\b|净含量|净重|规格|件装"), "net-content or spec information"),
    (re.compile(r"(?i)\b(?:warning|caution|danger|ingredients?|contains|allergen|keep\s+out|not\s+for\s+children|directions?|usage|storage|best\s+before|exp\.?|batch|manufactured\s+by|distributed\s+by)\b|警告|警示|注意|配料|成分|保质期|生产日期|执行标准|使用方法|储存"), "regulatory or instructional text"),
    (re.compile(r"(?i)(?:https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.-]+|\b\d{8,}\b)"), "URL, contact, or barcode digits"),
)


def is_info_or_regulatory_text(text: str) -> str | None:
    """ 规则①：判断文案是否属于信息 / 法规 / 年龄件数 / 净含量 / 网址条码（再大也必须引擎排）。"""
    for pat, label in _INFO_PATTERNS:
        if pat.search(text):
            return label
    return None


def lettering_word_count(text: str) -> tuple[int, int]:
    """返回 (拉丁词数, 中文字数)。"""
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_WORD_RE.findall(text))
    return latin, cjk


def lettering_admission_block(category: str, text: str) -> str | None:
    """检查 准入的前两条硬门槛（类别与信息/法规过滤 + 字数上限），不通过返回原因。"""
    if category not in LETTERING_ALLOWED_CATEGORIES:
        return t(
            "Category {category} is informational or regulatory copy and must be typeset by the engine",
            category=category,
        )
    hit = is_info_or_regulatory_text(text)
    if hit is not None:
        return t(
            "Copy contains {kind} and must be typeset by the engine regardless of size",
            kind=hit,
        )
    latin, cjk = lettering_word_count(text)
    if cjk > LETTERING_MAX_CJK_CHARS:
        return t(
            "Chinese character count {count} exceeds the limit of {limit} for traced lettering",
            count=cjk,
            limit=LETTERING_MAX_CJK_CHARS,
        )
    if latin > LETTERING_MAX_LATIN_WORDS:
        return t(
            "Word count {count} exceeds the limit of {limit} for traced lettering",
            count=latin,
            limit=LETTERING_MAX_LATIN_WORDS,
        )
    if (
        cjk > 0
        and latin > 0
        and (cjk / LETTERING_MAX_CJK_CHARS + latin / LETTERING_MAX_LATIN_WORDS) > 1.0
    ):
        return t(
            "Mixed copy length ({cjk} CJK chars + {latin} words) exceeds the limit for traced lettering",
            cjk=cjk,
            latin=latin,
        )
    return None


def lettering_fingerprint(
    content_id: str,
    text: str,
    concept_sha256: str,
    confirmed_by: str = "",
    confirmed_at: str = "",
) -> str:
    """艺术字确认指纹（）：绑定清单条目、逐字文案、来源平面图哈希与用户确认记录。"""
    blob = json.dumps(
        {
            "content_id": content_id,
            "text": text,
            "concept_sha256": concept_sha256,
            "confirmed_by": confirmed_by,
            "confirmed_at": confirmed_at,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _concept_file(job_dir: Path, concept_id: str) -> Path | None:
    concepts = job_dir / CONCEPT_DIR
    if not concepts.is_dir():
        return None
    for ext in RASTER_SUFFIXES:
        cand = concepts / f"{concept_id}{ext}"
        if cand.is_file():
            return cand
    return None


def validate_lettering_record(
    job_dir: Path,
    job: Any,
    rec: dict[str, Any],
    *,
    require_confirmed: bool = False,
) -> str | None:
    """校验艺术字记录是否仍有效：有效返回 None，失效或未确认返回原因。

    `status="auto_traced"`（：默认自动描摹进初稿，待随刀版图一并确认）：
    - 在 `require_confirmed=False`（渲染/印前/工作台预览）时，只要清单原文逐字一致、准入合法、
      来源平面图未变且防篡改指纹有效即放行；
    - 在 `require_confirmed=True`（正式导出兜底）时要求已升为 `confirmed`。"""
    cid = str(rec.get("content_id") or "").strip()
    text = rec.get("text")
    concept = str(rec.get("concept") or "").strip()
    csha = str(rec.get("concept_sha256") or "").strip()
    status = str(rec.get("status") or "confirmed").strip()
    by = str(rec.get("confirmed_by") or "").strip()
    at = str(rec.get("confirmed_at") or "").strip()
    fp = str(rec.get("fingerprint") or "").strip()
    if status not in ("confirmed", "auto_traced"):
        return t("Missing user confirmation record (unconfirmed lettering cannot enter print files)")
    if status == "auto_traced":
        if not (cid and isinstance(text, str) and concept and csha and fp):
            return t(
                "Missing user confirmation record (unconfirmed lettering cannot enter print files)"
            )
    else:
        if not (cid and isinstance(text, str) and concept and csha and by and at and fp):
            return t(
                "Missing user confirmation record (unconfirmed lettering cannot enter print files)"
            )
    inv = getattr(job, "content", None)
    item = inv.get(cid) if inv is not None else None
    if item is None or item.text is None:
        return t(
            "Content item {item} is missing from the content list (lettering confirmation expired)",
            item=cid,
        )
    if item.text != text:
        return t(
            "Content item {item} changed from “{old}” to “{new}” (lettering confirmation expired)",
            item=cid,
            old=text,
            new=item.text,
        )
    block = lettering_admission_block(item.category, item.text)
    if block is not None:
        return block
    cfile = _concept_file(job_dir, concept)
    if cfile is None:
        return t(
            "Source flat concept {concept} no longer exists (lettering confirmation expired)",
            concept=concept,
        )
    if sha256_of(cfile) != csha:
        return t(
            "Source flat concept {concept} changed since confirmation (lettering confirmation expired)",
            concept=concept,
        )
    if status == "auto_traced":
        want_fp = lettering_fingerprint(cid, text, csha, confirmed_by="", confirmed_at="")
        if fp != want_fp:
            return t("Lettering confirmation fingerprint mismatch (re-confirmation required)")
        if require_confirmed:
            return t(
                "Auto-traced lettering has not been confirmed yet (approve the dieline proof or confirm explicitly)"
            )
        return None
    want_fp = lettering_fingerprint(cid, text, csha, confirmed_by=by, confirmed_at=at)
    if fp != want_fp:
        return t("Lettering confirmation fingerprint mismatch (re-confirmation required)")
    return None


def sign_off_auto_lettering(
    job_dir: Path,
    job: Any,
    by: str,
    at: str,
) -> tuple[list[str], list[str]]:
    """随阶段 ⑤ 刀版确认将全部有效的 auto_traced 艺术字一并签章升级为 confirmed（同步更新 job 与 .meta.json）。

    返回 `(signed_ids, errors)`；若有任意一条已失效（文案改了或平面图换了），不落盘并返回 errors。"""
    targets: list[Any] = []
    errors: list[str] = []
    for pl in getattr(job, "placements", []):
        let = getattr(pl, "lettering", None)
        if let is None:
            continue
        why = validate_lettering_record(job_dir, job, let.model_dump(), require_confirmed=False)
        if why is not None:
            errors.append(f"{pl.id}: {why}")
        elif let.status == "auto_traced":
            targets.append(pl)
    if errors or not targets:
        return [], errors

    who = by.strip() or "user"
    signed: list[str] = []
    for pl in targets:
        let = pl.lettering
        fp = lettering_fingerprint(
            let.content_id,
            let.text,
            let.concept_sha256,
            confirmed_by=who,
            confirmed_at=at,
        )
        let.status = "confirmed"
        let.confirmed_by = who
        let.confirmed_at = at
        let.fingerprint = fp
        signed.append(pl.id)
        if pl.source:
            svg_path = job_dir / pl.source
            meta = read_meta(svg_path)
            if meta is not None:
                let_step = next(
                    (step for step in reversed(meta.steps) if step.step == "lettering"),
                    None,
                )
                if let_step is not None:
                    let_step.params["status"] = "confirmed"
                    let_step.params["confirmed_by"] = who
                    let_step.params["confirmed_at"] = at
                    let_step.params["fingerprint"] = fp
                    write_meta(svg_path, meta)
    job.save(job_dir)
    return signed, []


def _text_left(svg: Path, job_dir: Path | None = None) -> str | None:
    """从展开平面图描成的矢量：描的时候源头里还认得出和清单对得上的 AI 字，就不能进（：去掉文字、
    过文字检查才能进；R2 基准 p4 的手写标题就是这样漏进印刷文件的）。没查过的（旧素材、没装 tesseract）不拦。

     例外：经用户逐条确认、与信息清单逐字一致且来源平面图未变的展示艺术字（step="lettering"）放行；
    一旦清单改字或平面图换新，确认自动作废并重新拦截。"""
    meta = read_meta(svg)
    if meta is None:
        return None
    let_step = next((step for step in reversed(meta.steps) if step.step == "lettering"), None)
    if let_step is not None:
        if job_dir is None:
            return t(
                "{file} is traced lettering and requires a job context to verify confirmation",
                file=svg.name,
            )
        try:
            from pacxio.model.job import Job

            job = Job.load(job_dir)
        except Exception:  # noqa: BLE001
            return t(
                "{file} is traced lettering and job.json could not be read to verify confirmation",
                file=svg.name,
            )
        why = validate_lettering_record(job_dir, job, let_step.params)
        return (
            None
            if why is None
            else t("{file} traced lettering failed verification: {why}", file=svg.name, why=why)
        )
    if not any(step.step in RESTORE_STEPS for step in meta.steps):
        return None
    trace = next((step for step in reversed(meta.steps) if step.step == "trace"), None)
    check = trace.params.get("text_check") if trace is not None else None
    words = check.get("listed") if isinstance(check, dict) else None
    if not words:
        return None
    return t(
        "{file} was traced from the unfolded concept with AI-drawn text still in it: {words}",
        file=svg.name,
        words=", ".join(map(str, words[:8])),
    )


def not_printable(job_dir: Path, path: Path) -> str | None:
    """这个素材为什么不能进印刷文件（能进返回 None）：概念图、和概念图像素相同的图、从展开平面图裁出来的
    位图（`restore strip / face` 的图，和用它再处理出来的）。这些图里可能有 AI 画的字，分辨率也不够，
    只给描摹当源头；描成的矢量（.svg）可以进（、13.7，）。"""
    concepts = job_dir / CONCEPT_DIR
    try:
        path.resolve().relative_to(concepts.resolve())
        return t("{file} is a concept image", file=path.name)
    except ValueError:
        pass
    if path.suffix.lower() == ".svg" and path.is_file():
        return _text_left(path, job_dir)
    if path.suffix.lower() not in RASTER_SUFFIXES or not path.is_file():
        return None
    current = path
    for _ in range(MAX_DERIVED):  # 顺着 derived_from 往回找：prepare 过的也算
        meta = read_meta(current)
        if meta is None:
            break
        if any(step.step in RESTORE_STEPS for step in meta.steps):
            return t("{file} was cropped from an unfolded concept ({source}) and can only be used as a tracing reference", file=path.name, source=current.name)
        if not meta.derived_from:
            break
        current = job_dir / meta.derived_from
        if not current.is_file():
            break
    if concepts.is_dir():
        for image in concepts.iterdir():
            if image.suffix.lower() in RASTER_SUFFIXES and _same_picture(path, image):
                return t("{file} is identical to concept image {concept}", file=path.name, concept=image.name)
    return None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meta_path(asset: Path) -> Path:
    return asset.with_name(asset.stem + META_SUFFIX)


def write_meta(asset: Path, meta: AssetMeta) -> Path:
    p = meta_path(asset)
    p.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return p


def read_meta(asset: Path) -> AssetMeta | None:
    """读素材元数据；没有、或不是素材元数据的格式（例如概念图的元数据）时返回 None，不抛异常。"""
    p = meta_path(asset)
    if not p.is_file():
        return None
    try:
        return AssetMeta.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError:
        return None
