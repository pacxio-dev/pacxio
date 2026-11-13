"""Updates never replace an active environment or delay MCP startup with network work."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from packaging.version import Version

from pacxio import updates


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PACXIO_NO_UPDATE", raising=False)
    monkeypatch.delenv("PACXIO_AUTO_UPDATE", raising=False)
    return {"enabled": True, "reason": "official_uv_tool", "bootstrap_python": sys.executable,
            "bootstrap_prefix": sys.prefix, "uv_path": str(tmp_path / "uv"), "current_version": "1.0.0"}


@pytest.fixture
def official_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context: dict[str, Any]) -> Path:
    tool_root = tmp_path / "tools"
    prefix = tool_root / "pacxio"
    source = prefix / "lib" / "site-packages" / "pacxio" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('')
    receipt = prefix / "uv-receipt.toml"
    receipt.write_text('[tool]\nrequirements = [{name = "pacxio"}]\n')
    monkeypatch.setattr(updates.sys, "prefix", str(prefix))
    monkeypatch.setattr(updates.sys, "base_prefix", str(tmp_path / "base-python"))
    monkeypatch.setattr(updates.importlib.metadata, "distribution", lambda _: SimpleNamespace(
        version="1.0.0", read_text=lambda _: None, locate_file=lambda _: source.parent.parent))
    monkeypatch.setattr(updates.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(source)) if name == "pacxio" else None)
    monkeypatch.setattr(updates.shutil, "which", lambda _: context["uv_path"])
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=str(tool_root)))
    return prefix


def test_plain_uv_tool_is_eligible(official_install: Path) -> None:
    result = updates.eligibility()
    assert result["enabled"] and result["bootstrap_prefix"] == str(official_install)


def test_pro_added_after_original_install_disables_updates(official_install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = updates.importlib.util.find_spec
    monkeypatch.setattr(updates.importlib.util, "find_spec", lambda name: SimpleNamespace(origin="/extra/pacxio_pro/__init__.py") if name == "pacxio_pro" else original(name))
    assert updates.eligibility()["reason"] == "closed_source_installation"


@pytest.mark.parametrize(("requirements", "reason"), [
    ('[{name="pacxio", editable="/source"}]', "custom_or_editable_source"),
    ('[{name="pacxio", path="/source"}]', "custom_or_editable_source"),
    ('[{name="pacxio", git="https://example.invalid/repo"}]', "custom_or_editable_source"),
    ('[{name="pacxio", url="https://example.invalid/a.whl"}]', "custom_or_editable_source"),
    ('[{name="pacxio", specifier="==1.0.0"}]', "version_pinned"),
    ('[{name="pacxio"},{name="pacxio-pro",editable="/pro"}]', "additional_requirements"),
    ('[{name="pacxio", extras=["custom"]}]', "custom_requirement"),
])
def test_custom_tool_receipts_are_disabled(official_install: Path, requirements: str, reason: str) -> None:
    (official_install / "uv-receipt.toml").write_text(f'[tool]\nrequirements={requirements}\n')
    result = updates.eligibility()
    assert not result["enabled"] and result["reason"] == reason


def test_source_venv_and_missing_uv_are_disabled(official_install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates.importlib.util, "find_spec", lambda _: SimpleNamespace(origin="/source/pacxio/__init__.py"))
    assert updates.eligibility()["reason"] == "source_checkout"
    monkeypatch.setattr(updates.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(official_install / "pacxio.py")) if name == "pacxio" else None)
    monkeypatch.setattr(updates.shutil, "which", lambda _: None)
    assert updates.eligibility()["reason"] == "uv_not_found"
    monkeypatch.setattr(updates.shutil, "which", lambda _: "/uv")
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="/different-tools"))
    assert updates.eligibility()["reason"] == "not_uv_managed"


def test_direct_url_and_custom_index_are_disabled(official_install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(updates.importlib.metadata, "distribution", lambda _: SimpleNamespace(
            version="1.0.0", read_text=lambda _: '{"url":"https://example.invalid/a.whl"}'))
        assert updates.eligibility()["reason"] == "custom_or_editable_source"
    with (official_install / "uv-receipt.toml").open("a") as output:
        output.write('[tool.options]\nindex-url="https://example.invalid/simple"\n')
    assert updates.eligibility()["reason"] == "custom_install_options"


def _candidate(context: dict[str, Any], version: str = "1.1.0") -> dict[str, Any]:
    folder = updates.state_dir() / "versions" / f"{version}-test"
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake interpreter")
    (folder / "pyvenv.cfg").write_text("home=/base")
    record = {"python": str(python), "version": version, "path": str(folder),
              "bootstrap_prefix": context["bootstrap_prefix"], "checked_at": 1}
    (updates.state_dir() / "ready.json").write_text(json.dumps(record))
    return record


def test_selection_rejects_downgrade_foreign_install_and_escape(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "eligibility", lambda: context)
    record = _candidate(context)
    assert updates.select_runtime() == record
    for change in ({"version": "0.9.0"}, {"version": "2.0.0rc1"},
                   {"bootstrap_prefix": "/different-install"}, {"path": "/outside/runtime"}):
        (updates.state_dir() / "ready.json").write_text(json.dumps({**record, **change}))
        assert updates.select_runtime() is None
    (updates.state_dir() / "ready.json").write_text("not json")
    assert updates.select_runtime() is None


def test_supplied_context_avoids_duplicate_uv_query_and_honors_opt_out(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    record = _candidate(context)
    monkeypatch.setattr(updates, "eligibility", lambda: pytest.fail("context already verified"))
    assert updates.select_runtime(context) == record
    monkeypatch.setenv("PACXIO_NO_UPDATE", "1")
    assert updates.select_runtime(context) is None
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: pytest.fail("opted out"))
    updates.spawn_check(context)


@pytest.mark.parametrize("variable", ["PACXIO_AUTO_UPDATE", "PACXIO_NO_UPDATE"])
def test_environment_opt_out_disables_selection(official_install: Path, monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    context = updates.eligibility()
    _candidate(context)
    monkeypatch.setenv(variable, "0" if variable == "PACXIO_AUTO_UPDATE" else "1")
    assert not updates.eligibility()["enabled"]
    assert updates.select_runtime() is None


def test_pypi_selects_compatible_stable_non_yanked_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    def wheel(version: str, **change: Any) -> dict[str, Any]:
        return {"packagetype": "bdist_wheel", "filename": f"pacxio-{version}-py3-none-any.whl", **change}

    data = {"releases": {
        "1.0.0": [wheel("1.0.0")], "1.1.0": [wheel("1.1.0", requires_python=">=3.11")],
        "1.2.0": [wheel("1.2.0", yanked=True)], "1.3.0": [wheel("1.3.0", requires_python=">=99")],
        "1.4.0": [{"packagetype": "sdist", "filename": "pacxio-1.4.0.tar.gz"}],
        "2.0.0rc1": [wheel("2.0.0rc1")], "invalid": [],
    }}
    def respond(request: Any, timeout: int) -> io.BytesIO:
        assert request.full_url == updates.PYPI_JSON and timeout <= 3
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", respond)
    assert updates._latest_release()["version"] == "1.1.0"  # type: ignore[index]


@pytest.mark.parametrize("failure", ["offline", "install", "health"])
def test_failure_preserves_last_ready(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    record = _candidate(context)
    ready = updates.state_dir() / "ready.json"
    original = ready.read_bytes()
    def release() -> dict[str, str]:
        if failure == "offline":
            raise TimeoutError("simulated connection timeout")
        return {"version": "1.2.0"}
    def install(ctx: dict[str, Any], path: Path, release: dict[str, str]) -> Path:
        assert ctx == context and path.parent == updates.state_dir() / "versions"
        if failure == "install":
            raise subprocess.TimeoutExpired("uv", 180)
        return path / "bin/python"
    monkeypatch.setattr(updates, "_latest_release", release)
    monkeypatch.setattr(updates, "_install_candidate", install)
    monkeypatch.setattr(updates, "health_check", lambda python, version, **kwargs: version == "1.1.0")
    result = updates.check_and_prepare(context)
    assert result["status"] == "failed" and ready.read_bytes() == original
    assert list((updates.state_dir() / "versions").iterdir()) == [Path(record["path"])]
    assert json.loads((updates.state_dir() / "status.json").read_text())["status"] == "failed"


def test_ready_published_only_after_health_check(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    original = _candidate(context)
    def install(ctx: dict[str, Any], path: Path, release: dict[str, str]) -> Path:
        assert json.loads((updates.state_dir() / "ready.json").read_text()) == original
        assert json.loads((updates.state_dir() / "status.json").read_text())["status"] == "installing"
        python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir()
        python.write_text("candidate")
        (path / "pyvenv.cfg").write_text("home=/base")
        return python
    def health(python: Path | str, version: str, **kwargs: Any) -> bool:
        assert json.loads((updates.state_dir() / "ready.json").read_text()) == original
        if version == "1.2.0":
            assert kwargs["timeout_s"] == 30  # Cold imports happen in the detached worker.
        else:
            assert not kwargs  # Existing ready runtime retains the normal 5s default.
        return True
    monkeypatch.setattr(updates, "_latest_release", lambda: {"version": "1.2.0"})
    monkeypatch.setattr(updates, "_install_candidate", install)
    monkeypatch.setattr(updates, "health_check", health)
    result = updates.check_and_prepare(context)
    assert result["status"] == "ready" and result["version"] == "1.2.0"
    assert Path(original["python"]).is_file()
    assert json.loads((updates.state_dir() / "ready.json").read_text())["python"] == result["python"]


def test_no_downgrade_or_reinstallation_when_ready_is_newer(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _candidate(context, "1.3.0")
    monkeypatch.setattr(updates, "_latest_release", lambda: {"version": "1.2.0"})
    monkeypatch.setattr(updates, "health_check", lambda *a: True)
    monkeypatch.setattr(updates, "_install_candidate", lambda *a: pytest.fail("must not install"))
    result = updates.check_and_prepare(context)
    assert result["status"] == "current" and Version(result["version"]) == Version("1.3.0")


def test_nonblocking_lock_prevents_duplicate_checks(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    root = updates.state_dir()
    root.mkdir(parents=True)
    monkeypatch.setattr(updates, "_latest_release", lambda: pytest.fail("locked worker must not query"))
    with updates._update_lock(root) as acquired:
        assert acquired
        assert updates.check_and_prepare(context) == {"status": "busy"}
    with updates._update_lock(root) as acquired:
        assert acquired


def test_other_process_cannot_start_an_update_while_locked(context: dict[str, Any]) -> None:
    root = updates.state_dir()
    root.mkdir(parents=True)
    code = '''import json, sys
from pacxio import updates
def forbidden():
    raise AssertionError("A competing process must not query PyPI")
updates._latest_release = forbidden
print(json.dumps(updates.check_and_prepare(json.loads(sys.argv[1]))))
'''
    with updates._update_lock(root) as acquired:
        assert acquired
        done = subprocess.run([sys.executable, "-c", code, json.dumps(context)],
                              capture_output=True, text=True, timeout=10, check=True)
        assert json.loads(done.stdout) == {"status": "busy"}


def test_installer_uses_only_candidate_and_official_index(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setenv("UV_INDEX", "https://example.invalid/private")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://example.invalid/private")
    monkeypatch.setenv("PYTHONPATH", "/source")
    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs))
        assert not any(key in kwargs["env"] for key in ("UV_INDEX", "PIP_EXTRA_INDEX_URL", "PYTHONPATH"))
        assert kwargs["env"]["PACXIO_NO_UPDATE"] == "1"
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(updates.subprocess, "run", run)
    path = tmp_path / "candidate"
    updates._install_candidate(context, path, {"version": "1.2.0"})
    assert calls[0][0][1:3] == ["venv", str(path)]
    command = calls[1][0]
    assert "pacxio==1.2.0" in command and updates.PYPI_INDEX in command
    assert "--only-binary" in command and "--no-config" in command and "--no-sources" in command
    assert "upgrade" not in command and "tool" not in command


def test_health_check_requires_version_tools_and_current_skill(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [
        {"id": 1, "result": {"serverInfo": {"name": "pacxio", "version": "1.2.0"}}},
        {"id": 2, "result": {"tools": [{"name": name} for name in ("job_next", "studio_wait", "pacxio_run")]}},
        {"id": 3, "result": {"contents": [{"uri": "pacxio://skill/SKILL.md", "text": "Workflow instructions"}]}},
    ]
    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        assert kwargs["env"]["PACXIO_NO_UPDATE"] == "1"
        assert 0 < kwargs["timeout"] <= 5
        assert command[1] == "-I"
        if command[2] == "-m":
            return SimpleNamespace(returncode=0, stdout="Usage: pacxio [OPTIONS] COMMAND")
        assert "import pacxio.launcher" in command[3]
        assert "requirement.specifier.contains" in command[3]
        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(item) for item in replies))
    monkeypatch.setattr(updates.subprocess, "run", run)
    assert updates.health_check(sys.executable, "1.2.0")
    assert not updates.health_check(sys.executable, "1.3.0")
    replies[2]["result"]["contents"][0]["text"] = ""  # type: ignore[index]
    assert not updates.health_check(sys.executable, "1.2.0")


def test_spawn_detaches_and_never_shares_mcp_streams(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = []
    def popen(command: list[str], **kwargs: Any) -> None:
        calls.append(command)
        assert command[1:4] == ["-I", "-m", "pacxio.updates"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == kwargs["stderr"] and kwargs["stdout"].name.endswith("update.log")
        assert kwargs["close_fds"]
        assert json.loads(Path(command[-1]).read_text()) == context
    monkeypatch.setattr(updates.subprocess, "Popen", popen)
    updates.spawn_check(context)
    assert len(calls) == 1 and capsys.readouterr().out == ""


def test_spawn_failure_keeps_baseline_usable(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("missing interpreter")
    monkeypatch.setattr(updates.subprocess, "Popen", fail)
    updates.spawn_check(context)
    assert not list(updates.state_dir().glob("context-*.json"))
    assert capsys.readouterr().out == ""
    assert json.loads((updates.state_dir() / "status.json").read_text())["reason"] == "worker_start_failed"
