from __future__ import annotations

import http.client
import json
import os
import threading

import pytest
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.studio.display_assets import validated_scene
from pacxio.studio.server import Studio


@pytest.fixture
def job(tmp_path):
    directory = tmp_path / "box"
    result = CliRunner().invoke(
        app, ["job", "init", str(directory), "--L", "80", "--W", "50", "--H", "120"]
    )
    assert result.exit_code == 0, result.output
    return directory


@pytest.fixture
def studio(job):
    server = Studio(job)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)


def get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, {k.lower(): v for k, v in response.getheaders()}, body


def sample(root):
    texture = root / "base.png"
    texture.write_bytes(b"original")
    stat = texture.stat()
    return {
        "version": 3,
        "parts": [{"textures": {"base": "base.png"}}],
        "display": {
            "version": 1,
            "sourceFiles": {"base.png": {"size": stat.st_size, "mtime_ns": str(stat.st_mtime_ns)}},
        },
    }


def test_valid_source_keeps_descriptor(tmp_path):
    scene = sample(tmp_path)
    assert validated_scene(scene, tmp_path) is scene


@pytest.mark.parametrize("change", ["edited", "missing", "incomplete", "outside", "version"])
def test_stale_or_unsafe_source_falls_back_without_mutation(tmp_path, change):
    scene = sample(tmp_path)
    if change == "edited":
        (tmp_path / "base.png").write_bytes(b"changed!")
        os.utime(tmp_path / "base.png", ns=(1, 1))
    elif change == "missing":
        (tmp_path / "base.png").unlink()
    elif change == "incomplete":
        scene["display"]["sourceFiles"] = {}
    elif change == "outside":
        scene["parts"][0]["textures"]["base"] = "../outside.png"
        scene["display"]["sourceFiles"] = {"../outside.png": {}}
    else:
        scene["display"]["version"] = 9
    result = validated_scene(scene, tmp_path)
    assert "display" not in result and "display" in scene
    assert result["parts"] == scene["parts"]


def test_server_filters_stale_scene_and_serves_only_display_gzip(studio, job):
    root = job / "mockup"
    root.mkdir(exist_ok=True)
    scene = sample(root)
    (root / "scene.json").write_text(json.dumps(scene))
    code, headers, data = get(studio, "/files/mockup/scene.json?v=1")
    assert code == 200 and "display" in json.loads(data)
    assert headers["cache-control"] == "no-store"
    (root / "base.png").unlink()
    assert "display" not in json.loads(get(studio, "/files/mockup/scene.json?v=1")[2])
    (root / "mesh.pxa.gz").write_bytes(b"packed")
    (root / "archive.gz").write_bytes(b"archive")
    assert get(studio, "/files/mockup/mesh.pxa.gz")[0] == 200
    assert get(studio, "/files/mockup/archive.gz")[0] == 404
