"""S06 折叠检查（）：实物折过的盒型合上后没有穿插，故意改坏的几何要报出来。"""

from __future__ import annotations

from typing import Any

import pytest

from pacxio.mockup.foldcheck import fold_clashes
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style

PROFILE = load_profile("default")


def parts(style_id: str, material: str, **values: Any) -> dict[str | None, Dieline]:
    style = get_style(style_id)
    params, m = style.param_schema()(**values), resolve_material(material)
    assert not style.validate(params, m, PROFILE)
    multipart = getattr(style, "generate_parts", None)
    if multipart:
        return dict(multipart(params, m, PROFILE))
    return {None: style.generate(params, m, PROFILE)}


@pytest.mark.parametrize(
    ("style_id", "material", "dims"),
    [
        ("rte", "sbs_350", (80, 50, 120)),
        ("ste", "sbs_400", (100, 60, 180)),
        ("snaplock", "sbs_350", (80, 50, 120)),
        ("autolock", "sbs_350", (100, 60, 180)),
        ("fefco_0201", "flute_b", (300, 200, 200)),
        ("fefco_0427", "flute_e", (240, 170, 70)),  #  实物折样的那一组
        ("tray_lid", "sbs_350", (200, 150, 60)),
        ("drawer", "sbs_350", (150, 100, 40)),
    ],
)
def test_physically_folded_styles_close_without_clashes(
    style_id: str, material: str, dims: tuple[int, int, int]
) -> None:
    for part, dl in parts(style_id, material, L=dims[0], W=dims[1], H=dims[2]).items():
        assert fold_clashes(dl) == [], (style_id, part)


def test_dust_flaps_without_crease_offset_collide_with_the_lid() -> None:
    """防尘翼压线不比盖低（dust_offset = 0）时，两层纸整层叠在一起。"""
    (dl,) = parts("rte", "sbs_350", L=80, W=50, H=120, dust_offset=0).values()
    clashes = fold_clashes(dl)
    pairs = {(c.a, c.b) for c in clashes}
    assert pairs == {
        ("dust_ta", "top_lid"),
        ("dust_tb", "top_lid"),
        ("btm_lid", "dust_ba"),
        ("btm_lid", "dust_bb"),
    }
    assert all(c.parallel and c.overlap_mm == pytest.approx(dl.caliper_mm) for c in clashes)


def test_rim_sandwich_must_hold_the_ears() -> None:
    """飞机盒卷边夹层（卷边宽 − 2t）放不下一层耳朵时报出来，放宽卷边后就没有了。"""
    t = resolve_material("flute_e").caliper_mm
    (narrow,) = parts("mailer_tuck", "flute_e", L=240, W=170, H=70, roll_gap=2 * t + 0.5).values()
    clashes = fold_clashes(narrow)
    assert {(c.a, c.b) for c in clashes} == {
        ("ear_fa", "side_a_in"),
        ("ear_ba", "side_a_in"),
        ("ear_fb", "side_b_in"),
        ("ear_bb", "side_b_in"),
    }
    assert all(c.overlap_mm == pytest.approx(t - 0.5, abs=0.01) for c in clashes)
    (wide,) = parts("mailer_tuck", "flute_e", L=240, W=170, H=70, roll_gap=3 * t + 0.5).values()
    assert fold_clashes(wide) == []
