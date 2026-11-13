"""把本地 MCP 装进 agent（2026-11-08 用户选“一键安装”）：`pacxio mcp install` / `mcp doctor`。

- 不给 --yes：只列出要改哪个文件、写什么，不动文件；给了才写，写之前把原文件备份成 `*.bak-时间`。
- 命令用安装时的 Python 启动轻量 launcher，再选择已验证的运行版本，不依赖 PATH 上的 pacxio。
- Claude Code：项目级写项目根的 `.mcp.json`；用户级交给 `claude mcp add -s user`（不手改 ~/.claude.json）。
- Codex：`~/.codex/config.toml` 的 `[mcp_servers.pacxio]` 一段（有就替换，没有就加在最后）。
- Claude 桌面版：`claude_desktop_config.json` 的 mcpServers.pacxio。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pacxio.messages import t
from pacxio.model.common import Issue, Result

CLIENTS = ("claude-code", "codex", "claude-desktop")
NAME = "pacxio"
DOCTOR_TIMEOUT_S = 60


def server_command() -> tuple[str, list[str]]:
    from pacxio.launcher import bootstrap_python

    return bootstrap_python(), ["-I", "-m", "pacxio.launcher", "mcp", "serve"]


def _desktop_config() -> Path:
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if sys.platform.startswith("win"):
        return (
            Path(os.environ.get("APPDATA", str(Path.home())))
            / "Claude"
            / "claude_desktop_config.json"
        )
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # TOML 的基本字符串和 JSON 字符串转义一致


def _codex_block(cmd: str, args: list[str]) -> str:
    return f"[mcp_servers.{NAME}]\ncommand = {_toml_str(cmd)}\nargs = [{', '.join(_toml_str(a) for a in args)}]\n"


def _codex_text(old: str, block: str) -> str:
    """把 [mcp_servers.pacxio] 一段换成新的（一直到下一个表头），没有就加在最后。"""
    pat = re.compile(rf"(?ms)^\[mcp_servers\.{NAME}\]\s*\n.*?(?=^\[|\Z)")
    if pat.search(old):
        return pat.sub(block + "\n", old, count=1).rstrip("\n") + "\n"
    return (old.rstrip("\n") + "\n\n" if old.strip() else "") + block


def _json_merge(path: Path, cmd: str, args: list[str], typed: bool) -> tuple[str, str]:
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    data: dict[str, Any] = json.loads(old) if old.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(t("{path} is not a JSON object", path=path))
    entry: dict[str, Any] = {"command": cmd, "args": args}
    if typed:
        entry = {"type": "stdio", **entry}
    data.setdefault("mcpServers", {})[NAME] = entry
    return old, json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    dest = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    return dest


def install(
    client: str, scope: str = "project", yes: bool = False, project: Path | None = None
) -> Result:
    r = Result(command="mcp install", job=None)
    if client not in CLIENTS:
        return r.fail(
            Issue(
                id="ARG",
                message=t("Unknown agent {client}", client=client),
                fix=t("Available: ") + ", ".join(CLIENTS),
            ),
            2,
        )
    if scope not in ("project", "user"):
        return r.fail(
            Issue(
                id="ARG",
                message=t("Unknown scope {scope}", scope=scope),
                fix=t("project (this project) or user (all projects)"),
            ),
            2,
        )
    cmd, args = server_command()
    r.data = {"client": client, "scope": scope, "command": [cmd, *args]}
    if client == "claude-code" and scope == "user":
        claude = shutil.which("claude")
        line = ["claude", "mcp", "add", "-s", "user", NAME, "--", cmd, *args]
        r.data["run"] = line
        if not yes:
            r.warnings.append(
                Issue(
                    id="MCP",
                    message=t("Not installed yet: add --yes to run the claude command above"),
                )
            )
            return r
        if claude is None:
            return r.fail(
                Issue(
                    id="MCP",
                    message=t("claude command not found"),
                    fix=t("Run it by hand: ") + " ".join(line),
                ),
                2,
            )
        done = subprocess.run([claude, *line[1:]], capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            return r.fail(Issue(id="MCP", message=(done.stderr or done.stdout).strip()[-1000:],
                                fix=t("If it is already installed, run claude mcp remove -s user pacxio first")), 1)  # fmt: skip
        r.data["written"] = True
        return r
    if client == "claude-code":
        path = (project or Path.cwd()) / ".mcp.json"
        old, new = _json_merge(path, cmd, args, typed=True)
    elif client == "claude-desktop":
        path = _desktop_config()
        old, new = _json_merge(path, cmd, args, typed=False)
        if scope == "project":
            r.warnings.append(
                Issue(
                    id="MCP",
                    message=t("Claude Desktop has no project-level config; installing globally"),
                )
            )
    else:
        path = _codex_config()
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        new = _codex_text(old, _codex_block(cmd, args))
        if scope == "project":
            r.warnings.append(
                Issue(
                    id="MCP",
                    message=t(
                        "Codex's MCP config is global (~/.codex/config.toml); installing globally"
                    ),
                )
            )
    r.data |= {"file": str(path), "exists": path.is_file(), "changed": old != new, "new_text": new}
    if old == new:
        r.data["written"] = False
        r.warnings.append(
            Issue(id="MCP", message=t("Already configured like this; nothing to change"))
        )
        return r
    if not yes:
        r.data["written"] = False
        r.warnings.append(
            Issue(
                id="MCP",
                message=t(
                    "Not written yet: check data.new_text, then add --yes to write {path}",
                    path=path,
                ),
            )
        )
        return r
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    r.artifacts = [str(path)]
    r.data |= {"written": True, "backup": str(backup) if backup else None}
    r.data.pop("new_text", None)
    r.warnings.append(Issue(id="MCP", message=t("Written: restart the agent (or start a new session) to load it"),
                            fix=t("The first time Claude Code sees the project's .mcp.json it asks whether to enable it; choose enable")))  # fmt: skip
    return r


def _handshake(cmd: list[str]) -> dict[str, Any]:
    """起一次服务，走 initialize → tools/list → resources/list。"""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "doctor", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
    ]  # fmt: skip
    environment = os.environ.copy()
    environment["PACXIO_NO_UPDATE"] = "1"
    done = subprocess.run(cmd, input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True,
                          text=True, timeout=DOCTOR_TIMEOUT_S, env=environment)  # fmt: skip
    replies = {}
    for line in done.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            return {"ok": False, "why": t("Non-protocol output on stdout: {line}", line=line[:200])}
        replies[m.get("id")] = m
    if 1 not in replies or "result" not in replies[1]:
        return {"ok": False, "why": (done.stderr or t("No response")).strip()[-500:]}
    info = replies[1]["result"]
    return {"ok": True, "version": info.get("serverInfo", {}).get("version"), "protocol": info.get("protocolVersion"),
            "tools": len(replies.get(2, {}).get("result", {}).get("tools", [])),
            "resources": len(replies.get(3, {}).get("result", {}).get("resources", []))}  # fmt: skip


def _registered(project: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for client, path in (
        (t("claude-code (project)"), project / ".mcp.json"),
        ("claude-desktop", _desktop_config()),
    ):
        if path.is_file():
            try:
                entry = (json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {}).get(
                    NAME
                )
            except ValueError:
                entry = None
            if entry:
                out.append(
                    {
                        "client": client,
                        "file": str(path),
                        "command": [entry.get("command"), *(entry.get("args") or [])],
                    }
                )
    codex = _codex_config()
    if codex.is_file():
        import tomllib

        try:
            entry = (tomllib.loads(codex.read_text(encoding="utf-8")).get("mcp_servers") or {}).get(
                NAME
            )
        except tomllib.TOMLDecodeError:
            entry = None
        if entry:
            out.append(
                {
                    "client": "codex",
                    "file": str(codex),
                    "command": [entry.get("command"), *(entry.get("args") or [])],
                }
            )
    out.extend(_plugins())
    return out


def _plugins() -> list[dict[str, Any]]:
    """`pacxio setup` 装的插件：命令写在生成的插件 .mcp.json 里。"""
    from pacxio.plugin_setup import _claude_state, _codex_state, plugin_home

    mcp = plugin_home() / NAME / ".mcp.json"
    try:
        entry = json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"][NAME]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = []
    for client, state in (
        (t("claude-code (plugin)"), _claude_state),
        (t("codex (plugin)"), _codex_state),
    ):
        if state()[1]:
            out.append({"client": client, "file": str(mcp),
                        "command": [entry.get("command"), *(entry.get("args") or [])]})  # fmt: skip
    return out


def doctor(project: Path | None = None) -> Result:
    r = Result(command="mcp doctor", job=None)
    cmd, args = server_command()
    here = _handshake([cmd, *args])
    r.data = {"server": {"command": [cmd, *args], **here}, "registered": []}
    if not here["ok"]:
        r.fail(
            Issue(
                id="MCP",
                message=t("The MCP server does not start: {why}", why=here["why"]),
                fix=t("Run pacxio env check"),
            ),
            1,
        )
    for reg in _registered(project or Path.cwd()):
        same = reg["command"] == [cmd, *args]
        check = (
            here
            if same
            else (
                _handshake([c for c in reg["command"] if c])
                if reg["command"][0]
                else {"ok": False, "why": t("No command registered")}
            )
        )
        reg |= {"same_as_this": same, **check}
        r.data["registered"].append(reg)
        if not check.get("ok"):
            r.warnings.append(Issue(id="MCP", message=t("The pacxio registered in {client} does not start: {why}", client=reg["client"], why=check.get("why")),
                                    fix=t("Reinstall: pacxio setup --agent {agent} --yes", agent=reg["client"].split(" (")[0].split("（")[0])))  # fmt: skip
    if not r.data["registered"]:
        r.warnings.append(
            Issue(
                id="MCP",
                message=t("Not installed in any agent yet"),
                fix=t("pacxio setup --agent claude-code (or codex)"),
            )
        )
    return r


__all__ = ["CLIENTS", "doctor", "install", "server_command"]
