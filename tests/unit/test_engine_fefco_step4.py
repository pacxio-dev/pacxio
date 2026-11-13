"""几何引擎第 4 步：FEFCO 06 三片式（片材端翼、组件件尺寸）和 08 展示（斜口展示顶、取货口、撕拉条、
前墙矮的角翼 / 连角托盘、多边形陈列桶、头卡、展架组件）。"""

from __future__ import annotations

from typing import Any

import pytest

from pacxio.engine import fefco
from pacxio.engine.recipe import StructureParams
from pacxio.engine.sets import SetParams, generate_set
from pacxio.mockup.folding import plan_for
from pacxio.model.dieline import Dieline, EdgeType
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style

PROFILE = load_profile("default")
FLUTE = resolve_material("flute_b")
STYLE = get_style("structure")


def make(code: str, size: tuple[float, float, float] = (400, 300, 250)) -> tuple[Any, Dieline]:
    p = STYLE.param_schema()(**fefco.recipe(code, *size))
    assert not STYLE.validate(p, FLUTE, PROFILE), STYLE.validate(p, FLUTE, PROFILE)
    return p, STYLE.generate(p, FLUTE, PROFILE)


def test_every_fefco_code_now_has_a_recipe() -> None:
    """FEFCO 第 12 版 325 款全部有配方。"""
    assert len(fefco.SERIES_06) == 11 and len(fefco.SERIES_08) == 49
    have = (set(fefco.SHEETS) | set(fefco.STYLES) | set(fefco.TRAYS) | set(fefco.SLEEVES) | set(fefco.SETS)
            | set(fefco.MAILERS) | set(fefco.FOLDERS) | set(fefco.INSERTS) | set(fefco.NATIVE))  # fmt: skip
    assert len(have) == 325
    assert set(fefco.SERIES_06) | set(fefco.SERIES_08) <= have


def test_strip_flaps_fold_into_the_ends() -> None:
    """三片式盒身：每条两头的端翼折 90° 进端面，折回偏差 0；翼的压线在条的两头。"""
    p = StructureParams(body={"form": "sheet", "L": 300, "W": 200, "H": 150, "crease": "along_L",
                              "panels": ["H", "W", "H", "W"], "folds": [90, 90, 90],
                              "strip_flaps": {"panels": [0, 1, 2]}})  # fmt: skip
    dl = STYLE.generate(p, FLUTE, PROFILE)
    flaps = [q.id for q in dl.panels if "_flap_" in q.id]
    assert len(flaps) == 6 and not any(f.startswith("panel_4") for f in flaps)
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    creases = {e.id: e.panels for e in dl.edges if e.type == EdgeType.CREASE}
    assert creases["crease_panel_1_flap_a"] == ["panel_1", "panel_1_flap_a"]
    deep = StructureParams(body={"form": "sheet", "L": 300, "W": 200, "H": 150, "crease": "along_L",
                                 "panels": ["H", "W"], "folds": [90], "strip_flaps": {"depth": 80}})  # fmt: skip
    assert STYLE.validate(deep, FLUTE, PROFILE)  # 翼比条宽一半还深


def test_three_piece_kits_size_their_parts() -> None:
    """06：盒身按盒子尺寸，端板（W × H × v 的浅托）按式子定，做两块；反查出编号。"""
    for code in ("0601", "0607", "0621"):
        sp = SetParams(**fefco.set_recipe(code, 400, 300, 250))
        assert fefco.set_code(sp) == code
        assert sp.counts["end"] == 2
    sp = SetParams(**fefco.set_recipe("0610", 400, 300, 250))
    end = sp.parts["end"].body
    assert (end.L, end.W, end.H) == (300, 250, 30)
    parts = generate_set(sp, FLUTE, PROFILE, STYLE)
    assert parts["end"].style_meta["set"]["count"] == 2


def test_slope_top_cuts_the_walls() -> None:
    """斜口展示：正面墙矮到 v、端墙梯形，背面整高；压线跟着墙高缩短。"""
    p, dl = make("0801")
    hb = dl.panel("back").polygon.bbox()
    front = dl.panel("front").polygon.bbox()
    assert front[3] - front[1] < 0.5 * (hb[3] - hb[1])
    assert len(dl.panel("side_a").polygon.segments) == 4
    assert dl.style_meta["structure"]["fefco"] == "0801"
    bad = STYLE.param_schema()(body={"base": "rect", "L": 400, "W": 300, "H": 250},
                               top={"kind": "slope", "depth": 300}, bottom={"kind": "flaps"})  # fmt: skip
    assert STYLE.validate(bad, FLUTE, PROFILE)


def test_dispense_window_and_tear_strip() -> None:
    _, d25 = make("0825")
    assert any(e.id == "cut_window_front" for e in d25.edges)
    _, d86 = make("0886")
    perfs = [e for e in d86.edges if e.type == EdgeType.PERF]
    assert {e.id for e in perfs} == {"perf_top_tear_1", "perf_top_tear_2"} and all(
        e.panels == ["top_lid"] for e in perfs
    )
    with pytest.raises(ValueError):
        StructureParams(body={"base": "rect", "L": 400, "W": 300, "H": 250},
                        top={"kind": "flaps", "tear_strip": True})  # fmt: skip


@pytest.mark.parametrize("code", ["0802", "0803", "0808", "0817"])
def test_low_front_trays_shrink_the_front_corners(code: str) -> None:
    """前墙矮的展示托盘：前面两个墙角的角翼 / 连角缩到前墙高，后面的长边角翼顺着端墙斜下来。"""
    _, dl = make(code, (400, 300, 160))
    front = dl.panel("front").polygon.bbox()
    v = front[3] - front[1]
    for q in dl.panels:
        if "corner_front" in q.id or "gusset_front" in q.id:
            x0, y0, x1, y1 = q.polygon.bbox()
            assert min(x1 - x0, y1 - y0) <= v + 1e-6, q.id
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6


def test_polygon_bins_headers_and_stands() -> None:
    _, d58 = make("0858")
    assert d58.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    _, d81 = make("0881", (400, 300, 250))
    assert d81.style_meta["structure"]["fefco"] == "0881"
    sp = SetParams(**fefco.set_recipe("0845", 400, 300, 1200))
    assert sp.counts["shelf"] == 4 and sp.parts["shelf"].body.poly_sides == 8
    assert fefco.set_code(sp) == "0845"
    parts = generate_set(sp, FLUTE, PROFILE, STYLE)
    assert all(plan_for(dl) is not None for dl in parts.values())
