"""托盘连盖的展示开盒选项（engine/tray_display.py，2026-11-06 按用户实物盒做）：
卡纸托盒 + 顶板（盖）两侧锁舌插底板的槽 + 撕开窗 + 前墙上的展示盖（两角圆角 + 半圆易开拉手，背面粘窗口边的胶条）。"""

from __future__ import annotations

from typing import Any

import pytest
from shapely.geometry import Polygon

from pacxio import api
from pacxio.engine import function_check
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.foldcheck import fold_clashes
from pacxio.model.dieline import Dieline, EdgeType
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style

PROFILE = load_profile("default")
STYLE = get_style("structure")
RECIPE: dict[str, Any] = {
    "body": {"form": "tray", "corners": "flap", "corner_on": "long"},
    "top": {"kind": "hinged_lid", "lid_front": "none", "dust_lock": True, "tear_window": True,
            "display_lid": "tab"},
    "bottom": {"kind": "open"},
}  # fmt: skip


def _make(
    dims: tuple[float, float, float], material: str, **top: Any
) -> tuple[Dieline, dict[str, Any]]:
    recipe = {**RECIPE, "top": {**RECIPE["top"], **top}}
    found = api.structure_params(None, None, recipe, *dims)
    assert isinstance(found, tuple)
    p = STYLE.param_schema()(**found[1])
    mat = resolve_material(material)
    assert not STYLE.validate(p, mat, PROFILE), STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert (
        not check_s01(dl)
        + check_crease_sides(dl)
        + check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    )
    return dl, found[1]


@pytest.mark.parametrize("material", ["sbs_350", "sbs_400", "flute_e"])
@pytest.mark.parametrize("dims", [(150, 150, 40), (200, 120, 60), (120, 160, 35)])
def test_display_box_folds_locks_and_closes(
    dims: tuple[float, float, float], material: str
) -> None:
    dl, params = _make(dims, material)
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert function_check.check(dl, params)["status"] == "pass"
    assert fold_clashes(dl) == []
    folded = function_check.fold(dl)
    assert folded is not None
    # 侧翼在侧墙外面，下沿锁舌往里折、穿过底板 / 侧墙折线上的槽（用户实物）
    assert {"lock_a_1", "lock_a_2", "lock_b_1", "lock_b_2"} <= function_check.through_slots(folded)
    n_flap, n_wall = folded.normal("dust_a"), folded.normal("side_a")
    assert abs(float(n_flap @ n_wall)) > 0.99
    assert "tuck" not in {q.id for q in dl.panels}  # lid_front = none


def test_glue_band_pull_tab_and_true_arcs() -> None:
    dl, _ = _make((150, 150, 40), "sbs_350")
    perf = next(e for e in dl.edges if e.id == "perf_tear_window")
    assert perf.type == EdgeType.PERF and perf.panels == ["lid"]
    assert sum(1 for q in perf.segments if q.is_arc) == 4  # 四角圆角（撕的时候不裂进角里）
    window = path_to_polygon(GPath(perf.segments, closed=True))
    lid = path_to_polygon(dl.panel("lid").polygon)
    assert len(dl.glue_areas) == 1
    band = path_to_polygon(dl.glue_areas[0])
    # 胶条在盖上、窗口外，紧贴窗口的一条边（用户：胶在开口靠底那条边上）
    assert lid.contains(band) and not band.intersects(window) and band.distance(window) < 3.0
    # 两角圆角 + 远边中间半圆拉手，拉手是展示盖轮廓的一部分（没有折线、不单独成面）
    assert sum(1 for s in dl.panel("display").polygon.segments if s.is_arc) == 3
    assert "pull_tab" not in {q.id for q in dl.panels}
    assert sum(1 for s in dl.outline_segments() if s.is_arc) == 3  # 外轮廓里也是真圆弧
    assert Polygon(path_to_polygon(GPath(dl.outline_segments(), closed=True))).is_valid
    # 展示盖连拉手一共 = 盒子那一边的长度（拉手不伸出盒外，用户 2026-11-07）
    _, y0, _, y1 = dl.panel("display").polygon.bbox()
    _, b0, _, b1 = dl.panel("base").polygon.bbox()
    assert abs((y1 - y0) - (b1 - b0)) < 1e-6
    # 合上时展示盖平贴在盖外面（和盖平行）
    folded = function_check.fold(dl)
    assert folded is not None
    assert abs(float(folded.normal("display") @ folded.normal("lid"))) > 0.99


def test_options_combine_and_reject_misuse() -> None:
    dl, params = _make((150, 150, 40), "sbs_350", display_lid="rect", tear_window=False)
    assert function_check.check(dl, params)["status"] == "pass" and not dl.glue_areas
    mat = resolve_material("sbs_350")
    for bad in (
        {"kind": "open", "display_lid": "tab"},  # 不是连盖：recipe 就拒
        {"kind": "hinged_lid", "lid_on": "end", "display_lid": "tab"},
        {"kind": "hinged_lid", "lid_header": True, "tear_window": True},
    ):
        recipe = {**RECIPE, "top": bad}
        try:
            found = api.structure_params(None, None, recipe, 150, 150, 40)
            assert isinstance(found, tuple)
            issues = STYLE.validate(STYLE.param_schema()(**found[1]), mat, PROFILE)
        except ValueError:
            continue
        assert issues and issues[0].id == "S03", bad


def test_corner_radius_rounds_free_corners_only() -> None:
    from shapely.ops import unary_union

    dl, params = _make((150, 150, 40), "sbs_350")
    plain = sum(1 for q in dl.outline_segments() if q.is_arc)
    found = api.structure_params(None, None, {**RECIPE, "corner_radius": 3}, 150, 150, 40)
    assert isinstance(found, tuple)
    p = STYLE.param_schema()(**found[1])
    mat = resolve_material("sbs_350")
    rounded = STYLE.generate(p, mat, PROFILE)
    assert rounded.style_meta["structure"]["rounded_corners"] > 0
    assert sum(1 for q in rounded.outline_segments() if q.is_arc) > plain
    assert (
        not check_s01(rounded)
        + check_crease_sides(rounded)
        + check_s02(rounded, STYLE.expected_panel_sizes(p, mat, PROFILE))
    )
    outer = path_to_polygon(GPath(rounded.outline_segments(), closed=True))
    union = unary_union([path_to_polygon(q.polygon) for q in rounded.panels])
    assert union.symmetric_difference(outer).area < 1e-4  # 面拼起来 = 外轮廓
    # 压线端点不动：每条压线的两端仍在某块面的边界上
    creases = [e for e in rounded.edges if e.type == EdgeType.CREASE]
    for e in creases:
        for q in (e.segments[0].start, e.segments[-1].end):
            assert outer.buffer(1e-3).contains(
                Polygon([(q.x, q.y), (q.x + 1e-4, q.y), (q.x, q.y + 1e-4)])
            )
    assert function_check.check(rounded, found[1])["status"] == "pass"
