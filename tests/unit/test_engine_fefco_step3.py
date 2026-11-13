"""几何引擎第 3 步：FEFCO 07 预糊箱（自动锁底积木、开窗 / 撕开展示、预糊托盘）和 09 内衬（片材式子、
插槽、开孔、台阶、内托、隔板组）。"""

from __future__ import annotations

from typing import Any

import pytest

from pacxio.engine import fefco, function_check
from pacxio.engine.recipe import StructureParams
from pacxio.mockup.folding import plan_for
from pacxio.model.dieline import Dieline, EdgeType
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style

PROFILE = load_profile("default")
FLUTE = resolve_material("flute_b")
STYLE = get_style("structure")


def make(code: str, size: tuple[float, float, float] = (400, 300, 250)) -> tuple[Any, Dieline]:
    p = STYLE.param_schema()(**fefco.recipe(code, *size))
    assert not STYLE.validate(p, FLUTE, PROFILE), STYLE.validate(p, FLUTE, PROFILE)
    return p, STYLE.generate(p, FLUTE, PROFILE)


def test_every_07_and_09_code_has_a_recipe() -> None:
    assert len(fefco.SERIES_07) == 42 and len(fefco.SERIES_09) == 55
    have = (set(fefco.STYLES) | set(fefco.TRAYS) | set(fefco.SETS) | set(fefco.INSERTS)
            | set(fefco.NATIVE))  # fmt: skip
    assert set(fefco.SERIES_07) <= have and set(fefco.SERIES_09) <= have


@pytest.mark.parametrize("code", [*fefco.INSERTS, *fefco.NATIVE])
def test_inserts_generate_fold_and_look_up(code: str) -> None:
    """09 内衬片材 / 0730 六角筒：生成、过刀版检查、折回偏差 0；内衬按式子反查出这一款。"""
    p, dl = make(code, (300, 200, 100))
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, FLUTE, PROFILE))
    info = dl.style_meta["structure"]
    assert plan_for(dl) is not None and info["fold_deviation_mm"] < 1e-6
    if code in fefco.INSERTS:
        assert info["fefco"] == code


def test_insert_panels_follow_the_box_size() -> None:
    """条宽写成式子：改盒子尺寸，内衬跟着变；01 的数值片材照旧按 01 认。"""
    from pacxio.engine import sheet

    small = StructureParams(**fefco.recipe("0907", 300, 200, 100))
    big = StructureParams(**fefco.recipe("0907", 500, 400, 150))
    assert sheet.strips(small) == [150, 100, 300, 100, 150]
    assert sheet.strips(big) == [250, 150, 500, 150, 250]
    assert fefco.sheet_code(StructureParams(**fefco.recipe("0112", 400, 300, 120))) == "0112"
    with pytest.raises(ValueError):
        StructureParams(body={"form": "sheet", "L": 300, "W": 200, "H": 100, "panels": ["2X"]})


def test_partitions_have_slots_from_opposite_edges() -> None:
    """隔板组：纵隔板从上沿开槽、横隔板从下沿开槽，槽数 = 对方片数，深到一半高。"""
    from pacxio.engine.sets import SetParams, generate_set

    sp = SetParams(**fefco.set_recipe("0933", 300, 200, 150))
    assert sp.counts == {"long": 2, "cross": 3}
    parts = generate_set(sp, FLUTE, PROFILE, STYLE)
    for name, n in (("long", 3), ("cross", 2)):
        dl = parts[name]
        assert dl.style_meta["set"]["count"] == sp.counts[name]
        outline = next(e for e in dl.edges if e.id == "outline" or e.type == EdgeType.CUT)
        pts = {(round(s.start.x, 3), round(s.start.y, 3)) for s in outline.segments}
        depth_x = {x for x, _ in pts if 1 < x < 149}
        assert depth_x == {75.0}, (name, depth_x)  # 槽底在半高（顺 H 的条，x 是高度方向）
        assert len([q for q in pts if q[0] == 75.0]) == 2 * n


def test_sheet_holes_and_steps() -> None:
    _, d52 = make("0952", (300, 200, 100))
    holes = [e for e in d52.edges if e.id.startswith("cut_hole")]
    assert len(holes) == 2 and all(e.panels == ["panel_2"] for e in holes)
    p = StructureParams(body={"form": "sheet", "L": 300, "W": 200, "H": 120, "crease": "along_H",
                              "panels": ["L"], "steps": 3})  # fmt: skip
    dl = STYLE.generate(p, FLUTE, PROFILE)
    outline = dl.panels[0].polygon
    assert len(outline.segments) == 8  # 三级台阶：直角折线 8 段
    bad = StructureParams(body={"form": "sheet", "L": 100, "W": 200, "H": 100, "crease": "along_H",
                                "panels": ["L"], "slots": {"count": 12}})  # fmt: skip
    assert STYLE.validate(bad, FLUTE, PROFILE)  # 槽太密


