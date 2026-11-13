"""结构目录（几何引擎第 5 步 ②）：FEFCO 325 款 + ECMA 公开 130 款，每款记上 agent 选结构要用的信息。

每款：编号、系列、一句话说明、做法（筒 / 托盘 / 片材 / 多件 / 多边形筒）、材料、**成熟度**（已校准 = 走校准过的
做法、对过参考刀版；标准款 = 按目录示意图 / 公开资料推读、数值【初值】）、怎么合上、能不能反复开合、开口那头
要不要胶带 / 胶、用途标签。这些都从配方推出来，不另写一份表（配方改了跟着变）。

选结构的顺序（GEOMETRY_ENGINE.md §1.1）：先在目录里找（已校准的排前面），目录都不满足再和用户共创。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import cache
from typing import Any

from pacxio.engine import combos
from pacxio.messages import lang, t

SERIES_ZH = {
    "C": "常用组合",
    "01": "片材 / 卷材",
    "02": "开槽箱",
    "03": "套盖（天地盖、盖帽）",
    "04": "折叠盒 / 托盘",
    "05": "套盒（套筒、交叉套）",
    "06": "三片式（盒身 + 端板）",
    "07": "预糊箱（自动锁底、预糊托盘）",
    "08": "展示（陈列箱、展架、头卡）",
    "09": "内衬（隔板、内托、护角）",
    "A": "彩盒（ECMA A 类，长方筒 + 侧糊口）",
}
REF_SIZE = (300.0, 200.0, 150.0)  # 推特征用的参考尺寸（只看结构，不看尺寸）


@dataclass(frozen=True)
class Entry:
    code: str
    catalog: str  # FEFCO / ECMA
    series: str
    series_zh: str
    description: str
    kind: str  # tube 筒 / tray 托盘 / sheet 片材 / set 多件 / native 多边形筒
    material: str  # corrugated / folding_carton
    maturity: str  # calibrated / standard
    closure: str  # 怎么合上（开口那头 / 底）
    reclosable: bool | None  # 能不能反复开合（None = 开顶 / 不适用）
    no_tape: bool  # 开口那头和底都不用胶带 / 胶就能合上（锁底、插舌、套合……）
    tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["tags"] = list(self.tags)
        out["maturity_zh"] = {
            "calibrated": "已校准",
            "custom": "自定义（常用组合，按实物 / 描述推的比例，数值【初值】）",
        }.get(self.maturity, "标准款（按示意图推读，数值【初值】）")
        return out


# 封口 → (说法, 能否反复开合, 是否不用胶带)
def _end(end: dict[str, Any]) -> tuple[str, bool | None, bool]:
    kind = end.get("kind", "open")
    flap = end.get("flap", "half")
    if kind == "open":
        return "开口", None, True
    if kind == "flaps":
        if flap in ("slot_lock", "tongue_slot", "tongue_notch"):
            return "自锁底（摇盖互扣）", False, True
        if end.get("lock"):
            return "锁舌摇盖", True, True
        if flap.startswith("rim"):
            return "窄边框", None, True
        if flap in ("divider", "cells"):
            return "隔板摇盖（要胶带）", False, False
        return "摇盖（胶带 / 胶 / 钉）", False, False
    if kind == "lid_tuck":
        if end.get("tear_strip"):
            return "插舌盖 + 撕拉条（一次性开箱）", False, True
        return "插舌盖", True, True
    if kind == "hanger":
        return "挂板 + 插舌盖", True, True
    if kind == "side_tuck":
        return "侧插盖", True, True
    if kind == "reduced":
        return "缩小翼（粘）", False, False
    if kind == "seal":
        return "粘合封口", False, False
    if kind in ("snaplock", "autolock"):
        return (
            ("扣底（免胶带）" if kind == "snaplock" else "自动锁底（撑开成型、免胶带）"),
            None,
            True,
        )
    if kind == "gable":
        return "提手山形顶", True, True
    if kind == "handle":
        return "提手平顶", True, True
    if kind in ("hang_tab", "hang_mid", "display"):
        return "挂板 / 展示板 + 盖", True, True
    if kind == "slope":
        return "斜口展示（开顶）", None, True
    if kind == "mailer":
        return "飞机盒连盖（插舌 + 耳朵）", True, True
    if kind == "hinged_lid":
        front = end.get("lid_front", "tuck")
        text = {
            "lip": "连盖（前翼盖在外面）",
            "return": "连盖（回折内衬 + 锁舌）",
            "lock": "连盖（锁舌插槽）",
            "none": "连盖（远边平）",
        }.get(front, "连盖插舌")
        if end.get("dust_lock"):
            text += " + 两侧翼锁舌扣底（免胶）"
        if end.get("tear_window"):
            text += " + 齿线撕开窗"
        if end.get("display_lid", "none") != "none":
            text += " + 展示盖压外面（胶条粘住，第一次撕开后立起当头卡）"
        return text, True, True
    if kind == "lid_glue":
        return "盖 + 粘翼", False, False
    return kind, None, False


def _corners_zh(body: dict[str, Any]) -> tuple[str, bool]:
    c = body.get("corners", "glue")
    return {
        "glue": ("角翼（粘）", False),
        "flap": ("角翼（粘 / 钉）", False),
        "lock": ("扣舌墙角（免胶）", True),
        "gusset": ("连角（预糊、压扁出厂）", True),
        "open": ("墙角不连（胶带 / 钉）", False),
        "ear": ("双层墙 + 耳朵（免胶）", True),
        "wrap": ("包裹式", True),
        "strip": ("长条包裹", True),
        "bevel": ("切角", False),
        "polygon": ("多边形托盘", False),
    }.get(c, (c, False))


def _tags(code: str, series: str, recipe: dict[str, Any], kind: str) -> list[str]:
    body = recipe.get("body", {})
    ends = [recipe.get("top", {}), recipe.get("bottom", {})]
    kinds = {e.get("kind") for e in ends}
    out: list[str] = []
    if series in ("02", "07") and kind == "tube":
        out.append("运输")
    if series == "08" or kinds & {"slope", "display"} or body.get("tear") or body.get("front_low"):
        out.append("展示")
    if series == "09" or body.get("inside") or kind == "sheet" and series != "01":
        out.append("内衬")
    if (
        kinds & {"mailer"}
        or any(e.get("tear_strip") for e in ends)
        or code in ("0427", "0427.1", "0885")
    ):
        out.append("电商")
    if kinds & {"gable", "handle"} or body.get("hand_holes"):
        out.append("提手")
    if body.get("window") or any(str(e.get("flap", "")).startswith("rim") for e in ends):
        out.append("开窗")
    if kinds & {"hanger", "hang_tab", "hang_mid"}:
        out.append("挂孔")
    if body.get("holes") or body.get("base_holes") or body.get("cup_holes"):
        out.append("开孔")
    if series == "A":
        out.append("彩盒")
    return out


def features(
    recipe: dict[str, Any], code: str = "", series: str = ""
) -> tuple[str, str, bool | None, bool, list[str]]:
    """一份单件配方 → (做法, 怎么合上, 能否反复开合, 是否免胶带, 用途标签)。目录和实用性检查共用。"""
    body = recipe.get("body", {})
    form = body.get("form", "tube")
    kind = "sheet" if form == "sheet" else "tray" if form == "tray" else (
        "native" if body.get("base") in ("regular", "octagon") else "tube")  # fmt: skip
    if kind == "sheet":
        closure, reclosable, no_tape = "片材（折叠 / 插接，不用封）", None, True
    elif kind == "tray" and recipe.get("top", {}).get("kind") == "mailer":
        closure, reclosable, no_tape = "飞机盒：双层侧墙 + 耳朵，连盖插舌（免胶）", True, True
    elif kind == "tray":
        corner, corner_free = _corners_zh(body)
        if recipe.get("top", {}).get("dust_lock") and body.get("corners", "glue") == "flap":
            corner, corner_free = "角翼（不糊，靠盖侧翼压住）", True  # 侧翼锁舌扣底：侧面不糊胶
        top_zh, reclosable, top_free = _end(recipe.get("top", {}))
        closure = f"{corner}；{'开顶' if top_zh == '开口' else top_zh}"
        no_tape = corner_free and top_free
    else:
        (tz, tr, tf), (bz, _, bf) = _end(recipe.get("top", {})), _end(recipe.get("bottom", {}))
        closure, reclosable, no_tape = f"顶：{tz}；底：{bz}", tr, tf and bf
    tags = _tags(code, series, recipe, kind)
    if no_tape and kind != "sheet":
        tags.append("免胶带")
    if reclosable:
        tags.append("反复开合")
    return kind, closure, reclosable, no_tape, tags


def _single(code: str, catalog: str, series: str, description: str, recipe: dict[str, Any],
            material: str) -> Entry:  # fmt: skip
    from pacxio.engine.calibrated import calibrated_params
    from pacxio.engine.recipe import StructureParams
    from pacxio.model.profile import resolve_material

    mat = resolve_material("flute_b" if material == "corrugated" else "sbs_350")
    try:
        calibrated = isinstance(calibrated_params(StructureParams(**recipe), mat), tuple)
    except Exception:  # noqa: BLE001 — 参考尺寸下配方不合（极少数），按标准款
        calibrated = False
    kind, closure, reclosable, no_tape, tags = features(recipe, code, series)
    return Entry(code, catalog, series, SERIES_ZH[series], description, kind, material,
                 "calibrated" if calibrated else "standard", closure, reclosable, no_tape, tuple(tags))  # fmt: skip


def _set_entry(code: str, series: str, description: str) -> Entry:
    from pacxio.engine import fefco

    kind_set, parts, fits, _ = fefco.SETS[code]
    sp = fefco.set_recipe(code, *REF_SIZE)
    reclosable = kind_set in ("telescope", "caps", "slide")
    closure = {"telescope": "套合（天地盖 / 两件套）", "caps": "两头盖帽套合", "slide": "套筒交叉 / 抽屉式",
               "kit": "组件（各件插接 / 装配）"}[kind_set]  # fmt: skip
    tags: list[str] = []
    for part in sp["parts"].values():
        tags += _tags(code, series, part, "set")
    if series == "06":
        tags.append("三片式")
    tags.append("多件")
    if reclosable:
        tags.append("反复开合")
    return Entry(code, "FEFCO", series, SERIES_ZH[series], description, "set", "corrugated", "standard",
                 closure, reclosable, kind_set != "kit", tuple(dict.fromkeys(tags)))  # fmt: skip


@cache
def entries() -> tuple[Entry, ...]:
    from pacxio.engine import ecma, fefco

    L, W, H = REF_SIZE
    out: list[Entry] = []
    desc: dict[str, str] = {}
    desc |= {c: d for c, d in fefco.SHEETS.items()}
    desc |= {c: v[2] for c, v in fefco.STYLES.items()}
    desc |= {c: v[1] for c, v in fefco.TRAYS.items()}
    desc |= {c: v[1] for c, v in fefco.SLEEVES.items()}
    desc |= {c: v[1] for c, v in fefco.MAILERS.items()}
    desc |= {c: v[3] for c, v in fefco.FOLDERS.items()}
    desc |= {c: v[1] for c, v in fefco.INSERTS.items()}
    desc |= {c: v[3] for c, v in fefco.NATIVE.items()}
    for code in sorted({*desc, *fefco.SETS}):
        series = code[:2]
        if code in fefco.SETS:
            out.append(_set_entry(code, series, fefco.SETS[code][3]))
            continue
        out.append(
            _single(code, "FEFCO", series, desc[code], fefco.recipe(code, L, W, H), "corrugated")
        )
    for code in ecma.PUBLIC_A:
        b, t, bp, tp = ecma.parse(code)
        text = f"顶：{ecma.CLOSURE_ZH[t]}，底：{ecma.CLOSURE_ZH[b]}"
        out.append(
            _single(code, "ECMA", "A", text, ecma.recipe(code, 80, 50, 120), "folding_carton")
        )
    out += [_combo_entry(c) for c in combos.COMBOS.values()]
    return tuple(out)


def _combo_entry(c: Any) -> Entry:
    """常用组合进目录：成熟度“自定义”，关键词并进用途标签（描述里的词能搜到）。"""
    kind, closure, reclosable, no_tape, tags = features(
        combos.recipe(c.code, *c.ref_size), c.code, "C"
    )
    tags = list(dict.fromkeys([*tags, *c.keywords, "常用组合"]))
    return Entry(c.code, "组合", "C", SERIES_ZH["C"], f"{c.name}：{c.description}", kind, c.material, "custom",
                 closure, reclosable, no_tape, tuple(tags))  # fmt: skip


def default_material(code: str) -> str:
    """编号 → 默认材料预设：瓦楞款 flute_b，彩盒款（ECMA、彩盒组合）sbs_350。"""
    e = get(code)
    if e is not None:
        return "flute_b" if e.material == "corrugated" else "sbs_350"
    return "sbs_350" if code.strip().upper().startswith("A") else "flute_b"


def get(code: str) -> Entry | None:
    key = code.strip().upper()
    return next((e for e in entries() if e.code.upper() == key), None)


@cache
def _search_text(e: Entry) -> str:
    """关键词搜索用的文字：中文说明 + 英文说明（英文用户也能按词搜），小写。"""
    from pacxio.engine import i18n

    en = i18n.entry_dict(e, "en")
    words = [e.code, e.series_zh, e.description, e.closure, *e.tags]
    words += [en["series_name"], en["description"], en["closure"], *en["tags"]]
    return " ".join(str(w) for w in words).lower()


def search(
    query: str | None = None,
    series: str | None = None,
    tags: list[str] | None = None,
    reclosable: bool | None = None,
    no_tape: bool | None = None,
    material: str | None = None,
    maturity: str | None = None,
    any_word: bool = False,
) -> list[Entry]:
    """按条件筛（都可不给）；已校准的排前面，同成熟度按编号。any_word：关键词命中一个就算（推荐用）。"""
    out = []
    for e in entries():
        text = _search_text(e)
        if query and not (any if any_word else all)(w.lower() in text for w in query.split()):
            continue
        if series and e.series != series.upper():
            continue
        if tags and not set(norm_tags(tags) or []) <= set(e.tags):
            continue
        if reclosable is not None and e.reclosable is not reclosable:
            continue
        if no_tape is not None and e.no_tape is not no_tape:
            continue
        if material and e.material != material:
            continue
        if maturity and e.maturity != maturity:
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e.maturity != "calibrated", e.catalog, e.code))


# 工厂最常做、最好报价的款（同成熟度里排前面；不是质量保证）
COMMON = ("0201", "0427", "0711", "0215", "0216", "0210", "0200", "0203", "0300", "0421", "0422", "0409",
          "0410", "0501", "0310", "0701", "0933", "0930", "0904", "0911", "0951",
          "A20.20.03.01", "A20.20.01.01", "A55.20.03.01", "A60.20.00.01")  # fmt: skip
KIND_ORDER = {"tube": 0, "tray": 0, "native": 1, "sheet": 2, "set": 3}
USES = ("运输", "电商", "展示", "内衬", "彩盒", "提手", "开窗", "挂孔", "开孔", "多件", "三片式")
# 英文用途 / 标签 → 目录里的标签（英文界面和英文 Skill 用这些写法）
USE_ALIASES = {
    "shipping": "运输", "ecommerce": "电商", "display": "展示", "insert": "内衬", "carton": "彩盒",
    "handle": "提手", "window": "开窗", "hang_hole": "挂孔", "hole": "开孔", "multi_part": "多件",
    "three_piece": "三片式", "no_tape": "免胶带", "reclosable": "反复开合",
}  # fmt: skip


def norm_tags(tags: list[str] | None) -> list[str] | None:
    """英文用途 / 标签换成目录标签；中文和不认识的原样留着。"""
    return [USE_ALIASES.get(t.strip().lower(), t) for t in tags] if tags else tags


def suggest(
    uses: list[str] | None = None,
    reclosable: bool | None = None,
    no_tape: bool | None = None,
    material: str | None = None,
    single: bool | None = None,
    query: str | None = None,
    limit: int = 3,
) -> list[tuple[Entry, list[str]]]:
    """按产品需求从目录挑方案（GEOMETRY_ENGINE.md §1.1 第 1 步）：硬条件筛掉不满足的，再按
    已校准 → 工厂常做 → 结构简单（单件优先）排序；每个方案附上为什么选它。目录都不满足返回空，再共创。"""
    from pacxio.engine import i18n

    uses = norm_tags(uses)
    pool = search(query=query, tags=uses or None, reclosable=reclosable, no_tape=no_tape, material=material,
                  any_word=True)  # fmt: skip
    if single is not None:
        pool = [e for e in pool if (e.kind != "set") is single]

    words = [w for w in (query or "").split() if w]

    def rank(e: Entry) -> tuple[Any, ...]:
        common = COMMON.index(e.code) if e.code in COMMON else len(COMMON)
        # 描述里的词命中越多越靠前（用户说“撕开 展示盖 头卡”，常用组合要排在只沾边的目录款前面）
        hits = sum(w in e.tags or w in e.description for w in words)
        return (
            -hits,
            e.maturity != "calibrated",
            common,
            KIND_ORDER.get(e.kind, 9),
            e.catalog,
            e.code,
        )

    english = lang() == "en"
    use_labels = [i18n.table()["tags"].get(use, use) if english else use for use in uses or ()]
    out = []
    for e in sorted(pool, key=rank)[:limit]:
        why = []
        if use_labels:
            why.append(t("Uses: {uses}", uses=t(", ").join(use_labels)))
        if reclosable:
            why.append(t("Can be opened and closed repeatedly"))
        if no_tape:
            why.append(t("Closes without tape or glue"))
        why.append(
            t("Calibrated against a reference dieline")
            if e.maturity == "calibrated"
            else t(
                "Common custom combination with provisional dimensions estimated from samples or descriptions. Make a physical sample."
            )
            if e.maturity == "custom"
            else t(
                "Standard structure with provisional dimensions inferred from the catalogue drawing. Make a physical sample."
            )
        )
        if e.code in COMMON:
            why.append(t("Commonly made by factories and easy to quote"))
        closure = i18n.closure_en(e.closure) if english else e.closure
        why.append(t("Closure: {closure}", closure=closure))
        out.append((e, why))
    return out


__all__ = [
    "COMMON",
    "Entry",
    "SERIES_ZH",
    "USES",
    "entries",
    "features",
    "get",
    "search",
    "suggest",
]
