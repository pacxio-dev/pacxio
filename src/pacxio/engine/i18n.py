"""英文（：网站中英双语）：结构目录、可编辑参数的英文说法，数据在 catalog_en.yaml。

只翻给人看的文字；代码名、编号、参数路径不变。目录里拼出来的文字（怎么合上、ECMA 说明）按片段翻，
FEFCO 说明按编号查；"按示意图推读（待核对）"的备注统一换成英文备注。缺翻译时抛 KeyError（测试保证全覆盖）。
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml

LANGS = ("zh", "en")
NOTES = (  # 长的在前
    ("——按示意图推读，待核对", " — read from the catalogue drawing, to be checked"),
    ("——按示意图推读", " — read from the catalogue drawing"),
)
_CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")


@cache
def table() -> dict[str, Any]:
    return dict(
        yaml.safe_load((Path(__file__).parent / "catalog_en.yaml").read_text(encoding="utf-8"))
    )


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def check_lang(lang: str) -> None:
    if lang not in LANGS:
        raise ValueError(f"不认识的语言 {lang}（可用：{'、'.join(LANGS)}）")


def closure_en(text: str) -> str:
    t = table()
    closures = t["closure"]

    def translate(part: str) -> str:
        if part in closures:
            return str(closures[part])
        pieces = re.split(r" \+ (?=两侧翼锁舌扣底|齿线撕开窗|展示盖压外面)", part)
        if len(pieces) > 1:
            return " + ".join(translate(piece) for piece in pieces)
        if not has_cjk(part):  # 自定义配方可能带机器可读的 kind
            return part.replace("_", " ")
        raise KeyError(part)

    out = []
    for part in text.split("；"):
        m = re.match(r"^(顶|底)：(.*)$", part)
        if m:
            out.append(f"{t['closure_prefix'][m.group(1)]}: {translate(m.group(2))}")
        else:
            out.append(translate(part))
    return "; ".join(out)


def description_en(entry: Any) -> str:
    t = table()
    if entry.catalog == "ECMA":
        from pacxio.engine import ecma

        b, top, _, _ = ecma.parse(entry.code)
        return f"top: {t['ecma_closure'][top]}, bottom: {t['ecma_closure'][b]}"
    if entry.catalog == "组合":
        c = t["combos"][entry.code]
        return f"{c['name']}: {c['description']}"
    text = str(t["descriptions"][entry.code])
    note = next((en for zh, en in NOTES if zh in entry.description), "")
    return text + note


def entry_dict(entry: Any, lang: str = "zh") -> dict[str, Any]:
    """目录条目 → 字典；lang=en 时给人看的字段换成英文（字段名不变；series_zh / maturity_zh 换成
    series_name / maturity_name）。"""
    d: dict[str, Any] = dict(entry.as_dict())
    if lang != "en":
        return d
    t = table()
    d |= {
        "catalog": t["catalog"][entry.catalog],
        "series_name": t["series"][entry.series],
        "description": description_en(entry),
        "closure": closure_en(entry.closure),
        "tags": [t["tags"][x] for x in entry.tags],
        "maturity_name": t["maturity"][entry.maturity],
    }
    for k in ("series_zh", "maturity_zh"):
        d.pop(k, None)
    return d


def _humanize(path: str) -> str:
    return path.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def editable_en(data: dict[str, Any]) -> dict[str, Any]:
    """structure_editable 的结果换成英文说法（name / part / explain / option_names）。没收录的参数名按代码名写。"""
    e = table()["editable"]
    for item in data.get("parameters", []):
        key = item["path"].rsplit(".", 1)[-1]
        item["name"] = e["terms"].get(key) or _humanize(item["path"])
        item["part"] = e["parts"].get(item.get("part", ""), "Other")
        if key in e["explain"]:
            item["explain"] = e["explain"][key]
        else:
            item.pop("explain", None)
        if has_cjk(str(item.get("label", ""))):
            item.pop("label", None)
        if "option_names" in item:
            item["option_names"] = _options_en(item["path"], item["option_names"])
        if has_cjk(str(item.get("component_name", ""))):
            item["component_name"] = item.get("component", "")
    return data


def _options_en(path: str, names: dict[str, str]) -> dict[str, str]:
    e = table()["editable"]
    key = path.rsplit(".", 1)[-1]
    opts = e["options"].get("seam_wall" if path == "seam.wall" else key, {})
    out = {}
    for raw in names:
        value = json.loads(raw)
        if key in ("on", "thumb"):
            text = e["walls"].get("default" if value is None else value)
        elif isinstance(value, bool) or value is None and not opts:
            text = e["bool"]["default" if value is None else str(value).lower()]
        else:
            text = opts.get(value)
        out[raw] = text if text is not None else str(value)
    return out


__all__ = [
    "LANGS",
    "check_lang",
    "closure_en",
    "description_en",
    "editable_en",
    "entry_dict",
    "has_cjk",
    "table",
]
