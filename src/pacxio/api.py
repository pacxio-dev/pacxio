"""核心函数层：无全局状态、无交互输入，入参出参均为可序列化模型。

CLI 只是它的薄外壳，以后 MCP 直接复用（规格 14、16 节）。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from pacxio import dielines
from pacxio.cloud.config import closed_available
from pacxio.concept.service import concept_guard_issue
from pacxio.export import dieline_to_svg
from pacxio.fontfiles import TextError, find_font, parse_font_chain
from pacxio.jobfiles import import_asset as _import_asset
from pacxio.jobfiles import is_concept as _is_concept
from pacxio.layout.errors import LayoutError
from pacxio.layout.panels import panel_map
from pacxio.layout.spaces import body_strip
from pacxio.messages import display_name, t, use_language
from pacxio.messages import lang as lang_now
from pacxio.mockup.approval import approval_state, design_fingerprint, style_version
from pacxio.model.common import Issue, Result, parameter_issues, validation_message
from pacxio.model.dieline import Dieline
from pacxio.model.job import JOB_FILE, CornerFinish, Job, Placement, PrintSpec, Window
from pacxio.model.profile import (
    Material,
    ProfileNotFoundError,
    load_materials,
    load_profile,
    output_language,
    profile_dirs,
    resolve_material,
)
from pacxio.model.report import Report
from pacxio.notes import open_notes_issue
from pacxio.preflight.rules import check_crease_sides, check_s01
from pacxio.styles import all_styles, get_style
from pacxio.styles.base import ORIENTATION_VERIFIED, style_advisories
from pacxio.windows import area_findings, windows_by_panel

DIELINE_SVG = "dieline.svg"
PREVIEW_PNG = "preview.png"
PREVIEW_MAP_SUFFIX = ".map.json"  # preview.png → preview.map.json
ASSETS_DIR = "assets"
RENDER_DPI = (
    200  # render 出的就是刀版确认图：要看得清正文小字（= export.confirm.CONFIRM_DPI，测试对照）
)
MIN_PYTHON = (3, 11)
REQUIRED_MODULES = (
    "pydantic", "typer", "yaml", "shapely", "ezdxf", "reportlab", "pikepdf", "PIL", "numpy",
    "fontTools",
)  # fmt: skip
PRO_MODULES = ("uharfbuzz", "svgelements", "vtracer")  # 闭源包（pacxio-pro）的依赖：装着它时才查
DEFAULT_FONT = "SourceHanSansSC-Bold"
SAME_FACE_TOL_MM = 0.5  # 两个面尺寸差在这个范围内才能共用版面（补偿造成的微小差异）
SERIF_FONT = "SourceHanSerifSC-Regular"


def check_env() -> Result:
    """检查运行环境，输出缺什么、怎么装（规格 14 节）。"""
    r = Result(command="env check")
    checks: dict[str, Any] = {"python": sys.version.split()[0]}
    if sys.version_info < MIN_PYTHON:
        r.fail(
            Issue(
                id="ENV",
                message=t(
                    "Python {have} is older than the required {major}.{minor}",
                    have=sys.version.split()[0],
                    major=MIN_PYTHON[0],
                    minor=MIN_PYTHON[1],
                ),
                fix=t("Install Python 3.11+ with uv: uv python install 3.12"),
            ),
            exit_code=3,
        )
    local = closed_available()
    for mod in REQUIRED_MODULES + (PRO_MODULES if local else ()):
        if importlib.util.find_spec(mod) is None:
            r.fail(
                Issue(
                    id="ENV", message=t("Missing dependency {mod}", mod=mod), fix=t("Run uv sync")
                ),
                exit_code=3,
            )
    try:
        checks["font"] = str(find_font(DEFAULT_FONT))
    except TextError as e:
        checks["font"] = None
        r.warnings.append(e.issue)  # 没有字体时 text 放置会失败；其余功能不受影响
    try:
        checks["serif_font"] = str(find_font(SERIF_FONT))
    except TextError:
        checks["serif_font"] = None  # 可选：衬线字体（思源宋体），不装也能用，只提示
        r.warnings.append(
            Issue(
                id="FONT",
                message=t(
                    "Optional: no serif font (Source Han Serif); only needed for serif headings"
                ),
                fix=t(
                    "To use serif headings, put Source Han Serif in a font directory or set PACXIO_FONT_DIR"
                ),
            )
        )
    gs = shutil.which("gs")
    checks["ghostscript"] = gs
    # 开源版（local 为假）：分色、ICC 都在云端，本机不用装
    checks["composition"] = "local" if local else "cloud"
    if gs is None and local:
        r.warnings.append(
            Issue(
                id="ENV",
                message=t(
                    "Ghostscript not found: separation checks (B02, F01) are skipped and marked in the report"
                ),
                fix="macOS: brew install ghostscript",
            )
        )
    try:
        prof = load_profile("default")
        name = display_name(prof.name) if prof.id == "default" else prof.name
        checks["profile_default"] = {"id": prof.id, "verified": prof.verified, "name": name}
        checks["icc"] = prof.icc.path
        # 按纸张自动选的标准 ICC（）：涂布 / 非涂布各找到了哪个
        found: dict[str, str | None] = {}
        if local:
            from pacxio_pro.color.icc import find_standard_icc

            for surface in ("coated", "uncoated"):
                path = find_standard_icc(prof, surface)
                found[surface] = str(path) if path else None
        checks["icc_auto"] = found if local else "cloud"
        if local and prof.icc.path is None and not all(found.values()):
            r.warnings.append(
                Issue(
                    id="X02",
                    message=t("Standard ICC profiles missing on this machine (")
                    + ", ".join(k for k, v in found.items() if not v)
                    + t(
                        "): photos for these papers are converted to print colors with a simple formula; check colors on a proof"
                    ),
                    fix=t(
                        "They come with Adobe software (Photoshop / Illustrator); if the factory gives you an ICC, set it as icc.path in the profile"
                    ),
                )
            )
    except ProfileNotFoundError as e:
        r.fail(
            Issue(
                id="ENV",
                message=str(e),
                fix=t("Run from the repository root, or set PACXIO_PROFILE_DIR"),
            ),
            exit_code=3,
        )
    checks["profile_dirs"] = [str(d) for d in profile_dirs()]
    r.data = checks
    return r


def _localized_params(props: dict[str, Any]) -> dict[str, Any]:
    """参数说明在代码里是英文（import 时求值，不能用 t()），输出时按当前语言换。"""
    return {
        key: prop | {"description": t(prop["description"])} if isinstance(prop.get("description"), str) else prop
        for key, prop in props.items()
    }


def list_styles() -> Result:
    r = Result(command="styles list")
    r.data["styles"] = [
        {
            "id": s.id,
            "name_zh": s.name_zh,
            "name": display_name(s.name_zh),
            "refs": {key: display_name(ref) for key, ref in s.refs.items()},
            "material_types": sorted(s.material_types),
            "version": s.version,
            "accuracy_level": "L2" if s.id in ORIENTATION_VERIFIED else "L0",
            "orientation_verified": s.id in ORIENTATION_VERIFIED,
            "params": _localized_params(s.param_schema().model_json_schema()["properties"]),
            **({"parts": {key: display_name(name) for key, name in getattr(s, "part_labels", {}).items()}}
               if getattr(s, "parts", None) else {}),  # fmt: skip
        }
        for s in all_styles()
    ]
    r.data["materials"] = {
        key: preset.model_dump() | {"name": display_name(preset.name)}
        for key, preset in load_materials().items()
    }
    return r


def list_style_presets(style: str | None = None) -> Result:
    """List reference design presets without adding structural registry entries."""
    from pacxio.styles.presets import design_presets

    r = Result(command="styles presets")
    if style is not None:
        try:
            get_style(style)
        except KeyError:
            return r.fail(
                Issue(
                    id="S03",
                    message=t("Unknown box style {style}", style=style),
                    fix=t("Run styles list to see the box styles"),
                ),
                exit_code=2,
            )
    r.data["presets"] = [p.describe() for p in design_presets(style)]
    return r


def init_job_from_preset(
    directory: Path,
    preset_id: str,
    params: dict[str, Any] | None = None,
    material: str | None = None,
    profile: str = "default",
    liner: str | None = None,
) -> Result:
    """Resolve a reference preset into ordinary job parameters, with explicit overrides."""
    from pacxio.styles.presets import get_preset

    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        return Result(command="job init-preset", job=str(directory)).fail(
            Issue(
                id="ARG",
                message=t("Presets can only be used for a new job or an empty folder"),
                fix=t(
                    "Use a new job folder; for an existing job change its parameters instead of re-initialising"
                ),
            ),
            exit_code=2,
        )
    try:
        preset = get_preset(preset_id)
    except KeyError:
        return Result(command="job init-preset", job=str(directory)).fail(
            Issue(
                id="S03",
                message=t("Unknown design preset {preset}", preset=preset_id),
                fix=t("Run styles presets to see the available IDs"),
            ),
            exit_code=2,
        )
    result = init_job(
        directory,
        preset.style,
        preset.parameters() | (params or {}),
        material or preset.material,
        profile,
        liner=liner if liner is not None else "white",
    )
    result.command = "job init-preset"
    if result.ok:
        result.data["preset_used"] = preset.id
    return result


def structure_params(
    fefco_code: str | None = None,
    ecma_code: str | None = None,
    recipe: dict[str, Any] | None = None,
    L: float | None = None,
    W: float | None = None,
    H: float | None = None,
    basis: str = "inner",
) -> tuple[str, dict[str, Any]] | Issue:
    """几何引擎的结构 → (盒型, 参数)：FEFCO / ECMA 编号按内尺寸写成配方，或直接给一份配方（可带 set 的多件套）。
    编号对应多件套（天地盖、组件……）时盒型是 structure_set。"""
    from pacxio.engine import ecma, fefco

    picked = [x for x in (fefco_code, ecma_code, recipe) if x is not None]
    if len(picked) != 1:
        return Issue(id="ARG", message=t("Give only one structure: one of --fefco / --ecma / --recipe"),
                     fix=t("Remove the extra one"))  # fmt: skip
    dims = (L, W, H)
    if recipe is None and any(v is None for v in dims):
        return Issue(
            id="ARG",
            message=t("Building a structure from a code needs the inside size --L / --W / --H"),
            fix=t("Add --L --W --H"),
        )
    if fefco_code is not None:
        code = fefco_code.strip()
        from pacxio.engine import combos

        if code in combos.COMBOS:  # 常用组合（engine/combos.py）：不是目录条目，按名字取配方
            return "structure", combos.recipe(code, L, W, H) | {"dim_basis": basis}  # type: ignore[arg-type]
        if code in fefco.SETS:
            return "structure_set", fefco.set_recipe(code, L, W, H)  # type: ignore[arg-type]
        try:
            params = fefco.recipe(code, L, W, H)  # type: ignore[arg-type]
        except ValueError:
            return Issue(
                id="S03",
                message=t("FEFCO {code} is not in the catalog", code=code),
                fix=t("Write the code with four digits (e.g. 0201, 0427.1)"),
            )
        return "structure", params | {"dim_basis": basis}
    if ecma_code is not None:
        try:
            params = ecma.recipe(ecma_code.strip().upper(), L, W, H)  # type: ignore[arg-type]
        except (KeyError, ValueError) as e:
            return Issue(
                id="S03",
                message=t("ECMA {code} not recognised: {error}", code=ecma_code, error=e),
                fix=t(
                    "Write the full code like A20.20.03.01 (bottom closure.top closure.bottom position.top position)"
                ),
            )
        return "structure", params | {"dim_basis": basis}
    assert recipe is not None
    params = dict(recipe)
    if "set" in params:
        if any(v is not None for v in dims):  # 以前会悄悄忽略（能力边界测试 2026-11-06）
            return Issue(id="ARG", message=t("A multi-part recipe has its sizes in each part's body; --L / --W / --H are not used"),
                         fix=t("Remove --L --W --H, or change each part's size in the recipe returned by structures show <code> --L …"))  # fmt: skip
        return "structure_set", params
    body = dict(params.get("body") or {})
    for k, v in zip(("L", "W", "H"), dims, strict=True):
        if v is not None:
            body[k] = v
    params["body"] = body
    params.setdefault("dim_basis", basis)
    return "structure", params


def structures_list(
    query: str | None = None,
    series: str | None = None,
    tags: list[str] | None = None,
    reclosable: bool | None = None,
    no_tape: bool | None = None,
    material: str | None = None,
    maturity: str | None = None,
    lang: str = "zh",
) -> Result:
    """结构目录（FEFCO 325 + ECMA 130）按条件筛；已校准的排前面。lang=en：给人看的字段用英文（）。"""
    from pacxio.engine import catalog, i18n

    r = Result(command="structures list")
    if (bad := _lang_issue(lang)) is not None:
        return r.fail(bad, exit_code=2)
    found = catalog.search(query, series, tags, reclosable, no_tape, material, maturity)
    r.data = {
        "count": len(found),
        "lang": lang,
        "structures": [i18n.entry_dict(e, lang) for e in found],
    }
    return r


def _lang_issue(lang: str) -> Issue | None:
    from pacxio.engine.i18n import LANGS

    if lang in LANGS:
        return None
    return Issue(
        id="ARG",
        message=t("Unknown language {lang}", lang=lang),
        fix=t("Available: ") + ", ".join(LANGS),
    )


def structure_show(
    code: str,
    L: float | None = None,
    W: float | None = None,
    H: float | None = None,
    lang: str = "zh",
) -> Result:
    """一款结构：目录信息；给了尺寸再附配方（job init --recipe 能直接用）。lang=en：说明用英文。"""
    from pacxio.engine import catalog, i18n

    r = Result(command="structures show")
    if (bad := _lang_issue(lang)) is not None:
        return r.fail(bad, exit_code=2)
    e = catalog.get(code)
    if e is None:
        return r.fail(
            Issue(
                id="S03",
                message=t("{code} is not in the catalog", code=code),
                fix=t("Look up the code with structures list"),
            ),
            exit_code=2,
        )
    r.data = {"structure": i18n.entry_dict(e, lang)}
    if L is not None and W is not None and H is not None:
        found = structure_params(None if e.catalog == "ECMA" else code, code if e.catalog == "ECMA" else None,
                                 None, L, W, H)  # fmt: skip
        if isinstance(found, Issue):
            return r.fail(found, exit_code=2)
        r.data |= {"style": found[0], "recipe": found[1]}
    return r


def structure_preview(
    code: str, L: float, W: float, H: float, out: Path, material: str | None = None
) -> Result:
    """不建 job，出这款结构的刀版图和 3D 图（按角色上色，只看结构）。"""
    from pacxio.engine.preview import write_preview

    r = Result(command="structures preview")
    try:
        info = write_preview(code, L, W, H, out, material)
    except (ValueError, KeyError) as e:
        return r.fail(Issue(id="S03", message=t("{code} cannot be built at {L:g} × {W:g} × {H:g}: {error}", code=code, L=L, W=W, H=H, error=e),
                            fix=t("Change the size, or choose another structure")), exit_code=1)  # fmt: skip
    r.artifacts = info["files"]
    r.data = {"structure": info["entry"], "material": info["material"]}
    return r


def structures_suggest(
    uses: list[str] | None = None,
    reclosable: bool | None = None,
    no_tape: bool | None = None,
    material: str | None = None,
    single: bool | None = None,
    query: str | None = None,
    limit: int = 3,
    dims: tuple[float, float, float] | None = None,
    out: Path | None = None,
) -> Result:
    """按需求从目录挑 2–3 个方案（先目录、后共创，GEOMETRY_ENGINE.md §1.1）；给了尺寸和输出目录就各出预览。"""
    from pacxio.engine import catalog, i18n

    r = Result(command="structures suggest")
    picked = catalog.suggest(uses, reclosable, no_tape, material, single, query,
                             limit if dims is None else limit * 6)  # fmt: skip
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for e, why in picked:
        if len(rows) >= limit:
            break
        if dims is not None:  # 给了尺寸：先按这组尺寸生成、做机构功能核对，不过的不推荐
            bad = _function_problems(e.code, e.catalog, dims)
            if bad:
                skipped.append({"code": e.code, "why": bad[:2]})
                continue
            why = [*why, t("Mechanism check passed for this size")]
        row: dict[str, Any] = {"structure": i18n.entry_dict(e, lang_now()), "why": why}
        if dims is not None and out is not None:
            pv = structure_preview(e.code, *dims, out, None)
            if pv.ok:
                row["preview"] = pv.artifacts
                r.artifacts += pv.artifacts
            else:
                row["preview_error"] = pv.errors[0].message
        rows.append(row)
    r.data = {"suggestions": rows, "skipped": skipped}
    if dims is None and rows:
        r.warnings.append(Issue(id="S07", message=t("No size given: the suggested structures have not had their mechanism checked at a size"),
                                fix=t("Suggest again with --L --W --H, or run structures check after creating the job")))  # fmt: skip
    if not rows:
        r.warnings.append(Issue(id="CAT", message=t("No structure in the catalog meets all of these needs"),
                                fix=t("Relax one need, or co-design a new structure with the user (mark it \"non-standard\" and assess it for practical use)")))  # fmt: skip
    return r


def _function_problems(code: str, catalog_name: str, dims: tuple[float, float, float]) -> list[str]:
    """目录款按这组尺寸生成后的问题：放不下（S03）或机构功能核对不过（S07）。"""
    from pacxio.engine import function_check

    found = structure_params(None if catalog_name == "ECMA" else code,
                             code if catalog_name == "ECMA" else None, None, *dims)  # fmt: skip
    if isinstance(found, Issue):
        return [found.message]
    style_id, params = found
    profile = load_profile("default")
    from pacxio.engine.catalog import default_material

    mat = resolve_material(default_material(code))
    style = get_style(style_id)
    try:
        p = style.param_schema().model_validate(params)
    except ValidationError as e:
        return [i.message for i in parameter_issues(e)]
    issues = style.validate(p, mat, profile)
    if issues:
        return [i.message for i in issues]
    if style_id == "structure_set":
        dls = style.generate_parts(p, mat, profile)  # type: ignore[attr-defined]
    else:
        dls = {None: style.generate(p, mat, profile)}
    return [i.message for name, dl in dls.items()
            for i in function_check.issues(dl, function_check.part_recipe(p, name))]  # fmt: skip


def structure_check(
    fefco_code: str | None = None,
    ecma_code: str | None = None,
    recipe: dict[str, Any] | None = None,
    dims: tuple[float | None, float | None, float | None] = (None, None, None),
    material: str | None = None,
    needs: list[str] | None = None,
    load_kg: float | None = None,
    directory: Path | None = None,
) -> Result:
    """实用性检查：编号 / 配方 / 已有 job → 靠什么合上、能否反复开合、免不免胶带、折好后穿插、成熟度，
    逐条对照需求（GEOMETRY_ENGINE.md §1.1 第 3 步）。不满足的需求是 warning，不是 error。"""
    from pacxio.engine.practical import check

    r = Result(command="structures check", job=str(directory) if directory else None)
    if directory is not None:
        job = _load_job(directory, r)
        if job is None:
            return r
        style_id, params, mat_id = job.style, dict(job.params), job.material.preset or "flute_b"
        if style_id not in ("structure", "structure_set"):
            return r.fail(Issue(id="ARG", message=t("{style} is not a geometry-engine structure", style=style_id),
                                fix=t("The practical check only applies to jobs built with --fefco / --ecma / --recipe")), exit_code=2)  # fmt: skip
    else:
        found = structure_params(fefco_code, ecma_code, recipe, *dims)
        if isinstance(found, Issue):
            return r.fail(found, exit_code=2)
        style_id, params = found
        from pacxio.engine.catalog import default_material

        mat_id = material or (
            default_material(fefco_code or ecma_code or "")
            if (fefco_code or ecma_code)
            else "sbs_350"
        )
    try:
        mat = resolve_material(mat_id)
    except KeyError:
        return r.fail(Issue(id="S03", message=t("Unknown material preset {material}", material=mat_id), fix=t("Available presets: ") + ", ".join(load_materials())),
                      exit_code=2)  # fmt: skip
    profile = load_profile("default")
    style = get_style(style_id)
    p = style.param_schema()(**params)
    issues = style.validate(p, mat, profile)
    if issues:
        return _fail(r, issues, 1)
    dls = (list(style.generate_parts(p, mat, profile).values()) if style_id == "structure_set"  # type: ignore[attr-defined]
           else [style.generate(p, mat, profile)])  # fmt: skip
    report = check(style_id, params, dls, needs, load_kg)
    r.data = report
    for x in report["function"]:
        tag = t("{part}: ", part=x["part"]) if x["part"] else ""
        r.warnings.append(Issue(id="S07", message=t("{tag}mechanism check failed: {message}", tag=tag, message=x["message"]),
                                fix=t("Not usable at this size (preflight will block it): change the size, choose another structure, or change the recipe")))  # fmt: skip
    for row in report["needs"]:
        if row["met"] is False:
            r.warnings.append(Issue(id="FIT", message=t("Need \"{need}\" not met: {why}", need=row["need_name"], why=row["why"]),
                                    fix=t("Choose another structure (structures suggest with this need), or change the recipe")))  # fmt: skip
    return r


def structure_editable(
    fefco_code: str | None = None,
    ecma_code: str | None = None,
    recipe: dict[str, Any] | None = None,
    dims: tuple[float | None, float | None, float | None] = (None, None, None),
    material: str | None = None,
    directory: Path | None = None,
    lang: str = "zh",
) -> Result:
    """可编辑项（lang=en：参数说法用英文）：这个结构能改什么（只列改了刀版真会变的）、改了动到哪些面和线、长宽高变了哪些自动值跟着变，
    以及面怎么连、各种线、锁合机构（engine/editable.py）。改用 structures update。"""
    from pacxio.engine.editable import describe

    r = Result(command="structures editable", job=str(directory) if directory else None)
    if (bad := _lang_issue(lang)) is not None:
        return r.fail(bad, exit_code=2)
    cf: CornerFinish | None = None
    if directory is not None:
        job = _load_job(directory, r)
        if job is None:
            return r
        style_id, params, mat_id = job.style, dict(job.params), job.material.preset or "sbs_350"
        cf = job.corner_finish
    else:
        found = structure_params(fefco_code, ecma_code, recipe, *dims)
        if isinstance(found, Issue):
            return r.fail(found, exit_code=2)
        style_id, params = found
        from pacxio.engine.catalog import default_material

        mat_id = material or (default_material(fefco_code or ecma_code or "") if (fefco_code or ecma_code)
                              else "sbs_350")  # fmt: skip
    try:
        mat = resolve_material(mat_id)
    except KeyError:
        return r.fail(Issue(id="S03", message=t("Unknown material preset {material}", material=mat_id), fix=t("Available presets: ") + ", ".join(load_materials())),
                      exit_code=2)  # fmt: skip
    data = describe(params, mat, load_profile("default"), style_id)
    if "error" in data:
        return r.fail(Issue(id="S03", message=t("This recipe cannot be built: {error}", error=data["error"]), fix=t("Change it until it builds")),
                      exit_code=1)  # fmt: skip
    from pacxio.engine.rounding import CARD_INNER_MM, CARD_OUTER_MM, finish_radii

    data["corners"] = {
        "set": cf.model_dump() if cf else None,
        "default": list(finish_radii(mat.type)),
        "round": [CARD_OUTER_MM, CARD_INNER_MM],  # 页面“圆角”一键用的大小（同卡纸默认）
    }
    if lang == "en":
        from pacxio.engine.i18n import editable_en

        data = editable_en(data)
    r.data = {"recipe": params, "material": mat_id, **data,
              "basics": dims_basics(style_id, params) | {"materials": material_choices(mat, style_id, params, language=lang)}}  # fmt: skip
    return r


TRY_PNG_PX = 520  # 工作台预览：按刀版折好的 3D 静态图边长（没有折法时的退路）
LINE_RGB = {
    "cut": "#111111",
    "perf": "#1e5ac8",
}  # 其余（压线类）红色 #d20000，同 engine.preview.dieline_png


def dieline_svg_px(dl: Dieline, scale: float, ph: float, pw: float) -> str:
    """改结构页的矢量刀版：坐标同线图像素（viewBox 0 0 pw ph），线宽不随缩放变（non-scaling-stroke），
    放大看也是细线。只有数字和固定颜色，没有任何外来文字。"""
    from pacxio.geometry import Path as GPath

    paths = []
    for e in dl.edges:
        pts = GPath(e.segments, closed=False).to_points(max_sagitta=0.05)
        if len(pts) < 2:
            continue
        d = "M" + " L".join(f"{12 + q.x * scale:.2f},{ph - 12 - q.y * scale:.2f}" for q in pts)
        color = LINE_RGB.get(e.type.value, "#d20000")
        paths.append(f'<path d="{d}" stroke="{color}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw} {ph}" preserveAspectRatio="xMidYMid meet">'
            f'<g fill="none" stroke-width="1.1" stroke-linejoin="round" stroke-linecap="round" '
            f'vector-effect="non-scaling-stroke">{"".join(paths)}</g></svg>')  # fmt: skip


TRY_DIELINE_PX = 1200  # 改结构页刀版坐标系（矢量，长边 1200 个单位）


BOX_MATERIALS = (
    "folding_carton",
    "corrugated",
)  # 盒子之间能互换的材料类型（卡纸 ↔ 瓦楞，结构不分材料，§20）


# 纸厚和盒子尺寸配不配：最短的边至少 15·纸厚，最多要求到 50（同开槽箱拼装器的下限，engine/slotted.py）【初值】
THICK_MIN_DIM_T, THICK_MIN_DIM_CAP = 15.0, 50.0


def thickness_need(caliper_mm: float) -> float:
    """这么厚的纸，盒子最短的边至少要多长（mm）。"""
    return min(THICK_MIN_DIM_CAP, THICK_MIN_DIM_T * caliper_mm)


def thickness_problem(style_id: str, params: dict[str, Any], material: Material) -> Issue | None:
    """纸太厚、盒子太小折不起来（BC 双楞做 40 高的小盒之类）：返回说明；没问题返回 None。只管盒子。"""
    if material.type not in BOX_MATERIALS:
        return None
    from pacxio.engine.editable import _get, dims_paths

    dims = [
        v
        for v in (_get(params, p) for p in dims_paths(style_id, params).values())
        if isinstance(v, int | float)
    ]
    if not dims:
        return None
    need = thickness_need(material.caliper_mm)
    if min(dims) >= need:
        return None
    return Issue(id="S03", message=t("Board too thick ({caliper:g} mm): the shortest edge, {edge:g} mm, cannot be folded; board this thick needs at least {need:g} mm", caliper=material.caliper_mm, edge=min(dims), need=need), fix=t("Use a thinner material, or make the box bigger"))  # fmt: skip


def material_choices(
    current: Material, style_id: str = "", params: dict[str, Any] | None = None, *, language: str | None = None
) -> list[dict[str, Any]]:
    """这个 job 能换成的材料预设：盒子在卡纸、瓦楞里选；标签、软袋只在同类里选。
    给了参数时每项标能不能用（纸太厚、盒子太小折不起来的标 ok = False 和原因）。"""
    kinds = BOX_MATERIALS if current.type in BOX_MATERIALS else (current.type,)
    out = []
    for k, v in load_materials().items():
        if v.type not in kinds:
            continue
        item: dict[str, Any] = {"id": k, "name": display_name(v.name, language), "caliper_mm": v.caliper_mm, "type": v.type,
                                "min_dim": round(thickness_need(v.caliper_mm), 1) if v.type in BOX_MATERIALS else 0.0}  # fmt: skip
        if params is not None:
            bad = thickness_problem(style_id, params, resolve_material(k))
            item |= {"ok": bad is None, "why": bad.message if bad else ""}
        out.append(item)
    return out


def _swap_material(job: Job, material: str | None) -> Job | Issue:
    if not material or material == job.material.preset:
        return job
    allowed = {m["id"] for m in material_choices(job.material)}
    if material not in allowed:
        return Issue(
            id="ARG",
            message=t("This job cannot switch to {material}", material=material),
            fix=t("Options: ") + ", ".join(sorted(allowed)),
        )
    return job.model_copy(update={"material": resolve_material(material)})


def dims_basics(style_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """改结构页“基础”里的长宽高、尺寸类型：在参数里的路径和现在的值。"""
    from pacxio.engine.editable import _get, dims_paths, set_master

    paths = dims_paths(style_id, params)
    master = set_master(params) if style_id == "structure_set" else None
    basis_path = f"parts.{master}.dim_basis" if master else "dim_basis"  # 引擎多件套：说了算的那件
    return {"paths": paths, "values": {k: _get(params, v) for k, v in paths.items()}, "master": master,
            "basis_path": basis_path, "basis": _get(params, basis_path) or "inner"}  # fmt: skip


def dieline_sizes(dl: Dieline) -> dict[str, Any]:
    """刀版旁边写的尺寸：内尺寸、压线尺寸（压线到压线，盒子能找到时）、料片大小。"""
    d = dl.style_meta.get("dims", {})
    out: dict[str, Any] = {"sheet": [round(dl.bbox[2], 1), round(dl.bbox[3], 1)]}
    if all(k in d for k in ("Li", "Wi", "Hi")):
        out["inner"] = [round(float(d[k]), 1) for k in ("Li", "Wi", "Hi")]
    crease = [d.get("panel_back_front", d.get("base_l")), d.get("panel_side", d.get("base_w")),
              d.get("body_height", d.get("wall_h"))]  # fmt: skip
    if all(isinstance(v, int | float) for v in crease):
        out["crease"] = [round(float(v), 1) for v in crease]
        return out
    ids = {q.id: q for q in dl.panels}  # 没记压线尺寸的（手写托盘类）：底板的长宽 + 前墙的高
    base = ids.get("bottom") or ids.get("base")
    wall = ids.get("front") or ids.get("back")
    if base is not None and wall is not None:
        x0, y0, x1, y1 = base.polygon.bbox()
        _, wy0, _, wy1 = wall.polygon.bbox()
        out["crease"] = [round(x1 - x0, 1), round(y1 - y0, 1), round(wy1 - wy0, 1)]
    return out


DIM_MATCH_MM = 0.6  # 面的宽 / 高和压线尺寸差这么多以内算“就是它”
DIM_INSET = 0.18  # 尺寸线画在面里面，离面边这么一成


def dimension_marks(
    dl: Dieline, scale: float, px: int, shift: float = 0.0, prefix: str = ""
) -> list[dict[str, Any]]:
    """刀版上标长宽高（压线尺寸）：找宽 / 高正好等于那个尺寸的一块面，在面里面画一条尺寸线。px：线图的高。
    返回线图像素坐标（同 dieline_png 的换算）。找不到对应的面就不标。"""
    crease = dieline_sizes(dl).get("crease")
    if not crease:
        return []
    boxes = []
    for q in dl.panels:
        x0, y0, x1, y1 = q.polygon.bbox()
        rank = 0 if q.role.value in ("body", "base", "bottom") else 1
        boxes.append((rank, -(x1 - x0) * (y1 - y0), q.id, x0, y0, x1, y1))
    boxes.sort()

    def p(x: float, y: float) -> list[float]:  # 多件套：这件在合成图里往右挪了 shift
        return [round(12 + (x + shift) * scale, 1), round(px - 12 - y * scale, 1)]

    out: list[dict[str, Any]] = []
    used: dict[
        str, set[str]
    ] = {}  # 面 → 已经标过的方向（同一块面可以横竖各标一个，如托盘底板的长和宽）
    for value in crease:
        hit = None
        order = sorted(boxes, key=lambda b: (b[2] not in used, b))  # 先用标过的面的另一个方向
        for _, _, pid, x0, y0, x1, y1 in order:
            axes = used.get(pid, set())
            if "x" not in axes and abs((x1 - x0) - value) <= DIM_MATCH_MM:
                y = y0 + DIM_INSET * (y1 - y0)
                hit = (pid, "x", p(x0, y), p(x1, y))
                break
            if "y" not in axes and abs((y1 - y0) - value) <= DIM_MATCH_MM:
                vertical = sum(
                    1 for m in out if m["from"][0] == m["to"][0]
                )  # 竖着的尺寸线左右错开，不挤在一起
                x = x1 - DIM_INSET * (x1 - x0) if vertical % 2 == 0 else x0 + DIM_INSET * (x1 - x0)
                hit = (pid, "y", p(x, y0), p(x, y1))
                break
        if hit:
            used.setdefault(hit[0], set()).add(hit[1])
            out.append(
                {"from": hit[2], "to": hit[3], "label": f"{value:g} mm", "panel": prefix + hit[0]}
            )
    return out


def _with_corners(job: Job, corners: dict[str, Any] | None) -> Job | Issue:
    """改刀模圆角：corners 里给了的键覆盖（值 None = 回到按材料默认），没给的不动。"""
    if not corners:
        return job

    base: dict[str, Any] = job.corner_finish.model_dump() if job.corner_finish else {}
    merged: dict[str, Any] = dict(base)
    merged.update({k: corners[k] for k in ("outer", "inner") if k in corners})
    try:
        cf = CornerFinish.model_validate(merged)
    except ValidationError as e:
        return Issue(
            id="ARG",
            message=t("Corner radius not valid: {msg}", msg=validation_message(e.errors()[0])),
            fix=t("Outer corners 0–20 mm, inner corners 0–5 mm, 0 = none"),
        )
    return job.model_copy(
        update={"corner_finish": cf if (cf.outer is not None or cf.inner is not None) else None}
    )


def structure_try(
    directory: Path,
    patch: dict[str, Any],
    material: str | None = None,
    corners: dict[str, Any] | None = None,
) -> Result:
    """试改结构参数（可连材料一起换），不写任何文件：合并后的参数 → 刀版线图 / 折好的 3D 预览（PNG，base64）、
    刀版 SVG、尺寸说明、每块面在线图上的位置（页面高亮用）、问题。给工作台的改结构页用；真正保存用 structure_update。"""

    r = Result(command="structures try", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    return _try_job(r, job, patch, material, corners)


def _try_job(
    r: Result,
    job: Job,
    patch: dict[str, Any],
    material: str | None = None,
    corners: dict[str, Any] | None = None,
) -> Result:
    """structure_try / structure_draft 共用：job（可以只在内存里）+ 改动 → 刀版、3D 场景、尺寸，不写文件。"""
    import base64
    import io

    from pacxio.engine.preview import folded_png
    from pacxio.export.svg import dieline_to_svg

    swapped = _swap_material(job, material)
    if isinstance(swapped, Issue):
        return r.fail(swapped, exit_code=2)
    cornered = _with_corners(swapped, corners)
    if isinstance(cornered, Issue):
        return r.fail(cornered, exit_code=2)
    job = cornered
    params = _deep_merge(job.params, patch)
    thick = thickness_problem(job.style, params, job.material)
    if thick is not None:
        return r.fail(thick, exit_code=1)
    style = get_style(job.style)
    try:
        p = style.param_schema().model_validate(params)
    except ValidationError as e:
        return _fail(r, parameter_issues(e), 2)
    try:
        profile = load_profile(job.profile)
    except ProfileNotFoundError as e:
        return r.fail(Issue(id="ENV", message=str(e), fix=t("Check the --profile name")), 2)
    issues = style.validate(p, job.material, profile)
    if issues:
        return _fail(r, issues, 1)
    from pacxio.dielines import style_part_labels, style_parts
    from pacxio.engine.editable import combine, set_master

    part_ids = style_parts(style, p)
    labels = style_part_labels(style, p)
    try:
        if part_ids:  # 多件套：每件一张，并排合成一张给页面看
            dls: dict[str, Dieline] = style.generate_parts(p, job.material, profile)  # type: ignore[attr-defined]
        else:
            dls = {"": style.generate(p, job.material, profile)}
    except (ValueError, KeyError) as e:
        return r.fail(
            Issue(
                id="S03",
                message=t("These parameters cannot be built: {error}", error=e),
                fix=t("Go back to the last values that worked"),
            ),
            exit_code=1,
        )
    from pacxio.engine.rounding import apply_finish, finish_radii

    outer_r, inner_r = finish_radii(job.material.type, job.corner_finish)
    rounded = {"outer_corners": 0, "inner_corners": 0}
    for d in dls.values():  # 刀模圆角（同出刀版最后一步）
        rec = apply_finish(d, job.material.type, job.corner_finish)
        for k in rounded:
            rounded[k] += int(rec[k])
    structural = [i for d in dls.values() for i in check_s01(d) + check_crease_sides(d)]
    if structural:
        return _fail(r, structural, 1)
    if job.style in (
        "structure",
        "structure_set",
    ):  # 机构功能核对（S07）：几何引擎结构在印前检查里查，这里先提示
        from pacxio.engine import function_check

        for name, d in dls.items():
            found = function_check.issues(d, function_check.part_recipe(p, name or None))
            tag = t("{part}: ", part=labels.get(name, name) if lang_now() == "zh" else name) if name else ""
            r.warnings += [i.model_copy(update={"message": tag + i.message}) for i in found]
    dl = dls[""] if "" in dls else combine(dls)
    master = (set_master(params) if job.style == "structure_set" else None) or next(iter(dls))
    shift = 0.0  # 尺寸线标在说了算的那件上：它在合成图里往右挪了多少
    if part_ids:
        from pacxio.dielines import PART_GAP_MM

        for name, d in dls.items():
            if name == master:
                break
            shift += d.bbox[2] + PART_GAP_MM

    def b64(img: Any) -> str | None:
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    px = TRY_DIELINE_PX
    scale = (px - 24) / max(
        dl.bbox[2], dl.bbox[3]
    )  # 同 engine.preview.dieline_png(fit=True) 的换算
    pw, ph = max(24, round(dl.bbox[2] * scale) + 24), max(24, round(dl.bbox[3] * scale) + 24)

    def to_px(path: Any) -> list[list[float]]:
        return [
            [round(12 + q.x * scale, 1), round(ph - 12 - q.y * scale, 1)]
            for q in path.to_points(max_sagitta=0.5)
        ]

    from pacxio.engine.preview import preview_scene, preview_scene_parts

    sizes = dieline_sizes(dls[master]) | {"sheet": [round(dl.bbox[2], 1), round(dl.bbox[3], 1)]}
    if part_ids:
        fits, counts = params.get("fits", {}), params.get("counts", {})

        def count(n: str) -> int:  # 这张刀版要做几片（套盖的盖帽 ×2、隔板组……）
            return int(counts.get(n) or fits.get(n, {}).get("count") or 1)

        sizes["parts"] = [{"part": n, "name": labels.get(n, n), "count": count(n),
                           "sheet": [round(d.bbox[2], 1), round(d.bbox[3], 1)]} for n, d in dls.items()]  # fmt: skip
        sizes["master"] = labels.get(master, master)
    set_info = (
        {"set": params.get("set"), "fits": params.get("fits", {})}
        if job.style == "structure_set"
        else None
    )
    scene = preview_scene_parts(job.style, dls, set_info) if part_ids else preview_scene(dl)
    folded = None
    if scene is None:  # 没有折法的才要静态图（退路）
        try:
            folded = b64(folded_png(list(dls.values()), px=TRY_PNG_PX))
        except (KeyError, ValueError):
            folded = None
    r.data = {"params": params, "material": job.material.preset, "svg": dieline_to_svg(dl, labels=False),
              "scene": scene,
              "dims_px": dimension_marks(dls[master], scale, ph, shift, f"{master}." if part_ids else ""),
              "dieline_svg": dieline_svg_px(dl, scale, ph, pw), "size_px": [pw, ph],
              "folded_png": folded, "px": px,
              "panels_px": {q.id: to_px(q.polygon) for q in dl.panels}, "sizes": sizes,
              "corners": {"outer": outer_r, "inner": inner_r, **rounded,
                          "set": job.corner_finish.model_dump() if job.corner_finish else None,
                          "default": list(finish_radii(job.material.type))},
              "bbox": list(dl.bbox), "panels": len(dl.panels)}  # fmt: skip
    return r


DIELINE_FORMATS = (
    "dxf",
    "cf2",
    "pdf",
    "svg",
)  # 不建 job 也能出的刀版文件（开源部分；网站刀版生成器用）


def draft_job(
    code: str | None = None,
    L: float | None = None,
    W: float | None = None,
    H: float | None = None,
    material: str | None = None,
    basis: str = "inner",
    recipe: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    corners: dict[str, Any] | None = None,
) -> Job | Issue:
    """只在内存里的 job（不写文件）：结构编号（FEFCO / ECMA / 常用组合）或配方 + 尺寸 + 材料，和 `job init`
    建出来的同一个 job（同一套盒型、参数、刀模圆角）。patch：改结构参数（同 structures try）。"""
    from pacxio.engine import catalog

    entry = catalog.get(code) if code else None
    is_ecma = entry is not None and entry.catalog == "ECMA"
    found = structure_params(None if (code is None or is_ecma) else code, code if is_ecma else None,
                             recipe, L, W, H, basis)  # fmt: skip
    if isinstance(found, Issue):
        return found
    style, params = found
    if patch:
        params = _deep_merge(params, patch)
    mat_id = material or (catalog.default_material(code) if code else "sbs_350")
    try:
        mat = resolve_material(mat_id)
    except KeyError:
        return Issue(
            id="S03",
            message=t("Unknown material preset {material}", material=mat_id),
            fix=t("Available presets: ") + ", ".join(load_materials()),
        )
    method = getattr(schema_style(style), "default_print_method", "offset")
    job = Job(job_id=(code or "draft").replace(".", "_"), style=style, params=params, material=mat,
              print=PrintSpec(method=method))  # fmt: skip
    cornered = _with_corners(job, corners)
    return cornered


def structure_draft(
    code: str | None = None,
    L: float | None = None,
    W: float | None = None,
    H: float | None = None,
    material: str | None = None,
    basis: str = "inner",
    recipe: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    corners: dict[str, Any] | None = None,
) -> Result:
    """不建 job 的“改结构”：结构 + 尺寸 + 材料（+ 改动）→ 刀版 SVG、可转动的 3D 场景、尺寸说明、问题。
    和工作台改结构页（structure_try）同一个函数，只是 job 在内存里。网站刀版生成器用。"""
    r = Result(command="structures draft")
    job = draft_job(code, L, W, H, material, basis, recipe, patch, corners)
    if isinstance(job, Issue):
        return r.fail(job, exit_code=2)
    r = _try_job(r, job, {})
    r.data["style"] = job.style
    return r


def dieline_files(
    job: Job, formats: list[str] | None = None
) -> tuple[dict[str, bytes], list[Issue]]:
    """job（可以只在内存里）→ 刀版文件 {文件名: 内容}。和交付包里的刀版同一套函数（刀模圆角、开窗都算上），
    结构检查（S01、压线两侧）有问题时不出。多件套每件一份，文件名前加部件名。"""
    import tempfile

    from pacxio.export.cf2 import write_cf2
    from pacxio.export.dxf import dieline_to_dxf
    from pacxio.export.pdf import write_dieline_pdf

    wanted = list(formats or DIELINE_FORMATS)
    bad = [f for f in wanted if f not in DIELINE_FORMATS]
    if bad:
        return {}, [Issue(id="ARG", message=t("Unknown format {formats}", formats=", ".join(bad)), fix=t("Available: ") + ", ".join(DIELINE_FORMATS))]  # fmt: skip
    dset, issues = dielines.build_parts_for(job)
    if dset is None:
        return {}, issues
    structural = [i for d in dset.parts.values() for i in check_s01(d) + check_crease_sides(d)]
    if structural:
        return {}, structural
    profile = dielines.job_profile(job)
    out: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for part, dl in dset.parts.items():
            stem = f"{job.job_id}_{part}" if part else job.job_id
            for fmt in wanted:
                path = Path(tmp) / f"{stem}_dieline.{fmt}"
                if fmt == "dxf":
                    dieline_to_dxf(dl, profile, path)
                elif fmt == "cf2":
                    write_cf2(dl, profile, path)
                elif fmt == "pdf":
                    write_dieline_pdf(dl, profile, path)
                else:
                    path.write_text(dieline_to_svg(dl), encoding="utf-8")
                out[path.name] = path.read_bytes()
    return out, issues


def structure_files(
    code: str | None,
    L: float | None,
    W: float | None,
    H: float | None,
    out: Path,
    material: str | None = None,
    formats: list[str] | None = None,
    basis: str = "inner",
    recipe: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
) -> Result:
    """不建 job，直接出刀版文件（DXF / CF2 / PDF / SVG）到 out 目录。"""
    r = Result(command="structures files")
    job = draft_job(code, L, W, H, material, basis, recipe, patch)
    if isinstance(job, Issue):
        return r.fail(job, exit_code=2)
    files, issues = dieline_files(job, formats)
    if not files:
        return _fail(r, issues, 2 if any(i.id in ("ARG", "S03") for i in issues) else 1)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
        r.artifacts.append(str(out / name))
    r.warnings += issues  # 刀版建成了时带回来的是提醒（开窗离边近之类）
    r.data = {"style": job.style, "material": job.material.preset, "files": sorted(files)}
    return r


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def structure_update(
    directory: Path,
    patch: dict[str, Any],
    material: str | None = None,
    corners: dict[str, Any] | None = None,
) -> Result:
    """改 job 的结构参数（几何引擎配方或手写盒型的参数，按层合并：给 dict 往里并，给值替换，给 null 删掉这项），
    重新生成刀版。结构变了已有的确认会因指纹不同而过期，放置引用的面名对不上时 preflight 会报。
    能改哪些看 structures editable。"""
    r = Result(command="structures update", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    swapped = _swap_material(job, material)
    if isinstance(swapped, Issue):
        return r.fail(swapped, exit_code=2)
    cornered = _with_corners(swapped, corners)
    if isinstance(cornered, Issue):
        return r.fail(cornered, exit_code=2)
    job = cornered
    changed = job.model_copy(update={"params": _deep_merge(job.params, patch)})
    thick = thickness_problem(job.style, changed.params, job.material)
    if thick is not None and (
        patch or material
    ):  # 只拦这次改出来的（老 job 本来就这样的不在这里拦）
        return r.fail(thick, exit_code=1)
    try:
        get_style(job.style).param_schema().model_validate(changed.params)
    except ValidationError as e:
        return _fail(r, parameter_issues(e), 2)
    dset, issues = dielines.build_parts_for(changed)
    if dset is None:
        return _fail(r, issues, 1)
    structural = [
        i
        for part_dl in dset.parts.values()
        for i in check_s01(part_dl) + check_crease_sides(part_dl)
    ]
    if structural:
        return _fail(r, structural, 1)
    path = changed.save(directory)
    svg = directory / DIELINE_SVG
    svg.write_text(dieline_to_svg(dset.merged()), encoding="utf-8")
    r.artifacts = [str(path), str(svg)]
    st = get_style(job.style)
    r.warnings += style_advisories(st, st.param_schema().model_validate(changed.params), job.material,
                                   load_profile(job.profile))  # fmt: skip
    r.data = {"panels": [q.id for q in dset.merged().panels], "params": changed.params}
    return r


def build_dieline(job: Job) -> tuple[Dieline | None, list[Issue]]:
    """job → Dieline。参数或盒型约束有误时返回 (None, issues)。"""
    return dielines.build_dieline_for(job)


def schema_style(style: str) -> object | None:
    """盒型对象（未知盒型返回 None，后面 build_dieline 报错）。"""
    try:
        return get_style(style)
    except KeyError:
        return None


def init_job(
    directory: Path,
    style: str,
    params: dict[str, Any],
    material: str,
    profile: str = "default",
    print_method: str | None = None,
    liner: str | None = None,
) -> Result:
    """创建 job 并生成刀版：写 job.json 与 dieline.svg。print_method 为空时按盒型默认（开槽箱水印）；
    liner：瓦楞的面纸 kraft / white（不给 = 白）。"""
    r = Result(command="job init", job=str(directory))
    try:
        mat = resolve_material(material)
    except KeyError:
        return r.fail(
            Issue(
                id="S03",
                message=t("Unknown material preset {material}", material=material),
                fix=t("Available presets: ") + ", ".join(load_materials()),
            ),
            exit_code=2,
        )
    if liner is not None:
        issue = _liner_issue(mat, liner)
        if issue is not None:
            return r.fail(issue, exit_code=2)
        mat.liner = liner  # type: ignore[assignment]
    try:
        schema = get_style(style).param_schema()
    except KeyError:
        schema = None  # 未知盒型：下面 build_dieline 报错
    known = schema.model_fields if schema is not None else None
    unknown = sorted(set(params) - set(known)) if known is not None else []
    if schema is not None and not unknown:
        try:  # --set 传进来的是字符串：存 job.json 前换成参数表里的类型（部件参数只存给了的那几项）
            checked = schema.model_validate(params)
            params = {k: (v.model_dump(exclude_unset=True) if isinstance(v := getattr(checked, k), BaseModel)
                          else checked.model_dump(include={k})[k]) for k in params}  # fmt: skip
        except ValidationError:
            pass  # 下面 build_dieline 报出具体哪个参数不对
    if unknown:
        return r.fail(
            Issue(
                id="S03",
                message=t(
                    "Box style {style} has no parameter {names}",
                    style=style,
                    names=", ".join(unknown),
                ),
                fix=t("Available parameters: ")
                + ", ".join(known or {})
                + t(" (`pacxio styles list` shows the defaults)"),
            ),
            exit_code=2,
        )
    if print_method is None:
        print_method = getattr(schema_style(style), "default_print_method", "offset")
    if print_method not in ("offset", "flexo", "digital", "gravure"):
        return r.fail(
            Issue(
                id="ARG",
                message=t("Unknown print method {method}", method=print_method),
                fix=t("Use --print-method offset, flexo, digital or gravure (flexible packaging)"),
            ),
            exit_code=2,
        )
    job = Job(job_id=directory.name, style=style, params=params, material=mat, profile=profile,
              print=PrintSpec(method=print_method))  # fmt: skip
    dset, issues = dielines.build_parts_for(job)
    if dset is None:
        r.errors += issues
        r.ok = False
        r.exit_code = 2 if schema is None else 1
        return r

    structural = []
    for part, part_dl in dset.parts.items():  # 每个部件各查各的（外轮廓、压线两侧）
        label = dset.label(part) if lang_now() == "zh" else part
        for i in check_s01(part_dl) + check_crease_sides(part_dl):
            structural.append(
                i.model_copy(
                    update={"message": t("{label}: {message}", label=label, message=i.message)}
                )
                if label
                else i
            )
    dl = dset.merged()
    if structural:
        r.errors += structural
        r.ok = False
        r.exit_code = 1
        return r

    job_path = job.save(directory)
    svg_path = directory / DIELINE_SVG
    svg_path.write_text(dieline_to_svg(dl), encoding="utf-8")
    r.artifacts = [str(job_path), str(svg_path)]
    st = get_style(style)
    r.warnings += style_advisories(
        st, st.param_schema().model_validate(job.params), mat, load_profile(profile)
    )
    r.data = {
        "bbox_mm": {"w": round(dl.bbox[2], 3), "h": round(dl.bbox[3], 3)},
        "panels": [p.id for p in dl.panels],
        **(
            {
                "parts": {
                    part: {
                        "label": dset.labels.get(part or "") if lang_now() == "zh" else part,
                        **(
                            {"quantity": pd.style_meta["quantity"]}
                            if "quantity" in pd.style_meta
                            else {}
                        ),
                        "bbox_mm": {"w": round(pd.bbox[2], 3), "h": round(pd.bbox[3], 3)},
                    }
                    for part, pd in dset.parts.items()
                }
            }
            if dset.multipart
            else {}
        ),
        "profile_verified": load_profile(profile).verified,
        "orientation_verified": dl.style_meta["orientation_verified"],
        "compensation_applied": dl.style_meta["compensation_applied"],
    }
    if not r.data["profile_verified"]:
        r.warnings.append(
            Issue(
                id="PROFILE",
                message=t("Profile not confirmed by the factory"),
                fix=t("Regenerate with a factory-confirmed profile before delivery"),
            )
        )
    # 平面件（卡片、标签）本来就没有厚度补偿
    if not dl.style_meta["compensation_applied"] and dl.style_meta.get("layout") not in (
        "flat",
        "label",
        "pouch",
    ):
        r.warnings.append(
            Issue(
                id="COMP",
                message=t(
                    "Thickness compensation not applied (apply_compensation=false): panel sizes equal the inside size; not for proofing as is"
                ),
                fix=t("Remove apply_compensation=false and regenerate"),
            )
        )
    return r


LINERS = {"kraft": t("kraft liner"), "white": t("white liner")}


def _liner_issue(mat: Material, liner: str) -> Issue | None:
    """面纸只对瓦楞有意义（卡纸本身就是白卡 / 牛皮卡，选材料预设）。"""
    if liner not in LINERS:
        return Issue(
            id="ARG",
            message=t("Unknown liner {liner}", liner=liner),
            fix=t("Use kraft (kraft liner) or white (white liner)"),
        )
    if mat.type != "corrugated":
        return Issue(id="ARG", message=t("Liners only apply to corrugated materials"),
                     fix=t("For a kraft-colored carton choose the material preset kraft_350; remove --liner"))  # fmt: skip
    return None


def set_liner(directory: Path, liner: str) -> Result:
    """改瓦楞 job 的面纸（开槽箱测试 R-01）：刀版确认图、3D 按纸色显示，规格书写明；改了刀版确认要重新确认。"""
    r = Result(command="job liner", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    issue = _liner_issue(job.material, liner)
    if issue is not None:
        return _fail(r, [issue], 2)
    before = job.material.liner
    job.material.liner = liner  # type: ignore[assignment]
    r.artifacts = [str(job.save(directory))]
    r.data = {"liner": liner, "before": before}
    if before != liner:
        r.warnings.append(Issue(id="LINER", message=t("Liner changed to {liner}: render the dieline confirmation image and 3D again", liner=LINERS[liner]),
                                fix=t("`pacxio render {job}`, `pacxio mockup {job}`; a confirmed dieline confirmation image expires, ask the user to confirm again", job=directory)))  # fmt: skip
    return r


def _load_job(directory: Path, r: Result) -> Job | None:
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok = False
        r.exit_code = 2
    return job


def _fail(r: Result, issues: list[Issue], exit_code: int = 1) -> Result:
    r.errors += issues
    r.ok = False
    r.exit_code = max(r.exit_code, exit_code)
    return r


def _report_into(r: Result, report: Report) -> None:
    """把检查报告里的问题并入统一的 Result。"""
    for f in report.errors:
        r.errors.append(Issue(id=f.id, message=f.message, fix=f.fix))
    for f in report.warnings:
        r.warnings.append(Issue(id=f.id, message=f.message, fix=f.fix))
    if report.errors:
        r.ok = False
        r.exit_code = max(r.exit_code, 1)
    for rule, why in report.skipped.items():
        r.warnings.append(
            Issue(
                id="SKIPPED",
                message=t("Check {rule} not run: {why}", rule=rule, why=why),
                fix=t("Check again once the conditions are met"),
            )
        )
    r.data["report"] = {
        "passed": report.passed,
        "errors": len(report.errors),
        "warnings": len(report.warnings),
        "executed": report.executed,
        "skipped": report.skipped,
    }


def render_job(directory: Path) -> Result:
    """出刀版确认图（preview.png：展开图 + 刀线 + 面名与尺寸 + 工艺标注）与刀版 SVG，不跑印前检查。

    工作台 ⑤ 显示它，用户在它上面确认（approve），这是导出前唯一的闸门。"""
    from pacxio_pro import pipeline  # 闭源（）：开源版这条命令转给云端

    r = Result(command="render", job=str(directory))
    with use_language(dielines.job_output_language(directory)):  # 排版时的提示也进确认图 / 报告
        parts, dset, errs = pipeline.prepare_parts(directory, dpi=RENDER_DPI)
    if errs or dset is None:
        return _fail(r, errs, 2 if any(i.id == "JOB" for i in errs) else 1)
    whole, _ = dielines.load_job(directory)  # 多部件时 parts[].job 是部件视图，指纹要按整个 job 算
    fingerprint = design_fingerprint(directory, whole) if whole is not None else None
    with use_language(doc_language(whole or parts[0].job)):  # 确认图是交付件：语言按配置档
        _render_parts(directory, parts, dset, fingerprint, whole, r)
    svg = directory / DIELINE_SVG
    svg.write_text(dieline_to_svg(dset.merged()), encoding="utf-8")
    r.artifacts.append(str(svg))
    return _with_notes(r, directory)


def doc_language(job: Job) -> str:
    """交付件（确认图、报告、规格书、效果图说明）的语言：工厂配置档的 document_language。"""
    return output_language(job.profile)


def _render_parts(
    directory: Path, parts: list[Any], dset: dielines.DielineSet, fingerprint: str | None,
    whole: Job | None, r: Result,
) -> None:  # fmt: skip
    from pacxio_pro.export.confirm import render_confirm
    from pacxio_pro.export.preview import preview_map

    for p in parts:  # 多部件盒型每个部件一张：preview_<部件>.png
        png = directory / (f"preview_{p.part}.png" if p.part else PREVIEW_PNG)
        name = dielines.style_name(p.dl.style_meta, p.job.style)
        title = (f"{p.job.job_id} · " + t("Dieline confirmation · {name} ({style})", name=name, style=p.job.style.upper())
                 + (f" · {dset.shown(p.part)}" if p.part else ""))  # fmt: skip
        tags = {f.id: f.tag for f in p.job.finishes if f.tag}
        ci = render_confirm(p.layout, p.dl, title, tags, dpi=RENDER_DPI)
        ci.image.save(png)
        base = png.with_name(png.stem + ".base.png")
        render_confirm(p.layout, p.dl, title, tags, dpi=RENDER_DPI, annotations=False).image.save(
            base
        )
        # 不画刀线的一份：折线上的图被压线挡住，确认时看不出断开、错位（工作台⑤"刀线"按钮切换）
        art = png.with_name(png.stem + ".art.png")
        render_confirm(p.layout, p.dl, title, tags, dpi=RENDER_DPI, annotations=False,
                       dieline=False).image.save(art)  # fmt: skip
        # 旁边一份像素 ↔ 毫米对照（工作台用）；记下指纹，设计改了就知道这张不是最新、确认作废
        sidecar = png.with_suffix(PREVIEW_MAP_SUFFIX)
        frame = (ci.x0_mm, ci.y1_mm, ci.image.size)
        info = {
            **preview_map(p.layout, p.dl, RENDER_DPI, p.job, p.part, frame),
            "base_file": base.name,
            "art_file": art.name,
            "fingerprint": fingerprint,
            "style_version": style_version(whole) if whole is not None else None,
        }
        sidecar.write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        r.artifacts += [str(png), str(base), str(art), str(sidecar)]
        r.warnings += p.layout.canvas.notes


def _with_notes(r: Result, directory: Path) -> Result:
    """工作台上有等 agent 处理的批注：输出里多一条提醒（不是 error，不挡流程）。"""
    issue = open_notes_issue(directory)
    if issue is not None:
        r.warnings.append(issue)
    return r


def _next_id(existing: list[str], prefix: str) -> str:
    n = 1
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


ART_ARG_FIX = t(
    "Check the parameters: kind=image needs --source --box (and --panel when space=panel), kind=vector the same (source is SVG), kind=text needs --panel --box --text --font --size-pt, kind=fill needs --color, kind=pattern needs --source --tile-mm"
)


def _check_art_files(directory: Path, placement: Placement, job: Job) -> list[Issue]:
    """SVG 素材 / 裁切形状在添加时就检查能否印刷；返回提醒（如颜色要自动换算）。"""
    from pacxio.visual.meta import is_illustration, read_meta
    from pacxio_pro.color import ColorConverter  # 闭源（）：`art` 转给云端
    from pacxio_pro.color.icc import resolve_icc
    from pacxio_pro.layout.compose import resolve_colors
    from pacxio_pro.layout.svgart import load_svg_art

    warnings: list[Issue] = []
    svgs = [placement.clip] if placement.clip else []
    source = job.source_of(placement)
    if placement.kind == "vector" and source:
        svgs.append(source)
    for rel in svgs:
        art = load_svg_art(directory / rel)  # 失败抛 SvgArtError
        if placement.kind == "vector" and rel == source:
            profile = load_profile(job.profile)
            icc = resolve_icc(profile, job, directory).path
            illustration = is_illustration(read_meta(directory / rel))
            _, note = resolve_colors(placement, [s.hex for s in art.shapes], ColorConverter(icc),
                                     illustration)  # fmt: skip
            if note is not None:
                warnings.append(note)
    return warnings


def _content_ref(job: Job, fields: dict[str, Any]) -> Issue | None:
    """--content：引用信息清单的条目。文字放置不再单独存文字（以清单为准）；给了 --text 必须一致。"""
    ref = fields.get("content")
    if ref is None:
        return None
    item = job.content.get(str(ref)) if job.content is not None else None
    if item is None:
        return Issue(
            id="CONTENT",
            message=t("No item {ref} in the content list", ref=ref),
            fix=t(
                "See item ids with `pacxio content list <job>`, or add it with `content add` first"
            ),
        )
    if fields.get("kind") == "text":
        given = fields.pop("text", None)
        if given is not None and given != item.text:
            return Issue(
                id="CONTENT",
                message=t("--text differs from the text of content item {ref}", ref=ref),
                fix=t(
                    "Remove --text (the list's text is used); to change the wording use `content edit` and ask the user to confirm again"
                ),
            )
    return None


def art_add(directory: Path, fields: dict[str, Any]) -> Result:
    """增加一个图文放置（规格 14：art add）。fields 是 Placement 的字段，外加可选的 before
    （插在这个放置之前，即叠在它下面；不给就放在最上面）。"""
    from pacxio.brief import label_language_issue  # 闭源部分（）：`art` 转给云端
    from pacxio_pro.layout.svgart import SvgArtError

    r = Result(command="art add", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    dl, issues = build_dieline(job)
    if dl is None:
        return _fail(r, issues)
    fields = {k: v for k, v in fields.items() if v is not None}
    before = fields.pop("before", None)
    ids = [x.id for x in job.placements]
    if before is not None and before not in ids:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("--before {before}: no such placement", before=before),
                    fix=t("Available: ") + ", ".join(ids),
                )
            ],
            2,
        )
    if "id" not in fields:
        fields["id"] = _next_id(ids, str(fields.get("kind", "art")))
    if fields["id"] in ids:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("Placement id {id} already exists", id=fields["id"]),
                    fix=t("Use another --id"),
                )
            ],
            2,
        )
    ref_issue = _content_ref(job, fields)
    if ref_issue is not None:
        return _fail(r, [ref_issue], 2)
    local = [f"--{k}" for k in ("panel", "box") if k in fields]
    if fields.get("kind") in ("fill", "pattern") and local:
        # 铺底色 / 平铺按 scope 铺满整个面，panel / box 不起作用：给了会以为是局部色块，其实盖住整面
        return _fail(r, [Issue(id="ARG", message=t("kind={kind} fills the whole panel by --scope; {opts} have no effect (it covers the whole panel, not a part)", kind=fields["kind"], opts=" / ".join(local)),
                               fix=t("To fill a whole panel use --scope panel_id; draw a partial color block as a rectangle SVG and place it with kind=vector in --box"))], 2)  # fmt: skip
    for key in ("source", "clip"):
        given = fields.get(key)
        if given is None:
            continue
        if _is_concept(directory, Path(str(given))):
            return _fail(r, [concept_guard_issue(t("layout placement"))], 2)
        rel = _import_asset(directory, Path(str(given)))
        if rel is None:
            return _fail(
                r,
                [
                    Issue(
                        id="ASSET",
                        message=t("Asset file not found: {path}", path=given),
                        fix=t("Check the path"),
                    )
                ],
                2,
            )
        fields[key] = rel
    try:
        placement = Placement.model_validate(fields)
    except ValidationError as e:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t(
                        "{loc}: {msg}",
                        loc=".".join(str(x) for x in err["loc"]) or "placement",
                        msg=err["msg"],
                    ),
                    fix=ART_ARG_FIX,
                )
                for err in e.errors()
            ],
            2,
        )
    if placement.part is not None and not dl.style_meta.get("parts"):
        return _fail(r, [Issue(id="ARG", message=t("Box style {style} has only one part; do not give --part", style=job.style),
                               fix=t("Remove --part; panel names have no prefix"))], 2)  # fmt: skip
    panel_ids = {x.id for x in dl.panels}
    wanted_panels = [placement.panel] if placement.panel else (placement.scope_panels() or [])
    bad = [x for x in wanted_panels if x not in panel_ids]
    if bad:
        return _fail(
            r,
            [
                Issue(
                    id="PANEL",
                    message=t("Unknown panel {panel}", panel=bad),
                    fix=t("Available panels: ") + ", ".join(sorted(panel_ids)),
                )
            ],
            2,
        )
    if placement.coord_space == "body":
        try:
            strip = body_strip(dl)
        except LayoutError as e:
            return _fail(r, [e.issue], 2)
        assert placement.box is not None
        if placement.box.w > strip.circumference + 0.05:
            return _fail(
                r,
                [
                    Issue(
                        id="SPACE",
                        message=t(
                            "box width {w:g}mm is more than the body circumference {c:.2f}mm",
                            w=placement.box.w,
                            c=strip.circumference,
                        ),
                        fix=t(
                            "See body wrap coordinates with `pacxio panels`; the box width must not exceed one circumference"
                        ),
                    )
                ],
                2,
            )
    probe = job.model_copy(update={"placements": [*job.placements, placement]})
    ref_problems = [i for i in probe.layout_issues() if placement.id in i.message]
    if ref_problems:
        return _fail(r, ref_problems, 2)
    if placement.kind == "text":
        font = placement.font or (job.styles or {})[str(placement.style)].font
        try:
            for font_name in parse_font_chain(font):
                find_font(font_name, directory)
        except TextError as e:
            return _fail(r, [e.issue], 2)
    try:
        r.warnings += _check_art_files(directory, placement, probe)
    except SvgArtError as e:
        return _fail(r, [e.issue], 2)
    index = ids.index(before) if before is not None else len(job.placements)
    job.placements.insert(index, placement)
    r.artifacts = [str(job.save(directory))]
    r.data = {
        "id": placement.id,
        "kind": placement.kind,
        "placements": len(job.placements),
        "order": [x.id for x in job.placements],
    }
    single = job.model_copy(update={"placements": [placement]})  # 只提醒这一个放置
    lang = label_language_issue(single, directory)
    if lang is not None:
        r.warnings.append(lang)
    return r


def art_move(directory: Path, art_id: str, before: str | None = None, top: bool = False) -> Result:
    """调整叠放次序：把放置移到另一个放置之前（叠在它下面），或移到最上面（--top）。"""
    r = Result(command="art move", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    ids = [x.id for x in job.placements]
    if art_id not in ids:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("No placement {id}", id=art_id),
                    fix=t("Available: ") + ", ".join(ids),
                )
            ],
            2,
        )
    if (before is None) == (not top):
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("Give one of --before <id> or --top"),
                    fix=t("e.g. --top moves it to the top layer"),
                )
            ],
            2,
        )
    item = job.placements.pop(ids.index(art_id))
    rest = [x.id for x in job.placements]
    if before is not None:
        if before not in rest:
            return _fail(
                r,
                [
                    Issue(
                        id="ARG",
                        message=t("--before {before}: no such placement", before=before),
                        fix=t("Available: ") + ", ".join(rest),
                    )
                ],
                2,
            )
        job.placements.insert(rest.index(before), item)
    else:
        job.placements.append(item)
    r.artifacts = [str(job.save(directory))]
    r.data = {"id": art_id, "order": [x.id for x in job.placements]}
    return r


def art_fill(directory: Path, placeholder_id: str, fields: dict[str, Any]) -> Result:
    """用真实图片 / 矢量图形换掉占位框：位置、坐标系、叠放次序不变（ 2.4）。

    fields：source（必填）、kind（不给时 .svg 为 vector，其它为 image）、clip（不给就沿用占位框的
    裁切形状）、fit、color、colors、content（不给就沿用占位框引用的条目）、id（新 id，不给就沿用占位框的 id）。
    """
    r = Result(command="art fill", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    ph = next((p for p in job.placements if p.id == placeholder_id), None)
    if ph is None or ph.kind != "placeholder":
        holders = [p.id for p in job.placements if p.kind == "placeholder"]
        return _fail(r, [Issue(id="ARG", message=t("{id} is not a placeholder", id=placeholder_id),
                               fix=t("Placeholders: ") + (", ".join(holders) or t("(none)")))], 2)  # fmt: skip
    source = fields.get("source")
    if not source:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("Give --source (an image or SVG)"),
                    fix=t("e.g. --source photo.png"),
                )
            ],
            2,
        )
    new_id = str(fields.get("id") or ph.id)
    if new_id != ph.id and any(p.id == new_id for p in job.placements):
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("Placement id {id} already exists", id=new_id),
                    fix=t("Use another --id"),
                )
            ],
            2,
        )
    temp = _next_id([p.id for p in job.placements], f"{ph.id}-fill")
    assert ph.box is not None
    add = {
        "id": temp, "kind": fields.get("kind") or ("vector" if str(source).lower().endswith(".svg") else "image"),
        "source": source, "clip": fields.get("clip") or ph.clip, "fit": fields.get("fit"), "color": fields.get("color"),
        "colors": fields.get("colors"), "content": fields.get("content") or ph.content, "tag": ph.tag, "label": ph.label,
        "label_en": ph.label_en, "space": ph.space, "panel": ph.panel, "box": ph.box.model_dump(), "before": ph.id,
    }  # fmt: skip
    added = art_add(directory, add)
    if not added.ok:
        added.command = "art fill"
        return added
    job = Job.load(directory)
    placements = [p for p in job.placements if p.id != ph.id]
    job.placements = [
        p.model_copy(update={"id": new_id}) if p.id == temp else p for p in placements
    ]
    r.artifacts = [str(job.save(directory))]
    r.warnings += added.warnings
    r.data = {
        "id": new_id,
        "replaced": ph.id,
        "label": ph.label,
        "order": [p.id for p in job.placements],
    }
    return r


def art_same_face(
    directory: Path, target: str, source: str | None = None, clear: bool = False
) -> Result:
    """正反同款：把 target 面设成和 source 面相同（只有一份数据，改 source 时 target 一起变）。"""
    r = Result(command="art same-face", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    dl, issues = build_dieline(job)
    if dl is None:
        return _fail(r, issues)
    same = dict(job.same_faces or {})
    if clear:
        same.pop(target, None)
    else:
        ids = {p.id: p for p in dl.panels}
        bad = [x for x in (target, source) if x not in ids]
        if source is None or bad:
            return _fail(r, [Issue(id="PANEL", message=t("Unknown panel {panel}", panel=bad or t("(no --as given)")),
                                   fix=t("Available panels: ") + ", ".join(ids))], 2)  # fmt: skip
        if target == source or same.get(source) is not None:
            return _fail(r, [Issue(id="ARG", message=t("{target} and {source} cannot refer to each other or to themselves", target=target, source=source),
                                   fix=t("Only one level: e.g. back same as front"))], 2)  # fmt: skip
        ta, sa = ids[target].polygon.bbox(), ids[source].polygon.bbox()
        dw, dh = abs((ta[2] - ta[0]) - (sa[2] - sa[0])), abs((ta[3] - ta[1]) - (sa[3] - sa[1]))
        if max(dw, dh) > SAME_FACE_TOL_MM:
            return _fail(r, [Issue(id="ARG", message=t("{target} and {source} differ in size and cannot be the same", target=target, source=source),
                                   fix=t("Only panels of the same size can share a layout (e.g. front and back)"))], 2)  # fmt: skip
        same[target] = source
        own = [p.id for p in job.placements if p.coord_space == "panel" and p.panel == target]
        if own:
            r.warnings.append(Issue(id="ARG", message=t("{target} already has placements: {own}; they will sit on top of the copied layout", target=target, own=", ".join(own)),
                                    fix=t("If they are not needed, delete them with `art remove`")))  # fmt: skip
    job.same_faces = same or None
    r.artifacts = [str(job.save(directory))]
    r.data = {"same_faces": same}
    return r


SETTABLE_CLEAR = {"clip", "color", "colors", "tag", "style", "font", "size_pt", "tracking",
                  "line_height", "label", "label_en", "fit", "align", "content", "hanging_indent",
                  "vertical", "skew_x", "skew_y"}  # fmt: skip


def art_set(
    directory: Path, art_id: str, fields: dict[str, Any], clear: list[str] | None = None
) -> Result:
    """改一个放置的字段（位置、面、样式、颜色、裁切形状……）：id 与叠放次序不变，校验与 art add
    相同；校验不过时 job.json 原样不动。clear 是要去掉的字段（如 clip、color）。"""
    r = Result(command="art set", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    ids = [x.id for x in job.placements]
    if art_id not in ids:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("No placement {id}", id=art_id),
                    fix=t("Available: ") + ", ".join(ids),
                )
            ],
            2,
        )
    clear = list(clear or [])
    bad = [k for k in clear if k not in SETTABLE_CLEAR]
    if bad:
        return _fail(r, [Issue(id="ARG", message=t("Fields that cannot be removed: {names}", names=", ".join(bad)),
                               fix=t("Can be removed: ") + ", ".join(sorted(SETTABLE_CLEAR)))], 2)  # fmt: skip
    changes = {k: v for k, v in fields.items() if v is not None and k not in ("id", "kind")}
    if not changes and not clear:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("No fields to change"),
                    fix=t("e.g. --box 10,20,80,30 or --style body"),
                )
            ],
            2,
        )
    i = ids.index(art_id)
    old = job.placements[i]
    data = old.model_dump(exclude_none=True)
    data.update(changes)
    if changes.get("space") in ("body", "blank"):  # 换到盒身 / 展开图坐标：box 不再属于某个面
        data.pop("panel", None)
    if "panel" in changes and data.get("space") in ("body", "blank"):
        data.pop("space")
    if "text" in changes and "content" not in changes:  # 改成自己写的文字：不再引用清单条目
        data.pop("content", None)
    if "content" in changes:
        data.pop("text", None)
    for k in clear:
        data.pop(k, None)
    data["before"] = ids[i + 1] if i + 1 < len(ids) else None
    path = directory / JOB_FILE
    original = path.read_text(encoding="utf-8")
    del job.placements[i]
    job.save(directory)
    added = art_add(directory, data)
    if not added.ok:
        path.write_text(original, encoding="utf-8")
        added.command = "art set"
        return added
    r.artifacts = added.artifacts
    r.warnings += added.warnings
    r.data = {"id": art_id, "changed": sorted(changes), "cleared": clear}
    return r


def art_remove(directory: Path, art_id: str) -> Result:
    """删掉一个放置（素材文件留在 assets/ 里）。"""
    r = Result(command="art remove", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    ids = [x.id for x in job.placements]
    if art_id not in ids:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("No placement {id}", id=art_id),
                    fix=t("Available: ") + ", ".join(ids),
                )
            ],
            2,
        )
    del job.placements[ids.index(art_id)]
    r.artifacts = [str(job.save(directory))]
    r.data = {"removed": art_id, "order": [x.id for x in job.placements]}
    return r


def finish_add(directory: Path, fields: dict[str, Any]) -> Result:
    from pacxio_pro.finishes.service import edit

    res: Result = edit(directory, "finish", "add", fields)
    return res


def _save_with_dieline(directory: Path, job: Job, dl: Dieline) -> list[str]:
    """改了结构（如开窗）：job.json 与 dieline.svg 一起写。"""
    svg = directory / DIELINE_SVG
    svg.write_text(dieline_to_svg(dl), encoding="utf-8")
    return [str(job.save(directory)), str(svg)]


def window_add(directory: Path, fields: dict[str, Any]) -> Result:
    """开一个窗（）。违反 W01–W03 时不写进 job.json，返回问题与修法。"""
    r = Result(command="window add", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    dl, issues = build_dieline(job)
    if dl is None:
        return _fail(r, issues)
    fields = {k: v for k, v in fields.items() if v is not None}
    existing = [w.id for w in job.windows or []]
    fields.setdefault("id", _next_id(existing, "w"))
    if fields["id"] in existing:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("Window id {id} already exists", id=fields["id"]),
                    fix=t("Use another --id"),
                )
            ],
            2,
        )
    try:
        window = Window.model_validate(fields)
    except ValidationError as e:
        return _fail(r, parameter_issues(e), 2)
    candidate = job.model_copy(update={"windows": [*(job.windows or []), window]})
    new_dl, issues = build_dieline(candidate)
    if new_dl is None:
        return _fail(r, issues)
    profile = load_profile(job.profile)
    r.artifacts = _save_with_dieline(directory, candidate, new_dl)
    info = windows_by_panel(new_dl, candidate, profile)[window.panel]
    r.data = {
        "window": next(i for i in info if i["id"] == window.id),
        "windows": len(candidate.windows or []),
    }
    r.warnings += [
        Issue(id=f.id, message=f.message, fix=f.fix) for f in area_findings(new_dl, profile)
    ]
    stale = _window_stale_outputs(directory)
    if stale is not None:
        r.warnings.append(stale)
    return r


def _window_stale_outputs(directory: Path) -> Issue | None:
    """开窗改了结构：已经出过的图（只说真有的）画的还是没窗口的盒子。新 job 还什么都没出，不提醒。"""
    from pacxio.concept.service import concept_metas
    from pacxio.mockup.approval import MOCKUP_DIR, PREVIEW_MAPS, read_approval

    made, fixes = [], []
    if concept_metas(directory):
        made.append(t("concept images (main view / back view / flats)"))
        fixes.append(t("Generate the concept images again (the prompt will include the window)"))
    if any(directory.glob(PREVIEW_MAPS)):
        made.append(t("dieline confirmation image"))
        fixes.append(t("Render the dieline confirmation image again"))
        if read_approval(directory) is not None:
            fixes.append(t("Ask the user to confirm the dieline image again (approve)"))
    if any((directory / MOCKUP_DIR).glob("*.png")):
        made.append(t("3D mockups"))
        fixes.append(t("Make the 3D mockups again (view only, no confirmation)"))
    if not made:
        return None
    return Issue(id="WINDOW", message=t("The window changed the structure: the {made} already made do not have this window", made=", ".join(made)),
                 fix="; ".join(fixes))  # fmt: skip


def window_list(directory: Path) -> Result:
    r = Result(command="window list", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    dl, issues = build_dieline(job)
    if dl is None:
        return _fail(r, issues)
    by_panel = windows_by_panel(dl, job, load_profile(job.profile))
    r.data = {"windows": [dict(i, panel=pid) for pid, items in by_panel.items() for i in items]}
    return r


def window_remove(directory: Path, window_id: str) -> Result:
    r = Result(command="window remove", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    kept = [w for w in job.windows or [] if w.id != window_id]
    if len(kept) == len(job.windows or []):
        ids = ", ".join(w.id for w in job.windows or []) or t("(no windows)")
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("No window {id}", id=window_id),
                    fix=t("Windows: {ids}", ids=ids),
                )
            ],
            2,
        )
    job = job.model_copy(update={"windows": kept or None})
    dl, issues = build_dieline(job)
    if dl is None:
        return _fail(r, issues)
    r.artifacts = _save_with_dieline(directory, job, dl)
    r.data = {"removed": window_id, "windows": len(kept)}
    return r


def preflight_job(directory: Path) -> Result:
    """印前检查（规格 12）：写 out/preflight/ 下的印刷文件、报告 JSON / PDF。"""
    from pacxio_pro import pipeline  # 闭源（）：开源版这条命令转给云端
    from pacxio_pro.export.report_pdf import write_report_pdf

    r = Result(command="preflight", job=str(directory))
    with use_language(dielines.job_output_language(directory)):  # 排版时的提示（V01 等）进报告
        parts, dset, errs = pipeline.prepare_parts(directory)
    if errs or dset is None:
        return _fail(r, errs, 2 if any(i.id == "JOB" for i in errs) else 1)
    work = directory / dielines.PREFLIGHT_DIR
    job = _load_job(directory, r)
    assert job is not None
    with use_language(doc_language(job)):  # 报告是交付件：语言按配置档
        report, per = pipeline.check_parts(parts, dset, job, work)
        img = pipeline.combined_preview(parts, report)
        (work / dielines.REPORT_JSON).write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        write_report_pdf(work / dielines.REPORT_PDF, report, img)
    pdfs = [str(pdf) for _, pdf in per.values()]
    r.artifacts = [*pdfs, str(work / dielines.REPORT_JSON), str(work / dielines.REPORT_PDF)]
    _report_into(r, report)
    r.data["findings"] = [f.model_dump(exclude_none=True) for f in report.findings]
    return _with_notes(r, directory)


def _approval_issue(status: str) -> Issue:
    msgs = {
        "none": t("No dieline confirmation image yet; the design has not been confirmed"),
        "pending": t("The dieline confirmation image is made but not confirmed yet"),
        "stale": t(
            "The design changed after the last confirmation / confirmation image; what was confirmed is not the version being exported"
        ),
    }
    return Issue(
        id="APPROVE",
        message=msgs.get(status, t("Dieline confirmation image not confirmed"))
        + t(
            ": print files should be exported after the user confirms the dieline confirmation image"
        ),
        fix=t(
            "Run `pacxio render <job>` to make the dieline confirmation image; after the user confirms (clicks on the workbench, or says so) run `pacxio approve <job>`"
        ),
    )


def pack_name(job: Job) -> str:
    return dielines.pack_name(job)


def _resolve_wanted(
    formats: list[str] | None, profile_deliverables: list[str]
) -> tuple[set[str], list[str]]:
    """把 --formats 解析成交付物名称集合；返回 (集合, 未知项)。不给时取配置档 deliverables + archive。"""
    if not formats:
        names = {dielines.ALIASES.get(d, d) for d in profile_deliverables}
        return {d for d in names if d in dielines.DELIVERABLES or d in dielines.EXTRA}, []
    wanted: set[str] = set()
    unknown: list[str] = []
    for f in formats:
        name = dielines.ALIASES.get(f, f)
        if name in dielines.DELIVERABLES or name in dielines.EXTRA:
            wanted.add(name)
        else:
            unknown.append(f)
    return wanted, unknown


def export_job(
    directory: Path,
    formats: list[str] | None = None,
    proof: bool = False,
    out_dir: Path | None = None,
    require_approval: bool = False,
) -> Result:
    """导出交付包（规格 11）。印前检查有 error 时拒绝（D12）：只写标注"未通过"的预览与检查报告。"""
    from pacxio_pro import pipeline  # 闭源（）：开源版这条命令转给云端

    r = Result(command="export", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    profile = load_profile(job.profile)
    wanted, unknown = _resolve_wanted(formats, [*profile.deliverables, *profile.archive])
    in_pack = {dielines.ALIASES.get(d, d) for d in profile.deliverables}
    if unknown:
        return _fail(
            r,
            [
                Issue(
                    id="ARG",
                    message=t("Unknown format {formats}", formats=unknown),
                    fix=t("Available: ") + ", ".join([*dielines.DELIVERABLES, *dielines.ALIASES]),
                )
            ],
            2,
        )
    approval = approval_state(directory, job)
    if require_approval and not approval.ok:
        return _fail(r, [_approval_issue(approval.status)], 1)
    with use_language(dielines.job_output_language(directory)):  # 排版时的提示（V01 等）进报告
        parts, dset, errs = pipeline.prepare_parts(directory)
    if errs or dset is None:
        return _fail(r, errs, 1)
    base = out_dir if out_dir is not None else directory / "out"
    pack = base / dielines.pack_name(job)
    work = directory / dielines.PREFLIGHT_DIR
    language = doc_language(job)  # 交付包里给人看的（报告、规格书、说明）语言按配置档
    with use_language(language):
        report, per = pipeline.check_parts(parts, dset, job, work)
    dielines.reset_pack(pack)

    if not report.passed:  # D12（多部件盒型对整套：少一个部件的交付包没法用）
        with use_language(language):
            files = pipeline.write_failed_parts(parts, report, pack)
        r.artifacts = [str(f) for f in files]
        _report_into(r, report)
        return _with_notes(r, directory)

    try:
        with use_language(language):
            if dset.multipart:
                files = pipeline.write_set_deliverables(
                    parts, per, report, pack, wanted, proof, approval, in_pack, job.job_id
                )
            else:
                files = pipeline.write_deliverables(
                    parts[0], report, per[None][1], pack, wanted, proof, approval, in_pack
                )
    except KeyError as e:
        return _fail(
            r,
            [
                Issue(
                    id="PROFILE",
                    message=str(e.args[0]),
                    fix=t("Add the mapping for this line type / spot color to the profile"),
                )
            ],
        )
    r.artifacts = [str(f) for f in files]
    r.data = {
        "pack_dir": str(pack),
        "archive_dir": str(dielines.archive_dir(pack)),
        "in_pack": sorted(wanted & in_pack),
        "deliverables": sorted(wanted),
        "proof": proof,
        "approval_status": approval.status,
    }
    _report_into(r, report)
    if not approval.ok:
        r.warnings.append(_approval_issue(approval.status))
    if not all(  # 标签没有盖 / 插舌，不用做折叠方向测试
        p.dl.style_meta.get("orientation_verified")
        or p.dl.style_meta.get("layout") in ("label", "pouch")
        for p in parts
    ):
        r.warnings.append(
            Issue(
                id="ORIENT",
                message=t(
                    "Upright direction of lids and tucks not confirmed by a proof (orientation_verified=false)"
                ),
                fix=t(
                    "Test the folding direction with the --proof sheet and write the result back to the style metadata"
                ),
            )
        )
    if "cf2" in wanted & in_pack:  # 存档里的 CF2 不发给工厂，不用提醒
        r.warnings.append(
            Issue(
                id="CF2",
                message=t(
                    "CF2 is written the way the open-source converter DXF2Diecut writes it, not verified by die-maker software"
                ),
                fix=t(
                    "Ask the die maker to open it in their software; do not mark it verified before that"
                ),
            )
        )
    if "dxf" in wanted:
        r.warnings.append(
            Issue(
                id="DXF",
                message=t("DXF R12 has no unit header variable; units are mm by convention"),
                fix=t("Tell the die maker the DXF units are mm"),
            )
        )
    if not profile.verified:
        r.warnings.append(
            Issue(
                id="PROFILE",
                message=t("Profile not confirmed by the factory"),
                fix=t("Use a factory-confirmed profile before delivery"),
            )
        )
    return _with_notes(r, directory)


def get_panel_map(directory: Path) -> Result:
    """面映射：每个面的尺寸、正立方向、安全区、300ppi 下建议像素（规格 9）。"""
    r = Result(command="panels", job=str(directory))
    job = _load_job(directory, r)
    if job is None:
        return r
    dset, issues = dielines.build_parts_for(job)
    if dset is None:
        r.errors += issues
        r.ok = False
        r.exit_code = 1
        return r
    profile = load_profile(job.profile)
    if dset.multipart:  # 每个部件一份面映射；另给一个面全名（部件.面）的平铺列表，art add 直接用
        maps = {
            part: panel_map(pd, profile, job=job.for_part(part)) for part, pd in dset.parts.items()
        }
        flat = [
            {**pinfo, "id": f"{part}.{pinfo['id']}", "part": part}
            for part, m in maps.items()
            for pinfo in m["panels"]
        ]
        r.data = {"style": job.style, "parts": maps, "part_labels": {k: display_name(v) for k, v in dset.labels.items()}, "panels": flat}
    else:
        r.data = panel_map(dset.parts[None], profile, job=job)
    dl = next(iter(dset.parts.values()))
    if not dl.style_meta["orientation_verified"]:
        r.warnings.append(
            Issue(
                id="ORIENT",
                message=t(
                    "Upright direction of lids and tucks not confirmed by a proof (up_in_blank is provisional)"
                ),
                fix=t(
                    "Artwork direction on lids / tucks must wait for the direction test before it is final"
                ),
            )
        )
    return r
