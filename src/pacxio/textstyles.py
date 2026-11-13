"""文字样式（层级）的命令：标题、小标题、正文、小字……（ 2.2）。

文字放置用 `--style` 引用样式；放置自己没给的字体、字号、行距、字距、颜色取自样式。改一个样式，
同层级的文字一起变——排版初稿反复调整时最常用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pacxio import dielines
from pacxio.fontfiles import TextError, find_font, parse_font_chain
from pacxio.model.common import Issue, Result
from pacxio.model.job import STYLE_NAME, Job, TextStyle


def _load(directory: Path, r: Result) -> Job | None:
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
    return job


def style_set(directory: Path, name: str, fields: dict[str, Any]) -> Result:
    """新建或修改一个样式：给了的字段覆盖，没给的保留原值。"""
    r = Result(command="style set", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    if not STYLE_NAME.match(name):
        return r.fail(Issue(id="ARG", message=f"样式名只能用字母、数字、下划线、连字符：{name!r}",
                            fix="如 title、heading、body、small"), 2)  # fmt: skip
    styles = dict(job.styles or {})
    data = styles[name].model_dump() if name in styles else {}
    data.update({k: v for k, v in fields.items() if v is not None})
    try:
        style = TextStyle.model_validate(data)
    except ValidationError as e:
        err = e.errors()[0]
        return r.fail(
            Issue(
                id="ARG",
                message=f"{'.'.join(str(x) for x in err['loc']) or name}：{err['msg']}",
                fix="新样式至少要给 --font 和 --size-pt",
            ),
            2,
        )
    try:
        for font_name in parse_font_chain(style.font):
            find_font(font_name, directory)
    except TextError as e:
        return r.fail(e.issue, 2)
    styles[name] = style
    job.styles = styles
    r.artifacts = [str(job.save(directory))]
    users = [p.id for p in job.placements if p.style == name]
    r.data = {"name": name, "style": style.model_dump(exclude_none=True), "used_by": users}
    return r


def style_list(directory: Path) -> Result:
    r = Result(command="style list", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    rows = []
    for name, st in (job.styles or {}).items():
        row = {"name": name, **st.model_dump(exclude_none=True)}
        row["used_by"] = [p.id for p in job.placements if p.style == name]
        rows.append(row)
    r.data = {"styles": rows}
    return r


def style_remove(directory: Path, name: str) -> Result:
    r = Result(command="style remove", job=str(directory))
    job = _load(directory, r)
    if job is None:
        return r
    if not job.styles or name not in job.styles:
        return r.fail(
            Issue(id="ARG", message=f"没有样式 {name}", fix="`pacxio style list` 查看"), 2
        )
    users = [p.id for p in job.placements if p.style == name]
    if users:
        return r.fail(
            Issue(
                id="STYLE",
                message=f"样式 {name} 正被这些放置使用：{', '.join(users)}",
                fix="先给这些放置换样式或删掉它们，再删样式",
            ),
            2,
        )
    job.styles = {k: v for k, v in job.styles.items() if k != name} or None
    r.artifacts = [str(job.save(directory))]
    r.data = {"removed": name}
    return r


__all__ = ["style_list", "style_remove", "style_set"]
