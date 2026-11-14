"""Public-only generation from actual fold topology and synthetic preview pixels."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from pacxio import dielines
from pacxio.cli import app
from pacxio.mockup.display import service
from pacxio.mockup.display.geometry import closed_mesh
from pacxio.mockup.display.identity import digest
from pacxio.mockup.folding import build_part, plan_for
from pacxio.mockup.foldscene import _part_data, _single_poses
from pacxio.mockup.materials import presets
from pacxio.model.job import Job
from pacxio.studio.display_assets import validated_scene


def scene_job(root: Path, style="fefco_0427", length=240):
    args = [
        "job",
        "init",
        str(root),
        "--style",
        style,
        "--L",
        str(length),
        "--W",
        "170",
        "--H",
        "70",
    ]
    if style == "fefco_0427":
        args += ["--material", "flute_e"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    dset, errors = dielines.build_parts_for(Job.load(root))
    assert dset is not None, errors
    dl = next(iter(dset.parts.values()))
    plan = plan_for(dl)
    assert plan is not None
    part = build_part(dl, plan)
    data = _part_data(part, dl.caliper_mm * presets()["fold"]["crease_radius_caliper"])
    out = root / "mockup"
    out.mkdir()
    tex = out / "scene"
    tex.mkdir()
    base = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(base)
    draw.rectangle((5, 7, 41, 22), fill="red")
    base.save(tex / "base.png")
    Image.new("RGB", (64, 64), (180, 0, 0)).save(tex / "material.png")
    height = Image.new("RGB", (64, 64), (128, 0, 0))
    ImageDraw.Draw(height).rectangle((10, 10, 30, 30), fill=(190, 200, 0))
    height.save(tex / "height.png")
    data.update(
        id="main",
        tex_mm=[500, 600],
        uv=[0.002, 0, -1 / 600, 1],
        poses=_single_poses(part),
        textures={k: f"scene/{k}.png" for k in ["base", "material", "height"]},
    )
    scene = {
        "version": 3,
        "steps": plan.steps,
        "ground_step": plan.ground_step,
        "states": {
            "flat": {"t": 0},
            "open": {"t": plan.open_step / plan.steps},
            "closed": {"t": 1},
        },
        "parts": [data],
        "regions": [],
    }
    path = out / "scene.json"
    path.write_text(json.dumps(scene))
    return path


@pytest.mark.parametrize("style", ["rte", "ste", "fefco_0427"])
def test_real_topology_generates_and_decoded_artwork_is_identical(tmp_path, style):
    path = scene_job(tmp_path / style, style)
    status, assets = service.prepare_display(path, style)
    assert status["renderer"] == "enhanced", status
    scene = json.loads(path.read_bytes())
    assert validated_scene(scene, path.parent) is scene
    manifest = path.parent / scene["display"]["manifest"]
    m = json.loads(manifest.read_bytes())
    assert m["sourceSHA256"] == digest(scene)
    packed = gzip.decompress((manifest.parent / m["geometryAsset"]["url"]).read_bytes())
    assert hashlib.sha256(packed[16:]).hexdigest() == m["geometryAsset"]["sha256"]
    data = np.frombuffer(packed[16:], dtype="<f4").reshape(-1, 12)
    assert np.isfinite(data).all() and len(data) % 3 == 0
    assert np.all((data[:, 11] >= 0) & (data[:, 11] <= 1))
    # Clipped normals retain linear interpolation; the shader normalizes them.
    lengths = np.linalg.norm(data[:, 3:6], axis=1)
    assert np.all((lengths > 0.95) & (lengths < 1.0001))
    for col in [0, 1, 2]:
        assert np.ptp(data[:, col]) > 0
    assert (
        Image.open(manifest.parent / m["textures"]["baseFull"]).tobytes()
        == Image.open(path.parent / "scene/base.png").tobytes()
    )
    assert (
        m["materialLayers"]["finishes"]["hasRelief"]
        and m["materialLayers"]["finishes"]["hasHolographic"]
    )
    assert assets and all(a.is_file() for a in assets)
    second, _ = service.prepare_display(path, style)
    assert second["cache_hit"]


def test_artwork_only_change_reuses_geometry_and_never_serves_stale_maps(tmp_path):
    path = scene_job(tmp_path / "box", "rte")
    first, _ = service.prepare_display(path, "rte")
    assert first["renderer"] == "enhanced"
    before = json.loads(path.read_bytes())
    old = before["display"]["manifest"]
    base = path.parent / "scene/base.png"
    im = Image.open(base)
    im.putpixel((0, 0), (0, 255, 0))
    im.save(base)
    assert "display" not in validated_scene(before, path.parent)
    status, _ = service.prepare_display(path, "rte")
    assert status["geometry_cache_hit"] and not status["cache_hit"]
    after = json.loads(path.read_bytes())
    assert after["display"]["manifest"] != old
    assert (
        path.parent / old
    ).is_file()  # active viewers may finish loading the previous generation
    assert validated_scene(after, path.parent) is after


def test_changed_geometry_misses_cache_and_updates_bounds(tmp_path):
    path = scene_job(tmp_path / "box", "rte")
    service.prepare_display(path, "rte")
    scene = json.loads(path.read_bytes())
    old_size = closed_mesh(scene)[1]
    # The same supported scene at a new physical size.
    for part in scene["parts"]:
        part["caliper"] *= 1.5
        for panel in part["panels"]:
            panel["vertices"] = [v * 1.5 for v in panel["vertices"]]
            panel["rings"] = [[v * 1.5 for v in r] for r in panel["rings"]]
            if panel["origin"]:
                panel["origin"] = [v * 1.5 for v in panel["origin"]]
        for strip in part["strips"]:
            for key in ["origin", "crease", "parent_end", "child_end"]:
                strip[key] = [v * 1.5 for v in strip[key]]
            strip["half"] *= 1.5
        for pose in part["poses"]:
            pose["t"] = [v * 1.5 for v in pose["t"]]
    path.write_text(json.dumps(scene))
    status, _ = service.prepare_display(path, "rte")
    assert status["renderer"] == "enhanced" and not status["geometry_cache_hit"]
    after = json.loads(path.read_bytes())
    m = json.loads((path.parent / after["display"]["manifest"]).read_bytes())
    assert np.allclose(m["size"], old_size * 1.5, atol=1e-7)


@pytest.mark.parametrize(
    "kind", ["failed", "timeout", "concurrent", "unsupported", "corrupt-cache"]
)
def test_optional_failure_never_breaks_legacy(tmp_path, monkeypatch, kind):
    path = scene_job(tmp_path / "box", "rte")
    before = json.loads(path.read_bytes())
    if kind == "unsupported":
        before["parts"] *= 2
        path.write_text(json.dumps(before))
    elif kind == "corrupt-cache":
        service.prepare_display(path, "rte")
        s = json.loads(path.read_bytes())
        folder = (path.parent / s["display"]["manifest"]).parent
        (folder / "manifest.json").write_bytes(b"corrupt")

    def fail(*args, **kwargs):
        if kind == "concurrent":
            edited = json.loads(path.read_bytes())
            edited["steps"] += 1
            path.write_text(json.dumps(edited))
        if kind == "timeout":
            raise subprocess.TimeoutExpired("worker", 1)
        raise subprocess.CalledProcessError(1, "worker")

    monkeypatch.setattr(service.subprocess, "run", fail)
    status, assets = service.prepare_display(path, "rte")
    assert status["renderer"] == "legacy" and not assets
    after = json.loads(path.read_bytes())
    assert "display" not in after
    assert after["parts"] == before["parts"]
    assert (path.parent / "scene/base.png").is_file()
    assert not list(path.parent.glob(".display-build*"))


def test_same_bytes_copied_to_another_machine_remain_valid(tmp_path):
    path = scene_job(tmp_path / "box", "rte")
    service.prepare_display(path, "rte")
    scene = json.loads(path.read_bytes())
    tex = path.parent / "scene/base.png"
    os.utime(tex, ns=(1, 1))
    assert validated_scene(scene, path.parent) is scene
    content = bytearray(tex.read_bytes())
    content[-1] ^= 1
    tex.write_bytes(content)
    os.utime(tex, ns=(1, 1))
    assert "display" not in validated_scene(scene, path.parent)


def test_busy_worker_keeps_foreign_lock_and_legacy(tmp_path):
    path = scene_job(tmp_path / "box", "rte")
    lock = path.parent / ".display-build.lock"
    lock.write_text("other-process")
    status, _ = service.prepare_display(path, "rte")
    assert status["reason"] == "generation_busy"
    assert lock.read_text() == "other-process"


@pytest.mark.parametrize("conflict", [False, True])
def test_cloud_route_prepares_downloaded_preview_only(tmp_path, monkeypatch, conflict):
    from types import SimpleNamespace

    from pacxio.cloud import config, route

    remote = scene_job(tmp_path / "remote", "rte")
    job = tmp_path / "local"
    job.mkdir()
    (job / "job.json").write_bytes((remote.parent.parent / "job.json").read_bytes())
    monkeypatch.setattr(
        route.auth, "ensure", lambda: config.Login("https://example.invalid", "test")
    )
    monkeypatch.setattr(route, "Client", lambda *a: SimpleNamespace(start_run=lambda *a: "run"))
    monkeypatch.setattr(route, "push_with_retry", lambda *a: "job")

    def wait(*args):
        if conflict:
            data = json.loads((job / "job.json").read_bytes())
            data["job_id"] = "edited"
            (job / "job.json").write_text(json.dumps(data))
        return {
            "status": "done",
            "exit_code": 0,
            "files": {},
            "result": {"ok": True, "command": "mockup", "data": {}},
        }

    monkeypatch.setattr(route, "_wait", wait)

    def pull(*args, **kwargs):
        import shutil

        shutil.copytree(remote.parent, job / "mockup")

    monkeypatch.setattr(route, "pull", pull)
    result = route.forward(["mockup", str(job)], tmp_path)
    assert result.ok
    if conflict:
        assert "display" not in json.loads((job / "mockup/scene.json").read_bytes())
        assert any(w.id == "CLOUD_CONFLICT" for w in result.warnings)
    else:
        assert result.data["display_renderer"]["renderer"] == "enhanced"
        assert result.artifacts
