"""盒型接口与注册表（）。"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, MaterialType, Profile

# 各面图文正立方向经实物打样确认的盒型（2026-10-24 用户：8 种盒型的打样验证都通过）。
# 新加的盒型打样前不要放进来：不在这里的，盖、底等面标"方向待定"，导出、效果图如实提醒。
ORIENTATION_VERIFIED: frozenset[str] = frozenset(
    {"rte", "ste", "snaplock", "autolock", "fefco_0427", "fefco_0201", "tray_lid", "drawer"}
)


class BoxStyle(Protocol):
    id: ClassVar[str]
    name_zh: ClassVar[str]
    refs: ClassVar[dict[str, str]]
    material_types: ClassVar[frozenset[MaterialType]]
    # 刀版几何的版本：改了刀版形状就升版本——已经出的刀版确认图、效果图变成"不是最新"，确认作废
    version: ClassVar[str]

    def param_schema(self) -> type[BaseModel]: ...

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]: ...

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Any]: ...  # 面 id → (宽, 高)；多部件盒型是 部件 → {面 id → (宽, 高)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline: ...


_REGISTRY: dict[str, BoxStyle] = {}


def register(style: BoxStyle) -> BoxStyle:
    _REGISTRY[style.id] = style
    return style


def get_style(style_id: str) -> BoxStyle:
    # 延迟导入，避免循环依赖；新增盒型在 styles/__init__ 登记
    import pacxio.styles  # noqa: F401

    if style_id not in _REGISTRY:
        raise KeyError(style_id)
    return _REGISTRY[style_id]


def style_advisories(
    style: BoxStyle, params: Any, material: Material, profile: Profile
) -> list[Issue]:
    """盒型对材料 / 尺寸的建议（S04，只提醒不拦）；盒型没有这一项时为空。"""
    fn = getattr(style, "advisories", None)
    return list(fn(params, material, profile)) if fn is not None else []


def all_styles() -> list[BoxStyle]:
    """结构目录里的盒型（试验中的入口不列，如几何引擎的 structure；get_style 照样找得到）。"""
    import pacxio.styles  # noqa: F401

    return [s for s in _REGISTRY.values() if not getattr(s, "experimental", False)]
