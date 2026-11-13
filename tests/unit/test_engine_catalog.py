"""几何引擎第 5 步 ②③：结构目录（成熟度、怎么合上、用途标签）、按需求推荐、不建 job 的预览。"""

from __future__ import annotations

from pathlib import Path

from pacxio import api
from pacxio.engine import catalog


def test_catalog_covers_fefco_and_public_ecma() -> None:
    es = catalog.entries()
    from pacxio.engine import combos

    assert len(es) == 325 + 130 + len(combos.COMBOS)  # FEFCO + ECMA 公开 + 常用组合
    calibrated = {e.code for e in es if e.maturity == "calibrated"}
    assert {"0201", "0711", "0427", "A20.20.03.01"} <= calibrated
    assert catalog.get("0801").maturity == "standard"  # type: ignore[union-attr]


def test_closure_and_tags_come_from_the_recipe() -> None:
    rsc = catalog.get("0201")
    assert rsc is not None and rsc.reclosable is False and not rsc.no_tape and "运输" in rsc.tags
    mailer = catalog.get("0427")
    assert mailer is not None and mailer.reclosable and mailer.no_tape and "电商" in mailer.tags
    tuck = catalog.get("A20.20.03.01")
    assert tuck is not None and tuck.reclosable and "彩盒" in tuck.tags
    kit = catalog.get("0933")
    assert kit is not None and kit.kind == "set" and "多件" in kit.tags
    assert "展示" in catalog.get("0801").tags  # type: ignore[union-attr]


def test_search_puts_calibrated_first() -> None:
    found = catalog.search(series="02")
    assert found[0].maturity == "calibrated" and len(found) == 36  # 系列按编号前两位（0913 算 09）
    assert all("电商" in e.tags for e in catalog.search(tags=["电商"]))


def test_suggest_prefers_calibrated_common_single_structures() -> None:
    picks = catalog.suggest(uses=["电商"], reclosable=True)
    assert picks and picks[0][0].code == "0427"
    assert any("已校准" in w for w in picks[0][1])
    assert not catalog.suggest(uses=["电商"], query="不存在的关键词")


def test_show_gives_a_recipe_that_builds_a_job(tmp_path: Path) -> None:
    r = api.structure_show("0711", 400, 300, 250)
    assert r.ok and r.data["style"] == "structure"
    assert api.init_job(tmp_path / "j", r.data["style"], r.data["recipe"], "flute_b").ok
    assert not api.structure_show("9999").ok


def test_preview_and_suggest_write_images(tmp_path: Path) -> None:
    r = api.structure_preview("0601", 400, 300, 250, tmp_path)
    assert r.ok, r.errors
    names = {Path(a).name for a in r.artifacts}
    assert {"0601_body_dieline.png", "0601_end_dieline.png", "0601_3d.png"} <= names
    s = api.structures_suggest(uses=["运输"], limit=2, dims=(400, 300, 250), out=tmp_path / "s")
    assert len(s.data["suggestions"]) == 2 and all(
        "preview" in row for row in s.data["suggestions"]
    )


def test_practicality_check_reports_closure_needs_and_maturity(tmp_path: Path) -> None:
    rsc = api.structure_check(
        "0201", dims=(400, 300, 250), needs=["reclosable", "no_tape"], load_kg=8
    )
    assert rsc.ok and rsc.data["maturity"] == "calibrated" and not rsc.data["all_needs_met"]
    assert {w.id for w in rsc.warnings} == {"FIT"}
    assert any("承重" in a for a in rsc.data["advice"])
    mailer = api.structure_check("0427", dims=(240, 170, 70), needs=["reclosable", "no_tape"])
    assert mailer.ok and mailer.data["all_needs_met"] and not mailer.warnings
    custom = api.structure_check(
        recipe={"body": {"base": "rect"}, "top": {"kind": "slope"},
                "bottom": {"kind": "autolock", "autolock_form": "web"}},
        dims=(400, 300, 250), material="flute_b",
    )  # fmt: skip
    assert custom.ok and custom.data["maturity"] == "custom"
    assert any("非标准" in a for a in custom.data["advice"])
    st, params = api.structure_params("0930", None, None, 300, 200, 150)
    assert api.init_job(tmp_path / "j", st, params, "flute_b").ok
    kit = api.structure_check(directory=tmp_path / "j", needs=["single"])
    assert kit.ok and kit.warnings and kit.warnings[0].id == "FIT"


def test_combos_are_found_by_description_and_build(tmp_path: Path) -> None:
    from pacxio.engine import combos

    e = catalog.get("tear_display_tray")
    assert e is not None and e.catalog == "组合" and e.maturity == "custom" and e.no_tape
    assert "自定义" in e.as_dict()["maturity_zh"]
    for words in (
        "撕开 展示盖 头卡",
        "展示 开窗 礼盒",
        "零食 礼盒",
    ):  # 用户的说法命中关键词，排第一
        picks = catalog.suggest(query=words, limit=3)
        assert picks and picks[0][0].code == "tear_display_tray", words
    assert catalog.default_material("tear_display_tray") == "sbs_350"
    assert catalog.default_material("0201") == "flute_b"
    show = api.structure_show("tear_display_tray", 150, 150, 40)
    assert show.ok and show.data["recipe"]["top"]["display_lid"] == "tab"
    style, params = api.structure_params("tear_display_tray", None, None, 150, 150, 40)  # type: ignore[misc]
    assert api.init_job(
        tmp_path / "j", style, params, catalog.default_material("tear_display_tray")
    ).ok
    check = api.structure_check("tear_display_tray", dims=(150, 150, 40), needs=["no_tape"])
    assert check.ok and check.data["usable"] and check.data["all_needs_met"]
    assert set(combos.COMBOS) <= {x.code for x in catalog.search(series="C")}
