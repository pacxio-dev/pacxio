"""开源版边界（ ）：屏蔽闭源模块后，开源命令在本机照常跑，闭源命令全部转发。

每个场景在新进程里跑：先 `block_closed()`，再导入入口、跑命令。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PIL import Image

from pacxio.cloud import route
from pacxio.cloud.boundary import block_closed, is_closed

DRIVER = textwrap.dedent(
    """
    import contextlib, io, json, sys
    from pacxio.cloud.boundary import block_closed
    block_closed()
    import pacxio.cli, pacxio.mcp_cli, pacxio.mcp_server, pacxio.studio.server, pacxio.studio.state  # noqa
    from pacxio.cli import main
    out = []
    for argv in json.loads(sys.argv[1]):
        buf = io.StringIO()
        sys.argv = ["pacxio", *argv, "--json"]
        try:
            with contextlib.redirect_stdout(buf):
                main()
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except Exception as e:  # noqa: BLE001
            code = f"{type(e).__name__}: {e}"
        try:
            data = json.loads(buf.getvalue())
        except ValueError:
            data = {"raw": buf.getvalue()[-500:]}
        out.append({"argv": argv, "code": code, "errors": [i["id"] for i in data.get("errors", [])]})
    print("RESULT" + json.dumps(out))
    """
)


def _dead_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_blocked(cmds: list[list[str]], cwd: Path) -> list[dict]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PACXIO_")}
    env.update({
        "PACXIO_HOME": str(cwd / "home"),
        "PACXIO_CLOUD_URL": f"http://127.0.0.1:{_dead_port()}",
        "PACXIO_CLOUD_TOKEN": "test-token",
    })  # fmt: skip
    p = subprocess.run([sys.executable, "-c", DRIVER, json.dumps(cmds)], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=600)
    line = next((x for x in p.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line is not None, p.stdout[-2000:] + p.stderr[-3000:]
    return list(json.loads(line[len("RESULT"):]))


def test_closed_is_the_private_package() -> None:
    assert is_closed("pacxio_pro") and is_closed("pacxio_pro.layout.compose")
    assert not is_closed("pacxio.dielines") and not is_closed("pacxio_professional")


def test_open_package_has_no_top_level_private_imports() -> None:
    import ast

    src = Path(__file__).resolve().parents[2] / "src" / "pacxio"
    for p in src.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in tree.body:  # 模块顶层（TYPE_CHECKING 里的只给类型检查用，不算）
            mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else (
                [a.name for a in n.names] if isinstance(n, ast.Import) else [])
            assert not any(is_closed(m) for m in mods), f"{p}:{n.lineno}"


def test_block_closed_refuses_after_closed_import() -> None:
    pytest.importorskip("pacxio_pro.layout.compose")  # 只在装着闭源包的机器上测

    with pytest.raises(RuntimeError):
        block_closed()


def test_open_commands_run_locally_and_closed_commands_forward(tmp_path: Path) -> None:
    Image.new("RGB", (120, 120), (20, 40, 160)).save(tmp_path / "a.png")
    (tmp_path / "c.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="5"/></svg>'
    )
    local = [
        ["env", "check"],
        ["cloud", "status"],
        ["styles", "list"],
        ["structures", "show", "A20.20.03.01"],
        ["structures", "suggest", "--use", "电商"],
        ["job", "init", "j", "--style", "rte", "--L", "80", "--W", "50", "--H", "180"],
        ["panels", "j"],
        ["next", "j"],
        ["content", "add", "j", "--category", "brand", "--text", "Hi"],
        ["content", "list", "j"],
        ["style", "set", "j", "title", "--font", "SourceHanSansSC-Bold", "--size-pt", "12"],
        ["window", "add", "j", "--panel", "front", "--box", "20,40,30,40"],
        ["notes", "add", "j", "--text", "x"],
        ["visual", "import", "j", "a.png", "--role", "graphic", "--name", "mark"],
        ["concept", "import", "j", "a.png", "--name", "c1"],
        ["concept", "tag", "j", "c1", "--view", "main"],
        ["concept", "list", "j"],
        ["font", "list", "j"],
        ["studio", "status", "j"],
        ["structures", "files", "0427", "--L", "200", "--W", "150", "--H", "50", "--out", "files"],
    ]
    forwarded = [
        ["render", "j"],
        ["preflight", "j"],
        ["export", "j"],
        ["mockup", "j"],
        ["plan", "j"],
        ["flat", "j"],
        ["approve", "j", "--by", "test"],
        ["art", "add", "j", "--kind", "text", "--panel", "front", "--box", "10,10,40,10", "--text", "Hi"],
        ["restore", "score", "j"],
        ["visual", "trace", "j", "mark"],
        ["concept", "choose", "j", "c1", "--by", "test"],
        ["color", "list", "j"],
        ["finish", "list", "j"],
        ["surface", "list", "j"],
        ["attach", "add", "j", "--panel", "front", "--box", "10,10,20,20", "--source", "c.svg"],
    ]
    for argv in forwarded:
        assert route.command_of(argv) is not None, argv
    results = _run_blocked(local + forwarded, tmp_path)
    by = {tuple(r["argv"]): r for r in results}
    for argv in local:
        r = by[tuple(argv)]
        assert r["code"] == 0 and not any(e.startswith("CLOUD") for e in r["errors"]), r
    for argv in forwarded:
        r = by[tuple(argv)]
        assert r["code"] == 3 and r["errors"] == ["CLOUD_NET"], r  # 走了云端（这里连不上）
    assert not (tmp_path / "j" / "preview.png").exists()
