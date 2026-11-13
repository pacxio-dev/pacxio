"""一句话安装（2026-11-10 用户定，）：`pacxio setup --agent claude-code|codex`；
不给 --agent 时自动找本机装了的 agent（PATH 上的 claude / codex），都装上。

做的事：
1. 在本机生成一个插件（Skill + MCP 打在一起）和放它的本地插件市场：`~/.pacxio/plugin/`
   （Claude Code 读 `.claude-plugin/`，Codex 读 `.codex-plugin/` 和 `.agents/plugins/`）；
2. 用 agent 自己的命令装上：`claude plugin marketplace add / install`、`codex plugin marketplace add / add`；
3. 缺默认字体（思源黑体）时跑随包的 `fetch_fonts.py` 下载（约 33 MB；联网只在这个脚本里，核心引擎不联网）；
4. 起一次 MCP 服务走握手，确认能用。

不给 --yes：只列出要做的事和要跑的命令，什么都不动（和 `mcp install` 一样）。
以前用 `mcp install` 或手动软链接装过的，会在 warnings 里提示怎么去掉，免得工具和 Skill 重复；不替用户删。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from pacxio.messages import t
from pacxio.model.common import Issue, Result

AGENTS = ("claude-code", "codex")
CLI = {"claude-code": "claude", "codex": "codex"}  # 各 agent 在终端里的命令
LABEL = {"claude-code": "Claude Code", "codex": "Codex"}
NAME = "pacxio"  # 插件名，也是插件市场名：装出来是 pacxio@pacxio
SKILL = "packaging-prepress"
MARKER = ".pacxio-generated"  # 只清理带这个标记的目录，别的不碰
CLI_TIMEOUT_S = 120
DESCRIPTION = (
    "Packaging design to factory files: dielines, 3D, print PDF, finish plates and preflight, "
    "with a local workbench."
)


def plugin_home() -> Path:
    return Path(os.environ.get("PACXIO_HOME", str(Path.home() / ".pacxio"))) / "plugin"


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))


def _codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_plugin(dest: Path | None = None) -> dict[str, Any]:
    """生成插件市场目录（两种 agent 共用一份插件），返回路径和版本。"""
    from pacxio import __version__
    from pacxio.mcp_install import server_command
    from pacxio.mcp_server import skill_dir

    src = skill_dir()
    if src is None:
        raise FileNotFoundError(
            t("The Skill bundled with the package (packaging-prepress) was not found")
        )
    root = dest or plugin_home()
    if root.exists():
        if not (root / MARKER).is_file():
            raise FileExistsError(
                t(
                    "{root} already has other content (not made by pacxio setup); not overwriting",
                    root=root,
                )
            )
        shutil.rmtree(root)
    plugin = root / NAME
    cmd, args = server_command()
    author = {"name": "pacxio"}
    _write_json(root / ".claude-plugin" / "marketplace.json", {
        "name": NAME, "owner": author, "metadata": {"description": "pacxio, installed by `pacxio setup`"},
        "plugins": [{"name": NAME, "source": f"./{NAME}", "description": DESCRIPTION, "version": __version__}],
    })  # fmt: skip
    _write_json(root / ".agents" / "plugins" / "marketplace.json", {
        "name": NAME, "interface": {"displayName": "pacxio"},
        "plugins": [{"name": NAME, "source": {"source": "local", "path": f"./{NAME}"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                     "category": "Design"}],
    })  # fmt: skip
    _write_json(plugin / ".claude-plugin" / "plugin.json", {
        "name": NAME, "version": __version__, "description": DESCRIPTION, "author": author,
    })  # fmt: skip
    _write_json(plugin / ".codex-plugin" / "plugin.json", {
        "name": NAME, "version": __version__, "description": DESCRIPTION, "author": author,
        "mcpServers": "./.mcp.json", "skills": "./skills/",
        "interface": {"displayName": "pacxio", "shortDescription": "Design packaging and get factory files",
                      "category": "Design",
                      "defaultPrompt": ["Make a mailer box for my brand, 240 × 170 × 70 mm"]},
    })  # fmt: skip
    _write_json(plugin / ".mcp.json", {"mcpServers": {NAME: {"command": cmd, "args": args}}})
    # 软链接（scripts/fetch_fonts.py）复制成真文件：插件会被 agent 复制进缓存，链接会断
    shutil.copytree(src, plugin / "skills" / SKILL, symlinks=False,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))  # fmt: skip
    (root / MARKER).write_text(__version__ + "\n", encoding="utf-8")
    return {
        "dir": str(root),
        "plugin": str(plugin),
        "version": __version__,
        "command": [cmd, *args],
    }


def _agent_commands(agent: str, root: Path, installed: bool, has_market: bool) -> list[list[str]]:
    sel = f"{NAME}@{NAME}"
    if agent == "claude-code":
        market = (["claude", "plugin", "marketplace", "update", NAME] if has_market
                  else ["claude", "plugin", "marketplace", "add", str(root)])  # fmt: skip
        plug = (
            ["claude", "plugin", "update", sel]
            if installed
            else ["claude", "plugin", "install", sel, "-s", "user"]
        )
        return [market, plug]
    add: list[list[str]] = (
        [] if has_market else [["codex", "plugin", "marketplace", "add", str(root)]]
    )
    return [*add, ["codex", "plugin", "add", sel]]


def _run(line: list[str]) -> subprocess.CompletedProcess[str]:
    exe = shutil.which(line[0])
    if exe is None:
        raise FileNotFoundError(line[0])
    return subprocess.run([exe, *line[1:]], capture_output=True, text=True, timeout=CLI_TIMEOUT_S)


def _claude_state() -> tuple[bool, bool]:
    """(插件市场已加, 插件已装)：读 claude 自己的记录文件，不改。"""
    plugins = _claude_dir() / "plugins"
    market = installed = False
    try:
        market = NAME in json.loads(
            (plugins / "known_marketplaces.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        pass
    try:
        data = json.loads((plugins / "installed_plugins.json").read_text(encoding="utf-8"))
        installed = f"{NAME}@{NAME}" in (data.get("plugins") or data)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return market, installed


def _codex_state() -> tuple[bool, bool]:
    try:
        cfg = tomllib.loads((_codex_dir() / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False, False
    return NAME in (cfg.get("marketplaces") or {}), f"{NAME}@{NAME}" in (cfg.get("plugins") or {})


def _old_installs(agent: str) -> list[Issue]:
    """以前装过的独立 MCP / Skill：和插件重复，提示用户去掉（不替用户删）。"""
    out: list[Issue] = []
    if agent == "claude-code":
        skill = _claude_dir() / "skills" / SKILL
        claude_json = Path.home() / ".claude.json"
        try:
            user_mcp = NAME in (
                json.loads(claude_json.read_text(encoding="utf-8")).get("mcpServers") or {}
            )
        except (OSError, ValueError, AttributeError):
            user_mcp = False
        if user_mcp:
            out.append(Issue(id="SETUP", message=t("Claude Code still has a separately installed pacxio MCP that duplicates the plugin's"),
                             fix="claude mcp remove -s user pacxio"))  # fmt: skip
    else:
        skill = Path.home() / ".agents" / "skills" / SKILL
        try:
            codex_cfg = tomllib.loads((_codex_dir() / "config.toml").read_text(encoding="utf-8"))
            old_mcp = NAME in (codex_cfg.get("mcp_servers") or {})
        except (OSError, tomllib.TOMLDecodeError):
            old_mcp = False
        if old_mcp:
            out.append(Issue(id="SETUP", message=t("The Codex config still has a separately installed [mcp_servers.pacxio] that duplicates the plugin's"),
                             fix=t("Delete the [mcp_servers.pacxio] section in {path}", path=_codex_dir() / "config.toml")))  # fmt: skip
    if skill.exists() or skill.is_symlink():
        out.append(Issue(id="SETUP", message=t("{skill} is a separately installed Skill that duplicates the plugin's", skill=skill),
                         fix=t("rm {skill} (if it is a symlink, only the link is removed)", skill=skill)))  # fmt: skip
    return out


def _fonts_ready() -> bool:
    from pacxio.api import DEFAULT_FONT
    from pacxio.fontfiles import TextError, find_font

    try:
        find_font(DEFAULT_FONT)
    except TextError:
        return False
    return True


def detect_agents() -> list[str]:
    """本机终端里能运行的 agent（按 PATH 上有没有 claude / codex 命令判断）。"""
    return [a for a in AGENTS if shutil.which(CLI[a])]


def setup(agent: str | None = None, yes: bool = False, fonts: bool = True) -> Result:
    """agent 不给时自动找本机装了的 agent，找到几个装几个（终端一行安装：`pacxio setup --yes`）。"""
    r = Result(command="setup", job=None)
    if agent is not None and agent not in AGENTS:
        return r.fail(
            Issue(
                id="ARG",
                message=t("Unknown agent {client}", client=agent),
                fix=t("Available: ") + ", ".join(AGENTS),
            ),
            2,
        )
    agents = [agent] if agent else detect_agents()
    if not agents:
        return r.fail(Issue(id="SETUP", message=t("Neither Claude Code nor Codex found (no claude / codex command in the terminal)"),
                            fix=t("Install one of them and check that claude or codex runs in the terminal; or choose with --agent")), 3)  # fmt: skip
    root = plugin_home()
    plan: dict[str, list[list[str]]] = {}
    installed: dict[str, bool] = {}
    for a in agents:
        has_market, installed[a] = _claude_state() if a == "claude-code" else _codex_state()
        plan[a] = _agent_commands(a, root, installed[a], has_market)
    need_fonts = fonts and not _fonts_ready()
    r.data = {
        "agents": agents, "detected": agent is None, "plugin_dir": str(root),
        "already_installed": installed,
        "steps": [t("Build the plugin (Skill + MCP) in {root}", root=root), *(" ".join(x) for a in agents for x in plan[a]),
                  t("Download the open-source Source Han Sans fonts (about 33 MB)") if need_fonts else t("Fonts: already there, skipped") if fonts else t("Fonts: skipped (--no-fonts)"),
                  t("Start the MCP server once as a self-check")],
    }  # fmt: skip
    for a in agents:
        r.warnings.extend(_old_installs(a))
    if not yes:
        r.warnings.append(
            Issue(id="SETUP", message=t("Not installed yet: check data.steps, then add --yes"))
        )
        return r
    missing = [a for a in agents if shutil.which(CLI[a]) is None]
    if missing:
        a = missing[0]
        return r.fail(Issue(id="SETUP", message=t("{cli} command not found", cli=CLI[a]),
                            fix=t("Install {label} first and check that {cli} runs in the terminal", label=LABEL[a], cli=CLI[a])), 3)  # fmt: skip
    try:
        r.data["plugin"] = build_plugin(root)
    except (OSError, FileExistsError) as e:
        return r.fail(Issue(id="SETUP", message=t("Building the plugin failed: {error}", error=e),
                            fix=t("Set PACXIO_HOME to use another folder, or delete that folder and try again")), 1)  # fmt: skip
    ran: list[dict[str, Any]] = []
    for line in (x for a in agents for x in plan[a]):
        done = _run(line)
        ran.append({"run": line, "code": done.returncode})
        if done.returncode != 0:
            r.data["ran"] = ran
            return r.fail(Issue(id="SETUP", message=t("{cmd} failed: {tail}", cmd=" ".join(line), tail=(done.stderr or done.stdout).strip()[-800:]),
                                fix=t("Run this command by hand to see the full error; the plugin folder is {root}", root=root)), 1)  # fmt: skip
    r.data["ran"] = ran
    if need_fonts:
        r.data["fonts"] = _fetch_fonts(r)
    elif fonts:
        r.data["fonts"] = "ready"
    from pacxio.mcp_install import _handshake

    check = _handshake(r.data["plugin"]["command"])
    r.data["server"] = check
    if not check.get("ok"):
        return r.fail(Issue(id="SETUP", message=t("The plugin is installed, but the MCP server does not start: {why}", why=check.get("why")),
                            fix=t("Run pacxio env check")), 1)  # fmt: skip
    names = t(" and ").join(LABEL[a] for a in agents)
    r.warnings.append(
        Issue(
            id="SETUP",
            message=t(
                "Installed: restart {names} (or start a new session) to load the plugin",
                names=names,
            ),
        )
    )
    return r


def _fetch_fonts(r: Result) -> str:
    from pacxio.mcp_server import skill_dir

    src = skill_dir()
    script = src / "scripts" / "fetch_fonts.py" if src else None
    if script is None or not script.is_file():
        r.warnings.append(
            Issue(
                id="FONT",
                message=t("The font download script bundled with the package was not found"),
                fix=t("See what is missing with pacxio env check"),
            )
        )
        return "missing-script"
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=1800
    )
    if done.returncode != 0 or not _fonts_ready():
        r.warnings.append(Issue(id="FONT", message=t("Font download failed: ") + (done.stderr or done.stdout).strip()[-500:],
                                fix=t("When online, run: {python} {script}", python=sys.executable, script=script)))  # fmt: skip
        return "failed"
    return "downloaded"


__all__ = ["AGENTS", "build_plugin", "detect_agents", "plugin_home", "setup"]
