"""能力边界测试发现的问题（2026-11-06，tools/engine_boundary.py）：极端尺寸 / 材料下不抛异常，改成说得清的拒绝。"""

from __future__ import annotations

from pacxio import api
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_s01
from pacxio.styles import get_style

PROFILE = load_profile("default")
STYLE = get_style("structure")


def _params(code: str, dims: tuple[float, float, float]) -> object:
    found = api.structure_params(code, None, None, *dims)
    assert isinstance(found, tuple)
    return STYLE.param_schema()(**found[1])


def test_trays_that_cannot_be_built_are_rejected_not_raised() -> None:
    # 墙很高时角翼 / 斜压线交叉（以前 GEOSException）；卡纸做瓦楞托盘（以前 ValueError）
    for code, dims, material in (
        ("0418", (150, 150, 1000), "flute_b"),
        ("0416", (50, 40, 30), "flute_b"),
        ("0401", (400, 300, 100), "sbs_350"),
    ):
        issues = STYLE.validate(_params(code, dims), resolve_material(material), PROFILE)
        assert issues and issues[0].id == "S03" and "生成不了" in issues[0].message, code
        assert issues[0].fix


def test_window_on_a_slope_front_stays_inside_the_low_wall() -> None:
    recipe = {"body": {"base": "rect", "window": "front"}, "top": {"kind": "slope"},
              "bottom": {"kind": "autolock", "autolock_form": "web"}}  # fmt: skip
    found = api.structure_params(None, None, recipe, 400, 300, 250)
    assert isinstance(found, tuple)
    p = STYLE.param_schema()(**found[1])
    mat = resolve_material("flute_b")
    assert not STYLE.validate(p, mat, PROFILE)
    assert not check_s01(STYLE.generate(p, mat, PROFILE))


def test_slope_with_tear_strip_is_rejected() -> None:
    recipe = {"body": {"base": "rect", "tear": True}, "top": {"kind": "slope"},
              "bottom": {"kind": "flaps"}}  # fmt: skip
    found = api.structure_params(None, None, recipe, 400, 300, 250)
    assert isinstance(found, tuple)
    issues = STYLE.validate(STYLE.param_schema()(**found[1]), resolve_material("flute_b"), PROFILE)
    assert any("撕开展示" in i.message for i in issues)


def test_set_recipe_rejects_loose_dimensions() -> None:
    from pacxio.engine import fefco

    recipe = fefco.set_recipe("0301", 300, 200, 100)
    assert isinstance(api.structure_params(None, None, recipe), tuple)
    bad = api.structure_params(None, None, recipe, 300, 200, 100)
    assert not isinstance(bad, tuple) and bad.id == "ARG"
