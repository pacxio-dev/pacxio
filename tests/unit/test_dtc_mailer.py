"""Reference presets and actual printed-paper / unprinted-reverse opening views."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio import api
from pacxio.cli import app
from pacxio.export.canvas import _Map
from pacxio.geometry import Path as GPath
from pacxio.geometry import path_to_polygon
from pacxio.mockup.fold import build_box
from pacxio.mockup.folded_view import open_paper_view
from pacxio.mockup.folding import apply, build_part, closed_pose, panel_matrices, plan_for
from pacxio.mockup.scene import paper_rgb
from pacxio.model.job import Job
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style
from pacxio.styles.presets import design_presets, get_preset

RUNNER = CliRunner()


@pytest.mark.parametrize("preset", design_presets())
def test_preset_builds_an_ordinary_reproducible_job_and_does_not_register_a_style(tmp_path, preset):
    result = api.init_job_from_preset(tmp_path / "preset", preset.id)
    assert result.ok, result.errors
    job = Job.load(tmp_path / "preset")
    assert job.style == "fefco_0427" and job.params == preset.parameters()
    assert job.material.preset == preset.material and job.material.liner == "white"
    other = api.init_job(
        tmp_path / "direct", preset.style, preset.parameters(), preset.material, liner="white"
    )
    assert other.ok
    assert (tmp_path / "preset/dieline.svg").read_bytes() == (
        tmp_path / "direct/dieline.svg"
    ).read_bytes()
    assert preset.id not in {s["id"] for s in api.list_styles().data["styles"]}  # 预设不是盒型


def test_explicit_overrides_are_saved_and_preset_catalog_is_unchanged(tmp_path):
    before = api.list_style_presets().model_dump()
    result = api.init_job_from_preset(
        tmp_path / "custom",
        "dtc_mailer_standard",
        {"H": "55", "thumb_notch": "true"},
        "flute_b",
        liner="kraft",
    )
    assert result.ok, result.errors
    job = Job.load(tmp_path / "custom")
    assert job.params["H"] == 55 and job.params["thumb_notch"] is True
    assert job.material.preset == "flute_b" and job.material.liner == "kraft"
    assert api.list_style_presets().model_dump() == before
    p = get_preset("dtc_mailer_standard")
    fresh = p.parameters()
    fresh["H"] = 999
    assert p.parameters()["H"] == 70


@pytest.mark.parametrize(
    "ident,params,material",
    [
        ("missing", {}, None),
        ("dtc_mailer_slim", {"H": 2}, None),
        ("dtc_mailer_slim", {"typo": 3}, None),
        ("dtc_mailer_slim", {}, "sbs_350"),
    ],
)
def test_invalid_reference_job_does_not_create_files(tmp_path, ident, params, material):
    target = tmp_path / "invalid"
    result = api.init_job_from_preset(target, ident, params, material)
    assert not result.ok and result.errors
    assert not target.exists()


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_preset_will_not_replace_existing_data(tmp_path, kind):
    target = tmp_path / "existing"
    if kind == "directory":
        target.mkdir()
        sentinel = target / "job.json"
        sentinel.write_text("keep original")
    else:
        sentinel = target
        sentinel.write_text("keep original")
    result = api.init_job_from_preset(target, "dtc_mailer_slim")
    assert not result.ok and sentinel.read_text() == "keep original"


def test_presets_cli_filter_and_job_overrides(tmp_path):
    result = RUNNER.invoke(app, ["styles", "presets", "--style", "fefco_0427", "--json"])
    assert result.exit_code == 0 and len(json.loads(result.output)["data"]["presets"]) == 3
    assert api.list_style_presets("rte").data["presets"] == []
    assert not api.list_style_presets("missing").ok
    result = RUNNER.invoke(
        app,
        ["job", "init-preset", str(tmp_path / "cli"), "dtc_mailer_slim", "--set", "H=45", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert Job.load(tmp_path / "cli").params["H"] == 45
    assert (
        RUNNER.invoke(
            app,
            [
                "job",
                "init-preset",
                str(tmp_path / "bad"),
                "dtc_mailer_slim",
                "--set",
                "H",
                "--json",
            ],
        ).exit_code
        == 2
    )


def fixture(preset):
    s = get_style(preset.style)
    p = s.param_schema()(**preset.parameters())
    dl = s.generate(p, resolve_material(preset.material), load_profile("default"))
    # A real mapping with deliberately noninteger origin and crop coordinates.
    layout = SimpleNamespace(canvas=SimpleNamespace(x0_mm=-3.2, y1_mm=dl.bbox[3] + 3.7))
    mapping = _Map(layout, 40)
    size = (int((dl.bbox[2] + 7) * mapping.s) + 1, int((dl.bbox[3] + 8) * mapping.s) + 1)
    return dl, Image.new("RGB", size, (12, 120, 200)), mapping


@pytest.mark.parametrize("preset", design_presets())
def test_open_surfaces_match_real_matrices_and_reverse_is_plain_paper(preset):
    dl, art, m = fixture(preset)
    box, tex = open_paper_view(dl, art, m)
    plan = plan_for(dl)
    part = build_part(dl, plan)
    pose = closed_pose(part, build_box(dl))
    matrices = panel_matrices(part, plan.open_step)
    original = {}
    for panel in dl.panels:
        a, b, c, d = panel.polygon.bbox()
        mat = pose @ matrices[panel.id]
        original[panel.id] = [apply(mat, u, v) for u, v in ((a, d), (c, d), (c, b), (a, b))]
    # Normalize the scene as a whole, not each panel independently.
    shift = np.array(box.face("base").corners[0]) - original["base"][0]
    assert len(box.faces) == 2 * len(dl.panels)
    for panel in dl.panels:
        front = box.face(panel.id)
        back = box.face(panel.id + ":reverse")
        np.testing.assert_allclose(front.corners, np.array(original[panel.id]) + shift, atol=1e-7)
        np.testing.assert_allclose(front.normal, -np.array(back.normal), atol=1e-7)
        np.testing.assert_allclose(
            np.subtract(back.corners, front.corners),
            np.tile(-np.array(front.normal) * dl.caliper_mm, (4, 1)),
            atol=1e-7,
        )
        printed = tex[front.key]
        reverse = tex[back.key]
        alpha = np.array(printed.getchannel("A")) > 0
        assert np.array_equal(alpha, np.array(reverse.getchannel("A")) > 0)
        assert np.all(np.array(printed)[alpha, :3] == (12, 120, 200))
        assert np.all(np.array(reverse)[alpha, :3] == paper_rgb(dl))
        assert min(printed.size) > 0


def test_opening_masks_round_notches_and_actual_internal_slots():
    preset = get_preset("dtc_mailer_slim")
    dl, art, m = fixture(preset)
    box, textures = open_paper_view(dl, art, m)
    holes = dl.inner_cuts()
    assert holes
    for edge in holes:
        g = path_to_polygon(GPath(edge.segments, closed=True))
        point = g.representative_point()
        for panel in dl.panels:
            if not path_to_polygon(panel.polygon).contains(point):
                continue
            a, b, c, d = panel.polygon.bbox()
            left, top = m.pt(a, d)
            px, py = m.pt(point.x, point.y)
            tex = textures[panel.id]
            x, y = round(px) - round(left), round(py) - round(top)
            assert tex.getchannel("A").getpixel((x, y)) == 0
            assert textures[panel.id + ":reverse"].getchannel("A").getpixel((x, y)) == 0
    assert set(textures) == {f.key for f in box.faces}


@pytest.mark.pro
def test_mockup_adds_real_opening_view_and_text_edits_update_all_representations(
    tmp_path, monkeypatch
):
    from hashlib import sha256

    from pacxio.mockup.service import make_mockup
    from pacxio.model.job import Box, ColorSpec, Placement
    from tests.unit.fontkit import make_font

    fonts = tmp_path / "fonts"
    make_font(fonts / "Sample.ttf", family="Sample")
    monkeypatch.setenv("PACXIO_FONT_DIR", str(fonts))
    directory = tmp_path / "edited"
    assert api.init_job_from_preset(directory, "dtc_mailer_slim").ok
    job = Job.load(directory)
    job.placements = [
        Placement(id="background", kind="fill", color=ColorSpec(cmyk=(0, 20, 30, 0))),
        Placement(
            id="brand",
            kind="text",
            panel="top_lid",
            box=Box(x=12, y=50, w=180, h=20),
            text="SAMPLE",
            font="Sample",
            size_pt=24,
            color=ColorSpec(cmyk=(0, 0, 0, 100)),
        ),
    ]
    job.save(directory)

    def regenerate():
        assert api.render_job(directory).ok
        result = make_mockup(directory)
        assert result.ok, result.errors
        manifest = json.loads((directory / "mockup/manifest.json").read_text())
        scene = json.loads((directory / "mockup/scene.json").read_text())
        preview = json.loads((directory / "preview.map.json").read_text())
        assert set(manifest["views"]) == {"front_right", "back_left", "bottom", "open"}
        assert manifest["fingerprint"] == scene["fingerprint"] == preview["fingerprint"]
        assert (directory / "mockup/view_open.png").is_file()
        hashes = {
            name: sha256((directory / name).read_bytes()).hexdigest()
            for name in ("preview.png", "mockup/view_front_right.png", "mockup/scene/main_base.png")
        }
        return manifest["fingerprint"], hashes

    before, hashes = regenerate()
    job.placements[-1].text = "SAMPLE CLUB"
    job.save(directory)
    after, changed = regenerate()
    assert before != after and all(changed[k] != v for k, v in hashes.items())
    assert not (directory / "mockup/approval.json").exists()
