"""客户端匿名漏斗统计与可选错误上报测试（ A，）。"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.cloud import config, telemetry
from pacxio.cloud.client import Client
from pacxio.model.common import Result


@pytest.fixture()
def telemetry_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("PACXIO_HOME", str(home))
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, "1")
    monkeypatch.delenv(telemetry.ERROR_REPORTS_ENV, raising=False)
    monkeypatch.delenv(telemetry.DO_NOT_TRACK_ENV, raising=False)
    monkeypatch.delenv(telemetry.TELEMETRY_URL_ENV, raising=False)
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.delenv(config.URL_ENV, raising=False)
    # 单元测试内不派生真实后台子进程或线程（上报逻辑由 flush_now 显式测）
    monkeypatch.setattr(telemetry, "_spawn_flush_subprocess", lambda: None)
    monkeypatch.setattr(telemetry, "_spawn_flush_thread", lambda: None)
    telemetry.set_mcp_client(None)
    return home


class _Collector:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.batches: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []


def _serve_collector(col: _Collector) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _reply(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            col.headers.append({k.lower(): v for k, v in self.headers.items()})
            if self.path == "/v1/health":
                return self._reply(200, {"ok": True})
            return self._reply(404, {})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            col.headers.append({k.lower(): v for k, v in self.headers.items()})
            try:
                col.batches.append(json.loads(raw))
            except ValueError:
                col.batches.append({"raw": raw.decode("utf-8", errors="replace")})
            return self._reply(col.status, {"ok": 200 <= col.status < 300})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_silence_guards_respect_env_and_dnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(telemetry.TELEMETRY_ENV, raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(config, "closed_available", lambda: False)

    # pytest 环境下默认自动静默
    st = telemetry.effective_status()
    assert not st["enabled"] and st["reason"] == "pytest_environment"

    # 离开 pytest 后，开源版默认开启；DO_NOT_TRACK=1 或 PACXIO_CLOUD=0 立即关闭
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv(config.MODE_ENV, raising=False)
    monkeypatch.delenv(telemetry.DO_NOT_TRACK_ENV, raising=False)
    st_open = telemetry.effective_status()
    assert st_open["enabled"] and st_open["reason"] == "enabled"
    assert not st_open["error_reports"] and st_open["error_reports_reason"] == "opt_in_off"

    monkeypatch.setenv(telemetry.DO_NOT_TRACK_ENV, "1")
    st_dnt = telemetry.effective_status()
    assert not st_dnt["enabled"] and st_dnt["reason"] == "do_not_track"

    monkeypatch.delenv(telemetry.DO_NOT_TRACK_ENV, raising=False)
    monkeypatch.setenv(config.MODE_ENV, "0")
    st_offline = telemetry.effective_status()
    assert not st_offline["enabled"] and st_offline["reason"] == "local_offline_mode"

    monkeypatch.delenv(config.MODE_ENV, raising=False)
    monkeypatch.setattr(config, "closed_available", lambda: True)
    st_closed = telemetry.effective_status()
    assert not st_closed["enabled"] and st_closed["reason"] == "closed_dev_or_server_env"


def test_funnel_event_has_no_pii_and_skips_readonly_noise(
    telemetry_env: Path, tmp_path: Path
) -> None:
    del telemetry_env
    secret_job = tmp_path / "SecretClientBrand-2026"
    secret_job.mkdir()

    # 只读命令成功时不记入漏斗（避免噪音）
    assert telemetry.build_event(command="job next", ok=True, job=secret_job) is None
    assert telemetry.build_event(command="structures list", ok=True) is None
    assert telemetry.build_event(command="telemetry status", ok=True) is None

    # 关键漏斗命令记录阶段 + 不可逆 job_hash + 白名单元数据，不含路径或错误原文
    ev = telemetry.build_event(
        command="job init",
        ok=False,
        exit_code=1,
        job=secret_job,
        duration_ms=145,
        error_ids=["S03"],
        issues=[{"id": "S03", "message": "SecretClientBrand at /Users/alice/secret.json failed"}],
        data={"style": "rte", "material": "silver_card_350", "secret_prompt": "do not leak"},
        mode="cloud",
        agent="antigravity",
    )
    assert ev is not None
    raw = json.dumps(ev)
    assert "SecretClientBrand" not in raw
    assert "/Users/alice" not in raw
    assert "do not leak" not in raw
    assert ev["stage"] == "job_init"
    assert ev["command"] == "job init"
    assert ev["error_ids"] == ["S03"]
    assert "error_detail" not in ev  # 默认 Opt-in 关闭，绝不附带错误文本
    assert len(ev["job_hash"]) == 16
    assert ev["meta"] == {
        "mode": "cloud",
        "agent": "antigravity",
        "style": "rte",
        "material": "silver_card_350",
    }

    # 同一盒子后续阶段得到同一个 job_hash，便于串联单盒漏斗
    ev2 = telemetry.build_event(command="export", ok=True, job=secret_job)
    assert ev2 is not None and ev2["stage"] == "export" and ev2["job_hash"] == ev["job_hash"]


def test_opt_in_error_reports_scrub_paths_emails_tokens_and_tracebacks(
    telemetry_env: Path,
) -> None:
    del telemetry_env
    telemetry.set_error_reports(True)
    assert telemetry.error_reports_enabled()

    dirty_msg = (
        "Failed reading /Users/alice/projects/acme/logo.png and C:\\Users\\Bob\\box.svg "
        "for alice@acme.com with Bearer eyJhbGciOiJIUzI1NiJ9.secret and sk-proj1234567890abcdef "
        "at https://api.pacxio.dev/v1/x?token=supersecret"
    )
    scrubbed = telemetry.scrub_text(dirty_msg)
    for forbidden in ("alice", "Bob", "acme", "eyJhbGciOiJIUzI1NiJ9", "sk-proj", "supersecret"):
        assert forbidden not in scrubbed
    assert "<path>" in scrubbed
    assert "<redacted-email>" in scrubbed
    assert "Bearer <redacted>" in scrubbed
    assert "<redacted-key>" in scrubbed
    assert "https://api.pacxio.dev/v1/x?<redacted>" in scrubbed

    try:
        raise ValueError("boom at /Users/alice/private/job.json for bob@brand.io")
    except ValueError as exc:
        ev = telemetry.build_event(
            command="render",
            ok=False,
            exit_code=1,
            error_ids=["ValueError"],
            exception=exc,
        )
    assert ev is not None and "error_detail" in ev
    detail = ev["error_detail"]
    assert detail["exception_type"] == "ValueError"
    assert "alice" not in detail["exception_message"]
    assert "bob@brand.io" not in detail["exception_message"]
    assert detail["traceback"]
    assert all("/Users/" not in frame for frame in detail["traceback"])


def test_spool_bounded_and_cli_controls(telemetry_env: Path, tmp_path: Path) -> None:
    del telemetry_env
    runner = CliRunner()

    # 跑一次本地建盒命令，应记入 1 条 job_init 事件
    job = tmp_path / "demo-box"
    r = runner.invoke(
        app,
        ["job", "init", str(job), "--style", "rte", "--L", "80", "--W", "40", "--H", "120", "--json"],
    )
    assert r.exit_code == 0, r.output
    events = telemetry.read_spool()
    assert len(events) == 1
    assert events[0]["stage"] == "job_init"
    assert events[0]["command"] == "job init"
    assert events[0]["ok"] is True
    assert "demo-box" not in json.dumps(events[0])

    # telemetry show / status / errors / off CLI
    show = runner.invoke(app, ["telemetry", "show", "--json"])
    assert show.exit_code == 0
    assert json.loads(show.output)["data"]["count"] == 1

    err_on = runner.invoke(app, ["telemetry", "errors", "on", "--json"])
    assert err_on.exit_code == 0
    assert json.loads(err_on.output)["data"]["error_reports"] is True

    err_bad = runner.invoke(app, ["telemetry", "errors", "maybe", "--json"])
    assert err_bad.exit_code == 2

    off = runner.invoke(app, ["telemetry", "off", "--json"])
    assert off.exit_code == 0
    assert telemetry.read_spool() == []  # off 立即清空未发缓冲


def test_mcp_single_counting_and_probe_suppression(telemetry_env: Path, tmp_path: Path) -> None:
    del telemetry_env
    from pacxio import mcp_server

    # 内部自检探针（doctor / pacxio-runtime-check）不记入 mcp_connect
    mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "pacxio-runtime-check", "version": "1"},
            },
        }
    )
    assert telemetry.read_spool() == []

    # 真实 Agent 连接记 1 条 mcp_connect
    mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "antigravity", "version": "2.0"},
            },
        }
    )
    # 通过 pacxio_run 跑 job init，只能记 1 条（不能 CLI + MCP 重复记 2 条）
    job = tmp_path / "mcp-box"
    mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "pacxio_run",
                "arguments": {
                    "command": "job init",
                    "args": [str(job), "--style", "rte", "--L", "80", "--W", "40", "--H", "120"],
                },
            },
        }
    )
    events = telemetry.read_spool()
    assert [e["stage"] for e in events] == ["mcp_connect", "job_init"]
    assert all(e["surface"] == "mcp" for e in events)
    assert events[1]["meta"]["agent"] == "antigravity"


def test_flush_http_200_404_backoff_400_drop_and_install_id_header(
    telemetry_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del telemetry_env
    col = _Collector(status=200)
    srv, base = _serve_collector(col)
    try:
        monkeypatch.setenv(config.URL_ENV, base)
        monkeypatch.setenv(config.TOKEN_ENV, "tok-secret")

        # 正常云端请求带 X-Pacxio-Install-Id（开启统计时）
        client = Client(base, "tok-secret")
        try:
            assert client.health() is True
        finally:
            client.close()
        assert col.headers[-1].get("x-pacxio-install-id") == telemetry.install_id(create=False)

        # 200 上报成功清空队列，且带上 Bearer token 与 X-Pacxio-Install-Id
        telemetry.record_result(Result(command="job init", ok=True), surface="cli")
        res = telemetry.flush_now(force=True)
        assert res == {"sent": 1, "remaining": 0, "status": "sent"}
        assert len(col.batches) == 1
        assert col.batches[0]["events"][0]["stage"] == "job_init"
        assert col.headers[-1].get("authorization") == "Bearer tok-secret"

        # 404（服务端未上线采集接口）→ 静默退避 6 小时，保留队列不报错
        col.status = 404
        telemetry.record_result(Result(command="export", ok=True), surface="cli")
        res404 = telemetry.flush_now(force=True)
        assert res404["status"] == "unsupported:404"
        assert res404["remaining"] == 1
        st = json.loads(telemetry.state_path().read_text(encoding="utf-8"))
        assert st["next_retry_at"] > time.time() + 5 * 3600

        # 非 force flush 命中退避，直接返回 backoff 不再发请求
        before = len(col.batches)
        assert telemetry.flush_now(force=False)["status"] == "backoff"
        assert len(col.batches) == before

        # 400 坏批次丢弃，避免死循环
        col.status = 400
        res400 = telemetry.flush_now(force=True)
        assert res400 == {"sent": 0, "remaining": 0, "status": "dropped:400"}

        # 关闭统计后，正常云端请求不再发 X-Pacxio-Install-Id
        monkeypatch.setenv(telemetry.TELEMETRY_ENV, "0")
        client2 = Client(base, "tok-secret")
        try:
            assert client2.health() is True
        finally:
            client2.close()
        assert "x-pacxio-install-id" not in col.headers[-1]
    finally:
        srv.shutdown()
        srv.server_close()
