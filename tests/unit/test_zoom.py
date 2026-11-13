"""看清小字：`pacxio zoom` 取确认图的局部（ ）。只用开源包，图和对照是手写的。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio import zoom as zm
from pacxio.cli import app
from pacxio.cloud.client import CloudError

runner = CliRunner()
URL = "http://cloud.test"


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """400 x 200 px 的确认图，1 px = 1 mm；正面在 (10,20)–(110,120) mm。"""
    monkeypatch.setenv("PACXIO_CLOUD", "0")
    d = tmp_path / "j"
    d.mkdir()
    im = Image.new("RGB", (400, 200), "white")
    for x in range(400):
        im.putpixel((x, 100), (x % 256, 0, 0))  # 每列颜色不同，用来核对裁的位置
    im.save(d / "preview.png")
    im.save(d / "preview.base.png")
    m = {"space": "blank", "dpi": 25.4, "size_px": [400, 200], "x0_mm": 0.0, "y1_mm": 200.0,
         "base_file": "preview.base.png",
         "panels": [{"id": "front", "bbox": [10.0, 20.0, 110.0, 120.0]}]}  # fmt: skip
    (d / "preview.map.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_local_panel_crop_adds_margin(job: Path) -> None:
    r = zm.zoom(job, panel="front")
    assert r.ok and r.data["source"] == "local"
    out = Path(r.artifacts[0])
    assert out == job / "studio" / "zoom" / "preview-front.png"
    with Image.open(out) as im:
        assert im.size == (104, 104)  # 面 100 mm 四周各多 2 mm
        assert im.getpixel((0, 22)) == (8, 0, 0)  # 第 100 行在局部的 100-78 行，第一列是原图第 8 列


def test_local_box_crop(job: Path) -> None:
    r = zm.zoom(job, box=[0.5, 0.0, 0.75, 1.0])
    assert r.ok and r.data["box"] == [0.5, 0.0, 0.75, 1.0]
    with Image.open(r.artifacts[0]) as im:
        assert im.size == (100, 200)
        assert im.getpixel((0, 100)) == (200, 0, 0)


def test_layer_image_uses_the_confirm_image_map(job: Path) -> None:
    r = zm.zoom(job, panel="front", image="preview.base.png")
    assert r.ok
    assert Path(r.artifacts[0]).name == "preview.base-front.png"


@pytest.mark.parametrize(
    ("kwargs", "issue"),
    [
        ({}, "ARG"),
        ({"panel": "front", "box": [0, 0, 1, 1]}, "ARG"),
        ({"box": [0.6, 0.0, 0.4, 1.0]}, "ARG"),
        ({"panel": "lid"}, "ZOOM"),
        ({"panel": "front", "image": "other.png"}, "ZOOM"),
    ],
)
def test_errors(job: Path, kwargs: dict[str, Any], issue: str) -> None:
    r = zm.zoom(job, **kwargs)
    assert not r.ok and r.errors[0].id == issue and r.errors[0].fix
    assert not (job / "studio").exists()


def test_cloud_needs_a_cloud_render(job: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACXIO_CLOUD", "1")
    monkeypatch.setenv("PACXIO_CLOUD_URL", URL)
    r = zm.zoom(job, panel="front")
    assert not r.ok and r.errors[0].id == "ZOOM" and "render" in r.errors[0].fix


def cloud_job(job: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACXIO_CLOUD", "1")
    monkeypatch.setenv("PACXIO_CLOUD_URL", URL)
    monkeypatch.setenv("PACXIO_CLOUD_TOKEN", "t")
    (job / ".cloud").mkdir()
    (job / ".cloud" / "state.json").write_text(json.dumps({"url": URL, "job": "abc"}))


def test_cloud_crop_comes_from_the_server(job: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cloud_job(job, monkeypatch)
    calls: list[tuple[str, str, tuple[float, ...]]] = []
    buf = io.BytesIO()
    Image.new("RGB", (7, 5), "blue").save(buf, format="PNG")

    def crop(self: Any, jid: str, rel: str, box: tuple[float, ...]) -> bytes:
        calls.append((jid, rel, box))
        return buf.getvalue()

    monkeypatch.setattr(zm.Client, "crop", crop)
    r = zm.zoom(job, box=[0.1, 0.2, 0.3, 0.4])
    assert r.ok and r.data["source"] == "cloud"
    assert calls == [("abc", "preview.png", (0.1, 0.2, 0.3, 0.4))]
    assert png(Path(r.artifacts[0]).read_bytes()).size == (7, 5)


@pytest.mark.parametrize(("issue", "code"), [("CLOUD_NET", 3), ("CLOUD_LIMIT", 1)])
def test_cloud_errors_pass_through(job: Path, monkeypatch: pytest.MonkeyPatch, issue: str, code: int) -> None:
    cloud_job(job, monkeypatch)

    def crop(self: Any, jid: str, rel: str, box: tuple[float, ...]) -> bytes:
        raise CloudError(issue, "nope", "try later")

    monkeypatch.setattr(zm.Client, "crop", crop)
    r = zm.zoom(job, panel="front")
    assert not r.ok and r.errors[0].id == issue and r.exit_code == code


def test_cli_json(job: Path) -> None:
    res = runner.invoke(app, ["zoom", str(job), "--box", "0,0,0.5,0.5", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["ok"] and out["command"] == "zoom" and len(out["artifacts"]) == 1
    res = runner.invoke(app, ["zoom", str(job), "--json"])
    assert res.exit_code == 2 and json.loads(res.output)["errors"][0]["id"] == "ARG"
