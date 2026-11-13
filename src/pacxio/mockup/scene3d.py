"""给工作台的可转动 3D 效果图写场景文件（ 节）。

与静态示意图共用几何与图文裁切；v2 单独保存未烘焙高光的底色、材质、高度及区域遮罩。
无 materials 参数时仍可写 v1 场景，供旧数据与兼容调用使用。
写 `mockup/scene.json` 和 `mockup/scene/<状态>_<序号>.png`（贴图缩到最长边 SCENE_TEX_MAX）。

scene.json：
    {"version": 1, "fingerprint": …, "states": {"closed": {"size": [x, y, z], "faces": [
        {"key", "label", "normal": [x, y, z], "corners": [[x, y, z] × 4], "texture": "scene/closed_00.png"}, …]},
      "open": {…}（多部件盒型才有）}}
corners 依次对应贴图的左上、右上、右下、左下；坐标单位 mm，原点是包围盒的左前下角，z 向上，正面朝 -y。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from pacxio.mockup.fold import BoxModel

SCENE_FILE = "scene.json"
SCENE_DIR = "scene"
SCENE_TEX_MAX = 1024  # 3D 查看用不着打印分辨率，贴图最长边缩到这么多像素
SCENE_VERSION = 2
# 2.2：击凸 / 压凹改成均匀斜边、平顶，加凹处压暗（2026-10-24）；3.0：按刀版折叠的场景 v3（2026-11-01）
RENDERER_VERSION = "3.0"

State = tuple[BoxModel, dict[str, Image.Image]]


def _small(img: Image.Image) -> Image.Image:
    k = SCENE_TEX_MAX / max(img.size)
    if k >= 1:
        return img
    return img.resize(
        (max(1, round(img.width * k)), max(1, round(img.height * k))), Image.Resampling.LANCZOS
    )


def write_scene(
    out: Path,
    states: dict[str, State],
    fingerprint: str,
    materials: dict[str, Any] | None = None,
    regions: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """写场景文件与贴图；返回写出的文件。旧的贴图先清掉。"""
    tex_dir = out / SCENE_DIR
    tex_dir.mkdir(parents=True, exist_ok=True)
    for old in tex_dir.glob("*.png"):
        old.unlink()
    files: list[Path] = []
    states_out: dict[str, object] = {}
    for state, (box, textures) in states.items():
        faces: list[dict[str, Any]] = []
        for i, face in enumerate(box.faces):
            tex = textures.get(face.key)
            if tex is None:
                continue
            name = f"{state}_{i:02d}.png"
            _small(tex).save(tex_dir / name)
            files.append(tex_dir / name)
            faces.append(
                {
                    "key": face.key,
                    "label": face.label,
                    "normal": [round(v, 6) for v in face.normal],
                    "corners": [[round(v, 3) for v in c] for c in face.corners],
                    "texture": f"{SCENE_DIR}/{name}",
                }
            )
            if materials is not None:
                channels = {}
                for channel, image in materials[state][face.key].items():
                    # 避免用户 id 变成文件路径；编号由当前区域列表稳定映射。
                    suffix = (
                        channel
                        if not channel.startswith("region:")
                        else "region_"
                        + str(
                            next(j for j, r in enumerate(regions or []) if r["id"] == channel[7:])
                        )
                    )
                    fn = f"{state}_{i:02d}_{suffix}.png"
                    _small(image).save(tex_dir / fn)
                    files.append(tex_dir / fn)
                    channels[channel] = f"{SCENE_DIR}/{fn}"
                faces[-1]["maps"] = channels
        size = [round(box.x, 3), round(box.y, 3), round(box.z, 3)]
        states_out[state] = {"size": size, "faces": faces}
    data = {
        "version": SCENE_VERSION if materials is not None else 1,
        "renderer_version": RENDERER_VERSION,
        "fingerprint": fingerprint,
        "states": states_out,
        "regions": regions or [],
    }
    if materials is not None:
        from pacxio.mockup.materials import presets

        cfg = presets()
        data["material_version"] = cfg["version"]
        data["relief_height_mm"] = cfg["relief_height_mm"]
        data["relief_edge_mm"] = cfg["relief_edge_mm"]
        data["relief_cavity"] = cfg["relief_cavity"]
    scene = out / SCENE_FILE
    scene.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    return [scene, *files]


__all__ = ["SCENE_DIR", "SCENE_FILE", "write_scene"]
