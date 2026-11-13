"""常规参考表（ 2.2）：某一品类的包装，各类信息通常放在哪些面、常见版式、法规提醒。

只是提示，不是规定：出概念图时写进提示词，看图后用来核对。按品类配置在 `conventions/*.yaml`，
查找路径与配置档相同（环境变量 > 当前目录 > 源码仓库 > 随包安装的副本）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from pacxio.messages import t
from pacxio.model.content import CATEGORIES

CONVENTIONS_DIR_ENV = "PACXIO_CONVENTIONS_DIR"
Face = Literal["front", "back", "side", "top", "bottom"]
FACE_ZH: dict[str, str] = {"front": "正面", "back": "背面", "side": "侧面", "top": "顶面",
                           "bottom": "底面"}  # fmt: skip


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Layout(_Strict):
    id: str
    name_zh: str
    front: str
    back: str
    sides: str
    common_for: str


class Conventions(_Strict):
    id: str
    name_zh: str
    description: str
    verified: bool = False
    faces: dict[str, list[Face]]
    layouts: list[Layout]
    reminders: list[str] = []

    @field_validator("faces")
    @classmethod
    def _known(cls, v: dict[str, list[Face]]) -> dict[str, list[Face]]:
        unknown = [k for k in v if k not in CATEGORIES]
        if unknown:
            raise ValueError(t("Unknown content categories: {categories}", categories=unknown))
        return v

    def usual_faces(self, category: str) -> list[str]:
        return list(self.faces.get(category, []))


class ConventionsNotFoundError(LookupError):
    pass


def conventions_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get(CONVENTIONS_DIR_ENV)
    if env:
        dirs.append(Path(env))
    dirs.append(Path.cwd() / "conventions")
    dirs.append(Path(__file__).resolve().parents[3] / "conventions")  # 源码仓库
    dirs.append(Path(__file__).resolve().parents[1] / "_conventions")  # 随 wheel 安装的副本
    return dirs


def list_conventions() -> list[str]:
    seen: list[str] = []
    for d in conventions_dirs():
        if d.is_dir():
            for f in sorted(d.glob("*.yaml")):
                if f.stem not in seen:
                    seen.append(f.stem)
    return seen


def load_conventions(conv_id: str) -> Conventions:
    for d in conventions_dirs():
        f = d / f"{conv_id}.yaml"
        if f.is_file():
            try:
                return Conventions.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
            except (ValidationError, yaml.YAMLError) as e:
                raise ConventionsNotFoundError(
                    t("Invalid category reference {path}: {error}", path=f, error=e)
                ) from e
    available = ", ".join(list_conventions()) or t("None")
    raise ConventionsNotFoundError(
        t("Category reference {id} not found. Available: {available}", id=conv_id, available=available)
    )


__all__ = [
    "FACE_ZH",
    "Conventions",
    "ConventionsNotFoundError",
    "conventions_dirs",
    "list_conventions",
    "load_conventions",
]
