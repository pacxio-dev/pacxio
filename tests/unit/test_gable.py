"""gable 山形手提盒（ 节）：几何自洽、合上不穿插、两片手提板贴合且孔对齐、手提板穿过端翼的缝、
端翼的缝顶卡进手提板两头的 V 口（2026-11-06 按 FEFCO 0217 / reference 112321 重做扣）。"""

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
STYLE = get_style("gable")
CASES = ((160, 100, 120, "sbs_350"), (120, 80, 100, "sbs_400"), (230, 120, 150, "sbs_350"))
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
    assert len(dl.panels) == 15 and len(dl.inner_cuts()) == 4
    assert not dl.style_meta["orientation_verified"] and STYLE.id not in ORIENTATION_VERIFIED
    plan = plan_for(dl)
    assert plan is not None
    part = build_part(dl, plan)
    for step in np.linspace(0, plan.steps, 11):
        assert all(np.isfinite(mat).all() for mat in panel_matrices(part, step).values())
    assert len(build_meshes(part, dl.caliper_mm)[0]) == 15
    assert fold_clashes(dl) == []
    assert dieline_to_svg(dl) == (GOLDEN / f"gable_{index}.svg").read_text()


@pytest.mark.parametrize("index", range(3))
def test_handles_stand_back_to_back_with_aligned_holes(index: int) -> None:
    dl = make(index)
    mats, t = closed(dl), dl.caliper_mm
    centres = {}
    normals = {}
    for face in ("front", "back"):
        hole = next(e for e in dl.inner_cuts() if e.id == f"cut_hole_{face}")
        x0, y0, x1, y1 = GPath(hole.segments, closed=True).bbox()
        m = mats[f"handle_{face}"]
        centres[face] = np.array(apply(m, (x0 + x1) / 2, (y0 + y1) / 2, -t / 2))
        normals[face] = m[:3, 2]
    assert normals["front"] @ normals["back"] == pytest.approx(-1, abs=1e-9)  # 面对面
    gap = centres["front"] - centres["back"]
    # 两层手提板的中面相距一层纸厚（各占中线一侧一层），孔心在同一条法线上
    assert abs(gap @ normals["front"]) == pytest.approx(t, abs=1e-6)
    assert np.linalg.norm(gap - (gap @ normals["front"]) * normals["front"]) < 1e-6


@pytest.mark.parametrize("index", range(3))
def test_handles_pass_through_the_end_flap_slits(index: int) -> None:
    """手提板穿过端翼所在平面的地方，都落在端翼的缝里（不会顶在端翼纸上）。"""
    dl = make(index)
    mats, t = closed(dl), dl.caliper_mm
    for end in ("end_a", "end_b"):
        flap = mats[end]
        inv = np.linalg.inv(flap)
        slit = next(e for e in dl.inner_cuts() if e.id == f"cut_slit_{end}")
        slot = path_to_polygon(GPath(slit.segments, closed=True))
        flap_shape = path_to_polygon(dl.panel(end).polygon).difference(slot)
        for face in ("front", "back"):
            paper = path_to_polygon(
                dl.panel(f"handle_{face}").polygon
            )  # 两头有 V 口：只在有纸的地方取点
            handle = paper.bounds
            m = mats[f"handle_{face}"]
            xs = np.linspace(handle[0], handle[2], 300)
            ys = np.linspace(handle[1], handle[3], 120)
            for x in xs:
                for y in ys:
                    if not paper.contains(Point(x, y)):
                        continue
                    local = inv @ np.append(apply(m, x, y, -t / 2), 1.0)
                    if abs(local[2] + t / 2) < t / 2:  # 落在端翼的纸厚里
                        assert not flap_shape.buffer(-0.05).contains(Point(local[0], local[1]))


@pytest.mark.parametrize("index", range(3))
def test_slit_tops_seat_in_the_handle_notches(index: int) -> None:
    """扣：端翼缝顶（缝的上端）折好后落在手提板 V 口底（离开 ≤ 一层纸厚 + 1 mm），耳朵在端翼外面。"""
    dl = make(index)
    mats, t = closed(dl), dl.caliper_mm
    meta = dl.style_meta["dims"]
    for end in ("end_a", "end_b"):
        slit = next(e for e in dl.inner_cuts() if e.id == f"cut_slit_{end}")
        x0, _, x1, y1 = GPath(slit.segments, closed=True).bbox()
        top = np.array(apply(mats[end], (x0 + x1) / 2, y1, -t / 2))
        best = min(
            float(np.linalg.norm(top - np.array(apply(mats[f"handle_{face}"], q.x, q.y, -t / 2))))
            for face in ("front", "back")
            for q in (s.start for s in dl.panel(f"handle_{face}").polygon.segments)
        )
        assert best <= t + 1.0 + meta["handle_h"] * 0.05, (end, best)


def test_rejects_corrugated_tiny_and_oversized_holes() -> None:
    schema = STYLE.param_schema()
    assert STYLE.validate(schema(L=160, W=100, H=120), resolve_material("flute_e"), PROFILE)
    assert STYLE.validate(schema(L=30, W=100, H=120), resolve_material("sbs_350"), PROFILE)
    big_hole = schema(L=160, W=100, H=120, hole_w=150)
    assert STYLE.validate(big_hole, resolve_material("sbs_350"), PROFILE)
