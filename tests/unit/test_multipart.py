"""多部件盒型：tray_lid 天地盖、drawer 抽屉盒（，–8.4.9）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pikepdf
import pytest
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.dielines import build_parts_for
from pacxio.mockup.scene import build_scene
from pacxio.model.job import Box, Job, Placement
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style

runner = CliRunner()
PROFILE = load_profile("default")

# 草案第 2.7、3.7 节：每个部件的展开尺寸 + 合上后的外尺寸（按默认公式算的【初值】）
TRAY_LID = [
    ((200, 150, 60), "sbs_350", {}, (450.3, 271.4), (456.1, 274.4), (206.8, 152.9, 61.4)),
    ((300, 200, 100), "sbs_350", {}, (710.3, 401.4), (716.1, 404.4), (306.8, 202.9, 101.4)),
    ((120, 120, 50), "sbs_350", {}, (330.3, 221.4), (336.1, 224.4), (126.8, 122.9, 51.4)),
    ((300, 200, 100), "sbs_350", {"lid_h": 30}, (710.3, 401.4), (443.9, 261.5), (306.8, 202.9, 101.4)),
    ((200, 150, 60), "flute_e", {}, (459.5, 274.5), (476.0, 282.0), (219.5, 157.5, 64.5)),
    ((300, 200, 100), "flute_e", {}, (719.5, 404.5), (736.0, 412.0), (319.5, 207.5, 104.5)),
    ((120, 120, 50), "flute_e", {}, (339.5, 224.5), (356.0, 232.0), (139.5, 127.5, 54.5)),
    ((300, 200, 100), "flute_e", {"lid_h": 30}, (719.5, 404.5), (438.0, 263.0), (319.5, 207.5, 104.5)),
]  # fmt: skip
DRAWER = [
    ((100, 70, 20), "sbs_350", (141.4, 160.3), (265.7, 72.9), (103.4, 72.9, 22.9)),
    ((150, 100, 40), "sbs_350", (231.4, 270.3), (405.7, 102.9), (153.4, 102.9, 42.9)),
    ((80, 160, 30), "sbs_350", (141.4, 290.3), (245.7, 162.9), (83.4, 162.9, 32.9)),
    ((200, 200, 60), "sbs_350", (321.4, 450.3), (545.7, 202.9), (203.4, 202.9, 62.9)),
    ((150, 100, 40), "sbs_400", (231.7, 271.0), (406.7, 103.3), (153.8, 103.3, 43.2)),
]  # fmt: skip


def parts_of(style_id: str, dims: tuple[float, float, float], mat: str, **kw: Any):  # type: ignore[no-untyped-def]
    style = get_style(style_id)
    L, W, H = dims
    params = style.param_schema()(L=L, W=W, H=H, **kw)
    m = resolve_material(mat)
    assert style.validate(params, m, PROFILE) == []
    parts = style.generate_parts(params, m, PROFILE)
    expected = style.expected_panel_sizes(params, m, PROFILE)
    for part, dl in parts.items():
        assert check_s01(dl) == [] and check_crease_sides(dl) == [], part
        assert check_s02(dl, expected[part]) == [], part
    assert style.check_fit(parts, params, m, PROFILE) == []  # S05：部件之间配得上
    return parts


@pytest.mark.parametrize("dims,mat,kw,base,lid,closed", TRAY_LID)
def test_tray_lid_parts(dims, mat, kw, base, lid, closed) -> None:  # type: ignore[no-untyped-def]
    parts = parts_of("tray_lid", dims, mat, **kw)
    assert [round(v, 1) for v in parts["base"].bbox[2:]] == list(base)
    assert [round(v, 1) for v in parts["lid"].bbox[2:]] == list(lid)
    assert [round(v, 1) for v in parts["lid"].style_meta["set"]["closed_outer"]] == list(closed)


@pytest.mark.parametrize("dims,mat,tray,sleeve,closed", DRAWER)
def test_drawer_parts(dims, mat, tray, sleeve, closed) -> None:  # type: ignore[no-untyped-def]
    parts = parts_of("drawer", dims, mat)
    assert [round(v, 1) for v in parts["tray"].bbox[2:]] == list(tray)
    assert [round(v, 1) for v in parts["sleeve"].bbox[2:]] == list(sleeve)
    assert [round(v, 1) for v in parts["sleeve"].style_meta["set"]["closed_outer"]] == list(closed)


@pytest.mark.parametrize("kw", [{"lid": {"lugs": False}}, {"base": {"roll_gap": 0}}, {"lid_notch": False},
                                {"lid_clearance": 2.0}])  # fmt: skip
def test_tray_lid_variants(kw: dict[str, Any]) -> None:
    parts = parts_of("tray_lid", (200, 150, 60), "sbs_350", **kw)
    if "lid" in kw:
        assert not [
            e for e in parts["lid"].edges if e.id.startswith("cut_slot")
        ]  # 盖不插脚：盖顶上没有插孔


@pytest.mark.parametrize("kw", [{"notch_on": "top_bottom"}, {"pull": "none"}, {"tray": {"lugs": False}},
                                {"sleeve_len": 80}])  # fmt: skip
def test_drawer_variants(kw: dict[str, Any]) -> None:
    parts_of("drawer", (150, 100, 40), "sbs_350", **kw)


def test_validation_says_which_part_and_how_to_fix() -> None:
    st = get_style("tray_lid")
    m = resolve_material("sbs_350")
    issues = st.validate(st.param_schema()(L=200, W=150, H=60, lid_h=15), m, PROFILE)
    assert (
        issues
        and issues[0].message.startswith("天盖 lid：壁太矮")
        and "lid_h" in str(issues[0].fix)
    )
    issues = st.validate(st.param_schema()(L=200, W=150, H=60, lid_h=90), m, PROFILE)
    assert "地盒会悬在盖里" in issues[0].message
    dr = get_style("drawer")
    assert (
        "卡纸"
        in dr.validate(dr.param_schema()(L=150, W=100, H=40), resolve_material("flute_e"), PROFILE)[
            0
        ].message
    )
    with pytest.raises(ValueError):  # 部件参数写错名字：参数表直接拒绝
        st.param_schema()(L=200, W=150, H=60, lid={"lugz": False})


def test_fit_check_catches_parts_that_do_not_match() -> None:
    st = get_style("tray_lid")
    m = resolve_material("sbs_350")
    parts = st.generate_parts(st.param_schema()(L=200, W=150, H=60), m, PROFILE)
    issues = st.check_fit(
        parts, st.param_schema()(L=200, W=150, H=60, lid_clearance=2.0), m, PROFILE
    )
    assert issues and {i.id for i in issues} == {"S05"}


def job_with(style: str, placements: list[Placement], **kw: Any) -> Job:
    return Job(job_id="t", style=style, params={"L": 200, "W": 150, "H": 60}, material=resolve_material("sbs_350"),
               placements=placements, **kw)  # fmt: skip


def test_part_view_splits_placements() -> None:
    job = job_with("tray_lid", [
        Placement(id="bg", kind="fill", color={"cmyk": (0, 10, 20, 0)}),
        Placement(id="lidbg", kind="fill", part="lid", color={"cmyk": (0, 0, 0, 10)}),
        Placement(id="logo", kind="text", panel="lid.top", box=Box(x=10, y=10, w=50, h=20), text="A", font="F", size_pt=10),
        Placement(id="side", kind="text", panel="base.front", box=Box(x=10, y=10, w=50, h=20), text="B", font="F", size_pt=10),
    ], same_faces={"lid.back": "lid.front"})  # fmt: skip
    lid = job.for_part("lid")
    assert [p.id for p in lid.placements] == ["bg", "lidbg", "logo"]
    assert lid.placements[2].panel == "top" and lid.same_faces is None
    base = job.for_part("base")
    assert [(p.id, p.panel) for p in base.placements] == [("bg", None), ("side", "front")]
    assert job.for_part(None) is job  # 单部件盒型原样


def test_part_names_are_checked() -> None:
    bad = job_with("tray_lid", [Placement(id="x", kind="text", panel="top", box=Box(x=1, y=1, w=5, h=5),
                                          text="A", font="F", size_pt=8)])  # fmt: skip
    dset, issues = build_parts_for(bad)
    assert dset is None and issues[0].id == "PANEL" and "部件.面" in issues[0].message
    blank = job_with("tray_lid", [Placement(id="x", kind="vector", space="blank", source="a.svg",
                                            box=Box(x=1, y=1, w=5, h=5))])  # fmt: skip
    assert build_parts_for(blank)[1][0].id == "PANEL"


def test_scene_maps_closed_faces_to_full_names() -> None:
    parts = parts_of("tray_lid", (200, 150, 60), "sbs_350")
    closed = build_scene("tray_lid", parts)
    faces = {f.key: f.panel for f in closed.box.faces if ":" not in f.key}
    assert faces == {"front": "lid.front", "back": "lid.back", "side_a": "lid.side_a", "side_b": "lid.side_b",
                     "top": "lid.top", "bottom": "base.bottom"}  # fmt: skip
    opened = build_scene("tray_lid", parts, opened=True)
    inner = [f for f in opened.box.faces if f.key == "base:side_a:in"]
    assert inner and inner[0].panel == "base.side_a_in"  # 双层壁的内壁贴它自己的印刷图
    dparts = parts_of("drawer", (150, 100, 40), "sbs_350")
    dclosed = build_scene("drawer", dparts)
    assert {f.key: f.panel for f in dclosed.box.faces if ":" not in f.key}["front"] == "tray.front"


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    res = runner.invoke(app, [*args, "--json"])
    try:
        return res.exit_code, json.loads(res.output)
    except ValueError as e:
        raise AssertionError(res.output) from e


@pytest.fixture
def font_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.unit.fontkit import make_font

    make_font(tmp_path / "fonts" / "TestFont.ttf", "TestFont")
    monkeypatch.setenv("PACXIO_FONT_DIR", str(tmp_path / "fonts"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def text(job: Path, panel: str, words: str, *extra: str) -> tuple[int, dict[str, Any]]:
    return cli("art", "add", str(job), "--kind", "text", "--panel", panel, "--box", "10,10,60,12", "--text", words,
               "--font", "TestFont", "--size-pt", "12", *extra)  # fmt: skip


@pytest.mark.pro
def test_tray_lid_whole_pipeline(font_dir: Path) -> None:
    job = font_dir / "tl"
    code, out = cli("job", "init", str(job), "--style", "tray_lid", "--L", "200", "--W", "150", "--H", "60",
                    "--set", "lid.lugs=false")  # fmt: skip
    assert code == 0, out
    assert set(out["data"]["parts"]) == {"base", "lid"}
    assert Job.load(job).params["lid"] == {"lugs": False}  # 部件参数只存给了的那一项
    assert cli("art", "add", str(job), "--kind", "fill", "--color", "0,10,30,0")[0] == 0
    assert text(job, "top", "LID", "--part", "lid")[0] == 0
    assert any(
        p.panel == "lid.top" for p in Job.load(job).placements
    )  # --part lid --panel top → lid.top
    code, out = text(job, "top", "X")
    assert code == 2 and "未知的面" in out["errors"][0]["message"]
    code, out = cli("panels", str(job))
    assert "lid.top" in {p["id"] for p in out["data"]["panels"]}
    code, out = cli("render", str(job))
    assert {Path(a).name for a in out["artifacts"]} >= {
        "preview_base.png",
        "preview_lid.png",
        "dieline.svg",
    }
    code, out = cli("mockup", str(job))
    assert code == 0, out
    assert (job / "mockup" / "view_open.png").is_file()
    assert "MK14" in {w["id"] for w in out["warnings"]}
    code, out = cli("plan", str(job))
    assert (
        code == 0
        and (job / "plan" / "wireframe_lid.png").is_file()
        and (job / "plan" / "faces" / "lid.top.png").is_file()
    )
    code, out = cli("export", str(job), "--proof")
    assert code == 0, out
    pack, archive = Path(out["data"]["pack_dir"]), Path(out["data"]["archive_dir"])
    job_id = Job.load(job).job_id
    # ：不分子文件夹，部件名写在文件名里；规格书一份
    assert {p.name for p in pack.iterdir()} == {
        f"{job_id}_{part}_{suffix}" for part in ("base", "lid") for suffix in ("production.pdf", "dieline.dxf")
    } | {f"{job_id}_spec.pdf"}  # fmt: skip
    assert {"dieline_lid.cf2", "proof_1to1.pdf", "preflight_report.json", "job.json"} <= {
        p.name for p in archive.iterdir()
    }
    assert len(pikepdf.open(archive / "proof_1to1.pdf").pages) == 2  # 每个部件一页
    assert len(pikepdf.open(pack / f"{job_id}_spec.pdf").pages) >= 2  # 每个部件至少一页
    report = json.loads((archive / "preflight_report.json").read_text())
    assert "S05" in report["executed"]


@pytest.mark.pro
def test_one_bad_part_blocks_the_whole_pack(font_dir: Path) -> None:
    """D12 对整套：任何一个部件有 error 都不出交付包。"""
    job = font_dir / "dr"
    assert (
        cli("job", "init", str(job), "--style", "drawer", "--L", "150", "--W", "100", "--H", "40")[
            0
        ]
        == 0
    )
    assert cli("art", "add", str(job), "--kind", "fill", "--color", "0,10,30,0")[0] == 0
    code, out = cli("art", "add", str(job), "--kind", "text", "--panel", "sleeve.top", "--box", "0,0,60,12",
                    "--text", "EDGE", "--font", "TestFont", "--size-pt", "12")  # fmt: skip
    assert code == 0, out
    code, out = cli("export", str(job))
    assert code == 1 and any(
        e["id"] == "T01" and e["message"].startswith("外套 sleeve") for e in out["errors"]
    )
    files = {Path(a).name for a in out["artifacts"]}
    assert {"PREFLIGHT_FAILED_preview_tray.png", "PREFLIGHT_FAILED_preview_sleeve.png"} <= files


def test_windows_only_where_the_style_allows(font_dir: Path) -> None:
    job = font_dir / "w"
    assert (
        cli(
            "job", "init", str(job), "--style", "tray_lid", "--L", "200", "--W", "150", "--H", "60"
        )[0]
        == 0
    )
    code, out = cli("window", "add", str(job), "--panel", "lid.top", "--box", "60,40,80,60")
    assert code == 0, out
    code, out = cli("window", "add", str(job), "--panel", "base.front", "--box", "60,10,60,30")
    assert code == 1 and "lid.top" in out["errors"][0]["fix"]
