"""给人看的文字：默认英文，PACXIO_LANG=zh 换中文（）；代码里用到的英文都有中文。"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pacxio import messages

SRC = Path(__file__).resolve().parents[2] / "src" / "pacxio"
# The closed package (absent from the open repo) uses the same tables.
PRO = Path(__file__).resolve().parents[2] / "pro" / "src" / "pacxio_pro"
CJK = re.compile(r"[㐀-鿿＀-￯　-〿]")

WALK = """
import json, sys
import typer.main
from typer.testing import CliRunner
from pacxio.cli import app
runner, out = CliRunner(), {}
def walk(cmd, path):
    out[" ".join(path)] = runner.invoke(app, [*path, "--help"], terminal_width=200).output
    for name, sub in getattr(cmd, "commands", {}).items():
        walk(sub, [*path, name])
walk(typer.main.get_command(app), [])
print(json.dumps(out))
"""


def _help_pages(lang: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != messages.LANG_ENV}
    if lang:
        env[messages.LANG_ENV] = lang
    p = subprocess.run(
        [sys.executable, "-c", WALK], env=env, capture_output=True, text=True, check=True
    )
    return dict(json.loads(p.stdout))


def test_help_is_english_by_default() -> None:
    pages = _help_pages(None)
    assert len(pages) > 50
    bad = {
        k: [ln for ln in v.splitlines() if CJK.search(ln)][:2]
        for k, v in pages.items()
        if CJK.search(v)
    }
    assert not bad, bad


def test_help_switches_to_chinese() -> None:
    pages = _help_pages("zh")
    assert "包装印前引擎" in pages[""] and "创建 job" in pages["job"]


def test_lang_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for value, want in (
        ("", "en"),
        ("en", "en"),
        ("zh", "zh"),
        ("zh_CN.UTF-8", "zh"),
        ("ZH-Hans", "zh"),
    ):
        monkeypatch.setenv(messages.LANG_ENV, value)
        assert messages.lang() == want
    monkeypatch.setenv(messages.LANG_ENV, "zh")
    assert messages.t("Job folder") == "job 目录"
    assert messages.t("Workbench stopped (process {pid})", pid=7) == "工作台已停止（进程 7）"
    assert messages.t("not in the table {x}", x=1) == "not in the table 1"
    monkeypatch.setenv(messages.LANG_ENV, "en")
    assert messages.t("Workbench stopped (process {pid})", pid=7) == "Workbench stopped (process 7)"


def test_job_init_reports_errors_and_preserves_output_across_languages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pacxio.cli import app

    runner = CliRunner()
    errors = {}
    for language in ("en", "zh"):
        monkeypatch.setenv(messages.LANG_ENV, language)
        job = tmp_path / language / "box"
        command = ["job", "init", str(job), "--W", "50", "--H", "100", "--json"]
        rejected = runner.invoke(app, [*command, "--L", "10"])
        assert rejected.exit_code == 1
        assert not job.exists()
        errors[language] = json.loads(rejected.output)["errors"][0]
        assert runner.invoke(app, [*command, "--L", "80"]).exit_code == 0
    assert errors["en"]["id"] == errors["zh"]["id"] == "S03"
    assert "below the minimum" in errors["en"]["message"]
    assert "小于" in errors["zh"]["message"]
    assert CJK.search(errors["zh"]["fix"]) and not CJK.search(errors["en"]["fix"])
    for filename in ("job.json", "dieline.svg"):
        assert (tmp_path / "en" / "box" / filename).read_bytes() == (
            tmp_path / "zh" / "box" / filename
        ).read_bytes()


def test_suggest_localizes_reasons_without_changing_recommendations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pacxio.cli import app

    runner = CliRunner()
    command = ["structures", "suggest", "--use", "ecommerce", "--reclosable", "--limit", "2"]
    results = {}
    for language in ("en", "zh"):
        monkeypatch.setenv(messages.LANG_ENV, language)
        result = runner.invoke(app, [*command, "--json"])
        assert result.exit_code == 0
        results[language] = json.loads(result.output)["data"]["suggestions"]
        assert len(results[language]) == 2
        text = runner.invoke(app, command).output
        assert ("Closure:" if language == "en" else "怎么合上：") in text
        if language == "en":
            assert not CJK.search(result.output + text)
    assert [r["structure"]["code"] for r in results["en"]] == [
        r["structure"]["code"] for r in results["zh"]
    ]


def test_style_list_localizes_display_names_and_preserves_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pacxio.cli import app

    runner = CliRunner()
    data = {}
    for language in ("en", "zh"):
        monkeypatch.setenv(messages.LANG_ENV, language)
        result = runner.invoke(app, ["styles", "list", "--json"])
        assert result.exit_code == 0, result.output
        data[language] = json.loads(result.output)["data"]
    english = data["en"]["materials"]["sbs_350"]
    chinese = data["zh"]["materials"]["sbs_350"]
    assert english["name"] == "SBS 350 g/m²"
    assert chinese["name"] == "白卡 350g"
    assert {**english, "name": chinese["name"]} == chinese
    styles_en = {s["id"]: s for s in data["en"]["styles"]}
    styles_zh = {s["id"]: s for s in data["zh"]["styles"]}
    assert styles_en.keys() == styles_zh.keys()
    assert styles_en["rte"]["name"] == "Reverse tuck end"
    assert styles_zh["rte"]["name"] == "反向插舌盒"
    assert styles_en["rte"]["name_zh"] == styles_zh["rte"]["name_zh"] == "反向插舌盒"
    strip = lambda ps: {k: {**v, "description": None} for k, v in ps.items()}  # noqa: E731
    assert strip(styles_en["rte"]["params"]) == strip(styles_zh["rte"]["params"])
    assert styles_en["rte"]["params"]["W"]["description"] == "Width (side width)"
    assert styles_zh["rte"]["params"]["W"]["description"] == "宽（侧面宽）"
    for s in data["en"]["styles"]:  # 参数说明英文模式下没有中文
        assert not any(re.search(r"[一-鿿]", p.get("description") or "") for p in s["params"].values()), s["id"]
    assert "pending verification" in styles_en["rte"]["refs"]["ecma"]
    assert "待核对" in styles_zh["rte"]["refs"]["ecma"]


def test_structure_check_localizes_closure_and_unmet_need() -> None:
    from pacxio import api

    with messages.use_language("en"):
        report = api.structure_check("0201", dims=(400, 300, 250), needs=["reclosable"])
        assert report.ok and report.warnings[0].id == "FIT"
        assert not CJK.search(report.model_dump_json())
        assert "flaps" in report.data["closure"]
    with messages.use_language("zh"):
        report = api.structure_check("0201", dims=(400, 300, 250), needs=["reclosable"])
        assert "能反复开合" in report.warnings[0].message


def test_category_reference_is_english_in_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from pacxio.cli import app

    monkeypatch.setenv(messages.LANG_ENV, "en")
    result = CliRunner().invoke(app, ["conventions", "show", "paint", "--json"])
    assert result.exit_code == 0, result.output
    assert not CJK.search(result.output)
    assert json.loads(result.output)["data"]["faces_zh"]["Barcode"] == ["Side panel"]


def _t_calls() -> list[tuple[str, int, str]]:
    out = []
    files = sorted(SRC.rglob("*.py")) + (sorted(PRO.rglob("*.py")) if PRO.is_dir() else [])
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {
            item.asname or item.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "pacxio.messages"
            for item in node.names
            if item.name == "t"
        }
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in names
                and n.args
            ):
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.append((p.name, n.lineno, a.value))
    return out


def test_every_message_has_chinese_and_matching_fields() -> None:
    zh = messages.table("zh")
    calls = _t_calls()
    assert calls
    missing = [c for c in calls if c[2] not in zh]
    assert not missing, missing[:10]
    for en, cn in zh.items():
        assert not CJK.search(en), en
        fields = lambda s: sorted(re.findall(r"(?<!\{)\{([a-z_]\w*)", s))  # noqa: E731
        assert fields(en) == fields(cn), (en, cn)
