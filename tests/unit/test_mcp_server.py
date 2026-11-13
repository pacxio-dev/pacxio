"""几何引擎第 5 步 ⑤：本地 MCP 服务（stdio JSON-RPC）和改配方。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pacxio import api
from pacxio.mcp_server import TOOLS, handle, serve


def _rpc(lines: list[dict]) -> list[dict]:
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n"), out)
    return [json.loads(x) for x in out.getvalue().splitlines()]


def test_handshake_and_tool_list() -> None:
    replies = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "nope"},
    ])  # fmt: skip
    assert [r["id"] for r in replies] == [1, 2, 3, 4]  # 通知不回
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert (
        names == {t["name"] for t in TOOLS}
        and {"structures_suggest", "structure_check", "structure_update"} <= names
    )
    assert replies[3]["error"]["code"] == -32601


def test_preview_returns_the_3d_image(tmp_path: Path) -> None:
    r = handle({"method": "tools/call", "params": {"name": "structure_preview", "arguments": {
        "code": "0427", "L": 240, "W": 170, "H": 70, "out_dir": str(tmp_path)}}})  # fmt: skip
    assert r is not None and not r["isError"]
    assert [c["type"] for c in r["content"]] == ["text", "image"]
    assert json.loads(r["content"][0]["text"])["data"]["structure"]["code"] == "0427"


def test_tool_errors_come_back_as_results(tmp_path: Path) -> None:
    r = handle(
        {
            "method": "tools/call",
            "params": {"name": "structure_show", "arguments": {"code": "9999"}},
        }
    )
    assert r is not None and r["isError"]
    bad = _rpc(
        [{"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "structure_show"}}]
    )
    assert bad[0]["error"]["code"] == -32602  # 缺参数


def test_init_check_and_update_through_tools(tmp_path: Path) -> None:
    job = tmp_path / "bin"
    r = handle({"method": "tools/call", "params": {"name": "job_init_structure", "arguments": {
        "job": str(job), "code": "0801", "L": 400, "W": 300, "H": 250}}})  # fmt: skip
    assert r is not None and not r["isError"]
    up = api.structure_update(job, {"top": {"depth": 120.0}, "body": {"window": "back"}})
    assert up.ok, up.errors
    params = json.loads((job / "job.json").read_text(encoding="utf-8"))["params"]
    assert (
        params["top"]["depth"] == 120.0
        and params["body"]["window"] == "back"
        and params["body"]["L"] == 400
    )
    bad = api.structure_update(job, {"top": {"kind": "nonsense"}})
    assert not bad.ok
    check = handle({"method": "tools/call", "params": {"name": "structure_check", "arguments": {
        "job": str(job), "needs": ["no_tape"]}}})  # fmt: skip
    assert check is not None and json.loads(check["content"][0]["text"])["data"]["needs"][0]["met"]


# ---- 全流程、确认、安装（2026-11-08 MCP 收尾）----


def _call(name: str, args: dict) -> tuple[dict, list[dict]]:
    r = handle({"method": "tools/call", "params": {"name": name, "arguments": args}})
    assert r is not None
    return json.loads(r["content"][0]["text"]), r["content"][1:]


def test_tools_have_annotations_and_cover_the_flow() -> None:
    names = {t["name"] for t in TOOLS}
    assert {"job_next", "studio_open", "pacxio_run", "pacxio_help", "user_confirm"} <= names
    assert not {"dieline_import", "dieline_fold"} & names  # 导入刀版 2026-11-11 先删掉
    for t in TOOLS:
        assert set(t["annotations"]) >= {"readOnlyHint", "destructiveHint"}
    ro = {t["name"] for t in TOOLS if t["annotations"]["readOnlyHint"]}
    assert "pacxio_help" in ro and "pacxio_run" not in ro and "user_confirm" not in ro


def test_help_lists_every_cli_command(tmp_path: Path) -> None:
    from pacxio.mcp_cli import commands

    data, _ = _call("pacxio_help", {})
    listed = {c["command"] for c in data["commands"]}
    assert listed == set(commands()) - {"mcp serve", "studio serve"}
    data, _ = _call("pacxio_help", {"command": "art add"})
    assert any("--panel" in p.get("flags", []) for p in data["params"])
    r = handle(
        {
            "method": "tools/call",
            "params": {"name": "pacxio_help", "arguments": {"command": "nope"}},
        }
    )
    assert r is not None and r["isError"]


@pytest.mark.pro
def test_run_any_command_and_get_the_preview_image(tmp_path: Path) -> None:
    job = tmp_path / "rte"
    data, _ = _call("pacxio_run", {"command": "job init", "args": [str(job), "--style", "rte", "--L", "80",
                                                                         "--W", "50", "--H", "120"]})  # fmt: skip
    assert data["ok"], data
    data, images = _call("pacxio_run", {"command": "render", "args": [str(job)]})
    assert data["ok"] and images and images[0]["type"] == "image"
    data, _ = _call("job_next", {"job": str(job)})
    assert data["ok"] and data["data"]["now"]
    data, _ = _call("pacxio_run", {"command": "art nope", "args": [str(job)]})
    assert not data["ok"] and "没有命令" in data["errors"][0]["message"]


def test_confirmations_only_with_the_users_words(tmp_path: Path) -> None:
    from pacxio.mcp_cli import BY_AGENT

    job = tmp_path / "rte"
    assert _call("pacxio_run", {"command": "job init", "args": [str(job), "--style", "rte", "--L", "80",
                                                                     "--W", "50", "--H", "120"]})[0]["ok"]  # fmt: skip
    assert _call("pacxio_run", {"command": "content add", "args": [str(job), "--category", "brand",
                                                                        "--text", "山岚"]})[0]["ok"]  # fmt: skip
    for cmd, args in (("content confirm", [str(job)]), ("approve", [str(job)]), ("plan", [str(job), "--confirm"]),
                      ("concept choose", [str(job), "c1"])):  # fmt: skip
        data, _ = _call("pacxio_run", {"command": cmd, "args": args})
        assert not data["ok"] and "用户" in data["errors"][0]["message"], cmd
    assert (
        _call("pacxio_run", {"command": "approve", "args": [str(job), "--status"]})[0]["command"]
        == "approve"
    )
    # --status 当成别的选项的值（备注写 "--status"）时不是"只查状态"：照样拒绝
    for cmd, args in (("approve", [str(job), "--by", "x", "--note", "--status"]),
                      ("content confirm", [str(job), "--note", "--status"]),
                      ("plan", [str(job), "--confirm", "--status"])):  # fmt: skip
        data, _ = _call("pacxio_run", {"command": cmd, "args": args})
        assert not data["ok"] and "用户" in data["errors"][0]["message"], (cmd, args)
    data, _ = _call("user_confirm", {"job": str(job), "action": "content", "user_words": " "})
    assert not data["ok"]
    data, _ = _call(
        "user_confirm", {"job": str(job), "action": "content", "user_words": "对，就这样，确认"}
    )
    assert data["ok"], data
    record = json.dumps(
        json.loads((job / "job.json").read_text(encoding="utf-8")), ensure_ascii=False
    )
    found = list(job.rglob("*.json"))
    text = " ".join(p.read_text(encoding="utf-8") for p in found)
    assert BY_AGENT in text and "对，就这样，确认" in text, record[:200]


def test_skill_docs_are_resources() -> None:
    res = handle({"method": "resources/list"})
    assert res is not None
    uris = [r["uri"] for r in res["resources"]]
    assert "pacxio://skill/SKILL.md" in uris and any(
        u.endswith("references/commands.md") for u in uris
    )
    body = handle({"method": "resources/read", "params": {"uri": "pacxio://skill/SKILL.md"}})
    assert body is not None and "Hard rules" in body["contents"][0]["text"]


def test_install_previews_then_writes_with_backup(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import tomllib

    from pacxio import mcp_install

    monkeypatch.setattr(mcp_install, "_codex_config", lambda: tmp_path / "codex" / "config.toml")
    monkeypatch.setattr(
        mcp_install, "_desktop_config", lambda: tmp_path / "desktop" / "claude_desktop_config.json"
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    r = mcp_install.install("claude-code", "project", yes=False, project=proj)
    assert r.ok and not (proj / ".mcp.json").exists() and "pacxio" in r.data["new_text"]
    r = mcp_install.install("claude-code", "project", yes=True, project=proj)
    entry = json.loads((proj / ".mcp.json").read_text())["mcpServers"]["pacxio"]
    assert entry["args"] == ["-I", "-m", "pacxio.launcher", "mcp", "serve"] and entry["type"] == "stdio"
    # Codex：原有的配置保留，pacxio 一段装两次也只有一段
    codex = tmp_path / "codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "o"\n', encoding="utf-8")
    assert mcp_install.install("codex", yes=True).ok
    assert mcp_install.install("codex", yes=True).data["written"] is False  # 已经是这样
    cfg = tomllib.loads(codex.read_text())
    assert cfg["model"] == "x" and set(cfg["mcp_servers"]) == {"other", "pacxio"}
    assert list(codex.parent.glob("config.toml.bak-*"))
    assert mcp_install.install("claude-desktop", yes=True).ok
    doc = mcp_install.doctor(proj)
    assert doc.data["server"]["ok"] and doc.data["server"]["tools"] == len(TOOLS)
    assert {r["client"] for r in doc.data["registered"]} == {
        "claude-code（项目）",
        "codex",
        "claude-desktop",
    }


def test_errors_inside_a_tool_are_tool_results(tmp_path: Path) -> None:
    """参数齐了、工具里面出错（job.json 坏了等）：按工具结果回（isError + 原因），不是"参数不对"的协议错误。"""
    job = tmp_path / "broken"
    job.mkdir()
    (job / "job.json").write_text(
        '{"job_id": "x", "style": "rte", "params": {}}{', encoding="utf-8"
    )
    r = handle(
        {
            "method": "tools/call",
            "params": {"name": "job_next", "arguments": {"job": str(job)}},
        }
    )
    assert r is not None and r["isError"]
    bad = _rpc(
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        ]
    )
    assert bad[0]["error"]["code"] == -32602
