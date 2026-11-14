"""Guide preferences persist across jobs without changing their production state."""
from __future__ import annotations

import http.client
import json
import threading

import pytest

from pacxio import api
from pacxio.studio import onboarding
from pacxio.studio.server import STATIC_DIR, Studio


@pytest.fixture
def studios(tmp_path, monkeypatch):
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "account"))
    servers = []
    for name in ("one", "two"):
        directory = tmp_path / name
        assert api.init_job(directory, "rte", {"L": 80, "W": 50, "H": 180}, "sbs_350").ok
        s = Studio(directory)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
    yield servers
    for s in servers:
        s.shutdown()


def request(studio, method="GET", body=None, token=None, path="/api/onboarding"):
    conn = http.client.HTTPConnection("127.0.0.1", studio.port, timeout=10)
    headers = {"Content-Type": "application/json", "X-Studio-Token": token or studio.token}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = conn.getresponse()
    status, data = response.status, response.read()
    conn.close()
    return status, data


def test_preference_survives_other_job_and_port_without_confirming_job(studios):
    first, second = studios
    before = {s.directory: (s.directory / "job.json").read_bytes() for s in studios}
    assert json.loads(request(first)[1])["seen"] is False
    assert request(first, "POST", {"seen": True})[0] == 200
    assert json.loads(request(second)[1])["seen"] is True
    for s in studios:
        assert (s.directory / "job.json").read_bytes() == before[s.directory]
        assert not (s.directory / "content_confirmed.json").exists()
        assert not (s.directory / "concepts/chosen.json").exists()


def test_preference_requires_page_token_and_strict_payload(studios):
    first, _ = studios
    assert request(first, "POST", {"seen": True}, token="wrong")[0] == 403
    for body in ({"seen": "true"}, {"seen": False}, {"seen": 1}, {"seen": True, "job": "override"}):
        assert request(first, "POST", body)[0] == 400
    assert not onboarding.seen()


def test_corrupt_preference_and_write_failure_are_recoverable(studios, monkeypatch):
    path = onboarding.preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken")
    assert json.loads(request(studios[0])[1])["seen"] is False
    def fail():
        raise OSError("read-only directory")
    monkeypatch.setattr(onboarding, "dismiss", fail)
    assert request(studios[0], "POST", {"seen": True})[0] == 500
    assert request(studios[0], path="/api/ping")[0] == 200


def test_examples_are_local_small_assets_and_not_job_files(studios):
    assets = list(STATIC_DIR.glob("guide-tee-*.webp"))
    assert len(assets) == 5
    assert sum(p.stat().st_size for p in assets) < 80_000
    for asset in assets:
        status, body = request(studios[0], path="/static/" + asset.name)
        assert status == 200 and body == asset.read_bytes()
    assert request(studios[0], path="/static/../onboarding.py")[0] == 404
