"""信息清单的命令（ 2.1）：导入 / 增删改 / 列出 / 确认措辞。

确认记录写在 job 目录的 `content_confirmed.json`：谁、什么时候、对应哪一版措辞（指纹）。清单改了，
确认自动过期。**确认要等用户明确说"措辞确认"之后才运行**，不能替用户确认（与 approve 相同）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from pacxio import dielines
from pacxio.concept.service import concept_guard_issue
from pacxio.jobfiles import import_asset, is_concept
from pacxio.messages import display_name, t
from pacxio.model.common import Issue, Result, validation_message
from pacxio.model.content import (
    CATEGORIES,
    ContentInventory,
    ContentItem,
    next_item_id,
    template_ids,
)
from pacxio.model.conventions import (
    FACE_ZH,
    ConventionsNotFoundError,
    list_conventions,
    load_conventions,
)
from pacxio.model.job import Job

CONFIRM_FILE = "content_confirmed.json"


@dataclass
class Confirmation:
    by: str | None
    note: str | None
    confirmed_at: str
    fingerprint: str
    items: int
    pending: list[str]


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def read_confirmation(directory: Path) -> Confirmation | None:
    f = directory / CONFIRM_FILE
    if not f.is_file():
        return None
    try:
        return Confirmation(**json.loads(f.read_text(encoding="utf-8")))
    except (ValueError, TypeError):
        return None


def confirmation_status(directory: Path, job: Job) -> str:
    """none（没有清单或没确认）/ confirmed / stale（确认之后清单改过）。"""
    c = read_confirmation(directory)
    if job.content is None or c is None:
        return "none"
    return "confirmed" if c.fingerprint == job.content.fingerprint() else "stale"


def _load(directory: Path, r: Result) -> Job | None:
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
    return job


def _validation_issues(e: ValidationError) -> list[Issue]:
    return [
        Issue(
            id="ARG",
            message=t("Parameter {loc}: {msg}", loc=".".join(str(x) for x in err["loc"]) or t("Content item"), msg=validation_message(err)),
            fix=t("Available categories: ") + t(", ").join(t("{id} ({name})", id=k, name=display_name(v)) for k, v in CATEGORIES.items()),
        )
        for err in e.errors()
    ]


def _with_asset(
    directory: Path, fields: dict[str, Any], r: Result, base: Path | None = None
) -> bool:
    """把条目的 asset 收进 job/assets（概念图不行）。失败时写错误并返回 False。"""
    asset = fields.get("asset")
    if asset is None:
        return True
    if is_concept(directory, Path(str(asset)), base):
        r.fail(concept_guard_issue(t("content list assets")), 2)
        return False
    rel = import_asset(directory, Path(str(asset)), base)
    if rel is None:
        r.fail(Issue(id="ASSET", message=t("Asset file not found: {asset}", asset=asset), fix=t("Check the path")), 2)
        return False
    fields["asset"] = rel
    return True


def _save(directory: Path, job: Job, r: Result) -> None:
    r.artifacts = [str(job.save(directory))]
    status = confirmation_status(directory, job)
    if status == "stale":
        r.warnings.append(
            Issue(
                id="CONTENT",
                message=t("The content list changed after wording approval. The previous approval has expired."),
                fix=t("Show the changed list to the user and run `pacxio content confirm` after approval"),
            )
        )


def content_import(directory: Path, file: Path, replace: bool = False) -> Result:
    """从 YAML / JSON 文件导入清单（agent 整理好需求文档后一次写入）。

    文件格式：`{conventions: paint, items: [{id, category, text, asset, required, pending, note}]}`。
    默认合并：新 id 加入、已有 id 覆盖；`replace` 时整个清单换成文件里的。
    """
    r = Result(command="content import", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return r.fail(
            Issue(id="ARG", message=t("Could not read {file}: {error}", file=file, error=e), fix=t("Supply a YAML or JSON file")), 2
        )
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return r.fail(
            Issue(
                id="ARG",
                message=t("The content file must contain an items list"),
                fix=t("Format: {conventions: paint, items: [...]}"),
            ),
            2,
        )
    items: list[ContentItem] = []
    for row in raw["items"]:
        fields = dict(row) if isinstance(row, dict) else {}
        if not _with_asset(directory, fields, r, base=file.parent):
            return r
        try:
            items.append(ContentItem.model_validate(fields))
        except ValidationError as e:
            return r.fail(_validation_issues(e)[0], 2)
    inv = job.content or ContentInventory()
    if replace:
        merged = items
    else:
        new = {it.id: it for it in items}
        merged = [new.pop(it.id, it) for it in inv.items] + list(new.values())
    conventions = raw.get("conventions", inv.conventions)
    if conventions is not None:
        try:
            load_conventions(str(conventions))
        except ConventionsNotFoundError as e:
            return r.fail(
                Issue(id="ARG", message=str(e), fix=t("Run `pacxio conventions list` to see available categories")),
                2,
            )
    try:
        job.content = ContentInventory(conventions=conventions, items=merged)
    except ValidationError as e:
        return r.fail(_validation_issues(e)[0], 2)
    _save(directory, job, r)
    r.data = {"items": len(merged), "imported": len(items), "conventions": conventions}
    return r


def content_add(directory: Path, fields: dict[str, Any]) -> Result:
    r = Result(command="content add", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    inv = job.content or ContentInventory()
    fields = {k: v for k, v in fields.items() if v is not None}
    if "id" not in fields and "category" in fields:
        fields["id"] = next_item_id(inv, str(fields["category"]))
    if inv.get(str(fields.get("id"))) is not None:
        return r.fail(
            Issue(
                id="ARG",
                message=t("Item {id} already exists", id=fields['id']),
                fix=t("Choose a different --id or use `content edit`"),
            ),
            2,
        )
    if not _with_asset(directory, fields, r):
        return r
    try:
        item = ContentItem.model_validate(fields)
    except ValidationError as e:
        return r.fail(_validation_issues(e)[0], 2)
    try:
        job.content = ContentInventory(conventions=inv.conventions, items=[*inv.items, item])
    except ValidationError as e:
        return r.fail(_validation_issues(e)[0], 2)
    _save(directory, job, r)
    r.data = {"id": item.id, "items": len(job.content.items)}
    return r


def content_edit(directory: Path, item_id: str, fields: dict[str, Any], clear: list[str]) -> Result:
    """改一条：fields 里给了的字段覆盖；clear 里的字段清空（如 pending、asset、note）。"""
    r = Result(command="content edit", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    inv = job.content
    item = inv.get(item_id) if inv else None
    if inv is None or item is None:
        return r.fail(
            Issue(id="ARG", message=t("Item {id} does not exist", id=item_id), fix=t("Run `pacxio content list` to see the items")), 2
        )
    data = item.model_dump()
    data.update({k: v for k, v in fields.items() if v is not None})
    for k in clear:
        data[k] = None
    if not _with_asset(directory, data, r):
        return r
    try:
        new = ContentItem.model_validate(data)
    except ValidationError as e:
        return r.fail(_validation_issues(e)[0], 2)
    try:
        job.content = ContentInventory(
            conventions=inv.conventions, items=[new if it.id == item_id else it for it in inv.items]
        )
    except ValidationError as e:
        return r.fail(_validation_issues(e)[0], 2)
    _save(directory, job, r)
    r.data = {"id": item_id, "item": new.model_dump(exclude_none=True)}
    return r


def content_remove(directory: Path, item_id: str) -> Result:
    r = Result(command="content remove", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    inv = job.content
    if inv is None or inv.get(item_id) is None:
        return r.fail(
            Issue(id="ARG", message=t("Item {id} does not exist", id=item_id), fix=t("Run `pacxio content list` to see the items")), 2
        )
    users = [
        p.id for p in job.placements if p.content == item_id or item_id in template_ids(p.text)
    ]
    users += [f.id for f in job.finishes if f.content == item_id]
    users += [f"条目 {it.id}" for it in inv.items if item_id in template_ids(it.text)]
    if users:
        return r.fail(
            Issue(
                id="CONTENT",
                message=t("Item {id} is still referenced by {users}", id=item_id, users=', '.join(users)),
                fix=t("Remove or update these placements (`art remove`) or references in other items such as {{")
                + item_id
                + t("}}, then delete the item"),
            ),
            2,
        )
    job.content = ContentInventory(
        conventions=inv.conventions, items=[it for it in inv.items if it.id != item_id]
    )
    _save(directory, job, r)
    r.data = {"removed": item_id, "items": len(job.content.items)}
    return r


def set_conventions(directory: Path, conv_id: str | None) -> Result:
    r = Result(command="content conventions", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    if conv_id is not None:
        try:
            load_conventions(conv_id)
        except ConventionsNotFoundError as e:
            return r.fail(
                Issue(id="ARG", message=str(e), fix=t("Run `pacxio conventions list` to see available categories")),
                2,
            )
    inv = job.content or ContentInventory()
    job.content = ContentInventory(conventions=conv_id, items=inv.items)
    r.artifacts = [str(job.save(directory))]
    r.data = {"conventions": conv_id}
    return r


def content_list(directory: Path) -> Result:
    """列出清单：按类别分组，带常规位置提示、待定项、哪些已经放上版面、确认状态。"""
    r = Result(command="content list", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    inv = job.content or ContentInventory()
    conv = None
    if inv.conventions:
        try:
            conv = load_conventions(inv.conventions)
        except ConventionsNotFoundError as e:
            r.warnings.append(
                Issue(
                    id="CONTENT", message=str(e), fix=t("Run `content conventions <job> <category>` to set it again")
                )
            )
    placed = job.content_refs()
    rows = []
    for it in inv.items:
        row = it.model_dump(exclude_none=True)
        row["label"] = display_name(it.label)
        row["placed_by"] = placed.get(it.id, [])
        if conv is not None:
            row["usual_faces"] = [display_name(FACE_ZH[x]) for x in conv.usual_faces(it.category)]
        rows.append(row)
    status = confirmation_status(directory, job)
    conf = read_confirmation(directory)
    pending = [it.id for it in inv.items if it.pending]
    r.data = {
        "conventions": inv.conventions,
        "conventions_name": t(conv.name_zh) if conv else None,
        "items": rows,
        "pending": pending,
        "confirmation": {"status": status, **(asdict(conf) if conf else {})},
    }
    if pending:
        r.warnings.append(
            Issue(
                id="CONTENT",
                message=t("{count} content items are pending: {items}", count=len(pending), items=', '.join(pending)),
                fix=t("Get the exact content from the user or brand owner, then use `content edit <job> <id> --text ... --clear pending`"),
            )
        )
    if inv.items and status != "confirmed":
        r.warnings.append(
            Issue(
                id="CONTENT",
                message=t("The wording of the content list has not been confirmed")
                if status == "none"
                else t("The list changed after confirmation. The confirmation has expired."),
                fix=t("Show each item to the user. After explicit wording approval, run `pacxio content confirm <job> --by <approver>`."),
            )
        )
    return r


def content_confirm(
    directory: Path, by: str | None = None, note: str | None = None, status_only: bool = False
) -> Result:
    """记录用户对措辞的确认（或只查看状态）。不确认位置——位置在概念图之后再定。"""
    r = Result(command="content confirm", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    status = confirmation_status(directory, job)
    r.data = {"status": status}
    if status_only:
        conf = read_confirmation(directory)
        if conf:
            r.data.update(asdict(conf))
        return r
    if job.content is None or not job.content.items:
        return r.fail(
            Issue(
                id="CONTENT",
                message=t("There is no content list yet"),
                fix=t("Start a list with `content import` or `content add`"),
            ),
            2,
        )
    pending = [it.id for it in job.content.items if it.pending]
    c = Confirmation(by, note, _now(), job.content.fingerprint(), len(job.content.items), pending)
    path = directory / CONFIRM_FILE
    path.write_text(json.dumps(asdict(c), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    r.artifacts = [str(path)]
    r.data = {"status": "confirmed", **asdict(c)}
    if pending:
        r.warnings.append(
            Issue(
                id="CONTENT",
                message=t("{count} items were still pending at confirmation: {items}. The layout will use placeholders.", count=len(pending), items=', '.join(pending)),
                fix=t("Ask the user to confirm again after completing the list"),
            )
        )
    return r


def conventions_list() -> Result:
    r = Result(command="conventions list")
    rows = []
    for cid in list_conventions():
        try:
            c = load_conventions(cid)
            rows.append(
                {
                    "id": c.id,
                    "name_zh": t(c.name_zh),
                    "description": t(c.description),
                    "verified": c.verified,
                }
            )
        except ConventionsNotFoundError as e:
            r.warnings.append(Issue(id="CONTENT", message=str(e)))
    r.data = {"conventions": rows}
    return r


def conventions_show(conv_id: str) -> Result:
    r = Result(command="conventions show")
    try:
        c = load_conventions(conv_id)
    except ConventionsNotFoundError as e:
        return r.fail(Issue(id="ARG", message=str(e), fix=t("Run `pacxio conventions list` for the available categories")), 2)
    r.data = c.model_dump()
    r.data["name_zh"] = t(c.name_zh)
    r.data["description"] = t(c.description)
    r.data["layouts"] = [
        {key: value if key == "id" else t(value) for key, value in layout.model_dump().items()}
        for layout in c.layouts
    ]
    r.data["reminders"] = [t(reminder) for reminder in c.reminders]
    r.data["faces_zh"] = {
        display_name(CATEGORIES[k]): [display_name(FACE_ZH[f]) for f in v] for k, v in c.faces.items()
    }
    if not c.verified:
        r.warnings.append(
            Issue(
                id="CONTENT",
                message=t("Reference guide {id} is provisional and has yet to be verified by the brand, factory, or regulatory adviser", id=c.id),
                fix=t("Treat this as guidance. Ask the user to confirm regulatory details against current requirements."),
            )
        )
    return r


__all__ = [
    "CONFIRM_FILE",
    "confirmation_status",
    "content_add",
    "content_confirm",
    "content_edit",
    "content_import",
    "content_list",
    "content_remove",
    "conventions_list",
    "conventions_show",
    "read_confirmation",
    "set_conventions",
]
