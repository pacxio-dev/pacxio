"""Translate legacy scene maps into an explicit three-layer display contract."""

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def prepare(scene_path: Path, manifest_path: Path) -> dict[str, Any]:
    scene_path = Path(scene_path)
    target = Path(manifest_path)
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    m = json.loads(target.read_text(encoding="utf-8"))
    part = scene["parts"][0]

    def publish(image: Image.Image, name: str) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", lossless=True, quality=100, method=4, exact=True)
        data = buffer.getvalue()
        decoded = Image.open(io.BytesIO(data)).convert(image.mode)
        if decoded.tobytes() != image.tobytes():
            raise ValueError("Finish texture pixels changed")
        filename = f"{name}.{hashlib.sha256(data).hexdigest()[:12]}.webp"
        (target.parent / filename).write_bytes(data)
        return filename

    h = Image.open(scene_path.parent / part["textures"]["height"]).convert("RGB")
    red, green, _ = h.split()
    ext = red.getextrema()
    has_relief = ext[0] != ext[1]
    has_holo = bool(np.asarray(green).max() > 0)
    relief = None
    holo = None
    gradient_range = 1.0
    gradient_error = 0.0
    if has_relief or has_holo:
        cavity = np.zeros((h.height, h.width), dtype=np.float32)
        if has_relief:
            radius = scene.get("relief_edge_mm", 0.2)
            texmm = part["tex_mm"]
            for i in range(8):
                angle = i * np.pi / 4
                for ring in [1, 2]:
                    dx = np.cos(angle) * radius * ring * h.width / texmm[0]
                    dy = np.sin(angle) * radius * ring * h.height / texmm[1]
                    shifted = red.transform(
                        red.size,
                        Image.Transform.AFFINE,
                        (1, 0, dx, 0, 1, dy),
                        resample=Image.Resampling.BILINEAR,
                        fillcolor=128,
                    )
                    cavity += np.asarray(shifted, dtype=np.float32) / 16
            cavity = np.clip((cavity - np.asarray(red, dtype=np.float32)) * 2.5, 0, 255)
        # Precompute the same central differences at full resolution. A shared
        # adaptive range retains sub-gray-level gradient accuracy with one GPU fetch.
        pixels = np.asarray(red, dtype=np.float32)
        padded = np.pad(pixels, ((1, 1), (1, 1)), mode="edge")
        gx = padded[1:-1, 2:] - padded[1:-1, :-2]
        gy = padded[2:, 1:-1] - padded[:-2, 1:-1]
        gradient_range = max(1.0, float(np.max(np.abs(gx))), float(np.max(np.abs(gy))))
        qx = np.rint(gx * 127 / gradient_range + 128).astype("uint8")
        qy = np.rint(gy * 127 / gradient_range + 128).astype("uint8")
        gradient_error = max(
            float(np.max(np.abs((qx.astype("float32") - 128) * gradient_range / 127 - gx))),
            float(np.max(np.abs((qy.astype("float32") - 128) * gradient_range / 127 - gy))),
        )
        gradient = (
            Image.merge(
                "RGB",
                (
                    Image.fromarray(qx),
                    Image.fromarray(qy),
                    Image.fromarray(np.rint(cavity).astype("uint8")),
                ),
            )
            if has_relief
            else Image.new("RGB", (1, 1), (128, 128, 0))
        )
        relief = publish(gradient, "relief-gradient")
        if has_holo:
            holo = publish(green.convert("RGB"), "holographic-mask")
    regions = []
    for record in scene.get("regions", []):
        url = part["textures"].get("region:" + record["id"])
        if url:
            mask = Image.open(scene_path.parent / url).convert("RGB")
            regions.append(
                {
                    **record,
                    "mask": publish(
                        mask, "region-" + hashlib.sha256(record["id"].encode()).hexdigest()[:8]
                    ),
                }
            )
    m["materialLayers"] = {
        "version": 1,
        "substrate": {
            "color": part["paper"],
            "caliperMM": part["caliper"],
            "roughness": 0.82,
            "grain": "procedural-object-mm",
        },
        "artwork": {
            "basePreview": m["textures"]["basePreview"],
            "baseFull": m["textures"]["baseFull"],
            "encoding": "legacy-composite",
            "containsFinishColors": True,
        },
        "finishes": {
            "properties": m["textures"]["material"],
            "channels": {"r": "roughness", "g": "metalness", "b": "clearcoat"},
            "relief": relief,
            "holographicMask": holo,
            "reliefChannels": {"r": "signed-dx", "g": "signed-dy", "b": "baked-cavity"},
            "gradientRange": gradient_range,
            "gradientErrorGrayLevels": gradient_error,
            "encoding": "central-gradient-rgb8",
            "reliefHeightMM": scene.get("relief_height_mm", 0.08),
            "cavityStrength": scene.get("relief_cavity", 0),
            "texMM": part["tex_mm"],
            "regions": regions,
            "hasRelief": has_relief,
            "hasHolographic": has_holo,
        },
    }
    target.write_text(json.dumps(m, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return {
        "manifest": str(target),
        "heightSize": h.size,
        "hasRelief": has_relief,
        "hasHolographic": has_holo,
        "regions": len(regions),
        "extraBytes": sum(
            (target.parent / n).stat().st_size
            for n in [relief, holo, *[r["mask"] for r in regions]]
            if n
        ),
    }
