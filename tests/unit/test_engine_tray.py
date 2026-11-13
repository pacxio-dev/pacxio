"""几何引擎第 2 步：托盘（engine/tray.py）。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from pacxio.engine import fefco
from pacxio.engine.style import FOLD_TOL_MM, fold_deviation
from pacxio.mockup.folding import plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style

PROFILE = load_profile("default")
FLUTE = resolve_material("flute_b")
STYLE = get_style("structure")


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_c", "flute_bc"])
@pytest.mark.parametrize("size", [(200, 150, 40), (300, 200, 60), (400, 300, 100)])
def test_pair_double_walls_with_inner_ears(material: str, size: tuple[int, int, int]) -> None:
    from pacxio.mockup.fold import build_box
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    p = STYLE.param_schema()(**fefco.recipe("0424", *size))
    assert not STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    info = dl.style_meta["structure"]
    assert info["fefco"] == "0424"
    assert info["fold_deviation_mm"] < 1e-6
    assert info["lug_alignment"]["status"] == "pass"
    assert info["inner_clearance"]["status"] == "pass"
    assert not fold_clashes(dl)
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_")]) == 6
    assert all(e.panels[0].endswith("_in") for e in dl.edges if "_in_ear_" in e.id)
    box = build_box(dl)
    assert (box.x, box.y) == pytest.approx(
        (dl.style_meta["dims"]["base_w"], dl.style_meta["dims"]["base_l"])
    )


def test_inner_ears_reject_overlap_and_detect_wrong_fold() -> None:
    p = STYLE.param_schema()(**fefco.recipe("0424", 400, 300, 160))
    assert any("耳片太深" in e.message for e in STYLE.validate(p, FLUTE, PROFILE))
    _, dl = make("0424", (300, 200, 60))
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["ear_fa"]["keys"] = [[4, 90.0]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_c", "flute_bc"])
@pytest.mark.parametrize("size", [(200, 150, 40), (300, 200, 60), (400, 300, 100)])
def test_0421_and_0428_differ_only_in_inner_wall_foot(
    material: str, size: tuple[int, int, int]
) -> None:
    """0421 / 0428：双层端墙 + 耳朵、盖挂后墙（长边）、不带防尘翼；0421 内墙插脚插进底板插孔，
    0428 内墙下沿一条折边往盒里平压在底板上，底板不开孔（FEFCO 第 51 / 54 页）。"""
    mat = resolve_material(material)
    out: dict[str, Dieline] = {}
    for code in ("0421", "0428"):
        p = STYLE.param_schema()(**fefco.recipe(code, *size))
        assert not STYLE.validate(p, mat, PROFILE)
        dl = out[code] = STYLE.generate(p, mat, PROFILE)
        assert not check_s01(dl) + check_crease_sides(dl)
        assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
        info = dl.style_meta["structure"]
        assert info["fefco"] == code
        assert info["fold_deviation_mm"] < 1e-6
        assert next(e for e in dl.edges if e.id == "crease_back_lid").panels == ["back", "lid"]
        assert not any(q.id.startswith("dust_") for q in dl.panels)
    slots = {c: [e for e in out[c].edges if e.id.startswith("cut_slot_")] for c in out}
    assert len(slots["0421"]) == 4 and not slots["0428"]
    feet = [q.id for q in out["0428"].panels if q.id.startswith("foot_")]
    assert feet == ["foot_a", "foot_b"] and not any(
        q.id.startswith("foot_") for q in out["0421"].panels
    )
    pts: dict[str, list[tuple[float, float, float]]] = {}
    for pid, _, _, X, Y, Z in out["0428"].style_meta["structure"]["targets"]:
        pts.setdefault(pid, []).append((X, Y, Z))
    base_l = out["0428"].style_meta["dims"]["base_l"]
    for pid in feet:  # 折边平躺（同一高度），伸向盒子中间
        assert len({round(z, 6) for _, _, z in pts[pid]}) == 1
        xs = [x for x, _, _ in pts[pid]]
        assert all(0 < x < base_l / 2 for x in xs) or all(base_l / 2 < x < base_l for x in xs)


def test_inner_wall_foot_detects_wrong_fold_and_refuses_bad_combos() -> None:
    _, dl = make("0428")
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["foot_a"]["keys"] = [[3, 90.0]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5
    spec = fefco.recipe("0428", 300, 200, 80)
    for body in ({"lugs": True}, {"corners": "flap"}, {"double_walls": "all"}):
        with pytest.raises(ValueError):
            STYLE.param_schema()(**{**spec, "body": spec["body"] | body})
    deep = STYLE.param_schema()(**{**spec, "tuning": {"foot_depth": 200.0}})
    assert any("折边" in i.message for i in STYLE.validate(deep, FLUTE, PROFILE))
    no_foot = STYLE.param_schema()(**{**spec, "body": spec["body"] | {"inner_foot": False}})
    assert STYLE.generate(no_foot, FLUTE, PROFILE).style_meta["structure"]["fefco"] is None


def test_long_double_walls_refuse_a_lid() -> None:
    """盖挂在端墙上（双层长边墙 + 连盖）不是目录款，机构也没按它做：明确拒绝。"""
    spec = fefco.recipe("0421", 300, 200, 80)
    p = STYLE.param_schema()(**{**spec, "body": spec["body"] | {"corner_on": "long"}})
    assert any("暂不带盖" in i.message for i in STYLE.validate(p, FLUTE, PROFILE))


@pytest.mark.parametrize("code", ["0423", "0455"])
def test_long_double_walls_oriented_to_product_dimensions(code: str) -> None:
    from pacxio.mockup.fold import build_box

    _, dl = make(code, (400, 300, 100))
    box = build_box(dl)
    assert (box.x, box.y) == pytest.approx(
        (dl.style_meta["dims"]["base_w"], dl.style_meta["dims"]["base_l"])
    )


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_c", "flute_bc"])
@pytest.mark.parametrize("size", [(200, 150, 100), (300, 200, 80), (400, 300, 100)])
def test_full_overlap_folder(material: str, size: tuple[int, int, int]) -> None:
    from pacxio.mockup.foldcheck import fold_clashes
    from pacxio.mockup.folding import apply, build_part, panel_matrices

    mat = resolve_material(material)
    p = STYLE.param_schema()(**fefco.recipe("0409", *size))
    assert not STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    assert dl.style_meta["structure"]["fefco"] == "0409"
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["joint"] == "none"
    assert not dl.glue_areas
    assert not fold_clashes(dl)
    a, b = (dl.panel(pid).polygon.bbox() for pid in ("side_b_joint", "side_b"))
    assert a[2] - a[0] == pytest.approx(b[2] - b[0])
    assert dl.style_meta["structure"]["tube"]["pieces"] == {
        "side_b_joint": ["side_b", 0.0],
        "side_b": ["side_b", 0.0],
    }
    plan = plan_for(dl)
    assert plan is not None
    matrices = panel_matrices(build_part(dl, plan), plan.steps)

    def centre(pid: str) -> tuple[float, float, float]:
        x0, y0, x1, y1 = dl.panel(pid).polygon.bbox()
        return apply(matrices[pid], (x0 + x1) / 2, (y0 + y1) / 2)

    assert abs(centre("side_b_joint")[0] - centre("side_a")[0]) - mat.caliper_mm == pytest.approx(
        size[1]
    )
    assert abs(centre("front")[2] - centre("back")[2]) - mat.caliper_mm == pytest.approx(size[2])


def test_full_overlap_detects_bad_fold_and_wrong_joint() -> None:
    p, dl = make("0409")
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["side_b"]["keys"] = [[2, 0.0]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5
    spec = fefco.recipe("0409", 300, 200, 80)
    spec["seam"]["wall"] = "long"
    other = STYLE.param_schema()(**spec)
    assert STYLE.generate(other, FLUTE, PROFILE).style_meta["structure"]["fefco"] is None
    for invalid in ({"kind": "glue", "full_overlap": True},
                    {"kind": "none", "full_overlap": True, "center_open": True},
                    {"kind": "none", "full_overlap": True, "width": 20}):  # fmt: skip
        with pytest.raises(ValueError):
            STYLE.param_schema()(**{**spec, "seam": invalid})


def make(code: str, size: tuple[float, float, float] = (300, 200, 80)) -> tuple[Any, Dieline]:
    p = STYLE.param_schema()(**fefco.recipe(code, *size))
    assert not STYLE.validate(p, FLUTE, PROFILE), STYLE.validate(p, FLUTE, PROFILE)
    return p, STYLE.generate(p, FLUTE, PROFILE)


def targets(dl: Dieline) -> list[tuple[str, tuple[float, float], tuple[float, float, float]]]:
    return [
        (pid, (x, y), (X, Y, Z)) for pid, x, y, X, Y, Z in dl.style_meta["structure"]["targets"]
    ]


@pytest.mark.parametrize("code", list(fefco.TRAYS))
def test_fefco_trays_generate_and_fold(code: str) -> None:
    p, dl = make(code, (300, 300, 80) if code == "0476" else (300, 200, 80))  # 正八角要 L = W
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, FLUTE, PROFILE))
    info = dl.style_meta["structure"]
    assert plan_for(dl) is not None
    assert info["fold_deviation_mm"] <= FOLD_TOL_MM
    assert info["fefco"] == code


def test_tray_stands_up_with_walls_around_the_base() -> None:
    """立体目标：底板在 Z = 0、墙立到墙高；正面墙在 Y = 0，side_a 在 X = 0（站在正面看的左边）。"""
    _, dl = make("0452")
    info, dims = dl.style_meta["structure"], dl.style_meta["dims"]
    pts: dict[str, list[tuple[float, float, float]]] = {}
    for pid, _, _, X, Y, Z in info["targets"]:
        pts.setdefault(pid, []).append((X, Y, Z))
    assert {round(z, 6) for _, _, z in pts["base"]} == {0.0}
    assert max(z for _, _, z in pts["front"]) == pytest.approx(dims["wall_h"])
    assert {round(y, 6) for _, y, _ in pts["front"]} == {0.0}
    assert {round(x, 6) for x, _, _ in pts["side_a"]} == {0.0}


def test_fold_check_catches_wrong_corner_folds() -> None:
    for code, pid, keys in (("0452", "corner_front_a", [[2, -90.0]]), ("0449", "gusset_back_b_end", [[2, 0.0]]),
                            ("0453", "side_a", [[1, 60.0]])):  # fmt: skip
        _, dl = make(code)
        broken = copy.deepcopy(dl)
        broken.style_meta["fold_plan"]["moves"][pid]["keys"] = keys
        assert fold_deviation(broken, targets(dl), layers=False) > 5, (code, pid)


def test_tray_corners_refuse_what_does_not_fit() -> None:
    def issues(body: dict[str, Any]) -> list[str]:
        p = STYLE.param_schema()(
            body={"form": "tray", "L": 300, "W": 200, "H": 80} | body, top={"kind": "open"}
        )
        return [i.message for i in STYLE.validate(p, FLUTE, PROFILE)]

    assert any("角翼深" in m for m in issues({"corners": "flap", "corner_depth": 160}))
    assert any("连角" in m for m in issues({"corners": "gusset", "corner_on": "end", "H": 120}))
    assert issues({"corners": "open"}) == []
    carton = STYLE.param_schema()(
        body={"form": "tray", "L": 120, "W": 80, "H": 40}, top={"kind": "open"}
    )
    dl = STYLE.generate(carton, resolve_material("sbs_350"), PROFILE)
    assert (
        dl.style_meta["structure"]["calibrated_from"] == "tray_glued"
    )  # 彩盒糊角托盘仍走校准过的做法


@pytest.mark.parametrize("code", list(fefco.SLEEVES))
def test_sleeves_are_open_tubes_along_the_right_axis(code: str) -> None:
    """0501–0503：四面筒上下开口；筒长是 H / W / L（body.axis），面宽按另外两个尺寸。"""
    p = STYLE.param_schema()(**fefco.recipe(code, 300, 200, 100))
    dl = STYLE.generate(p, FLUTE, PROFILE)
    info = dl.style_meta["structure"]
    assert info["fefco"] == code and info["fold_deviation_mm"] < 1e-6
    assert not [q for q in dl.panels if q.role.value != "body" and q.id != "glue"]  # 两头不带摇盖
    tube_len = {"0501": 100, "0502": 200, "0503": 300}[code]
    assert dl.style_meta["dims"]["Hi"] == pytest.approx(tube_len)


def test_axis_works_on_carton_tubes_and_is_refused_elsewhere() -> None:
    """结构不分材料（§20）：横放筒身卡纸也走开槽箱拼装器（照样按它的尺寸规则查）；多边形筒身不行。"""
    p = STYLE.param_schema()(body={"L": 120, "W": 80, "H": 50, "axis": "W"}, top={"kind": "open"},
                             bottom={"kind": "open"})  # fmt: skip
    assert not STYLE.validate(p, resolve_material("sbs_350"), PROFILE)
    poly = STYLE.param_schema()(body={"base": "regular", "sides": 6, "edge": 40, "H": 120, "axis": "W"},
                                top={"kind": "open"}, bottom={"kind": "open"})  # fmt: skip
    assert any(
        "body.axis" in i.message for i in STYLE.validate(poly, resolve_material("sbs_350"), PROFILE)
    )


@pytest.mark.parametrize("code", list(fefco.SETS))
def test_fefco_sets_fit_and_fold(code: str) -> None:
    """03 套盖：每件都过检查、折得回去；套在外面的件内尺寸 = 被套件外尺寸 + 间隙；反查编号对。"""
    from pacxio.engine.sets import SetParams, check_fit, generate_set, part_params, validate_set

    sp = SetParams(**fefco.set_recipe(code, 400, 300, 150))
    assert not validate_set(sp, FLUTE, PROFILE, STYLE)
    parts = generate_set(sp, FLUTE, PROFILE, STYLE)
    pps = part_params(sp, FLUTE, PROFILE, STYLE)
    for name, dl in parts.items():
        assert not check_s01(dl) + check_crease_sides(dl), name
        assert not check_s02(dl, STYLE.expected_panel_sizes(pps[name], FLUTE, PROFILE)), name
        assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6, name
        assert dl.style_meta["set"]["part"] == name
    assert not check_fit(sp, parts)
    assert fefco.set_code(sp) == code


def test_lid_follows_the_base_size() -> None:
    """改地盒尺寸，天盖跟着变：天盖内长 = 地盒外长 + 间隙。"""
    from pacxio.engine.sets import SetParams, generate_set, inner_of, outer_of

    for L in (300, 420):
        parts = generate_set(
            SetParams(**fefco.set_recipe("0300", L, 200, 100)), FLUTE, PROFILE, STYLE
        )
        assert inner_of(parts["lid"])[0] == pytest.approx(outer_of(parts["base"])[0] + 3.0)
    from pacxio.engine.sets import problems

    cyclic = SetParams(**{**fefco.set_recipe("0300", 300, 200, 100),
                          "fits": {"lid": {"over": "base"}, "base": {"over": "lid"}}})  # fmt: skip
    assert any("绕成一圈" in i.message for i in problems(cyclic))


def test_hinged_lid_closes_over_the_tray() -> None:
    """连盖托盘（0420 / 0421 / 0428 / 0429）：盖合上在墙顶高度，插舌插在前墙里面，防尘翼折进端墙。"""
    for code in ("0420", "0421", "0428", "0429"):
        _, dl = make(code)
        info = dl.style_meta["structure"]
        pts: dict[str, list[tuple[float, float, float]]] = {}
        for pid, _, _, X, Y, Z in info["targets"]:
            pts.setdefault(pid, []).append((X, Y, Z))
        top = max(z for _, _, z in pts["back"])
        assert {round(z, 6) for _, _, z in pts["lid"]} == {round(top, 6)}
        assert {round(y, 6) for _, y, _ in pts["tuck"]} == {0.0}
        assert ("dust_a" in pts) == (code == "0429")
        assert [f["key"] for f in info["faces"]] == ["bottom", "top"]


def test_calibrated_mailer_gets_fold_targets() -> None:
    """0427 走校准过的 fefco_0427（几何不动），引擎按面名给立体目标，按它自带的折法折回去对得上。"""
    for mat, size in (("flute_e", (250, 180, 70)), ("flute_b", (400, 300, 120))):
        p = STYLE.param_schema()(**fefco.recipe("0427", *size))
        dl = STYLE.generate(p, resolve_material(mat), PROFILE)
        info = dl.style_meta["structure"]
        assert info["calibrated_from"] == "fefco_0427" and info["fefco"] == "0427"
        assert info["fold_deviation_mm"] < 1e-6


def test_dust_option_only_for_hinged_lids() -> None:
    with pytest.raises(ValueError):
        STYLE.param_schema()(
            body={"form": "tray", "L": 300, "W": 200, "H": 80}, top={"kind": "open", "dust": False}
        )


def test_folder_is_a_slotted_tube_lying_along_l() -> None:
    """0410：开槽箱横放（筒沿 L），反查认得出“横放”，同样两头摇盖的竖放还是 0201。"""
    p = STYLE.param_schema()(**fefco.recipe("0410", 400, 300, 100))
    dl = STYLE.generate(p, FLUTE, PROFILE)
    info = dl.style_meta["structure"]
    assert info["fefco"] == "0410" and info["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["dims"]["Hi"] == pytest.approx(400)  # 筒长 = L
    assert dl.style_meta["joint"] == "tuck"
    assert dl.panel("glue").role.value == "tuck"
    assert (
        fefco.code_of({"kind": "flaps", "flap": "half"}, {"kind": "flaps", "flap": "half"})
        == "0201"
    )


def test_legacy_0410_glue_job_stays_readable_and_warns() -> None:
    legacy = fefco.recipe("0410", 400, 300, 100)
    legacy.pop("seam")  # 旧 job 没有 seam 字段
    p = STYLE.param_schema()(**legacy)
    assert not STYLE.validate(p, FLUTE, PROFILE)
    dl = STYLE.generate(p, FLUTE, PROFILE)
    assert dl.style_meta["structure"]["fefco"] == "0410"
    assert dl.style_meta["joint"] == "glue"
    assert dl.panel("glue").role.value == "glue_flap"
    assert any(
        i.id == "S04" and "没写接头" in i.message for i in STYLE.advisories(p, FLUTE, PROFILE)
    )

    # 只剩“未经折样校准”的成熟度提示（），没有接头提示
    def joint_notes(params: Any) -> list[str]:
        notes = STYLE.advisories(params, FLUTE, PROFILE)
        assert all(i.id == "S04" and "未经折样校准" in i.message for i in notes)
        return [i.message for i in notes if "没写接头" in i.message]

    fresh = STYLE.param_schema()(**fefco.recipe("0410", 400, 300, 100))
    assert not joint_notes(fresh)
    glued = STYLE.param_schema()(**{**legacy, "seam": {"kind": "glue"}})  # 有意粘接：不提示
    assert not joint_notes(glued)
    assert STYLE.generate(fresh, FLUTE, PROFILE).outline_segments() == dl.outline_segments()


@pytest.mark.parametrize("code", ["0425", "0425.1"])
@pytest.mark.parametrize("material", ["sbs_350", "flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("size", [(150, 100, 40), (300, 200, 80), (400, 300, 160)])
def test_four_double_walls_fold_with_lugs_inside_the_slots(
    code: str, material: str, size: tuple[int, int, int]
) -> None:
    """用实际折叠矩阵量插脚：穿过底平面的脚必须落在底槽里；各材料 / 尺寸也做刀版验收。"""
    from dataclasses import replace

    from shapely.geometry import Point, Polygon

    from pacxio.mockup.folding import Move, apply, build_part, panel_matrices

    mat = resolve_material(material)
    p = STYLE.param_schema()(**fefco.recipe(code, *size))
    issues = STYLE.validate(p, mat, PROFILE)
    assert not issues
    dl = STYLE.generate(p, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["structure"]["lug_alignment"]["status"] == "pass"
    assert dl.style_meta["structure"]["inner_clearance"]["status"] == "pass"
    from pacxio.mockup.foldcheck import fold_clashes

    assert not fold_clashes(dl)
    assert dl.style_meta["structure"]["fefco"] == code
    plan = plan_for(dl)
    assert plan is not None
    plan = replace(plan, moves={pid: Move(m.keys) for pid, m in plan.moves.items()})
    mats = panel_matrices(build_part(dl, plan), plan.steps)
    slots = [Polygon([(s.start.x, s.start.y) for s in e.segments])
             for e in dl.edges if e.id.startswith("cut_slot_")]  # fmt: skip
    assert len(slots) == 10  # 原端墙各 2；新增长边墙各 3
    for pid in ("front_in", "back_in", "side_a_in", "side_b_in"):
        pts = [apply(mats[pid], s.start.x, s.start.y) for s in dl.panel(pid).polygon.segments]
        # 根面是底板、Z=0；盒子朝纸背（负 Z）折，插脚穿底后 Z>0。
        feet = [Point(x, y) for x, y, z in pts if z > 1e-6]
        assert feet, pid
        assert all(any(slot.buffer(1e-6).covers(q) for slot in slots) for q in feet), pid


@pytest.mark.parametrize(
    "code,pid,angle", [("0425.1", "front_in", 0), ("0425", "ear_back_a_in", 90)]
)
def test_new_return_wall_folds_have_independent_targets(code: str, pid: str, angle: float) -> None:
    _, dl = make(code)
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"][pid]["keys"] = [[5, angle]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5


def test_return_wall_options_change_geometry_and_are_validated() -> None:
    spec = fefco.recipe("0425", 300, 200, 80)
    p = STYLE.param_schema()(**spec, tuning={"return_lug_count": 2, "return_wall": {"roll_gap": 0}})
    assert not STYLE.validate(p, FLUTE, PROFILE)
    dl = STYLE.generate(p, FLUTE, PROFILE)
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert "rim_front" not in {q.id for q in dl.panels}
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_")]) == 8
    assert not check_s01(dl) + check_crease_sides(dl)
    for tuning in ({"return_lug_count": 8, "return_wall": {"lug_w": 40}},
                   {"return_wall": {"ear_depth": 140}},
                   {"return_wall": {"inner_inset": 0.1}},
                   {"return_wall": {"roll_gap": 9}}):  # fmt: skip
        bad = STYLE.param_schema()(**spec, tuning=tuning)
        assert STYLE.validate(bad, FLUTE, PROFILE), tuning
    pair = STYLE.param_schema()(**{**spec, "body": {**spec["body"], "double_walls": "pair"}})
    assert not STYLE.validate(pair, FLUTE, PROFILE)
    assert STYLE.generate(pair, FLUTE, PROFILE).style_meta["structure"]["fefco"] is None
    for body in ({"form": "tube"}, {"corners": "glue"}, {"corners": "flap"}):
        with pytest.raises(ValueError, match="double_walls"):
            STYLE.param_schema()(body=spec["body"] | body)
    lid = STYLE.param_schema()(**{**spec, "top": {"kind": "hinged_lid"}})
    assert any("卷边与连盖" in i.message for i in STYLE.validate(lid, FLUTE, PROFILE))
    too_narrow = STYLE.param_schema()(
        **spec, tuning={"return_wall": {"lugs": False, "inner_inset": 200}}
    )
    assert any("没有可用宽度" in i.message for i in STYLE.validate(too_narrow, FLUTE, PROFILE))


def test_return_wall_options_do_not_alias_a_pair_of_double_walls() -> None:
    a, b = make("0425")[1], make("0425.1")[1]
    assert len(a.panels) == len(b.panels) + 4
    for code in ("0422", "0423", "0425", "0425.1"):
        p, _ = make(code)
        assert fefco.tray_code(p) == code
    spec = fefco.recipe("0425.1", 300, 200, 80)
    spec["body"]["lugs"] = False
    p = STYLE.param_schema()(**spec)
    dl = STYLE.generate(p, FLUTE, PROFILE)
    assert not [e for e in dl.edges if e.id.startswith("cut_slot_")]
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["structure"]["fefco"] is None


def test_lug_alignment_checks_actual_layers_not_just_zero_thickness_targets() -> None:
    from pacxio.engine.assembly import tray_inner_clearance, tray_lug_alignment

    _, dl = make("0425")
    info = dl.style_meta["structure"]
    assert info["fold_deviation_mm"] < 1e-6
    assert info["lug_alignment"]["status"] == "pass"
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["side_a_in"]["layer"] = -12.0
    assert tray_lug_alignment(broken)["status"] == "fail"
    assert tray_inner_clearance(broken, 300, 200)["status"] == "fail"
    flat_layers = copy.deepcopy(dl)
    for move in flat_layers.style_meta["fold_plan"]["moves"].values():
        move["layer"] = 0.0
    assert tray_lug_alignment(flat_layers)["status"] == "pass"


def test_catalog_acceptance_fails_if_lugs_miss_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.engine_targets import check

    from pacxio.engine import assembly

    monkeypatch.setattr(
        assembly,
        "tray_lug_alignment",
        lambda _: {"status": "fail", "max_miss_mm": 7.0, "misses_mm": {"side_a_in": 7.0}},
    )
    result = check("0425", (300, 200, 80), "fefco")
    assert result["status"] == "fail"
    assert any("插脚 / 底槽偏离" in issue for issue in result["issues"])


@pytest.mark.parametrize("corner_on", ["end", "long"])
@pytest.mark.parametrize("compensated", [True, False])
@pytest.mark.parametrize("roll_gap", [0.0, 3.0])
@pytest.mark.parametrize("code", ["0425", "0425.1"])
def test_four_double_wall_inner_size_tracks_recipe_and_roll_gap(
    code: str, corner_on: str, compensated: bool, roll_gap: float
) -> None:
    spec = fefco.recipe(code, 300, 200, 80)
    spec["body"]["corner_on"] = corner_on
    p = STYLE.param_schema()(**spec, apply_compensation=compensated,
                             tuning={"return_wall": {"roll_gap": roll_gap, "ear_depth": 60}})  # fmt: skip
    assert not STYLE.validate(p, FLUTE, PROFILE)
    dl = STYLE.generate(p, FLUTE, PROFILE)
    assert dl.style_meta["structure"]["inner_clearance"]["status"] == "pass"
    assert dl.style_meta["structure"]["lug_alignment"]["status"] == "pass"
    assert not check_s01(dl) + check_crease_sides(dl)


@pytest.mark.parametrize("code", ["0454", "0455", "0456"])
@pytest.mark.parametrize("material", ["sbs_350", "flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("size", [(150, 100, 30), (300, 200, 60), (400, 300, 100)])
def test_full_corner_ears_fold_and_preserve_inner_size(
    code: str, material: str, size: tuple[int, int, int]
) -> None:
    """整幅角翼的两种朝向：检查独立立体目标、实际内尺寸和静态纸厚干涉。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    params = STYLE.param_schema()(**fefco.recipe(code, *size))
    assert not STYLE.validate(params, mat, PROFILE)
    dl = STYLE.generate(params, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(params, mat, PROFILE))
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["structure"]["inner_clearance"]["status"] == "pass"
    assert dl.style_meta["structure"]["fefco"] == code
    assert not fold_clashes(dl)
    assert not [e for e in dl.edges if e.id.startswith("cut_slot_")]
    ears = [p for p in dl.panels if p.id.startswith("ear_")]
    assert len(ears) == 4
    for ear in ears:
        x0, y0, x1, y1 = ear.polygon.bbox()
        assert len(ear.polygon.segments) == 4
        assert x1 - x0 == pytest.approx(y1 - y0)


