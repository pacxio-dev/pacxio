"""刀模圆角（engine/rounding.apply_finish）：卡纸默认外角 2 / 内角 1，瓦楞不倒，项目里可调或关掉。"""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.ops import unary_union

from pacxio import api
from pacxio.dielines import build_dieline_for
from pacxio.engine.rounding import INNER_MIN_MM, apply_finish, finish_radii
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.model.job import CornerFinish, Job
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01
from pacxio.styles import get_style


def _rte(material: str = "sbs_350"):
    style = get_style("rte")
    mat = resolve_material(material)
    prof = load_profile("default")
    p = style.param_schema().model_validate({"L": 80, "W": 50, "H": 120})
    return style.generate(p, mat, prof)


def _arc_radii(dl) -> list[float]:
    return [s.arc_info()[1] for e in dl.edges if e.type == "cut" for s in e.segments if s.is_arc]


def test_defaults_by_material() -> None:
    assert finish_radii("folding_carton") == (2.0, 1.0)
    assert finish_radii("corrugated") == (0.0, 0.0)
    assert finish_radii("folding_carton", CornerFinish(outer=0, inner=0.5)) == (0.0, 0.5)
    assert finish_radii("corrugated", CornerFinish(inner=1)) == (0.0, 1.0)


@pytest.mark.parametrize(
    "style_id", ["rte", "ste", "snaplock", "autolock", "tray_lock", "seal_end", "gable"]
)
def test_card_styles_round_without_breaking(style_id: str) -> None:
    style = get_style(style_id)
    mat = resolve_material("sbs_350")
    prof = load_profile("default")
    dl = style.generate(
        style.param_schema().model_validate({"L": 80, "W": 50, "H": 120}), mat, prof
    )
    before = len(_arc_radii(dl))
    rec = apply_finish(dl, "folding_carton")
    assert rec["outer_corners"] > 0 and rec["inner_corners"] > 0
    assert len(_arc_radii(dl)) == before + rec["outer_corners"] + rec["inner_corners"]
    assert not check_s01(dl) and not check_crease_sides(dl)
    # 面拼起来仍等于外轮廓
    outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
    union = unary_union([path_to_polygon(p.polygon) for p in dl.panels])
    assert union.symmetric_difference(outer).area < 1e-3
    # 内角放不下 1 mm 的缩小，但不小于下限
    small = [r for r in _arc_radii(dl) if r < 1.0 - 1e-6]
    assert all(r >= INNER_MIN_MM - 1e-6 for r in small)


def test_crease_end_corners_get_small_radius() -> None:
    dl = _rte()
    apply_finish(dl, "folding_carton")
    radii = sorted({round(r, 2) for r in _arc_radii(dl) if r < 1.0 - 1e-6})
    assert radii and min(radii) >= INNER_MIN_MM  # 压线端头的凹角也倒了（缩小到放得下）


def test_zero_means_sharp_and_corrugated_untouched() -> None:
    dl = _rte()
    n = len(_arc_radii(dl))
    rec = apply_finish(dl, "folding_carton", CornerFinish(outer=0, inner=0))
    assert rec["outer_corners"] == rec["inner_corners"] == 0 and len(_arc_radii(dl)) == n
    dl2 = _rte()
    rec2 = apply_finish(dl2, "corrugated")
    assert rec2 == {"outer": 0.0, "inner": 0.0, "outer_corners": 0, "inner_corners": 0}
    assert dl2.style_meta["corner_finish"] == rec2


def _card_job(tmp_path: Path) -> Path:
    d = tmp_path / "card"
    r = api.init_job(d, "rte", {"L": 80, "W": 50, "H": 120}, "sbs_350")
    assert r.ok, r.errors
    return d


def test_try_and_update_corners(tmp_path: Path) -> None:
    d = _card_job(tmp_path)
    r = api.structure_try(d, {})
    assert r.ok and r.data["corners"]["outer"] == 2.0 and r.data["corners"]["inner"] == 1.0
    assert r.data["corners"]["set"] is None and r.data["corners"]["default"] == [2.0, 1.0]
    r = api.structure_try(d, {}, None, {"outer": 0, "inner": 0})
    assert (
        r.ok and r.data["corners"]["outer_corners"] == 0 and r.data["corners"]["inner_corners"] == 0
    )
    bad = api.structure_try(d, {}, None, {"inner": 9})
    assert not bad.ok
    u = api.structure_update(d, {}, None, {"outer": 3})
    assert u.ok, u.errors
    job = Job.model_validate_json((d / "job.json").read_text())
    assert job.corner_finish == CornerFinish(outer=3)
    dl, _ = build_dieline_for(job)
    assert dl is not None and dl.style_meta["corner_finish"]["outer"] == 3
    assert api.structure_update(d, {}, None, {"outer": None}).ok  # 回到按材料：不写这一项
    assert "corner_finish" not in (d / "job.json").read_text()
    e = api.structure_editable(directory=d)
    assert e.ok and e.data["corners"]["set"] is None and e.data["corners"]["default"] == [2.0, 1.0]


def test_mailer_slot_end_corners_round_and_s02_uses_structure() -> None:
    """用户指出飞机盒插耳和侧墙之间的缝头没倒圆角：两个角都要倒；面因此大了一点的，S02 按倒角前量。"""
    from pacxio.preflight.rules import check_s02, measure_panel

    style = get_style("fefco_0427")
    mat = resolve_material("flute_e")
    prof = load_profile("default")
    p = style.param_schema().model_validate({"L": 260, "W": 180, "H": 70})
    dl = style.generate(p, mat, prof)
    before = {q.id: measure_panel(dl, q.id) for q in dl.panels}
    rec = apply_finish(dl, "corrugated", CornerFinish(outer=2, inner=1))
    assert rec["inner_corners"] >= 28 and not check_s01(dl) and not check_crease_sides(dl)
    centres = [s.arc_info()[0] for e in dl.edges if e.type == "cut" for s in e.segments if s.is_arc]
    assert sum(1 for c in centres if abs(c.x - 421) < 0.01 and 67.9 < c.y < 69.1) == 2
    assert rec.get("structural_bbox")  # 有的面包围盒大了一点，记下了倒角前的
    assert {q.id: measure_panel(dl, q.id) for q in dl.panels} == before
    assert not check_s02(dl, style.expected_panel_sizes(p, mat, prof))
