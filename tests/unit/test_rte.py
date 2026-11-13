"""RTE / STE：结构检查、尺寸量回、补偿、约束、序列化往返。"""

from __future__ import annotations

import pytest

from pacxio.export.svg import dieline_to_svg
from pacxio.geometry import Path
from pacxio.model.dieline import Dieline, EdgeType, PanelRole
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.preflight.rules import DIM_TOLERANCE_MM, measure_panel
from pacxio.styles import get_style
from pacxio.styles.rte import RteParams, resolve

SIZES = [(80, 50, 180), (60, 40, 100), (120, 30, 45)]  # 至少 3 组尺寸（）
MATERIALS = ["sbs_350", "sbs_400"]  # 至少 2 种材料（规格 M1 验收）
STYLES = ["rte", "ste"]


def make(
    style_id: str, L: float, W: float, H: float, material: str = "sbs_350", **kw: object
) -> tuple[Dieline, RteParams]:
    style = get_style(style_id)
    params = RteParams(L=L, W=W, H=H, **kw)  # type: ignore[arg-type]
    mat, profile = resolve_material(material), load_profile("default")
    assert style.validate(params, mat, profile) == []
    return style.generate(params, mat, profile), params


@pytest.mark.parametrize("style_id", STYLES)
@pytest.mark.parametrize("material", MATERIALS)
@pytest.mark.parametrize("L,W,H", SIZES)
def test_structure_checks_pass(style_id: str, material: str, L: float, W: float, H: float) -> None:
    dl, _ = make(style_id, L, W, H, material)
    assert check_s01(dl) == []
    assert check_crease_sides(dl) == []


@pytest.mark.parametrize("style_id", STYLES)
@pytest.mark.parametrize("material", MATERIALS)
@pytest.mark.parametrize("L,W,H", SIZES)
def test_dimensions_measured_back_from_geometry(
    style_id: str, material: str, L: float, W: float, H: float
) -> None:
    """S02：参数尺寸可从几何中量回。这里用与实现无关的手算公式（ 补偿式）。"""
    dl, p = make(style_id, L, W, H, material)
    mat = resolve_material(material)
    t = mat.caliper_mm
    k = load_profile("default").compensation["folding_carton"]
    wb, ws = L + k.k_panel * t, W + k.k_panel * t
    hb, ld = H + k.k_height * t, W + k.k_lid * t
    off = 0.5 * t  # dust_offset 默认
    expected = {
        "back": (wb, hb),
        "front": (wb, hb),
        "top_lid": (wb, ld),  # 插舌根部的小卡位归盖，但量结构尺寸时不算（Dieline.structural_bbox）
        "btm_lid": (wb, ld),
        "side_a": (ws, hb - 2 * off),
        "side_b": (ws, hb - 2 * off),
    }
    assert check_s02(dl, expected) == []
    assert measure_panel(dl, "glue")[0] == pytest.approx(p.glue_w, abs=DIM_TOLERANCE_MM)
    tl = min(0.2 * W + 6, 25.0, 0.5 * H)  # 插舌长默认（v2，按 reference 校准）
    _, _, bw, bh = dl.bbox
    assert bw == pytest.approx(p.glue_w + 2 * wb + 2 * ws)
    assert bh == pytest.approx(hb + 2 * (ld + tl))


def test_bottom_lid_hangs_on_front_for_rte_and_back_for_ste() -> None:
    rte, _ = make("rte", 80, 50, 180)
    ste, _ = make("ste", 80, 50, 180)
    assert rte.panel("btm_lid").hinge is not None
    assert rte.panel("btm_lid").hinge.parent_panel == "front"  # type: ignore[union-attr]
    assert ste.panel("btm_lid").hinge.parent_panel == "back"  # type: ignore[union-attr]
    # 底盖与同面的 x 范围一致
    for dl, parent in ((rte, "front"), (ste, "back")):
        px0, _, px1, _ = dl.panel(parent).polygon.bbox()
        lx0, _, lx1, _ = dl.panel("btm_lid").polygon.bbox()
        assert (lx0, lx1) == pytest.approx((px0, px1))
    # 同样尺寸下两者展开尺寸相同，轮廓不同
    assert rte.bbox == pytest.approx(ste.bbox)
    assert dieline_to_svg(rte) != dieline_to_svg(ste)