def test_full_ears_reject_invalid_combinations_and_detect_wrong_folds() -> None:
    spec = fefco.recipe("0454", 300, 200, 60)
    with pytest.raises(ValueError, match="无插脚"):
        STYLE.param_schema()(**{**spec, "body": {**spec["body"], "lugs": True}})
    with pytest.raises(ValueError, match="ear_shape"):
        STYLE.param_schema()(**{**spec, "body": {**spec["body"], "corners": "flap"}})
    bad = STYLE.param_schema()(**{**spec, "body": {**spec["body"], "corner_depth": 120}})
    assert any("太深" in issue.message for issue in STYLE.validate(bad, FLUTE, PROFILE))
    _, dl = make("0454", (300, 200, 60))
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["ear_fa"]["keys"] = [[1, -90.0]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5
    assert fefco.tray_code(STYLE.param_schema()(**fefco.recipe("0455", 300, 200, 60))) == "0455"


def test_full_ears_reject_unsafe_lid_and_corner_depth() -> None:
    spec = fefco.recipe("0454", 300, 200, 60)
    spec["top"] = {"kind": "hinged_lid"}
    params = STYLE.param_schema()(**spec)
    assert any("不带盖" in issue.message for issue in STYLE.validate(params, FLUTE, PROFILE))


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_c", "flute_bc"])
@pytest.mark.parametrize("size", [(200, 150, 100), (400, 300, 250), (500, 150, 120)])
def test_center_open_folder_has_equal_halves_and_no_joint(
    material: str, size: tuple[int, int, int]
) -> None:
    """0411：两个半盖在同一墙面中间对口，没有接舌或胶合接头。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    p = STYLE.param_schema()(**fefco.recipe("0411", *size))
    assert not STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    info = dl.style_meta["structure"]
    assert info["fefco"] == "0411" and info["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["joint"] == "none" and dl.style_meta["dims"]["joint_w"] == 0
    assert not [panel for panel in dl.panels if panel.id == "glue"]
    half_a = dl.panel("back_joint").polygon.bbox()
    half_b = dl.panel("back").polygon.bbox()
    assert half_a[2] - half_a[0] == pytest.approx(half_b[2] - half_b[0])
    assert (half_a[2] - half_a[0]) + (half_b[2] - half_b[0]) == pytest.approx(info["tube"]["L"])
    assert not fold_clashes(dl)


def test_center_open_seam_is_not_mislabeled_or_accepted_with_incompatible_options() -> None:
    spec = fefco.recipe("0411", 400, 300, 100)
    p = STYLE.param_schema()(**spec)
    dl = STYLE.generate(p, FLUTE, PROFILE)
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["back"]["keys"] = [[1, 0]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5
    spec["seam"]["wall"] = "short"
    other = STYLE.param_schema()(**spec)
    assert STYLE.generate(other, FLUTE, PROFILE).style_meta["structure"]["fefco"] is None
    for invalid in ({"kind": "none"}, {"kind": "glue", "center_open": True},
                    {"kind": "none", "center_open": True, "offset": 50}):  # fmt: skip
        with pytest.raises(ValueError):
            STYLE.param_schema()(**{**spec, "seam": invalid})
    for form in ("sheet", "tray"):
        with pytest.raises(ValueError, match="长方形筒身"):
            STYLE.param_schema()(**{**spec, "body": {**spec["body"], "form": form}})


@pytest.mark.parametrize("code", ["0409", "0410", "0411", "0501", "0502", "0503"])
def test_horizontal_tubes_are_oriented_to_product_dimensions(code: str) -> None:
    """3D 模型按产品 L/W/H 摆正，而不是把横放筒身的筒长错误当成高。"""
    from pacxio.mockup.fold import build_box

    p = STYLE.param_schema()(**fefco.recipe(code, 400, 300, 100))
    dl = STYLE.generate(p, FLUTE, PROFILE)
    box = build_box(dl)
    assert (box.x, box.y, box.z) == pytest.approx((403, 303, 103))
    corners = [c for face in box.faces for c in face.corners]
    assert min(z for _, _, z in corners) == pytest.approx(0, abs=1e-6)
    assert max(z for _, _, z in corners) == pytest.approx(box.z, abs=1e-6)


@pytest.mark.parametrize("code", ["0401", "0402", "0407"])
@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_c", "flute_bc"])
@pytest.mark.parametrize("size", [(200, 150, 40), (300, 200, 60), (400, 300, 100)])
def test_cross_flaps_close_to_expected_cover_depths(
    code: str, material: str, size: tuple[int, int, int]
) -> None:
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    p = STYLE.param_schema()(**fefco.recipe(code, *size))
    assert not STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    assert dl.style_meta["structure"]["fefco"] == code
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert not fold_clashes(dl)
    dims = dl.style_meta["dims"]
    front = dl.panel("rim_front").polygon.bbox()
    side = dl.panel("rim_side_a").polygon.bbox()
    expected_front = dims["base_w"] if code == "0407" else dims["base_w"] / 2
    assert front[3] - front[1] == pytest.approx(expected_front)
    expected_side = dims["base_l"] / 2 if code == "0402" else dims["wall_h"]
    assert side[2] - side[0] == pytest.approx(expected_side)
    if code == "0407":
        from pacxio.mockup.folding import apply, build_part, panel_matrices

        assert dl.panel("rim_side_a").role.value == "inner_wall"
        assert dl.style_meta["fold_plan"]["moves"]["rim_side_a"]["keys"] == [[2, 180.0]]
        plan = plan_for(dl)
        assert plan is not None
        matrices = panel_matrices(build_part(dl, plan), plan.steps)

        def centre(panel: str) -> tuple[float, float, float]:
            x0, y0, x1, y1 = dl.panel(panel).polygon.bbox()
            return apply(matrices[panel], (x0 + x1) / 2, (y0 + y1) / 2)

        assert abs(centre("rim_side_a")[0] - centre("rim_side_b")[0]) == pytest.approx(size[0])
        assert abs(centre("front")[1] - centre("back")[1]) - 2 * mat.caliper_mm == pytest.approx(
            size[1]
        )


def test_cross_flaps_detect_wrong_fold_and_reject_unsupported_options() -> None:
    p, dl = make("0402", (300, 200, 60))
    broken = copy.deepcopy(dl)
    broken.style_meta["fold_plan"]["moves"]["rim_front"]["keys"] = [[3, -90.0]]
    assert fold_deviation(broken, targets(dl), layers=False) > 5
    assert fefco.tray_code(p) == "0402"
    assert fefco.tray_code(STYLE.param_schema()(**fefco.recipe("0458", 300, 200, 60))) == "0458"
    custom = fefco.recipe("0458", 300, 200, 60)
    custom["body"]["rim"] = 20
    assert fefco.tray_code(STYLE.param_schema()(**custom)) is None
    custom = fefco.recipe("0458", 300, 200, 60)
    custom["top"] = {"kind": "hinged_lid"}
    assert fefco.tray_code(STYLE.param_schema()(**custom)) is None
    spec = fefco.recipe("0401", 300, 200, 60)
    spec["top"]["lock"] = True
    assert STYLE.validate(STYLE.param_schema()(**spec), FLUTE, PROFILE)
    spec = fefco.recipe("0402", 300, 200, 60)
    spec["top"]["depth"] = 20
    assert STYLE.validate(STYLE.param_schema()(**spec), FLUTE, PROFILE)


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_c", "flute_bc"])
@pytest.mark.parametrize("size", [(200, 150, 100), (400, 300, 250), (500, 150, 120)])
def test_free_tab_folder_folds_without_glue(material: str, size: tuple[int, int, int]) -> None:
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    p = STYLE.param_schema()(**fefco.recipe("0406", *size))
    assert not STYLE.validate(p, mat, PROFILE)
    dl = STYLE.generate(p, mat, PROFILE)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
    assert dl.style_meta["structure"]["fefco"] == "0406"
    assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
    assert dl.style_meta["joint"] == "tuck"
    assert dl.panel("glue").role.value == "tuck" and dl.panel("glue").printable
    assert not [q for q in dl.panels if q.role.value == "glue_flap"]
    assert not fold_clashes(dl)


def test_free_tab_does_not_alias_glued_seam_or_ignore_invalid_options() -> None:
    spec = fefco.recipe("0406", 400, 300, 100)
    other = STYLE.param_schema()(**{**spec, "seam": {"kind": "glue"}})
    dl = STYLE.generate(other, FLUTE, PROFILE)
    assert dl.panel("glue").role.value == "glue_flap"
    assert dl.style_meta["structure"]["fefco"] is None
    for seam in ({"kind": "tuck", "offset": 20}, {"kind": "tuck", "partition": True}):
        with pytest.raises(ValueError):
            STYLE.param_schema()(**{**spec, "seam": seam})
    on_card = STYLE.param_schema()(**spec)  # 结构不分材料（§20）：白卡也能做
    assert not STYLE.validate(on_card, resolve_material("sbs_350"), PROFILE)


def _pts(dl: Dieline) -> dict[str, list[tuple[float, float, float]]]:
    out: dict[str, list[tuple[float, float, float]]] = {}
    for pid, _, _, X, Y, Z in dl.style_meta["structure"]["targets"]:
        out.setdefault(pid, []).append((X, Y, Z))
    return out


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("code", ["0431", "0432", "0433", "0434", "0451.1"])
def test_rim_corners_fold_without_render_clashes(material: str, code: str) -> None:
    """0431–0434、0451.1：窄边收头 / 角柱 / 底边孔，3 组尺寸折回对得上、效果图不打架。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    for size in ((200, 150, 50), (300, 200, 80), (400, 300, 120)):
        p = STYLE.param_schema()(**fefco.recipe(code, *size))
        assert not STYLE.validate(p, mat, PROFILE), (size, STYLE.validate(p, mat, PROFILE))
        dl = STYLE.generate(p, mat, PROFILE)
        assert not check_s01(dl) + check_crease_sides(dl)
        assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
        assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-6
        assert dl.style_meta["structure"]["fefco"] == code
        assert not fold_clashes(dl), (size, fold_clashes(dl)[:2])


def test_rim_corner_mechanisms_land_where_the_catalog_shows() -> None:
    _, dl = make("0431")
    pts = _pts(dl)
    for ab, outside in (
        ("a", lambda x: x < 0),
        ("b", lambda x: x > dl.style_meta["dims"]["base_l"]),
    ):
        tip = pts[f"lug_front_{ab}"]
        assert all(outside(x) for x, _, _ in tip[1:-1])  # 插脚尖穿出端墙
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_post_")]) == 4
    # 插脚高度按带纸厚分层的折法对准墙脚的槽（立柱缩短 2.5·t【初值】，2026-11-06 机构功能核对查出原来低了）；
    # 零纸厚的折法里插脚会比槽高 2.5·t——两种模型差这么多，实物要打样确认
    from pacxio.engine import function_check

    folded = function_check.fold(dl)
    assert folded is not None
    assert {
        f"lug_{fb}_{ab}" for fb in ("front", "back") for ab in "ab"
    } <= function_check.through_slots(folded)
    assert not function_check.slots(folded) and not function_check.piercing(folded)
    _, dl = make("0433")
    pts = _pts(dl)
    (x0, y0, _), (x1, y1, _) = pts["post_front_a_diag"][0], pts["post_front_a_diag"][1]
    assert abs(abs(x1 - x0) - abs(y1 - y0)) < 1e-6  # 三角角柱斜边 45°
    assert {round(y, 6) for _, y, _ in pts["post_front_a_flat"]} == {0.0}  # 贴边贴在长边墙上
    _, dl = make("0434")
    pts = _pts(dl)
    assert {round(z, 6) for _, _, z in pts["rim_front_a"] + pts["mitre_front_a"]} == {
        round(dl.style_meta["dims"]["wall_h"], 6)
    }
    _, dl = make("0432")
    tabs = [e for e in dl.edges if e.id.startswith("cut_slot_tab_")]
    assert len(tabs) == 4 and all(e.panels[0] in ("rim_a", "rim_b") for e in tabs)
    assert {q.id for q in dl.panels if q.id.startswith("wing_")} == {
        "wing_front_a",
        "wing_front_b",
        "wing_back_a",
        "wing_back_b",
    }
    _, dl = make("0451.1")
    assert len([e for e in dl.edges if e.id.startswith("cut_edgehole_")]) == 8


def test_rim_corner_options_refuse_what_they_do_not_support() -> None:
    def issues(body: dict[str, Any]) -> list[str]:
        p = STYLE.param_schema()(
            body={"form": "tray", "L": 300, "W": 200, "H": 80} | body, top={"kind": "open"}
        )
        return [i.message for i in STYLE.validate(p, FLUTE, PROFILE)]

    assert issues({"corners": "flap", "corner_on": "end", "rim": 24, "rim_ends": "posts"})
    assert issues({"corners": "flap", "corner_on": "long", "rim": 24, "rim_ends": "mitre"})
    assert issues({"corners": "flap", "corner_on": "long", "rim": 24, "rim_ends": "tabs"})
    assert issues({"corners": "ear", "rim": 24, "rim_on": "long", "rim_ends": "posts"})
    assert issues({"corners": "flap", "corner_on": "long", "rim": 4, "rim_ends": "lugs"})
    with pytest.raises(ValueError):
        STYLE.param_schema()(body={"base": "rect", "L": 300, "W": 200, "H": 80, "edge_slots": True},
                             top={"kind": "open"}, bottom={"kind": "open"})  # fmt: skip


PRODUCE = [
    "0435",
    "0435.1",
    "0436",
    "0436.1",
    "0436.2",
    "0437",
    "0438",
    "0480",
    "0481",
    "0482",
    "0483",
    "0484",
]


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("code", PRODUCE)
def test_produce_trays_fold_without_render_clashes(material: str, code: str) -> None:
    """水果箱类托盘（堆叠凸舌、矮端墙、包角窄边、盖翼插底、三角角柱）：3 组尺寸。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    for size in ((200, 150, 60), (300, 200, 80), (400, 300, 120)):
        p = STYLE.param_schema()(**fefco.recipe(code, *size))
        assert not STYLE.validate(p, mat, PROFILE), (size, STYLE.validate(p, mat, PROFILE))
        dl = STYLE.generate(p, mat, PROFILE)
        assert not check_s01(dl) + check_crease_sides(dl)
        assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
        assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-3
        assert dl.style_meta["structure"]["fefco"] == code
        assert not fold_clashes(dl), (size, fold_clashes(dl)[:2])


def test_stacking_tabs_stand_above_the_wall_and_meet_base_slots() -> None:
    from pacxio.engine.tray_stack import tab_h

    t = FLUTE.caliper_mm
    for code, n in (
        ("0435", 0),
        ("0435.1", 2),
        ("0436", 2),
        ("0436.2", 4),
        ("0484", 4),
        ("0481", 4),
    ):
        _, dl = make(code)
        top = max(z for pts in _pts(dl).values() for _, _, z in pts)
        wall = dl.style_meta["dims"]["wall_h"]
        assert wall - t + tab_h(t) - 1e-6 <= top <= wall + tab_h(t) + 1e-6, code  # 凸舌竖着高出墙顶
        assert len([e for e in dl.edges if e.id.startswith("cut_slot_stack_")]) == n, code
    _, dl = make("0435")  # 一个插脚在中间：凸舌孔和插脚孔并成一个
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_a")]) == 1
    _, dl = make("0435.1")  # 两插脚之间：内墙下沿给下一层的凸舌让缺口
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_a")]) == 2


def test_produce_tray_mechanisms_land_where_the_catalog_shows() -> None:
    _, dl = make("0481")
    pts = _pts(dl)
    end_h = dl.style_meta["structure"]["recipe"]["body"]["end_height"]
    assert end_h <= min(z for _, _, z in pts["post_front_a"]) <= end_h + 4  # 立柱垂到矮端墙顶
    assert max(z for _, _, z in pts["side_a"]) == pytest.approx(end_h)
    _, dl = make("0437")
    assert min(z for _, _, z in _pts(dl)["drop_front_a"]) < 0  # 插脚穿过底板
    _, dl = make("0483")
    pts = _pts(dl)
    assert min(z for _, _, z in pts["post_front_a_tab"]) < 0  # 角柱插舌插进底板斜缝
    xs = [x for x, _, _ in pts["window_a"]]
    assert min(xs) >= -1e-6  # 开口那块翻进盒里
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_post_")]) == 4
    _, dl = make("0480")
    assert {q.id for q in dl.panels if q.id.startswith("wrap_")} == {
        "wrap_front_a",
        "wrap_front_b",
        "wrap_back_a",
        "wrap_back_b",
    }


def test_produce_options_refuse_what_they_do_not_support() -> None:
    def issues(body: dict[str, Any], top: dict[str, Any] | None = None) -> list[str]:
        p = STYLE.param_schema()(
            body={"form": "tray", "L": 300, "W": 200, "H": 80} | body, top=top or {"kind": "open"}
        )
        return [i.message for i in STYLE.validate(p, FLUTE, PROFILE)]

    assert issues({"corners": "flap", "flap_drops": True})
    assert issues({"corners": "flap", "corner_on": "long", "corner_posts": True})
    assert issues({"corners": "flap", "corner_on": "end", "end_height": 40})
    assert issues({"corners": "flap", "corner_on": "long", "end_height": 120})
    assert issues(
        {"corners": "flap", "corner_on": "end", "rim": 24, "rim_on": "long", "rim_ends": "wrap"}
    )
    assert issues({"corners": "ear", "stack": "center", "stack_on": "long"})
    assert issues({"corners": "flap", "lug_count": 1})


LIDDED = ["0418", "0426", "0440", "0441", "0442", "0461", "0464", "0465", "0466", "0475"]
TALL = ["0469", "0470", "0471", "0472"]


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("code", LIDDED + TALL)
def test_lidded_trays_fold_without_render_clashes(material: str, code: str) -> None:
    """连盖托盘（盖前沿插舌 / 外翻前翼 / 回折锁舌 / 锁舌，盖挂端墙，单面 / 两面双层墙）与高身包裹式。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    sizes = ((200, 150, 120), (300, 200, 150), (400, 300, 200)) if code in TALL else (
        (200, 150, 60), (300, 200, 80), (400, 300, 120))  # fmt: skip
    for size in sizes:
        p = STYLE.param_schema()(**fefco.recipe(code, *size))
        assert not STYLE.validate(p, mat, PROFILE), (size, STYLE.validate(p, mat, PROFILE))
        dl = STYLE.generate(p, mat, PROFILE)
        assert not check_s01(dl) + check_crease_sides(dl)
        assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
        assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-3
        assert dl.style_meta["structure"]["fefco"] == code
        assert not fold_clashes(dl), (size, fold_clashes(dl)[:2])


def test_lid_fronts_close_where_the_catalog_shows() -> None:
    _, dl = make("0418")  # 回折内衬顶到盖、锁舌穿过盖沿的槽
    pts = _pts(dl)
    top = dl.style_meta["dims"]["wall_h"]
    assert max(z for _, _, z in pts["tuck_return"]) > top
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_lock_")]) == 3
    _, dl = make("0441")  # 前翼盖在前墙外面：整幅
    lid = dl.panel("lid").polygon.bbox()
    lip = dl.panel("tuck").polygon.bbox()
    assert lip[2] - lip[0] == pytest.approx(lid[2] - lid[0])
    _, dl = make("0440")  # 盖挂在端墙上
    assert next(e for e in dl.edges if e.panels[1:] == ["lid"]).panels[0] == "side_a"
    _, dl = make("0475")  # 防尘翼前耳贴前墙里面
    assert {round(y, 6) for _, y, _ in _pts(dl)["dust_a_ear"]} == {round(FLUTE.caliper_mm + 1.0, 6)}
    _, dl = make("0426")
    assert not any(q.id.endswith("_ear") for q in dl.panels)
    _, dl = make("0470", (300, 200, 150))  # 端墙挂后墙、不连底板
    assert next(e for e in dl.edges if e.panels[1:] == ["side_a"]).panels[0] == "back"
    _, dl = make("0464")
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_flap_")]) == 4


def test_two_piece_mailer_is_a_set() -> None:
    from pacxio.engine.sets import SetParams, generate_set

    sp = SetParams(**fefco.set_recipe("0427.1", 300, 200, 80))
    parts = generate_set(sp, FLUTE, PROFILE, STYLE)
    assert set(parts) == {"base", "lid"}
    assert fefco.set_code(sp) == "0427.1"
    assert parts["base"].style_meta["structure"]["fefco"] == "0422"


def test_lid_options_refuse_what_they_do_not_support() -> None:
    def issues(body: dict[str, Any], top: dict[str, Any]) -> list[str]:
        p = STYLE.param_schema()(body={"form": "tray", "L": 300, "W": 200, "H": 80} | body, top=top)
        return [i.message for i in STYLE.validate(p, FLUTE, PROFILE)]

    assert issues({"corners": "wrap"}, {"kind": "hinged_lid"})  # 包裹式不带防尘翼
    assert issues({"corners": "flap", "front_return": True}, {"kind": "hinged_lid", "dust": False})
    with pytest.raises(ValueError):
        STYLE.param_schema()(body={"form": "tray", "L": 300, "W": 200, "H": 80},
                             top={"kind": "open", "lid_front": "lip"})  # fmt: skip


FOLDERS_04 = [
    "0400",
    "0403",
    "0412",
    "0413",
    "0414",
    "0415",
    "0416",
    "0443",
    "0444",
    "0445",
    "0447",
    "0473",
    "0474",
]


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("code", FOLDERS_04)
def test_folders_fold_without_render_clashes(material: str, code: str) -> None:
    """包裹式折叠盒：长条积木（strip）和托盘选项（半宽盖翼 + 端墙窄边 / 宽卷边 / 竖缝……）。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    for size in ((200, 150, 60), (300, 200, 100), (400, 300, 150)):
        p = STYLE.param_schema()(**fefco.recipe(code, *size))
        assert not STYLE.validate(p, mat, PROFILE), (size, STYLE.validate(p, mat, PROFILE))
        dl = STYLE.generate(p, mat, PROFILE)
        assert not check_s01(dl) + check_crease_sides(dl)
        assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
        assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-3
        assert dl.style_meta["structure"]["fefco"] == code
        assert not fold_clashes(dl), (size, fold_clashes(dl)[:2])


def test_strip_folds_around_the_cross_section() -> None:
    """长条：每过一条压线转 90°；端墙立在两头；同一面的第二层（内衬）落在底板上。"""
    _, dl = make("0400", (300, 200, 100))
    pts = _pts(dl)
    h = dl.style_meta["dims"]["wall_h"]
    assert {round(z, 6) for _, _, z in pts["right_2_top"]} == {round(h, 6)}  # 盖在顶上
    assert {round(z, 6) for _, _, z in pts["right_4_bottom"]} == {0.0}  # 内衬落回底面
    assert max(z for _, _, z in pts["base_a"]) == pytest.approx(h)  # 底板两头的端墙立到墙高
    sig = fefco._strip_sig([{"ends": "wall+rim"}, {}, {}, {}, {"inset": True}])
    assert sig is not None and sig[0]["side"] == "right"
    with pytest.raises(ValueError):
        STYLE.param_schema()(body={"form": "tray", "L": 300, "W": 200, "H": 80, "corners": "strip",
                                   "strip": [{"ends": "lid"}]}, top={"kind": "open"})  # fmt: skip


@pytest.mark.parametrize("code", ["0404", "0405"])
def test_two_piece_wraps_are_sets(code: str) -> None:
    from pacxio.engine.sets import SetParams, generate_set

    sp = SetParams(**fefco.set_recipe(code, 300, 200, 100))
    parts = generate_set(sp, FLUTE, PROFILE, STYLE)
    assert len(parts) == 2 and fefco.set_code(sp) == code


SINGLES = ["0439", "0446", "0457", "0459", "0459.1", "0460", "0462", "0463", "0485"]


@pytest.mark.parametrize("material", ["flute_e", "flute_b", "flute_bc"])
@pytest.mark.parametrize("code", SINGLES + ["0476"])
def test_bevel_and_single_mechanisms(material: str, code: str) -> None:
    """切角 / 八角托盘、杯托、扣角、展示托盘 / 展示盒。"""
    from pacxio.mockup.foldcheck import fold_clashes

    mat = resolve_material(material)
    sizes = (
        ((200, 200, 60), (300, 300, 80))
        if code == "0476"
        else ((200, 150, 60), (300, 200, 80), (400, 300, 120))
    )
    for size in sizes:
        p = STYLE.param_schema()(**fefco.recipe(code, *size))
        assert not STYLE.validate(p, mat, PROFILE), (size, STYLE.validate(p, mat, PROFILE))
        dl = STYLE.generate(p, mat, PROFILE)
        assert not check_s01(dl) + check_crease_sides(dl)
        assert not check_s02(dl, STYLE.expected_panel_sizes(p, mat, PROFILE))
        assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-3
        assert dl.style_meta["structure"]["fefco"] == code
        assert not fold_clashes(dl), (size, fold_clashes(dl)[:2])


def test_single_mechanisms_land_where_the_catalog_shows() -> None:
    _, dl = make("0476", (300, 300, 80))  # 正八角：八条边一样长
    xs = dl.panel("base").polygon.bbox()
    assert xs[2] - xs[0] == pytest.approx(xs[3] - xs[1])
    _, dl = make("0446")
    cups = [e for e in dl.edges if e.id.startswith("cut_cup_")]
    assert cups and all(len(e.segments) == 4 for e in cups)  # 真圆弧
    _, dl = make("0457")
    assert len([e for e in dl.edges if e.id.startswith("cut_slot_hook_")]) == 4
    pts = _pts(make("0460")[1])
    assert max(z for _, _, z in pts["side_a"]) < max(z for _, _, z in pts["front"])  # 端墙矮
    _, dl = make("0485")
    assert any(e.id == "crease_lid_header" for e in dl.edges)
    p = STYLE.param_schema()(**fefco.recipe("0476", 300, 200, 80))
    assert any("L = W" in i.message for i in STYLE.validate(p, FLUTE, PROFILE))


SETS_NEW = [
    "0307",
    "0314",
    "0321",
    "0322",
    "0323",
    "0325",
    "0350",
    "0351",
    "0352",
    "0360",
    "0404",
    "0405",
    "0509",
    "0510",
    "0511",
    "0512",
]


@pytest.mark.parametrize("code", SETS_NEW)
def test_new_sets_generate_fit_and_read_back(code: str) -> None:
    """03 / 05 其余多件套：每件刀版检查、折回，套合（S05），反查编号。"""
    from pacxio.engine.sets import SetParams, check_fit, generate_set, validate_set

    for size in ((300, 200, 150), (400, 300, 250)):
        sp = SetParams(**fefco.set_recipe(code, *size))
        assert not validate_set(sp, FLUTE, PROFILE, STYLE)
        parts = generate_set(sp, FLUTE, PROFILE, STYLE)
        for dl in parts.values():
            assert not check_s01(dl) + check_crease_sides(dl)
            assert dl.style_meta["structure"]["fold_deviation_mm"] < 1e-3
        assert not check_fit(sp, parts)
        assert fefco.set_code(sp) == code


def test_polygon_caps_fit_polygon_tubes() -> None:
    from pacxio.engine.sets import SetParams, generate_set, outer_of

    for code, n in (("0350", 8), ("0360", 6)):
        parts = generate_set(
            SetParams(**fefco.set_recipe(code, 300, 300, 200)), FLUTE, PROFILE, STYLE
        )
        cap = parts["cap"].panel("base").polygon
        assert len(cap.segments) == n  # 盖帽底板正 n 边形
        a, b = outer_of(parts["body"])
        assert a == pytest.approx(b)
