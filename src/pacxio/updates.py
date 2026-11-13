"""Prepare verified plugin runtimes without modifying an installed tool environment.

Only a plain, unpinned PyPI ``uv tool install pacxio`` is eligible. A running
session keeps its selected interpreter; the ready pointer is for future sessions.
All network and installation work belongs to a detached, bounded worker.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PYPI_JSON = "https://pypi.org/pypi/pacxio/json"
PYPI_INDEX = "https://pypi.org/simple"
QUERY_TIMEOUT_S = 3
INSTALL_TIMEOUT_S = 180
PREPARE_HEALTH_TIMEOUT_S = 30
MAX_METADATA_BYTES = 10 * 1024 * 1024


def state_dir() -> Path:
    return Path(os.environ.get("PACXIO_HOME", str(Path.home() / ".pacxio"))).expanduser().absolute() / "runtime"


def _version(value: str) -> Any:
    from packaging.version import Version

    return Version(value)


def _disabled_by_environment() -> bool:
    return (os.environ.get("PACXIO_NO_UPDATE") == "1"
            or os.environ.get("PACXIO_AUTO_UPDATE", "1").lower() in ("0", "false", "off"))


def eligibility() -> dict[str, Any]:
    """Fail closed for development, custom-source and non-uv installations."""
    from pacxio import __version__

    result: dict[str, Any] = {
        "enabled": False, "reason": "unsupported_installation",
        "bootstrap_python": sys.executable, "bootstrap_prefix": str(Path(sys.prefix).resolve()),
        "uv_path": None, "current_version": __version__,
    }

    def reject(reason: str) -> dict[str, Any]:
        return {**result, "reason": reason}

    if _disabled_by_environment():
        return reject("disabled_by_environment")
    try:
        dist = importlib.metadata.distribution("pacxio")
        result["current_version"] = dist.version
        if dist.read_text("direct_url.json") is not None:
            return reject("custom_or_editable_source")
        prefix = Path(sys.prefix).resolve()
        spec = importlib.util.find_spec("pacxio")
        if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(prefix):
            return reject("source_checkout")
        if not Path(str(dist.locate_file(""))).resolve().is_relative_to(prefix):
            return reject("external_distribution")
        if importlib.util.find_spec("pacxio_pro") is not None:
            return reject("closed_source_installation")
        if prefix == Path(sys.base_prefix).resolve():
            return reject("not_a_tool_environment")
        uv = shutil.which("uv")
        if not uv:
            return reject("uv_not_found")
        result["uv_path"] = str(Path(uv).absolute())
        tool_dir = subprocess.run(
            [uv, "tool", "dir", "--no-config"], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=2, check=True,
        ).stdout.strip()
        if prefix != (Path(tool_dir) / "pacxio").resolve():
            return reject("not_uv_managed")
        receipt = tomllib.loads((prefix / "uv-receipt.toml").read_text())
        tool = receipt["tool"]
        requirements = tool.get("requirements", [])
        if len(requirements) != 1:
            return reject("additional_requirements")
        requirement = requirements[0]
        if requirement.get("name") != "pacxio":
            return reject("unexpected_requirement")
        if any(key in requirement for key in ("editable", "path", "directory", "git", "url")):
            return reject("custom_or_editable_source")
        if requirement.get("specifier") or requirement.get("version"):
            return reject("version_pinned")
        if requirement.get("extras") or set(requirement) - {"name", "specifier", "extras"}:
            return reject("custom_requirement")
        # A simple official installation has no resolver overrides or custom indexes.
        if any(tool.get(key) for key in ("constraints", "overrides", "excludes", "options")):
            return reject("custom_install_options")
        if _version(dist.version).is_prerelease or _version(dist.version).is_devrelease:
            return reject("development_version")
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError):
        return reject("installation_unverifiable")
    return {**result, "enabled": True, "reason": "official_uv_tool"}


def _read_ready() -> dict[str, Any] | None:
    try:
        value = json.loads((state_dir() / "ready.json").read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _valid_ready(record: dict[str, Any], context: dict[str, Any]) -> bool:
    try:
        version = _version(record["version"])
        folder = Path(record["path"])
        python = Path(record["python"])
        expected = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        return bool(
            not version.is_prerelease and not version.is_devrelease
            and version > _version(context["current_version"])
            and Path(record["bootstrap_prefix"]).resolve() == Path(context["bootstrap_prefix"]).resolve()
            and folder.is_dir() and not folder.is_symlink()
            and folder.resolve().parent == (state_dir() / "versions").resolve()
            and python.absolute() == expected.absolute() and python.is_file()
            and (folder / "pyvenv.cfg").is_file()
        )
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return False


def select_runtime(context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Select a verified candidate, never a downgrade or a foreign installation."""
    if _disabled_by_environment():
        return None
    context = eligibility() if context is None else context
    record = _read_ready()
    if not context["enabled"] or not record or not _valid_ready(record, context):
        return None
    return record


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".update-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _status(context: dict[str, Any], status: str, **details: Any) -> dict[str, Any]:
    result = {"status": status, "checked_at": time.time(), "available_version": None, "reason": None,
              "bootstrap_prefix": context.get("bootstrap_prefix"), **details}
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        _atomic_json(state_dir() / "status.json", result)
    except OSError:
        pass
    return result