@pytest.mark.parametrize("style_id", STYLES)
def test_origin_is_bbox_lower_left(style_id: str) -> None:
    dl, _ = make(style_id, 80, 50, 180)
    outline = Path([s for e in dl.edges_of(EdgeType.CUT) for s in e.segments], closed=True)
    x0, y0, x1, y1 = outline.bbox()
    assert (x0, y0) == pytest.approx((0, 0), abs=1e-9)
    assert (x1, y1) == pytest.approx(dl.bbox[2:], abs=1e-9)


def test_outer_basis_subtracts_two_calipers() -> None:
    mat = resolve_material("sbs_350")
    dl, _ = make("rte", 80, 50, 180, dim_basis="outer")
    t = mat.caliper_mm
    # 内尺寸 = 外尺寸 − 2t，面尺寸 = 内尺寸 + k·t（k=1）
    assert measure_panel(dl, "back") == pytest.approx((80 - 2 * t + t, 180 - 2 * t + t))


@pytest.mark.parametrize("style_id", STYLES)
def test_tuck_has_arcs_and_friction_lock(style_id: str) -> None:
    """v2：插舌两个 R5 圆角；防尘翼顶角各一个圆角；friction_lock = 防尘翼卡口 + 插舌压线两端切口
    （2026-11-07 起默认；tuck_slit="crease" 是老做法：插舌根部离压线 2 的短压线）。"""
    dl, _ = make(style_id, 80, 50, 180)
    segs = [s for e in dl.edges_of(EdgeType.CUT) for s in e.segments]
    arcs = [s for s in segs if s.is_arc]
    assert len(arcs) == 8
    tuck_arcs = [s for s in arcs if s.arc_info()[1] == pytest.approx(5.0)]  # min(5, tuck_len/2)
    assert len(tuck_arcs) == 4
    slits = [e for e in dl.edges if "slit" in e.id]
    assert sorted(e.id for e in slits) == ["cut_btm_tuck_slit_l", "cut_btm_tuck_slit_r",
                                           "cut_top_tuck_slit_l", "cut_top_tuck_slit_r"]  # fmt: skip
    assert all(e.type == EdgeType.CUT and e.panels[-1].endswith("_tuck") for e in slits)
    # 钩形切口：离插舌压线 1.5、从插舌边往里，末端拐下来碰到压线；压线只在两个钩之间，小卡位归盖
    (hinge,) = [e for e in dl.edges if e.id == "crease_top_lid_tuck"]
    y = hinge.segments[0].start.y
    for e in slits:
        if not e.id.startswith("cut_top"):
            continue
        a, b = e.segments
        t = dl.caliper_mm  # 钩高 = 一层纸厚
        assert a.start.y == pytest.approx(y + t) and a.end.y == pytest.approx(y + t)
        assert abs(a.end.x - a.start.x) == pytest.approx(8.0)  # 照 reference：长 8
        assert b.end.y == pytest.approx(y) and b.start.x == pytest.approx(b.end.x)
        assert (
            min(abs(b.end.x - q.x) for q in (hinge.segments[0].start, hinge.segments[0].end)) < 1e-9
        )
    legacy_dl = make(style_id, 80, 50, 180, tuck_slit="crease")[0]
    assert measure_panel(dl, "top_lid") == measure_panel(legacy_dl, "top_lid")  # 结构尺寸不变
    _, ly0, _, ly1 = dl.panel("top_lid").polygon.bbox()  # 面本身多了伸过压线一层纸厚的小卡位
    _, gy0, _, gy1 = legacy_dl.panel("top_lid").polygon.bbox()
    assert ly1 - ly0 == pytest.approx(gy1 - gy0 + dl.caliper_mm)
    legacy = legacy_dl
    assert sorted(e.id for e in legacy.edges if "slit" in e.id) == [
        "crease_btm_tuck_slit_l",
        "crease_btm_tuck_slit_r",
        "crease_top_tuck_slit_l",
        "crease_top_tuck_slit_r",
    ]
    assert legacy.outline_segments() == dl.outline_segments()  # 只差压线处那几条线
    dl2, _ = make(style_id, 80, 50, 180, friction_lock=False)
    assert len([s for e in dl2.edges_of(EdgeType.CUT) for s in e.segments]) < len(segs)
    assert not [e for e in dl2.edges if "slit" in e.id]
    old, _ = make(
        style_id, 80, 50, 180, friction_lock_depth=2, friction_lock_pos=3, dust_taper_deg=20
    )
    assert old.outline_segments() == dl.outline_segments()  # v1 的参数还能读，不再起作用




