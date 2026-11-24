"""标准法规矢量符号库（PAO 开口罐、莫比乌斯回收环、EU ℮ 标）单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.symbols import (
    SYMBOLS,
    add_symbol_asset,
    list_symbols,
    pao_text_box,
    render_symbol_svg,
    resolve_symbol_id,
)


def test_symbol_catalog_aliases_and_svg_structure() -> None:
    items = list_symbols()
    assert {i["id"] for i in items} == set(SYMBOLS)
    assert resolve_symbol_id("pao-jar") == "pao"
    assert resolve_symbol_id("recycle") == "mobius"
    assert resolve_symbol_id("estimated") == "e-mark"
    assert resolve_symbol_id("unknown") is None

    for sid in ("pao", "mobius", "e-mark"):
        svg, spec = render_symbol_svg(sid, color="#4a4a4e")
        assert spec.id == sid
        assert svg.startswith("<svg ") and "viewBox=" in svg
        assert "<text" not in svg and "<image" not in svg and "<linearGradient" not in svg
        assert "#4a4a4e" in svg

    with pytest.raises(ValueError, match="Unknown regulatory symbol"):
        render_symbol_svg("nonexistent")
    with pytest.raises(ValueError, match="#RRGGBB"):
        render_symbol_svg("pao", color="red")


def test_pao_text_box_centers_inside_jar_body() -> None:
    tx, ty, tw, th = pao_text_box((10.0, 20.0, 14.0, 12.0))
    assert 10.0 < tx < 15.0 and tx + tw < 24.0
    assert 20.0 < ty < 24.0 and ty + th < 32.0


def test_cli_visual_symbol_writes_print_valid_svg_and_passes_svgart(tmp_path: Path) -> None:
    run = CliRunner()
    d = tmp_path / "marks"
    r0 = run.invoke(app, ["job", "init", str(d), "--style", "rte", "--L", "80", "--W", "40", "--H", "120"])
    assert r0.exit_code == 0, r0.output

    for sid in ("pao", "mobius", "e-mark"):
        res = run.invoke(app, ["visual", "symbol", str(d), sid, "--name", f"sym-{sid}", "--json"])
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)["data"]
        assert data["symbol"] == sid
        svg_path = d / data["asset"]
        assert svg_path.is_file()
        assert svg_path.with_suffix(".meta.json").is_file()

        # 闭源 SVG 解析器直接验收：无禁止标签、无渐变/透明、能完整产出几何图形
        svgart = pytest.importorskip("pacxio_pro.layout.svgart")
        art = svgart.load_svg_art(svg_path)
        assert art.shapes, f"{sid} produced no vector shapes"

    bad = add_symbol_asset(d, "not-a-symbol")
    assert not bad.ok and bad.errors[0].id == "ARG"
