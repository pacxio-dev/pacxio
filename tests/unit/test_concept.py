"""概念图：提示词（允许文字）、出图 / 导入 / 选定、取色、守卫（图本身不进印刷文件）、与效果图对照。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.concept.palette import extract_palette
from pacxio.visual.prompts import (
    CONCEPT_NO_TEXT,
    CONCEPT_TEXT_RULE,
    NO_TEXT,
    build_concept_prompt,
)
from tests.unit.conftest import SECRET, FakeApi
from tests.unit.fontkit import make_font

runner = CliRunner()


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "paint"
    res = runner.invoke(
        app, ["job", "init", str(d), "--L", "240", "--W", "150", "--H", "210", "--json"]
    )
    assert res.exit_code == 0, res.output
    fonts = tmp_path / "fonts"
    make_font(fonts / "TestFont.ttf", "TestFont")
    monkeypatch.setenv("PACXIO_FONT_DIR", str(fonts))
    monkeypatch.chdir(tmp_path)
    return d


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    res = runner.invoke(app, [*args, "--json"])
    return res.exit_code, json.loads(res.output)


def two_tone(path: Path) -> Path:
    """左半深绿、右半米白的测试图。"""
    im = Image.new("RGB", (200, 100), (245, 238, 228))
    im.paste((61, 69, 54), (0, 0, 100, 100))
    im.save(path)
    return path


# ---------------- 提示词 ----------------


def test_concept_prompt_carries_exact_texts_box_shape_and_real_proportions() -> None:
    p = build_concept_prompt(
        "soft green organic shapes, interior photo inset",
        ["TIDEWELL", "Water-Based Paint"],
        "rte",
        (240, 150, 210),
    )
    assert '"TIDEWELL"' in p and '"Water-Based Paint"' in p and CONCEPT_TEXT_RULE in p
    assert "reverse tuck end" in p and "240 mm wide, 150 mm deep, 210 mm tall" in p
    assert "1 : 0.62 : 0.88" in p  # 正面宽 : 侧面深 : 高
    assert NO_TEXT not in p  # 概念图允许文字：不能带素材的"无文字"约束
    assert "Three-quarter view" in p and "photography" in p
    ste = build_concept_prompt("x", [], "ste", (80, 50, 180), scene="on a marble table")
    assert (
        "straight tuck end" in ste and CONCEPT_NO_TEXT in ste and ste.endswith("on a marble table")
    )


def test_concept_prompt_for_pouch_is_film_not_box_with_width_height_gusset() -> None:
    # 软袋的 L / W / H 是袋宽 / 袋高 / 底褶；以前落到默认"纸盒"，AI 画成了纸盒（示例项目 Saltwick）
    p = build_concept_prompt("navy and chips photo", ["SALTWICK"], "pouch_standup", (160, 230, 90))
    assert "stand-up pouch" in p and "NOT a paperboard box" in p
    assert "160 mm wide and 230 mm tall, with a 90 mm bottom gusset" in p and "1 : 1.44" in p
    assert "Box size" not in p and "printed on the pouch" in p and "standing upright" in p
    flat = build_concept_prompt("x", [], "sachet_3side", (100, 140, 0))
    assert "three-side-seal sachet" in flat and "100 mm wide and 140 mm tall;" in flat


@pytest.mark.pro
@pytest.mark.parametrize(
    ("style", "depth", "extra", "faces"),
    [
        ("pouch_standup", "90", [], "back"),
        ("sachet_3side", "0", [], "back"),
        ("pouch_backseal", "0", [], "back_b,back_a"),
        ("pouch_backseal", "35", [], "back_b,back_a,side_a"),
        ("pouch_backseal", "35", ["--set", "seam=corner"], "back,side_a"),
        ("pouch_flat_bottom", "0", [], "back_b,back_a,side_a"),
        ("pouch_flat_bottom", "60", ["--set", "seam=corner"], "back,side_a"),
    ],
)
def test_pouch_back_prompt_uses_real_print_panels(
    tmp_path: Path, style: str, depth: str, extra: list[str], faces: str
) -> None:
    job = str(tmp_path / "pouch")
    code, out = cli(
        "job", "init", job, "--style", style, "--L", "160", "--W", "230", "--H", depth,
        "--material", "pouch_pet_vmpet_pe", *extra,
    )  # fmt: skip
    assert code == 0, out
    front = two_tone(tmp_path / "front.png")
    assert cli("concept", "import", job, str(front), "--name", "main-A")[0] == 0
    assert cli("concept", "choose", job, "main-A", "--by", "tester")[0] == 0
    assert cli("plan", job)[0] == 0

    code, out = cli("concept", "prompt", job, "--step", "back")
    assert code == 0, out
    prompt = Path(out["artifacts"][0]).read_text()
    assert f"--faces {faces}\n" in prompt
    assert "160 mm wide and 230 mm tall" in prompt and "width : height = 1 : 1.44" in prompt
    assert "flexible film" in prompt and "side depth : height" not in prompt
    assert ("Show the left side gusset" in prompt) == ("side_a" in faces)
    assert ("vertical center seam" in prompt) == ("back_b" in faces)
    if style == "pouch_flat_bottom" and depth == "0":
        assert "64 mm deep flat bottom" in prompt

    rear = tmp_path / "rear.png"
    Image.new("RGB", (200, 100), (55, 80, 101)).save(rear)
    code, out = cli(
        "concept", "import", job, str(rear), "--name", "main-A-back1", "--view", "back",
        "--faces", faces,
    )  # fmt: skip
    assert code == 0, out


# ---------------- 出图 ----------------


def test_concept_gen_makes_n_concepts_with_provenance(job: Path, api: type[FakeApi]) -> None:
    code, out = cli("concept", "gen", str(job), "--design", "soft green organic shapes", "--text", "TIDEWELL",
                    "--text", "Water-Based Paint", "--n", "2")  # fmt: skip
    assert code == 0, out
    assert out["data"]["concepts"] == ["c1", "c2"] and len(api.requests) == 2
    body = api.requests[0]["json"]
    assert (
        body["size"] == "1536x1024"
        and '"TIDEWELL"' in body["prompt"]
        and NO_TEXT not in body["prompt"]
    )
    meta = json.loads((job / "concepts" / "c1.meta.json").read_text())
    assert meta["texts"] == ["TIDEWELL", "Water-Based Paint"] and meta["provider"] == "openai"
    assert meta["design"] == "soft green organic shapes" and meta["full_prompt"] == body["prompt"]
    assert any(w["id"] == "CONCEPT" for w in out["warnings"])
    for f in job.rglob("*"):
        if f.is_file():
            assert SECRET.encode() not in f.read_bytes()


def test_concept_gen_rejects_bad_n_and_stops_on_provider_error(
    job: Path, api: type[FakeApi]
) -> None:
    assert cli("concept", "gen", str(job), "--design", "x", "--n", "5")[0] == 2
    assert cli("concept", "gen", str(job), "--design", "x", "--n", "0")[0] == 2
    api.status, api.body = 429, {"error": {"message": "quota"}}
    code, out = cli("concept", "gen", str(job), "--design", "x", "--n", "3")
    assert (
        code == 1 and "HTTP 429" in out["errors"][0]["message"] and len(api.requests) == 1
    )  # 失败就停，不继续花钱
    assert not (job / "concepts").exists() or not list((job / "concepts").glob("*.png"))


def test_concept_gen_without_key_is_environment_error(
    job: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code, out = cli("concept", "gen", str(job), "--design", "x")
    assert code == 3 and "OPENAI_API_KEY" in out["errors"][0]["message"]
    assert not (job / "concepts").exists()  # 失败不留半成品


# ---------------- 导入 / 列表 ----------------


def test_concept_import_list_and_codex_latest(
    job: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "designer.webp"
    Image.new("RGB", (300, 200), (10, 120, 60)).save(src, "WEBP")
    code, out = cli("concept", "import", str(job), str(src))
    assert code == 0 and out["data"] == {"id": "c1", "size_px": [300, 200]}
    assert (job / "concepts" / "c1.png").is_file()  # 统一存成 PNG
    assert cli("concept", "import", str(job), str(src), "--name", "c1")[0] == 2  # 重名
    code, out = cli("concept", "import", str(job), str(src), "--name", "again")  # 同一张图再导入
    assert (
        code == 2 and out["errors"][0]["id"] == "DUPLICATE" and "c1" in out["errors"][0]["message"]
    )
    home = tmp_path / "codex"
    (home / "generated_images").mkdir(parents=True)
    Image.new("RGB", (64, 64)).save(home / "generated_images" / "x.png")
    monkeypatch.setenv("CODEX_HOME", str(home))
    code, out = cli("concept", "import", str(job), "--codex-latest", "--name", "fromcodex")
    assert code == 0 and out["data"]["id"] == "fromcodex"
    code, out = cli("concept", "import", str(job), "--codex-latest", "--name", "fromcodex2")
    assert code == 2 and "还没出新图" in out["errors"][0]["fix"]  # Codex 没出新图就导入
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    assert cli("concept", "import", str(job), str(bad))[0] == 2
    code, out = cli("concept", "list", str(job))
    assert [c["id"] for c in out["data"]["concepts"]] == ["c1", "fromcodex"] and out["data"][
        "chosen"
    ] is None


# ---------------- 选定与取色 ----------------


@pytest.mark.pro
def test_palette_cmyk_conversion_is_correct() -> None:
    """回归：uint8 直接乘 100 曾经溢出，CMYK 全是 0 / 1。"""
    for rgb, cmyk in (
        ((0, 0, 0), (0, 0, 0, 100)),
        ((255, 0, 0), (0, 100, 100, 0)),
        ((255, 255, 255), (0, 0, 0, 0)),
    ):
        pal = extract_palette(Image.new("RGB", (20, 20), rgb))
        assert pal[0].cmyk_pct == cmyk and pal[0].share == pytest.approx(1.0)


@pytest.mark.pro
def test_palette_respects_region_and_is_deterministic(tmp_path: Path) -> None:
    img = Image.open(two_tone(tmp_path / "t.png"))
    whole = extract_palette(img)
    assert {c.hex for c in whole} == {"#3D4536", "#F5EEE4"} and [c.share for c in whole] == [
        0.5,
        0.5,
    ]
    left = extract_palette(img, (0.0, 0.0, 0.5, 1.0))
    assert [c.hex for c in left] == ["#3D4536"]
    assert extract_palette(img) == whole  # 确定性
    with pytest.raises(ValueError):
        extract_palette(img, (0.5, 0.0, 0.2, 1.0))


@pytest.mark.pro
def test_choose_records_direction_and_palette(job: Path, tmp_path: Path) -> None:
    cli("concept", "import", str(job), str(two_tone(tmp_path / "t.png")))
    code, out = cli(
        "concept",
        "choose",
        str(job),
        "c1",
        "--region",
        "0,0,0.5,1",
        "--by",
        "王总",
        "--note",
        "标题再大一点",
    )
    assert code == 0 and out["data"]["palette"] == [
        {"hex": "#3D4536", "share": 1.0, "cmyk": "12,0,22,73"}
    ]
    chosen = json.loads((job / "concepts" / "chosen.json").read_text())
    assert chosen["id"] == "c1" and chosen["by"] == "王总" and chosen["note"] == "标题再大一点"
    assert chosen["region"] == [0, 0, 0.5, 1] and len(chosen["sha256"]) == 64
    assert cli("concept", "list", str(job))[1]["data"]["chosen"] == "c1"
    code, out = cli("concept", "choose", str(job), "c9")
    assert code == 2 and "c1" in out["errors"][0]["fix"]
    code, out = cli("concept", "choose", str(job), "c1", "--region", "0.6,0,0.2,1")
    assert code == 2 and out["errors"][0]["id"] == "ARG"
    code, out = cli("concept", "choose", str(job), "c1")  # 不给区域：提醒场景颜色会混进来
    assert code == 0 and any("--region" in (w["fix"] or "") for w in out["warnings"])


# ---------------- 守卫：概念图的图本身不进印刷文件 ----------------


@pytest.mark.pro
def test_concepts_are_refused_everywhere_in_the_print_pipeline(job: Path, tmp_path: Path) -> None:
    cli("concept", "import", str(job), str(two_tone(tmp_path / "t.png")))
    rel, absolute = "concepts/c1.png", str(job / "concepts" / "c1.png")
    for src in (rel, absolute):
        code, out = cli(
            "art",
            "add",
            str(job),
            "--kind",
            "image",
            "--source",
            src,
            "--panel",
            "front",
            "--box",
            "0,0,50,50",
        )
        assert code == 2 and out["errors"][0]["id"] == "CONCEPT", src
        code, out = cli(
            "art", "add", str(job), "--kind", "pattern", "--source", src, "--tile-mm", "20"
        )
        assert code == 2 and out["errors"][0]["id"] == "CONCEPT"
        code, out = cli(
            "finish",
            "add",
            str(job),
            "--type",
            "foil_gold",
            "--source",
            src,
            "--panel",
            "front",
            "--box",
            "0,0,50,50",
        )
        assert code == 2 and out["errors"][0]["id"] == "CONCEPT"
    code, out = cli("visual", "import", str(job), absolute, "--name", "hero")
    assert code == 2 and out["errors"][0]["id"] == "CONCEPT"
    code, out = cli("visual", "prepare", str(job), rel)
    assert code == 2 and out["errors"][0]["id"] == "CONCEPT"


def test_codex_latest_that_is_still_the_concept_is_not_imported_as_a_print_asset(
    job: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 还没出新图时，--codex-latest 取到的还是概念图那一张：不能当印刷素材导入。"""
    home = tmp_path / "codex"
    (home / "generated_images").mkdir(parents=True)
    two_tone(home / "generated_images" / "gen.png")
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert cli("concept", "import", str(job), "--codex-latest")[0] == 0  # 概念图 c1
    code, out = cli("visual", "import", str(job), "--codex-latest", "--name", "hero")
    err = out["errors"][0]
    assert code == 2 and err["id"] == "CONCEPT" and "c1" in err["message"]
    assert "还没出新图" in err["fix"] and not (job / "assets" / "hero.png").exists()
    Image.new("RGB", (200, 100), (200, 30, 30)).save(tmp_path / "new.png")  # 新出的图照常导入
    assert cli("visual", "import", str(job), str(tmp_path / "new.png"), "--name", "hero")[0] == 0


