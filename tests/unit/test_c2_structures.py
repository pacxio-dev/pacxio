"""C2 mechanical relationships, geometry, quantity and delivery invariants."""

from pathlib import Path

import ezdxf
import numpy as np
import pytest
from pydantic import ValidationError
from shapely.geometry import Point
from shapely.ops import unary_union

from pacxio import api, dielines
from pacxio.export import dieline_to_dxf, dieline_to_svg, write_cf2
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
from pacxio.mockup.regular import divider_instances, divider_pose
from pacxio.model.job import Box, Job, Window
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style
from pacxio.styles.base import ORIENTATION_VERIFIED
from pacxio.windows import apply_windows

NEW = ("tray_glued", "tray_lock", "hinged_lid", "sleeve", "belly_band", "insert_card", "divider")
PROFILE = load_profile("default")


def make(sid, index=0, **overrides):
    s = get_style(sid)
    dims = ((120, 80, 30), (180, 120, 45), (250, 160, 60))[index]
    extra = {}
    if sid in {"sleeve", "belly_band"}:
        dims = ((100, 80, 30), (160, 100, 50), (220, 40, 70))[index]
    if sid == "insert_card":
        dims = ((100, 70, 15), (160, 100, 25), (220, 150, 35))[index]
        extra = (
            {},
            dict(hole_shape="round", hole_w=25, rows=2, cols=3),
            dict(hole_shape="rect", hole_w=30, hole_h=20, corner_radius=3, rows=2, cols=3),
        )[index]
    if sid == "divider":
        dims = ((120, 80, 35), (240, 180, 60), (360, 240, 90))[index]
        rows, cols = ((2, 2), (3, 4), (4, 3))[index]
        extra = dict(rows=rows, cols=cols)
    material = resolve_material(
        "flute_b" if sid == "divider" and index else ("sbs_350", "sbs_400", "sbs_350")[index]
    )
    p = s.param_schema()(**(dict(zip(("L", "W", "H"), dims, strict=True)) | extra | overrides))
    assert not s.validate(p, material, PROFILE)
    parts = (
        s.generate_parts(p, material, PROFILE)
        if sid == "divider"
        else {"main": s.generate(p, material, PROFILE)}
    )
    return s, p, material, parts


@pytest.mark.parametrize("sid", NEW)
@pytest.mark.parametrize("index", range(3))
def test_three_sizes_partition_crease_sides_dimensions_meshes_and_final_faces(sid, index):
    s, p, m, parts = make(sid, index)
    expected = s.expected_panel_sizes(p, m, PROFILE)
    for name, dl in parts.items():
        sizes = expected[name] if sid == "divider" else expected
        assert not check_s01(dl) + check_crease_sides(dl) + check_s02(dl, sizes)
        assert not dl.style_meta["orientation_verified"] and sid not in ORIENTATION_VERIFIED
        outer = path_to_polygon(GPath(dl.outline_segments(), closed=True))
        polygons = [path_to_polygon(pn.polygon) for pn in dl.panels]
        assert outer.is_valid and all(poly.is_valid for poly in polygons)
        union = unary_union(polygons)
        assert union.symmetric_difference(outer).area < 1e-5
        assert sum(poly.area for poly in polygons) == pytest.approx(union.area)
        plan = plan_for(dl)
        assert plan is not None
        part = build_part(dl, plan)
        for step in np.linspace(0, plan.steps, 17):
            matrices = panel_matrices(part, step)
            assert set(matrices) == {pn.id for pn in dl.panels}
            assert all(np.isfinite(mat).all() for mat in matrices.values())
        meshes, _ = build_meshes(part, 0.1)
        assert len(meshes) == len(dl.panels)
        box = build_box(dl)
        pose = closed_pose(part, box)
        matrices = panel_matrices(part, plan.steps)
        for face in box.faces:
            x0, y0, x1, y1 = face.tex_rect
            got = [
                apply(pose @ matrices[face.panel], x, y)
                for x, y in ((x0, y1), (x1, y1), (x1, y0), (x0, y0))
            ]
            np.testing.assert_allclose(got, face.corners, atol=1e-6)
        snapshot = Path(__file__).resolve().parents[1] / "golden/c2" / f"{sid}_{index}_{name}.svg"
        assert dieline_to_svg(dl) == snapshot.read_text()


