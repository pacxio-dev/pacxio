"""飞机盒 LUMERA 测试（Codex，`jobs/lumera-beauty-mailer-240x170x70/test-issues.md`）和抽屉盒测试
（`briefs/drawer-test/findings.md`）里剩下的问题。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio.cli import app

runner = CliRunner()


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    res = runner.invoke(app, [*args, "--json"])
    try:
        return res.exit_code, json.loads(res.output)
    except ValueError as e:
        raise AssertionError(res.output) from e


@pytest.fixture
def mailer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.unit.fontkit import make_font

    make_font(tmp_path / "fonts" / "TestFont.ttf", "TestFont")
    monkeypatch.setenv("PACXIO_FONT_DIR", str(tmp_path / "fonts"))
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "mailer"
    code, out = cli("job", "init", str(job), "--style", "fefco_0427", "--L", "240", "--W", "170",
                    "--H", "70", "--material", "flute_e")  # fmt: skip
    assert code == 0, out
    return job


def _ink_offset(img: Image.Image) -> tuple[float, float]:
    """单面图中间区域里深灰笔画的重心，相对笔画包围盒中心（右、下为正）。"""
    a = np.asarray(img.convert("L"), dtype=np.int16)
    h, w = a.shape
    a = a[h // 10 : h - h // 10, w // 10 : w - w // 10]
    ys, xs = np.nonzero(a < 130)
    assert len(xs) > 10, "单面图里找不到占位说明"
    return xs.mean() - (xs.min() + xs.max()) / 2, ys.mean() - (ys.min() + ys.max()) / 2


@pytest.mark.pro
def test_placeholder_label_follows_the_panel_upright_direction(mailer: Path) -> None:
    """ISSUE-001：盖在刀版上是倒着的（侧板是横着的），占位说明要跟着面转，单面图里才是正的。"""
    for pid, box in (("top_lid", "60,40,120,90"), ("side_a", "40,10,90,50")):
        code, out = cli("art", "add", str(mailer), "--kind", "placeholder", "--id", f"ph-{pid}",
                        "--panel", pid, "--box", box, "--label", "L")  # fmt: skip
        assert code == 0, out
    code, out = cli("plan", str(mailer))
    assert code == 0, out
    for pid in ("top_lid", "side_a"):
        dx, dy = _ink_offset(Image.open(mailer / "plan" / "faces" / f"{pid}.png"))
        assert dx < 0 and dy > 0, (
            f"{pid} 的 L 不是正的（重心偏 {dx:.1f}, {dy:.1f}）"
        )  # L 的笔画偏左下


def test_flat_prompt_explains_closed_box_width() -> None:
    """ISSUE-002：平面图按合上后看到的宽度画（底板整宽），印刷的插舌窄两层纸厚：提示词把两个数都写清楚。"""
    from pacxio.concept.prompts import _closed_width_notes
    from pacxio.model.profile import load_profile, resolve_material
    from pacxio.styles import get_style

    profile = load_profile("default")
    st = get_style("fefco_0427")
    dl = st.generate(st.param_schema()(L=240, W=170, H=70), resolve_material("flute_e"), profile)
    notes = _closed_width_notes(dl, ["top_tuck"], ["FRONT"])
    assert len(notes) == 1 and "253 mm" in notes[0] and "250 mm" in notes[0]
    rte = get_style("rte")
    plain = rte.generate(
        rte.param_schema()(L=80, W=50, H=180), resolve_material("sbs_350"), profile
    )
    assert _closed_width_notes(plain, ["front"], ["FRONT"]) == []  # 卡纸盒正面就是那块面


@pytest.mark.pro
def test_mailer_flats_stack_lid_over_front(mailer: Path, tmp_path: Path) -> None:
    """ISSUE-004：盖 + 插舌（合上后的顶面和正面）、背板 + 底、两个侧面各上下叠一张；盖 + 插舌这张导得进、量得准。"""
    from PIL import ImageDraw

    assert cli("plan", str(mailer))[0] == 0
    pairs = sorted(p.name for p in (mailer / "plan" / "pairs").glob("*.png"))
    assert pairs == ["back+base.png", "side_b+side_a.png", "top_lid+top_tuck.png"]
    main, back = tmp_path / "main.png", tmp_path / "back.png"
    Image.new("RGB", (600, 400), (200, 180, 150)).save(main)
    Image.new("RGB", (600, 400), (20, 80, 150)).save(back)
    assert (
        cli("concept", "import", str(mailer), str(main), "--name", "c1", "--view", "main")[0] == 0
    )
    assert cli("concept", "choose", str(mailer), "c1", "--by", "测试")[0] == 0
    code, out = cli("concept", "import", str(mailer), str(back), "--name", "c1-back1", "--view", "back",
                    "--faces", "base,back,side_a")  # fmt: skip
    assert code == 0, out
    code, out = cli("concept", "prompt", str(mailer), "--step", "flat")
    assert code == 0, out
    assert [p["faces"] for p in out["data"]["prompts"]] == [
        ["top_lid", "top_tuck"], ["back", "base"], ["side_b", "side_a"],
    ]  # fmt: skip
    texts = [Path(p["file"]).read_text(encoding="utf-8") for p in out["data"]["prompts"]]
    assert "CONNECTED" in texts[0] and "FRONT LID FLAP" in texts[0]
    assert "SEPARATE" in texts[2]  # 两个侧面各自成画
    s = 3  # 盖 253 × 173 在上、插舌 253 × 71.5 在下，铺满
    img = Image.new("RGB", (253 * s, 173 * s + round(71.5 * s)), (200, 210, 170))
    ImageDraw.Draw(img).line([(0, 173 * s), (253 * s, 173 * s)], fill=(80, 80, 80), width=2)
    flat = tmp_path / "flat.png"
    img.save(flat)
    code, out = cli("concept", "import", str(mailer), str(flat), "--name", "c1-flat-lid", "--view", "flat",
                    "--faces", "top_lid,top_tuck")  # fmt: skip
    assert code == 0, out
    code, out = cli("concept", "rectify", str(mailer), "c1-flat-lid")
    assert code == 0, out
    assert [f["face"] for f in out["data"]["faces"]] == ["top_lid", "top_tuck"]
    assert all(abs(f["aspect_error"]) < 0.05 for f in out["data"]["faces"])
    code, out = cli("concept", "import", str(mailer), str(flat), "--name", "c1-flat-bad", "--view", "flat",
                    "--faces", "top_lid,back")  # fmt: skip
    assert code == 2 and "分组" in out["errors"][0]["message"]  # 不在分组里的两面不收


@pytest.mark.pro
def test_main_prompt_brief_lists_only_faces_seen_in_the_main_view(mailer: Path) -> None:
    """抽屉测试 F-07：路线 A 的主视角提示词加 --brief，引擎只写正面、右侧、顶面那几段，不用手工挑面。"""
    code, out = cli("art", "add", str(mailer), "--kind", "placeholder", "--id", "ph-back", "--panel",
                    "back", "--box", "20,10,100,40", "--label", "背面", "--label-en", "Back area")  # fmt: skip
    assert code == 0, out
    assert cli("plan", str(mailer))[0] == 0
    code, out = cli("concept", "prompt", str(mailer), "--step", "main", "--design", "Cream, olive",
                    "--text", "LUMERA", "--brief", "--name", "main-A")  # fmt: skip
    assert code == 0, out
    text = Path(out["data"]["prompts"][0]["file"]).read_text(encoding="utf-8")
    assert "Layout draft" in text and "FRONT LID FLAP (" in text and "TOP (" in text
    assert "RIGHT SIDE (" in text and "BACK (" not in text and "Back area" not in text


@pytest.mark.pro
def test_flat_box_gets_a_mockup_view_matching_the_back_view_from_below(
    mailer: Path, tmp_path: Path
) -> None:
    """抽屉测试 F-23：扁平盒的背面视角是从底下看的（底、背、左），效果图另出一张同角度的和它并排。"""
    main, back = tmp_path / "main.png", tmp_path / "back.png"
    Image.new("RGB", (600, 400), (200, 180, 150)).save(main)
    Image.new("RGB", (600, 400), (20, 80, 150)).save(back)
    assert (
        cli("concept", "import", str(mailer), str(main), "--name", "c1", "--view", "main")[0] == 0
    )
    assert cli("concept", "choose", str(mailer), "c1", "--by", "测试")[0] == 0
    code, out = cli("concept", "import", str(mailer), str(back), "--name", "c1-back1", "--view", "back",
                    "--faces", "base,back,side_a")  # fmt: skip
    assert code == 0, out
    code, out = cli("mockup", str(mailer))
    assert code == 0, out
    assert (mailer / "mockup" / "view_below_back_left.png").is_file()


@pytest.mark.pro
def test_illustration_on_a_gradient_is_traced_without_the_gradient(
    mailer: Path, tmp_path: Path
) -> None:
    """抽屉测试 F-20：渐变底上的插画（可可果、月牙）单独描：--background gradient 按慢慢变的底色抠掉，只描插画。"""
    from PIL import ImageDraw

    w, h = 600, 400
    grad = np.linspace(0, 1, w)[None, :, None]
    a, b = np.array([235, 200, 150]), np.array([150, 90, 60])  # 橙到棕的渐变底
    img = Image.fromarray((a + (b - a) * grad).repeat(h, axis=0).astype(np.uint8), "RGB")
    ImageDraw.Draw(img).ellipse([380, 120, 500, 240], fill=(60, 120, 60))  # 一颗绿色的果子
    src = tmp_path / "fruit.png"
    img.save(src)
    code, out = cli(
        "visual", "import", str(mailer), str(src), "--role", "graphic", "--name", "fruit"
    )
    assert code == 0, out
    code, out = cli("visual", "trace", str(mailer), "fruit", "--background", "gradient")
    assert code == 0, out
    assert out["data"]["method"] == "vtracer"
    svg = (mailer / out["data"]["asset"]).read_text(encoding="utf-8")
    fills = {m.lower() for m in __import__("re").findall(r'fill="(#[0-9a-fA-F]{6})"', svg)}
    green = [f for f in fills if int(f[3:5], 16) > int(f[1:3], 16) + 30]  # 绿色占优的颜色
    assert green and len(fills) <= 4  # 描到了果子，渐变底没描成一道道色带
    code, out = cli(
        "visual", "trace", str(mailer), "fruit", "--background", "gradient", "--colors", "2"
    )
    assert code == 2  # 只用于全彩描


def test_flat_detail_captions_fit_their_column() -> None:
    """抽屉测试：窄的面（侧面）对照图三列标题太长时互相压着——缩字号，还放不下就截断。"""
    from pacxio.mockup.flat import _fit_caption

    text, font = _fit_caption(
        "外套 · 左侧面  AI 平面图（拉正）· main-C-front3-flat-side_b-side_a", 200
    )
    assert font.getlength(text) <= 200 and text.endswith("…")
    short, _ = _fit_caption("正式稿", 200)
    assert short == "正式稿"
