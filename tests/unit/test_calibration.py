"""Reference PDF transforms, Form XObjects, rounded paths and real differences."""

import json
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen.canvas import Canvas
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.export.pdf import _stroke_edges
from pacxio.model.dieline import EdgeType
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style
from pacxio.styles.calibration import calibrate


def reference(path: Path, scale: float = 1, form: bool = False, crease: bool = True):
    s = get_style("rte")
    profile = load_profile("default")
    dl = s.generate(
        s.param_schema()(L=80, W=50, H=120, apply_compensation=False),
        resolve_material("sbs_350"),
        profile,
    )
    canvas = Canvas(str(path), pagesize=(1000, 1000))
    if form:
        canvas.beginForm("Dieline")
    canvas.saveState()
    canvas.translate(43, 57)
    canvas.scale(scale, scale)
    canvas.setStrokeColorRGB(1, 0, 0)
    _stroke_edges(canvas, dl.edges_of(EdgeType.CUT), 0, 0)
    if crease:
        canvas.setStrokeColorRGB(0, 1, 0)
        _stroke_edges(canvas, dl.edges_of(EdgeType.CREASE), 0, 0)
    canvas.restoreState()
    if form:
        canvas.endForm()
        canvas.saveState()
        canvas.translate(20, 30)
        canvas.doForm("Dieline")
        canvas.restoreState()
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.line(0, 0, 900, 900)
    canvas.showPage()
    canvas.save()


@pytest.mark.parametrize("form", [False, True])
def test_calibration_accepts_translated_vector_pdf_and_form_without_scaling_it(tmp_path, form):
    pdf = tmp_path / "reference.pdf"
    reference(pdf, form=form)
    result = calibrate("rte", pdf, "80x50x120", "sbs_350", "default", tmp_path / "out")
    assert result.ok, result.errors
    assert result.data["passed"] and not result.data["accuracy_level_updated"]
    assert all(v["max_mm"] < 0.03 for v in result.data["metrics"].values())
    assert all(Path(p).is_file() for p in result.artifacts)


def test_calibration_detects_scale_mismatch_instead_of_rescaling_reference(tmp_path):
    pdf = tmp_path / "wrong.pdf"
    reference(pdf, scale=1.02)
    result = calibrate("rte", pdf, "80x50x120", "sbs_350", "default", tmp_path / "out")
    assert not result.ok and result.artifacts
    assert result.data["metrics"]["cut"]["max_mm"] > 1


def test_missing_crease_or_black_bitmap_like_reference_never_passes(tmp_path):
    pdf = tmp_path / "missing.pdf"
    reference(pdf, crease=False)
    result = calibrate("rte", pdf, "80x50x120", "sbs_350", "default", tmp_path / "out")
    assert not result.ok and "缺失" in result.errors[0].message
    pdf = tmp_path / "black.pdf"
    c = Canvas(str(pdf))
    c.rect(10, 10, 100, 100)
    c.showPage()
    c.save()
    assert not calibrate("rte", pdf, "80x50x120", "sbs_350", "default", tmp_path / "out").ok


def test_calibration_cli_returns_structured_result_and_validates_color(tmp_path):
    pdf = tmp_path / "reference.pdf"
    reference(pdf)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "styles",
            "calibrate",
            "rte",
            "--ref",
            str(pdf),
            "--size",
            "80x50x120",
            "--out",
            str(tmp_path / "out"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["passed"]
    result = runner.invoke(
        app,
        [
            "styles",
            "calibrate",
            "rte",
            "--ref",
            str(pdf),
            "--size",
            "80x50x120",
            "--cut-color",
            "2,0,0",
        ],
    )
    assert result.exit_code == 2


def test_inherited_page_rotation_is_not_silently_ignored(tmp_path):
    source = tmp_path / "original.pdf"
    reference(source)
    rotated = tmp_path / "rotated.pdf"
    with pikepdf.open(source) as document:
        page = document.pages[0].obj
        if "/Rotate" in page:
            del page["/Rotate"]
        page["/Parent"]["/Rotate"] = 90
        document.save(rotated)
    result = calibrate("rte", rotated, "80x50x120", "sbs_350", "default", tmp_path / "out")
    assert not result.ok and "旋转" in result.errors[0].message