@pytest.mark.parametrize("index", range(3))
def test_lock_tongues_meet_receiving_slots_on_outside_plane(index):
    _, p, m, parts = make("tray_lock", index)
    dl = parts["main"]
    part = build_part(dl, plan_for(dl))
    mat = panel_matrices(part, part.plan.steps)
    for wall in ("front", "back"):
        for side in ("a", "b"):
            pid = f"tongue_{wall}_{side}"
            panel = dl.panel(pid)
            e = next(e for e in dl.edges if e.id == panel.hinge.edge_id)
            seg = e.segments[0]
            c = (seg.start + seg.end) * 0.5
            slot = next(e for e in dl.edges if e.id == f"cut_slot_{wall}_{side}")
            g = path_to_polygon(GPath(slot.segments, closed=True))
            a = apply(mat[pid], c.x, c.y)
            b = apply(mat[f"side_{side}"], g.centroid.x, g.centroid.y)
            np.testing.assert_allclose(a, b, atol=1e-7)
            np.testing.assert_allclose(-mat[pid][:3, 2], mat[f"side_{side}"][:3, 2], atol=1e-7)
            paper_outside = np.subtract(apply(mat[pid], c.x, c.y, -m.caliper_mm), b)
            np.testing.assert_allclose(
                paper_outside, mat[f"side_{side}"][:3, 2] * m.caliper_mm, atol=1e-7
            )
            assert seg.length() + p.slot_clearance == pytest.approx(g.bounds[2] - g.bounds[0])
            assert seg.length() + 2 * p.hook > g.bounds[2] - g.bounds[0]


@pytest.mark.parametrize("index", range(3))
def test_divider_actual_quantities_and_slots_clear_opposite_caliper(index):
    _, p, m, parts = make("divider", index)
    instances = divider_instances(parts)
    assert len(instances) == p.rows + p.cols - 2
    for xi in range(1, p.rows):
        for yi in range(1, p.cols):
            a = divider_pose(parts["x"], xi)
            b = divider_pose(parts["y"], yi)
            for z in (p.H * 0.25, p.H * 0.75):
                for dx, dy in (
                    (0, 0),
                    (-m.caliper_mm * 0.45, -m.caliper_mm * 0.45),
                    (m.caliper_mm * 0.45, m.caliper_mm * 0.45),
                ):
                    world = np.array([yi * p.L / p.cols + dx, xi * p.W / p.rows + dy, z, 1])
                    u = np.linalg.inv(a) @ world
                    v = np.linalg.inv(b) @ world
                    gx = path_to_polygon(parts["x"].panel("front").polygon)
                    gy = path_to_polygon(parts["y"].panel("front").polygon)
                    assert not (gx.contains(Point(*u[:2])) and gy.contains(Point(*v[:2])))


@pytest.mark.parametrize("sid", NEW)
def test_job_api_and_vector_exports_preserve_parts_and_units(tmp_path, sid):
    s, p, m, parts = make(sid, 1)
    directory = tmp_path / sid
    r = api.init_job(directory, sid, p.model_dump(), m.preset, "default")
    assert r.ok, r.errors
    dset, errs = dielines.build_parts_for(Job.load(directory))
    assert not errs
    assert dset.multipart == (sid == "divider")
    for name, dl in parts.items():
        path = tmp_path / f"{sid}_{name}.dxf"
        dieline_to_dxf(dl, PROFILE, path)
        doc = ezdxf.readfile(path)
        assert doc.dxfversion == "AC1009"
        assert list(doc.modelspace())
        write_cf2(dl, PROFILE, tmp_path / f"{sid}_{name}.cf2")


@pytest.mark.parametrize(
    "sid,extra",
    [
        ("tray_glued", {"corner_depth": 70}),
        ("tray_lock", {"tongue_len": 40}),
        ("tray_lock", {"slot_clearance": 4}),
        ("hinged_lid", {"tuck_len": 50}),
        ("hinged_lid", {"dust_depth": 100}),
        ("sleeve", {"glue_w": 30, "H": 15}),
        ("belly_band", {"W": 3}),
        ("insert_card", {"hole_shape": "round"}),
        ("insert_card", {"hole_shape": "round", "hole_w": 50, "cols": 4}),
        ("insert_card", {"H": 80}),
        ("divider", {"L": 10, "cols": 8}),
    ],
)
def test_impossible_geometry_is_rejected(sid, extra):
    s = get_style(sid)
    p = s.param_schema()(**(dict(L=120, W=80, H=30) | extra))
    assert s.validate(p, resolve_material("sbs_350"), PROFILE)


