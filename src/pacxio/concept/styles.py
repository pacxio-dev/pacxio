"""包装概念图视觉风格读取与软融入埋点（纯数据驱动，零硬编码风格列表）。

风格内容与持续更新全部托管在官网独立页（`https://pacxio.dev/styles`，静态目录
`https://pacxio.dev/styles/catalog.json`）；引擎只负责按契约读取、缓存、筛选、
软融入主视角提示词，以及在选定概念后按缓存提供可选的重建参考。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pacxio.cloud.styles import StylesFetchError, cached_catalog, fetch_catalog
from pacxio.messages import lang, t
from pacxio.model.common import Issue, Result
from pacxio.model.job import Job, load_job_checked

RESTORE_LABELS: dict[str, str] = {
    "vector_native": "Vector-native (high print fidelity)",
    "hybrid_photo": "Hybrid (separate 300 ppi photo + vector layout)",
    "illustrated_bitmap": "Illustrated (300 ppi text-free art + vector copy)",
}


class _Open(BaseModel):
    """向前兼容模型：官网后续新增任意展示字段（样张图、标签、季节等）老版本客户端均平滑忽略。"""

    model_config = ConfigDict(extra="ignore")


class StyleGroup(_Open):
    id: str
    name: str
    summary: str = ""
    i18n: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StyleCategory(_Open):
    id: str
    name: str
    default_pair: list[str] = Field(default_factory=list)
    recommended: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    shelf_focus: str = ""
    prompt_note: str = ""
    i18n: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Printability(_Open):
    restore_mode: str = "vector_native"
    vector_friendly: bool = True
    notes: str = ""
    font_pairing_hint: str = ""
    recommended_papers: list[str] = Field(default_factory=list)
    recommended_finishes: list[str] = Field(default_factory=list)


class VisualStyle(_Open):
    id: str
    name: str
    tagline: str = ""
    group: str = ""
    best_for: list[str] = Field(default_factory=list)
    pairs_well_with: list[str] = Field(default_factory=list)
    aesthetic_summary: str = ""
    palette_philosophy: str = ""
    typography_mood: str = ""
    visual_language: str = ""
    avoid: str = ""
    printability: Printability = Field(default_factory=Printability)
    prompt_direction: str = ""
    prompt_avoid: str = ""
    prompt_scene: str = ""
    i18n: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StyleCatalog(_Open):
    schema_version: int = 1
    version: str = "1"
    default_pair: list[str] = Field(default_factory=list)
    groups: dict[str, StyleGroup] = Field(default_factory=dict)
    categories: dict[str, StyleCategory] = Field(default_factory=dict)
    styles: dict[str, VisualStyle] = Field(default_factory=dict)


def _as_dict_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [{"id": k, **v} for k, v in raw.items() if isinstance(v, dict)]
    return []


def parse_catalog(raw: dict[str, Any]) -> StyleCatalog:
    """将官网 catalog.json 解析为 StyleCatalog（容忍官网未来新增字段，不因扩展字段报错）。"""
    groups = {
        g.id: g for item in _as_dict_items(raw.get("groups")) for g in (StyleGroup.model_validate(item),)
    }
    categories = {
        c.id: c
        for item in _as_dict_items(raw.get("categories"))
        for c in (StyleCategory.model_validate(item),)
    }
    styles = {
        s.id: s for item in _as_dict_items(raw.get("styles")) for s in (VisualStyle.model_validate(item),)
    }
    return StyleCatalog(
        schema_version=int(raw.get("schema_version") or 1),
        version=str(raw.get("version") or "1"),
        default_pair=[str(x) for x in (raw.get("default_pair") or []) if str(x) in styles],
        groups=groups,
        categories=categories,
        styles=styles,
    )


def load_catalog(refresh: bool = False) -> tuple[StyleCatalog, str, str | None]:
    """从官网或本地源/缓存加载目录，返回 `(catalog, source, stale_warning)`。"""
    raw, source, stale = fetch_catalog(refresh=refresh)
    try:
        cat = parse_catalog(raw)
    except (ValidationError, ValueError, TypeError) as e:
        raise StylesFetchError(
            "STYLES_BAD",
            t("Visual style catalog at {source} has an invalid structure: {error}", source=source, error=e),
            t("Check the catalog JSON schema or pass --design directly without --style"),
        ) from e
    return cat, source, stale


def load_cached_catalog() -> StyleCatalog | None:
    """只读本地源或已有缓存，绝不发起联网请求。"""
    raw = cached_catalog()
    if raw is None:
        return None
    try:
        return parse_catalog(raw)
    except (ValidationError, ValueError, TypeError):
        return None


def _i18n_str(i18n: dict[str, dict[str, Any]], key: str, fallback: str) -> str:
    block = i18n.get(lang())
    if isinstance(block, dict):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return fallback


def _i18n_printability_str(style: VisualStyle, key: str, fallback: str) -> str:
    block = style.i18n.get(lang())
    if isinstance(block, dict):
        sub = block.get("printability")
        if isinstance(sub, dict):
            val = sub.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return fallback


def _norm_id(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def get_visual_style(style_id: str, catalog: StyleCatalog) -> VisualStyle | None:
    return catalog.styles.get(_norm_id(style_id))


def resolve_category(query: str, catalog: StyleCatalog) -> StyleCategory | None:
    """按品类 ID、名称或目录自带的多语言关键词匹配品类（零硬编码关键词）。"""
    q = query.strip().lower()
    if not q:
        return None
    key = _norm_id(query)
    if key in catalog.categories:
        return catalog.categories[key]
    for item in catalog.categories.values():
        names = [item.name.lower()]
        for loc in item.i18n.values():
            if isinstance(loc, dict) and isinstance(loc.get("name"), str):
                names.append(loc["name"].lower())
        if q in names:
            return item
    scored: list[tuple[int, StyleCategory]] = []
    for item in catalog.categories.values():
        kws = [k.lower() for k in item.keywords]
        for loc in item.i18n.values():
            if isinstance(loc, dict) and isinstance(loc.get("keywords"), list):
                kws.extend(str(k).lower() for k in loc["keywords"] if str(k).strip())
        hits = sum(1 for k in kws if k and (k in q or q in k))
        if hits:
            scored.append((hits, item))
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return scored[0][1]


def _paper_score(style: VisualStyle, job: Job | None) -> int:
    if job is None:
        return 0
    recs = style.printability.recommended_papers
    preset = job.material.preset
    if preset and preset in recs:
        return 2
    from pacxio.paper import display_paper_face

    face = display_paper_face(job.material)
    if face is None:
        return 0
    if face.finish in ("metallic", "holographic") and any(
        p in recs for p in ("silver_card_350", "gold_card_350", "holo_card_350")
    ):
        return 1
    if face.finish == "pearl" and "pearl_card_300" in recs:
        return 1
    if face.finish == "textured" and "textured_300" in recs:
        return 1
    if face.white_needed() and "black_card_350" in recs:
        return 1
    return 0


def paper_fit_warning(style: VisualStyle, job: Job) -> Issue | None:
    """纯数据驱动的纸面气质柔和提醒（零硬编码风格 ID，不拦截用户决定）。"""
    from pacxio.paper import display_paper_face

    face = display_paper_face(job.material)
    if face is None or _paper_score(style, job) > 0:
        return None
    recs = style.printability.recommended_papers
    if not recs:
        return None
    if face.finish in ("metallic", "holographic") and style.printability.restore_mode == "illustrated_bitmap":
        return Issue(
            id="STYLE_FIT",
            message=t(
                "Visual style {style} usually pairs with stocks like {papers}, while this job uses a metallic/holographic substrate",
                style=style.id,
                papers=", ".join(recs),
            ),
            fix=t(
                "Proceed if you want an intentional contrast, or run `pacxio concept styles --job <job>` to see styles that pair naturally with this paper"
            ),
        )
    return None


def resolve_style_tag(
    visual_style: str | None, category: str | None
) -> tuple[str | None, str | None, Issue | None]:
    """导入概念图时记录可选风格/品类标签：不强制联网；若本地已有缓存则校验并规范化。"""
    if visual_style is None and category is None:
        return None, None, None
    cat = load_cached_catalog()
    style_id = _norm_id(visual_style) if visual_style else None
    cat_id = _norm_id(category) if category else None
    if cat is None:
        return style_id, cat_id, None
    if style_id is not None:
        found = get_visual_style(style_id, cat)
        if found is None:
            return (
                None,
                None,
                Issue(
                    id="ARG",
                    message=t("Unknown visual style {style}", style=visual_style or style_id),
                    fix=t(
                        "Available visual styles: {styles} (run `pacxio concept styles --refresh` to update from pacxio.dev)",
                        styles=", ".join(cat.styles),
                    ),
                ),
            )
        style_id = found.id
    if category is not None:
        found_cat = resolve_category(category, cat)
        if found_cat is None:
            return (
                None,
                None,
                Issue(
                    id="ARG",
                    message=t("Unknown category {category}", category=category),
                    fix=t("Available categories: {categories}", categories=", ".join(cat.categories)),
                ),
            )
        cat_id = found_cat.id
    return style_id, cat_id, None


def compose_design(
    design: str | None,
    scene: str | None,
    style_id: str | None,
    category_query: str | None = None,
) -> tuple[str | None, str | None, VisualStyle | None, StyleCategory | None, Issue | None, str | None]:
    """把用户的 --design 与官网视觉风格方向软融合。

    仅在传了 --style 或 --category 时读取风格目录；纯 --design 用法零联网、零额外开销。
    返回 `(effective_design, effective_scene, style, category, error_issue, stale_warning)`。
    """
    user_design = (design or "").strip()
    if style_id is None and category_query is None:
        if not user_design:
            return (
                None,
                None,
                None,
                None,
                Issue(
                    id="ARG",
                    message=t("Provide --design or --style for the main-view concept"),
                    fix=t(
                        'Describe style, colors, graphics and layout in --design (or pass --style <id> from pacxio.dev/styles)'
                    ),
                ),
                None,
            )
        return user_design, scene, None, None, None, None

    try:
        cat, _, stale = load_catalog()
    except StylesFetchError as e:
        return None, None, None, None, Issue(id=e.id, message=e.message, fix=e.fix), None

    style: VisualStyle | None = None
    category: StyleCategory | None = None
    if style_id is not None:
        style = get_visual_style(style_id, cat)
        if style is None:
            return (
                None,
                None,
                None,
                None,
                Issue(
                    id="ARG",
                    message=t("Unknown visual style {style}", style=style_id),
                    fix=t(
                        "Available visual styles: {styles} (run `pacxio concept styles --refresh` to update from pacxio.dev)",
                        styles=", ".join(cat.styles),
                    ),
                ),
                stale,
            )
    if category_query is not None:
        category = resolve_category(category_query, cat)
        if category is None:
            return (
                None,
                None,
                None,
                None,
                Issue(
                    id="ARG",
                    message=t("Unknown category {category}", category=category_query),
                    fix=t("Available categories: {categories}", categories=", ".join(cat.categories)),
                ),
                stale,
            )
    if style is None and not user_design:
        return (
            None,
            None,
            None,
            None,
            Issue(
                id="ARG",
                message=t("Provide --design or --style for the main-view concept"),
                fix=t(
                    'Describe style, colors, graphics and layout in --design (or pass --style <id> from pacxio.dev/styles)'
                ),
            ),
            stale,
        )

    parts: list[str] = []
    if user_design:
        parts.append(user_design.rstrip(".") + ".")
    if style is not None:
        direction = style.prompt_direction or style.aesthetic_summary or style.tagline
        lead = (
            f"Visual style direction ({style.name}; interpret freely for this specific product and brand "
            "rather than copying a fixed layout template):"
            if user_design
            else f"Visual style direction ({style.name}; create an original, product-specific design in this "
            "aesthetic rather than a rigid template):"
        )
        parts.append(f"{lead} {direction}".strip())
    if category is not None and category.prompt_note:
        parts.append(category.prompt_note)
    if style is not None and style.prompt_avoid:
        parts.append(style.prompt_avoid)
    effective_design = " ".join(parts)
    effective_scene = scene if scene else (style.prompt_scene or None if style is not None else None)
    return effective_design, effective_scene, style, category, None, stale


def _restore_label(mode: str) -> str:
    return t(RESTORE_LABELS[mode]) if mode in RESTORE_LABELS else mode


def starter_prompt(style: VisualStyle) -> str:
    return t(
        'Use Pacxio visual style "{id}" ({name}) as the aesthetic direction (adapt freely to my brand rather than using a rigid template) to design packaging and generate main-view concepts.',
        id=style.id,
        name=_i18n_str(style.i18n, "name", style.name),
    )


def style_summary_row(style: VisualStyle, cat: StyleCatalog) -> dict[str, Any]:
    grp = cat.groups.get(style.group)
    grp_name = _i18n_str(grp.i18n, "name", grp.name) if grp is not None else style.group
    return {
        "id": style.id,
        "name": _i18n_str(style.i18n, "name", style.name),
        "name_en": style.name,
        "group": style.group,
        "group_name": grp_name,
        "tagline": _i18n_str(style.i18n, "tagline", style.tagline),
        "best_for": list(style.best_for),
        "pairs_well_with": list(style.pairs_well_with),
        "restore_mode": style.printability.restore_mode,
        "restore_label": _restore_label(style.printability.restore_mode),
        "vector_friendly": style.printability.vector_friendly,
        "recommended_papers": list(style.printability.recommended_papers),
        "recommended_finishes": list(style.printability.recommended_finishes),
    }


def style_detail_row(style: VisualStyle, cat: StyleCatalog) -> dict[str, Any]:
    row = style_summary_row(style, cat)
    row.update(
        {
            "aesthetic_summary": _i18n_str(style.i18n, "aesthetic_summary", style.aesthetic_summary),
            "palette_philosophy": _i18n_str(style.i18n, "palette_philosophy", style.palette_philosophy),
            "typography_mood": _i18n_str(style.i18n, "typography_mood", style.typography_mood),
            "visual_language": _i18n_str(style.i18n, "visual_language", style.visual_language),
            "avoid": _i18n_str(style.i18n, "avoid", style.avoid),
            "printability": {
                "restore_mode": style.printability.restore_mode,
                "restore_label": _restore_label(style.printability.restore_mode),
                "vector_friendly": style.printability.vector_friendly,
                "notes": _i18n_printability_str(style, "notes", style.printability.notes),
                "font_pairing_hint": _i18n_printability_str(
                    style, "font_pairing_hint", style.printability.font_pairing_hint
                ),
                "recommended_papers": list(style.printability.recommended_papers),
                "recommended_finishes": list(style.printability.recommended_finishes),
            },
            "starter_prompt": starter_prompt(style),
            "cli_example": f'pacxio concept prompt <job> --step main --style {style.id} --name main-A --text "Brand"',
        }
    )
    return row


def rebuild_hints(style_id: str | None) -> dict[str, Any] | None:
    """选定方向后返回给正式稿重建的软参考（仅查本地源或本地缓存，不触发联网）。"""
    if not style_id:
        return None
    cat = load_cached_catalog()
    if cat is None:
        return None
    style = get_visual_style(style_id, cat)
    if style is None:
        return None
    return {
        "style": style.id,
        "name": _i18n_str(style.i18n, "name", style.name),
        "restore_mode": style.printability.restore_mode,
        "restore_label": _restore_label(style.printability.restore_mode),
        "vector_friendly": style.printability.vector_friendly,
        "font_pairing_hint": _i18n_printability_str(
            style, "font_pairing_hint", style.printability.font_pairing_hint
        ),
        "recommended_papers": list(style.printability.recommended_papers),
        "recommended_finishes": list(style.printability.recommended_finishes),
        "notes": _i18n_printability_str(style, "notes", style.printability.notes),
    }


def _pick_pair(
    cat: StyleCatalog,
    styles: list[VisualStyle],
    selected_cat: StyleCategory | None,
    job: Job | None,
) -> list[str]:
    if not styles:
        return []
    valid_ids = {s.id for s in styles}
    if selected_cat is not None:
        base = [sid for sid in selected_cat.default_pair if sid in cat.styles][:2]
        if job is not None and _paper_score(styles[0], job) > 0 and styles[0].id not in base:
            partner = next(
                (p for p in styles[0].pairs_well_with if p in valid_ids),
                base[0] if base else (styles[1].id if len(styles) > 1 else styles[0].id),
            )
            return [styles[0].id, partner] if partner != styles[0].id else [styles[0].id]
        if len(base) == 2:
            return base
    if job is not None and len(styles) >= 2:
        first = styles[0]
        second = next((p for p in first.pairs_well_with if p in cat.styles), styles[1].id)
        return [first.id, second] if second != first.id else [first.id]
    if len(cat.default_pair) >= 2:
        return cat.default_pair[:2]
    return [s.id for s in styles[:2]]


def list_visual_styles(
    style_id: str | None = None,
    category: str | None = None,
    job_dir: Path | None = None,
    full: bool = False,
    refresh: bool = False,
) -> Result:
    """从官网风格目录读取、按品类/纸面推荐 A/B 对撞方向，或查看单套风格总结。"""
    r = Result(command="concept styles", job=str(job_dir) if job_dir else None)
    try:
        cat, source, stale = load_catalog(refresh=refresh)
    except StylesFetchError as e:
        return r.fail(Issue(id=e.id, message=e.message, fix=e.fix), 3 if e.id == "STYLES_NET" else 2)
    if stale:
        r.warnings.append(
            Issue(
                id="STYLES_CACHE",
                message=stale,
                fix=t("Run `pacxio concept styles --refresh` once back online to fetch updates"),
            )
        )

    job: Job | None = None
    if job_dir is not None:
        job, errs = load_job_checked(job_dir)
        if job is None:
            r.errors += errs
            r.ok, r.exit_code = False, 2
            return r

    if style_id is not None:
        style = get_visual_style(style_id, cat)
        if style is None:
            return r.fail(
                Issue(
                    id="ARG",
                    message=t("Unknown visual style {style}", style=style_id),
                    fix=t(
                        "Available visual styles: {styles} (run `pacxio concept styles --refresh` to update from pacxio.dev)",
                        styles=", ".join(cat.styles),
                    ),
                ),
                2,
            )
        r.data = {"source": source, "version": cat.version, "style": style_detail_row(style, cat)}
        if job is not None:
            warn = paper_fit_warning(style, job)
            if warn is not None:
                r.warnings.append(warn)
        return r

    selected_cat: StyleCategory | None = None
    if category is not None:
        selected_cat = resolve_category(category, cat)
        if selected_cat is None:
            return r.fail(
                Issue(
                    id="ARG",
                    message=t("Unknown category {category}", category=category),
                    fix=t("Available categories: {categories}", categories=", ".join(cat.categories)),
                ),
                2,
            )

    if selected_cat is not None:
        rec = [sid for sid in selected_cat.recommended if sid in cat.styles]
        ids = rec + [sid for sid in cat.styles if sid not in rec] if full else rec
        styles = [cat.styles[sid] for sid in ids]
    else:
        styles = list(cat.styles.values())

    if job is not None:
        styles.sort(key=lambda s: (-_paper_score(s, job), s.id))

    recommended_pair = _pick_pair(cat, styles, selected_cat, job)
    rows = [style_detail_row(s, cat) if full else style_summary_row(s, cat) for s in styles]
    data: dict[str, Any] = {
        "source": source,
        "version": cat.version,
        "recommended_pair": recommended_pair,
        "styles": rows,
        "categories": [
            {
                "id": c.id,
                "name": _i18n_str(c.i18n, "name", c.name),
                "name_en": c.name,
                "default_pair": list(c.default_pair),
                "recommended": list(c.recommended),
                "shelf_focus": _i18n_str(c.i18n, "shelf_focus", c.shelf_focus),
            }
            for c in cat.categories.values()
        ],
    }
    if full:
        data["groups"] = [
            {
                "id": g.id,
                "name": _i18n_str(g.i18n, "name", g.name),
                "name_en": g.name,
                "summary": _i18n_str(g.i18n, "summary", g.summary),
            }
            for g in cat.groups.values()
        ]
    if selected_cat is not None:
        data["category"] = {
            "id": selected_cat.id,
            "name": _i18n_str(selected_cat.i18n, "name", selected_cat.name),
            "default_pair": list(selected_cat.default_pair),
            "shelf_focus": _i18n_str(selected_cat.i18n, "shelf_focus", selected_cat.shelf_focus),
        }
    if job is not None:
        data["job_material"] = job.material.preset or job.material.type
    r.data = data
    return r


__all__ = [
    "Printability",
    "StyleCatalog",
    "StyleCategory",
    "StyleGroup",
    "VisualStyle",
    "compose_design",
    "get_visual_style",
    "list_visual_styles",
    "load_cached_catalog",
    "load_catalog",
    "paper_fit_warning",
    "parse_catalog",
    "rebuild_hints",
    "resolve_category",
    "resolve_style_tag",
    "starter_prompt",
]
