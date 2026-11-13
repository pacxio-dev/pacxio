"""One new mechanism, including its editable dependencies and physical closed placement."""

import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from shapely.ops import unary_union

from pacxio import api
from pacxio.export.cf2 import dieline_to_cf2, read_cf2
from pacxio.export.dxf import dieline_to_dxf
from pacxio.export.svg import dieline_to_svg
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.fold import build_box
from pacxio.mockup.folding import (
    apply,
    build_meshes,
    build_part,
    closed_pose,
    panel_matrices,
    plan_for,
)
from pacxio.model.job import Job
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style
from pacxio.styles.base import ORIENTATION_VERIFIED

PROFILE = load_profile("default")
CASES = ((150, 100, 40, "flute_e"), (260, 180, 70, "flute_e"), (300, 220, 110, "flute_b"))
EDITS = dict(
    roll_gap=6,  # 默认 3t+0.5 在 E 楞正好是 5，编辑值要和默认不同
    inner_inset=2,
    ear_offset=1,
    ear_depth=60,
    ear_gap=4,
    lug_w=24,
    lug_len=5,
    slot_depth=6,
    lid_side_clearance=1.2,
    front_clearance=1,
    tuck_len=18,
    tuck_side_inset=4,
    tuck_radius=1,
    dust_depth=20,
    dust_taper_deg=10,
    dust_radius=1,
)


def make(index=1, **edits):
    length, w, h, mat = CASES[index]
    s, m = get_style("mailer_tuck"), resolve_material(mat)
    p = s.param_schema()(L=length, W=w, H=h, **edits)
    assert not s.validate(p, m, PROFILE)
    return s, p, m, s.generate(p, m, PROFILE)


def coordinates(dl, pid, *, paper_back=False):
    plan = plan_for(dl)
    part = build_part(dl, plan)
    matrix = closed_pose(part, build_box(dl)) @ panel_matrices(part, plan.steps)[pid]
    x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
    return np.array(
        [
            apply(matrix, x, y, -dl.caliper_mm if paper_back else 0)
            for x in (x0, x1)
            for y in (y0, y1)
        ]
    )


@pytest.mark.parametrize("index", range(3))
def test_partition_hinges_dimensions_and_golden(index):
    s, p, m, dl = make(index)
    assert (
        not check_s01(dl)
        + check_s02(dl, s.expected_panel_sizes(p, m, PROFILE))
        + check_crease_sides(dl)
    )
    outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
    polys = [path_to_polygon(panel.polygon) for panel in dl.panels]
    union = unary_union(polys)
    assert outer.is_valid and all(poly.is_valid for poly in polys)
    assert union.symmetric_difference(outer).area < 1e-5
    assert sum(poly.area for poly in polys) == pytest.approx(union.area)
    for a, b in zip(
        dl.outline_segments(), dl.outline_segments()[1:] + dl.outline_segments()[:1], strict=True
    ):
        assert a.end.is_close(b.start) and a.length() > 0
    assert len(dl.panels) == 17 and len([p for p in dl.panels if p.hinge]) == 16
    assert not dl.style_meta["orientation_verified"] and s.id not in ORIENTATION_VERIFIED
    plan = plan_for(dl)
    part = build_part(dl, plan)
    for step in np.linspace(0, plan.steps, 15):
        assert all(np.isfinite(a).all() for a in panel_matrices(part, step).values())
    meshes, _ = build_meshes(part, 0.1)
    assert len(meshes) == 17
    golden = Path(__file__).resolve().parents[1] / "golden/c3" / f"mailer_tuck_{index}.svg"
    assert dieline_to_svg(dl) == golden.read_text()


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize(
    "edits",
    ({}, {"front_clearance": 1.2, "lid_side_clearance": 1.1, "tuck_len": 15, "dust_depth": 12}),
)
def test_closed_tuck_is_behind_front_and_dust_inside_side_walls(index, edits):
    _, p, m, dl = make(index, **edits)
    front = coordinates(dl, "front", paper_back=True)
    tuck = coordinates(dl, "top_tuck")
    # Test actual transform / paper-thickness planes, not schema/meta claims.
    np.testing.assert_allclose(tuck[:, 1] - front[:, 1], p.front_clearance, atol=1e-7)
    assert tuck[:, 2].min() >= m.caliper_mm + 1 - 1e-7
    for side in ("a", "b"):
        wall = coordinates(dl, f"side_{side}_in")
        dust = coordinates(dl, f"dust_l{side}")
        delta = dust[:, 0] - wall[:, 0]
        np.testing.assert_allclose(
            delta, p.lid_side_clearance * (1 if side == "a" else -1), atol=1e-7
        )
        assert dust[:, 2].min() >= m.caliper_mm + 1 - 1e-7
    assert tuck[:, 0].min() > coordinates(dl, "side_a_in")[:, 0].max()
    assert tuck[:, 0].max() < coordinates(dl, "side_b_in")[:, 0].min()


