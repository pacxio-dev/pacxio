"""厚度补偿（）。补偿 = 系数 × 材料厚度 t，系数来自工厂配置档。

M0 的 RTE v0 "不含补偿"：enabled=False 时所有系数为 0，面尺寸等于内尺寸。
M1 起默认启用。
"""

from __future__ import annotations

from pacxio.model.profile import CompensationSet, Material, Profile

_OFF = CompensationSet(k_panel=0.0, k_height=0.0, k_lid=0.0, k_flap=0.0)


def coefficients(profile: Profile, material: Material, enabled: bool) -> CompensationSet:
    if not enabled:
        return _OFF
    return profile.compensation[material.type]
