"""结构配方的逐面对照：0427 的外露插舌、旋转侧面和非标准面名不能遗漏。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from pacxio import dielines
from pacxio.cli import app
from pacxio.concept.rectify import face_rects
from pacxio.concept.service import import_concept
from pacxio.geometry import Vec
from pacxio.mockup import flat as flat_module
from pacxio.model.job import Box, Job, Placement

pytestmark = pytest.mark.pro
MAILER_FACES = {"top_tuck", "side_b", "back", "side_a", "top_lid", "base"}
QUADRANTS = ((230, 70, 45), (40, 120, 220), (40, 170, 100), (190, 80, 190))


@pytest.fixture(autouse=True)
def local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACXIO_CLOUD", "0")


def cli(*args: str) -> dict[str, Any]:
    result = CliRunner().invoke(app, [*args, "--json"])
    assert result.exit_code == 0, result.output or result.exception
    return json.loads(result.output)


def init(directory: Path, *selector: str) -> None:
    cli(
        "job",
        "init",
        str(directory),
        *selector,
        "--L",
        "240",
        "--W",
        "170",
        "--H",
        "70",
        "--material",
        "flute_b",
    )


def add_reference(directory: Path, face: str, *, artwork: bool = False) -> tuple[float, float]:
    """在面局部坐标中画四个不同颜色的象限，独立检查输出是否旋转 / 镜像错位。"""
    saved = Job.load(directory)
    dl, errors = dielines.build_dieline_for(saved)
    assert dl is not None, errors
    rect = face_rects(dl)[face]
    inverse = dl.panel(face).frame.inverse()
    corners = [inverse.apply(Vec(x, y)) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    x0, y0 = min(p.x for p in corners), min(p.y for p in corners)
    w, h = max(p.x for p in corners) - x0, max(p.y for p in corners) - y0
    width, height = round(w * 3), round(h * 3)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for i, color in enumerate(QUADRANTS):
        x, y = (i % 2) * width // 2, (i // 2) * height // 2
        draw.rectangle((x, y, x + width // 2, y + height // 2), fill=color)
    source = directory / f"reference-{face}.png"
    image.save(source)
    imported = import_concept(
        directory, source, name=f"reference-{face}", view="flat", faces=[face]
    )
    assert imported.ok, imported.errors
    assert not imported.data.get("redo"), imported.data
    if artwork:
        assets = directory / "assets"
        assets.mkdir(exist_ok=True)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        for i, color in enumerate(QUADRANTS):
            hex_color = "#" + "".join(f"{c:02x}" for c in color)
            svg += (
                f'<rect x="{(i % 2) * 50}" y="{(i // 2) * 50}" '
                f'width="50" height="50" fill="{hex_color}"/>'
            )
        (assets / "quadrants.svg").write_text(svg + "</svg>")
        saved.placements.append(
            Placement(
                id=f"quadrants-{face}",
                kind="vector",
                panel=face,
                source="assets/quadrants.svg",
                box=Box(x=x0, y=y0, w=w, h=h),
                fit="stretch",
            )
        )
        saved.save(directory)
    return w, h


@pytest.mark.parametrize("selector", [("--fefco", "0427"), ("--style", "fefco_0427")])
def test_mailer_compares_all_six_visible_faces_upright(
    tmp_path: Path, selector: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "mailer"
    init(directory, *selector)
    assert Job.load(directory).style == ("structure" if selector[0] == "--fefco" else "fefco_0427")
    sizes = {face: add_reference(directory, face, artwork=True) for face in sorted(MAILER_FACES)}
    pairs: list[Image.Image] = []
    build_sheet = flat_module.build_flat_sheet

    def inspect_pair(*args: Any, **kwargs: Any) -> Image.Image:
        pair = args[4]
        assert pair is not None
        pairs.append(pair.copy())
        return build_sheet(*args, **kwargs)

    monkeypatch.setattr(flat_module, "build_flat_sheet", inspect_pair)
    output = cli("flat", str(directory), "--diff", "测试四象限颜色及方向")
    assert {row["face"] for row in output["data"]["flats"]} == MAILER_FACES
    assert output["data"]["flats_to_redo"] == []
    manifest = json.loads((directory / "flat" / "manifest.json").read_text())
    assert manifest["flats"] == output["data"]["flats"]
    assert manifest["faces_without_flat"] == []
    assert manifest["diffs"] == ["测试四象限颜色及方向"]
    assert {
        p.stem.removeprefix("vs_flat_") for p in (directory / "flat").glob("vs_flat_*.png")
    } == MAILER_FACES
    # 主视角栏也必须拿外露插舌，不应拿未印图案的 front 前板或夹带中间整片刀版。
    (pair,) = pairs
    scale = 150 / 25.4
    front_w, front_h = sizes["top_tuck"]
    right_w, right_h = sizes["side_b"]
    assert pair.width == pytest.approx((front_w + right_w) * scale, abs=3)
    assert pair.height == pytest.approx(max(front_h, right_h) * scale, abs=2)
    for left, width, height in ((0, front_w, front_h), (front_w, right_w, right_h)):
        for i, expected in enumerate(QUADRANTS):
            x = round((left + (0.25 + (i % 2) * 0.5) * width) * scale)
            y = round(pair.height - height * scale + (0.25 + (i // 2) * 0.5) * height * scale)
            actual = pair.getpixel((x, y))
            assert max(abs(a - b) for a, b in zip(actual, expected, strict=True)) < 25
    for face, (w, h) in sizes.items():
        with Image.open(directory / "flat" / f"vs_flat_{face}.png") as detail:
            detail.load()
            # 三列依次为参考、正式稿、50% 叠图；每毫米约 4px，标题及边距之外不应拉伸。
            assert detail.width == pytest.approx(3 * w * 4 + 80, abs=12)
            assert detail.height == pytest.approx(h * 4 + 60, abs=6)
            tile_w, tile_h = (detail.width - 80) // 3, detail.height - 60
            for column in range(3):
                for i, expected in enumerate(QUADRANTS):
                    x = 20 + column * (tile_w + 20) + round((0.25 + (i % 2) * 0.5) * tile_w)
                    y = 40 + round((0.25 + (i // 2) * 0.5) * tile_h)
                    actual = detail.convert("RGB").getpixel((x, y))
                    assert max(abs(a - b) for a, b in zip(actual, expected, strict=True)) < 25, (
                        face,
                        column,
                        i,
                        actual,
                    )
    for name in ("flat.png", "00_flat_sheet.png"):
        with Image.open(directory / "flat" / name) as image:
            image.load()
            assert min(image.size) > 100


def test_native_structure_preserves_missing_faces_and_diff_notes(tmp_path: Path) -> None:
    directory = tmp_path / "hexagon"
    init(directory, "--fefco", "0730")
    assert Job.load(directory).style == "structure"
    empty = cli("flat", str(directory))
    assert empty["data"]["flats"] == []
    assert (directory / "flat" / "00_flat_sheet.png").is_file()
    add_reference(directory, "wall_0")
    one = cli("flat", str(directory), "--diff", "其余五面待补参考图")
    assert [row["face"] for row in one["data"]["flats"]] == ["wall_0"]
    manifest = json.loads((directory / "flat" / "manifest.json").read_text())
    assert set(manifest["faces_without_flat"]) == {f"wall_{i}" for i in range(1, 6)}
    for i in range(1, 6):
        add_reference(directory, f"wall_{i}")
    complete = cli("flat", str(directory))
    assert {row["face"] for row in complete["data"]["flats"]} == {f"wall_{i}" for i in range(6)}
    assert complete["data"]["diffs"] == ["其余五面待补参考图"]
    manifest = json.loads((directory / "flat" / "manifest.json").read_text())
    assert manifest["faces_without_flat"] == []
    assert len(list((directory / "flat").glob("vs_flat_*.png"))) == 6
