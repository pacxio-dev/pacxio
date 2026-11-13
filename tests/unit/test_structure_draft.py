"""不建 job 出刀版文件 / 改结构预览，以及结构目录的英文（，网站第一期 S1）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pacxio import api
from pacxio.cli import app
from pacxio.engine import catalog, i18n
from pacxio.model.common import Issue


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    res = CliRunner().invoke(app, [*args, "--json"])
    return res.exit_code, json.loads(res.output)


@pytest.mark.pro
@pytest.mark.parametrize(
    ("code", "dims"),
    [
        ("0201", (300, 200, 150)),
        ("0427", (200, 150, 60)),
        ("A20.20.03.01", (80, 50, 120)),
        ("0300", (300, 200, 100)),
    ],
)
def test_stateless_files_match_the_delivery_pack(
    tmp_path: Path, code: str, dims: tuple[float, float, float]
) -> None:
    """S1 验收：不建 job 出的刀版文件和走 job（job init → export）出的逐字节一样（刀模圆角、多件套都算上）。"""
    job = api.draft_job(code, *dims)
    assert not isinstance(job, Issue)
    mine, _ = api.dieline_files(job)
    e = catalog.get(code)
    assert e is not None
    found = api.structure_params(
        None if e.catalog == "ECMA" else code, code if e.catalog == "ECMA" else None, None, *dims
    )
    assert not isinstance(found, Issue)
    d = tmp_path / job.job_id
    assert api.init_job(d, found[0], found[1], job.material.preset).ok
    ex = api.export_job(d, ["dxf", "cf2", "dieline_pdf"], out_dir=tmp_path / "out")
    assert ex.ok, ex.errors
    got = [
        Path(a).read_bytes()
        for a in ex.artifacts
        if Path(a).suffix in (".dxf", ".cf2", ".pdf") and "dieline" in Path(a).name
    ]
    for name, data in mine.items():
        if name.endswith((".dxf", ".cf2", ".pdf")):
            assert data in got, name
    parts = 2 if code == "0300" else 1
    assert len(mine) == 4 * parts


def test_files_command_and_errors(tmp_path: Path) -> None:
    code, out = cli(
        "structures",
        "files",
        "0427",
        "--L",
        "200",
        "--W",
        "150",
        "--H",
        "60",
        "--out",
        str(tmp_path / "f"),
    )
    assert code == 0, out
    assert sorted(Path(a).suffix for a in out["artifacts"]) == [".cf2", ".dxf", ".pdf", ".svg"]
    code, out = cli("structures", "files", "0427", "--L", "200", "--W", "150", "--H", "60", "--out", str(tmp_path / "g"),
                    "--format", "dxf")  # fmt: skip
    assert code == 0 and len(out["artifacts"]) == 1
    code, out = cli("structures", "files", "0427", "--L", "200", "--W", "150", "--H", "60", "--out", str(tmp_path / "h"),
                    "--format", "png")  # fmt: skip
    assert code == 2 and "格式" in out["errors"][0]["message"]
    code, out = cli(
        "structures",
        "files",
        "9999",
        "--L",
        "200",
        "--W",
        "150",
        "--H",
        "60",
        "--out",
        str(tmp_path / "i"),
    )
    assert code == 2


def test_draft_is_the_same_as_trying_on_a_job(tmp_path: Path) -> None:
    """网站生成器用的 structure_draft 和工作台改结构页（structure_try）是同一个函数：刀版 SVG、3D 场景一样。"""
    draft = api.structure_draft("0427", 200, 150, 60, patch={"top": {"tuck_len": 20}})
    assert draft.ok, draft.errors
    assert draft.data["scene"]["parts"] and "<svg" in draft.data["svg"]
    found = api.structure_params("0427", None, None, 200, 150, 60)
    assert not isinstance(found, Issue)
    d = tmp_path / "m"
    assert api.init_job(d, found[0], found[1], "flute_b").ok
    tried = api.structure_try(d, {"top": {"tuck_len": 20}})
    assert tried.ok
    assert tried.data["svg"] == draft.data["svg"]
    assert tried.data["sizes"] == draft.data["sizes"]


def test_catalog_has_english_for_every_entry() -> None:
    for e in catalog.entries():
        d = i18n.entry_dict(e, "en")
        left = {k: v for k, v in d.items() if i18n.has_cjk(str(v))}
        assert not left, (e.code, left)
    assert i18n.entry_dict(catalog.get("0201"), "zh")["series_zh"]  # 中文照旧


def test_english_in_commands() -> None:
    code, out = cli("structures", "list", "--search", "0427", "--lang", "en")
    assert code == 0 and out["data"]["structures"][0]["description"].startswith("Mailer")
    code, out = cli("structures", "list", "--lang", "fr")
    assert code == 2
    code, out = cli(
        "structures",
        "editable",
        "--fefco",
        "0201",
        "--L",
        "300",
        "--W",
        "200",
        "--H",
        "150",
        "--lang",
        "en",
    )
    assert code == 0
    for item in out["data"]["parameters"]:
        texts = [
            item["name"],
            item["part"],
            item.get("explain", ""),
            *(item.get("option_names") or {}).values(),
        ]
        assert not any(i18n.has_cjk(str(t)) for t in texts), item["path"]


def test_every_term_has_english() -> None:
    from pacxio.engine.editable_terms import EXPLAIN, OPTIONS, PART_ORDER, TERMS

    e = i18n.table()["editable"]
    assert set(TERMS) <= set(e["terms"])
    assert set(EXPLAIN) <= set(e["explain"])
    assert set(PART_ORDER) <= set(e["parts"])
    for key, values in OPTIONS.items():
        assert set(values) <= set(e["options"][key]), key