@pytest.mark.parametrize("sid", NEW)
@pytest.mark.parametrize("extra", [{"L": float("nan")}, {"W": float("inf")}, {"typo": 3}])
def test_nonfinite_and_unknown_parameters_rejected(sid, extra):
    with pytest.raises(ValidationError):
        get_style(sid).param_schema()(**(dict(L=120, W=80, H=30) | extra))


def test_insert_intrinsic_holes_survive_extra_window_and_reject_intersection():
    _, _, m, parts = make("insert_card", 0, hole_shape="round", hole_w=16)
    dl = parts["main"]
    w = Window(id="extra", panel="platform", shape="circle", box=Box(x=7, y=7, w=10, h=10))
    result, errors = apply_windows(dl, [w], PROFILE, m)
    assert result is not None and not errors
    assert len(result.windows) == 2 and len(result.inner_cuts()) == 2
    conflict = w.model_copy(update={"box": Box(x=44, y=29, w=12, h=12)})
    assert apply_windows(dl, [conflict], PROFILE, m)[0] is None


@pytest.mark.parametrize("sid", ("tray_glued", "tray_lock", "hinged_lid", "sleeve", "belly_band"))
def test_compensation_disabled_and_outer_basis_are_explicit(sid):
    _, p, m, parts = make(sid, apply_compensation=False)
    dl = parts["main"]
    pid = "bottom" if sid in {"sleeve", "belly_band"} else "base"
    x0, _, x1, _ = dl.panel(pid).polygon.bbox()
    assert x1 - x0 == pytest.approx(p.L)
    assert not dl.style_meta["compensation_applied"]
    _, _, _, outer = make(sid, dim_basis="outer")
    assert outer["main"].style_meta["dims"]["Li"] == pytest.approx(p.L - 2 * m.caliper_mm)


@pytest.mark.parametrize("index", range(3))
def test_divider_fit_reads_actual_geometry_and_rejects_missing_notches(index):
    s, p, m, parts = make("divider", index)
    assert not s.check_fit(parts, p, m, PROFILE)
    parts["x"].panel("front").polygon = GPath.rect(0, 0, p.L, p.H)
    assert s.check_fit(parts, p, m, PROFILE)


@pytest.mark.parametrize(
    "sid,extra",
    [
        ("tray_glued", {"corner_bevel": 0}),
        ("tray_lock", {"corner_bevel": 0}),
        ("hinged_lid", {"tuck_r": 0, "dust": False}),
    ],
)
def test_optional_straight_corners_and_disabled_dust_do_not_break_contour(sid, extra):
    _, _, _, parts = make(sid, **extra)
    dl = parts["main"]
    assert not check_s01(dl) + check_crease_sides(dl)
    assert all(seg.length() > 1e-6 for e in dl.edges for seg in e.segments)
    assert build_meshes(build_part(dl, plan_for(dl)), 0.1)[0]


def test_static_open_paper_keeps_cutout_alpha_and_physical_reverse_surface():
    from PIL import Image

    from pacxio.mockup.regular import interior_surfaces

    _, _, m, parts = make("tray_lock")
    dl = parts["main"]
    box = build_box(dl)
    tex = {face.key: Image.new("RGBA", (20, 20), (20, 50, 90, 0)) for face in box.faces}
    shown, maps = interior_surfaces(box, tex, dl)
    assert len(shown.faces) == 2 * len(box.faces)
    for face in box.faces:
        reverse = shown.face(face.key + ":inside")
        assert maps[reverse.key].getchannel("A").getextrema() == (0, 0)
        np.testing.assert_allclose(
            np.subtract(reverse.corners, face.corners),
            np.tile(-np.array(face.normal) * m.caliper_mm, (4, 1)),
        )
