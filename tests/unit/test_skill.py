"""Skill（规格 15 节）：跨 agent 兼容的 frontmatter、引用文件、脚本、文档里的命令都真实存在。"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pacxio.cli import app

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skill" / "packaging-prepress"
SKILL_MD = SKILL / "SKILL.md"
ENGINE_BIN = Path(sys.executable).parent / "pacxio"


def split_frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def test_frontmatter_has_only_name_and_description_and_meets_both_agents_rules() -> None:
    fm, _ = split_frontmatter()
    assert set(fm) == {"name", "description"}  # 跨 agent 兼容：不用其它字段
    name, desc = str(fm["name"]), str(fm["description"])
    assert name == SKILL.name == "packaging-prepress"
    # Claude 的约束：≤64 字符，只含小写字母 / 数字 / 连字符，不含保留词
    assert len(name) <= 64 and re.fullmatch(r"[a-z0-9-]+", name)
    assert "claude" not in name and "anthropic" not in name
    assert 0 < len(desc) <= 1024 and not re.search(
        r"</?[a-zA-Z][^>]*>", desc
    )  # 非空、≤1024、无 XML 标签
    # description 要同时说清"做什么"和"什么时候用"，以及不用于什么（Codex 据此隐式触发）
    assert "dieline" in desc and "Use when" in desc and "Not for" in desc
    assert "reverse tuck end" in desc and "straight tuck end" in desc  # 如实列出当前支持的盒型
    # 要出概念图 / 效果图时必须能触发；"Not for"里不能把它们排除掉（曾经自相矛盾过）
    for word in ("mockup", "concept image"):
        assert word in desc.split("Not for")[0] and word not in desc.split("Not for")[1], word


def test_referenced_files_exist_and_font_script_matches() -> None:
    _, body = split_frontmatter()
    refs = set(re.findall(r"`((?:references|scripts)/[\w./-]+)`", body))
    assert {"references/box-styles.md", "references/visual-prompts.md", "references/factory-profile.md",
            "references/troubleshooting.md", "references/prepress-rules.md", "references/fonts.md",
            "references/mockup.md", "references/concept.md"} <= refs  # fmt: skip
    for r in refs:
        assert (SKILL / r).exists(), r
    # 全部 references 都被 SKILL.md 引用（没有孤立文件）
    on_disk = {f"references/{p.name}" for p in (SKILL / "references").glob("*.md")}
    assert on_disk <= refs
    link = SKILL / "scripts" / "fetch_fonts.py"
    assert link.is_file()
    assert link.read_bytes() == (ROOT / "tools" / "fetch_fonts.py").read_bytes()
    if link.is_symlink():
        assert link.resolve() == (ROOT / "tools" / "fetch_fonts.py").resolve()
    assert body.count("\n") < 500  # 正文保持精简（详细内容放 references，按需加载）


def test_codex_metadata_is_valid() -> None:
    cfg = yaml.safe_load((SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    assert set(cfg) <= {"interface", "policy", "dependencies"}
    assert cfg["interface"]["display_name"] and cfg["interface"]["short_description"]
    assert "$packaging-prepress" in cfg["interface"]["default_prompt"]  # Codex 用 $技能名 显式调用
    assert cfg["policy"]["allow_implicit_invocation"] is True


def run_check_env(*args: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "check_env.py"), *args],
        capture_output=True, text=True, env=env, cwd=ROOT, check=False,
    )  # fmt: skip


@pytest.mark.skipif(not ENGINE_BIN.exists(), reason="没有 pacxio 可执行文件")
def test_check_env_reports_ready_environment_as_json() -> None:
    p = run_check_env("--json", env_extra={"PACXIO_BIN": str(ENGINE_BIN)})
    out = json.loads(p.stdout)
    assert p.returncode == (0 if out["ok"] else 3)
    assert out["engine"]["command"] == [str(ENGINE_BIN)]
    assert set(out["image_sources"]) == {"openai_api_key", "codex_generated_images"}
    assert isinstance(
        out["image_sources"]["openai_api_key"], bool
    )  # 只报告有没有，绝不输出 key 本身


def test_check_env_never_prints_the_api_key(tmp_path: Path) -> None:
    secret = "sk-very-secret-value"
    p = run_check_env(
        "--json", env_extra={"OPENAI_API_KEY": secret, "PACXIO_BIN": str(tmp_path / "nope")}
    )
    assert secret not in p.stdout and secret not in p.stderr
    assert json.loads(p.stdout)["image_sources"]["openai_api_key"] is True


def test_check_env_without_engine_exits_3_with_install_guidance(tmp_path: Path) -> None:
    p = run_check_env(env_extra={"PACXIO_BIN": str(tmp_path / "nope")})
    assert p.returncode == 3
    assert "uv tool install" in p.stdout and "没有安装" in p.stdout  # conftest 设了 PACXIO_LANG=zh
    en = run_check_env(env_extra={"PACXIO_BIN": str(tmp_path / "nope"), "PACXIO_LANG": "en"})
    assert en.returncode == 3 and "is not installed" in en.stdout


def test_check_env_script_never_installs_anything() -> None:
    """CLAUDE.md：Skill 运行时不做 pip install。只看真正的子进程调用，不看注释和说明文字。"""
    import ast

    tree = ast.parse((SKILL / "scripts" / "check_env.py").read_text(encoding="utf-8"))
    banned = {"pip", "install", "uv", "add", "sync", "download"}
    calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = getattr(node.func.value, "id", "")
            if owner == "subprocess" or (owner == "os" and node.func.attr in {"system", "popen"}):
                calls += 1
                strings = {
                    c.value
                    for c in ast.walk(node)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                }
                assert not (strings & banned), f"子进程调用里出现了安装类参数：{strings & banned}"
    assert calls >= 2  # 确认真的检查到了脚本里的子进程调用


# ---------------- 文档里的命令必须真实存在 ----------------

runner = CliRunner()


def doc_text() -> str:
    """SKILL.md 正文和全部 references：命令写在哪个文件里都要真实存在。"""
    _, body = split_frontmatter()
    refs = sorted((SKILL / "references").glob("*.md"))
    return "\n".join([body, *(p.read_text(encoding="utf-8") for p in refs)])


def doc_commands() -> list[list[str]]:
    body = doc_text()
    cmds: list[list[str]] = []
    for block in re.findall(r"```bash\n(.*?)```", body, re.S):
        joined = re.sub(r"\\\n\s*", " ", block)
        for line in joined.splitlines():
            line = line.strip()
            if line.startswith("pacxio "):
                cmds.append(shlex.split(line))
    # 正文里的行内代码，如 `pacxio visual import jobs/<名字> --codex-latest ...`
    for span in re.findall(r"`(pacxio [^`]+)`", body):
        cmds.append(shlex.split(span.replace("...", "").replace("\\", "")))
    return cmds


def test_every_command_and_option_in_skill_md_exists_in_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "300")  # 帮助文字不折行，选项名不会被拆开
    cmds = doc_commands()
    assert len(cmds) >= 12
    seen = set()
    for tokens in cmds:
        path: list[str] = []
        for t in tokens[1:]:
            if t.startswith("-") or "/" in t or "<" in t:
                break
            path.append(t)
        res = runner.invoke(app, [*path, "--help"])
        assert res.exit_code == 0, f"文档里的命令不存在：{' '.join(path)}"
        for opt in {t for t in tokens if t.startswith("--")}:
            assert opt in res.output, f"`{' '.join(path)}` 没有选项 {opt}"
        seen.add(" ".join(path))
    assert {"job init", "panels", "render", "art add", "finish add", "font add", "preflight", "export",
            "visual import", "visual gen", "visual prepare"} <= seen  # fmt: skip


def test_command_table_lists_every_cli_command() -> None:
    """命令速查表在 references/commands.md（SKILL.md 瘦身后，）。"""
    table = (SKILL / "references" / "commands.md").read_text(encoding="utf-8")
    for name in ("env check", "next", "styles list", "job init", "panels", "visual import / gen / prepare",
                 "art add", "font add / list", "mockup", "approve", "finish add", "render", "preflight",
                 "export", "concept gen / import / list / choose"):  # fmt: skip
        assert f"`{name}`" in table, name


# ---------------- 按 SKILL.md 的工作流端到端走一遍 ----------------

from tests.unit.conftest import FakeApi, png_bytes  # noqa: E402


def cli(*args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    res = runner.invoke(app, [*args, "--json"])
    return res.exit_code, json.loads(res.output)


@pytest.mark.pro
def test_documented_workflow_runs_end_to_end(
    tmp_path: Path,
    api: type[FakeApi],
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """规格 M3 验收的可自动化部分：一句需求对应的整条流程（出图用本地假服务，不花钱、不联网）。

    "80×50×180 的反插盒，白卡 350g，正面烫金 Logo，茶饮风格"
    """
    import base64

    api.body = {
        "data": [{"b64_json": base64.b64encode(png_bytes((1024, 1536), (28, 90, 60))).decode()}]
    }
    J = str(tmp_path / "tea")
    (tmp_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><circle cx="20" cy="20" r="19"/></svg>'
    )
    # 3. 建 job → 面映射 → 预览
    assert cli("job", "init", J, "--style", "rte", "--L", "80", "--W", "50", "--H", "180",
               "--basis", "inner", "--material", "sbs_350")[0] == 0  # fmt: skip
    code, out = cli("panels", J)
    front = next(p for p in out["data"]["panels"] if p["id"] == "front")
    assert code == 0 and front["px"]["w"] == 951
    assert cli("render", J)[0] == 0
    # 4. 出图 → 预处理（用 prepare 的输出）
    code, out = cli("visual", "gen", J, "--prompt", "山茶花，深墨绿 #1F4D3A，茶饮风格", "--name", "hero",
                    "--role", "hero", "--size", "951x2132")  # fmt: skip
    assert code == 0, out
    code, out = cli(
        "visual", "prepare", J, "hero", "--role", "hero", "--target-mm", "70,110", "--check-text"
    )
    assert code == 0 and out["data"]["asset"] == "assets/hero.prepared.png"
    assert out["data"]["effective_ppi"] >= 299
    # 5. 排版与工艺
    assert cli("art", "add", J, "--kind", "fill", "--color", "6,3,18,0", "--id", "bg")[0] == 0
    assert cli("art", "add", J, "--kind", "image", "--source", "assets/hero.prepared.png", "--panel", "front",
               "--box", "5,40,70,110")[0] == 0  # fmt: skip
    assert cli("art", "add", J, "--kind", "text", "--panel", "front", "--text", "山茶花 · 手作茶点",
               "--font", "SourceHanSansSC-Bold", "--size-pt", "18", "--box", "5,155,70,15",
               "--align", "center", "--color", "0,0,0,100")[0] == 0  # fmt: skip
    assert cli("finish", "add", J, "--type", "foil_gold", "--source", str(tmp_path / "logo.svg"),
               "--panel", "front", "--box", "25,15,30,24")[0] == 0  # fmt: skip
    # 6. 印前检查通过
    code, out = cli("preflight", J)
    assert code == 0, out["errors"]
    # 7. 导出，并且警告如实
    code, out = cli("export", J, "--proof")
    assert code == 0, out["errors"]
    pack, archive = Path(out["data"]["pack_dir"]), Path(out["data"]["archive_dir"])
    assert len(list(pack.iterdir())) == 3  # 生产文件、规格书、刀模 DXF
    assert {p.name for p in archive.iterdir()} >= {
        "proof_1to1.pdf", "dieline.pdf", "dieline.cf2", "finish_foil_gold.pdf",
        "preflight_report.json", "preflight_report.pdf", "preview.png", "job.json",
    }  # fmt: skip
    warned = {w["id"] for w in out["warnings"]}
    # SKILL.md 要求如实转告的几项；方向和配置档经打样 / 工厂确认（），不再出现
    assert {"X02", "DXF"} <= warned and not {"ORIENT", "PROFILE"} & warned
    # 出图记录可追溯，且 key 没有落盘
    meta = json.loads((Path(J) / "assets/hero.meta.json").read_text())
    assert (
        meta["provider"] == "openai"
        and "无文字" not in meta["full_prompt"]
        and "no text" in meta["full_prompt"].lower()
    )
    for f in Path(J).rglob("*"):
        if f.is_file() and f.suffix in {".json", ".png", ".pdf", ".svg"}:
            assert b"sk-test-secret" not in f.read_bytes(), f


def test_skill_tells_the_agent_never_to_bypass_a_font_license_refusal() -> None:
    """授权由用户负责：被拒绝时 agent 不能自己加 --accept-license-risk。"""
    _, body = split_frontmatter()
    fonts = (SKILL / "references" / "fonts.md").read_text(encoding="utf-8")
    assert "do not add `--accept-license-risk` yourself" in body
    assert "Never add it yourself" in fonts and "clearly says" in fonts


def test_skill_orders_confirmation_before_export_and_forbids_self_approval() -> None:
    """流程：先确认刀版确认图再导出（3D 效果图只看）；agent 不能替用户确认。"""
    _, body = split_frontmatter()
    stages = body.split("## Stage map")[1]
    order = ["| Main-view concepts", "| Back view", "| Final design from the flats",
             "| Dieline confirmation image", "| 3D mockups", "| Preflight", "| Export"]  # fmt: skip
    pos = [stages.index(h) for h in order]
    assert pos == sorted(pos)  # 需求 → 概念图 → 正式设计 → 刀版确认图 → 3D → 检查 → 导出
    assert "pacxio next" in body  # 每一轮先问引擎现在到哪一步
    assert "flat --confirm" not in body  # 2026-10-22 起没有这一步
    assert "the only gate before export" in body
    assert "never confirm for them" in body and "clearly says" in body
    assert "--require-approval" in body
    mock = (SKILL / "references" / "mockup.md").read_text(encoding="utf-8")
    assert "Never confirm for the user" in mock and "top" in mock and "direction" in mock
    # 必须出现的信息由用户负责：引擎不判断法规
    assert "who is responsible for it" in body and "cannot judge" in mock


def test_skill_uses_the_users_copy_as_given() -> None:
    """用户给了明确文案就照用：只查拼写语法（列建议由用户定），不判断宣称依据、不为此标待定（2026-10-22）。"""
    _, body = split_frontmatter()
    content = (SKILL / "references" / "content.md").read_text(encoding="utf-8")
    for text in (body, content):
        assert "Use the user's copy exactly as given" in text and "spelling" in text
        assert "original → suggestion" in text
        assert "judge whether claims are supported" in text
    assert "`pending` is only for information **the user has not given**" in content


def test_skill_keeps_concepts_out_of_print_and_rebuilds_from_them() -> None:
    _, body = split_frontmatter()
    concept = (SKILL / "references" / "concept.md").read_text(encoding="utf-8")
    assert "Concept images are never assets" in body and "rebuilt from the confirmed flats" in body
    assert "cannot reproduce" in body and "Not reproducible yet" in concept  # 差距要提前告诉用户
    assert "which one they choose" in body  # 选定方向也由用户决定
