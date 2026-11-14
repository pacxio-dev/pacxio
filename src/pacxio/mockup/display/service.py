"""Automatic, transactional display generation after the legacy scene is ready."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from .identity import digest

SUPPORTED_STYLES = frozenset({"rte", "ste", "fefco_0427"})
BUILDER_VERSION = 1
BUILD_TIMEOUT_SECONDS = 45


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _records(scene: dict[str, Any], root: Path) -> dict[str, dict[str, Any]]:
    records = {}
    for part in scene["parts"]:
        for rel in part["textures"].values():
            p = (root / rel).resolve()
            p.relative_to(root.resolve())
            st = p.stat()
            records[rel] = {"size": st.st_size, "mtime_ns": str(st.st_mtime_ns), "sha256": _sha(p)}
    return records


def _atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _valid_generation(folder: Path, key: str) -> dict[str, Any] | None:
    try:
        info = json.loads((folder / "build.json").read_bytes())
        if info["key"] != key or info["version"] != BUILDER_VERSION:
            return None
        for name, checksum in info["assets"].items():
            if Path(name).name != name or _sha(folder / name) != checksum:
                return None
        return cast(dict[str, Any], info)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare_display(scene_path: Path, style: str) -> tuple[dict[str, Any], list[Path]]:
    """Return (public status, artifacts); never turn a valid mockup into failure."""
    scene_path = scene_path.resolve()
    root = scene_path.parent
    staging = None
    lock = None
    try:
        scene = json.loads(scene_path.read_bytes())
        scene.pop("display", None)
        # Discard an earlier descriptor immediately, including on unsupported jobs.
        _atomic(scene_path, json.dumps(scene, ensure_ascii=False).encode())
        if (
            style not in SUPPORTED_STYLES
            or scene.get("version") != 3
            or len(scene.get("parts", [])) != 1
        ):
            return {"renderer": "legacy", "reason": "unsupported_scene"}, []
        if sum(len(p["triangles"]) for p in scene["parts"][0]["panels"]) > 120000:
            return {"renderer": "legacy", "reason": "complex_scene"}, []
        source_id = digest(scene)
        records = _records(scene, root)
        inputs = {rel: r["sha256"] for rel, r in records.items()}
        key = hashlib.sha256(
            json.dumps([BUILDER_VERSION, style, source_id, inputs], sort_keys=True).encode()
        ).hexdigest()
        generations = root / "display"
        generations.mkdir(exist_ok=True)
        target = generations / key[:24]
        info = _valid_generation(target, key)
        cache_hit = info is not None
        if info is None:
            lock = root / ".display-build.lock"
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.time() - lock.stat().st_mtime > BUILD_TIMEOUT_SECONDS * 2:
                    lock.unlink(missing_ok=True)
                lock = None
                return {"renderer": "legacy", "reason": "generation_busy"}, []
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            staging = Path(tempfile.mkdtemp(prefix=".display-build-", dir=root))
            source = staging / "source"
            source.mkdir()
            build = staging / "assets"
            build.mkdir()
            snapshot = source / "scene.json"
            snapshot.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
            for rel in records:
                dst = source / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, dst)
                if _sha(dst) != records[rel]["sha256"]:
                    raise ValueError("Source changed while snapshotting")
            cache = root / ".display-cache"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pacxio.mockup.display.worker",
                    str(snapshot),
                    str(build),
                    str(cache),
                    style,
                ],
                capture_output=True,
                text=True,
                timeout=BUILD_TIMEOUT_SECONDS,
                check=True,
            )
            info = json.loads(proc.stdout)
            info["key"] = key
            if info.get("version") != BUILDER_VERSION or not info.get("assets"):
                raise ValueError("Invalid generator result")
            (build / "build.json").write_text(json.dumps(info), encoding="utf-8")
            if _valid_generation(build, key) is None:
                raise ValueError("Asset verification failed")
            # Reject concurrent design/texture edits; never publish a mixed generation.
            if (
                digest(json.loads(scene_path.read_bytes())) != source_id
                or {rel: r["sha256"] for rel, r in _records(scene, root).items()} != inputs
            ):
                raise ValueError("Source changed during generation")
            if target.exists():
                shutil.rmtree(target)
            build.rename(target)
        assert info is not None
        latest = _records(scene, root)
        if (
            digest(json.loads(scene_path.read_bytes())) != source_id
            or {rel: r["sha256"] for rel, r in latest.items()} != inputs
        ):
            raise ValueError("Source changed before activation")
        scene["display"] = {
            "version": 1,
            "hashFormat": "typed-ieee754-v1",
            "sourceSHA256": source_id,
            "manifest": f"display/{target.name}/manifest.json",
            "sourceFiles": latest,
        }
        _atomic(scene_path, json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode())
        # Keep the previous generation for viewers still finishing their fetches.
        old = sorted(
            (p for p in generations.iterdir() if p.is_dir() and p != target),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for p in old[1:]:
            shutil.rmtree(p, ignore_errors=True)
        cached = sorted(
            (root / ".display-cache").glob("*.npz"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for p in cached[8:]:
            with suppress(OSError):
                p.unlink(missing_ok=True)
        paths = [target / name for name in info["assets"]]
        return {
            "renderer": "enhanced",
            "cache_hit": cache_hit,
            "geometry_cache_hit": info.get("geometryCacheHit", False),
            "build_seconds": 0 if cache_hit else info["seconds"],
        }, paths
    except Exception as error:
        # Optional enhancement must not affect old scene/images or CLI success.
        reason = (
            "generation_timeout"
            if isinstance(error, subprocess.TimeoutExpired)
            else "generation_failed"
        )
        return {"renderer": "legacy", "reason": reason}, []
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
        if lock:
            with suppress(OSError):
                lock.unlink(missing_ok=True)


def prepare_job_display(directory: Path) -> tuple[dict[str, Any], list[Path]]:
    """Enhance downloaded previews without requiring the private composition package."""
    try:
        job = json.loads((directory / "job.json").read_bytes())
        return prepare_display(directory / "mockup" / "scene.json", job["style"])
    except Exception:
        return {"renderer": "legacy", "reason": "generation_failed"}, []