@contextmanager
def _update_lock(root: Path) -> Iterator[bool]:
    with (root / "update.lock").open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _latest_release() -> dict[str, str] | None:
    from packaging.specifiers import SpecifierSet
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    request = urllib.request.Request(PYPI_JSON, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=QUERY_TIMEOUT_S) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("PyPI metadata exceeded the size limit")
    metadata = json.loads(raw)
    supported = set(sys_tags())
    python_version = ".".join(str(x) for x in sys.version_info[:3])
    versions = []
    for name, files in metadata.get("releases", {}).items():
        try:
            version = _version(name)
        except ValueError:
            continue
        if not version.is_prerelease and not version.is_devrelease:
            versions.append((version, name, files))
    for _, name, files in sorted(versions, reverse=True):
        for file in files:
            if file.get("packagetype") != "bdist_wheel" or file.get("yanked"):
                continue
            requirement = file.get("requires_python")
            if requirement and python_version not in SpecifierSet(requirement):
                continue
            try:
                package, wheel_version, _, tags = parse_wheel_filename(file["filename"])
            except (ValueError, KeyError):
                continue
            if package != "pacxio" or wheel_version != _version(name) or not tags & supported:
                continue
            return {"version": name, "filename": file["filename"]}
    return None


def _child_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("UV_", "PIP_")) and key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env.update(PACXIO_NO_UPDATE="1", PACXIO_CLOUD="0", UV_HTTP_TIMEOUT="8", UV_HTTP_RETRIES="0")
    return env


def _install_candidate(context: dict[str, Any], path: Path, release: dict[str, str]) -> Path:
    """Install only into our newly allocated directory, from the official index."""
    uv = context["uv_path"]
    env = _child_env()
    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    commands = [
        [uv, "venv", str(path), "--python", context["bootstrap_python"],
         "--no-project", "--no-config", "--no-python-downloads"],
        [uv, "pip", "install", "--python", str(python), f"pacxio=={release['version']}",
         "--default-index", PYPI_INDEX, "--only-binary", ":all:", "--no-config",
         "--no-sources", "--no-python-downloads", "--no-progress"],
    ]
    for command in commands:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=INSTALL_TIMEOUT_S,
                              env=env, check=True, cwd=path.parent)
        if done.stdout:
            print(done.stdout[-4000:], file=sys.stderr, flush=True)
    return python


def health_check(python: Path | str, version: str, timeout_s: float = 5) -> bool:
    """Probe the candidate without starting another update or touching user jobs."""
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "pacxio-runtime-check", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
         "params": {"uri": "pacxio://skill/SKILL.md"}},
    ]
    probe = '''import importlib.metadata as metadata
import sys
import pacxio.launcher
assert metadata.version("pacxio") == sys.argv[1]
requirements = [r for d in metadata.distributions() for r in (d.requires or [])]
if requirements:
    from packaging.requirements import Requirement
    for item in requirements:
        requirement = Requirement(item)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            assert requirement.specifier.contains(metadata.version(requirement.name), prereleases=True)
from pacxio.mcp_server import serve
serve()
'''
    try:
        started = time.monotonic()
        env = _child_env()
        cwd = state_dir() if state_dir().is_dir() else Path.home()
        cli = subprocess.run([str(python), "-I", "-m", "pacxio", "--help"],
                             capture_output=True, text=True, timeout=timeout_s, env=env, cwd=cwd)
        if cli.returncode or not cli.stdout.strip():
            return False
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            return False
        done = subprocess.run(
            [str(python), "-I", "-c", probe, version],
            input="\n".join(json.dumps(item) for item in messages) + "\n",
            capture_output=True, text=True, timeout=remaining, env=env, cwd=cwd,
        )
        if done.returncode:
            return False
        replies = {item["id"]: item for item in (json.loads(line) for line in done.stdout.splitlines()) if "id" in item}
        info = replies[1]["result"]["serverInfo"]
        names = {tool["name"] for tool in replies[2]["result"]["tools"]}
        contents = replies[3]["result"]["contents"]
        return bool(info["name"] == "pacxio" and _version(info["version"]) == _version(version)
                    and {"job_next", "studio_wait", "pacxio_run"} <= names
                    and any(item.get("uri") == "pacxio://skill/SKILL.md" and item.get("text", "").strip() for item in contents))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, subprocess.SubprocessError):
        return False


