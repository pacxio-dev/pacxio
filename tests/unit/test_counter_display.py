"""counter_display 柜台展示盒（ 节）：几何自洽、合上不穿插、插脚落进插孔、头卡贴着后板。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point
from shapely.ops import unary_union

from pacxio.export.svg import dieline_to_svg
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.foldcheck import fold_clashes
from pacxio.mockup.folding import apply, build_meshes, build_part, panel_matrices, plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style
from pacxio.styles.base import ORIENTATION_VERIFIED

PROFILE = load_profile("default")
STYLE = get_style("counter_display")


def test_cli_can_create_counter_display_with_default_basis(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from pacxio.cli import app

    result = CliRunner().invoke(
        app,
        ["job", "init", str(tmp_path / "display"), "--style", "counter_display", "--material",
         "flute_b", "--L", "300", "--W", "200", "--H", "150", "--json"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
CASES = ((300, 200, 80, "flute_e"), (400, 250, 150, "flute_b"), (160, 120, 100, "sbs_400"))
GOLDEN = Path(__file__).resolve().parents[1] / "golden/b1"


def make(index: int, **extra: object) -> Dieline:
    L, W, H, material = CASES[index]
    p = STYLE.param_schema()(L=L, W=W, H=H, **extra)
    m = resolve_material(material)
    assert not STYLE.validate(p, m, PROFILE)
    return STYLE.generate(p, m, PROFILE)


def closed(dl: Dieline) -> dict[str, np.ndarray]:
    plan = plan_for(dl)
    assert plan is not None
    return panel_matrices(build_part(dl, plan), plan.steps)


@pytest.mark.parametrize("index", range(3))
def test_three_sizes_partition_checks_fold_and_golden(index: int) -> None:
    L, W, H, material = CASES[index]
    p, m = STYLE.param_schema()(L=L, W=W, H=H), resolve_material(material)
    dl = make(index)
    assert not (
        check_s01(dl)
        + check_s02(dl, STYLE.expected_panel_sizes(p, m, PROFILE))
        + check_crease_sides(dl)
    )
    outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
    polys = [path_to_polygon(panel.polygon) for panel in dl.panels]
    union = unary_union(polys)
    assert union.symmetric_difference(outer).area < 1e-5
    assert sum(poly.area for poly in polys) == pytest.approx(union.area)
    assert len(dl.panels) == 15
    assert not dl.style_meta["orientation_verified"] and STYLE.id not in ORIENTATION_VERIFIED
    plan = plan_for(dl)
    assert plan is not None
    part = build_part(dl, plan)
    for step in np.linspace(0, plan.steps, 11):
        assert all(np.isfinite(mat).all() for mat in panel_matrices(part, step).values())
    assert len(build_meshes(part, dl.caliper_mm)[0]) == 15
    assert fold_clashes(dl) == []
    assert dieline_to_svg(dl) == (GOLDEN / f"counter_display_{index}.svg").read_text()


@pytest.mark.parametrize("index", range(3))
def test_lugs_pass_through_base_slots(index: int) -> None:
    """内壁和头卡下端的插脚合上后穿过底板，每个插孔里都有插脚，没有插脚落在插孔外。"""
    dl = make(index)
    mats, t = closed(dl), dl.caliper_mm
    slots = [path_to_polygon(GPath(e.segments, closed=True)) for e in dl.inner_cuts()]
    assert len(slots) >= 3
    hits = [0] * len(slots)
    for pid in ("side_a_in", "side_b_in", "header_front"):
        panel = next(p for p in dl.panels if p.id == pid)
        for seg in panel.polygon.segments:
            x, y, z = apply(mats[pid], seg.start.x, seg.start.y, 0.0)
            if z <= t / 2:  # 没伸进底板的顶点
                continue
            inside = [i for i, s in enumerate(slots) if s.buffer(1e-6).covers(Point(x, y))]
            assert inside, f"{pid} 插脚顶点 ({x:.2f}, {y:.2f}) 落在插孔外"
            hits[inside[0]] += 1
    assert all(n >= 2 for n in hits)


@pytest.mark.parametrize("index", range(3))
def test_header_stands_against_back_full_height(index: int) -> None:
    dl = make(index)
    mats = closed(dl)
    meta = dl.style_meta["dims"]
    H, hh = meta["Hi"], meta["header_h"]
    back = next(p for p in dl.panels if p.id == "back")
    head = next(p for p in dl.panels if p.id == "header_front")
    bp = np.array([apply(mats["back"], s.start.x, s.start.y, 0.0) for s in back.polygon.segments])
    hp = np.array(
        [apply(mats["header_front"], s.start.x, s.start.y, 0.0) for s in head.polygon.segments]
    )
    # 两块都竖直（y 不变），头卡在后板里侧、隔一条顶卷边
    assert np.ptp(bp[:, 1]) < 1e-6 and np.ptp(hp[:, 1]) < 1e-6
    assert 0 < bp[0, 1] - hp[0, 1] <= meta["rim"]
    # 后板高 H + hh；头卡从顶一直到底板
    assert np.ptp(bp[:, 2]) == pytest.approx(H + hh)
    assert hp[:, 2].min() == pytest.approx(bp[:, 2].min())
    assert hp[:, 2].max() > 0


def test_front_cut_defaults_fit_and_options() -> None:
    m = resolve_material("flute_e")
    # 小尺寸默认缺口宽自动收窄，不会让默认参数报错
    p = STYLE.param_schema()(L=160, W=120, H=100)
    assert not STYLE.validate(p, m, PROFILE)
    # 不挖：前板是整块矩形
    dl = make(0, cut_d=0)
    front = next(p for p in dl.panels if p.id == "front")
    assert len(front.polygon.segments) == 4
    # 缺口太宽报错，并给修复建议
    bad = STYLE.param_schema()(L=300, W=200, H=80, cut_w=280)
    issues = STYLE.validate(bad, m, PROFILE)
    assert issues and issues[0].fix
    # 头卡高可调
    tall = make(0, header_h=300)
    assert tall.style_meta["dims"]["outer"][2] == pytest.approx(80 + 300)


def test_rejects_tiny_size() -> None:
    tiny = STYLE.param_schema()(L=50, W=200, H=80)
    assert STYLE.validate(tiny, resolve_material("flute_e"), PROFILE)
