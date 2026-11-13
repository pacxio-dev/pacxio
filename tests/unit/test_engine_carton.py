"""长方彩盒的封口积木和 ECMA 编号（engine/ecma.py）。原彩盒拼装器 engine/carton.py 2026-11-07 并进唯一的长方筒身拼装器
（engine/slotted.py，GEOMETRY_ENGINE.md §20）：这里查卡纸下 ECMA 款照样生成、折得回、编号认得出。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from pacxio import api
from pacxio.engine.ecma import PUBLIC_A, code_of, parse, recipe
from pacxio.engine.style import FOLD_TOL_MM, fold_deviation
from pacxio.mockup.folding import plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style

PROFILE = load_profile("default")
CARTON = resolve_material("sbs_350")
STYLE = get_style("structure")


def make(code: str, size: tuple[float, float, float] = (80, 50, 120)) -> tuple[Any, Dieline]:
    p = STYLE.param_schema()(**recipe(code, *size))
    assert not STYLE.validate(p, CARTON, PROFILE), STYLE.validate(p, CARTON, PROFILE)
    return p, STYLE.generate(p, CARTON, PROFILE)


def targets(dl: Dieline) -> list[tuple[str, tuple[float, float], tuple[float, float, float]]]:
    return [
        (pid, (x, y), (X, Y, Z)) for pid, x, y, X, Y, Z in dl.style_meta["structure"]["targets"]
    ]


@pytest.mark.parametrize("code", PUBLIC_A)
def test_every_public_ecma_code_generates_and_folds(code: str) -> None:
    """reference 公开刀版库列的 130 款 ECMA：都能生成、过刀版检查、按自带折法折好后每块面落在该在的地方。"""
    p, dl = make(code)
    assert not check_s01(dl) + check_crease_sides(dl)
    assert not check_s02(dl, STYLE.expected_panel_sizes(p, CARTON, PROFILE))
    info = dl.style_meta["structure"]
    assert plan_for(dl) is not None
    assert info["fold_deviation_mm"] <= FOLD_TOL_MM
    if info.get("assembled"):  # 积木拼装的：反查出来的编号就是这一款
        assert info["ecma"] == code
        assert info["fold_deviation_mm"] < 1e-6


def test_ecma_positions_follow_the_public_numbering() -> None:
    """面号：01 = 紧挨糊口的第 1 面（背面），03 = 正面。反插 = 上下盖在不同面（A20.20.03.01 = rte）。"""
    assert parse("A20.20.03.01") == ("20", "20", "03", "01")
    calibrated = {}
    for code in ("A20.20.03.01", "A20.20.01.01", "A55.20.03.01", "A60.20.00.01", "A20.21.01.01"):
        _, dl = make(code)
        calibrated[code] = dl.style_meta["structure"]["calibrated_from"]
    assert calibrated == {"A20.20.03.01": "rte", "A20.20.01.01": "ste", "A55.20.03.01": "snaplock",
                          "A60.20.00.01": "autolock", "A20.21.01.01": "rte_hanger"}  # fmt: skip
    _, tongue_back = make("A55.01.01.00")  # 扣底锁舌翼挂背面（现有扣底盒在正面）
    assert tongue_back.style_meta["structure"]["bottom"]["on"] == "back"
    ids = {p.id for p in tongue_back.panels}
    assert {"btm_back", "btm_front", "btm_sa", "btm_sb"} <= ids
    assert code_of({"kind": "side_tuck", "on": "front"}, {"kind": "open"}) == "A01.40.00.03"
    with pytest.raises(ValueError):
        parse("A30.20.01.01")


def test_side_tuck_blocks_look_like_the_public_dieline() -> None:
    """侧插（ECMA 40）：盖两侧带翼、对面一片梯形内翼、侧墙不带翼也不缩；翼根和侧墙上沿共用一刀。"""
    _, dl = make("A01.40.00.01")
    ids = {p.id for p in dl.panels}
    assert {"top_lid", "top_wing_a", "top_wing_b", "top_inner"} <= ids
    assert not ids & {"dust_ta", "dust_tb"}
    back, side = dl.panel("back").polygon.bbox(), dl.panel("side_a").polygon.bbox()
    assert side[3] == pytest.approx(back[3])  # 开口 / 侧插端侧墙不缩
    assert side[1] == pytest.approx(back[1])
    cut = next(e for e in dl.edges if e.id == "cut_top_wing_a")
    assert cut.panels == ["top_wing_a", "side_a"]
    lid = dl.panel("top_lid").polygon.bbox()
    inner = dl.panel("top_inner").polygon.bbox()
    assert inner[3] - inner[1] < lid[3] - lid[1]  # 内翼比盖浅，合上不顶对面墙


def test_fold_check_catches_wrong_wing_and_flap_folds() -> None:
    for code, pid, keys in (
        ("A40.40.01.03", "top_wing_a", [[7, -90.0]]),  # 翼往外翻
        ("A11.10.01.03", "btm_back", [[5, 90.0], [6, 0.0]]),  # 缩小翼没折进去
        ("A01.21.00.03", "hanger", [[3, 90.0]]),  # 挂板被折倒
    ):
        _, dl = make(code)
        broken = copy.deepcopy(dl)
        broken.style_meta["fold_plan"]["moves"][pid]["keys"] = keys
        assert fold_deviation(broken, targets(dl), layers=False) > 5, (code, pid)


def test_blocks_refuse_wrong_ends_and_sizes() -> None:
    def issues(top: dict[str, Any], bottom: dict[str, Any], **body: float) -> list[str]:
        values = {"L": 80, "W": 50, "H": 120} | body
        p = STYLE.param_schema()(body={"base": "rect", **values}, top=top, bottom=bottom)
        return [i.message for i in STYLE.validate(p, CARTON, PROFILE)]

    assert any("只能在顶部" in m for m in issues({"kind": "open"}, {"kind": "hanger"}))
    assert any("侧插翼宽" in m for m in issues({"kind": "side_tuck", "wing": 60}, {"kind": "open"}))
    assert issues({"kind": "side_tuck"}, {"kind": "reduced", "on": 0}) == []


@pytest.mark.pro
def test_assembled_recipe_runs_the_pipeline(tmp_path: Path) -> None:
    from pacxio.mockup.service import make_mockup

    directory = tmp_path / "side_tuck"
    assert api.init_job(
        directory, "structure", recipe("A55.40.01.01", 80, 50, 120), "sbs_350", "default"
    ).ok
    result = make_mockup(directory)
    assert result.ok, result.errors
    assert (directory / "mockup" / "view_front_right.png").exists()
