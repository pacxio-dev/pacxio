"""CLI：统一 JSON 结构与退出码（规格 14 节）。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.model.job import Job

runner = CliRunner()
ENVELOPE = {"ok", "command", "job", "artifacts", "data", "errors", "warnings"}


def test_job_init_writes_job_and_svg(tmp_path: Path) -> None:
    d = tmp_path / "demo-rte-001"
    res = runner.invoke(
        app,
        [
            "job",
            "init",
            str(d),
            "--style",
            "rte",
            "--L",
            "80",
            "--W",
            "50",
            "--H",
            "180",
            "--basis",
            "inner",
            "--material",
            "sbs_350",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert set(out) == ENVELOPE and out["ok"] is True
    assert (d / "dieline.svg").read_text().startswith("<?xml")
    job = Job.load(d)  # job.json 是唯一事实来源，可重新读回
    assert job.style == "rte" and job.params["L"] == 80 and job.material.preset == "sbs_350"
    assert not any(
        w["id"] == "PROFILE" for w in out["warnings"]
    )  # default 配置档经工厂确认（）


def test_constraint_violation_exit_1_with_fix(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["job", "init", str(tmp_path / "x"), "--L", "15", "--W", "50", "--H", "180", "--json"]
    )
    assert res.exit_code == 1
    out = json.loads(res.output)
    assert out["ok"] is False and out["errors"][0]["fix"]
    assert not (tmp_path / "x" / "job.json").exists()  # 失败不留半成品


def test_unknown_material_exit_2(tmp_path: Path) -> None:
    res = runner.invoke(
        app,
        [
            "job",
            "init",
            str(tmp_path / "x"),
            "--L",
            "80",
            "--W",
            "50",
            "--H",
            "180",
            "--material",
            "nope",
            "--json",
        ],
    )
    assert res.exit_code == 2
    assert "sbs_350" in json.loads(res.output)["errors"][0]["fix"]


def test_missing_required_option_exit_2(tmp_path: Path) -> None:
    assert runner.invoke(app, ["job", "init", str(tmp_path / "x")]).exit_code == 2


def test_styles_list_and_env_check() -> None:
    res = runner.invoke(app, ["styles", "list", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["data"]["styles"][0]["id"] == "rte"
    res = runner.invoke(app, ["env", "check", "--json"])
    assert res.exit_code == 0
    assert set(json.loads(res.output)) == ENVELOPE


def test_unverified_profile_is_declared(tmp_path: Path, unverified: None) -> None:
    """配置档没经工厂确认时，job init 如实提醒（PROFILE）。"""
    d = tmp_path / "job1"
    res = runner.invoke(
        app,
        ["job", "init", str(d), "--style", "rte", "--L", "80", "--W", "50", "--H", "180", "--json"],
    )
    assert res.exit_code == 0, res.output
    assert any(w["id"] == "PROFILE" for w in json.loads(res.output)["warnings"])
