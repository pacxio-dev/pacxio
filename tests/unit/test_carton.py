"""carton 组合彩盒（ 节）：已有的组合和原盒型逐面逐线一致，新组合几何自洽、合上不穿插。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely.ops import unary_union

from pacxio.export.svg import dieline_to_svg
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.foldcheck import fold_clashes
from pacxio.mockup.folding import build_part, panel_matrices, plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style
from pacxio.styles.carton import BOTTOMS, EQUIVALENT, TOPS, catalog_combos

PROFILE = load_profile("default")
STYLE = get_style("carton")
MATERIAL = resolve_material("sbs_350")
GOLDEN = Path(__file__).resolve().parents[1] / "golden/b1"
DIMS = {"L": 100, "W": 60, "H": 150}


def make(top: str, bottom: str, **dims: Any) -> Dieline:
    p = STYLE.param_schema()(**(dims or DIMS), top=top, bottom=bottom)
    assert not STYLE.validate(p, MATERIAL, PROFILE), (top, bottom)
    return STYLE.generate(p, MATERIAL, PROFILE)


def geometry(dl: Dieline) -> tuple[dict[str, Any], dict[str, Any]]:
    def segs(items: Any) -> list[tuple[float, ...]]:
        return [
            tuple(round(v, 6) for v in (s.start.x, s.start.y, s.end.x, s.end.y, s.bulge))
            for s in items
        ]

    panels = {p.id: (p.role, segs(p.polygon.segments)) for p in dl.panels}
    edges = {e.id: (e.type, segs(e.segments)) for e in dl.edges}
    return panels, edges


@pytest.mark.parametrize(("combo", "style_id"), list(EQUIVALENT.items()))
def test_existing_combinations_reproduce_the_registered_styles(
    combo: tuple[str, str], style_id: str
) -> None:
    dl = make(*combo)
    other = get_style(style_id)
    extra = {"hang_hole": "euro"} if style_id.endswith("_hanger") else {}
    ref = other.generate(other.param_schema()(**DIMS, **extra), MATERIAL, PROFILE)
    ours, theirs = geometry(dl), geometry(ref)
    if style_id == "gable":  # 组合里锁底一律用圆弧糊口端（同 snaplock），只有糊口和外轮廓不同
        for part in (ours, theirs):
            part[0].pop("glue")
            part[1].pop("cut_outline")
    assert ours == theirs


@pytest.mark.parametrize("top", TOPS)
@pytest.mark.parametrize("bottom", BOTTOMS)
def test_every_combination_is_consistent_and_closes_without_clashes(top: str, bottom: str) -> None:
    for dims in (DIMS, {"L": 160, "W": 100, "H": 120}):
        p = STYLE.param_schema()(**dims, top=top, bottom=bottom)
        dl = make(top, bottom, **dims)
        assert not (
            check_s01(dl)
            + check_s02(dl, STYLE.expected_panel_sizes(p, MATERIAL, PROFILE))
            + check_crease_sides(dl)
        )
        outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
        polys = [path_to_polygon(panel.polygon) for panel in dl.panels]
        union = unary_union(polys)
        assert union.symmetric_difference(outer).area < 1e-5
        assert sum(poly.area for poly in polys) == pytest.approx(union.area)
        plan = plan_for(dl)
        assert plan is not None and set(plan.moves) <= {panel.id for panel in dl.panels}
        part = build_part(dl, plan)
        for step in np.linspace(0, plan.steps, 7):
            assert all(np.isfinite(m).all() for m in panel_matrices(part, step).values())
        assert fold_clashes(dl) == []
        assert dl.style_meta["carton"] == {
            "top": top,
            "bottom": bottom,
            "same_as": EQUIVALENT.get((top, bottom)),
        }
        assert dl.style_meta["accuracy_level"] == "L0"


@pytest.mark.parametrize("combo", catalog_combos())
def test_new_combinations_match_their_golden_dielines(combo: tuple[str, str]) -> None:
    dl = make(*combo)
    golden = GOLDEN / f"carton_{combo[0]}_{combo[1]}.svg"
    assert dieline_to_svg(dl) == golden.read_text()


def test_catalog_lists_only_new_combinations() -> None:
    combos = catalog_combos()
    assert len(combos) == len(TOPS) * len(BOTTOMS) - len(EQUIVALENT) == 10
    assert not set(combos) & set(EQUIVALENT)


def test_invalid_end_parameters_are_rejected() -> None:
    schema = STYLE.param_schema()
    assert STYLE.validate(
        schema(**DIMS, top="gable", bottom="tuck", handle_hole_w=200), MATERIAL, PROFILE
    )
    assert STYLE.validate(
        schema(**DIMS, top="seal", bottom="tuck", end_overlap=80), MATERIAL, PROFILE
    )
    assert STYLE.validate(
        schema(L=60, W=100, H=150, top="tuck", bottom="autolock"), MATERIAL, PROFILE
    )


# ---------------- 附件（模切选项，不是新条目）----------------


def test_thumb_notch_cuts_a_half_round_into_the_free_front_edge() -> None:
    plain = make("tuck", "tuck")
    dl = make("tuck", "tuck", **DIMS, thumb_notch=True, thumb_w=30)
    arcs = [s for s in dl.panel("front").polygon.segments if s.is_arc]
    assert len(arcs) == 1 and arcs[0].start.dist(arcs[0].end) == pytest.approx(30)
    area = path_to_polygon(dl.panel("front").polygon).area
    full = path_to_polygon(plain.panel("front").polygon).area
    assert area == pytest.approx(full - np.pi * 15**2 / 2, rel=1e-3)
    assert not check_s01(dl) and fold_clashes(dl) == []
    assert dl.style_meta["carton"]["attachments"] == ["thumb_notch"]
    p = STYLE.param_schema()(**DIMS, top="seal", bottom="tuck", thumb_notch=True)
    assert STYLE.validate(p, MATERIAL, PROFILE)  # 只有插舌顶有空着的正面上沿


def test_tear_strip_runs_round_the_body_with_bridges_at_every_crease() -> None:
    dl = make("seal", "autolock", **DIMS, tear_strip=True, tear_w=10)
    perfs = [e for e in dl.edges if e.type.value == "perf"]
    assert {e.panels[0] for e in perfs} == {"glue", "back", "side_a", "front", "side_b"}
    ys = {round(s.start.y, 6) for e in perfs for s in e.segments}
    assert len(ys) == 2 and max(ys) - min(ys) == pytest.approx(10)
    xs = sorted(s.start.x for e in perfs for s in e.segments) + [
        s.end.x for e in perfs for s in e.segments
    ]
    creases = [e.segments[0].start.x for e in dl.edges if e.id in (
        "crease_glue_back", "crease_back_side_a", "crease_side_a_front", "crease_front_side_b")]  # fmt: skip
    assert all(min(abs(x - c) for x in xs) >= 2 - 1e-9 for c in creases)  # 压线两边都留桥
    tab = next(e for e in dl.edges if e.id == "cut_tear_tab")
    assert tab.segments[0].is_arc and tab.panels == ["front"]
    assert not check_s01(dl) and fold_clashes(dl) == []
    p = STYLE.param_schema()(**DIMS, top="tuck", bottom="tuck", tear_strip=True, tear_y=2)
    assert STYLE.validate(p, MATERIAL, PROFILE)  # 撕拉条贴着压线放不下


def test_attachments_are_options_not_new_catalog_entries() -> None:
    assert len(catalog_combos()) == 10  # 开窗 / 缺口 / 撕拉条不增加条目
    plain = make("tuck", "tuck")
    assert plain.style_meta["carton"]["same_as"] == "rte"
    assert "attachments" not in plain.style_meta["carton"]


def test_windows_work_on_a_composed_carton(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from pacxio import dielines
    from pacxio.cli import app
    from pacxio.model.job import Job

    runner = CliRunner()
    job = tmp_path / "box"
    init = ["job", "init", str(job), "--style", "carton", "--L", "100", "--W", "60", "--H", "150",
            "--set", "top=gable", "--set", "bottom=autolock", "--json"]  # fmt: skip
    assert runner.invoke(app, init).exit_code == 0
    add = ["window", "add", str(job), "--panel", "front", "--box", "20,40,60,50", "--json"]
    assert runner.invoke(app, add).exit_code == 0
    dl, errors = dielines.build_dieline_for(Job.load(job))
    assert dl is not None and not errors and len(dl.windows) == 1
