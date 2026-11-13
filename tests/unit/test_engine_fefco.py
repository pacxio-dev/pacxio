"""几何引擎第 1 步：瓦楞开槽箱积木（engine/slotted.py）、片材（engine/sheet.py）和 FEFCO 编号（engine/fefco.py）。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from pacxio.engine import fefco
from pacxio.engine.slotted import SlottedAssembled, SlottedAssembledParams
from pacxio.engine.style import fold_deviation
from pacxio.mockup.folding import plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style

PROFILE = load_profile("default")
FLUTE = resolve_material("flute_b")
STYLE = get_style("structure")


def make(code: str, size: tuple[float, float, float] = (400, 300, 250)) -> tuple[Any, Dieline]:
    p = STYLE.param_schema()(**fefco.recipe(code, *size))
    assert not STYLE.validate(p, FLUTE, PROFILE), STYLE.validate(p, FLUTE, PROFILE)
    return p, STYLE.generate(p, FLUTE, PROFILE)


def targets(dl: Dieline) -> list[tuple[str, tuple[float, float], tuple[float, float, float]]]:
    return [
        (pid, (x, y), (X, Y, Z)) for pid, x, y, X, Y, Z in dl.style_meta["structure"]["targets"]
    ]


@pytest.mark.parametrize("code", [*fefco.SHEETS, *fefco.STYLES])
def test_fefco_codes_generate_and_fold(code: str) -> None:
    """已写成配方的 FEFCO 款：生成、过刀版检查、按自带折法折好后落在该在的地方、反查出的编号就是这一款。"""
    p, dl = make(code)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, FLUTE, PROFILE))
    info = dl.style_meta["structure"]
    assert plan_for(dl) is not None
    assert info["fold_deviation_mm"] < 1e-6
    assert (info.get("fefco") or info.get("calibrated_from", "").removeprefix("fefco_")) == code


def test_every_01_and_02_code_is_listed() -> None:
    assert len(fefco.SERIES_01) == 11 and len(fefco.SERIES_02) == 36
    assert set(fefco.SHEETS) == set(fefco.SERIES_01)
    assert set(fefco.SERIES_02) <= set(fefco.STYLES)  # 02 全部 36 款都有配方
    with pytest.raises(ValueError):
        fefco.recipe("0999", 1, 1, 1)


def _segments(dl: Dieline) -> dict[str, Any]:
    def norm(ss: Any) -> list[tuple[float, ...]]:
        out = []
        for s in ss:
            a, b = (
                (round(s.start.x, 6), round(s.start.y, 6)),
                (round(s.end.x, 6), round(s.end.y, 6)),
            )
            out.append((*min(a, b), *max(a, b), round(abs(s.bulge), 6)))
        return sorted(out)

    out: dict[str, Any] = {
        f"panel:{p.id}": (p.role.value, norm(p.polygon.segments)) for p in dl.panels
    }
    out |= {f"edge:{e.id}": (e.type.value, norm(e.segments)) for e in dl.edges}
    return out


@pytest.mark.parametrize(("style_id", "top", "bottom", "extra"), [
    ("fefco_0201", {"kind": "flaps"}, {"kind": "flaps"}, {}),
    ("fefco_0200", {"kind": "open"}, {"kind": "flaps"}, {}),
    ("fefco_0202", {"kind": "flaps", "flap": "overlap"}, {"kind": "flaps", "flap": "overlap"}, {"flap_gap": 0}),
    ("fefco_0203", {"kind": "flaps", "flap": "full"}, {"kind": "flaps", "flap": "full"}, {"flap_gap": 0}),
])  # fmt: skip
def test_slotted_blocks_reproduce_calibrated_boxes(
    style_id: str, top: dict[str, Any], bottom: dict[str, Any], extra: dict[str, Any]
) -> None:
    """瓦楞积木拼出的 0200–0203 和校准过的现有入口逐面、逐线一样（不比走向和次序）。"""
    values = {"L": 400, "W": 300, "H": 250}
    new, _ = SlottedAssembled().generate(
        SlottedAssembledParams(**values, **extra, top_end=top, bottom_end=bottom), FLUTE, PROFILE
    )
    old_style = get_style(style_id)
    old = old_style.generate(old_style.param_schema()(**values), FLUTE, PROFILE)
    assert _segments(new) == _segments(old)


def test_fold_check_catches_wrong_folds() -> None:
    for code, pid, keys in (("0210", "top_lid", [[6, -90.0]]), ("0229", "btm_back_drop", [[3, 0.0]]),
                            ("0214", "top_front", [[6, 90.0]]), ("0119", "panel_5", [[1, 90.0]])):  # fmt: skip
        _, dl = make(code, (400, 300, 120) if code.startswith("01") else (400, 300, 250))
        broken = copy.deepcopy(dl)
        broken.style_meta["fold_plan"]["moves"][pid]["keys"] = keys
        assert fold_deviation(broken, targets(dl), layers=False) > 5, (code, pid)


def test_slotted_blocks_refuse_what_does_not_fit() -> None:
    def issues(top: dict[str, Any], material: str = "flute_b") -> list[str]:
        p = STYLE.param_schema()(body={"base": "rect", "L": 400, "W": 300, "H": 250}, top=top,
                                 bottom={"kind": "flaps"})  # fmt: skip
        return [i.message for i in STYLE.validate(p, resolve_material(material), PROFILE)]

    assert any("窄边" in m for m in issues({"kind": "flaps", "flap": "rim", "depth": 160}))
    assert not issues(
        {"kind": "flaps", "flap": "center"}, "sbs_350"
    )  # 结构不分材料（§20）：卡纸也能用
    sheet = STYLE.param_schema()(body={"form": "sheet", "L": 400, "W": 500, "panels": [100, 300]})
    assert [i.id for i in STYLE.validate(sheet, FLUTE, PROFILE)] == ["S03"]  # 每条宽加起来对不上 W


def test_flap_options_look_like_the_catalogue() -> None:
    """摇盖选项（0225 / 0227 / 0218）：侧翼和带翼那片之间是压线、拇指缺口是半圆、锁舌对面那片有两道斜缝。"""
    _, d25 = make("0225")
    ids = {p.id for p in d25.panels}
    assert {"btm_back", "btm_front", "btm_wing_a", "btm_wing_b"} <= ids
    assert not ids & {"btm_side_a", "btm_side_b"}  # 底部端墙不带摇盖
    creases = {e.id: e.panels for e in d25.edges if e.type.value == "crease"}
    assert creases["crease_btm_front_wing_a"] == ["btm_front", "btm_wing_a"]
    cut = next(e for e in d25.edges if e.id == "cut_btm_wing_a")
    assert cut.panels == ["btm_wing_a", "side_a"]  # 翼根和端墙下沿之间切开
    assert any(abs(abs(s.bulge) - 1) < 1e-9 for s in d25.panel("btm_back").polygon.segments)
    wing = d25.panel("btm_wing_a").polygon.bbox()
    assert wing[2] - wing[0] == pytest.approx(150)  # ½W

    _, d27 = make("0227")
    side, front = d27.panel("top_side_a").polygon.bbox(), d27.panel("top_front").polygon.bbox()
    w = d27.panel("top_wing_a").polygon.bbox()
    assert side[3] - side[1] == pytest.approx(
        front[3] - front[1] - (w[2] - w[0])
    )  # 端摇盖浅一个翼宽
    assert any(abs(abs(s.bulge) - 1) < 1e-9 for s in d27.panel("top_front").polygon.segments)

    _, d18 = make("0218")
    slits = [e for e in d18.edges if e.id.startswith("cut_top_slit")]
    assert [e.panels for e in slits] == [["top_back"], ["top_back"]]
    back = d18.panel("top_back").polygon.bbox()
    for e in slits:  # 斜缝在那片摇盖里面
        for s in e.segments:
            assert back[0] < s.start.x < back[2] and back[1] < s.start.y < back[3]
    tab = d18.panel("top_front").polygon.bbox()
    assert tab[3] - tab[1] < back[3] - back[1]  # 锁舌那片比全叠摇盖浅


def test_fold_check_catches_wings_folded_out() -> None:
    for code, pid in (("0225", "btm_wing_a"), ("0227", "top_wing_b")):
        _, dl = make(code)
        broken = copy.deepcopy(dl)
        keys = broken.style_meta["fold_plan"]["moves"][pid]["keys"]
        broken.style_meta["fold_plan"]["moves"][pid]["keys"] = [[k[0], -k[1]] for k in keys]
        assert fold_deviation(broken, targets(dl), layers=False) > 5, (code, pid)


def test_flap_options_are_refused_where_they_do_not_fit() -> None:
    def issues(top: dict[str, Any], material: str = "flute_b") -> list[str]:
        p = STYLE.param_schema()(body={"base": "rect", "L": 400, "W": 300, "H": 250}, top=top,
                                 bottom={"kind": "flaps"})  # fmt: skip
        return [i.message for i in STYLE.validate(p, resolve_material(material), PROFILE)]

    assert any("正面" in m for m in issues({"kind": "flaps", "flap": "full", "on": 0, "wing": 30}))
    assert any("full" in m for m in issues({"kind": "flaps", "lock": True}))
    assert any("太宽" in m for m in issues({"kind": "flaps", "flap": "full", "wing": 200}))
    assert any("rim" in m or "half / overlap / full" in m
               for m in issues({"kind": "flaps", "flap": "rim", "thumb": 0}))  # fmt: skip
    # 结构不分材料（§20）：卡纸也走开槽箱拼装器，照样按摇盖规则检查（锁舌要全叠摇盖）
    assert any("full" in m for m in issues({"kind": "flaps", "lock": True}, "sbs_350"))
    with pytest.raises(ValueError):
        STYLE.param_schema()(
            body={"base": "rect", "L": 1, "W": 1, "H": 1}, top={"kind": "open", "lock": True}
        )
    # 带选项的全叠摇盖不会被当成校准过的 0203
    p, dl = make("0218")
    assert "calibrated_from" not in dl.style_meta["structure"]


def test_joint_in_the_middle_of_a_wall() -> None:
    """0230 / 0231：接头开在墙中间——没有糊口，那面墙分两截（v + 搭接、墙长 − v − 纸厚），两截搭着。"""
    for code, wall, length in (("0230", "back", 400), ("0231", "side_b", 300)):
        p, dl = make(code)
        ids = [q.id for q in dl.panels if q.role.value == "body"]
        assert (
            ids[0] == f"{wall}_joint"
            and ids[-1] == wall
            and "glue" not in {q.id for q in dl.panels}
        )
        v = p.seam.offset
        assert v == pytest.approx(0.25 * length)
        joint, head = dl.panel(f"{wall}_joint").polygon.bbox(), dl.panel(wall).polygon.bbox()
        dims = dl.style_meta["dims"]
        assert joint[2] - joint[0] == pytest.approx(v + dims["joint_w"])
        full = dims["panel_back_front"] if wall == "back" else dims["panel_side"]
        assert head[2] - head[0] == pytest.approx(full - v - FLUTE.caliper_mm)
        tube = dl.style_meta["structure"]["tube"]
        assert tube["pieces"][f"{wall}_joint"] == [wall, pytest.approx(full - v - dims["joint_w"])]
        broken = copy.deepcopy(tube)  # 那截放错位置，自检能看出来
        broken["pieces"][f"{wall}_joint"][1] = 0.0
        from pacxio.engine.targets import tube_targets

        assert fold_deviation(dl, tube_targets(dl, broken), layers=False) > 5


def test_joint_offset_is_refused_where_it_does_not_fit() -> None:
    def issues(
        seam: dict[str, Any], top: dict[str, Any] | None = None, material: str = "flute_b"
    ) -> list[str]:
        p = STYLE.param_schema()(body={"base": "rect", "L": 400, "W": 300, "H": 250}, seam=seam,
                                 top=top or {"kind": "flaps"}, bottom={"kind": "flaps"})  # fmt: skip
        return [i.message for i in STYLE.validate(p, resolve_material(material), PROFILE)]

    assert any("放不下" in m for m in issues({"offset": 390}))
    assert any("只能是开口或普通摇盖" in m for m in issues({"offset": 100}, {"kind": "lid_tuck"}))
    assert (
        issues({"offset": 100}, None, "sbs_350") == []
    )  # 结构不分材料（§20）：卡纸也能把接头开在墙中间
    assert issues({"offset": 100, "wall": "short"}) == []


def test_partition_and_gusset_mechanisms() -> None:
    """0228：接头那截外面接一块隔板、糊口在隔板头上；0226：小三角折页挂在中间三角上（沿斜压线翻）。"""
    _, d28 = make("0228")
    assert d28.panel("glue").hinge.parent_panel == "partition"
    assert d28.panel("back_joint").hinge.parent_panel == "partition"
    tube = d28.style_meta["structure"]["tube"]
    assert tube["partition"]["panel"] == "partition"
    part = d28.panel("partition").polygon.bbox()
    assert part[2] - part[0] == pytest.approx(
        d28.style_meta["dims"]["panel_side"] - FLUTE.caliper_mm
    )

    _, d26 = make("0226")
    for gid in ("top_side_a_gusset_l", "btm_side_b_gusset_r"):
        assert d26.panel(gid).hinge.parent_panel == gid.rsplit("_gusset", 1)[0]
    assert not any(q.id.startswith("cut_") and q.id != "cut_outline" for q in d26.edges)  # 不开槽
    broken = copy.deepcopy(d26)
    broken.style_meta["fold_plan"]["moves"]["top_side_a_gusset_l"]["keys"] = [[6, 0.0]]
    assert fold_deviation(broken, targets(d26), layers=False) > 5


@pytest.mark.parametrize("kind", ["handle", "hang_tab", "hang_mid", "display"])
def test_top_only_blocks_are_refused_at_the_bottom(kind: str) -> None:
    p = STYLE.param_schema()(body={"base": "rect", "L": 400, "W": 300, "H": 250}, top={"kind": "flaps"},
                             bottom={"kind": kind})  # fmt: skip
    assert any("只能在顶部" in i.message for i in STYLE.validate(p, FLUTE, PROFILE))


def test_display_and_hanger_tops_stand_up() -> None:
    """0222 提手、0241 / 0242 挂板、0219 展示板：折好后在盒顶上面立着（高过盒身）。"""
    for code, pid in (("0222", "handle_back"), ("0241", "hanger_side_a"), ("0242", "hanger_a"),
                      ("0219", "top_back")):  # fmt: skip
        _, dl = make(code)
        hb = dl.style_meta["dims"]["body_height"]
        zs = [Z for q, _, _, _, _, Z in dl.style_meta["structure"]["targets"] if q == pid]
        assert max(zs) > hb + 20, (code, pid)
    _, d41 = make("0241")
    assert {e.id for e in d41.edges} >= {
        "cut_hang_hanger_side_a",
        "cut_hang_hanger_side_b",
        "cut_slit_top_front",
    }


def test_divider_flaps_reach_into_the_box() -> None:
    """0207 / 0208：隔板折进箱子里（立体上在盒身高度之内）。"""
    for code, pid in (("0207", "btm_side_a_wall"), ("0207", "btm_back_wall"), ("0208", "top_back_drop"),
                      ("0208", "btm_side_b_r_drop")):  # fmt: skip
        _, dl = make(code)
        hb = dl.style_meta["dims"]["body_height"]
        zs = [Z for q, _, _, _, _, Z in dl.style_meta["structure"]["targets"] if q == pid]
        assert -1e-6 <= min(zs) and max(zs) <= hb + 1e-6 and max(zs) - min(zs) > 20, (code, pid)


def test_self_locking_bottoms_have_the_catalogue_cuts() -> None:
    _, d32 = make("0232")
    assert sum(e.id.startswith("cut_btm_back_slit") for e in d32.edges) == 2
    _, d33 = make("0233")
    assert any(e.id == "cut_btm_front_slot" for e in d33.edges)
    inner = [e for e in d33.edges if e.type.value == "crease" and len(e.panels) == 1]
    assert len(inner) == 4  # 两片长摇盖各两条斜压线
    _, d331 = make("0233.1")
    back = d331.panel("btm_back").polygon.bbox()
    assert back[3] - back[1] == pytest.approx(0.65 * 300)