def check_and_prepare(context: dict[str, Any]) -> dict[str, Any]:
    """Prepare a new version and publish its pointer only after a successful probe."""
    if not context.get("enabled"):
        return _status(context, "disabled", reason=context.get("reason", "unsupported_installation"))
    candidate: Path | None = None
    available: str | None = None
    try:
        root = state_dir()
        root.mkdir(parents=True, exist_ok=True)
        with _update_lock(root) as acquired:
            if not acquired:
                return {"status": "busy"}
            _status(context, "checking")
            release = _latest_release()
            if release is None:
                return _status(context, "current", reason="no_compatible_stable_wheel")
            available = release["version"]
            current = _version(context["current_version"])
            previous = _read_ready()
            if previous and _valid_ready(previous, context) and health_check(previous["python"], previous["version"]):
                current = max(current, _version(previous["version"]))
            if _version(release["version"]) <= current:
                return _status(context, "current", version=str(current), available_version=available)
            versions = root / "versions"
            versions.mkdir(exist_ok=True)
            candidate = Path(tempfile.mkdtemp(prefix=release["version"] + "-", dir=versions))
            _status(context, "installing", available_version=available)
            python = _install_candidate(context, candidate, release)
            # Native dependencies may take several seconds on their first import.
            # This probe runs only in the worker; startup checks keep their 5s budget.
            if not health_check(python, release["version"], timeout_s=PREPARE_HEALTH_TIMEOUT_S):
                raise ValueError("Candidate failed the MCP and Skill health check")
            record = {"python": str(python), "version": release["version"], "path": str(candidate),
                      "bootstrap_prefix": context["bootstrap_prefix"], "checked_at": time.time()}
            _atomic_json(root / "ready.json", record)
            candidate = None  # Preserve verified environments; active sessions may still use them.
            return _status(context, "ready", available_version=available, **record)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, subprocess.SubprocessError) as exc:
        return _status(context, "failed", reason=type(exc).__name__, available_version=available)
    finally:
        if candidate is not None:
            shutil.rmtree(candidate, ignore_errors=True)


def spawn_check(context: dict[str, Any]) -> None:
    """Start a detached updater; never wait for network or emit protocol output."""
    if _disabled_by_environment() or not context.get("enabled"):
        return
    path: Path | None = None
    try:
        root = state_dir()
        root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="context-", suffix=".json", dir=root)
        path = Path(name)
        with os.fdopen(descriptor, "w") as output:
            json.dump(context, output)
        options: dict[str, Any] = {"start_new_session": True}
        if sys.platform == "win32":
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        with (root / "update.log").open("a") as log:
            env = {key: value for key, value in os.environ.items()
                   if key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
            subprocess.Popen(
                [context["bootstrap_python"], "-I", "-m", "pacxio.updates", "--prepare", str(path)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                cwd=root, env=env, **options,
            )
        path = None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _status(context, "failed", reason="worker_start_failed")
        try:
            with (state_dir() / "update.log").open("a") as log:
                log.write(json.dumps({"status": "failed", "reason": "worker_start_failed",
                                      "error_type": type(exc).__name__}) + "\n")
        except OSError:
            pass  # Updates must never prevent a usable installed version from starting.
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--prepare":
        return
    path = Path(sys.argv[2])
    if path.parent.resolve() != state_dir().resolve() or not path.name.startswith("context-"):
        return
    try:
        context = json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)
    current = eligibility()
    keys = ("bootstrap_python", "bootstrap_prefix", "uv_path", "current_version")
    if not current["enabled"] or any(context.get(key) != current.get(key) for key in keys):
        print(json.dumps(_status(current, "disabled", reason="installation_changed")))
        return
    print(json.dumps(check_and_prepare(context)), flush=True)


if __name__ == "__main__":
    _main()