@pytest.mark.parametrize("field,value", EDITS.items())
def test_each_edit_changes_real_geometry_and_preserves_valid_partition(field, value):
    _, _, _, before = make()
    s, p, m, after = make(**{field: value})
    include = {"panels", "edges", "bbox"}
    assert before.model_dump(include=include) != after.model_dump(include=include)
    assert not check_s01(after) + check_crease_sides(after)
    polys = [path_to_polygon(panel.polygon) for panel in after.panels]
    outer = path_to_polygon(GPath(after.outline_segments(), closed=True))
    assert unary_union(polys).symmetric_difference(outer).area < 1e-5
    assert sum(poly.area for poly in polys) == pytest.approx(outer.area)
    if field in {"tuck_len", "tuck_side_inset", "tuck_radius"}:
        for pid in ("base", "front", "top_lid", "dust_la", "dust_lb"):
            assert before.panel(pid) == after.panel(pid)
    if field in {"lug_w", "slot_depth"}:
        old = {e.id: e for e in before.edges}
        assert all(e != old[e.id] for e in after.edges if e.id.startswith("cut_slot_"))
    assert not s.validate(p, m, PROFILE)


def test_same_input_new_mechanism_and_front_art_mapping():
    _, p, m, new = make()
    old_style = get_style("fefco_0427")
    old = old_style.generate(old_style.param_schema()(L=p.L, W=p.W, H=p.H), m, PROFILE)
    assert len(old.panels) == 19 and len(new.panels) == 17
    assert {p.id for p in old.panels} - {p.id for p in new.panels} == {"tuck_ear_a", "tuck_ear_b"}
    assert old.style_meta["faces"]["front"] == "top_tuck"
    assert new.style_meta["faces"]["front"] == build_box(new).face("front").panel == "front"
    assert coordinates(old, "top_tuck")[:, 1].max() < coordinates(old, "front")[:, 1].min()
    assert (
        coordinates(new, "top_tuck")[:, 1].min()
        > coordinates(new, "front", paper_back=True)[:, 1].max()
    )


@pytest.mark.parametrize(
    "edits",
    (
        {"tuck_len": 80},
        {"tuck_radius": 30},
        {"dust_depth": 80},
        {"dust_radius": 20},
        {"tuck_side_inset": 150},
        {"front_clearance": 8},
        {"lid_side_clearance": 8},
        {"lug_w": 100},
        {"ear_gap": 69},
        {"ear_depth": 100},
        {"roll_gap": 1},
        {"inner_inset": 80},
    ),
)
def test_invalid_interdependent_geometry_is_rejected_without_writing_jobs(tmp_path, edits):
    result = api.init_job(
        tmp_path / "bad", "mailer_tuck", dict(L=260, W=180, H=70) | edits, "flute_e"
    )
    assert not result.ok and result.errors and not (tmp_path / "bad").exists()


def test_decimal_dimensions_keep_mirrored_creases_on_panel_boundaries():
    s = get_style("mailer_tuck")
    m = resolve_material("flute_e")
    p = s.param_schema()(
        L=247.65406175355082,
        W=491.3003525726851,
        H=222.26176351441447,
        tuck_len=34.08520120783863,
        dust_depth=44.227179300546254,
        tuck_radius=4.923386809841141,
        dust_radius=2.303574894138774,
        front_clearance=1.7118055972558652,
        lid_side_clearance=2.6043607235748207,
        dust_taper_deg=15.187820659266885,
    )
    assert not s.validate(p, m, PROFILE)
    dl = s.generate(p, m, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("compensation", (True, False))
def test_outer_size_conversion_and_compensation(index, compensation):
    _, _, _, inner = make(index, apply_compensation=compensation)
    _, _, _, outer = make(index, dim_basis="outer", apply_compensation=compensation)
    d = outer.style_meta["dims"]
    p = outer.params
    t = outer.caliper_mm
    assert d["Li"] == pytest.approx(p["L"] - 2 * d["roll_gap"] - 3 * t)
    assert d["Wi"] == pytest.approx(p["W"] - 2 * t)
    assert d["Hi"] == pytest.approx(p["H"] - 2 * t)
    assert outer.bbox != inner.bbox
    assert not check_s01(outer) + check_crease_sides(outer)


@pytest.mark.parametrize(
    "edits",
    (
        {"tuck_ear_len": 30},
        {"lid_flap_inset": 1},
        {"tuck_len": float("nan")},
        {"dust_taper_deg": float("inf")},
    ),
)
def test_deleted_and_nonfinite_parameters_are_not_editing_controls(edits):
    with pytest.raises(ValidationError):
        get_style("mailer_tuck").param_schema()(L=260, W=180, H=70, **edits)


@pytest.mark.pro
def test_roundtrip_job_rebuild_and_arc_exports(tmp_path):
    result = api.init_job(
        tmp_path / "job", "mailer_tuck", dict(L=260, W=180, H=70, tuck_radius=2), "flute_e"
    )
    assert result.ok, result.errors
    job = Job.load(tmp_path / "job")
    dl, errors = api.build_dieline(job)
    assert dl is not None and not errors and job.params["tuck_radius"] == 2
    assert api.render_job(tmp_path / "job").ok
    cf2 = read_cf2(dieline_to_cf2(dl, PROFILE))
    assert cf2 and any(e.kind == "A" for e in cf2)
    dxf = tmp_path / "mailer.dxf"
    dieline_to_dxf(dl, PROFILE, dxf)
    import ezdxf

    doc = ezdxf.readfile(dxf)
    assert not doc.audit().has_errors
    assert list(doc.modelspace().query("ARC"))
    assert " A" in dieline_to_svg(dl)
    assert not json.loads((tmp_path / "job/job.json").read_text()).get("approvals")
