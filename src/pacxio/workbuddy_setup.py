"""Project-scoped WorkBuddy installation, diagnostics and reversible removal."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from pacxio import __version__
from pacxio.messages import t, use_language
from pacxio.model.common import Issue, Result

NAME = "pacxio"
SKILL = "packaging-prepress"
CONFIG = Path(".workbuddy/mcp.json")
SKILL_PATH = Path(".codebuddy/skills") / SKILL
RECORD = Path(".workbuddy/pacxio-install.json")
BUNDLE = Path(".workbuddy/pacxio-packaging-prepress.zip")
LOCK = Path(".workbuddy/.pacxio-setup.lock")


def _config_path(root: Path, scope: str) -> Path:
    return (Path.home() if scope == "user" else root) / CONFIG


def _safe_paths(root: Path, scope: str = "project") -> None:
    for rel in (CONFIG, SKILL_PATH, RECORD, BUNDLE, LOCK, Path(".workbuddy/pacxio-backups")):
        for part in (rel, *rel.parents):
            if (root / part).is_symlink():
                raise ValueError(
                    t("WorkBuddy installation paths must not be symlinks: {path}", path=root / part)
                )
    if scope == "user":
        for path in (Path.home() / CONFIG, Path.home() / LOCK):
            for part in (path, *path.parents):
                if part.is_symlink():
                    raise ValueError(
                        t("WorkBuddy installation paths must not be symlinks: {path}", path=part)
                    )


def _json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(t("{path} is not a JSON object", path=path))
    return data


def _encode(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if not path.is_dir():
        raise ValueError(t("Expected a Skill directory: {path}", path=path))
    result = {}
    for file in sorted(path.rglob("*")):
        if file.is_symlink():
            raise ValueError(
                t("WorkBuddy installation paths must not be symlinks: {path}", path=file)
            )
        if file.is_file():
            result[file.relative_to(path).as_posix()] = _hash(file.read_bytes())
    return result


def _state(root: Path, scope: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    _safe_paths(root)
    record = _json(root / RECORD)
    installed_scope = record.get("mcp_scope", "project")
    scope = scope or installed_scope
    if scope not in ("project", "user"):
        raise ValueError(t("WorkBuddy MCP scope must be project or user"))
    if record and scope != installed_scope:
        raise ValueError(t("Remove the recorded WorkBuddy installation before changing MCP scope"))
    _safe_paths(root, scope)
    config = _json(_config_path(root, scope))
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(t("mcpServers must be a JSON object"))
    entry = servers.get(NAME)
    skill_exists = (root / SKILL_PATH).exists()
    if record:
        if record.get("schema") != 1 or record.get("project") != str(root):
            raise ValueError(t("The WorkBuddy installation record does not match this project"))
        if entry != record.get("entry") or _tree(root / SKILL_PATH) != record.get("skill_files"):
            raise ValueError(
                t(
                    "The installed pacxio configuration or Skill was edited; keep those changes before reinstalling or removing"
                )
            )
        if (root / BUNDLE).exists() and _hash((root / BUNDLE).read_bytes()) != record.get(
            "bundle_sha256"
        ):
            raise ValueError(
                t("The generated Skill ZIP was edited; keep it before reinstalling or removing")
            )
    elif entry is not None or skill_exists or (root / BUNDLE).exists() or (root / RECORD).exists():
        raise ValueError(
            t(
                "A pacxio entry or Skill already exists without an installation record; it will not be overwritten"
            )
        )
    return config, record


def _skill_files() -> dict[str, bytes]:
    from pacxio.mcp_server import TOOLS, skill_dir
    from pacxio.plugin_setup import _skill_entry

    source = skill_dir()
    if source is None:
        raise ValueError(t("The Skill bundled with the package (packaging-prepress) was not found"))
    full = (source / "SKILL.md").read_text(encoding="utf-8")
    entry = _skill_entry(full)
    header = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", entry, re.DOTALL)
    if header is None:
        raise ValueError("The bundled Skill has no YAML frontmatter")
    metadata = yaml.safe_load(header.group(1))
    metadata.update(
        display_name_en="pacxio Packaging",
        version=__version__,
        author="pacxio",
        description_en=metadata["description"],
    )
    with use_language("zh"):
        metadata.update(
            display_name=t("pacxio packaging design"),
            description_zh=t("Packaging design, dielines, previews and prepress workflow"),
        )
    entry = (
        "---\n"
        + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
        + "---\n"
        + entry[header.end() :]
    )
    entry += "\n## WorkBuddy project installation\n\n"
    entry += (
        "Use the pacxio MCP tools registered for this project. If this host cannot read MCP "
        "resources, read SKILL.local.md and the bundled references; they are a snapshot "
        "for the installed version. Do not present them as a newer workflow. "
        "Only use image generation tools actually available in this session; otherwise "
        "ask for user-provided images. Never claim an image was generated without a result.\n"
    )
    entry += (
        "\nSome WorkBuddy versions omit connected pacxio tools and resources from search/list "
        "indexes. Read the workflow URI directly. If tool search has no match, read "
        "references/workbuddy-mcp-tools.md for the exact native tool names and schemas, "
        "then use WorkBuddy's native direct/deferred tool invocation when available. "
        "Do not probe write tools with empty arguments or substitute shell calls. "
        "If native invocation is unavailable, report that limitation.\n\n"
        "Show studio_open's URL as a standalone Markdown link, for example "
        "[Open the packaging Workbench](http://127.0.0.1:PORT/), with punctuation outside "
        "the link. Display returned preview files as native image artifacts when supported. "
        "Keep the Workbench open while the user reviews; stop it only when requested.\n"
    )
    entry += (
        "\nBefore generating or editing packaging concepts, read "
        "references/workbuddy-images.md for host capability checks and visual review. "
        "These checks supplement, and never bypass, the current workflow's user confirmations.\n"
    )
    files = {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in sorted(source.rglob("*"))
        if p.is_file()
        and not set(p.relative_to(source).parts) & {"__pycache__", "agents", ".DS_Store"}
        and p.suffix != ".pyc"
    }
    files["SKILL.md"] = entry.encode()
    files["SKILL.local.md"] = full.encode()
    files["references/workbuddy-images.md"] = (
        b"# WorkBuddy image generation and review\n\n"
        b"Before every stage call job_next; its revision and stage state are the authority. "
        b"Keep preflight warnings, export warnings and unverified physical checks separate.\n\n"
        b"Check the image tool actually available in this session and its reference-image slot "
        b"limit. Use the engine's concept prompt and preserve the chosen design. If the prompt "
        b"has more references than the host allows, keep the chosen design and required geometry "
        b"references, split flat generation into face pairs when needed, and rewrite numbered "
        b"references consistently. Inspect every reference before generating: a layout wireframe "
        b"defines panel geometry, not empty artwork.\n\n"
        b"Inspect every returned image before importing or asking the user to confirm. Check "
        b"actual pixel dimensions, exact wording, title line breaks, panel placement, flatness, "
        b"colors, unwanted margins, ghost shapes and wrap seams. For transparent cutout photo "
        b"assets, verify real PNG alpha channels rather than painted checkerboards or solid "
        b"backgrounds. Use restore_run for automatic flat-to-print reconstruction; never place "
        b"concept images directly into print files.\n"
    )
    files["references/workbuddy-mcp-tools.md"] = (
        "# WorkBuddy native MCP tool reference\n\n"
        f"Generated from pacxio {__version__}; this matches the installed runtime. "
        "These are native MCP calls, not terminal commands. Use the schema exactly. "
        "Only the user can confirm a design.\n\n"
        + "\n\n".join(
            f"## mcp__pacxio__{tool['name']}\n\n{tool['description']}\n\n"
            + "```json\n"
            + json.dumps(tool["inputSchema"], ensure_ascii=False, indent=2)
            + "\n```"
            for tool in TOOLS
        )
        + "\n"
    ).encode()
    return files


def _bundle(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(f"{SKILL}/{name}")
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return output.getvalue()


def _local_test_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "::1")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is None
        or parsed.port == 0
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or value != value.strip()
    ):
        raise ValueError(t("Test cloud URL must be http://127.0.0.1:PORT or http://[::1]:PORT"))
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{parsed.port}"


def _entry(root: Path, test_cloud_url: str | None = None) -> dict[str, Any]:
    from pacxio.mcp_install import server_command

    command, args = server_command()
    env: dict[str, str] = {"PACXIO_STUDIO_DETACH": "1"}
    if test_cloud_url is not None:
        env |= {
            "PACXIO_HOME": str(root / ".workbuddy/pacxio-home"),
            "PACXIO_NO_UPDATE": "1",
            "PACXIO_AUTO_UPDATE": "0",
            "PACXIO_CLOUD_URL": _local_test_url(test_cloud_url),
            "PACXIO_CLOUD_TOKEN": "",
            "PACXIO_CLOUD": "1",
        }
    return {
        "type": "stdio",
        "command": command,
        "args": args,
        "cwd": str(root),
        "env": env,
    }


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _check(entry: dict[str, Any], root: Path) -> dict[str, Any]:
    from pacxio.mcp_install import _handshake

    try:
        return _handshake([entry["command"], *entry["args"]], env=entry["env"], cwd=root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": str(exc)}


def _apply(
    root: Path,
    config: dict[str, Any],
    previous: dict[str, Any],
    entry: dict[str, Any],
    files: dict[str, bytes],
    bundle: bytes,
    remove: bool,
    scope: str,
) -> Path:
    """Back up before writing; restore the previous state on a failed transaction."""
    config_path, skill_path = _config_path(root, scope), root / SKILL_PATH
    backup = root / ".workbuddy/pacxio-backups" / uuid.uuid4().hex
    backup.mkdir(parents=True)
    originals: dict[Path, bytes | None] = {}
    for path in (config_path, root / RECORD, root / BUNDLE):
        original = path.read_bytes() if path.exists() else None
        originals[path] = original
        if original is not None:
            _atomic(backup / path.name, original)
    if skill_path.exists():
        shutil.copytree(skill_path, backup / SKILL)
    staged = backup / "new-skill"
    if not remove:
        staged.mkdir()
        for name, data in files.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    servers = config.setdefault("mcpServers", {})
    if remove:
        servers.pop(NAME, None)
        if not servers and not previous.get("had_mcp_servers"):
            config.pop("mcpServers", None)
    else:
        servers[NAME] = entry
    try:
        if skill_path.exists():
            shutil.rmtree(skill_path)
        if not remove:
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, skill_path)
        if remove and not config and not previous.get("had_config"):
            config_path.unlink(missing_ok=True)
        else:
            _atomic(config_path, _encode(config))
        if remove:
            (root / RECORD).unlink()
            (root / BUNDLE).unlink(missing_ok=True)
        else:
            _atomic(root / BUNDLE, bundle)
            record = {
                "schema": 1,
                "project": str(root),
                "mcp_scope": scope,
                "config": str(config_path),
                "version": __version__,
                "entry": entry,
                "skill_files": {n: _hash(b) for n, b in files.items()},
                "bundle_sha256": _hash(bundle),
                "last_backup": str(backup),
                "had_config": previous.get("had_config", originals[config_path] is not None),
                "had_mcp_servers": previous.get(
                    "had_mcp_servers", "mcpServers" in json.loads(originals[config_path] or b"{}")
                ),
            }
            _atomic(root / RECORD, _encode(record))
    except OSError:
        if skill_path.exists():
            shutil.rmtree(skill_path)
        if (backup / SKILL).exists():
            shutil.copytree(backup / SKILL, skill_path)
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _atomic(path, original)
        raise
    return backup


def setup(
    project: Path | None = None,
    *,
    yes: bool = False,
    fonts: bool = True,
    remove: bool = False,
    scope: str | None = None,
    test_cloud_url: str | None = None,
) -> Result:
    from pacxio.plugin_setup import _fetch_fonts, _fonts_ready

    r = Result(command="setup", job=None)
    root = (project or Path.cwd()).expanduser().resolve()
    r.data = {
        "agent": "workbuddy",
        "scope": "project",
        "project": str(root),
        "written": False,
        "client_verified": False,
    }
    if not root.is_dir():
        return r.fail(
            Issue(
                id="ARG",
                message=t("Choose an existing WorkBuddy project directory: {path}", path=root),
            ),
            2,
        )
    try:
        if remove and test_cloud_url is not None:
            raise ValueError(t("--test-cloud-url cannot be combined with --remove"))
        config, previous = _state(root, scope)
        scope = scope or previous.get("mcp_scope", "project")
        assert scope in ("project", "user")
        config_path = _config_path(root, scope)
        entry = _entry(root, test_cloud_url)
        files = {} if remove else _skill_files()
        bundle = b"" if remove else _bundle(files)
        changed = (
            bool(previous)
            if remove
            else (
                previous.get("entry") != entry
                or previous.get("skill_files") != {n: _hash(b) for n, b in files.items()}
                or not (root / BUNDLE).exists()
            )
        )
        need_fonts = not remove and fonts and not _fonts_ready()
        steps = [
            t("Remove only the recorded WorkBuddy installation")
            if remove
            else t("Install the project Skill and merge the pacxio MCP entry"),
            t("Back up changed files; keep other MCP servers and Skills"),
        ]
        if not remove:
            steps.append(
                t("Isolated test mode: production cloud access and automatic updates are disabled")
                if test_cloud_url is not None
                else t("Use pacxio cloud and detached local Workbench (PACXIO_STUDIO_DETACH=1)")
            )
            steps.append(
                t("Download the open-source Source Han Sans fonts (about 33 MB)")
                if need_fonts
                else t("Fonts: already there, skipped")
                if fonts
                else t("Fonts: skipped (--no-fonts)")
            )
        r.data |= {
            "scope": scope,
            "skill_scope": "project",
            "changed": changed,
            "remove": remove,
            "mcp_scope": scope,
            "config": str(config_path),
            "skill": str(root / SKILL_PATH),
            "bundle": str(root / BUNDLE),
            "entry": entry,
            "test_cloud_url": entry["env"]["PACXIO_CLOUD_URL"] if test_cloud_url else None,
            "steps": steps,
        }
        if not remove:
            r.warnings.append(
                Issue(
                    id="MCP",
                    message=t(
                        "User MCP registration is visible in other WorkBuddy projects; keep tasks in the configured test directory"
                        if scope == "user"
                        else "WorkBuddy 5.9.3 did not load project MCP files in desktop tests; explicit user scope may be required"
                    ),
                )
            )
        if not yes:
            r.warnings.append(
                Issue(id="SETUP", message=t("Not installed yet: check data.steps, then add --yes"))
            )
            return r
        if not remove:
            check = _check(entry, root)
            r.data["server"] = check
            if not check.get("ok") or not check.get("tools") or not check.get("resources"):
                return r.fail(
                    Issue(
                        id="MCP",
                        message=t(
                            "The WorkBuddy MCP self-check failed; no installation files were changed"
                        ),
                    )
                )
        if changed:
            (root / ".workbuddy").mkdir(exist_ok=True)
            with ExitStack() as locks:
                paths = [root / LOCK]
                if scope == "user":
                    paths.append(Path.home() / LOCK)
                for lock in dict.fromkeys(paths):
                    lock.parent.mkdir(parents=True, exist_ok=True)
                    with lock.open("x", encoding="utf-8") as stream:
                        stream.write(str(os.getpid()))
                    locks.callback(lock.unlink)
                latest, latest_record = _state(root, scope)
                if latest != config or latest_record != previous:
                    raise ValueError(
                        t("WorkBuddy configuration changed during setup; run the preview again")
                    )
                backup = _apply(root, config, previous, entry, files, bundle, remove, scope)
            r.data |= {"written": True, "backup": str(backup)}
            r.artifacts = (
                [str(backup)]
                if remove
                else [str(config_path), *[str(root / p) for p in (SKILL_PATH, BUNDLE, RECORD)]]
            )
        if not remove:
            if need_fonts:
                r.data["fonts"] = _fetch_fonts(r)
            elif fonts:
                r.data["fonts"] = "ready"
            else:
                r.data["fonts"] = "skipped"
        if changed:
            r.warnings.append(
                Issue(
                    id="SETUP",
                    message=t(
                        "WorkBuddy files are ready; open this project in WorkBuddy and verify a real tool call"
                    )
                    if not remove
                    else t(
                        "WorkBuddy installation removed; test projects and runtime data were kept"
                    ),
                )
            )
        return r
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return r.fail(
            Issue(
                id="SETUP",
                message=t("WorkBuddy setup stopped: {error}", error=exc),
                fix=t(
                    "Keep existing files, resolve the reported conflict, then run the preview again"
                ),
            )
        )


def doctor(project: Path | None = None) -> Result:
    r = Result(command="mcp doctor", job=None)
    root = (project or Path.cwd()).expanduser().resolve()
    r.data = {"agent": "workbuddy", "project": str(root), "client_verified": False}
    try:
        _, record = _state(root)
        if not record:
            raise ValueError(t("No recorded WorkBuddy installation in this project"))
        check = _check(record["entry"], root)
        r.data |= {
            "server": check,
            "skill_files": len(record["skill_files"]),
            "mcp_scope": record.get("mcp_scope", "project"),
            "config": str(_config_path(root, record.get("mcp_scope", "project"))),
        }
        if not check.get("ok") or not check.get("tools") or not check.get("resources"):
            return r.fail(
                Issue(
                    id="MCP",
                    message=t(
                        "The WorkBuddy MCP self-check failed; no installation files were changed"
                    ),
                )
            )
        r.warnings.append(
            Issue(
                id="MCP",
                message=t(
                    "The server handshake passed; this does not verify WorkBuddy client behavior"
                ),
            )
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        r.fail(Issue(id="MCP", message=t("WorkBuddy setup stopped: {error}", error=exc)))
    return r
