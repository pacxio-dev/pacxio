"""MCP 的通用工具（2026-11-08 用户：大部分操作工作台和 agent 对话都要支持）。

- `pacxio_run`：跑任意一条 pacxio 命令（和命令行同一个函数，自动加 --json），返回同样的 JSON；
  出图类命令（刀版确认图、效果图、结构预览）把图一并返回；
- `pacxio_help`：列出全部命令，或某条命令的参数；
- `user_confirm`：用户在对话里明确说“确认”之后，代用户记一条确认（确认人写“用户（对话，经 agent）”，
  用户原话写进备注）。通用工具不能跑确认命令（铁律 4：确认只能是用户做）。

命令在本进程里跑（typer 的 CliRunner 隔离 stdin / stdout，不碰 MCP 的协议通道）。
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from pacxio.messages import t

MAX_TEXT = 60_000  # 返回的文字最多这么长（结构目录这类很长的列表截断，提示用 limit / 过滤）
MAX_IMAGES = 2
IMAGE_MAX_PX = 1400  # 返回的图缩到最长边这么多（省 agent 的上下文）
# 出图类命令：结果里这些文件当图片一并返回
IMAGE_NAMES = ("preview.png", "00_mockup_sheet.png", "view_front_right.png")
IMAGE_SUFFIXES = ("_3d.png", "_dieline.png")
BY_AGENT = t("user (conversation, via agent)")
# 确认命令：只能走 user_confirm（带用户原话）
CONFIRM_COMMANDS = {("content", "confirm"), ("concept", "choose"), ("concept", "use"), ("concept", "confirm"),
                    ("approve",)}  # fmt: skip
BLOCKED = {("mcp", "serve"): t("Cannot start an MCP server inside MCP"),
           ("studio", "serve"): t("A foreground server would block: use studio_open (starts in the background and returns the address)")}  # fmt: skip
CONFIRM_ACTIONS = {
    "content": t("wording of the content list"),
    "plan": t("layout draft"),
    "concept": t("choose this concept image (direction)"),
    "use": t("use this concept image"),
    "flats": t("this set of flats"),
    "design": t("dieline confirmation image (the only gate before export)"),
}


def _root() -> Any:
    from typer.main import get_command

    from pacxio.cli import app

    return get_command(app)


def commands() -> dict[str, Any]:
    """命令全名（"art add"）→ click 命令。"""
    out: dict[str, Any] = {}

    def walk(cmd: Any, prefix: list[str]) -> None:
        subs = getattr(cmd, "commands", None)
        if subs:
            for name, sub in subs.items():
                walk(sub, [*prefix, name])
        else:
            out[" ".join(prefix)] = cmd

    walk(_root(), [])
    return dict(sorted(out.items()))


def _first_line(text: str | None) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def help_text(command: str | None = None) -> dict[str, Any]:
    """不给命令：全部命令和一句说明；给了：这条命令的参数（位置参数、选项、说明、默认值）。"""
    cmds = commands()
    if not command:
        return {"commands": [{"command": k, "help": _first_line(c.help)} for k, c in cmds.items()
                             if tuple(k.split()) not in BLOCKED],  # fmt: skip
                "how": t("pacxio_run: command is the full command name (e.g. \"art add\"), args the argument list (as on the command line, without --json); confirmation commands (content confirm, plan --confirm, concept choose / use / confirm, approve) go through user_confirm")}  # fmt: skip
    key = " ".join(command.split())
    cmd = cmds.get(key)
    if cmd is None:
        close = [k for k in cmds if key.split()[0] in k]
        raise KeyError(
            t("No command {command}", command=command)
            + (t(" (did you mean: {names})", names=", ".join(close[:6])) if close else "")
        )
    params = []
    for p in cmd.params:
        if getattr(p, "name", None) == "json_out":
            continue
        item: dict[str, Any] = {"name": p.name, "kind": p.param_type_name}
        if p.param_type_name == "option":
            item["flags"] = list(p.opts) + list(getattr(p, "secondary_opts", []) or [])
            item["help"] = getattr(p, "help", None) or ""
            if p.default not in (None, False, ()) and not callable(p.default):
                item["default"] = p.default
            if getattr(p, "multiple", False):
                item["multiple"] = True
            if getattr(p, "is_flag", False):
                item["flag"] = True
        item["required"] = bool(p.required)
        params.append(item)
    return {"command": key, "help": (cmd.help or "").strip(), "params": params,
            "confirm_only": tuple(key.split()) in CONFIRM_COMMANDS}  # fmt: skip


def _confirms(parts: tuple[str, ...], cmd: Any, args: list[str]) -> bool:
    """这样跑会不会记一条确认。按命令行实际的解析结果判断（不看字符串里有没有 --status：
    `--note --status` 里的 --status 是备注的值，不是“只查状态”）；解析不了的按会确认处理。"""
    if parts not in CONFIRM_COMMANDS and parts != ("plan",):
        return False
    try:
        ctx = cmd.make_context(" ".join(parts), list(args), resilient_parsing=True)
    except Exception:  # noqa: BLE001
        return True
    params = ctx.params
    if parts == ("plan",):
        return bool(params.get("confirm"))
    return not params.get("status")


def _split(command: str) -> tuple[str, ...]:
    return tuple(command.split())


def _small_png(path: Path) -> bytes:
    from PIL import Image

    with Image.open(path) as src:
        im = src.convert("RGB")
    im.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def images_of(result: dict[str, Any]) -> list[bytes]:
    """结果里的预览图（刀版确认图、效果图汇总、结构 3D 图），缩小后一并返回给 agent。"""
    picked: list[Path] = []
    for a in result.get("artifacts") or []:
        p = Path(str(a))
        if p.suffix.lower() != ".png" or not p.is_file():
            continue
        if p.name in IMAGE_NAMES or p.name.endswith(IMAGE_SUFFIXES):
            picked.append(p)
    picked.sort(key=lambda p: (IMAGE_NAMES.index(p.name) if p.name in IMAGE_NAMES else 9, p.name))
    return [_small_png(p) for p in picked[:MAX_IMAGES]]


def run(command: str, args: list[str] | None = None) -> tuple[dict[str, Any], list[bytes]]:
    """跑一条命令 → (结果 JSON, 图片)。确认命令、前台服务拒绝。"""
    from typer.testing import CliRunner

    from pacxio.cli import app

    parts = _split(command)
    args = [str(a) for a in (args or [])]
    if parts in BLOCKED:
        return _refused(
            command, BLOCKED[parts], t("Use the tool named in the description instead")
        ), []
    cmds = commands()
    cmd = cmds.get(" ".join(parts))
    if cmd is None:
        return _refused(
            command,
            t("No command {command}", command=command),
            t("pacxio_help without arguments lists every command"),
        ), []
    if _confirms(parts, cmd, args):
        return _refused(command, t("Only the user confirms: the general tool cannot run confirmation commands"),
                        t("After the user clearly says they confirm in the conversation, use user_confirm (with the user's own words in user_words)")), []  # fmt: skip
    from pacxio.cloud.route import maybe_forward

    fw = maybe_forward([*parts, *args, "--json"])  # 闭源命令在云端模式下转给合成服务
    if fw is not None:
        text = fw[1]
    else:
        text = CliRunner().invoke(app, [*parts, *args, "--json"]).output or ""
    try:
        data = json.loads(text)
    except ValueError:
        start = text.find("{")
        try:
            data = json.loads(text[start:]) if start >= 0 else None
        except ValueError:
            data = None
    if not isinstance(data, dict):
        msg = text.strip()[-4000:]
        return {"ok": False, "command": command, "errors": [{"id": "CLI", "message": msg or t("The command printed nothing"),
                "fix": t("See the arguments of {command} with pacxio_help", command=command)}]}, []  # fmt: skip
    return data, images_of(data)


def _refused(command: str, message: str, fix: str) -> dict[str, Any]:
    return {"ok": False, "command": command, "job": None, "artifacts": [], "data": {},
            "errors": [{"id": "MCP", "message": message, "fix": fix}], "warnings": []}  # fmt: skip


def confirm(job: str, action: str, user_words: str, target: str | None = None, note: str | None = None,
            region: str | None = None) -> tuple[dict[str, Any], list[bytes]]:  # fmt: skip
    """用户在对话里说了确认 → 记一条确认（和工作台、命令行同一个函数）。没有用户原话不记。"""
    words = (user_words or "").strip()
    if action not in CONFIRM_ACTIONS:
        return _refused(
            "user_confirm",
            t("Unknown confirmation {action}", action=action),
            t("Available: ") + ", ".join(CONFIRM_ACTIONS),
        ), []
    if len(words) < 2:
        return _refused("user_confirm", t("No words from the user: only the user confirms"),
                        t("Show the user what they are confirming first (workbench or image); after they clearly confirm, put their own words in user_words")), []  # fmt: skip
    text = t("User's words: {words}", words=words) + (t("; {note}", note=note) if note else "")
    by = ["--by", BY_AGENT, "--note", text[:500]]
    from typer.testing import CliRunner

    from pacxio.cli import app

    cmd: list[str]
    if action == "content":
        cmd = ["content", "confirm", job, *by]
    elif action == "plan":
        cmd = ["plan", job, "--confirm", *by]
    elif action in ("concept", "use"):
        if not target:
            return _refused(
                "user_confirm",
                t("Say which concept image is being confirmed"),
                t("target is the concept id (see concept list)"),
            ), []
        cmd = ["concept", "choose" if action == "concept" else "use", job, target, *by]
        if region and action == "concept":
            cmd += ["--region", region]
    elif action == "flats":
        cmd = ["concept", "confirm", job, *by]
    else:
        cmd = ["approve", job, *by]
    from pacxio.cloud.route import maybe_forward

    fw = maybe_forward([*cmd, "--json"])  # approve 在云端模式下转给合成服务
    output = fw[1] if fw is not None else CliRunner().invoke(app, [*cmd, "--json"]).output
    try:
        data = json.loads(output)
    except ValueError:
        data = {
            "ok": False,
            "command": " ".join(cmd[:2]),
            "errors": [{"id": "CLI", "message": (output or "")[-2000:]}],
        }
    return data, []


def studio_open(job: str, port: int | None = None) -> dict[str, Any]:
    """后台启动工作台（已在跑就返回原来的地址）。"""
    from typer.testing import CliRunner

    from pacxio.cli import app

    args = ["studio", "start", job, "--json"] + (["--port", str(port)] if port else [])
    res = CliRunner().invoke(app, args)
    try:
        return dict(json.loads(res.output))
    except ValueError:
        return _refused(
            "studio start",
            res.output[-2000:] or t("The workbench did not start"),
            t("Run studio status with pacxio_run to check"),
        )


def truncate(text: str) -> str:
    if len(text) <= MAX_TEXT:
        return text
    return text[:MAX_TEXT] + t(
        "\n… (truncated, {n} characters in all; narrow it with limit / filters)", n=len(text)
    )


__all__ = [
    "BY_AGENT",
    "CONFIRM_ACTIONS",
    "commands",
    "confirm",
    "help_text",
    "images_of",
    "run",
    "studio_open",
]
