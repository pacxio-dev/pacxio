"""印前检查报告模型（规格 12 节）：JSON 给 agent 读，PDF 给人看。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

Level = Literal["error", "warning", "info"]


class Where(BaseModel):
    """问题位置：刀版坐标系下的矩形（mm），供预览图标注。"""

    x0: float
    y0: float
    x1: float
    y1: float
    panel: str | None = None
    part: str | None = None  # 多部件盒型：哪个部件（坐标是这个部件自己刀版的）

    @model_serializer(mode="wrap")
    def _drop_empty_part(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """单部件盒型不写 part：报告 JSON 和加这个字段以前一样。"""
        d: dict[str, Any] = handler(self)
        if d.get("part") is None:
            d.pop("part", None)
        return d


class Finding(BaseModel):
    id: str
    level: Level
    message: str
    fix: str | None = None
    where: Where | None = None


class Report(BaseModel):
    job: str
    findings: list[Finding] = Field(default_factory=list)
    # 实际执行过的规则 / 被跳过的规则及原因（如缺 Ghostscript：规格 12 节要求报告里标明"未执行"）
    executed: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)
    degraded_color: bool = False
    profile_verified: bool = False

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def passed(self) -> bool:
        """没有 error 才算通过（D12）。"""
        return not self.errors
