"""Runtime selection and real local wheel/MCP transport; no live registry or user install.

The integration fixture replaces registry discovery and candidate installation with
two tiny locally built wheels. The production launcher, ready-file validation,
health checks and JSON-RPC processes remain real. Registry policy is tested in
test_updates.py; these fixtures are not evidence of a live PyPI update.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pacxio import __version__, launcher, updates


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "account"))
    for key in (
        launcher.PIN_ENV,
        launcher.BOOTSTRAP_ENV,
        launcher.FALLBACK_ENV,
        "PACXIO_NO_UPDATE",
    ):
        # main() writes PIN/BOOTSTRAP directly; register their original values for teardown.
        monkeypatch.setenv(key, "")
    return tmp_path


@pytest.fixture
def context(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    value = {
        "enabled": True,
        "reason": "synthetic_registry_install",
        "bootstrap_python": sys.executable,
        "bootstrap_prefix": str(Path(sys.prefix).resolve()),
        "uv_path": "test-uv",
        "current_version": __version__,
    }
    monkeypatch.setattr(updates, "eligibility", lambda: value)
    monkeypatch.setattr(updates, "spawn_check", lambda _: None)
    return value


@pytest.mark.parametrize("key,value", [("PACXIO_NO_UPDATE", "1"), (launcher.PIN_ENV, "fixed")])
def test_self_check_and_pinned_sessions_never_reenter_update_selection(
    isolated: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(updates, "eligibility", lambda: pytest.fail("Unexpected update check"))
    plan = launcher.choose_launch(["mcp", "serve"])
    assert plan["python"] == sys.executable and not plan["switch"]
    assert plan["context"] is None


@pytest.mark.parametrize(
    "reason",
    [
        "source_checkout",
        "custom_or_editable_source",
        "version_pinned",
        "closed_source_installation",
    ],
)
def test_ineligible_installation_never_selects_or_prepares_a_runtime(
    isolated: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    monkeypatch.setattr(updates, "eligibility", lambda: {"enabled": False, "reason": reason})
    monkeypatch.setattr(updates, "select_runtime", lambda _: pytest.fail("Unexpected selection"))
    monkeypatch.setattr(updates, "spawn_check", lambda _: pytest.fail("Unexpected preparation"))
    calls = []
    monkeypatch.setattr(launcher, "_run_local", lambda args: calls.append(args))
    launcher.main(["mcp", "serve"])
    assert calls == [["mcp", "serve"]]
    assert os.environ[launcher.PIN_ENV] == sys.prefix


def test_failed_health_check_keeps_the_installed_version(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        updates,
        "select_runtime",
        lambda _: {"python": "/candidate/python", "version": "9.0.0", "path": "/candidate"},
    )
    monkeypatch.setattr(updates, "health_check", lambda *args, **kwargs: False)
    plan = launcher.choose_launch(["mcp", "serve"])
    assert not plan["switch"] and plan["python"] == sys.executable
    assert plan["fallback"] == "runtime_health_check_failed"


def test_unreadable_update_state_keeps_the_installed_version(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_: dict[str, Any]) -> None:
        raise OSError("Synthetic unavailable state")

    monkeypatch.setattr(updates, "select_runtime", unavailable)
    plan = launcher.choose_launch(["mcp", "serve"])
    assert not plan["switch"] and plan["fallback"] == "update_state_unavailable"


def test_launch_snapshot_does_not_switch_to_a_newly_prepared_version(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    runtime = {"python": "/runtime/v2/bin/python", "version": "2.0.0", "path": "/runtime/v2"}

    def select(_: dict[str, Any]) -> dict[str, str]:
        events.append("select")
        return dict(runtime)

    def healthy(*args: Any, **kwargs: Any) -> bool:
        events.append("health")
        assert args == ("/runtime/v2/bin/python", "2.0.0") and kwargs == {"timeout_s": 5}
        return True

    def prepare(_: dict[str, Any]) -> None:
        events.append("prepare")
        runtime.update(python="/runtime/v3/bin/python", version="3.0.0", path="/runtime/v3")

    class ReplacedProcess(BaseException):
        pass

    def execute(python: str, argv: list[str], environment: dict[str, str]) -> None:
        events.append("exec")
        assert python == "/runtime/v2/bin/python"
        assert argv == [python, "-I", "-m", "pacxio", "mcp", "serve"]
        assert environment[launcher.PIN_ENV] == "/runtime/v2"
        assert json.loads(environment[launcher.BOOTSTRAP_ENV]) == context
        assert "PYTHONPATH" not in environment and "PYTHONHOME" not in environment
        raise ReplacedProcess

    monkeypatch.setenv("PYTHONPATH", "/synthetic/source")
    monkeypatch.setenv("PYTHONHOME", "/synthetic/home")
    monkeypatch.setattr(updates, "select_runtime", select)
    monkeypatch.setattr(updates, "health_check", healthy)
    monkeypatch.setattr(updates, "spawn_check", prepare)
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(ReplacedProcess):
        launcher.main(["mcp", "serve"])
    assert events == ["select", "health", "prepare", "exec"]


def test_exec_failure_falls_back_without_consuming_protocol_input(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = {"python": "/runtime/python", "version": "9.0.0", "path": "/runtime"}
    monkeypatch.setattr(updates, "select_runtime", lambda _: runtime)
    monkeypatch.setattr(updates, "health_check", lambda *args, **kwargs: True)

    def fail_exec(*args: Any) -> None:
        raise OSError("Synthetic exec failure")

    calls = []
    monkeypatch.setattr(os, "execve", fail_exec)
    monkeypatch.setattr(
        launcher, "_run_local", lambda args: calls.append((args, os.environ[launcher.PIN_ENV]))
    )
    launcher.main(["mcp", "serve"])
    assert calls == [(["mcp", "serve"], sys.prefix)]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [["--help"], ["next", "synthetic-job", "--json"]])
def test_cli_calls_do_not_start_background_installation(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    monkeypatch.setattr(updates, "select_runtime", lambda _: None)
    monkeypatch.setattr(updates, "spawn_check", lambda _: pytest.fail("CLI launched an installer"))
    calls = []
    monkeypatch.setattr(launcher, "_run_local", lambda args: calls.append(args))
    launcher.main(argv)
    assert calls == [argv]


def test_background_start_failure_does_not_break_mcp_launch(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(updates, "select_runtime", lambda _: None)

    def fail(_: dict[str, Any]) -> None:
        raise OSError("Synthetic background start failure")

    calls = []
    monkeypatch.setattr(updates, "spawn_check", fail)
    monkeypatch.setattr(launcher, "_run_local", lambda args: calls.append(args))
    launcher.main(["mcp", "serve"])
    assert calls == [["mcp", "serve"]] and capsys.readouterr().out == ""


def test_status_reads_versions_without_spawning_an_update(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = updates.state_dir()
    root.mkdir(parents=True)
    (root / "ready.json").write_text(
        json.dumps({"version": "9.0.0", "bootstrap_prefix": context["bootstrap_prefix"]})
    )
    (root / "status.json").write_text(
        json.dumps(
            {"status": "ready", "checked_at": 123, "bootstrap_prefix": context["bootstrap_prefix"]}
        )
    )
    monkeypatch.setattr(updates, "spawn_check", lambda _: pytest.fail("Status started an update"))
    monkeypatch.setattr(updates, "select_runtime", lambda _: {"version": "9.0.0"})
    status = launcher.update_status()
    assert status["running_version"] == __version__ and status["ready_version"] == "9.0.0"
    assert status["pending_restart"] and status["checked_at"] == 123


def test_invalid_ready_record_is_not_reported_as_prepared(context: dict[str, Any]) -> None:
    root = updates.state_dir()
    root.mkdir(parents=True)
    (root / "ready.json").write_text(
        json.dumps({"version": "9.0.0", "bootstrap_prefix": context["bootstrap_prefix"]})
    )
    (root / "status.json").write_text(
        json.dumps({"status": "ready", "bootstrap_prefix": context["bootstrap_prefix"]})
    )
    status = launcher.update_status()
    assert status["ready_version"] is None and not status["pending_restart"]
    assert status["status"] != "ready"


@pytest.mark.parametrize(
    "failure,reason", [("health", "runtime_health_check_failed"), ("exec", "runtime_start_failed")]
)
def test_launch_fallback_is_reported_as_failed_in_current_session(
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason: str,
) -> None:
    runtime = {"python": "/runtime/python", "version": "9.0.0", "path": "/runtime"}
    monkeypatch.setattr(updates, "select_runtime", lambda _: runtime)
    monkeypatch.setattr(updates, "health_check", lambda *args, **kwargs: failure == "exec")

    def fail_exec(*args: Any) -> None:
        raise OSError("Synthetic runtime unavailable")

    observed = []
    monkeypatch.setattr(os, "execve", fail_exec)
    monkeypatch.setattr(launcher, "_run_local", lambda _: observed.append(launcher.update_status()))
    launcher.main(["mcp", "serve"])
    assert len(observed) == 1
    assert observed[0]["status"] == "failed" and observed[0]["last_error"] == reason
    assert observed[0]["ready_version"] is None and not observed[0]["pending_restart"]


def _wheel(folder: Path, version: str) -> Path:
    """Build a dependency-free test distribution, not a pacxio production artifact."""
    server = """import json, os, sys
