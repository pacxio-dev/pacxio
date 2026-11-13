"""几何引擎第 5 步 ①：引擎结构接进完整流程——按编号 / 配方建项目、多件套进流水线、交付包标结构来源和成熟度
（：能出交付包，非已校准提醒先打样、不拦；规格书不写参照编号，）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pacxio import api
from pacxio.engine import fefco
from pacxio.engine.style import maturity
from pacxio.model.job import Job


def test_structure_params_from_codes_and_recipes() -> None:
    st, p = api.structure_params("0801", None, None, 400, 300, 250)
    assert st == "structure" and p["top"]["kind"] == "slope" and p["dim_basis"] == "inner"
    st, p = api.structure_params("0933", None, None, 400, 300, 200)
    assert st == "structure_set" and p["counts"] == {"long": 2, "cross": 3}
    st, p = api.structure_params(None, "A20.20.03.01", None, 80, 50, 120)
    assert st == "structure"
    st, p = api.structure_params(
        None, None, {"body": {"base": "rect"}, "top": {"kind": "flaps"}}, 400, 300, 250
    )
    assert p["body"] == {"base": "rect", "L": 400, "W": 300, "H": 250}
    assert isinstance(api.structure_params("9999", None, None, 1, 1, 1), api.Issue)
    assert isinstance(api.structure_params("0201", "A20.20.03.01", None, 1, 1, 1), api.Issue)
    assert isinstance(api.structure_params("0201", None, None, None, 1, 1), api.Issue)


def test_maturity_levels() -> None:
    assert maturity({"calibrated_from": "crashlock_rsc", "fefco": "0711"})["level"] == "calibrated"
    std = maturity({"fefco": "0801"})
    assert std["level"] == "standard" and "0801" in std["label"] and "0801" not in std["plain"]
    assert maturity({})["level"] == "custom"


def test_box_dims_for_plain_engine_and_set_jobs() -> None:
    def job(params: dict) -> Job:
        return Job.model_construct(params=params)

    assert job({"L": 1, "W": 2, "H": 3}).box_dims()["H"] == 3
    assert job(fefco.recipe("0801", 400, 300, 250)).box_dims()["L"] == 400
    hexa = job(fefco.recipe("0730", 300, 300, 300)).box_dims()
    assert hexa["L"] == pytest.approx(300) and hexa["W"] == pytest.approx(300)
    assert job(fefco.set_recipe("0933", 400, 300, 200)).box_dims()["H"] == 200


@pytest.mark.pro
def test_single_engine_structure_exports_with_maturity(tmp_path: Path) -> None:
    st, params = api.structure_params("0801", None, None, 400, 300, 250)
    d = tmp_path / "bin"
    r = api.init_job(d, st, params, "flute_b")
    assert r.ok, r.errors
    assert any(w.id == "S04" and "打样" in w.fix for w in r.warnings)
    e = api.export_job(d)
    assert e.ok, e.errors
    names = {Path(a).name for a in e.artifacts}
    assert {"bin_production.pdf", "bin_spec.pdf", "bin_dieline.dxf"} <= names
    assert any(w.id == "S04" and "标准结构" in w.message for w in e.warnings)


def test_calibrated_engine_structure_has_no_proof_advisory(tmp_path: Path) -> None:
    st, params = api.structure_params("0201", None, None, 400, 300, 250)
    r = api.init_job(tmp_path / "rsc", st, params, "flute_b")
    assert r.ok and not any("未经折样校准" in w.message for w in r.warnings)


@pytest.mark.pro
def test_engine_set_runs_as_a_multipart_job(tmp_path: Path) -> None:
    st, params = api.structure_params("0601", None, None, 400, 300, 250)
    d = tmp_path / "three"
    r = api.init_job(d, st, params, "flute_b")
    assert r.ok, r.errors
    assert set(r.data["parts"]) == {"body", "end"} and r.data["parts"]["end"]["quantity"] == 2
    e = api.export_job(d)
    assert e.ok, e.errors
    names = {Path(a).name for a in e.artifacts}
    assert {"three_body_production.pdf", "three_end_production.pdf", "three_spec.pdf"} <= names


@pytest.mark.pro
def test_engine_set_mockup_lays_parts_side_by_side(tmp_path: Path) -> None:
    from pacxio.mockup.service import make_mockup
    from pacxio.studio.state import job_state

    st, params = api.structure_params("0930", None, None, 300, 200, 150)
    d = tmp_path / "cross"
    assert api.init_job(d, st, params, "flute_b").ok
    m = make_mockup(d)
    assert m.ok, m.errors
    assert (d / "mockup" / "view_front_right.png").is_file()
    s = job_state(d)
    assert not s.errors and s.summary["dims"].startswith("300×200×150")
