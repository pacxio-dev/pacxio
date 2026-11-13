"""几何引擎第 0 步（）：配方 → 刀版 + 自动折法；折回去要和立体重合。"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from pacxio import api
from pacxio.engine.net import build_net, outline
from pacxio.engine.recipe import StructureParams
from pacxio.engine.solid import Prism, offset_polygon, rect_polygon, regular_polygon
from pacxio.engine.style import fold_deviation
from pacxio.mockup.folding import plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02
from pacxio.styles import all_styles, get_style
from pacxio.styles.compensation import coefficients

PROFILE = load_profile("default")
CARTON = resolve_material("sbs_350")
STYLE = get_style("structure")
RECIPES = {
    "rect_side": {"body": {"base": "rect", "L": 80, "W": 50, "H": 120},
                  "top": {"kind": "lid_tuck", "on": 1}, "bottom": {"kind": "lid_tuck", "on": 3}},
    "rect_flaps": {"body": {"base": "rect", "L": 300, "W": 200, "H": 200},
                   "top": {"kind": "flaps"}, "bottom": {"kind": "flaps"}},
    "rect_full": {"body": {"base": "rect", "L": 300, "W": 200, "H": 200},
                  "top": {"kind": "flaps", "flap": "full"}, "bottom": {"kind": "flaps", "flap": "overlap", "overlap": 40}},
    "rect_glue": {"body": {"base": "rect", "L": 60, "W": 60, "H": 90},
                  "top": {"kind": "lid_glue", "on": 1}, "bottom": {"kind": "open"}},
    "hex_tuck": {"body": {"base": "regular", "sides": 6, "edge": 40, "H": 100},
                 "top": {"kind": "lid_tuck", "on": 0}, "bottom": {"kind": "lid_glue", "on": 0}},
    "oct_flaps": {"body": {"base": "regular", "sides": 8, "edge": 30, "H": 90},
                  "top": {"kind": "flaps"}, "bottom": {"kind": "lid_glue", "on": 0}},
    "tri_tuck": {"body": {"base": "regular", "sides": 3, "edge": 70, "H": 120},
                 "top": {"kind": "lid_tuck", "on": 0}, "bottom": {"kind": "lid_glue", "on": 1}},
    "penta_tuck": {"body": {"base": "regular", "sides": 5, "edge": 45, "H": 80},
                   "top": {"kind": "lid_tuck", "on": 2}, "bottom": {"kind": "flaps"}},
}  # fmt: skip


def make(recipe: dict[str, object]) -> Dieline:
    p = STYLE.param_schema()(**recipe)
    assert not STYLE.validate(p, CARTON, PROFILE), STYLE.validate(p, CARTON, PROFILE)
    return STYLE.generate(p, CARTON, PROFILE)


def test_solid_helpers() -> None:
    hexagon = Prism(tuple(regular_polygon(6, 40)), 100)
    assert all(math.isclose(hexagon.fold_deg(i), 60) for i in range(6))
    assert math.isclose(hexagon.width(0), 40)
    mid = Prism(tuple(offset_polygon(rect_polygon(80, 50), 0.24)), 120)
    assert [round(mid.width(i), 6) for i in range(4)] == [
        80.48,
        50.48,
        80.48,
        50.48,
    ]  # 墙宽 = 内尺寸 + k·t
    with pytest.raises(ValueError):
        Prism(((0, 0), (1, 1), (0, 1), (1, 0)), 10)  # 自交 / 顺时针


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_recipe_folds_back_onto_its_solid(name: str) -> None:
    dl = make(RECIPES[name])
    p = STYLE.param_schema()(**RECIPES[name])
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, CARTON, PROFILE))
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6  # 墙和盖折好后正好落在立体上
    plan = plan_for(dl)
    assert plan is not None and plan.steps >= 4  # 折法来自配方 / 积木，不查表


def test_fold_check_catches_a_wrong_angle() -> None:
    p = StructureParams(**RECIPES["hex_tuck"])  # type: ignore[arg-type]
    dl = STYLE.generate(p, CARTON, PROFILE)
    net = build_net(p, CARTON.caliper_mm, coefficients(PROFILE, CARTON, True), False)
    x0, y0, _, _ = outline(net).bbox()
    targets = [(pid, (x - x0, y - y0), t) for pid, (x, y), t in net.targets]
    assert fold_deviation(dl, targets) < 1e-6
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["wall_2"]["keys"] = [[2, 90.0]]  # 六角盒的墙应该折 60°
    assert fold_deviation(broken, targets) > 5


def test_polygon_lids_do_not_overlap_neighbouring_flaps() -> None:
    """六角的盖比它挂的那面墙宽：旁边墙上的底翼碰到盖就被裁掉或去掉，刀版上没有两块面重叠。"""
    from shapely.geometry import Polygon

    dl = make(RECIPES["hex_tuck"])

    polys = {p.id: Polygon([(s.start.x, s.start.y) for s in p.polygon.segments]) for p in dl.panels}
    names = sorted(polys)
    assert all(
        polys[a].intersection(polys[b]).area < 0.01
        for i, a in enumerate(names)
        for b in names[i + 1 :]
    )


def _one_per_style(suite: str) -> list[tuple[str, str, dict[str, object]]]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from structure_baseline import cases

    seen: dict[str, tuple[str, str, dict[str, object]]] = {}
    for style, material, values in cases(suite):
        seen.setdefault(style, (style, material, values))
    return list(seen.values())


@pytest.mark.parametrize(("style", "material", "values"), _one_per_style("c1"))
def test_engine_reproduces_existing_entries(
    style: str, material: str, values: dict[str, object]
) -> None:
    """现有入口是标准答案：能写成配方的，引擎生成的刀版逐面、逐线一样，还带折法；彩盒筒身折回自检为 0。
    （全量对比：tools/engine_compare.py，基线 104 组里 100 组一样，另外 4 组是多件盒型。）"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from engine_compare import geometry, recipe_for, same

    recipe = recipe_for(style, values)
    if recipe is None:
        pytest.skip(f"{style} 还没有配方（多件 / 平面卡片，后面几步）")
    mat = resolve_material(material)
    old_style = get_style(style)
    old = old_style.generate(old_style.param_schema()(**values), mat, PROFILE)
    p = STYLE.param_schema()(**recipe)
    assert not STYLE.validate(p, mat, PROFILE)
    new = STYLE.generate(p, mat, PROFILE)
    assert same(geometry(old), geometry(new))
    assert new.style_meta["structure"]["calibrated_from"] == style and plan_for(new) is not None
    dev = new.style_meta["structure"].get("fold_deviation_mm")
    if style.startswith("fefco_02"):  # 开槽箱最后一面故意短一层纸厚（接头让位）：偏差约 0.8t
        assert dev is not None and dev <= mat.caliper_mm
    elif dev is not None:
        assert dev < 1e-6


