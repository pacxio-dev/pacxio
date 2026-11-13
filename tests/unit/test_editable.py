"""可编辑项（engine/editable.py）：只列改了刀版真会变的参数，标出动到哪儿、依赖、离开校准做法的值。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pacxio import api
from pacxio.engine.editable import describe
from pacxio.model.profile import load_profile, resolve_material

PROFILE = load_profile("default")


def _params(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["path"]: p for p in data["parameters"]}


def _recipe(code: str, dims: tuple[float, float, float]) -> dict[str, Any]:
    found = api.structure_params(None if code.startswith("A") else code,
                                 code if code.startswith("A") else None, None, *dims)  # fmt: skip
    assert isinstance(found, tuple)
    return found[1]


def test_calibrated_rte_lists_effective_params_and_route_changes() -> None:
    data = describe(_recipe("A20.20.03.01", (80, 50, 120)), resolve_material("sbs_350"), PROFILE)
    assert data["route"] == "calibrated:rte"
    p = _params(data)
    tuck = p["top.tuck_len"]
    assert tuck["auto"] and tuck["auto_value"] > 0 and tuck["affects"]["panels"]
    assert tuck.get("both_ends")  # 校准过的插舌盒两头插舌一样长
    assert "tuning.friction_lock" in p and p["tuning.friction_lock"]["label"] == "插舌卡口"
    assert p["bottom.on"]["changes_route"]["0"] == "calibrated:ste"  # 底盖换到同一面：变成同向插
    assert data["dependencies"]["body.W"] == ["top.tuck_len", "bottom.tuck_len"]
    assert not any(k.startswith("body.window") for k in p) or p["body.window"]["affects"]


def test_parameters_that_do_not_change_the_dieline_are_not_listed(tmp_path: Path) -> None:
    """0427 飞机盒的旧兼容字段 tuck_radius / lid_flap_inset 在 v2 里不生效（C3 实测）：不能当编辑项。"""
    data = describe(
        {"L": 260, "W": 180, "H": 70}, resolve_material("flute_e"), PROFILE, "fefco_0427"
    )
    p = _params(data)
    assert "tuck_radius" not in p and "lid_flap_inset" not in p
    assert {"roll_gap", "lug_w", "tuck_ear_len"} <= p.keys()
    assert any(m["type"] == "tab_slot" for m in data["structure"]["mechanisms"])


def test_shared_value_is_not_credited_to_the_other_end() -> None:
    """0215：底是扣底，bottom.tuck_len 只会改顶盖插舌（两端共用一个插舌长）——不算底部的参数。"""
    p = _params(describe(_recipe("0215", (300, 200, 150)), resolve_material("flute_b"), PROFILE))
    assert "top.tuck_len" in p and "bottom.tuck_len" not in p
    assert p["tuning.slot_w"]["auto_value"] > 0  # 拼装器记下的实际开槽宽


def test_display_box_options_and_lock_mechanisms() -> None:
    recipe = {
        "body": {"form": "tray", "corners": "flap", "corner_on": "long", "L": 150, "W": 150, "H": 40},
        "top": {"kind": "hinged_lid", "lid_front": "none", "dust_lock": True, "tear_window": True,
                "display_lid": "tab"},
        "bottom": {"kind": "open"},
    }  # fmt: skip
    data = describe(recipe, resolve_material("sbs_350"), PROFILE)
    p = _params(data)
    assert set(p["top.display_lid"]["options"]) == {"none", "rect", "tab"}
    assert p["top.dust_lock"]["group"] == "option"
    tabs = {m["tab"] for m in data["structure"]["mechanisms"] if m["type"] == "tab_slot"}
    assert tabs == {"lock_a_1", "lock_a_2", "lock_b_1", "lock_b_2"}


def test_update_works_for_hand_written_styles(tmp_path: Path) -> None:
    job = tmp_path / "mailer"
    assert api.init_job(job, "fefco_0427", {"L": 260, "W": 180, "H": 70}, "flute_e").ok
    r = api.structure_editable(directory=job)
    assert r.ok and r.data["style"] == "fefco_0427"
    up = api.structure_update(job, {"lug_w": 24})
    assert up.ok, up.errors
    assert up.data["params"]["lug_w"] == 24


def test_cli_editable_and_update(tmp_path: Path) -> None:
    import json

    from typer.testing import CliRunner

    from pacxio.cli import app

    runner = CliRunner()
    job = tmp_path / "rte"
    found = api.structure_params(None, "A20.20.03.01", None, 80, 50, 120)
    assert isinstance(found, tuple)
    assert api.init_job(job, "structure", found[1], "sbs_350").ok
    res = runner.invoke(app, ["structures", "editable", str(job), "--json"])
    data = json.loads(res.stdout)
    assert data["ok"] and data["command"] == "structures editable"
    assert any(p["path"] == "top.tuck_len" for p in data["data"]["parameters"])
    res = runner.invoke(
        app, ["structures", "update", str(job), "--set", "top.tuck_len=18", "--json"]
    )
    out = json.loads(res.stdout)
    assert out["ok"], out["errors"]
    assert out["data"]["params"]["top"]["tuck_len"] == 18
    res = runner.invoke(app, ["structures", "update", str(job), "--set", "nonsense", "--json"])
    assert res.exit_code != 0


def test_items_carry_designer_terms() -> None:
    """页面用设计师的说法：每项有中文名、部位、常用与否；选项有中文名；不把代码名当名字。"""
    recipe = {
        "body": {"form": "tray", "corners": "flap", "corner_on": "long", "L": 150, "W": 150, "H": 40},
        "top": {"kind": "hinged_lid", "lid_front": "none", "dust_lock": True, "tear_window": True,
                "display_lid": "tab"},
        "bottom": {"kind": "open"},
    }  # fmt: skip
    data = describe(recipe, resolve_material("sbs_350"), PROFILE)
    p = _params(data)
    lid = p["top.display_lid"]
    assert lid["name"] == "展示盖" and lid["part"] == "顶部" and lid["common"]
    assert lid["option_names"]['"tab"'] == "两角圆 + 半圆拉手"
    assert p["top.dust_lock"]["option_names"]["true"] == "有"
    for item in data["parameters"]:
        assert "." not in item["name"] and "_" not in item["name"], item["name"]


def test_multipart_styles_list_params_per_component(tmp_path: Path) -> None:
    """多件套也能改：参数按部件分（天地盖的地盒 / 天盖、抽屉的内托 / 外套）；“base”在这里是部件，不跳过。"""
    from collections import Counter

    data = describe({"L": 120, "W": 80, "H": 40}, resolve_material("sbs_350"), PROFILE, "tray_lid")
    comps = Counter(p.get("component_name") for p in data["parameters"])
    assert comps["地盒"] > 0 and comps["天盖"] > 0
    assert _params(data)["lid_h"]["component_name"] == "天盖"  # 最外层但只管盖的参数，归到盖
    drawer = describe({"L": 120, "W": 80, "H": 40}, resolve_material("sbs_350"), PROFILE, "drawer")
    assert _params(drawer)["sleeve_clearance"]["component_name"] == "外套"
    job = tmp_path / "lid"
    assert api.init_job(job, "tray_lid", {"L": 120, "W": 80, "H": 40}, "sbs_350").ok
    tried = api.structure_try(job, {"lid_h": 20})
    assert (
        tried.ok
        and len(tried.data["sizes"]["parts"]) == 2
        and len(tried.data["scene"]["parts"]) == 2
    )
    assert tried.data["dims_px"] and tried.data["dims_px"][0]["panel"].startswith("base.")
    assert api.structure_update(job, {"lid_h": 20}).ok


def test_engine_set_edits_the_master_part_and_fits_follow(tmp_path: Path) -> None:
    """引擎多件套：基础的长宽高是被套那件的；套在外面的件长宽跟着变，自己的高在高级里。"""
    found = api.structure_params("0310", None, None, 300, 200, 100)
    assert isinstance(found, tuple) and found[0] == "structure_set"
    job = tmp_path / "caps"
    assert api.init_job(job, "structure_set", found[1], "flute_b").ok
    info = api.structure_editable(directory=job)
    assert info.ok and info.data["basics"]["paths"]["L"] == "parts.body.body.L"
    assert "parts.cap.body.H" in {p["path"] for p in info.data["parameters"]}
    before = api.structure_try(job, {}).data["sizes"]["parts"]
    after = api.structure_try(job, {"parts": {"body": {"body": {"L": 350}}}}).data["sizes"]["parts"]
    assert after[1]["sheet"][0] > before[1]["sheet"][0]  # 盖帽跟着变大


def test_engine_caps_set_preview_assembles_both_caps() -> None:
    """0310 套盖：中间件上下开口，两个一样的盖帽当顶和底——预览 3D 装上两个、尺寸说明写 ×2。"""
    from pacxio.engine.preview import preview_scene_parts
    from pacxio.styles import get_style

    found = api.structure_params("0310", None, None, 300, 200, 100)
    assert isinstance(found, tuple)
    st = get_style("structure_set")
    p = st.param_schema()(**found[1])
    dls = st.generate_parts(p, resolve_material("flute_b"), PROFILE)  # type: ignore[attr-defined]
    scene = preview_scene_parts(
        "structure_set", dls, {"set": found[1]["set"], "fits": found[1]["fits"]}
    )
    assert scene is not None
    assert [q["id"] for q in scene["parts"]] == ["body", "cap_1", "cap_2"]
    # 平铺时各占一条道：三件平铺位置的 y 都不一样
    lanes = {q["id"]: q["poses"][0]["t"][1] for q in scene["parts"]}
    assert len(set(lanes.values())) == 3


def test_too_thick_material_is_greyed_and_refused(tmp_path: Path) -> None:
    """120×80×40 的天地盖：B / C / BC 瓦楞比盒子的边厚太多、折不起来——材料列表标不能用，试改、保存都拒。"""
    job = tmp_path / "lid"
    assert api.init_job(job, "tray_lid", {"L": 120, "W": 80, "H": 40}, "sbs_350").ok
    mats = {m["id"]: m for m in api.structure_editable(directory=job).data["basics"]["materials"]}
    assert (
        mats["flute_e"]["ok"] and not mats["flute_bc"]["ok"] and "纸太厚" in mats["flute_bc"]["why"]
    )
    assert mats["flute_bc"]["min_dim"] == 50
    bad = api.structure_try(job, {}, "flute_bc")
    assert not bad.ok and "纸太厚" in bad.errors[0].message
    assert not api.structure_update(job, {}, "flute_b").ok
    assert api.structure_try(job, {}, "flute_e").ok
    # 盒子改小到卡纸也太厚（< 15·t）以前不会发生；E 楞下把高改成 20 就拒
    assert not api.structure_try(job, {"H": 20}, "flute_e").ok


def test_hand_written_rte_shows_real_values_and_narrow_highlight() -> None:
    """用户：插舌长填 15、糊口宽“没反应”——页面没给现在的值（自动 16、默认 15），悬停高亮了所有面。"""
    data = describe({"L": 80, "W": 50, "H": 120}, resolve_material("sbs_350"), PROFILE, "rte")
    p = _params(data)
    glue, tuck = p["glue_w"], p["tuck_len"]
    assert glue["value"] == 15.0 and not glue["auto"]  # 有固定默认值的不是“自动”
    assert tuck["auto"] and tuck["auto_value"] == 16.0 and tuck.get("both_ends")
    assert glue["affects"]["panels"] == ["glue"]  # 只是挪动位置的面不算动到
    assert tuck["affects"]["panels"] == ["btm_tuck", "top_tuck"]
    assert p["tuck_radius"]["auto_value"] == 5.0  # 刀版上量不出来的按盒型规则算
