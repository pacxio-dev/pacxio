"""Mechanical / process tests for two actual closures, rather than size presets."""

import json
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from pydantic import ValidationError
from shapely.geometry import Point
from shapely.ops import unary_union

from pacxio import api
from pacxio.export.cf2 import dieline_to_cf2, read_cf2
from pacxio.export.dxf import dieline_to_dxf
from pacxio.export.svg import dieline_to_svg
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.layout.zones import compute_zones
from pacxio.mockup.fold import build_box
from pacxio.mockup.folding import (
    apply,
    build_meshes,
    build_part,
    closed_pose,
    panel_matrices,
    plan_for,
)
from pacxio.model.dieline import EdgeType
from pacxio.model.job import ColorSpec, Job, Placement
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style
from pacxio.styles.base import ORIENTATION_VERIFIED

STYLES = ("mailer_front_lock", "mailer_tear_seal")
PROFILE = load_profile("default")
CASES = ((150, 100, 40, "flute_e"), (260, 180, 70, "flute_e"), (300, 220, 110, "flute_b"))
COMMON_EDITS = dict(
    roll_gap=6,  # 默认 3t+0.5 在 E 楞正好是 5，编辑值要和默认不同
    inner_inset=2,
    ear_offset=1,
    ear_depth=60,
    ear_gap=4,
    lug_w=24,
    lug_len=5,
    slot_depth=6,
    lid_side_clearance=1.2,
    dust_depth=20,
    dust_taper_deg=10,
    dust_radius=1,
)
LOCK_EDITS = COMMON_EDITS | dict(
    apron_depth=15,
    front_clearance=1,
    lock_neck_w=20,
    lock_tab_len=18,
    lock_neck_len=8,
    lock_hook=4,
    lock_spacing=100,
    lock_clearance=1.2,
    lock_bevel=0,
)
TEAR_EDITS = COMMON_EDITS | dict(
    header_depth=10,
    tear_width=8,
    seal_depth=16,
    adhesive_width=6,
    adhesive_margin=3,
    adhesive_end_inset=14,
    pull_tab=8,
    perf_cut=2,
    perf_bridge=1,
)


def make(sid, index=1, **edits):
    length, w, h, preset = CASES[index]
    s, m = get_style(sid), resolve_material(preset)
    p = s.param_schema()(L=length, W=w, H=h, **edits)
    assert not s.validate(p, m, PROFILE)
    return s, p, m, s.generate(p, m, PROFILE)


def check_partition(dl):
    outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
    polys = [path_to_polygon(p.polygon) for p in dl.panels]
    union = unary_union(polys)
    assert outer.is_valid and all(p.is_valid for p in polys)
    assert union.symmetric_difference(outer).area < 1e-5
    assert sum(p.area for p in polys) == pytest.approx(union.area)
    assert not check_s01(dl) + check_crease_sides(dl)


def matrices(dl):
    plan = plan_for(dl)
    part = build_part(dl, plan)
    pose = closed_pose(part, build_box(dl))
    return {pid: pose @ mat for pid, mat in panel_matrices(part, plan.steps).items()}


