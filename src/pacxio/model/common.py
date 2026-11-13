"""跨模块共用的结果与问题模型（规格第 14 节统一输出结构）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from pacxio.messages import t

Level = Literal["error", "warning"]


class Issue(BaseModel):
    """一条可操作的问题：id 对应规格第 12 节的检查编号，fix 给出怎么改。"""

    id: str
    message: str
    fix: str | None = None


class Result(BaseModel):
    """所有 CLI 命令 / api 函数的统一返回。"""

    ok: bool = True
    command: str
    job: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)
    # 退出码：0 成功；1 有 error；2 参数错误；3 环境缺失
    exit_code: int = Field(default=0, exclude=True)

    def fail(self, issue: Issue, exit_code: int = 1) -> Result:
        self.ok = False
        self.errors.append(issue)
        self.exit_code = max(self.exit_code, exit_code)
        return self


VALIDATION_MESSAGES = {
    "missing": "Field required",
    "extra_forbidden": "Extra inputs are not permitted",
    "greater_than": "Input should be greater than {gt}",
    "greater_than_equal": "Input should be greater than or equal to {ge}",
    "less_than": "Input should be less than {lt}",
    "less_than_equal": "Input should be less than or equal to {le}",
    "finite_number": "Input should be a finite number",
    "float_type": "Input should be a valid number",
    "float_parsing": "Input should be a valid number, unable to parse string as a number",
    "int_type": "Input should be a valid integer",
    "int_parsing": "Input should be a valid integer, unable to parse string as an integer",
    "int_from_float": "Input should be a valid integer, got a number with a fractional part",
    "bool_type": "Input should be a valid boolean",
    "bool_parsing": "Input should be a valid boolean, unable to interpret input",
    "string_type": "Input should be a valid string",
    "dict_type": "Input should be a valid dictionary",
    "list_type": "Input should be a valid list",
    "literal_error": "Input should be {expected}",
    "enum": "Input should be {expected}",
    "json_invalid": "Invalid JSON: {error}",
    "value_error": "Value error, {error}",
}


def validation_message(error: Mapping[str, Any]) -> str:  # pydantic 的 ErrorDetails
    template = VALIDATION_MESSAGES.get(error["type"])
    return t(template, **error.get("ctx", {})) if template else error["msg"]


def parameter_issues(exc: ValidationError) -> list[Issue]:
    return [
        Issue(
            id="S03",
            message=t(
                "Parameter {loc}: {msg}",
                loc=".".join(str(x) for x in error["loc"]) or t("Entire input"),
                msg=validation_message(error),
            ),
            fix=t("Fix it using the parameter table from `pacxio styles list`"),
        )
        for error in exc.errors()
    ]
