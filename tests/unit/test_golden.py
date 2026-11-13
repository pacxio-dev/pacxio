"""黄金快照：每个盒型至少 3 组尺寸的 SVG（）。

快照变化必须人工确认：先看 diff 与预览图，再用 PACXIO_UPDATE_GOLDEN=1 重新生成。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pacxio.export.svg import dieline_to_svg
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style
from pacxio.styles.rte import RteParams

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
CASES = [(80, 50, 180), (60, 40, 100), (120, 30, 45)]


@pytest.mark.parametrize("style_id", ["rte", "ste"])
@pytest.mark.parametrize("L,W,H", CASES)
def test_svg_snapshot(style_id: str, L: int, W: int, H: int) -> None:
    dl = get_style(style_id).generate(
        RteParams(L=L, W=W, H=H), resolve_material("sbs_350"), load_profile("default")
    )
    svg = dieline_to_svg(dl)
    path = GOLDEN / f"{style_id}_{L}x{W}x{H}.svg"
    if os.environ.get("PACXIO_UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(svg, encoding="utf-8")
        if not os.environ.get("PACXIO_UPDATE_GOLDEN"):
            pytest.fail(f"快照 {path.name} 不存在，已生成；请人工确认后提交并重跑")
    assert svg == path.read_text(encoding="utf-8"), f"{path.name} 与快照不一致，需人工确认"


LOCK_CASES = {
    "snaplock": [(80, 50, 180), (60, 40, 100), (120, 60, 45)],
    "autolock": [(80, 50, 180), (100, 100, 120), (150, 60, 80)],
}


@pytest.mark.parametrize(
    "style_id,L,W,H", [(s, *c) for s, cases in LOCK_CASES.items() for c in cases]
)
def test_lock_bottom_svg_snapshot(style_id: str, L: int, W: int, H: int) -> None:
    style = get_style(style_id)
    dl = style.generate(
        style.param_schema()(L=L, W=W, H=H), resolve_material("sbs_350"), load_profile("default")
    )
    svg = dieline_to_svg(dl)
    path = GOLDEN / f"{style_id}_{L}x{W}x{H}.svg"
    if os.environ.get("PACXIO_UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(svg, encoding="utf-8")
        if not os.environ.get("PACXIO_UPDATE_GOLDEN"):
            pytest.fail(f"快照 {path.name} 不存在，已生成；请人工确认后提交并重跑")
    assert svg == path.read_text(encoding="utf-8"), f"{path.name} 与快照不一致，需人工确认"


def test_window_svg_snapshot() -> None:
    """开窗（）：rte 80×50×180，正面贴膜圆角矩形窗 + 左侧面敞口圆窗。"""
    from pacxio.dielines import build_dieline_for
    from pacxio.model.job import Box, Job, Window

    job = Job(
        job_id="golden", style="rte", params={"L": 80, "W": 50, "H": 180},
        material=resolve_material("sbs_350"),
        windows=[
            Window(id="w1", panel="front", box=Box(x=20.24, y=50, w=40, h=100), corner_r=3),
            Window(id="w2", panel="side_a", box=Box(x=15.24, y=110, w=20, h=20), shape="circle", film="none"),
        ],
    )  # fmt: skip
    dl, issues = build_dieline_for(job)
    assert dl is not None, issues
    svg = dieline_to_svg(dl)
    path = GOLDEN / "rte_80x50x180_window.svg"
    if os.environ.get("PACXIO_UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(svg, encoding="utf-8")
        if not os.environ.get("PACXIO_UPDATE_GOLDEN"):
            pytest.fail(f"快照 {path.name} 不存在，已生成；请人工确认后提交并重跑")
    assert svg == path.read_text(encoding="utf-8"), f"{path.name} 与快照不一致，需人工确认"


CORRUGATED_CASES = [
    ("fefco_0427", dims, mat) for dims in ((150, 100, 40), (220, 160, 60), (300, 250, 100))
    for mat in ("flute_e", "flute_b")
] + [
    ("fefco_0201", dims, mat) for dims in ((300, 200, 200), (400, 300, 250), (250, 250, 250), (600, 400, 400))
    for mat in ("flute_b", "flute_c")
] + [("fefco_0201", (600, 400, 400), "flute_bc")]  # fmt: skip


@pytest.mark.parametrize("style_id,dims,mat", CORRUGATED_CASES)
def test_corrugated_svg_snapshot(style_id: str, dims: tuple[int, int, int], mat: str) -> None:
    """瓦楞盒型（）：飞机盒 3 组 × E / B，开槽箱 4 组 × B / C + 1 组 BC。"""
    style = get_style(style_id)
    L, W, H = dims
    dl = style.generate(
        style.param_schema()(L=L, W=W, H=H), resolve_material(mat), load_profile("default")
    )
    svg = dieline_to_svg(dl)
    path = GOLDEN / f"{style_id}_{L}x{W}x{H}_{mat}.svg"
    if os.environ.get("PACXIO_UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(svg, encoding="utf-8")
        if not os.environ.get("PACXIO_UPDATE_GOLDEN"):
            pytest.fail(f"快照 {path.name} 不存在，已生成；请人工确认后提交并重跑")
    assert svg == path.read_text(encoding="utf-8"), f"{path.name} 与快照不一致，需人工确认"


MULTIPART_CASES = [
    ("tray_lid", dims, mat, kw) for dims, kw in (((200, 150, 60), {}), ((300, 200, 100), {}), ((120, 120, 50), {}),
                                                  ((300, 200, 100), {"lid_h": 30}))
    for mat in ("sbs_350", "flute_e")
] + [("tray_lid", (200, 150, 60), "sbs_400", {}), ("tray_lid", (300, 200, 100), "flute_b", {})] + [
    ("drawer", dims, mat, {}) for dims in ((100, 70, 20), (150, 100, 40), (80, 160, 30), (200, 200, 60))
    for mat in ("sbs_350", "sbs_400")
]  # fmt: skip


@pytest.mark.parametrize("style_id,dims,mat,kw", MULTIPART_CASES)
def test_multipart_svg_snapshot(
    style_id: str, dims: tuple[int, int, int], mat: str, kw: dict
) -> None:  # type: ignore[type-arg]
    """多部件盒型（）：每个部件一张快照。"""
    style = get_style(style_id)
    L, W, H = dims
    parts = style.generate_parts(  # type: ignore[attr-defined]
        style.param_schema()(L=L, W=W, H=H, **kw), resolve_material(mat), load_profile("default")
    )
    suffix = "".join(f"_{k}{v}" for k, v in kw.items())
    for part, dl in parts.items():
        svg = dieline_to_svg(dl)
        path = GOLDEN / f"{style_id}_{L}x{W}x{H}_{mat}{suffix}_{part}.svg"
        if os.environ.get("PACXIO_UPDATE_GOLDEN") == "1" or not path.exists():
            path.write_text(svg, encoding="utf-8")
            if not os.environ.get("PACXIO_UPDATE_GOLDEN"):
                pytest.fail(f"快照 {path.name} 不存在，已生成；请人工确认后提交并重跑")
        assert svg == path.read_text(encoding="utf-8"), f"{path.name} 与快照不一致，需人工确认"
