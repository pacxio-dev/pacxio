"""导入素材、出图（开源，）：`visual import` / `visual gen`。

素材都放进 job 的 `assets/`（Job 目录即状态），旁边写 `.meta.json` 记录来源与处理过程。
素材预处理、转矢量（`visual prepare` / `visual trace`）在 `visual.service`（闭源）。
"""

from __future__ import annotations

import io
import re
import shutil
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from pacxio import dielines
from pacxio.concept.service import concept_guard_issue, is_concept_path, same_concept
from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.visual.meta import (
    AssetMeta,
    SourceType,
    now_iso,
    sha256_of,
    write_meta,
)
from pacxio.visual.prompts import build_prompt
from pacxio.visual.providers import PROVIDERS, GenRequest, ImageProvider, ProviderError
from pacxio.visual.providers.file_import import VECTOR_SUFFIXES, codex_generated_dir, latest_image

ASSETS_DIR = "assets"
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")
VECTOR_ROLES = {"logo", "icon"}
ROLES = ("hero", "pattern", "texture", "logo", "icon", "graphic")


def _fail(r: Result, issue: Issue, code: int = 2) -> Result:
    return r.fail(issue, exit_code=code)


def _bad_role(r: Result, role: str) -> bool:
    if role in ROLES:
        return True
    _fail(r, Issue(id="ARG", message=t("Unknown asset role {role}", role=role), fix=t("Available roles: ") + ", ".join(ROLES)))
    return False


def _clean_name(raw: str) -> str:
    name = NAME_RE.sub("_", raw).strip("_")
    return name or "asset"


def _require_job(directory: Path, r: Result) -> bool:
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok = False
        r.exit_code = 2
        return False
    return True


