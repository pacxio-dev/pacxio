"""tests/unit 共用的夹具：本地假 OpenAI 服务（不花钱、不联网）。"""

from __future__ import annotations

import base64
import io
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PIL import Image

SECRET = "sk-test-secret-123"


def png_bytes(
    size: tuple[int, int] = (64, 96), color: tuple[int, int, int] = (20, 120, 80)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class FakeApi(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    status = 200
    body: dict[str, Any] | None = None

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers["Content-Length"])
        FakeApi.requests.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "json": json.loads(self.rfile.read(n)),
            }
        )
        payload = FakeApi.body if FakeApi.body is not None else {
            "data": [{"b64_json": base64.b64encode(png_bytes()).decode(), "revised_prompt": "revised by model"}]
        }  # fmt: skip
        self.send_response(FakeApi.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeApi]]:
    FakeApi.requests, FakeApi.status, FakeApi.body = [], 200, None
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.delenv("PACXIO_OPENAI_IMAGE_MODEL", raising=False)
    yield FakeApi
    server.shutdown()


@pytest.fixture
def unverified(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """回到"没经打样 / 工厂确认"的状态（盒型方向待定、配置档 verified: false），检查提醒还在。

    2026-10-24 起 8 种盒型的方向和 default 配置档都经实物打样 / 工厂确认（）；
    以后新加的盒型、别的工厂的配置档还会是没确认的，提醒的机制要一直能用。"""
    import shutil
    from pathlib import Path

    from pacxio.styles import corrugated, rte

    monkeypatch.setattr(rte, "ORIENTATION_VERIFIED", frozenset())
    monkeypatch.setattr(corrugated, "ORIENTATION_VERIFIED", frozenset())
    d = tmp_path_factory.mktemp("profiles")
    src = Path(__file__).resolve().parents[2] / "profiles"
    for f in src.iterdir():
        shutil.copy(f, d / f.name)
    text = (d / "default.yaml").read_text(encoding="utf-8")
    (d / "default.yaml").write_text(
        text.replace("verified: true", "verified: false", 1), encoding="utf-8"
    )
    monkeypatch.setenv("PACXIO_PROFILE_DIR", str(d))