@pytest.mark.parametrize("sid", STYLES)
@pytest.mark.parametrize("index", range(3))
def test_three_sizes_real_partition_hinges_meshes_and_golden(sid, index):
    s, p, m, dl = make(sid, index)
    check_partition(dl)
    assert not check_s02(dl, s.expected_panel_sizes(p, m, PROFILE))
    assert len(dl.panels) == 19 and len([p for p in dl.panels if p.hinge]) == 18
    assert not dl.style_meta["orientation_verified"] and sid not in ORIENTATION_VERIFIED
    assert not {"tuck_ear_a", "tuck_ear_b"} & {p.id for p in dl.panels}
    plan = plan_for(dl)
    part = build_part(dl, plan)
    for t in np.linspace(0, plan.steps, 17):
        assert all(np.isfinite(mat).all() for mat in panel_matrices(part, t).values())
    meshes, strips = build_meshes(part, 0.1)
    assert len(meshes) == 19
    if sid == "mailer_tear_seal":
        assert not {s.child for s in strips} & {"tear_strip", "seal_pad"}
    golden = Path(__file__).resolve().parents[1] / "golden/c3" / f"{sid}_{index}.svg"
    assert golden.read_text() == dieline_to_svg(dl)


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize(
    "edits", ({}, dict(apron_depth=12, lock_neck_w=14, lock_spacing=65, front_clearance=1))
)
def test_lock_necks_pass_through_receiver_and_shoulders_are_inside(index, edits):
    _, p, m, dl = make("mailer_front_lock", index, **edits)
    mats = matrices(dl)
    inv = np.linalg.inv(mats["front"])
    dim = dl.style_meta["dims"]
    for pid in ("lock_tab_a", "lock_tab_b"):
        hinge = next(e for e in dl.edges if e.id == f"crease_apron_{pid}")
        cx = (hinge.segments[0].start.x + hinge.segments[0].end.x) / 2
        yr = hinge.segments[0].start.y
        receiver = next(e for e in dl.inner_cuts() if e.id == f"cut_front_{pid}")
        shape = path_to_polygon(GPath(receiver.segments, closed=True))
        # Intersect both sides of the tab's actual paper slab with both front-wall faces.
        offset = m.caliper_mm + p.front_clearance
        for wall_y in (0, m.caliper_mm):
            for x in (cx - dim["lock_neck_w"] / 2, cx + dim["lock_neck_w"] / 2):
                for paper_z in (0, -m.caliper_mm):
                    point = apply(mats[pid], x, yr + offset + wall_y, paper_z)
                    local = inv @ np.array([*point, 1])
                    assert shape.buffer(1e-7).covers(Point(local[0], local[1]))
        shoulder = apply(mats[pid], cx, yr + dim["lock_neck_len"])
        assert shoulder[1] >= m.caliper_mm + p.lock_clearance + 1 - 1e-7
        assert dim["lock_neck_w"] + 2 * dim["lock_hook"] >= dim["front_slot_w"] + 1 - 1e-7


@pytest.mark.parametrize("index", range(3))
def test_adhesive_reverse_contacts_unprinted_target_and_perf_bridges_survive(index):
    _, p, m, dl = make("mailer_tear_seal", index)
    mats = matrices(dl)
    glue, receiving = dl.glue_areas[0], dl.zones.no_ink[0]
    a = glue.bbox()
    b = receiving.bbox()
    source = [apply(mats["seal_pad"], x, y, -m.caliper_mm) for x, y in ((a[0], a[1]), (a[2], a[3]))]
    target = [apply(mats["front"], x, y) for x, y in ((b[0], b[1]), (b[2], b[3]))]
    np.testing.assert_allclose(source, target, atol=1e-7)
    zg = compute_zones(dl, PROFILE)
    patch = path_to_polygon(receiving)
    assert zg.no_ink.covers(patch) and zg.allowed.intersection(patch).area < 1e-7
    assert zg.safe["front"].intersection(patch).area < 1e-7
    assert zg.finish_ok["front"].intersection(patch).area < 1e-7
    for edge in dl.edges_of(EdgeType.PERF):
        assert len(edge.segments) >= 3 and all(
            s.length() == pytest.approx(p.perf_cut) for s in edge.segments
        )
        for left, right in zip(edge.segments, edge.segments[1:], strict=False):
            assert right.start.x - left.end.x == pytest.approx(p.perf_bridge)
        assert (
            edge.segments[0].start.x
            >= dl.panel("top_tuck").polygon.bbox()[0] + p.perf_bridge - 1e-7
        )
        assert (
            edge.segments[-1].end.x <= dl.panel("top_tuck").polygon.bbox()[2] - p.perf_bridge + 1e-7
        )
    assert dl.panel("tear_strip").polygon.bbox()[0] < dl.panel("top_tuck").polygon.bbox()[0]
    assert dl.style_meta["closure_process"]["source_side"] == "reverse"
    assert {"top_tuck", "tear_strip", "seal_pad"} <= set(build_box(dl).face("front").also)


