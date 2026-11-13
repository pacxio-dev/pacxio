"""机构功能核对（2026-11-06，engine/function_check.py）：折好后合不合得上、扣不扣得上、有没有面互相穿过。

不看配方给的目标位置，只看按刀版和折法真正折好以后的位置——所以故意折坏的结构必须抓得到，
已校准的结构必须通过。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pacxio import api
from pacxio.engine import function_check as fc
from pacxio.mockup.folding import build_part, panel_matrices, plan_for
from pacxio.model.dieline import Dieline
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style

PROFILE = load_profile("default")


def _make(code: str, dims: tuple[float, float, float]) -> tuple[Dieline, dict[str, Any]]:
    ecma = code.startswith("A")
    found = api.structure_params(None if ecma else code, code if ecma else None, None, *dims)
    assert isinstance(found, tuple)
    style = get_style(found[0])
    p = style.param_schema()(**found[1])
    dl = style.generate(p, resolve_material("sbs_350" if ecma else "flute_b"), PROFILE)
    return dl, found[1]


def _broken(dl: Dieline, panel: str, factor: float) -> fc.Folded:
    """把一块面的折角乘上 factor（0 = 不折、0.5 = 折一半）再折好。"""
    plan = plan_for(dl)
    assert plan is not None
    move = plan.moves[panel]
    keys = tuple((step, angle * factor) for step, angle in move.keys)
    plan = replace(plan, moves={**plan.moves, panel: replace(move, keys=keys)})
    f = fc.fold(dl)
    assert f is not None
    f.mats = {k: np.asarray(v) for k, v in panel_matrices(build_part(dl, plan), plan.steps).items()}
    f.__post_init__()
    return f


def _all(f: fc.Folded, params: dict[str, Any]) -> list[fc.Finding]:
    ends = fc.open_ends(params)
    found = fc.enclosure(f, 0) if ends == 0 else []
    return (
        found
        + fc.protrusion(f)
        + fc.shell_overhang(f, params)
        + fc.piercing(f)
        + fc.slots(f)
        + fc.holes(f)
    )


CALIBRATED = [
    ("0201", (400, 300, 250)),
    ("0427", (240, 170, 70)),
    ("0711", (400, 300, 250)),
    ("A20.20.03.01", (60, 40, 120)),
    ("A55.20.03.01", (60, 40, 120)),
    ("A20.21.01.01", (60, 40, 120)),  # 带挂板：挂板高出盒顶不算没合上
]


@pytest.mark.parametrize(("code", "dims"), CALIBRATED)
def test_calibrated_structures_pass(code: str, dims: tuple[float, float, float]) -> None:
    dl, params = _make(code, dims)
    assert fc.check(dl, params)["status"] == "pass"


BROKEN = [
    ("0201", (400, 300, 250), "top_back", 0.5, "overhang"),  # 大摇盖只合到一半
    ("0201", (400, 300, 250), "top_side_a", 0.0, "protrusion"),  # 小摇盖没合
    ("A20.20.03.01", (60, 40, 120), "top_tuck", 0.0, "protrusion"),  # 插舌没折进去
    ("A20.20.03.01", (60, 40, 120), "top_lid", 0.6, "overhang"),  # 盖子没盖严
    ("0427", (240, 170, 70), "side_a_in", 0.0, "slot"),  # 内墙没折回去，锁扣槽空着
    ("0427", (240, 170, 70), "top_lid", 0.7, "protrusion"),  # 盖子没盖上
    ("0711", (400, 300, 250), "btm_front", 0.5, "overhang"),  # 自动锁底一块没锁
    (
        "0217",
        (180, 90, 330),
        "end_a",
        0.0,
        "slot",
    ),  # 山形端翼竖着没折进去，缝没套住手提板（斜得不够、缝顶没卡进 V 口这种半扣上的查不出，test_gable 专门核对缝顶）
]


@pytest.mark.parametrize(("code", "dims", "panel", "factor", "kind"), BROKEN)
def test_broken_folds_are_caught(
    code: str, dims: tuple[float, float, float], panel: str, factor: float, kind: str
) -> None:
    dl, params = _make(code, dims)
    assert not _all(fc.fold(dl), params)  # type: ignore[arg-type]  # 原样通过
    found = _all(_broken(dl, panel, factor), params)
    assert any(x.check == kind for x in found), [x.message for x in found]


FIXED = [  # S07 查出来、已修（2026-11-06）
    (
        "0701",
        (400, 300, 250),
        "flute_b",
    ),  # 插舌锁底：长翼做到对面墙、缝开在长翼那一层的高度、对准插舌
    ("0866", (300, 200, 150), "flute_e"),
    ("0232", (400, 300, 250), "flute_bc"),  # 斜缝自锁底：端摇盖外段能折、角顶进斜缝
    ("0431", (400, 300, 100), "flute_b"),  # 立柱插脚对准端墙脚的槽
    ("0730", (400, 300, 250), "flute_b"),  # 扇形翼合到中心
    ("0850", (400, 300, 250), "flute_e"),
    ("0948", (400, 600, 100), "flute_b"),  # 两根管立在窄条两头（原来卷成一圈）
    ("0942", (400, 600, 100), "flute_b"),
]


@pytest.mark.parametrize(("code", "dims", "material"), FIXED)
def test_fixed_structures_now_work(
    code: str, dims: tuple[float, float, float], material: str
) -> None:
    found = api.structure_params(code, None, None, *dims)
    assert isinstance(found, tuple)
    style = get_style(found[0])
    p = style.param_schema()(**found[1])
    mat = resolve_material(material)
    assert not style.validate(p, mat, PROFILE)
    assert fc.check(style.generate(p, mat, PROFILE), found[1])["status"] == "pass"


def test_sizes_that_cannot_work_are_rejected_up_front() -> None:
    style = get_style("structure")
    for code, dims in (("0942", (400, 300, 250)), ("0205", (500, 150, 120))):
        found = api.structure_params(code, None, None, *dims)
        assert isinstance(found, tuple)
        issues = style.validate(
            style.param_schema()(**found[1]), resolve_material("flute_b"), PROFILE
        )
        assert issues and issues[0].id == "S03", code


OCTAGON_BC = {"body": {"base": "regular", "sides": 8, "edge": 120},
              "top": {"kind": "flaps"}, "bottom": {"kind": "flaps"}}  # fmt: skip
# BC 楞的翼缝宽，八片扇形翼收不到中心，中间留一个约 24 mm 的洞（如实报）


def test_open_structures_and_assembly_slots_are_not_flagged() -> None:
    for code, dims in (
        ("0436", (400, 300, 100)),
        ("0451.1", (400, 300, 100)),
        ("0801", (400, 300, 250)),
    ):
        dl, params = _make(code, dims)  # 堆叠槽、底边孔、斜口展示箱
        assert fc.check(dl, params)["status"] == "pass", code


def test_check_and_suggest_use_it(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = api.structure_check(recipe=OCTAGON_BC, dims=(None, None, 150), material="flute_bc")
    assert bad.ok and bad.data["usable"] is False and any(w.id == "S07" for w in bad.warnings)
    good = api.structure_check("0427", dims=(240, 170, 70))
    assert good.data["usable"] and not good.warnings
    # 推荐：给了尺寸时，核对不过的款跳过（这里让排第一的那款不过）
    first = api.structures_suggest(uses=["展示"], limit=1).data["suggestions"][0]["structure"][
        "code"
    ]
    real = api._function_problems
    monkeypatch.setattr(api, "_function_problems",
                        lambda code, cat, dims: ["折好后合不上"] if code == first else real(code, cat, dims))  # fmt: skip
    s = api.structures_suggest(uses=["展示"], dims=(400, 300, 250), limit=3)
    codes = [row["structure"]["code"] for row in s.data["suggestions"]]
    assert codes and first not in codes and s.data["skipped"][0]["code"] == first


@pytest.mark.pro
def test_preflight_blocks_delivery_with_s07(tmp_path: Path) -> None:
    found = api.structure_params(None, None, OCTAGON_BC, None, None, 150)
    assert isinstance(found, tuple)
    assert api.init_job(tmp_path / "j", found[0], found[1], "flute_bc").ok
    r = api.preflight_job(tmp_path / "j")
    assert not r.ok and any(e.id == "S07" for e in r.errors)  # error：不出工厂交付包