from pathlib import Path
from pacxio import __version__

def serve():
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {},
                      "serverInfo": {"name": "pacxio", "version": __version__}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "inputSchema": {"type": "object"}}
                                for name in ("job_next", "studio_wait", "pacxio_run")]}
        elif method == "resources/read":
            text = (Path(__file__).parent / "_skill/packaging-prepress/SKILL.md").read_text()
            result = {"contents": [{"uri": "pacxio://skill/SKILL.md", "mimeType": "text/markdown", "text": text}]}
        elif method == "ping":
            result = {"version": __version__, "pin": os.environ.get("PACXIO_RUNTIME_PIN")}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""
    dist = f"pacxio-{version}.dist-info"
    files = {
        "pacxio/__init__.py": f"__version__ = {version!r}\n",
        "pacxio/mcp_server.py": server,
        "pacxio/__main__.py": "from pacxio.launcher import main\nmain()\n",
        "pacxio/launcher.py": Path(launcher.__file__).read_text(),
        "pacxio/cli.py": "import sys\nfrom pacxio.mcp_server import serve\n\ndef main():\n    if '--help' in sys.argv:\n        print('Synthetic pacxio CLI help')\n    else:\n        serve()\n",
        "pacxio/_skill/packaging-prepress/SKILL.md": f"# Synthetic packaging skill {version}\nMarker: skill-{version}\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: pacxio\nVersion: {version}\nRequires-Python: >=3.11\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: pacxio-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, contents in files.items():
        raw = contents.encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(raw)}")
    files[f"{dist}/RECORD"] = "\n".join([*record, f"{dist}/RECORD,,"]) + "\n"
    wheel = folder / f"pacxio-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, contents in files.items():
            archive.writestr(path, contents)
    return wheel