@pytest.mark.parametrize(("code", "glue"), [("0700", {"btm_glue_back", "btm_glue_front"}),
                                            ("0701", {"btm_glue_sa", "btm_glue_sb"}),
                                            ("0706", {"btm_glue_back", "btm_glue_front"})])  # fmt: skip
def test_autolock_forms(code: str, glue: set[str]) -> None:
    """三种自动锁底：糊胶三角挂在哪两片翼上、斜压线连着；tab 那片长翼整幅深、对面墙脚开插缝。"""
    _, dl = make(code)
    ids = {q.id for q in dl.panels}
    assert glue <= ids and not ({"btm_glue_back", "btm_glue_sa"} - glue) & ids
    creases = {e.id: e.panels for e in dl.edges if e.type == EdgeType.CREASE}
    for g in glue:
        assert any(ps[1] == g for ps in creases.values())
    if code == "0701":
        _, y0, _, y1 = dl.panel("btm_front").polygon.bbox()
        _, b0, _, b1 = dl.panel("btm_back").polygon.bbox()
        assert y1 - y0 > 1.5 * (b1 - b0)  # 整幅深 vs ½W
        assert any(e.id == "cut_btm_lock_slit" and e.panels == ["back"] for e in dl.edges)


def test_autolock_at_both_ends_window_and_tear() -> None:
    _, d22 = make("0722")
    ids = {q.id for q in d22.panels}
    assert {"top_glue_back", "btm_glue_back"} <= ids  # 两头都是自动锁底
    window = next(e for e in d22.edges if e.id == "cut_window_front")
    assert window.segments[0].start == window.segments[-1].end  # 八角窗闭合
    _, d28 = make("0728")
    perfs = {e.id: e.panels for e in d28.edges if e.type == EdgeType.PERF}
    assert perfs == {
        "perf_tear_back": ["back"],
        "perf_tear_side_a": ["side_a"],
        "perf_tear_front": ["front"],
    }
    _, d40 = make("0740")
    assert {e.id for e in d40.edges if e.type == EdgeType.PERF} == {
        "perf_tap_back",
        "perf_tap_flap_back",
    }


def test_autolock_refuses_what_does_not_fit() -> None:
    def issues(**body: Any) -> list[str]:
        p = STYLE.param_schema()(body={"base": "rect", "L": 400, "W": 300, "H": 250, **body},
                                 top={"kind": "open"}, bottom={"kind": "autolock", "autolock_form": "tab"})  # fmt: skip
        return [i.message for i in STYLE.validate(p, FLUTE, PROFILE)]

    assert any("W=300 > L=200" in m for m in issues(L=200))  # 开槽箱本身就要 L ≥ W
    carton = STYLE.param_schema()(body={"base": "rect", "L": 60, "W": 40, "H": 100}, top={"kind": "open"},
                                  bottom={"kind": "autolock", "autolock_form": "web"})  # fmt: skip
    # 结构不分材料（§20）：卡纸也能做 web / tab 锁底，折得回、机构核对通过
    sbs = resolve_material("sbs_350")
    assert not STYLE.validate(carton, sbs, PROFILE)
    folded = STYLE.generate(carton, sbs, PROFILE)
    assert folded.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert function_check.check(folded, carton.model_dump())["status"] == "pass"
    with pytest.raises(ValueError):
        StructureParams(body={"base": "rect", "L": 400, "W": 300, "H": 250},
                        top={"kind": "flaps", "autolock_form": "web"})  # fmt: skip


def test_inside_trays_are_smaller_than_the_box() -> None:
    """内托：外尺寸 = 盒内尺寸 − 间隙；和同样墙角的 04 托盘不混。"""
    _, d11 = make("0911", (400, 300, 100))
    _, d52 = make("0452", (400, 300, 100))
    dims, plain = d11.style_meta["dims"], d52.style_meta["dims"]
    assert dims["Li"] == pytest.approx(400 - 3 - 2 * 3) and plain["Li"] == 400
    assert (
        d11.style_meta["structure"]["fefco"] == "0911"
        and d52.style_meta["structure"]["fefco"] == "0452"
    )
    _, d55 = make("0955", (400, 300, 100))
    front = d55.panel("front").polygon
    assert len(front.segments) == 4 and front.bbox()[2] - front.bbox()[0] > 0  # 梯形墙


def test_sets_in_07_and_09_look_up() -> None:
    from pacxio.engine.sets import SetParams

    for code in ("0714", "0715", "0724", "0929", "0935", "0960"):
        assert fefco.set_code(SetParams(**fefco.set_recipe(code, 400, 300, 200))) == code