@pytest.mark.parametrize(("recipe", "material", "expect"), [
    ({"body": {"form": "tray", "base": "rect", "L": 240, "W": 170, "H": 70}, "top": {"kind": "mailer"}, "bottom": {"kind": "open"}}, "flute_e", "fefco_0427"),
    ({"body": {"form": "tray", "base": "rect", "L": 200, "W": 150, "H": 40}, "top": {"kind": "open"}, "bottom": {"kind": "open"}}, "sbs_350", "tray_glued"),
    ({"body": {"form": "tray", "corners": "lock", "base": "rect", "L": 200, "W": 150, "H": 40}, "top": {"kind": "open"}, "bottom": {"kind": "open"}}, "sbs_350", "tray_lock"),
    ({"body": {"form": "tray", "base": "rect", "L": 200, "W": 150, "H": 40}, "top": {"kind": "hinged_lid"}, "bottom": {"kind": "open"}}, "sbs_350", "hinged_lid"),
    ({"body": {"base": "rect", "L": 300, "W": 200, "H": 200}, "top": {"kind": "flaps"}, "bottom": {"kind": "autolock"}}, "flute_b", "crashlock_rsc"),
    ({"body": {"base": "rect", "L": 80, "W": 50, "H": 120}, "top": {"kind": "gable"}, "bottom": {"kind": "seal"}}, "sbs_350", "carton"),
])  # fmt: skip
def test_recipes_map_onto_calibrated_mechanisms(
    recipe: dict[str, object], material: str, expect: str
) -> None:
    mat = resolve_material(material)
    p = STYLE.param_schema()(**recipe)
    assert not STYLE.validate(p, mat, PROFILE), STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert dl.style == "structure" and dl.style_meta["structure"]["calibrated_from"] == expect
    assert plan_for(dl) is not None and not check_s01(dl)


def test_unmatched_recipes_fall_back_or_explain() -> None:
    side_lid = {
        "body": {"base": "rect", "L": 80, "W": 50, "H": 120},
        "top": {"kind": "lid_tuck", "on": 1},
        "bottom": {"kind": "lid_tuck", "on": 3},
    }  # 盖挂在侧面：校准过的做法里没有，引擎原生生成
    dl = make(side_lid)
    assert (
        dl.style_meta["structure"]["native"]
        and dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    )
    hex_lock = STYLE.param_schema()(body={"base": "regular", "sides": 6, "edge": 40, "H": 100},
                                    top={"kind": "lid_tuck"}, bottom={"kind": "autolock"})  # fmt: skip
    assert [i.id for i in STYLE.validate(hex_lock, CARTON, PROFILE)] == [
        "S03"
    ]  # 多边形自动锁底还没有


@pytest.mark.pro
def test_engine_style_is_experimental_and_runs_the_pipeline(tmp_path: Path) -> None:
    from pacxio.mockup.service import make_mockup

    assert "structure" not in {s.id for s in all_styles()}  # 不进结构目录
    directory = tmp_path / "hex"
    assert api.init_job(directory, "structure", RECIPES["hex_tuck"], "sbs_350", "default").ok
    result = make_mockup(directory)
    assert result.ok, result.errors
    assert (directory / "mockup" / "view_front_right.png").exists()


def test_recipe_validation() -> None:
    with pytest.raises(ValueError):
        StructureParams(body={"base": "rect", "L": 80, "H": 100})  # type: ignore[arg-type]  # 缺 W
    bad = STYLE.param_schema()(body={"base": "regular", "sides": 4, "edge": 50, "H": 80},
                               top={"kind": "lid_tuck", "on": 7})  # fmt: skip
    assert [i.id for i in STYLE.validate(bad, CARTON, PROFILE)] == ["S03"]
    board = resolve_material("pouch_kraft_pe")
    assert STYLE.validate(STYLE.param_schema()(**RECIPES["rect_side"]), board, PROFILE)