@pytest.mark.parametrize("verified", [True, False])
def test_panel_roles_and_hinges(monkeypatch: pytest.MonkeyPatch, verified: bool) -> None:
    """盖、插舌的方向：打样确认过（）就不标待定；没确认的盒型标待定、元数据写未确认。"""
    if not verified:
        from pacxio.styles import rte

        monkeypatch.setattr(rte, "ORIENTATION_VERIFIED", frozenset())
    dl, _ = make("rte", 80, 50, 180)
    assert {p.id for p in dl.panels} == {
        "glue", "back", "side_a", "front", "side_b", "top_lid", "btm_lid",
        "top_tuck", "btm_tuck", "dust_ta", "dust_tb", "dust_ba", "dust_bb",
    }  # fmt: skip
    for p in dl.panels:
        if p.role in (PanelRole.LID, PanelRole.TUCK):
            assert p.orientation_provisional is not verified
        elif p.role == PanelRole.BODY:
            assert not p.orientation_provisional
    assert dl.panel("top_tuck").hinge is not None
    assert dl.style_meta["orientation_verified"] is verified


@pytest.mark.parametrize("style_id", STYLES)
def test_compensation_on_by_default_and_can_be_disabled(style_id: str) -> None:
    mat = resolve_material("sbs_350")
    t = mat.caliper_mm
    on, _ = make(style_id, 80, 50, 180)
    assert measure_panel(on, "back") == pytest.approx((80 + t, 180 + t))
    assert on.style_meta["compensation_applied"] is True
    off, _ = make(style_id, 80, 50, 180, apply_compensation=False)
    assert measure_panel(off, "back") == pytest.approx((80, 180))
    assert off.style_meta["compensation_applied"] is False


def test_compensation_scales_with_caliper() -> None:
    thin, _ = make("rte", 80, 50, 180, "sbs_300")
    thick, _ = make("rte", 80, 50, 180, "sbs_400")
    dt = resolve_material("sbs_400").caliper_mm - resolve_material("sbs_300").caliper_mm
    assert measure_panel(thick, "back")[0] - measure_panel(thin, "back")[0] == pytest.approx(dt)


def test_compensation_coefficients_come_from_profile() -> None:
    mat = resolve_material("sbs_350")
    profile = load_profile("default")
    doubled = profile.model_copy(deep=True)
    doubled.compensation["folding_carton"].k_panel = 2.0
    params = RteParams(L=80, W=50, H=180)
    d1, d2 = resolve(params, mat, profile), resolve(params, mat, doubled)
    assert d2.wb - d1.wb == pytest.approx(mat.caliper_mm)