@pytest.mark.parametrize(
    "sid,edits",
    [
        (sid, edits)
        for sid, options in (("mailer_front_lock", LOCK_EDITS), ("mailer_tear_seal", TEAR_EDITS))
        for edits in options.items()
    ],
)
def test_every_exposed_parameter_changes_geometry_or_actual_process_area(sid, edits):
    key, value = edits
    *_, before = make(sid)
    *_, after = make(sid, **{key: value})
    include = {"edges", "panels", "glue_areas", "zones"}
    assert before.model_dump(include=include) != after.model_dump(include=include)
    check_partition(after)
    if key in {"lock_neck_w", "lock_clearance", "lock_spacing", "apron_depth"}:
        old = {e.id: e for e in before.edges}
        assert all(e != old[e.id] for e in after.edges if e.id.startswith("cut_front_"))
    if key in {"perf_cut", "perf_bridge"}:
        assert before.edges_of(EdgeType.PERF) != after.edges_of(EdgeType.PERF)
    if key == "adhesive_end_inset":
        assert before.edges == after.edges and before.glue_areas != after.glue_areas


@pytest.mark.parametrize(
    "sid,edits",
    [
        ("mailer_front_lock", e)
        for e in (
            {"lock_neck_len": 1},
            {"lock_hook": 0.1},
            {"apron_depth": 1},
            {"apron_depth": 90},
            {"lock_spacing": 10},
            {"lock_neck_w": 100},
            {"lock_bevel": 10},
        )
    ]
    + [
        ("mailer_tear_seal", e)
        for e in (
            {"seal_depth": 5},
            {"header_depth": 1},
            {"header_depth": 65},
            {"tear_width": 14, "pull_tab": 3},
            {"adhesive_end_inset": 200},
            {"perf_bridge": 0},
            {"adhesive_width": 30},
        )
    ],
)
def test_invalid_mechanisms_do_not_write_jobs(tmp_path, sid, edits):
    result = api.init_job(tmp_path / "invalid", sid, dict(L=260, W=180, H=70) | edits, "flute_e")
    assert not result.ok and result.errors and not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("sid", STYLES)
@pytest.mark.parametrize(
    "extra",
    ({"tuck_len": 20}, {"tuck_radius": 2}, {"dust_depth": float("nan")}, {"L": float("inf")}),
)
def test_unrelated_legacy_or_nonfinite_controls_are_rejected(sid, extra):
    with pytest.raises(ValidationError):
        get_style(sid).param_schema()(**(dict(L=260, W=180, H=70) | extra))


@pytest.mark.parametrize("sid", STYLES)
def test_dxf_cf2_roundtrip_keep_real_slot_arcs_and_separate_perf_tools(tmp_path, sid):
    _, _, _, dl = make(sid)
    target = tmp_path / f"{sid}.dxf"
    dieline_to_dxf(dl, PROFILE, target)
    doc = ezdxf.readfile(target)
    assert not doc.audit().has_errors and list(doc.modelspace().query("ARC"))
    cf2 = read_cf2(dieline_to_cf2(dl, PROFILE))
    if sid == "mailer_tear_seal":
        segments = sum(len(e.segments) for e in dl.edges_of(EdgeType.PERF))
        assert len([e for e in cf2 if e.tool == PROFILE.cf2.linetypes["perf"]]) == segments
        assert "Glue_Area" not in dieline_to_cf2(dl, PROFILE)
        import xml.etree.ElementTree as ET

        paths = ET.fromstring(dieline_to_svg(dl)).findall(".//{http://www.w3.org/2000/svg}path")
        assert all(
            "stroke-dasharray" not in path.attrib
            for path in paths
            if path.get("data-type") == "perf"
        )
    assert any(e.kind == "A" for e in cf2)


@pytest.mark.pro
def test_full_front_fill_respects_adhesive_no_ink_and_job_preserves_params(tmp_path):
    result = api.init_job(
        tmp_path / "seal",
        "mailer_tear_seal",
        dict(L=260, W=180, H=70, perf_cut=2, perf_bridge=1),
        "flute_e",
    )
    assert result.ok, result.errors
    job = Job.load(tmp_path / "seal")
    job.placements = [
        Placement(
            id="front_fill", kind="fill", scope=["front"], color=ColorSpec(cmyk=(50, 30, 10, 0))
        )
    ]
    job.save(tmp_path / "seal")
    result = api.render_job(tmp_path / "seal")
    assert result.ok, result.errors
    assert Job.load(tmp_path / "seal").params["perf_bridge"] == 1
    assert not json.loads((tmp_path / "seal/job.json").read_text()).get("approvals")
