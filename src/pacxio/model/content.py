"""信息清单（ 2.1 节）：包装上要出现的每一条信息，逐字记录。

清单是 job.json 的一部分（D8）。它只记录"有什么、原文是什么、必须还是可选"，**不记录放在哪个面**——
位置由出概念图时 AI 按包装常规安排，看了图再改。版面上的文字放置用 `content` 引用清单条目，
文字内容以清单为准（改清单，所有引用它的地方一起变）。

用户确认措辞的记录单独存在 job 目录的 `content_confirmed.json`（和效果图确认 approval.json 一样，
确认记录不是设计输入）；清单改了，确认自动过期。
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from pacxio.messages import t

# 信息类别 → 中文名。效果图确认页、提示词、常规参考表都用这些键。
CATEGORIES: dict[str, str] = {
    "brand": "品牌",
    "logo": "Logo",
    "product_name": "品名",
    "variant": "品类 / 系列",
    "spec": "规格",
    "net_content": "净含量",
    "slogan": "标语",
    "selling_point": "卖点",
    "icon": "卖点图标",
    "description": "产品介绍",
    "usage": "使用方法",
    "technical": "技术参数",
    "ingredients": "成分",
    "caution": "注意事项",
    "storage": "储存",
    "standard": "执行标准",
    "manufacturer": "生产商",
    "contact": "联系方式",
    "origin": "产地",
    "barcode": "条码",
    "certification": "认证标志",
    "handling": "储运标志",  # GB/T 191 向上、怕雨……（开槽箱测试 R-02）
    "batch": "批号 / 日期",
    "other": "其它",
}
ITEM_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
# 文字放置里引用清单条目：{{条目id}}。标签、格式由排版决定，信息内容以清单为准
# {{条目id}} 或 {{条目id:N}}（只取条目的第 N 行，如标题两行用不同字号）
PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_\-]{1,40})(?::([1-9][0-9]?))?\}\}")
MAX_NESTING = 3  # 条目原文里引用别的条目时，最多展开几层
PENDING_PLACEHOLDER = "[TBD]"  # 措辞还没定、也没有草稿时，版面上先排这个占位


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentItem(_Strict):
    """一条信息。text 是原文（逐字）；Logo、图标、条码、认证标志这类图形用 asset。"""

    id: str
    category: str
    text: str | None = None
    asset: str | None = None
    required: bool = True
    # 措辞 / 素材还没定时写明缺什么（如"表干时间待品牌方提供"）；text 可以是草稿
    pending: str | None = None
    note: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ITEM_ID.match(v):
            raise ValueError(t("Item IDs may contain letters, numbers, underscores, and hyphens, up to 40 characters: {id!r}", id=v))
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(t("Unknown category {category!r}. Available categories: {available}.", category=v, available=', '.join(CATEGORIES)))
        return v

    @model_validator(mode="after")
    def _something(self) -> ContentItem:
        if self.text is None and self.asset is None and self.pending is None:
            raise ValueError(
                t("Item {id} requires text, an asset, or a pending note", id=self.id)
            )
        if self.text is not None and not self.text.strip():
            raise ValueError(t("The text for item {id} is empty", id=self.id))
        return self

    @property
    def label(self) -> str:
        return CATEGORIES[self.category]

    def display_text(self) -> str | None:
        """排到版面上的文字：原文；还没有原文的待定条目用占位。图形条目返回 None。"""
        if self.text is not None:
            return self.text
        if self.pending is not None and self.asset is None:
            return PENDING_PLACEHOLDER
        return None


class ContentInventory(_Strict):
    conventions: str | None = None  # 常规参考表 id（如 paint），出概念图时写进提示词
    items: list[ContentItem] = []

    @model_validator(mode="after")
    def _unique(self) -> ContentInventory:
        seen: set[str] = set()
        for it in self.items:
            if it.id in seen:
                raise ValueError(t("Item ID {id} is duplicated", id=it.id))
            seen.add(it.id)
        for it in self.items:  # 原文里引用的条目必须存在、不能引用自己
            for ref in template_ids(it.text):
                if ref == it.id or ref not in seen:
                    raise ValueError(
                        t("Text in item {id} references {target}", id=it.id, target=t("itself") if ref == it.id else t("missing item {id}", id=ref))
                    )
        return self

    def expand_refs(self, ids: list[str]) -> set[str]:
        """这些条目，加上它们原文里（逐层）引用的条目。"""
        out: set[str] = set()
        todo = list(ids)
        while todo:
            i = todo.pop()
            if i in out:
                continue
            out.add(i)
            item = self.get(i)
            if item is not None:
                todo += template_ids(item.text)
        return out

    def get(self, item_id: str) -> ContentItem | None:
        return next((it for it in self.items if it.id == item_id), None)

    def fingerprint(self) -> str:
        """措辞指纹：条目的类别、原文、素材、必须 / 可选、待定说明。备注不算。"""
        rows = [
            [it.id, it.category, it.text, it.asset, it.required, it.pending] for it in self.items
        ]
        blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def shown_as_logo(inv: ContentInventory, item: ContentItem, placed: set[str]) -> bool:
    """品牌名没有单独的文字、但 Logo 已放上版面：算"以 Logo 出现"——只提醒，不拦确认
    （2026-10-20 用户决定：要不要另加品牌名文字由用户看效果图后自己提）。"""
    return item.category == "brand" and any(
        i.category == "logo" and i.id in placed for i in inv.items
    )


def template_ids(text: str | None) -> list[str]:
    """文字里引用的条目 id（按出现顺序，去重）。"""
    return list(dict.fromkeys(m.group(1) for m in PLACEHOLDER.finditer(text or "")))


def bad_line_refs(text: str | None, inv: ContentInventory) -> list[tuple[str, int, int]]:
    """{{条目id:N}} 里超出条目行数的引用：(条目 id, N, 条目实际行数)。"""
    out = []
    for m in PLACEHOLDER.finditer(text or ""):
        item = inv.get(m.group(1))
        if m.group(2) is None or item is None:
            continue
        shown = item.display_text()
        lines = len(fill_template(shown, inv).split("\n")) if shown is not None else 0
        if int(m.group(2)) > lines:
            out.append((m.group(1), int(m.group(2)), lines))
    return out


def fill_template(text: str, inv: ContentInventory, depth: int = 0) -> str:
    """把 {{条目id}} 换成清单里的文字（待定条目没有文字时换成占位）；{{条目id:N}} 只取第 N 行。

    条目的原文里也可以引用别的条目（如施工步骤里的表干时间引用技术参数），逐层展开，最多
    MAX_NESTING 层；未知的 id 原样保留。
    """

    def sub(m: re.Match[str]) -> str:
        item = inv.get(m.group(1))
        shown = item.display_text() if item is not None else None
        if shown is None:
            return m.group(0)
        full = fill_template(shown, inv, depth + 1) if depth < MAX_NESTING else shown
        if m.group(2) is None:
            return full
        lines = full.split("\n")
        n = int(m.group(2))
        return lines[n - 1] if n <= len(lines) else m.group(0)

    return PLACEHOLDER.sub(sub, text)


def next_item_id(inv: ContentInventory, category: str) -> str:
    n = 1
    while inv.get(f"{category}{n}") is not None:
        n += 1
    return f"{category}{n}"


__all__ = [
    "CATEGORIES",
    "PENDING_PLACEHOLDER",
    "ContentInventory",
    "ContentItem",
    "PLACEHOLDER",
    "bad_line_refs",
    "shown_as_logo",
    "fill_template",
    "next_item_id",
    "template_ids",
]
