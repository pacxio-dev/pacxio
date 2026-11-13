"""job → 刀版（开源）：盒型 + 参数 + 配置档 → 一套刀版（每个部件一张，加上窗口和刀模圆角）。

还有交付文件的文件名约定（工作台、导出共用）。合成、印刷文件、印前检查、交付包在 `pipeline`（闭源，
）；`pipeline` 把这里的名字原样再导出，旧代码照常用 `pipeline.xxx`。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pacxio.geometry import Affine
from pacxio.messages import display_name, lang, pick, t
from pacxio.model.common import Issue, parameter_issues
from pacxio.model.dieline import Dieline
from pacxio.model.job import Job, load_job_checked
from pacxio.model.profile import Profile, ProfileNotFoundError, load_profile, output_language
from pacxio.styles import all_styles, get_style
from pacxio.windows import apply_windows

PREFLIGHT_DIR = "out/preflight"
PRINT_PDF = (
    "01_print_PDFX4.pdf"  # 印前检查时在 PREFLIGHT_DIR 生成的印刷总文件；导出时复制成生产文件
)
REPORT_JSON = "05_preflight_report.json"  # PREFLIGHT_DIR 里的检查报告
REPORT_PDF = "05_preflight_report.pdf"
JOB_PREVIEW = "preview.png"  # render 写在 job 目录里的刀版确认图；多部件为 preview_<部件>.png
FAILED_PREVIEW = "PREFLIGHT_FAILED_preview.png"
FAILED_BANNER_PX = 28

# 交付包 v2（，）：发给工厂的文件夹只放配置档 deliverables 里的
# （默认生产文件、规格书、刀模 DXF），文件名 <job_id>[_<部件>]_<后缀>；其余放旁边的 <交付包>_archive/。
PRODUCTION_PDF = "production.pdf"  # 生产文件：分层的印刷总文件
SPEC_PDF = "spec.pdf"
DIELINE_DXF = "dieline.dxf"
ARCHIVE_SUFFIX = "_archive"
CONFIRM_PNG = "confirm.png"  # 用户确认过的刀版确认图（导出闸门确认的就是它）
PREVIEW_PNG = "preview.png"  # 展开图预览（不带检查问题框）
PROOF_PDF = "proof_1to1.pdf"
ARCHIVE_REPORT_JSON = "preflight_report.json"
ARCHIVE_REPORT_PDF = "preflight_report.pdf"

# 交付物名称（对应配置档 deliverables / archive）与别名
DELIVERABLES = (
    "production_pdf", "spec_sheet", "dxf", "dieline_pdf", "cf2",
    "finish_pdfs", "preflight_report", "preview_png", "confirm_png",
)  # fmt: skip
ALIASES = {
    "print": "production_pdf",
    "print_pdfx4": "production_pdf",  #  之前的名字
    "production": "production_pdf",
    "finish": "finish_pdfs",
    "pdf": "dieline_pdf",
    "spec": "spec_sheet",
    "report": "preflight_report",
    "preview": "preview_png",
    "confirm": "confirm_png",
    "svg": "preview_svg",
}
EXTRA = ("preview_svg",)


PART_GAP_MM = 30.0  # 合并的"目录刀版"里部件之间的间隔（只用于名字校验、列表与 dieline.svg）


@dataclass
class DielineSet:
    """一套刀版（ 0.4）：单部件盒型只有一个键为 None 的部件。"""

    style: str
    parts: dict[str | None, Dieline]
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def multipart(self) -> bool:
        return None not in self.parts

    def label(self, part: str | None) -> str:
        return f"{self.labels.get(part, part)} {part}" if part else ""

    def shown(self, part: str | None) -> str:
        """给人看的部件名，按当前语言：中文"盖 lid"，英文 lid。"""
        return pick(part, self.label(part)) if part else ""

    def merged(self) -> Dieline:
        """把各部件并排放进一张"目录刀版"，面、线、窗口的 id 加"部件."前缀。

        只用于面名校验、`panels` 列表、dieline.svg 预览；印刷、检查、导出都按部件各做各的。
        """
        if not self.multipart:
            return self.parts[None]
        panels, edges, glue, windows = [], [], [], []
        x = 0.0
        height = 0.0
        first = next(iter(self.parts.values()))
        offsets: dict[str, float] = {}
        for part, dl in self.parts.items():
            offsets[str(part)] = x
            m = Affine.translate(x, 0.0)
            pre = f"{part}."
            for pn in dl.panels:
                hinge = pn.hinge.model_copy(update={"parent_panel": pre + pn.hinge.parent_panel,
                                                    "edge_id": pre + pn.hinge.edge_id}) if pn.hinge else None  # fmt: skip
                panels.append(pn.model_copy(update={"id": pre + pn.id, "polygon": pn.polygon.transformed(m),
                                                    "frame": m @ pn.frame, "hinge": hinge}))  # fmt: skip
            for e in dl.edges:
                edges.append(e.model_copy(update={"id": pre + e.id, "panels": [pre + q for q in e.panels],
                                                  "segments": [m.apply_segment(sg) for sg in e.segments]}))  # fmt: skip
            glue += [g.transformed(m) for g in dl.glue_areas]
            windows += [
                w.model_copy(update={"panel": pre + w.panel, "cut": w.cut.transformed(m)})
                for w in dl.windows
            ]
            x += dl.bbox[2] + PART_GAP_MM
            height = max(height, dl.bbox[3])
        meta = {
            k: v
            for k, v in first.style_meta.items()
            if k not in ("part", "part_label", "faces", "dims", "layout")
        }
        faces, rects = self._set_faces(offsets)
        return first.model_copy(update={
            "style_meta": {**meta, "parts": list(self.parts), "set_faces": faces, "set_rects": rects,
                           "part_offsets": offsets},  # 部件刀版在目录刀版里往右挪了多少（抽屉测试 F-21）
            "bbox": (0.0, 0.0, x - PART_GAP_MM, height),
            "panels": panels, "edges": edges, "glue_areas": glue, "windows": windows,
        })  # fmt: skip

    def _set_faces(
        self, offsets: dict[str, float]
    ) -> tuple[dict[str, str], dict[str, tuple[float, float, float, float]]]:
        """合上后外面看得到的面（和效果图同一个场景）：位置 key（front / side_b / back / side_a / top /
        bottom）→ "部件.面"，以及每个面在目录刀版里取的矩形——背面视角、平面图、拉正、对照都按它
        （天地盖测试 F-15：多部件盒型也要平面图）。"""
        from pacxio.mockup.fold import FACE_LABELS
        from pacxio.mockup.scene import build_scene

        try:
            scene = build_scene(self.style, {str(k): v for k, v in self.parts.items()})
        except (KeyError, ValueError):
            return {}, {}
        faces: dict[str, str] = {}
        rects: dict[str, tuple[float, float, float, float]] = {}
        for f in scene.box.faces:
            src = scene.sources.get(f.key)
            if f.key not in FACE_LABELS or src is None or len(src) != 2:  # 纯色（纸背面）不算
                continue
            part, rect = src
            assert isinstance(part, str) and isinstance(rect, tuple)
            x0, y0, x1, y1 = rect
            dx = offsets.get(part, 0.0)
            faces[f.key] = f.panel
            rects[f.panel] = (x0 + dx, y0, x1 + dx, y1)
        return faces, rects


def style_parts(style: object, params: Any = None) -> tuple[str, ...]:
    """多部件盒型的部件 id；单部件盒型为空元组。部件按配方给的盒型（几何引擎多件套）要传参数。"""
    if params is not None and hasattr(style, "parts_for"):
        return tuple(style.parts_for(params))
    return tuple(getattr(style, "parts", ()))


def style_part_labels(style: object, params: Any = None) -> dict[str, str]:
    if params is not None and hasattr(style, "labels_for"):
        return dict(style.labels_for(params))
    return dict(getattr(style, "part_labels", {}))


def _style_and_params(job: Job) -> tuple[Any, Any, Profile | None, list[Issue]]:
    try:
        style = get_style(job.style)
    except KeyError:
        return None, None, None, [
            Issue(id="S03", message=t("Unknown box style {style}", style=job.style),
                  fix=t("Available box styles: {styles}", styles=", ".join(s.id for s in all_styles())))
        ]  # fmt: skip
    try:
        params = style.param_schema().model_validate(job.params)
        profile = load_profile(job.profile)
    except ValidationError as e:
        return style, None, None, parameter_issues(e)
    except ProfileNotFoundError as e:
        return (
            style,
            None,
            None,
            [Issue(id="ENV", message=str(e), fix=t("Check the --profile name"))],
        )
    return style, params, profile, style.validate(params, job.material, profile)


def build_parts_for(job: Job) -> tuple[DielineSet | None, list[Issue]]:
    """job → 一套刀版（每个部件一张，各自加上自己的窗口）。有错时返回 (None, issues)。"""
    style, params, profile, issues = _style_and_params(job)
    if issues:
        return None, issues
    assert profile is not None
    parts = style_parts(style, params)
    labels = style_part_labels(style, params)
    from pacxio.engine.rounding import apply_finish

    if not parts:
        dl, issues = apply_windows(
            style.generate(params, job.material, profile), job.windows, profile, job.material
        )
        if dl is not None:  # 刀模圆角：卡纸默认外 2 / 内 1
            apply_finish(dl, job.material.type, job.corner_finish)
        return (DielineSet(job.style, {None: dl}) if dl is not None else None), issues
    issues = part_name_issues(job, parts)
    if issues:
        return None, issues
    out: dict[str | None, Dieline] = {}
    allowed = getattr(style, "window_panels", {})
    window_panels = tuple(f"{part}.{name}" for part, names in allowed.items() for name in sorted(names))
    for part, dl in style.generate_parts(params, job.material, profile).items():
        view = job.for_part(part)
        built, errs = apply_windows(
            dl, view.windows, profile, job.material, allowed.get(part, set()),
            allowed_on_other_parts=window_panels,
        )
        if built is None:
            return None, [
                e.model_copy(update={"message": t("{part}: {message}",
                    part=f"{display_name(labels[part])} {part}", message=e.message)})
                for e in errs
            ]
        apply_finish(built, job.material.type, job.corner_finish)
        out[part] = built
    return DielineSet(job.style, out, labels), []


def part_name_issues(job: Job, parts: tuple[str, ...]) -> list[Issue]:
    """多部件盒型：面名要写成"部件.面"、part 要是这个盒型的部件。"""
    names: list[str] = []
    for p in job.placements:
        names += [p.panel] if p.panel else []
        names += p.scope_panels() or []
        if p.part is not None and p.part not in parts:
            return [Issue(id="PANEL", message=t("Placement {id} refers to part={part}, which is not a part of this box style", id=p.id, part=p.part),
                          fix=t("Available parts: ") + "、".join(parts))]  # fmt: skip
        if p.coord_space == "blank" and p.part is None:
            return [Issue(id="PANEL", message=t("Placement {id} uses full-dieline coordinates (space=blank). Specify the part for this multi-part style.", id=p.id),
                          fix=t("Add --part ") + " / ".join(parts))]  # fmt: skip
        if p.coord_space == "body":
            return [Issue(id="SPACE", message=t("Placement {id}: space=body is unavailable for multi-part styles", id=p.id),
                          fix=t("Use --space panel for local panel coordinates or --space blank --part …"))]  # fmt: skip
    for surface in job.surface_treatments or []:
        coverage = surface.coverage
        if coverage.part is not None and coverage.part not in parts:
            return [
                Issue(
                    id="PANEL",
                    message=t("Surface treatment {id} refers to missing part {part}", id=surface.id, part=coverage.part),
                    fix=t("Available parts: ") + "、".join(parts),
                )
            ]
        names += coverage.panels or []
        names += [e.panel for e in surface.exclusions]
    names += [f.panel for f in job.finishes] + [w.panel for w in job.windows or []]
    names += [n for pair in (job.same_faces or {}).items() for n in pair]
    for n in names:
        if n.split(".", 1)[0] not in parts or "." not in n:
            return [Issue(id="PANEL", message=t("Panel {panel}: a multi-part style requires part.panel names such as {example}.top", panel=n, example=parts[-1]),
                          fix=t("Available parts: ") + "、".join(parts) + t(". Run `pacxio panels <job>` to list all panels"))]  # fmt: skip
    return []


def build_dieline_for(job: Job) -> tuple[Dieline | None, list[Issue]]:
    """job → Dieline（单部件盒型）。多部件盒型返回合并的"目录刀版"（面名带部件前缀，只用于校验与列表）。"""
    dset, issues = build_parts_for(job)
    if dset is None:
        return None, issues
    return dset.merged(), []


def job_profile(job: Job) -> Profile:
    """job 用的配置档：柔印（print.method = flexo）时用配置档 flexo 一节的总墨量、最小字号。"""
    profile = load_profile(job.profile)
    if job.print.method == "flexo" and profile.flexo is not None:
        profile = profile.model_copy(update=profile.flexo.model_dump(exclude_none=True))
    if job.material.type == "film_laminate":  # 软袋：出血、安全区按配置档 pouches 一节（C5）
        from pacxio.styles.pouch import pouch_rules

        prules = pouch_rules(profile)
        profile = profile.model_copy(
            update={"bleed_mm": prules.bleed_mm, "safe_margin_mm": prules.safe_margin_mm}
        )
    if job.material.type == "label_stock":  # 标签：出血、安全区按配置档 labels 一节（C4）
        from pacxio.styles.label import label_rules

        rules = label_rules(profile)
        profile = profile.model_copy(
            update={"bleed_mm": rules.bleed_mm, "safe_margin_mm": rules.safe_margin_mm}
        )
    return profile


def load_job(directory: Path) -> tuple[Job | None, list[Issue]]:
    return load_job_checked(directory)


def job_output_language(directory: Path) -> str:
    """job 的交付件语言（见 output_language）；job 读不出来时跟随 PACXIO_LANG，错误由命令自己报。"""
    job, _ = load_job_checked(directory)
    return output_language(job.profile) if job is not None else lang()


def style_name(meta: dict[str, Any], style: str) -> str:
    """盒型名，按当前交付件语言：英文用 name_en（没有就查名称表），中文用 name_zh。"""
    zh = str(meta.get("name_zh") or style)
    return pick(str(meta.get("name_en") or display_name(zh, "en")), zh)


def pack_name(job: Job) -> str:
    q = job.box_dims()
    if job.material.type in (
        "label_stock",
        "film_laminate",
    ):  # 标签 / 软袋：H 是占位或底褶，不写进名字
        return f"{job.job_id}_{job.style}_{q['L']:g}x{q['W']:g}"
    return f"{job.job_id}_{job.style}_{q['L']:g}x{q['W']:g}x{q['H']:g}"


def archive_dir(pack: Path) -> Path:
    """存档文件夹：和交付包并排，自己留着、不用发给工厂。"""
    return pack.with_name(pack.name + ARCHIVE_SUFFIX)


def reset_pack(pack: Path) -> None:
    """重新导出前清空交付包和存档：里面都是能从 job.json 重建的生成文件，不清会留下旧文件
    （包括检查不通过时留着上一次的生产文件）。"""
    for folder in (pack, archive_dir(pack)):
        if folder.is_dir():
            shutil.rmtree(folder)
