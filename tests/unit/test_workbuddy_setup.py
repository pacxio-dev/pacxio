"""WorkBuddy setup, doctor, scope fallback, conflict guard, and reversible removal."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pacxio import mcp_install, workbuddy_setup
from pacxio.mcp_server import TOOLS
from pacxio.plugin_setup import setup


def _ok_check(entry: dict, root: Path) -> dict:
    return {"ok": True, "version": "0.0.8", "tools": len(TOOLS), "resources": 12}


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(workbuddy_setup, "_check", _ok_check)
    return home


def test_workbuddy_preview_and_install_uses_production_cloud_and_detach(
    tmp_path: Path, fake_home: Path
) -> None:
    project = tmp_path / "wb project"
    project.mkdir()

    preview = setup("workbuddy", yes=False, fonts=False, project=project)
    assert preview.ok and not preview.data["written"]
    assert not (project / ".workbuddy").exists()
    assert not (project / ".codebuddy").exists()

    installed = setup("workbuddy", yes=True, fonts=False, project=project)
    assert installed.ok and installed.data["written"]
    cfg = json.loads((project / ".workbuddy/mcp.json").read_text(encoding="utf-8"))
    entry = cfg["mcpServers"]["pacxio"]
    assert entry["cwd"] == str(project.resolve())
    assert entry["env"] == {"PACXIO_STUDIO_DETACH": "1"}
    assert "PACXIO_CLOUD_URL" not in entry["env"]
    skill_dir = project / ".codebuddy/skills/packaging-prepress"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "SKILL.local.md").is_file()
    assert (skill_dir / "references/workbuddy-mcp-tools.md").is_file()
    assert (skill_dir / "references/workbuddy-images.md").is_file()
    with zipfile.ZipFile(project / ".workbuddy/pacxio-packaging-prepress.zip") as zf:
        assert "packaging-prepress/SKILL.md" in zf.namelist()

    doc = mcp_install.doctor(project=project, client="workbuddy")
    assert doc.ok and doc.data["mcp_scope"] == "project"
    assert doc.data["server"]["tools"] == len(TOOLS)


def test_workbuddy_test_cloud_url_enables_isolated_mode_and_validates_loopback(
    tmp_path: Path, fake_home: Path
) -> None:
    project = tmp_path / "wb-iso"
    project.mkdir()
    bad = setup("workbuddy", yes=False, fonts=False, project=project, test_cloud_url="https://api.pacxio.dev")
    assert not bad.ok

    ok = setup("workbuddy", yes=True, fonts=False, project=project, test_cloud_url="http://127.0.0.1:8787")
    assert ok.ok
    env = ok.data["entry"]["env"]
    assert env["PACXIO_CLOUD_URL"] == "http://127.0.0.1:8787"
    assert env["PACXIO_CLOUD_TOKEN"] == ""
    assert env["PACXIO_AUTO_UPDATE"] == "0"
    assert env["PACXIO_STUDIO_DETACH"] == "1"


def test_workbuddy_preserves_other_servers_guards_edits_and_removes_cleanly(
    tmp_path: Path, fake_home: Path
) -> None:
    project = tmp_path / "wb-edit"
    (project / ".workbuddy").mkdir(parents=True)
    mcp_file = project / ".workbuddy/mcp.json"
    mcp_file.write_text(json.dumps({"mcpServers": {"other": {"command": "o"}}}), encoding="utf-8")

    assert setup("workbuddy", yes=True, fonts=False, project=project).ok
    data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert set(data["mcpServers"]) == {"other", "pacxio"}

    # Editing installed skill blocks overwrite or removal until resolved
    skill_md = project / ".codebuddy/skills/packaging-prepress/SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    skill_md.write_text(original + "\ncustom note\n", encoding="utf-8")
    blocked = setup("workbuddy", yes=True, fonts=False, project=project, remove=True)
    assert not blocked.ok

    skill_md.write_text(original, encoding="utf-8")
    removed = setup("workbuddy", yes=True, fonts=False, project=project, remove=True)
    assert removed.ok and removed.data["written"]
    after = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert set(after["mcpServers"]) == {"other"}
    assert not (project / ".codebuddy/skills/packaging-prepress").exists()
    assert not (project / ".workbuddy/pacxio-install.json").exists()


def test_workbuddy_user_scope_and_symlink_and_flag_guards(
    tmp_path: Path, fake_home: Path
) -> None:
    project = tmp_path / "wb-user"
    project.mkdir()
    r = setup("workbuddy", yes=True, fonts=False, project=project, scope="user")
    assert r.ok and r.data["mcp_scope"] == "user"
    user_cfg = fake_home / ".workbuddy/mcp.json"
    assert "pacxio" in json.loads(user_cfg.read_text(encoding="utf-8"))["mcpServers"]
    # Refuses switching scope while installed
    assert not setup("workbuddy", yes=False, fonts=False, project=project, scope="project").ok
    # Removes cleanly from user scope
    assert setup("workbuddy", yes=True, fonts=False, project=project, remove=True).ok
    assert not user_cfg.exists()

    # Non-workbuddy flags are rejected
    assert not setup("antigravity", yes=False, project=project).ok
    assert not setup("claude-code", yes=False, test_cloud_url="http://127.0.0.1:8000").ok
    assert not mcp_install.doctor(project=project, client="codex").ok