@pytest.mark.pro
def test_hand_edited_job_referencing_a_concept_is_stopped_at_layout(
    job: Path, tmp_path: Path
) -> None:
    """有人绕过命令行直接改 job.json：排版合成这一道也会挡住。"""
    cli("concept", "import", str(job), str(two_tone(tmp_path / "t.png")))
    data = json.loads((job / "job.json").read_text())
    data["placements"] = [{"id": "sneaky", "kind": "image", "source": "concepts/c1.png", "panel": "front",
                           "box": {"x": 0, "y": 0, "w": 50, "h": 50}, "fit": "cover"}]  # fmt: skip
    (job / "job.json").write_text(json.dumps(data))
    code, out = cli("preflight", str(job))
    assert (
        code == 1
        and out["errors"][0]["id"] == "CONCEPT"
        and "sneaky" in out["errors"][0]["message"]
    )
    code, out = cli("export", str(job))
    assert code == 1 and not list((job / "out").rglob("*_production.pdf"))


# ---------------- 与效果图对照 ----------------


@pytest.mark.pro
def test_mockup_shows_the_chosen_concept_and_approval_records_it(job: Path, tmp_path: Path) -> None:
    cli("art", "add", str(job), "--kind", "text", "--tag", "品牌名", "--panel", "front", "--text", "TIDEWELL",
        "--font", "TestFont", "--size-pt", "30", "--box", "20,160,200,25")  # fmt: skip
    code, out = cli("mockup", str(job))
    assert any(w["id"] == "MK07" for w in out["warnings"])  # 没有选定的概念图：提醒先定方向
    sheet_without = Image.open(job / "mockup" / "00_mockup_sheet.png").size[1]
    cli("concept", "import", str(job), str(two_tone(tmp_path / "t.png")))
    cli("concept", "choose", str(job), "c1")
    code, out = cli("mockup", str(job))
    assert (
        code == 0
        and out["data"]["concept"] == "c1"
        and not any(w["id"] == "MK07" for w in out["warnings"])
    )
    m = json.loads((job / "mockup" / "manifest.json").read_text())
    assert m["concept"]["id"] == "c1" and len(m["concept"]["sha256"]) == 64
    assert Image.open(job / "mockup" / "00_mockup_sheet.png").size[1] > sheet_without  # 多了对照区
    assert cli("render", str(job))[0] == 0  # 确认的是刀版确认图
    code, out = cli("approve", str(job), "--by", "王总")
    assert code == 0 and out["data"]["concept"] == "c1"
    assert json.loads((job / "mockup" / "approval.json").read_text())["concept_id"] == "c1"
    code, out = cli("export", str(job), "--require-approval")
    assert code == 0
    pack = Path(out["data"]["pack_dir"])
    names = {
        p.name for folder in (pack, Path(out["data"]["archive_dir"])) for p in folder.iterdir()
    }
    assert "confirm.png" in names  # 确认过的刀版确认图放进存档
    assert not any(
        "c1" in n or n.startswith("concept") for n in names
    )  # 概念图本身不进给工厂的交付包


@pytest.mark.pro
def test_concept_does_not_change_the_design_fingerprint(job: Path, tmp_path: Path) -> None:
    """概念图只是方向参考：增删概念图不会让已确认的效果图"过期"。"""
    from pacxio.mockup.approval import design_fingerprint
    from pacxio.model.job import Job

    f0 = design_fingerprint(job, Job.load(job))
    cli("concept", "import", str(job), str(two_tone(tmp_path / "t.png")))
    cli("concept", "choose", str(job), "c1")
    assert design_fingerprint(job, Job.load(job)) == f0
    assert os.path.isdir(job / "concepts") and np.asarray(
        Image.open(job / "concepts" / "c1.png")
    ).shape == (100, 200, 3)
