"""云端客户端（、）：转发判断、参数改写、同步、结果路径换回本机。

用一个进程内的假服务，不连真服务器。
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from pacxio.cloud import auth, config, route
from pacxio.cloud.client import Client, CloudError
from pacxio.cloud.sync import local_manifest


class Fake:
    """假合成服务：记下收到的东西，跑命令时按规则产出文件。"""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.inbox: dict[str, bytes] = {}
        self.argv: list[Any] = []
        self.returnable: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.device = "pending"  # pending / approved / denied / expired
        self.device_starts = 0
        self.polls = 0
        self.revoked: list[str] = []


def _serve(fake: Fake, token: str = "tok") -> tuple[ThreadingHTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _send(self, code: int, body: Any) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self) -> bool:
            if self.headers.get("Authorization") != f"Bearer {token}":
                self._send(401, {"detail": "not signed in"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/v1/health":
                return self._send(200, {"ok": True})
            if not self._authed():
                return None
            if u.path == "/v1/me":
                return self._send(200, {"user": "u1", "email": "ada@example.com"})
            if u.path.startswith("/v1/runs/"):
                rid = u.path.rsplit("/", 1)[1]
                if rid != "r1":
                    return self._send(404, {"detail": "no such run"})
                files = {
                    k: {"sha256": hashlib.sha256(v).hexdigest(), "size": len(v)}
                    for k, v in fake.returnable.items()
                }
                result = {"ok": True, "command": "render", "job": "@job",
                          "artifacts": ["@job/preview.png"], "data": {"font": [a for a in map(str, fake.argv) if "file" in a]},
                          "errors": [], "warnings": []}  # fmt: skip
                return self._send(
                    200, {"status": "done", "exit_code": 0, "result": result, "files": files}
                )
            if "/files/" in u.path:
                rel = unquote(u.path.split("/files/", 1)[1])
                return (
                    self._send(200, fake.returnable[rel])
                    if rel in fake.returnable
                    else self._send(404, {})
                )
            return self._send(404, {})

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            u = urlparse(self.path)
            if u.path == "/v1/device/code":
                fake.device_starts += 1
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                return self._send(200, {"device_code": "dev1", "user_code": "BCDF-GHJK", "verification_uri": f"{base}/device",
                                        "verification_uri_complete": f"{base}/device?code=BCDF-GHJK",
                                        "expires_in": 900, "interval": 0})  # fmt: skip
            if u.path == "/v1/device/token":
                fake.polls += 1
                if fake.device == "approved":
                    fake.device = "used"
                    return self._send(200, {"access_token": token, "token_type": "bearer"})
                err = {"pending": "authorization_pending", "denied": "access_denied"}.get(fake.device, "expired_token")
                return self._send(400, {"error": err})
            if not self._authed():
                return None
            if u.path == "/v1/logout":
                fake.revoked.append(token)
                return self._send(200, {"ok": True})
            if u.path == "/v1/jobs":
                return self._send(200, {"job": "j1"})
            if u.path.endswith("/manifest"):
                missing = [r for r, s in body["files"].items()
                           if hashlib.sha256(fake.files.get(r, b"\0")).hexdigest() != s]  # fmt: skip
                return self._send(200, {"missing": missing})
            if u.path.endswith("/runs"):
                fake.argv = body["argv"]
                fake.returnable = {"preview.png": b"PNG-from-server",
                                   "job.json": fake.files["job.json"].replace(b"}", b', "rendered": true}')}  # fmt: skip
                fake.files.update(fake.returnable)  # 真服务器上这些文件就是新的
                return self._send(200, {"run": "r1"})
            return self._send(404, {})

        def do_PUT(self) -> None:  # noqa: N802
            if not self._authed():
                return None
            data = self.rfile.read(int(self.headers["Content-Length"]))
            u = urlparse(self.path)
            if "/inbox/" in u.path:
                fake.inbox[unquote(u.path.split("/inbox/", 1)[1])] = data
            else:
                rel = unquote(u.path.split("/files/", 1)[1])
                fake.puts.append(rel)
                assert parse_qs(u.query)["sha256"][0] == hashlib.sha256(data).hexdigest()
                fake.files[rel] = data
            return self._send(200, {"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture()
def job(tmp_path: Path) -> Path:
    j = tmp_path / "jobs" / "box"
    (j / "assets").mkdir(parents=True)
    (j / "job.json").write_text('{"id": "box"}')
    (j / "assets" / "logo.svg").write_text("<svg/>")
    (j / "out").mkdir()
    (j / "out" / "old.pdf").write_bytes(b"x")
    (j / ".cloud").mkdir()
    return j


@pytest.fixture()
def cloud_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.delenv(config.URL_ENV, raising=False)
    monkeypatch.setenv(config.MODE_ENV, "1")


def test_route_table_and_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    assert route.command_of(["render", "jobs/x"]) == ("render",)
    assert route.command_of(["restore", "strip", "jobs/x"]) == ("restore", "strip")
    assert route.command_of(["visual", "import", "jobs/x"]) is None
    assert route.command_of(["job", "init"]) is None
    monkeypatch.setenv(config.MODE_ENV, "0")
    assert not route.should_forward(["render", "jobs/x"])
    monkeypatch.setenv(config.MODE_ENV, "1")
    assert route.should_forward(["render", "jobs/x"])
    assert not route.should_forward(["render", "--help"])
    assert not route.should_forward(["job", "init", "jobs/x"])
    monkeypatch.delenv(config.MODE_ENV)
    assert route.should_forward(["render", "x"]) is (not config.closed_available())


def test_prepare_rewrites_paths(job: Path, tmp_path: Path) -> None:
    font = tmp_path / "Brand.otf"
    font.write_bytes(b"font")
    p = route.prepare(["restore", "face", str(job), "--source", str(job / "assets" / "logo.svg"),
                       f"--font={font}", "front", "--json"], tmp_path)  # fmt: skip
    assert p.job == job.resolve()
    sha = hashlib.sha256(b"font").hexdigest()
    assert p.argv == ["restore", "face", {"job": True}, "--source", {"jobfile": "assets/logo.svg"},
                      "--font", {"file": f"{sha}/Brand.otf"}, "front"]  # fmt: skip
    assert p.inbox == {f"{sha}/Brand.otf": font.resolve()}
    rel = route.prepare(["render", "jobs/box"], tmp_path)  # 相对路径按 cwd 找
    assert rel.job == job.resolve()


def test_manifest_skips_generated_and_state(job: Path) -> None:
    assert set(local_manifest(job)) == {"job.json", "assets/logo.svg"}


def test_forward_end_to_end_with_fake(job: Path, tmp_path: Path, cloud_env: None) -> None:
    fake = Fake()
    srv, url = _serve(fake)
    try:
        config.save(url, "tok")
        font = tmp_path / "Brand.otf"
        font.write_bytes(b"font")
        out = route.maybe_forward(["render", str(job), "--font", str(font), "--json"], cwd=tmp_path)
        assert out is not None
        code, text = out
        data = json.loads(text)
        assert code == 0 and data["ok"], data
        assert data["job"] == str(job.resolve())
        assert data["artifacts"] == [str(job.resolve() / "preview.png")]
        assert {"job.json", "assets/logo.svg"} <= set(fake.files)
        assert not any(k.startswith(("out/", ".cloud/")) for k in fake.files)  # 生成物、记账不上传
        assert list(fake.inbox) == [f"{hashlib.sha256(b'font').hexdigest()}/Brand.otf"]
        assert (job / "preview.png").read_bytes() == b"PNG-from-server"
        assert json.loads((job / "job.json").read_text())["rendered"] is True
        assert json.loads((job / ".cloud" / "state.json").read_text()) == {"url": url, "job": "j1"}
        # 第二次：没变的文件不再上传
        fake.puts.clear()
        route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path)
        assert fake.puts == []
    finally:
        srv.shutdown()


def test_errors_carry_fixes(job: Path, tmp_path: Path, cloud_env: None) -> None:
    config.save("http://127.0.0.1:9", "tok")  # 没人监听的端口
    code, text = route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path) or (0, "")
    assert code == 3 and json.loads(text)["errors"][0]["id"] == "CLOUD_NET"
    code, text = route.maybe_forward(
        ["export", str(job), "--out", "x", "--json"], cwd=tmp_path
    ) or (0, "")
    assert code == 2 and json.loads(text)["errors"][0]["id"] == "CLOUD_ARG"
    code, text = route.maybe_forward(["render", "nowhere", "--json"], cwd=tmp_path) or (0, "")
    assert code == 2 and json.loads(text)["errors"][0]["id"] == "CLOUD_JOB"
    fake = Fake()
    srv, url = _serve(fake)
    try:
        with pytest.raises(CloudError) as e:
            Client(url, "wrong").create_job("x")
        assert e.value.id == "CLOUD_AUTH" and "pacxio login" in e.value.fix
    finally:
        srv.shutdown()


# ---------------- 登录（S4）----------------


@pytest.fixture()
def served(cloud_env: None, monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = Fake()
    srv, url = _serve(fake)
    monkeypatch.setenv(config.URL_ENV, url)
    yield fake, url
    srv.shutdown()


def test_first_cloud_command_starts_login_without_blocking(job: Path, tmp_path: Path, served: Any) -> None:
    fake, url = served
    fake.files["job.json"] = (job / "job.json").read_bytes()
    code, text = route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path) or (0, "")
    err = json.loads(text)["errors"][0]
    assert code == 3 and err["id"] == "CLOUD_LOGIN"
    assert f"{url}/device?code=BCDF-GHJK" in err["message"] and "BCDF-GHJK" in err["message"]
    assert "不能替用户确认" in err["fix"]
    # 还没确认：重跑还是同一个代码，不重新要
    code, text = route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path) or (0, "")
    assert json.loads(text)["errors"][0]["id"] == "CLOUD_LOGIN" and fake.device_starts == 1
    # 用户在浏览器确认后，重跑就登录完成并接着跑
    fake.device = "approved"
    code, text = route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path) or (0, "")
    assert code == 0 and json.loads(text)["ok"], text
    assert config.login().token == "tok" and auth.read_pending(url) is None
    assert config.creds_path().stat().st_mode & 0o077 == 0


def test_denied_and_revoked(job: Path, tmp_path: Path, served: Any) -> None:
    fake, url = served
    route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path)
    fake.device = "denied"
    code, text = route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path) or (0, "")
    assert json.loads(text)["errors"][0]["id"] == "CLOUD_LOGIN_DENIED" and auth.read_pending(url) is None
    # 保存的令牌被服务器撤销：清掉它、开始新的登录
    config.save(url, "revoked-token")
    fake.device = "pending"
    code, text = route.maybe_forward(["render", str(job), "--json"], cwd=tmp_path) or (0, "")
    assert code == 3 and json.loads(text)["errors"][0]["id"] == "CLOUD_LOGIN"
    assert config.login().token is None and auth.read_pending(url) is not None


def test_login_command_waits_and_logout_revokes(served: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, url = served
    said: list[str] = []
    r = auth.login(wait=False, say=said.append)
    assert r.ok and r.data["pending"] and r.data["user_code"] == "BCDF-GHJK" and "BCDF-GHJK" in said[0]

    def approve_on_second_poll(*a: Any) -> None:
        fake.device = "approved"

    monkeypatch.setattr(auth.time, "sleep", approve_on_second_poll)
    r = auth.login(say=said.append)
    assert r.ok and r.data == {"url": url, "signed_in": True, "email": "ada@example.com"}
    r = auth.login(say=said.append)
    assert r.data["already"] and fake.device_starts == 1
    r = auth.logout()
    assert r.data == {"removed": True, "revoked": True} and fake.revoked == ["tok"]
    assert config.login().token is None


def test_login_cli_json(served: Any) -> None:
    from typer.testing import CliRunner

    from pacxio.cli import app

    res = CliRunner().invoke(app, ["login", "--no-wait", "--json"])
    assert res.exit_code == 0
    out = json.loads(res.stdout)
    assert out["command"] == "login" and out["data"]["user_code"] == "BCDF-GHJK"


def test_credentials_file_is_private(tmp_path: Path, cloud_env: None) -> None:
    p = config.save("https://example.test/", "secret")
    assert p.stat().st_mode & 0o077 == 0
    assert config.login().url == "https://example.test"
    assert config.clear() and config.login().token is None