def _save_raster(img: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")


def _open_image(data: bytes | Path) -> Image.Image:
    src = io.BytesIO(data) if isinstance(data, bytes) else data
    img = Image.open(src)
    img.load()
    out = ImageOps.exif_transpose(img)
    assert out is not None
    return out.convert("RGBA") if "A" in out.getbands() or out.mode == "P" else out.convert("RGB")


def import_asset(
    directory: Path,
    source: Path | None,
    role: str,
    name: str | None = None,
    codex_latest: bool = False,
) -> Result:
    """导入用户提供的图片，或 Codex 内置出图目录里最新的一张（）。"""
    r = Result(command="visual import", job=str(directory))
    if not _bad_role(r, role) or not _require_job(directory, r):
        return r
    stype: SourceType = "file"
    if codex_latest:
        d = codex_generated_dir()
        source = latest_image(d)
        stype = "codex_latest"
        if source is None:
            return _fail(
                r,
                Issue(
                    id="ASSET",
                    message=t("No image found in {folder}", folder=d),
                    fix=t("Generate an image in Codex first, set CODEX_HOME, or supply the image path"),
                ),
            )
    if source is None or not source.is_file():
        return _fail(
            r,
            Issue(id="ASSET", message=t("Image not found: {path}", path=source), fix=t("Check the path or use --codex-latest")),
        )
    if is_concept_path(directory, source):
        return _fail(r, concept_guard_issue(t("asset import")))
    if source.suffix.lower() not in VECTOR_SUFFIXES:
        dup = same_concept(directory, source)
        if dup is not None:  # 常见原因：Codex 还没出新图，--codex-latest 取到的是概念图那一张
            issue = concept_guard_issue(t("asset import"))
            issue.message = f"这张图就是概念图 {dup}。" + issue.message
            if codex_latest:
                issue.fix = "Codex 可能还没出新图（--codex-latest 取的是最新一张）：先按提示词出无文字的素材图，再导入"
            return _fail(r, issue)
    name = _clean_name(name or source.stem)
    assets = directory / ASSETS_DIR

    if source.suffix.lower() in VECTOR_SUFFIXES:
        if role not in VECTOR_ROLES:
            return _fail(
                r,
                Issue(
                    id="ASSET",
                    message=t("SVG is available for logo or icon assets. Current role: {role}.", role=role),
                    fix=t("Supply a PNG or JPEG for hero, pattern, or texture"),
                ),
            )
        dest = assets / f"{name}.svg"
        size_px = (0, 0)
    else:
        dest = assets / f"{name}.png"
    if dest.exists():
        return _fail(
            r,
            Issue(id="ASSET", message=t("Font file {file} already exists", file=dest.name), fix=t("Choose another --name or remove the old asset first")),
        )
    if dest.suffix == ".svg":
        assets.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    else:
        try:
            img = _open_image(source)
        except (UnidentifiedImageError, OSError) as e:
            return _fail(
                r,
                Issue(
                    id="ASSET", message=t("Could not read image {image}: {error}", image=source.name, error=e), fix=t("Use PNG or JPEG")
                ),
            )
        _save_raster(img, dest)
        size_px = img.size
    meta = AssetMeta(
        name=name, role=role, source_type=stype, source_path=str(source), created=now_iso(),
        sha256=sha256_of(dest), size_px=size_px,
    )  # fmt: skip
    meta_file = write_meta(dest, meta)
    r.artifacts = [str(dest), str(meta_file)]
    r.data = {"asset": str(dest.relative_to(directory)), "role": role, "size_px": list(size_px)}
    return r


def generate_asset(
    directory: Path,
    prompt: str,
    role: str,
    name: str,
    size: tuple[int, int] | None = None,
    transparent: bool = False,
    provider_name: str = "openai",
    provider: ImageProvider | None = None,
) -> Result:
    """用出图来源生成素材。强制约束（无文字、正视平面……）由引擎追加，不能关闭。"""
    r = Result(command="visual gen", job=str(directory))
    if not _bad_role(r, role) or not _require_job(directory, r):
        return r
    if provider is None:
        if provider_name not in PROVIDERS:
            return _fail(
                r,
                Issue(
                    id="ARG",
                    message=t("Unknown image provider {provider}", provider=provider_name),
                    fix=t("Available image providers: ") + ", ".join(PROVIDERS) + t(", or use `visual import`"),
                ),
            )
        provider = PROVIDERS[provider_name]()
    name = _clean_name(name)
    dest = directory / ASSETS_DIR / f"{name}.png"
    if dest.exists():
        return _fail(r, Issue(id="ASSET", message=t("Font file {file} already exists", file=dest.name), fix=t("Choose another --name")))
    full = build_prompt(prompt, role, transparent)
    try:
        res = provider.generate(GenRequest(prompt=full, size=size))
    except ProviderError as e:
        return _fail(r, e.issue, e.exit_code)
    try:
        img = _open_image(res.image)
    except (UnidentifiedImageError, OSError) as e:
        return _fail(
            r,
            Issue(id="PROVIDER", message=t("The image provider returned an invalid image: {error}", error=e), fix=t("Try again later")),
            1,
        )
    _save_raster(img, dest)
    meta = AssetMeta(
        name=name, role=role, source_type="openai" if provider.name == "openai" else "file",
        provider=provider.name, model=res.model, prompt=prompt, full_prompt=full,
        revised_prompt=res.revised_prompt, created=now_iso(), sha256=sha256_of(dest), size_px=img.size,
    )  # fmt: skip
    meta_file = write_meta(dest, meta)
    r.artifacts = [str(dest), str(meta_file)]
    r.data = {
        "asset": str(dest.relative_to(directory)),
        "role": role,
        "size_px": list(img.size),
        "model": res.model,
    }
    if transparent:
        r.warnings.append(
            Issue(
                id="V05",
                message=t("A solid green background was requested for background removal"),
                fix=t("Run `visual prepare <job> assets/{name}.png --role {role} --chroma-key` next", name=name, role=role),
            )
        )
    r.warnings.append(
        Issue(
            id="V03",
            message=t("The prompt requests artwork without text. The model may still draw text."),
            fix=t("Check with `visual prepare --check-text`. Typeset copy with `art add --kind text`."),
        )
    )
    return r


def find_asset(directory: Path, asset: str) -> Path:
    """素材路径：相对 job 目录（`assets/a.png`）、素材名（`a`），或从当前目录写起的路径（`jobs/x/assets/a.png`，
    `visual import` 输出里打印的就是这种，天地盖测试 F-11）。都找不到时返回按 job 目录拼的路径。"""
    p = Path(asset).expanduser()
    if p.is_absolute():
        return p
    for cand in (directory / p, directory / ASSETS_DIR / f"{asset}.png", p):
        if cand.is_file():
            return cand
    return directory / p
