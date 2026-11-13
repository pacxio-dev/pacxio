"""出血 / 安全区 / 糊口禁墨区 / 工艺可用区（、9）。"""

from __future__ import annotations

import pytest
from shapely.geometry import Point

from pacxio.layout.panels import panel_map
from pacxio.layout.zones import compute_zones, zones_model
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Profile, load_profile, resolve_material
from pacxio.styles import get_style
from pacxio.styles.rte import RteParams


def build(style_id: str = "rte", **profile_changes: object) -> tuple[Dieline, Profile]:
    profile = load_profile("default")
    if "ink" in profile_changes:
        profile = profile.model_copy(deep=True)
        profile.glue_flap.ink = profile_changes["ink"]  # type: ignore[assignment]
    dl = get_style(style_id).generate(
        RteParams(L=80, W=50, H=180), resolve_material("sbs_350"), profile
    )
    return dl, profile


def test_bleed_is_outline_offset_by_bleed_mm() -> None:
    dl, profile = build()
    zg = compute_zones(dl, profile)
    assert zg.outline.within(zg.bleed)
    # 外轮廓的最左边 x=0，出血边界应在 x=−bleed
    assert zg.bleed.bounds[0] == pytest.approx(-profile.bleed_mm, abs=1e-3)
    assert zg.bleed.bounds[1] == pytest.approx(-profile.bleed_mm, abs=1e-3)
    assert zg.bleed.bounds[2] == pytest.approx(dl.bbox[2] + profile.bleed_mm, abs=1e-3)
    assert zg.bleed.bounds[3] == pytest.approx(dl.bbox[3] + profile.bleed_mm, abs=1e-3)
    # 出血边界上任一点距轮廓恰为 bleed
    for x, y in list(zg.bleed.exterior.coords)[::37]:
        assert zg.outline.distance(Point(x, y)) == pytest.approx(profile.bleed_mm, abs=1e-2)


@pytest.mark.parametrize("style_id", ["rte", "ste"])
def test_glue_bleed_only_allows_ink_only_near_hinge(style_id: str) -> None:
    dl, profile = build(style_id)
    zg = compute_zones(dl, profile)
    hinge_x = dl.panel("glue").polygon.bbox()[2]  # 糊口压线所在 x
    mid_y = dl.bbox[3] / 2
    into = profile.glue_flap.bleed_into_mm
    allowed = lambda x, y: zg.allowed.contains(Point(x, y))  # noqa: E731
    assert allowed(hinge_x - into / 2, mid_y)  # 糊口压线内侧 bleed_into 内：有墨
    assert not allowed(hinge_x - into - 0.5, mid_y)  # 超出 bleed_into：禁墨
    assert not allowed(-1.5, mid_y)  # 糊口外缘的出血带：禁墨
    assert allowed(hinge_x + 10, mid_y)  # back 面内：有墨


@pytest.mark.parametrize("style_id", ["rte", "ste", "snaplock", "autolock"])
def test_bleed_next_to_the_glue_flap_ends_belongs_to_the_neighbouring_panels(style_id: str) -> None:
    """糊口两端旁边的出血（背面下角、盖旁边）照常有墨；以前糊口外扩的圆角把它吃掉了（2026-10-22 修）。"""
    style = get_style(style_id)
    profile = load_profile("default")
    dl = style.generate(
        style.param_schema()(L=80, W=50, H=180), resolve_material("sbs_350"), profile
    )
    zg = compute_zones(dl, profile)
    bx0, by0, _, by1 = dl.panel("back").polygon.bbox()
    allowed = lambda x, y: zg.allowed.contains(Point(x, y))  # noqa: E731
    assert allowed(bx0 - 0.5, by0 - 0.5)  # 背面左下角外
    assert allowed(bx0 - 0.5, by1 + 0.5)  # 盖左边、糊口上端外
    assert not allowed(bx0 - 8, (by0 + by1) / 2)  # 糊口本身仍禁墨
    assert not allowed(-1.5, (by0 + by1) / 2)  # 糊口外沿自己的出血仍禁墨


def test_glue_ink_none_forbids_whole_flap_and_full_forbids_nothing() -> None:
    dl, p_none = build(ink="none")
    zg = compute_zones(dl, p_none)
    hinge_x = dl.panel("glue").polygon.bbox()[2]
    mid_y = dl.bbox[3] / 2
    assert not zg.allowed.contains(Point(hinge_x - 1, mid_y))
    assert zg.allowed.contains(Point(hinge_x + 1, mid_y))  # back 不受影响
    dl, p_full = build(ink="full")
    zg_full = compute_zones(dl, p_full)
    assert zg_full.no_ink.is_empty
    assert zg_full.allowed.contains(Point(-1.5, mid_y))


def test_safe_zone_insets_panels_by_margin_and_skips_glue() -> None:
    dl, profile = build()
    zg = compute_zones(dl, profile)
    assert "glue" not in zg.safe
    bx0, by0, bx1, by1 = dl.panel("back").polygon.bbox()
    sx0, sy0, sx1, sy1 = zg.safe["back"].bounds
    m = profile.safe_margin_mm
    assert (sx0 - bx0, sy0 - by0, bx1 - sx1, by1 - sy1) == pytest.approx((m, m, m, m), abs=1e-6)


def test_finish_ok_insets_by_crease_clearance() -> None:
    dl, profile = build()
    zg = compute_zones(dl, profile)
    bx0, _, bx1, _ = dl.panel("front").polygon.bbox()
    fx0, _, fx1, _ = zg.finish_ok["front"].bounds
    c = profile.finish_rules.crease_clearance_mm
    assert (fx0 - bx0, bx1 - fx1) == pytest.approx((c, c), abs=1e-6)


def test_zones_model_serializes() -> None:
    dl, profile = build()
    z = zones_model(compute_zones(dl, profile))
    assert z.bleed is not None and z.bleed.is_closed()
    assert set(z.safe) >= {"back", "front"} and z.no_ink
    assert z.model_dump_json()


def test_panel_map_planning_pixels_and_safe_boxes() -> None:
    dl, profile = build()
    m = panel_map(dl, profile)
    by_id = {p["id"]: p for p in m["panels"]}
    back = by_id["back"]
    assert back["px"] == {"w": 951, "h": 2132, "dpi": 300}  # 80.48/25.4×300, 180.48/25.4×300
    assert back["safe_box_local_mm"] == {"x": 3.0, "y": 3.0, "w": 74.48, "h": 174.48}
    assert "safe_box_local_mm" not in by_id["glue"]
    assert by_id["top_lid"]["orientation_provisional"] is False  # 方向经实物打样确认（）
    assert m["blank_with_bleed_px"]["w"] == round((dl.bbox[2] + 6) * 300 / 25.4)
