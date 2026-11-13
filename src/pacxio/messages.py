"""给人看的文字：代码里写英文，`PACXIO_LANG=zh` 时查中文表（，2026-11-12 用户定）。

    from pacxio.messages import t
    t("Job not found: {path}", path=p)

- 英文原文就是键；中文表在 `locales/zh/*.yaml`（按模块分文件，键是英文原文、值是中文），启动时合并。
- 给了参数时两边都用 `str.format` 填；原文里的字面大括号要写成 `{{` `}}`。
- 中文表里没有的，照原样给英文（测试 `test_messages.py` 保证代码里用到的英文都有中文）。
- 只翻给人看的文字；错误代码、字段名、参数路径、job.json 里存的值不翻（改了会让旧的确认失效）。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path

import yaml

LANG_ENV = "PACXIO_LANG"
LANGS = ("en", "zh")
# 交付件（刀版确认图、印前报告、规格书……）的语言由工厂配置档定，可以中英对照（）
DOCUMENT_LANGS = ("en", "zh", "bilingual")
BILINGUAL = "bilingual"
LOCALES = Path(__file__).parent / "locales"
_language: ContextVar[str | None] = ContextVar("message_language", default=None)


def lang() -> str:
    """当前语言：`PACXIO_LANG=zh`（或 zh_CN、zh-Hans 这类）是中文，其余都是英文。

    交付件在 `use_language("bilingual")` 里时返回 "bilingual"（`t()` 给中英对照）。"""
    if (language := _language.get()) is not None:
        return language
    v = os.environ.get(LANG_ENV, "").strip().lower()
    return "zh" if v.startswith("zh") else "en"


@contextmanager
def use_language(language: str | None) -> Iterator[None]:
    """Override display messages for the current request."""
    token = _language.set(language)
    try:
        yield
    finally:
        _language.reset(token)


@cache
def table(code: str) -> dict[str, str]:
    """某种语言的消息表：`locales/<code>/*.yaml` 合并。同一个英文在两个文件里写了不同中文时报错。"""
    out: dict[str, str] = {}
    for p in sorted((LOCALES / code).glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            if k in out and out[k] != v:
                raise ValueError(f"{p.name}: {k!r} has a different translation in another file")
            out[str(k)] = str(v)
    return out


_FIELDS = re.compile(r"\{[^{}]*\}")
_WORDS = re.compile(r"[A-Za-z㐀-鿿]")


class Bilingual(str):
    """中英对照的一段文字：本身是 "English / 中文"，另外记着两边，拼进别的 `t()` 时各取各的。"""

    en: str
    zh: str

    def __new__(cls, en: str, zh: str, sep: str = " / ") -> Bilingual:
        obj = super().__new__(cls, f"{en}{sep}{zh}")
        obj.en, obj.zh = en, zh
        return obj


LONG = 60  # 中英对照：英文超过这么多字的整句分两行（英文一行、中文一行），短的写成 "English / 中文"


def both(en: str, zh: str) -> str:
    """两边一样时就是一份（数字、用户写的字）；不一样时给 `Bilingual`。"""
    if en == zh:
        return en
    return Bilingual(en, zh, "\n" if len(en) > LONG else " / ")


def side(value: object, language: str) -> object:
    """中英对照的文字取一边；别的原样。"""
    if isinstance(value, Bilingual):
        return value.zh if language == "zh" else value.en
    return value


def t(en: str, /, **kw: object) -> str:
    """英文原文 → 当前语言的文字（中英对照时两边分别填参数）。"""
    language = lang()
    if language == BILINGUAL and not _WORDS.search(_FIELDS.sub("", en)):
        return en.format(**kw)  # 只有标点的模板（"{part}: {message}"）：拼一次，参数自己是中英对照
    if language == BILINGUAL:
        zh = table("zh").get(en, en)
        en_text = en.format(**{k: side(v, "en") for k, v in kw.items()}) if kw else en
        zh_text = zh.format(**{k: side(v, "zh") for k, v in kw.items()}) if kw else zh
        return both(en_text, zh_text)
    text = table("zh").get(en, en) if language == "zh" else en
    return text.format(**kw) if kw else text


def pick(en: str, zh: str) -> str:
    """已经有中英两份的文字（盒型名、部件名）按当前语言取：中英对照时两份都给。"""
    language = lang()
    if language == BILINGUAL:
        return both(en, zh)
    return zh if language == "zh" else en


def join(parts: Iterable[str], en_sep: str = ", ", zh_sep: str = "、") -> str:
    """把几段文字连起来：中文用中文标点；中英对照时两边分别连。"""
    items = list(parts)
    language = lang()
    if language == BILINGUAL:
        return both(
            en_sep.join(str(side(x, "en")) for x in items),
            zh_sep.join(str(side(x, "zh")) for x in items),
        )
    return (zh_sep if language == "zh" else en_sep).join(items)


def document_language(setting: str | None) -> str:
    """配置档的 `document_language` → 交付件用的语言；auto（或没写）跟着现在的语言。"""
    if setting in DOCUMENT_LANGS:
        return setting
    return lang()


@cache
def _display_names(language: str) -> dict[str, str]:
    chinese = json.loads((LOCALES / "zh" / "names.json").read_text(encoding="utf-8"))
    if language == "zh":
        return dict(chinese)
    return {name: english for english, name in chinese.items()}


def display_name(name: str, language: str | None = None) -> str:
    """Localize a built-in display name, accepting names from older profiles."""
    language = language or lang()
    if language == BILINGUAL:
        return both(_display_names("en").get(name, name), _display_names("zh").get(name, name))
    return _display_names(language).get(name, name)