def _rpc(
    process: subprocess.Popen[str], messages: queue.Queue[str], message: dict[str, Any]
) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    process.stdin.flush()
    try:
        response = json.loads(messages.get(timeout=5))
    except queue.Empty:
        pytest.fail("Runtime did not answer the MCP request")
    assert response["jsonrpc"] == "2.0" and response["id"] == message["id"]
    return response["result"]


def test_real_wheels_keep_current_session_fixed_then_load_new_skill(
    isolated: Path,
    context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv = os.environ.get("PACXIO_TEST_UV") or shutil.which("uv")
    if not uv:
        pytest.skip("Set PACXIO_TEST_UV to run the real offline wheel acceptance test")
    context["uv_path"] = uv
    wheels = {version: _wheel(isolated, version) for version in ("1.0.0", "2.0.0")}

    def install(ctx: dict[str, Any], path: Path, release: dict[str, str]) -> Path:
        python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
        }
        environment["UV_CACHE_DIR"] = str(isolated / "uv-cache")
        for command in (
            [
                uv,
                "venv",
                str(path),
                "--python",
                ctx["bootstrap_python"],
                "--no-project",
                "--no-config",
                "--no-python-downloads",
            ],
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-index",
                "--no-deps",
                "--no-config",
                str(wheels[release["version"]]),
            ],
        ):
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=environment,
                cwd=isolated,
                timeout=30,
            )
        return python

    monkeypatch.setattr(updates, "_install_candidate", install)
    monkeypatch.setattr(
        updates, "_latest_release", lambda: {"version": "1.0.0", "filename": wheels["1.0.0"].name}
    )
    updates.check_and_prepare(context)
    first_ready = json.loads((updates.state_dir() / "ready.json").read_text())
    assert first_ready["version"] == "1.0.0"
    assert updates.health_check(first_ready["python"], "1.0.0")
    evidence: dict[str, Any] = {"scope": "local fake wheels; registry boundary injected"}
    original_pointer = (updates.state_dir() / "ready.json").read_bytes()

    def offline() -> None:
        raise OSError("Synthetic offline registry")

    monkeypatch.setattr(updates, "_latest_release", offline)
    evidence["offline"] = updates.check_and_prepare(context)
    assert evidence["offline"]["status"] == "failed"
    assert (updates.state_dir() / "ready.json").read_bytes() == original_pointer

    def broken_install(*args: Any) -> Path:
        raise subprocess.CalledProcessError(1, "synthetic-installer")

    monkeypatch.setattr(
        updates, "_latest_release", lambda: {"version": "2.0.0", "filename": wheels["2.0.0"].name}
    )
    monkeypatch.setattr(updates, "_install_candidate", broken_install)
    evidence["installation_failure"] = updates.check_and_prepare(context)
    assert evidence["installation_failure"]["status"] == "failed"
    assert (updates.state_dir() / "ready.json").read_bytes() == original_pointer

    # The worker uses real preparation with a local wheel installer in a separate process.
    source = Path(launcher.__file__).resolve().parents[1]
    worker = isolated / "prepare.py"
    worker.write_text(f"""import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, {str(source)!r})
from pacxio import updates
context = json.loads({json.dumps(context)!r})
root = Path({str(isolated)!r})
def install(context, path, release):
    (root / "candidate-started").touch()
    deadline = time.monotonic() + 20
    while not (root / "allow-update").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Test did not release candidate installation")
        time.sleep(0.01)
    python = path / ({"Scripts/python.exe" if os.name == "nt" else "bin/python"!r})
    environment = dict(os.environ)
    environment["UV_CACHE_DIR"] = str(root / "uv-cache")
    for command in (
        [{uv!r}, "venv", str(path), "--python", context["bootstrap_python"], "--no-project", "--no-config", "--no-python-downloads"],
        [{uv!r}, "pip", "install", "--python", str(python), "--no-index", "--no-deps", "--no-config", {str(wheels["2.0.0"])!r}],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True, env=environment, cwd=root, timeout=30)
    return python
updates._latest_release = lambda: {{"version": "2.0.0", "filename": {wheels["2.0.0"].name!r}}}
updates._install_candidate = install
result = updates.check_and_prepare(context)
(root / "worker-result.json").write_text(json.dumps(result))
print(json.dumps(result), flush=True)
""")
    # Replace background spawning only to inject the local registry/installer fixture.
    # Runtime selection, validation, process replacement and MCP framing remain production code.
    harness = isolated / "launch.py"
    harness.write_text(
        "import json, subprocess, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from pacxio import launcher, updates\n"
        f"updates.eligibility = lambda: json.loads({json.dumps(context)!r})\n"
        f"root = Path({str(isolated)!r})\n"
        "def spawn(context):\n"
        "    if (root / 'worker-started').exists():\n        return\n"
        "    (root / 'worker-started').touch()\n"
        "    with (root / 'worker.log').open('w') as log:\n"
        f"        child = subprocess.Popen([sys.executable, {str(worker)!r}], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)\n"
        "    (root / 'worker-pid').write_text(str(child.pid))\n"
        "updates.spawn_check = spawn\n"
        "launcher.main(['mcp', 'serve'])\n"
    )
    environment = dict(os.environ)
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        launcher.PIN_ENV,
        launcher.BOOTSTRAP_ENV,
        launcher.FALLBACK_ENV,
        "PACXIO_NO_UPDATE",
    ):
        environment.pop(key, None)
    running: list[tuple[subprocess.Popen[str], threading.Thread, queue.Queue[str]]] = []
    (isolated / "pacxio.py").write_text("raise RuntimeError('Unexpected import from caller cwd')\n")

    def start() -> tuple[subprocess.Popen[str], queue.Queue[str]]:
        process = subprocess.Popen(
            [sys.executable, str(harness)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            cwd=isolated,
        )
        messages: queue.Queue[str] = queue.Queue()
        output_file = isolated / f"session-{len(running) + 1}.stdout.jsonl"

        def read() -> None:
            assert process.stdout is not None
            with output_file.open("w") as output:
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    messages.put(line)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        running.append((process, reader, messages))
        return process, messages

    try:
        first, first_messages = start()
        evidence["initial"] = _rpc(first, first_messages, {"id": 1, "method": "initialize"})
        assert evidence["initial"]["serverInfo"]["version"] == "1.0.0"
        assert (
            "skill-1.0.0"
            in _rpc(first, first_messages, {"id": 2, "method": "resources/read"})["contents"][0][
                "text"
            ]
        )

        deadline = time.monotonic() + 10
        while not (isolated / "candidate-started").exists():
            assert time.monotonic() < deadline, "Background preparation did not start"
            time.sleep(0.01)
        evidence["during_install"] = _rpc(first, first_messages, {"id": 3, "method": "ping"})
        assert evidence["during_install"]["version"] == "1.0.0"
        assert (updates.state_dir() / "ready.json").read_bytes() == original_pointer
        (isolated / "allow-update").touch()
        deadline = time.monotonic() + 30
        while not (isolated / "worker-result.json").exists():
            assert time.monotonic() < deadline, "Background preparation did not finish"
            time.sleep(0.01)
        prepared = json.loads((isolated / "worker-result.json").read_text())
        assert prepared["status"] == "ready", prepared
        second_ready = json.loads((updates.state_dir() / "ready.json").read_text())
        assert second_ready["version"] == "2.0.0" and second_ready["path"] != first_ready["path"]
        evidence["prepared"] = prepared
        evidence["running_after_update"] = _rpc(first, first_messages, {"id": 4, "method": "ping"})
        assert evidence["running_after_update"]["version"] == "1.0.0"
        assert evidence["running_after_update"]["pin"] == first_ready["path"]
        assert (
            "skill-1.0.0"
            in _rpc(first, first_messages, {"id": 5, "method": "resources/read"})["contents"][0][
                "text"
            ]
        )

        second, second_messages = start()
        evidence["next_session"] = _rpc(second, second_messages, {"id": 1, "method": "initialize"})
        evidence["next_skill"] = _rpc(
            second, second_messages, {"id": 2, "method": "resources/read"}
        )
        assert evidence["next_session"]["serverInfo"]["version"] == "2.0.0"
        assert "skill-2.0.0" in evidence["next_skill"]["contents"][0]["text"]
    finally:
        if (isolated / "worker-pid").exists() and not (isolated / "worker-result.json").exists():
            try:
                os.kill(int((isolated / "worker-pid").read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process, reader, messages in running:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=1)
            assert process.returncode == 0
            while not messages.empty():
                assert json.loads(messages.get_nowait())["jsonrpc"] == "2.0"
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        (isolated / "launcher-evidence.json").write_text(json.dumps(evidence, indent=2))
