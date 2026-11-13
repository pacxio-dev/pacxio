"""排版错误（带可执行的修复建议）。"""

from __future__ import annotations

from pacxio.model.common import Issue


class LayoutError(Exception):
    """排版失败：携带可执行的修复建议。"""

    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


__all__ = ["LayoutError"]
