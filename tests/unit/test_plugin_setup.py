"""pacxio setup：生成的插件（两种 agent 的清单、Skill、MCP 命令）和只预览不动文件。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pacxio.mcp_install import server_command
from pacxio.plugin_setup import MARKER, build_plugin, plugin_home, setup


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import pacxio.plugin_setup as ps

    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / ".pacxio"))
    monkeypatch.setattr(ps, "_home_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "_claude_dir", lambda: tmp_path / ".claude")
    monkeypatch.setattr(ps, "_codex_dir", lambda: tmp_path / ".codex")
    return tmp_path


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_build_plugin_writes_both_manifests(home: Path) -> None:
    info = build_plugin()
    root = Path(info["dir"])
    assert root == plugin_home()
    claude_market = _load(root / ".claude-plugin" / "marketplace.json")
    assert (
        claude_market["name"] == "pacxio" and "description" not in claude_market
    )  # claude 校验不收根上的 description
    assert claude_market["plugins"][0]["source"] == "./pacxio"
    codex_market = _load(root / ".agents" / "plugins" / "marketplace.json")
    assert codex_market["plugins"][0]["source"] == {"source": "local", "path": "./pacxio"}
    plugin = root / "pacxio"
    assert _load(plugin / ".claude-plugin" / "plugin.json")["name"] == "pacxio"
    codex = _load(plugin / ".codex-plugin" / "plugin.json")
    assert codex["mcpServers"] == "./.mcp.json" and codex["skills"] == "./skills/"
    cmd, args = server_command()
    assert _load(plugin / ".mcp.json")["mcpServers"]["pacxio"] == {"command": cmd, "args": args}
    skill = plugin / "skills" / "packaging-prepress"
    assert (skill / "SKILL.md").is_file()
    fonts = skill / "scripts" / "fetch_fonts.py"
    assert fonts.is_file() and not fonts.is_symlink()  # 软链接复制成真文件
    assert (root / MARKER).is_file()


def test_plugin_entry_stays_stable_while_full_workflow_changes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pacxio import mcp_server

    source = home / "source-skill"
    (source / "references").mkdir(parents=True)
    header = '---\nname: packaging-prepress\ndescription: "Design packaging"\n---\n'
    first = header + "\n# Complete workflow\n\nFirst workflow.\n"
    (source / "SKILL.md").write_text(first)
    (source / "references" / "commands.md").write_text("First commands.\n")
    monkeypatch.setattr(mcp_server, "skill_dir", lambda: source)
    plugin = Path(build_plugin()["plugin"]) / "skills" / "packaging-prepress"
    entry = (plugin / "SKILL.md").read_text()
    assert entry.startswith(header)
    assert "pacxio://skill/SKILL.md" in entry and "resources/read" in entry
    assert "pacxio://skill/references/<filename>.md" in entry
    assert "SKILL.local.md" in entry
    assert "First workflow." not in entry
    assert (plugin / "SKILL.local.md").read_text() == first
    assert (source / "SKILL.md").read_text() == first
    resource = mcp_server.handle(
        {"method": "resources/read", "params": {"uri": "pacxio://skill/SKILL.md"}}
    )
    assert resource is not None and resource["contents"][0]["text"] == first

    second = header + "\n# Complete workflow\n\nUpdated workflow.\n"
    (source / "SKILL.md").write_text(second)
    (source / "references" / "commands.md").write_text("Updated commands.\n")
    build_plugin()
    assert (plugin / "SKILL.md").read_text() == entry
    assert (plugin / "SKILL.local.md").read_text() == second
    assert (plugin / "references" / "commands.md").read_text() == "Updated commands.\n"
    assert (source / "SKILL.md").read_text() == second
    resource = mcp_server.handle(
        {"method": "resources/read", "params": {"uri": "pacxio://skill/SKILL.md"}}
    )
    assert resource is not None and resource["contents"][0]["text"] == second


def test_invalid_skill_does_not_replace_existing_plugin(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pacxio import mcp_server

    root = Path(build_plugin()["dir"])
    marker = (root / MARKER).read_text()
    source = home / "invalid-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("No frontmatter\n")
    monkeypatch.setattr(mcp_server, "skill_dir", lambda: source)
    with pytest.raises(ValueError, match="frontmatter"):
        build_plugin()
    assert (root / MARKER).read_text() == marker
    assert (root / "pacxio" / "skills" / "packaging-prepress" / "SKILL.md").is_file()


def test_build_plugin_rebuilds_own_dir_but_not_others(home: Path) -> None:
    root = Path(build_plugin()["dir"])
    (root / "pacxio" / "stale.txt").write_text("x")
    build_plugin()
    assert not (root / "pacxio" / "stale.txt").exists()
    other = home / "other"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        build_plugin(other)
    assert (other / "keep.txt").is_file()


def test_setup_without_yes_only_lists_steps(home: Path) -> None:
    r = setup("claude-code")
    assert r.ok
    assert any("claude plugin marketplace add" in s for s in r.data["steps"])
    assert any("claude plugin install pacxio@pacxio" in s for s in r.data["steps"])
    assert not plugin_home().exists()
    assert any("--yes" in w.message for w in r.warnings)


def test_setup_updates_when_already_installed(home: Path) -> None:
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "known_marketplaces.json").write_text(json.dumps({"pacxio": {}}))
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"pacxio@pacxio": []}})
    )
    steps = setup("claude-code").data["steps"]
    assert "claude plugin marketplace update pacxio" in steps
    assert "claude plugin update pacxio@pacxio" in steps
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[marketplaces.pacxio]\nsource = "x"\n')
    steps = setup("codex").data["steps"]
    assert not any("marketplace add" in s for s in steps)
    assert "codex plugin add pacxio@pacxio" in steps


def test_setup_warns_about_old_installs(home: Path) -> None:
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "packaging-prepress").symlink_to(home)
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"pacxio": {"command": "x"}}}))
    msgs = [w.fix or "" for w in setup("claude-code").warnings]
    assert any("claude mcp remove -s user pacxio" in m for m in msgs)
    assert any("packaging-prepress" in m for m in msgs)
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[mcp_servers.pacxio]\ncommand = "x"\n')
    assert any("mcp_servers.pacxio" in (w.fix or "") for w in setup("codex").warnings)


def test_setup_without_agent_uses_every_detected_cli(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pacxio.plugin_setup as ps

    found = {"claude": "/x/claude", "codex": "/x/codex"}
    monkeypatch.setattr(ps.shutil, "which", lambda name: found.get(name))
    r = setup()
    assert r.ok and r.data["agents"] == ["claude-code", "codex"] and r.data["detected"]
    steps = r.data["steps"]
    assert any(s.startswith("claude plugin install") for s in steps)
    assert "codex plugin add pacxio@pacxio" in steps
    assert sum(s.startswith("生成插件") for s in steps) == 1  # 插件只生成一份
    del found["claude"]
    assert setup().data["agents"] == ["codex"]
    found.clear()
    r = setup()
    assert not r.ok and r.exit_code == 3 and "claude" in (r.errors[0].fix or "")


def test_setup_rejects_unknown_agent(home: Path) -> None:
    r = setup("claude-desktop")
    assert not r.ok and r.errors[0].id == "ARG"
