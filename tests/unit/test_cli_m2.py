"""M2 的 CLI：art add / finish add / panels / preflight / render / 整包确定性。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.model.job import Job

runner = CliRunner()
FONT = "SourceHanSansSC-Bold"


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tea"
    res = runner.invoke(
        app,
        ["job", "init", str(d), "--style", "rte", "--L", "80", "--W", "50", "--H", "180", "--json"],
    )
    assert res.exit_code == 0, res.output
    Image.new("RGB", (1000, 1650), (30, 120, 80)).save(tmp_path / "hero.png")
    (tmp_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="12"><rect width="30" height="12"/></svg>'
    )
    return d


def run(*args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    res = runner.invoke(app, [*args, "--json"])
    return res.exit_code, json.loads(res.output)


@pytest.mark.pro
def test_art_add_copies_asset_into_job_and_records_relative_path(
    job_dir: Path, tmp_path: Path
) -> None:
    code, out = run("art", "add", str(job_dir), "--kind", "image", "--source", str(tmp_path / "hero.png"),
                    "--panel", "front", "--box", "5,40,70,110")  # fmt: skip
    assert code == 0 and out["data"]["id"] == "image1"
    assert (job_dir / "assets" / "hero.png").is_file()  # Job 目录即状态：素材进了 job
    job = Job.load(job_dir)
    p = job.placements[0]
    assert p.source == "assets/hero.png" and p.box is not None and p.box.w == 70


@pytest.mark.pro
def test_art_add_same_name_different_content_does_not_overwrite(
    job_dir: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(other / "hero.png")
    args = ["art", "add", str(job_dir), "--kind", "pattern", "--tile-mm", "20", "--source"]
    run(*args, str(tmp_path / "hero.png"))
    run(*args, str(other / "hero.png"))
    names = sorted(p.name for p in (job_dir / "assets").iterdir())
    assert len(names) == 2 and "hero.png" in names  # 第二份加了内容哈希后缀


@pytest.mark.pro
@pytest.mark.parametrize(
    "args,exit_code,rule",
    [
        (["--kind", "fill"], 2, "ARG"),  # 缺 --color
        (["--kind", "fill", "--color", "0,0,0,200"], 2, "ARG"),  # 超出 0–100
        (["--kind", "image", "--source", "nope.png", "--panel", "front", "--box", "0,0,5,5"], 2, "ASSET"),
        (["--kind", "fill", "--color", "0,0,0,5", "--scope", "zzz"], 2, "PANEL"),
        (["--kind", "text", "--panel", "front", "--text", "x", "--font", "NoSuchFont",
          "--size-pt", "10", "--box", "0,0,20,10"], 2, "FONT"),
    ],
)  # fmt: skip
def test_art_add_rejects_bad_input_with_exit_2(
    job_dir: Path, args: list[str], exit_code: int, rule: str
) -> None:
    code, out = run("art", "add", str(job_dir), *args)
    assert code == exit_code and out["errors"][0]["id"] == rule and out["errors"][0]["fix"]
    assert Job.load(job_dir).placements == []  # 失败不改 job.json


@pytest.mark.pro
def test_art_add_duplicate_id_and_bad_box_syntax(job_dir: Path) -> None:
    assert (
        run("art", "add", str(job_dir), "--kind", "fill", "--color", "0,0,0,5", "--id", "bg")[0]
        == 0
    )
    code, out = run(
        "art", "add", str(job_dir), "--kind", "fill", "--color", "0,0,0,5", "--id", "bg"
    )
    assert code == 2 and "已存在" in out["errors"][0]["message"]
    res = runner.invoke(app, ["art", "add", str(job_dir), "--kind", "fill", "--box", "1,2,3"])
    assert res.exit_code == 2  # 参数格式错误：typer 的参数错误退出码
    # 铺底色按 scope 铺满整面：给 panel / box 会以为是局部色块，其实盖住整面，直接拦下
    code, out = run("art", "add", str(job_dir), "--kind", "fill", "--color", "0,0,0,0",
                    "--panel", "side_a", "--box", "20,15,40,30")  # fmt: skip
    assert code == 2 and "--panel / --box" in out["errors"][0]["message"]
    assert "kind=vector" in out["errors"][0]["fix"]


@pytest.mark.pro
def test_finish_add_validates_type_panel_and_source(job_dir: Path, tmp_path: Path) -> None:
    ok = ["finish", "add", str(job_dir), "--type", "foil_gold", "--source", str(tmp_path / "logo.svg"),
          "--panel", "front", "--box", "25,20,30,12"]  # fmt: skip
    code, out = run(*ok)
    assert code == 0 and out["data"]["id"] == "foil_gold1"
    assert (job_dir / "assets" / "logo.svg").is_file()
    bad_type = [*ok[:4], "magic_ink", *ok[5:]]  # 替换 --type 的值
    code, out = run(*bad_type)
    assert (
        code == 2
        and "foil_gold" in out["errors"][0]["fix"]
        and "cut" not in out["errors"][0]["fix"]
    )
    code, out = run(*ok[:8], "zzz", *ok[9:])  # 替换 --panel 的值
    assert code == 2 and out["errors"][0]["id"] == "PANEL"


def test_panels_command_json(job_dir: Path) -> None:
    code, out = run("panels", str(job_dir))
    assert code == 0
    back = next(p for p in out["data"]["panels"] if p["id"] == "back")
    assert back["px"]["w"] == 951 and back["safe_box_local_mm"]["w"] == 74.48


@pytest.mark.pro
def test_preflight_command_writes_report_and_exit_code_follows_errors(
    job_dir: Path, tmp_path: Path
) -> None:
    run("art", "add", str(job_dir), "--kind", "fill", "--color", "0,0,0,5", "--id", "bg")
    code, out = run("preflight", str(job_dir))
    assert code == 0 and out["data"]["report"]["passed"] is True
    rep = Path(job_dir / "out/preflight/05_preflight_report.json")
    assert rep.is_file() and json.loads(rep.read_text())["job"] == "tea"
    # 本机没有 ICC 照旧报 X02；只有底色、没有照片时不说"照片按简单公式转"（天地盖测试 F-18）
    x02 = next(w for w in out["warnings"] if w["id"] == "X02")
    assert "没有照片" in x02["message"] and "照片按" not in x02["message"]
    # 低分辨率图 → I01 error → 退出码 1
    Image.new("RGB", (100, 150), (9, 9, 9)).save(tmp_path / "low.png")
    run("art", "add", str(job_dir), "--kind", "image", "--source", str(tmp_path / "low.png"),
        "--panel", "front", "--box", "5,40,70,110")  # fmt: skip
    code, out = run("preflight", str(job_dir))
    assert code == 1 and out["ok"] is False and out["errors"][0]["id"] == "I01"
    assert {"X02"} <= {w["id"] for w in out["warnings"]}


@pytest.mark.pro
def test_render_writes_png_preview_without_running_checks(job_dir: Path) -> None:
    run("art", "add", str(job_dir), "--kind", "fill", "--color", "10,0,0,0")
    code, out = run("render", str(job_dir))
    assert code == 0
    png = job_dir / "preview.png"
    assert png.is_file() and Image.open(png).size[0] > 200
    assert not (job_dir / "out").exists()  # render 不生成印刷文件、不跑检查


@pytest.mark.pro
def test_full_pack_is_byte_identical_across_runs(
    job_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D8：同一 job.json 两次导出，所有交付文件逐字节一致（固定文档日期）。"""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    run("art", "add", str(job_dir), "--kind", "fill", "--color", "4,2,12,0", "--id", "bg")
    run("art", "add", str(job_dir), "--kind", "image", "--source", str(tmp_path / "hero.png"),
        "--panel", "front", "--box", "5,40,70,110")  # fmt: skip
    run("finish", "add", str(job_dir), "--type", "foil_gold", "--source", str(tmp_path / "logo.svg"),
        "--panel", "front", "--box", "25,20,30,12")  # fmt: skip
    packs = []
    for i in (1, 2):
        out_dir = tmp_path / f"o{i}"
        code, out = run("export", str(job_dir), "--proof", "--out", str(out_dir))
        assert code == 0, out
        packs.append(Path(out["data"]["pack_dir"]))
    folders = [[p, p.with_name(p.name + "_archive")] for p in packs]  # 交付包 + 存档
    names = sorted(f"{d.name[-8:]}/{p.name}" for d in folders[0] for p in d.iterdir())
    assert (
        names == sorted(f"{d.name[-8:]}/{p.name}" for d in folders[1] for p in d.iterdir())
        and "_archive/finish_foil_gold.pdf" in names
    )
    for a, b in zip(folders[0], folders[1], strict=True):
        for f in a.iterdir():  # PNG 由 PIL 写出，同样应一致
            assert f.read_bytes() == (b / f.name).read_bytes(), f.name