@pytest.mark.parametrize("style_id", STYLES)
@pytest.mark.parametrize(
    "kw",
    [
        {"L": 15, "W": 50, "H": 180},  # 低于 MVP 下限
        {"L": 80, "W": 50, "H": 180, "glue_w": 25},  # 超出 10–20
        {"L": 80, "W": 20, "H": 180, "glue_w": 19},  # > Wi − 2
        {"L": 80, "W": 50, "H": 40, "tuck_len": 45},  # tuck_len ≥ Hi 且超范围
        {"L": 40, "W": 50, "H": 180, "dust_depth": 30},  # > 0.5Li − 1
    ],
)
def test_constraints_reject_with_actionable_fix(style_id: str, kw: dict[str, float]) -> None:
    style = get_style(style_id)
    issues = style.validate(RteParams(**kw), resolve_material("sbs_350"), load_profile("default"))
    assert issues and {i.id for i in issues} == {"S03"}
    assert all(i.fix for i in issues)


def test_corrugated_material_rejected() -> None:
    style = get_style("ste")
    issues = style.validate(
        RteParams(L=80, W=50, H=180), resolve_material("flute_b"), load_profile("default")
    )
    assert issues and "STE" in issues[0].message


@pytest.mark.parametrize("style_id", STYLES)
def test_dieline_json_roundtrip_and_determinism(style_id: str) -> None:
    dl, _ = make(style_id, 80, 50, 180)
    again = Dieline.model_validate_json(dl.model_dump_json())
    assert dieline_to_svg(again) == dieline_to_svg(dl)
    dl2, _ = make(style_id, 80, 50, 180)
    assert dieline_to_svg(dl2) == dieline_to_svg(dl)


@pytest.mark.parametrize("style_id", STYLES)
def test_every_panel_hangs_on_the_right_parent(style_id: str) -> None:
    """面的挂接关系（供 3D 折叠与糊口处理使用）：back 是根，其余每个面都有唯一的父面。"""
    dl, _ = make(style_id, 80, 50, 180)
    lid_parent = {"top_lid": "back", "btm_lid": "front" if style_id == "rte" else "back"}
    expected = {
        "glue": "back", "side_a": "back", "front": "side_a", "side_b": "front",
        "top_tuck": "top_lid", "btm_tuck": "btm_lid",
        "dust_ta": "side_a", "dust_ba": "side_a", "dust_tb": "side_b", "dust_bb": "side_b",
        **lid_parent,
    }  # fmt: skip
    assert dl.panel("back").hinge is None
    for pid, parent in expected.items():
        h = dl.panel(pid).hinge
        assert h is not None, pid
        assert h.parent_panel == parent, pid
        # hinge 指向的压线确实连着这两个面
        edge = next(e for e in dl.edges if e.id == h.edge_id)
        assert set(edge.panels) == {pid, parent}, pid


@pytest.mark.parametrize("style_id", ["rte", "ste"])
def test_top_lid_reads_from_the_opening_side(style_id: str) -> None:
    """顶盖挂在背面上沿、插舌从正面打开：盖上的图文按开盒顺手方向——从正面看是正的，
    所以面局部 +y 在展开图里朝下（指向铰链），面局部的上边在铰链（背面）一侧。"""
    from pacxio.geometry import Vec

    dl, _ = make(style_id, 80, 50, 180)
    lid, back = dl.panel("top_lid"), dl.panel("back")
    up = lid.frame.apply_vector(Vec(0, 1))
    assert (round(up.x, 6), round(up.y, 6)) == (0, -1)
    x0, y0, x1, y1 = lid.polygon.bbox()
    top_left_local = lid.frame.apply(Vec(0, y1 - y0))  # 面局部左上角 → 展开图
    assert abs(top_left_local.y - back.polygon.bbox()[3]) < 1e-6  # 落在铰链（背面上沿）上
    assert abs(top_left_local.x - x1) < 1e-6
    inv = lid.frame.inverse()
    back_to_local = inv.apply(lid.frame.apply(Vec(12.5, 7.0)))
    assert abs(back_to_local.x - 12.5) < 1e-9 and abs(back_to_local.y - 7.0) < 1e-9
    # 其它面不转
    for pid in ("front", "back", "side_a", "side_b", "btm_lid"):
        v = dl.panel(pid).frame.apply_vector(Vec(0, 1))
        assert (round(v.x, 6), round(v.y, 6)) == (0, 1), pid
