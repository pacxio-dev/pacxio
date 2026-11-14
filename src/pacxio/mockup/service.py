"""mockup / approve 命令的核心函数。无全局状态、无交互输入，返回统一的 Result。

流程：设计（job）→ 效果图确认 → 印前检查 → 导出。效果图和印刷文件来自同一份 job，
所以确认的就是最终会被印出来的内容。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from pacxio.concept.service import back_view_for, read_chosen
from pacxio.export.canvas import _Map
from pacxio.inventory import confirmation_status
from pacxio.messages import display_name, join, lang, use_language
from pacxio.messages import t as translate
from pacxio.mockup.approval import (
    APPROVAL,
    MANIFEST,
    MOCKUP_DIR,
    approval_state,
    design_fingerprint,
    make_approval,
    read_manifest,
    style_version,
    write_approval,
)
from pacxio.mockup.art import MOCKUP_DPI, face_textures, render_art
from pacxio.mockup.content import ContentMap, blocking_content, check_content, collect_content
from pacxio.mockup.fold import FACE_LABELS, BoxModel, build_box
from pacxio.mockup.materials import presets, roughness_map
from pacxio.mockup.raster import STATIC_RENDERER, folded_static, render_folded
from pacxio.mockup.render import BELOW_BACK_VIEW, OPEN_VIEW, VIEWS, render_view
from pacxio.mockup.scene import Scene, build_scene, scene_textures
from pacxio.mockup.scene3d import State, write_scene
from pacxio.mockup.sheet import build_sheet
from pacxio.model.common import Issue, Result
from pacxio.model.job import Job
from pacxio.model.report import Finding
from pacxio.notes import open_notes_issue
from pacxio.visual.meta import now_iso

if TYPE_CHECKING:  # 闭源（）：只用作类型
    from pacxio import dielines
    from pacxio_pro import pipeline
    from pacxio_pro.layout.build import LayoutResult

SHEET_NAME = "00_mockup_sheet.png"
FACE_FILE = "face_{key}.png"
VIEW_FILE = "view_{name}.png"
VIEW_SIZE = (1200, 1200)
STYLES_WITH_MOCKUP = {
    "tray_glued",
    "tray_lock",
    "hinged_lid",
    "sleeve",
    "belly_band",
    "insert_card",
    "divider",
    "rte",
    "ste",
    "snaplock",
    "autolock",
    "fefco_0427",
    "mailer_tuck",
    "mailer_front_lock",
    "mailer_tear_seal",
    "fefco_0201",
    "tray_lid",
    "drawer",
    "seal_end",
    "rte_hanger",
    "ste_hanger",
    "snaplock_hanger",
    "autolock_hanger",
    "fefco_0200",
    "fefco_0202",
    "fefco_0203",
    "card",
    "header_card",
    "crashlock_rsc",
    "gable",
    "carton",
    "counter_display",
    "label_panel",
    "label_wrap",
    "label_cone",
    "pouch_standup",
    "sachet_3side",
    "pouch_backseal",
    "pouch_flat_bottom",
    "structure",  # 几何引擎（第 0 步）：折法从立体推出来
    "structure_set",  # 几何引擎多件套（第 5 步）：各件折好并排摆
}


def _issue(f: Finding) -> Issue:
    return Issue(id=f.id, message=f.message, fix=f.fix)


def _rough_art(layout: LayoutResult, art: Image.Image, m: _Map) -> Image.Image:
    """和成品外观图同尺寸的粗糙度图（RGB 三通道同值），按同样的裁切、折叠做成每张纸的粗糙度纹理。"""
    return roughness_map(layout, art.size, m).convert("RGB")


def _printed(tex: dict[str, Image.Image]) -> dict[str, Image.Image]:
    """粗糙度纹理只留印刷面：纸背、内面不覆膜、不上 UV，不反光。"""
    return {k: v for k, v in tex.items() if not k.endswith((":reverse", ":in"))}


def _missing_fix(required: list[str], missing: list[str]) -> str:
    """缺信息时可以照做的修法：标签类给出具体的 --tag，清单类用 --content。"""
    parts = []
    tags = [t for t in missing if t in required]
    if tags:
        parts.append(translate("Add the missing content and tag it (`art add --tag {tag} ...`)", tag=tags[0]))
    if len(tags) < len(missing):
        parts.append(translate("Add checklist items with `art add --content <item>` and finalize any pending wording"))
    separator = "；" if lang() == "zh" else "; "
    return separator.join(parts) + translate(". Then rerun mockup.")


def _multipart_views(
    job: Job,
    dset: dielines.DielineSet,
    parts: list[pipeline.Prepared],
    reflect: dict[str, float],
) -> tuple[
    BoxModel,
    dict[str, Image.Image],
    dict[str, Image.Image],
    ContentMap,
    list[Finding],
    dict[str, State],
]:
    """多部件盒型：合上的三张 + 打开的一张、六个面的纹理、信息清单（ 0.6）。"""
    from pacxio_pro.preflight.checks import check_t04, check_t06, layout_warnings

    dls = {str(q.part): q.dl for q in parts}
    arts = {str(q.part): render_art(q.layout, q.dl) for q in parts}
    closed = build_scene(job.style, dls)
    opened = build_scene(job.style, dls, opened=True)
    roughs = {
        str(q.part): (_rough_art(q.layout, *arts[str(q.part)]), arts[str(q.part)][1]) for q in parts
    }

    def rough_of(scene: Scene) -> dict[str, Image.Image]:
        """纯色的面（纸背、内面）没有裁切来源，不反光。"""
        tex = scene_textures(scene, roughs, dls)
        return _printed({k: v for k, v in tex.items() if isinstance(scene.sources[k][1], tuple)})

    all_tex = scene_textures(closed, arts, dls)
    closed_rough = rough_of(closed)
    views = {
        v.name: render_view(
            closed.box,
            all_tex,
            v,
            VIEW_SIZE,
            depth_sort=job.style == "divider",
            rough=closed_rough,
            reflect=reflect,
        )
        for v in VIEWS
    }
    open_tex = scene_textures(opened, arts, dls)
    views["open"] = render_view(
        opened.box,
        open_tex,
        OPEN_VIEW,
        VIEW_SIZE,
        depth_sort=True,
        rough=rough_of(opened),
        reflect=reflect,
    )
    standard = [f for f in closed.box.faces if f.key in FACE_LABELS]
    if job.style == "divider":
        standard = [f for f in closed.box.faces if f.key in ("x_1", "y_1")]
    box = BoxModel(x=closed.box.x, y=closed.box.y, z=closed.box.z, faces=standard,
                   open_only=closed.open_only, open_hint=closed.open_hint)  # fmt: skip
    hidden_roles = {"tuck", "dust_flap", "glue_flap", "glue_tab", "inner_wall", "rim"}
    box.hidden_panels = [
        f"{q.part}.{pn.id}" for q in parts for pn in q.dl.panels if pn.role.value in hidden_roles
    ]
    textures = {f.key: all_tex[f.key].convert("RGB") for f in standard}
    content = collect_content(job, dset.merged(), box)
    findings: list[Finding] = []
    for q in parts:
        for f in check_t04(q.layout) + check_t06(q.layout) + layout_warnings(q.layout):
            findings.append(f.model_copy(update={"message": translate("{part}: {message}", part=dset.shown(q.part), message=f.message)}))
    scenes = {"closed": (closed.box, all_tex), "open": (opened.box, open_tex)}
    return box, views, textures, content, findings, scenes


def _set_folded(
    parts: list[pipeline.Prepared],
) -> tuple[BoxModel, dict[str, Image.Image], dict[str, Image.Image]]:
    """多件套：每件折好的纸并排放（x 方向，件间留 OPEN_GAP_MM），贴图键加"部件."前缀；另给印刷面的粗糙度纹理。"""
    from dataclasses import replace

    from pacxio.mockup.scene import OPEN_GAP_MM

    faces, textures, roughs = [], {}, {}
    cursor, ymax, zmax = 0.0, 0.0, 0.0
    for q in parts:
        art, m = render_art(q.layout, q.dl)
        built = folded_static(q.dl, art, m)
        if built is None:
            continue
        box, tex = built
        rough_built = folded_static(q.dl, _rough_art(q.layout, art, m), m)
        rough = _printed(rough_built[1]) if rough_built is not None else {}
        pts = [c for f in box.faces for c in f.corners]
        x0, y0, z0 = (min(p[i] for p in pts) for i in range(3))
        x1, y1, z1 = (max(p[i] for p in pts) for i in range(3))
        for f in box.faces:
            key = f"{q.part}.{f.key}"
            corners = tuple((c[0] - x0 + cursor, c[1] - y0, c[2] - z0) for c in f.corners)
            faces.append(replace(f, key=key, corners=corners))  # type: ignore[arg-type]
            if f.key in tex:
                textures[key] = tex[f.key]
            if f.key in rough:
                roughs[key] = rough[f.key]
        cursor += x1 - x0 + OPEN_GAP_MM
        ymax, zmax = max(ymax, y1 - y0), max(zmax, z1 - z0)
    return (
        BoxModel(x=max(cursor - OPEN_GAP_MM, 1.0), y=max(ymax, 1.0), z=max(zmax, 1.0), faces=faces),
        textures,
        roughs,
    )


def make_mockup(directory: Path, require: list[str] | None = None) -> Result:
    """说明文字用交付件语言（工厂配置档 document_language），和刀版确认图、检查报告一致。"""
    from pacxio import dielines

    with use_language(dielines.job_output_language(directory)):
        return _make_mockup(directory, require)


def _make_mockup(directory: Path, require: list[str] | None = None) -> Result:
    """生成精确效果图：3 个三维视角、6 个面的平视（单独的文件）、带信息清单的汇总页、manifest。"""
    from pacxio import dielines
    from pacxio_pro import pipeline
    from pacxio_pro.color.icc import icc_name
    from pacxio_pro.preflight.checks import check_t04, check_t06, layout_warnings

    r = Result(command="mockup", job=str(directory))
    head, _ = dielines.load_job(directory)  # 读不出来的 job 交给下面 prepare_parts 报 JOB 错误
    parts, dset, errs = pipeline.prepare_parts(directory, dpi=MOCKUP_DPI)
    if errs or dset is None:
        r.errors += errs
        r.ok = False
        r.exit_code = 2 if any(i.id == "JOB" for i in errs) else 1
        return r
    job = Job.load(directory)
    separator = "、" if lang() == "zh" else ", "
    if job.style not in STYLES_WITH_MOCKUP:
        return r.fail(
            Issue(
                id="ARG",
                message=translate("Style {style} does not support mockups yet", style=job.style),
                fix=translate("Supported styles: {styles}", styles=separator.join(sorted(STYLES_WITH_MOCKUP))),
            ),
            2,
        )
    required = [t.strip() for t in (require or []) if t.strip()]
    reflect: dict[str, float] = presets()["static_reflection"]
    folded: tuple[BoxModel, dict[str, Image.Image]] | None = None
    folded_rough: dict[str, Image.Image] | None = None
    box_rough: dict[str, Image.Image] | None = None
    if (
        job.style == "structure_set"
    ):  # 几何引擎多件套：每件按刀版折好、并排摆成一张图（组装关系还没画）
        set_box, set_tex, folded_rough = _set_folded(parts)
        folded = (set_box, set_tex)
        views = {v.name: render_folded(*folded, v, VIEW_SIZE, folded_rough, reflect) for v in VIEWS}
        p0 = parts[0]
        box = build_box(p0.dl)
        art0, m0 = render_art(p0.layout, p0.dl)
        textures = face_textures(art0, m0, box, cutout=True)
        content = collect_content(p0.job, p0.dl, box)
        layout_findings = []
        for q in parts:
            for f in check_t04(q.layout) + check_t06(q.layout) + layout_warnings(q.layout):
                layout_findings.append(f.model_copy(update={"message": translate("{part}: {message}", part=dset.shown(q.part), message=f.message)}))
        subtitle = translate("{name}  parts {parts} (each part folded and laid side by side; how they assemble is not drawn)",
                             name=dielines.style_name(p0.dl.style_meta, job.style),
                             parts=join([dset.shown(q.part) for q in parts]))  # fmt: skip
        orientation_ok = False
    elif dset.multipart:
        box, views, textures, content, layout_findings, _scenes = _multipart_views(
            job, dset, parts, reflect
        )
        set_meta = parts[0].dl.style_meta["set"]
        lc, wc, hc = set_meta["closed_outer"]
        subtitle = translate(
            "{name} ({style})  closed outer size {l} x {w} x {h} mm  material {material}  parts {parts}",
            name=dielines.style_name(parts[0].dl.style_meta, job.style), style=job.style.upper(),
            l=f"{lc:.1f}", w=f"{wc:.1f}", h=f"{hc:.1f}", material=job.material.preset or translate("Custom"),
            parts=join([dset.shown(q.part) for q in parts]),
        )  # fmt: skip
        # 每个部件都经打样确认过方向才不报 MK03（天地盖、抽屉盒 2026-10-24 已确认；以前这里写死了 False，
        # 天地盖测试 F-19）
        orientation_ok = all(bool(q.dl.style_meta.get("orientation_verified")) for q in parts)
    else:
        p = parts[0]
        box = build_box(p.dl)
        art, m = render_art(p.layout, p.dl)
        if p.dl.style_meta.get("layout") == "pouch":  # 透明窗：没印的地方透出内容物（只作展示）
            from pacxio.mockup.pouch import window_art

            art = window_art(art, m, p.dl, job.pouch.contents if job.pouch else None)
        textures = face_textures(art, m, box, cutout=True)
        rough_art = _rough_art(p.layout, art, m)
        box_rough = face_textures(rough_art, m, box, cutout=True)
        folded = folded_static(p.dl, art, m)
        rough_built = folded_static(p.dl, rough_art, m)
        if rough_built is not None:
            folded_rough = _printed(rough_built[1])
        if p.dl.style_meta.get("layout") == "label":  # 标签：贴在瓶 / 罐上（C4，容器只作展示）
            from pacxio.mockup.container import container_scene

            folded = container_scene(p.dl, art, m, job.container)
            folded_rough = None  # 贴在瓶上的标签：纹理另有一套键，暂不画反光
        elif p.dl.style_meta.get("layout") == "pouch":  # 软袋：装满鼓起（C5 第二步），另附平放
            from pacxio.mockup.pouch import LAID_VIEW, pouch_scene
            from pacxio.mockup.pouch_fill import filled_scene, pouch_fill
            from pacxio.styles.pouch import pouch_rules

            rules = pouch_rules(dielines.job_profile(job))
            fill = pouch_fill(job, rules)
            folded = filled_scene(p.dl, art, m, fill, rules, film=job.material.film)
            folded_rough = None  # 鼓起的软袋：纹理另有一套键，暂不画反光
            laid = pouch_scene(p.dl, art, m)
        if (
            folded is not None
        ):  # 按刀版真实折叠出图（深度缓冲，raster.py）：斜壁、翻盖、敞口托盘都按实际前后关系画
            views = {
                v.name: render_folded(*folded, v, VIEW_SIZE, folded_rough, reflect) for v in VIEWS
            }
        else:
            views = {
                v.name: render_view(box, textures, v, VIEW_SIZE, rough=box_rough, reflect=reflect)
                for v in VIEWS
            }
        if p.dl.style_meta.get("layout") == "pouch":
            views["open"] = render_folded(*laid, LAID_VIEW, VIEW_SIZE)
        if job.style in {"fefco_0427", "mailer_tuck", "mailer_front_lock", "mailer_tear_seal"}:
            from pacxio.mockup.folded_view import open_paper_view

            opened, open_tex = open_paper_view(p.dl, art, m)
            open_rough = _printed(open_paper_view(p.dl, rough_art, m)[1])
            views["open"] = render_folded(
                opened, open_tex, OPEN_VIEW, VIEW_SIZE, open_rough, reflect
            )
        content = collect_content(p.job, p.dl, box)
        layout_findings = check_t04(p.layout) + check_t06(p.layout) + layout_warnings(p.layout)
        dims = p.dl.style_meta["dims"]
        name = dielines.style_name(p.dl.style_meta, p.dl.style)
        material = p.job.material.preset or translate("Custom")
        if p.dl.style_meta.get("layout") == "label":
            subtitle = translate("{name} ({style})  flat {w} x {h} mm  material {material}; the bottle is for display only",
                                 name=name, style=p.dl.style, w=f"{p.dl.bbox[2]:.1f}", h=f"{p.dl.bbox[3]:.1f}", material=material)  # fmt: skip
        elif p.dl.style_meta.get("layout") == "pouch":
            subtitle = translate("{name} ({style})  pouch {w} x {h} mm  material {material}; filled shape approximated with a bulge of {fill} (initial value, not verified); bottom left is laid flat",
                                 name=name, style=p.dl.style, w=f"{dims['width']:g}", h=f"{dims['height']:g}", material=material, fill=f"{fill:g}")  # fmt: skip
        elif p.dl.style_meta.get("layout") == "flat":
            subtitle = translate("{name} ({style})  flat {w} x {h} mm  caliper {caliper} mm; H is a compatibility placeholder",
                                 name=name, style=p.dl.style, w=f"{p.dl.bbox[2]:g}", h=f"{p.dl.bbox[3]:g}", caliper=f"{p.dl.caliper_mm:g}")  # fmt: skip
        else:
            subtitle = translate(
                "{name} ({style})  inner size {l} x {w} x {h} mm  material {material}  flat {fw} x {fh} mm",
                name=name, style=p.dl.style.upper(), l=f"{dims['Li']:g}", w=f"{dims['Wi']:g}", h=f"{dims['Hi']:g}",
                material=material, fw=f"{p.dl.bbox[2]:g}", fh=f"{p.dl.bbox[3]:g}",
            )  # fmt: skip
        # 标签没有顶 / 底面、不折叠：MK03（顶 / 底面方向待打样）不适用
        orientation_ok = bool(p.dl.style_meta.get("orientation_verified")) or (
            p.dl.style_meta.get("layout") in ("label", "pouch")
        )
    findings, missing = check_content(
        box,
        content,
        required,
        orientation_ok,
        job,
        confirmation_status(directory, job),
        icc_name(parts[0].layout.canvas.icc_path),
    )
    findings += layout_findings
    if dset.multipart and job.style != "structure_set":
        findings.append(
            Finding(
                id="MK14",
                level="info",
                message=translate("The lid can go on turned 180° and the insert can go in back to front: take care "
                                  "when artwork on the front and back of the lid and base (insert and sleeve) must line up"),
                fix=translate("Put artwork that must line up (such as the front logo) on the same side of both parts, "
                              "or design it so it still works when turned round"),
            )
        )

    chosen = read_chosen(directory)
    concept_img = None
    if chosen is not None and (directory / chosen.file).is_file():
        concept_img = Image.open(directory / chosen.file).convert("RGB")
    # 背面 + 左侧视角（v2.1 第 4 步）：和效果图的同角度视图并排
    back = back_view_for(directory, chosen.id if chosen else None)
    back_below = bool(
        back and any(f.split(".")[-1] in ("bottom", "base", "btm_lid") for f in back.faces or [])
    )
    if back_below:  # 扁平盒从底下看的背面视角：出一张同角度的（抽屉测试 F-23）
        views[BELOW_BACK_VIEW.name] = (
            render_folded(*folded, BELOW_BACK_VIEW, VIEW_SIZE, folded_rough, reflect)
            if folded is not None
            else render_view(
                box, textures, BELOW_BACK_VIEW, VIEW_SIZE, rough=box_rough, reflect=reflect
            )
        )
    sheet = build_sheet(
        translate("Static finish illustration  {job} (fixed highlights; see the workbench 3D for the interactive view)", job=job.job_id),
        subtitle,
        box,
        views,
        content,
        findings,
        concept=concept_img,
        concept_label=translate("Chosen concept image {id}", id=chosen.id) if chosen else None,
        back_concept=Image.open(directory / back.file).convert("RGB") if back else None,
        back_label=translate("Back view concept image {id}", id=back.id) if back else None,
        back_below=back_below,
        open_label=translate("Open (mailer box; inside of the lid is unprinted board)")
        if job.style in {"fefco_0427", "mailer_tuck", "mailer_front_lock", "mailer_tear_seal"}
        else (display_name(LAID_VIEW.label) if parts[0].dl.style_meta.get("layout") == "pouch" else None),
    )

    out = directory / MOCKUP_DIR
    out.mkdir(exist_ok=True)
    for old in [*out.glob("*.png"), out / MANIFEST]:
        old.unlink(
            missing_ok=True
        )  # 清掉旧效果图（确认记录 approval.json 保留，靠指纹判断是否过期）
    files: list[Path] = []
    for name, view_img in views.items():
        files.append(out / VIEW_FILE.format(name=name))
        view_img.save(files[-1])
    for key, tex in textures.items():
        files.append(out / FACE_FILE.format(key=key))
        tex.save(files[-1])
    files.append(out / SHEET_NAME)
    sheet.save(files[-1])

    fingerprint = design_fingerprint(directory, job)
    # 工作台的可转动 3D：有折法的盒型按刀版折（场景 v3），其余仍是贴图方盒子（v2）
    from pacxio.mockup.foldscene import foldable, write_fold_scene

    if parts[0].dl.style_meta.get("layout") == "label":  # 标签：瓶 / 罐 + 贴好的标签（粗网格）
        from pacxio.mockup.container import SCENE_SEGMENTS, container_scene

        art_s, m_s = render_art(parts[0].layout, parts[0].dl)
        files += write_scene(
            out,
            {"closed": container_scene(parts[0].dl, art_s, m_s, job.container, SCENE_SEGMENTS)},
            fingerprint,
        )
    elif parts[0].dl.style_meta.get("layout") == "pouch":  # 装满（默认）/ 平放两种状态
        from pacxio.mockup.pouch import pouch_scene, window_art
        from pacxio.mockup.pouch_fill import SCENE_GRID, filled_scene, pouch_fill
        from pacxio.styles.pouch import pouch_rules

        art_s, m_s = render_art(parts[0].layout, parts[0].dl)
        art_s = window_art(art_s, m_s, parts[0].dl, job.pouch.contents if job.pouch else None)
        rules = pouch_rules(dielines.job_profile(job))
        filled = filled_scene(parts[0].dl, art_s, m_s, pouch_fill(job, rules), rules, SCENE_GRID,
                              bake_shading=False, film=job.material.film)  # fmt: skip
        files += write_scene(
            out, {"filled": filled, "laid": pouch_scene(parts[0].dl, art_s, m_s)}, fingerprint
        )
    elif foldable(parts):
        files += write_fold_scene(out, job, parts, fingerprint)
    else:
        from pacxio.mockup.materials import material_states

        material_scenes, maps, regions = material_states(job, parts)
        files += write_scene(out, material_scenes, fingerprint, maps, regions)
    # The ordinary scene remains usable if enhanced preparation is unsupported or fails.
    from pacxio.mockup.display.service import prepare_display

    display_status, display_files = prepare_display(out / "scene.json", job.style)
    files += display_files
    manifest = {
        "display_renderer": display_status,
        "job_id": job.job_id,
        "fingerprint": fingerprint,
        "style_version": style_version(job),
        "static_renderer": STATIC_RENDERER if folded is not None else "box",
        "created": now_iso(),
        "style": job.style,
        "views": list(views),
        "sheet": SHEET_NAME,
        "required": required,
        "missing_required": missing,
        "tags": content.tags,
        "faces": {k: [i.__dict__ for i in v] for k, v in content.by_face.items()},
        "hidden": [i.__dict__ for i in content.hidden],
        "orientation_verified": orientation_ok,
        "findings": [f.model_dump(exclude_none=True) for f in findings],
        "concept": {"id": chosen.id, "sha256": chosen.sha256} if chosen else None,
        "concept_back": {"id": back.id, "sha256": back.sha256} if back else None,
    }
    mf = out / MANIFEST
    mf.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    files.append(mf)

    r.artifacts = [str(f) for f in files]
    r.data = {
        "display_renderer": display_status,
        "sheet": str(out / SHEET_NAME),
        "fingerprint": fingerprint,
        "tags": content.tags,
        "missing_required": missing,
        "faces_with_content": [k for k, v in content.by_face.items() if v],
        "orientation_verified": orientation_ok,
        "concept": chosen.id if chosen else None,
        "concept_back": back.id if back else None,
    }
    if chosen is None:
        r.warnings.append(
            Issue(
                id="MK07",
                message=translate("No concept has been chosen. The mockup sheet has no design reference."),
                fix=translate("Have the decision-maker select a direction (`concept gen` / `concept import` → `concept choose`)"),
            )
        )
    for f in findings:
        if f.id in {"MK04", "MK01"}:
            continue  # MK04 每次都有，确认页上写了；MK01 逐条在确认页上，命令行里合并成一条
        r.warnings.append(_issue(f))
    if missing:
        r.warnings.append(
            Issue(
                id="MK01",
                message=translate("Required content is missing: {items}. Add it before approval.", items=separator.join(missing)),
                fix=_missing_fix(required, missing),
            )
        )
    note = open_notes_issue(directory)
    if note is not None:
        r.warnings.append(note)
    return r


def approve_design(
    directory: Path, by: str | None = None, note: str | None = None, status_only: bool = False
) -> Result:
    """确认刀版确认图（或只查看确认状态）：导出前唯一的闸门。

    设计在 render 之后改过、或必须出现的信息没齐，都不能确认。3D 效果图只是看最终效果，不挡确认。"""
    from pacxio import dielines

    r = Result(command="approve", job=str(directory))
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    state = approval_state(directory, job)
    r.data = {"status": state.status, "fingerprint": state.current}
    if state.approval:
        r.data["approved_at"] = state.approval.approved_at
        r.data["by"] = state.approval.by
    if status_only:
        return r
    if state.status == "none":
        return r.fail(
            Issue(
                id="APPROVE",
                message=translate("There is no dieline proof yet"),
                fix=translate("Run `pacxio render <job>` and ask the user to review the proof before approving"),
            ),
            2,
        )
    if state.status == "stale":
        return r.fail(
            Issue(
                id="APPROVE",
                message=translate("The design changed after the dieline proof was rendered. The proof is stale."),
                fix=translate("Rerun `pacxio render <job>` and ask the user to review the new proof before approving"),
            ),
            1,
        )
    if state.status == "approved":
        r.data["already"] = True
        return r
    required = (read_manifest(directory) or {}).get("required") or []  # mockup --require 声明过的
    missing = blocking_content(job, confirmation_status(directory, job), required)
    if missing:
        separator = "、" if lang() == "zh" else ", "
        return r.fail(
            Issue(
                id="MK01",
                message=translate("Approval is blocked by missing required content or placeholders: {items}", items=separator.join(missing)),
                fix=translate("Add these items with `art add --content` for checklist items or `--tag` for other items. Finalize pending wording and rerun `render`."),
            ),
            1,
        )
    dset, _ = dielines.build_parts_for(job)
    orientation_ok = bool(
        dset and all(d.style_meta.get("orientation_verified") for d in dset.parts.values())
    )
    acknowledged = [] if orientation_ok else ["MK03"]  # 盖的正立方向还没经打样确认
    chosen = read_chosen(directory)
    a = make_approval(state.current, by, note, orientation_ok, acknowledged,
                      chosen.id if chosen else None, style_version(job))  # fmt: skip
    path = write_approval(directory, a)
    r.artifacts = [str(path)]
    r.data.update(
        {
            "status": "approved",
            "approved_at": a.approved_at,
            "acknowledged": acknowledged,
            "concept": a.concept_id,
        }
    )
    for code in acknowledged:
        r.warnings.append(
            Issue(
                id=code,
                message=translate("The top and bottom artwork orientation has yet to be checked on a physical proof. This is recorded in the approval."),
                fix=translate("Fold a sample box to check orientation before production"),
            )
        )
    return r


__all__ = ["APPROVAL", "SHEET_NAME", "approve_design", "make_mockup"]
