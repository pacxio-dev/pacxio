"""Bounded subprocess builder. Writes only an unpublished generation directory."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import struct
import sys
import time
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import numpy as np
from PIL import Image

from .ao import bake
from .corners import repair
from .geometry import closed_mesh
from .identity import digest
from .materials import prepare

VERSION = 1
RESOURCES = Path(__file__).parent / "resources"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(scene_path: Path, out: Path, geometry_cache: Path, style: str) -> dict[str, Any]:
    start = time.perf_counter()
    scene = json.loads(scene_path.read_bytes())
    scene.pop("display", None)
    part = scene["parts"][0]
    out.mkdir(parents=True, exist_ok=True)
    geometry_cache.mkdir(parents=True, exist_ok=True)

    def publish(data: bytes, stem: str, suffix: str) -> str:
        name = f"{stem}.{sha(data)[:16]}{suffix}"
        (out / name).write_bytes(data)
        return name

    def packed(data: bytes, name: str, **extra: Any) -> dict[str, Any]:
        raw = b"PXA1" + struct.pack("<BBHII", 1, 0, 0, len(data), len(data)) + data
        blob = gzip.compress(raw, compresslevel=6, mtime=0)
        return {
            "kind": name,
            "format": "pacxio-packed-gzip",
            "version": 1,
            "codec": "raw",
            "decodedByteLength": len(data),
            "compressedByteLength": len(blob),
            "sha256": sha(data),
            "packedSHA256": sha(blob),
            "url": publish(blob, name, ".pxa.gz"),
            **extra,
        }

    # Geometry cache excludes texture paths/pixels and colour. A geometry change always misses.
    geo = {k: part[k] for k in ["caliper", "panels", "strips", "uv", "poses"]}
    key = sha(
        json.dumps(
            [VERSION, style, scene["steps"], scene["ground_step"], geo], sort_keys=True
        ).encode()
    )
    cache = geometry_cache / (key + ".npz")
    hit = False
    try:
        with np.load(cache, allow_pickle=False) as saved:
            data = saved["data"]
            size = saved["size"]
            counts = saved["counts"]
            checksum = saved["checksum"].item()
        if (
            sha(data.tobytes() + size.tobytes() + counts.tobytes()) != checksum
            or not np.isfinite(data).all()
        ):
            raise ValueError("Invalid geometry cache")
        hit = True
    except (OSError, ValueError, KeyError, EOFError, BadZipFile):
        meshes, size = closed_mesh(scene)
        if style == "fefco_0427":
            repair(meshes, scene, size)
        counts = np.array(
            [
                sum(m["data"].size // 12 for m in meshes if m["role"] == role)
                for role in ["print", "paper", "cut"]
            ]
        )
        data = np.concatenate(
            [
                m["data"].reshape(-1, 12)
                for role in ["print", "paper", "cut"]
                for m in meshes
                if m["role"] == role
            ]
        ).astype("<f4")
        if len(data) > 120000:
            raise ValueError("Display mesh exceeds tested size") from None
        bake(data)
        temp = cache.with_suffix(".tmp")
        with temp.open("wb") as f:
            np.savez_compressed(
                f,
                data=data,
                size=size,
                counts=counts,
                checksum=sha(data.tobytes() + size.tobytes() + counts.tobytes()),
            )
        temp.replace(cache)
    geometry_seconds = time.perf_counter() - start
    groups = []
    offset = 0
    for role, count in zip(["print", "paper", "cut"], counts, strict=True):
        groups.append({"role": role, "start": offset, "count": int(count)})
        offset += int(count)

    def image_asset(rel: str, name: str, preview: bool = False) -> str:
        path = (scene_path.parent / rel).resolve()
        path.relative_to(scene_path.parent.resolve())
        with Image.open(path) as source:
            original = source.convert("RGB")
        image = original.copy()
        if preview:
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        image.save(
            buf, "WEBP", lossless=not preview, quality=95 if preview else 20, method=4, exact=True
        )
        blob = buf.getvalue()
        if not preview:
            with Image.open(io.BytesIO(blob)) as decoded:
                if decoded.convert("RGB").tobytes() != original.tobytes():
                    raise ValueError("Texture pixels changed")
        return publish(blob, name, ".webp")

    textures = {
        "baseFull": image_asset(part["textures"]["base"], "artwork"),
        "basePreview": image_asset(part["textures"]["base"], "preview", True),
        "material": image_asset(part["textures"]["material"], "material"),
    }
    cut = next(RESOURCES.glob("cut-mask.*.png"))
    shutil.copyfile(cut, out / cut.name)
    textures["cutMask"] = cut.name
    env = next(RESOURCES.glob("studio-env.*.pxa.gz"))
    shutil.copyfile(env, out / env.name)
    fold = {k: scene[k] for k in ["version", "steps", "ground_step", "states", "parts"]}
    fold = json.loads(json.dumps(fold))
    fold["parts"][0].pop("textures", None)
    m = {
        "version": 2,
        "adapterVersion": 1,
        "hashFormat": "typed-ieee754-v1",
        "sourceSHA256": digest(scene),
        "units": "metres",
        "size": size.tolist(),
        "parts": [{"caliper": part["caliper"] * 0.001, "paper": part["paper"]}],
        "vertexCount": len(data),
        "stride": 12,
        "groups": groups,
        "textures": textures,
        "geometryAsset": packed(
            data.tobytes(), "geometry", componentType="float32-le", stride=12, vertexCount=len(data)
        ),
        "foldAsset": packed(
            json.dumps(fold, separators=(",", ":"), ensure_ascii=False).encode(),
            "fold-scene",
            contentType="application/json",
            sceneVersion=3,
        ),
        "environmentAsset": {
            "kind": "environment",
            "format": "pacxio-packed-gzip",
            "version": 1,
            "codec": "rgba16-rdelta-shuffle8",
            "decodedByteLength": 6291456,
            "sha256": "84a6bbbb2f5800cf2056ee066cf7329267929f3cfd6f8dde427e41bb844f0fc9",
            "url": env.name,
        },
        "ao": {"samples": 48, "maxDistanceMetres": 0.005, "normalOffsetMetres": 0.00005},
    }
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(m, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    prepare(scene_path, manifest)
    assets = {p.name: sha(p.read_bytes()) for p in out.iterdir() if p.is_file()}
    return {
        "version": VERSION,
        "assets": assets,
        "geometryCacheHit": hit,
        "geometrySeconds": geometry_seconds,
        "seconds": time.perf_counter() - start,
    }


if __name__ == "__main__":
    scene, out, cache, style = sys.argv[1:]
    print(json.dumps(build(Path(scene), Path(out), Path(cache), style)))
