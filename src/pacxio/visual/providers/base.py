"""出图来源抽象（规格 D10、13.1）。只有 `visual/providers/` 可以联网（CLAUDE.md）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pacxio.model.common import Issue


class ProviderError(Exception):
    """出图失败：携带可执行的修复建议，exit_code 对应 CLI 退出码（3 = 环境缺失，1 = 其它）。"""

    def __init__(self, issue: Issue, exit_code: int = 1) -> None:
        super().__init__(issue.message)
        self.issue = issue
        self.exit_code = exit_code


@dataclass(frozen=True)
class GenRequest:
    prompt: str  # 已含强制约束的完整提示词
    size: tuple[int, int] | None = None  # 期望像素尺寸，provider 取最接近的受支持尺寸


@dataclass
class GenResult:
    image: bytes  # PNG / JPEG 字节
    model: str
    revised_prompt: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


class ImageProvider(Protocol):
    name: str

    def generate(self, request: GenRequest) -> GenResult: ...
