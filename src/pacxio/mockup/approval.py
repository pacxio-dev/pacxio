"""设计指纹与确认记录（导出前唯一的闸门）。

指纹 = job.json（规范化）+ 所有被引用素材文件的哈希 + 用到的字体文件的哈希。
`render` 出刀版确认图时在 preview*.map.json 里记下指纹；用户确认时记录"确认了哪个指纹"。
导出时比对：设计在确认之后又改过，指纹就对不上——这样不会出现"签的是 A 版，导出的是 B 版"。

（2026-10-22 起确认的是刀版确认图；以前确认的是效果图，记录格式一样，旧记录照样有效。
记录仍放在 mockup/approval.json。）

盒型的刀版几何也会变（修正盒型结构）：每份记录（确认、刀版确认图对照文件、效果图 manifest）
另记 `style_version`。指纹本身不变（旧确认不会无故作废）；记录里的盒型版本和现在的对不上，
同样算过期。2026-10-22 之前的记录没有这一项，按当时的版本（LEGACY_STYLE_VERSIONS）算。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from pacxio.fontfiles import TextError, find_font, parse_font_chain
from pacxio.model.job import Job
from pacxio.styles import get_style
from pacxio.visual.meta import now_iso

MOCKUP_DIR = "mockup"
MANIFEST = "manifest.json"
APPROVAL = "approval.json"
PREVIEW_MAPS = "preview*.map.json"  # render 写的刀版确认图对照文件（多部件每个部件一份）
# 开始记盒型版本之前（2026-10-22）各盒型的刀版版本：旧记录没有 style_version，按它算
LEGACY_STYLE_VERSIONS = {"rte": "0.1.0-m1", "ste": "0.1.0-m1", "snaplock": "0.1.0-m1",
                         "autolock": "0.1.0-m1", "fefco_0201": "0.1.0-m4", "fefco_0427": "0.1.0-m4",
                         "tray_lid": "0.1.0-m4", "drawer": "0.1.0-m4"}  # fmt: skip
Status = Literal["none", "pending", "stale", "approved"]


class Approval(BaseModel):
    fingerprint: str
    approved_at: str
    by: str | None = None
    note: str | None = None
    orientation_verified: bool = False
    acknowledged: list[str] = []  # 确认时仍存在的警告编号（如 MK03）
    concept_id: str | None = None  # 按哪张概念图做的（选定了的话）
    style_version: str | None = None  # 确认时盒型刀版的版本；旧记录没有（按 LEGACY_STYLE_VERSIONS）


@dataclass(frozen=True)
class ApprovalState:
    status: Status
    current: str
    approval: Approval | None = None
    preview_fingerprint: str | None = None  # 刀版确认图是按哪个指纹出的（不是最新时是旧的那个）

    @property
    def ok(self) -> bool:
        return self.status == "approved"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def design_fingerprint(directory: Path, job: Job) -> str:
    assets = {
        s: _sha(directory / s)
        for s in sorted(
            {p.source for p in job.placements if p.source}
            | {p.clip for p in job.placements if p.clip}
            | {f.source for f in job.finishes if f.source}
            | {e.source for s in job.surface_treatments or [] for e in s.exclusions if e.source}
            | {x for a in job.attachments or [] for x in (a.source, a.relief) if x}
        )
    }
    fonts: dict[str, str] = {}
    for p in job.placements:
        if p.kind == "text" and p.font:
            for name in parse_font_chain(p.font):
                try:
                    fonts[name] = _sha(find_font(name, directory))
                except TextError:
                    fonts[name] = "MISSING"
    blob = json.dumps(
        {
            # 关键颜色、工艺说明只是给工厂的标注，英文说明只进出图提示词，都不改印刷内容和确认时
            # 看的图：改了不让确认过期
            "job": job.model_dump(
                mode="json",
                exclude_none=True,
                exclude={
                    "key_colors": True,
                    "container": True,  # 标签贴的瓶子只影响效果图展示（C4）
                    "pouch": True,  # 软袋鼓起量只影响装满效果图（C5）
                    "placements": {"__all__": {"label_en"}},
                    "finishes": {"__all__": {"note"}},
                    **({"attachments": {"__all__": {"note"}}} if job.attachments else {}),
                },
            ),
            "assets": assets,
            "fonts": dict(sorted(fonts.items())),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def style_version(job: Job) -> str:
    """现在这个盒型的刀版几何版本。"""
    return get_style(job.style).version


def is_current(record: Mapping[str, Any] | None, fingerprint: str, job: Job) -> bool:
    """一份记录（对照文件、manifest、确认）是不是按现在的设计、现在的盒型刀版出的。"""
    if not record or record.get("fingerprint") != fingerprint:
        return False
    recorded = record.get("style_version") or LEGACY_STYLE_VERSIONS.get(job.style)
    return recorded == style_version(job)


def read_manifest(directory: Path) -> dict | None:  # type: ignore[type-arg]
    p = directory / MOCKUP_DIR / MANIFEST
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def read_approval(directory: Path) -> Approval | None:
    p = directory / MOCKUP_DIR / APPROVAL
    return Approval.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None


def preview_records(directory: Path) -> list[dict[str, Any]]:
    """刀版确认图旁边的对照文件（每个部件一份；读不出来的当空记录，算过期）；还没 render 过是空列表。"""
    out: list[dict[str, Any]] = []
    for p in sorted(directory.glob(PREVIEW_MAPS)):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        out.append(data if isinstance(data, dict) else {})
    return out


def approval_state(directory: Path, job: Job, fingerprint: str | None = None) -> ApprovalState:
    """fingerprint：调用方已算好的设计指纹（不给就现算）。

    approved：确认的就是现在的设计（和现在的盒型刀版）；none：还没出刀版确认图；
    stale：设计或盒型刀版在出图之后又改过；pending：刀版确认图是最新的，等用户确认。"""
    current = fingerprint if fingerprint is not None else design_fingerprint(directory, job)
    approval = read_approval(directory)
    if approval is not None and is_current(approval.model_dump(), current, job):
        return ApprovalState("approved", current, approval, current)
    previews = preview_records(directory)
    if not previews:
        return ApprovalState("none", current, approval)
    old = next((r for r in previews if not is_current(r, current, job)), None)
    if old is not None:
        return ApprovalState("stale", current, approval, old.get("fingerprint"))
    return ApprovalState("pending", current, approval, current)


def write_approval(directory: Path, a: Approval) -> Path:
    p = directory / MOCKUP_DIR / APPROVAL
    p.parent.mkdir(exist_ok=True)  # 确认不再要求先出效果图：mockup/ 可能还没有
    p.write_text(a.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return p


def make_approval(
    fingerprint: str,
    by: str | None,
    note: str | None,
    orientation_verified: bool,
    acknowledged: list[str],
    concept_id: str | None = None,
    style_version: str | None = None,
) -> Approval:
    return Approval(
        concept_id=concept_id,
        style_version=style_version,
        fingerprint=fingerprint,
        approved_at=now_iso(),
        by=by,
        note=note,
        orientation_verified=orientation_verified,
        acknowledged=acknowledged,
    )


# ---- 排版初稿（`plan`）的确认状态：工作台要读，开源；画示意图的 plan.py 闭源（）----

PLAN_DIR = "plan"
CONFIRM_FILE = "plan_confirmed.json"


def read_confirmation(directory: Path) -> dict[str, Any] | None:
    f = directory / CONFIRM_FILE
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except ValueError:
        return None


def confirmation_status(directory: Path, job: Job, fingerprint: str | None = None) -> str:
    """none / confirmed / stale。fingerprint：调用方已算好的设计指纹（不给就现算）。"""
    c = read_confirmation(directory)
    if c is None:
        return "none"
    fp = fingerprint if fingerprint is not None else design_fingerprint(directory, job)
    return "confirmed" if c.get("fingerprint") == fp else "stale"
