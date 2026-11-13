"""按折叠几何的静态效果图（mockup/raster.py）：深度缓冲遮挡正确、挖空透过去、效果图流程和工作台用新画法。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.mockup.fold import BoxModel, Face
from pacxio.mockup.materials import presets
from pacxio.mockup.raster import STATIC_RENDERER, expected_static_renderer, render_folded
from pacxio.mockup.render import View, _background

runner = CliRunner()
FRONT = View("front", 0.0, 0.0, "正对")


def square(key: str, y: float, size: float = 100.0) -> Face:
    """竖在 y 处、朝 −y（朝相机）的正方形纸。"""
    return Face(
        key,
        key,
        key,
        (0.0, -1.0, 0.0),
        (0, 0, size, size),
        ((0.0, y, size), (size, y, size), (size, y, 0.0), (0.0, y, 0.0)),
    )


def solid(rgb: tuple[int, int, int], alpha: Image.Image | None = None) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (*rgb, 255))
    if alpha is not None:
        img.putalpha(alpha)
    return img


def centre(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img, dtype=np.int32)
    h, w = arr.shape[:2]
    return arr[h // 2 - 2 : h // 2 + 2, w // 2 - 2 : w // 2 + 2].reshape(-1, 3).mean(axis=0)


def test_nearer_paper_wins_whatever_the_drawing_order() -> None:
    near, far = square("near", 0.0), square("far", 50.0)
    textures = {"near": solid((200, 30, 30)), "far": solid((30, 30, 200))}
    for faces in ([near, far], [far, near]):
        box = BoxModel(x=100, y=50, z=100, faces=faces)
        rgb = centre(render_folded(box, textures, FRONT, (200, 200)))
        assert rgb[0] > 120 and rgb[2] < 80  # 红的那张在前面


def test_transparent_cutouts_show_what_is_behind() -> None:
    hole = Image.new("L", (64, 64), 255)
    hole.paste(0, (16, 16, 48, 48))  # 正中挖一个洞
    box = BoxModel(x=100, y=50, z=100, faces=[square("near", 0.0), square("far", 50.0)])
    textures = {"near": solid((200, 30, 30), hole), "far": solid((30, 30, 200))}
    rgb = centre(render_folded(box, textures, FRONT, (200, 200)))
    assert rgb[2] > 120 and rgb[0] < 80  # 透过洞看到后面蓝的那张


def test_single_part_styles_render_folded_and_multipart_keep_the_box() -> None:
    assert expected_static_renderer("rte", False) == STATIC_RENDERER
    assert expected_static_renderer("crashlock_rsc", False) == STATIC_RENDERER
    assert expected_static_renderer("tray_lid", True) == "box"


@pytest.mark.pro
def test_mockup_records_the_static_renderer_and_old_manifests_go_stale(tmp_path: Path) -> None:
    job = tmp_path / "box"
    init = ["job", "init", str(job), "--L", "80", "--W", "50", "--H", "120", "--json"]
    assert runner.invoke(app, init).exit_code == 0
    assert runner.invoke(app, ["mockup", str(job), "--json"]).exit_code == 0
    manifest_path = job / "mockup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["static_renderer"] == STATIC_RENDERER
    for name in manifest["views"]:
        assert Image.open(job / f"mockup/view_{name}.png").size == (1200, 1200)

    from pacxio.model.job import Job
    from pacxio.studio.state import _mockup

    loaded = Job.load(job)
    fp = manifest["fingerprint"]
    assert not _mockup(job, loaded, fp).cards[0].stale
    del manifest["static_renderer"]  # 2026-11-04 以前的效果图：方盒子画的
    manifest_path.write_text(json.dumps(manifest))
    assert _mockup(job, loaded, fp).cards[0].stale


def test_gloss_film_reflects_the_studio_strip_and_paper_does_not() -> None:
    """亮膜（粗糙度低）照到影棚灯板的地方往白色提亮、亮带有边；纸（粗糙度高）和没给粗糙度时不反光（2026-11-11 Tidewell）。"""
    cfg = presets()
    reflect = cfg["static_reflection"]
    face = square("a", 0.0)
    box = BoxModel(x=100.0, y=1.0, z=100.0, faces=[face])
    view = View("front_right", 35.0, 24.0, "右前")
    tex = {"a": solid((40, 40, 44))}
    plain = np.asarray(render_folded(box, tex, view, (240, 240)), dtype=np.float32)
    paper = Image.new("L", (64, 64), cfg["paper"][0])
    film = Image.new("L", (64, 64), cfg["gloss_lamination"][0])
    on_paper = np.asarray(
        render_folded(box, tex, view, (240, 240), {"a": paper}, reflect), dtype=np.float32
    )
    on_film = np.asarray(
        render_folded(box, tex, view, (240, 240), {"a": film}, reflect), dtype=np.float32
    )
    assert np.abs(on_paper - plain).max() < 1  # 纸：不反光
    lift = (on_film - plain).sum(axis=2)
    assert lift.max() > 60  # 亮膜：照到灯板的地方明显提亮
    bg = np.asarray(_background((plain.shape[1], plain.shape[0])), dtype=np.float32)
    on_face = np.abs(plain - bg).sum(axis=2) > 30
    assert (
        lift[on_face] < 0.3 * lift.max()
    ).any()  # 有一部分没照到灯板：是一条亮带，不是整面一样亮
